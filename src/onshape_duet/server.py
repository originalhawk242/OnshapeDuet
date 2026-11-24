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
from .tools import Context, cad_read, cad_sketch, cad_status, cad_variable

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
