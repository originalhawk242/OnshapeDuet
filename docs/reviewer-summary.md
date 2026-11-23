# Onshape CAD MCP — Summary for Reviewers

Working name; Python, MIT-licensed. Status: design draft, nothing built yet.

## What it is

An MCP server that lets an AI agent (Claude) create and edit parametric CAD in Onshape through Onshape's REST API.
Reviewer assumed to know CAD fundamentals (sketches, constraints, features, variables), REST APIs and MCP.

## The problem

Existing Onshape MCP servers are mostly thin wrappers over the REST API. They let an agent produce CAD that looks
right but is hard to edit: sketches that are under-constrained, numbers hard-coded instead of driven by variables,
default feature names, references to geometry ids that break on edit. Constraints and dimensions are usually
optional arguments, so nothing stops a lazy call. Separately, Onshape bills API usage against a yearly allocation,
so wasteful call patterns have a real cost.

## Goals

1. **Claude → human:** Claude's output is editable by a person in Onshape: constrained and dimensioned sketches,
   named variables, named features and parts.
2. **Human → Claude:** Claude can read and safely edit CAD a person built, including feature types the server has
   no dedicated tool for.
3. **Wide capability:** any native Onshape feature can be created or edited, not just a beginner subset.
4. **Frugal:** every API request counted against an owner-set budget; reads cached per document version; writes
   are dry-run by default.
5. **Safe:** writes are pinned to the document version last read, so a human's concurrent edits are never
   overwritten.

## Non-goals / anti-goals

- No custom FeatureScript authoring by the agent (read-only evaluation for measurement is acceptable).
- Not a wrapper of all REST endpoints; not text-to-CAD without a human in the loop; no hosted service.
- A model that rebuilds fine now but breaks when one dimension changes counts as failure.

## Design

```
MCP tools (~20, design-intent level)
  -> Editability lint (every write: OK / STRONG WARNING / REFUSE)
  -> Typed builders (common features)  |  Generic feature layer (any native feature, spec-validated)
  -> Document model + cache (keyed by document microversion)
  -> Request budget ledger + HTTP client (version-pinned writes)
```

- **Typed tools** for common features: variable, plane, sketch, extrude, revolve, fillet, chamfer, hole, shell,
  pattern, mirror, boolean. Sketches are one declarative spec (entities, constraints, dimensions) written in one
  API call.
- **Generic tools** read Onshape's own feature specs, validate a proposed feature against them, then add or
  update it. This provides breadth without a tool per feature.
- **Read tools:** an outline of the feature tree (statuses, editable numbers, per-sketch constraint report), a
  single-feature inspector, a geometry check (volume, bounding box, holes, diff since last check), and semantic
  edge/face selection (e.g. "concave edges", "cylindrical face of radius r") instead of raw ids.
- **Sketch gate:** Onshape's API does not report whether a sketch is fully defined, so constraints are modelled
  locally and the Jacobian rank gives the free degrees of freedom and which entities can still move. Runs with
  zero API calls.
- **Editability lint:**
  - *Refuse:* wholly floating, undimensioned sketch; use of fragile geometry ids where a semantic reference
    exists; stale document version; regeneration error; budget exceeded.
  - *Strong warning:* partially defined sketch (lists what moves), magic numbers that should be variables,
    default names, unnamed parts, regen warnings, and for generic features, anything the lint could not
    classify.
- **API frugality:** one feature-list fetch per document version, cached; dry runs free; budget defaults to 0 and
  only the owner raises it; a benchmark harness measures real request counts per standard job.

## Constraints

- Python 3.11+, MIT. Some prior-art projects are GPL or unlicensed, so their code cannot be copied; only ideas
  are reused, and the sketch solver is written clean-room.
- Assemblies and mates come after part modelling.

## Where I want your scrutiny

1. Is "refuse only wholly floating sketches, warn on partially defined" the right line, or does it let bad CAD
   through?
2. For generic (unmodelled) features the lint is rule-based, not proof. Which rules would you add to catch
   non-editable output?
3. Is a local Jacobian-rank check a sound proxy for Onshape's own solver status? Failure cases (redundant or
   conflicting constraints, unsupported constraint types)?
4. Cache keyed by document microversion plus version-pinned writes: any race or staleness gaps with a human
   editing concurrently?
5. Is a generic spec-driven feature layer a safe way to give an agent wide capability, or does it reintroduce
   the failure the project exists to prevent?
6. What important CAD workflows or editability failure modes are missing?
