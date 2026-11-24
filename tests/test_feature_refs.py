import pytest

from onshape_duet.features.refs import (RefError, compile_selector, plane_param, resolve,
                                        selector_entity)
from onshape_duet.model import Feature, FeatureList

FL = FeatureList("mv", [Feature("F1", "extrude", "Body", "OK"), Feature("F2", "newSketch", "Base", "OK"),
                        Feature("F3", "cPlane", "Mid", "OK")])


def test_compile_created_by_geometry():
    assert compile_selector({"entity": "edge", "created_by": "Body", "geometry": "line"}, FL, "edges") == \
        'qGeometry(qCreatedBy(makeId("F1"), EntityType.EDGE), GeometryType.LINE)'


def test_compile_cap_and_edges_of_and_farthest():
    assert compile_selector({"entity": "face", "created_by": "Body", "cap": "end"}, FL, "f") == \
        'qCapEntity(makeId("F1"), CapType.END, EntityType.FACE)'
    assert compile_selector({"entity": "edge", "edges_of": {"entity": "face", "created_by": "Body",
                             "cap": "end"}}, FL, "e") == \
        'qAdjacent(qCapEntity(makeId("F1"), CapType.END, EntityType.FACE), AdjacencyType.EDGE, EntityType.EDGE)'
    assert compile_selector({"entity": "face", "normal": "+Z", "farthest": "+Z"}, FL, "f") == \
        'qFarthestAlong(qParallelPlanes(qOwnedByBody(qAllModifiableSolidBodies(), EntityType.FACE), ' \
        'vector(0, 0, 1), false), vector(0, 0, 1))'


def test_compile_body_and_negative_vector():
    assert compile_selector({"entity": "body"}, FL, "b") == "qAllModifiableSolidBodies()"
    assert compile_selector({"entity": "body", "created_by": "Body"}, FL, "b") == \
        'qCreatedBy(makeId("F1"), EntityType.BODY)'
    assert "vector(-1, 0, 0)" in compile_selector({"entity": "face", "farthest": "-X"}, FL, "f")


def test_compile_union():
    s = compile_selector([{"entity": "edge", "created_by": "Body"}, {"entity": "edge", "created_by": "Body",
                          "geometry": "circle"}], FL, "edges")
    assert s.startswith("qUnion([") and s.count("makeId") == 2


@pytest.mark.parametrize("sel", [{"entity": "vertex"}, {"entity": "edge", "normal": "+Z"},
    {"entity": "edge", "geometry": "plane"}, {"entity": "face", "created_by": "Body", "evil": 1},
    {"entity": "edge", "edges_of": {"entity": "face", "edges_of": {}}}, [], [{"entity": "edge"}] * 9,
    {"entity": "face", "cap": "end"}, {"entity": "edge", "created_by": 'Body") + x('},
    {"entity": "body", "geometry": "line"}, {"entity": "body", "cap": "end", "created_by": "Body"},
    {"entity": "edge", "edges_of": [{"entity": "face"}]},
    {"entity": "face", "edges_of": {"entity": "face"}},
    {"entity": "edge", "edges_of": {"entity": "face"}, "created_by": "Body"},
    {"entity": "face", "normal": "+W"}, "edge", None, {}])
def test_compile_rejects(sel):
    with pytest.raises(RefError):
        compile_selector(sel, FL, "edges")


def test_resolve_ambiguous_name_refused():
    fl = FeatureList("mv", [Feature("F1", "extrude", "Body", "OK"), Feature("F9", "extrude", "Body", "OK")])
    with pytest.raises(RefError, match="F1.*F9|F9.*F1"):
        resolve(fl, "Body", "sketch")


def test_resolve_unknown_type_and_empty():
    assert resolve(FL, "Body", "x").id == "F1"
    with pytest.raises(RefError, match="Base"):
        resolve(FL, "nope", "x")
    with pytest.raises(RefError, match="expected"):
        resolve(FL, "Body", "x", types={"cPlane"})
    for bad in ("", 3, None):
        with pytest.raises(RefError):
            resolve(FL, bad, "x")


def test_unknown_name_lists_at_most_10():
    fl = FeatureList("mv", [Feature(f"F{i}", "extrude", f"N{i}", "OK") for i in range(15)])
    with pytest.raises(RefError) as e:
        resolve(fl, "zz", "x")
    assert "N9" in str(e.value) and "N10" not in str(e.value)


def test_hostile_feature_id_refused():
    fl = FeatureList("mv", [Feature('F1"); x(', "extrude", "Body", "OK")])
    with pytest.raises(RefError):
        compile_selector({"entity": "edge", "created_by": "Body"}, fl, "edges")


def test_selector_entity():
    assert selector_entity({"entity": "face"}) == "face"
    assert selector_entity([{"entity": "edge"}, {"entity": "edge"}]) == "edge"
    with pytest.raises(RefError):
        selector_entity([{"entity": "edge"}, {"entity": "face"}])


def test_plane_param_forms():
    assert plane_param("sketchPlane", "Top", FL)["queries"][0]["deterministicIds"] == ["JDC"]
    assert 'makeId("F3")' in plane_param("p", {"plane": "Mid"}, FL)["queries"][0]["queryString"]
    assert "CapType" in plane_param("p", {"entity": "face", "created_by": "Body", "cap": "end"},
                                    FL)["queries"][0]["queryString"]
    assert "GeometryType.PLANE" in plane_param("p", {"entity": "face", "geometry": "plane"},
                                               FL)["queries"][0]["queryString"]
    with pytest.raises(RefError):
        plane_param("p", {"plane": "Body"}, FL)     # not a cPlane
    with pytest.raises(RefError):
        plane_param("p", {"entity": "face", "created_by": "Body"}, FL)  # not provably planar
    with pytest.raises(RefError, match="Front"):
        plane_param("p", "Bottom", FL)
