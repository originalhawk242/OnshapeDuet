# Generic Feature Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Claude look up, inspect, add and update any native Onshape Part Studio feature through four tools (`cad_specs`, `cad_inspect`, `cad_feature_add`, `cad_feature_update`). The tools are spec-validated, dry-run by default, pinned to the version last read, and linted.

**Architecture:** A new package `src/onshape_duet/generic/` holds pure logic: spec parsing, parameter building, validation, lint, update merge and rendering. The tool bodies live in `tools.py` beside the existing tools and share `Context`, `_reply`, `_locked` and milestone 4's `_apply_feature`. `_apply_feature` gains an update mode. Features keep their raw Onshape JSON in the cache, so updates can send back everything they did not change.

**Tech Stack:** Python 3.11+, the `mcp` SDK (`MCPServer`), httpx, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-24-generic-feature-layer-design.md` (parent: `docs/superpowers/specs/2026-11-23-onshape-duet-design.md`)

## Global Constraints

- Python 3.11+, MIT. No GPL code copied (parent §5).
- No tool takes FeatureScript text. Query parameters are built only via milestone 4's `compile_selector` + `p_query`.
- Native features only. A spec with a non-empty `namespace` is not offered (`CUSTOM_FEATURE`).
- Every document- or caller-controlled string placed in a message goes through `textsafe.clean`.
- Writes: dry run unless `apply=True`. Apply needs a prior `cad_read` and no `refuse` finding, and goes through `_apply_feature`. Apply costs 2 requests (+1 if specs are not cached).
- Untouched parameters on update are sent back byte-for-byte as read.
- Shared files: `model.py` (only `Feature.raw`), `backends/onshape.py` (`_parse_feature` fills `raw`, plus two new methods), `backends/fake.py`, `backends/base.py`, `tools.py`, `server.py` (append-only). Do **not** edit `lint.py`, `features/`, or `_apply_feature`'s add behaviour.
- Base branch: `generic-features` in worktree `/home/varun/Documents/OnshapeDuet-generic`, on top of milestone 4's `f5e8809`.
- Feature types with milestone-4 typed tools (keep in sync with that plan): `extrude, revolve, fillet, chamfer, hole, shell, booleanBodies, linearPattern, circularPattern, mirror, cPlane`. Refused outright in generic tools: `newSketch, assignVariable`.
- Unverified Onshape shapes (featurespecs; update with sent-back raw) are isolated in `generic/specs.py` and `OnshapeBackend.update_feature`, and listed in `docs/live-checklist.md`.

## Review Focus

1. **A cached feature with no raw JSON.** This happens to a variable spliced in by `cad_variable`, or to anything built by `make_variable_feature`. `cad_feature_update` must refuse with "re-read with cad_read first" and never send a feature rebuilt from `params`. Test in Task 7: `test_update_refuses_feature_without_raw`.
2. **Spec fetch fails** (budget exhausted, HTTP error, malformed JSON) during a dry run. Expected: a clean `error` reply, no exception, and no partial entry in `ctx.specs`, so the next call retries. Test in Task 2: `test_specs_fetch_failure_not_cached`.
3. **Near-miss values:** enum `"blind"` for `"BLIND"`, a dict or `None` where a string is expected, a 10 000-char string, a string with a newline. Expected: `BAD_VALUE` naming the parameter (and the exact option for case misses), never an exception. Test in Task 4: `test_build_rejects_near_misses`.
4. **Human edit between dry run and apply.** Apply builds from `snapshot_for_apply`, so it refuses as stale with zero POSTs. Tests in Tasks 6 and 7: `test_add_apply_after_human_edit_refused`, `test_update_apply_after_human_edit_refused`.
5. **Duplicate feature names in a human-made doc.** `cad_inspect` and `cad_feature_update` refuse, listing the matching ids, and never pick one. A name that equals another feature's id resolves by id first. Test in Task 3: `test_find_feature_ambiguous_and_id_first`.

---

### Task 1: Keep raw feature JSON

**Files:**
- Modify: `src/onshape_duet/model.py` (`Feature`)
- Modify: `src/onshape_duet/backends/onshape.py` (`_parse_feature`)
- Modify: `src/onshape_duet/backends/fake.py` (`add_feature` stores raw)
- Test: `tests/test_generic_raw.py`

**Interfaces:**
- Produces: `Feature.raw: dict = field(default_factory=dict)`, the feature's JSON exactly as Onshape returned it (`{}` when unknown). `params` is unchanged. In the fake backend, a feature added from a JSON with `parameters` has `raw == feature_json` plus `featureId`.

- [ ] **Step 0: Set up the worktree venv.** `cd /home/varun/Documents/OnshapeDuet-generic && python3 -m venv .venv && .venv/bin/pip install -q -e '.[dev]' && .venv/bin/pytest -q`. Expected: all existing tests pass.
- [ ] **Step 1: Write failing tests**

```python
def test_parse_feature_keeps_raw():
    f = {"featureId": "F1", "featureType": "extrude", "name": "Base",
         "parameters": [{"btType": "BTMParameterQuantity-147", "parameterId": "depth", "expression": "10 mm"}]}
    feat = _parse_feature(f, {})
    assert feat.raw == f and feat.raw is not f      # a copy, not the response object
    assert feat.params == {"depth": "10 mm"}

