#!/usr/bin/env python3
"""对 ComfyUI 视频工作流做小样本基准测试。

Wan2.2、LTX 等视频模型通过不同 ComfyUI workflow 接入；本脚本复用
``ComfyUIVideoProvider``，不复制提交/轮询/下载逻辑。默认只报告配置和
workflow 合同，只有显式传入 ``--run`` 才会触发真实 GPU 推理。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from studio_api.providers import ComfyUIVideoProvider, ProviderError
from studio_core.workflow_contract import SUPPORTED_PLACEHOLDERS, validate_comfyui_api_workflow, workflow_placeholders


def _workflow_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"valid": False, "error": "workflow-not-found"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"valid": False, "error": "workflow-invalid-json"}
    errors = validate_comfyui_api_workflow(payload, role="video")
    if errors:
        return {"valid": False, "error": errors[0]}
    serialized = json.dumps(payload, ensure_ascii=False)
    placeholders = workflow_placeholders(payload)
    return {
        "valid": True,
        "node_count": len(payload),
        "placeholders": [placeholder for placeholder in placeholders if placeholder in SUPPORTED_PLACEHOLDERS],
        "requires_source_image": any(placeholder in serialized for placeholder in ("{{IMAGE_REF}}", "{{IMAGE_FILENAME}}", "{{SOURCE_IMAGE_FILENAME}}", "{{IMAGE_SUBFOLDER}}", "{{IMAGE_TYPE}}")),
        "has_video_output_hint": any(key in serialized.lower() for key in ("video", "combine", "savevideo", "videocombine")),
    }


def _target(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "?"
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return f"{host}:{port}"


def _probe(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"available": False}
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"available": True, "valid": False}
    streams = payload.get("streams", []) if isinstance(payload, dict) else []
    stream_types = sorted({str(stream.get("codec_type")) for stream in streams if isinstance(stream, dict) and stream.get("codec_type")})
    format_info = payload.get("format", {}) if isinstance(payload, dict) else {}
    try:
        duration = float(format_info.get("duration"))
    except (TypeError, ValueError):
        duration = None
    return {"available": True, "valid": bool(result.returncode == 0 and streams), "stream_types": stream_types, "duration_seconds": duration}


def _safe_provider_metadata(metadata: Any) -> dict[str, str]:
    """仅保留固定白名单的短标签，避免报告携带 Provider 原始字段。"""

    if not isinstance(metadata, dict):
        return {}
    safe: dict[str, str] = {}
    for key in ("provider", "source"):
        value = metadata.get(key)
        if isinstance(value, str) and value and len(value) <= 64:
            safe[key] = value
    return safe


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    workflow_path = Path(args.workflow) if args.workflow else None
    summary = _workflow_summary(workflow_path) if workflow_path else {"valid": False, "error": "workflow-not-configured"}
    base_url = args.base_url.strip()
    parsed = urlparse(base_url) if base_url else None
    if not base_url:
        return {"status": "not-configured", "provider": "comfyui", "workflow": summary, "reason": "base-url-not-configured"}
    if not parsed or parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return {"status": "failed", "provider": "comfyui", "target": "invalid", "workflow": summary, "reason": "invalid-base-url"}
    if not summary.get("valid"):
        return {"status": "failed", "provider": "comfyui", "target": _target(base_url), "workflow": summary, "reason": summary.get("error", "workflow-invalid")}
    report: dict[str, Any] = {
        "status": "ready-to-run" if not args.run else "running",
        "provider": "comfyui",
        "target": _target(base_url),
        "workflow": summary,
        "source_image_configured": bool(args.source_image),
        "label": args.label,
        "runs_requested": args.runs,
        "runs": [],
    }
    if not args.run:
        report["status"] = "dry-run"
        return report
    if summary.get("requires_source_image") and not args.source_image:
        report["status"] = "failed"
        report["reason"] = "source-image-not-configured"
        report.pop("runs", None)
        return report

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    provider = ComfyUIVideoProvider(base_url, str(workflow_path), timeout=args.timeout, poll_interval=args.poll_interval)
    source_image_path = Path(args.source_image) if args.source_image else None
    for index in range(args.runs):
        asset_id = f"benchmark-{args.label}-{index + 1}-{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        item: dict[str, Any] = {"asset_id": asset_id, "run": index + 1}
        try:
            generated = provider.generate(
                asset_id,
                args.shot_id,
                output_dir,
                source_image_path=source_image_path,
                prompt=args.prompt,
            )
            elapsed = time.perf_counter() - started
            candidates = sorted(output_dir.glob(f"{asset_id}.*"))
            output_path = candidates[0] if candidates else None
            item.update(
                {
                    "status": "completed",
                    "elapsed_seconds": round(elapsed, 3),
                    "relative_url": generated.relative_url,
                    "provider_metadata": _safe_provider_metadata(generated.metadata),
                    "output_bytes": output_path.stat().st_size if output_path else None,
                    "ffprobe": _probe(output_path) if output_path else {"available": bool(shutil.which("ffprobe")), "valid": False},
                }
            )
        except ProviderError:
            item.update({"status": "failed", "elapsed_seconds": round(time.perf_counter() - started, 3), "reason": "comfyui-provider-contract-failed"})
        except (OSError, ValueError):
            item.update({"status": "failed", "elapsed_seconds": round(time.perf_counter() - started, 3), "reason": "benchmark-output-error"})
        report["runs"].append(item)
    completed = sum(1 for item in report["runs"] if item.get("status") == "completed")
    report["status"] = "completed" if completed == args.runs else "failed"
    report["runs_completed"] = completed
    if completed:
        report["mean_elapsed_seconds"] = round(sum(float(item["elapsed_seconds"]) for item in report["runs"] if item.get("status") == "completed") / completed, 3)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="video", help="基准标签，例如 wan22 或 ltx")
    parser.add_argument("--base-url", default=os.environ.get("COMFYUI_BASE_URL", ""))
    parser.add_argument("--workflow", default=os.environ.get("COMFYUI_VIDEO_WORKFLOW", ""))
    parser.add_argument("--shot-id", default="benchmark-shot")
    parser.add_argument("--prompt", default="", help="文生视频 workflow 的 {{PROMPT}} 内容；图生视频可留空")
    parser.add_argument("--source-image", default=os.environ.get("COMFYUI_VIDEO_SOURCE_IMAGE", ""), help="图生视频 workflow 的输入关键帧路径；仅在显式 --run 时上传到 ComfyUI")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--output-dir", default="/tmp/ai-manhua-video-benchmark")
    parser.add_argument("--run", action="store_true", help="显式触发真实 ComfyUI/GPU 工作流；默认只做 dry-run")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.runs > 5:
        parser.error("--runs must be between 1 and 5")
    if args.timeout < 10:
        parser.error("--timeout must be at least 10 seconds")
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    report = build_report(parse_args(argv))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] in {"dry-run", "completed", "not-configured"} else 1


if __name__ == "__main__":
    sys.exit(main())
