# OnshapeDuet

A Python MCP server that lets an AI agent read and edit Onshape Part Studios in a safe, budgeted way. MIT licensed. Early work in progress.

## What exists

Tools: `cad_status`, `cad_read` (outline of features and variables), `cad_sketch` (create a sketch from a spec, with a local degrees-of-freedom check and lint), `cad_variable` (create or update a variable), and the generic feature layer: `cad_specs` (native feature types and their parameters), `cad_inspect` (every parameter of one feature), `cad_feature_add` (add a native feature of any type from its spec) and `cad_feature_update` (change named parameters of an existing feature). Writes are dry runs unless `apply=True`, are refused if the document changed since the last `cad_read`, and every HTTP request is counted against a persistent budget before it is sent.

## What is verified

All of it is verified against an in-memory fake backend and a scripted HTTP transport only (the test suite). The generic feature tools (`cad_specs`, `cad_inspect`, `cad_feature_add`, `cad_feature_update`) are verified offline only, against the fake backend and a scripted transport, with a hand-written feature-specs fixture. **Nothing has been run against live Onshape.** The endpoint paths, JSON shapes, variable model and sketch serialization are assumptions; each one is listed with the exact check to run in [docs/live-checklist.md](docs/live-checklist.md). Until that checklist is done, treat writes as experimental and use a throwaway document. Not implemented: reading existing sketches into DOF reports, typed tools for features other than sketches and variables (the generic tools cover them), Windows (the ledger needs POSIX file locking), version (`/v/`) URLs.

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
