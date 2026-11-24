# Live verification checklist (Task 12)

Everything below is an assumption about the live Onshape API that the offline tests
cannot prove. Verify each against a throwaway document with a small request budget,
and record the request count per step. Do not "fix" a shape without a live response
that shows the real one. The shapes are isolated in `backends/onshape.py`,
`variables.py` and `sketch/serialize.py`.

## Safety (do these first)

1. **Stale write rejected.** Run `cad_read`, then edit the document in the Onshape UI
   (move or add a feature), then call a write tool with `apply=True`. Expect the write
   to be refused as stale (HTTP 409/412, or `microversionSkew: true` surfaced as
   "applied against a newer version"). If the write goes through, the version pin does
   not hold: stop and fix `rejectMicroversionSkew` / `sourceMicroversion` handling.
2. **Whether a write reply's `sourceMicroversion` is the NEW version or echoes the
   request.** The backend refetches the version when it equals the request, so this
   only affects the request count (apply = 1 vs 2).
3. **`currentmicroversion` key:** `microversionId` vs `microversion`.
4. **Whether `currentmicroversion` and the feature list's `sourceMicroversion` are the
   same id.** If not, the cache never hits (cad_read on an unchanged document would
   cost 2 requests instead of 1).

## Variables

5. **Variable model.** Assign features probably keep the variable name in a string
   parameter `name` (not the feature name `#name`), with the value in
   `lengthValue` / `angleValue` / `numberValue` / `anyValue` plus `variableType`.
   Consider the variables endpoint
   `/api/v6/variables/d/{d}/w/{w}/e/{e}/variables`. The single mapping point is
   `variables_from_features` in `variables.py`; the write body is `set_variable`.
6. **`#name` in expressions** is accepted by Onshape in a sketch dimension.

## Sketch JSON

7. `DISTANCE` is unsigned and its direction comes from reference order (`first` ->
   `second`); the serializer orders references so the nominal delta is non-negative.
   Check a point left of / below the origin.
8. Ground constraint form (COINCIDENT with an external query to the origin vertex
   `IB`), and the `externalFirst` / `externalSecond` parameter ids.
9. `DIAMETER` parameter id; rectangle side point ids (`<id>.<side>.start/end`).
10. Whether `BTMSketch-151` needs `featureId` / namespace fields.
11. `sketchPlane` query form (`queryStatement` vs `BTMIndividualQuery-138`
    `deterministicIds`) and the plane ids `JCC` / `JDC` / `JEC` for Front/Top/Right.
12. `serializationVersion` `"1.1.23"`.
13. Flattened parameter shapes in `cad_read` output (expression / value) look right on
    real quantity, enum, boolean and string parameters.

## Authentication

14. HMAC nonce charset (25 alphanumerics are sent) and whether `Content-Type` belongs
    in the signed string on GET requests.

## Request counts (record what actually happens)

Expected: `cad_read` on a fresh document 2, unchanged 1; dry runs after a read 0 (a
sketch dry run that references no `#variable` is always free); apply 1 (2 if the write
reply lacks a new version).
