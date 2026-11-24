"""Local degrees-of-freedom analysis.

Algorithm: every solvable entity contributes variables (point 2, line 4,
circle 3). Each constraint/dimension contributes residual rows. The analytic
Jacobian is evaluated at the nominal coordinates stored in the sketch.
dof = n_vars - rank(J); redundant = rank(J) < n_rows. An entity is free when
the null space of J has a component on its variables above 1e-9.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .spec import Sketch, SketchSpecError, resolve_ref

TOL = 1e-9
# Each residual row is compared to a tolerance built from the size of ITS OWN
# entities (line length, circle diameter, the distance a dimension spans), never
# from coordinate extents or one sketch-wide scale: a far-away point must not loosen
# the check on a small line, a small feature far from the origin must not be judged
# by its distance from the origin, and a tiny feature near the origin must not be
# tightened to nothing:
#   tol = max(REL_TOL * size, FLOAT_EPS * magnitude, ABS_TOL)
#   quadratic rows (products of two direction vectors): REL_TOL * size**2,
#   FLOAT_EPS * magnitude * size, ABS_TOL**2
# magnitude is the largest |coordinate| the row touches (float-noise floor).
# REL_TOL = 1e-6 of size is far above float noise yet far below any real design
# error (1 micron on 1 m); FLOAT_EPS * magnitude is the noise floor of the
# coordinates themselves; ABS_TOL (1 nanometre) is the floor near zero.
REL_TOL = 1e-6
FLOAT_EPS = 1e-12
ABS_TOL = 1e-6
_NPTS = {"point": 1, "line": 2, "circle": 1}


@dataclass
class DofReport:
    dof: int
    redundant: bool
    free: list[str]
    not_assessed: list[str]
    conflicts: list[str] = field(default_factory=list)


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


@dataclass
class Row:
    label: str
    jac: np.ndarray
    fn: Callable[[np.ndarray], float]  # residual as a function of the variable vector
    degree: int  # 1 linear, 2 quadratic (scales with length squared)
    value: float = 0.0  # driving value of a dimension row (millimetres)
    size: float = 0.0  # size of the entities the row relates (millimetres)


def row_tolerance(row: Row, x: np.ndarray) -> float:
    idx = np.flatnonzero(row.jac)
    vals = x[idx] if idx.size else np.zeros(0)
    magnitude = max(float(np.abs(vals).max()) if vals.size else 0.0, abs(row.value))
    size = max(row.size, abs(row.value))
    if row.degree == 1:
        return max(REL_TOL * size, FLOAT_EPS * magnitude, ABS_TOL)
    return max(REL_TOL * size ** 2, FLOAT_EPS * magnitude * size, ABS_TOL ** 2)


def _skipped(sketch: Sketch, refs) -> bool:
    for r in refs:
        eid, _ = resolve_ref(sketch, r, "ref")
        if eid in sketch.not_assessed:
            return True
    return False


def build_rows(sketch: Sketch) -> tuple[list[Row], _Layout]:
    lay = _Layout(sketch)
    x = lay.x
    n = len(x)
    rows: list[Row] = []

    def emit(label, jac, fn, degree=1, value=0.0, size=0.0):
        rows.append(Row(label, jac, fn, degree, value, size))

    def esize(refs) -> float:
        """Largest own size (line length, circle diameter) among the referenced entities."""
        best = 0.0
        for r in refs:
            eid, _ = resolve_ref(sketch, r, "ref")
            ent = sketch.entities.get(eid)
            if ent is None:
                continue
            if ent.type == "line":
                best = max(best, float(np.hypot(ent.coords[2] - ent.coords[0],
                                                ent.coords[3] - ent.coords[1])))
            elif ent.type == "circle":
                best = max(best, 2.0 * ent.coords[2])
        return best

    def unit(*terms):
        r = np.zeros(n)
        for idx, coef in terms:
            r[idx] += coef
        return r

    def nrefs(refs, k, what):
        if len(refs) != k:
            raise SketchSpecError(f"{what}: expected {k} ref(s), got {len(refs)}")

    def dirvec(o):
        return x[o + 2] - x[o], x[o + 3] - x[o + 1]

    for i, c in enumerate(sketch.constraints):
        what = f"constraint #{i + 1} ({c.type})"
        if _skipped(sketch, c.refs):
            continue
        if c.type == "coincident":
            nrefs(c.refs, 2, what)
            pa, pb = lay.point(c.refs[0], what), lay.point(c.refs[1], what)
            for a, b in zip(pa, pb):
                emit(what, unit((a, 1), (b, -1)), lambda v, a=a, b=b: v[a] - v[b],
                     size=esize(c.refs))
        elif c.type in ("horizontal", "vertical"):
            k = 1 if c.type == "horizontal" else 0
            if len(c.refs) == 1:
                o = lay.line(c.refs[0], what)
                a, b = o + k, o + 2 + k
            else:
                nrefs(c.refs, 2, what)
                a = lay.point(c.refs[0], what)[k]
                b = lay.point(c.refs[1], what)[k]
            emit(what, unit((a, 1), (b, -1)), lambda v, a=a, b=b: v[a] - v[b],
                 size=esize(c.refs))
        elif c.type in ("parallel", "perpendicular"):
            nrefs(c.refs, 2, what)
            o1, o2 = lay.line(c.refs[0], what), lay.line(c.refs[1], what)
            d1x, d1y = dirvec(o1)
            d2x, d2y = dirvec(o2)
            if c.type == "parallel":  # d1x*d2y - d1y*d2x
                jac = unit((o1 + 2, d2y), (o1, -d2y), (o1 + 3, -d2x), (o1 + 1, d2x),
                           (o2 + 3, d1x), (o2 + 1, -d1x), (o2 + 2, -d1y), (o2, d1y))

                def fn(v, o1=o1, o2=o2):
                    return ((v[o1 + 2] - v[o1]) * (v[o2 + 3] - v[o2 + 1])
                            - (v[o1 + 3] - v[o1 + 1]) * (v[o2 + 2] - v[o2]))
            else:  # d1x*d2x + d1y*d2y
                jac = unit((o1 + 2, d2x), (o1, -d2x), (o1 + 3, d2y), (o1 + 1, -d2y),
                           (o2 + 2, d1x), (o2, -d1x), (o2 + 3, d1y), (o2 + 1, -d1y))

                def fn(v, o1=o1, o2=o2):
                    return ((v[o1 + 2] - v[o1]) * (v[o2 + 2] - v[o2])
                            + (v[o1 + 3] - v[o1 + 1]) * (v[o2 + 3] - v[o2 + 1]))
            emit(what, jac, fn, 2, size=esize(c.refs))
        elif c.type == "equal":
            nrefs(c.refs, 2, what)
            (e1, o1), (e2, o2) = lay.entity(c.refs[0], what), lay.entity(c.refs[1], what)
            if e1.type == "line" and e2.type == "line":
                terms = []
                for o, s in ((o1, 1.0), (o2, -1.0)):
                    dx, dy = dirvec(o)
                    terms += [(o + 2, 2 * s * dx), (o, -2 * s * dx),
                              (o + 3, 2 * s * dy), (o + 1, -2 * s * dy)]

                def fn(v, o1=o1, o2=o2):
                    return (((v[o1 + 2] - v[o1]) ** 2 + (v[o1 + 3] - v[o1 + 1]) ** 2)
                            - ((v[o2 + 2] - v[o2]) ** 2 + (v[o2 + 3] - v[o2 + 1]) ** 2))
                emit(what, unit(*terms), fn, 2, size=esize(c.refs))
            elif e1.type == "circle" and e2.type == "circle":
                emit(what, unit((o1 + 2, 1), (o2 + 2, -1)), lambda v, a=o1 + 2, b=o2 + 2: v[a] - v[b],
                     size=esize(c.refs))
            else:
                raise SketchSpecError(
                    f"{what}: equal needs two lines or two circles, got {e1.type} and {e2.type}")
        elif c.type == "ground":
            nrefs(c.refs, 1, what)
            for idx in lay.point(c.refs[0], what):
                emit(what, unit((idx, 1)), lambda v, idx=idx: v[idx])

    for i, d in enumerate(sketch.dimensions):
        what = f"dimension #{i + 1} ({d.type})"
        if _skipped(sketch, d.refs):
            continue
        val = d.value
        if d.type == "length":
            nrefs(d.refs, 1, what)
            o = lay.line(d.refs[0], what)
            dx, dy = dirvec(o)
            length = float(np.hypot(dx, dy))
            if length < TOL:
                raise SketchSpecError(f"{what}: line {d.refs[0]!r} has zero length at its nominal position")
            ux, uy = dx / length, dy / length
            emit(what, unit((o + 2, ux), (o, -ux), (o + 3, uy), (o + 1, -uy)),
                 lambda v, o=o, val=val: float(np.hypot(v[o + 2] - v[o], v[o + 3] - v[o + 1])) - val,
                 value=val, size=length)
        elif d.type == "diameter":
            nrefs(d.refs, 1, what)
            ent, o = lay.entity(d.refs[0], what)
            if ent.type != "circle":
                raise SketchSpecError(f"{what}: {d.refs[0]!r} is not a circle")
            emit(what, unit((o + 2, 2.0)), lambda v, o=o, val=val: 2.0 * v[o + 2] - val, value=val, size=val)
        else:  # distance_h / distance_v
            # Onshape's distance is unsigned: residual |delta| - val, with the
            # Jacobian sign taken from the nominal sign of delta.
            k = 0 if d.type == "distance_h" else 1
            if len(d.refs) == 1:
                a, b = lay.point(d.refs[0], what)[k], None
            else:
                nrefs(d.refs, 2, what)
                a = lay.point(d.refs[0], what)[k]
                b = lay.point(d.refs[1], what)[k]
            sgn = -1.0 if (x[a] - (x[b] if b is not None else 0.0)) < 0 else 1.0
            terms = [(a, sgn)] + ([(b, -sgn)] if b is not None else [])
            emit(what, unit(*terms),
                 lambda v, a=a, b=b, val=val: abs(v[a] - (v[b] if b is not None else 0.0)) - val,
                 value=val, size=max(esize(d.refs), abs(x[a] - (x[b] if b is not None else 0.0))))
    return rows, lay


def analyze(sketch: Sketch) -> DofReport:
    rows, lay = build_rows(sketch)
    n_vars = len(lay.x)
    n_rows = len(rows)
    x0 = np.array(lay.x, dtype=float)
    jac = np.vstack([r.jac for r in rows]) if rows else np.zeros((0, n_vars))
    res = np.array([r.fn(x0) for r in rows], dtype=float)

    conflicts: list[str] = []
    for r, val in zip(rows, res):
        tol = row_tolerance(r, x0)
        if abs(val) > tol:
            msg = f"{r.label}: residual {val:.6g} at the spec coordinates (tolerance {tol:.3g})"
            if r.label.endswith("(ground)"):
                msg += "; the point is not at the origin (ground pins a point to (0, 0))"
            if msg not in conflicts:
                conflicts.append(msg)

    rank = int(np.linalg.matrix_rank(jac)) if n_rows and n_vars else 0
    if n_rows and n_vars:
        aug_rank = int(np.linalg.matrix_rank(np.hstack([jac, res[:, None]])))
        if aug_rank > rank:
            conflicts.append(
                "contradictory constraints/dimensions: no configuration satisfies all rows "
                f"(rank [J|r] = {aug_rank} > rank J = {rank})")
        _, _, vt = np.linalg.svd(jac, full_matrices=True)
        null = vt[rank:].T
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
        redundant=rank < n_rows or bool(conflicts),
        free=free,
        not_assessed=list(sketch.not_assessed),
        conflicts=conflicts,
    )