def test_feature_default_raw_is_empty():
    assert Feature("F1", "extrude", "x", "OK").raw == {}

def test_fake_add_feature_keeps_raw():
    fb = FakeBackend(); fj = {"featureType": "extrude", "name": "B", "parameters": []}
    r = fb.add_feature(REF, fj, fb.get_microversion(REF))
    assert r.feature.raw["featureType"] == "extrude" and r.feature.raw["featureId"] == r.feature.id
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_generic_raw.py -q`. Expected: FAIL (`raw` attribute missing).
- [ ] **Step 3: Implement.** Add the defaulted `raw` field last in `Feature`. `_parse_feature` passes `raw=copy.deepcopy(f)`. Fake `add_feature` sets `raw={**copy.deepcopy(feature_json), "featureId": new_id}` when `parameters` is a list, else `{}`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: keep raw feature JSON on Feature (generic layer)`. Send the SHA to session `onshapeduet-7b`. It rebases its `flatten_params` change onto this.

---

### Task 2: Feature specs: parse, fetch, cache

**Files:**
- Create: `src/onshape_duet/generic/__init__.py`, `src/onshape_duet/generic/specs.py`
- Create: `tests/fixtures/featurespecs_UNVERIFIED.json` (hand-written from the OpenAPI schema; replaced by a live capture later)
- Modify: `backends/base.py`, `backends/onshape.py`, `backends/fake.py`, `tools.py` (`Context`)
- Test: `tests/test_generic_specs.py`

**Interfaces:**
- Produces, in `generic/specs.py`:
  - `@dataclass(frozen=True) ParamSpec`: `id: str, name: str, kind: str, quantity_type: str = "", units: str = "", default: object = None, options: tuple[str, ...] = (), enum_name: str = "", enum_namespace: str = "", items: tuple["ParamSpec", ...] = (), conditional: bool = False, condition: str = ""`. `kind` is one of `"quantity", "enum", "boolean", "string", "query", "feature_list", "array", "unsupported"`.
  - `@dataclass(frozen=True) FeatureSpec`: `type: str, name: str, params: tuple[ParamSpec, ...]`, plus `def param(self, pid: str) -> ParamSpec | None`.
  - `parse_feature_specs(data: dict) -> list[FeatureSpec]`. Native specs only (`namespace` empty or missing), sorted by `type`. Raises `RuntimeError("unexpected Onshape response: ...")` on a malformed shape.
  - `KIND_BY_BTTYPE: dict[str, str]` maps each btType prefix to a kind: `BTParameterSpecQuantity-173`→quantity, `BTParameterSpecEnum-171`→enum, `BTParameterSpecBoolean-170`→boolean, `BTParameterSpecString-175`→string, `BTParameterSpecQuery-174`→query, `BTParameterSpecFeatureList-703`→feature_list, `BTParameterSpecArray-2600`→array. Everything else (lookup table, reference*, appearance, material, derived, foreign id, nullable quantity, multi-enum, ...) maps to unsupported.
  - Field sources (from Onshape OpenAPI, unverified live): spec `featureType`, `featureTypeName`, `namespace`, `parameters`. Param `parameterId`, `parameterName`, `btType`, `quantityType`, `defaultValue` (a `BTMParameter-1`; flatten with `model.flatten_params([d])`), `visibilityCondition` (anything other than absent/`BTParameterVisibilityAlwaysShown-5487` sets `conditional=True`; `condition` is a short summary of a `BTParameterVisibilityOnEqual-180` as `"<parameterId> == <value>"`, else `"conditional"`). Quantity `ranges[0].units` and `ranges[0].defaultValue`. Enum `options`, `enumName`, `namespace`. Array item params come from the array spec's `parameters` (absent from OpenAPI; assumed, live check 1).
