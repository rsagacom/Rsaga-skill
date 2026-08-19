#!/usr/bin/env python3
"""Verify GOLDEN_SHOTS with real SHA-256, ffprobe and full ffmpeg decode."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from studio_core.golden_shots import (  # noqa: E402
    GoldenShotManifest,
    ffmpeg_full_decode,
    ffprobe_media,
    verify_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=PROJECT_ROOT / "GOLDEN_SHOTS" / "manifest.json",
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()

    manifest = GoldenShotManifest.load(args.manifest)
    results = verify_manifest(
        manifest,
        args.project_root,
        probe=ffprobe_media,
        decode=ffmpeg_full_decode,
    )
    payload = {
        "manifest": str(args.manifest),
        "project_root": str(args.project_root),
        "records": [result.to_dict() for result in results],
        "status": "passed" if all(result.status == "passed" for result in results) else "failed",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
