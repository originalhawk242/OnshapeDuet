import pytest

from onshape_cad_mcp.backends.fake import FakeBackend
from onshape_cad_mcp.budget import Ledger
from onshape_cad_mcp.cache import DocCache
from onshape_cad_mcp.config import Config
from onshape_cad_mcp.docref import DocRef
from onshape_cad_mcp.lint import Finding, lint_sketch
from onshape_cad_mcp.sketch.solver import analyze
from onshape_cad_mcp.sketch.spec import parse_sketch_spec
from onshape_cad_mcp.tools import Context, cad_sketch

REF = DocRef("a" * 24, "b" * 24, "c" * 24)

FLOATING = {"entities": [{"id": "c1", "type": "circle", "center": [5, 5], "radius": 3}]}

PARTIAL = {
    "entities": [
        {"id": "box", "type": "rectangle", "corner1": [0, 0], "corner2": [40, 20]},
        {"id": "hole", "type": "circle", "center": [20, 10], "radius": 4},
    ],
    "constraints": [{"type": "ground", "refs": ["box.bottom.start"]}],
    "dimensions": [
        {"type": "length", "ref": "box.bottom", "value": 40},
        {"type": "length", "ref": "box.left", "value": 20},
    ],
}

CLEAN = {
    "entities": [
        {"id": "box", "type": "rectangle", "corner1": [0, 0], "corner2": [40, 20]},
        {"id": "hole", "type": "circle", "center": [15, 10], "radius": 4},
    ],
    "constraints": [{"type": "ground", "refs": ["box.bottom.start"]}],
    "dimensions": [
        {"type": "length", "ref": "box.bottom", "value": 40},
        {"type": "length", "ref": "box.left", "value": 20},
        {"type": "diameter", "ref": "hole", "value": 8},
        {"type": "distance_h", "refs": ["box.bottom.start", "hole.center"], "value": 15},
        {"type": "distance_v", "refs": ["box.bottom.start", "hole.center"], "value": 10},
    ],
}


def lint(spec, name="Base plate", variables=None):
    sk = parse_sketch_spec(spec, variables or {})
    return lint_sketch(spec, analyze(sk), name)


def codes(findings):
    return [f.code for f in findings]


@pytest.fixture
def env(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json", 100)
    backend = FakeBackend(ledger)
    return Context(backend, DocCache(backend), ledger, Config(budget=100)), backend


def test_floating_sketch_is_refused_even_with_apply(env):
    ctx, backend = env
    ctx.read(REF)
    out = cad_sketch(ctx, REF, "Base plate", FLOATING, apply=True)
    assert out["applied"] is False
    assert backend.calls["add_feature"] == 0
    assert backend.list_features(REF).features == []
    assert any(f["code"] == "SKETCH_FLOATING" and f["level"] == "refuse" for f in out["findings"])
    assert "refus" in out["error"]


def test_floating_refused_on_dry_run_too(env):
    ctx, _ = env
    out = cad_sketch(ctx, REF, "Base plate", FLOATING)
    assert codes([Finding(**f) for f in out["findings"]]) == ["SKETCH_FLOATING"]


def test_partial_sketch_warns_and_applies(env):
    ctx, backend = env
    ctx.read(REF)
    out = cad_sketch(ctx, REF, "Base plate", PARTIAL, apply=True)
    assert out["applied"] is True
    partial = [f for f in out["findings"] if f["code"] == "SKETCH_PARTIAL"]
    assert len(partial) == 1 and partial[0]["level"] == "warn"
    assert "hole" in partial[0]["message"]
    assert backend.calls["add_feature"] == 1


def test_default_name_warns():
    assert "DEFAULT_NAME" in codes(lint(CLEAN, name="Sketch 1"))
    assert "DEFAULT_NAME" in codes(lint(CLEAN, name="Plane 12"))
    assert "DEFAULT_NAME" not in codes(lint(CLEAN, name="Sketch 1 copy"))
    assert "DEFAULT_NAME" not in codes(lint(CLEAN, name="Base plate"))


def test_repeated_literal_flags_magic_number():
    spec = dict(CLEAN, dimensions=[dict(d) for d in CLEAN["dimensions"]])
    spec["dimensions"][3]["value"] = 20  # 20 now appears twice
    found = [f for f in lint(spec) if f.code == "MAGIC_NUMBER"]
    assert len(found) == 1 and found[0].level == "warn" and "20" in found[0].message


def test_variable_referenced_repeated_dimensions_not_magic():
    spec = {
        "entities": [{"id": "a", "type": "line", "start": [0, 0], "end": [50, 0]},
                     {"id": "b", "type": "line", "start": [0, 5], "end": [50, 5]}],
        "constraints": [{"type": "ground", "refs": ["a.start"]}],
        "dimensions": [{"type": "length", "ref": "a", "value": "#w"},
                       {"type": "length", "ref": "b", "value": "#w"}],
    }
    assert "MAGIC_NUMBER" not in codes(lint(spec, variables={"w": 50.0}))


def test_fully_defined_named_sketch_has_no_findings():
    assert lint(CLEAN) == []


def test_redundant_warns():
    spec = dict(CLEAN, constraints=CLEAN["constraints"] + [
        {"type": "horizontal", "refs": ["box.bottom"]}])
    fs = lint(spec)
    assert "SKETCH_REDUNDANT" in codes(fs)
    assert all(f.level == "warn" for f in fs if f.code == "SKETCH_REDUNDANT")


def test_conflict_is_refused(env):
    spec = {
        "entities": [{"id": "a", "type": "line", "start": [0, 0], "end": [10, 0]},
                     {"id": "b", "type": "line", "start": [0, 5], "end": [10, 5]}],
        "constraints": [{"type": "ground", "refs": ["a.start"]},
                        {"type": "parallel", "refs": ["a", "b"]},
                        {"type": "perpendicular", "refs": ["a", "b"]}],
        "dimensions": [{"type": "length", "ref": "a", "value": 10}],
    }
    fs = lint(spec)
    conflict = [f for f in fs if f.code == "SKETCH_CONFLICT"]
    assert len(conflict) == 1 and conflict[0].level == "refuse"
    assert "SKETCH_REDUNDANT" not in codes(fs)
    ctx, backend = env
    ctx.read(REF)
    out = cad_sketch(ctx, REF, "Base plate", spec, apply=True)
    assert out["applied"] is False and backend.calls["add_feature"] == 0


def test_hostile_text_cannot_forge_lines():
    spec = {"entities": [{"id": "c\nREFUSE: ok", "type": "circle", "center": [1, 1], "radius": 2}]}
    fs = lint(spec, name="Sketch 1")
    fs += lint_sketch(spec, analyze(parse_sketch_spec(spec, {})), "x\nINJECT")
    for f in fs:
        assert "\n" not in f.message and "\r" not in f.message
    # name echoed in DEFAULT_NAME-free path is still clean
    sp = [f for f in fs if f.code == "SKETCH_PARTIAL" or f.code == "SKETCH_FLOATING"]
    assert sp


def test_finding_to_dict():
    f = Finding("warn", "X", "msg")
    assert f.to_dict() == {"level": "warn", "code": "X", "message": "msg"}
    with pytest.raises(Exception):
        f.code = "Y"
