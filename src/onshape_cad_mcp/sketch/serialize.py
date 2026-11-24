"""Serialise a parsed Sketch into Onshape feature JSON.

UNVERIFIED: these shapes are not yet checked against a live Onshape document
(that happens in Task 12). The constraint/dimension shapes were ported from
cadkit's `cadkit_mcp/sketch.py` (MIT; see NOTICE), which reports them as
live-verified for its own use. Treat every shape here as a hypothesis until
Task 12 confirms it.

Units: spec numbers are millimetres; geometry is emitted in metres and
dimension expressions carry an explicit "mm" unit. A dimension that came from a
"#name" variable reference keeps it as the Onshape expression ("#name"), so a
later variable change drives the sketch; the numeric value is only used for the
local solve. The "#name" expression syntax is unverified until Task 12.
"""
from __future__ import annotations

import math

from .spec import Sketch, SketchSpecError, point_coords, touches_unassessed

MM = 0.001  # millimetres -> metres
PLANES = {"Front": "JCC", "Top": "JDC", "Right": "JEC"}
ORIGIN_VERTEX = "IB"

_UNARY = {"horizontal": "HORIZONTAL", "vertical": "VERTICAL"}
_BINARY = {"coincident": "COINCIDENT", "parallel": "PARALLEL",
           "perpendicular": "PERPENDICULAR", "equal": "EQUAL"}


def _str(value: str, pid: str) -> dict:
    return {"btType": "BTMParameterString-149", "value": value, "parameterId": pid}


def _origin(pid: str) -> dict:
    return {"btType": "BTMParameterQueryList-148", "parameterId": pid,
            "queries": [{"btType": "BTMIndividualQuery-138", "deterministicIds": [ORIGIN_VERTEX]}]}


def _num(value: float) -> str:
    text = f"{value:.12f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _qty(dim) -> dict:
    # The driving value of every linear dimension lives under parameterId "length".
    return {"btType": "BTMParameterQuantity-147", "expression": dim.expr if dim.expr else f"{_num(dim.value)} mm",
            "parameterId": "length", "isInteger": False}


def _label(pid: str, expr: str) -> dict:
    return {"btType": "BTMParameterQuantity-147", "expression": expr, "parameterId": pid, "isInteger": False}


def _direction(d: str) -> dict:
    return {"btType": "BTMParameterEnum-145", "value": d,
            "enumName": "DimensionDirection", "parameterId": "direction"}


def _con(ctype: str, eid: str, params: list[dict]) -> dict:
    return {"btType": "BTMSketchConstraint-2", "constraintType": ctype,
            "entityId": eid, "parameters": params}


def _entity(eid: str, ent) -> dict:
    c = ent.coords
    if ent.type == "point":
        return {"btType": "BTMSketchPoint-158", "entityId": eid,
                "x": c[0] * MM, "y": c[1] * MM, "isConstruction": False}
    if ent.type == "line":
        sx, sy, ex, ey = c
        length = math.hypot(ex - sx, ey - sy)
        if length == 0:
            raise SketchSpecError(f"line {eid!r} has zero length")
        return {"btType": "BTMSketchCurveSegment-155", "entityId": eid,
                "startPointId": f"{eid}.start", "endPointId": f"{eid}.end",
                "startParam": 0.0, "endParam": length * MM,
                "geometry": {"btType": "BTCurveGeometryLine-117",
                             "pntX": sx * MM, "pntY": sy * MM,
                             "dirX": (ex - sx) / length, "dirY": (ey - sy) / length},
                "isConstruction": False}
    if ent.type == "circle":
        return {"btType": "BTMSketchCurve-4", "entityId": eid,
                "geometry": {"btType": "BTCurveGeometryCircle-115", "radius": c[2] * MM,
                             "xCenter": c[0] * MM, "yCenter": c[1] * MM,
                             "xDir": 1.0, "yDir": 0.0, "clockwise": False},
                "centerId": f"{eid}.center", "isConstruction": False}
    raise SketchSpecError(f"cannot serialise entity {eid!r} of type {ent.type!r}")


def _point_param(ref: str | None, first: bool) -> dict:
    """ref None = the sketch origin."""
    pid = ("externalFirst" if first else "externalSecond") if ref is None \
        else ("localFirst" if first else "localSecond")
    return _origin(pid) if ref is None else _str(ref, pid)


