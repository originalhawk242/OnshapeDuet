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
