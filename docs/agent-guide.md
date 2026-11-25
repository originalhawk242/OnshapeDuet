# Agent guide: onshape-duet

Workflow: `cad_status` -> `cad_read` -> dry run -> show the owner -> apply -> `cad_read`.

1. `cad_status`: budget, used, remaining, whether credentials are set. Costs zero requests.
2. `cad_read(url)`: features and variables of the Part Studio. Required before any apply; a write is refused as stale if the document changed after your last read.
3. Dry run (`cad_sketch` / `cad_variable` with `apply=False`, the default): nothing is written. You get the DOF count and lint findings.
4. Show the dry-run result to the owner and get a yes.
5. Apply (`apply=True`), then `cad_read` again to confirm.

If a write reply says `applied: "unknown"`, or carries a `note` telling you to re-read (for example after creating a variable), run `cad_read` before anything else: the write may have landed, and the next write is refused until you do. A `REGEN_STATUS` error finding means Onshape wrote the feature but it does not regenerate cleanly. A `REGEN_INFO` warn finding means it regenerated with an information message from Onshape (for a hole, usually a non-standard size); that is not a failure.

## Generic features
Typed tools first where one exists. Use the generic tools for native feature types that have no typed tool, for options a typed tool lacks, and for editing features a human made.
`cad_sketch` and `cad_variable` stay the only way to write sketches and variables: the generic tools refuse them (`TYPED_TOOL_REQUIRED`).

Workflow: `cad_read` -> `cad_specs` -> `cad_feature_add` / `cad_feature_update` dry run -> show the owner -> apply -> `cad_read`.

- `cad_specs(url)` lists native feature types. `cad_specs(url, "extrude")` lists that type's parameters: id, kind, unit or enum options, default, and whether it is shown only under a condition. Cost: 1 request the first time per document in this process, then 0. A custom FeatureScript feature has no spec and cannot be added or updated here.
- `cad_inspect(url, feature)` shows every parameter of one feature, by name or id. 0 requests after a `cad_read`. Geometry picks show as a query string or `N geometry ids (set in Onshape)`; only Onshape can change those. Feature lists show feature names, inside arrays too; an empty selection or feature list shows `(none)`. After any update Onshape stores a feature's selections in a compressed form, shown as `selection compressed by Onshape`; it cannot be read here. Verified live: the feature still regenerates and the compressed selection can be sent back in later updates. Whether it follows an upstream edit the way a readable query does is not yet verified (open item in live-checklist.md), so after an upstream change, check such a feature in Onshape.
- `cad_feature_add(url, feature_type, name, params)`: Onshape does not fill parameters left out of a write, so every parameter you omit is sent where the spec allows: numbers, options, true/false, text and lookup tables (such as hole sizes) at the spec default, including ones shown only under a condition; selections, feature lists and arrays empty (nothing is picked for you: name the ones the feature needs). A full array item you give gets its omitted item parameters at their defaults too. The dry run lists all of these in `defaults_applied` (item ones as `param[i].item`). If a default switches off a parameter you set (e.g. `angle` with `cplaneType` left at `OFFSET`), you get `HIDDEN_PARAM`; a parameter with no sendable default is reported as `DEFAULT_NOT_SENT`: set it yourself if the feature needs it. A selection or feature list sent empty that the feature may show is listed in `EMPTY_SELECTION`: name the ones it needs. Dry run 0 requests (+1 if specs are not cached), apply 2 (+1 if specs are not cached).
- `cad_feature_update(url, feature, params)`: only the parameters you name change; everything else is sent back as read. Dry run `changes` shows `before` and `after` per parameter. Dry run 0 after a read (+1 if specs are not cached); apply 2 (+1 if specs are not cached). The reply carries a `note` demanding a re-read in two cases: the feature is not the last one (later features may have changed status), or the write's new version was fetched separately and may include someone else's edit. Run `cad_read` before the next write.
- Read `findings` in every dry run. REFUSE codes: `UNKNOWN_FEATURE_TYPE`, `CUSTOM_FEATURE`, `UNKNOWN_PARAM`, `BAD_VALUE`, `MISSING_UNIT`, `RAW_GEOMETRY_ID`, `UNSUPPORTED_PARAM_KIND`, `SELF_REFERENCE`, `TYPED_TOOL_REQUIRED`. WARN codes: `DEFAULT_NAME`, `MAGIC_NUMBER`, `UNKNOWN_VARIABLE`, `TYPED_TOOL_AVAILABLE`, `HIDDEN_PARAM` (best effort, not evaluated exactly), `DEFAULT_NOT_SENT`, `EMPTY_SELECTION`.

