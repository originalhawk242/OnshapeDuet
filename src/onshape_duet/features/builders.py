"""Typed-feature builders: validated arguments -> Onshape feature JSON.

Parameter ids and enum values come from Onshape's own featurespecs, never from guesses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from onshape_duet.features.params import (FeatureSpecError, Value, count as _count, feature, length,
                                          p_bool, p_enum, p_features, p_lookup, p_qty, p_query)
from onshape_duet.features.params import angle as _angle
from onshape_duet.features.refs import (PlaneRef, RefError, compile_selector, plane_param, resolve,
                                       selector_entity)
from onshape_duet.model import FeatureList
from onshape_duet.textsafe import clean


@dataclass(frozen=True)
class Built:
    feature_json: dict
    values: list[Value]
    summary: dict


# Shown to the agent (summary["note"]) for mappings no live run has confirmed yet: a wrong
# mapping gives wrong geometry that still regenerates OK, so no REGEN_STATUS would fire.
UNVERIFIED_LIVE = "unverified against live Onshape: check the result in Onshape after applying"

_OPERATIONS = {"new": "NEW", "add": "ADD", "remove": "REMOVE", "intersect": "INTERSECT"}
_ENDS = ("blind", "symmetric", "through_all")


def _choice(arg: str, val, allowed) -> str:
    if not isinstance(val, str) or val not in allowed:
        raise FeatureSpecError(f"{arg}: must be one of {', '.join(allowed)}, got {clean(val)!r}")
    return val


def _flag(arg: str, val) -> bool:
    if not isinstance(val, bool):
        raise FeatureSpecError(f"{arg}: must be true or false")
    return val


def _profile_params(fl: FeatureList, sketch: str, op: str) -> tuple[list, str]:
    """Shared by extrude/revolve: sketch-region entities, SOLID body, operation type."""
    sk = resolve(fl, sketch, "sketch", {"newSketch"})
    return [p_query("entities", [f'qSketchRegion(makeId("{sk.id}"), true)']),
            p_enum("bodyType", "ExtendedToolBodyType", "SOLID"),
            p_enum("operationType", "NewBodyOperationType", _OPERATIONS[op])], sk.id


def _scope_params(op: str) -> list:
    # add/remove/intersect merge with all bodies; the spec default (False) leaves booleanScope empty
    return [p_bool("defaultScope", True)] if op != "new" else []


def build_extrude(fl: FeatureList, *, sketch: str, depth=None, operation: str = "new",
                  end: str = "blind", flip: bool = False) -> Built:
    op = _choice("operation", operation, tuple(_OPERATIONS))
    end = _choice("end", end, _ENDS)
    if not isinstance(flip, bool):
        raise FeatureSpecError("flip: must be true or false")
    params, _ = _profile_params(fl, sketch, op)
    values: list[Value] = []
    if end == "through_all":
        if depth is not None:
            raise FeatureSpecError("depth: not allowed with end='through_all' (the extrude "
                                   "goes through everything); omit depth")
    else:
        if depth is None:
            raise FeatureSpecError(f"depth: required for end={end!r}")
        values.append(length(depth, "depth"))
    params.append(p_enum("endBound", "BoundingType",
                         "THROUGH_ALL" if end == "through_all" else "BLIND"))
    if values:
        params.append(p_qty("depth", values[0]))
    params.append(p_bool("oppositeDirection", flip))
    if end == "symmetric":
        params.append(p_bool("symmetric", True))
    params += _scope_params(op)
    summary = {"sketch": clean(sketch, 100), "depth": values[0].expr if values else None,
               "operation": op, "end": end, "flip": flip}
    return Built(feature("extrude", "", params), values, summary)


def _clean_deep(x):
    if isinstance(x, str):
        return clean(x, 100)
    if isinstance(x, dict):
        return {clean(k, 100) if isinstance(k, str) else k: _clean_deep(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean_deep(v) for v in x]
    return x


def build_plane(fl: FeatureList, *, base: PlaneRef, offset, flip: bool = False) -> Built:
    if not isinstance(flip, bool):
        raise FeatureSpecError("flip: must be true or false")
    entities = plane_param("entities", base, fl)
    off = length(offset, "offset")
    params = [entities,
              p_enum("cplaneType", "CPlaneType", "OFFSET"),
              p_qty("offset", off),
              p_bool("oppositeDirection", flip)]
    summary = {"base": _clean_deep(base), "offset": off.expr, "flip": flip}
    return Built(feature("cPlane", "", params), [off], summary)


_SKETCH_ENTITY_ID = re.compile(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*")
_MAX_SKETCH_ENTITY_ID = 64


def _sketch_entity_id(arg: str, val, what: str) -> str:
    """Caller text placed inside a query string: this check is the security boundary."""
    if (not isinstance(val, str) or len(val) > _MAX_SKETCH_ENTITY_ID
            or not _SKETCH_ENTITY_ID.fullmatch(val)):
        raise FeatureSpecError(f"{arg}: expected {what} (letters, digits, underscore, "
                               f"dot-separated, at most {_MAX_SKETCH_ENTITY_ID} characters), "
                               f"got {clean(val, 60)!r}")
    return val


def build_revolve(fl: FeatureList, *, sketch: str, axis: str, angle=360,
                  operation: str = "new", symmetric: bool = False, flip: bool = False) -> Built:
    op = _choice("operation", operation, tuple(_OPERATIONS))
    symmetric, flip = _flag("symmetric", symmetric), _flag("flip", flip)
    axis = _sketch_entity_id("axis", axis, "a sketch line id like 'box.left'")
    value = _angle(angle, "angle")
    full = value.literal == 360
    if full and symmetric:
        raise FeatureSpecError("symmetric: meaningless for a full 360 revolve; give a smaller angle")
    params, sid = _profile_params(fl, sketch, op)
    params.append(p_query("axis", [f'sketchEntityQuery(makeId("{sid}"), EntityType.EDGE, "{axis}")']))
    values: list[Value] = []
    if full:
        params.append(p_bool("fullRevolve", True))
    else:
        values.append(value)
        params += [p_bool("fullRevolve", False), p_enum("endBound", "RevolveBoundingType", "BLIND"),
                   p_qty("angle", value), p_bool("symmetric", symmetric)]
    params.append(p_bool("oppositeDirection", flip))
    params += _scope_params(op)
    summary = {"sketch": clean(sketch, 100), "axis": axis, "angle": value.expr,
               "operation": op, "symmetric": symmetric, "flip": flip}
    return Built(feature("revolve", "", params), values, summary)


def _selector_query(sel, fl: FeatureList, arg: str, allowed: tuple[str, ...], param_id: str):
    """Selector whose entity must be one of `allowed` -> query parameter `param_id`."""
    if selector_entity(sel) not in allowed:
        raise RefError(f"{arg}: selector entity must be {' or '.join(repr(a) for a in allowed)}, "
                       f"got {clean(selector_entity(sel), 30)!r}")
    return p_query(param_id, [compile_selector(sel, fl, arg)])


def _edge_query(edges, fl: FeatureList):
    """Edge or face selector (a face rounds all its edges); refuses bodies."""
    if selector_entity(edges) not in ("edge", "face"):
        raise RefError("edges: selector entity must be 'edge' or 'face' (a face selects all of "
                       "its edges), not 'body'")
    return p_query("entities", [compile_selector(edges, fl, "edges")])


def build_fillet(fl: FeatureList, *, edges, radius) -> Built:
    query = _edge_query(edges, fl)
    r = length(radius, "radius")
    params = [p_enum("filletType", "FilletType", "EDGE"), query,
              p_enum("blendControlType", "BlendControlType", "RADIUS"),
              p_enum("crossSection", "FilletCrossSection", "CIRCULAR"),
              p_qty("radius", r)]
    return Built(feature("fillet", "", params), [r],
                 {"edges": _clean_deep(edges), "radius": r.expr})


def build_chamfer(fl: FeatureList, *, edges, distance) -> Built:
    query = _edge_query(edges, fl)
    d = length(distance, "distance")
    params = [query, p_enum("chamferType", "ChamferType", "EQUAL_OFFSETS"), p_qty("width", d)]
    return Built(feature("chamfer", "", params), [d],
                 {"edges": _clean_deep(edges), "distance": d.expr})


def build_shell(fl: FeatureList, *, faces, thickness, outward: bool = False) -> Built:
    query = _selector_query(faces, fl, "faces", ("face",), "entities")
    t = length(thickness, "thickness")
    outward = _flag("outward", outward)
    # UNVERIFIED live until Task 14: "oppositeDirection = outward" (spec says only Boolean).
    # isHollow and parts are deliberately not emitted.
    params = [query, p_qty("thickness", t), p_bool("oppositeDirection", outward)]
    return Built(feature("shell", "", params), [t],
                 {"faces": _clean_deep(faces), "thickness": t.expr, "outward": outward,
                  "note": f"shell direction (outward): {UNVERIFIED_LIVE}"})


_BOOLEAN_OPS = {"union": "UNION", "subtract": "SUBTRACTION", "intersect": "INTERSECTION"}


def build_boolean(fl: FeatureList, *, operation: str, tools, targets=None,
                  keep_tools: bool = False) -> Built:
    op = _choice("operation", operation, tuple(_BOOLEAN_OPS))
    keep_tools = _flag("keep_tools", keep_tools)
    if op == "subtract" and targets is None:
        raise FeatureSpecError("targets: required for subtract (the bodies to cut from)")
    if op != "subtract" and targets is not None:
        raise FeatureSpecError(f"targets: only allowed for subtract; {op} combines the tools")
    params = [p_enum("operationType", "BooleanOperationType", _BOOLEAN_OPS[op]),
              _selector_query(tools, fl, "tools", ("body",), "tools")]
    summary = {"operation": op, "tools": _clean_deep(tools), "keep_tools": keep_tools}
    if targets is not None:
        params.append(_selector_query(targets, fl, "targets", ("body",), "targets"))
        summary["targets"] = _clean_deep(targets)
    params.append(p_bool("keepTools", keep_tools))
    return Built(feature("booleanBodies", "", params), [], summary)


_MAX_FEATURES = 20


def _feature_ids(fl: FeatureList, features) -> tuple[list[str], list[str]]:
    """1-20 feature names -> (de-duplicated ids in order, cleaned names)."""
    if (not isinstance(features, list) or not 1 <= len(features) <= _MAX_FEATURES
            or not all(isinstance(n, str) for n in features)):
        raise FeatureSpecError(f"features: expected a list of 1 to {_MAX_FEATURES} feature names")
    ids: list[str] = []
    names: list[str] = []
    for n in features:
        f = resolve(fl, n, "features")
        if f.id not in ids:
            ids.append(f.id)
            names.append(clean(n, 100))
    return ids, names


def build_pattern(fl: FeatureList, *, kind: str, features, count, direction=None, spacing=None,
                  axis=None, angle=None) -> Built:
    """angle: circular only (None means a full 360); a linear pattern refuses it."""
    kind = _choice("kind", kind, ("linear", "circular"))
    ids, names = _feature_ids(fl, features)
    n = _count(count, "count")
    params = [p_enum("patternType", "PatternType", "FEATURE"), p_features("instanceFunction", ids)]
    if kind == "linear":
        if axis is not None:
            raise FeatureSpecError("axis: not allowed for a linear pattern; use direction")
        if angle is not None:
            raise FeatureSpecError("angle: not allowed for a linear pattern; it is for circular "
                                   "patterns only")
        if direction is None:
            raise FeatureSpecError("direction: required for a linear pattern (an edge or face selector)")
        if spacing is None:
            raise FeatureSpecError("spacing: required for a linear pattern")
        params.append(_selector_query(direction, fl, "direction", ("edge", "face"), "directionOne"))
        d = length(spacing, "spacing")
        params += [p_qty("distance", d), p_qty("instanceCount", n), p_bool("oppositeDirection", False)]
        values = [d, n]
        summary = {"kind": kind, "features": names, "count": n.expr, "spacing": d.expr,
                   "direction": _clean_deep(direction)}
    else:
        for arg, val in (("direction", direction), ("spacing", spacing)):
            if val is not None:
                raise FeatureSpecError(f"{arg}: not allowed for a circular pattern; use axis")
        if axis is None:
            raise FeatureSpecError("axis: required for a circular pattern (an edge selector)")
        params.append(_selector_query(axis, fl, "axis", ("edge",), "axis"))
        a = _angle(360 if angle is None else angle, "angle")
        params += [p_qty("angle", a), p_qty("instanceCount", n), p_bool("equalSpace", True)]
        values = [a, n]
        summary = {"kind": kind, "features": names, "count": n.expr, "angle": a.expr,
                   "axis": _clean_deep(axis)}
    return Built(feature(f"{kind}Pattern", "", params), values, summary)


def build_mirror(fl: FeatureList, *, features, plane: PlaneRef) -> Built:
    ids, names = _feature_ids(fl, features)
    params = [p_enum("patternType", "MirrorType", "FEATURE"), p_features("instanceFunction", ids),
              plane_param("mirrorPlane", plane, fl)]
    return Built(feature("mirror", "", params), [],
                 {"features": names, "plane": _clean_deep(plane)})


# Hole: the live spec carries LEGACY, V2 and V3 variants of the same settings, and which ones the
# current hole reads cannot be known offline. Every setting is therefore emitted in every variant
# consistently; the Task 14 live run decides which can be dropped.
def build_hole(fl: FeatureList, *, sketch: str, points, diameter, depth=None,
               style: str = "simple") -> Built:
    if style != "simple":
        raise FeatureSpecError("style: only 'simple' holes are supported; counterbore/countersink "
                               "are deferred")
    if not isinstance(points, list) or not 1 <= len(points) <= 50:
        raise FeatureSpecError("points: expected a list of 1 to 50 sketch point ids")
    ids = list(dict.fromkeys(_sketch_entity_id("points", p, "a sketch point id like 'c1.center'")
                             for p in points))
    sk = resolve(fl, sketch, "sketch", {"newSketch"})
    d = length(diameter, "diameter")
    values = [d]
    params = [p_enum("holeVersion", "HoleVersion", "V3"), p_bool("isV2", True),
              p_enum("unitsSystem", "UnitsSystem", "METRIC"),
              p_enum("threadStandard", "ThreadStandard", "UNSET"),
              p_enum("style", "HoleStyle", "SIMPLE"), p_enum("styleV2", "HoleStyle", "SIMPLE"),
              p_query("locations", [f'sketchEntityQuery(makeId("{sk.id}"), EntityType.VERTEX, "{pt}")'
                                    for pt in ids]),
              p_query("scope", ["qAllModifiableSolidBodies()"]),
              p_qty("holeDiameter", d), p_qty("holeDiameterV2", d), p_qty("holeDiameterV3", d),
              p_enum("startStyle", "HoleStartStyle", "PART"),
              # Onshape fills in no omitted parameter: the V2 precondition needs a lookup table
              # path, and V3 copies tipAngleV3 over tipAngle (live finding, 2026-11-24).
              p_lookup("isoHoleTableEx", {"size": "5", "holeType": "Drilled"}),
              p_enum("tipAngleStyle", "TipAngleStyle", "DEGREE118"),
              p_qty("tipAngleV3", _angle(118, "tipAngleV3"))]
    if depth is None:
        params += [p_enum("endStyle", "HoleEndStyle", "THROUGH"),
                   p_enum("endStyleV2", "HoleEndStyleV2", "THROUGH")]
    else:
        dep = length(depth, "depth")
        values.append(dep)
        params += [p_enum("endStyle", "HoleEndStyle", "BLIND"),
                   p_enum("endStyleV2", "HoleEndStyleV2", "BLIND"),
                   p_qty("holeDepth", dep), p_qty("holeDepthV3", dep)]
    params.append(p_bool("oppositeDirection", False))
    summary = {"sketch": clean(sketch, 100), "points": ids, "diameter": d.expr,
               "depth": values[1].expr if depth is not None else None,
               "note": f"hole settings (LEGACY/V2/V3 variants): {UNVERIFIED_LIVE}"}
    return Built(feature("hole", "", params), values, summary)
