import pytest

from onshape_cad_mcp.model import Variable
from onshape_cad_mcp.sketch.solver import DofReport, analyze
from onshape_cad_mcp.sketch.spec import (
    SketchSpecError,
    evaluate_variables,
    parse_sketch_spec,
)


def rect(extra_constraints=(), dims=()):
    return {
        "plane": "Front",
        "entities": [{"id": "r", "type": "rectangle", "corner1": [0, 0], "corner2": [60, 40]}],
        "constraints": list(extra_constraints),
        "dimensions": list(dims),
    }


GROUND = {"type": "ground", "refs": ["r.bottom.start"]}
DIMS = [
    {"type": "length", "ref": "r.bottom", "value": 60},
    {"type": "length", "ref": "r.left", "value": 40},
]


def run(spec, variables=None):
    return analyze(parse_sketch_spec(spec, variables or {}))


def test_rectangle_unconstrained_dof_is_4():
    rep = run(rect())
    assert isinstance(rep, DofReport)
    assert rep.dof == 4 and rep.redundant is False


def test_rectangle_grounded_dof_is_2():
    assert run(rect([GROUND])).dof == 2


def test_rectangle_grounded_and_dimensioned_dof_is_0():
    rep = run(rect([GROUND], DIMS))
    assert rep.dof == 0 and rep.redundant is False and rep.free == []


def test_rectangle_dimensions_from_variables():
    dims = [
        {"type": "length", "ref": "r.bottom", "value": "#w"},
        {"type": "length", "ref": "r.left", "value": "#h"},
    ]
    assert run(rect([GROUND], dims), {"w": 60.0, "h": 40.0}).dof == 0


def circle(constraints=(), dims=()):
    return {
        "plane": "Top",
        "entities": [{"id": "c", "type": "circle", "center": [5, 5], "radius": 10}],
        "constraints": list(constraints),
        "dimensions": list(dims),
    }


DIA = {"type": "diameter", "ref": "c", "value": 20}


def test_circle_diameter_only_dof_is_2_and_names_circle_in_free():
    rep = run(circle(dims=[DIA]))
    assert rep.dof == 2 and "c" in rep.free


def test_circle_position_and_diameter_dof_is_0():
    dims = [DIA,
            {"type": "distance_h", "ref": "c.center", "value": 5},
            {"type": "distance_v", "ref": "c.center", "value": 5}]
    rep = run(circle(dims=dims))
    assert rep.dof == 0 and rep.free == [] and rep.redundant is False


def test_duplicate_dimension_is_redundant():
    rep = run(rect([GROUND], DIMS + [DIMS[0]]))
    assert rep.redundant is True
    assert not (rep.dof == 0 and not rep.redundant)


def test_unknown_ref_error_names_it():
    spec = rect([{"type": "ground", "refs": ["nope.start"]}])
    with pytest.raises(SketchSpecError, match="nope"):
        parse_sketch_spec(spec, {})


def test_undefined_variable_error_names_it():
    spec = rect([GROUND], [{"type": "length", "ref": "r.bottom", "value": "#missing"}])
    with pytest.raises(SketchSpecError, match="missing"):
        parse_sketch_spec(spec, {})


def test_unknown_constraint_type_error_names_it():
    with pytest.raises(SketchSpecError, match="tangentish"):
        parse_sketch_spec(rect([{"type": "tangentish", "refs": ["r.top"]}]), {})


def test_unknown_entity_type_error_names_it():
    spec = {"plane": "Front", "entities": [{"id": "x", "type": "blob"}]}
    with pytest.raises(SketchSpecError, match="blob"):
        parse_sketch_spec(spec, {})


# --- per-constraint Jacobian coverage ---
def two_lines(constraints, dims=()):
    return {
        "plane": "Front",
        "entities": [
            {"id": "a", "type": "line", "start": [0, 0], "end": [10, 0]},
            {"id": "b", "type": "line", "start": [0, 5], "end": [10, 5]},
        ],
        "constraints": constraints,
        "dimensions": list(dims),
    }


def test_parallel_removes_one_dof():
    assert run(two_lines([])).dof == 8
    assert run(two_lines([{"type": "parallel", "refs": ["a", "b"]}])).dof == 7


def test_perpendicular_removes_one_dof():
    spec = two_lines([{"type": "perpendicular", "refs": ["a", "b"]}])
    spec["entities"][1] = {"id": "b", "type": "line", "start": [0, 5], "end": [0, 15]}
    assert run(spec).dof == 7


