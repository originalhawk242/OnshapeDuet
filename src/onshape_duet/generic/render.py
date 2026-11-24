"""Plain-text rendering of feature specs and of existing features' raw parameters.
Every document- or Onshape-supplied string passes through `clean`."""
from __future__ import annotations

from ..model import Feature
from ..textsafe import clean
from .specs import FeatureSpec, ParamSpec

_MAX = 200


def render_spec_index(specs: list[FeatureSpec]) -> str:
    return "\n".join(f"{clean(s.type, 60)}: {clean(s.name, 60)}" for s in specs)


def _spec_line(p: ParamSpec, indent: str) -> list[str]:
    line = f"{indent}{clean(p.id, 60)} ({clean(p.name, 60)}) {clean(p.kind, 30)}"
    if p.kind == "quantity" and p.units:
        line += f" {clean(p.units, 20)}"
    if p.kind == "enum" and p.options:
        line += " " + "|".join(clean(o, 40) for o in p.options)
    if p.default is not None:
        line += f" default={clean(p.default, 60)}"
    if p.conditional:
        line += f" only when {clean(p.condition, 80)}"
    lines = [line]
    for item in p.items:
        lines.extend(_spec_line(item, indent + "  "))
    return lines


def render_spec(spec: FeatureSpec) -> str:
    head = f"{clean(spec.type, 60)}: {clean(spec.name, 60)}"
    return "\n".join([head, *(l for p in spec.params for l in _spec_line(p, ""))])


def _query(p: dict) -> str:
    queries = [q for q in p.get("queries") or [] if isinstance(q, dict)]
    strings = [clean(q["queryString"], _MAX) for q in queries
               if isinstance(q.get("queryString"), str) and q["queryString"].strip()]
    ids = sum(len(q["deterministicIds"]) for q in queries
              if not q.get("queryString") and isinstance(q.get("deterministicIds"), list))
    parts = strings + ([f"{ids} geometry ids (set in Onshape)"] if ids or not strings else [])
    return "; ".join(parts)


def _array(p: dict) -> str:
    out = []
    for i, item in enumerate(p.get("items") or []):
        subs = item.get("parameters") if isinstance(item, dict) else None
        body = ", ".join(f"{clean(s.get('parameterId', '?'), 60)}={_flat(param_value(s))}"
                         for s in subs or [] if isinstance(s, dict))
        out.append(f"  [{i}] {body}")
    return "\n".join(out)


def _flat(text: str) -> str:
    return " ".join(text.split())


def param_value(p: dict) -> str:
    """One raw BTM parameter as readable text (arrays: one `  [i] ...` line per item)."""
    if not isinstance(p, dict):
        return "<?>"
    bt = p.get("btType")
    bt = bt if isinstance(bt, str) else ""
    if bt.startswith("BTMParameterQuantity"):
        v = p.get("expression")
        return clean(v if isinstance(v, str) else p.get("value", ""), _MAX)
    if bt.startswith(("BTMParameterEnum", "BTMParameterString", "BTMParameterBoolean")):
        v = p.get("value")
        return "true" if v is True else "false" if v is False else clean(v if v is not None else "", _MAX)
    if bt.startswith("BTMParameterQuery"):
        return _query(p)
    if bt.startswith("BTMParameterFeatureList"):
        ids = p.get("featureIds")
        return ", ".join(clean(i, 64) for i in ids) if isinstance(ids, list) else ""
    if bt.startswith("BTMParameterArray"):
        return _array(p)
    return f"<{clean(bt or '?', 60)}>"


def render_feature(f: Feature, spec: FeatureSpec | None) -> str:
    lines = [f"{clean(f.name)} [{clean(f.type, 60)}] {clean(f.status, 40)} {clean(f.id, 64)}"]
    params = f.raw.get("parameters") if isinstance(f.raw, dict) else None
    for p in params if isinstance(params, list) else []:
        if not isinstance(p, dict):
            continue
        pid = clean(p.get("parameterId", "?"), 60)
        ps = spec.param(p["parameterId"]) if spec and isinstance(p.get("parameterId"), str) else None
        label = f"{pid} ({clean(ps.name, 60)})" if ps and ps.name else pid
        value = param_value(p)
        if "\n" in value:
            lines.append(f"  {label}:")
            lines.extend(f"  {l}" for l in value.splitlines())
        else:
            lines.append(f"  {label}: {value}")
    return "\n".join(lines)
