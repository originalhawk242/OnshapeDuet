import base64
import hashlib
import hmac
import json

import httpx
import pytest

from onshape_cad_mcp.backends.base import StaleVersion
from onshape_cad_mcp.backends.onshape import OnshapeBackend, sign
from onshape_cad_mcp.budget import BudgetExceeded, Ledger
from onshape_cad_mcp.config import Config
from onshape_cad_mcp.docref import DocRef

REF = DocRef("a" * 24, "b" * 24, "c" * 24)


@pytest.fixture(autouse=True)
def creds(monkeypatch):
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "key")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "secret")


def make(tmp_path, handler, budget=10):
    cfg = Config(budget=budget, ledger_path=tmp_path / "l.json")
    ledger = Ledger(cfg.ledger_path, budget)
    return OnshapeBackend(cfg, ledger, httpx.MockTransport(handler)), ledger


def test_sign_is_deterministic_for_fixed_inputs():
    args = ("GET", "/api/x", "a=b", "application/json", "Mon, 01 Jan 2024 00:00:00 GMT", "nonce", "key", "secret")
    a = sign(*args)
    assert a.startswith("On key:HmacSHA256:")
    assert a == sign(*args)
    assert a != sign("POST", *args[1:])


def test_every_request_charges_before_sending(tmp_path):
    seen = []

    def handler(req):
        seen.append(ledger.used)
        assert req.headers["Authorization"].startswith("On key:HmacSHA256:")
        return httpx.Response(200, json={"microversionId": "mv9"})

    b, ledger = make(tmp_path, handler)
    assert b.get_microversion(REF) == "mv9"
    assert seen == [1]


def test_budget_zero_sends_nothing(tmp_path):
    calls = []
    b, _ = make(tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={}), budget=0)
    with pytest.raises(BudgetExceeded):
        b.get_microversion(REF)
    assert calls == []


def test_redirect_hop_is_charged(tmp_path):
    def handler(req):
        if req.url.path.endswith("currentmicroversion"):
            return httpx.Response(302, headers={"Location": "/api/moved"})
        return httpx.Response(200, json={"microversionId": "mv2"})

    b, ledger = make(tmp_path, handler)
    assert b.get_microversion(REF) == "mv2"
    assert ledger.used == 2


def test_redirect_to_other_host_refused(tmp_path):
    b, _ = make(tmp_path, lambda r: httpx.Response(302, headers={"Location": "https://evil.example/x"}))
    with pytest.raises(RuntimeError, match="other host"):
        b.get_microversion(REF)


def test_missing_credentials_charge_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("ONSHAPE_SECRET_KEY")
    b, ledger = make(tmp_path, lambda r: httpx.Response(200, json={}))
    with pytest.raises(RuntimeError, match="ONSHAPE_SECRET_KEY"):
        b.get_microversion(REF)
    assert ledger.used == 0


@pytest.mark.parametrize("status", [409, 412])
def test_stale_write_maps_to_StaleVersion(tmp_path, status):
    b, _ = make(tmp_path, lambda r: httpx.Response(status, json={"message": "stale"}))
    with pytest.raises(StaleVersion):
        b.add_feature(REF, {"type": "x"}, "mv1")


def test_list_features_parses(tmp_path):
    body = {
        "sourceMicroversion": "mv5",
        "features": [{"featureId": "F1", "featureType": "newSketch", "name": "S", "parameters": []}],
        "featureStates": {"F1": {"featureStatus": "OK"}},
    }
    b, _ = make(tmp_path, lambda r: httpx.Response(200, json=body))
    fl = b.list_features(REF)
    assert fl.microversion == "mv5"
    assert (fl.features[0].id, fl.features[0].type, fl.features[0].status) == ("F1", "newSketch", "OK")


def test_add_feature_posts_and_returns_result(tmp_path):
    def handler(req):
        if req.method == "POST":
            sent = json.loads(req.content)
            assert sent["sourceMicroversion"] == "mv1"
            return httpx.Response(200, json={
                "feature": {"featureId": "F2", "featureType": "extrude", "name": "E"},
                "featureState": {"featureStatus": "OK"},
            })
        return httpx.Response(200, json={"microversionId": "mv2"})

    b, _ = make(tmp_path, handler)
    r = b.add_feature(REF, {"type": "extrude"}, "mv1")
    assert r.feature.id == "F2" and r.microversion == "mv2"


def test_get_variables_from_assign_features(tmp_path):
    body = {"sourceMicroversion": "mv1", "featureStates": {}, "features": [
        {"featureId": "A", "featureType": "assign", "name": "#w", "parameters": [
            {"parameterId": "value", "expression": "10 mm"}]},
        {"featureId": "B", "featureType": "extrude", "name": "E", "parameters": []},
    ]}
    b, _ = make(tmp_path, lambda r: httpx.Response(200, json=body))
    vs = b.get_variables(REF)
    assert [(v.name, v.expression) for v in vs] == [("w", "10 mm")]


def test_sign_matches_independent_hmac():
    date, nonce = "Mon, 01 Jan 2024 00:00:00 GMT", "AbC123"
    to_sign = f"GET\n{nonce}\n{date}\napplication/json\n/api/Foo\nq=Z\n".lower()
    mac = hmac.new(b"sEcret", to_sign.encode(), hashlib.sha256).digest()
    expected = "On KEY:HmacSHA256:" + base64.b64encode(mac).decode()
    assert sign("GET", "/api/Foo", "q=Z", "application/json", date, nonce, "KEY", "sEcret") == expected


