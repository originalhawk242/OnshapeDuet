# Agent guide: onshape-duet

Workflow: `cad_status` -> `cad_read` -> dry run -> show the owner -> apply -> `cad_read`.

1. `cad_status`: budget, used, remaining, whether credentials are set. Costs zero requests.
2. `cad_read(url)`: features and variables of the Part Studio. Required before any apply; a write is refused as stale if the document changed after your last read.
3. Dry run (`cad_sketch` / `cad_variable` with `apply=False`, the default): nothing is written. You get the DOF count and lint findings.
4. Show the dry-run result to the owner and get a yes.
5. Apply (`apply=True`), then `cad_read` again to confirm.

If a write reply says `applied: "unknown"`, or carries a `note` telling you to re-read (for example after creating a variable), run `cad_read` before anything else: the write may have landed, and the next write is refused until you do. A `REGEN_STATUS` error finding means Onshape wrote the feature but it does not regenerate cleanly.

## REFUSE vs WARN
- REFUSE: the sketch has a defect (for example conflicting constraints). Nothing is written, even with `apply=True`. Fix the spec.
- WARN: advice (for example a sketch that is not fully constrained). The write is allowed; tell the owner.

## Typed feature tools

Tools: `cad_find`, `cad_extrude`, `cad_revolve`, `cad_plane`, `cad_fillet`, `cad_chamfer`, `cad_shell`, `cad_boolean`, `cad_pattern` (linear or circular, features only, not parts), `cad_mirror`, `cad_hole` (simple holes, through or blind). `cad_sketch` can also sit on a plane feature or a planar face.

Workflow: `cad_status` -> `cad_read` -> `cad_find(evaluate=True)` for any non-trivial selection -> dry run of the typed tool -> show the owner -> apply -> `cad_read`.

All of them are dry runs unless `apply=True`, are refused if the document changed since the last `cad_read`, and use the REFUSE/WARN rule above.

**References** are by feature name (resolved to ids locally). **Values:**
- Lengths: a number in mm, `"<n> mm|cm|m|in"`, or `"#variable"` (length variables only).
- Angles: degrees as a number, or `"<n> deg"`. Variables are not supported yet.
- Counts: integers 2..10000.

**Selectors** (`edges`, `faces`, `tools`, `direction`, `axis`, face-plane bases): a dict, or a list of 1 to 8 dicts (union). Unknown keys are refused.

| key | values | meaning |
|---|---|---|
| `entity` (required) | `face`, `edge`, `body` | what to select |
| `created_by` | feature name | entities made by that feature |
| `cap` | `start`, `end` (needs `created_by`; face or edge) | the start or end cap of that feature |
| `edges_of` | one face selector; `entity` must be `edge`; not with `created_by`/`cap` | edges adjacent to those faces |
| (no base key) | | all entities of that type on modifiable solid bodies |
| `geometry` | edge: `line`, `circle`, `arc`; face: `plane`, `cylinder`, `cone`, `sphere`, `torus` | filter by geometry |
| `normal` | `+X -X +Y -Y +Z -Z` (faces only) | faces with that normal |
| `farthest` | `+X` ... `-Z` | the one farthest along that direction (applied last) |

Filters apply in the order geometry, normal, farthest. A `body` selector allows only `created_by`.

The selection is compiled to an Onshape query stored in the feature (`queryString`), so it is re-evaluated on rebuild. Verified live: the query is kept and the feature regenerates OK. That it follows an upstream edit is still to be verified (see live-checklist.md).

**Sketch planes:** `cad_sketch` spec `plane` is `"Front"`, `"Top"`, `"Right"`, `{"plane": "<cPlane feature name>"}`, or a face selector with geometry `plane` or a cap.

**Costs:** a dry run is 0 requests when the last-read version is cached, else at most 2. `cad_find` is 0 requests if the document was read; `evaluate=True` adds 1 and returns the match count and centroids in mm. Apply is 2 requests (version check + write), or 3 when the write reply names no new version.

**Lint codes:** `DEFAULT_NAME` (warn, names like `Extrude 1`), `DUPLICATE_NAME` (refuse), `UNKNOWN_VARIABLE` (refuse), `NON_LENGTH_VARIABLE` (refuse), `MAGIC_NUMBER` (warn).

**Not supported:** counterbore and countersink holes, concave/convex edge selection, angle and count variables, part patterns.

Worked example (sketch `Base` on Top, then extrude and round the end-cap edges):
1. `cad_sketch(name="Base", spec={"plane": "Top", ...})`, apply, `cad_read`.
2. `cad_extrude(name="Body", sketch="Base", depth="#t")`, dry run, apply, `cad_read`.
3. `cad_find(selector={"entity": "edge", "edges_of": {"entity": "face", "created_by": "Body", "cap": "end"}}, evaluate=True)`: check the count (4 for a rectangle).
4. `cad_fillet(name="Round", edges=<same selector>, radius=2)`, dry run, show the owner, apply, `cad_read`.

## Budget
- Every Onshape API request is counted in a persistent ledger. The default budget is 0, so no request is sent until the owner sets `budget` in the config file.
- When the budget runs out, tools return an error naming the `budget` config key and send nothing. Do not retry; ask the owner.
- Reads cost requests; check `requests used` / `budget left` at the end of each result.

## Limits
Reading existing sketches into DOF reports is not implemented: every sketch in `cad_read` shows `not assessed`. Do not infer constraint state from the outline.

## For the owner: CLAUDE.md snippet
```
## Onshape CAD
Use the onshape-duet tools for Onshape work. Order: cad_status, cad_read,
dry run (default), show me the result, apply only after I agree, cad_read again.
Never apply without a prior cad_read. If a tool refuses, report it; do not work around it.
Credentials come from the environment variables named in the config
(default ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY); never print or store them.
Config path: $ONSHAPE_DUET_CONFIG or ~/.onshape-duet/config.toml (set `budget`).
```
