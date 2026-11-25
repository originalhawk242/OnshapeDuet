# Live verification checklist (Task 12)

Everything below is an assumption about the live Onshape API that the offline tests
cannot prove. Verify each against a throwaway document with a small request budget,
and record the request count per step. Do not "fix" a shape without a live response
that shows the real one. The shapes are isolated in `backends/onshape.py`,
`variables.py` and `sketch/serialize.py`.

## Safety (do these first, in this order, on a throwaway document)

RESULT (see "Live results"): Onshape does NOT reject a skewed write, so the
client-side backstop is the only stale guard and is permanent. Step 1 below is
already done; do not repeat it except to re-check Onshape's behavior (it WRITES a
feature). Record the request count of every step.

1. **Server-side rejection, with the client backstop bypassed.** The normal tools
   never reach this case: `_backstop` (tools.py) refuses a stale write BEFORE the POST,
   so an "edit in the UI between cad_read and apply" test only exercises the backstop.
   To test Onshape's own `rejectMicroversionSkew`, call the backend directly from a
   Python shell (budget >= 5). Build it from your config; credentials come from the
   environment variables, never from the snippet:

   ```python
   import os
   from pathlib import Path
   from onshape_duet.server import build_context, DEFAULT_CONFIG
   from onshape_duet.docref import parse_url

   path = Path(os.environ.get("ONSHAPE_DUET_CONFIG", DEFAULT_CONFIG)).expanduser()
   ctx = build_context(path)          # makes no request
   backend, ledger = ctx.backend, ctx.ledger
   ref = parse_url("https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid>",
                   ctx.config.base_url)   # a THROWAWAY document
   print(ledger.used, ledger.remaining)
   ```

   Then:
   - `fl = backend.list_features(ref)` (note `fl.microversion`, call it M0);
   - edit the document in the Onshape UI (add or move any feature);
   - `backend.add_feature(ref, <small valid feature JSON>, expected_microversion=M0)`.
   Observed live: `StaleVersion(may_have_landed=True)` (`microversionSkew: true`)
   AND the feature WAS added. The backstop therefore must stay. Expected
   requests: 2 (the `list_features` GET and the POST).
