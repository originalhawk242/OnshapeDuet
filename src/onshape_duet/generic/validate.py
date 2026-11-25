"""Validate and build a generic feature write; collect every problem as a finding."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Literal

from ..lint import Finding
from ..model import FeatureList
from ..textsafe import clean
from .build import ParamError, build_param, default_param, shown_default
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
        if mode == "add" and ps.kind == "array" and isinstance(value, list):
            for i, item in enumerate(value):  # item parameters _item_params filled in
                for sub in ps.items:
                    if isinstance(item, dict) and sub.id not in item \
                            and default_param(sub) is not None:
                        prep.defaults_applied[f"{clean(pid, 60)}[{i}].{clean(sub.id, 60)}"] = \
                            shown_default(sub)
        if mode == "update" and self_index is not None and ps.kind == "feature_list":
            for fid in built.get("featureIds", []):
                if index_of.get(fid, -1) >= self_index:
                    prep.findings.append(_refuse(
                        "SELF_REFERENCE",
                        f"{clean(pid)} names feature {clean(fid)}, which is not before the "
                        "feature being updated"))

    if mode == "add":  # Onshape does not fill omitted parameters: send the spec defaults
        for p in spec.params:
            if p.id in params:
                continue
            d = default_param(p)
            if d is not None:
                prep.parameters.append(d)
                prep.defaults_applied[clean(p.id, 60)] = shown_default(p)
            else:
                why = (f"has a spec default ({clean(p.default, 60)}) that could not be built"
                       if p.default is not None else "has no spec default this server can send")
                prep.findings.append(Finding(
                    "warn", "DEFAULT_NOT_SENT",
                    f"{clean(p.id)} {why}, so it is not sent; Onshape does not fill it in, so "
                    "set it explicitly if the feature needs it"))
        shown = {p.id: _may_be_shown(p, params, prep.defaults_applied) for p in spec.params
                 if p.id not in params and p.kind in ("query", "feature_list")}
        sure = [clean(k) for k, v in shown.items() if v is True]
        maybe = [clean(k) for k, v in shown.items() if v is None]
        if sure or maybe:
            parts = ([f"shown in this feature: {', '.join(sure)}"] if sure else []) + \
                    ([f"shown under conditions not checked here: {', '.join(maybe)}"] if maybe else [])
            prep.findings.append(Finding(
                "warn", "EMPTY_SELECTION",
                f"sent with nothing selected ({'; '.join(parts)}); name the ones this feature "
                "needs, or it may fail to regenerate"))
    return prep


def _may_be_shown(p, params: dict, defaults: dict) -> bool | None:
    """True if shown, False if p's simple `X == V` condition is off, None if not known."""
    if not p.conditional:
        return True
    head, sep, want = p.condition.partition(" == ")
    if not sep:
        return None
    value = params[head] if head in params else defaults.get(head)
    if value is None:
        return None
    return str(value).lower() == want.lower() or False
