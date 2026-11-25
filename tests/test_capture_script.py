"""scripts/capture_features.py must not damage the committed fixtures (final review M10).
Offline: the backend's request method is stubbed; nothing reaches Onshape."""
import gzip
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).parent.parent / "scripts" / "capture_features.py"


def _load():
    spec = importlib.util.spec_from_file_location("capture_features", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_capture_writes_gzip_specs_object_queries_and_merges_probes(tmp_path, monkeypatch):
    mod = _load()
    (tmp_path / "probes.json").write_text(json.dumps({"find_eval": {"keep": 1},
                                                      "extrude_querystring": {"keep": 2}}))
    sent = []

    class Backend:
        def _request(self, method, path, body=None):
            sent.append((method, path, body))
            if path.endswith("/featurespecs"):
                return {"featureSpecs": [{"featureType": t} for t in mod.WANTED]}
            if path.endswith("/featurescript"):
                return {"result": {"btType": "x"}}
            return {"features": []}

        def _ok(self, reply):
            return reply

    class Ledger:
        used = 0

    url = "https://cad.onshape.com/documents/%s/w/%s/e/%s" % ("a" * 24, "b" * 24, "c" * 24)
    ctx = SimpleNamespace(backend=Backend(), ledger=Ledger(),
                          config=SimpleNamespace(base_url="https://cad.onshape.com"))
    monkeypatch.setattr(mod, "build_context", lambda path: ctx)
    monkeypatch.setattr(sys, "argv", ["capture", url, str(tmp_path)])
    mod.main()

    assert not (tmp_path / "feature_specs.json").exists()
    with gzip.open(tmp_path / "feature_specs.json.gz", "rt") as f:
        assert {s["featureType"] for s in json.load(f)["kept"]} == mod.WANTED
    fs_body = next(b for m, p, b in sent if p.endswith("/featurescript"))
    assert fs_body["queries"] == {}
    probes = json.loads((tmp_path / "probes.json").read_text())
    assert probes["find_eval"] == {"keep": 1} and probes["extrude_querystring"] == {"keep": 2}
    assert probes["fs_eval"] == {"result": {"btType": "x"}}
