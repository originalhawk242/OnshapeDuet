"""Warnings for generic feature writes. Nothing here refuses a write."""
from __future__ import annotations

import re
from typing import Iterator, Literal

from ..lint import Finding
from ..model import FeatureList
from ..textsafe import clean
from ..variables import variables_from_features
from .specs import FeatureSpec, ParamSpec

_VAR_REF = re.compile(r"#([A-Za-z_][A-Za-z0-9_]*)")


def _item_dicts(value: object) -> Iterator[dict]:
    """Item mappings of an array value, in list form or {'items': {idx: {...}}} patch form."""
    if isinstance(value, list):
        yield from (v for v in value if isinstance(v, dict))
    elif isinstance(value, dict) and isinstance(value.get("items"), dict):
        yield from (v for v in value["items"].values() if isinstance(v, dict))


def _quantities(specs: dict[str, ParamSpec], params: dict) -> Iterator[tuple[str, str]]:
    """(param id, expression) for every quantity value given, including inside array items."""
    for pid, value in params.items():
        ps = specs.get(pid) if isinstance(pid, str) else None
        if ps is None:
            continue
        if ps.kind == "quantity":
            if isinstance(value, bool):
                continue
            if isinstance(value, (str, int, float)):
                yield pid, str(value)
        elif ps.kind == "array":
            sub = {s.id: s for s in ps.items}
            for item in _item_dicts(value):
                yield from _quantities(sub, item)


def lint_generic(feature_type: str, name: str | None, params: dict, spec: FeatureSpec,
                 fl: FeatureList, *, mode: Literal["add", "update"],
                 defaults: dict | None = None) -> list[Finding]:
    """`defaults`: the add's defaults_applied, so a controlling parameter that will be sent
    at its default still counts for HIDDEN_PARAM."""
    from .validate import TYPED_TOOL_AVAILABLE  # late: validate imports nothing from here
    out: list[Finding] = []
    if mode == "add" and isinstance(name, str) \
            and re.fullmatch(re.escape(spec.name) + r" \d+", name):
        out.append(Finding("warn", "DEFAULT_NAME",
                           f"{clean(name)!r} is Onshape's default name; give the feature a "
                           "descriptive name"))
    if not isinstance(params, dict):
        return out
    quantities = list(_quantities({p.id: p for p in spec.params}, params))

    counts: dict[str, list[str]] = {}
    shown: dict[str, str] = {}
    for pid, expr in quantities:
        if "#" in expr:
            continue
        key = "".join(expr.split()).lower()
        counts.setdefault(key, []).append(pid)
        shown.setdefault(key, expr)
    for key, pids in counts.items():
        if len(pids) >= 2:
            out.append(Finding("warn", "MAGIC_NUMBER",
                               f"the literal {clean(shown[key])!r} is used in {len(pids)} "
                               f"parameters ({', '.join(clean(p) for p in pids)}); consider a "
                               "variable (cad_variable) so they change together"))

    known = {v.name for v in variables_from_features(fl)}
    seen: set[str] = set()
    for _, expr in quantities:
        for var in _VAR_REF.findall(expr):
            if var not in known and var not in seen:
                seen.add(var)
                out.append(Finding("warn", "UNKNOWN_VARIABLE",
                                   f"#{clean(var)} is not a variable in this document"))

    for pid in params:
        ps = spec.param(pid) if isinstance(pid, str) else None
        if ps is None or not ps.conditional:
            continue
        m = re.fullmatch(r"(\S+) == (.*)", ps.condition)
        ctrl = m.group(1) if m else None
        from_default = ctrl is not None and ctrl not in params and ctrl in (defaults or {})
        if ctrl is None or (ctrl not in params and not from_default):
            continue
        actual = params[ctrl] if ctrl in params else (defaults or {})[ctrl]
        if str(actual).lower() != m.group(2).lower():
            how = (f"{clean(ctrl)} is sent at its default {clean(actual)} because this call "
                   f"does not set it; set {clean(ctrl)} for {clean(pid)} to apply"
                   if from_default else f"this call sets {clean(ctrl)} to {clean(actual)}")
            out.append(Finding("warn", "HIDDEN_PARAM",
                               f"{clean(pid)} is only shown when {clean(ps.condition)}, but {how}; "
                               "it may have no effect (condition not evaluated exactly)"))

    tool = TYPED_TOOL_AVAILABLE.get(feature_type)
    if mode == "add" and tool:
        out.append(Finding("warn", "TYPED_TOOL_AVAILABLE",
                           f"{tool} covers {clean(feature_type)} with a simpler, safer interface"))
    return out
