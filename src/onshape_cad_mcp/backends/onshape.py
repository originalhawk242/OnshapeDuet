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
import secrets
from email.utils import formatdate
from urllib.parse import urlsplit

import httpx

from ..budget import Ledger
from ..config import Config
from ..docref import DocRef
from ..model import Feature, FeatureList, Variable, WriteResult
from .base import StaleVersion

# --- Unverified endpoint paths (Task 12) -----------------------------------
def features_path(ref: DocRef) -> str:
    return f"/api/v6/partstudios/d/{ref.document}/w/{ref.workspace}/e/{ref.element}/features"


def feature_update_path(ref: DocRef, feature_id: str) -> str:
    return f"{features_path(ref)}/featureid/{feature_id}"


def microversion_path(ref: DocRef) -> str:
    return f"/api/documents/d/{ref.document}/w/{ref.workspace}/currentmicroversion"


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
        base_host = urlsplit(self.config.base_url).netloc
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
            if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                loc = urlsplit(resp.headers["location"])
                if loc.netloc and loc.netloc != base_host:
                    raise RuntimeError("refusing redirect to other host")
                path, query = loc.path, loc.query
                if resp.status_code in (301, 302, 303):
                    method, body = "GET", None
                continue
            return resp
        raise RuntimeError("too many redirects")

    def _ok(self, resp: httpx.Response, stale_ok: bool = False) -> dict:
        if stale_ok and resp.status_code in STALE_STATUSES:
            raise StaleVersion(f"document changed (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise RuntimeError(f"Onshape API error {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def get_microversion(self, ref: DocRef) -> str:
        return self._ok(self._request("GET", microversion_path(ref)))["microversionId"]

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
        st = {data.get("feature", {}).get("featureId", ""): data.get("featureState") or {}}
        feat = _parse_feature(data.get("feature", {}), st)
        mv = data.get("sourceMicroversion") or self.get_microversion(ref)
        return WriteResult(feat, mv)

    def set_variable(self, ref: DocRef, name: str, expression: str, expected_microversion: str) -> str:
        # TODO(Task 12): variable feature JSON and update endpoint are unverified.
        data = self._ok(self._request("GET", features_path(ref)))
        if data.get("sourceMicroversion") != expected_microversion:
            raise StaleVersion(f"expected {expected_microversion}, current {data.get('sourceMicroversion')}")
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
        path = feature_update_path(ref, existing["featureId"]) if existing else features_path(ref)
        resp = self._ok(self._request("POST", path, body=body), stale_ok=True)
        return resp.get("sourceMicroversion") or self.get_microversion(ref)
