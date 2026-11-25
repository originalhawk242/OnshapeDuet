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
from .feature_tools import (cad_boolean, cad_shell, cad_chamfer, cad_extrude, cad_fillet, cad_find, cad_hole, cad_mirror,
                           cad_pattern, cad_plane, cad_revolve)
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
        """Create a sketch from a spec. spec plane: "Front"|"Top"|"Right",
        {"plane": "<plane feature name>"} (e.g. from cad_plane), or a planar face
        selector (geometry "plane" or a cap, e.g. {"entity": "face", "created_by":
        "Body", "cap": "end"}). On a plane feature or a face, the sketch x/y axes and
        origin come from that plane or face, so horizontal/vertical, signed distances
        and the DOF check may not mean what you assume: check the result in Onshape.
        DRY RUN IS THE DEFAULT: nothing is written unless apply=True. Show the dry-run
        result (dof, findings) to the owner, then apply, and read afterwards. Read the
        document (cad_read) before apply. The lint REFUSES (writes nothing) on
        conflicts/contradictions in the sketch or a duplicate feature name; WARN
        findings are advice only. Costs: a dry run is free once the document was read
        (else up to 2 requests); apply costs 2 requests (version check + write)."""
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
        selects all of that face's edges), e.g. {"entity": "edge", "created_by": "Body"};
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
        selects all of that face's edges), e.g. {"entity": "edge", "created_by": "Body"};
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
        {"entity": "face", "created_by": "Body", "cap": "end"}; edge and body selectors are
        refused. thickness: length number (mm), '2 in' or '#variable'. outward: grow the wall
        outward instead of inward (default false). The outward mapping is unverified against
        live Onshape: check the result in Onshape after applying. DRY RUN IS THE DEFAULT:
        nothing is written
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
        selectors (or lists of them), e.g. {"entity": "body", "created_by": "Body"}; other
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

    @mcp.tool(name="cad_pattern")
    def cad_pattern_tool(url: str, name: str, kind: str, features: list[str],
                         count: StrictInt | StrictStr, direction: dict | list | None = None,
                         spacing: StrictFloat | StrictInt | StrictStr | None = None,
                         axis: dict | list | None = None,
                         angle: StrictFloat | StrictInt | StrictStr | None = None,
                         apply: StrictBool = False) -> dict:
        """Repeat existing features. kind: linear or circular. features: 1 to 20 feature
        names. count: number of instances including the original (2 to 10000). linear needs
        direction (an edge or face selector) and spacing (length number in mm, '2 in' or
        '#variable'); circular needs axis (an edge selector) and takes angle (degrees, default
        360, spread equally); angle is refused for linear. Selectors look like {"entity": "edge", "created_by": "Body"}.
        DRY RUN IS THE DEFAULT: nothing is written unless apply=True. Show the dry-run result
        (summary, findings) to the owner, then apply, and read afterwards. Read the document
        (cad_read) before apply; a stale write is refused. Costs: a dry run is free once the
        document was read; apply costs 2 requests (version check + write), or 3 when the write
        reply names no new version."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_pattern(ctx, ref, name, kind, features, count, direction, spacing, axis,
                           angle, apply)

    @mcp.tool(name="cad_mirror")
    def cad_mirror_tool(url: str, name: str, features: list[str], plane: str | dict,
                        apply: StrictBool = False) -> dict:
        """Mirror existing features across a plane. features: 1 to 20 feature names. plane:
        Front, Top or Right, {"plane": <name of a plane feature>} or a planar face selector.
        DRY RUN IS THE DEFAULT: nothing is written unless apply=True. Show the dry-run result
        (summary, findings) to the owner, then apply, and read afterwards. Read the document
        (cad_read) before apply; a stale write is refused. Costs: a dry run is free once the
        document was read; apply costs 2 requests (version check + write), or 3 when the write
        reply names no new version."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_mirror(ctx, ref, name, features, plane, apply)

    @mcp.tool(name="cad_hole")
    def cad_hole_tool(url: str, name: str, sketch: str, points: list[str],
                      diameter: StrictFloat | StrictInt | StrictStr,
                      depth: StrictFloat | StrictInt | StrictStr | None = None,
                      style: str = "simple", apply: StrictBool = False) -> dict:
        """Drill simple holes at sketch points. sketch: name of an existing sketch. points: 1 to
        50 sketch point ids in that sketch (like 'c1.center'). diameter: length number in mm,
        '2 in' or '#variable'. depth: omit for a through hole, or give a length for a blind hole.
        style: only 'simple' (counterbore and countersink are deferred). The hole settings
        sent are unverified against live Onshape: check the result in Onshape after applying.
        points must be entity ids of a sketch made with cad_sketch. DRY RUN IS THE DEFAULT:
        nothing is written unless apply=True. Show the dry-run result (summary, findings) to the
        owner, then apply, and read afterwards. Read the document (cad_read) before apply; a
        stale write is refused. Costs: a dry run is free once the document was read; apply costs
        2 requests (version check + write), or 3 when the write reply names no new version."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_hole(ctx, ref, name, sketch, points, diameter, depth, style, apply)

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
        Costs 0 requests after a cad_read of the document; selections made by query
        string show as that text, and selections held as geometry ids show only a
        count, since those can be changed in Onshape only."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_inspect(ctx, ref, feature)

    @mcp.tool(name="cad_feature_add")
    def cad_feature_add_tool(url: str, feature_type: str, name: str, params: dict,
                             apply: StrictBool = False) -> dict:
        """Add a native Onshape feature of any type from its spec (see cad_specs).
        DRY RUN IS THE DEFAULT: nothing is written unless apply=True. Read the
        document (cad_read) before apply; stale writes are refused. Geometry
        parameters take selector dicts in the same DSL as cad_find; raw geometry
        ids are refused. Prefer the typed tools (cad_extrude etc.) when one exists
        for the feature. Cost: dry run 0 requests (1 the first time the specs are
        fetched), apply 2 (+1 if specs are not cached)."""
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
        requests for a dry run after a read, 2 for apply (+1 if specs are not cached). After updating a feature
        that is not the last one, re-read (cad_read) before the next write."""
        try:
            ref = parse_url(url, ctx.config.base_url)
        except ValueError as e:
            return {"applied": False, "error": str(e)}
        return cad_feature_update(ctx, ref, feature, params, apply)

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
