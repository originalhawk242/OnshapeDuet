"""Feature-name resolution and the enum-only selector compiler.

Security: callers never supply FeatureScript.  Every selector field maps through a
fixed table; the only document-derived text in an expression is a feature id that
fully matches _ID_RE.  Feature names are never embedded.
"""
from __future__ import annotations

import re

from onshape_duet.features.params import FeatureSpecError, p_ids, p_query
from onshape_duet.model import Feature, FeatureList
from onshape_duet.sketch.serialize import PLANES
from onshape_duet.textsafe import clean

Selector = dict | list
PlaneRef = str | dict

_ID_RE = re.compile(r"[A-Za-z0-9_]{1,64}")
_ENTITIES = {"face": "FACE", "edge": "EDGE", "body": "BODY"}
_CAPS = {"start": "START", "end": "END"}
_GEOMETRY = {"edge": {"line": "LINE", "circle": "CIRCLE", "arc": "ARC"},
             "face": {"plane": "PLANE", "cylinder": "CYLINDER", "cone": "CONE",
                      "sphere": "SPHERE", "torus": "TORUS"}}
_DIRS = {"+X": (1, 0, 0), "-X": (-1, 0, 0), "+Y": (0, 1, 0), "-Y": (0, -1, 0),
         "+Z": (0, 0, 1), "-Z": (0, 0, -1)}
_KEYS = {"entity", "created_by", "cap", "edges_of", "geometry", "normal", "farthest"}
MAX_UNION = 8


class RefError(FeatureSpecError):
    pass


def resolve(fl: FeatureList, name: str, arg: str, types: set[str] | None = None) -> Feature:
    a = clean(arg)
    if not isinstance(name, str) or not name:
        raise RefError(f"{a}: expected a non-empty feature name")
    hits = [f for f in fl.features if f.name == name]
    if not hits:
        names = [clean(f.name) for f in fl.features[:10] if f.name]
        more = ", ..." if len(fl.features) > 10 else ""
        raise RefError(f"{a}: no feature named {clean(name)!r}; existing names: "
                       f"{', '.join(repr(n) for n in names) or '(none)'}{more}")
    if len(hits) > 1:
        ids = ", ".join(clean(f.id) for f in hits[:10])
        raise RefError(f"{a}: feature name {clean(name)!r} is ambiguous (ids {ids}); rename one first")
    f = hits[0]
    if not isinstance(f.id, str) or not _ID_RE.fullmatch(f.id):
        raise RefError(f"{a}: feature {clean(name)!r} has an unusable id {clean(f.id)!r}")
    if types is not None and f.type not in types:
        raise RefError(f"{a}: feature {clean(name)!r} is a {clean(f.type)!r}, expected "
                       f"{' or '.join(sorted(types))}")
    return f


def _vec(key: str, val, a: str) -> str:
    if not isinstance(val, str) or val not in _DIRS:
        raise RefError(f"{a}: {key} must be one of {', '.join(_DIRS)}, got {clean(val)!r}")
    x, y, z = _DIRS[val]
    return f"vector({x}, {y}, {z})"


def _enum(key: str, val, table: dict, a: str) -> str:
    if not isinstance(val, str) or val not in table:
        raise RefError(f"{a}: {key} must be one of {', '.join(table)}, got {clean(val)!r}")
    return table[val]


