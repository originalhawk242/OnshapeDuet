import json
from pathlib import Path

import httpx
import pytest

from onshape_duet.backends.fake import FakeBackend
from onshape_duet.budget import Ledger
from onshape_duet.cache import DocCache
from onshape_duet.config import Config
from onshape_duet.docref import DocRef
from test_onshape_backend import make as make_onshape
from onshape_duet.tools import (_MIDTREE, Context, cad_feature_add, cad_feature_update,
                                cad_read, cad_specs)

REF = DocRef("a" * 24, "b" * 24, "c" * 24)
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "featurespecs_UNVERIFIED.json").read_text())


def make(tmp_path, planes=("P1", "P2")):
    cfg = Config(budget=100, ledger_path=tmp_path / "l.json")
    ledger = Ledger(cfg.ledger_path, 100)
    fake = FakeBackend(ledger)
    fake.specs_json = FIXTURE
    ctx = Context(fake, DocCache(fake), ledger, cfg)
    cad_read(ctx, REF)
    for name in planes:
        mv = fake.get_microversion(REF)
        fake.add_feature(REF, {"btType": "BTMFeature-134", "featureType": "cPlane", "name": name,
                               "parameters": [{"btType": "BTMParameterQuantity-147",
                                               "parameterId": "offset", "expression": "5 mm"}]}, mv)
    cad_read(ctx, REF)
    cad_specs(ctx, REF)
    return ctx, fake


def test_dry_run_zero_requests_after_read(tmp_path):
    ctx, fake = make(tmp_path)
    out = cad_feature_update(ctx, REF, "P1", {"offset": "7 mm"})
    assert out["applied"] is False and out["requests"] == 0
    assert out["changes"] == [{"parameterId": "offset", "before": "5 mm", "after": "7 mm"}]
    assert out["feature_id"] == "F1" and "based_on_version" in out
    assert fake.calls["update_feature"] == 0


def test_refuses_feature_without_raw(tmp_path):
    ctx, fake = make(tmp_path)
    fake.add_feature(REF, {"featureType": "cPlane", "name": "NoRaw", "params": {"offset": "1 mm"}},
                     fake.get_microversion(REF))
    cad_read(ctx, REF)
    out = cad_feature_update(ctx, REF, "NoRaw", {"offset": "7 mm"}, apply=True)
    assert out["applied"] is False
    assert out["error"] == "no full copy of this feature; re-read with cad_read first"


def test_refuses_empty_params_and_unknown_feature(tmp_path):
    ctx, fake = make(tmp_path)
    assert "empty" in cad_feature_update(ctx, REF, "P1", {})["error"]
    assert cad_feature_update(ctx, REF, "nope", {"offset": "1 mm"})["applied"] is False


def test_refuses_sketch_and_unspecced_types(tmp_path):
    ctx, fake = make(tmp_path)
    for t in ("newSketch", "assignVariable"):
        mv = fake.get_microversion(REF)
        fake.add_feature(REF, {"featureType": t, "name": "X" + t, "parameters": []}, mv)
    cad_read(ctx, REF)
    out = cad_feature_update(ctx, REF, "XnewSketch", {"x": 1}, apply=True)
    assert "TYPED_TOOL_REQUIRED" in out["error"]
    out = cad_feature_update(ctx, REF, "XassignVariable", {"x": 1}, apply=True)
    assert "TYPED_TOOL_REQUIRED" in out["error"]
    mv = fake.get_microversion(REF)
    fake.add_feature(REF, {"featureType": "mystery", "name": "M", "parameters": []}, mv)
    cad_read(ctx, REF)
    out = cad_feature_update(ctx, REF, "M", {"x": 1}, apply=True)
    assert "UNKNOWN_FEATURE_TYPE" in out["error"] and fake.calls["update_feature"] == 0


def test_update_last_feature_splices(tmp_path):
    ctx, fake = make(tmp_path)
    out = cad_feature_update(ctx, REF, "P2", {"offset": "9 mm"}, apply=True)
    assert out["applied"] is True and out["requests"] == 2 and "note" not in out
    sent = fake.last_feature_json
    assert sent["featureId"] == "F2" and sent["parameters"][0]["expression"] == "9 mm"
    assert ctx.last_read[REF] == fake.get_microversion(REF)
    assert ctx.cache.peek(REF).features[-1].raw["parameters"][0]["expression"] == "9 mm"
    again = cad_feature_update(ctx, REF, "P2", {"offset": "11 mm"}, apply=True)
    assert again["applied"] is True and fake.calls["list_features"] == 2