- Produces, on the backends: `get_feature_specs(ref: DocRef) -> list[FeatureSpec]`. Onshape: 1 GET `features_path(ref).removesuffix("/features") + "/featurespecs"`. Fake: returns `parse_feature_specs(self.specs_json)`, where `FakeBackend.specs_json: dict` defaults to the fixture's contents; counted via `_enter("get_feature_specs")`.
- Produces, on `Context`: `specs: dict[DocRef, list[FeatureSpec]]` (default empty) and `def feature_specs(self, ref) -> list[FeatureSpec]`, which fetches once per ref and stores only on success.

- [ ] **Step 1: Write the fixture.** Include 6 native specs (`extrude` with quantity `depth` LENGTH "mm" conditional on `endBound == BLIND`, enum `endBound` options `BLIND, THROUGH_ALL, UP_TO_NEXT`, query `entities`, boolean `oppositeDirection`; `fillet` with query `entities` and quantity `radius`; `linearPattern` with feature_list `instanceFunction` and integer quantity `instanceCount`; `cPlane` with quantity `offset` LENGTH "mm"; a type with an array param `holeTable`-like `items` containing a quantity and a string; `newSketch`), one custom spec with `namespace: "e123::m456"`, and one param of `BTParameterSpecLookupTablePath-761`. Mark the top-level key `"_note": "UNVERIFIED hand-written from OpenAPI; replace with live capture"`.
- [ ] **Step 2: Write failing tests**

```python
def test_parse_native_only_and_sorted():
    specs = parse_feature_specs(FIXTURE)
    types = [s.type for s in specs]
    assert types == sorted(types) and all("::" not in t for t in types)
    assert not any(s.type == CUSTOM_TYPE for s in specs)

def test_param_kinds_and_fields():
    ex = {s.type: s for s in parse_feature_specs(FIXTURE)}["extrude"]
    d = ex.param("depth")
    assert (d.kind, d.quantity_type, d.units, d.conditional) == ("quantity", "LENGTH", "mm", True)
    assert d.condition == "endBound == BLIND"
    e = ex.param("endBound")
    assert e.kind == "enum" and e.options == ("BLIND", "THROUGH_ALL", "UP_TO_NEXT")
    assert ex.param("nope") is None

def test_unsupported_kind_and_array_items(): ...   # lookup-table param → "unsupported"; array → items parsed

def test_malformed_specs_raise():
    with pytest.raises(RuntimeError): parse_feature_specs({"featureSpecs": "x"})

def test_specs_cached_per_ref(ctx_fake):            # 2 calls → 1 get_feature_specs
def test_specs_fetch_failure_not_cached(ctx_fake):  # Review Focus 2: backend raises once → error; next call fetches
def test_onshape_get_feature_specs_path():          # scripted transport sees GET .../e/<id>/featurespecs, 1 ledger charge
```

- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_generic_specs.py -q`. Expected: FAIL (module missing).
- [ ] **Step 4: Implement** `generic/specs.py`, the three backend methods, the `Backend` protocol entry, and `Context.specs` / `feature_specs`.
- [ ] **Step 5: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 6: Commit** `feat(generic): parse and cache native feature specs`.

---

### Task 3: Read tools: `cad_specs` and `cad_inspect`

**Files:**
- Create: `src/onshape_duet/generic/lookup.py`, `src/onshape_duet/generic/render.py`
- Modify: `tools.py` (two tools), `server.py` (append two registrations)
- Test: `tests/test_generic_read.py`

**Interfaces:**
- Consumes: `Context.feature_specs`, `Context.snapshot`, `FeatureSpec`, `Feature.raw`.
- Produces:
  - `generic/lookup.py`: `class LookupError_(ValueError)`, `find_feature(fl: FeatureList, key: str) -> Feature`. An exact id match wins. Otherwise an exact unique name. Ambiguous → error listing up to 10 ids; none → error listing up to 10 names close by `difflib.get_close_matches`. (When milestone 4's `features/refs.py` resolver lands, check it has these semantics. If it does, switch `find_feature` to call it in a follow-up commit.)
  - `generic/render.py`: `render_spec_index(specs) -> str` (one `type: Display name` line per spec); `render_spec(spec) -> str` (one line per param: `id (Name) kind[ unit/options] default=… [only when …]`, array items indented two spaces); `render_feature(f: Feature, spec: FeatureSpec | None) -> str` (header `name [type] status id`, then one line per raw parameter: quantity→expression, enum→value, query→`queryString` or `N geometry ids (set in Onshape)`, feature list→ids, array→`[i]` items indented, other→`<btType>`). Every string goes through `clean`.
  - `tools.cad_specs(ctx, ref, feature_type: str | None = None) -> dict`: `{"specs": str}` or `{"error": str}`, plus `requests` and `budget_left` via `_reply` (use `applied=False`; it never writes). An unknown or custom type gives an error naming up to 5 close native types.
  - `tools.cad_inspect(ctx, ref, feature: str) -> dict`: `{"feature": str}` or `{"error": str}`. Uses `ctx.snapshot(ref)`. Specs are only fetched if already cached (it must not cost a request just to label kinds; without specs, rendering uses raw btTypes).
  - `server.py`: `cad_specs(url, feature_type: str | None = None)` and `cad_inspect(url, feature: str)`, with docstrings stating the costs (`cad_specs`: 1 request first time, then 0; `cad_inspect`: 0 after a read).

- [ ] **Step 1: Write failing tests**

```python
def test_cad_specs_index_then_one_type(ctx_fake):
    out = cad_specs(ctx, REF); assert "extrude: Extrude" in out["specs"] and out["requests"] == 1
    out = cad_specs(ctx, REF, "extrude"); assert "depth" in out["specs"] and out["requests"] == 0

def test_cad_specs_custom_or_unknown_type_errors(ctx_fake):   # error names close native types
def test_cad_inspect_renders_all_params_zero_requests(ctx_fake):  # after cad_read: requests == 0; shows query as "2 geometry ids (set in Onshape)"
def test_find_feature_ambiguous_and_id_first():               # Review Focus 5
    fl = FeatureList("mv", [Feature("F1", "extrude", "Body", "OK"), Feature("F2", "fillet", "Body", "OK"),
                            Feature("F3", "fillet", "F1", "OK")])
    with pytest.raises(ValueError, match="F1.*F2"): find_feature(fl, "Body")
    assert find_feature(fl, "F1").id == "F1"
