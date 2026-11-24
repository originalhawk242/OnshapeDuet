"""Parsed Onshape feature specs (the /featurespecs response): what parameters each
native feature type takes. Field names come from Onshape's OpenAPI schema and are
not yet verified against a live response."""
from __future__ import annotations

from dataclasses import dataclass

from ..model import flatten_params

KIND_BY_BTTYPE: dict[str, str] = {
    "BTParameterSpecQuantity-173": "quantity",
    "BTParameterSpecEnum-171": "enum",
    "BTParameterSpecBoolean-170": "boolean",
    "BTParameterSpecString-175": "string",
    "BTParameterSpecQuery-174": "query",
    "BTParameterSpecFeatureList-703": "feature_list",
    "BTParameterSpecArray-2600": "array",
}
_ALWAYS_SHOWN = "BTParameterVisibilityAlwaysShown-5487"
_ON_EQUAL = "BTParameterVisibilityOnEqual-180"


@dataclass(frozen=True)
class ParamSpec:
    id: str
    name: str
    kind: str
    quantity_type: str = ""
    units: str = ""
    default: object = None
    options: tuple[str, ...] = ()
    enum_name: str = ""
    enum_namespace: str = ""
    items: tuple["ParamSpec", ...] = ()
    conditional: bool = False
    condition: str = ""


@dataclass(frozen=True)
class FeatureSpec:
    type: str
    name: str
    params: tuple[ParamSpec, ...]

    def param(self, pid: str) -> ParamSpec | None:
        return next((p for p in self.params if p.id == pid), None)


def _bad(why: str) -> RuntimeError:
    return RuntimeError(f"unexpected Onshape response: {why}")


def _str(d: dict, key: str) -> str:
    v = d.get(key)
    return v if isinstance(v, str) else ""


def _condition(vis: object) -> tuple[bool, str]:
    if not isinstance(vis, dict) or not vis or vis.get("btType") == _ALWAYS_SHOWN:
        return False, ""
    if set(vis) <= {"btType"}:  # bare BTParameterVisibilityCondition-177: always shown
        return False, ""
    if vis.get("btType") == _ON_EQUAL and isinstance(vis.get("parameterId"), str):
        return True, f"{vis['parameterId']} == {vis.get('value')}"
    return True, "conditional"


_PREFERRED_UNIT = {"LENGTH": ("millimeter", "mm"), "ANGLE": ("degree", "deg")}
_SHORT_UNIT = {"millimeter": "mm", "degree": "deg"}


def _num(v: object) -> str | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return f"{v:g}"


def _range_default(ranges: list, quantity_type: str) -> str | None:
    """A readable default from a quantity's `ranges`: the millimeter / degree entry
    when present (live specs list meter or radian first), else the first entry."""
    entries = [r for r in ranges if isinstance(r, dict) and _num(r.get("defaultValue")) is not None]
    if not entries:
        return None
    want = _PREFERRED_UNIT.get(quantity_type)
    pick = next((r for r in entries if want and r.get("units") == want[0]), entries[0])
    unit = pick.get("units")
    unit = _SHORT_UNIT.get(unit, unit) if isinstance(unit, str) else ""
    num = _num(pick["defaultValue"])
    return f"{num} {unit}".strip()


def _default(d: dict, ranges: list, kind: str, quantity_type: str) -> object:
    """A scalar default (str, bool, number) or None. Never a raw BTM dict."""
    dv = d.get("defaultValue")
    if isinstance(dv, dict):
        vals = list(flatten_params([dv]).values())
        flat = vals[0] if vals else None
    else:
        flat = None
    if kind == "quantity":
        if isinstance(flat, str) and flat:
            return flat
        return _range_default(ranges, quantity_type)
    if isinstance(flat, (str, bool, int, float)):
        return flat
    return None


def _parse_param(d: object) -> ParamSpec:
    if not isinstance(d, dict):
        raise _bad("malformed parameter spec")
    pid = d.get("parameterId")
    if not isinstance(pid, str) or not pid:
        raise _bad("parameter spec without parameterId")
    bt = d.get("btType")
    kind = KIND_BY_BTTYPE.get(bt, "unsupported") if isinstance(bt, str) else "unsupported"
    conditional, condition = _condition(d.get("visibilityCondition"))
    raw_ranges = d.get("ranges")
    ranges = raw_ranges if isinstance(raw_ranges, list) else []
    units = ""
    if kind == "quantity" and ranges and isinstance(ranges[0], dict):
        units = _str(ranges[0], "units")
    options: tuple[str, ...] = ()
    if kind == "enum" and isinstance(d.get("options"), list):
        options = tuple(o for o in d["options"] if isinstance(o, str))
    items: tuple[ParamSpec, ...] = ()
    if kind == "array":
        sub = d.get("parameters")
        if sub is not None and not isinstance(sub, list):
            raise _bad("malformed array item specs")
        items = tuple(_parse_param(s) for s in sub or [])
    return ParamSpec(
        id=pid, name=_str(d, "parameterName"), kind=kind,
        quantity_type=_str(d, "quantityType") if kind == "quantity" else "",
        units=units, default=(_default(d, ranges, kind, _str(d, "quantityType"))
                 if kind != "unsupported" else None),
        options=options,
        enum_name=_str(d, "enumName") if kind == "enum" else "",
        enum_namespace=_str(d, "namespace") if kind == "enum" else "",
        items=items, conditional=conditional, condition=condition)


def parse_feature_specs(data: dict) -> list[FeatureSpec]:
    """Native feature specs (empty/missing namespace) from a featurespecs response,
    sorted by type. Raises RuntimeError on a malformed shape."""
    raw = data.get("featureSpecs") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise _bad("featureSpecs is not a list")
    out: list[FeatureSpec] = []
    for s in raw:
        if not isinstance(s, dict):
            raise _bad("malformed feature spec")
        if s.get("namespace"):
            continue
        t = s.get("featureType")
        if not isinstance(t, str) or not t:
            raise _bad("feature spec without featureType")
        params = s.get("parameters")
        if params is not None and not isinstance(params, list):
            raise _bad("malformed feature spec parameters")
        out.append(FeatureSpec(t, _str(s, "featureTypeName"),
                               tuple(_parse_param(p) for p in params or [])))
    return sorted(out, key=lambda f: f.type)