def test_equal_lines_and_circles_remove_one_dof():
    assert run(two_lines([{"type": "equal", "refs": ["a", "b"]}])).dof == 7
    spec = {
        "plane": "Front",
        "entities": [
            {"id": "c1", "type": "circle", "center": [0, 0], "radius": 1},
            {"id": "c2", "type": "circle", "center": [5, 0], "radius": 1},
        ],
        "constraints": [{"type": "equal", "refs": ["c1", "c2"]}],
    }
    assert run(spec).dof == 5


def test_parallel_plus_perpendicular_to_same_line_is_redundant():
    spec = {
        "plane": "Front",
        "entities": [
            {"id": "a", "type": "line", "start": [0, 0], "end": [10, 0]},
            {"id": "b", "type": "line", "start": [0, 5], "end": [10, 5]},
        ],
        "constraints": [{"type": "horizontal", "refs": ["a"]},
                        {"type": "horizontal", "refs": ["b"]},
                        {"type": "parallel", "refs": ["a", "b"]}],
    }
    assert run(spec).redundant is True


def test_not_assessed_entity_listed():
    spec = rect()
    spec["entities"].append({"id": "s", "type": "spline"})
    rep = run(spec)
    assert rep.not_assessed == ["s"] and rep.dof == 4


def test_evaluate_variables_units():
    got = evaluate_variables([
        Variable("a", "10 mm"), Variable("b", "2 cm"), Variable("c", "1 in"),
        Variable("d", "0.5m"), Variable("e", "7"),
    ])
    assert got == {"a": 10.0, "b": 20.0, "c": 25.4, "d": 500.0, "e": 7.0}


def test_evaluate_variables_rejects_expressions_naming_variable():
    with pytest.raises(SketchSpecError, match="width"):
        evaluate_variables([Variable("width", "#a * 2")])


# --- consistency at the nominal configuration (R12) ---
def test_reviewer_repro_contradiction_reports_conflict():
    spec = {
        "plane": "Front",
        "entities": [
            {"id": "a", "type": "line", "start": [0, 0], "end": [10, 0]},
            {"id": "b", "type": "line", "start": [0, 0], "end": [0, 10]},
        ],
        "constraints": [
            {"type": "ground", "refs": ["a.start"]},
            {"type": "ground", "refs": ["a.end"]},
            {"type": "ground", "refs": ["b.start"]},
            {"type": "parallel", "refs": ["a", "b"]},
            {"type": "perpendicular", "refs": ["a", "b"]},
        ],
    }
    rep = run(spec)
    assert rep.conflicts and rep.redundant is True
    assert not (rep.dof == 0 and not rep.conflicts)


def one_line(dims):
    return {"plane": "Front",
            "entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [10, 0]}],
            "dimensions": [{"type": "length", "ref": "l", "value": v} for v in dims]}


def test_contradictory_lengths_are_a_conflict_but_identical_are_only_redundant():
    bad = run(one_line([10, 12]))
    assert bad.conflicts and bad.redundant is True
    dup = run(one_line([10, 10]))
    assert dup.conflicts == [] and dup.redundant is True


def test_rectangle_coordinates_violating_dimensions_name_the_dimension():
    spec = rect([GROUND], DIMS)
    spec["entities"][0]["corner2"] = [50, 40]
    rep = run(spec)
    assert rep.redundant is True
    assert any("dimension #1 (length)" in c for c in rep.conflicts)


def test_consistent_sketch_has_no_conflicts():
    assert run(rect([GROUND], DIMS)).conflicts == []


def test_refs_into_not_assessed_entity_are_tolerated():
    spec = rect()
    spec["entities"].append({"id": "s", "type": "spline"})
    spec["constraints"] = [{"type": "coincident", "refs": ["s.start", "r.bottom.start"]},
                           {"type": "horizontal", "refs": ["s"]}]
    rep = run(spec)
    assert rep.dof == 4 and rep.not_assessed == ["s"]


@pytest.mark.parametrize("spec", [
    {"entities": ["x"]},
    {"entities": [], "constraints": ["x"]},
    {"entities": [], "dimensions": [3]},
    {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [1, 0]}],
     "constraints": [{"type": "horizontal", "refs": "l"}]},
    {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [1, 0]}],
     "dimensions": [{"type": "length", "refs": "l", "value": 1}]},
])
def test_malformed_spec_raises_spec_error(spec):
    with pytest.raises(SketchSpecError):
        parse_sketch_spec(spec, {})


