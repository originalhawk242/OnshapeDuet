# Foundation, Read Path and Sketch Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working MCP server that reads an Onshape Part Studio into a cached outline, solves sketch degrees of freedom locally, and writes a constrained sketch and variables through a budgeted, version-pinned, linted path.

**Architecture:** A `Backend` protocol hides Onshape; a fake backend drives all tests offline. A request ledger charges every call before it is sent. A document cache is keyed by microversion. A local numpy Jacobian-rank solver feeds an editability lint that returns OK / WARN / REFUSE before any write.

**Tech Stack:** Python 3.11+, `mcp` (official SDK, FastMCP), `httpx`, `numpy`, `pytest`. MIT license.

**Spec:** `docs/superpowers/specs/2026-11-23-onshape-duet-design.md` (milestones 1–3). Later milestones (typed features, generic feature layer, lifecycle, assemblies, benchmark) get their own plans.

## Global Constraints

- Python 3.11+, MIT license, package `onshape_duet` under `src/`. No TypeScript.
- Do not copy code from `bogdanr/onshape-mcp` (GPL-3.0) or `hedless/onshape-mcp` (no LICENSE file). The solver is clean-room from the algorithm (rank of the constraint Jacobian).
- Port from `saltyeg/cadkit-mcp` (only `cadkit_mcp/`) or `ReshefElisha/jarvis-onshape-mcp` (only its own additions) only after checking the file is not hedless-derived; keep attribution in a `NOTICE` file.
- Request budget defaults to 0; only the owner's config file raises it. Every request is charged before it is sent, retries and redirects included.
- Every write tool is dry-run by default and applies only with `apply=True`.
- No FeatureScript authoring by the agent.
- Onshape's API does not report "fully defined"; degrees of freedom are computed locally.

## Review Focus

- Over-constrained or redundant constraints: reported as redundant, never as "fully defined".
- Budget 0 (the default): any live read or write refuses with a message naming the config key to set.
- A human edits the document between Claude's read and write: the write is refused as stale and nothing is sent.
- Sketch spec referencing an entity or variable that does not exist: a clear error naming it, not a stack trace.
- Ledger file missing, corrupt or unwritable: fail closed (no requests), not open.

---

## File Structure

```
pyproject.toml  LICENSE  NOTICE  README.md
src/onshape_duet/
  config.py        Config + load_config(path)
  docref.py        DocRef + parse_url
  budget.py        Ledger, BudgetExceeded
  model.py         Feature, FeatureList, Variable
  backends/base.py     Backend protocol
  backends/fake.py     FakeBackend (tests)
  backends/onshape.py  OnshapeBackend (HTTP, HMAC auth)
  cache.py         DocCache
  outline.py       render_outline
  sketch/spec.py       parse_sketch_spec -> Sketch
  sketch/solver.py     analyze(sketch) -> DofReport
  sketch/serialize.py  to_feature_json(sketch, name)
  lint.py          Finding, lint_sketch, lint_name_and_numbers
  tools.py         tool functions (pure, take a Context)
  server.py        MCP registration
tests/  (mirrors modules)
```

---

### Task 1: Scaffold, config and document references

**Files:**
- Create: `pyproject.toml`, `LICENSE` (MIT), `NOTICE`, `src/onshape_duet/__init__.py`, `config.py`, `docref.py`
- Test: `tests/test_config.py`, `tests/test_docref.py`

**Interfaces:**
- Produces: `Config(budget: int, ledger_path: Path, key_env: str, secret_env: str, base_url: str)`; `load_config(path: Path | None) -> Config` (missing file → defaults with `budget=0`); `DocRef(document: str, workspace: str, element: str)`; `parse_url(url: str) -> DocRef` (raises `ValueError` on non-Onshape-document URLs).

- [ ] **Step 1:** `git init`; write `pyproject.toml` (deps above, `pytest` in dev extras, entry point `onshape-duet = onshape_duet.server:main`) and the MIT `LICENSE`.
- [ ] **Step 2: Write failing tests.** `test_default_budget_is_zero`: `load_config(None).budget == 0`. `test_toml_sets_budget`: a TOML with `budget = 40` loads to `40`. `test_parse_url`: `parse_url("https://cad.onshape.com/documents/d1/w/w2/e/e3") == DocRef("d1","w2","e3")`; `test_parse_bad_url_raises`: `parse_url("https://example.com")` raises `ValueError`.
- [ ] **Step 3:** Run `pytest tests/test_config.py tests/test_docref.py -v`; expect FAIL (import errors).
- [ ] **Step 4:** Implement `config.py` (stdlib `tomllib`) and `docref.py` (regex on `/documents/<d>/(w|v)/<w>/e/<e>`; version URLs (`/v/`) are accepted and treated as read-only later).
- [ ] **Step 5:** Run the tests; expect PASS. Commit `chore: scaffold project, config, docref`.

