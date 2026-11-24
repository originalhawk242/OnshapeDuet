"""Typed-feature builders: validated arguments -> Onshape feature JSON.

Parameter ids and enum values come from Onshape's own featurespecs, never from guesses.
"""
from __future__ import annotations

from dataclasses import dataclass

from onshape_duet.features.params import (FeatureSpecError, Value, feature, length, p_bool,
                                          p_enum, p_qty, p_query)
from onshape_duet.features.refs import resolve
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


def build_extrude(fl: FeatureList, *, sketch: str, depth=None, operation: str = "new",
                  end: str = "blind", flip: bool = False) -> Built:
    op = _choice("operation", operation, tuple(_OPERATIONS))
    end = _choice("end", end, _ENDS)
    if not isinstance(flip, bool):
        raise FeatureSpecError("flip: must be true or false")
    sk = resolve(fl, sketch, "sketch", {"newSketch"})
    values: list[Value] = []
    if end == "through_all":
        if depth is not None:
            raise FeatureSpecError("depth: not allowed with end='through_all' (the extrude "
                                   "goes through everything); omit depth")
    else:
        if depth is None:
            raise FeatureSpecError(f"depth: required for end={end!r}")
        values.append(length(depth, "depth"))
    params = [
        p_query("entities", [f'qSketchRegion(makeId("{sk.id}"), true)']),
        p_enum("bodyType", "ExtendedToolBodyType", "SOLID"),
        p_enum("operationType", "NewBodyOperationType", _OPERATIONS[op]),
        p_enum("endBound", "BoundingType", "THROUGH_ALL" if end == "through_all" else "BLIND"),
    ]
    if values:
        params.append(p_qty("depth", values[0]))
    params.append(p_bool("oppositeDirection", flip))
    if end == "symmetric":
        params.append(p_bool("symmetric", True))
    if op != "new":  # merge with all bodies; the spec default (False) leaves booleanScope empty
        params.append(p_bool("defaultScope", True))
    summary = {"sketch": clean(sketch, 100), "depth": values[0].expr if values else None,
               "operation": op, "end": end}
    return Built(feature("extrude", "", params), values, summary)
