"""Typed-feature tools (read-only ones so far)."""
from __future__ import annotations

from collections.abc import Callable

from .backends.base import PostWriteError, StaleVersion
from .docref import DocRef
from .features.builders import Built, build_extrude, build_plane, build_revolve
from .features.find import find_script, parse_find_result
from .features.params import FeatureSpecError
from .features.refs import compile_selector, selector_entity
from .lint import lint_feature
from .model import FeatureList
from .textsafe import clean
from .tools import (_FAILURES, _NO_READ, _apply_feature, _ledger_numbers, _locked, _refusal,
                    _reply, _spent, _start, Context)

_MAX_NAME = 100


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


@_locked
def run_feature(ctx: Context, ref: DocRef, name: str, build: Callable[[FeatureList], Built],
                apply: bool, what: str) -> dict:
    """Shared dry-run/apply flow for typed feature tools (builds, lints, writes)."""
    start = _start(ctx)
    if (not isinstance(name, str) or not name.strip() or len(name) > _MAX_NAME
            or not name.isprintable()):
        return _reply(ctx, start, False, error=(
            f"invalid name: must be a non-empty string of at most {_MAX_NAME} printable "
            "characters"))
    if apply and ref not in ctx.last_read:
        return _reply(ctx, start, False, error=ctx.reread.get(ref) or _NO_READ)
    try:
        fl, based = ctx.snapshot_for_apply(ref) if apply else ctx.snapshot(ref)
    except StaleVersion:
        return _reply(ctx, start, False, error=_refusal(ctx, ref))
    except _FAILURES as e:
        return _reply(ctx, start, False, error=clean(e, 300))
    try:
        built = build(fl)
    except (FeatureSpecError, *_FAILURES) as e:
        return _reply(ctx, start, False, error=clean(e, 300))
    feature_json = {**built.feature_json, "name": name}
    findings = lint_feature(name, built.values, fl)
    extra = {"feature_type": feature_json["featureType"], "summary": built.summary,
             "findings": [f.to_dict() for f in findings],
             "based_on_version": clean(based, 64)}
    refusals = [f for f in findings if f.level == "refuse"]
    if refusals:
        return _reply(ctx, start, False,
                      error="refused, nothing written: " + "; ".join(
                          f"{f.code}: {f.message}" for f in refusals), **extra)
    if not apply:
        return _reply(ctx, start, False, **extra)
    return _apply_feature(ctx, ref, start, feature_json, extra, what)


def cad_extrude(ctx: Context, ref: DocRef, name: str, sketch: str, depth=None,
                operation: str = "new", end: str = "blind", flip: bool = False,
                apply: bool = False) -> dict:
    return run_feature(
        ctx, ref, name,
        lambda fl: build_extrude(fl, sketch=sketch, depth=depth, operation=operation,
                                 end=end, flip=flip),
        apply, "the extrude")


def cad_plane(ctx: Context, ref: DocRef, name: str, base, offset, flip: bool = False,
              apply: bool = False) -> dict:
    return run_feature(
        ctx, ref, name,
        lambda fl: build_plane(fl, base=base, offset=offset, flip=flip),
        apply, "the plane")


def cad_revolve(ctx: Context, ref: DocRef, name: str, sketch: str, axis: str, angle=360,
                operation: str = "new", symmetric: bool = False, flip: bool = False,
                apply: bool = False) -> dict:
    return run_feature(
        ctx, ref, name,
        lambda fl: build_revolve(fl, sketch=sketch, axis=axis, angle=angle, operation=operation,
                                 symmetric=symmetric, flip=flip),
        apply, "the revolve")