# --- finite-difference check of every analytic Jacobian row ---
def _fd_sketch():
    import numpy as np  # noqa: F401
    return {
        "plane": "Front",
        "entities": [
            {"id": "a", "type": "line", "start": [1, 2], "end": [7, 4]},
            {"id": "b", "type": "line", "start": [3, -1], "end": [5, 6]},
            {"id": "p", "type": "point", "x": 2, "y": 9},
            {"id": "c1", "type": "circle", "center": [4, 4], "radius": 3},
            {"id": "c2", "type": "circle", "center": [-2, 1], "radius": 5},
        ],
        "constraints": [
            {"type": "coincident", "refs": ["a.end", "p"]},
            {"type": "horizontal", "refs": ["a"]},
            {"type": "horizontal", "refs": ["a.start", "p"]},
            {"type": "vertical", "refs": ["b"]},
            {"type": "vertical", "refs": ["b.start", "c1.center"]},
            {"type": "parallel", "refs": ["a", "b"]},
            {"type": "perpendicular", "refs": ["a", "b"]},
            {"type": "equal", "refs": ["a", "b"]},
            {"type": "equal", "refs": ["c1", "c2"]},
            {"type": "ground", "refs": ["p"]},
        ],
        "dimensions": [
            {"type": "length", "ref": "a", "value": 5},
            {"type": "diameter", "ref": "c1", "value": 6},
            {"type": "distance_h", "ref": "p", "value": 1},
            {"type": "distance_v", "ref": "p", "value": 1},
            {"type": "distance_h", "refs": ["a.start", "c2.center"], "value": 1},
            {"type": "distance_v", "refs": ["a.start", "c2.center"], "value": 1},
        ],
    }


def test_analytic_jacobian_matches_finite_differences():
    import numpy as np
    from onshape_cad_mcp.sketch.solver import build_rows

    rows, lay = build_rows(parse_sketch_spec(_fd_sketch(), {}))
    x0 = np.array(lay.x, dtype=float)
    labels = {r.label.split(" (")[1] for r in rows}
    assert {"length)", "diameter)", "distance_h)", "distance_v)", "coincident)", "horizontal)",
            "vertical)", "parallel)", "perpendicular)", "equal)", "ground)"} <= labels
    h = 1e-6
    for r in rows:
        num = np.zeros_like(x0)
        for k in range(len(x0)):
            xp, xm = x0.copy(), x0.copy()
            xp[k] += h
            xm[k] -= h
            num[k] = (r.fn(xp) - r.fn(xm)) / (2 * h)
        assert np.allclose(r.jac, num, atol=1e-5), (r.label, r.jac, num)


# --- final-review fixes: I4 numeric problems ---

def _sk(entities, constraints=(), dimensions=()):
    from onshape_cad_mcp.sketch.spec import parse_sketch_spec
    return parse_sketch_spec({"entities": entities, "constraints": list(constraints),
                              "dimensions": list(dimensions)}, {})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_rejected(bad):
    with pytest.raises(SketchSpecError, match="finite"):
        _sk([{"id": "p", "type": "point", "x": bad, "y": 0}])


def test_non_finite_variable_rejected():
    from onshape_cad_mcp.model import Variable
    from onshape_cad_mcp.sketch.spec import evaluate_variables
    with pytest.raises(SketchSpecError, match="finite"):
        evaluate_variables([Variable("w", "1e999 mm")])


@pytest.mark.parametrize("radius", [0, -1])
def test_circle_radius_must_be_positive(radius):
    with pytest.raises(SketchSpecError, match="radius"):
        _sk([{"id": "c", "type": "circle", "center": [0, 0], "radius": radius}])


@pytest.mark.parametrize("dim", ["length", "diameter"])
@pytest.mark.parametrize("value", [0, -3])
def test_length_and_diameter_must_be_positive(dim, value):
    ents = [{"id": "l", "type": "line", "start": [0, 0], "end": [5, 0]},
            {"id": "c", "type": "circle", "center": [0, 0], "radius": 2}]
    with pytest.raises(SketchSpecError, match="positive|> 0"):
        _sk(ents, dimensions=[{"type": dim, "ref": "l" if dim == "length" else "c", "value": value}])


def test_negative_distance_value_rejected():
    with pytest.raises(SketchSpecError, match="unsigned"):
        _sk([{"id": "p", "type": "point", "x": 1, "y": 1}],
            dimensions=[{"type": "distance_h", "ref": "p", "value": -5}])


@pytest.mark.parametrize("axis,pt", [("distance_h", {"x": -5, "y": 3}), ("distance_v", {"x": 3, "y": -5})])
def test_point_left_or_below_origin_is_dimensionable(axis, pt):
    sk = _sk([{"id": "p", "type": "point", **pt}],
             constraints=[{"type": "ground", "refs": []}][:0],
             dimensions=[{"type": axis, "ref": "p", "value": 5}])
    rep = analyze(sk)
    assert rep.conflicts == [] and rep.redundant is False
    assert rep.dof == 1  # the other axis stays free


def test_signed_distance_between_two_points_is_unsigned():
    sk = _sk([{"id": "a", "type": "point", "x": 10, "y": 0}, {"id": "b", "type": "point", "x": 4, "y": 0}],
             dimensions=[{"type": "distance_h", "refs": ["a", "b"], "value": 6}])
    assert analyze(sk).conflicts == []


