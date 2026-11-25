# OnshapeDuet

A Python MCP server that lets an AI agent read and edit Onshape Part Studios in a safe, budgeted way. MIT licensed. Early work in progress.

## What exists

Tools: `cad_status`, `cad_read` (outline of features and variables), `cad_sketch` (from a spec, with a local degrees-of-freedom check and lint; on Front/Top/Right, a plane feature or a planar face), `cad_variable`, and the typed feature tools `cad_find` (selector preview), `cad_extrude`, `cad_revolve`, `cad_plane`, `cad_fillet`, `cad_chamfer`, `cad_shell`, `cad_boolean`, `cad_pattern`, `cad_mirror` and `cad_hole` (simple holes). Writes are dry runs unless `apply=True`, are refused if the document changed since the last `cad_read`, and every HTTP request is counted against a persistent budget before it is sent. See [docs/agent-guide.md](docs/agent-guide.md).

## What is verified

The test suite runs against an in-memory fake backend and a scripted HTTP transport. The foundation tools (read, sketch, variable) and several typed-feature probes (featurespecs, `cad_find` evaluation, extrude query persistence) have also been run against live Onshape; results and the items still open are in [docs/live-checklist.md](docs/live-checklist.md). Use a throwaway document for writes. Not implemented: reading existing sketches into DOF reports, counterbore/countersink holes, part patterns, Windows (the ledger needs POSIX file locking), version (`/v/`) URLs.

## Setup

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

Create an Onshape API key and export it (names are configurable):

```
export ONSHAPE_ACCESS_KEY=...   ONSHAPE_SECRET_KEY=...
```

Config file `~/.onshape-duet/config.toml` (or the path in `ONSHAPE_DUET_CONFIG`):

```
budget = 40                     # max Onshape requests, ever, until you raise it; default 0 sends nothing
# ledger_path = "~/.onshape-duet/ledger.json"
# key_env = "ONSHAPE_ACCESS_KEY"
# secret_env = "ONSHAPE_SECRET_KEY"
# base_url = "https://cad.onshape.com"   # enterprise stacks: set your own host; URLs for other hosts are refused
```

Register the `onshape-duet` command as a stdio MCP server in your client. Credentials are read from the environment only.

## Docs

- [docs/agent-guide.md](docs/agent-guide.md): how an agent should use the tools.
- [docs/testing-guide.md](docs/testing-guide.md): step-by-step test plan a fresh Claude session can follow (offline, MCP wiring, live).
- [docs/live-checklist.md](docs/live-checklist.md): what must still be verified on live Onshape.
