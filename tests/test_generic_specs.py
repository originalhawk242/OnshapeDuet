import json
from pathlib import Path

import httpx
import pytest

from onshape_duet.backends.fake import FakeBackend
from onshape_duet.backends.onshape import OnshapeBackend
from onshape_duet.budget import Ledger
from onshape_duet.cache import DocCache
from onshape_duet.config import Config
from onshape_duet.docref import DocRef
from onshape_duet.generic.specs import parse_feature_specs
from onshape_duet.tools import Context

REF = DocRef("a" * 24, "b" * 24, "c" * 24)
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "featurespecs_UNVERIFIED.json").read_text())
CUSTOM_TYPE = "e123::m456"


def fake_ctx(tmp_path):
    cfg = Config(budget=50, ledger_path=tmp_path / "l.json")
    ledger = Ledger(cfg.ledger_path, 50)
    backend = FakeBackend(ledger)
    backend.specs_json = FIXTURE
    return Context(backend, DocCache(backend), ledger, cfg), backend


def by_type():
    return {s.type: s for s in parse_feature_specs(FIXTURE)}


def test_parse_native_only_and_sorted():
    specs = parse_feature_specs(FIXTURE)
    types = [s.type for s in specs]
    assert types == sorted(types) and all("::" not in t for t in types)
    assert not any(s.type == CUSTOM_TYPE for s in specs)
    assert len(specs) == 6


def test_param_kinds_and_fields():
    ex = by_type()["extrude"]
    d = ex.param("depth")
    assert (d.kind, d.quantity_type, d.units, d.conditional) == ("quantity", "LENGTH", "mm", True)
    assert d.condition == "endBound == BLIND" and d.default == "25 mm"
    e = ex.param("endBound")
    assert e.kind == "enum" and e.options == ("BLIND", "THROUGH_ALL", "UP_TO_NEXT")
    assert e.enum_name == "BoundingType" and not e.conditional
    assert ex.param("entities").kind == "query"
    assert ex.param("oppositeDirection").kind == "boolean"
    assert ex.param("oppositeDirection").default is False
    assert ex.param("nope") is None
    pat = by_type()["linearPattern"]
    assert pat.param("instanceFunction").kind == "feature_list"
    assert pat.param("instanceCount").quantity_type == "INTEGER"


def test_unsupported_kind_and_array_items():
    h = by_type()["holeLike"]
    assert h.param("tablePath").kind == "unsupported"
    arr = h.param("holes")
    assert arr.kind == "array"
    assert [(i.id, i.kind) for i in arr.items] == [("diameter", "quantity"), ("label", "string")]
    assert arr.items[1].default == "H1"


def test_malformed_specs_raise():
    with pytest.raises(RuntimeError, match="unexpected Onshape response"):
        parse_feature_specs({"featureSpecs": "x"})
    with pytest.raises(RuntimeError):
        parse_feature_specs({"featureSpecs": [{"featureType": "a", "parameters": [{"btType": "x"}]}]})


def test_specs_cached_per_ref(tmp_path):
    ctx, backend = fake_ctx(tmp_path)
    a = ctx.feature_specs(REF)
    assert ctx.feature_specs(REF) is a
    assert backend.calls["get_feature_specs"] == 1
    ctx.feature_specs(DocRef("d" * 24, "b" * 24, "c" * 24))
    assert backend.calls["get_feature_specs"] == 2


def test_specs_fetch_failure_not_cached(tmp_path):
    ctx, backend = fake_ctx(tmp_path)
    good = backend.specs_json
    backend.specs_json = {"featureSpecs": "broken"}
    with pytest.raises(RuntimeError):
        ctx.feature_specs(REF)
    assert REF not in ctx.specs
    backend.specs_json = good
    assert ctx.feature_specs(REF)
    assert backend.calls["get_feature_specs"] == 2


def test_fake_default_specs_empty():
    assert FakeBackend().get_feature_specs(REF) == []


def test_onshape_get_feature_specs_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "key")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "secret")
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path))
        return httpx.Response(200, json=FIXTURE)

    cfg = Config(budget=10, ledger_path=tmp_path / "l.json")
    ledger = Ledger(cfg.ledger_path, 10)
    b = OnshapeBackend(cfg, ledger, httpx.MockTransport(handler))
    specs = b.get_feature_specs(REF)
    assert seen == [("GET", f"/api/v6/partstudios/d/{'a'*24}/w/{'b'*24}/e/{'c'*24}/featurespecs")]
    assert ledger.used == 1 and len(specs) == 6
