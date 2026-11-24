import asyncio

import httpx

import pytest

from onshape_duet import server
from onshape_duet.backends.fake import FakeBackend
from onshape_duet.budget import Ledger
from onshape_duet.cache import DocCache
from onshape_duet.config import Config
from onshape_duet.docref import DocRef
from onshape_duet.tools import Context, cad_read, cad_status, cad_variable

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


def test_server_registers_original_four_tools(tmp_path):
    ctx, _, _ = make(tmp_path)
    mcp = server.build_server(ctx)
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"cad_status", "cad_read", "cad_sketch", "cad_variable"} <= names


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


@pytest.mark.parametrize("fail", [{"list_features"}, {"get_microversion"}])
def test_read_transport_error_returns_error_and_counts(tmp_path, fail):
    ctx, ledger = flaky_ctx(tmp_path, fail)
    out = cad_read(ctx, REF)
    assert out.startswith("error:") and "boom" in out
    assert "requests used: " in out.splitlines()[-1]
    assert not out.splitlines()[-1].startswith("requests used: 0")
    assert ledger.used >= 1


def test_sketch_and_variable_transport_errors(tmp_path):
    from onshape_duet.tools import cad_sketch
    ctx, ledger = flaky_ctx(tmp_path, {"get_microversion"})
    spec = {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [5, 0]}],
            "dimensions": [{"type": "length", "ref": "l", "value": "#w"}]}
    for i, out in enumerate((cad_sketch(ctx, REF, "s", spec), cad_variable(ctx, REF, "w", "1 mm"))):
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
    monkeypatch.setenv("ONSHAPE_DUET_CONFIG", str(cfg))
    with pytest.raises(SystemExit) as e:
        server.main()
    assert e.value.code != 0
    assert str(cfg) in capsys.readouterr().err


# --- final-review fixes: I6 (frugality: one feature-list fetch per version) ---

def test_cad_read_costs_2_on_fresh_document_and_1_when_unchanged(tmp_path):
    ctx, backend, ledger = make(tmp_path)
    backend._doc(REF).variables["w"] = "10 mm"
    out = cad_read(ctx, REF)
    assert out.splitlines()[-1].startswith("requests used: 2")
    assert backend.calls["get_variables"] == 0
    assert "w = 10 mm" in out
    out = cad_read(ctx, REF)
    assert out.splitlines()[-1].startswith("requests used: 1")
    assert ledger.used == 3


def test_dry_runs_after_a_read_cost_zero_requests(tmp_path):
    from onshape_duet.tools import cad_sketch
    ctx, backend, ledger = make(tmp_path)
    backend._doc(REF).variables["w"] = "50 mm"
    cad_read(ctx, REF)
    used = ledger.used
    v = cad_variable(ctx, REF, "w", "60 mm")
    s = cad_sketch(ctx, REF, "S", {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [50, 0]}],
                                   "dimensions": [{"type": "length", "ref": "l", "value": "#w"}]})
    assert ledger.used == used
    assert v["requests"] == 0 and s["requests"] == 0
    assert v["old"] == "50 mm" and "error" not in s
    assert v["based_on_version"] == s["based_on_version"] == ctx.last_read[REF]


def test_dry_run_without_prior_read_costs_one_read_and_does_not_count_as_read(tmp_path):
    ctx, backend, _ = make(tmp_path)
    out = cad_variable(ctx, REF, "w", "1 mm")
    assert out["requests"] == 2 and out["based_on_version"] == "mv0"
    assert REF not in ctx.last_read
    again = cad_variable(ctx, REF, "w", "1 mm")  # cache now warm for the version but...
    assert cad_variable(ctx, REF, "w", "1 mm", apply=True)["error"].startswith("read the document first")


def test_apply_costs_backstop_check_plus_write(tmp_path):
    ctx, backend, ledger = make(tmp_path)
    cad_read(ctx, REF)
    used = ledger.used
    out = cad_variable(ctx, REF, "w", "1 mm", apply=True)
    assert out["applied"] is True and out["requests"] == 2 and ledger.used == used + 2