### Value forms
| Spec kind | Value in `params` | Notes |
|---|---|---|
| Quantity (length, angle, unitless, integer) | expression string: `"10 mm"`, `"#plate_w / 2"`, `"30 deg"`; a bare number only for unitless or integer | no unit where the spec has one: `MISSING_UNIT`. `#name` for a variable the document lacks: WARN `UNKNOWN_VARIABLE` |
| Enum | option name string | must be one of the spec's options |
| Boolean | `true` / `false` | |
| String | printable string, length-capped | |
| Query | selector dict, or a list of them | same selector DSL as the typed tools; raw geometry ids and raw BTM JSON are refused (`RAW_GEOMETRY_ID`) |
| Feature list | list of existing feature names or ids | on update, naming the feature itself or a later one: `SELF_REFERENCE` |
| Array | list of item maps, each checked against the array's item spec | see the update patch form below |
| Lookup table (e.g. hole size and standard) | cannot be set by a caller | naming one: `UNSUPPORTED_PARAM_KIND` (add and update). An add sends it at its spec default (listed in `defaults_applied`); an update leaves it as read. `cad_inspect` shows its value, e.g. `{size: 5, holeType: Drilled}` |
| Anything else (references to other elements, appearance) | not buildable | naming one: `UNSUPPORTED_PARAM_KIND`. On add it has no sendable default (`DEFAULT_NOT_SENT`); on update it is left as read |

Array parameters on update take one of two forms:
- a list: replaces the whole array. Refused (`RAW_GEOMETRY_ID`) if any current item holds a geometry pick, since it cannot be restated without losing the human's pick. Each item's omitted parameters are filled with their spec defaults (Onshape rejects an item with a missing parameter), so an item parameter you leave out is reset to its default, not kept; the update reply has no `defaults_applied`, so check `changes` (before/after) to see them.
- a patch `{"items": {"<index>": {parameterId: value}}}`: changes only the named parameters of the named items (0-based index into the array as read) and keeps the rest. An index out of range is `BAD_VALUE`.

### Worked add: an offset plane
```
cad_read(url)
cad_specs(url, "cPlane")                      # parameters: entities (query), offset (LENGTH, default 25 mm), ...
cad_feature_add(url, "cPlane", "Mid plane",
                {"entities": {"entity": "face", "created_by": "Base plate", "cap": "end"},
                 "offset": "5 mm"})
    -> params {...}, defaults_applied {cplaneType: OFFSET, width: 150 mm, ...},
       findings [TYPED_TOOL_AVAILABLE: cad_plane ...] ; prefer cad_plane; generic shown for illustration
cad_feature_add(url, "cPlane", "Mid plane", {...same params...}, apply=True)
cad_read(url)
```
Name the plane's reference in `entities`. Left out, it is sent empty (`EMPTY_SELECTION`) and the plane has nothing to offset from, so it fails to regenerate.
Give the feature a real name: `Plane 1` is a `DEFAULT_NAME` warning.

### Worked update: a fillet radius
```
cad_read(url)
cad_inspect(url, "Edge fillet")               # radius = 5 mm, entities = 4 geometry ids (set in Onshape)
cad_feature_update(url, "Edge fillet", {"radius": "3 mm"})
    -> changes [{parameterId: "radius", before: "5 mm", after: "3 mm"}] ; show the owner
cad_feature_update(url, "Edge fillet", {"radius": "3 mm"}, apply=True)
cad_read(url)                                 # required next if the fillet was not the last feature
```
The picked edges are not touched, because `entities` is not named.

## REFUSE vs WARN
- REFUSE: the sketch has a defect (for example conflicting constraints). Nothing is written, even with `apply=True`. Fix the spec.
- WARN: advice (for example a sketch that is not fully constrained). The write is allowed; tell the owner.

## Typed feature tools