def test_update_midtree_demands_reread(tmp_path):
    ctx, fake = make(tmp_path)
    out = cad_feature_update(ctx, REF, "P1", {"offset": "9 mm"}, apply=True)
    assert out["applied"] is True and out["note"] == _MIDTREE
    assert REF not in ctx.last_read
    nxt = cad_feature_update(ctx, REF, "P2", {"offset": "1 mm"}, apply=True)
    assert nxt["applied"] is False and nxt["error"] == _MIDTREE
    cad_read(ctx, REF)
    assert cad_feature_update(ctx, REF, "P2", {"offset": "1 mm"}, apply=True)["applied"] is True


def test_update_inexact_write_takes_refetched_path(tmp_path):
    ctx, fake = make(tmp_path)
    from onshape_duet.model import WriteResult
    real = fake.update_feature
    fake.update_feature = lambda *a, **k: (lambda r: WriteResult(r.feature, r.microversion, False))(
        real(*a, **k))
    out = cad_feature_update(ctx, REF, "P2", {"offset": "9 mm"}, apply=True)
    assert out["applied"] is True and "may include edits" in out["note"]
    assert REF not in ctx.last_read


def test_update_apply_after_human_edit_refused(tmp_path):
    ctx, fake = make(tmp_path)
    fake.simulate_human_edit(REF)
    out = cad_feature_update(ctx, REF, "P2", {"offset": "9 mm"}, apply=True)
    assert out["applied"] is False and "stale" in out["error"]
    assert fake.calls["update_feature"] == 0


def test_update_bad_value_refused_nothing_written(tmp_path):
    ctx, fake = make(tmp_path)
    out = cad_feature_update(ctx, REF, "P1", {"bogus": 1}, apply=True)
    assert out["error"].startswith("refused, nothing written: ") and "UNKNOWN_PARAM" in out["error"]
    assert fake.calls["update_feature"] == 0


def test_update_regen_error_warns(tmp_path):
    ctx, fake = make(tmp_path)
    fake.next_status = "ERROR"
    out = cad_feature_update(ctx, REF, "P2", {"offset": "9 mm"}, apply=True)
    assert out["applied"] is True and out["status"] == "ERROR" and "warning" in out


def test_fake_update_keeps_position_and_unknown_id(tmp_path):
    ctx, fake = make(tmp_path)
    mv = fake.get_microversion(REF)
    fake.update_feature(REF, "F1", {"featureType": "cPlane", "name": "Renamed", "parameters": []}, mv)
    assert [f.name for f in fake.list_features(REF).features] == ["Renamed", "P2"]
    with pytest.raises(RuntimeError, match="no such feature"):
        fake.update_feature(REF, "ZZ", {"name": "x"}, fake.get_microversion(REF))


def test_onshape_update_feature_posts_to_featureid_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "k")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "s")
    seen = []

    def handler(req):
        if req.method == "POST":
            seen.append((req.url.path, json.loads(req.content)))
            return httpx.Response(200, json={
                "feature": {"featureId": "F2", "featureType": "cPlane", "name": "E"},
                "featureState": {"featureStatus": "OK"}})
        return httpx.Response(200, json={"microversion": "mv2"})

    b, _ = make_onshape(tmp_path, handler, 20)
    r = b.update_feature(REF, "F2", {"featureType": "cPlane"}, "mv1")
    path, body = seen[0]
    assert path.endswith("/featureid/F2") and body["feature"] == {"featureType": "cPlane"}
    assert body["sourceMicroversion"] == "mv1" and "serializationVersion" in body
    assert r.feature.id == "F2" and r.microversion == "mv2"
    from onshape_duet.backends.base import PostWriteError
    b2, _ = make_onshape(tmp_path / "x", lambda r: httpx.Response(200, json={}), 20)
    with pytest.raises(PostWriteError, match="update_feature reply missing"):
        b2.update_feature(REF, "F2", {}, "mv1")
    with pytest.raises(RuntimeError, match="bad featureId"):
        b.update_feature(REF, "a/b", {}, "mv1")
