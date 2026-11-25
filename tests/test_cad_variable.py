import pytest

from onshape_duet.backends.fake import FakeBackend
from onshape_duet.budget import Ledger
from onshape_duet.cache import DocCache
from onshape_duet.config import Config
from onshape_duet.docref import DocRef
from onshape_duet.tools import _MIDTREE, Context, cad_read, cad_variable

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
    ctx.read(REF)  # a newly created variable has an unknown feature id: the cache entry is dropped
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
    from onshape_duet.backends.base import PostWriteError

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


# --- S1: a write is decided on exactly the version it is pinned to ---

def test_s1_update_then_update_again_needs_no_reread(env):
    ctx, backend, _ = env
    backend._doc(REF).variables["w"] = "10 mm"
    ctx.read(REF)
    assert cad_variable(ctx, REF, "w", "20 mm", apply=True)["applied"] is True
    out = cad_variable(ctx, REF, "w", "30 mm", apply=True)  # spliced cache is at last_read
    assert out["applied"] is True and out["old"] == "20 mm"


def test_s1_human_edit_after_read_refused_with_zero_writes(env):
    ctx, backend, _ = env
    ctx.read(REF)
    backend.simulate_human_edit(REF)
    out = cad_variable(ctx, REF, "w", "5 mm", apply=True)
    assert out["applied"] is False and "stale" in out["error"]
    assert backend.calls["set_variable"] == 0


def test_s1_snapshot_version_differs_from_last_read_is_refused(env):
    ctx, backend, _ = env
    ctx.read(REF)
    ctx.last_read[REF] = "mv-older"  # cache holds a different version than the pin
    out = cad_variable(ctx, REF, "w", "5 mm", apply=True)
    assert out["applied"] is False and "stale" in out["error"]
    assert backend.calls["set_variable"] == 0 and backend.calls["list_features"] == 1


def test_s1_dropped_cache_entry_is_refused_not_refetched(env):
    ctx, backend, _ = env
    ctx.read(REF)
    ctx.cache.invalidate(REF)
    before = backend.calls["list_features"]
    out = cad_variable(ctx, REF, "w", "5 mm", apply=True)
    assert out["applied"] is False and "stale" in out["error"]
    assert backend.calls["list_features"] == before and backend.calls["set_variable"] == 0


def test_s1_dry_run_fallback_never_advances_last_read(env):
    ctx, backend, _ = env
    ctx.read(REF)
    backend.simulate_human_edit(REF)
    cad_variable(ctx, REF, "w", "5 mm")
    assert ctx.last_read[REF] == "mv0"


# --- wave 2: D (regeneration status) and #name validation (T10) ---

class _StatusBackend(FakeBackend):
    status = "OK"

    def set_variable(self, *a, **k):
        res = super().set_variable(*a, **k)
        from dataclasses import replace
        return type(res)(replace(res.feature, status=self.status), res.microversion, res.exact)


def _status_env(tmp_path, status):
    ledger = Ledger(tmp_path / "ledger.json", 100)
    backend = _StatusBackend(ledger)
    backend.status = status
    ctx = Context(backend, DocCache(backend), ledger, Config(budget=100))
    ctx.read(REF)
    return ctx


def test_variable_apply_info_status_is_a_warn_not_an_error(tmp_path):
    out = cad_variable(_status_env(tmp_path, "INFO"), REF, "w", "1 mm", apply=True)
    assert out["applied"] is True and out["status"] == "INFO" and "warning" not in out
    assert [f for f in out["findings"] if f["level"] == "warn" and f["code"] == "REGEN_INFO"]
    assert not [f for f in out["findings"] if f["code"] == "REGEN_STATUS"]


@pytest.mark.parametrize("status", ["ERROR", "WARNING"])
def test_variable_apply_reports_regeneration_status(tmp_path, status):
    out = cad_variable(_status_env(tmp_path, status), REF, "w", "1 mm", apply=True)
    assert out["applied"] is True and out["status"] == status and "warning" in out
    assert [f for f in out["findings"] if f["level"] == "error" and f["code"] == "REGEN_STATUS"]


def test_variable_apply_ok_status_has_no_error_finding(tmp_path):
    out = cad_variable(_status_env(tmp_path, "OK"), REF, "w", "1 mm", apply=True)
    assert out["status"] == "OK" and out["findings"] == [] and "warning" not in out


def test_unknown_status_is_not_an_error(env):
    ctx, backend, _ = env
    ctx.read(REF)

    class NoStatus(type(backend)):
        def set_variable(self, *a, **k):
            res = super().set_variable(*a, **k)
            from dataclasses import replace
            return type(res)(replace(res.feature, status="", id=""), res.microversion, res.exact)
    backend.__class__ = NoStatus
    out = cad_variable(ctx, REF, "w", "1 mm", apply=True)
    assert out["applied"] is True and "status" not in out and out["findings"] == []


