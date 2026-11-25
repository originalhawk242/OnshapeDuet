from collections import Counter

import pytest

from onshape_duet.backends.base import StaleVersion
from onshape_duet.backends.fake import FakeBackend
from onshape_duet.budget import BudgetExceeded, Ledger
from onshape_duet.docref import DocRef
from onshape_duet.model import Feature, FeatureList, Variable, WriteResult

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
    assert new.microversion != res.microversion and b.get_microversion(REF) == new.microversion
    assert new.feature.name == "#name = #value" and new.feature.params["name"] == "w"


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


def test_no_ledger_means_no_charge():
    b = FakeBackend()
    for _ in range(5):
        b.get_microversion(REF)
    assert b.ledger is None and b.calls["get_microversion"] == 5


def test_budget_is_enforced_per_backend_call(tmp_path):
    led = Ledger(tmp_path / "l.json", 1)
    b = FakeBackend(led)
    b.get_microversion(REF)
    with pytest.raises(BudgetExceeded):
        b.list_features(REF)


def test_state_unchanged_after_stale_write():
    b = FakeBackend()
    mv = b.get_microversion(REF)
    b.simulate_human_edit(REF)
    after_edit = b.get_microversion(REF)
    with pytest.raises(StaleVersion):
        b.add_feature(REF, FEAT, mv)
    with pytest.raises(StaleVersion):
        b.set_variable(REF, "w", "1 mm", mv)
    assert b.list_features(REF).features == [] and b.get_variables(REF) == []
    assert b.get_microversion(REF) == after_edit


def test_stale_write_is_still_charged(tmp_path):
    led = Ledger(tmp_path / "l.json", 100)
    b = FakeBackend(led)
    mv = b.get_microversion(REF)
    b.simulate_human_edit(REF)
    before = led.used
    with pytest.raises(StaleVersion):
        b.add_feature(REF, FEAT, mv)
    assert led.used == before + 1 and b.calls["add_feature"] == 1


def test_ledger_rejection_does_not_mutate_or_count(tmp_path):
    led = Ledger(tmp_path / "l.json", 1)
    b = FakeBackend(led)
    mv = b.get_microversion(REF)  # uses the whole budget
    with pytest.raises(BudgetExceeded):
        b.add_feature(REF, FEAT, mv)
    with pytest.raises(BudgetExceeded):
        b.set_variable(REF, "w", "1 mm", mv)
    assert b.calls["add_feature"] == 0 and b.calls["set_variable"] == 0
    assert b._doc(REF).features == [] and b._doc(REF).version == 0


def test_params_are_deep_copied_in_and_out():
    b = FakeBackend()
    params = {"a": {"b": 1}}
    res = b.add_feature(REF, {"type": "sketch", "name": "S", "params": params}, b.get_microversion(REF))
    params["a"]["b"] = 99                       # caller mutates its input
    res.feature.params["a"]["b"] = 77           # caller mutates the returned feature
    listed = b.list_features(REF)
    assert listed.features[0].params == {"a": {"b": 1}}
    listed.features[0].params["a"]["b"] = 55    # and the listed one
    assert b.list_features(REF).features[0].params == {"a": {"b": 1}}


def test_feature_ids_do_not_collide_after_deletes():
    b = FakeBackend()
    mv = b.get_microversion(REF)
    r1 = b.add_feature(REF, FEAT, mv)
    r2 = b.add_feature(REF, FEAT, r1.microversion)
    doc = b._doc(REF)
    doc.features.remove(doc.features[0])        # delete the first feature
    r3 = b.add_feature(REF, FEAT, r2.microversion)
    b.set_variable(REF, "w", "1 mm", r3.microversion)
    ids = [f.id for f in b.list_features(REF).features]
    assert len(ids) == len(set(ids)) == 3 and r1.feature.id not in ids


def test_docref_hashable():
    assert {REF: 1}[DocRef("a"*24, "b"*24, "c"*24)] == 1


def test_add_feature_flattens_parameters():
    fb = FakeBackend()
    fb.add_feature(REF, {"featureType": "extrude", "name": "Body", "parameters": [
        {"btType": "BTMParameterQuantity-147", "parameterId": "depth", "expression": "#depth"},
        {"btType": "BTMParameterEnum-145", "parameterId": "operationType", "value": "NEW"}]}, "mv0")
    f = fb.list_features(REF).features[0]
    assert (f.type, f.params) == ("extrude", {"depth": "#depth", "operationType": "NEW"})
