from collections import Counter

import pytest

from onshape_cad_mcp.backends.base import StaleVersion
from onshape_cad_mcp.backends.fake import FakeBackend
from onshape_cad_mcp.budget import BudgetExceeded, Ledger
from onshape_cad_mcp.docref import DocRef
from onshape_cad_mcp.model import Feature, FeatureList, Variable, WriteResult

REF = DocRef("a"*24, "b"*24, "c"*24)
FEAT = {"type": "sketch", "name": "S1", "status": "OK", "params": {"a": 1}}


def test_write_bumps_microversion():
    b = FakeBackend()
    mv = b.get_microversion(REF)
    res = b.add_feature(REF, FEAT, mv)
    assert isinstance(res, WriteResult)
    assert res.microversion != mv
    assert b.get_microversion(REF) == res.microversion
    new = b.set_variable(REF, "w", "10 mm", res.microversion)
    assert new != res.microversion and b.get_microversion(REF) == new


def test_stale_write_raises():
    b = FakeBackend()
    mv = b.get_microversion(REF)
    b.simulate_human_edit(REF)
    with pytest.raises(StaleVersion):
        b.add_feature(REF, FEAT, mv)
    with pytest.raises(StaleVersion):
        b.set_variable(REF, "w", "1", mv)


def test_added_feature_is_listed():
    b = FakeBackend()
    res = b.add_feature(REF, FEAT, b.get_microversion(REF))
    fl = b.list_features(REF)
    assert isinstance(fl, FeatureList)
    assert fl.microversion == res.microversion
    assert fl.features == [res.feature]
    assert isinstance(res.feature, Feature) and res.feature.name == "S1"
    b.set_variable(REF, "w", "10 mm", fl.microversion)
    assert b.get_variables(REF) == [Variable("w", "10 mm")]


def test_calls_counter():
    b = FakeBackend()
    b.get_microversion(REF)
    b.get_microversion(REF)
    b.list_features(REF)
    assert isinstance(b.calls, Counter)
    assert b.calls["get_microversion"] == 2 and b.calls["list_features"] == 1


def test_ledger_charged_per_call(tmp_path):
    led = Ledger(tmp_path / "l.json", 100)
    b = FakeBackend(led)
    mv = b.get_microversion(REF)
    b.list_features(REF)
    b.get_variables(REF)
    b.add_feature(REF, FEAT, mv)
    assert led.used == 4


def test_no_ledger_no_charge_and_budget_enforced(tmp_path):
    led = Ledger(tmp_path / "l.json", 1)
    b = FakeBackend(led)
    b.get_microversion(REF)
    with pytest.raises(BudgetExceeded):
        b.list_features(REF)


def test_docref_hashable():
    assert {REF: 1}[DocRef("a"*24, "b"*24, "c"*24)] == 1
