"""Tool implementations (plain functions; the MCP server wraps them).

Staleness model (R4): write tools pin to the microversion Claude last READ via
`Context.last_read`, never to the version seen at call time, so a human edit
between read and write is refused. The tool must not refresh `last_read`
before the write.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .backends.base import Backend, PostWriteError, StaleVersion
from .budget import BudgetExceeded, Ledger
from .cache import DocCache
from .config import Config
from .docref import DocRef
from .lint import lint_sketch
from .model import FeatureList
from .outline import render_outline
from .sketch.serialize import to_feature_json
from .sketch.solver import analyze
from .sketch.spec import SketchSpecError, evaluate_variables, parse_sketch_spec
from .textsafe import clean


@dataclass
class Context:
    backend: Backend
    cache: DocCache
    ledger: Ledger
    config: Config
    last_read: dict[DocRef, str] = field(default_factory=dict)
    config_path: Path | None = None

    def read(self, ref: DocRef) -> FeatureList:
        fl = self.cache.read(ref)
        self.last_read[ref] = fl.microversion
        return fl


_STALE = ("stale: the document changed since it was last read; "
          "re-read the document (cad_read) and retry")
_VAR_NAME = re.compile(r"[a-z][a-z0-9_]*")
_MAX_EXPR = 200


_UNAVAILABLE = "unavailable"
_FAILURES = (BudgetExceeded, RuntimeError, httpx.HTTPError)


def _ledger_numbers(ctx: Context) -> tuple[object, object]:
    """(used, remaining), or "unavailable" for both if the ledger is unusable."""
    try:
        return ctx.ledger.used, ctx.ledger.remaining
    except BudgetExceeded:
        return _UNAVAILABLE, _UNAVAILABLE


def _start(ctx: Context):
    return _ledger_numbers(ctx)[0]


def _spent(ctx: Context, start) -> object:
    used = _ledger_numbers(ctx)[0]
    if start == _UNAVAILABLE or used == _UNAVAILABLE:
        return _UNAVAILABLE
    return used - start


def _unknown(ctx: Context, start, ref: DocRef, detail: str, **extra) -> dict:
    """The write was sent but its outcome is unknown: drop the cache entry and do
    NOT advance last_read, so a retry cannot silently duplicate the write."""
    ctx.cache.invalidate(ref)
    return _reply(ctx, start, "unknown",
                  error=f"{clean(detail, 300)}: the write may have landed; "
                        "re-read with cad_read before retrying", **extra)


def _reply(ctx: Context, start_used, applied: bool | str, **extra) -> dict:
    return {"applied": applied, **extra,
            "requests": _spent(ctx, start_used), "budget_left": _ledger_numbers(ctx)[1]}


def cad_sketch(ctx: Context, ref: DocRef, name: str, spec: dict, apply: bool = False) -> dict:
    start = _start(ctx)
    if apply and ref not in ctx.last_read:
        return _reply(ctx, start, False, error="read the document first (cad_read) before applying")
    try:
        variables = evaluate_variables(ctx.backend.get_variables(ref))
        sketch = parse_sketch_spec(spec, variables)
        report = analyze(sketch)
        feature_json = to_feature_json(sketch, name)
    except (SketchSpecError, *_FAILURES) as e:
        return _reply(ctx, start, False, error=clean(e, 300))

    findings = lint_sketch(spec, report, name)
    extra = {"dof": report.dof, "findings": [f.to_dict() for f in findings]}
    refusals = [f for f in findings if f.level == "refuse"]
    if refusals:
        # Dry run or apply: nothing is written while a refuse finding stands.
        return _reply(ctx, start, False,
                      error="refused, nothing written: " + "; ".join(
                          f"{f.code}: {f.message}" for f in refusals), **extra)
    if not apply:
        return _reply(ctx, start, False, **extra)

    try:
        result = ctx.backend.add_feature(ref, feature_json, expected_microversion=ctx.last_read[ref])
    except PostWriteError as e:
        return _unknown(ctx, start, ref, str(e), **extra)
    except StaleVersion as e:
        if e.may_have_landed:
            return _unknown(ctx, start, ref, str(e), **extra)
        return _reply(ctx, start, False, error=_STALE, **extra)
    except _FAILURES as e:
        return _reply(ctx, start, False, error=clean(e, 300), **extra)
    # note_write splices our feature into the cached list; that is only sound
    # because every write passes expected_microversion (nothing else changed).
    ctx.last_read[ref] = result.microversion
    ctx.cache.note_write(ref, result.microversion, result.feature)
    return _reply(ctx, start, True, **extra)


def cad_variable(ctx: Context, ref: DocRef, name: str, expression: str, apply: bool = False) -> dict:
    start = _start(ctx)
    if not isinstance(name, str) or not _VAR_NAME.fullmatch(name):
        return _reply(ctx, start, False,
                      error="invalid variable name: must match ^[a-z][a-z0-9_]*$")
    if (not isinstance(expression, str) or not expression.strip()
            or len(expression) > _MAX_EXPR
            or any(not c.isprintable() for c in expression)):
        return _reply(ctx, start, False, name=name,
                      error=f"invalid expression: must be non-empty, at most {_MAX_EXPR} "
                            "characters, with no newlines or control characters")
    if apply and ref not in ctx.last_read:
        return _reply(ctx, start, False, error="read the document first (cad_read) before applying")
    try:
        existing = {v.name: v.expression for v in ctx.backend.get_variables(ref)}
    except _FAILURES as e:
        return _reply(ctx, start, False, error=clean(e, 300))
    old = existing.get(name)
    extra = {"name": name, "old": None if old is None else clean(old, 200),
             "new": expression, "findings": []}
    if not apply:
        return _reply(ctx, start, False, **extra)

    try:
        new_mv = ctx.backend.set_variable(ref, name, expression,
                                          expected_microversion=ctx.last_read[ref])
    except PostWriteError as e:
        return _unknown(ctx, start, ref, str(e), **extra)
    except StaleVersion as e:
        if e.may_have_landed:
            return _unknown(ctx, start, ref, str(e), **extra)
        return _reply(ctx, start, False, error=_STALE, **extra)
    except _FAILURES as e:
        return _reply(ctx, start, False, error=clean(e, 300), **extra)
    ctx.last_read[ref] = new_mv
    ctx.cache.note_write(ref, new_mv)  # no feature: cache drops the entry (intended)
    return _reply(ctx, start, True, **extra)


def cad_status(ctx: Context) -> dict:
    """Budget and credential status. Makes zero Onshape requests."""
    cfg = ctx.config
    out = {
        "budget": ctx.ledger.limit,
        "credentials_present": bool(os.environ.get(cfg.key_env) and os.environ.get(cfg.secret_env)),
        "credential_env_vars": [cfg.key_env, cfg.secret_env],
        "config_path": str(ctx.config_path) if ctx.config_path else None,
    }
    try:
        out["used"] = ctx.ledger.used
        out["remaining"] = ctx.ledger.remaining
    except BudgetExceeded as e:
        out["used"] = out["remaining"] = _UNAVAILABLE
        out["error"] = clean(e, 300)
    return out


def cad_read(ctx: Context, ref: DocRef) -> str:
    """Outline of the Part Studio (features + variables). Every sketch is
    `not assessed` (R6: reading existing sketches into DOF reports is out of scope)."""
    start = _start(ctx)
    try:
        fl = ctx.read(ref)
        variables = ctx.backend.get_variables(ref)
        body = render_outline(fl, variables)
    except (*_FAILURES, ValueError) as e:
        body = f"error: {clean(e, 300)}"
    used, left = _ledger_numbers(ctx)
    n = _spent(ctx, start)
    if n == _UNAVAILABLE:
        return f"{body}\nrequests used: unavailable (ledger unreadable), budget left: unavailable"
    return f"{body}\nrequests used: {n}, budget left: {left}"
