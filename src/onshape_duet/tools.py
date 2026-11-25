"""Tool implementations (plain functions; the MCP server wraps them).

Staleness model (R4): write tools pin to the microversion Claude last READ via
`Context.last_read`, never to the version seen at call time, so a human edit
between read and write is refused. The tool must not refresh `last_read`
before the write.
"""
from __future__ import annotations

import os
import difflib
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
from .features.params import feature as _btm_feature
from .generic.lookup import find_feature
from .generic.lint import lint_generic
from .generic.update import merge
from .generic.render import render_feature, render_spec, render_spec_index
from .generic.specs import FeatureSpec
from .generic.validate import TYPED_TOOL_REQUIRED, prepare
from .features.params import FeatureSpecError
from .features.refs import plane_param
from .lint import Finding, lint_sketch
from .model import Feature, FeatureList
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
    # Why the next write needs a fresh cad_read (cleared by `read`).
    reread: dict[DocRef, str] = field(default_factory=dict)
    # Native feature specs per document, fetched once (stored only on success).
    specs: dict[DocRef, list[FeatureSpec]] = field(default_factory=dict)
    # MCP runs sync tools in worker threads; one RLock serialises every tool body
    # (last_read, DocCache and the ledger-delta request counts are shared state).
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def read(self, ref: DocRef) -> FeatureList:
        fl = self.cache.read(ref)
        self.last_read[ref] = fl.microversion
        self.reread.pop(ref, None)
        return fl

    def feature_specs(self, ref: DocRef) -> list[FeatureSpec]:
        if ref not in self.specs:
            self.specs[ref] = self.backend.get_feature_specs(ref)
        return self.specs[ref]

    def lose_track(self, ref: DocRef, why: str) -> None:
        """After a landed write whose new version Claude has not actually read:
        forget what was read, so the next write demands a fresh cad_read."""
        self.cache.invalidate(ref)
        self.last_read.pop(ref, None)
        self.reread[ref] = why

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


_NO_READ = "read the document first (cad_read) before applying"
_REFETCHED = ("the write landed, but its new version was fetched separately and may include "
              "edits you have not read; re-read with cad_read before the next write")
_MIDTREE = ("features after this one may have changed status; "
            "re-read with cad_read before the next write")
_NEW_VARIABLE = ("re-read with cad_read before the next write: a variable was just created, "
                 "so the cached feature list no longer matches the document")


def _refusal(ctx: Context, ref: DocRef) -> str:
    """Why a write is refused for lack of a cached copy, from the CURRENT state: if a
    re-read hint is pending, one version check tells whether the document has changed
    since (then the plain stale message is the true one) or not (then the hint is)."""
    reason = ctx.reread.get(ref)
    if not reason:
        return _STALE
    mv = ctx.last_read.get(ref)
    if mv is not None:
        try:
            if ctx.backend.get_microversion(ref) != mv:
                ctx.reread.pop(ref, None)
                return _STALE
        except _FAILURES:
            pass
    return reason


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(ctx: Context, *args, **kwargs):
        with ctx.lock:
            return fn(ctx, *args, **kwargs)
    return wrapper


_STALE = ("stale: the document changed since it was last read; "
          "re-read the document (cad_read) and retry")
_VAR_NAME = re.compile(r"[a-z][a-z0-9_]*")
_HASH_REF = re.compile(r"#([A-Za-z_][A-Za-z0-9_]*)")
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
    """Client-side check before every write (costs 1 request). This is the ONLY
    stale-write guard: verified live, Onshape ignores rejectMicroversionSkew and
    applies a skewed write. Must stay. A human edit between this GET and the POST
    can still land; the backend then reports StaleVersion(may_have_landed=True)."""
    if ctx.backend.get_microversion(ref) != expected:
        raise StaleVersion("document version differs from the version last read")


