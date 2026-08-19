#!/usr/bin/env python3
"""Create ClipProj variants of the validated Director/Motion Context templates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_FIRST = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine/workflows/h3-production/h3-reference-first-segment-av-latent-640x384-8steps.json")
DEFAULT_CONTEXT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine/workflows/h3-production/h3-motion-context-segment-640x384-8steps.json")


def clone(source: Path, destination: Path, *, encoder: str, clip_type: str, projection: str) -> None:
    workflow = json.loads(source.read_text(encoding="utf-8"))
    workflow["2"]["inputs"]["clip_name"] = encoder
    workflow["2"]["inputs"]["type"] = clip_type
    workflow["26"] = {
        "class_type": "ClipProjApply",
        "inputs": {"clip": ["2", 0], "projection": projection},
    }
    for node_id in ("8",):
        workflow[node_id]["inputs"]["clip"] = ["26", 0]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--encoder", default="qwen3vl_8b_fp8_scaled.safetensors")
    parser.add_argument("--clip-type", default="boogu")
    parser.add_argument("--projection", default="mmh3-8b-ClipProj-v3.1-mlp.safetensors")
    parser.add_argument("--first", type=Path, default=DEFAULT_FIRST)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    args = parser.parse_args()
    clone(
        args.first,
        args.output_dir / "h3-reference-first-segment-av-latent-640x384-8steps-clipproj.json",
        encoder=args.encoder,
        clip_type=args.clip_type,
        projection=args.projection,
    )
    clone(
        args.context,
        args.output_dir / "h3-motion-context-segment-640x384-8steps-clipproj.json",
        encoder=args.encoder,
        clip_type=args.clip_type,
        projection=args.projection,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
