# Generic feature layer: design

Status: approved in conversation 2026-11-24, written up for review. This covers milestone 5 of
`2026-11-23-onshape-duet-design.md` (the parent design). Branch `generic-features`, worktree
`../OnshapeDuet-generic`.

## 1. Intent

The typed tools (sketch, variable, and the extrude/fillet/... set another session is building in
milestone 4) cover common features well, but Onshape has around a hundred native features. The parent
design's goal 2 is that Claude can create or edit **any native feature**, and §7.6 is that Claude can
edit features a human built, including types with no typed tool. This layer does that. It keeps the
project's guarantees: dry-run by default, writes pinned to the version Claude last read, every request
counted, and a lint on every write.

## 2. Goals

1. `cad_specs`: look up the native feature types and the parameters of any one type.
2. `cad_inspect`: show one existing feature in full (every parameter), at no request cost after a read.
3. `cad_feature_add`: create any native feature from a simple `{parameterId: value}` map, checked against
   Onshape's own feature spec before anything is sent.
4. `cad_feature_update`: change only the named parameters of an existing feature (typed or human-made).
   Every other parameter, including geometry a human picked, goes back to Onshape exactly as it was read.
5. Honest dry runs: they show what will be sent (values, defaults that will apply, before→after for
   updates), the lint verdict, and the cost.

## 3. Non-goals

- Delete, suppress, rename and versions: milestone 6.
- Custom (FeatureScript-defined) features from Feature Studios or libraries. Native features only for now.
  A spec with a non-empty namespace is not offered and is refused.
- Assemblies, and insert-from-library (FRCDesignApp-style): planned after this milestone.
- Building every parameter kind on `add`. Kinds this layer cannot build (§7.4) are refused on add and
  left untouched on update.

## 4. Anti-goals

- **No FeatureScript authoring.** No tool takes FeatureScript text. Geometry is referenced only through
  the milestone-4 selector DSL, which is a fixed, enum-only dict that compiles to a query string.