Tools: `cad_find`, `cad_extrude`, `cad_revolve`, `cad_plane`, `cad_fillet`, `cad_chamfer`, `cad_shell`, `cad_boolean`, `cad_pattern` (linear or circular, features only, not parts), `cad_mirror`, `cad_hole` (simple holes, through or blind). `cad_sketch` can also sit on a plane feature or a planar face.

Workflow: `cad_status` -> `cad_read` -> `cad_find(evaluate=True)` for any non-trivial selection -> dry run of the typed tool -> show the owner -> apply -> `cad_read`.

All of them are dry runs unless `apply=True`, are refused if the document changed since the last `cad_read`, and use the REFUSE/WARN rule above.

**References** are by feature name (resolved to ids locally). The exceptions are revolve `axis` and hole `points`: they are entity ids inside a sketch created with `cad_sketch` (for example `"box.left"` for a rectangle side, `"c1.center"` for a circle centre, or a `point` entity id such as `"p1"`). A sketch drawn by hand in the Onshape UI has no such ids, so it cannot be referenced this way. Ids are letters, digits and underscores, dot-separated, at most 64 characters.

**Values:**
- Lengths: a number in mm, `"<n> mm|cm|m|in"`, or `"#variable"` (length variables only).
- Angles: degrees as a number, or `"<n> deg"`. Variables are not supported yet.
- Counts: integers 2..10000.

**Selectors** (`edges`, `faces`, boolean `tools` and `targets`, pattern `direction` and `axis`, face-plane bases): a dict, or a list of 1 to 8 dicts (union). Every dict in a list must use the same `entity`. Unknown keys are refused.

Entities each slot accepts (others are refused):

| tool, slot | entity |
|---|---|
| `cad_fillet` / `cad_chamfer` `edges` | `edge` or `face` (a face selects all its edges) |
| `cad_shell` `faces` | `face` |
| `cad_boolean` `tools`, `targets` | `body` |
| `cad_pattern` linear `direction` | `edge` or `face` |
| `cad_pattern` circular `axis` | `edge` |
| plane bases (`cad_plane`, `cad_mirror`, `cad_sketch`) | `face` with geometry `plane` or a cap |

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

The selection is compiled to an Onshape query stored in the feature (`queryString`), so it is re-evaluated on rebuild. Verified live: the query is kept, the feature regenerates OK, and it follows an upstream edit (a variable driving the base sketch changed 40 -> 50 mm: every query-based feature regenerated OK and moved with it). This holds for the readable query a typed tool writes; once Onshape compresses a selection on update, see `cad_inspect` above.

**Sketch planes:** `cad_sketch` spec `plane` is `"Front"`, `"Top"`, `"Right"`, `{"plane": "<cPlane feature name>"}`, or a face selector with geometry `plane` or a cap.

**Costs:** a dry run is 0 requests when the last-read version is cached, else at most 2. `cad_find` is 0 requests if the document was read; `evaluate=True` adds 1 and returns the match count and centroids in mm. Onshape evaluates the current document: the reply's `evaluated_version` is the version it ran on, and a `note` says when that differs from `based_on_version` (someone edited since your last read; re-read). Apply is 2 requests (version check + write), or 3 when the write reply names no new version.

**Verified live:** `cad_hole` through and blind holes regenerate and cut (status INFO, `REGEN_INFO`, for a diameter that is not a standard table size), and `cad_shell` with `outward=false` gives an inward wall. **Unverified live:** `cad_shell` with `outward=true`; a wrong direction still regenerates OK, so after applying it, check the result in Onshape with the owner.

**Sketches on a plane feature or a face:** the sketch's x/y axes and origin come from that plane or face, so horizontal/vertical, signed distances and the DOF check may not mean what you assume. Check the result in Onshape.

**Lint codes:** `DEFAULT_NAME` (warn, names like `Extrude 1`), `DUPLICATE_NAME` (refuse; `cad_sketch` too), `UNKNOWN_VARIABLE` (refuse), `NON_LENGTH_VARIABLE` (refuse), `MAGIC_NUMBER` (warn). For `MAGIC_NUMBER`, a parameter of another feature that sits at its spec default (generic adds and the Onshape UI store every hidden default) does not count, when that feature type's spec is cached (any generic tool call fetches it; hole defaults are always known); a hole counts only its diameter, and its depth when blind.

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
Generic writes have not been run against live Onshape; see docs/live-checklist.md.

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
