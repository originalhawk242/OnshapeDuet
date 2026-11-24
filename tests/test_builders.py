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


# ---- cad_plane builder ----
from onshape_duet.features.builders import build_plane  # noqa: E402
from onshape_duet.features.refs import RefError  # noqa: E402


def test_build_plane_matches_spec():
    b = build_plane(FL, base="Top", offset=10, flip=True)
    assert_matches_spec(b.feature_json, "cPlane")
    ps = _by_id(b)
    assert ps["cplaneType"]["value"] == "OFFSET"
    assert ps["cplaneType"]["enumName"] == "CPlaneType"
    assert ps["offset"]["expression"] == "10 mm"
    assert ps["oppositeDirection"]["value"] is True
    assert ps["entities"]["queries"][0]["deterministicIds"] == ["JDC"]
    assert b.summary == {"base": "Top", "offset": "10 mm", "flip": True}


def test_build_plane_variable_offset_and_face_base():
    face = {"entity": "face", "created_by": "Base", "cap": "end"}
    b = build_plane(FL, base=face, offset="#h")
    assert _by_id(b)["offset"]["expression"] == "#h"
    assert b.values[0].expr == "#h"
    assert b.summary["base"] == face


def test_build_plane_refuses_bad_base_and_flip():
    with pytest.raises(RefError):
        build_plane(FL, base="Nope", offset=5)
    with pytest.raises(FeatureSpecError):
        build_plane(FL, base="Top", offset=5, flip="yes")


def test_build_plane_summary_cleans_strings():
    fl = FeatureList("v1", [Feature("Fp", "cPlane", "Mid\x1b[31m", "OK")])
    b = build_plane(fl, base={"plane": "Mid\x1b[31m"}, offset=1)
    assert "\x1b" not in str(b.summary)


from onshape_duet.features.builders import build_revolve  # noqa: E402


def test_build_revolve_matches_spec():
    b = build_revolve(FL, sketch="Base", axis="box.left")
    assert_matches_spec(b.feature_json, "revolve")
    p = _by_id(b)
    assert b.feature_json["featureType"] == "revolve"
    assert p["fullRevolve"]["value"] is True
    assert not {"angle", "endBound", "symmetric"} & set(p)
    assert p["bodyType"]["value"] == "SOLID" and p["operationType"]["value"] == "NEW"
    assert 'qSketchRegion(makeId("Fabc"), true)' in p["entities"]["queries"][0]["queryString"]
    assert 'sketchEntityQuery(makeId("Fabc"), EntityType.EDGE, "box.left")' in \
        p["axis"]["queries"][0]["queryString"]
    assert p["oppositeDirection"]["value"] is False
    assert "defaultScope" not in p


def test_revolve_partial_angle_sets_type_and_angle():
    b = build_revolve(FL, sketch="Base", axis="ax", angle=90, operation="add",
                      symmetric=True, flip=True)
    assert_matches_spec(b.feature_json, "revolve")
    p = _by_id(b)
    assert p["fullRevolve"]["value"] is False
    assert p["endBound"]["value"] == "BLIND"
    assert p["angle"]["expression"] == "90 deg"
    assert p["symmetric"]["value"] is True and p["oppositeDirection"]["value"] is True
    assert p["defaultScope"]["value"] is True
    assert b.values[0].literal == 90


def test_revolve_partial_angle_not_symmetric_emits_false():
    p = _by_id(build_revolve(FL, sketch="Base", axis="ax", angle="45 deg"))
    assert p["symmetric"]["value"] is False and p["angle"]["expression"] == "45 deg"


def test_revolve_symmetric_full_refused():
    with pytest.raises(FeatureSpecError, match="symmetric"):
        build_revolve(FL, sketch="Base", axis="ax", symmetric=True)


@pytest.mark.parametrize("axis", ['a"); x(', "a b", "", None, 5, ["a"], "a.", ".a", "a..b",
                                  "a\n", "é", 'a"'])
def test_revolve_axis_id_rejects_hostile(axis):
    with pytest.raises(FeatureSpecError, match="axis"):
        build_revolve(FL, sketch="Base", axis=axis)


@pytest.mark.parametrize("kw", [{"angle": True}, {"angle": "#v"}, {"angle": 0}, {"angle": 400},
                                {"operation": "zap"}, {"flip": "yes"}, {"symmetric": 1}])
def test_revolve_bad_args_refused(kw):
    with pytest.raises(FeatureSpecError):
        build_revolve(FL, sketch="Base", axis="ax", **kw)


def test_revolve_unknown_sketch():
    with pytest.raises(Exception, match="Nope"):
        build_revolve(FL, sketch="Nope", axis="ax")


def test_revolve_summary():
    b = build_revolve(FL, sketch="Base", axis="ax", angle=90)
    assert b.summary == {"sketch": "Base", "axis": "ax", "angle": "90 deg",
                         "operation": "new", "symmetric": False}


# ---- cad_fillet / cad_chamfer builders ----
from onshape_duet.features.builders import build_chamfer, build_fillet  # noqa: E402

EDGES = {"entity": "edge", "created_by": "Base", "geometry": "line"}


def test_build_fillet_matches_spec():
    b = build_fillet(FL, edges=EDGES, radius=2)
    assert_matches_spec(b.feature_json, "fillet")
    p = _by_id(b)
    assert b.feature_json["featureType"] == "fillet"
    assert p["filletType"]["value"] == "EDGE"
    assert p["blendControlType"]["value"] == "RADIUS"
    assert p["crossSection"]["value"] == "CIRCULAR"
    assert p["radius"]["expression"] == "2 mm"
    assert p["entities"]["queries"][0]["queryString"]
    assert b.values[0].literal == 2


def test_build_chamfer_matches_spec():
    b = build_chamfer(FL, edges=[EDGES], distance="1 mm")
    assert_matches_spec(b.feature_json, "chamfer")
    p = _by_id(b)
    assert b.feature_json["featureType"] == "chamfer"
    assert p["chamferType"]["value"] == "EQUAL_OFFSETS"
    assert p["width"]["expression"] == "1 mm"
    assert "entities" in p


def test_fillet_face_selector_allowed():
    face = {"entity": "face", "created_by": "Base", "cap": "end"}
    assert build_fillet(FL, edges=face, radius=1).feature_json["featureType"] == "fillet"


@pytest.mark.parametrize("build,kw", [(build_fillet, {"radius": 1}), (build_chamfer, {"distance": 1})])
def test_fillet_chamfer_body_selector_refused(build, kw):
    with pytest.raises(FeatureSpecError, match="edges"):
        build(FL, edges={"entity": "body", "created_by": "Base"}, **kw)


def test_fillet_summary_echoes_selector_not_ids():
    sel = {"entity": "edge", "created_by": "Base", "geometry": "line"}
    s = build_fillet(FL, edges=sel, radius=3).summary
    assert s["edges"] == sel and s["radius"] == "3 mm"
    import json
    text = json.dumps(s)
    assert "Fabc" not in text and "makeId" not in text


def test_fillet_radius_variable_reference():
    b = build_fillet(FL, edges=EDGES, radius="#r")
    assert _by_id(b)["radius"]["expression"] == "#r"
    assert b.values[0].expr == "#r"
    assert build_chamfer(FL, edges=EDGES, distance="#d").values[0].expr == "#d"


@pytest.mark.parametrize("v", [True, 0, -1, "x"])
def test_fillet_chamfer_bad_size_refused(v):
    with pytest.raises(FeatureSpecError):
        build_fillet(FL, edges=EDGES, radius=v)
    with pytest.raises(FeatureSpecError):
        build_chamfer(FL, edges=EDGES, distance=v)