- **No invented geometry ids.** A raw `deterministicIds` reference in an add, or in an update value, is
  refused (the parent design's §7.3 "transient geometry id" REFUSE rule).
- **Never silently drop a human's settings.** An update that would change a parameter the caller did not
  name is a bug. Tests assert byte-for-byte that untouched parameters are preserved.
- Spending requests on lookups that could be cached.

## 5. Constraints

- Everything in the parent design §5: Python 3.11+, MIT, no GPL code.
- **Shared code with the milestone-4 session (agreed 2026-11-24):**
  - `tools._apply_feature(ctx, ref, start, feature_json, extra, what) -> dict` is extracted by
    milestone 4 (its plan's Task 2) as its own commit. It handles backstop, add_feature, stale/unknown
    handling, cache splice and REGEN_STATUS, and is add-only. **This layer extends it** with a keyword
    `feature_id: str | None = None`: when set, it calls `backend.update_feature` instead of `add_feature`
    and applies the splice rule in §7.3. That is one small commit on top of milestone 4's, announced to
    that session, not a parallel helper. Wiring `apply=True` waits for milestone 4's SHA.
  - `features/refs.py` (milestone 4, its plan's Task 4): `compile_selector(sel, fl, arg) -> str` returns
    a FeatureScript query expression (no `query=` prefix). `features/` also has
    `p_query(pid, exprs) -> dict`, which wraps expressions into a `BTMParameterQueryList-148`. This layer
    builds every query parameter as `p_query(pid, [compile_selector(sel, fl, pid)])` and does not build
    query JSON itself. Wiring query parameters waits for these to land; until then a query value is
    refused with `UNSUPPORTED_PARAM_KIND` ("selectors not available yet").
  - `model.Feature` gains `raw: dict = field(default_factory=dict)`. `params` keeps being populated
    exactly as now. Owned by this layer. Milestone 4 rebases its `flatten_params` move onto it.
  - `backends/onshape.py _parse_feature`: one-line change to fill `raw=f`.
  - `server.py`: append-only registrations. `lint.py`: untouched. This layer's lint lives in
    `generic/lint.py`.
- The feature-spec and update JSON shapes are **unverified against live Onshape** (§10). As with the
  foundation, they are isolated in small functions and listed in `docs/live-checklist.md`.

## 6. Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **Chosen** | Spec-validated simple params map; server builds the BTM JSON from the spec's parameter kinds | Claude writes short, readable maps and the server catches mistakes before sending. Needs a builder per parameter kind. |
| Raw BTM JSON passthrough | Claude writes the full `BTMFeature-134` JSON | Widest and least code, but verbose and error-prone, and it bypasses selector and id checks. This is the parent design's rejected approach 3. |
| Typed tool per feature | One tool per feature type | That is milestone 4's job. It cannot reach "any feature". |

Geometry references, decided by the owner: **selectors only** (from `cad_find`/`compile_selector`). Raw
geometry ids are refused on add. A human's existing references are preserved on update.

## 7. Design

### 7.1 Modules

```
src/onshape_duet/generic/
  specs.py      parse a featurespecs response into FeatureSpec / ParamSpec; native-only filter
  build.py      {parameterId: value} + FeatureSpec -> BTMParameter* JSON (per-kind builders)
  validate.py   check a params map against a FeatureSpec; returns findings
  update.py     merge changed params onto a feature's raw JSON; before/after diff
  lint.py       generic-write lint (DEFAULT_NAME, MAGIC_NUMBER, RAW_GEOMETRY_ID, UNCLASSIFIED, ...)
  render.py     compact text for cad_specs / cad_inspect
```

Tool bodies go in `tools.py` next to the others (`cad_specs`, `cad_inspect`, `cad_feature_add`,
`cad_feature_update`) so they share `Context`, `_reply`, `_locked` and `_apply_feature`.

### 7.2 Backend

- `Backend.get_feature_specs(ref) -> list[FeatureSpec]`:
  `GET /api/v6/partstudios/d/{d}/w/{w}/e/{e}/featurespecs` (1 request). Fake backend: returns a fixture.
- `Backend.update_feature(ref, feature_id, feature_json, expected_microversion) -> WriteResult`:
  `POST .../features/featureid/{fid}` through the existing `_write`, with the same stale/unknown
  semantics as `add_feature`. `set_variable` already uses this path, so that part is known to work live.
  If `_apply_feature` grows an update mode in milestone 4, use that instead.
- The spec cache is held in `Context`, keyed by `DocRef`, for the life of the server process. It is not
  tied to microversion, because native specs do not change with document edits.

### 7.3 Tools

All take `url` first. All return `requests` and `budget_left` like the existing tools.

- **`cad_specs(url, feature_type: str | None = None)`**
  - No type: one line per native feature type (`featureType`: display name), sorted.
  - With a type: one line per parameter: id, display name, kind, unit or enum options, default, and
    whether it is shown only under some condition. Conditions are summarised, not evaluated.
  - Unknown or custom type: an error naming the closest native types.
  - Cost: 1 request on first use per Part Studio in this process, then 0.
- **`cad_inspect(url, feature: str)`**
  - `feature` is a feature id or an exact feature name. An ambiguous name is an error listing the
    matching ids.
  - Output: type, name, status, and every parameter in readable form. Queries show as their
    `queryString` or as `N geometry ids (set in Onshape)`; arrays are expanded one item per line.
  - Uses the cached copy at the version last read (0 requests). With no read yet, it reads first, as
    `snapshot` does. It does not count as a `cad_read`.
- **`cad_feature_add(url, feature_type, name, params: dict, apply=False)`**
  - Validate (§7.5), then build the `BTMFeature-134` JSON from the spec. Omitted parameters are left out,
    so Onshape applies its defaults. The dry run lists those defaults so Claude can see them.
  - Dry run: `{feature_type, name, params (as will be sent, readable), defaults_applied, findings,
    based_on_version}`.
  - Apply: refused unless read first and no `refuse` finding. Sent through `_apply_feature`.
    Cost 2 requests (backstop + POST), plus 1 if the specs are not yet cached.
- **`cad_feature_update(url, feature, params: dict, apply=False)`**
  - Start from the feature's cached `raw` JSON at the version last read. Replace exactly the named
    parameters, built from the spec as on add. Keep every other parameter and field verbatim.
  - Dry run: `{feature, changes: [{parameterId, before, after}], findings, based_on_version}`.
  - Apply: through `_apply_feature(..., feature_id=...)`, which calls `update_feature`.
  - **Cache after an update (decided at review).** The write reply reports only the updated feature's
    status, so features after it could have broken unseen.
    - If the updated feature is the **last** in the tree, the cache entry is spliced (same id, new raw
      JSON, reply status) and `last_read` advances, as for adds.
    - Otherwise the cache entry is dropped and the next write requires a `cad_read`. The reply says so
      (`note`: "features after this one may have changed status; re-read with cad_read"). Apply still
      costs 2 requests, and the next `cad_read` costs its usual amount.
  - **Array parameters on update (decided at review).** The value is one of two forms:
    - a list, which replaces the whole array. This is refused (`RAW_GEOMETRY_ID`) if any current item
      holds a geometry-id query, because it can't be restated without losing the human's pick;
    - a patch, `{"items": {"<index>": {parameterId: value}}}`, which changes only the named parameters
      of the named items (0-based index into the array as read) and keeps everything else verbatim. The
      dry run diffs per item. An index out of range is `BAD_VALUE`.
  - A feature whose `featureType` has no native spec (a custom feature) is refused.

### 7.4 Parameter kinds (value forms Claude writes)

| Spec kind | Value form in `params` | Built as |
|---|---|---|
| Quantity (length, angle, unitless, integer) | expression string: `"10 mm"`, `"#plate_w / 2"`, `"30 deg"`; a bare number only for unitless/integer | `BTMParameterQuantity-147` `expression` |
| Enum | option name string, checked against the spec's options | `BTMParameterEnum-145` with `enumName` from the spec |
| Boolean | `true` / `false` | `BTMParameterBoolean-144` |
| String | string (printable, length-capped) | `BTMParameterString-149` |
| Query | selector dict, or a list of them | `compile_selector` → `BTMParameterQueryList-148` of `BTMIndividualQuery-138` |
| Feature list | list of existing feature names or ids | `BTMParameterFeatureList-1749` with feature ids |
| Array | list of item maps, each validated against the array's item spec | `BTMParameterArray-2025` of `BTMArrayParameterItem-1843` |
| Anything else (lookup tables such as hole standards, references to other elements, appearance, ...) | not buildable | add: refused with `UNSUPPORTED_PARAM_KIND`; update: untouched unless named, and then refused |

Units: a quantity expression with no unit, where the spec has one, is refused (`MISSING_UNIT`). A
`#name` reference to a variable the document does not have is a WARN (`UNKNOWN_VARIABLE`), as in
`cad_variable`.

### 7.5 Validation and lint

REFUSE (nothing is sent, dry run or apply):
- `UNKNOWN_FEATURE_TYPE` / `CUSTOM_FEATURE`: not a native spec.
- `UNKNOWN_PARAM`: id not in the spec (message suggests close ids).
- `BAD_VALUE`: wrong kind, enum value not in the options, or bad array item.
- `MISSING_UNIT`: see §7.4.
- `RAW_GEOMETRY_ID`: any `deterministicIds`, or a dict that looks like raw BTM JSON, as a value.
- `UNSUPPORTED_PARAM_KIND`: see §7.4.
- `SELF_REFERENCE` (update only): a feature-list value naming the feature itself or a later feature.
- `TYPED_TOOL_REQUIRED`: add or update of `newSketch` or `assignVariable`. Their typed tools enforce
  guarantees the generic path would bypass: the sketch gate's floating-sketch refusal, and
  `cad_variable`'s refusal to convert non-LENGTH variables. The message names the typed tool.

WARN (written, shown prominently):
- `DEFAULT_NAME`: same rule as sketches, extended to any `<Display name> N`.
- `MAGIC_NUMBER`: the same literal (with unit) in two or more quantity params of this write.
- `UNKNOWN_VARIABLE`: see §7.4.
- `TYPED_TOOL_AVAILABLE` (add only): the type has a milestone-4 typed tool (extrude, revolve, fillet,
  ...). The add still goes ahead, for options the typed tool lacks, but the message names the tool.
  Update of those types gets no warning: editing a human's extrude is what this layer is for.
- `HIDDEN_PARAM`: a param the spec shows only under a condition, given while that condition looks
  unmet. Best effort; the message says it was not evaluated exactly.
- `REGEN_STATUS` (after apply): via `_apply_feature`, as for sketches.

### 7.6 Changes to shared code

- `model.Feature.raw` (defaulted). `_parse_feature` fills it. `DocCache` already deep-copies.
- `Context.specs: dict[DocRef, list[FeatureSpec]]`.
- `docs/agent-guide.md`: a section on when to use the generic tools instead of typed tools (typed first;
  generic for anything else and for editing human-made features), with an example of each.
- `docs/live-checklist.md`: the new live checks (§10).

## 8. Testing

- Fixture: a real `featurespecs` response cut down to about 6 types (extrude, fillet, linear pattern,
  hole, plane, a type with an array param). Until it is captured live, use a hand-written fixture marked
  `UNVERIFIED` in its file name.
- Unit tests per module: spec parsing (native filter, each kind); each builder; each REFUSE and WARN rule
  with a passing and a failing case; update merge preserving untouched parameters byte-for-byte
  (including a human-made `deterministicIds` query); before/after diff.
- Tool tests against the fake backend: dry run costs 0 requests (1 when specs are not cached); apply
  costs 2; stale refusal; apply without read refused; cache splice after updating the last feature;
  cache dropped and re-read demanded after updating a mid-tree feature; array item patch keeps a
  sibling item's human `deterministicIds` byte-for-byte; whole-array replace refused over such an item;
  ambiguous name; `TYPED_TOOL_REQUIRED` for `newSketch`/`assignVariable`.

## 9. Order of work

1. `Feature.raw` + `_parse_feature` (one small commit; tell the milestone-4 session the SHA).
2. `specs.py` + backend `get_feature_specs` + `cad_specs` + `cad_inspect`. Read-only, so these can ship
   first.
3. `build.py`, `validate.py`, `lint.py`, then `cad_feature_add` and `cad_feature_update` dry runs.
   Query params are stubbed until `compile_selector` lands.
4. Apply paths, after `_apply_feature` lands: `update_feature` backend method, then the `feature_id`
   extension of `_apply_feature` with the §7.3 splice rule.
5. Docs: agent guide, live checklist.

## 10. Live checks (to add to `docs/live-checklist.md`)

1. `featurespecs` response shape: spec and parameter `btType`s, enum options, quantity units and
   defaults, visibility conditions, how custom features show (namespace). About 1 request.
2. Add with omitted parameters: does Onshape apply spec defaults? (1 add on a throwaway doc, ~2 requests.)
3. Update by sending back the read `raw` JSON with one parameter changed: accepted as is, or must some
   fields be stripped? (~2 requests.)
4. Update reply: does it name the new `sourceMicroversion` (exact) like add does?

## 11. Calls made without the owner (please check)

1. `cad_inspect` is built here (the parent design lists it in milestone 2, where it was never done).
2. Omitted parameters take Onshape's defaults, and the dry run lists them. There is no "required
   parameter" refusal, because the spec does not reliably say what is required; a missing one shows up
   as a regeneration error after apply.
3. A bare number is accepted only for unitless/integer quantities. Everything else needs a unit.
4. The spec cache lives for the server process, per Part Studio, not on disk.
5. Feature lookup is by id or exact name. An ambiguous name is an error, never a guess.
6. Parameter kinds in §7.4's last row are not buildable in this milestone.
7. (review) Cache after update: splice only when the updated feature is last; otherwise drop and demand
   a re-read (§7.3).
8. (review) Array updates: whole-list replace, or per-item patch by index (§7.3).
9. (review) Sketches and variables are refused in the generic tools; milestone-4 types get a WARN on add
   only (§7.5).
10. (review) This layer extends milestone 4's `_apply_feature` with `feature_id`, as a small commit on
    top of theirs (§5).
