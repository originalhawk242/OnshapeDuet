"""Editability lint for sketches (spec 7.x).

Every document- or caller-controlled string placed in a message goes through
`textsafe.clean` (R14).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from .sketch.solver import DofReport
from .textsafe import clean

_DEFAULT_NAME = re.compile(
    r"(Sketch|Extrude|Fillet|Revolve|Chamfer|Shell|Hole|Boolean|Pattern|Mirror|Plane) \d+")


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


def lint_sketch(spec: dict, report: DofReport, name: str) -> list[Finding]:
    out: list[Finding] = []
    dims = _dimensions(spec)

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

    if _DEFAULT_NAME.fullmatch(str(name)):
        out.append(Finding("warn", "DEFAULT_NAME",
                           f"name {clean(name)!r} looks like a default; give it a "
                           "descriptive name"))

    literals: dict[float, int] = defaultdict(int)
    for d in dims:
        v = d.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            literals[float(v)] += 1
    for v, n in literals.items():
        if n >= 2:
            out.append(Finding("warn", "MAGIC_NUMBER",
                               f"the literal {v:g} is used in {n} dimensions; "
                               "make it a variable and reference it with '#name'"))
    return out
