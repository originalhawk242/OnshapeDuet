import pytest

from onshape_duet.features.builders import build_extrude
from onshape_duet.features.params import FeatureSpecError
from onshape_duet.model import Feature, FeatureList

from ui_fixture import assert_matches_spec

FL = FeatureList("v1", [Feature("Fabc", "newSketch", "Base", "OK")])


def _by_id(built):
    return {p["parameterId"]: p for p in built.feature_json["parameters"]}


def test_build_extrude_matches_spec():
    b = build_extrude(FL, sketch="Base", depth="#depth")
    assert_matches_spec(b.feature_json, "extrude")
    ps = _by_id(b)
    assert ps["operationType"]["value"] == "NEW"
    assert ps["endBound"]["value"] == "BLIND"
    assert ps["depth"]["expression"] == "#depth"
    assert ps["entities"]["queries"][0]["queryString"] == 'query=qSketchRegion(makeId("Fabc"), true);'
    assert "defaultScope" not in ps


def test_build_extrude_remove_through_all_matches_spec():
    b = build_extrude(FL, sketch="Base", operation="remove", end="through_all", flip=True)
    assert_matches_spec(b.feature_json, "extrude")
    ps = _by_id(b)
    assert ps["operationType"]["value"] == "REMOVE"
    assert ps["endBound"]["value"] == "THROUGH_ALL"
    assert "depth" not in ps and ps["oppositeDirection"]["value"] is True
    assert ps["defaultScope"]["value"] is True


def test_build_extrude_symmetric_is_blind_plus_flag():
    b = build_extrude(FL, sketch="Base", depth=5, end="symmetric", operation="add")
    assert_matches_spec(b.feature_json, "extrude")
    ps = _by_id(b)
    assert ps["endBound"]["value"] == "BLIND" and ps["symmetric"]["value"] is True
    assert ps["depth"]["expression"] == "5 mm"


def test_extrude_through_all_with_depth_refused():
    with pytest.raises(FeatureSpecError, match="through_all"):
        build_extrude(FL, sketch="Base", depth=5, end="through_all")


def test_extrude_blind_needs_depth():
    with pytest.raises(FeatureSpecError, match="depth"):
        build_extrude(FL, sketch="Base")


def test_extrude_unknown_sketch_lists_names():
    with pytest.raises(FeatureSpecError, match="'Base'"):
        build_extrude(FL, sketch="Nope", depth=5)


@pytest.mark.parametrize("kw", [{"operation": "cut"}, {"end": "up_to"}, {"flip": "yes"}])
def test_extrude_bad_enums_refused(kw):
    with pytest.raises(FeatureSpecError):
        build_extrude(FL, sketch="Base", depth=5, **kw)
