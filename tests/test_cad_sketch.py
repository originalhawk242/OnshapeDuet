import pytest

from onshape_duet.backends.fake import FakeBackend
from onshape_duet.budget import Ledger
from onshape_duet.cache import DocCache
from onshape_duet.config import Config
from onshape_duet.docref import DocRef
from onshape_duet.model import Variable
from onshape_duet.sketch.serialize import to_feature_json
from onshape_duet.sketch.spec import SketchSpecError, parse_sketch_spec
from onshape_duet.tools import Context, cad_read, cad_sketch

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


def _clean_spec():
    """SPEC without repeated literals, so a good sketch has no findings at all."""
    import copy
    spec = copy.deepcopy(SPEC)
    spec["entities"][1]["center"] = [15, 10]
    spec["dimensions"][3]["value"] = 15
    return spec


def test_dry_run_sends_no_write(env):
    ctx, backend, _ = env
    out = cad_sketch(ctx, REF, "Base plate", _clean_spec())
    assert out["applied"] is False
    assert backend.calls["add_feature"] == 0
    assert out["dof"] == 0
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
    ctx.read(REF)
    before = ledger.used
    out = cad_sketch(ctx, REF, "S1", SPEC)
    assert out["requests"] == ledger.used - before == 0  # dry run after a read: free
    assert out["budget_left"] == ledger.remaining == 98
    before = ledger.used
    out = cad_sketch(ctx, REF, "S1", SPEC, apply=True)
    assert out["requests"] == ledger.used - before == 2  # backstop version check + the write


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
    """A sketch dimension stores the reference '#w', not the variable's value, so
    changing the variable leaves the serialized dimension unchanged: both
    serializations carry exactly '#w' and neither contains either value."""
    ctx, backend, _ = env
    sent = []
    real = backend.add_feature
    backend.add_feature = lambda ref, fj, expected_microversion: (
        sent.append(fj), real(ref, fj, expected_microversion))[1]
    backend._doc(REF).variables["w"] = "50 mm"
    ctx.read(REF)
    spec = {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [50, 0]}],
            "dimensions": [{"type": "length", "ref": "l", "value": "#w"}]}
    assert cad_sketch(ctx, REF, "S", spec, apply=True)["applied"] is True
    mv = ctx.read(REF).microversion
    backend.set_variable(REF, "w", "80 mm", mv)
    ctx.read(REF)
    spec2 = {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [80, 0]}],
             "dimensions": spec["dimensions"]}
    assert cad_sketch(ctx, REF, "S2", spec2, apply=True)["applied"] is True  # unique name (M7)
    assert len(sent) == 2
    import json
    assert _dim_expr(sent[0]) == _dim_expr(sent[1]) == "#w"
    for feat in sent:
        dim = next(c for c in feat["constraints"] if c["constraintType"] == "LENGTH")
        text = json.dumps(dim)
        assert "50 mm" not in text and "80 mm" not in text and "0.05" not in text and "0.08" not in text


@pytest.mark.parametrize("bad", ["#W", "#a-b", "#1x", "#a b", "#", "#a;b", "#_x"])
def test_hash_reference_with_invalid_name_is_rejected_before_serialising(env, bad):
    ctx, backend, _ = env
    spec = {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [50, 0]}],
            "dimensions": [{"type": "length", "ref": "l", "value": bad}]}
    out = cad_sketch(ctx, REF, "S", spec)
    assert out["applied"] is False and "error" in out
    assert backend.calls["add_feature"] == 0


def test_fake_sketch_recognised_by_outline(env):
    from onshape_duet.outline import render_outline
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
    from onshape_duet.backends.base import PostWriteError  # noqa: F401

    class Boom(FakeBackend):
        def add_feature(self, ref, feature_json, expected_microversion):
            super().add_feature(ref, feature_json, expected_microversion)
            raise exc

    ledger = Ledger(tmp_path / "ledger.json", 100)
    backend = Boom(ledger)
    return Context(backend, DocCache(backend), ledger, Config(budget=100)), backend


