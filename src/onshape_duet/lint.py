"""Editability lint for sketches (spec 7.x).

Every document- or caller-controlled string placed in a message goes through
`textsafe.clean` (R14).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from .features.params import Value, parse_length_mm
from .model import FeatureList
from .sketch.solver import DofReport
from .textsafe import clean
from .variables import VARIABLE_FEATURE_TYPE, find_variable

# Onshape's own default feature names ("Linear pattern 1", "Plane 2", ...).
DEFAULT_NAME = re.compile(
    r"(Sketch|Extrude|Fillet|Revolve|Chamfer|Shell|Hole|Boolean|Pattern|Linear pattern"
    r"|Circular pattern|Mirror|Plane|Offset plane) \d+")


@dataclass(frozen=True)
class Finding:
    level: Literal["refuse", "error", "warn"]
    code: str
    message: str

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message}


def _list(items: list, max_items: int = 10) -> str:
    shown = ", ".join(clean(i, 60) for i in items[:max_items])
    more = len(items) - max_items
    return shown + (f" (+{more} more)" if more > 0 else "")


def _has_ground(spec: dict) -> bool:
    cons = spec.get("constraints") or []
    return any(isinstance(c, dict) and c.get("type") == "ground" for c in cons)


def _dimensions(spec: dict) -> list[dict]:
    dims = spec.get("dimensions") or []
    return [d for d in dims if isinstance(d, dict)]


def duplicate_name(name: str, fl: FeatureList) -> Finding | None:
    """REFUSE a name another (non-variable) feature already has: every later typed tool
    would refuse that name as ambiguous."""
    if any(f.type != VARIABLE_FEATURE_TYPE and f.name == name for f in fl.features):
        return Finding("refuse", "DUPLICATE_NAME",
                       f"a feature named {clean(name)!r} already exists; references "
                       "are by name, so choose a unique name")
    return None


def lint_sketch(spec: dict, report: DofReport, name: str,
                fl: FeatureList | None = None) -> list[Finding]:
    """`fl` (the document's feature list) enables the DUPLICATE_NAME check."""
    out: list[Finding] = []
    dims = _dimensions(spec)
    if fl is not None and (dup := duplicate_name(name, fl)) is not None:
        out.append(dup)

    if report.not_assessed:
        out.append(Finding("refuse", "SKETCH_UNSUPPORTED_ENTITY",
                           f"entities not supported yet: {_list(report.not_assessed)}; "
                           "they cannot be written, so nothing is written. Remove them "
                           "(supported: point, line, circle, rectangle)"))

    if report.conflicts:
        out.append(Finding("refuse", "SKETCH_CONFLICT",
                           "constraints/dimensions contradict the geometry: "
                           + _list(report.conflicts, 5)))
    elif report.redundant:
        out.append(Finding("warn", "SKETCH_REDUNDANT",
                           "the sketch has redundant constraints or dimensions; "
                           "remove the extras so it stays editable"))

    if report.dof > 0:
        if not dims and not _has_ground(spec):
            out.append(Finding("refuse", "SKETCH_FLOATING",
                               f"sketch has {report.dof} degrees of freedom, no dimensions "
                               "and no ground constraint; it would float free. Add "
                               "dimensions and a ground constraint"))
        else:
            out.append(Finding("warn", "SKETCH_PARTIAL",
                               f"sketch is under-constrained ({report.dof} degrees of freedom "
                               f"left); free: {_list(report.free) or 'unknown'}"))

    if DEFAULT_NAME.fullmatch(str(name)):
        out.append(Finding("warn", "DEFAULT_NAME",
                           f"name {clean(name)!r} looks like a default; give it a "
                           "descriptive name"))

    literals: dict[float, int] = defaultdict(int)
    for d in dims:
        v = d.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            literals[abs(float(v))] += 1  # 20 and -20 are the same magic number
    for v, n in literals.items():
        if n >= 2:
            out.append(Finding("warn", "MAGIC_NUMBER",
                               f"the literal {v:g} is used in {n} dimensions; "
                               "make it a variable and reference it with '#name'"))
    return out


def lint_feature(name: str, values: list[Value], fl: FeatureList) -> list[Finding]:
    out: list[Finding] = []
    if DEFAULT_NAME.fullmatch(str(name)):
        out.append(Finding("warn", "DEFAULT_NAME",
                           f"name {clean(name)!r} looks like a default; give it a "
                           "descriptive name"))
    if (dup := duplicate_name(name, fl)) is not None:
        out.append(dup)

    for v in values:
        if v.var is None:
            continue
        var_feature = find_variable(fl, v.var)
        if var_feature is None:
            out.append(Finding("refuse", "UNKNOWN_VARIABLE",
                               f"{clean(v.arg)}: variable {clean(v.var)!r} does not exist "
                               "in this Part Studio; create it with cad_variable first"))
        elif v.kind == "length":
            vtype = var_feature.params.get("variableType", "LENGTH")
            if vtype != "LENGTH":
                out.append(Finding("refuse", "NON_LENGTH_VARIABLE",
                                   f"{clean(v.arg)}: variable {clean(v.var)!r} is a "
                                   f"{clean(vtype, 20)} variable, not a length"))

    literals = [v for v in values if v.kind == "length" and v.literal is not None]
    flagged: set[int] = set()
    for i, a in enumerate(literals):
        if i in flagged:
            continue
        twins = [j for j in range(i + 1, len(literals))
                 if abs(literals[j].literal - a.literal) <= 1e-9]
        if twins:
            flagged.update(twins)
            others = ", ".join(clean(literals[j].arg) for j in twins)
            out.append(Finding("warn", "MAGIC_NUMBER",
                               f"the literal {a.literal:g} mm is used for both "
                               f"{clean(a.arg)} and {others}; make it a variable "
                               "(cad_variable) and reference it with '#name'"))
            continue
        hit = next((f for f in fl.features if f.type != VARIABLE_FEATURE_TYPE
                    and any(isinstance(p, str) and (m := parse_length_mm(p, require_unit=True)) is not None
                            and abs(m - a.literal) <= 1e-9
                            for p in f.params.values())), None)
        if hit is not None:
            out.append(Finding("warn", "MAGIC_NUMBER",
                               f"{clean(a.arg)}: the literal {a.literal:g} mm also appears "
                               f"in feature {clean(hit.name)!r}; make it a variable "
                               "(cad_variable) and reference it with '#name'"))
    return out