### Task 2: Request budget ledger

**Files:** Create `budget.py`; Test `tests/test_budget.py`

**Interfaces:**
- Produces: `class BudgetExceeded(Exception)`; `Ledger(path: Path, limit: int)` with `used: int`, `remaining: int`, `charge(n: int = 1, reason: str = "") -> None` (persists the new total to disk **before returning**; raises `BudgetExceeded` if `used + n > limit`, leaving the file unchanged).

- [ ] **Step 1: Failing tests.** `test_charge_counts`; `test_limit_zero_refuses_first_charge` (message contains `"budget"`); `test_persists_across_instances` (new `Ledger` on same path sees `used == 2`); `test_corrupt_file_fails_closed` (garbage in file → `BudgetExceeded`, not a reset to 0); `test_unwritable_path_fails_closed`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement with a JSON file, atomic write via temp file + `os.replace`; any read/write error raises `BudgetExceeded("ledger unusable: ...")`.
- [ ] **Step 4:** Run; expect PASS. Commit `feat: request budget ledger`.

### Task 3: Domain model, Backend protocol and fake backend

**Files:** Create `model.py`, `backends/base.py`, `backends/fake.py`; Test `tests/test_fake_backend.py`

**Interfaces:**
- Produces (`model.py`): `Feature(id: str, type: str, name: str, status: str, params: dict)`; `Variable(name: str, expression: str)`; `FeatureList(microversion: str, features: list[Feature])`.
- Produces (`Backend` Protocol): `get_microversion(ref) -> str`; `list_features(ref) -> FeatureList`; `get_variables(ref) -> list[Variable]`; `add_feature(ref, feature_json: dict, expected_microversion: str) -> Feature`; `set_variable(ref, name: str, expression: str, expected_microversion: str) -> str` (returns new microversion). Writes raise `StaleVersion` when `expected_microversion` differs from current.
- `FakeBackend` keeps state in memory, bumps microversion on every write, and has `simulate_human_edit(ref)` which bumps it without Claude's involvement. It does **not** charge the ledger; the real backend does (Task 4).

- [ ] **Step 1: Failing tests.** `test_write_bumps_microversion`; `test_stale_write_raises` (read version, `simulate_human_edit`, write with old version → `StaleVersion`); `test_added_feature_is_listed`.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). Commit `feat: backend protocol and fake backend`.

### Task 4: Onshape HTTP backend (budgeted)

**Files:** Create `backends/onshape.py`; Test `tests/test_onshape_backend.py` (uses `httpx.MockTransport`)

**Interfaces:**
- Consumes: `Ledger.charge`, `Backend`, `Config`.
- Produces: `OnshapeBackend(config: Config, ledger: Ledger, transport: httpx.BaseTransport | None = None)` implementing `Backend`; `sign(method, path, query, content_type, date, nonce, access, secret) -> str` returning the `Authorization` header value (`On <access>:HmacSHA256:<base64 sig>`).

- [ ] **Step 1: Failing tests.** `test_sign_is_deterministic_for_fixed_inputs` (assert header prefix `"On key:HmacSHA256:"` and stable value); `test_every_request_charges_before_sending` (transport asserts ledger `used` already incremented when the request arrives); `test_budget_zero_sends_nothing` (transport never called, `BudgetExceeded`); `test_redirect_hop_is_charged` (302 then 200 → `used == 2`); `test_stale_write_maps_to_StaleVersion` (mock 409/412 response).
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement: Onshape API-key HMAC scheme per Onshape docs (string-to-sign is lowercase `method\nnonce\ndate\ncontent-type\npath\nquery\n`), `follow_redirects=False` with manual hop handling so each hop is charged. Endpoint paths for features, variables and microversion are verified live in Task 12, so isolate them as module constants.
- [ ] **Step 4:** Run; expect PASS. Commit `feat: budgeted onshape backend`.

### Task 5: Document cache keyed by microversion

**Files:** Create `cache.py`; Test `tests/test_cache.py`

**Interfaces:**
- Produces: `DocCache(backend: Backend)` with `read(ref) -> FeatureList` (calls `get_microversion`; if it matches the cached version, returns cached; otherwise refetches `list_features`), `invalidate(ref)`.
- Request accounting: first read = microversion + features; a repeat read with no change = microversion only.