def test_render_cleans_hostile_names():                       # name with newline renders on one line
def test_server_registers_generic_read_tools():               # tool names present in build_server(ctx)
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_generic_read.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** lookup, render, the two tools (`@_locked`), and the registrations (parse the URL as the existing tools do).
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(generic): cad_specs and cad_inspect`.

---

### Task 4: Parameter builders

**Files:**
- Create: `src/onshape_duet/generic/build.py`
- Test: `tests/test_generic_build.py`

**Interfaces:**
- Consumes: `ParamSpec`, `FeatureList`, `find_feature`.
- Produces:
  - `class ParamError(ValueError)` with `.code` (one of `BAD_VALUE, MISSING_UNIT, RAW_GEOMETRY_ID, UNSUPPORTED_PARAM_KIND`) and `.param`.
  - `build_param(spec: ParamSpec, value: object, fl: FeatureList, *, current: dict | None = None) -> dict` returns one BTM parameter JSON with `parameterId` set. `current` is the feature's existing raw parameter with this id (updates only):
    - quantity → `BTMParameterQuantity-147` `{"expression": value}`. The value must be a printable string of at most 200 chars. A bare int/float (not bool) is allowed only when `quantity_type` is `INTEGER` or `REAL` (becomes `str(value)`). For a quantity with `units`, a string that is only a number (regex `^\s*[-+]?\d+(\.\d+)?\s*$`) is `MISSING_UNIT`.
    - enum → `BTMParameterEnum-145` `{"value", "enumName", "namespace"}`, from the spec. A value not in `options` is `BAD_VALUE`; if `value.upper()` matches an option, the message names it.
    - boolean → `BTMParameterBoolean-144`; strictly `bool`.
    - string → `BTMParameterString-149`; printable, ≤ 200 chars.
    - query → selector dict or list of them, built as `p_query(spec.id, [compile_selector(sel, fl, spec.id) for sel in sels])`. Import `onshape_duet.features.refs` lazily inside the function; on `ImportError` raise `UNSUPPORTED_PARAM_KIND` "selectors not available yet". Any dict containing a `deterministicIds` or `btType` key anywhere is `RAW_GEOMETRY_ID`, checked before the import.
    - feature_list → list of names/ids, resolved with `find_feature`, → `BTMParameterFeatureList-1749` `{"featureIds": [...]}`.
    - array, list value → list of item maps → `BTMParameterArray-2025` `{"items": [{"btType": "BTMArrayParameterItem-1843", "parameters": [build_param(item_spec, v, fl) ...]}]}`. An unknown item key is `BAD_VALUE`. If `current` contains `deterministicIds` anywhere → `RAW_GEOMETRY_ID` ("... use the items patch form").
    - array, patch value `{"items": {"<index>": {pid: value}}}` (only with `current`; without it → `BAD_VALUE`): deep-copy `current`, and for each named item build the named params and replace them in that item by `parameterId` (append if absent). Everything else stays identical. A non-integer or out-of-range index → `BAD_VALUE`.
    - unsupported → `UNSUPPORTED_PARAM_KIND` naming the spec btType kind.
  - Builders never raise anything but `ParamError`.

- [ ] **Step 1: Write failing tests** (one per kind plus):

```python
def test_quantity_forms():
    assert build_param(DEPTH, "10 mm", FL)["expression"] == "10 mm"
    assert build_param(DEPTH, "#plate_w / 2", FL)["expression"] == "#plate_w / 2"
    assert build_param(COUNT, 4, FL)["expression"] == "4"
    with pytest.raises(ParamError) as e: build_param(DEPTH, "10", FL)
    assert e.value.code == "MISSING_UNIT"

def test_build_rejects_near_misses():   # Review Focus 3
    for spec, bad in [(END_BOUND, "blind"), (NAME_STR, None), (NAME_STR, {"a": 1}),
                      (NAME_STR, "x" * 10_000), (NAME_STR, "a\nb"), (FLAG, "true"), (DEPTH, True)]:
        with pytest.raises(ParamError) as e: build_param(spec, bad, FL)
        assert e.value.code == "BAD_VALUE" and e.value.param == spec.id
    with pytest.raises(ParamError, match="BLIND"): build_param(END_BOUND, "blind", FL)

def test_raw_geometry_id_refused():
    with pytest.raises(ParamError) as e:
        build_param(ENTITIES, {"btType": "BTMIndividualQuery-138", "deterministicIds": ["JHD"]}, FL)
    assert e.value.code == "RAW_GEOMETRY_ID"

