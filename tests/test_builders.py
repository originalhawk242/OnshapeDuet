import pytest

from onshape_duet.features.builders import build_extrude
from onshape_duet.features.params import FeatureSpecError
from onshape_duet.features.refs import RefError
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


# ---- cad_shell / cad_boolean builders ----
from onshape_duet.features.builders import build_boolean, build_shell  # noqa: E402

FACES = {"entity": "face", "created_by": "Base", "cap": "end"}
BODY = {"entity": "body", "created_by": "Base"}


def test_build_shell_matches_spec():
    b = build_shell(FL, faces=FACES, thickness=2, outward=True)
    assert_matches_spec(b.feature_json, "shell")
    p = _by_id(b)
    assert b.feature_json["featureType"] == "shell"
    assert p["thickness"]["expression"] == "2 mm"
    assert p["oppositeDirection"]["value"] is True
    assert p["entities"]["queries"][0]["queryString"]
    assert "isHollow" not in p and "parts" not in p
    assert b.values[0].literal == 2
    assert _by_id(build_shell(FL, faces=FACES, thickness=2))["oppositeDirection"]["value"] is False


def test_build_boolean_union_matches_spec():
    b = build_boolean(FL, operation="union", tools=[BODY, BODY])
    assert_matches_spec(b.feature_json, "booleanBodies")
    p = _by_id(b)
    assert b.feature_json["featureType"] == "booleanBodies"
    assert p["operationType"]["value"] == "UNION"
    assert p["keepTools"]["value"] is False
    assert "targets" not in p and p["tools"]["queries"][0]["queryString"]


def test_build_boolean_subtract_and_intersect():
    b = build_boolean(FL, operation="subtract", tools=BODY, targets=BODY, keep_tools=True)
    assert_matches_spec(b.feature_json, "booleanBodies")
    p = _by_id(b)
    assert p["operationType"]["value"] == "SUBTRACTION"
    assert p["keepTools"]["value"] is True and "targets" in p
    assert _by_id(build_boolean(FL, operation="intersect", tools=BODY))["operationType"]["value"] \
        == "INTERSECTION"
    assert b.summary["tools"] == BODY


def test_boolean_subtract_requires_targets():
    with pytest.raises(FeatureSpecError, match="targets"):
        build_boolean(FL, operation="subtract", tools=BODY)


@pytest.mark.parametrize("op", ["union", "intersect"])
def test_boolean_union_rejects_targets(op):
    with pytest.raises(FeatureSpecError, match="targets"):
        build_boolean(FL, operation=op, tools=BODY, targets=BODY)


def test_boolean_bad_operation_and_entities():
    with pytest.raises(FeatureSpecError, match="operation"):
        build_boolean(FL, operation="merge", tools=BODY)
    with pytest.raises(RefError, match="tools"):
        build_boolean(FL, operation="union", tools=FACES)
    with pytest.raises(RefError, match="targets"):
        build_boolean(FL, operation="subtract", tools=BODY, targets=FACES)
    with pytest.raises(FeatureSpecError, match="keep_tools"):
        build_boolean(FL, operation="union", tools=BODY, keep_tools="yes")


def test_shell_edge_selector_refused():
    with pytest.raises(RefError, match="faces"):
        build_shell(FL, faces=EDGES, thickness=1)
    with pytest.raises(FeatureSpecError, match="outward"):
        build_shell(FL, faces=FACES, thickness=1, outward="x")


# ---- cad_pattern / cad_mirror builders ----
from onshape_duet.features.builders import build_mirror, build_pattern  # noqa: E402

PFL = FeatureList("v1", [Feature("Fabc", "newSketch", "Base", "OK"),
                         Feature("Fext", "extrude", "Boss", "OK"),
                         Feature("Fcp", "cPlane", "Mid", "OK")])
EDGE = {"entity": "edge", "created_by": "Base", "geometry": "line"}