def _apply_feature(ctx: Context, ref: DocRef, start, feature_json: dict, extra: dict,
                   what: str, *, feature_id: str | None = None) -> dict:
    """Shared apply path for every feature write (lock already held). `extra` must
    hold a `findings` list; `what` names the written thing in the regen warning.
    With `feature_id` the existing feature is updated in place instead of added."""
    try:
        expected = ctx.last_read[ref]
        _backstop(ctx, ref, expected)
        if feature_id is None:
            result = ctx.backend.add_feature(ref, feature_json, expected_microversion=expected)
        else:
            # Decided at the pinned version, before the write: only an update of the
            # last feature leaves the cached list valid (nothing after it to regenerate).
            cached = ctx.cache.peek(ref)
            is_last = (cached is not None and cached.microversion == expected
                       and bool(cached.features) and cached.features[-1].id == feature_id)
            result = ctx.backend.update_feature(ref, feature_id, feature_json,
                                                expected_microversion=expected)
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
    ctx.reread.pop(ref, None)  # a successful write supersedes any earlier hint
    # An update reply without the full `parameters` list cannot be spliced: the cache
    # would keep a stale or partial raw. Treat it like an inexact write and refetch.
    full_reply = feature_id is None or isinstance(result.feature.raw.get("parameters"), list)
    if result.exact and full_reply and (feature_id is None or is_last):
        ctx.last_read[ref] = result.microversion
        ctx.cache.note_write(ref, result.microversion, result.feature,
                             expected_microversion=expected)
        extra = dict(extra)
    elif result.exact and full_reply:
        ctx.lose_track(ref, _MIDTREE)
        extra = {**extra, "note": _MIDTREE}
    else:
        ctx.lose_track(ref, _REFETCHED)
        extra = {**extra, "note": _REFETCHED}
    status = clean(result.feature.status, 40)
    done = {**extra, "feature_id": clean(result.feature.id, 64), "status": status}
    if status != "OK":
        warning = (f"applied, but regeneration status is {status}: {what} was written; "
                   "inspect and fix it, or delete it")
        done["warning"] = warning
        done["findings"] = [*extra["findings"],
                            {"level": "error", "code": "REGEN_STATUS", "message": warning}]
    return _reply(ctx, start, True, **done)


@_locked
def cad_sketch(ctx: Context, ref: DocRef, name: str, spec: dict, apply: bool = False) -> dict:
    start = _start(ctx)
    if apply and ref not in ctx.last_read:
        return _reply(ctx, start, False, error=ctx.reread.get(ref) or _NO_READ)
    based_on: dict = {}
    try:
        wanted = referenced_variables(spec)
        variables: dict[str, float] = {}
        plane_ref = spec.get("plane") if isinstance(spec, dict) else None
        fl = None
        if wanted or isinstance(plane_ref, dict):  # else the document is not consulted at all
            fl, based = ctx.snapshot_for_apply(ref) if apply else ctx.snapshot(ref)
            based_on = {"based_on_version": clean(based, 64)}
            if wanted:
                variables = evaluate_variables(variables_from_features(fl), only=wanted)
        sketch = parse_sketch_spec(spec, variables)
        report = analyze(sketch)
        plane = (plane_param("sketchPlane", sketch.plane, fl)
                 if isinstance(sketch.plane, dict) else None)
        feature_json = to_feature_json(sketch, name, plane_param=plane)
    except StaleVersion:
        return _reply(ctx, start, False, error=_refusal(ctx, ref))
    except (SketchSpecError, FeatureSpecError, *_FAILURES) as e:
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

    return _apply_feature(ctx, ref, start, feature_json, extra, "the sketch")


@_locked
def cad_feature_add(ctx: Context, ref: DocRef, feature_type: str, name: str, params: dict,
                    apply: bool = False) -> dict:
    start = _start(ctx)
    if not isinstance(name, str) or not 1 <= len(name) <= 64 or not name.isprintable():
        return _reply(ctx, start, False,
                      error="invalid name: must be 1-64 printable characters")
    if apply and ref not in ctx.last_read:
        return _reply(ctx, start, False, error=ctx.reread.get(ref) or _NO_READ)
    try:
        specs = ctx.feature_specs(ref)
        fl, based = ctx.snapshot_for_apply(ref) if apply else ctx.snapshot(ref)
    except StaleVersion:
        return _reply(ctx, start, False, error=_refusal(ctx, ref))
    except _FAILURES as e:
        return _reply(ctx, start, False, error=clean(e, 300))

    spec_by_type = {s.type: s for s in specs}
    prepared = prepare(spec_by_type, feature_type, params, fl, mode="add")
    findings = list(prepared.findings)
    spec = spec_by_type.get(feature_type) if isinstance(feature_type, str) else None
    if spec is not None:  # no native spec: prepare's refusal is the whole story
        findings += lint_generic(feature_type, name, params, spec, fl, mode="add")
    extra = {"feature_type": clean(feature_type, 60), "name": name, "params": prepared.readable,
             "defaults_applied": prepared.defaults_applied,
             "findings": [f.to_dict() for f in findings],
             "based_on_version": clean(based, 64)}
    refusals = [f for f in findings if f.level == "refuse"]
    if refusals:
        return _reply(ctx, start, False,
                      error="refused, nothing written: " + "; ".join(
                          f"{f.code}: {f.message}" for f in refusals), **extra)
    if not apply:
        return _reply(ctx, start, False, **extra)
    return _apply_feature(ctx, ref, start,
                          _btm_feature(feature_type, name, prepared.parameters), extra,
                          f"the {clean(spec.name, 60)} feature")