def test_query_without_selectors_module(monkeypatch):  # simulate ImportError → UNSUPPORTED_PARAM_KIND
def test_feature_list_resolves_names():                 # ["Base"] → featureIds ["F1"]
def test_array_items_built_and_unknown_key_refused():
def test_array_patch_keeps_sibling_geometry_ids():   # patch item 1's quantity; item 0's deterministicIds identical
def test_array_list_replace_refused_over_geometry_ids():
def test_array_patch_bad_index_and_patch_without_current():
def test_unsupported_kind_refused():
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_generic_build.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `generic/build.py`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(generic): spec-driven parameter builders`.

---

### Task 5: Validation and lint

**Files:**
- Create: `src/onshape_duet/generic/validate.py`, `src/onshape_duet/generic/lint.py`
- Test: `tests/test_generic_validate.py`

**Interfaces:**
- Consumes: `build_param`, `ParamError`, `FeatureSpec`, `lint.Finding` (import only; do not modify `lint.py`), `variables_from_features`.
- Produces:
  - `validate.TYPED_TOOL_REQUIRED = {"newSketch": "cad_sketch", "assignVariable": "cad_variable"}` and `validate.TYPED_TOOL_AVAILABLE = {"extrude": "cad_extrude", "revolve": "cad_revolve", "fillet": "cad_fillet", "chamfer": "cad_chamfer", "hole": "cad_hole", "shell": "cad_shell", "booleanBodies": "cad_boolean", "linearPattern": "cad_pattern", "circularPattern": "cad_pattern", "mirror": "cad_mirror", "cPlane": "cad_plane"}`.
  - `@dataclass Prepared`: `parameters: list[dict]` (BTM JSON, in the order given), `readable: dict[str, object]` (what Claude sent), `defaults_applied: dict[str, object]` (spec params not given, with their spec default; conditional ones are excluded), `findings: list[Finding]`.
  - `validate.prepare(spec_by_type: dict[str, FeatureSpec], feature_type: str, params: dict, fl: FeatureList, *, mode: Literal["add", "update"], self_index: int | None = None, current_raw: dict | None = None) -> Prepared`. In update mode, each param is built with `current=` the raw parameter of the same id from `current_raw["parameters"]`. Collects **all** problems as findings rather than stopping at the first. Refuse codes: `CUSTOM_FEATURE` (the type contains `::`, or has no native spec while matching a custom one; otherwise `UNKNOWN_FEATURE_TYPE`), `TYPED_TOOL_REQUIRED`, `UNKNOWN_PARAM` (message: close ids via difflib), every `ParamError.code`, and `SELF_REFERENCE` (update: a feature_list id whose index in `fl` is ≥ `self_index`). `params` that is not a dict with str keys is a single `BAD_VALUE`.
  - `generic/lint.py: lint_generic(feature_type, name: str | None, params: dict, spec: FeatureSpec, fl: FeatureList, *, mode) -> list[Finding]`. WARN codes:
    - `DEFAULT_NAME`: `name` fullmatches `re.escape(spec.name) + r" \d+"`. Add only.
    - `MAGIC_NUMBER`: the same normalised literal quantity (`"10 mm"` == `"10mm"`) in ≥ 2 quantity params, including inside array items.
    - `UNKNOWN_VARIABLE`: a `#name` not among `variables_from_features(fl)`.
    - `HIDDEN_PARAM`: a given conditional param whose `"p == V"` condition is contradicted by a value given in the same call (message says "not evaluated exactly").
    - `TYPED_TOOL_AVAILABLE`: add only.

- [ ] **Step 1: Write failing tests**

```python
def test_collects_all_refusals():
    p = prepare(SPECS, "extrude", {"depht": "1 mm", "endBound": "blind"}, FL, mode="add")
    codes = {f.code for f in p.findings if f.level == "refuse"}
    assert codes == {"UNKNOWN_PARAM", "BAD_VALUE"}
    assert "depth" in next(f.message for f in p.findings if f.code == "UNKNOWN_PARAM")

def test_typed_tool_required_for_sketch_and_variable():
def test_custom_and_unknown_type():
def test_defaults_applied_excludes_conditional():
def test_self_reference_on_update():          # feature at index 2 naming index 2 or 3 → refuse; index 1 ok
def test_lint_default_name_magic_unknown_var_typed_available():
def test_hidden_param_warn():                 # endBound THROUGH_ALL + depth given → HIDDEN_PARAM
def test_params_not_a_dict():
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_generic_validate.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(generic): validation and lint for generic writes`.

---

### Task 6: `cad_feature_add`

**Files:**
- Modify: `tools.py`, `server.py` (append)
- Test: `tests/test_cad_feature_add.py`

