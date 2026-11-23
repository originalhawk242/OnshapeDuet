"""Budgeted Onshape REST backend.

Every HTTP send (including each redirect hop) is charged to the Ledger first.

ALL endpoint paths and JSON shapes below are UNVERIFIED against live Onshape
until Task 12; they are isolated here as constants/small functions.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from email.utils import formatdate
from urllib.parse import quote, urlsplit

import httpx

from ..budget import Ledger
from ..config import Config
from ..docref import DocRef, is_valid_id
from ..model import Feature, FeatureList, Variable, WriteResult
from .base import StaleVersion

# --- Unverified endpoint paths (Task 12) -----------------------------------
_FEATURE_ID = re.compile(r"[A-Za-z0-9_]{1,64}")  # real feature ids are not 24-hex (unverified, Task 12)


def _seg(value: str) -> str:
    if not is_valid_id(value):
        raise ValueError(f"invalid Onshape id: {value!r}")
    return quote(value, safe="")


def features_path(ref: DocRef) -> str:
    return (f"/api/v6/partstudios/d/{_seg(ref.document)}/w/{_seg(ref.workspace)}"
            f"/e/{_seg(ref.element)}/features")


def feature_update_path(ref: DocRef, feature_id: str) -> str:
    if not _FEATURE_ID.fullmatch(feature_id):
        raise ValueError(f"invalid feature id: {feature_id!r}")
    return f"{features_path(ref)}/featureid/{quote(feature_id, safe='')}"


def microversion_path(ref: DocRef) -> str:
    return f"/api/documents/d/{_seg(ref.document)}/w/{_seg(ref.workspace)}/currentmicroversion"


# --- Unverified JSON shape assumptions (Task 12) ---------------------------
SERIALIZATION_VERSION = "1.1.23"
VARIABLE_FEATURE_TYPE = "assign"
VARIABLE_NAME_PREFIX = "#"  # assign features are named "#<var>"
VARIABLE_EXPR_PARAM = "value"
STALE_STATUSES = (409, 412)
MAX_REDIRECTS = 5
JSON = "application/json"


def sign(method: str, path: str, query: str, content_type: str, date: str,
         nonce: str, access: str, secret: str) -> str:
    """Authorization header value for Onshape API-key auth."""
    to_sign = f"{method}\n{nonce}\n{date}\n{content_type}\n{path}\n{query}\n".lower()
    mac = hmac.new(secret.encode(), to_sign.encode(), hashlib.sha256).digest()
    return f"On {access}:HmacSHA256:{base64.b64encode(mac).decode()}"


def _parse_feature(f: dict, states: dict) -> Feature:
    fid = f.get("featureId", "")
    st = states.get(fid) or {}
    return Feature(
        id=fid,
        type=f.get("featureType", ""),
        name=f.get("name", ""),
        status=st.get("featureStatus", "OK"),
        params={p.get("parameterId", ""): p for p in f.get("parameters", [])},
    )


class OnshapeBackend:
    def __init__(self, config: Config, ledger: Ledger,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self.ledger = ledger
        self._client = httpx.Client(base_url=config.base_url, transport=transport,
                                    follow_redirects=False, timeout=30.0)

    def _creds(self) -> tuple[str, str]:
        access = os.environ.get(self.config.key_env)
        secret = os.environ.get(self.config.secret_env)
        for name, val in ((self.config.key_env, access), (self.config.secret_env, secret)):
            if not val:
                raise RuntimeError(f"missing credential: set environment variable {name}")
        return access, secret  # type: ignore[return-value]

    def _request(self, method: str, path: str, query: str = "", body: dict | None = None) -> httpx.Response:
        access, secret = self._creds()  # before charging: missing creds cost nothing
        base = urlsplit(self.config.base_url)
        base_host, base_scheme = base.netloc, base.scheme
        for _ in range(MAX_REDIRECTS + 1):
            self.ledger.charge(1, f"{method} {path}")
            date = formatdate(usegmt=True)
            nonce = secrets.token_urlsafe(18)
            headers = {
                "On-Nonce": nonce,
                "Date": date,
                "Content-Type": JSON,
                "Accept": JSON,
                "Authorization": sign(method, path, query, JSON, date, nonce, access, secret),
            }
            url = path + (f"?{query}" if query else "")
            resp = self._client.request(method, url, headers=headers,
                                        json=body if body is not None else None)
            if 300 <= resp.status_code < 400:
                if method != "GET":
                    raise RuntimeError(f"refusing redirect on {method} (write not replayed)")
                if "location" not in resp.headers:
                    raise RuntimeError(f"unexpected Onshape response: HTTP {resp.status_code} without Location")
                loc = urlsplit(resp.headers["location"])
                if loc.scheme and loc.scheme != base_scheme:
                    raise RuntimeError("refusing redirect with different scheme")
                if loc.netloc and loc.netloc != base_host:
                    raise RuntimeError("refusing redirect to other host")
                if not loc.path.startswith("/api/") or ".." in loc.path.split("/"):
                    raise RuntimeError("refusing redirect outside /api/")
                path, query = loc.path, loc.query
                continue
            return resp
        raise RuntimeError("too many redirects")

    def _ok(self, resp: httpx.Response, stale_ok: bool = False) -> dict:
        if stale_ok and resp.status_code in STALE_STATUSES:
            raise StaleVersion(f"document changed (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise RuntimeError(f"Onshape API error {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise RuntimeError(f"unexpected Onshape response: body is not JSON ({e})") from e
        if not isinstance(data, dict):
            raise RuntimeError("unexpected Onshape response: JSON body is not an object")
        return data

    def get_microversion(self, ref: DocRef) -> str:
        mv = self._ok(self._request("GET", microversion_path(ref))).get("microversionId")
        if not isinstance(mv, str) or not mv:
            raise RuntimeError("unexpected Onshape response: missing microversionId")
        return mv

    def list_features(self, ref: DocRef) -> FeatureList:
        data = self._ok(self._request("GET", features_path(ref)))
        states = data.get("featureStates", {})
        return FeatureList(data.get("sourceMicroversion", ""),
                           [_parse_feature(f, states) for f in data.get("features", [])])

    @staticmethod
    def _variables_of(data: dict) -> list[Variable]:
        out = []
        for f in data.get("features", []):
            if f.get("featureType") != VARIABLE_FEATURE_TYPE:
                continue
            expr = next((p.get("expression", "") for p in f.get("parameters", [])
                         if p.get("parameterId") == VARIABLE_EXPR_PARAM), "")
            out.append(Variable(f.get("name", "").removeprefix(VARIABLE_NAME_PREFIX), expr))
        return out

    def get_variables(self, ref: DocRef) -> list[Variable]:
        return self._variables_of(self._ok(self._request("GET", features_path(ref))))

    def add_feature(self, ref: DocRef, feature_json: dict, expected_microversion: str) -> WriteResult:
        body = {"feature": feature_json, "serializationVersion": SERIALIZATION_VERSION,
                "sourceMicroversion": expected_microversion}
        data = self._ok(self._request("POST", features_path(ref), body=body), stale_ok=True)
        raw = data.get("feature")
        if not isinstance(raw, dict) or not raw.get("featureId"):
            raise RuntimeError("unexpected Onshape response: add_feature reply missing feature/featureId")
        st = {raw["featureId"]: data.get("featureState") or {}}
        feat = _parse_feature(raw, st)
        mv = data.get("sourceMicroversion") or self.get_microversion(ref)
        return WriteResult(feat, mv)

    def set_variable(self, ref: DocRef, name: str, expression: str, expected_microversion: str) -> str:
        # TODO(Task 12): variable feature JSON and update endpoint are unverified.
        data = self._ok(self._request("GET", features_path(ref)))
        current = data.get("sourceMicroversion")
        if not current:
            raise RuntimeError("unexpected Onshape response: missing sourceMicroversion")
        if current != expected_microversion:
            raise StaleVersion(f"expected {expected_microversion}, current {current}")
        feature = {
            "btType": "BTMFeature-134",
            "featureType": VARIABLE_FEATURE_TYPE,
            "name": VARIABLE_NAME_PREFIX + name,
            "parameters": [{"btType": "BTMParameterQuantity-147", "parameterId": VARIABLE_EXPR_PARAM,
                            "expression": expression}],
        }
        existing = next((f for f in data.get("features", [])
                         if f.get("featureType") == VARIABLE_FEATURE_TYPE
                         and f.get("name") == VARIABLE_NAME_PREFIX + name), None)
        body = {"feature": feature, "serializationVersion": SERIALIZATION_VERSION,
                "sourceMicroversion": expected_microversion}
        if existing is not None:
            fid = existing.get("featureId")
            if not isinstance(fid, str) or not _FEATURE_ID.fullmatch(fid):
                raise RuntimeError("unexpected Onshape response: bad featureId on existing variable")
            path = feature_update_path(ref, fid)
        else:
            path = features_path(ref)
        resp = self._ok(self._request("POST", path, body=body), stale_ok=True)
        return resp.get("sourceMicroversion") or self.get_microversion(ref)
