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


def _hole_spec_params():
    from onshape_duet.generic.specs import parse_feature_specs
    gen = _load_gen()
    (fs,) = parse_feature_specs({"featureSpecs": [gen.load_hole_spec()]})
    return gen, fs.params


def test_gen_hole_defaults_one_entry_per_default():
    gen = _load_gen()
    spec = gen.load_hole_spec()
    out, skipped = gen.convert(spec)
    with_default = [p["parameterId"] for p in spec["parameters"] if p.get("defaultValue")]
    assert [d["parameterId"] for d in out] == with_default
    assert skipped == len(spec["parameters"]) - len(with_default)


def test_hole_defaults_equal_generic_default_param():
    """The typed hole and a generic add send the same default for every hole parameter
    (review I1: the two used to disagree on 59 tolerance bounds, 100 mm against 0.1 mm)."""
    from onshape_duet.features.hole_defaults import HOLE_DEFAULTS
    from onshape_duet.generic.build import default_param
    _, params = _hole_spec_params()
    generic = [d for p in params if (d := default_param(p)) is not None]
    assert len(generic) == len(HOLE_DEFAULTS) == 161
    for typed, gen in zip(HOLE_DEFAULTS, generic):
        assert typed == gen
    by_id = {d["parameterId"]: d for d in HOLE_DEFAULTS}
    # A per-unit range default (0.1 in every unit), not 0.1 m converted to millimeters.
    assert by_id["holeDiameterV2ToleranceBoundSymmetrical"]["expression"] == "0.1 mm"
    assert by_id["holeDiameterV2ToleranceBoundDeviationLower"]["expression"] == "-0.1 mm"
    assert by_id["tappedAngle"]["expression"] == "1.789911 deg"  # full precision, not 1.78991
    assert by_id["holeDiameterV3"]["expression"] == "5 mm"
    assert by_id["isoHoleTableEx"]["value"] == {"size": "5", "holeType": "Drilled"}


def test_gen_hole_defaults_refuses_a_default_it_cannot_build():
    import pytest
    gen = _load_gen()
    bad = {"btType": "BTParameterSpecQuantity-173", "parameterId": "a", "quantityType": "LENGTH",
           "defaultValue": {"btType": "BTMParameterQuantity-147", "parameterId": "a",
                            "value": 1.0, "units": "meter", "expression": ""}, "ranges": []}
    with pytest.raises(ValueError, match="a: "):
        gen.convert({"featureType": "hole", "namespace": "", "parameters": [bad]})


def test_committed_hole_defaults_match_fresh_generation():
    gen = _load_gen()
    out, _ = gen.convert(gen.load_hole_spec())
    assert gen.OUT.read_text() == gen.render(out)
