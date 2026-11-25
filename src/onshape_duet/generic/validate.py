"""Validate and build a generic feature write; collect every problem as a finding."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Literal

from ..lint import Finding
from ..model import FeatureList
from ..textsafe import clean
from .build import ParamError, build_param
from .specs import FeatureSpec

TYPED_TOOL_REQUIRED = {"newSketch": "cad_sketch", "assignVariable": "cad_variable"}
# Milestone 4 adds an entry here as it ships each typed tool; name only tools that exist.
TYPED_TOOL_AVAILABLE = {"extrude": "cad_extrude", "revolve": "cad_revolve", "fillet": "cad_fillet",
                        "chamfer": "cad_chamfer", "hole": "cad_hole", "shell": "cad_shell",
                        "booleanBodies": "cad_boolean", "linearPattern": "cad_pattern",
                        "circularPattern": "cad_pattern", "mirror": "cad_mirror", "cPlane": "cad_plane"}


@dataclass
class Prepared:
    parameters: list[dict] = field(default_factory=list)
    readable: dict[str, object] = field(default_factory=dict)
    defaults_applied: dict[str, object] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)


def _refuse(code: str, message: str) -> Finding:
    return Finding("refuse", code, message)


def _type_finding(spec_by_type: dict[str, FeatureSpec], feature_type: str) -> Finding | None:
    t = clean(feature_type)
    if feature_type in TYPED_TOOL_REQUIRED:
        return _refuse("TYPED_TOOL_REQUIRED",
                       f"{t} must be written with its typed tool: use {TYPED_TOOL_REQUIRED[feature_type]}")
    if "::" in feature_type:
        return _refuse("CUSTOM_FEATURE",
                       f"{t} is a custom (FeatureScript) feature; generic writes support native types only")
    if feature_type not in spec_by_type:
        close = difflib.get_close_matches(feature_type, list(spec_by_type), n=3)
        hint = f"; close native types: {', '.join(clean(c) for c in close)}" if close else ""
        return _refuse("UNKNOWN_FEATURE_TYPE", f"unknown feature type {t}{hint}")
    return None


def prepare(spec_by_type: dict[str, FeatureSpec], feature_type: str, params: dict,
            fl: FeatureList, *, mode: Literal["add", "update"], self_index: int | None = None,
            current_raw: dict | None = None) -> Prepared:
    prep = Prepared()
    bad_type = _type_finding(spec_by_type, feature_type)
    if bad_type:
        prep.findings.append(bad_type)
        return prep
    spec = spec_by_type[feature_type]
    if not isinstance(params, dict) or not all(isinstance(k, str) for k in params):
        prep.findings.append(_refuse("BAD_VALUE", "params must be a mapping of parameter id to value"))
        return prep
    prep.readable = dict(params)

    existing = []
    if mode == "update" and isinstance(current_raw, dict):
        raw = current_raw.get("parameters", [])
        existing = [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []

    index_of = {f.id: i for i, f in enumerate(fl.features)}
    for pid, value in params.items():
        ps = spec.param(pid)
        if ps is None:
            close = difflib.get_close_matches(pid, [p.id for p in spec.params], n=3)
            hint = (f"; did you mean {', '.join(clean(c) for c in close)}?" if close else
                    f"; valid ids: {', '.join(clean(p.id) for p in spec.params)}")
            prep.findings.append(_refuse(
                "UNKNOWN_PARAM", f"{clean(feature_type)} has no parameter {clean(pid)!r}{hint}"))
            continue
        cur = next((p for p in existing if p.get("parameterId") == pid), None) \
            if mode == "update" else None
        try:
            built = build_param(ps, value, fl, current=cur)
        except ParamError as e:
            prep.findings.append(_refuse(e.code, clean(str(e), 300)))
            continue
        prep.parameters.append(built)
        if mode == "update" and self_index is not None and ps.kind == "feature_list":
            for fid in built.get("featureIds", []):
                if index_of.get(fid, -1) >= self_index:
                    prep.findings.append(_refuse(
                        "SELF_REFERENCE",
                        f"{clean(pid)} names feature {clean(fid)}, which is not before the "
                        "feature being updated"))

    if mode == "add":
        prep.defaults_applied = {p.id: p.default for p in spec.params
                                 if p.id not in params and not p.conditional
                                 and p.default is not None}
    return prep
