"""Typed-feature builders: validated arguments -> Onshape feature JSON.

Parameter ids and enum values come from Onshape's own featurespecs, never from guesses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from onshape_duet.features.params import (FeatureSpecError, Value, feature, length, p_bool,
                                          p_enum, p_qty, p_query)
from onshape_duet.features.params import angle as _angle
from onshape_duet.features.refs import PlaneRef, plane_param, resolve
from onshape_duet.model import FeatureList
from onshape_duet.textsafe import clean


@dataclass(frozen=True)
class Built:
    feature_json: dict
    values: list[Value]
    summary: dict


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
               "operation": op, "end": end}
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


_AXIS_ID = re.compile(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*")


def build_revolve(fl: FeatureList, *, sketch: str, axis: str, angle=360,
                  operation: str = "new", symmetric: bool = False, flip: bool = False) -> Built:
    op = _choice("operation", operation, tuple(_OPERATIONS))
    symmetric, flip = _flag("symmetric", symmetric), _flag("flip", flip)
    # `axis` is caller text placed inside a query string: this check is the security boundary.
    if not isinstance(axis, str) or not _AXIS_ID.fullmatch(axis):
        raise FeatureSpecError(f"axis: expected a sketch line id like 'box.left' (letters, digits, "
                               f"underscore, dot-separated), got {clean(axis, 60)!r}")
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
               "operation": op, "symmetric": symmetric}
    return Built(feature("revolve", "", params), values, summary)