def test_build_linear_pattern_matches_spec():
    b = build_pattern(PFL, kind="linear", features=["Boss"], count=4, direction=EDGE, spacing="10 mm")
    assert_matches_spec(b.feature_json, "linearPattern")
    p = _by_id(b)
    assert b.feature_json["featureType"] == "linearPattern"
    assert p["patternType"]["value"] == "FEATURE" and p["patternType"]["enumName"] == "PatternType"
    assert p["instanceFunction"]["btType"] == "BTMParameterFeatureList-1749"
    assert p["instanceFunction"]["featureIds"] == ["Fext"]
    assert p["distance"]["expression"] == "10 mm"
    assert p["instanceCount"]["expression"] == "4" and p["instanceCount"]["isInteger"] is True
    assert p["oppositeDirection"]["value"] is False
    assert "directionOne" in p and "operationType" not in p and "defaultScope" not in p
    assert "Fext" not in repr(b.summary)


def test_build_circular_pattern_matches_spec():
    b = build_pattern(PFL, kind="circular", features=["Boss"], count=6, axis=EDGE)
    assert_matches_spec(b.feature_json, "circularPattern")
    p = _by_id(b)
    assert p["patternType"]["value"] == "FEATURE"
    assert p["angle"]["expression"] == "360 deg" and p["equalSpace"]["value"] is True
    assert p["instanceCount"]["expression"] == "6" and "axis" in p
    assert "operationType" not in p and "defaultScope" not in p


def test_build_mirror_matches_spec():
    b = build_mirror(PFL, features=["Boss"], plane="Top")
    assert_matches_spec(b.feature_json, "mirror")
    p = _by_id(b)
    assert p["patternType"]["value"] == "FEATURE" and p["patternType"]["enumName"] == "MirrorType"
    assert p["instanceFunction"]["featureIds"] == ["Fext"]
    assert "mirrorPlane" in p and "operationType" not in p
    b = build_mirror(PFL, features=["Boss"], plane={"plane": "Mid"})
    assert_matches_spec(b.feature_json, "mirror")


@pytest.mark.parametrize("n", [1, 0, -3, True, "x", 10001, "#n"])
def test_pattern_count_below_two_refused(n):
    with pytest.raises(FeatureSpecError):
        build_pattern(PFL, kind="linear", features=["Boss"], count=n, direction=EDGE, spacing=5)


def test_linear_pattern_rejects_axis():
    with pytest.raises(FeatureSpecError, match="axis"):
        build_pattern(PFL, kind="linear", features=["Boss"], count=3, direction=EDGE, spacing=5, axis=EDGE)
    with pytest.raises(FeatureSpecError, match="direction"):
        build_pattern(PFL, kind="linear", features=["Boss"], count=3, spacing=5)
    with pytest.raises(FeatureSpecError, match="spacing"):
        build_pattern(PFL, kind="linear", features=["Boss"], count=3, direction=EDGE)


def test_circular_pattern_rules():
    with pytest.raises(FeatureSpecError, match="axis"):
        build_pattern(PFL, kind="circular", features=["Boss"], count=3)
    with pytest.raises(FeatureSpecError, match="direction"):
        build_pattern(PFL, kind="circular", features=["Boss"], count=3, axis=EDGE, direction=EDGE)
    with pytest.raises(FeatureSpecError, match="spacing"):
        build_pattern(PFL, kind="circular", features=["Boss"], count=3, axis=EDGE, spacing=5)
    with pytest.raises(FeatureSpecError, match="axis"):
        build_pattern(PFL, kind="circular", features=["Boss"], count=3,
                      axis={"entity": "face", "created_by": "Base"})
    with pytest.raises(FeatureSpecError):
        build_pattern(PFL, kind="circular", features=["Boss"], count=3, axis=EDGE, angle=0)


def test_pattern_bad_kind():
    with pytest.raises(FeatureSpecError, match="kind"):
        build_pattern(PFL, kind="spiral", features=["Boss"], count=3, axis=EDGE)


def test_pattern_unknown_feature_lists_names():
    with pytest.raises(FeatureSpecError) as e:
        build_pattern(PFL, kind="circular", features=["Nope"], count=3, axis=EDGE)
    assert "features" in str(e.value) and "'Boss'" in str(e.value)


