"""Argument values and Onshape feature-parameter JSON makers."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

from onshape_duet.sketch.serialize import _num
from onshape_duet.sketch.spec import _NUM, _UNIT_MM
from onshape_duet.textsafe import clean


class FeatureSpecError(ValueError):
    pass


@dataclass(frozen=True)
class Value:
    kind: Literal["length", "angle", "count"]
    expr: str                  # Onshape expression: "10 mm", "45 deg", "#w", "4"
    literal: float | None      # mm / deg / count for literals; None for "#var"
    var: str | None            # variable name for "#var", else None
    arg: str                   # argument name, for messages


_LEN_RE = re.compile(rf"^\s*({_NUM})\s*(mm|cm|m|in)?\s*$")
_ANGLE_RE = re.compile(rf"^\s*({_NUM})\s*(deg)?\s*$")
_VAR_RE = re.compile(r"^#([a-z][a-z0-9_]*)$")
MAX_COUNT = 10000
_INT_RE = re.compile(r"^\s*\+?\d+\s*$")


def _show(x) -> str:
    """Render a caller value for a message; never stringify an oversized int."""
    if isinstance(x, int) and not isinstance(x, bool) and abs(x) > 10**12:
        return "a very large number"
    return repr(clean(x))


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _var(x, arg: str, kind: str):
    m = _VAR_RE.match(x.strip())
    if not m:
        raise FeatureSpecError(f"{clean(arg)}: invalid variable reference {_show(x)} (use #name, lowercase letters/digits/_)")
    if kind != "length":
        raise FeatureSpecError(f"{clean(arg)}: variable references are not supported for {kind} arguments yet")
    return m.group(1)


def length(x, arg: str, *, positive: bool = True) -> Value:
    if isinstance(x, str) and x.strip().startswith("#"):
        name = _var(x, arg, "length")
        return Value("length", f"#{name}", None, name, arg)
    if _is_number(x):
        try:
            mm = float(x)
        except OverflowError:
            raise FeatureSpecError(f"{clean(arg)}: length is too large") from None
        expr = f"{_num(mm)} mm" if math.isfinite(mm) else None
    elif isinstance(x, str) and (m := _LEN_RE.match(x)):
        unit = m.group(2) or "mm"
        mm = float(m.group(1)) * _UNIT_MM[unit]
        expr = f"{_num(float(m.group(1)))} {unit}"
    else:
        raise FeatureSpecError(
            f"{clean(arg)}: expected a length like 10, '10 mm', '2 in' or '#var', got {_show(x)}")
    if not math.isfinite(mm):
        raise FeatureSpecError(f"{clean(arg)}: length must be finite, got {_show(x)}")
    if positive and (mm <= 0 or float(expr.split()[0]) == 0):
        raise FeatureSpecError(f"{clean(arg)}: length must be > 0, got {_show(x)}")
    return Value("length", expr, mm, None, arg)


def angle(x, arg: str) -> Value:
    if isinstance(x, str) and x.strip().startswith("#"):
        _var(x, arg, "angle")
    if _is_number(x):
        try:
            deg = float(x)
        except OverflowError:
            raise FeatureSpecError(f"{clean(arg)}: angle is too large") from None
    elif isinstance(x, str) and (m := _ANGLE_RE.match(x)):
        deg = float(m.group(1))
    else:
        raise FeatureSpecError(f"{clean(arg)}: expected an angle like 45 or '45 deg', got {_show(x)}")
    if not math.isfinite(deg) or not 0 < deg <= 360:
        raise FeatureSpecError(f"{clean(arg)}: angle must satisfy 0 < deg <= 360, got {_show(x)}")
    if float(_num(deg)) == 0:
        raise FeatureSpecError(f"{clean(arg)}: angle must satisfy 0 < deg <= 360, got {_show(x)}")
    return Value("angle", f"{_num(deg)} deg", deg, None, arg)


def count(x, arg: str, *, minimum: int = 2) -> Value:
    if isinstance(x, str) and x.strip().startswith("#"):
        _var(x, arg, "count")
    if isinstance(x, int) and not isinstance(x, bool):
        n = x
    elif isinstance(x, str) and _INT_RE.match(x) and len(x.strip()) <= 12:
        n = int(x)
    else:
        raise FeatureSpecError(f"{clean(arg)}: expected an integer count, got {_show(x)}")
    if n < minimum or n > MAX_COUNT:
        raise FeatureSpecError(f"{clean(arg)}: count must be between {minimum} and {MAX_COUNT}, got {_show(x)}")
    return Value("count", str(n), float(n), None, arg)


def parse_length_mm(expr: str) -> float | None:
    if not isinstance(expr, str):
        return None
    m = _LEN_RE.match(expr)
    if not m:
        return None
    mm = float(m.group(1)) * _UNIT_MM[m.group(2)]
    return mm if math.isfinite(mm) else None


# ---- parameter JSON makers ----

def p_qty(pid: str, value: Value) -> dict:
    return {"btType": "BTMParameterQuantity-147", "parameterId": pid,
            "expression": value.expr, "isInteger": value.kind == "count"}


def p_enum(pid: str, enum_name: str, value: str) -> dict:
    return {"btType": "BTMParameterEnum-145", "parameterId": pid,
            "enumName": enum_name, "value": value}


def p_bool(pid: str, v: bool) -> dict:
    return {"btType": "BTMParameterBoolean-144", "parameterId": pid, "value": bool(v)}


def p_query(pid: str, exprs: list[str]) -> dict:
    return {"btType": "BTMParameterQueryList-148", "parameterId": pid,
            "queries": [{"btType": "BTMIndividualQuery-138", "queryString": f"query={e};"} for e in exprs]}


def p_ids(pid: str, ids: list[str]) -> dict:
    return {"btType": "BTMParameterQueryList-148", "parameterId": pid,
            "queries": [{"btType": "BTMIndividualQuery-138", "deterministicIds": list(ids)}]}


def feature(feature_type: str, name: str, params: list[dict]) -> dict:
    return {"btType": "BTMFeature-134", "featureType": feature_type, "name": name,
            "suppressed": False, "parameters": params}