@pytest.mark.parametrize("make_exc", [
    lambda: __import__("onshape_duet.backends.base", fromlist=["x"]).PostWriteError("follow-up failed"),
    lambda: __import__("onshape_duet.backends.base", fromlist=["x"]).StaleVersion("skew", may_have_landed=True),
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
    from onshape_duet.model import Feature, WriteResult

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


def test_s1_sketch_with_variable_refuses_when_snapshot_not_at_last_read(env):
    ctx, backend, _ = env
    backend._doc(REF).variables["w"] = "50 mm"
    ctx.read(REF)
    ctx.cache.invalidate(REF)
    spec = {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [50, 0]}],
            "dimensions": [{"type": "length", "ref": "l", "value": "#w"}]}
    out = cad_sketch(ctx, REF, "S", spec, apply=True)
    assert out["applied"] is False and "stale" in out["error"]
    assert backend.calls["add_feature"] == 0


def test_s1_sketch_human_edit_after_read_refused_before_any_write(env):
    ctx, backend, _ = env
    ctx.read(REF)
    backend.simulate_human_edit(REF)
    out = cad_sketch(ctx, REF, "S", SPEC, apply=True)
    assert out["applied"] is False and "stale" in out["error"]
    assert backend.calls["add_feature"] == 0


# --- wave 2 (T10): last_read and request accounting on refusal/failure paths ---

def test_last_read_unchanged_after_a_stale_refusal(env):
    ctx, backend, _ = env
    mv = ctx.read(REF).microversion
    backend.simulate_human_edit(REF)
    out = cad_sketch(ctx, REF, "S1", SPEC, apply=True)
    assert out["applied"] is False and "stale" in out["error"]
    assert ctx.last_read[REF] == mv
    assert cad_sketch(ctx, REF, "S1", SPEC, apply=True)["applied"] is False  # still stale


def test_requests_and_budget_left_on_stale_and_budget_failure_paths(env):
    ctx, backend, ledger = env
    ctx.read(REF)
    backend.simulate_human_edit(REF)
    before = ledger.used
    out = cad_sketch(ctx, REF, "S1", SPEC, apply=True)
    assert out["requests"] == ledger.used - before == 1   # only the backstop check was spent
    assert out["budget_left"] == ledger.remaining


def test_budget_exhausted_mid_apply_reports_numbers(tmp_path):
    ledger = Ledger(tmp_path / "l.json", 2)
    backend = FakeBackend(ledger)
    ctx = Context(backend, DocCache(backend), ledger, Config(budget=2))
    ctx.read(REF)                                          # uses the whole budget (2)
    out = cad_sketch(ctx, REF, "S1", SPEC, apply=True)
    assert out["applied"] is False and "budget" in out["error"]
    assert out["requests"] == 0 and out["budget_left"] == 0
    assert backend.calls["add_feature"] == 0


def test_diameter_dimension_carries_label_placement():
    # Live: a UI-made DIAMETER has labelRatio and labelAngle; without them the label is not drawn.
    feat = to_feature_json(parse_sketch_spec(SPEC, {}), "S1")
    dia = next(c for c in feat["constraints"] if c["constraintType"] == "DIAMETER")
    ids = {p["parameterId"] for p in dia["parameters"]}
    assert {"localFirst", "length", "labelRatio", "labelAngle"} <= ids


# ---- sketches on plane features and planar faces ----
from onshape_duet.feature_tools import cad_extrude, cad_plane  # noqa: E402


def _plane_env(env):
    ctx, backend, ledger = env
    ctx.read(REF)
    assert cad_plane(ctx, REF, "Mid", "Top", 10, apply=True)["applied"] is True
    return env


def test_sketch_on_plane_feature(env):
    ctx, backend, ledger = _plane_env(env)
    spec = {**SPEC, "plane": {"plane": "Mid"}}
    out = cad_sketch(ctx, REF, "S", spec, apply=True)
    assert out["applied"] is True, out
    fl, _ = ctx.snapshot(REF)
    s = next(f for f in fl.features if f.name == "S")
    assert s.type == "newSketch"


