from onshape_cad_mcp.model import Feature, FeatureList, Variable
from onshape_cad_mcp.outline import render_outline
from onshape_cad_mcp.sketch.solver import DofReport


def _fl(*feats):
    return FeatureList("m" * 24, list(feats))


def test_outline_lists_features_in_order():
    fl = _fl(
        Feature("F1", "newSketch", "Base profile", "OK"),
        Feature("F2", "extrude", "Boss", "OK", {"depth": "10 mm"}),
    )
    out = render_outline(fl, [])
    lines = out.splitlines()
    i1 = next(i for i, l in enumerate(lines) if "Base profile" in l)
    i2 = next(i for i, l in enumerate(lines) if "Boss" in l)
    assert i1 < i2
    assert "1. Base profile [newSketch] OK" in out
    assert "2. Boss [extrude] OK" in out
    assert "depth=10 mm" in out
    assert "dof=" not in out.split("Boss")[0] or "not assessed" in out


def test_default_name_is_flagged():
    fl = _fl(Feature("F1", "extrude", "Extrude 1", "OK"),
             Feature("F2", "extrude", "Mount boss", "OK"),
             Feature("F3", "extrude", "extrude 2", "OK"))
    out = render_outline(fl, [])
    assert "Extrude 1 [extrude] OK ⚠ default name" in out
    assert "Mount boss [extrude] OK\n" in out + "\n"
    assert "extrude 2 [extrude] OK ⚠" not in out


def test_variables_section_lists_expressions():
    out = render_outline(_fl(), [Variable("width", "40 mm"), Variable("h", "#width * 2")])
    assert "Variables" in out
    assert "width = 40 mm" in out
    assert "h = #width * 2" in out


def test_unknown_feature_type_still_listed():
    fl = _fl(Feature("F1", "weirdThing", "Odd", "SUPPRESSED", {"x": {"nested": 1}, "y": [1]}))
    out = render_outline(fl, [])
    assert "1. Odd [weirdThing] SUPPRESSED" in out


def test_sketch_dof_and_not_assessed():
    fl = _fl(Feature("S1", "newSketch", "A", "OK"), Feature("S2", "newSketch", "B", "OK"),
             Feature("S3", "newSketch", "C", "OK"))
    out = render_outline(fl, [], {"S1": DofReport(2, False, [], [])})
    assert "A [newSketch] OK dof=2" in out
    assert "B [newSketch] OK not assessed" in out
    assert "C [newSketch] OK not assessed" in render_outline(fl, [], None)


def test_conflict_rendering():
    fl = _fl(Feature("S1", "newSketch", "A", "OK"))
    rep = DofReport(0, True, [], [], conflicts=["horizontal l1 violated", "second"])
    out = render_outline(fl, [], {"S1": rep})
    assert "CONFLICT: horizontal l1 violated" in out
    assert "dof=" not in out


def test_empty():
    out = render_outline(_fl(), [])
    assert "no features" in out.lower()
    assert "Variables" not in out
