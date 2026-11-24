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
import string
from email.utils import formatdate
from urllib.parse import quote, unquote, urlsplit

import httpx

from ..budget import BudgetExceeded, Ledger
from ..config import Config
from ..docref import DocRef, is_valid_id
from ..model import Feature, FeatureList, Variable, WriteResult
from ..variables import (VARIABLE_EXPR_PARAM, VARIABLE_FEATURE_TYPE, VARIABLE_NAME_PREFIX,
                         variables_from_features)
from .base import PostWriteError, StaleVersion

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
STALE_STATUSES = (409, 412)
MAX_REDIRECTS = 5
REDIRECT_STATUSES = (301, 302, 303, 307, 308)  # 304 etc. are not redirects
JSON = "application/json"


_NONCE_CHARS = string.ascii_letters + string.digits


def _nonce() -> str:
    """25 alphanumeric characters (the length/charset Onshape documents)."""
    return "".join(secrets.choice(_NONCE_CHARS) for _ in range(25))


def sign(method: str, path: str, query: str, content_type: str, date: str,
         nonce: str, access: str, secret: str) -> str:
    """Authorization header value for Onshape API-key auth."""
    to_sign = f"{method}\n{nonce}\n{date}\n{content_type}\n{path}\n{query}\n".lower()
    mac = hmac.new(secret.encode(), to_sign.encode(), hashlib.sha256).digest()
    return f"On {access}:HmacSHA256:{base64.b64encode(mac).decode()}"


def _s(x: object) -> str:
    return x if isinstance(x, str) else ""


def _flatten_param(p: dict) -> object:
    """Editable value of a parameter: its expression (quantities), else its scalar
    value (strings, enums, booleans, numbers); anything else (queries...) stays raw."""
    if isinstance(p.get("expression"), str):
        return p["expression"]
    val = p.get("value")
    if isinstance(val, (str, int, float, bool)):
        return val
    return p


