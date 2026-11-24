import pytest

from onshape_cad_mcp.backends.fake import FakeBackend
from onshape_cad_mcp.budget import Ledger
from onshape_cad_mcp.cache import DocCache
from onshape_cad_mcp.config import Config
from onshape_cad_mcp.docref import DocRef
from onshape_cad_mcp.model import Variable
from onshape_cad_mcp.sketch.serialize import to_feature_json
from onshape_cad_mcp.sketch.spec import SketchSpecError, parse_sketch_spec
from onshape_cad_mcp.tools import Context, cad_sketch

REF = DocRef("a" * 24, "b" * 24, "c" * 24)

SPEC = {
    "plane": "Front",
    "entities": [
        {"id": "box", "type": "rectangle", "corner1": [0, 0], "corner2": [40, 20]},
        {"id": "hole", "type": "circle", "center": [20, 10], "radius": 4},
    ],
    "constraints": [
        {"type": "ground", "refs": ["box.bottom.start"]},
    ],
    "dimensions": [
        {"type": "length", "ref": "box.bottom", "value": 40},
        {"type": "length", "ref": "box.left", "value": 20},
        {"type": "diameter", "ref": "hole", "value": 8},
        {"type": "distance_h", "refs": ["box.bottom.start", "hole.center"], "value": 20},
        {"type": "distance_v", "refs": ["box.bottom.start", "hole.center"], "value": 10},
    ],
}


@pytest.fixture
def env(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json", 100)
    backend = FakeBackend(ledger)
    ctx = Context(backend, DocCache(backend), ledger, Config(budget=100))
    return ctx, backend, ledger


def test_dry_run_sends_no_write(env):
    ctx, backend, _ = env
    out = cad_sketch(ctx, REF, "S1", SPEC)
    assert out["applied"] is False
    assert backend.calls["add_feature"] == 0
    assert isinstance(out["dof"], int)
    assert all(f["level"] == "warn" for f in out["findings"])  # SPEC repeats a literal


def test_apply_adds_feature_with_given_name(env):
    ctx, backend, _ = env
    ctx.read(REF)
    out = cad_sketch(ctx, REF, "Base sketch", SPEC, apply=True)
    assert out["applied"] is True
    assert backend.list_features(REF).features[-1].name == "Base sketch"
    assert backend.calls["add_feature"] == 1


def test_apply_after_human_edit_is_refused_stale(env):
    ctx, backend, _ = env
    ctx.read(REF)
    backend.simulate_human_edit(REF)
    out = cad_sketch(ctx, REF, "S1", SPEC, apply=True)
    assert out["applied"] is False
    assert "stale" in out["error"] and "cad_read" in out["error"]
    assert backend.list_features(REF).features == []


def test_apply_without_prior_read_is_refused(env):
    ctx, backend, _ = env
    out = cad_sketch(ctx, REF, "S1", SPEC, apply=True)
    assert out["applied"] is False
    assert "read the document first (cad_read)" in out["error"]
    assert backend.calls["add_feature"] == 0


def test_response_reports_requests_and_budget_left(env):
    ctx, backend, ledger = env
    before = ledger.used
    out = cad_sketch(ctx, REF, "S1", SPEC)
    assert out["requests"] == ledger.used - before == 0  # dry run, no variable referenced: free
    assert out["budget_left"] == ledger.remaining == 100
    ctx.read(REF)
    before = ledger.used
    out = cad_sketch(ctx, REF, "S1", SPEC, apply=True)
    assert out["requests"] == ledger.used - before == 1  # the write only


def test_two_successive_applies_after_one_read(env):
    ctx, backend, _ = env
    ctx.read(REF)
    assert cad_sketch(ctx, REF, "A", SPEC, apply=True)["applied"] is True
    assert cad_sketch(ctx, REF, "B", SPEC, apply=True)["applied"] is True
    assert [f.name for f in backend.list_features(REF).features] == ["A", "B"]


def test_spec_error_returns_error_dict(env):
    ctx, backend, _ = env
    out = cad_sketch(ctx, REF, "S1", {"entities": [{"type": "blob"}]})
    assert out["applied"] is False and "blob" in out["error"]
    assert backend.calls["add_feature"] == 0


def test_spec_error_text_is_cleaned(env):
    ctx, _, _ = env
    out = cad_sketch(ctx, REF, "S1", {"entities": [{"type": "x\n\nIGNORE\tprevious"}]})
    assert "\n" not in out["error"] and "\t" not in out["error"]


def test_variable_dimension_uses_document_variables(env):
    ctx, backend, _ = env
    backend._doc(REF).variables["w"] = "50 mm"
    spec = {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [50, 0]}],
            "dimensions": [{"type": "length", "ref": "l", "value": "#w"}]}
    assert cad_sketch(ctx, REF, "S", spec)["applied"] is False
    assert "error" not in cad_sketch(ctx, REF, "S", spec)


