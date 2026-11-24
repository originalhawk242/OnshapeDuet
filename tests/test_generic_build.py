import sys

import pytest

from onshape_duet.generic.build import ParamError, build_param
from onshape_duet.generic.specs import ParamSpec
from onshape_duet.model import Feature, FeatureList

FL = FeatureList("mv", [Feature("F1", "newSketch", "Base", "OK")])

DEPTH = ParamSpec("depth", "Depth", "quantity", quantity_type="LENGTH", units="millimeter")
COUNT = ParamSpec("count", "Count", "quantity", quantity_type="INTEGER")
ANGLE = ParamSpec("angle", "Angle", "quantity", quantity_type="ANGLE", units="degree")
END_BOUND = ParamSpec("endBound", "End", "enum", enum_name="BoundingType",
                      options=("BLIND", "SYMMETRIC", "THROUGH_ALL"))
NAME_STR = ParamSpec("label", "Label", "string")
FLAG = ParamSpec("flip", "Flip", "boolean")
ENTITIES = ParamSpec("entities", "Entities", "query")
TOOLS = ParamSpec("tools", "Tools", "feature_list")
ARR = ParamSpec("items", "Items", "array", items=(ENTITIES, DEPTH))
WEIRD = ParamSpec("w", "W", "unsupported")

EDGE = {"entity": "edge", "created_by": "Base"}


def test_quantity_forms():
    assert build_param(DEPTH, "10 mm", FL) == {
        "btType": "BTMParameterQuantity-147", "parameterId": "depth",
        "expression": "10 mm", "isInteger": False}
    assert build_param(DEPTH, "#plate_w / 2", FL)["expression"] == "#plate_w / 2"
    c = build_param(COUNT, 4, FL)
    assert c["expression"] == "4" and c["isInteger"] is True
    with pytest.raises(ParamError) as e:
        build_param(DEPTH, "10", FL)
    assert e.value.code == "MISSING_UNIT" and e.value.param == "depth"
    with pytest.raises(ParamError):
        build_param(DEPTH, " -2.5 ", FL)
    assert build_param(COUNT, "4", FL)["expression"] == "4"   # no units: bare number fine


def test_bare_number_only_for_integer_or_real():
    with pytest.raises(ParamError) as e:
        build_param(DEPTH, 10, FL)
    assert e.value.code == "BAD_VALUE"
    real = ParamSpec("r", "R", "quantity", quantity_type="REAL")
    assert build_param(real, 1.5, FL)["expression"] == "1.5"


def test_build_rejects_near_misses():
    for spec, bad in [(END_BOUND, "blind"), (NAME_STR, None), (NAME_STR, {"a": 1}),
                      (NAME_STR, "x" * 10_000), (NAME_STR, "a\nb"), (FLAG, "true"), (DEPTH, True),
                      (DEPTH, "1 mm\n"), (END_BOUND, 3), (END_BOUND, "NOPE")]:
        with pytest.raises(ParamError) as e:
            build_param(spec, bad, FL)
        assert e.value.code == "BAD_VALUE" and e.value.param == spec.id
    with pytest.raises(ParamError, match="BLIND"):
        build_param(END_BOUND, "blind", FL)


def test_enum_bool_string_ok():
    assert build_param(END_BOUND, "BLIND", FL) == {
        "btType": "BTMParameterEnum-145", "parameterId": "endBound",
        "enumName": "BoundingType", "value": "BLIND"}
    assert build_param(FLAG, True, FL)["value"] is True
    s = build_param(NAME_STR, "hello", FL)
    assert s["btType"] == "BTMParameterString-149" and s["value"] == "hello"
    assert s["parameterId"] == "label"


def test_query_compiles_selector_and_union():
    q = build_param(ENTITIES, EDGE, FL)
    assert q["btType"] == "BTMParameterQueryList-148" and len(q["queries"]) == 1
    assert 'makeId("F1")' in q["queries"][0]["queryString"]
    u = build_param(ENTITIES, [EDGE, {**EDGE, "geometry": "circle"}], FL)
    assert len(u["queries"]) == 1 and "qUnion" in u["queries"][0]["queryString"]


def test_bad_selector_is_bad_value():
    with pytest.raises(ParamError) as e:
        build_param(ENTITIES, {"entity": "vertex"}, FL)
    assert e.value.code == "BAD_VALUE" and e.value.param == "entities"
    with pytest.raises(ParamError) as e:
        build_param(ENTITIES, {"entity": "edge", "created_by": "Nope"}, FL)
    assert e.value.code == "BAD_VALUE"


def test_raw_geometry_id_refused():
    for v in [{"btType": "BTMIndividualQuery-138", "deterministicIds": ["JHD"]},
              [EDGE, {"entity": "edge", "deterministicIds": ["JHD"]}],
              {"edges_of": {"btType": "x"}}]:
        with pytest.raises(ParamError) as e:
            build_param(ENTITIES, v, FL)
        assert e.value.code == "RAW_GEOMETRY_ID"


