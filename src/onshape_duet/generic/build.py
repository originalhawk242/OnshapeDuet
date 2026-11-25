"""Turn a caller value plus a ParamSpec into one Onshape BTM parameter JSON."""
from __future__ import annotations

import copy
import re

from ..features.params import p_bool, p_enum, p_query
from ..model import FeatureList
from ..textsafe import clean
from .lookup import find_feature
from .specs import ParamSpec

MAX_STR = 200
_BARE_NUMBER = re.compile(r"^\s*[-+]?\d+(\.\d+)?\s*$")
_RAW_KEYS = ("deterministicIds", "btType")


class ParamError(ValueError):
    """A parameter value that cannot be built. `.code` is one of BAD_VALUE,
    MISSING_UNIT, RAW_GEOMETRY_ID, UNSUPPORTED_PARAM_KIND; `.param` the parameter id."""

    def __init__(self, code: str, message: str, param: str):
        super().__init__(message)
        self.code = code
        self.param = param


def _bad(spec: ParamSpec, why: str, code: str = "BAD_VALUE") -> ParamError:
    return ParamError(code, f"{clean(spec.id)}: {why}", spec.id)


def _has_key(x: object, keys: tuple[str, ...]) -> bool:
    if isinstance(x, dict):
        return any(k in x for k in keys) or any(_has_key(v, keys) for v in x.values())
    if isinstance(x, list):
        return any(_has_key(v, keys) for v in x)
    return False


