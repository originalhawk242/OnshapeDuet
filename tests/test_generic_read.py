import asyncio
import json
from pathlib import Path

import pytest

from onshape_duet import server
from onshape_duet.backends.fake import FakeBackend
from onshape_duet.budget import Ledger
from onshape_duet.cache import DocCache
from onshape_duet.config import Config
from onshape_duet.docref import DocRef
from onshape_duet.generic.lookup import find_feature
from onshape_duet.generic.render import (param_value, render_feature, render_spec,
                                         render_spec_index)
from onshape_duet.generic.specs import parse_feature_specs
from onshape_duet.model import Feature, FeatureList
from onshape_duet.tools import Context, cad_inspect, cad_read, cad_specs

REF = DocRef("a" * 24, "b" * 24, "c" * 24)
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "featurespecs_UNVERIFIED.json").read_text())

EXTRUDE = {
    "featureType": "extrude", "name": "Boss",
    "parameters": [
        {"btType": "BTMParameterEnum-145", "parameterId": "endBound", "value": "BLIND",
         "enumName": "BoundingType"},
        {"btType": "BTMParameterQuantity-147", "parameterId": "depth", "expression": "25 mm"},
        {"btType": "BTMParameterQueryList-148", "parameterId": "entities",
         "queries": [{"btType": "BTMIndividualQuery-138", "deterministicIds": ["JHD", "JHF"]}]},
        {"btType": "BTMParameterBoolean-144", "parameterId": "oppositeDirection", "value": False},
        {"btType": "BTMParameterWeird-1", "parameterId": "mystery"},
    ],
}


def make(tmp_path, specs=True):
    cfg = Config(budget=50, ledger_path=tmp_path / "l.json")
    ledger = Ledger(cfg.ledger_path, 50)
    backend = FakeBackend(ledger)
    if specs:
        backend.specs_json = FIXTURE
    return Context(backend, DocCache(backend), ledger, cfg), backend


def add(ctx, backend, fj):
    backend.add_feature(REF, fj, backend.get_microversion(REF))


def test_cad_specs_index_then_one_type(tmp_path):
    ctx, _ = make(tmp_path)
    out = cad_specs(ctx, REF)
    assert "extrude: Extrude" in out["specs"] and out["requests"] == 1 and out["applied"] is False
    out = cad_specs(ctx, REF, "extrude")
    assert "depth" in out["specs"] and "mm" in out["specs"] and out["requests"] == 0
    assert "only when endBound == BLIND" in out["specs"]


def test_cad_specs_custom_or_unknown_type_errors(tmp_path):
    ctx, _ = make(tmp_path)
    out = cad_specs(ctx, REF, "extrud")
    assert "extrude" in out["error"] and "specs" not in out
    out = cad_specs(ctx, REF, "e123::m456")
    assert "error" in out and "e123::m456" in out["error"]


def test_cad_specs_backend_failure_is_an_error(tmp_path):
    ctx, backend = make(tmp_path)
    backend.specs_json = {"featureSpecs": "bad"}
    out = cad_specs(ctx, REF)
    assert "unexpected Onshape response" in out["error"]


def test_cad_inspect_renders_all_params_zero_requests(tmp_path):
    ctx, backend = make(tmp_path)
    add(ctx, backend, EXTRUDE)
    cad_read(ctx, REF)
    out = cad_inspect(ctx, REF, "Boss")
    assert out["requests"] == 0
    text = out["feature"]
    assert "Boss [extrude] OK" in text
    assert "depth" in text and "25 mm" in text and "BLIND" in text
    assert "2 geometry ids (set in Onshape)" in text
    assert "<BTMParameterWeird-1>" in text
    assert "mystery" in text


def test_cad_inspect_uses_cached_specs_only(tmp_path):
    ctx, backend = make(tmp_path)
    add(ctx, backend, EXTRUDE)
    cad_read(ctx, REF)
    assert "(Depth)" not in cad_inspect(ctx, REF, "Boss")["feature"]
    assert REF not in ctx.specs
    cad_specs(ctx, REF)
    assert "depth (" in cad_inspect(ctx, REF, "Boss")["feature"]


def test_cad_inspect_errors(tmp_path):
    ctx, backend = make(tmp_path)
    add(ctx, backend, EXTRUDE)
    cad_read(ctx, REF)
    out = cad_inspect(ctx, REF, "Nope")
    assert "Boss" in out["error"] and out["requests"] == 0 and "budget_left" in out


def test_cad_inspect_backend_failure(tmp_path, monkeypatch):
    ctx, backend = make(tmp_path)

    def boom(ref):
        raise RuntimeError("Onshape is down")
    monkeypatch.setattr(backend, "list_features", boom)
    out = cad_inspect(ctx, REF, "Boss")
    assert "Onshape is down" in out["error"] and "budget_left" in out


def test_find_feature_ambiguous_and_id_first():
    fl = FeatureList("mv", [Feature("F1", "extrude", "Body", "OK"), Feature("F2", "fillet", "Body", "OK"),
                            Feature("F3", "fillet", "F1", "OK")])
    with pytest.raises(ValueError, match="F1.*F2"):
        find_feature(fl, "Body")
    assert find_feature(fl, "F1").id == "F1"
    assert find_feature(fl, "F3").name == "F1"
    with pytest.raises(ValueError, match="no feature named"):
        find_feature(fl, "zzz")


def test_render_cleans_hostile_names():
    f = Feature("F1", "extrude", "Bad\nname\x1b[31m", "OK",
                raw={"parameters": [{"btType": "BTMParameterString-149", "parameterId": "p\nq",
                                     "value": "line1\nline2"}]})
    lines = render_feature(f, None).splitlines()
    assert len(lines) == 2 and "Bad name" in lines[0]
    assert "line1 line2" in lines[1]
    spec = parse_feature_specs(FIXTURE)[0]
    assert "\n\n" not in render_spec(spec)


def test_param_value_kinds():
    assert param_value({"btType": "BTMParameterQuantity-147", "expression": "3 mm"}) == "3 mm"
    assert param_value({"btType": "BTMParameterBoolean-144", "value": True}) == "true"
    assert param_value({"btType": "BTMParameterFeatureList-1519", "featureIds": ["a", "b"]}) == "a, b"
    assert param_value({"btType": "BTMParameterQueryList-148",
                        "queries": [{"queryString": "qEverything(EntityType.FACE)"}]}) \
        == "qEverything(EntityType.FACE)"
    arr = {"btType": "BTMParameterArray-2025", "items": [
        {"btType": "BTMArrayParameterItem-1843",
         "parameters": [{"btType": "BTMParameterQuantity-147", "parameterId": "d", "expression": "1 mm"}]}]}
    assert param_value(arr).strip() == "[0] d=1 mm"
    assert param_value("junk") == "<?>"


def test_render_spec_index_and_spec():
    specs = parse_feature_specs(FIXTURE)
    assert "extrude: Extrude" in render_spec_index(specs).splitlines()
    text = render_spec([s for s in specs if s.type == "holeLike"][0])
    assert "holes" in text and "\n  " in text


def test_server_registers_generic_read_tools(tmp_path):
    ctx, _ = make(tmp_path)
    mcp = server.build_server(ctx)
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"cad_specs", "cad_inspect"} <= names
    out = asyncio.run(mcp.call_tool("cad_specs", {"url": "nope"}))
    assert "error" in str(out)
