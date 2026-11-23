"""Local degrees-of-freedom analysis.

Algorithm: every solvable entity contributes variables (point 2, line 4,
circle 3). Each constraint/dimension contributes residual rows. The analytic
Jacobian is evaluated at the nominal coordinates stored in the sketch.
dof = n_vars - rank(J); redundant = rank(J) < n_rows. An entity is free when
the null space of J has a component on its variables above 1e-9.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .spec import Sketch, SketchSpecError, resolve_ref

TOL = 1e-9
_NPTS = {"point": 1, "line": 2, "circle": 1}


@dataclass
class DofReport:
    dof: int
    redundant: bool
    free: list[str]
    not_assessed: list[str]


class _Layout:
    def __init__(self, sketch: Sketch) -> None:
        self.offset: dict[str, int] = {}
        self.x: list[float] = []
        for eid, ent in sketch.entities.items():
            self.offset[eid] = len(self.x)
            self.x.extend(ent.coords)
        self.sketch = sketch

    def point(self, ref: str, what: str) -> tuple[int, int]:
        """Variable indices (x, y) of a point reference."""
        eid, part = resolve_ref(self.sketch, ref, what)
        ent = self.sketch.entities.get(eid)
        if ent is None:
            raise SketchSpecError(f"{what}: {ref!r} cannot be analysed")
        o = self.offset[eid]
        if ent.type == "point" and part is None:
            return o, o + 1
        if ent.type == "line" and part == "start":
            return o, o + 1
        if ent.type == "line" and part == "end":
            return o + 2, o + 3
        if ent.type == "circle" and part == "center":
            return o, o + 1
        raise SketchSpecError(f"{what}: {ref!r} is not a point")

    def line(self, ref: str, what: str) -> int:
        eid, part = resolve_ref(self.sketch, ref, what)
        ent = self.sketch.entities.get(eid)
        if ent is None or ent.type != "line" or part is not None:
            raise SketchSpecError(f"{what}: {ref!r} is not a line")
        return self.offset[eid]

    def entity(self, ref: str, what: str):
        eid, part = resolve_ref(self.sketch, ref, what)
        ent = self.sketch.entities.get(eid)
        if ent is None or part is not None:
            raise SketchSpecError(f"{what}: {ref!r} cannot be analysed")
        return ent, self.offset[eid]


def _jacobian(sketch: Sketch) -> tuple[np.ndarray, _Layout]:
    lay = _Layout(sketch)
    x = lay.x
    n = len(x)
    rows: list[np.ndarray] = []

    def row() -> np.ndarray:
        r = np.zeros(n)
        rows.append(r)
        return r

    def nrefs(refs, k, what):
        if len(refs) != k:
            raise SketchSpecError(f"{what}: expected {k} ref(s), got {len(refs)}")

    def dirvec(o):  # line direction (dx, dy) and its variable indices
        return x[o + 2] - x[o], x[o + 3] - x[o + 1]

    for i, c in enumerate(sketch.constraints):
        what = f"constraint #{i + 1} ({c.type})"
        if any(r.split(".")[0] in sketch.not_assessed or r in sketch.not_assessed for r in c.refs):
            continue
        if c.type == "coincident":
            nrefs(c.refs, 2, what)
            (ax, ay), (bx, by) = lay.point(c.refs[0], what), lay.point(c.refs[1], what)
            for a, b in ((ax, bx), (ay, by)):
                r = row(); r[a] += 1; r[b] -= 1
        elif c.type in ("horizontal", "vertical"):
            k = 1 if c.type == "horizontal" else 0  # horizontal: y equal
            if len(c.refs) == 1:
                o = lay.line(c.refs[0], what)
                a, b = o + k, o + 2 + k
            else:
                nrefs(c.refs, 2, what)
                a = lay.point(c.refs[0], what)[k]
                b = lay.point(c.refs[1], what)[k]
            r = row(); r[a] += 1; r[b] -= 1
        elif c.type in ("parallel", "perpendicular"):
            nrefs(c.refs, 2, what)
            o1, o2 = lay.line(c.refs[0], what), lay.line(c.refs[1], what)
            d1x, d1y = dirvec(o1)
            d2x, d2y = dirvec(o2)
            r = row()
            if c.type == "parallel":  # d1x*d2y - d1y*d2x
                r[o1 + 2] += d2y; r[o1] -= d2y
                r[o1 + 3] -= d2x; r[o1 + 1] += d2x
                r[o2 + 3] += d1x; r[o2 + 1] -= d1x
                r[o2 + 2] -= d1y; r[o2] += d1y
            else:  # d1x*d2x + d1y*d2y
                r[o1 + 2] += d2x; r[o1] -= d2x
                r[o1 + 3] += d2y; r[o1 + 1] -= d2y
                r[o2 + 2] += d1x; r[o2] -= d1x
                r[o2 + 3] += d1y; r[o2 + 1] -= d1y
        elif c.type == "equal":
            nrefs(c.refs, 2, what)
            (e1, o1), (e2, o2) = lay.entity(c.refs[0], what), lay.entity(c.refs[1], what)
            if e1.type == "line" and e2.type == "line":
                r = row()  # |d1|^2 - |d2|^2
                for o, s in ((o1, 1.0), (o2, -1.0)):
                    dx, dy = dirvec(o)
                    r[o + 2] += 2 * s * dx; r[o] -= 2 * s * dx
                    r[o + 3] += 2 * s * dy; r[o + 1] -= 2 * s * dy
            elif e1.type == "circle" and e2.type == "circle":
                r = row(); r[o1 + 2] += 1; r[o2 + 2] -= 1
            else:
                raise SketchSpecError(
                    f"{what}: equal needs two lines or two circles, got {e1.type} and {e2.type}")
        elif c.type == "ground":
            nrefs(c.refs, 1, what)
            px, py = lay.point(c.refs[0], what)
            r = row(); r[px] = 1
            r = row(); r[py] = 1

    for i, d in enumerate(sketch.dimensions):
        what = f"dimension #{i + 1} ({d.type})"
        if any(r.split(".")[0] in sketch.not_assessed or r in sketch.not_assessed for r in d.refs):
            continue
        if d.type == "length":
            nrefs(d.refs, 1, what)
            o = lay.line(d.refs[0], what)
            dx, dy = dirvec(o)
            length = float(np.hypot(dx, dy))
            if length < TOL:
                raise SketchSpecError(f"{what}: line {d.refs[0]!r} has zero length at its nominal position")
            ux, uy = dx / length, dy / length
            r = row()
            r[o + 2] += ux; r[o] -= ux; r[o + 3] += uy; r[o + 1] -= uy
        elif d.type == "diameter":
            nrefs(d.refs, 1, what)
            ent, o = lay.entity(d.refs[0], what)
            if ent.type != "circle":
                raise SketchSpecError(f"{what}: {d.refs[0]!r} is not a circle")
            r = row(); r[o + 2] = 2.0
        else:  # distance_h / distance_v
            k = 0 if d.type == "distance_h" else 1
            if len(d.refs) == 1:
                r = row(); r[lay.point(d.refs[0], what)[k]] += 1
            else:
                nrefs(d.refs, 2, what)
                a = lay.point(d.refs[0], what)[k]
                b = lay.point(d.refs[1], what)[k]
                r = row(); r[b] += 1; r[a] -= 1

    jac = np.vstack(rows) if rows else np.zeros((0, n))
    return jac, lay


def analyze(sketch: Sketch) -> DofReport:
    jac, lay = _jacobian(sketch)
    n_rows, n_vars = jac.shape
    rank = int(np.linalg.matrix_rank(jac)) if n_rows and n_vars else 0
    if n_rows and n_vars:
        _, _, vt = np.linalg.svd(jac, full_matrices=True)
        null = vt[rank:].T  # n_vars x (n_vars - rank)
    else:
        null = np.eye(n_vars)
    free = []
    for eid, ent in sketch.entities.items():
        o = lay.offset[eid]
        block = null[o:o + len(ent.coords), :]
        if block.size and np.linalg.norm(block) > TOL:
            free.append(eid)
    return DofReport(
        dof=n_vars - rank,
        redundant=rank < n_rows,
        free=free,
        not_assessed=list(sketch.not_assessed),
    )
