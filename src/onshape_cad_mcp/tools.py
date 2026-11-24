"""Tool implementations (plain functions; the MCP server wraps them).

Staleness model (R4): write tools pin to the microversion Claude last READ via
`Context.last_read`, never to the version seen at call time, so a human edit
between read and write is refused. The tool must not refresh `last_read`
before the write.
"""
from __future__ import annotations

import os
import functools
import re
import threading
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
from .sketch.spec import (SketchSpecError, evaluate_variables, parse_sketch_spec,
                          referenced_variables)
from .textsafe import clean
from .variables import find_variable, make_variable_feature, variables_from_features


@dataclass
class Context:
    backend: Backend
    cache: DocCache
    ledger: Ledger
    config: Config
    last_read: dict[DocRef, str] = field(default_factory=dict)
    config_path: Path | None = None
    # MCP runs sync tools in worker threads; one RLock serialises every tool body
    # (last_read, DocCache and the ledger-delta request counts are shared state).
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def read(self, ref: DocRef) -> FeatureList:
        fl = self.cache.read(ref)
        self.last_read[ref] = fl.microversion
        return fl

    def snapshot_for_apply(self, ref: DocRef) -> tuple[FeatureList, str]:
        """The cached feature list at exactly last_read[ref], with no fetch.

        A write is decided on, and pinned to, the same version: anything else
        (no read, entry dropped or at another version) is stale."""
        mv = self.last_read.get(ref)
        fl = self.cache.peek(ref)
        if mv is None or fl is None or fl.microversion != mv:
            raise StaleVersion("no cached copy of the version last read")
        return fl, mv

    def snapshot(self, ref: DocRef) -> tuple[FeatureList, str]:
        """(feature list, version it is based on) for dry runs and variable lookups.

        Zero requests when the cache still holds the version Claude last read;
        otherwise one cache read (2 requests at most). Unlike `read`, this does
        NOT count as Claude having read the document, so last_read is untouched.
        """
        mv = self.last_read.get(ref)
        if mv is not None:
            fl = self.cache.peek(ref)
            if fl is not None and fl.microversion == mv:
                return fl, mv
        fl = self.cache.read(ref)
        return fl, fl.microversion


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(ctx: Context, *args, **kwargs):
        with ctx.lock:
            return fn(ctx, *args, **kwargs)
    return wrapper


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


def _backstop(ctx: Context, ref: DocRef, expected: str) -> None:
    """Client-side check before every write (costs 1 request). Removable once
    rejectMicroversionSkew is verified live (docs/live-checklist.md)."""
    if ctx.backend.get_microversion(ref) != expected:
        raise StaleVersion("document version differs from the version last read")


@_locked
def cad_sketch(ctx: Context, ref: DocRef, name: str, spec: dict, apply: bool = False) -> dict:
    start = _start(ctx)
    if apply and ref not in ctx.last_read:
        return _reply(ctx, start, False, error="read the document first (cad_read) before applying")
    based_on: dict = {}
    try:
        wanted = referenced_variables(spec)
        variables: dict[str, float] = {}
        if wanted:  # no '#variable' reference: the document is not consulted at all
            fl, based = ctx.snapshot_for_apply(ref) if apply else ctx.snapshot(ref)
            based_on = {"based_on_version": clean(based, 64)}
            variables = evaluate_variables(variables_from_features(fl), only=wanted)
        sketch = parse_sketch_spec(spec, variables)
        report = analyze(sketch)
        feature_json = to_feature_json(sketch, name)
    except StaleVersion:
        return _reply(ctx, start, False, error=_STALE)
    except (SketchSpecError, *_FAILURES) as e:
        return _reply(ctx, start, False, error=clean(e, 300))

    findings = lint_sketch(spec, report, name)
    extra = {"dof": report.dof, "findings": [f.to_dict() for f in findings],
             "not_assessed": [clean(i, 60) for i in report.not_assessed], **based_on}
    refusals = [f for f in findings if f.level == "refuse"]
    if refusals:
        # Dry run or apply: nothing is written while a refuse finding stands.
        return _reply(ctx, start, False,
                      error="refused, nothing written: " + "; ".join(
                          f"{f.code}: {f.message}" for f in refusals), **extra)
    if not apply:
        return _reply(ctx, start, False, **extra)

    try:
        expected = ctx.last_read[ref]
        _backstop(ctx, ref, expected)
        result = ctx.backend.add_feature(ref, feature_json, expected_microversion=expected)
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
    ctx.cache.note_write(ref, result.microversion, result.feature,
                         expected_microversion=expected)
    status = clean(result.feature.status, 40)
    done = {**extra, "feature_id": clean(result.feature.id, 64), "status": status}
    if status != "OK":
        warning = (f"applied, but regeneration status is {status}: the sketch was written; "
                   "inspect and fix it, or delete it")
        done["warning"] = warning
        done["findings"] = [*extra["findings"],
                            {"level": "error", "code": "REGEN_STATUS", "message": warning}]
    return _reply(ctx, start, True, **done)


@_locked
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
        fl, based = ctx.snapshot_for_apply(ref) if apply else ctx.snapshot(ref)
    except StaleVersion:
        return _reply(ctx, start, False, error=_STALE)
    except _FAILURES as e:
        return _reply(ctx, start, False, error=clean(e, 300))
    current = {v.name: v.expression for v in variables_from_features(fl)}
    old = current.get(name)
    existing = find_variable(fl, name)
    extra = {"name": name, "old": None if old is None else clean(old, 200),
             "new": expression, "findings": [], "based_on_version": clean(based, 64)}
    if not apply:
        return _reply(ctx, start, False, **extra)

    try:
        expected = ctx.last_read[ref]
        _backstop(ctx, ref, expected)
        new_mv = ctx.backend.set_variable(ref, name, expression,
                                          expected_microversion=expected,
                                          feature_id=existing.id if existing else None)
    except PostWriteError as e:
        return _unknown(ctx, start, ref, str(e), **extra)
    except StaleVersion as e:
        if e.may_have_landed:
            return _unknown(ctx, start, ref, str(e), **extra)
        return _reply(ctx, start, False, error=_STALE, **extra)
    except _FAILURES as e:
        return _reply(ctx, start, False, error=clean(e, 300), **extra)
    ctx.last_read[ref] = new_mv
    # An updated variable keeps its feature id, so the cache can be spliced; a new
    # one has an unknown id, so the entry is dropped (the next write needs cad_read).
    ctx.cache.note_write(ref, new_mv,
                         make_variable_feature(existing.id, name, expression) if existing else None,
                         expected_microversion=expected)
    return _reply(ctx, start, True, **extra)


@_locked
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


@_locked
def cad_read(ctx: Context, ref: DocRef) -> str:
    """Outline of the Part Studio (features + variables). Every sketch is
    `not assessed` (R6: reading existing sketches into DOF reports is out of scope)."""
    start = _start(ctx)
    try:
        fl = ctx.read(ref)
        body = render_outline(fl, variables_from_features(fl))
    except (*_FAILURES, ValueError) as e:
        body = f"error: {clean(e, 300)}"
    used, left = _ledger_numbers(ctx)
    n = _spent(ctx, start)
    if n == _UNAVAILABLE:
        return f"{body}\nrequests used: unavailable (ledger unreadable), budget left: unavailable"
    return f"{body}\nrequests used: {n}, budget left: {left}"