def test_variable_update_passes_existing_feature_id(tmp_path):
    seen = {}

    class Spy(FakeBackend):
        def set_variable(self, ref, name, expression, expected_microversion, feature_id=None):
            seen["fid"] = feature_id
            return super().set_variable(ref, name, expression, expected_microversion, feature_id)

    ledger = Ledger(tmp_path / "l.json", 100)
    backend = Spy(ledger)
    ctx = Context(backend, DocCache(backend), ledger, Config(budget=100))
    backend._doc(REF).variables["w"] = "10 mm"
    cad_read(ctx, REF)
    cad_variable(ctx, REF, "w", "20 mm", apply=True)
    assert seen["fid"] == "F1"


def test_unreferenced_derived_variable_does_not_block_sketches(tmp_path):
    from onshape_duet.tools import cad_sketch
    ctx, backend, _ = make(tmp_path)
    backend._doc(REF).variables["w"] = "10 mm"
    backend._doc(REF).variables["h"] = "#w * 2"
    spec = {"entities": [{"id": "l", "type": "line", "start": [0, 0], "end": [10, 0]}],
            "dimensions": [{"type": "length", "ref": "l", "value": "#w"}]}
    assert "error" not in cad_sketch(ctx, REF, "S", spec)
    bad = {**spec, "dimensions": [{"type": "length", "ref": "l", "value": "#h"}]}
    out = cad_sketch(ctx, REF, "S", bad)
    assert "cannot evaluate" in out["error"] and "'h'" in out["error"]


# --- final-review fixes: I5 (concurrent tool calls share one Context) ---

class _Slow(FakeBackend):
    """Sleeps inside every backend call so unlocked tool calls certainly overlap."""
    def _enter(self, method):
        super()._enter(method)
        import time
        time.sleep(0.03)


def _race(fns):
    import threading
    barrier = threading.Barrier(len(fns))
    results = [None] * len(fns)

    def run(i, fn):
        barrier.wait()
        results[i] = fn()
    ts = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(fns)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    return results