2. **Normal stale refusal through the tools.** `cad_read`, edit in the UI, then call a
   write tool with `apply=True`. Expect a refusal ("stale ... re-read") with zero
   POSTs; this is the backstop, not Onshape. Expected requests for the refusal: 1
   (the backstop's `get_microversion`).
3. **Whether a write reply's `sourceMicroversion` is the NEW version or echoes the
   request.** This is a SAFETY question, not only a request-count one: when the reply
   lacks a new version the backend refetches it with a separate GET, and a human edit
   landing between the POST and that GET would be adopted unseen. The code therefore
   treats a refetched version as NOT read (`WriteResult.exact` is False): `last_read`
   is dropped and the reply says "re-read with cad_read before the next write".
   Expected if the reply carries a distinct new version: apply costs 2 (backstop +
   POST) and a second apply works without a re-read. If instead the reply echoes the
   request or omits it, apply costs 3 and the second apply is refused until `cad_read`.
   Record which one happens. If the reply is always exact, the re-read demand never
   triggers; keep it anyway as a guard.
4. **`currentmicroversion` key:** VERIFIED LIVE: the key is `microversion`.
5. **Whether `currentmicroversion` and the feature list's `sourceMicroversion` are the
   same id.** If they DIFFER, EVERY apply is refused by the client-side backstop
   (the backstop compares the two), not just cache lookups missing; `cad_read` on an
   unchanged document would also cost 2 instead of 1. That makes the tools unusable
   for writes, so a mismatch is a blocker, not a request-count curiosity.

   **Client-side backstop (S1).** Until step 1 passes, every write tool does one
   `get_microversion` check against the last-read version before the POST (1 extra
   request per apply; `_backstop` in `tools.py`). The backstop may only be removed
   after the direct-call test in step 1 has proven server-side rejection, and only
   if steps 4-5 show the ids are comparable.
6. **POST redirect / odd replies.** If a write ever answers 3xx or a 5xx, the tool must
   say `applied: "unknown"` and demand a re-read; nothing to test deliberately, but
   note any occurrence.

## Variables

7. **Variable model.** Assign features probably keep the variable name in a string
   parameter `name` (not the feature name `#name`), with the value in
   `lengthValue` / `angleValue` / `numberValue` / `anyValue` plus `variableType`.
   Consider the variables endpoint
   `/api/v6/variables/d/{d}/w/{w}/e/{e}/variables`. The single mapping point is
   `variables_from_features` in `variables.py`; the write body is `set_variable`.
8. **Variable write replies.** `set_variable` reads the new feature id and its
   `featureState.featureStatus` from the write reply (so `cad_variable` can report a
   regeneration error). Check the reply to creating and to updating a variable has
   `feature.featureId` and `featureState`; if not, `cad_variable` reports no status.
9. **`#name` in expressions** is accepted by Onshape in a sketch dimension.

## Sketch JSON

10. `DISTANCE` is unsigned and its direction comes from reference order (`first` ->
   `second`); the serializer orders references so the nominal delta is non-negative.
   Check a point left of / below the origin.
11. Ground constraint form (COINCIDENT with an external query to the origin vertex
   `IB`), and the `externalFirst` / `externalSecond` parameter ids.
12. `DIAMETER` parameter id; rectangle side point ids (`<id>.<side>.start/end`).
13. Whether `BTMSketch-151` needs `featureId` / namespace fields.
14. `sketchPlane` query form (`queryStatement` vs `BTMIndividualQuery-138`
    `deterministicIds`) and the plane ids `JCC` / `JDC` / `JEC` for Front/Top/Right.
15. `serializationVersion` `"1.1.23"`.
16. Flattened parameter shapes in `cad_read` output (expression / value) look right on
    real quantity, enum, boolean and string parameters.

## Authentication

17. HMAC nonce charset (25 alphanumerics are sent) and whether `Content-Type` belongs
    in the signed string on GET requests.

## Request counts (record what actually happens)

Expected: `cad_read` on a fresh document 2, unchanged 1; dry runs after a read 0 (a
dry run with nothing cached costs up to 2, sketches included: the duplicate-name check
needs the feature list); apply 2 with the backstop (version check + write; +1 if the
write reply lacks a new version, after which a `cad_read` is required before the next
write), 1 once the backstop is removed.

## Generic features

Design: docs/superpowers/specs/2026-11-24-generic-feature-layer-design.md. Use a throwaway
document. Request counts are for the tools. Record the real count of each step.

G1. **`featurespecs` response shape** (1 request; `cad_specs` makes it). Capture the whole
   response and confirm each field name and string that `generic/specs.py` assumes:
   - top-level key `featureSpecs` (a wrong key fails the whole parse; native specs are those
     with empty or missing `namespace`);
   - spec: `featureType`, `featureTypeName`, `namespace` (what do custom features show?),
     `parameters`;
   - parameter: `btType`, `parameterId`, `parameterName`, `quantityType`, `defaultValue`,
     `visibilityCondition`;
   - parameter `btType` strings (`KIND_BY_BTTYPE`): `BTParameterSpecQuantity-173`,
     `BTParameterSpecEnum-171`, `BTParameterSpecBoolean-170`, `BTParameterSpecString-175`,
     `BTParameterSpecQuery-174`, `BTParameterSpecFeatureList-703`, `BTParameterSpecArray-2600`;
   - quantity: `ranges[0].units` and `ranges[0].defaultValue`;
   - enum: `options` (list of strings), `enumName`, and the enum's own `namespace`;
   - array item specs under `parameters`;
   - `visibilityCondition`: always-shown btType `BTParameterVisibilityAlwaysShown-5487`;
     conditional btType `BTParameterVisibilityOnEqual-180` with fields `parameterId` and
     `value`. The spelling of that `value` (`BLIND` or `BoundingType.BLIND`) matters:
     `HIDDEN_PARAM` compares against it.
   Pass: every name and string matches, or the mismatch is recorded and `specs.py` fixed from
   the capture.
G2. **Fixture replacement.** `tests/fixtures/featurespecs_UNVERIFIED.json` is hand-written.
   Another session (typed-features) committed a live capture at `tests/fixtures/feature_specs.json.gz`
   as `{"kept": [...]}` of whole specs; `test_real_capture_defaults_if_present` now runs against it. Point the generic tests at it, delete the UNVERIFIED
   file, and confirm the array spec's item specs sit under `parameters`. Pass: suite green on
   the live fixture. 0 requests.

   ANSWERED 2026-11-24 from the typed-features featurespecs capture (76bb554, 13 specs):
   - enum `namespace` is per parameter (each enum parameter carries its own);
   - array item specs sit under `parameters`;
   - OnEqual values are spelled plainly (`PART`, `true`);
   - most visibility conditions are `BTParameterVisibilityLogical-178` trees, so `HIDDEN_PARAM`
     stays best effort; a few are a bare `BTParameterVisibilityCondition-177` (always shown);
   - quantity `defaultValue` carries an empty `expression` plus `value`/`units` (meter, radian
     in SI), so readable defaults come from the `ranges` entry in millimeter / degree;
   - custom features have no "::" in `featureType`; `namespace` is a separate field.
   Still open: the fixture file swap itself, which waits for the merge with typed-features.
G3. **Add with omitted parameters** (`cad_feature_add` apply, 2 requests, +1 if specs are not
   cached). Add a `cPlane` with only `offset`. Pass: Onshape accepts it and fills the omitted
   parameters, and the defaults the dry run listed (`defaults_applied`) match what
   `cad_inspect` then shows.
G4. **Update by sending back the read `raw` JSON with one parameter changed**
   (`cad_feature_update` apply, 2 requests, +1 if specs are not cached). The rebuilt named parameter replaces the original
   whole, so fields such as `nodeId` on that one parameter are not sent back. Pass: Onshape
   accepts the update, the changed parameter holds the new value, and `cad_inspect` shows every
   other parameter unchanged. If Onshape rejects it, record which field must be kept.
G5. **Update reply.** Does the reply name a new `sourceMicroversion` (exact), as add does?
   Check (a) updating the last feature: the cache is spliced and the next apply works with no
   re-read; (b) updating a middle feature: the reply carries the re-read `note` and the next
   write is refused until `cad_read`. Also confirm the update reply carries the full `parameters` list of the feature
   (the cache is only spliced from a reply that has it; otherwise the code refetches and says so)
   and that its `featureId` is the one updated (a different id is treated as unknown outcome).
   Pass: both behave so, in 2 requests per apply (+1 if specs are not cached).
G6. **Raw-parameter shapes `cad_inspect` renders.** On a feature with each, confirm the
   field names: `BTMParameterQueryList` `queries[].queryString` and `deterministicIds`,
   `BTMParameterFeatureList` `featureIds`, `BTMParameterArray` `items[].parameters`. Pass:
   `cad_inspect` shows the query string or `N geometry ids (set in Onshape)`, feature names for
   a feature list, and one line per array item. 0 requests after `cad_read`.
G7. **Array patch.** On a feature with an array parameter, `cad_feature_update` with
   `{"items": {"0": {...}}}` (2 requests, +1 if specs are not cached). Pass: only item 0's named parameters change; a
   list value over human-picked geometry is refused with `RAW_GEOMETRY_ID`.

### Generic features: live run (G3-G7, 2026-11-24)

Run with the tool functions directly on typed-features head 37bc3bc in a new Part Studio `GenericLive`
(element `a9109357fbd6c32e5b0a5c94`) of the foundation test document; G6 also read `TypedLive` (no writes there).
Setup: `cad_sketch Base` (60 x 40 on Top), `cad_extrude Block` 10 mm. Ledger 127 -> 159 (32 requests, including
1 to create the element and 2 spent on a write Onshape rejected).

| Check | Result |
|---|---|
| G3 add `cPlane OffPlane` with only `entities` (Block's end cap) and `offset` | **FAIL, fixed**: written, but regenerates with **ERROR**. Onshape does NOT fill omitted parameters. `cad_feature_update` adding `cplaneType=OFFSET`, `oppositeDirection=false` turned it OK |
| G3, array item without `index2` (`linearPattern skippedInstances [{"index1": 1}]`) | **FAIL, fixed**: HTTP 400 `Parameter skippedInstances ... does not match its feature spec`. Same add with `index2: 0` was accepted, status OK |
| G5(a) update of the last feature (`OffPlane`, then `Copies distance` 50 -> 60 mm) | PASS: reply has the full parameter list and the same `featureId`; cache spliced, no `note`; the next apply went through with no re-read |
| G7 array patch `{"items": {"0": {"index1": 2}}}` straight after G5(a) | PASS: only `index1` of item 0 changed, `index2` kept |
| G4 + G5(b) update a middle feature (`OffPlane offset` 5 -> 8 mm, `Copies` after it) | PASS: accepted, read back 8 mm with every other parameter unchanged; reply carried the re-read `note`; the next apply was refused at 0 requests until `cad_read` |
| G6 `cad_inspect` on TypedLive Fillet, Chamfer, Shell, Union, Mirror, Pattern, Hole and GenericLive Copies | Query strings and array items render. **Fixed**: feature lists showed raw ids (now names), a one-item array rendered on the label line |

Facts learned:
- Onshape does not apply spec defaults for parameters missing from a feature write. Depending on the feature it
  regenerates with ERROR (cPlane without `cplaneType`) or rejects the write with HTTP 400 (array item missing a
  parameter); a missing lookup table fails a FeatureScript precondition (hole `isoHoleTableEx`, found by the
  typed-features session). Fix: a generic add sends every omitted parameter: scalars and lookup tables at the spec default
  (conditional ones too), selections, feature lists and arrays empty; a full array item gets its omitted item
  parameters. `defaults_applied` lists them all.
- After an update, Onshape rewrites the feature's selections, ours included, as `query=qCompressed(1.0,"...")` (opaque).
  Sending them back in later updates works (OffPlane was updated twice). `cad_inspect` now shows
  `selection compressed by Onshape` instead of the blob.
- A spec fetch is per process, so a fresh process pays 1 request on its first generic call.

Open generic items:
- [x] G3b: re-run G3 live with the defaults fix (2026-11-24, 6 requests, ledger 186 -> 192): `cPlane OffPlane2` given only
  `entities` and `offset` regenerates **OK**; the 7 defaults the dry run listed are what `cad_inspect` reads back.
- [ ] G6 `N geometry ids (set in Onshape)`: no human-picked (id-only) selection was available; still offline-only.
- [ ] G7 refusal of a list over human-picked array items (`RAW_GEOMETRY_ID`): no such feature live; unit-tested only.
- [ ] `EMPTY_SELECTION` / `HIDDEN_PARAM` evaluate only simple `X == V` conditions; most live conditions are
  `BTParameterVisibilityLogical-178` trees (extrude `entities` among them), so those are listed as not checked.
  Evaluating Logical trees would cut the noise.
- [ ] Item-level selections filled empty inside a full array item are listed in `defaults_applied` but not in
  `EMPTY_SELECTION`.
- [ ] Why Onshape compresses selections on update, and whether a compressed selection still follows upstream edits
  like our readable queries do.

## Live results (recorded)

- **Server-side skew rejection does NOT prevent the write.** Sending `sourceMicroversion`
  (stale) + `rejectMicroversionSkew: true` to the features POST: Onshape APPLIED the
  feature and replied with `microversionSkew: true`. The new feature was present
  afterwards. So the client-side backstop (`_backstop`, one `currentmicroversion` GET
  before each POST) is the only real guard and MUST stay; a human edit between the
  backstop GET and the POST can still land unseen (narrow race; the reply then reports
  `StaleVersion(may_have_landed=True)`).
- `currentmicroversion` returns `{"microversion": ...}`; it is comparable with the
  feature list's `sourceMicroversion` (the backstop passes on an unchanged document).
- Variables: `assignVariable` feature, name template `#name = #value`, params `name`
  (string), `variableType` (enum LENGTH), `lengthValue` (quantity). Create and update
  both verified. Create's reply carries no exact new version, so a re-read is demanded.
- **Diameter label (verified live):** a DIAMETER needs `labelRatio` (radial multiple, >1 = outside the
  circle) and `labelAngle` (`<n>*rad`) or the dimension is constrained but not drawn. We now send
  1.8 and -0.7*rad. `labelDistance` is for LENGTH/DISTANCE only.
- **Real-newline feature name (verified live via API):** collapsed to one outline line, no forged line.
- **Variable create reply** carries a new `sourceMicroversion`; the re-read demand after create is the
  cached-list rule, not a missing version. Variable update dropping UI default params is harmless.

## Typed features: captured facts (2026-11-24)

Ground truth for the typed feature tools is Onshape's own featurespecs, not hand-built UI features (owner's decision).
Captured from the foundation test document with `scripts/capture_features.py` (68 of 75 budget used afterwards).

- `GET /api/v6/partstudios/d/{d}/w/{w}/e/{e}/featurespecs` works. All 13 wanted specs are present (`BTFeatureSpec-129`) in
  `tests/fixtures/feature_specs.json.gz`, as `{"kept": [...], "featureTypes_seen": [...]}`, gzipped because the hole spec alone is about 6 MB.
- `tests/fixtures/doc_features.json` is the raw feature list of that document (sketches and variables from the foundation tests).
- (Q2) `POST .../featurescript` needs `"queries": {}` (a JSON object). A list gives HTTP 400. The reply is
  `BTFeatureScriptEvalResponse-1859` with `result` = `{"btType": "com.belmonttech.serialize.fsvalue.BTFSValueArray", "value": [{"btType": "com.belmonttech.serialize.fsvalue.BTFSValueString", "value": "2"}, ...]}`.
  `toString(vector(1, 2, 3))` renders as `"(1, 2, 3)"`. The reply also carries `sourceMicroversion` and `serializationVersion` (`1.2.10` live; the code sends `1.1.23` for writes).
- (Q1) Whether `queryString` selections persist: PASS for extrude (see the Q1 result below). Fillet and chamfer
  persistence, and selections following an upstream edit, remain open items (listed at the end).
- `cad_find` script verified live (2026-11-24, 1 request): `find_script` with the bare `function(context is Context, queries is map)` form evaluated `qCreatedBy(makeId("FYg6qkcmnXyyqI5_0"), EntityType.EDGE)` (sketch `PlateVar`) → count 10, centroids like `(25.000000000000004, 0, 0)` in mm. The reply is stored under `find_eval` in `tests/fixtures/probes.json`. A sketch's edges are reported twice (10 for a 5-edge sketch), which is Onshape's own count for qCreatedBy on a sketch.
- (Q1) `queryString` selections persist (2026-11-24, real `cad_extrude` apply, 4 requests): `ProbeExtrude` (8 mm, sketch `PlateVar`) regenerated **OK**.
  Read back, its `entities` query keeps `"queryString": "query=qSketchRegion(makeId(\"FYg6qkcmnXyyqI5_0\"), true);"`, and Onshape adds the resolved `"deterministicIds": ["JIC"]` beside it.
  Verified in Task 14b (see "Typed features: live run" below): that after an upstream edit (e.g. a variable change), the selection follows the query rather than the stored ids. The reply is under `extrude_querystring` in `tests/fixtures/probes.json`.
  `ProbeExtrude` is left in the foundation test document. Delete it in the UI, or leave it for Task 14 to build on.

### Typed features: live run (Task 14b, 2026-11-24)

Run with the tool functions directly (not over MCP) on head 678ea49 in a new Part Studio `TypedLive`
(element `a64ea7ef640d79b80c37151b`) of the foundation test document. Each step: dry run, then apply.
The ledger went from 74 to 127, plus 4 diagnostic evaluations, for 131 in total. Geometry was checked with read-only FeatureScript evaluations (body bounding boxes,
cylinder face radii and origins).

| Step | Result |
|---|---|
| `cad_variable t = 40 mm` | OK. Creating a variable makes the next call re-read (2 requests) |
| `cad_sketch Base` on Top (60 x `#t` rectangle), `cad_extrude Body` 20 mm | OK |
| `cad_find` edges of Body's end cap, `evaluate=true` | 4 edges, centroids at z = 20 (1 request) |
| `cad_fillet` 3 mm (end-cap edges), `cad_chamfer` 1 mm (start-cap edges) | OK; four r = 3 fillet faces at the top edges |
| `cad_plane MidX` (Right + 30 mm), `Above` (Top + 40 mm) | OK; `Above` origin (0, 0, 40), normal +Z, x +X |
| `cad_sketch BossSk` on Body's end cap, `cad_extrude Boss` 10 mm add | OK; the circle at sketch (15, 20) landed at world (15, 20, 20) |
| `cad_mirror` Boss across `MidX` | OK; copy at x = 45 |
| `cad_sketch HoleSk` on `{"plane": "Above"}`, point p1 (10, 10) | OK; p1 at world (10, 10, 40) |
| `cad_hole` 5 mm through at p1 | **ERROR** (`getFeatureError` = `REGEN_ERROR`, no geometry). Fixed in Task 15 (below); `Hole` and `Hole2` stay in the tree as failed attempts |
| `cad_pattern` linear, Boss x 2, 12 mm, direction = Body's +Y face | OK; copy at (15, 32) |
| `cad_shell` Body's start cap, 1.5 mm, `outward=false` | OK; **inward**: bounding box unchanged (0..60, 0..40), inner radii 3 → 1.5 and 5 → 3.5 |
| `cad_sketch RevSk` on Front, `cad_revolve Rev` about line `ax` | OK; ring r 10..20 about the vertical axis at (70, 0) |
| `cad_boolean` union of Body and Rev | OK; one solid body, bounding box (0, -20, 0)..(90, 50, 30) after the `t` change |
| `cad_variable t = 50 mm` | OK; **every feature regenerated OK** (only Hole stays in ERROR). The fillet edges followed the face to y = 25 and y = 47 |

Facts learned:
- Front-plane sketch x/y map to world X/Z; Top and its offset planes, and a +Z extrude cap, map sketch x/y to world X/Y
  with the origin over the world origin.
- `box` is a reserved word in FeatureScript (a parse error in an eval script).
- The FS eval reply now reports `serializationVersion` `1.2.24`.
- When an eval script fails to parse, `result` is null and the error is only in `notices`. `eval_featurescript` then raises
  "missing result" and the notice text is lost. Surfacing it is a candidate improvement for `cad_find`.

### Typed features: hole fix (Task 15, 2026-11-24)

- Root cause: Onshape fills in nothing a feature JSON leaves out. A live regeneration applies neither the spec defaults nor the
  `defineFeature` default map. A read-only FS eval that calls `hole(context, id, definition)` *does* apply the default map,
  so it is not a faithful stand-in. hole.fs's precondition needs `isoHoleTableEx is LookupTablePath` (METRIC + SIMPLE),
  plus `initEntities`, `featureName`, `hasClearance`, `showTappedDepth` and `isMultiple`. A failing precondition shows only as
  the generic `REGEN_ERROR`. The eval reply carries "Precondition failed (...)" only as a notice.
- Fix: `cad_hole` sends every hole parameter that has a spec default (`features/hole_defaults.py`, generated by
  `scripts/gen_hole_defaults.py` from the captured featurespecs), and the caller's values override them.
- Live (ledger 168 → 186): `HoleThru` 4 mm through cuts r = 2 at (10, 10). `HolePattern` (linear x 3, 20 mm along Body's +X face)
  gives holes at x = 10, 30, 50. `HoleBlind` 3 mm x 8 mm from a second sketch cuts r = 1.5 at (50, 40). Both holes regenerate
  with status **INFO** (a non-standard diameter compared with the hole table). The tools now report INFO as a warn
  `REGEN_INFO`, not an error.
- After the final-review fix wave, the generated defaults take their values from `generic.specs.default_param`: tolerance bounds are
  `0.1 mm`, and enums carry no `namespace`. Re-checked live (ledger 192 → 197): `HoleCheck`, 6 mm through at p1, cuts r = 3 at (10, 10)
  with status INFO, and the reply carries the warn `REGEN_INFO` finding.

### Typed features: open live items

- [x] Hole: fixed and verified live (above). The hole depth itself was not measured; only the cylinder radius and position were.
- [x] Shell direction: `outward=false` gives an inward wall, as intended (verified). `outward=true` is not exercised live.
- [x] Selections follow upstream edits: verified (`t` 40 → 50 mm, every query-based feature regenerated OK and moved).
- [x] Fillet and chamfer edge `queryString` persistence: verified (both OK after an upstream change).
- [ ] `serializationVersion`: the code sends 1.1.23; live replies report 1.2.10 and 1.2.24. Nothing failed because of it.
- [x] Sketch on an offset plane or a +Z face: lands in world X/Y with the origin over the world origin (verified).
  Faces with other normals are not verified, so the `cad_sketch` docstring warning stays.
