"""Sketch spec parsing. Spec numbers are millimetres.

Spec shape:
  {"plane": "Front",
   "entities": [{"id", "type": "point|line|circle|rectangle|<unassessed>", ...}],
   "constraints": [{"type", "refs": [...]}],
   "dimensions": [{"type", "ref" | "refs", "value": number | "#var"}]}

Entity coordinates (the nominal configuration the solver linearises at):
  point:     x, y
  line:      start [x, y], end [x, y]
  circle:    center [x, y], radius
  rectangle: corner1 [x, y], corner2 [x, y]  (expanded to lines
             <id>.bottom/.right/.top/.left plus coincident/horizontal/vertical)

Refs: an entity id, or a point on one: "<line>.start", "<line>.end",
"<circle>.center".
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..model import Variable


class SketchSpecError(ValueError):
    pass


SOLVABLE_ENTITIES = {"point", "line", "circle"}
UNASSESSED_ENTITIES = {"arc", "ellipse", "spline", "text"}
CONSTRAINT_TYPES = {"coincident", "horizontal", "vertical", "parallel",
                    "perpendicular", "equal", "ground"}
DIMENSION_TYPES = {"length", "diameter", "distance_h", "distance_v"}
POINT_PARTS = {"start", "end", "center"}
PLANE_NAMES = ("Front", "Top", "Right")
_ENTITY_ID = re.compile(r"[A-Za-z0-9_]+")
_VAR_NAME = re.compile(r"[a-z][a-z0-9_]*")  # same rule as cad_variable


@dataclass
class Entity:
    id: str
    type: str  # point | line | circle | unassessed type
    coords: list[float] = field(default_factory=list)  # point x,y; line x1,y1,x2,y2; circle cx,cy,r


@dataclass
class Constraint:
    type: str
    refs: list[str]


@dataclass
class Dimension:
    type: str
    refs: list[str]
    value: float
    expr: str | None = None  # original "#name" variable reference, if any


@dataclass
class Sketch:
    plane: str | dict  # standard name, or a PlaneRef dict validated by features.refs
    entities: dict[str, Entity] = field(default_factory=dict)
    constraints: list[Constraint] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    not_assessed: list[str] = field(default_factory=list)


_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
_VAR_RE = re.compile(rf"^\s*({_NUM})\s*(mm|cm|m|in)?\s*$")
_UNIT_MM = {None: 1.0, "mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}


def referenced_variables(spec: dict) -> set[str]:
    """Names of the '#variable' references used by the spec's dimensions."""
    names: set[str] = set()
    dims = spec.get("dimensions") if isinstance(spec, dict) else None
    for d in dims if isinstance(dims, list) else []:
        val = d.get("value") if isinstance(d, dict) else None
        if isinstance(val, str) and val.startswith("#"):
            names.add(val[1:])
    return names


def evaluate_variables(vars: list[Variable], only: set[str] | None = None) -> dict[str, float]:
    """Evaluate '<number> [unit]' variables to millimetres.

    With `only`, variables outside that set are ignored entirely, so a derived
    variable (e.g. "#w * 2") only matters when the spec references it.
    """
    out: dict[str, float] = {}
    for v in vars:
        if only is not None and v.name not in only:
            continue
        m = _VAR_RE.match(v.expression)
        if not m:
            raise SketchSpecError(
                f"variable {v.name!r}: cannot evaluate {v.expression!r} "
                "(only '<number> [mm|cm|m|in]' is supported)")
        val = float(m.group(1)) * _UNIT_MM[m.group(2)]
        if not math.isfinite(val):
            raise SketchSpecError(f"variable {v.name!r}: value must be finite")
        out[v.name] = val
    return out


