#!/usr/bin/env python3
"""Download the learned planner checkpoint from Hugging Face and export it for the brain.

    uv run --with torch --with numpy tools/fetch_planner.py              # newest .pt named ppo_v3 in the org
    uv run --with torch --with numpy tools/fetch_planner.py --list
    uv run --with torch --with numpy tools/fetch_planner.py --want ppo_v4
    uv run --with torch --with numpy tools/fetch_planner.py --repo pi-hack-mining/planner --file ppo_v3.pt

Saves models/<want>.pt and exports models/<want>.npz (docker-compose default PLANNER_CKPT is
/app/models/ppo_v3.npz). HF_TOKEN is sent if set (needed if the repo is private).
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HF = "https://huggingface.co"
ORG = "pi-hack-mining"


def get(url: str) -> bytes:
    req = urllib.request.Request(url)
    if os.environ.get("HF_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['HF_TOKEN']}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def checkpoints(org: str) -> list[tuple[str, str, str]]:
    models = json.loads(get(f"{HF}/api/models?author={org}&sort=lastModified&direction=-1&full=true"))
    found = []
    for m in models:
        info = json.loads(get(f"{HF}/api/models/{m['id']}"))
        for s in info.get("siblings", []):
            if s["rfilename"].endswith(".pt"):
                found.append((m["id"], s["rfilename"], info.get("lastModified", "")))
    return found


def matches(item: tuple[str, str, str], want: str) -> bool:
    return want.lower() in (item[0] + "/" + item[1]).lower()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--org", default=ORG)
    p.add_argument("--repo")
    p.add_argument("--file")
    p.add_argument("--out-dir", type=Path, default=ROOT / "models")
    p.add_argument("--want", default="ppo_v3", help="checkpoint name to look for and to save as")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()

    if args.repo and args.file:
        repo, fname = args.repo, args.file
    else:
        found = sorted((c for c in checkpoints(args.org) if not args.repo or c[0] == args.repo), key=lambda c: c[2], reverse=True)
        if args.list or not found:
            for c in found:
                print("%s  %s  %s" % c)
            if not found:
                sys.exit(f"no .pt checkpoints visible under {HF}/{args.repo or args.org} (private repo? set HF_TOKEN)")
            return
        hits = [c for c in found if matches(c, args.want)]
        if not hits:
            sys.exit(f"no .pt matching {args.want!r}; found: " + ", ".join(f"{r}/{f}" for r, f, _ in found))
        repo, fname, _ = hits[0]

    data = get(f"{HF}/{repo}/resolve/main/{fname}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pt = args.out_dir / f"{args.want}.pt"
    pt.write_bytes(data)
    print(f"{repo}/{fname} -> {pt} sha256 {hashlib.sha256(data).hexdigest()}")

    sys.path.insert(0, str(ROOT / "brain"))
    from brain.planning.export_scorer import export
    from brain.planning.learned import CheckpointError

    try:
        config = export(pt, pt.with_suffix(".npz"))
    except CheckpointError as e:
        sys.exit(f"{pt}: {e}")
    print(f"exported {pt.with_suffix('.npz')} {json.dumps({**config, 'repo': repo, 'file': fname})}")


if __name__ == "__main__":
    main()
