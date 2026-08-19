#!/usr/bin/env python3
"""Run one H3 production segment with preflight, resumable state and media QA.

This is intentionally a segment runner. Director remains the timeline/shot
orchestrator and Motion Context remains the AV-latent continuation layer.
The runner only makes one segment auditable and safe to resume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from studio_api.providers import ComfyUIVideoProvider, GeneratedAsset, ProviderError
from studio_core.workflow_contract import validate_comfyui_api_workflow, workflow_placeholders


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("workflow must be an object")
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def get_json(base_url: str, path: str) -> Any:
    request = Request(f"{base_url.rstrip('/')}{path}", headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def workflow_class_types(workflow: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(node.get("class_type"))
            for node in workflow.values()
            if isinstance(node, dict) and isinstance(node.get("class_type"), str)
        }
    )


def preflight(base_url: str, workflow: dict[str, Any]) -> dict[str, Any]:
    errors = validate_comfyui_api_workflow(workflow, role="video")
    if errors:
        raise ValueError("workflow contract failed: " + ",".join(errors))
    try:
        system_stats = get_json(base_url, "/system_stats")
        queue = get_json(base_url, "/queue")
        object_info = get_json(base_url, "/object_info")
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("ComfyUI preflight failed") from exc
    if not isinstance(object_info, dict):
        raise RuntimeError("ComfyUI object_info is invalid")
    required = workflow_class_types(workflow)
    missing = [class_type for class_type in required if class_type not in object_info]
    if missing:
        raise RuntimeError("ComfyUI nodes missing: " + ",".join(missing[:8]))
    if not isinstance(queue, dict):
        raise RuntimeError("ComfyUI queue is invalid")
    return {
        "base_url": base_url.rstrip("/"),
        "queue_running": len(queue.get("queue_running", [])) if isinstance(queue.get("queue_running", []), list) else None,
        "queue_pending": len(queue.get("queue_pending", [])) if isinstance(queue.get("queue_pending", []), list) else None,
        "required_nodes": required,
        "system_stats_available": isinstance(system_stats, dict),
    }


def find_media(output_dir: Path, asset_id: str) -> Path | None:
    candidates = sorted(
        path for path in output_dir.glob(f"{asset_id}.*") if path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}
    )
    return candidates[0] if candidates else None


def media_qa(path: Path) -> dict[str, Any]:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries",
            "format=duration,size,format_name:stream=index,codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise RuntimeError("ffprobe failed")
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, check=False)
    if decode.returncode != 0:
        raise RuntimeError("full media decode failed")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    metadata = json.loads(probe.stdout)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": digest,
        "ffprobe": metadata,
        "full_decode": True,
    }


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def run(args: argparse.Namespace) -> dict[str, Any]:
    workflow = read_json(args.workflow)
    placeholders = workflow_placeholders(workflow)
    state_path = args.state or args.output_dir / "runner-state.json"
    state_path = Path(state_path)
    existing = load_state(state_path)
    output = find_media(args.output_dir, args.asset_id)
    if args.resume and output:
        try:
            qa = media_qa(output)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            qa = None
        if qa:
            result = {
                "status": "resumed-existing-output",
                "asset_id": args.asset_id,
                "shot_id": args.shot_id,
                "state_path": str(state_path),
                "media": qa,
                "previous_state": existing.get("status") if existing else None,
            }
            atomic_write_json(state_path, result)
            return result

    started = time.perf_counter()
    base = args.base_url.rstrip("/")
    try:
        check = preflight(base, workflow)
        state = {
            "status": "running",
            "asset_id": args.asset_id,
            "shot_id": args.shot_id,
            "workflow": str(args.workflow),
            "placeholders": placeholders,
            "preflight": check,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        atomic_write_json(state_path, state)
        generated: GeneratedAsset = ComfyUIVideoProvider(
            base,
            str(args.workflow),
            timeout=args.timeout,
            poll_interval=args.poll_interval,
        ).generate(
            args.asset_id,
            args.shot_id,
            args.output_dir,
            source_image_path=args.source_image,
            prompt=args.prompt,
        )
        output = find_media(args.output_dir, args.asset_id)
        if output is None:
            raise RuntimeError("provider returned without a local media output")
        qa = media_qa(output)
        result = {
            "status": "completed",
            "asset_id": args.asset_id,
            "shot_id": args.shot_id,
            "workflow": str(args.workflow),
            "placeholders": placeholders,
            "preflight": check,
            "provider": generated.metadata,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "media": qa,
        }
    except (OSError, ProviderError, RuntimeError, ValueError, URLError, json.JSONDecodeError) as exc:
        result = {
            "status": "failed",
            "asset_id": args.asset_id,
            "shot_id": args.shot_id,
            "workflow": str(args.workflow),
            "placeholders": placeholders,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "reason": str(exc),
        }
    atomic_write_json(state_path, result)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--shot-id", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--source-image", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    args = parser.parse_args(argv)
    if args.timeout < 10:
        parser.error("--timeout must be at least 10 seconds")
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be positive")
    args.workflow = args.workflow.resolve()
    args.output_dir = args.output_dir.resolve()
    if args.source_image:
        args.source_image = args.source_image.resolve()
    if args.state:
        args.state = args.state.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    return args


def main(argv: list[str] | None = None) -> int:
    result = run(parse_args(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] in {"completed", "resumed-existing-output"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