- [ ] **Step 1: Failing tests** with a `FakeBackend` call counter: `test_second_read_skips_feature_fetch`; `test_human_edit_forces_refetch`; `test_own_write_updates_cache_without_refetch` (write returns new microversion, next read costs one microversion call and zero feature fetches).
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). Commit `feat: microversion-keyed cache`.

### Task 6: Outline renderer and `cad_read`

**Files:** Create `outline.py`; Test `tests/test_outline.py`

**Interfaces:**
- Consumes: `FeatureList`, `DofReport` (Task 7; before Task 7 the sketch line says `not assessed`).
- Produces: `render_outline(fl: FeatureList, variables: list[Variable], reports: dict[str, DofReport] | None = None) -> str` — one line per feature: `index. name [type] status`, then the editable numbers; sketches show `dof=N` or `not assessed`; default names (`Extrude 1`, `Sketch 3`) are suffixed `⚠ default name`.

- [ ] **Step 1: Failing tests:** `test_outline_lists_features_in_order`; `test_default_name_is_flagged`; `test_variables_section_lists_expressions`; `test_unknown_feature_type_still_listed` (generic features appear with type and status, no crash).
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). Commit `feat: outline renderer`.

### Task 7: Sketch spec and local DOF solver

**Files:** Create `sketch/spec.py`, `sketch/solver.py`; Test `tests/test_solver.py`

**Interfaces:**
- Produces (`spec.py`): `parse_sketch_spec(spec: dict, variables: dict[str, float]) -> Sketch`; raises `SketchSpecError` naming any unknown entity id, unknown constraint type, or undefined variable. Spec shape: `{"plane": "Front", "entities": [{"id","type": "line|circle|point|rectangle", ...}], "constraints": [{"type","refs":[...]}], "dimensions": [{"type": "length|diameter|distance_h|distance_v","ref","value": number|"#var"}]}`. Constraint types: `coincident, horizontal, vertical, parallel, perpendicular, equal, ground`.
- Produces (`solver.py`): `DofReport(dof: int, redundant: bool, free: list[str], not_assessed: list[str])`; `analyze(sketch: Sketch) -> DofReport`. `free` names entities that can still move (projection onto the Jacobian null space above tolerance 1e-9). Uses `numpy.linalg.matrix_rank` on the numeric Jacobian at the nominal configuration.

- [ ] **Step 1: Failing tests** (exact numbers):
  - `test_rectangle_unconstrained_dof_is_4` (rectangle as four lines with coincident, horizontal, vertical only).
  - `test_rectangle_grounded_dof_is_2`; `test_rectangle_grounded_and_dimensioned_dof_is_0` (60×40).
  - `test_circle_diameter_only_dof_is_2_and_names_circle_in_free`; `test_circle_position_and_diameter_dof_is_0`.
  - `test_duplicate_dimension_is_redundant` (same length dimensioned twice → `redundant is True`, and the report is never `dof == 0 and not redundant`).
  - `test_unknown_ref_error_names_it`; `test_undefined_variable_error_names_it`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement: variables per entity (point 2, line 4, circle 3), one residual row per constraint and dimension; Jacobian by analytic partials. `dof = n_vars - rank(J)`; `redundant = rank(J) < n_rows`.
- [ ] **Step 4:** Run; expect PASS. Commit `feat: sketch spec and DOF solver`.

### Task 8: Sketch serializer and `cad_sketch` (dry-run, version-pinned apply)

**Files:** Create `sketch/serialize.py`, `tools.py` (sketch part); Test `tests/test_cad_sketch.py`

**Interfaces:**
- Consumes: `parse_sketch_spec`, `analyze`, `DocCache`, `Backend.add_feature`.
- Produces: `to_feature_json(sketch: Sketch, name: str) -> dict` (BTMFeature with a BTMSketch-151 body; constraint and dimension JSON ported from cadkit `cadkit_mcp/sketch.py` after the provenance check, with attribution in `NOTICE`); `Context(backend, cache, ledger, config)`; `cad_sketch(ctx, ref, name: str, spec: dict, apply: bool = False) -> dict` returning `{"applied": bool, "dof": int, "findings": [...], "requests": int, "budget_left": int}`.

- [ ] **Step 1: Failing tests:** `test_dry_run_sends_no_write` (fake backend write count 0, `applied is False`); `test_apply_adds_feature_with_given_name`; `test_apply_after_human_edit_is_refused_stale` (re-read required; nothing written); `test_response_reports_requests_and_budget_left`; `test_serialized_json_contains_every_constraint_and_dimension` (counts match the spec).
- [ ] **Step 2–4:** Run (FAIL), implement (lint integration comes in Task 9; here `findings` is `[]`), run (PASS). Commit `feat: cad_sketch with dry run and version pin`.

