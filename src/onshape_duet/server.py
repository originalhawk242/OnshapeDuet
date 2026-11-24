"""MCP server (stdio) exposing cad_status, cad_read, cad_sketch, cad_variable."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from pydantic import StrictBool, StrictFloat, StrictInt, StrictStr

from .backends.onshape import OnshapeBackend
from .budget import Ledger
from .cache import DocCache
from .config import load_config
from .docref import parse_url
from .feature_tools import (cad_boolean, cad_shell, cad_chamfer, cad_extrude, cad_fillet, cad_find, cad_plane,
                           cad_revolve)
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

    @mcp.tool(name="cad_find")
    def cad_find_tool(url: str, selector: dict | list, evaluate: StrictBool = False) -> dict:
        """Preview a selection. The selector dict (entity face/edge/body, created_by,
        cap, edges_of, geometry, normal, farthest) is the same one every typed feature
        tool takes for edges/faces/bodies. Dry (default): returns the compiled query;
        costs 0 requests if the document was read with cad_read. evaluate=True runs one
        read-only evaluation (+1 request) and returns the match count and centroids in
        mm. Never returns transient ids; nothing is written."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"error": str(e)}
        return cad_find(ctx, ref, selector, evaluate)

    @mcp.tool(name="cad_extrude")
    def cad_extrude_tool(url: str, name: str, sketch: str, depth: StrictFloat | StrictInt | StrictStr | None = None,
                         operation: str = "new", end: str = "blind", flip: StrictBool = False,
                         apply: StrictBool = False) -> dict:
        """Extrude the profile of an existing sketch (referenced by feature name).
        depth: mm number, '10 mm'/'2 in', or '#variable'; required unless end is
        through_all (then it is refused). operation: new|add|remove|intersect (add,
        remove and intersect merge with all bodies). end: blind|symmetric|through_all.
        flip reverses the direction. DRY RUN IS THE DEFAULT: nothing is written unless
        apply=True. Show the dry-run result (summary, findings) to the owner, then
        apply, and read afterwards. Read the document (cad_read) before apply; a stale
        write is refused. The lint REFUSES (writes nothing) on e.g. a duplicate feature
        name or an unknown variable; WARN findings (magic numbers, default names) are
        advice only. Costs: a dry run is free once the document was read; apply costs 2
        requests (version check + write), or 3 when the write reply names no new version."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_extrude(ctx, ref, name, sketch, depth, operation, end, flip, apply)

    @mcp.tool(name="cad_plane")
    def cad_plane_tool(url: str, name: str, base: str | dict,
                       offset: StrictFloat | StrictInt | StrictStr, flip: StrictBool = False,
                       apply: StrictBool = False) -> dict:
        """Create an offset construction plane. base: Front|Top|Right, {"plane": <name of
        an existing cPlane feature>}, or a planar face selector (geometry "plane" or a cap,
        e.g. {"entity": "face", "created_by": "Body", "cap": "end"}). offset: mm number,
        '10 mm'/'2 in', or '#variable'. flip offsets to the other side. Sketches can then
        sit on it via cad_sketch spec plane {"plane": <name>}. DRY RUN IS THE DEFAULT:
        nothing is written unless apply=True. Show the dry-run result to the owner, then
        apply, and read afterwards. Read the document (cad_read) before apply; a stale
        write is refused. Costs: a dry run is free once the document was read; apply costs
        2 requests (version check + write), or 3 when the write reply names no new version."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_plane(ctx, ref, name, base, offset, flip, apply)

    @mcp.tool(name="cad_revolve")
    def cad_revolve_tool(url: str, name: str, sketch: str, axis: str,
                         angle: StrictFloat | StrictInt | StrictStr = 360,
                         operation: str = "new", symmetric: StrictBool = False,
                         flip: StrictBool = False, apply: StrictBool = False) -> dict:
        """Revolve the profile of an existing sketch (referenced by feature name) about a
        line in that sketch. axis: the sketch line's entity id, e.g. "box.left" (letters,
        digits, underscore, dots only). angle: degrees number or '90 deg' (0 < angle <= 360;
        360 is a full revolve; '#variable' is not supported). symmetric spreads a partial
        angle across both sides of the sketch plane (not allowed with 360). operation:
        new|add|remove|intersect (add, remove and intersect merge with all bodies). flip
        reverses the direction. DRY RUN IS THE DEFAULT: nothing is written unless
        apply=True. Show the dry-run result (summary, findings) to the owner, then apply,
        and read afterwards. Read the document (cad_read) before apply; a stale write is
        refused. Costs: a dry run is free once the document was read; apply costs 2
        requests (version check + write), or 3 when the write reply names no new version."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_revolve(ctx, ref, name, sketch, axis, angle, operation, symmetric, flip, apply)

    @mcp.tool(name="cad_fillet")
    def cad_fillet_tool(url: str, name: str, edges: dict | list,
                    radius: StrictFloat | StrictInt | StrictStr,
                    apply: StrictBool = False) -> dict:
        """Round (fillet) the selected edges with a constant circular radius. edges: an edge selector (or a list of them, or a face selector, which
        selects all of that face's edges), e.g. {"entity": "edge", "created_by": "Base"};
        body selectors are refused. radius: length number (mm), '2 in' or '#variable'. DRY RUN
        IS THE DEFAULT: nothing is written unless apply=True. Show the dry-run result
        (summary, findings) to the owner, then apply, and read afterwards. Read the document
        (cad_read) before apply; a stale write is refused. Costs: a dry run is free once the
        document was read; apply costs 2 requests (version check + write), or 3 when the
        write reply names no new version."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_fillet(ctx, ref, name, edges, radius, apply)

    @mcp.tool(name="cad_chamfer")
    def cad_chamfer_tool(url: str, name: str, edges: dict | list,
                    distance: StrictFloat | StrictInt | StrictStr,
                    apply: StrictBool = False) -> dict:
        """Chamfer the selected edges with equal offsets. edges: an edge selector (or a list of them, or a face selector, which
        selects all of that face's edges), e.g. {"entity": "edge", "created_by": "Base"};
        body selectors are refused. distance: length number (mm), '2 in' or '#variable'. DRY RUN
        IS THE DEFAULT: nothing is written unless apply=True. Show the dry-run result
        (summary, findings) to the owner, then apply, and read afterwards. Read the document
        (cad_read) before apply; a stale write is refused. Costs: a dry run is free once the
        document was read; apply costs 2 requests (version check + write), or 3 when the
        write reply names no new version."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_chamfer(ctx, ref, name, edges, distance, apply)

    @mcp.tool(name="cad_shell")
    def cad_shell_tool(url: str, name: str, faces: dict | list,
                       thickness: StrictFloat | StrictInt | StrictStr,
                       outward: StrictBool = False, apply: StrictBool = False) -> dict:
        """Hollow a solid: remove the selected faces and leave walls of the given thickness.
        faces: a face selector (or a list of them) for the faces to remove, e.g.
        {"entity": "face", "created_by": "Base", "cap": "end"}; edge and body selectors are
        refused. thickness: length number (mm), '2 in' or '#variable'. outward: grow the wall
        outward instead of inward (default false). DRY RUN IS THE DEFAULT: nothing is written
        unless apply=True. Show the dry-run result (summary, findings) to the owner, then apply,
        and read afterwards. Read the document (cad_read) before apply; a stale write is
        refused. Costs: a dry run is free once the document was read; apply costs 2 requests
        (version check + write), or 3 when the write reply names no new version."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_shell(ctx, ref, name, faces, thickness, outward, apply)

    @mcp.tool(name="cad_boolean")
    def cad_boolean_tool(url: str, name: str, operation: str, tools: dict | list,
                         targets: dict | list | None = None, keep_tools: StrictBool = False,
                         apply: StrictBool = False) -> dict:
        """Combine bodies. operation: union (merge the tools), subtract (cut the tools out of
        targets) or intersect (keep the common volume of the tools). tools/targets: body
        selectors (or lists of them), e.g. {"entity": "body", "created_by": "Base"}; other
        entities are refused. targets is required for subtract and refused otherwise.
        keep_tools: keep the tool bodies afterwards (default false). DRY RUN IS THE DEFAULT:
        nothing is written unless apply=True. Show the dry-run result (summary, findings) to the
        owner, then apply, and read afterwards. Read the document (cad_read) before apply; a
        stale write is refused. Costs: a dry run is free once the document was read; apply costs
        2 requests (version check + write), or 3 when the write reply names no new version."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_boolean(ctx, ref, name, operation, tools, targets, keep_tools, apply)

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
