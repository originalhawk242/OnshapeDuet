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
    plane: str
    entities: dict[str, Entity] = field(default_factory=dict)
    constraints: list[Constraint] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    not_assessed: list[str] = field(default_factory=list)


_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
_VAR_RE = re.compile(rf"^\s*({_NUM})\s*(mm|cm|m|in)?\s*$")
_UNIT_MM = {None: 1.0, "mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}


def evaluate_variables(vars: list[Variable]) -> dict[str, float]:
    out: dict[str, float] = {}
    for v in vars:
        m = _VAR_RE.match(v.expression)
        if not m:
            raise SketchSpecError(
                f"variable {v.name!r}: cannot evaluate {v.expression!r} "
                "(only '<number> [mm|cm|m|in]' is supported)")
        out[v.name] = float(m.group(1)) * _UNIT_MM[m.group(2)]
    return out


def _number(x, what: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise SketchSpecError(f"{what}: expected a number, got {x!r}")
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
    sketch = Sketch(plane=str(spec.get("plane", "Front")))
    for key in ("entities", "constraints", "dimensions"):
        items = spec.get(key, [])
        if not isinstance(items, list) or not all(isinstance(it, dict) for it in items):
            raise SketchSpecError(f"{key}: expected a list of objects")
    for i, e in enumerate(spec.get("entities", [])):
        etype = e.get("type")
        eid = e.get("id") or f"{etype}_{i + 1}"
        what = f"entity {eid!r}"
        if etype == "point":
            _add(sketch, Entity(eid, "point", [_number(e.get("x"), what), _number(e.get("y"), what)]))
        elif etype == "line":
            sx, sy = _pair(e.get("start"), what + " start")
            ex, ey = _pair(e.get("end"), what + " end")
            _add(sketch, Entity(eid, "line", [sx, sy, ex, ey]))
        elif etype == "circle":
            cx, cy = _pair(e.get("center"), what + " center")
            _add(sketch, Entity(eid, "circle", [cx, cy, _number(e.get("radius"), what + " radius")]))
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
            if name not in variables:
                raise SketchSpecError(f"dimension #{j + 1}: undefined variable {name!r}")
            val = variables[name]
            expr = "#" + name
        sketch.dimensions.append(Dimension(dtype, refs, _number(val, f"dimension #{j + 1} value"), expr))
    return sketch
