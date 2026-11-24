import asyncio
import json
from pathlib import Path

from onshape_duet import server
from onshape_duet.backends.fake import FakeBackend
from onshape_duet.budget import Ledger
from onshape_duet.cache import DocCache
from onshape_duet.config import Config
from onshape_duet.docref import DocRef
from onshape_duet.tools import Context, cad_feature_add, cad_read, cad_specs

REF = DocRef("a" * 24, "b" * 24, "c" * 24)
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "featurespecs_UNVERIFIED.json").read_text())


def make(tmp_path):
    cfg = Config(budget=50, ledger_path=tmp_path / "l.json")
    ledger = Ledger(cfg.ledger_path, 50)
    backend = FakeBackend(ledger)
    backend.specs_json = FIXTURE
    return Context(backend, DocCache(backend), ledger, cfg), backend


def test_add_dry_run_costs_specs_only(tmp_path):
    ctx, fake = make(tmp_path)
    cad_read(ctx, REF)
    first = cad_feature_add(ctx, REF, "cPlane", "Mid plane", {"offset": "5 mm"})
    assert first["applied"] is False and first["requests"] == 1
    assert first["feature_type"] == "cPlane" and first["params"] == {"offset": "5 mm"}
    assert "based_on_version" in first
    second = cad_feature_add(ctx, REF, "cPlane", "Mid plane", {"offset": "5 mm"})
    assert second["requests"] == 0 and fake.calls["add_feature"] == 0


def test_add_refused_writes_nothing(tmp_path):
    ctx, fake = make(tmp_path)
    cad_read(ctx, REF)
    out = cad_feature_add(ctx, REF, "cPlane", "P", {"bogus": 1}, apply=True)
    assert out["applied"] is False
    assert out["error"].startswith("refused, nothing written: ")
    assert "UNKNOWN_PARAM" in out["error"]
    assert fake.calls["add_feature"] == 0


def test_add_unknown_type_has_only_prepare_findings(tmp_path):
    ctx, fake = make(tmp_path)
    cad_read(ctx, REF)
    out = cad_feature_add(ctx, REF, "nope", "P", {})
    assert [f["code"] for f in out["findings"]] == ["UNKNOWN_FEATURE_TYPE"]


def test_add_apply_costs_two_and_sends_btm_json(tmp_path):
    ctx, fake = make(tmp_path)
    cad_read(ctx, REF)
    cad_specs(ctx, REF)
    out = cad_feature_add(ctx, REF, "cPlane", "Mid plane", {"offset": "5 mm"}, apply=True)
    assert out["applied"] is True and out["requests"] == 2
    assert fake.last_feature_json["parameters"][0]["btType"] == "BTMParameterQuantity-147"
    assert fake.last_feature_json["name"] == "Mid plane"


def test_add_apply_without_read_refused(tmp_path):
    ctx, fake = make(tmp_path)
    out = cad_feature_add(ctx, REF, "cPlane", "P", {"offset": "5 mm"}, apply=True)
    assert out["applied"] is False and "cad_read" in out["error"]
    assert fake.calls["add_feature"] == 0


def test_add_apply_after_human_edit_refused(tmp_path):
    ctx, fake = make(tmp_path)
    cad_read(ctx, REF)
    cad_specs(ctx, REF)
    fake.simulate_human_edit(REF)
    out = cad_feature_add(ctx, REF, "cPlane", "P", {"offset": "5 mm"}, apply=True)
    assert out["applied"] is False and "stale" in out["error"].lower()
    assert fake.calls["add_feature"] == 0


def test_add_regen_error_reported(tmp_path):
    ctx, fake = make(tmp_path)
    cad_read(ctx, REF)
    fake.next_status = "ERROR"
    out = cad_feature_add(ctx, REF, "cPlane", "P", {"offset": "5 mm"}, apply=True)
    assert out["applied"] is True
    assert any(f["code"] == "REGEN_STATUS" for f in out["findings"])
    assert "Plane feature" in out["warning"]


def test_add_bad_name(tmp_path):
    ctx, fake = make(tmp_path)
    for bad in ["", "x" * 65, "a\nb", 5]:
        out = cad_feature_add(ctx, REF, "cPlane", bad, {"offset": "5 mm"})
        assert out["applied"] is False and "invalid name" in out["error"]
    assert fake.calls["get_feature_specs"] == 0


def test_server_registers_cad_feature_add(tmp_path):
    ctx, _ = make(tmp_path)
    mcp = server.build_server(ctx)
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    assert "cad_feature_add" in tools
    assert "DRY RUN" in tools["cad_feature_add"].description
