"""Dev-only: record Onshape's own feature specs plus the live feature list (LIVE requests, read-only).

Usage: python scripts/capture_features.py <part-studio-url> <out_dir> [--skip-probes]

1. GET featurespecs -> feature_specs.json (the specs for the feature types we build).
2. GET features -> doc_features.json (verbatim).
3. POST a tiny FeatureScript eval -> probes.json["fs_eval"] (skipped by --skip-probes).
Nothing is written to the document. Not imported by the package.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from onshape_duet.backends.onshape import features_path
from onshape_duet.docref import parse_url
from onshape_duet.server import DEFAULT_CONFIG, build_context

SCRIPT = 'function(context is Context, queries is map) { return ["2", toString(vector(1, 2, 3))]; }'
WANTED = {"extrude", "revolve", "fillet", "chamfer", "hole", "shell", "linearPattern",
          "circularPattern", "mirror", "booleanBodies", "cPlane", "newSketch", "assignVariable"}


def dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--skip-probes", action="store_true", help="skip step 3 (the featurescript eval)")
    args = ap.parse_args()

    env = os.environ.get("ONSHAPE_DUET_CONFIG")
    ctx = build_context(Path(env).expanduser() if env else DEFAULT_CONFIG)
    be, ref = ctx.backend, parse_url(args.url, ctx.config.base_url)
    path = features_path(ref)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    used_before = ctx.ledger.used

    try:
        reply = be._ok(be._request("GET", path.replace("/features", "/featurespecs")))
        specs = reply.get("featureSpecs") if isinstance(reply, dict) else None
        if specs is None:
            keys = sorted(reply) if isinstance(reply, dict) else type(reply).__name__
            raise SystemExit(f"capture: no 'featureSpecs' in the reply; top-level keys: {keys}")
        kept = [s for s in specs if s.get("featureType") in WANTED]
        seen = sorted({s.get("featureType") for s in specs if s.get("featureType")})
        dump(args.out_dir / "feature_specs.json", {"kept": kept, "featureTypes_seen": seen})
        missing = sorted(WANTED - {s["featureType"] for s in kept})
        if len(WANTED) - len(missing) < 10:
            print(f"WARNING: fewer than 10 wanted types found; missing: {missing}", file=sys.stderr)

        dump(args.out_dir / "doc_features.json", be._ok(be._request("GET", path)))

        if not args.skip_probes:
            probes = {}
            try:
                probes["fs_eval"] = be._ok(be._request(
                    "POST", path.replace("/features", "/featurescript"),
                    body={"script": SCRIPT, "queries": []}))
            except RuntimeError as e:
                probes["fs_eval"] = {"error": str(e)}
            dump(args.out_dir / "probes.json", probes)
    finally:
        print(f"requests charged: {ctx.ledger.used - used_before}", file=sys.stderr)


if __name__ == "__main__":
    main()
