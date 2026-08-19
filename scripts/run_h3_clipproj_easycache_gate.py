#!/usr/bin/env python3
"""Run the isolated H3 ClipProj/EasyCache gate and preserve media evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path("/Volumes/AJW-Data/Projects/novel-to-comic-engine")
DEFAULT_RUN = ROOT / "test-runs/2026-08-18-h3-clipproj-easycache-gate"
CASES = ("fullclip_8steps", "clipproj_4b_8steps", "clipproj_4b_20steps_easycache")


def http_json(url: str, method: str = "GET", payload: object | None = None, timeout: int = 60) -> object:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=body, method=method, headers=headers), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def find_media(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        if isinstance(value.get("filename"), str) and str(value["filename"]).lower().endswith((".mp4", ".webm", ".mov")):
            return value
        for child in value.values():
            media = find_media(child)
            if media:
                return media
    elif isinstance(value, list):
        for child in value:
            media = find_media(child)
            if media:
                return media
    return None


def remote_resource_snapshot() -> dict[str, object]:
    command = [
        "ssh", "cachyos-ai", "/bin/bash", "-s",
    ]
    script = """set +e
date -Is
nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu,temperature.gpu --format=csv,noheader,nounits
free -b | awk '/Mem:/ {print \"mem_total=\"$2,\"mem_used=\"$3,\"mem_free=\"$4}'
swapon --show=NAME,SIZE,USED --bytes --noheadings
"""
    result = subprocess.run(command, input=script, text=True, capture_output=True, check=False, timeout=30)
    return {"returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}


def verify_media(path: Path, run_dir: Path) -> dict[str, object]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels", "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, check=False)
    frames = run_dir / "frames" / path.stem
    frames.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf", "select='eq(n,0)+eq(n,61)+eq(n,123)'", "-vsync", "0", str(frames / "frame_%02d.png")], capture_output=True, text=True, check=False)
    return {
        "status": "passed" if probe.returncode == 0 and decode.returncode == 0 else "failed",
        "ffprobe": json.loads(probe.stdout) if probe.returncode == 0 and probe.stdout else {"stderr": probe.stderr.strip()},
        "decode_returncode": decode.returncode,
        "decode_stderr": decode.stderr.strip(),
        "frames": [str(p) for p in sorted(frames.glob("frame_*.png"))],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def run_case(case: str, base_url: str, run_dir: Path, poll_seconds: int) -> dict[str, object]:
    workflow_path = run_dir / "workflows" / f"workflow_{case}.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    row: dict[str, object] = {"case": case, "workflow": str(workflow_path), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    started = time.perf_counter()
    row["resources_start"] = remote_resource_snapshot()
    try:
        submitted = http_json(f"{base_url}/prompt", method="POST", payload={"prompt": workflow})
        prompt_id = str(submitted["prompt_id"])
        row["prompt_id"] = prompt_id
        while True:
            history = http_json(f"{base_url}/history/{prompt_id}")
            item = history.get(prompt_id) if isinstance(history, dict) else None
            if item:
                status = item.get("status", {})
                if status.get("status_str") == "error":
                    row.update({"status": "error", "error_messages": status.get("messages", [])[-20:]})
                    break
                if status.get("completed") or status.get("status_str") == "success":
                    media = find_media(item.get("outputs", {}))
                    if not media:
                        row["status"] = "no_media"
                        break
                    target = run_dir / "outputs" / f"{case}.mp4"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    query = urlencode({"filename": str(media["filename"]), "subfolder": str(media.get("subfolder", "")), "type": str(media.get("type", "output"))})
                    with urlopen(Request(f"{base_url}/view?{query}"), timeout=300) as response, target.open("wb") as handle:
                        while chunk := response.read(1024 * 1024):
                            handle.write(chunk)
                    row.update({"status": "success", "remote_media": media, "output": str(target), "verification": verify_media(target, run_dir)})
                    break
            if time.perf_counter() - started > 4 * 3600:
                row["status"] = "timeout"
                break
            time.sleep(poll_seconds)
    except Exception as exc:  # preserve failure evidence for diagnosis
        row.update({"status": "client_error", "error": f"{type(exc).__name__}: {exc}"})
    row["resources_end"] = remote_resource_snapshot()
    row["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    row["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(json.dumps({"case": case, "status": row.get("status"), "elapsed_seconds": row["elapsed_seconds"]}, ensure_ascii=False), flush=True)
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("H3_COMFYUI_BASE_URL", "http://192.168.1.6:8192"))
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--cases", default=",".join(CASES))
    parser.add_argument("--poll-seconds", type=int, default=5)
    args = parser.parse_args()
    args.run_dir.mkdir(parents=True, exist_ok=True)
    results_path = args.run_dir / "results.jsonl"
    completed = {}
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                # The first t020 attempt in this run had a generator bug and
                # produced an 8-step filename.  Keep that evidence, but do
                # not let it suppress the corrected 20-step rerun.
                if item.get("status") == "success" and not (
                    str(item.get("case")) == "clipproj_4b_20steps_easycache_t020"
                    and "_640x384_20steps_" not in str(item.get("remote_media", {}).get("filename", ""))
                ):
                    completed[str(item["case"])] = item
    for case in [x.strip() for x in args.cases.split(",") if x.strip()]:
        if case in completed:
            continue
        item = run_case(case, args.base_url.rstrip("/"), args.run_dir, args.poll_seconds)
        with results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        if item.get("status") != "success":
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
