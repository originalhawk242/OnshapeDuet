import json
from pathlib import Path

import httpx
import pytest

from onshape_duet.backends.fake import FakeBackend
from onshape_duet.backends.onshape import OnshapeBackend
from onshape_duet.budget import Ledger
from onshape_duet.cache import DocCache
from onshape_duet.config import Config
from onshape_duet.docref import DocRef
from onshape_duet.generic.specs import parse_feature_specs
from onshape_duet.tools import Context

REF = DocRef("a" * 24, "b" * 24, "c" * 24)
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "featurespecs_UNVERIFIED.json").read_text())
CUSTOM_TYPE = "e123::m456"


def fake_ctx(tmp_path):
    cfg = Config(budget=50, ledger_path=tmp_path / "l.json")
    ledger = Ledger(cfg.ledger_path, 50)
    backend = FakeBackend(ledger)
    backend.specs_json = FIXTURE
    return Context(backend, DocCache(backend), ledger, cfg), backend


def by_type():
    return {s.type: s for s in parse_feature_specs(FIXTURE)}


def test_parse_native_only_and_sorted():
    specs = parse_feature_specs(FIXTURE)
    types = [s.type for s in specs]
    assert types == sorted(types) and all("::" not in t for t in types)
    assert not any(s.type == CUSTOM_TYPE for s in specs)
    assert len(specs) == 6


def test_param_kinds_and_fields():
    ex = by_type()["extrude"]
    d = ex.param("depth")
    assert (d.kind, d.quantity_type, d.units, d.conditional) == ("quantity", "LENGTH", "mm", True)
    assert d.condition == "endBound == BLIND" and d.default == "25 mm"
    e = ex.param("endBound")
    assert e.kind == "enum" and e.options == ("BLIND", "THROUGH_ALL", "UP_TO_NEXT")
    assert e.enum_name == "BoundingType" and not e.conditional
    assert ex.param("entities").kind == "query"
    assert ex.param("oppositeDirection").kind == "boolean"
    assert ex.param("oppositeDirection").default is False
    assert ex.param("nope") is None
    pat = by_type()["linearPattern"]
    assert pat.param("instanceFunction").kind == "feature_list"
    assert pat.param("instanceCount").quantity_type == "INTEGER"


def test_unsupported_kind_and_array_items():
    h = by_type()["holeLike"]
    assert h.param("tablePath").kind == "unsupported"
    arr = h.param("holes")
    assert arr.kind == "array"
    assert [(i.id, i.kind) for i in arr.items] == [("diameter", "quantity"), ("label", "string")]
    assert arr.items[1].default == "H1"


def test_malformed_specs_raise():
    with pytest.raises(RuntimeError, match="unexpected Onshape response"):
        parse_feature_specs({"featureSpecs": "x"})
    with pytest.raises(RuntimeError):
        parse_feature_specs({"featureSpecs": [{"featureType": "a", "parameters": [{"btType": "x"}]}]})


def test_specs_cached_per_ref(tmp_path):
    ctx, backend = fake_ctx(tmp_path)
    a = ctx.feature_specs(REF)
    assert ctx.feature_specs(REF) is a
    assert backend.calls["get_feature_specs"] == 1
    ctx.feature_specs(DocRef("d" * 24, "b" * 24, "c" * 24))
    assert backend.calls["get_feature_specs"] == 2


def test_specs_fetch_failure_not_cached(tmp_path):
    ctx, backend = fake_ctx(tmp_path)
    good = backend.specs_json
    backend.specs_json = {"featureSpecs": "broken"}
    with pytest.raises(RuntimeError):
        ctx.feature_specs(REF)
    assert REF not in ctx.specs
    backend.specs_json = good
    assert ctx.feature_specs(REF)
    assert backend.calls["get_feature_specs"] == 2


def test_fake_default_specs_empty():
    assert FakeBackend().get_feature_specs(REF) == []


def test_onshape_get_feature_specs_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "key")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "secret")
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path))
        return httpx.Response(200, json=FIXTURE)

    cfg = Config(budget=10, ledger_path=tmp_path / "l.json")
    ledger = Ledger(cfg.ledger_path, 10)
    b = OnshapeBackend(cfg, ledger, httpx.MockTransport(handler))
    specs = b.get_feature_specs(REF)
    assert seen == [("GET", f"/api/v6/partstudios/d/{'a'*24}/w/{'b'*24}/e/{'c'*24}/featurespecs")]
    assert ledger.used == 1 and len(specs) == 6


