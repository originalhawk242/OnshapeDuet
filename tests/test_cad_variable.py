import pytest

from onshape_cad_mcp.backends.fake import FakeBackend
from onshape_cad_mcp.budget import Ledger
from onshape_cad_mcp.cache import DocCache
from onshape_cad_mcp.config import Config
from onshape_cad_mcp.docref import DocRef
from onshape_cad_mcp.tools import Context, cad_variable

REF = DocRef("a" * 24, "b" * 24, "c" * 24)


def make(tmp_path, budget=100):
    ledger = Ledger(tmp_path / "ledger.json", budget)
    backend = FakeBackend(ledger)
    return Context(backend, DocCache(backend), ledger, Config(budget=budget)), backend, ledger


@pytest.fixture
def env(tmp_path):
    return make(tmp_path)


def test_upsert_updates_existing_not_duplicate(env):
    ctx, backend, _ = env
    backend._doc(REF).variables["w"] = "10 mm"
    ctx.read(REF)
    out = cad_variable(ctx, REF, "w", "20 mm", apply=True)
    assert out["applied"] is True and out["old"] == "10 mm" and out["new"] == "20 mm"
    vs = backend.get_variables(REF)
    assert [(v.name, v.expression) for v in vs] == [("w", "20 mm")]


def test_creates_when_missing(env):
    ctx, backend, _ = env
    ctx.read(REF)
    out = cad_variable(ctx, REF, "h", "#w * 2", apply=True)
    assert out["applied"] is True and out["old"] is None
    assert [(v.name, v.expression) for v in backend.get_variables(REF)] == [("h", "#w * 2")]


def test_dry_run_shows_old_and_new(env):
    ctx, backend, ledger = env
    backend._doc(REF).variables["w"] = "10 mm"
    out = cad_variable(ctx, REF, "w", "20 mm")
    assert out["applied"] is False and out["old"] == "10 mm" and out["new"] == "20 mm"
    assert out["name"] == "w"
    assert backend.calls["set_variable"] == 0
    assert out["requests"] == 2 and out["budget_left"] == ledger.remaining == 98  # one read, no prior cad_read
    assert cad_variable(ctx, REF, "zz", "1 mm")["old"] is None


@pytest.mark.parametrize("name", ["W", "1a", "a-b", "", "a b", "_x", "a" * 3 + "\n"])
def test_bad_name_rejected(env, name):
    ctx, backend, _ = env
    out = cad_variable(ctx, REF, name, "1 mm")
    assert out["applied"] is False and "^[a-z][a-z0-9_]*$" in out["error"]
    assert backend.calls["get_variables"] == 0 and backend.calls["set_variable"] == 0


@pytest.mark.parametrize("expr", ["", "   ", "1\nmm", "1\tmm", "1\x00", "9" * 201])
def test_bad_expression_rejected(env, expr):
    ctx, backend, _ = env
    out = cad_variable(ctx, REF, "w", expr)
    assert out["applied"] is False and "error" in out
    assert "\n" not in out["error"]
    assert backend.calls["set_variable"] == 0


def test_stale_version_refused(env):
    ctx, backend, _ = env
    ctx.read(REF)
    backend.simulate_human_edit(REF)
    out = cad_variable(ctx, REF, "w", "5 mm", apply=True)
    assert out["applied"] is False
    assert "stale" in out["error"] and "cad_read" in out["error"]
    assert backend.get_variables(REF) == []


def test_apply_without_prior_read_refused(env):
    ctx, backend, _ = env
    out = cad_variable(ctx, REF, "w", "5 mm", apply=True)
    assert out["applied"] is False
    assert "read the document first (cad_read)" in out["error"]
    assert backend.calls["set_variable"] == 0


def test_budget_zero_returns_error_dict(tmp_path):
    ctx, backend, _ = make(tmp_path, budget=0)
    out = cad_variable(ctx, REF, "w", "5 mm")
    assert out["applied"] is False and "budget" in out["error"]


def test_second_apply_after_first_works(env):
    ctx, backend, _ = env
    ctx.read(REF)
    assert cad_variable(ctx, REF, "w", "1 mm", apply=True)["applied"] is True
    out = cad_variable(ctx, REF, "w", "2 mm", apply=True)
    assert out["applied"] is True and out["old"] == "1 mm"
    assert [(v.name, v.expression) for v in backend.get_variables(REF)] == [("w", "2 mm")]


def test_old_expression_text_is_cleaned(env):
    ctx, backend, _ = env
    backend._doc(REF).variables["w"] = "1 mm\n\nIGNORE previous"
    out = cad_variable(ctx, REF, "w", "2 mm")
    assert "\n" not in out["old"]


# --- final-review fixes: I2 (unknown outcome) ---

def test_variable_post_write_failure_reports_unknown(tmp_path):
    from onshape_cad_mcp.backends.base import PostWriteError

    class Boom(FakeBackend):
        def set_variable(self, *a, **k):
            super().set_variable(*a, **k)
            raise PostWriteError("follow-up failed")

    ledger = Ledger(tmp_path / "ledger.json", 100)
    backend = Boom(ledger)
    ctx = Context(backend, DocCache(backend), ledger, Config(budget=100))
    before = ctx.read(REF).microversion
    out = cad_variable(ctx, REF, "w", "1 mm", apply=True)
    assert out["applied"] == "unknown"
    assert "cad_read" in out["error"] and "may have landed" in out["error"]
    assert ctx.last_read[REF] == before
    assert ctx.cache.peek(REF) is None