### Task 9: Editability lint

**Files:** Create `lint.py`; Modify `tools.py` (call lint before apply); Test `tests/test_lint.py`

**Interfaces:**
- Produces: `Finding(level: Literal["refuse","warn"], code: str, message: str)`; `lint_sketch(spec: dict, report: DofReport, name: str) -> list[Finding]`.
- Rules: `SKETCH_FLOATING` (refuse: `dof > 0`, no dimensions, and no `ground` constraint); `SKETCH_PARTIAL` (warn: `dof > 0` otherwise; message lists `report.free`); `SKETCH_REDUNDANT` (warn); `DEFAULT_NAME` (warn: name matches `^(Sketch|Extrude|Fillet|Revolve) \d+$`); `MAGIC_NUMBER` (warn: the same literal appears in two or more dimensions with no variable reference). `cad_sketch` with any refuse finding returns `applied: False` even when `apply=True`.

- [ ] **Step 1: Failing tests:** `test_floating_sketch_is_refused_even_with_apply` (nothing written); `test_partial_sketch_warns_and_applies` (message names the free circle); `test_default_name_warns`; `test_repeated_literal_flags_magic_number`; `test_fully_defined_named_sketch_has_no_findings`.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). Commit `feat: editability lint on sketches`.

### Task 10: `cad_variable`

**Files:** Modify `tools.py`; Test `tests/test_cad_variable.py`

**Interfaces:**
- Produces: `cad_variable(ctx, ref, name: str, expression: str, apply: bool = False) -> dict` (upsert-by-name; no duplicates; dry run shows old and new expression; same response fields as `cad_sketch`). Names must match `^[a-z][a-z0-9_]*$` else an error that says so.

- [ ] **Step 1: Failing tests:** `test_upsert_updates_existing_not_duplicate`; `test_dry_run_shows_old_and_new`; `test_bad_name_rejected`; `test_stale_version_refused`.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). Commit `feat: cad_variable`.

### Task 11: MCP server, `cad_status`, `cad_read`

**Files:** Create `server.py`; Modify `tools.py`; Test `tests/test_server.py`; Create `docs/agent-guide.md` (the workflow snippet: status → read → dry run → show owner → apply → read)

**Interfaces:**
- Produces: `cad_status(ctx) -> dict` (budget, used, remaining, keys found: bool, config path; zero requests); `cad_read(ctx, ref_or_url: str) -> str` (outline from cache plus a DOF report for each sketch that parses; unparseable sketches say `not assessed`); `main()` registers `cad_status`, `cad_read`, `cad_sketch`, `cad_variable` with FastMCP over stdio.

- [ ] **Step 1: Failing tests:** `test_status_costs_zero_requests`; `test_read_with_budget_zero_refuses_and_names_config_key`; `test_server_registers_four_tools` (list tool names from the FastMCP instance).
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). Commit `feat: MCP server with status and read`.

### Task 12: Live verification and call-count record (manual, needs keys and a budget)

**Files:** Create `tests/live/smoke.py` (not collected by default), `docs/live-findings.md`

- [ ] **Step 1:** Owner sets `budget = 40` in the config and an empty Part Studio URL. Script runs: `cad_status` → `cad_read` → `cad_variable` (apply) → `cad_sketch` 60×40 rectangle plus 10 mm circle (apply) → `cad_read`, asserting the request count after each step.
- [ ] **Step 2:** Confirm in the Onshape UI that the sketch is fully defined, dimensions show their values, and changing the variable rebuilds. Record the request counts in `docs/live-findings.md`.
- [ ] **Step 3:** Fix any endpoint-path or JSON-shape mismatches the live run exposes in `backends/onshape.py` and `sketch/serialize.py`, adding a regression test for each. Commit `test: live smoke and findings`.

---

## Self-Review

- **Spec coverage:** budget ledger (§7.5) T2/T4; cache by microversion (§7.5) T5; outline and human→Claude read (§7.6) T6/T11; sketch gate (§7.4) T7; lint tiers (§7.3) T9; dry-run default and version pinning (§2, §7.2) T8/T10; fake-backend tests and live smoke (§9) T3/T12. Deferred to later plans: typed features, `cad_find`, generic layer, lifecycle, assemblies, benchmark harness.
- **Type consistency:** `Backend`, `FeatureList`, `DofReport`, `Finding` and `Context` are defined in Tasks 3, 7, 9 and 8 and used with those names later.
- **Review Focus** lines are each pinned to a test in T2 (fail-closed ledger), T4 (budget 0), T7 (redundant, unknown refs), T8 (stale write) and T11 (budget-0 read message).