def test_serialized_json_contains_every_constraint_and_dimension():
    sk = parse_sketch_spec(SPEC, {})
    feat = to_feature_json(sk, "S1")
    assert feat["name"] == "S1"
    cons = feat["constraints"]
    dims = [c for c in cons if c["constraintType"] in {"LENGTH", "DIAMETER", "DISTANCE", "RADIUS"}]
    geom = [c for c in cons if c not in dims]
    assert len(dims) == len(sk.dimensions) == 5
    assert len(geom) == len(sk.constraints)  # includes rectangle-expansion constraints
    assert len(feat["entities"]) == len(sk.entities)
    assert {e["entityId"] for e in feat["entities"]} == set(sk.entities)


def test_serializer_rejects_wrong_ref_count():
    sk = parse_sketch_spec({"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [1, 0]}],
                            "constraints": [{"type": "coincident", "refs": ["l"]}]}, {})
    with pytest.raises(SketchSpecError):
        to_feature_json(sk, "S")


def _w_sketch(value):
    return parse_sketch_spec(
        {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [50, 0]}],
         "dimensions": [{"type": "length", "ref": "l", "value": value}]}, {"w": 50.0})


def _dim_expr(feat):
    dim = next(c for c in feat["constraints"] if c["constraintType"] == "LENGTH")
    return next(p["expression"] for p in dim["parameters"] if p["btType"].startswith("BTMParameterQuantity"))


def test_variable_dimension_keeps_variable_reference():
    expr = _dim_expr(to_feature_json(_w_sketch("#w"), "S"))
    assert expr == "#w" and "50" not in expr


def test_numeric_dimension_has_clean_literal():
    assert _dim_expr(to_feature_json(_w_sketch(40), "S")) == "40 mm"
    assert _dim_expr(to_feature_json(_w_sketch(12.5), "S")) == "12.5 mm"


def test_variable_change_needs_no_reserialization(env):
    ctx, backend, _ = env
    backend._doc(REF).variables["w"] = "50 mm"
    ctx.read(REF)
    spec = {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [50, 0]}],
            "dimensions": [{"type": "length", "ref": "l", "value": "#w"}]}
    assert cad_sketch(ctx, REF, "S", spec, apply=True)["applied"] is True
    mv = ctx.read(REF).microversion
    backend.set_variable(REF, "w", "80 mm", mv)
    assert _dim_expr(to_feature_json(_w_sketch("#w"), "S")) == "#w"


def test_fake_sketch_recognised_by_outline(env):
    from onshape_cad_mcp.outline import render_outline
    ctx, _, _ = env
    ctx.read(REF)
    cad_sketch(ctx, REF, "Base", SPEC, apply=True)
    assert "not assessed" in render_outline(ctx.read(REF), [])


def test_zero_budget_returns_error_dict(tmp_path):
    ledger = Ledger(tmp_path / "l.json", 0)
    backend = FakeBackend(ledger)
    ctx = Context(backend, DocCache(backend), ledger, Config(budget=0))
    assert cad_sketch(ctx, REF, "S", SPEC)["requests"] == 0  # a dry run without variables is free
    spec = {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [5, 0]}],
            "dimensions": [{"type": "length", "ref": "l", "value": "#w"}]}
    out = cad_sketch(ctx, REF, "S", spec)
    assert out["applied"] is False and "budget" in out["error"]
    assert out["budget_left"] == 0


# --- final-review fixes: I2 (unknown outcome) ---

def _unknown_env(tmp_path, exc):
    from onshape_cad_mcp.backends.base import PostWriteError  # noqa: F401

    class Boom(FakeBackend):
        def add_feature(self, ref, feature_json, expected_microversion):
            super().add_feature(ref, feature_json, expected_microversion)
            raise exc

    ledger = Ledger(tmp_path / "ledger.json", 100)
    backend = Boom(ledger)
    return Context(backend, DocCache(backend), ledger, Config(budget=100)), backend