def _need(refs: list[str], n: int, what: str) -> None:
    if len(refs) != n:
        raise SketchSpecError(f"{what}: expected {n} ref(s), got {len(refs)}")


def to_feature_json(sketch: Sketch, name: str) -> dict:
    entities = [_entity(eid, e) for eid, e in sketch.entities.items()]
    constraints: list[dict] = []
    for i, c in enumerate(sketch.constraints, 1):
        what = f"constraint #{i} ({c.type})"
        eid = f"con{i}"
        if touches_unassessed(sketch, c.refs):
            continue  # never emit a constraint on an entity that is not written
        if c.type in _UNARY:
            if len(c.refs) not in (1, 2):
                raise SketchSpecError(f"{what}: expected 1 ref (a line) or 2 refs (points), got {len(c.refs)}")
            constraints.append(_con(_UNARY[c.type], eid, [
                _str(r, "localFirst" if n == 0 else "localSecond") for n, r in enumerate(c.refs)]))
        elif c.type in _BINARY:
            _need(c.refs, 2, what)
            constraints.append(_con(_BINARY[c.type], eid, [
                _str(c.refs[0], "localFirst"), _str(c.refs[1], "localSecond")]))
        elif c.type == "ground":
            _need(c.refs, 1, what)
            constraints.append(_con("COINCIDENT", eid, [
                _origin("externalFirst"), _str(c.refs[0], "localSecond")]))
        else:
            raise SketchSpecError(f"{what}: cannot serialise")
    for i, d in enumerate(sketch.dimensions, 1):
        what = f"dimension #{i} ({d.type})"
        eid = f"dim{i}"
        if touches_unassessed(sketch, d.refs):
            continue
        if d.type == "length":
            _need(d.refs, 1, what)
            constraints.append(_con("LENGTH", eid, [
                _str(d.refs[0], "localFirst"),
                {"btType": "BTMParameterEnum-145", "value": "MINIMUM",
                 "enumName": "DimensionDirection", "parameterId": "direction"},
                _qty(d),
                {"btType": "BTMParameterEnum-145", "value": "ALIGNED",
                 "enumName": "DimensionAlignment", "parameterId": "alignment"}]))
        elif d.type == "diameter":
            _need(d.refs, 1, what)
            # Live: a UI-made DIAMETER has labelRatio (radial multiple, >1 = outside the
            # circle) and labelAngle (rad); without them the label is not drawn.
            constraints.append(_con("DIAMETER", eid, [_str(d.refs[0], "localFirst"), _qty(d),
                                                      _label("labelRatio", "1.8"),
                                                      _label("labelAngle", "-0.7*rad")]))
        elif d.type in ("distance_h", "distance_v"):
            direction = _direction("HORIZONTAL" if d.type == "distance_h" else "VERTICAL")
            k = 0 if d.type == "distance_h" else 1
            # Onshape's DISTANCE value is unsigned and its direction comes from the
            # order of the two references, so order them (first -> second) with a
            # non-negative nominal delta along the axis; the value stays positive.
            if len(d.refs) == 1:  # point and the sketch origin
                pair = [None, d.refs[0]]
                if point_coords(sketch, d.refs[0])[k] < 0:
                    pair.reverse()
            else:
                _need(d.refs, 2, what)
                pair = list(d.refs)
                if point_coords(sketch, pair[1])[k] < point_coords(sketch, pair[0])[k]:
                    pair.reverse()
            constraints.append(_con("DISTANCE", eid, [
                _point_param(pair[0], True), _point_param(pair[1], False), direction, _qty(d)]))
        else:
            raise SketchSpecError(f"{what}: cannot serialise")
    plane_id = PLANES[sketch.plane]
    return {
        "btType": "BTMSketch-151", "featureType": "newSketch",
        "name": name, "suppressed": False,
        "parameters": [{"btType": "BTMParameterQueryList-148", "parameterId": "sketchPlane",
                        "queries": [{"btType": "BTMIndividualQuery-138",
                                     "deterministicIds": [plane_id]}]}],
        "entities": entities, "constraints": constraints,
    }
