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
    from onshape_cad_mcp.backends.base import PostWriteError
    b, _ = make(tmp_path, lambda r: httpx.Response(200, json=body))
    with pytest.raises(PostWriteError, match="unexpected Onshape response"):
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


def test_set_variable_costs_only_the_post(tmp_path):
    """No pre-read GET any more (I6): staleness is enforced by the pinned write."""
    methods = []

    def handler(req):
        methods.append(req.method)
        return httpx.Response(200, json={"sourceMicroversion": "mv2"})
    b, ledger = make(tmp_path, handler)
    assert b.set_variable(REF, "w", "1 mm", "mv1") == "mv2"
    assert methods == ["POST"] and ledger.used == 1


@pytest.mark.parametrize("status", [409, 412])
def test_set_variable_write_stale_maps(tmp_path, status):
    def handler(req):
        return httpx.Response(status, json={})
    b, _ = make(tmp_path, handler)
    with pytest.raises(StaleVersion):
        b.set_variable(REF, "w", "1 mm", "mv1")


def test_set_variable_create_vs_update_path(tmp_path):
    posts = []

    def handler(req):
        posts.append(req.url.path)
        return httpx.Response(200, json={"sourceMicroversion": "mv9"})
    b, _ = make(tmp_path, handler)
    assert b.set_variable(REF, "w", "1 mm", "mv1") == "mv9"
    b.set_variable(REF, "w", "2 mm", "mv1", feature_id="FabC_12")
    assert posts[0].endswith("/features")
    assert posts[1].endswith("/features/featureid/FabC_12")


def test_set_variable_rejects_malicious_feature_id(tmp_path):
    calls = []
    b, ledger = make(tmp_path, lambda r: calls.append(r) or httpx.Response(200, json={}))
    with pytest.raises(RuntimeError, match="bad featureId"):
        b.set_variable(REF, "w", "2 mm", "mv1", feature_id="../../x")
    assert calls == [] and ledger.used == 0


# --- final-review fixes: C1 (version pinning on the wire) and I2 (post-write failures) ---

def _post_capture(tmp_path, reply, feats=()):
    bodies = []

    def handler(req):
        if req.method == "POST":
            bodies.append(json.loads(req.content))
            return httpx.Response(200, json=reply)
        if req.url.path.endswith("currentmicroversion"):
            return httpx.Response(200, json={"microversionId": "mv-after"})
        return httpx.Response(200, json=_vars_get("mv1", feats))
    b, ledger = make(tmp_path, handler)
    return b, bodies, ledger


ADD_REPLY = {"feature": {"featureId": "F2", "featureType": "newSketch", "name": "S"},
             "featureState": {"featureStatus": "OK"}, "sourceMicroversion": "mv2"}


def test_add_feature_body_pins_and_rejects_skew(tmp_path):
    b, bodies, _ = _post_capture(tmp_path, ADD_REPLY)
    b.add_feature(REF, {"type": "x"}, "mv1")
    assert bodies[0]["rejectMicroversionSkew"] is True
    assert bodies[0]["sourceMicroversion"] == "mv1"


def test_set_variable_body_pins_and_rejects_skew(tmp_path):
    b, bodies, _ = _post_capture(tmp_path, {"sourceMicroversion": "mv2"})
    b.set_variable(REF, "w", "1 mm", "mv1")
    assert bodies[0]["rejectMicroversionSkew"] is True
    assert bodies[0]["sourceMicroversion"] == "mv1"


def test_add_feature_skew_reply_raises_loudly(tmp_path):
    b, _, _ = _post_capture(tmp_path, {**ADD_REPLY, "microversionSkew": True})
    with pytest.raises(StaleVersion, match="newer version") as ei:
        b.add_feature(REF, {"type": "x"}, "mv1")
    assert ei.value.may_have_landed is True


def test_set_variable_skew_reply_raises_loudly(tmp_path):
    b, _, _ = _post_capture(tmp_path, {"sourceMicroversion": "mv2", "microversionSkew": True})
    with pytest.raises(StaleVersion, match="newer version") as ei:
        b.set_variable(REF, "w", "1 mm", "mv1")
    assert ei.value.may_have_landed is True


def test_plain_409_is_stale_but_not_landed(tmp_path):
    b, _ = make(tmp_path, lambda r: httpx.Response(409, json={}))
    with pytest.raises(StaleVersion) as ei:
        b.add_feature(REF, {"type": "x"}, "mv1")
    assert ei.value.may_have_landed is False


def test_post_transport_error_is_post_write_error(tmp_path):
    from onshape_cad_mcp.backends.base import PostWriteError

    def handler(req):
        raise httpx.ReadTimeout("slow")
    b, _ = make(tmp_path, handler)
    with pytest.raises(PostWriteError, match="may have landed"):
        b.add_feature(REF, {"type": "x"}, "mv1")


def test_post_5xx_is_post_write_error_but_4xx_is_plain(tmp_path):
    from onshape_cad_mcp.backends.base import PostWriteError
    b, _ = make(tmp_path, lambda r: httpx.Response(502, text="bad gateway"))
    with pytest.raises(PostWriteError):
        b.add_feature(REF, {"type": "x"}, "mv1")
    b, _ = make(tmp_path, lambda r: httpx.Response(400, text="nope"))
    with pytest.raises(RuntimeError, match="400") as ei:
        b.add_feature(REF, {"type": "x"}, "mv1")
    assert not isinstance(ei.value, PostWriteError)