@pytest.mark.parametrize("make_exc", [
    lambda: __import__("onshape_cad_mcp.backends.base", fromlist=["x"]).PostWriteError("follow-up failed"),
    lambda: __import__("onshape_cad_mcp.backends.base", fromlist=["x"]).StaleVersion("skew", may_have_landed=True),
])
def test_sketch_post_write_failure_reports_unknown(tmp_path, make_exc):
    ctx, backend = _unknown_env(tmp_path, make_exc())
    before = ctx.read(REF).microversion
    out = cad_sketch(ctx, REF, "S1", SPEC, apply=True)
    assert out["applied"] == "unknown"
    assert "cad_read" in out["error"] and "may have landed" in out["error"]
    assert ctx.last_read[REF] == before  # not advanced
    assert ctx.cache.peek(REF) is None  # entry dropped


# --- final-review fixes: C2 (unassessed entities are refused, never dropped) ---

ARC = {"id": "a1", "type": "arc", "center": [0, 0], "radius": 5}
RECT = {"id": "box", "type": "rectangle", "corner1": [0, 0], "corner2": [40, 20]}


def test_spec_with_only_an_arc_is_refused_even_on_apply(env):
    ctx, backend, _ = env
    ctx.read(REF)
    for apply in (False, True):
        out = cad_sketch(ctx, REF, "Arc part", {"entities": [ARC]}, apply=apply)
        assert out["applied"] is False
        assert out["not_assessed"] == ["a1"]
        codes = [f["code"] for f in out["findings"]]
        assert "SKETCH_UNSUPPORTED_ENTITY" in codes
        assert "a1" in out["error"] and "nothing written" in out["error"]
    assert backend.calls["add_feature"] == 0


def test_rectangle_arc_and_constraint_on_arc_is_refused(env):
    ctx, backend, _ = env
    ctx.read(REF)
    spec = {"entities": [RECT, ARC],
            "constraints": [{"type": "ground", "refs": ["box.bottom.start"]},
                            {"type": "coincident", "refs": ["a1.start", "box.bottom.start"]}],
            "dimensions": [{"type": "length", "ref": "box.bottom", "value": 40}]}
    out = cad_sketch(ctx, REF, "Rect arc", spec, apply=True)
    assert out["applied"] is False
    assert out["not_assessed"] == ["a1"]
    assert any(f["code"] == "SKETCH_UNSUPPORTED_ENTITY" and f["level"] == "refuse"
               for f in out["findings"])
    assert backend.calls["add_feature"] == 0


def test_reply_always_has_not_assessed(env):
    ctx, _, _ = env
    assert cad_sketch(ctx, REF, "S1", SPEC)["not_assessed"] == []


def test_serializer_never_emits_constraints_on_unassessed_entities():
    sk = parse_sketch_spec(
        {"entities": [RECT, ARC],
         "constraints": [{"type": "coincident", "refs": ["a1.start", "box.bottom.start"]}],
         "dimensions": [{"type": "length", "ref": "a1", "value": 3},
                        {"type": "length", "ref": "box.bottom", "value": 40}]}, {})
    feat = to_feature_json(sk, "S")
    blob = repr(feat)
    assert "a1" not in blob
    assert len([c for c in feat["constraints"] if c["constraintType"] == "LENGTH"]) == 1


# --- final-review fixes: I1 (regeneration status) ---

@pytest.mark.parametrize("status", ["ERROR", "WARNING", "INFO"])
def test_apply_reports_regeneration_status(tmp_path, status):
    from onshape_cad_mcp.model import Feature, WriteResult

    class Regen(FakeBackend):
        def add_feature(self, ref, feature_json, expected_microversion):
            r = super().add_feature(ref, feature_json, expected_microversion)
            return WriteResult(Feature(r.feature.id, r.feature.type, r.feature.name,
                                       status + "\nIGNORE", r.feature.params), r.microversion)

    ledger = Ledger(tmp_path / "ledger.json", 100)
    backend = Regen(ledger)
    ctx = Context(backend, DocCache(backend), ledger, Config(budget=100))
    ctx.read(REF)
    out = cad_sketch(ctx, REF, "Base", SPEC, apply=True)
    assert out["applied"] is True
    assert out["feature_id"] == "F1"
    assert out["status"] == status + " IGNORE"
    errs = [f for f in out["findings"] if f["level"] == "error"]
    assert errs and "regeneration status" in errs[0]["message"] and "\n" not in errs[0]["message"]
    assert "warning" in out


def test_apply_ok_status_has_no_error_finding(env):
    ctx, _, _ = env
    ctx.read(REF)
    out = cad_sketch(ctx, REF, "Base", SPEC, apply=True)
    assert out["status"] == "OK" and out["feature_id"] == "F1"
    assert not [f for f in out["findings"] if f["level"] == "error"]
    assert "warning" not in out