def _parse_feature(f: dict, states: dict) -> Feature:
    try:
        fid = _s(f.get("featureId"))
        st = states.get(fid) or {}
        return Feature(
            id=fid,
            type=_s(f.get("featureType")),
            name=_s(f.get("name")),
            status=_s(st.get("featureStatus")) or "OK",
            params={_s(p.get("parameterId")): _flatten_param(p) for p in f.get("parameters", [])},
        )
    except (AttributeError, KeyError, TypeError) as e:
        raise RuntimeError(f"unexpected Onshape response: malformed feature ({type(e).__name__})") from e


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
            nonce = _nonce()
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
            if resp.status_code in REDIRECT_STATUSES:
                if method != "GET":
                    # The POST was sent and the server answered with a redirect: the
                    # write may have been applied, so the outcome is unknown.
                    raise PostWriteError(
                        f"HTTP {resp.status_code} redirect in reply to a {method} "
                        "(write not replayed)")
                if "location" not in resp.headers:
                    raise RuntimeError(f"unexpected Onshape response: HTTP {resp.status_code} without Location")
                loc = urlsplit(resp.headers["location"])
                if loc.scheme and loc.scheme != base_scheme:
                    raise RuntimeError("refusing redirect with different scheme")
                if loc.netloc and loc.netloc != base_host:
                    raise RuntimeError("refusing redirect to other host")
                if "%" in loc.path or "\\" in loc.path:
                    raise RuntimeError("refusing redirect with an encoded or odd path")
                if (not loc.path.startswith("/api/")
                        or ".." in unquote(loc.path).split("/")):
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

    def _write(self, path: str, body: dict, expected_microversion: str) -> dict:
        """POST a write body and return the parsed reply.

        Every write pins to the microversion Claude read and asks Onshape to
        reject (not silently apply) a skewed one. After the request is sent the
        outcome is uncertain, so transport errors, 5xx, unparsable replies and a
        skew report raise PostWriteError / StaleVersion(may_have_landed=True).
        """
        body = {**body, "sourceMicroversion": expected_microversion,
                "rejectMicroversionSkew": True}
        try:
            resp = self._request("POST", path, body=body)
        except httpx.HTTPError as e:
            raise PostWriteError(f"transport error during write ({type(e).__name__})") from e
        if resp.status_code in STALE_STATUSES:
            raise StaleVersion(f"document changed (HTTP {resp.status_code})")
        if 400 <= resp.status_code < 500:
            self._ok(resp)  # raises the plain API error: the request was rejected
        try:
            data = self._ok(resp)
        except RuntimeError as e:
            raise PostWriteError(f"unusable reply to a write ({e})") from e
        if data.get("microversionSkew") is True:
            raise StaleVersion(
                "Onshape reports the write was applied against a newer version "
                "(microversionSkew)",
                may_have_landed=True)
        return data

    def _new_microversion(self, ref: DocRef, data: dict, expected_microversion: str) -> tuple[str, bool]:
        """(microversion after a landed write, exact).

        exact is True only when the write reply itself named a version different
        from the request's. Otherwise the version comes from a SEPARATE GET, so a
        human edit between the POST and the GET would be adopted unseen: the
        caller must treat that version as not read. Any failure is PostWriteError."""
        mv = data.get("sourceMicroversion")
        try:
            if isinstance(mv, str) and mv and mv != expected_microversion:
                return mv, True
            # missing, or an echo of the request version (a landed write changes it)
            return self.get_microversion(ref), False
        except (BudgetExceeded, RuntimeError, httpx.HTTPError) as e:
            raise PostWriteError(
                f"the write succeeded but reading the new version failed ({e}); the write landed") from e

    def get_microversion(self, ref: DocRef) -> str:
        mv = self._ok(self._request("GET", microversion_path(ref))).get("microversionId")
        if not isinstance(mv, str) or not mv:
            raise RuntimeError("unexpected Onshape response: missing microversionId")
        return mv

    def list_features(self, ref: DocRef) -> FeatureList:
        data = self._ok(self._request("GET", features_path(ref)))
        mv = data.get("sourceMicroversion")
        if not isinstance(mv, str) or not mv:
            raise RuntimeError("unexpected Onshape response: missing sourceMicroversion")
        states = data.get("featureStates") or {}
        feats = data.get("features") or []
        if not isinstance(states, dict) or not isinstance(feats, list):
            raise RuntimeError("unexpected Onshape response: malformed feature list")
        return FeatureList(mv, [_parse_feature(f, states) for f in feats])

    def get_variables(self, ref: DocRef) -> list[Variable]:
        return variables_from_features(self.list_features(ref))

    def add_feature(self, ref: DocRef, feature_json: dict, expected_microversion: str) -> WriteResult:
        body = {"feature": feature_json, "serializationVersion": SERIALIZATION_VERSION}
        data = self._write(features_path(ref), body, expected_microversion)
        raw = data.get("feature")
        if not isinstance(raw, dict) or not raw.get("featureId"):
            raise PostWriteError(
                "unexpected Onshape response: add_feature reply missing feature/featureId")
        try:
            st = {raw["featureId"]: data.get("featureState") or {}}
            feat = _parse_feature(raw, st)
        except RuntimeError as e:
            raise PostWriteError(str(e)) from e
        mv, exact = self._new_microversion(ref, data, expected_microversion)
        return WriteResult(feat, mv, exact)

    def set_variable(self, ref: DocRef, name: str, expression: str, expected_microversion: str,
                     feature_id: str | None = None) -> WriteResult:
        # TODO(Task 12): variable feature JSON and update endpoint are unverified.
        if feature_id is not None and not _FEATURE_ID.fullmatch(feature_id):
            raise RuntimeError("unexpected Onshape response: bad featureId on existing variable")
        feature = {
            "btType": "BTMFeature-134",
            "featureType": VARIABLE_FEATURE_TYPE,
            "name": VARIABLE_NAME_PREFIX + name,
            "parameters": [{"btType": "BTMParameterQuantity-147", "parameterId": VARIABLE_EXPR_PARAM,
                            "expression": expression}],
        }
        body = {"feature": feature, "serializationVersion": SERIALIZATION_VERSION}
        path = features_path(ref) if feature_id is None else feature_update_path(ref, feature_id)
        resp = self._write(path, body, expected_microversion)
        feat = self._variable_feature(resp, feature_id, name, expression)
        mv, exact = self._new_microversion(ref, resp, expected_microversion)
        return WriteResult(feat, mv, exact)

    @staticmethod
    def _variable_feature(resp: dict, feature_id: str | None, name: str, expression: str) -> Feature:
        """The variable feature the write reply describes (id and regeneration
        status); when the reply carries none, the id is the one that was updated
        (or "" for a new variable) and the status is "" (not reported)."""
        raw = resp.get("feature")
        if isinstance(raw, dict) and isinstance(raw.get("featureId"), str) \
                and _FEATURE_ID.fullmatch(raw["featureId"]):
            try:
                parsed = _parse_feature(raw, {raw["featureId"]: resp.get("featureState") or {}})
                if isinstance(resp.get("featureState"), dict):
                    return parsed
                return Feature(parsed.id, parsed.type, parsed.name, "", parsed.params)
            except RuntimeError:
                pass
        return Feature(feature_id or "", VARIABLE_FEATURE_TYPE, VARIABLE_NAME_PREFIX + name, "",
                       {VARIABLE_EXPR_PARAM: expression})