def _text(spec: ParamSpec, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise _bad(spec, f"expected a non-empty string, got {clean(repr(value))}")
    if len(value) > MAX_STR or not value.isprintable():
        raise _bad(spec, f"must be printable text of at most {MAX_STR} characters")
    return value


def _quantity(spec: ParamSpec, value: object) -> dict:
    if isinstance(value, (int, float)) and not isinstance(value, bool) \
            and spec.quantity_type in ("INTEGER", "REAL"):
        value = str(value)
    expr = _text(spec, value)
    if spec.units and _BARE_NUMBER.match(expr):
        eg = "deg" if spec.quantity_type == "ANGLE" else "mm"
        raise _bad(spec, f"{clean(expr)!r} needs a unit ({clean(spec.units)}), e.g. '{expr.strip()} {eg}'",
                   "MISSING_UNIT")
    return {"btType": "BTMParameterQuantity-147", "parameterId": spec.id, "expression": expr,
            "isInteger": spec.quantity_type == "INTEGER"}


def _enum(spec: ParamSpec, value: object) -> dict:
    if not isinstance(value, str) or value not in spec.options:
        hint = ""
        if isinstance(value, str) and value.upper() in spec.options:
            hint = f"; did you mean {value.upper()}?"
        opts = ", ".join(clean(o) for o in spec.options)
        raise _bad(spec, f"{clean(repr(value))} is not one of: {opts}{hint}")
    return p_enum(spec.id, spec.enum_name, value)


def _query(spec: ParamSpec, value: object, fl: FeatureList) -> dict:
    if _has_key(value, _RAW_KEYS):
        raise _bad(spec, "raw geometry ids / btType are not accepted; describe the geometry with a "
                   "selector (entity, created_by, ...)", "RAW_GEOMETRY_ID")
    try:
        from ..features.refs import compile_selector
    except ImportError:
        raise _bad(spec, "selectors not available yet", "UNSUPPORTED_PARAM_KIND") from None
    try:
        return p_query(spec.id, [compile_selector(value, fl, spec.id)])
    except ValueError as e:
        raise ParamError("BAD_VALUE", str(e), spec.id) from None


def _feature_list(spec: ParamSpec, value: object, fl: FeatureList) -> dict:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _bad(spec, "expected a list of feature names or ids")
    try:
        ids = [find_feature(fl, v).id for v in value]
    except ValueError as e:
        raise ParamError("BAD_VALUE", f"{clean(spec.id)}: {e}", spec.id) from None
    return {"btType": "BTMParameterFeatureList-1749", "parameterId": spec.id, "featureIds": ids}


_SCALAR_KINDS = ("quantity", "enum", "boolean", "string")


def default_param(spec: ParamSpec) -> dict | None:
    """The spec's own default as a BTM parameter, or None if it cannot be built. Onshape does
    not fill omitted parameters (live G3), so a new feature or array item sends these
    explicitly: scalars and lookup tables at the spec default; selections, feature lists and
    arrays empty (nothing is picked for the caller)."""
    if spec.lookup_default:
        return {"btType": "BTMParameterLookupTablePath-1419", "parameterId": spec.id,
                "namespace": "", "value": dict(spec.lookup_default)}
    if spec.kind == "query":  # an empty selection, as a new feature in Onshape starts
        return {"btType": "BTMParameterQueryList-148", "parameterId": spec.id, "queries": []}
    if spec.kind == "feature_list":
        return {"btType": "BTMParameterFeatureList-1749", "parameterId": spec.id, "featureIds": []}
    if spec.kind == "array":
        return {"btType": "BTMParameterArray-2025", "parameterId": spec.id, "items": []}
    if spec.kind == "string" and spec.default == "":  # e.g. hole featureName
        return {"btType": "BTMParameterString-149", "parameterId": spec.id, "value": ""}
    if spec.kind not in _SCALAR_KINDS or spec.default is None:
        return None
    try:
        return build_param(spec, spec.default, FeatureList("", []))
    except ParamError:
        return None


def shown_default(spec: ParamSpec) -> object:
    """What `defaults_applied` shows for a default sent by `default_param` (cleaned)."""
    if spec.lookup_default:
        return {clean(k, 60): clean(v, MAX_STR) for k, v in spec.lookup_default}
    if spec.kind in ("query", "feature_list", "array"):
        return []
    return clean(spec.default, MAX_STR) if isinstance(spec.default, str) else spec.default


def _item_params(spec: ParamSpec, item: object, fl: FeatureList,
                 existing: list | None = None, *, fill_defaults: bool = False) -> list[dict]:
    if not isinstance(item, dict):
        raise _bad(spec, "each item must be a mapping of parameter id to value")
    subs = {s.id: s for s in spec.items}
    out = []
    for k, v in item.items():
        if k not in subs:
            raise _bad(spec, f"unknown item key {clean(k)!r}; allowed: "
                       f"{', '.join(clean(s) for s in subs) or '(none)'}")
        cur = next((p for p in existing or [] if isinstance(p, dict) and p.get("parameterId") == k), None)
        out.append(build_param(subs[k], v, fl, current=cur))
    if fill_defaults:
        out.extend(d for s in spec.items if s.id not in item and (d := default_param(s)))
    return out


def _array(spec: ParamSpec, value: object, fl: FeatureList, current: dict | None) -> dict:
    if isinstance(value, list):
        if current is not None and _has_key(current, ("deterministicIds",)):
            raise _bad(spec, "the existing items hold geometry selections that a full list would "
                       "discard; use the items patch form {'items': {'<index>': {...}}}",
                       "RAW_GEOMETRY_ID")
        items = [{"btType": "BTMArrayParameterItem-1843",
                  "parameters": _item_params(spec, it, fl, fill_defaults=True)}
                 for it in value]
        return {"btType": "BTMParameterArray-2025", "parameterId": spec.id, "items": items}
    if not (isinstance(value, dict) and set(value) == {"items"} and isinstance(value["items"], dict)):
        raise _bad(spec, "expected a list of item mappings, or {'items': {'<index>': {...}}} to patch")
    cur_items = current.get("items") if isinstance(current, dict) else None
    if not isinstance(cur_items, list):
        raise _bad(spec, "the items patch form only works on a parameter that already has items")
    out = copy.deepcopy(current)
    for key, patch in value["items"].items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            raise _bad(spec, f"item index {clean(repr(key))} is not an integer") from None
        if not 0 <= idx < len(cur_items):
            raise _bad(spec, f"item index {idx} out of range (0..{len(cur_items) - 1})")
        params = out["items"][idx].setdefault("parameters", [])
        for new in _item_params(spec, patch, fl, params):
            for i, old in enumerate(params):
                if isinstance(old, dict) and old.get("parameterId") == new["parameterId"]:
                    params[i] = new
                    break
            else:
                params.append(new)
    out["parameterId"] = spec.id
    return out


def build_param(spec: ParamSpec, value: object, fl: FeatureList, *,
                current: dict | None = None) -> dict:
    """One BTM parameter JSON for `value`. `current` is the feature's existing raw
    parameter with this id (updates only). Raises ParamError and nothing else."""
    kind = spec.kind
    if kind == "quantity":
        return _quantity(spec, value)
    if kind == "enum":
        return _enum(spec, value)
    if kind == "boolean":
        if not isinstance(value, bool):
            raise _bad(spec, f"expected true or false, got {clean(repr(value))}")
        return p_bool(spec.id, value)
    if kind == "string":
        return {"btType": "BTMParameterString-149", "parameterId": spec.id, "value": _text(spec, value)}
    if kind == "query":
        return _query(spec, value, fl)
    if kind == "feature_list":
        return _feature_list(spec, value, fl)
    if kind == "array":
        return _array(spec, value, fl, current)
    raise _bad(spec, f"parameter kind {clean(kind)!r} is not supported", "UNSUPPORTED_PARAM_KIND")
