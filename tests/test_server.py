import asyncio

import httpx

import pytest

from onshape_cad_mcp import server
from onshape_cad_mcp.backends.fake import FakeBackend
from onshape_cad_mcp.budget import Ledger
from onshape_cad_mcp.cache import DocCache
from onshape_cad_mcp.config import Config
from onshape_cad_mcp.docref import DocRef
from onshape_cad_mcp.tools import Context, cad_read, cad_status, cad_variable

REF = DocRef("a" * 24, "b" * 24, "c" * 24)


def make(tmp_path, budget=100):
    ledger = Ledger(tmp_path / "ledger.json", budget)
    backend = FakeBackend(ledger)
    return Context(backend, DocCache(backend), ledger, Config(budget=budget)), backend, ledger


def test_status_costs_zero_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "SECRET-ACCESS-VALUE")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "SECRET-SECRET-VALUE")
    ctx, backend, ledger = make(tmp_path, 5)
    out = cad_status(ctx)
    assert sum(backend.calls.values()) == 0
    assert ledger.used == 0
    assert out["budget"] == 5 and out["used"] == 0 and out["remaining"] == 5
    assert out["credentials_present"] is True
    assert "SECRET-ACCESS-VALUE" not in repr(out) and "SECRET-SECRET-VALUE" not in repr(out)


def test_status_credentials_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("ONSHAPE_ACCESS_KEY", raising=False)
    ctx, _, _ = make(tmp_path)
    assert cad_status(ctx)["credentials_present"] is False


def test_read_with_budget_zero_refuses_and_names_config_key(tmp_path):
    ctx, backend, ledger = make(tmp_path, 0)
    out = cad_read(ctx, REF)
    assert "set `budget` in the config file" in out
    assert sum(backend.calls.values()) == 0 and ledger.used == 0


def test_read_lists_features_variables_and_budget_line(tmp_path):
    ctx, backend, _ = make(tmp_path)
    backend._doc(REF).variables["width"] = "10 mm"
    out = cad_read(ctx, REF)
    assert "width" in out and "10 mm" in out
    assert out.rstrip().splitlines()[-1].startswith("requests used: ")
    assert "budget left: " in out.splitlines()[-1]


def test_read_then_apply_works_and_stale_refused(tmp_path):
    ctx, backend, _ = make(tmp_path)
    cad_read(ctx, REF)
    assert cad_variable(ctx, REF, "w", "5 mm", apply=True)["applied"] is True
    cad_read(ctx, REF)
    backend.simulate_human_edit(REF)
    out = cad_variable(ctx, REF, "w", "6 mm", apply=True)
    assert out["applied"] is False and "stale" in out["error"]


def test_server_registers_four_tools(tmp_path):
    ctx, _, _ = make(tmp_path)
    mcp = server.build_server(ctx)
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"cad_status", "cad_read", "cad_sketch", "cad_variable"}


def test_wrappers_reject_invalid_url(tmp_path):
    ctx, backend, _ = make(tmp_path)
    mcp = server.build_server(ctx)
    for name, args in [("cad_read", {"url": "nope"}),
                       ("cad_variable", {"url": "nope", "name": "a", "expression": "1"}),
                       ("cad_sketch", {"url": "nope", "name": "a", "spec": {}})]:
        res = asyncio.run(mcp.call_tool(name, args))
        assert "not an Onshape document URL" in repr(res)
    assert sum(backend.calls.values()) == 0


def test_build_context_no_network(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'budget = 3\nledger_path = "{tmp_path}/l.json"\n')
    ctx = server.build_context(cfg)
    assert ctx.config.budget == 3 and ctx.ledger.limit == 3 and ctx.config_path == cfg


class Flaky(FakeBackend):
    """Charges like the real backend, then fails with a transport error."""
    def __init__(self, ledger, fail):
        super().__init__(ledger)
        self.fail = set(fail)

    def _enter(self, method):
        super()._enter(method)
        if method in self.fail:
            raise httpx.ConnectError("boom")


def flaky_ctx(tmp_path, fail):
    ledger = Ledger(tmp_path / "ledger.json", 50)
    backend = Flaky(ledger, fail)
    return Context(backend, DocCache(backend), ledger, Config(budget=50)), ledger


@pytest.mark.parametrize("fail", [{"list_features"}, {"get_variables"}])
def test_read_transport_error_returns_error_and_counts(tmp_path, fail):
    ctx, ledger = flaky_ctx(tmp_path, fail)
    out = cad_read(ctx, REF)
    assert out.startswith("error:") and "boom" in out
    assert "requests used: " in out.splitlines()[-1]
    assert not out.splitlines()[-1].startswith("requests used: 0")
    assert ledger.used >= 1


def test_sketch_and_variable_transport_errors(tmp_path):
    from onshape_cad_mcp.tools import cad_sketch
    ctx, ledger = flaky_ctx(tmp_path, {"get_variables"})
    for i, out in enumerate((cad_sketch(ctx, REF, "s", {}), cad_variable(ctx, REF, "w", "1 mm"))):
        assert out["applied"] is False and out["error"]
        assert out["requests"] == 1 and out["budget_left"] == 49 - i


def test_variable_apply_transport_error(tmp_path):
    ctx, ledger = flaky_ctx(tmp_path, {"set_variable"})
    cad_read(ctx, REF)
    out = cad_variable(ctx, REF, "w", "1 mm", apply=True)
    assert out["applied"] is False and out["error"] and out["requests"] >= 1


def test_corrupt_ledger_read_and_status(tmp_path):
    ctx, backend, ledger = make(tmp_path)
    ledger.path.write_text("{garbage")
    out = cad_read(ctx, REF)
    assert str(ledger.path) in out and "fix or delete" in out
    assert "unavailable" in out.splitlines()[-1]
    st = cad_status(ctx)
    assert "fix or delete" in st["error"] and str(ledger.path) in st["error"]
    assert st["budget"] == 100 and "credentials_present" in st
    assert sum(backend.calls.values()) == 0
    res = cad_variable(ctx, REF, "w", "1 mm")
    assert res["applied"] is False and res["requests"] == "unavailable"


def test_main_bad_config_exits_cleanly(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "bad.toml"
    cfg.write_text("budget = [")
    monkeypatch.setenv("ONSHAPE_CAD_MCP_CONFIG", str(cfg))
    with pytest.raises(SystemExit) as e:
        server.main()
    assert e.value.code != 0
    assert str(cfg) in capsys.readouterr().err
