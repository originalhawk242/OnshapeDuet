"""Helpers checking built feature JSON against Onshape's own featurespecs (live capture in
tests/fixtures/feature_specs.json.gz). The module name is historical: ground truth is the
featurespecs, not features captured from the UI."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

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
