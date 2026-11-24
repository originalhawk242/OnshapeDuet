"""Dev-only: record ground-truth Onshape feature JSON plus two probes (makes LIVE requests).

Usage: python scripts/capture_features.py <part-studio-url> <out_dir> [--skip-probes]

(a) GET features -> ui_features.json (verbatim).
(b) POST a copy of the `Round` fillet named ProbeQS with a queryString selection, re-read it
    -> probes.json["querystring_write"/"querystring_readback"].
(c) POST a tiny FeatureScript eval -> probes.json["fs_eval"].
Afterwards delete the ProbeQS feature in the Onshape UI. Not imported by the package.
"""
import argparse
import copy
import json
import os
import sys
from pathlib import Path

from onshape_duet.backends.onshape import SERIALIZATION_VERSION, features_path
from onshape_duet.docref import parse_url
from onshape_duet.server import DEFAULT_CONFIG, build_context

SCRIPT = 'function(context is Context, queries is map) { return ["2", toString(vector(1, 2, 3))]; }'


def strip_ids(node):
    """Recursively drop featureId and nodeId keys (a new feature must not carry them)."""
    if isinstance(node, dict):
        return {k: strip_ids(v) for k, v in node.items() if k not in ("featureId", "nodeId")}
    if isinstance(node, list):
        return [strip_ids(v) for v in node]
    return node


def dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def find(features: list, name: str) -> dict:
    for f in features:
        if f.get("name") == name:
            return f
    raise SystemExit(f"capture: no feature named {name!r} in the Part Studio")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--skip-probes", action="store_true", help="only (a): capture ui_features.json")
    args = ap.parse_args()

    env = os.environ.get("ONSHAPE_DUET_CONFIG")
    ctx = build_context(Path(env).expanduser() if env else DEFAULT_CONFIG)
    be, ref = ctx.backend, parse_url(args.url, ctx.config.base_url)
    path = features_path(ref)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    used_before = ctx.ledger._read()

    raw = be._ok(be._request("GET", path))
    dump(args.out_dir / "ui_features.json", raw)

    posted = False
    try:
        if not args.skip_probes:
            features = raw.get("features") or []
            probe = copy.deepcopy(find(features, "Round"))
            body_id = find(features, "Body")["featureId"]
            probe["name"] = "ProbeQS"
            probe = strip_ids(probe)
            query = {"btType": "BTMIndividualQuery-138", "queryString":
                     f'query=qGeometry(qCreatedBy(makeId("{body_id}"), EntityType.EDGE), GeometryType.LINE);'}
            replaced = False
            for p in probe["parameters"]:
                if p.get("parameterId") == "entities":
                    p["queries"] = [query]
                    replaced = True
            if not replaced:
                raise SystemExit("capture: the Round feature has no 'entities' parameter; nothing posted")
            posted = True  # ProbeQS may exist from here on, even if the POST errors
            write = be._ok(be._request("POST", path, body={
                "feature": probe, "serializationVersion": SERIALIZATION_VERSION,
                "sourceMicroversion": be.get_microversion(ref), "rejectMicroversionSkew": True}))
            probes = {"querystring_write": write}
            dump(args.out_dir / "probes.json", probes)

            again = be._ok(be._request("GET", path))
            back = dict(find(again.get("features") or [], "ProbeQS"))
            back["state"] = (again.get("featureStates") or {}).get(back.get("featureId"))
            probes["querystring_readback"] = back
            dump(args.out_dir / "probes.json", probes)

            try:
                probes["fs_eval"] = be._ok(be._request(
                    "POST", path.replace("/features", "/featurescript"),
                    body={"script": SCRIPT, "queries": []}))
            except RuntimeError as e:
                probes["fs_eval"] = {"error": str(e)}
            dump(args.out_dir / "probes.json", probes)
    finally:
        if posted:
            print("Delete the ProbeQS feature in the Onshape UI.")
        print(f"requests charged: {ctx.ledger._read() - used_before}", file=sys.stderr)


if __name__ == "__main__":
    main()