@pytest.mark.parametrize("fs", [[], "Boss", None, [1], ["Boss"] * 0 + [f"f{i}" for i in range(21)], [None]])
def test_pattern_bad_features_list(fs):
    with pytest.raises(FeatureSpecError, match="features"):
        build_mirror(PFL, features=fs, plane="Top")


def test_pattern_features_deduplicated_in_order():
    b = build_mirror(PFL, features=["Boss", "Base", "Boss"], plane="Top")
    assert _by_id(b)["instanceFunction"]["featureIds"] == ["Fext", "Fabc"]


# ---- cad_hole builder ----
from onshape_duet.features.builders import UNVERIFIED_LIVE, build_hole  # noqa: E402


def _loc(b):
    return [q["queryString"] for q in _by_id(b)["locations"]["queries"]]


def test_build_hole_through_matches_spec():
    b = build_hole(FL, sketch="Base", points=["p1", "p2"], diameter=5)
    assert_matches_spec(b.feature_json, "hole")
    ps = _by_id(b)
    for pid in ("holeDiameter", "holeDiameterV2", "holeDiameterV3"):
        assert ps[pid]["expression"] == "5 mm"
    assert ps["endStyle"]["value"] == "THROUGH" and ps["endStyleV2"]["value"] == "THROUGH"
    assert ps["endStyle"]["enumName"] == "HoleEndStyle" and ps["endStyleV2"]["enumName"] == "HoleEndStyleV2"
    assert "holeDepth" not in ps and "holeDepthV3" not in ps
    assert ps["style"]["value"] == ps["styleV2"]["value"] == "SIMPLE"
    assert ps["holeVersion"]["value"] == "V3" and ps["isV2"]["value"] is True
    assert ps["unitsSystem"]["value"] == "METRIC" and ps["threadStandard"]["value"] == "UNSET"
    assert ps["startStyle"]["value"] == "PART" and ps["oppositeDirection"]["value"] is False
    assert _loc(b) == ['query=sketchEntityQuery(makeId("Fabc"), EntityType.VERTEX, "p1");',
                       'query=sketchEntityQuery(makeId("Fabc"), EntityType.VERTEX, "p2");']
    assert ps["scope"]["queries"][0]["queryString"] == "query=qAllModifiableSolidBodies();"
    assert b.summary == {"sketch": "Base", "points": ["p1", "p2"], "diameter": "5 mm", "depth": None,
                         "note": f"hole settings (LEGACY/V2/V3 variants): {UNVERIFIED_LIVE}"}
    assert "Fabc" not in repr(b.summary)


def test_hole_blind_sets_depth():
    b = build_hole(FL, sketch="Base", points=["p1", "p1"], diameter="#d", depth="8 mm")
    assert_matches_spec(b.feature_json, "hole")
    ps = _by_id(b)
    assert ps["endStyle"]["value"] == "BLIND" and ps["endStyleV2"]["value"] == "BLIND"
    assert ps["holeDepth"]["expression"] == ps["holeDepthV3"]["expression"] == "8 mm"
    assert ps["holeDiameterV3"]["expression"] == "#d"
    assert len(_loc(b)) == 1 and b.summary["depth"] == "8 mm"


@pytest.mark.parametrize("bad", ['p"); x(', "a b", "p\n1", "", 5, None, "a..b", "a;b"])
def test_hole_point_id_rejects_quotes(bad):
    with pytest.raises(FeatureSpecError, match="points"):
        build_hole(FL, sketch="Base", points=["ok", bad], diameter=5)


@pytest.mark.parametrize("pts", [[], "p1", None, ["p"] * 0, [f"p{i}" for i in range(51)]])
def test_hole_points_shape_refused(pts):
    with pytest.raises(FeatureSpecError, match="points"):
        build_hole(FL, sketch="Base", points=pts, diameter=5)


@pytest.mark.parametrize("style", ["counterbore", "countersink", "weird"])
def test_hole_counterbore_refused_with_message(style):
    with pytest.raises(FeatureSpecError, match="counterbore/countersink are deferred"):
        build_hole(FL, sketch="Base", points=["p1"], diameter=5, style=style)