@_locked
def cad_feature_update(ctx: Context, ref: DocRef, feature: str, params: dict,
                       apply: bool = False) -> dict:
    start = _start(ctx)
    if not isinstance(feature, str) or not feature:
        return _reply(ctx, start, False, error="feature must be a feature name or id")
    if not isinstance(params, dict) or not params:
        return _reply(ctx, start, False, error="params is empty: name the parameters to change")
    if apply and ref not in ctx.last_read:
        return _reply(ctx, start, False, error=ctx.reread.get(ref) or _NO_READ)
    try:
        specs = ctx.feature_specs(ref)
        fl, based = ctx.snapshot_for_apply(ref) if apply else ctx.snapshot(ref)
    except StaleVersion:
        return _reply(ctx, start, False, error=_refusal(ctx, ref))
    except _FAILURES as e:
        return _reply(ctx, start, False, error=clean(e, 300))
    try:
        f = find_feature(fl, feature)
    except ValueError as e:
        return _reply(ctx, start, False, error=clean(e, 300))
    if not f.raw:
        return _reply(ctx, start, False,
                      error="no full copy of this feature; re-read with cad_read first")

    namespace = f.raw.get("namespace")
    if isinstance(namespace, str) and namespace:  # live custom features carry it apart from featureType
        return _reply(ctx, start, False, error=(
            f"refused, nothing written: CUSTOM_FEATURE: {clean(f.type)} is a custom (FeatureScript) "
            "feature; generic writes support native types only"))
    spec_by_type = {s.type: s for s in specs}
    index = fl.features.index(f)
    prepared = prepare(spec_by_type, f.type, params, fl, mode="update", self_index=index,
                       current_raw=f.raw)
    findings = list(prepared.findings)
    spec = spec_by_type.get(f.type)
    if spec is not None and f.type not in TYPED_TOOL_REQUIRED:
        findings += lint_generic(f.type, None, params, spec, fl, mode="update")
    extra = {"feature": clean(f.name), "feature_id": clean(f.id, 64), "changes": [],
             "findings": [x.to_dict() for x in findings], "based_on_version": clean(based, 64)}
    refusals = [x for x in findings if x.level == "refuse"]
    if refusals:
        return _reply(ctx, start, False,
                      error="refused, nothing written: " + "; ".join(
                          f"{x.code}: {x.message}" for x in refusals), **extra)
    new_raw, changes = merge(f.raw, prepared.parameters)
    extra["changes"] = changes
    if not apply:
        return _reply(ctx, start, False, **extra)
    new_raw.setdefault("featureId", f.id)
    return _apply_feature(ctx, ref, start, new_raw, extra,
                          f"the {clean(spec.name, 60)} feature", feature_id=f.id)


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
        return _reply(ctx, start, False, error=ctx.reread.get(ref) or _NO_READ)
    try:
        fl, based = ctx.snapshot_for_apply(ref) if apply else ctx.snapshot(ref)
    except StaleVersion:
        return _reply(ctx, start, False, error=_refusal(ctx, ref))
    except _FAILURES as e:
        return _reply(ctx, start, False, error=clean(e, 300))
    current = {v.name: v.expression for v in variables_from_features(fl)}
    old = current.get(name)
    existing = find_variable(fl, name)
    vtype = existing.params.get("variableType") if existing else None
    if vtype not in (None, "LENGTH"):  # only length variables are written; never convert one
        return _reply(ctx, start, False, error=(
            f"variable {clean(name, 40)} is a {clean(str(vtype), 20)} variable; cad_variable only "
            "writes LENGTH variables, so nothing was written. Edit it in Onshape."))
    unknown = sorted({m for m in _HASH_REF.findall(expression) if m not in current})
    findings = [Finding("warn", "UNKNOWN_VARIABLE",
                        f"the expression references #{clean(m, 40)}, which is not a variable "
                        "in the document as last read").to_dict() for m in unknown]
    extra = {"name": name, "old": None if old is None else clean(old, 200),
             "new": expression, "findings": findings, "based_on_version": clean(based, 64)}
    if not apply:
        return _reply(ctx, start, False, **extra)

    try:
        expected = ctx.last_read[ref]
        _backstop(ctx, ref, expected)
        # Decided at the pinned version, before the write (as in _apply_feature): only
        # an update of the last feature leaves the cached list valid.
        cached = ctx.cache.peek(ref)
        is_last = (existing is not None and cached is not None
                   and cached.microversion == expected and bool(cached.features)
                   and cached.features[-1].id == existing.id)
        result = ctx.backend.set_variable(ref, name, expression,
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
    status = clean(result.feature.status, 40)
    if status:  # "" means the backend did not report a regeneration status
        extra["status"] = status
        if status != "OK":
            warning = (f"applied, but the variable feature's regeneration status is {status}: "
                       "the variable was written; inspect and fix it")
            extra["warning"] = warning
            extra["findings"] = [*findings, {"level": "error", "code": "REGEN_STATUS",
                                             "message": warning}]
    if result.feature.id:
        extra["feature_id"] = clean(result.feature.id, 64)
    if not result.exact:
        ctx.lose_track(ref, _REFETCHED)
        return _reply(ctx, start, True, **extra, note=_REFETCHED)
    ctx.reread.pop(ref, None)
    if existing and not is_last:  # later features may have regenerated; the cache can't know
        ctx.lose_track(ref, _MIDTREE)
        return _reply(ctx, start, True, **extra, note=_MIDTREE)
    new_mv = result.microversion
    ctx.last_read[ref] = new_mv
    # An updated variable keeps its feature id, so the cache can be spliced; a new
    # one has an unknown id, so the entry is dropped (the next write needs cad_read).
    spliced = None
    if existing:
        spliced = make_variable_feature(existing.id, name, expression)
        if status:  # keep the regeneration status the write reply reported
            spliced = Feature(spliced.id, spliced.type, spliced.name, status, spliced.params)
    ctx.cache.note_write(ref, new_mv, spliced, expected_microversion=expected)
    if existing is None:
        ctx.reread[ref] = _NEW_VARIABLE
        extra["note"] = _NEW_VARIABLE
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


@_locked
def cad_specs(ctx: Context, ref: DocRef, feature_type: str | None = None) -> dict:
    """Native feature types (index) or one type's parameters. 1 request the first
    time per document, then 0. Never writes."""
    start = _start(ctx)
    try:
        specs = ctx.feature_specs(ref)
    except _FAILURES as e:
        return _reply(ctx, start, False, error=clean(e, 300))
    if feature_type is None:
        return _reply(ctx, start, False, specs=render_spec_index(specs))
    hit = next((s for s in specs if s.type == feature_type), None)
    if hit is None:
        close = difflib.get_close_matches(str(feature_type), [s.type for s in specs], n=5, cutoff=0.4)
        hint = (f"close native types: {', '.join(clean(c, 60) for c in close)}" if close
                else "call cad_specs without feature_type for the list")
        return _reply(ctx, start, False, error=(
            f"no native feature type {clean(feature_type, 60)!r} (custom FeatureScript "
            f"features have no spec here); {hint}"))
    return _reply(ctx, start, False, specs=render_spec(hit))


@_locked
def cad_inspect(ctx: Context, ref: DocRef, feature: str) -> dict:
    """Every parameter of one feature, by name or id. 0 requests after a cad_read;
    specs label the parameters only if cad_specs already fetched them."""
    start = _start(ctx)
    try:
        fl, _ = ctx.snapshot(ref)
        f = find_feature(fl, feature)
    except (StaleVersion, ValueError, *_FAILURES) as e:
        return _reply(ctx, start, False, error=clean(e, 300))
    cached = ctx.specs.get(ref)
    spec = next((s for s in cached or [] if s.type == f.type), None)
    return _reply(ctx, start, False, feature=render_feature(f, spec))