def test_wrong_unsigned_distance_still_conflicts():
    sk = _sk([{"id": "p", "type": "point", "x": -5, "y": 0}],
             dimensions=[{"type": "distance_h", "ref": "p", "value": 7}])
    assert analyze(sk).conflicts


def test_serializer_emits_positive_distance_with_orientation():
    from onshape_cad_mcp.sketch.serialize import to_feature_json
    sk = _sk([{"id": "p", "type": "point", "x": -5, "y": 3}],
             dimensions=[{"type": "distance_h", "ref": "p", "value": 5}])
    dim = next(c for c in to_feature_json(sk, "S")["constraints"] if c["constraintType"] == "DISTANCE")
    qty = next(p for p in dim["parameters"] if p["btType"].startswith("BTMParameterQuantity"))
    assert qty["expression"] == "5 mm"
    ids = {p["parameterId"] for p in dim["parameters"]}
    assert {"localFirst", "externalSecond"} <= ids  # point is the first ref: origin -> point is negative


@pytest.mark.parametrize("bad_id", ["a b", "a.b", "", 5, None, "x/y", "é"])
def test_entity_ids_restricted(bad_id):
    with pytest.raises(SketchSpecError, match="id"):
        _sk([{"id": bad_id, "type": "point", "x": 0, "y": 0}])


@pytest.mark.parametrize("plane", ["Back", "front", 3, ["Front"], "JCC"])
def test_unknown_plane_rejected(plane):
    from onshape_cad_mcp.sketch.spec import parse_sketch_spec
    with pytest.raises(SketchSpecError, match="plane"):
        parse_sketch_spec({"plane": plane, "entities": []}, {})


def test_two_point_horizontal_serializes():
    from onshape_cad_mcp.sketch.serialize import to_feature_json
    sk = _sk([{"id": "a", "type": "point", "x": 0, "y": 0}, {"id": "b", "type": "point", "x": 5, "y": 0}],
             constraints=[{"type": "horizontal", "refs": ["a", "b"]}])
    con = to_feature_json(sk, "S")["constraints"][0]
    assert con["constraintType"] == "HORIZONTAL"
    assert [p["parameterId"] for p in con["parameters"]] == ["localFirst", "localSecond"]


# --- wave 2: per-row tolerance (each row scaled by its own entities) ---

def test_far_away_point_does_not_loosen_a_small_lines_length_check():
    spec = {"plane": "Front",
            "entities": [{"id": "far", "type": "point", "x": 1e9, "y": 0},
                         {"id": "l", "type": "line", "start": [0, 0], "end": [60, 0]}],
            "constraints": [], "dimensions": [{"type": "length", "ref": "l", "value": 60.001}]}
    rep = run(spec)
    assert rep.conflicts and "dimension #1" in rep.conflicts[0]
    spec["dimensions"][0]["value"] = 60
    assert run(spec).conflicts == []


def test_zero_distance_between_points_1e_minus_9_apart_is_not_a_conflict_near_the_origin():
    spec = {"plane": "Front",
            "entities": [{"id": "a", "type": "point", "x": 0, "y": 0},
                         {"id": "b", "type": "point", "x": 1e-9, "y": 0}],
            "constraints": [], "dimensions": [{"type": "distance_h", "refs": ["a", "b"], "value": 0}]}
    rep = run(spec)
    assert rep.conflicts == [] and rep.redundant is False


def test_a_real_error_near_the_origin_is_still_a_conflict():
    spec = {"plane": "Front",
            "entities": [{"id": "a", "type": "point", "x": 0, "y": 0},
                         {"id": "b", "type": "point", "x": 0.001, "y": 0}],
            "constraints": [], "dimensions": [{"type": "distance_h", "refs": ["a", "b"], "value": 0}]}
    assert run(spec).conflicts


def test_large_sketch_float_noise_is_not_a_conflict():
    spec = {"plane": "Front",
            "entities": [{"id": "l", "type": "line", "start": [1e6, 1e6], "end": [1e6 + 60, 1e6]}],
            "constraints": [{"type": "horizontal", "refs": ["l"]}],
            "dimensions": [{"type": "length", "ref": "l", "value": 60}]}
    assert run(spec).conflicts == []


def test_ground_conflict_message_says_point_is_not_at_the_origin():
    spec = {"plane": "Front",
            "entities": [{"id": "p", "type": "point", "x": 5, "y": 5}],
            "constraints": [{"type": "ground", "refs": ["p"]}], "dimensions": []}
    assert any("point is not at the origin" in c for c in run(spec).conflicts)
