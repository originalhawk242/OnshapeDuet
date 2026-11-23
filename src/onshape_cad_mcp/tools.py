"""Tool implementations (plain functions; the MCP server wraps them).

Staleness model (R4): write tools pin to the microversion Claude last READ via
`Context.last_read`, never to the version seen at call time, so a human edit
between read and write is refused. The tool must not refresh `last_read`
before the write.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .backends.base import Backend, StaleVersion
from .budget import BudgetExceeded, Ledger
from .cache import DocCache
from .config import Config
from .docref import DocRef
from .lint import lint_sketch
from .model import FeatureList
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

    def read(self, ref: DocRef) -> FeatureList:
        fl = self.cache.read(ref)
        self.last_read[ref] = fl.microversion
        return fl


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
                      error="stale: the document changed since it was last read; "
                            "re-read the document (cad_read) and retry", **extra)
    # note_write splices our feature into the cached list; that is only sound
    # because every write passes expected_microversion (nothing else changed).
    ctx.last_read[ref] = result.microversion
    ctx.cache.note_write(ref, result.microversion, result.feature)
    return _reply(ctx, start, True, **extra)
