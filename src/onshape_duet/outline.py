"""Compact plain-text outline of a Part Studio for LLM consumption."""
from __future__ import annotations

from .lint import DEFAULT_NAME as _DEFAULT_NAME
from .model import Feature, FeatureList, Variable
from .sketch.solver import DofReport
from .textsafe import clean

_MAX_VALUE_LEN = 40


_MAX_PARAMS = 12
_HOLE_KEYS = ("styleV2", "holeDiameterV3", "endStyleV2", "holeDepthV3")


def _editable_numbers(feature: Feature) -> str:
    entries = {}
    for key, val in feature.params.items():
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)) or (isinstance(val, str) and val.strip()):
            entries[key] = f"{clean(key, 30)}={clean(val, _MAX_VALUE_LEN)}"
    if feature.type == "hole":
        blind = feature.params.get("endStyleV2") == "BLIND"
        shown = [entries[k] for k in _HOLE_KEYS
                 if k in entries and (blind or k != "holeDepthV3")]
    else:
        shown = list(entries.values())[:_MAX_PARAMS]
    parts = list(shown)
    if len(entries) > len(shown):
        parts.append(f"+{len(entries) - len(shown)} more (cad_inspect)")
    return ", ".join(parts)


def _sketch_note(feature: Feature, reports: dict[str, DofReport] | None) -> str:
    report = (reports or {}).get(feature.id)
    if report is None:
        return "not assessed"
    if report.conflicts:
        return f"CONFLICT: {clean(report.conflicts[0], 120)}"
    return f"dof={report.dof}"


def render_outline(
    fl: FeatureList,
    variables: list[Variable],
    reports: dict[str, DofReport] | None = None,
) -> str:
    lines: list[str] = []
    if not fl.features:
        lines.append("(no features)")
    for i, f in enumerate(fl.features, 1):
        name = clean(f.name)
        line = f"{i}. {name} [{clean(f.type, 40)}] {clean(f.status, 40)}"
        if "sketch" in f.type.lower():
            line += " " + _sketch_note(f, reports)
        if _DEFAULT_NAME.fullmatch(name):
            line += " ⚠ default name"
        nums = _editable_numbers(f)
        if nums:
            line += f" | {nums}"
        lines.append(line)
    if variables:
        lines.append("")
        lines.append("Variables:")
        lines.extend(f"{clean(v.name, 60)} = {clean(v.expression, 120)}" for v in variables)
    return "\n".join(lines)
