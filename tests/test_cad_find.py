import json
from pathlib import Path

import pytest

from onshape_duet.backends.fake import FakeBackend
from onshape_duet.budget import Ledger
from onshape_duet.cache import DocCache
from onshape_duet.config import Config
from onshape_duet.docref import DocRef
from onshape_duet.features.find import find_script, parse_find_result
from onshape_duet.features.refs import compile_selector
from onshape_duet.feature_tools import cad_find
from onshape_duet.model import FeatureList
from onshape_duet.tools import Context, cad_read

REF = DocRef("a" * 24, "b" * 24, "c" * 24)
SEL = {"entity": "edge", "geometry": "line"}
EXPR = compile_selector(SEL, FeatureList("", []), "selector")


@pytest.fixture
def env(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json", 100)
    backend = FakeBackend(ledger)
    return Context(backend, DocCache(backend), ledger, Config(budget=100)), backend, ledger


def test_find_dry_compiles_without_eval(env):
    ctx, backend, ledger = env
    cad_read(ctx, REF)
    before = ledger.used
    out = cad_find(ctx, REF, SEL)
    assert out["query"] == EXPR and out["entity"] == "edge"
    assert backend.calls["eval_featurescript"] == 0
    assert ledger.used == before and "count" not in out and out["requests"] == 0


def test_find_evaluate_costs_one_request(env):
    ctx, backend, ledger = env
    backend.find_results[EXPR] = (4, ["(0, 0, 0)", "(1, 0, 0)", "(2, 0, 0)", "(3, 0, 0)"])
    cad_read(ctx, REF)
    before = ledger.used
    out = cad_find(ctx, REF, SEL, evaluate=True)
    assert out["count"] == 4 and len(out["centroids_mm"]) == 4
    assert ledger.used - before == 1 and out["requests"] == 1
    assert backend.calls["eval_featurescript"] == 1
    assert out["based_on_version"] == ctx.last_read[REF]


def test_find_bad_selector_is_error_not_request(env):
    ctx, backend, ledger = env
    cad_read(ctx, REF)
    before = ledger.used
    out = cad_find(ctx, REF, {"entity": "vertex"}, evaluate=True)
    assert "selector" in out["error"] and ledger.used == before
    assert out["requests"] == 0 and backend.calls["eval_featurescript"] == 0


def test_find_eval_failure_is_error_dict(env):
    ctx, backend, _ = env
    cad_read(ctx, REF)

    def boom(ref, script):
        raise RuntimeError("Onshape API error 400: bad")
    backend.eval_featurescript = boom
    out = cad_find(ctx, REF, SEL, evaluate=True)
    assert "400" in out["error"] and "budget_left" in out


def test_parse_find_result_rejects_garbage():
    for bad in ({"btType": "x"}, {}, "s", {"btType": "x.BTFSValueArray", "value": [1]},
                {"btType": "x.BTFSValueArray", "value": [{"btType": "x.BTFSValueString", "value": "-1"}]},
                {"btType": "x.BTFSValueArray", "value": []}):
        with pytest.raises(RuntimeError, match="unexpected Onshape response"):
            parse_find_result(bad)


def test_parse_find_result_real_fixture():
    probes = json.loads((Path(__file__).parent / "fixtures" / "probes.json").read_text())
    assert parse_find_result(probes["fs_eval"]["result"]) == (2, ["(1, 2, 3)"])


def test_find_script_shape():
    s = find_script(EXPR, limit=5)
    assert EXPR in s and "evApproximateCentroid" in s and "millimeter" in s and "5" in s
