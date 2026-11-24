"""Typed-feature tools (read-only ones so far)."""
from __future__ import annotations

from .backends.base import PostWriteError
from .docref import DocRef
from .features.find import find_script, parse_find_result
from .features.params import FeatureSpecError
from .features.refs import compile_selector, selector_entity
from .textsafe import clean
from .tools import _FAILURES, _ledger_numbers, _locked, _spent, _start, Context


def _out(ctx: Context, start, **fields) -> dict:
    return {**fields, "requests": _spent(ctx, start), "budget_left": _ledger_numbers(ctx)[1]}


@_locked
def cad_find(ctx: Context, ref: DocRef, selector, evaluate: bool = False) -> dict:
    """Preview a selector. Dry: compiled query only (0 requests once the document
    was read). evaluate=True: one read-only FeatureScript evaluation."""
    start = _start(ctx)
    try:
        fl, based = ctx.snapshot(ref)
    except _FAILURES as e:
        return _out(ctx, start, error=clean(e, 300))
    try:
        entity = selector_entity(selector)
        query = compile_selector(selector, fl, "selector")
    except FeatureSpecError as e:
        return _out(ctx, start, error=f"invalid selector: {clean(e, 300)}")
    fields = {"query": query, "entity": entity, "based_on_version": clean(based, 64)}
    if evaluate:
        try:
            count, centroids = parse_find_result(
                ctx.backend.eval_featurescript(ref, find_script(query)))
        except (*_FAILURES, PostWriteError) as e:
            return _out(ctx, start, error=clean(e, 300), **fields)
        fields.update(count=count, centroids_mm=centroids)
    return _out(ctx, start, **fields)