def test_concurrent_reads_do_not_lose_or_double_count_charges(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json", 100)
    backend = _Slow(ledger)
    ctx = Context(backend, DocCache(backend), ledger, Config(budget=100))
    other = DocRef("d" * 24, "e" * 24, "f" * 24)
    outs = _race([lambda: cad_read(ctx, REF), lambda: cad_read(ctx, other)])
    counted = [int(o.splitlines()[-1].split("requests used: ")[1].split(",")[0]) for o in outs]
    assert sum(counted) == ledger.used == 4  # each call reports exactly its own requests


def test_concurrent_apply_and_read_keep_last_read_consistent(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json", 100)
    backend = _Slow(ledger)
    ctx = Context(backend, DocCache(backend), ledger, Config(budget=100))
    cad_read(ctx, REF)
    outs = _race([lambda: cad_variable(ctx, REF, "w", "1 mm", apply=True),
                  lambda: cad_read(ctx, REF)])
    assert outs[0]["applied"] is True
    # whichever order they ran in, last_read must be the document's real version
    assert ctx.last_read[REF] == backend.get_microversion(REF)
    assert outs[0]["requests"] == 2


def test_server_tools_hold_the_context_lock(tmp_path):
    ctx, _, _ = make(tmp_path)
    mcp = server.build_server(ctx)
    held = []
    real = ctx.backend.get_microversion

    def spy(ref):
        held.append(ctx.lock._is_owned())
        return real(ref)
    ctx.backend.get_microversion = spy
    asyncio.run(mcp.call_tool("cad_read", {"url": f"https://cad.onshape.com/documents/{'a'*24}/w/{'b'*24}/e/{'c'*24}"}))
    assert held and all(held)


@pytest.mark.parametrize("tool,args", [
    ("cad_variable", {"name": "w", "expression": "1 mm"}),
    ("cad_sketch", {"name": "s", "spec": {}}),
])
@pytest.mark.parametrize("apply", ["yes", "1", 1, "true"])
def test_apply_must_be_a_real_boolean(tmp_path, tool, args, apply):
    ctx, backend, _ = make(tmp_path)
    mcp = server.build_server(ctx)
    url = f"https://cad.onshape.com/documents/{'a'*24}/w/{'b'*24}/e/{'c'*24}"
    with pytest.raises(Exception) as e:
        asyncio.run(mcp.call_tool(tool, {"url": url, **args, "apply": apply}))
    assert "apply" in repr(e.value) or "bool" in repr(e.value)
    assert sum(backend.calls.values()) == 0


@pytest.mark.parametrize("depth", [True, False])
def test_extrude_depth_rejects_bool_at_mcp_layer(tmp_path, depth):
    ctx, backend, _ = make(tmp_path)
    mcp = server.build_server(ctx)
    url = "https://cad.onshape.com/documents/%s/w/%s/e/%s" % ("a" * 24, "b" * 24, "c" * 24)
    try:
        res = asyncio.run(mcp.call_tool("cad_extrude", {"url": url, "name": "B", "sketch": "S",
                                                        "depth": depth}))
    except Exception as e:  # validation error is acceptable
        res = e
    assert "applied" not in repr(res) or "error" in repr(res)
    assert sum(backend.calls.values()) == 0


@pytest.mark.parametrize("offset", [True, False])
def test_plane_offset_rejects_bool_at_mcp_layer(tmp_path, offset):
    ctx, backend, _ = make(tmp_path)
    mcp = server.build_server(ctx)
    assert "cad_plane" in {t.name for t in asyncio.run(mcp.list_tools())}
    url = "https://cad.onshape.com/documents/%s/w/%s/e/%s" % ("a" * 24, "b" * 24, "c" * 24)
    try:
        res = asyncio.run(mcp.call_tool("cad_plane", {"url": url, "name": "P", "base": "Top",
                                                      "offset": offset}))
    except Exception as e:
        res = e
    assert "applied" not in repr(res) or "error" in repr(res)
    assert sum(backend.calls.values()) == 0


@pytest.mark.parametrize("angle", [True, False])
def test_revolve_angle_rejects_bool_at_mcp_layer(tmp_path, angle):
    ctx, backend, _ = make(tmp_path)
    mcp = server.build_server(ctx)
    assert "cad_revolve" in {t.name for t in asyncio.run(mcp.list_tools())}
    url = "https://cad.onshape.com/documents/%s/w/%s/e/%s" % ("a" * 24, "b" * 24, "c" * 24)
    try:
        res = asyncio.run(mcp.call_tool("cad_revolve", {"url": url, "name": "R", "sketch": "S",
                                                        "axis": "a", "angle": angle}))
    except Exception as e:
        res = e
    assert "applied" not in repr(res) or "error" in repr(res)
    assert sum(backend.calls.values()) == 0


@pytest.mark.parametrize("tool,key", [("cad_fillet", "radius"), ("cad_chamfer", "distance")])
@pytest.mark.parametrize("size", [True, False])
def test_fillet_chamfer_size_rejects_bool_at_mcp_layer(tmp_path, tool, key, size):
    ctx, backend, _ = make(tmp_path)
    mcp = server.build_server(ctx)
    assert tool in {t.name for t in asyncio.run(mcp.list_tools())}
    url = "https://cad.onshape.com/documents/%s/w/%s/e/%s" % ("a" * 24, "b" * 24, "c" * 24)
    try:
        res = asyncio.run(mcp.call_tool(tool, {"url": url, "name": "R", "edges": {"entity": "edge"},
                                               key: size}))
    except Exception as e:
        res = e
    assert "applied" not in repr(res) or "error" in repr(res)
    assert sum(backend.calls.values()) == 0
