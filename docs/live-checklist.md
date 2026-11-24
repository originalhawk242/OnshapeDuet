# Live verification checklist (Task 12)

Everything below is an assumption about the live Onshape API that the offline tests
cannot prove. Verify each against a throwaway document with a small request budget,
and record the request count per step. Do not "fix" a shape without a live response
that shows the real one. The shapes are isolated in `backends/onshape.py`,
`variables.py` and `sketch/serialize.py`.

## Safety (do these first, in this order, on a throwaway document)

Step order matters: step 1 proves Onshape itself rejects a skewed write; only then
can the client-side backstop be judged. Record the request count of every step.

1. **Server-side rejection, with the client backstop bypassed.** The normal tools
   never reach this case: `_backstop` (tools.py) refuses a stale write BEFORE the POST,
   so an "edit in the UI between cad_read and apply" test only exercises the backstop.
   To test Onshape's own `rejectMicroversionSkew`, call the backend directly from a
   Python shell (budget >= 5):
   - `fl = backend.list_features(ref)` (note `fl.microversion`, call it M0);
   - edit the document in the Onshape UI (add or move any feature);
   - `backend.add_feature(ref, <small valid feature JSON>, expected_microversion=M0)`.
   Expect `StaleVersion` (HTTP 409/412, or `microversionSkew: true` reported as
   "applied against a newer version"), and in the UI that NOTHING was added. If the
   feature appears, the pin does not hold: stop, keep the backstop, and fix the
   `rejectMicroversionSkew` / `sourceMicroversion` handling in `_write`. Expected
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
4. **`currentmicroversion` key:** `microversionId` vs `microversion`.
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