def _live_param(bt, pid, qt=None, default=None, ranges=None, vis=None):
    return {"btType": bt, "parameterId": pid, "parameterName": pid, "quantityType": qt,
            "defaultValue": default, "ranges": ranges, "visibilityCondition": vis}


def _live_spec():
    qty = lambda pid, qt, u, v: {"btType": "BTMParameterQuantity-147", "parameterId": pid,
                                 "units": u, "value": v, "expression": "", "isInteger": False}
    return {"featureSpecs": [{"featureType": "liveThing", "featureTypeName": "Live", "namespace": "",
        "parameters": [
            _live_param("BTParameterSpecQuantity-173", "depth", "LENGTH", qty("depth", "LENGTH", "meter", 0.025),
                        [{"defaultValue": 0.025, "units": "meter"}, {"defaultValue": 2.5, "units": "centimeter"},
                         {"defaultValue": 25.0, "units": "millimeter"}]),
            _live_param("BTParameterSpecQuantity-173", "draft", "ANGLE", qty("draft", "ANGLE", "degree", 3.0),
                        [{"defaultValue": 3.0, "units": "degree"}, {"defaultValue": 0.0523, "units": "radian"}]),
            _live_param("BTParameterSpecQuantity-173", "count", "INTEGER", qty("count", "INTEGER", "", 4.0),
                        [{"defaultValue": 4.0, "units": ""}]),
            _live_param("BTParameterSpecQuery-174", "entities",
                        default={"btType": "BTMParameterQueryList-148", "parameterId": "entities", "queries": []}),
            _live_param("BTParameterSpecFeatureList-703", "fl",
                        default={"btType": "BTMParameterFeatureList-1749", "parameterId": "fl", "featureIds": []}),
            _live_param("BTParameterSpecArray-2600", "arr",
                        default={"btType": "BTMParameterArray-2025", "parameterId": "arr", "items": []}),
            _live_param("BTParameterSpecBoolean-170", "flip",
                        default={"btType": "BTMParameterBoolean-144", "parameterId": "flip", "value": False},
                        vis={"btType": "BTParameterVisibilityCondition-177"}),
        ]}]}


def test_live_shaped_defaults():
    (spec,) = parse_feature_specs(_live_spec())
    d = {p.id: p.default for p in spec.params}
    assert d["depth"] == "25 mm"
    assert d["draft"] == "3 deg"
    assert d["count"] == "4"
    assert d["entities"] is None and d["fl"] is None and d["arr"] is None
    assert d["flip"] is False


def test_bare_visibility_condition_is_always_shown():
    (spec,) = parse_feature_specs(_live_spec())
    assert spec.param("flip").conditional is False and spec.param("flip").condition == ""


def test_real_capture_defaults_if_present():
    path = Path(__file__).parent / "fixtures" / "feature_specs.json.gz"
    if not path.exists():
        pytest.skip("feature_specs.json.gz arrives when the branch merges with typed-features")
    import gzip
    data = json.loads(gzip.open(path).read())
    specs = {s.type: s for s in parse_feature_specs({"featureSpecs": data.get("kept", data)})}
    assert specs["extrude"].param("depth").default == "25 mm"
    for s in specs.values():
        for p in s.params:
            assert p.default is None or isinstance(p.default, (str, bool, int, float))


def test_range_default_keeps_full_precision():
    # A '%g' rendering cut 1.789911 deg (hole tappedAngle) to 1.78991 deg (6 significant digits).
    qty = {"btType": "BTMParameterQuantity-147", "parameterId": "a", "units": "degree",
           "value": 1.789911, "expression": "", "isInteger": False}
    spec = {"featureSpecs": [{"featureType": "t", "featureTypeName": "T", "namespace": "", "parameters": [
        _live_param("BTParameterSpecQuantity-173", "a", "ANGLE", qty,
                    [{"defaultValue": 1.789911, "units": "degree"}]),
        _live_param("BTParameterSpecQuantity-173", "b", "LENGTH", dict(qty, parameterId="b", units="meter"),
                    [{"defaultValue": 0.1, "units": "meter"}, {"defaultValue": 0.1, "units": "millimeter"}]),
        _live_param("BTParameterSpecQuantity-173", "c", "LENGTH", dict(qty, parameterId="c", units="meter"),
                    [{"defaultValue": float("inf"), "units": "millimeter"}]),
    ]}]}
    (s,) = parse_feature_specs(spec)
    assert s.param("a").default == "1.789911 deg"
    assert s.param("b").default == "0.1 mm"  # the millimeter range's own default, not 0.1 m
    assert s.param("c").default is None  # a non-finite default is not sent
