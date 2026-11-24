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
sketch dry run that references no `#variable` is always free); apply 2 with the backstop (version check + write; +1 if the
write reply lacks a new version, after which a `cad_read` is required before the next
write), 1 once the backstop is removed.

## Generic features

Design: docs/superpowers/specs/2026-11-24-generic-feature-layer-design.md. Use a throwaway
document. Request counts are for the tools. Record the real count of each step.

G1. **`featurespecs` response shape** (1 request; `cad_specs` makes it). Capture the whole
   response and confirm each field name that `generic/specs.py` assumes: spec `featureType`,
   `featureTypeName`, `namespace` (what do custom features show?), `parameters`; per parameter
   `btType`, `parameterId`, `quantityType`, `defaultValue`, `visibilityCondition`; quantity
   `ranges[0].units`; enum `options` and `enumName`; array item specs under `parameters`.
   Also the spelling of an OnEqual condition `value` (`BLIND` or `BoundingType.BLIND`):
   `HIDDEN_PARAM` compares against it. Pass: every name matches, or the mismatch is recorded
   and `specs.py` fixed from the capture.
G2. **Fixture replacement.** `tests/fixtures/featurespecs_UNVERIFIED.json` is hand-written.
   Another session (typed-features) commits a live capture at `tests/fixtures/feature_specs.json`
   as `{"kept": [...]}` of whole specs. Point the generic tests at it, delete the UNVERIFIED
   file, and confirm the array spec's item specs sit under `parameters`. Pass: suite green on
   the live fixture. 0 requests.
G3. **Add with omitted parameters** (`cad_feature_add` apply, 2 requests, +1 if specs are not
   cached). Add a `cPlane` with only `offset`. Pass: Onshape accepts it and fills the omitted
   parameters, and the defaults the dry run listed (`defaults_applied`) match what
   `cad_inspect` then shows.
G4. **Update by sending back the read `raw` JSON with one parameter changed**
   (`cad_feature_update` apply, 2 requests). The rebuilt named parameter replaces the original
   whole, so fields such as `nodeId` on that one parameter are not sent back. Pass: Onshape
   accepts the update, the changed parameter holds the new value, and `cad_inspect` shows every
   other parameter unchanged. If Onshape rejects it, record which field must be kept.
G5. **Update reply.** Does the reply name a new `sourceMicroversion` (exact), as add does?
   Check (a) updating the last feature: the cache is spliced and the next apply works with no
   re-read; (b) updating a middle feature: the reply carries the re-read `note` and the next
   write is refused until `cad_read`. Pass: both behave so, in 2 requests per apply.
G6. **Raw-parameter shapes `cad_inspect` renders.** On a feature with each, confirm the
   field names: `BTMParameterQueryList` `queries[].queryString` and `deterministicIds`,
   `BTMParameterFeatureList` `featureIds`, `BTMParameterArray` `items[].parameters`. Pass:
   `cad_inspect` shows the query string or `N geometry ids (set in Onshape)`, feature names for
   a feature list, and one line per array item. 0 requests after `cad_read`.
G7. **Array patch.** On a feature with an array parameter, `cad_feature_update` with
   `{"items": {"0": {...}}}` (2 requests). Pass: only item 0's named parameters change; a
   list value over human-picked geometry is refused with `RAW_GEOMETRY_ID`.

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
