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

from .backends.base import Backend, StaleVersion
from .budget import BudgetExceeded, Ledger
from .cache import DocCache
from .config import Config
from .docref import DocRef
from .lint import lint_sketch
from pathlib import Path

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


def _reply(ctx: Context, start_used: int, applied: bool, **extra) -> dict:
    return {"applied": applied, **extra,
            "requests": ctx.ledger.used - start_used, "budget_left": ctx.ledger.remaining}


def cad_sketch(ctx: Context, ref: DocRef, name: str, spec: dict, apply: bool = False) -> dict:
    start = ctx.ledger.used
    if apply and ref not in ctx.last_read:
        return _reply(ctx, start, False, error="read the document first (cad_read) before applying")
    try:
        variables = evaluate_variables(ctx.backend.get_variables(ref))
        sketch = parse_sketch_spec(spec, variables)
        report = analyze(sketch)
        feature_json = to_feature_json(sketch, name)
    except (SketchSpecError, BudgetExceeded) as e:
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
    except BudgetExceeded as e:
        return _reply(ctx, start, False, error=clean(e, 300), **extra)
    except StaleVersion:
        return _reply(ctx, start, False,
                      error=_STALE, **extra)
    # note_write splices our feature into the cached list; that is only sound
    # because every write passes expected_microversion (nothing else changed).
    ctx.last_read[ref] = result.microversion
    ctx.cache.note_write(ref, result.microversion, result.feature)
    return _reply(ctx, start, True, **extra)


def cad_variable(ctx: Context, ref: DocRef, name: str, expression: str, apply: bool = False) -> dict:
    start = ctx.ledger.used
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
    except BudgetExceeded as e:
        return _reply(ctx, start, False, error=clean(e, 300))
    old = existing.get(name)
    extra = {"name": name, "old": None if old is None else clean(old, 200),
             "new": expression, "findings": []}
    if not apply:
        return _reply(ctx, start, False, **extra)

    try:
        new_mv = ctx.backend.set_variable(ref, name, expression,
                                          expected_microversion=ctx.last_read[ref])
    except BudgetExceeded as e:
        return _reply(ctx, start, False, error=clean(e, 300), **extra)
    except StaleVersion:
        return _reply(ctx, start, False, error=_STALE, **extra)
    ctx.last_read[ref] = new_mv
    ctx.cache.note_write(ref, new_mv)  # no feature: cache drops the entry (intended)
    return _reply(ctx, start, True, **extra)


def cad_status(ctx: Context) -> dict:
    """Budget and credential status. Makes zero Onshape requests."""
    cfg = ctx.config
    return {
        "budget": ctx.ledger.limit,
        "used": ctx.ledger.used,
        "remaining": ctx.ledger.remaining,
        "credentials_present": bool(os.environ.get(cfg.key_env) and os.environ.get(cfg.secret_env)),
        "credential_env_vars": [cfg.key_env, cfg.secret_env],
        "config_path": str(ctx.config_path) if ctx.config_path else None,
    }


def cad_read(ctx: Context, ref: DocRef) -> str:
    """Outline of the Part Studio (features + variables). Every sketch is
    `not assessed` (R6: reading existing sketches into DOF reports is out of scope)."""
    start = ctx.ledger.used
    try:
        fl = ctx.read(ref)
        variables = ctx.backend.get_variables(ref)
        body = render_outline(fl, variables)
    except (BudgetExceeded, RuntimeError) as e:
        body = "error: " + clean(e, 300)
    return (f"{body}\nrequests used: {ctx.ledger.used - start}, "
            f"budget left: {ctx.ledger.remaining}")
