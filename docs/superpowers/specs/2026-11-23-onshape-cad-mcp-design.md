# Onshape CAD MCP (working name) — Design

Status: DRAFT, awaiting approval. Date: 2026-11-23.

## 1. Intent

An MCP server, in Python and MIT-licensed, that lets Claude work in Onshape on a footing equal to a competent
human CAD user. Two directions, both first-class:

- **Claude → human:** Claude builds CAD a person can open in Onshape and keep editing by hand. Sketches are
  constrained and dimensioned, numbers that matter live in named variables, features and parts have real names.
  The person never has to rebuild Claude's work.
- **Human → Claude:** a person builds CAD in Onshape, and Claude can read it, understand it, and edit it
  safely, including features this project has no dedicated tool for.

Why: the owner has used `hedless/onshape-mcp` extensively. It is capable, but it lets Claude emit
under-constrained, hard-to-edit output, because constraints and dimensions are optional arguments and nothing
checks the result. The owner also pays for Onshape API calls out of a yearly allocation, so call count matters.

## 2. Goals

1. Output a human can edit: constrained sketches, named variables, named features/parts, no dependence on
   transient geometry ids.
2. Wide capability: Claude can create or edit **any native Onshape feature**, not just a beginner set.
3. Frugal with API calls: every request counted, a budget only the owner can raise, reads cached by document
   version, writes dry-run first.
4. Safe collaboration: never overwrite a human's edits; every write is pinned to the version Claude last read.
5. Honest feedback: tools report measured numbers and regeneration status, never a bare "OK".

## 3. Non-goals

- Not text-to-CAD without a person. The owner remains the designer.
- Not a wrapper of every Onshape REST endpoint (no admin, billing, teams, comments, etc.).
- No TypeScript or non-Python components. No hosted service.

## 4. Anti-goals (a failure even if it technically works)

- FeatureScript authoring. Claude must not solve problems by writing custom FeatureScript features. Read-only
  FeatureScript evaluation (queries, measurements) used internally is acceptable; user-facing custom-feature
  creation is out of scope.
- Output that looks finished but collapses when one dimension changes.
- Silently exceeding the API budget, or silently overwriting a human's edit.

## 5. Constraints

- Python 3.11+, MIT license. Everything copied in must be MIT-compatible with attribution preserved.
- **Cannot reuse GPL code.** `bogdanr/onshape-mcp` is GPL-3.0: its ideas (sketch gate via Jacobian rank, budget
  ledger, version pinning, dry-run default, outline read) may inform the design, but its code must not be
  copied. The sketch solver is written clean-room from the algorithm (rank of the constraint Jacobian), not
  from that source.
- `hedless/onshape-mcp` has no LICENSE file in the repo (README says MIT). Do not copy its code.
- Provenance caution: `saltyeg/cadkit-mcp` and `ReshefElisha/jarvis-onshape-mcp` are forks that contain
  hedless-derived scaffolding. Port only their own additions (cadkit: `cadkit_mcp/`; jarvis: `sketch_constraints.py`,
  `sketch_edit.py`, `sketch_inspect.py`, `geometry_diff.py`, hint logic), and verify provenance per file before
  copying.
- Onshape does not expose true "fully defined" status for a sketch through the API, so degrees of freedom must
  be computed locally.

## 6. Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **1 (chosen)** | Generic feature core (reads Onshape's own feature specs, validates, adds/updates any feature) + typed tools for beginner/common features + an editability lint on everything | Breadth with enforcement where it can be proven; lint on exotic features is rule-based, not proof |
| 2 | One typed tool per feature (cadkit style) | Strongest per-feature guarantees but coverage stays narrow and every feature is new work |
| 3 | Raw spec passthrough + a prompt/skill document | Widest and cheapest to write, but editability depends on prompting — the hedless failure mode |

Chosen by the owner: **1**, with typed tools for beginner/common features and a two-tier lint
(refuse "extremely bad", strongly warn on "normal bad").

## 7. Design

### 7.1 Layers

```
MCP tools (thin)           cad_read, cad_set, cad_sketch, cad_extrude, ... cad_feature_add, ...
  │
Editability lint           classifies every proposed write: OK / STRONG WARNING / REFUSE
  │
Typed builders             sketch, plane, extrude, revolve, fillet, chamfer, hole, shell, pattern, mirror...
Generic feature layer      feature specs → validate → add/update any native feature
  │
Document model + cache     feature list, variables, geometry summary, keyed by microversion
  │
Budget ledger + HTTP       counts every request before it is sent; version-pinned writes
```

A "backend" interface keeps Onshape-specific code behind one boundary (other CAD systems or a fake for tests).

### 7.2 Tool surface (about 20, design-intent level)