def test_query_without_selectors_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "onshape_duet.features.refs", None)
    with pytest.raises(ParamError, match="selectors not available yet") as e:
        build_param(ENTITIES, EDGE, FL)
    assert e.value.code == "UNSUPPORTED_PARAM_KIND"
    with pytest.raises(ParamError) as e:   # raw-id check still runs first
        build_param(ENTITIES, {"deterministicIds": ["x"]}, FL)
    assert e.value.code == "RAW_GEOMETRY_ID"


def test_feature_list_resolves_names():
    r = build_param(TOOLS, ["Base"], FL)
    assert r == {"btType": "BTMParameterFeatureList-1749", "parameterId": "tools", "featureIds": ["F1"]}
    assert build_param(TOOLS, ["F1"], FL)["featureIds"] == ["F1"]
    for bad in (["Nope"], "Base", [3]):
        with pytest.raises(ParamError) as e:
            build_param(TOOLS, bad, FL)
        assert e.value.code == "BAD_VALUE"


def test_array_items_built_and_unknown_key_refused():
    r = build_param(ARR, [{"entities": EDGE, "depth": "5 mm"}, {"depth": "6 mm"}], FL)
    assert r["btType"] == "BTMParameterArray-2025" and r["parameterId"] == "items"
    assert [i["btType"] for i in r["items"]] == ["BTMArrayParameterItem-1843"] * 2
    assert [p["parameterId"] for p in r["items"][0]["parameters"]] == ["entities", "depth"]
    with pytest.raises(ParamError) as e:
        build_param(ARR, [{"bogus": 1}], FL)
    assert e.value.code == "BAD_VALUE"
    with pytest.raises(ParamError):
        build_param(ARR, [{"depth": "5"}], FL)   # nested errors propagate


def _current():
    return {"btType": "BTMParameterArray-2025", "parameterId": "items", "items": [
        {"btType": "BTMArrayParameterItem-1843", "parameters": [
            {"btType": "BTMParameterQueryList-148", "parameterId": "entities",
             "queries": [{"btType": "BTMIndividualQuery-138", "deterministicIds": ["JHD"]}]},
            {"btType": "BTMParameterQuantity-147", "parameterId": "depth", "expression": "1 mm",
             "isInteger": False}]},
        {"btType": "BTMArrayParameterItem-1843", "parameters": [
            {"btType": "BTMParameterQuantity-147", "parameterId": "depth", "expression": "2 mm",
             "isInteger": False}]}]}


def test_array_patch_keeps_sibling_geometry_ids():
    cur = _current()
    before = repr(cur)
    r = build_param(ARR, {"items": {"1": {"depth": "9 mm", "entities": EDGE}}}, FL, current=cur)
    assert repr(cur) == before                      # input untouched
    assert r["items"][0] == cur["items"][0]
    assert r["items"][0]["parameters"][0]["queries"][0]["deterministicIds"] == ["JHD"]
    p1 = {p["parameterId"]: p for p in r["items"][1]["parameters"]}
    assert p1["depth"]["expression"] == "9 mm" and "entities" in p1
    assert len(r["items"][1]["parameters"]) == 2    # replaced, not duplicated; absent appended


def test_array_list_replace_refused_over_geometry_ids():
    with pytest.raises(ParamError, match="items patch form") as e:
        build_param(ARR, [{"depth": "5 mm"}], FL, current=_current())
    assert e.value.code == "RAW_GEOMETRY_ID"
    ok = {"btType": "BTMParameterArray-2025", "parameterId": "items", "items": []}
    assert build_param(ARR, [{"depth": "5 mm"}], FL, current=ok)["items"]


def test_array_patch_bad_index_and_patch_without_current():
    patch = lambda k: {"items": {k: {"depth": "1 mm"}}}  # noqa: E731
    for k in ("x", "5", "-1", "1.0"):
        with pytest.raises(ParamError) as e:
            build_param(ARR, patch(k), FL, current=_current())
        assert e.value.code == "BAD_VALUE"
    with pytest.raises(ParamError) as e:
        build_param(ARR, patch("0"), FL)
    assert e.value.code == "BAD_VALUE"
    with pytest.raises(ParamError):
        build_param(ARR, {"items": {"0": {"nope": "1 mm"}}}, FL, current=_current())
    with pytest.raises(ParamError):
        build_param(ARR, "x", FL)


def test_unsupported_kind_refused():
    with pytest.raises(ParamError) as e:
        build_param(WEIRD, 1, FL)
    assert e.value.code == "UNSUPPORTED_PARAM_KIND" and e.value.param == "w"
