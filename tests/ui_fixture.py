"""Helpers comparing built feature JSON against features captured from the Onshape UI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "ui_features.json"
_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        if not FIXTURE.exists():
            pytest.skip(f"UI fixture missing: {FIXTURE}")
        _cache = json.loads(FIXTURE.read_text())
    return _cache


def ui_feature(name: str) -> dict:
    feats = _load().get("features", [])
    for f in feats:
        if f.get("name") == name:
            return f
    pytest.fail(f"UI feature {name!r} not in fixture; available: {[f.get('name') for f in feats]}")


def assert_matches_ui(built: dict, ui_name: str) -> None:
    ui = ui_feature(ui_name)
    assert built["featureType"] == ui["featureType"]
    ui_params = {p["parameterId"]: p for p in ui.get("parameters", [])}
    for p in built["parameters"]:
        pid = p["parameterId"]
        assert pid in ui_params, f"parameter {pid!r} not in UI feature {ui_name!r}"
        assert p["btType"] == ui_params[pid]["btType"], pid
        if "enumName" in ui_params[pid]:
            assert p.get("enumName") == ui_params[pid]["enumName"], pid


SPECS = Path(__file__).parent / "fixtures" / "feature_specs.json.gz"
_specs: dict | None = None

_KIND = {  # emitted btType prefix -> spec btType prefix
    "BTMParameterQuantity": "BTParameterSpecQuantity",
    "BTMParameterEnum": "BTParameterSpecEnum",
    "BTMParameterBoolean": "BTParameterSpecBoolean",
    "BTMParameterQueryList": "BTParameterSpecQuery",
    "BTMParameterString": "BTParameterSpecString",
    "BTMParameterFeatureList": "BTParameterSpecFeatureList",
    "BTMParameterArray": "BTParameterSpecArray",
}


def _prefix(bt: str) -> str:
    return bt.rsplit("-", 1)[0]


def feature_spec(feature_type: str) -> dict:
    global _specs
    if _specs is None:
        import gzip
        _specs = json.loads(gzip.open(SPECS, "rt").read())
    for s in _specs["kept"]:
        if s.get("featureType") == feature_type:
            return s
    pytest.fail(f"no featurespec for {feature_type!r}")


def assert_matches_spec(built: dict, feature_type: str) -> None:
    """Check built feature JSON against Onshape's own featurespec (ground truth)."""
    spec = feature_spec(feature_type)
    assert built["featureType"] == feature_type
    params = {p["parameterId"]: p for p in spec["parameters"]}
    for p in built["parameters"]:
        pid = p["parameterId"]
        assert pid in params, f"parameter {pid!r} not in featurespec {feature_type!r}"
        sp = params[pid]
        emitted = _prefix(p["btType"])
        assert emitted in _KIND, f"{pid}: unknown emitted kind {emitted}"
        assert _KIND[emitted] == _prefix(sp["btType"]), f"{pid}: kind {emitted} vs spec {sp['btType']}"
        if emitted == "BTMParameterEnum":
            assert p["enumName"] == sp["defaultValue"]["enumName"], pid
            assert p["value"] in sp["options"], f"{pid}: {p['value']!r} not in {sp['options']}"
        if emitted == "BTMParameterQuantity":
            expr = p["expression"].strip()
            if not expr.startswith("#"):  # a variable's unit is not visible here
                is_angle = expr.endswith(("deg", "rad"))
                assert is_angle == (sp.get("quantityType") == "ANGLE"), pid
