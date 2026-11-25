"""Read-only selection preview: the FeatureScript template and its reply parser.

Verified live (a0a2432): the endpoint, request body, reply shape and this script
body (tests/fixtures/probes.json, fs_eval and find_eval).

The expression spliced in is a compiled selector (refs.compile_selector): only
enum-mapped text and validated feature ids, never caller text. The script
returns strings only (count, then centroids in mm), never transient ids.
"""
from __future__ import annotations

from onshape_duet.textsafe import clean

MAX_CENTROIDS = 20


def find_script(expr: str, limit: int = MAX_CENTROIDS) -> str:
    """An anonymous function, as in the live-verified probe (std is in scope)."""
    n = max(0, min(int(limit), MAX_CENTROIDS))
    return (
        'function(context is Context, queries is map)\n'
        '{\n'
        f'    var all = evaluateQuery(context, {expr});\n'
        '    var out = ["" ~ size(all)];\n'
        f'    for (var i = 0; i < size(all) && i < {n}; i += 1)\n'
        '    {\n'
        '        out = append(out, toString(evApproximateCentroid(context, '
        '{"entities" : all[i]}) / millimeter));\n'
        '    }\n'
        '    return out;\n'
        '}\n'
    )


def _bad(why: str) -> RuntimeError:
    return RuntimeError(f"unexpected Onshape response: {why}")


def parse_find_result(result: dict) -> tuple[int, list[str]]:
    if not isinstance(result, dict) or not str(result.get("btType", "")).endswith("BTFSValueArray"):
        raise _bad("find result is not an array")
    items = result.get("value")
    if not isinstance(items, list) or not items:
        raise _bad("find result array is empty or malformed")
    vals: list[str] = []
    for it in items:
        if (not isinstance(it, dict) or not str(it.get("btType", "")).endswith("BTFSValueString")
                or not isinstance(it.get("value"), str)):
            raise _bad("find result element is not a string")
        vals.append(it["value"])
    head = vals[0].strip()
    if not head.isascii() or not head.isdigit():
        raise _bad("find result count is not a non-negative integer")
    return int(head), [clean(v) for v in vals[1:1 + MAX_CENTROIDS]]
