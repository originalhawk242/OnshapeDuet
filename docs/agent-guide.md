# Agent guide: onshape-duet

Workflow: `cad_status` -> `cad_read` -> dry run -> show the owner -> apply -> `cad_read`.

1. `cad_status`: budget, used, remaining, whether credentials are set. Costs zero requests.
2. `cad_read(url)`: features and variables of the Part Studio. Required before any apply; a write is refused as stale if the document changed after your last read.
3. Dry run (`cad_sketch` / `cad_variable` with `apply=False`, the default): nothing is written. You get the DOF count and lint findings.
4. Show the dry-run result to the owner and get a yes.
5. Apply (`apply=True`), then `cad_read` again to confirm.

If a write reply says `applied: "unknown"`, or carries a `note` telling you to re-read (for example after creating a variable), run `cad_read` before anything else: the write may have landed, and the next write is refused until you do. A `REGEN_STATUS` error finding means Onshape wrote the feature but it does not regenerate cleanly.

## Generic features
Typed tools first where one exists. Use the generic tools for native feature types that have no typed tool, for options a typed tool lacks, and for editing features a human made.
`cad_sketch` and `cad_variable` stay the only way to write sketches and variables: the generic tools refuse them (`TYPED_TOOL_REQUIRED`).

Workflow: `cad_read` -> `cad_specs` -> `cad_feature_add` / `cad_feature_update` dry run -> show the owner -> apply -> `cad_read`.

- `cad_specs(url)` lists native feature types. `cad_specs(url, "extrude")` lists that type's parameters: id, kind, unit or enum options, default, and whether it is shown only under a condition. Cost: 1 request the first time per document in this process, then 0. A custom FeatureScript feature has no spec and cannot be added or updated here.
- `cad_inspect(url, feature)` shows every parameter of one feature, by name or id. 0 requests after a `cad_read`. Geometry picks show as a query string or `N geometry ids (set in Onshape)`; only Onshape can change those.
- `cad_feature_add(url, feature_type, name, params)`: parameters you omit take Onshape's defaults; the dry run lists them in `defaults_applied`. Dry run 0 requests (+1 if specs are not cached), apply 2 (+1 if specs are not cached).
- `cad_feature_update(url, feature, params)`: only the parameters you name change; everything else is sent back as read. Dry run `changes` shows `before` and `after` per parameter. Dry run 0 after a read (+1 if specs are not cached); apply 2 (+1 if specs are not cached). The reply carries a `note` demanding a re-read in two cases: the feature is not the last one (later features may have changed status), or the write's new version was fetched separately and may include someone else's edit. Run `cad_read` before the next write.
- Read `findings` in every dry run. REFUSE codes: `UNKNOWN_FEATURE_TYPE`, `CUSTOM_FEATURE`, `UNKNOWN_PARAM`, `BAD_VALUE`, `MISSING_UNIT`, `RAW_GEOMETRY_ID`, `UNSUPPORTED_PARAM_KIND`, `SELF_REFERENCE`, `TYPED_TOOL_REQUIRED`. WARN codes: `DEFAULT_NAME`, `MAGIC_NUMBER`, `UNKNOWN_VARIABLE`, `TYPED_TOOL_AVAILABLE`, `HIDDEN_PARAM` (best effort, not evaluated exactly).

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
| Anything else (lookup tables such as hole standards, references to other elements, appearance) | not buildable | add: `UNSUPPORTED_PARAM_KIND`; update: left alone unless named, then refused |

Array parameters on update take one of two forms:
- a list: replaces the whole array. Refused (`RAW_GEOMETRY_ID`) if any current item holds a geometry pick, since it cannot be restated without losing the human's pick.
- a patch `{"items": {"<index>": {parameterId: value}}}`: changes only the named parameters of the named items (0-based index into the array as read) and keeps the rest. An index out of range is `BAD_VALUE`.

### Worked add: an offset plane
```
cad_read(url)
cad_specs(url, "cPlane")                      # parameters: offset (LENGTH, default 10 mm), ...
cad_feature_add(url, "cPlane", "Mid plane", {"offset": "5 mm"})
    -> params {"offset": "5 mm"}, defaults_applied {...}, findings [] ; show the owner
cad_feature_add(url, "cPlane", "Mid plane", {"offset": "5 mm"}, apply=True)
cad_read(url)
```
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
