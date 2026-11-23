import json

import httpx
import pytest

from onshape_cad_mcp.backends.base import StaleVersion
from onshape_cad_mcp.backends.onshape import OnshapeBackend, sign
from onshape_cad_mcp.budget import BudgetExceeded, Ledger
from onshape_cad_mcp.config import Config
from onshape_cad_mcp.docref import DocRef

REF = DocRef("d1", "w1", "e1")


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
