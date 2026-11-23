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
