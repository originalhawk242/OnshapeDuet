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
    assert out["summary"] == {"sketch": "Base", "depth": "7 mm", "operation": "new", "end": "blind"}
    assert out["feature_type"] == "extrude" and out["findings"] == []
    assert out["based_on_version"]


def test_extrude_dry_run_summary_with_variable(with_sketch):
    ctx, backend, _ = with_sketch
    assert cad_variable(ctx, REF, "depth", "12 mm", apply=True)["applied"] is True
    ctx.read(REF)
    out = cad_extrude(ctx, REF, "Body", "Base", "#depth")
    assert out["summary"] == {"sketch": "Base", "depth": "#depth", "operation": "new", "end": "blind"}
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
