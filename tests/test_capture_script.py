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


# ---- gen_hole_defaults ----
def _load_gen():
    import importlib.util
    path = Path(__file__).parent.parent / "scripts" / "gen_hole_defaults.py"
    spec = importlib.util.spec_from_file_location("gen_hole_defaults", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _q(pid, value, units, expression=""):
    return {"parameterId": pid, "defaultValue": {"btType": "BTMParameterQuantity-147",
            "value": value, "units": units, "expression": expression, "isInteger": False}}


def test_gen_hole_defaults_one_entry_per_default_and_units():
    gen = _load_gen()
    spec = gen.load_hole_spec()
    out, skipped = gen.convert(spec)
    with_default = [p["parameterId"] for p in spec["parameters"] if p.get("defaultValue")]
    assert [d["parameterId"] for d in out] == with_default
    assert skipped == len(spec["parameters"]) - len(with_default)
    assert gen.convert_param(_q("a", 0.012, "meter"))["expression"] == "12 mm"
    assert gen.convert_param(_q("b", 118.0, "degree"))["expression"] == "118 deg"
    assert gen.convert_param(_q("c", 3.0, ""))["expression"] == "3"
    assert gen.convert_param(_q("d", 1, "meter", "7 mm"))["expression"] == "7 mm"


def test_gen_hole_defaults_unknown_unit_raises():
    import pytest
    gen = _load_gen()
    with pytest.raises(ValueError, match="furlong"):
        gen.convert_param(_q("a", 1.0, "furlong"))


def test_committed_hole_defaults_match_fresh_generation():
    gen = _load_gen()
    out, _ = gen.convert(gen.load_hole_spec())
    assert gen.OUT.read_text() == gen.render(out)