- **Read:** `cad_status` (budget, config, keys), `cad_read` (one-screen outline of a Part Studio: features,
  status, editable numbers, per-sketch DOF/dimension report), `cad_inspect` (one feature in full),
  `cad_check` (regen errors + measured bodies: volume, bbox, holes/bosses, diff since last check),
  `cad_find` (semantic edge/face selection: circular, concave, planar-by-normal, extreme, on-plane),
  `cad_specs` (look up a native feature's spec/parameters).
- **Typed write (beginner/common):** `cad_variable`, `cad_plane`, `cad_sketch`, `cad_extrude`, `cad_revolve`,
  `cad_fillet`, `cad_chamfer`, `cad_hole`, `cad_shell`, `cad_pattern`, `cad_mirror`, `cad_boolean`.
- **Generic write:** `cad_feature_add`, `cad_feature_update` (any native feature, spec-validated).
- **Lifecycle:** `cad_rename`, `cad_suppress`, `cad_delete`, `cad_version` (create / list / rollback).
- **Assemblies (later milestone):** instances, mates, interference.

Every write tool is **dry-run by default**, shows its cost in requests and the lint verdict, and applies only
when told to. Every response ends with requests used and budget left.

`cad_sketch` takes one declarative spec (entities, constraints, dimensions, plane, variable references) and
produces one API write, instead of a many-call session. The spec is solved locally first.

### 7.3 Editability lint

Runs on every write, typed or generic. Two tiers:

**REFUSE** (write is not sent; message says what to fix):
- A typed sketch with free movement and *no* dimensions at all (floating geometry, not tied to the origin or
  any reference).
- A write that depends on a transient geometry id that will not survive edits (raw face/edge ids), where a
  semantic selection or named reference could be used.
- A write whose regeneration would produce an error state (checked after apply; the write is reported and
  Claude is told to fix or roll back).
- A write against a document version older than the last read (human edited in between).
- Any write that would exceed the owner's request budget.

**STRONG WARNING** (write proceeds; warning is prominent in the response and repeated in `cad_read`):
- Sketch partially defined: some dimensions present but some entities can still move (lists exactly which).
- Magic numbers where a variable would be natural (a value repeated across features, or a derived value).
- Default feature/part names (`Extrude 1`, `Sketch 3`).
- Unnamed parts, regen warnings, features that reference other features by position rather than name.
- For generic-feature writes: parameters that cannot be classified (the lint says what it could not check).

Tier membership is configurable per project; the defaults above are the design's call (see §10).

### 7.4 Sketch gate

Constraints and dimensions are modelled locally; the rank of the constraint Jacobian gives the number of free
movements and which entities move. This runs offline (zero API calls) for typed sketches, and for existing
sketches read from the document where the constraint types are supported (unsupported types are reported as
"not assessed", never guessed).

### 7.5 API frugality

- Reads: one feature-list fetch per document version, cached; outline, inspect and lint use the cache.
- Writes: dry run is free; apply costs the minimum number of requests; batch where Onshape allows (to be
  measured, not assumed).
- Ledger file counts every request (including retries and redirects) before sending; budget defaults to 0 and
  only the owner can raise it.
- A call-count test harness records real request counts per standard job, so claims about cost are measured.

### 7.6 Human → Claude

- `cad_read` produces an outline of any feature tree, including feature types we have no typed tool for.
- `cad_inspect` / `cad_feature_update` can read and edit parameters on any feature via the generic layer.
- When a human's sketch uses constraint types we don't model, we say "not assessed" and still allow edits that
  don't touch it.
- Version pinning means Claude re-reads after a human edit instead of overwriting it.

### 7.7 Claude → human

- Typed tools emit native features with real names, variables from the variable table, and constrained
  sketches.
- A closing `cad_read` shows the owner the same outline they will see in Onshape.
- A project `CLAUDE.md` snippet (shipped with the server) tells agents the workflow: status, read, dry-run,
  show owner, apply, check.

## 8. Reuse plan (MIT-compatible only)

| Capability | Source | Notes |
|---|---|---|
| Sketch constraint/dimension JSON serializers | jarvis `sketch_constraints.py`, cadkit `cadkit_mcp/sketch.py` | Port with attribution; reconcile differences |
| Mirror, pattern, slot, fillet-in-sketch | cadkit `cadkit_mcp/sketch.py` | |
| Semantic edge/face selection | cadkit `cadkit_mcp/selection.py` | |
| Geometry diffs, regen hints, describe | jarvis `geometry_diff.py`, hints, `describe.py` | |
| Generic feature specs idea; versions/rollback, configurations, drawings, BOM, validate | gpambrozio (TypeScript) | **Ideas only**; reimplement in Python |
| Mate builders, face coordinate systems (assembly milestone) | cadkit/jarvis builders | Verify provenance (hedless-derived) first |
| Budget ledger, version pinning, dry-run, outline, gate | bogdanr (GPL) | **Ideas only; clean-room** |
| Sketch solver (Jacobian rank) | Original | Clean-room |

## 9. Testing

- Unit tests with no network: a fake Onshape backend with fixtures cut from real responses.
- Solver tests: known fully-defined, under-defined and over-constrained sketches.
- Lint tests: each REFUSE/WARN rule has a passing and a failing case.
- One live smoke test on an empty Part Studio, with its request count recorded and asserted.

## 10. Calls made without the owner (please check)

1. Project name is a placeholder.
2. The REFUSE/WARN split in §7.3 — in particular, that a partially dimensioned sketch is a *strong warning*,
   not a refusal, and only a wholly floating/undimensioned sketch is refused.
3. `cad_sketch` is a single declarative call, not a multi-call session.
4. Auth: API key pair first (simplest, matches the owner's existing hedless setup); OAuth later if wanted.
5. Typed-tool list in §7.2 and ordering of milestones below.
6. Assemblies deferred to a later milestone.
7. Budget default 0 (owner must set a number before any write or live read).

## 11. Milestones (for the plan)

1. Skeleton: config, budget ledger, backend interface, fake backend, `cad_status`.
2. Read path: document model + cache, `cad_read`, `cad_inspect`, `cad_check`.
3. Sketch solver + `cad_sketch` + variables + lint core.
4. Typed features (extrude, revolve, fillet, chamfer, hole, shell, pattern, mirror, boolean, plane) + `cad_find`.
5. Generic feature layer (`cad_specs`, `cad_feature_add/update`) with lint rules for unmodelled features.
6. Lifecycle tools (rename, suppress, delete, versions/rollback).
7. Assemblies (instances, mates, interference).
8. Call-count benchmark and README/agent guide.

## 12. Left to the builder

Exact module layout, MCP SDK choice, the shape of the sketch spec format, how Onshape's feature-spec endpoints
are called (to be verified against live responses), and the specific heuristics for the "magic number" and
"transient reference" lint rules.
