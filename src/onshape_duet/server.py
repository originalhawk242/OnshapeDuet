"""MCP server (stdio) exposing cad_status, cad_read, cad_sketch, cad_variable."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from pydantic import StrictBool

from .backends.onshape import OnshapeBackend
from .budget import Ledger
from .cache import DocCache
from .config import load_config
from .docref import parse_url
from .tools import (Context, cad_feature_add, cad_feature_update, cad_inspect, cad_read,
                    cad_sketch, cad_specs, cad_status, cad_variable)

DEFAULT_CONFIG = Path.home() / ".onshape-duet" / "config.toml"


def build_context(config_path: Path | None) -> Context:
    """Load config and wire the real backend. Makes no network request."""
    config = load_config(config_path)
    ledger = Ledger(config.ledger_path, config.budget)
    backend = OnshapeBackend(config, ledger)
    return Context(backend, DocCache(backend), ledger, config, config_path=config_path)


def build_server(ctx: Context) -> MCPServer:
    mcp = MCPServer("onshape-duet")

    @mcp.tool(name="cad_status")
    def cad_status_tool() -> dict:
        """Show request budget (budget, used, remaining) and whether API credentials
        are set. Costs zero requests. Call this first."""
        return cad_status(ctx)

    @mcp.tool(name="cad_read")
    def cad_read_tool(url: str) -> str:
        """Read a Part Studio: features, variables, requests used and budget left.
        Required before any apply=True write (a write is refused if the document
        changed after your last read). Existing sketches show `not assessed`."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return f"error: {e}"
        return cad_read(ctx, ref)

    @mcp.tool(name="cad_sketch")
    def cad_sketch_tool(url: str, name: str, spec: dict, apply: StrictBool = False) -> dict:
        """Create a sketch from a spec. DRY RUN IS THE DEFAULT: nothing is written
        unless apply=True. Show the dry-run result (dof, findings) to the owner,
        then apply, and read afterwards. Read the document (cad_read) before apply.
        The lint REFUSES (writes nothing) on conflicts/contradictions in the
        sketch; WARN findings are advice only."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_sketch(ctx, ref, name, spec, apply)

    @mcp.tool(name="cad_variable")
    def cad_variable_tool(url: str, name: str, expression: str, apply: StrictBool = False) -> dict:
        """Create or update a Part Studio variable (name ^[a-z][a-z0-9_]*$,
        expression e.g. "10 mm"). DRY RUN IS THE DEFAULT: nothing is written unless
        apply=True. Read the document (cad_read) before apply; stale writes are refused."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_variable(ctx, ref, name, expression, apply)

    @mcp.tool(name="cad_feature_add")
    def cad_feature_add_tool(url: str, feature_type: str, name: str, params: dict,
                             apply: StrictBool = False) -> dict:
        """Add a native Onshape feature of any type from its spec (see cad_specs).
        DRY RUN IS THE DEFAULT: nothing is written unless apply=True. Read the
        document (cad_read) before apply; stale writes are refused. Geometry
        parameters take selector dicts in the same DSL as cad_find; raw geometry
        ids are refused. Prefer the typed tools (cad_extrude etc.) when one exists
        for the feature. Cost: dry run 0 requests (1 the first time the specs are
        fetched), apply 2."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_feature_add(ctx, ref, feature_type, name, params, apply)

    @mcp.tool(name="cad_feature_update")
    def cad_feature_update_tool(url: str, feature: str, params: dict,
                                apply: StrictBool = False) -> dict:
        """Update parameters of an existing native feature, by name or id. DRY RUN IS
        THE DEFAULT: nothing is written unless apply=True. Read the document
        (cad_read) first; stale writes are refused. Changes only the named
        parameters and sends everything else back exactly as read. Array
        parameters take a list (replaces all items; refused over human-picked
        geometry) or {"items": {"<index>": {...}}} to patch single items. Cost: 0
        requests for a dry run after a read, 2 for apply. After updating a feature
        that is not the last one, re-read (cad_read) before the next write."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_feature_update(ctx, ref, feature, params, apply)

    @mcp.tool(name="cad_specs")
    def cad_specs_tool(url: str, feature_type: str | None = None) -> dict:
        """List Onshape's native feature types, or with feature_type (e.g. "extrude")
        that type's parameters (kind, units, options, defaults). Costs 1 request the
        first time for a document, then 0. Never writes."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_specs(ctx, ref, feature_type)

    @mcp.tool(name="cad_inspect")
    def cad_inspect_tool(url: str, feature: str) -> dict:
        """Show every parameter of one feature, by name or id (what cad_read omits).
        Costs 0 requests after a cad_read of the document; geometry selections show
        only a count, since they can be changed in Onshape only."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_inspect(ctx, ref, feature)

    return mcp


def main() -> None:
    env = os.environ.get("ONSHAPE_DUET_CONFIG")
    path = Path(env).expanduser() if env else DEFAULT_CONFIG
    try:
        ctx = build_context(path)
    except ValueError as e:
        print(f"onshape-duet: invalid config {path}: {e}", file=sys.stderr)
        raise SystemExit(2) from None
    build_server(ctx).run("stdio")


if __name__ == "__main__":
    main()
