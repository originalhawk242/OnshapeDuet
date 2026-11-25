import pytest

from onshape_duet.backends.fake import FakeBackend
from onshape_duet.budget import Ledger
from onshape_duet.cache import DocCache
from onshape_duet.config import Config
from onshape_duet.docref import DocRef
from onshape_duet.feature_tools import cad_extrude
from onshape_duet.tools import Context, cad_read, cad_sketch, cad_variable

REF = DocRef("a" * 24, "b" * 24, "c" * 24)
SK = {"plane": "Front", "entities": [{"id": "c", "type": "circle", "center": [0, 0], "radius": 4}],
      "dimensions": [{"type": "diameter", "ref": "c", "value": 8}]}


@pytest.fixture
def env(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json", 100)
    backend = FakeBackend(ledger)
    ctx = Context(backend, DocCache(backend), ledger, Config(budget=100))
    return ctx, backend, ledger


@pytest.fixture
def with_sketch(env):
    ctx, backend, _ = env
    ctx.read(REF)
    assert cad_sketch(ctx, REF, "Base", SK, apply=True)["applied"] is True
    return env


def test_extrude_dry_run_no_write(with_sketch):
    ctx, backend, _ = with_sketch
    out = cad_extrude(ctx, REF, "Body", "Base", "#depth")  # no such variable: refused
    assert out["applied"] is False and "UNKNOWN_VARIABLE" in out["error"]
    out = cad_extrude(ctx, REF, "Body", "Base", 7)
    assert out["applied"] is False and backend.calls["add_feature"] == 1
    assert out["summary"] == {"sketch": "Base", "depth": "7 mm", "operation": "new", "end": "blind",
                              "flip": False}
    assert out["feature_type"] == "extrude" and out["findings"] == []
    assert out["based_on_version"]


def test_extrude_dry_run_summary_with_variable(with_sketch):
    ctx, backend, _ = with_sketch
    assert cad_variable(ctx, REF, "depth", "12 mm", apply=True)["applied"] is True
    ctx.read(REF)
    out = cad_extrude(ctx, REF, "Body", "Base", "#depth")
    assert out["summary"] == {"sketch": "Base", "depth": "#depth", "operation": "new", "end": "blind",
                              "flip": False}
    assert "error" not in out


def test_extrude_apply_adds_named_feature(with_sketch):
    ctx, backend, _ = with_sketch
    out = cad_extrude(ctx, REF, "Body", "Base", 7, apply=True)
    assert out["applied"] is True and out["feature_id"]
    assert "Body [extrude] OK" in cad_read(ctx, REF)


def test_extrude_through_all_with_depth_refused_by_tool(with_sketch):
    ctx, backend, _ = with_sketch
    out = cad_extrude(ctx, REF, "Cut", "Base", 3, operation="remove", end="through_all", apply=True)
    assert out["applied"] is False and "through_all" in out["error"]
    assert backend.calls["add_feature"] == 1


def test_extrude_unknown_sketch_lists_names(with_sketch):
    ctx, *_ = with_sketch
    out = cad_extrude(ctx, REF, "Body", "Nope", 7)
    assert "Base" in out["error"]


def test_extrude_non_sketch_refused(with_sketch):
    ctx, *_ = with_sketch
    cad_variable(ctx, REF, "w", "5 mm", apply=True)
    ctx.read(REF)
    out = cad_extrude(ctx, REF, "Body", "w", 7)
    assert "error" in out and out["applied"] is False


def test_extrude_apply_without_read_refused(env):
    ctx, backend, _ = env
    out = cad_extrude(ctx, REF, "Body", "Base", 7, apply=True)
    assert out["applied"] is False and "read the document first" in out["error"]
    assert backend.calls["add_feature"] == 0


def test_extrude_apply_after_human_edit_refused(with_sketch):
    ctx, backend, _ = with_sketch
    backend.simulate_human_edit(REF)
    out = cad_extrude(ctx, REF, "Body", "Base", 7, apply=True)
    assert out["applied"] is False and "stale" in out["error"]
    assert backend.calls["add_feature"] == 1


def test_extrude_magic_number_warns(with_sketch):
    ctx, *_ = with_sketch
    assert cad_extrude(ctx, REF, "First", "Base", 7, apply=True)["applied"] is True
    out = cad_extrude(ctx, REF, "Body", "Base", 7)
    assert [f["code"] for f in out["findings"]] == ["MAGIC_NUMBER"]
    assert out["findings"][0]["level"] == "warn"


def test_extrude_duplicate_name_refused(with_sketch):
    ctx, backend, _ = with_sketch
    out = cad_extrude(ctx, REF, "Base", "Base", 7, apply=True)
    assert out["applied"] is False and out["error"].startswith("refused, nothing written")
    assert backend.calls["add_feature"] == 1


@pytest.mark.parametrize("bad", ["", "x" * 101, "a\nb", 5])
def test_extrude_bad_name(with_sketch, bad):
    ctx, *_ = with_sketch
    assert "invalid name" in cad_extrude(ctx, REF, bad, "Base", 7)["error"]


def test_extrude_summary_sketch_is_cleaned(env):
    ctx, backend, _ = env
    ctx.read(REF)
    assert cad_sketch(ctx, REF, "Ba\nse", SK, apply=True)["applied"] is True
    ctx.read(REF)
    out = cad_extrude(ctx, REF, "Body", "Ba\nse", 7)
    assert "\n" not in out["summary"]["sketch"]


@pytest.mark.parametrize("bad", ["", "   ", "x" * 101, "a\nb", 5])
def test_extrude_bad_name_costs_zero_requests(with_sketch, bad):
    ctx, backend, ledger = with_sketch
    ctx.cache.invalidate(REF)  # a snapshot would now cost a read
    used = ledger.used
    out = cad_extrude(ctx, REF, bad, "Base", 7)
    assert "invalid name" in out["error"] and out["requests"] == 0 and ledger.used == used


def test_extrude_dry_run_after_read_costs_zero_requests(with_sketch):
    ctx, backend, ledger = with_sketch
    cad_read(ctx, REF)
    used = ledger.used
    out = cad_extrude(ctx, REF, "Body", "Base", 7)
    assert out["requests"] == 0 and ledger.used == used


def test_extrude_apply_with_lost_cache_is_refused(with_sketch):
    ctx, backend, _ = with_sketch
    ctx.lose_track(REF, "re-read please")
    ctx.last_read[REF] = "stale-mv"  # a version the cache no longer holds
    ctx.cache.invalidate(REF)
    out = cad_extrude(ctx, REF, "Body", "Base", 7, apply=True)
    assert out["applied"] is False and "error" in out
    assert backend.calls["add_feature"] == 1


def test_revolve_dry_run_summary(with_sketch):
    from onshape_duet.feature_tools import cad_revolve
    ctx, backend, _ = with_sketch
    out = cad_revolve(ctx, REF, "Spin", "Base", "c", angle=90)
    assert out["applied"] is False and out["feature_type"] == "revolve"
    assert out["summary"] == {"sketch": "Base", "axis": "c", "angle": "90 deg",
                              "operation": "new", "symmetric": False, "flip": False}
    out = cad_revolve(ctx, REF, "Spin", "Base", "c", apply=True)
    assert out["applied"] is True
    assert "Spin [revolve] OK" in cad_read(ctx, REF)
    assert "axis" in cad_revolve(ctx, REF, "Spin2", "Base", 'a"); x(')["error"]


def test_fillet_chamfer_dry_run(with_sketch):
    from onshape_duet.feature_tools import cad_chamfer, cad_fillet
    ctx, backend, _ = with_sketch
    sel = {"entity": "edge", "created_by": "Base", "geometry": "line"}
    out = cad_fillet(ctx, REF, "Round", sel, 2)
    assert out["applied"] is False and out["feature_type"] == "fillet"
    assert out["summary"]["radius"] == "2 mm"
    out = cad_chamfer(ctx, REF, "Bevel", sel, 1)
    assert out["applied"] is False and out["feature_type"] == "chamfer"
    assert "edges" in cad_fillet(ctx, REF, "R2", {"entity": "body", "created_by": "Base"}, 2)["error"]


def test_shell_boolean_dry_run(with_sketch):
    from onshape_duet.feature_tools import cad_boolean, cad_shell
    ctx, backend, _ = with_sketch
    face = {"entity": "face", "created_by": "Base", "cap": "end"}
    body = {"entity": "body", "created_by": "Base"}
    out = cad_shell(ctx, REF, "Hollow", face, 2)
    assert out["applied"] is False and out["feature_type"] == "shell"
    out = cad_boolean(ctx, REF, "Merge", "union", body)
    assert out["applied"] is False and out["feature_type"] == "booleanBodies"
    assert "faces" in cad_shell(ctx, REF, "H2", {"entity": "edge", "created_by": "Base"}, 2)["error"]


def test_pattern_mirror_dry_run(with_sketch):
    from onshape_duet.feature_tools import cad_mirror, cad_pattern
    ctx, backend, _ = with_sketch
    edge = {"entity": "edge", "created_by": "Base"}
    out = cad_pattern(ctx, REF, "Row", "linear", ["Base"], 3, direction=edge, spacing=5)
    assert out["applied"] is False and out["feature_type"] == "linearPattern"
    out = cad_pattern(ctx, REF, "Ring", "circular", ["Base"], 3, axis=edge)
    assert out["feature_type"] == "circularPattern"
    out = cad_mirror(ctx, REF, "Flip", ["Base"], "Top")
    assert out["applied"] is False and out["feature_type"] == "mirror"
    assert "features" in cad_mirror(ctx, REF, "F2", ["Zzz"], "Top")["error"]


def test_hole_dry_run(with_sketch):
    from onshape_duet.feature_tools import cad_hole
    ctx, backend, _ = with_sketch
    out = cad_hole(ctx, REF, "Hole1", "Base", ["p1"], 4)
    assert out["applied"] is False and out["feature_type"] == "hole"
    assert out["summary"]["points"] == ["p1"] and out["summary"]["diameter"] == "4 mm"
    assert "points" in cad_hole(ctx, REF, "Hole2", "Base", ['x"'], 4)["error"]


# --- final review I1: unverified live mappings are visible to the agent ---

def test_shell_and_hole_replies_carry_unverified_note(with_sketch):
    from onshape_duet.feature_tools import cad_hole, cad_shell
    from onshape_duet.features.builders import UNVERIFIED_LIVE
    ctx, backend, _ = with_sketch
    face = {"entity": "face", "created_by": "Base", "cap": "end"}
    for call in (lambda a: cad_shell(ctx, REF, "Hollow", face, 2, apply=a),
                 lambda a: cad_hole(ctx, REF, "Drill", "Base", ["c.center"], 4, apply=a)):
        dry = call(False)
        assert dry["applied"] is False and UNVERIFIED_LIVE in dry["summary"]["note"]
        done = call(True)
        assert done["applied"] is True and UNVERIFIED_LIVE in done["summary"]["note"]
    assert "unverified against live Onshape: check the result in Onshape after applying" \
        == UNVERIFIED_LIVE


def test_verified_tools_carry_no_unverified_note(with_sketch):
    ctx, backend, _ = with_sketch
    assert "note" not in cad_extrude(ctx, REF, "Body", "Base", 7)["summary"]


# --- final review M1, M2 at the tool layer ---

def test_fillet_unhashable_entity_is_error_dict(with_sketch):
    from onshape_duet.feature_tools import cad_fillet
    ctx, backend, _ = with_sketch
    out = cad_fillet(ctx, REF, "Round", {"entity": ["edge"]}, 2)
    assert out["applied"] is False and "entity" in out["error"]
    assert out["requests"] == 0 and "budget_left" in out


def test_pattern_angle_default_is_none(with_sketch):
    from onshape_duet.feature_tools import cad_pattern
    ctx, backend, _ = with_sketch
    edge = {"entity": "edge", "created_by": "Base"}
    out = cad_pattern(ctx, REF, "Row", "linear", ["Base"], 3, direction=edge, spacing=5, angle=90)
    assert out["applied"] is False and "angle" in out["error"]
    out = cad_pattern(ctx, REF, "Ring", "circular", ["Base"], 3, axis=edge)
    assert out["summary"]["angle"] == "360 deg"