def test_sketch_plane_param_in_json(env):
    ctx, backend, ledger = _plane_env(env)
    fl, _ = ctx.snapshot(REF)
    mid = next(f for f in fl.features if f.name == "Mid")
    from onshape_duet.features.refs import plane_param
    sk = parse_sketch_spec({**SPEC, "plane": {"plane": "Mid"}}, {})
    fj = to_feature_json(sk, "S", plane_param=plane_param("sketchPlane", sk.plane, fl))
    p = fj["parameters"][0]
    assert p["parameterId"] == "sketchPlane"
    assert mid.id in p["queries"][0]["queryString"]


def test_sketch_on_face_selector(env):
    ctx, backend, ledger = env
    ctx.read(REF)
    assert cad_sketch(ctx, REF, "Base", SPEC, apply=True)["applied"] is True
    assert cad_extrude(ctx, REF, "Body", "Base", 5, apply=True)["applied"] is True
    face = {"entity": "face", "created_by": "Body", "cap": "end"}
    out = cad_sketch(ctx, REF, "Top sk", {**SPEC, "plane": face})
    assert "error" not in out, out
    assert out["applied"] is False
    out = cad_sketch(ctx, REF, "Top sk", {**SPEC, "plane": face}, apply=True)
    assert out["applied"] is True, out


def test_sketch_on_nonplanar_selector_refused(env):
    ctx, backend, ledger = env
    ctx.read(REF)
    cad_sketch(ctx, REF, "Base", SPEC, apply=True)
    cad_extrude(ctx, REF, "Body", "Base", 5, apply=True)
    face = {"entity": "face", "created_by": "Body", "geometry": "cylinder"}
    out = cad_sketch(ctx, REF, "S2", {**SPEC, "plane": face}, apply=True)
    assert out["applied"] is False
    assert "planar" in out["error"]


def test_sketch_unknown_plane_feature_is_error(env):
    ctx, backend, ledger = env
    ctx.read(REF)
    out = cad_sketch(ctx, REF, "S", {**SPEC, "plane": {"plane": "Nope"}})
    assert out["applied"] is False and "Nope" in out["error"]


def test_sketch_standard_plane_still_zero_requests(env):
    """Zero requests once the document was read: the duplicate-name check (M7) needs the
    feature list, so without a cached read a dry run costs up to 2 requests."""
    ctx, backend, ledger = env
    cad_read(ctx, REF)
    before = ledger.used
    out = cad_sketch(ctx, REF, "S", SPEC)
    assert out["applied"] is False and "error" not in out
    assert ledger.used - before == 0


def test_parse_dict_plane_unchanged():
    sk = parse_sketch_spec({"plane": {"plane": "Mid"}, "entities": []}, {})
    assert sk.plane == {"plane": "Mid"}
    with pytest.raises(SketchSpecError):
        parse_sketch_spec({"plane": "Bogus", "entities": []}, {})


# --- final review M7: duplicate sketch names are refused like typed features ---

def test_sketch_duplicate_name_refused_on_dry_run_and_apply(env):
    ctx, backend, _ = env
    ctx.read(REF)
    assert cad_sketch(ctx, REF, "Base", SPEC, apply=True)["applied"] is True
    for apply in (False, True):
        out = cad_sketch(ctx, REF, "Base", SPEC, apply=apply)
        assert out["applied"] is False and "DUPLICATE_NAME" in out["error"]
        assert ("refuse", "DUPLICATE_NAME") in {(f["level"], f["code"]) for f in out["findings"]}
    assert backend.calls["add_feature"] == 1


def test_sketch_may_share_a_variable_name(env):
    ctx, backend, _ = env
    backend._doc(REF).variables["w"] = "5 mm"
    ctx.read(REF)
    assert "DUPLICATE_NAME" not in repr(cad_sketch(ctx, REF, "w", SPEC))


def test_sketch_dry_run_without_read_consults_document(env):
    ctx, backend, ledger = env
    out = cad_sketch(ctx, REF, "S", SPEC)
    assert out["applied"] is False and "error" not in out
    assert out["requests"] == 2 and out["based_on_version"] == "mv0"
    assert REF not in ctx.last_read  # a dry run is not a read
