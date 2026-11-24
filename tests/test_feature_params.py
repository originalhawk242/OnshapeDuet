import pytest

from onshape_duet.features.params import (
    FeatureSpecError, Value, angle, count, feature, length, p_bool, p_enum, p_ids,
    p_qty, p_query, parse_length_mm,
)


def test_length_forms():
    assert length(10, "depth") == Value("length", "10 mm", 10.0, None, "depth")
    assert length("2 in", "depth").literal == pytest.approx(50.8)
    assert length("2   in", "depth").expr == "2 in"
    assert length("#depth", "depth") == Value("length", "#depth", None, "depth", "depth")


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, "10 mm); x(", "#Bad", "#a b", "", "10 deg", None])
def test_length_rejects_bad_values(bad):
    with pytest.raises(FeatureSpecError, match="depth"):
        length(bad, "depth")


def test_length_nonpositive_allowed():
    assert length(0, "d", positive=False).literal == 0.0
    assert length(-2, "d", positive=False).expr == "-2 mm"


def test_angle_and_count():
    assert angle(90, "angle").expr == "90 deg"
    assert angle("45 deg", "angle").literal == 45.0
    assert count(3, "count") == Value("count", "3", 3.0, None, "count")
    assert count("3", "count").literal == 3.0
    with pytest.raises(FeatureSpecError, match="not supported"):
        angle("#a", "angle")
    with pytest.raises(FeatureSpecError, match="not supported"):
        count("#a", "count")
    for bad in (2.5, 1, True, None, "x"):
        with pytest.raises(FeatureSpecError, match="count"):
            count(bad, "count")
    for bad in (0, -5, 361, "10 mm", True, None):
        with pytest.raises(FeatureSpecError, match="angle"):
            angle(bad, "angle")
    assert angle(360, "angle").literal == 360.0


def test_parse_length_mm():
    assert parse_length_mm("0.01 m") == pytest.approx(10.0)
    assert parse_length_mm("5") == 5.0
    assert parse_length_mm("#w") is None and parse_length_mm("#w * 2") is None


def test_p_query_wraps_expression():
    p = p_query("entities", ['qCreatedBy(makeId("F1"), EntityType.EDGE)'])
    assert p["btType"] == "BTMParameterQueryList-148"
    assert p["queries"][0]["btType"] == "BTMIndividualQuery-138"
    assert p["queries"][0]["queryString"] == 'query=qCreatedBy(makeId("F1"), EntityType.EDGE);'


def test_makers():
    q = p_qty("depth", length(5, "depth"))
    assert q["btType"] == "BTMParameterQuantity-147" and q["expression"] == "5 mm" and q["isInteger"] is False
    assert p_qty("n", count(3, "n"))["isInteger"] is True
    e = p_enum("endBound", "BoundingType", "BLIND")
    assert e["btType"] == "BTMParameterEnum-145" and e["enumName"] == "BoundingType" and e["value"] == "BLIND"
    assert p_bool("flip", True)["btType"] == "BTMParameterBoolean-144"
    assert p_ids("sketchPlane", ["JDC"])["queries"][0]["deterministicIds"] == ["JDC"]
    f = feature("extrude", "E", [q])
    assert f == {"btType": "BTMFeature-134", "featureType": "extrude", "name": "E", "suppressed": False, "parameters": [q]}
