#!/usr/bin/env python3
"""Build pruned-INT8, no-LoRA Director continuity workflows.

The existing Director workflow is reused for its tested hidden timeline
contract.  Each generated segment remains 124 frames (5.167 s target), so
10/15/30 s tests exercise Director handoff without forcing a single long
latent through the 12 GB GPU.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
SOURCE = ROOT / "test-runs/2026-08-13-h3-director-aimixer/workflow_aimixer_director_t8_2segments_832x480.json"
OUT = ROOT / "test-runs/2026-08-17-h3-pruned-long-continuity/workflows"


def make_workflow(duration: int) -> dict[str, dict[str, object]]:
    if duration not in {10, 15, 30}:
        raise ValueError("duration must be 10, 15, or 30")
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    data["1"]["inputs"]["unet_name"] = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    data.pop("7", None)
    data["8"]["inputs"]["model"] = ["6", 0]
    director = data["8"]["inputs"]
    director.update({"width": 640, "height": 384, "ref_max_size": 640})

    timeline = json.loads(director["timeline_data"])
    base_segments = timeline["segments"]
    segments = []
    count = {10: 2, 15: 3, 30: 6}[duration]
    for index in range(count):
        source = dict(base_segments[0] if index == 0 else base_segments[1])
        source["id"] = f"s{index}"
        source["start"] = index * 124
        source["length"] = 124
        source["frameCount"] = 124
        source["durationSec"] = 124 / 24
        segments.append(source)
    timeline["totalFrames"] = count * 124
    timeline["width"] = 640
    timeline["height"] = 384
    timeline["refMaxSize"] = 640
    timeline["output"].update({"longEdge": 640, "width": 640, "height": 384, "maxExportFrames": 0})
    timeline["segments"] = segments
    director["total_frames"] = count * 124
    director["timeline_data"] = json.dumps(timeline, ensure_ascii=False, separators=(",", ":"))
    data["10"]["inputs"]["filename_prefix"] = f"h3_pruned_int8_director_no_lora_{duration}s_640x384"
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, choices=[10, 15, 30], required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    path = args.output or OUT / f"workflow_pruned_int8_director_no_lora_{args.duration}s_640x384.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(make_workflow(args.duration), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