**Interfaces:**
- Consumes: `Context.feature_specs`, `snapshot` / `snapshot_for_apply`, `prepare`, `lint_generic`, `_apply_feature(ctx, ref, start, feature_json, extra, what)`.
- Produces: `tools.cad_feature_add(ctx, ref, feature_type: str, name: str, params: dict, apply: bool = False) -> dict`.
  - Name: printable, 1–64 chars, else error before anything else.
  - Order: name check → `apply` without read → specs → snapshot (`snapshot_for_apply` when applying, stale → `_refusal`) → `prepare` + `lint_generic`.
  - `extra = {"feature_type", "name", "params": readable, "defaults_applied", "findings", "based_on_version"}`.
  - Any refuse finding → `_reply(False, error="refused, nothing written: " + "; ".join(code: message))`, the same wording as `cad_sketch`.
  - Dry run → `_reply(False, **extra)`.
  - Apply → `_apply_feature(ctx, ref, start, {"btType": "BTMFeature-134", "featureType": t, "name": name, "parameters": prepared.parameters}, extra, f"the {spec.name} feature")`.
  - `server.py`: `cad_feature_add(url, feature_type: str, name: str, params: dict, apply: StrictBool = False)`. The docstring says: dry run default; geometry via selector dicts (same DSL as `cad_find`); use typed tools first; cost.

- [ ] **Step 1: Write failing tests**

```python
def test_add_dry_run_costs_specs_only(ctx_fake):          # after cad_read: requests == 1 first time, 0 second
def test_add_refused_writes_nothing(ctx_fake):            # UNKNOWN_PARAM → applied False, fake.calls["add_feature"] == 0
def test_add_apply_costs_two_and_sends_btm_json(ctx_fake):
    cad_read(ctx, REF); cad_specs(ctx, REF)
    out = cad_feature_add(ctx, REF, "cPlane", "Mid plane", {"offset": "5 mm"}, apply=True)
    assert out["applied"] is True and out["requests"] == 2
    assert fake.last_feature_json["parameters"][0]["btType"] == "BTMParameterQuantity-147"
def test_add_apply_without_read_refused(ctx_fake):
def test_add_apply_after_human_edit_refused(ctx_fake):    # Review Focus 4: simulate_human_edit → stale, 0 POSTs
def test_add_regen_error_reported(ctx_fake):              # fake.next_status = "ERROR" → applied True + REGEN_STATUS finding
def test_add_bad_name(ctx_fake):
```

(Add `FakeBackend.last_feature_json` in this task if milestone 4 did not; check `f5e8809` first.)

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_cad_feature_add.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the tool and the registration.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(generic): cad_feature_add`.

---

### Task 7: `cad_feature_update` (merge, backend, `_apply_feature` update mode)

**Files:**
- Create: `src/onshape_duet/generic/update.py`
- Modify: `tools.py` (`_apply_feature` keyword, new tool), `backends/base.py`, `backends/onshape.py`, `backends/fake.py`, `server.py` (append)
- Test: `tests/test_generic_update.py`, `tests/test_cad_feature_update.py`

**Interfaces:**
- Consumes: `Feature.raw`, `find_feature`, `build_param`, `prepare(mode="update", self_index=...)`, `lint_generic(mode="update")`.
- Produces:
  - `generic/update.py: merge(raw: dict, built: list[dict]) -> tuple[dict, list[dict]]`, where `built` is `Prepared.parameters`. It returns `(new_raw, changes)`. It deep-copies `raw` and replaces the parameter with the same `parameterId` in place (same list position). A named param not present in `raw` is appended. `changes` is `[{"parameterId", "before", "after"}]`, using readable values via `render`'s per-param formatter. Every other key and parameter is left identical.
  - Array patches are already built by Task 4 (`build_param(..., current=)`). For arrays, `changes` lists one entry per changed item param as `"<arrayId>[i].<pid>"`.
  - `Backend.update_feature(ref, feature_id: str, feature_json: dict, expected_microversion: str) -> WriteResult`. Onshape: `_write(feature_update_path(ref, feature_id), {"feature": feature_json, "serializationVersion": SERIALIZATION_VERSION}, expected)`, parsed like `add_feature`. Fake: replaces the feature with the same id (raw = sent JSON, params = `flatten_params`), honours `next_status`, and bumps the version.
  - `_apply_feature(..., what, *, feature_id: str | None = None)`. With `feature_id`, call `update_feature`. After an exact success, if the feature is the last in the cached entry at `expected`, splice and advance `last_read` as add does. Otherwise call `ctx.lose_track(ref, _MIDTREE)` with `_MIDTREE = "features after this one may have changed status; re-read with cad_read before the next write"` and set `note` to it. With no `feature_id`, add behaviour is unchanged (the existing tests prove it).
  - `tools.cad_feature_update(ctx, ref, feature: str, params: dict, apply: bool = False) -> dict`. Steps: resolve via `find_feature` on the snapshot. If `raw` is empty, error `"no full copy of this feature; re-read with cad_read first"`. A type in `TYPED_TOOL_REQUIRED` or with no native spec is refused. Then `prepare(mode="update", self_index=index, current_raw=f.raw)`, then `merge(f.raw, prepared.parameters)`. `extra = {"feature", "feature_id", "changes", "findings", "based_on_version"}`. An empty `params` is an error. Dry run/refuse/apply as in Task 6. The JSON sent is `new_raw` with `featureId` kept.
  - `server.py`: `cad_feature_update(url, feature: str, params: dict, apply: StrictBool = False)`.

- [ ] **Step 1: Write failing tests**

```python
def test_merge_preserves_untouched_bytes():
    new, changes = merge(RAW_FILLET, [built_radius])
    assert json.dumps({k: v for k, v in new.items() if k != "parameters"}, sort_keys=True) == \
           json.dumps({k: v for k, v in RAW_FILLET.items() if k != "parameters"}, sort_keys=True)
    others = [p for p in new["parameters"] if p["parameterId"] != "radius"]
    assert others == [p for p in RAW_FILLET["parameters"] if p["parameterId"] != "radius"]  # incl. deterministicIds query
    assert changes == [{"parameterId": "radius", "before": "2 mm", "after": "3 mm"}]

