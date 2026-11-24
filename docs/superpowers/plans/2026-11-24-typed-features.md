# Typed Features + cad_find Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add typed, dry-run-first write tools for the common Part Studio features (`cad_extrude`, `cad_revolve`, `cad_fillet`, `cad_chamfer`, `cad_hole`, `cad_shell`, `cad_pattern`, `cad_mirror`, `cad_boolean`, `cad_plane`), sketches on planes and faces, and `cad_find` (semantic edge/face/body selection).

**Architecture:** Each typed tool is a pure builder `(FeatureList, args) -> Built` that emits native Onshape feature JSON, run through one shared tool runner. The runner resolves names against the last-read snapshot, lints, dry-runs, and applies through the same backstop/stale/cache path `cad_sketch` uses today (extracted into one helper). References are by **feature name**, resolved to feature ids locally. Geometry is selected with an enum-only selector dict that compiles to a FeatureScript query expression, placed in `BTMIndividualQuery-138.queryString`, so a selection is re-evaluated at every regeneration instead of pinning transient ids. `cad_find` compiles the same selector and can evaluate it read-only (1 request) to show what it matches.

**Tech Stack:** Python 3.11+, existing deps only (`mcp`, `httpx`, `numpy`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-23-onshape-duet-design.md` (milestone 4; §7.2 tool surface, §7.3 lint, §7.5 frugality, §4 anti-goals).

## Global Constraints

- Python 3.11+, MIT. No new runtime dependencies.
- Do not copy code from `hedless/onshape-mcp` (no LICENSE) or `bogdanr/onshape-mcp` (GPL). Onshape JSON field names are facts about Onshape's API. The ground truth for every parameter id, btType, enumName and enum value is the UI-made fixture captured in Task 1, never another project's code.
- Anti-goal (§4): callers never supply FeatureScript. Selectors are dicts whose every field maps through a fixed enum table. The only document-derived text placed in a query is a feature id that matched `^[A-Za-z0-9_]{1,64}$`, and feature *names* are never embedded.
- REFUSE (§7.3): a write that depends on raw transient face/edge ids. Typed tools accept no `deterministicIds` input of any kind, so this rule holds by construction. The standard plane ids `JCC`/`JDC`/`JEC` (already used by sketches) are the only deterministic ids emitted.
- Every write tool is dry-run by default (`apply: StrictBool = False`). Refuse findings block apply. Every reply ends with `requests` and `budget_left` via `tools._reply`.
- Request costs (§7.5): dry run ≤ 2 requests (0 when the last-read version is cached). Apply = 1 backstop GET + 1 POST (+1 GET when the reply names no new version). `cad_find` without `evaluate` = same as a dry run; `cad_find(evaluate=True)` adds exactly 1 request.
- `tools._backstop` stays the only stale guard (verified live: Onshape applies skewed writes).
- Every document- or caller-controlled string in a message goes through `textsafe.clean`.
- Units: bare numbers are millimetres (lengths) and degrees (angles). Strings may be `"<number> mm|cm|m|in"`, `"<number> deg"` (angles), or `"#name"` (a variable reference; lengths only in this milestone).

## Coordination

A parallel session builds milestone 5 (generic layer) on branch `generic-features`, under `src/onshape_duet/generic/`. Agreed split:
- **That session owns:** adding raw feature JSON to `Feature`/cache (as a defaulted field; `params` keeps its current meaning) and the featurespecs fetch.
- **This plan owns:** `tools._apply_feature` (Task 2), `model.flatten_params` (Task 2), `backends.*.eval_featurescript` (Task 5), `lint.lint_feature` (Task 6), and everything under `src/onshape_duet/features/`.
- Land Task 2 first and tell that session the commit SHA. Before every merge to `main`, rebase onto the latest `main`.

## Review Focus

1. **A selector that matches nothing**, e.g. a fillet on `created_by` a sketch with `entity: edge`, `geometry: arc` when there are no arcs. Onshape then fails the regeneration. Expected: the write is reported `applied: True` with a `REGEN_STATUS` error finding that says to fix or delete the feature, never a bare OK. The test goes in Task 2: `test_apply_feature_reports_regen_error`.
2. **Ambiguous or hostile feature names and ids from a human-edited document**: two features named `Body`, or a feature id containing `"` or `)`. Expected: refusal naming both candidates, or refusal of the bad id. Nothing reaches a queryString. Tests in Task 4: `test_resolve_ambiguous_name_refused` and `test_hostile_feature_id_refused`.
3. **A `#name` length that points at a human-made non-LENGTH variable** (e.g. an ANGLE variable used as a depth). Expected: refusal saying the variable is not a length. Test in Task 6: `test_lint_non_length_variable_refused`.
4. **Apply after a human edit, or before any read.** Name resolution must use the pinned snapshot (`snapshot_for_apply`), so resolved ids belong to the version the write is pinned to. Expected: plain stale/no-read refusal and zero POSTs. Test in Task 7: `test_extrude_apply_without_read_refused` and `test_extrude_apply_after_human_edit_refused`.
5. **Nonsense sizes**: depth `0`, negative radius, `nan`, `True`, `"10 mm); x("`, through-all with a depth, a pattern count of 1. Expected: a dry-run error naming the argument, and no request beyond the snapshot. Tests in Task 3: `test_length_rejects_bad_values`. Task 7: `test_extrude_through_all_with_depth_refused`. Task 12: `test_pattern_count_below_two_refused`.

## File Structure

| File | Responsibility |
|---|---|
| `scripts/capture_features.py` (create) | Dev-only: dump raw feature-list JSON and run the Task 1 probes, budget-charged |
| `tests/fixtures/ui_features.json` (create) | Raw features from a UI-built reference Part Studio (ground truth for shapes) |
| `tests/fixtures/probes.json` (create) | Raw replies from the queryString-persistence and FeatureScript-eval probes |
| `tests/ui_fixture.py` (create) | `ui_feature(name)`, `assert_matches_ui(built, ui_name)` test helpers |
| `src/onshape_duet/model.py` (modify) | `flatten_params` moved here from `backends/onshape.py` |
| `src/onshape_duet/tools.py` (modify) | `_apply_feature` extracted from `cad_sketch`; `cad_sketch` plane resolution |
| `src/onshape_duet/features/__init__.py` (create) | empty |
| `src/onshape_duet/features/params.py` | Value parsing (length/angle/count) and parameter-JSON makers |
| `src/onshape_duet/features/refs.py` | Name→feature resolution, selector compiler, plane references |
| `src/onshape_duet/features/find.py` | FeatureScript template for `cad_find` and its result parser |
| `src/onshape_duet/features/builders.py` | One `build_<feature>` per typed feature |
| `src/onshape_duet/feature_tools.py` (create) | `run_feature` runner, `cad_find`, and one `cad_<feature>` per builder |
| `src/onshape_duet/lint.py` (modify) | `lint_feature` |
| `src/onshape_duet/backends/{base,onshape,fake}.py` (modify) | `eval_featurescript`; fake parses `parameters` |
| `src/onshape_duet/sketch/{spec,serialize}.py` (modify) | Sketch plane may be a plane feature or a face selector |
| `src/onshape_duet/server.py` (modify) | Register the new tools |
| `docs/agent-guide.md`, `docs/live-checklist.md`, `README.md` (modify) | Usage, live results, "What exists" |

Shared types (defined once and used everywhere after):

```python
# features/params.py
class FeatureSpecError(ValueError): ...
@dataclass(frozen=True)
class Value:
    kind: Literal["length", "angle", "count"]
    expr: str                  # Onshape expression: "10 mm", "45 deg", "#w", "4"
    literal: float | None      # mm / deg / count for literals; None for "#var"
    var: str | None            # variable name for "#var", else None
    arg: str                   # argument name, for messages ("depth")

# features/builders.py
@dataclass(frozen=True)
class Built:
    feature_json: dict         # BTMFeature-134 body passed to backend.add_feature
    values: list[Value]        # every size the caller gave (for lint)
    summary: dict              # human-readable echo of the resolved inputs (names, not ids)

Selector = dict | list[dict]   # see Task 4
PlaneRef = str | dict          # "Front"|"Top"|"Right" | {"plane": "<feature name>"} | face Selector
```

---

### Task 1: Capture UI ground truth and run the probes (human + agent, about 8 requests)

This task gates the rest. The diameter bug showed that guessed JSON shapes fail silently. The owner builds every feature once by hand. Then one script records the raw JSON and answers the two questions the design depends on.

**Files:**
- Create: `scripts/capture_features.py`
- Create: `tests/fixtures/ui_features.json`, `tests/fixtures/probes.json`
- Modify: `docs/live-checklist.md` (new section "Typed features: captured facts")

**Interfaces:**
- Produces: `tests/fixtures/ui_features.json` = the verbatim JSON body of `GET /api/v6/partstudios/d/.../features`. `tests/fixtures/probes.json` = `{"querystring_write": <POST reply>, "querystring_readback": <the feature as re-read>, "fs_eval": <POST /featurescript reply>}`.

- [ ] **Step 1: Ask the owner to build the reference Part Studio by hand** in a throwaway document, with exactly these feature names. The owner raises `budget` by 10 in the config.
  - variable `depth = 10 mm`
  - sketch `Base` on Top: 40×20 rectangle, Ø8 circle inside, one sketch point `p1`
  - extrude `Body`: `Base`, blind, depth `#depth`, new
  - fillet `Round`: the four vertical edges, 2 mm
  - chamfer `Bevel`: one top edge, 1 mm equal distance
  - offset plane `Mid`: Top plane, 5 mm
  - sketch `OnFace` on Body's top face: one circle Ø4
  - extrude `Boss`: `OnFace`, 3 mm, add
  - extrude `Cut`: `OnFace`, through all, remove
  - shell `Hollow`: remove Body's bottom face, 1 mm inward
  - hole `Bore`: simple, through, Ø3, at point `p1`
  - linear pattern `Row`: feature pattern of `Bore`, 3 instances, 8 mm along an X edge
  - circular pattern `Ring`: feature pattern of `Boss`, 4 instances, 360°, equal spacing, about a circular edge
  - mirror `Flip`: feature mirror of `Boss` across Right plane
  - sketch `Profile` on Front with a rectangle beside the origin. Revolve `Spin`: `Profile`, about the rectangle's left line, 360°, new
  - boolean `Merge`: union of `Body` and `Spin` parts

- [ ] **Step 2: Write `scripts/capture_features.py`**: `main(url: str, out_dir: Path)`. Reuse `server.build_context(config_path)` so every request goes through the ledger. Use `OnshapeBackend._request` and `_ok` to:
  - (a) GET the features list and write it verbatim to `ui_features.json`.
  - (b) POST a copy of the `Round` fillet, renamed `ProbeQS`, whose `entities` query is replaced by `{"btType": "BTMIndividualQuery-138", "queryString": "query=qGeometry(qCreatedBy(makeId(\"<Body id>\"), EntityType.EDGE), GeometryType.LINE);"}`. Re-GET the features and store the reply plus the `ProbeQS` feature as read back.
  - (c) POST `.../featurescript` with `{"script": find_script_probe, "queries": []}`, where the script returns `["2", toString(vector(1, 2, 3))]`. Store the reply.

  Pin writes with `sourceMicroversion` like `_write`. The script is not imported by the package and has no tests.

- [ ] **Step 3: Run it** with `.venv/bin/python scripts/capture_features.py <url> tests/fixtures`. Expected: two JSON files written, 6–8 requests charged (`cad_status` shows it).

- [ ] **Step 4: Record the answers in `docs/live-checklist.md`** under "Typed features: captured facts":
  - (Q1) Did the readback of `ProbeQS` keep `queryString` (or an equivalent query), and did it regenerate OK? This decides the selection design.
  - (Q2) Exact JSON shape of the FeatureScript eval `result` for an array of strings (btType names, where the strings sit).
  - (Q3) For each UI feature: `featureType`, and the parameterId/btType/enumName/value of the parameters the builders below set.

  **Gate:** if Q1 is "no" (the queryString was dropped, or resolved into ids that do not follow edits), stop and ask the owner before Task 4. Selection would then need a different design.

- [ ] **Step 5: Ask the owner to delete `ProbeQS` in the UI.** Then commit.

```bash
git add scripts/capture_features.py tests/fixtures docs/live-checklist.md
git commit -m "test: capture UI-built reference features and query/eval probes"
```

---

### Task 2: Shared write path and fake parameter parsing

**Files:**
- Modify: `src/onshape_duet/tools.py` (`cad_sketch` apply block, currently lines ~200–232)
- Modify: `src/onshape_duet/model.py`, `src/onshape_duet/backends/onshape.py` (`_flatten_param`), `src/onshape_duet/backends/fake.py` (`add_feature`)
- Test: `tests/test_write_safety.py`, `tests/test_fake_backend.py`

**Interfaces:**
- Produces: `tools._apply_feature(ctx: Context, ref: DocRef, start, feature_json: dict, extra: dict, what: str) -> dict`. It is the whole apply path now inside `cad_sketch`: backstop, `add_feature`, PostWriteError/StaleVersion/_FAILURES handling, `reread` pop, exact→`note_write`, inexact→`lose_track`, and the `REGEN_STATUS` finding. `what` names the thing in the regen warning ("the sketch", "the extrude"). Returns the `_reply` dict.
- Produces: `model.flatten_params(parameters: list) -> dict[str, object]`, with the same semantics as today's `_flatten_param` applied per parameter and keyed by `parameterId`.
- Produces: `FakeBackend.add_feature` uses `featureType` and, when `feature_json["parameters"]` is a list, `flatten_params` for `params`. The old `"params"`/`"type"` keys still work. A test may set `fake.next_status = "ERROR"` so the next added feature gets that status.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_write_safety.py
def test_apply_feature_reports_regen_error(env):  # Review Focus 1
    ctx, backend, _ = env
    ctx.read(REF)
    backend.next_status = "ERROR"
    out = _apply_feature(ctx, REF, _start(ctx), {"featureType": "fillet", "name": "R",
                         "parameters": []}, {"findings": []}, "the fillet")
    assert out["applied"] is True and out["status"] == "ERROR"
    assert any(f["code"] == "REGEN_STATUS" for f in out["findings"])
    assert "the fillet" in out["warning"]

# tests/test_fake_backend.py
def test_add_feature_flattens_parameters():
    fb = FakeBackend()
    fb.add_feature(REF, {"featureType": "extrude", "name": "Body", "parameters": [
        {"btType": "BTMParameterQuantity-147", "parameterId": "depth", "expression": "#depth"},
        {"btType": "BTMParameterEnum-145", "parameterId": "operationType", "value": "NEW"}]}, "mv0")
    f = fb.list_features(REF).features[0]
    assert (f.type, f.params) == ("extrude", {"depth": "#depth", "operationType": "NEW"})
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_write_safety.py tests/test_fake_backend.py -q`. Expected: the 2 new tests FAIL (ImportError on `_apply_feature`; params `{}`).

- [ ] **Step 3: Implement.** Move the apply block of `cad_sketch` into `_apply_feature` unchanged except for the `what` text. `cad_sketch` calls it. Move `_flatten_param` to `model.py`, wrapped by `flatten_params`. `onshape._parse_feature` and the fake both call it.

- [ ] **Step 4: Run the full suite** with `.venv/bin/pytest -q`. Expected: all pass. Existing `cad_sketch` tests prove the extraction changed nothing.

- [ ] **Step 5: Commit, and send the SHA to the `generic-features` session.**

```bash
git commit -am "refactor: extract _apply_feature from cad_sketch; flatten_params in model"
```

---

### Task 3: Values and parameter JSON

**Files:**
- Create: `src/onshape_duet/features/__init__.py`, `src/onshape_duet/features/params.py`, `tests/ui_fixture.py`
- Test: `tests/test_feature_params.py`

**Interfaces:**
- Produces: `FeatureSpecError`, `Value` (see Shared types), and:
  - `length(x, arg: str, *, positive: bool = True) -> Value`
  - `angle(x, arg: str) -> Value` (0 < deg ≤ 360)
  - `count(x, arg: str, *, minimum: int = 2) -> Value`
  - `parse_length_mm(expr: str) -> float | None` (a literal Onshape expression like `"0.01 m"` → 10.0; anything else → None)
- Produces (JSON makers, each returns one parameter dict):
  - `p_qty(pid, value: Value)` → `BTMParameterQuantity-147` with `expression`, `isInteger` (True for count)
  - `p_enum(pid, enum_name, value)` → `BTMParameterEnum-145`
  - `p_bool(pid, v)` → `BTMParameterBoolean-144`
  - `p_query(pid, exprs: list[str])` → `BTMParameterQueryList-148` with one `BTMIndividualQuery-138` per expr, `queryString = f"query={expr};"`
  - `p_ids(pid, ids)` → `BTMParameterQueryList-148` with `deterministicIds` (standard planes only)
  - `p_features(pid, feature_ids)` → the feature-list parameter shape from the `Row` fixture
  - `feature(feature_type, name, params) -> dict` → `{"btType": "BTMFeature-134", "featureType", "name", "suppressed": False, "parameters"}`
- Produces (tests): `ui_feature(name) -> dict`, and `assert_matches_ui(built: dict, ui_name: str)`. The helper checks that `featureType` is equal and that every emitted parameter exists in the UI feature with the same `parameterId`, `btType` and (if present) `enumName`.

- [ ] **Step 1: Write the failing tests**

```python
def test_length_forms():
    assert length(10, "depth") == Value("length", "10 mm", 10.0, None, "depth")
    assert length("2 in", "depth").literal == pytest.approx(50.8)
    assert length("#depth", "depth") == Value("length", "#depth", None, "depth", "depth")

@pytest.mark.parametrize("bad", [0, -1, float("nan"), True, "10 mm); x(", "#Bad", "#a b", "", "10 deg", None])
def test_length_rejects_bad_values(bad):  # Review Focus 5
    with pytest.raises(FeatureSpecError, match="depth"):
        length(bad, "depth")

def test_angle_and_count():
    assert angle(90, "angle").expr == "90 deg"
    assert count(3, "count") == Value("count", "3", 3.0, None, "count")
    with pytest.raises(FeatureSpecError, match="not supported"):
        angle("#a", "angle")          # angle/count variables: later milestone
    with pytest.raises(FeatureSpecError):
        count(2.5, "count")

def test_parse_length_mm():
    assert parse_length_mm("0.01 m") == pytest.approx(10.0)
    assert parse_length_mm("#w") is None and parse_length_mm("#w * 2") is None

def test_p_query_wraps_expression():
    p = p_query("entities", ['qCreatedBy(makeId("F1"), EntityType.EDGE)'])
    assert p["queries"][0]["queryString"] == 'query=qCreatedBy(makeId("F1"), EntityType.EDGE);'
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_feature_params.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `features/params.py`.** Literal grammar: reuse `sketch.spec._NUM`. Length units are `mm|cm|m|in` (`_UNIT_MM`). Angle units are `deg` only. Variable names follow `^[a-z][a-z0-9_]*$` after `#`. Expressions render with `sketch.serialize._num`. Implement `tests/ui_fixture.py` too.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat: feature value parsing and parameter JSON makers"`.

---

### Task 4: Name resolution, selectors, plane references

**Files:**
- Create: `src/onshape_duet/features/refs.py`
- Test: `tests/test_feature_refs.py`

**Interfaces:**
- Produces: `class RefError(FeatureSpecError)`
- Produces: `resolve(fl: FeatureList, name: str, arg: str, types: set[str] | None = None) -> Feature`. It refuses an unknown name and lists up to 10 existing names. It refuses an ambiguous name and lists both ids. It refuses a type outside `types`. It refuses an id that does not match `^[A-Za-z0-9_]{1,64}$`.
- Produces: `compile_selector(sel: Selector, fl: FeatureList, arg: str) -> str` (an FS expression, no `query=` prefix) and `selector_entity(sel) -> str` (`"face"|"edge"|"body"`; a list must agree).
- Produces: `plane_param(pid: str, plane: PlaneRef, fl: FeatureList) -> dict`. `"Front"/"Top"/"Right"` → `p_ids(pid, [JCC|JDC|JEC])`. `{"plane": name}` → `p_query` of `qCreatedBy(makeId(id), EntityType.FACE)` for a `cPlane` feature. A dict with `entity: "face"` → `p_query(compile_selector)`, which must include `geometry: "plane"` or a `cap`.

**Selector grammar.** A dict, or a list of 1–8 dicts (compiled to `qUnion([...])`). Unknown keys are refused. Keys:

| key | values | compiles to |
|---|---|---|
| `entity` (required) | `face`, `edge`, `body` | `EntityType.FACE/EDGE/BODY` |
| `created_by` | feature name | base `qCreatedBy(makeId("<id>"), EntityType.<E>)` |
| `cap` | `start`, `end` (needs `created_by`; face or edge) | base `qCapEntity(makeId("<id>"), CapType.START/END, EntityType.<E>)` |
| `edges_of` | a single face selector dict (no nesting deeper than one); `entity` must be `edge`; excludes `created_by`/`cap` | base `qAdjacent(<face expr>, AdjacencyType.EDGE, EntityType.EDGE)` |
| (no base key) | | `qOwnedByBody(qAllModifiableSolidBodies(), EntityType.<E>)`; for body: `qAllModifiableSolidBodies()` |
| `geometry` | edge: `line`, `circle`, `arc`; face: `plane`, `cylinder`, `cone`, `sphere`, `torus` | `qGeometry(q, GeometryType.<G>)` |
| `normal` | `+X -X +Y -Y +Z -Z` (faces only) | `qParallelPlanes(q, vector(x, y, z), false)` |
| `farthest` | `+X … -Z` | `qFarthestAlong(q, vector(x, y, z))` (applied last) |

Filters apply in the order geometry → normal → farthest. A `body` selector allows only `created_by`. Signatures were verified against the FeatureScript standard library docs on 2026-11-24. Concave/convex edge selection is deferred: the library has no edge-convexity filter, and building one would mean custom FeatureScript.

- [ ] **Step 1: Write the failing tests** (fl built with `Feature("F1", "extrude", "Body", "OK")`, `Feature("F2", "newSketch", "Base", "OK")`, `Feature("F3", "cPlane", "Mid", "OK")`)

```python
def test_compile_created_by_geometry():
    assert compile_selector({"entity": "edge", "created_by": "Body", "geometry": "line"}, FL, "edges") == \
        'qGeometry(qCreatedBy(makeId("F1"), EntityType.EDGE), GeometryType.LINE)'

def test_compile_cap_and_edges_of_and_farthest():
    assert compile_selector({"entity": "face", "created_by": "Body", "cap": "end"}, FL, "f") == \
        'qCapEntity(makeId("F1"), CapType.END, EntityType.FACE)'
    assert compile_selector({"entity": "edge", "edges_of": {"entity": "face", "created_by": "Body",
                             "cap": "end"}}, FL, "e") == \
        'qAdjacent(qCapEntity(makeId("F1"), CapType.END, EntityType.FACE), AdjacencyType.EDGE, EntityType.EDGE)'
    assert compile_selector({"entity": "face", "normal": "+Z", "farthest": "+Z"}, FL, "f") == \
        'qFarthestAlong(qParallelPlanes(qOwnedByBody(qAllModifiableSolidBodies(), EntityType.FACE), ' \
        'vector(0, 0, 1), false), vector(0, 0, 1))'

def test_compile_union():
    s = compile_selector([{"entity": "edge", "created_by": "Body"}, {"entity": "edge", "created_by": "Body",
                          "geometry": "circle"}], FL, "edges")
    assert s.startswith("qUnion([") and s.count("makeId") == 2

@pytest.mark.parametrize("sel", [{"entity": "vertex"}, {"entity": "edge", "normal": "+Z"},
    {"entity": "edge", "geometry": "plane"}, {"entity": "face", "created_by": "Body", "evil": 1},
    {"entity": "edge", "edges_of": {"entity": "face", "edges_of": {}}}, [], [{"entity": "edge"}] * 9,
    {"entity": "face", "cap": "end"}, {"entity": "edge", "created_by": 'Body") + x('}])
def test_compile_rejects(sel):
    with pytest.raises(RefError):
        compile_selector(sel, FL, "edges")

def test_resolve_ambiguous_name_refused():  # Review Focus 2
    fl = FeatureList("mv", [Feature("F1", "extrude", "Body", "OK"), Feature("F9", "extrude", "Body", "OK")])
    with pytest.raises(RefError, match="F1.*F9|F9.*F1"):
        resolve(fl, "Body", "sketch")

def test_hostile_feature_id_refused():  # Review Focus 2
    fl = FeatureList("mv", [Feature('F1"); x(', "extrude", "Body", "OK")])
    with pytest.raises(RefError):
        compile_selector({"entity": "edge", "created_by": "Body"}, fl, "edges")

def test_plane_param_forms():
    assert plane_param("sketchPlane", "Top", FL)["queries"][0]["deterministicIds"] == ["JDC"]
    assert 'makeId("F3")' in plane_param("p", {"plane": "Mid"}, FL)["queries"][0]["queryString"]
    with pytest.raises(RefError):
        plane_param("p", {"plane": "Body"}, FL)     # not a cPlane
    with pytest.raises(RefError):
        plane_param("p", {"entity": "face", "created_by": "Body"}, FL)  # not provably planar
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_feature_refs.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement `features/refs.py`.** Error messages name `arg` and clean every document string.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat: feature name resolution, selector compiler, plane references"`.

---

### Task 5: cad_find (read-only selection preview)

**Files:**
- Create: `src/onshape_duet/features/find.py`, `src/onshape_duet/feature_tools.py`
- Modify: `backends/base.py`, `backends/onshape.py`, `backends/fake.py`, `server.py`
- Test: `tests/test_cad_find.py`, `tests/test_onshape_backend.py`

**Interfaces:**
- Produces: `Backend.eval_featurescript(ref: DocRef, script: str) -> dict` (the reply's `result` value). Onshape: `POST /api/v6/partstudios/d/{d}/w/{w}/e/{e}/featurescript`, body `{"script": script, "queries": []}`, through `_request` + `_ok` (budget-charged). It is **not** a write: no `sourceMicroversion`, no backstop, no cache change. Fake: tests set `fake.find_results[expr] = (count, [descriptions])`. `eval_featurescript` charges 1, finds the key `expr` that occurs in `script` (default `(0, [])`), and returns it wrapped in the Q2 shape, so `parse_find_result` is exercised.
- Produces: `find.find_script(expr: str, limit: int = 20) -> str`, a fixed template. It returns an array of strings: element 0 is the count, and the following elements are `toString(evApproximateCentroid(context, {"entities": e}) / millimeter)` for up to `limit` entities. It never returns transient ids.
- Produces: `find.parse_find_result(result: dict) -> tuple[int, list[str]]`. Parses the Q2 shape and raises `RuntimeError("unexpected Onshape response: ...")` on anything else.
- Produces: `feature_tools.cad_find(ctx, ref, selector, evaluate: bool = False) -> dict`. Keys: `query` (the compiled expression), `entity`, `based_on_version`, and with evaluate also `count` and `centroids_mm` (cleaned strings). Uses `ctx.snapshot` (does not touch `last_read`). Wrapped in `tools._locked`.
- MCP: `cad_find(url: str, selector: dict | list, evaluate: StrictBool = False) -> dict`.

- [ ] **Step 1: Write the failing tests**
  - `test_find_dry_compiles_without_eval`: `evaluate=False` → `query` equals the compiled string and `backend.calls["eval_featurescript"] == 0`.
  - `test_find_evaluate_costs_one_request`: after `cad_read`, `evaluate=True` → `count == 4`, 4 centroids, ledger delta exactly 1.
  - `test_find_bad_selector_is_error_not_request`: `{"entity": "vertex"}` → `error` mentions `selector`, ledger delta 0 (doc cached).
  - `test_parse_find_result_rejects_garbage`: `parse_find_result({"btType": "x"})` raises RuntimeError.
  - In `tests/test_onshape_backend.py`, `test_eval_featurescript_posts_script_unpinned`: the scripted transport sees one POST to `.../featurescript` whose body has `script` and no `sourceMicroversion`.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_cad_find.py tests/test_onshape_backend.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the backend methods, `find.py`, `cad_find`, and the server registration. The tool docstring says that it costs 0 requests dry and 1 with `evaluate`, and that the selector dict is what every typed tool takes for `edges`/`faces`/`bodies`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** with `git commit -m "feat: cad_find selection preview with read-only FeatureScript eval"`.

---

### Task 6: Feature lint

**Files:**
- Modify: `src/onshape_duet/lint.py`
- Test: `tests/test_lint.py`

**Interfaces:**
- Produces: `lint_feature(name: str, values: list[Value], fl: FeatureList) -> list[Finding]`. Rules:
  - `DEFAULT_NAME` (warn): the existing `_DEFAULT_NAME` regex.
  - `DUPLICATE_NAME` (refuse): another feature in `fl` already has this name. References are by name, so a duplicate would make later references ambiguous.
  - `UNKNOWN_VARIABLE` (refuse): a `Value.var` that is not a variable in `fl`. Onshape would fail the regeneration.
  - `NON_LENGTH_VARIABLE` (refuse): a length `Value.var` whose assign feature has `variableType` other than `LENGTH` (missing counts as LENGTH, matching `cad_variable`).
  - `MAGIC_NUMBER` (warn): a length literal used twice in `values`, or equal (within 1e-9 mm) to `parse_length_mm` of any string param of an existing non-variable feature. The message names the other feature and suggests `cad_variable` and `#name`.

- [ ] **Step 1: Write the failing tests**: `test_lint_feature_default_name_warns`, `test_lint_feature_duplicate_name_refused`, `test_lint_unknown_variable_refused`, `test_lint_non_length_variable_refused` (Review Focus 3: fl has an assign feature with `variableType: "ANGLE"`, `name: "tilt"`; a length `#tilt` → refuse `NON_LENGTH_VARIABLE`), `test_lint_magic_number_across_features` (existing extrude has `depth: "10 mm"`; new fillet radius 10 → warn naming that feature), `test_lint_clean_feature_has_no_findings`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_lint.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `lint_feature`, using `variables.find_variable` for the type check.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** with `git commit -m "feat: lint_feature (duplicate name, unknown/non-length variable, magic number)"`.

---

### Task 7: Tool runner and cad_extrude

**Files:**
- Create: `src/onshape_duet/features/builders.py`
- Modify: `src/onshape_duet/feature_tools.py`, `src/onshape_duet/server.py`
- Test: `tests/test_builders.py`, `tests/test_feature_tools.py`

**Interfaces:**
- Produces: `Built` (see Shared types).
- Produces: `feature_tools.run_feature(ctx, ref, name: str, build: Callable[[FeatureList], Built], apply: bool, what: str) -> dict`. Flow:
  1. `apply` and no `last_read` → `_NO_READ` / `reread` hint, same as `cad_sketch`.
  2. Snapshot: `snapshot_for_apply` if apply, else `snapshot`. A StaleVersion → `_refusal`.
  3. `build(fl)`. A FeatureSpecError or `_FAILURES` → `error`.
  4. Validate `name`: a non-empty string, at most 100 chars, printable.
  5. `lint_feature`.
  6. Refuse findings → `applied: False`, `"refused, nothing written: ..."`.
  7. Dry run → reply with `feature_type`, `summary`, `findings`, `based_on_version`.
  8. Apply → `tools._apply_feature(..., what)`.

  It holds `ctx.lock`.
- Produces: `build_extrude(fl, *, sketch: str, depth=None, operation: str = "new", end: str = "blind", flip: bool = False) -> Built`.
  - `sketch` resolves to type `newSketch`; `entities` = `p_query` of `qSketchRegion(makeId("<id>"), true)`.
  - `operation`: `new|add|remove|intersect` → the `NewBodyOperationType` values in the fixture.
  - `end`: `blind|symmetric|through_all` → the `endBound` values in the fixture (`Cut` shows through-all). Depth is required unless the end is `through_all`, and is refused if given with it.
  - `flip` → `oppositeDirection`. Body type SOLID.
  - Whatever scope/merge parameters the UI `Boss`/`Cut` fixtures carry for add/remove are emitted with the UI's defaults (merge with all).
- Produces: `cad_extrude(ctx, ref, name, sketch, depth=None, operation="new", end="blind", flip=False, apply=False) -> dict`.
- MCP: `cad_extrude(url, name, sketch, depth: float | str | None = None, operation="new", end="blind", flip: StrictBool = False, apply: StrictBool = False)`.

- [ ] **Step 1: Write the failing tests**
  - `test_build_extrude_matches_ui`: `assert_matches_ui(build_extrude(FL, sketch="Base", depth="#depth").feature_json, "Body")`. The NEW enum value and the `depth` expression equal the fixture's.
  - `test_build_extrude_remove_through_all_matches_ui`: against `"Cut"`.
  - `test_extrude_through_all_with_depth_refused` (Review Focus 5) and `test_extrude_unknown_sketch_lists_names`.
  - `test_extrude_dry_run_no_write`: zero `add_feature`, `summary == {"sketch": "Base", "depth": "#depth", "operation": "new", "end": "blind"}`.
  - `test_extrude_apply_adds_named_feature`: after `cad_read`, apply → `applied: True`, and a following `cad_read` outline contains `Body [extrude] OK`.
  - `test_extrude_apply_without_read_refused` and `test_extrude_apply_after_human_edit_refused` (Review Focus 4: `simulate_human_edit` → stale, `add_feature` count 0).
  - `test_extrude_magic_number_warns`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_builders.py tests/test_feature_tools.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `run_feature`, `build_extrude`, `cad_extrude`, and the server registration. The server docstring follows `cad_sketch`'s (dry run default, read before apply, refuse vs warn).
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** with `git commit -m "feat: typed feature runner and cad_extrude"`.

---

### Task 8: cad_plane and sketches on planes/faces

**Files:**
- Modify: `features/builders.py`, `feature_tools.py`, `server.py`, `sketch/spec.py`, `sketch/serialize.py`, `tools.py` (`cad_sketch`)
- Test: `tests/test_builders.py`, `tests/test_cad_sketch.py`

**Interfaces:**
- Produces: `build_plane(fl, *, base: PlaneRef, offset, flip: bool = False) -> Built`. It is an offset plane matching fixture `Mid` (`featureType`, plane-type enum value for offset, `offset`, `oppositeDirection`, `entities` from `plane_param`).
- Produces: `cad_plane(ctx, ref, name, base, offset, flip=False, apply=False)`. MCP takes the same arguments, with `url` first.
- Changes: the sketch spec `plane` may be a standard name (unchanged, still zero requests) or any `PlaneRef`. `parse_sketch_spec` accepts a dict `plane` without validating it, and `Sketch.plane` becomes `str | dict`. `to_feature_json(sketch, name, plane_param: dict | None = None)` uses `plane_param` when given. `cad_sketch` takes a snapshot when the plane is a dict, the same way it does for variables, and builds the parameter with `refs.plane_param("sketchPlane", ...)`. A RefError → `error`.

- [ ] **Step 1: Write the failing tests**: `test_build_plane_matches_ui` (against `"Mid"`), `test_sketch_on_plane_feature` (spec plane `{"plane": "Mid"}` → `sketchPlane` queryString contains Mid's id), `test_sketch_on_face_selector` (`{"entity": "face", "created_by": "Body", "cap": "end"}`), `test_sketch_on_nonplanar_selector_refused`, and `test_sketch_standard_plane_still_zero_requests` (ledger delta 0 on a dry run with no variables).
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_builders.py tests/test_cad_sketch.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** with `git commit -m "feat: cad_plane; sketches on plane features and planar faces"`.

---

### Task 9: cad_revolve

**Files:** `features/builders.py`, `feature_tools.py`, `server.py`. Tests: `tests/test_builders.py`, `tests/test_feature_tools.py`.

**Interfaces:**
- Produces: `build_revolve(fl, *, sketch: str, axis: str, angle=360, operation: str = "new", symmetric: bool = False, flip: bool = False) -> Built`.
  - `axis` is a line entity id in that sketch (e.g. `"box.left"`, validated by `sketch.spec._ENTITY_ID` plus dots) → `p_query("axis", [f'sketchEntityQuery(makeId("<id>"), EntityType.EDGE, "{axis}")'])`.
  - A literal angle of 360 → the fixture's full-revolve type. Otherwise the one-direction type, or the symmetric type if `symmetric`. `angle` is emitted unless full.
  - Profile, operation and flip work as in extrude.
- Produces: `cad_revolve(ctx, ref, name, sketch, axis, angle=360, operation="new", symmetric=False, flip=False, apply=False)`, with the same MCP shape.

- [ ] **Step 1: Write the failing tests**: `test_build_revolve_matches_ui` (against `"Spin"`), `test_revolve_partial_angle_sets_type_and_angle`, `test_revolve_axis_id_rejects_quotes` (`axis='a"); x('` → FeatureSpecError), and `test_revolve_dry_run_summary`.
- [ ] **Step 2: Run** the tests. Expected: FAIL. **Step 3:** Implement. **Step 4:** Run `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** with `git commit -m "feat: cad_revolve"`.

---

### Task 10: cad_fillet and cad_chamfer

**Files:** as Task 9.

**Interfaces:**
- Produces: `build_fillet(fl, *, edges: Selector, radius) -> Built`. The selector entity must be `edge` or `face`. Parameters as fixture `Round`: `entities` = `p_query` of the compiled selector, `radius`, and the circular cross-section enum value from the fixture.
- Produces: `build_chamfer(fl, *, edges: Selector, distance) -> Built`. Fixture `Bevel`: the equal-offsets type and the width/distance parameter id from the fixture.
- Produces: `cad_fillet(ctx, ref, name, edges, radius, apply=False)` and `cad_chamfer(ctx, ref, name, edges, distance, apply=False)`. MCP takes `edges: dict | list`.

- [ ] **Step 1: Write the failing tests**: `test_build_fillet_matches_ui`, `test_build_chamfer_matches_ui`, `test_fillet_body_selector_refused` (`entity: body` → error), `test_fillet_summary_echoes_selector_not_ids` (the summary holds the caller's selector with names; no `F\d` ids), and `test_fillet_radius_variable_reference`.
- [ ] **Step 2–4:** Run (FAIL), implement, then run `.venv/bin/pytest -q` (PASS).
- [ ] **Step 5: Commit** with `git commit -m "feat: cad_fillet and cad_chamfer"`.

---

### Task 11: cad_shell and cad_boolean

**Files:** as Task 9.

**Interfaces:**
- Produces: `build_shell(fl, *, faces: Selector, thickness, outward: bool = False) -> Built`. The faces selector must have `entity: face`. Parameters as fixture `Hollow`: faces to remove, thickness, and the inward/outward flag the fixture uses.
- Produces: `build_boolean(fl, *, operation: str, tools: Selector, targets: Selector | None = None, keep_tools: bool = False) -> Built`.
  - `operation`: `union|subtract|intersect` → the `BooleanOperationType` values (UNION verified from fixture `Merge`; SUBTRACTION/INTERSECTION per that enum).
  - `tools`/`targets` must have `entity: body`. `targets` is required for subtract and refused otherwise.
- Produces: `cad_shell(ctx, ref, name, faces, thickness, outward=False, apply=False)` and `cad_boolean(ctx, ref, name, operation, tools, targets=None, keep_tools=False, apply=False)`.

- [ ] **Step 1: Write the failing tests**: `test_build_shell_matches_ui`, `test_build_boolean_union_matches_ui`, `test_boolean_subtract_requires_targets`, `test_boolean_union_rejects_targets`, and `test_shell_edge_selector_refused`.
- [ ] **Step 2–4:** Run (FAIL), implement, then run `.venv/bin/pytest -q` (PASS).
- [ ] **Step 5: Commit** with `git commit -m "feat: cad_shell and cad_boolean"`.

---

### Task 12: cad_pattern and cad_mirror (feature patterns)

**Files:** as Task 9.

**Interfaces:**
- Produces: `build_pattern(fl, *, kind: str, features: list[str], count, direction: Selector | None = None, spacing=None, axis: Selector | None = None, angle=360) -> Built`.
  - `features`: 1–20 names, each resolved and de-duplicated → `p_features`. The pattern type is FEATURE.
  - `linear` (fixture `Row`): needs `direction` (an edge or planar-face selector) and `spacing`. Refuses `axis`.
  - `circular` (fixture `Ring`): needs `axis` (an edge selector). Emits `angle` and equal spacing as in the fixture. Refuses `direction`/`spacing`.
  - `count` ≥ 2.
- Produces: `build_mirror(fl, *, features: list[str], plane: PlaneRef) -> Built`. Fixture `Flip`: the FEATURE pattern type, the feature list, and the mirror plane from `plane_param`.
- Produces: `cad_pattern(ctx, ref, name, kind, features, count, direction=None, spacing=None, axis=None, angle=360, apply=False)` and `cad_mirror(ctx, ref, name, features, plane, apply=False)`.

- [ ] **Step 1: Write the failing tests**: `test_build_linear_pattern_matches_ui`, `test_build_circular_pattern_matches_ui`, `test_build_mirror_matches_ui`, `test_pattern_count_below_two_refused` (Review Focus 5), `test_linear_pattern_rejects_axis`, and `test_pattern_unknown_feature_lists_names`.
- [ ] **Step 2–4:** Run (FAIL), implement, then run `.venv/bin/pytest -q` (PASS).
- [ ] **Step 5: Commit** with `git commit -m "feat: cad_pattern (linear/circular feature patterns) and cad_mirror"`.

---

### Task 13: cad_hole (simple holes at sketch points)

**Files:** as Task 9.

**Interfaces:**
- Produces: `build_hole(fl, *, sketch: str, points: list[str], diameter, depth=None) -> Built`.
  - Simple style only (counterbore/countersink are deferred and refused with that message).
  - `points` are 1–50 point entity ids in `sketch` → `p_query("locations", [sketchEntityQuery(makeId(id), EntityType.VERTEX, "<pt>") ...])`, using the location parameter id from fixture `Bore`.
  - `depth=None` gives the fixture's through end style. Otherwise the blind style and its depth parameter.
  - If the fixture shows an explicit body scope parameter, emit `qAllModifiableSolidBodies()` there.
  - Every other parameter the UI hole carries is left to Onshape's defaults, unless Task 1's probe or the Task 14 live run shows the feature fails without it. In that case, add it and record it in the live checklist.
- Produces: `cad_hole(ctx, ref, name, sketch, points, diameter, depth=None, apply=False)`.

- [ ] **Step 1: Write the failing tests**: `test_build_hole_through_matches_ui`, `test_hole_blind_sets_depth`, `test_hole_point_id_rejects_quotes`, and `test_hole_counterbore_refused_with_message`.
- [ ] **Step 2–4:** Run (FAIL), implement, then run `.venv/bin/pytest -q` (PASS).
- [ ] **Step 5: Commit** with `git commit -m "feat: cad_hole (simple, through or blind, at sketch points)"`.

---

### Task 14: Docs and live verification (owner raises the budget by about 30)

**Files:** `docs/agent-guide.md`, `docs/live-checklist.md`, `README.md`, `docs/testing-guide.md`.

- [ ] **Step 1: Docs.**
  - Agent guide: the selector grammar table, the workflow `cad_read` → `cad_find(evaluate=True)` on non-trivial selections → dry-run the typed tool → show the owner → apply → `cad_read`, and the request costs.
  - README "What exists": list the new tools and correct the stale line saying nothing has run live.
  - Testing guide: a live section for the typed tools.
- [ ] **Step 2: Live run on a fresh throwaway Part Studio**, through the MCP tools themselves:
  1. variable `t = 10 mm` and sketch `Base` on Top
  2. `cad_extrude Body`
  3. `cad_find` on `edges_of` the end cap (evaluate; expect 4)
  4. `cad_fillet`, then `cad_chamfer`
  5. `cad_plane Mid`, plus a sketch on Body's end cap and `cad_extrude Boss`
  6. `cad_hole`, then `cad_pattern` (linear) and `cad_mirror`
  7. `cad_shell`
  8. a revolve from a sketch on Front, then `cad_boolean` union

  After each apply, `status` must be `OK`. Then change `t` with `cad_variable` and confirm in the UI that every feature regenerated (proves the queryString selections follow edits). Expected cost is about 35 requests. Record each result in the live checklist.
- [ ] **Step 3: Ask the owner to open each feature in the UI.** Check that the selections show and that one edit by hand (e.g. the fillet radius) works. Record the result.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass. Then commit: `git commit -am "docs: typed features usage and live verification results"`.