def test_add_feature_followup_microversion_failure_is_post_write_error(tmp_path):
    from onshape_cad_mcp.backends.base import PostWriteError
    reply = {k: v for k, v in ADD_REPLY.items() if k != "sourceMicroversion"}

    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json=reply)
        return httpx.Response(200, content=b"not json")
    b, _ = make(tmp_path, handler)
    with pytest.raises(PostWriteError, match="landed"):
        b.add_feature(REF, {"type": "x"}, "mv1")


def test_add_feature_followup_budget_failure_is_post_write_error(tmp_path):
    from onshape_cad_mcp.backends.base import PostWriteError
    reply = {k: v for k, v in ADD_REPLY.items() if k != "sourceMicroversion"}
    b, _ = make(tmp_path, lambda r: httpx.Response(200, json=reply), budget=1)
    with pytest.raises(PostWriteError, match="landed"):
        b.add_feature(REF, {"type": "x"}, "mv1")


def test_set_variable_followup_failure_is_post_write_error(tmp_path):
    from onshape_cad_mcp.backends.base import PostWriteError

    def handler(req):
        if req.method == "GET" and req.url.path.endswith("/features"):
            return httpx.Response(200, json=_vars_get())
        if req.method == "POST":
            return httpx.Response(200, json={})
        raise httpx.ConnectError("down")
    b, _ = make(tmp_path, handler)
    with pytest.raises(PostWriteError, match="landed"):
        b.set_variable(REF, "w", "1 mm", "mv1")


def test_echoed_request_version_triggers_refetch(tmp_path):
    b, _, ledger = _post_capture(tmp_path, {**ADD_REPLY, "sourceMicroversion": "mv1"})
    assert b.add_feature(REF, {"type": "x"}, "mv1").microversion == "mv-after"


def test_nonce_is_25_alphanumerics(tmp_path):
    seen = []

    def handler(req):
        seen.append(req.headers["On-Nonce"])
        return httpx.Response(200, json={"microversionId": "m"})
    b, _ = make(tmp_path, handler)
    b.get_microversion(REF)
    assert len(seen[0]) == 25 and seen[0].isalnum()


# --- final-review fixes: I3 (param shapes, response parsing) ---

REAL_FEATURES = {"sourceMicroversion": "mv5", "featureStates": {"E1": {"featureStatus": "OK"}}, "features": [
    {"featureId": "E1", "featureType": "extrude", "name": "Base extrude", "parameters": [
        {"btType": "BTMParameterQuantity-147", "parameterId": "depth", "expression": "10 mm", "isInteger": False},
        {"btType": "BTMParameterEnum-145", "parameterId": "endBound", "value": "BLIND", "enumName": "BoundingType"},
        {"btType": "BTMParameterBoolean-144", "parameterId": "flip", "value": True},
        {"btType": "BTMParameterString-149", "parameterId": "note", "value": "hi"},
        {"btType": "BTMParameterQueryList-148", "parameterId": "entities", "queries": [{"x": 1}]}]},
]}


def test_real_param_shapes_are_flattened_and_printed(tmp_path):
    from onshape_cad_mcp.outline import render_outline
    b, _ = make(tmp_path, lambda r: httpx.Response(200, json=REAL_FEATURES))
    fl = b.list_features(REF)
    p = fl.features[0].params
    assert p["depth"] == "10 mm" and p["endBound"] == "BLIND" and p["flip"] is True and p["note"] == "hi"
    assert isinstance(p["entities"], dict)  # queries stay raw
    out = render_outline(fl, [])
    assert "depth=10 mm" in out and "endBound=BLIND" in out and "note=hi" in out


def test_variables_from_real_shaped_assign_feature(tmp_path):
    body = {"sourceMicroversion": "mv1", "features": [
        {"featureId": "A", "featureType": "assign", "name": "#w", "parameters": [
            {"btType": "BTMParameterQuantity-147", "parameterId": "value", "expression": "10 mm"}]}]}
    b, _ = make(tmp_path, lambda r: httpx.Response(200, json=body))
    assert [(v.name, v.expression) for v in b.get_variables(REF)] == [("w", "10 mm")]


def test_missing_source_microversion_in_feature_list_raises(tmp_path):
    b, _ = make(tmp_path, lambda r: httpx.Response(200, json={"features": []}))
    with pytest.raises(RuntimeError, match="unexpected Onshape response"):
        b.list_features(REF)


@pytest.mark.parametrize("body", [
    {"sourceMicroversion": "m", "features": ["oops"]},
    {"sourceMicroversion": "m", "features": [{"featureId": "A", "parameters": [1]}]},
    {"sourceMicroversion": "m", "features": [{"featureId": "A", "parameters": "x"}]},
    {"sourceMicroversion": "m", "features": 5},
    {"sourceMicroversion": "m", "features": [], "featureStates": [1]},
])
def test_malformed_feature_list_is_wrapped(tmp_path, body):
    b, _ = make(tmp_path, lambda r: httpx.Response(200, json=body))
    with pytest.raises(RuntimeError, match="unexpected Onshape response"):
        b.list_features(REF)