def _compile_one(sel, fl: FeatureList, arg: str, allow_edges_of: bool) -> str:
    a = clean(arg)
    if not isinstance(sel, dict):
        raise RefError(f"{a}: selector must be an object")
    unknown = set(sel) - _KEYS
    if unknown:
        raise RefError(f"{a}: unknown selector keys {', '.join(sorted(clean(k) for k in unknown))}; "
                       f"allowed: {', '.join(sorted(_KEYS))}")
    if "entity" not in sel:
        raise RefError(f"{a}: selector needs 'entity' (face, edge or body)")
    ent_name = sel["entity"]
    ent = _enum("entity", ent_name, _ENTITIES, a)
    E = f"EntityType.{ent}"
    if ent_name == "body" and set(sel) - {"entity", "created_by"}:
        raise RefError(f"{a}: a body selector allows only entity and created_by")
    if "edges_of" in sel:
        if not allow_edges_of:
            raise RefError(f"{a}: edges_of may not be nested")
        if ent_name != "edge":
            raise RefError(f"{a}: edges_of requires entity 'edge'")
        if "created_by" in sel or "cap" in sel:
            raise RefError(f"{a}: edges_of cannot be combined with created_by or cap")
        inner = sel["edges_of"]
        if not isinstance(inner, dict):
            raise RefError(f"{a}: edges_of must be a single face selector object")
        if inner.get("entity") != "face":
            raise RefError(f"{a}: edges_of selector must have entity 'face'")
        q = (f"qAdjacent({_compile_one(inner, fl, arg, False)}, "
             f"AdjacencyType.EDGE, EntityType.EDGE)")
    elif "cap" in sel:
        if "created_by" not in sel:
            raise RefError(f"{a}: cap requires created_by")
        cap = _enum("cap", sel["cap"], _CAPS, a)
        fid = resolve(fl, sel["created_by"], f"{a}.created_by").id
        q = f'qCapEntity(makeId("{fid}"), CapType.{cap}, {E})'
    elif "created_by" in sel:
        fid = resolve(fl, sel["created_by"], f"{a}.created_by").id
        q = f'qCreatedBy(makeId("{fid}"), {E})'
    elif ent_name == "body":
        q = "qAllModifiableSolidBodies()"
    else:
        q = f"qOwnedByBody(qAllModifiableSolidBodies(), {E})"
    if "geometry" in sel:
        g = _enum("geometry", sel["geometry"], _GEOMETRY[ent_name], a)
        q = f"qGeometry({q}, GeometryType.{g})"
    if "normal" in sel:
        if ent_name != "face":
            raise RefError(f"{a}: normal applies to faces only")
        q = f"qParallelPlanes({q}, {_vec('normal', sel['normal'], a)}, false)"
    if "farthest" in sel:
        q = f"qFarthestAlong({q}, {_vec('farthest', sel['farthest'], a)})"
    return q


def compile_selector(sel: Selector, fl: FeatureList, arg: str) -> str:
    if isinstance(sel, list):
        if not 1 <= len(sel) <= MAX_UNION:
            raise RefError(f"{clean(arg)}: selector list must have 1 to {MAX_UNION} entries")
        parts = [_compile_one(s, fl, arg, True) for s in sel]
        return parts[0] if len(parts) == 1 else f"qUnion([{', '.join(parts)}])"
    return _compile_one(sel, fl, arg, True)


def selector_entity(sel: Selector) -> str:
    items = sel if isinstance(sel, list) else [sel]
    # str check first: a caller value such as ["edge"] is unhashable (TypeError in a set)
    raw = [s.get("entity") if isinstance(s, dict) else None for s in items]
    ents = {e if isinstance(e, str) else None for e in raw}
    if len(ents) != 1 or next(iter(ents)) not in _ENTITIES:
        raise RefError(f"selector must have one consistent entity (face, edge or body), got "
                       f"{', '.join(sorted(clean(e) for e in ents))}")
    return next(iter(ents))


def plane_param(pid: str, plane: PlaneRef, fl: FeatureList) -> dict:
    if isinstance(plane, str):
        if plane not in PLANES:
            raise RefError(f"{clean(pid)}: unknown plane {clean(plane)!r}; use Front, Top or Right, "
                           f"{{'plane': <cPlane feature name>}} or a planar face selector")
        return p_ids(pid, [PLANES[plane]])
    if isinstance(plane, dict) and set(plane) == {"plane"}:
        f = resolve(fl, plane["plane"], pid, types={"cPlane"})
        return p_query(pid, [f'qCreatedBy(makeId("{f.id}"), EntityType.FACE)'])
    if isinstance(plane, dict) and plane.get("entity") == "face":
        if plane.get("geometry") != "plane" and "cap" not in plane:
            raise RefError(f"{clean(pid)}: a face selector used as a plane must be provably planar "
                           f"(geometry 'plane' or a cap)")
        return p_query(pid, [compile_selector(plane, fl, pid)])
    raise RefError(f"{clean(pid)}: plane must be Front/Top/Right, {{'plane': name}} or a face selector")