def test_redirected_request_signed_with_redirected_path(tmp_path):
    seen = []

    def handler(req):
        seen.append(req)
        if len(seen) == 1:
            return httpx.Response(302, headers={"Location": "/api/moved?x=Y"})
        return httpx.Response(200, json={"microversionId": "m"})

    b, _ = make(tmp_path, handler)
    b.get_microversion(REF)
    r = seen[1]
    assert (r.url.path, r.url.query) == ("/api/moved", b"x=Y")
    expected = sign("GET", "/api/moved", "x=Y", "application/json", r.headers["Date"],
                    r.headers["On-Nonce"], "key", "secret")
    assert r.headers["Authorization"] == expected


def test_redirect_scheme_downgrade_refused(tmp_path):
    b, _ = make(tmp_path, lambda r: httpx.Response(302, headers={"Location": "http://cad.onshape.com/api/x"}))
    with pytest.raises(RuntimeError, match="scheme"):
        b.get_microversion(REF)


def test_redirect_outside_api_refused(tmp_path):
    b, _ = make(tmp_path, lambda r: httpx.Response(302, headers={"Location": "/login"}))
    with pytest.raises(RuntimeError, match="/api/"):
        b.get_microversion(REF)


def test_redirect_without_location_raises(tmp_path):
    b, _ = make(tmp_path, lambda r: httpx.Response(302))
    with pytest.raises(RuntimeError, match="unexpected Onshape response"):
        b.get_microversion(REF)


def test_redirect_on_write_raises(tmp_path):
    b, ledger = make(tmp_path, lambda r: httpx.Response(307, headers={"Location": "/api/x"}))
    with pytest.raises(RuntimeError, match="POST"):
        b.add_feature(REF, {"type": "x"}, "mv1")
    assert ledger.used == 1


@pytest.mark.parametrize("resp", [
    httpx.Response(200, content=b""),
    httpx.Response(200, content=b"not json"),
    httpx.Response(200, json=[1]),
    httpx.Response(200, json={}),
])
def test_bad_microversion_body_is_clear_error(tmp_path, resp):
    b, _ = make(tmp_path, lambda r: resp)
    with pytest.raises(RuntimeError, match="unexpected Onshape response"):
        b.get_microversion(REF)


@pytest.mark.parametrize("body", [{}, {"feature": {}}, {"feature": {"name": "x"}}])
def test_add_feature_missing_feature_id(tmp_path, body):
    b, _ = make(tmp_path, lambda r: httpx.Response(200, json=body))
    with pytest.raises(RuntimeError, match="unexpected Onshape response"):
        b.add_feature(REF, {"type": "x"}, "mv1")


def test_ref_with_bad_component_cannot_be_built():
    for bad in ("../x", "a/b", "q?x", "%2f"):
        with pytest.raises(ValueError):
            DocRef(bad, "b" * 24, "c" * 24)


def test_path_builders_reject_forged_ref():
    from onshape_cad_mcp.backends.onshape import features_path, feature_update_path
    forged = object.__new__(DocRef)
    object.__setattr__(forged, "document", "../..")
    object.__setattr__(forged, "workspace", "b" * 24)
    object.__setattr__(forged, "element", "c" * 24)
    with pytest.raises(ValueError):
        features_path(forged)
    with pytest.raises(ValueError):
        feature_update_path(REF, "../x")


def _vars_get(mv="mv1", feats=()):
    return {"sourceMicroversion": mv, "featureStates": {}, "features": list(feats)}


def test_set_variable_client_side_stale(tmp_path):
    calls = []
    def handler(req):
        calls.append(req.method)
        return httpx.Response(200, json=_vars_get("mv2"))
    b, _ = make(tmp_path, handler)
    with pytest.raises(StaleVersion):
        b.set_variable(REF, "w", "1 mm", "mv1")
    assert calls == ["GET"]


def test_set_variable_missing_source_microversion_is_not_stale(tmp_path):
    b, _ = make(tmp_path, lambda r: httpx.Response(200, json={"features": []}))
    with pytest.raises(RuntimeError, match="unexpected Onshape response") as ei:
        b.set_variable(REF, "w", "1 mm", "mv1")
    assert not isinstance(ei.value, StaleVersion)


@pytest.mark.parametrize("status", [409, 412])
def test_set_variable_write_stale_maps(tmp_path, status):
    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json=_vars_get())
        return httpx.Response(status, json={})
    b, _ = make(tmp_path, handler)
    with pytest.raises(StaleVersion):
        b.set_variable(REF, "w", "1 mm", "mv1")


def test_set_variable_create_vs_update_path(tmp_path):
    posts = []
    def make_handler(feats):
        def handler(req):
            if req.method == "GET":
                return httpx.Response(200, json=_vars_get(feats=feats))
            posts.append(req.url.path)
            return httpx.Response(200, json={"sourceMicroversion": "mv9"})
        return handler
    b, _ = make(tmp_path, make_handler([]))
    assert b.set_variable(REF, "w", "1 mm", "mv1") == "mv9"
    existing = [{"featureId": "FabC_12", "featureType": "assign", "name": "#w", "parameters": []}]
    b, _ = make(tmp_path, make_handler(existing))
    b.set_variable(REF, "w", "2 mm", "mv1")
    assert posts[0].endswith("/features")
    assert posts[1].endswith("/features/featureid/FabC_12")


def test_set_variable_rejects_malicious_existing_feature_id(tmp_path):
    bad = [{"featureId": "../../x", "featureType": "assign", "name": "#w", "parameters": []}]
    b, _ = make(tmp_path, lambda r: httpx.Response(200, json=_vars_get(feats=bad)))
    with pytest.raises(RuntimeError, match="bad featureId"):
        b.set_variable(REF, "w", "2 mm", "mv1")