def test_merge_array_changes_listed_per_item():
def test_update_refuses_feature_without_raw(ctx_fake):     # Review Focus 1
def test_update_refuses_sketch_and_variable(ctx_fake):
def test_update_last_feature_splices(ctx_fake):            # applied, last_read advanced, next write needs no read
def test_update_midtree_demands_reread(ctx_fake):          # note == _MIDTREE; next apply refused until cad_read
def test_update_apply_after_human_edit_refused(ctx_fake):  # Review Focus 4
def test_update_dry_run_zero_requests_after_read(ctx_fake):  # specs cached → 0
def test_apply_feature_add_unchanged():                    # existing write-safety tests still pass (run them)
def test_onshape_update_feature_posts_to_featureid_path():  # scripted transport
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_generic_update.py tests/test_cad_feature_update.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `update.py`, the backend methods, the `_apply_feature` keyword, the tool and the registration.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(generic): cad_feature_update with mid-tree re-read rule`. Tell `onshapeduet-7b` the SHA, because it touches `_apply_feature`.

---

### Task 8: `cad_variable` mid-tree splice fix

**Files:**
- Modify: `tools.py` (`cad_variable` after an exact update)
- Test: `tests/test_cad_variable.py` (add)

**Interfaces:**
- Consumes: `_MIDTREE` from Task 7.
- Behaviour: when updating an existing variable that is **not** the last feature in the cached entry, call `ctx.lose_track(ref, _MIDTREE)` and add `note`, instead of splicing. A variable that is last keeps today's splice. Creating a variable is unchanged (it already demands a re-read).

- [ ] **Step 1: Write failing tests** `test_variable_update_midtree_demands_reread` and `test_variable_update_last_still_splices`.
- [ ] **Step 2: Run.** Expected: the first FAILs.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `fix: cad_variable demands a re-read after a mid-tree update`.

---

### Task 9: Docs

**Files:**
- Modify: `docs/agent-guide.md`, `docs/live-checklist.md`, `README.md` ("What exists" tool list)

- [ ] **Step 1: Agent guide.** Add a "Generic features" section:
  - When to use: typed tools first; generic for other native features and for editing human-made ones.
  - Workflow: `cad_specs` → `cad_feature_add`/`update` dry run → show the owner → apply.
  - The value forms table from spec §7.4 and the array patch form.
  - One worked add (`cPlane` offset) and one update (fillet radius).
- [ ] **Step 2: Live checklist.** Add spec §10's four checks, each with its request cost and exact pass condition. Also: replace `tests/fixtures/featurespecs_UNVERIFIED.json` with a trimmed live capture, and confirm the array spec's `parameters` key.
- [ ] **Step 3: README.** List the four new tools, and state that generic writes are verified offline only.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `docs: generic feature layer guide and live checks`.
