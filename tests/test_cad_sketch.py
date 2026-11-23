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
    assert out["findings"] == []


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
    assert out["requests"] == ledger.used - before == 1  # get_variables only
    assert out["budget_left"] == ledger.remaining == 99
    ctx.read(REF)
    before = ledger.used
    out = cad_sketch(ctx, REF, "S1", SPEC, apply=True)
    assert out["requests"] == ledger.used - before == 2  # get_variables + add_feature


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