def test_expression_referencing_missing_variable_warns_but_is_not_refused(env):
    ctx, backend, _ = env
    backend._doc(REF).variables["w"] = "10 mm"
    ctx.read(REF)
    out = cad_variable(ctx, REF, "h", "#w * 2 + #nope")
    assert [f["code"] for f in out["findings"]] == ["UNKNOWN_VARIABLE"]
    assert out["findings"][0]["level"] == "warn" and "#nope" in out["findings"][0]["message"]
    done = cad_variable(ctx, REF, "h", "#w * 2 + #nope", apply=True)
    assert done["applied"] is True
    assert cad_variable(ctx, REF, "k", "#w")["findings"] == []


def test_last_read_unchanged_and_requests_reported_after_stale_refusal(env):
    ctx, backend, ledger = env
    mv = ctx.read(REF).microversion
    backend.simulate_human_edit(REF)
    before = ledger.used
    out = cad_variable(ctx, REF, "w", "5 mm", apply=True)
    assert out["applied"] is False and "stale" in out["error"]
    assert ctx.last_read[REF] == mv
    assert out["requests"] == ledger.used - before == 1
    assert out["budget_left"] == ledger.remaining


def test_apply_reports_requests_and_budget_left(env):
    ctx, backend, ledger = env
    ctx.read(REF)
    before = ledger.used
    out = cad_variable(ctx, REF, "w", "5 mm", apply=True)
    assert out["applied"] is True and out["requests"] == ledger.used - before == 2
    assert out["budget_left"] == ledger.remaining


def test_reread_hint_does_not_mask_a_later_human_edit(env):
    ctx, backend, _ = env
    ctx.read(REF)
    assert cad_variable(ctx, REF, "w", "5 mm", apply=True)["applied"] is True
    # untouched document: the accurate "variable was just created" hint
    hint = cad_variable(ctx, REF, "w", "6 mm", apply=True)
    assert hint["applied"] is False and "variable was just created" in hint["error"]
    # a genuine human edit afterwards: the plain stale message, not the old hint
    backend.simulate_human_edit(REF)
    out = cad_variable(ctx, REF, "w", "6 mm", apply=True)
    assert out["applied"] is False and "stale" in out["error"]
    assert "just created" not in out["error"]
    assert backend.calls["set_variable"] == 1
    ctx.read(REF)
    assert cad_variable(ctx, REF, "w", "7 mm", apply=True)["applied"] is True


def test_successful_write_clears_the_hint(env):
    ctx, backend, _ = env
    ctx.read(REF)
    cad_variable(ctx, REF, "w", "5 mm", apply=True)     # creation sets the hint
    assert REF in ctx.reread
    ctx.read(REF)
    assert REF not in ctx.reread                         # a read clears it
    cad_variable(ctx, REF, "h", "5 mm", apply=True)     # creation again
    ctx.read(REF)
    # update the last variable: a mid-tree update now leaves a re-read hint on purpose
    assert cad_variable(ctx, REF, "h", "9 mm", apply=True)["applied"] is True
    assert REF not in ctx.reread


def test_refuses_to_convert_a_non_length_variable(env):
    ctx, backend, _ = env
    backend._doc(REF).variables["count"] = "5"
    # make the existing feature an ANGLE variable, as Onshape would report it
    feat = next(f for f in backend._doc(REF).features if f.params.get("name") == "count")
    feat.params["variableType"] = "ANGLE"
    ctx.read(REF)
    out = cad_variable(ctx, REF, "count", "5 mm", apply=True)
    assert out["applied"] is False and "ANGLE" in out["error"] and "nothing was written" in out["error"]


def test_variable_update_midtree_demands_reread(env):
    ctx, backend, _ = env
    backend._doc(REF).variables["w"] = "10 mm"
    backend.add_feature(REF, {"featureType": "cPlane", "name": "P", "parameters": []},
                        backend.get_microversion(REF))
    cad_read(ctx, REF)
    out = cad_variable(ctx, REF, "w", "20 mm", apply=True)
    assert out["applied"] is True and out["note"] == _MIDTREE
    assert REF not in ctx.last_read
    nxt = cad_variable(ctx, REF, "w", "30 mm", apply=True)
    assert nxt["applied"] is False and nxt["error"] == _MIDTREE
    cad_read(ctx, REF)
    assert cad_variable(ctx, REF, "w", "30 mm", apply=True)["applied"] is True


def test_variable_update_last_still_splices(env):
    ctx, backend, _ = env
    backend.add_feature(REF, {"featureType": "cPlane", "name": "P", "parameters": []},
                        backend.get_microversion(REF))
    backend._doc(REF).variables["w"] = "10 mm"  # created after P, so it is the last feature
    cad_read(ctx, REF)
    out = cad_variable(ctx, REF, "w", "20 mm", apply=True)
    assert out["applied"] is True and "note" not in out
    assert ctx.last_read[REF] == backend.get_microversion(REF)
    again = cad_variable(ctx, REF, "w", "30 mm", apply=True)
    assert again["applied"] is True and again["old"] == "20 mm"
