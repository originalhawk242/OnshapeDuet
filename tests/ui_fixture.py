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