def _number(x, what: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise SketchSpecError(f"{what}: expected a number, got {x!r}")
    if not math.isfinite(x):
        raise SketchSpecError(f"{what}: must be a finite number, got {x!r}")
    return float(x)


def _pair(x, what: str) -> tuple[float, float]:
    if not isinstance(x, (list, tuple)) or len(x) != 2:
        raise SketchSpecError(f"{what}: expected [x, y], got {x!r}")
    return _number(x[0], what), _number(x[1], what)


def _add(sketch: Sketch, ent: Entity) -> None:
    if ent.id in sketch.entities or ent.id in sketch.not_assessed:
        raise SketchSpecError(f"duplicate entity id {ent.id!r}")
    sketch.entities[ent.id] = ent


def _expand_rectangle(sketch: Sketch, rid: str, c1, c2) -> None:
    (x1, y1), (x2, y2) = c1, c2
    sides = {
        "bottom": (x1, y1, x2, y1),
        "right": (x2, y1, x2, y2),
        "top": (x2, y2, x1, y2),
        "left": (x1, y2, x1, y1),
    }
    for name, co in sides.items():
        _add(sketch, Entity(f"{rid}.{name}", "line", list(co)))
    for a, b in (("bottom", "right"), ("right", "top"), ("top", "left"), ("left", "bottom")):
        sketch.constraints.append(Constraint("coincident", [f"{rid}.{a}.end", f"{rid}.{b}.start"]))
    for n in ("bottom", "top"):
        sketch.constraints.append(Constraint("horizontal", [f"{rid}.{n}"]))
    for n in ("left", "right"):
        sketch.constraints.append(Constraint("vertical", [f"{rid}.{n}"]))


def point_coords(sketch: Sketch, ref: str) -> tuple[float, float]:
    """Nominal (x, y) of a point reference such as 'p', 'l.start' or 'c.center'."""
    eid, part = resolve_ref(sketch, ref, "ref")
    ent = sketch.entities.get(eid)
    if ent is None:
        raise SketchSpecError(f"ref {ref!r} cannot be analysed")
    c = ent.coords
    if ent.type == "point" and part is None:
        return c[0], c[1]
    if ent.type == "line" and part == "start":
        return c[0], c[1]
    if ent.type == "line" and part == "end":
        return c[2], c[3]
    if ent.type == "circle" and part == "center":
        return c[0], c[1]
    raise SketchSpecError(f"ref {ref!r} is not a point")


def touches_unassessed(sketch: Sketch, refs) -> bool:
    return any(resolve_ref(sketch, r, "ref")[0] in sketch.not_assessed for r in refs)


def resolve_ref(sketch: Sketch, ref, what: str) -> tuple[str, str | None]:
    """Return (entity_id, part) where part is start/end/center or None."""
    if not isinstance(ref, str):
        raise SketchSpecError(f"{what}: ref must be a string, got {ref!r}")
    if ref in sketch.entities:
        return ref, None
    if ref in sketch.not_assessed:
        return ref, None
    base, _, part = ref.rpartition(".")
    if base and part in POINT_PARTS and base in sketch.not_assessed:
        return base, part
    if base and part in POINT_PARTS and base in sketch.entities:
        ent = sketch.entities[base]
        ok = (ent.type == "line" and part in ("start", "end")) or (
            ent.type == "circle" and part == "center")
        if not ok:
            raise SketchSpecError(f"{what}: {ent.type} {base!r} has no point {part!r}")
        return base, part
    raise SketchSpecError(f"{what}: unknown entity ref {ref!r}")


def parse_sketch_spec(spec: dict, variables: dict[str, float]) -> Sketch:
    if not isinstance(spec, dict):
        raise SketchSpecError("sketch spec must be an object")
    plane = spec.get("plane", "Front")
    if not isinstance(plane, dict) and (not isinstance(plane, str) or plane not in PLANE_NAMES):
        raise SketchSpecError(f"plane must be one of {', '.join(PLANE_NAMES)}, got {plane!r}")
    sketch = Sketch(plane=plane)
    for key in ("entities", "constraints", "dimensions"):
        items = spec.get(key, [])
        if not isinstance(items, list) or not all(isinstance(it, dict) for it in items):
            raise SketchSpecError(f"{key}: expected a list of objects")
    for i, e in enumerate(spec.get("entities", [])):
        etype = e.get("type")
        if not isinstance(etype, str) or etype not in SOLVABLE_ENTITIES | UNASSESSED_ENTITIES | {"rectangle"}:
            raise SketchSpecError(f"entity #{i + 1}: unknown entity type {etype!r}")
        eid = e["id"] if "id" in e else f"{etype}_{i + 1}"
        if not isinstance(eid, str) or not _ENTITY_ID.fullmatch(eid):
            raise SketchSpecError(
                f"entity #{i + 1}: id must be a non-empty string of letters, digits and "
                f"underscores, got {eid!r}")
        what = f"entity {eid!r}"
        if etype == "point":
            _add(sketch, Entity(eid, "point", [_number(e.get("x"), what), _number(e.get("y"), what)]))
        elif etype == "line":
            sx, sy = _pair(e.get("start"), what + " start")
            ex, ey = _pair(e.get("end"), what + " end")
            _add(sketch, Entity(eid, "line", [sx, sy, ex, ey]))
        elif etype == "circle":
            cx, cy = _pair(e.get("center"), what + " center")
            radius = _number(e.get("radius"), what + " radius")
            if radius <= 0:
                raise SketchSpecError(f"{what}: radius must be positive, got {radius:g}")
            _add(sketch, Entity(eid, "circle", [cx, cy, radius]))
        elif etype == "rectangle":
            _expand_rectangle(sketch, eid, _pair(e.get("corner1"), what + " corner1"),
                              _pair(e.get("corner2"), what + " corner2"))
        elif etype in UNASSESSED_ENTITIES:
            if eid in sketch.entities or eid in sketch.not_assessed:
                raise SketchSpecError(f"duplicate entity id {eid!r}")
            sketch.not_assessed.append(eid)
        else:
            raise SketchSpecError(f"{what}: unknown entity type {etype!r}")

    for j, c in enumerate(spec.get("constraints", [])):
        ctype = c.get("type")
        if ctype not in CONSTRAINT_TYPES:
            raise SketchSpecError(f"constraint #{j + 1}: unknown constraint type {ctype!r}")
        refs = c.get("refs", [])
        if not isinstance(refs, list):
            raise SketchSpecError(f"constraint #{j + 1}: refs must be a list, got {refs!r}")
        for r in refs:
            resolve_ref(sketch, r, f"constraint #{j + 1} ({ctype})")
        sketch.constraints.append(Constraint(ctype, list(refs)))

    for j, d in enumerate(spec.get("dimensions", [])):
        dtype = d.get("type")
        if dtype not in DIMENSION_TYPES:
            raise SketchSpecError(f"dimension #{j + 1}: unknown dimension type {dtype!r}")
        refs = d["refs"] if "refs" in d else [d.get("ref")]
        if not isinstance(refs, list):
            raise SketchSpecError(f"dimension #{j + 1}: refs must be a list, got {refs!r}")
        for r in refs:
            resolve_ref(sketch, r, f"dimension #{j + 1} ({dtype})")
        val = d.get("value")
        expr = None
        if isinstance(val, str):
            name = val[1:] if val.startswith("#") else None
            if name is None:
                raise SketchSpecError(
                    f"dimension #{j + 1}: string value {val!r} must be a '#variable' reference")
            if not _VAR_NAME.fullmatch(name):
                # the name is emitted verbatim into an Onshape expression ("#name")
                raise SketchSpecError(
                    f"dimension #{j + 1}: invalid variable name {name!r} in {val!r} "
                    "(must match ^[a-z][a-z0-9_]*$)")
            if name not in variables:
                raise SketchSpecError(f"dimension #{j + 1}: undefined variable {name!r}")
            val = variables[name]
            expr = "#" + name
        num = _number(val, f"dimension #{j + 1} value")
        if dtype in ("length", "diameter") and num <= 0:
            raise SketchSpecError(f"dimension #{j + 1} ({dtype}): value must be positive (> 0), got {num:g}")
        if dtype in ("distance_h", "distance_v") and num < 0:
            raise SketchSpecError(
                f"dimension #{j + 1} ({dtype}): value is an unsigned distance and must be >= 0, got {num:g}")
        sketch.dimensions.append(Dimension(dtype, refs, num, expr))
    return sketch
