#!/usr/bin/env python3
"""Verify the H3 HD matrix and emit reviewable metadata and frames."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
RUN = ROOT / "test-runs/2026-08-13-h3-hd-matrix"


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def probe(path: Path) -> dict:
    return json.loads(run(
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=index,codec_name,codec_type,width,height,r_frame_rate,nb_frames,sample_rate,channels",
        "-of", "json", str(path)
    ))


def main() -> int:
    rows = [json.loads(line) for line in (RUN / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = []
    for row in rows:
        if row.get("status") != "success":
            row["verification"] = {"status": "not_run", "reason": "generation_failed"}
            manifest.append(row)
            continue
        path = Path(str(row["output"]))
        frame_dir = RUN / "frames" / str(row["id"])
        frame_dir.mkdir(parents=True, exist_ok=True)
        metadata = probe(path)
        streams = metadata.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        frame_count = int(video.get("nb_frames") or 124)
        mid = max(1, frame_count // 2)
        last = max(0, frame_count - 1)
        decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
            "-vf", f"select='eq(n,0)+eq(n,{mid})+eq(n,{last})'", "-vsync", "0",
            str(frame_dir / "frame_%02d.png")
        ], check=True)
        (frame_dir / "ffprobe.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sha256 = run("shasum", "-a", "256", str(path)).split()[0]
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        row["verification"] = {
            "status": "passed" if decode.returncode == 0 else "decode_failed",
            "decode_stderr": decode.stderr[-1000:],
            "sha256": sha256,
            "frames": [str(p) for p in sorted(frame_dir.glob("frame_*.png"))],
            "media": {
                "duration_seconds": float(metadata.get("format", {}).get("duration", 0)),
                "size_bytes": int(metadata.get("format", {}).get("size", 0)),
                "video_codec": video.get("codec_name"),
                "video_width": video.get("width"),
                "video_height": video.get("height"),
                "video_frames": video.get("nb_frames"),
                "fps": video.get("r_frame_rate"),
                "audio_codec": audio.get("codec_name"),
                "audio_sample_rate": audio.get("sample_rate"),
                "audio_channels": audio.get("channels"),
            },
        }
        manifest.append(row)
    out = RUN / "review_manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    passed = sum(item.get("verification", {}).get("status") == "passed" for item in manifest)
    print(json.dumps({"status": "passed" if passed == len(manifest) else "partial", "cases": len(manifest), "verified": passed, "manifest": str(out)}, ensure_ascii=False))
    for item in manifest:
        media = item.get("verification", {}).get("media", {})
        print(f"{item['id']}\t{item.get('elapsed_seconds')}s\t{media.get('video_width')}x{media.get('video_height')}\t{item.get('verification', {}).get('status')}\t{item.get('output', '')}")
    return 0 if passed == len(manifest) else 1


if __name__ == "__main__":
    raise SystemExit(main())
