"""Merge newly built parameters into a feature's raw JSON, changing nothing else."""
from __future__ import annotations

import copy

from ..textsafe import clean
from .render import param_value


def _items(p: dict | None) -> list[list[dict]]:
    out = []
    for item in (p.get("items") if isinstance(p, dict) else None) or []:
        subs = item.get("parameters") if isinstance(item, dict) else None
        out.append([s for s in subs or [] if isinstance(s, dict)])
    return out


def _array_changes(pid: str, old: dict | None, new: dict) -> list[dict]:
    before, after = _items(old), _items(new)
    changes = []
    for i in range(max(len(before), len(after))):
        old_by = {s.get("parameterId"): s for s in (before[i] if i < len(before) else [])}
        new_by = {s.get("parameterId"): s for s in (after[i] if i < len(after) else [])}
        for sid in [*new_by, *(k for k in old_by if k not in new_by)]:
            b = param_value(old_by[sid]) if sid in old_by else None
            a = param_value(new_by[sid]) if sid in new_by else None
            if b != a:
                changes.append({"parameterId": f"{clean(pid, 60)}[{i}].{clean(sid, 60)}",
                                "before": b, "after": a})
    return changes


def merge(raw: dict, built: list[dict]) -> tuple[dict, list[dict]]:
    """(new_raw, changes): a deep copy of raw in which each built parameter replaces
    the one with the same parameterId (same position), or is appended if new."""
    new_raw = copy.deepcopy(raw)
    params = new_raw.setdefault("parameters", [])
    changes: list[dict] = []
    for b in built:
        pid = b.get("parameterId")
        pos = next((i for i, p in enumerate(params)
                    if isinstance(p, dict) and p.get("parameterId") == pid), None)
        old = params[pos] if pos is not None else None
        if isinstance(b.get("items"), list) or str(b.get("btType", "")).startswith(
                "BTMParameterArray"):
            changes.extend(_array_changes(pid, old, b))
        else:
            changes.append({"parameterId": clean(pid, 60),
                            "before": param_value(old) if old is not None else None,
                            "after": param_value(b)})
        if pos is None:
            params.append(copy.deepcopy(b))
        else:
            params[pos] = copy.deepcopy(b)
    return new_raw, changes
