#!/usr/bin/env python3
"""对单个真实 Provider 做显式、脱敏的合同 smoke。

默认只检查配置，不访问网络；只有 ``--run`` 才会调用 Provider。这个脚本
不创建项目、不扣积分、不写数据库，适合在全栈 smoke 之前按文本、视觉、
图片、视频顺序验证外部服务。报告不会输出 API key、密钥值、原始视觉响应
或本地绝对路径。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from studio_api.providers import (
    ComfyUIImageProvider,
    ComfyUIVideoProvider,
    OpenAICompatibleTextProvider,
    OpenAICompatibleVisionProvider,
    OpenAISpeechProvider,
    ProviderError,
)
from studio_core.workflow_contract import validate_comfyui_api_workflow


def target(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.hostname}:{port}"


def base_report(kind: str, base_url: str, *, run: bool) -> dict[str, Any]:
    return {
        "kind": kind,
        "provider": "openai" if kind == "speech" else "openai-compatible" if kind in {"text", "vision"} else "comfyui",
        "target": target(base_url),
        "run_requested": run,
    }


def output_file(output_dir: Path, asset_id: str) -> Path | None:
    candidates = sorted(path for path in output_dir.glob(f"{asset_id}.*") if path.is_file())
    return candidates[0] if candidates else None


def validate_workflow(workflow_path: Path | None, *, role: str) -> tuple[bool, str | None, dict[str, Any] | None]:
    """验证 ComfyUI workflow，但绝不把路径、原始 JSON 或解析异常写入报告。"""

    if not workflow_path:
        return False, "workflow-not-configured", None
    if not workflow_path.is_file():
        return False, "workflow-file-not-found", None
    try:
        payload = json.loads(workflow_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False, "workflow-file-invalid-json", None
    errors = validate_comfyui_api_workflow(payload, role=role)
    if errors:
        return False, errors[0], None
    return True, None, payload


def run_text(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url or os.environ.get("STUDIO_TEXT_BASE_URL", "")
    model = args.model or os.environ.get("STUDIO_TEXT_MODEL", "")
    api_key_env = args.api_key_env or os.environ.get("STUDIO_TEXT_API_KEY_ENV", "STUDIO_TEXT_API_KEY")
    report = base_report("text", base_url, run=args.run)
    report.update({"model_configured": bool(model), "credential_configured": bool(os.environ.get(api_key_env)), "api_key_env_configured": bool(api_key_env)})
    if not base_url:
        report.update({"status": "not-configured", "reason": "base-url-not-configured"})
        return report
    if not target(base_url) or not model or not api_key_env or not os.environ.get(api_key_env):
        report.update({"status": "failed" if args.run else "configuration-warning", "reason": "incomplete-text-provider-config"})
        return report
    if not args.run:
        report["status"] = "dry-run"
        return report
    started = time.perf_counter()
    try:
        raw, metadata = OpenAICompatibleTextProvider(base_url, model, api_key_env, timeout=args.timeout).complete(
            '只返回 JSON 对象：{"status":"ok"}。不要添加解释。',
            json_mode=True,
        )
        payload = json.loads(raw)
        if not isinstance(payload, (dict, list)):
            raise ProviderError("text provider returned unsupported JSON")
        report.update({"status": "completed", "elapsed_seconds": round(time.perf_counter() - started, 3), "response_json": True, "provider_metadata": {"provider": metadata.get("provider"), "model": metadata.get("model")}})
    except (ProviderError, OSError, ValueError, json.JSONDecodeError):
        report.update({"status": "failed", "elapsed_seconds": round(time.perf_counter() - started, 3), "reason": "text-provider-contract-failed"})
    return report


def run_vision(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url or os.environ.get("STUDIO_VISION_BASE_URL", "")
    model = args.model or os.environ.get("STUDIO_VISION_MODEL", "")
    api_key_env = args.api_key_env or os.environ.get("STUDIO_VISION_API_KEY_ENV", "STUDIO_VISION_API_KEY")
    image_path = Path(args.image) if args.image else (Path(os.environ["STUDIO_VISION_SMOKE_IMAGE"]) if os.environ.get("STUDIO_VISION_SMOKE_IMAGE") else None)
    report = base_report("vision", base_url, run=args.run)
    report.update({"model_configured": bool(model), "credential_configured": bool(os.environ.get(api_key_env)), "image_configured": bool(image_path), "image_available": bool(image_path and image_path.is_file()), "image_mime": mimetypes.guess_type(image_path.name)[0] if image_path else None})
    if not base_url:
        report.update({"status": "not-configured", "reason": "base-url-not-configured"})
        return report
    if not target(base_url) or not model or not api_key_env or not os.environ.get(api_key_env) or not image_path or not image_path.is_file():
        report.update({"status": "failed" if args.run else "configuration-warning", "reason": "incomplete-vision-provider-config"})
        return report
    if not args.run:
        report["status"] = "dry-run"
        return report
    started = time.perf_counter()
    try:
        result = OpenAICompatibleVisionProvider(base_url, model, api_key_env, timeout=args.timeout).review(image_path, "只判断图片是否适合漫剧关键帧；第一行只返回 PASS、FAIL 或 UNKNOWN，后续列出简短问题。")
        status = result.get("status")
        if status not in {"PASS", "FAIL", "UNKNOWN"}:
            raise ProviderError("vision provider returned invalid status")
        report.update({"status": "completed", "elapsed_seconds": round(time.perf_counter() - started, 3), "review_status": status, "issue_count": len(result.get("issues") or []), "provider_metadata": {"provider": result.get("provider"), "model": result.get("model")}})
    except (ProviderError, OSError, ValueError):
        report.update({"status": "failed", "elapsed_seconds": round(time.perf_counter() - started, 3), "reason": "vision-provider-contract-failed"})
    return report


def run_speech(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url or os.environ.get("STUDIO_SPEECH_BASE_URL", "")
    model = args.model or os.environ.get("STUDIO_SPEECH_MODEL", "")
    api_key_env = args.api_key_env or os.environ.get("STUDIO_SPEECH_API_KEY_ENV", "OPENAI_API_KEY")
    report = base_report("speech", base_url, run=args.run)
    report.update({
        "model_configured": bool(model),
        "credential_configured": bool(os.environ.get(api_key_env)),
        "voice": args.voice,
        "speed": args.speed,
    })
    if not base_url:
        report.update({"status": "not-configured", "reason": "base-url-not-configured"})
        return report
    if not target(base_url) or not model or not api_key_env or not os.environ.get(api_key_env):
        report.update({"status": "failed" if args.run else "configuration-warning", "reason": "incomplete-speech-provider-config"})
        return report
    if not args.run:
        report["status"] = "dry-run"
        return report
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="ai-manhua-speech-smoke-") as directory:
            generated = OpenAISpeechProvider(base_url, model, api_key_env, timeout=args.timeout).synthesize(
                "provider-smoke-speech",
                "这是一次语音 Provider 合同检查。",
                Path(directory),
                voice=args.voice,
                speed=args.speed,
            )
            path = output_file(Path(directory), "provider-smoke-speech")
            if not path:
                raise ProviderError("speech provider did not produce a local output")
            report.update({
                "status": "completed",
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "output_bytes": path.stat().st_size,
                "provider_metadata": {
                    "provider": generated.metadata.get("provider"),
                    "model": generated.metadata.get("model"),
                    "voice": generated.metadata.get("voice"),
                },
            })
    except (ProviderError, OSError, ValueError):
        report.update({"status": "failed", "elapsed_seconds": round(time.perf_counter() - started, 3), "reason": "speech-provider-contract-failed"})
    return report


def run_comfyui(args: argparse.Namespace, *, video: bool) -> dict[str, Any]:
    base_url = args.base_url or os.environ.get("COMFYUI_BASE_URL", "")
    workflow = args.workflow or os.environ.get("COMFYUI_VIDEO_WORKFLOW" if video else "COMFYUI_IMAGE_WORKFLOW", "")
    workflow_path = Path(workflow) if workflow else None
    source_image = Path(args.source_image) if args.source_image else None
    report = base_report("video" if video else "image", base_url, run=args.run)
    workflow_valid, workflow_error, workflow_payload = validate_workflow(workflow_path, role="video" if video else "image")
    report.update({
        "workflow_configured": bool(workflow_path),
        "workflow_available": bool(workflow_path and workflow_path.is_file()),
        "workflow_valid": workflow_valid,
    })
    if video:
        report.update({"source_image_configured": bool(source_image), "source_image_available": bool(source_image and source_image.is_file())})
    if not base_url:
        report.update({"status": "not-configured", "reason": "base-url-not-configured"})
        return report
    if not target(base_url):
        report.update({"status": "failed" if args.run else "configuration-warning", "reason": "incomplete-comfyui-provider-config"})
        return report
    if not workflow_valid:
        report.update({"status": "failed" if args.run else "configuration-warning", "reason": workflow_error or "invalid-comfyui-workflow"})
        return report
    if video and source_image and not source_image.is_file():
        report.update({"status": "failed" if args.run else "configuration-warning", "reason": "incomplete-comfyui-provider-config"})
        return report
    if not args.run:
        report["status"] = "dry-run"
        return report
    if video and "{{IMAGE_" in json.dumps(workflow_payload, ensure_ascii=False) and not source_image:
        report.update({"status": "failed", "reason": "source-image-not-configured"})
        return report
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_id = "provider-smoke-video" if video else "provider-smoke-image"
    started = time.perf_counter()
    try:
        if video:
            generated = ComfyUIVideoProvider(base_url, str(workflow_path), timeout=args.timeout, poll_interval=args.poll_interval).generate(
                asset_id,
                "provider-smoke-shot",
                output_dir,
                source_image_path=source_image,
                prompt=args.prompt,
            )
        else:
            generated = ComfyUIImageProvider(base_url, str(workflow_path), timeout=args.timeout, poll_interval=args.poll_interval).generate(asset_id, "provider-smoke", args.prompt, output_dir)
        path = output_file(output_dir, asset_id)
        if not path:
            raise ProviderError("provider did not produce a local output")
        report.update({"status": "completed", "elapsed_seconds": round(time.perf_counter() - started, 3), "relative_url": generated.relative_url, "output_bytes": path.stat().st_size, "provider_metadata": {"provider": generated.metadata.get("provider"), "prompt_id": generated.metadata.get("prompt_id")}})
    except (ProviderError, OSError, ValueError, json.JSONDecodeError):
        report.update({"status": "failed", "elapsed_seconds": round(time.perf_counter() - started, 3), "reason": "comfyui-provider-contract-failed"})
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("text", "vision", "speech", "image", "video"), required=True)
    parser.add_argument("--run", action="store_true", help="显式调用外部 Provider；默认只做脱敏配置检查")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--api-key-env", default="")
    parser.add_argument("--workflow", default="")
    parser.add_argument("--image", default="", help="vision smoke 的本地图片路径")
    parser.add_argument("--source-image", default="", help="video smoke 的关键帧路径")
    parser.add_argument("--prompt", default="provider smoke image")
    parser.add_argument("--voice", default="cedar")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--output-dir", default="/tmp/ai-manhua-provider-smoke")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args(argv)
    if args.timeout < 10:
        parser.error("--timeout must be at least 10 seconds")
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be positive")
    if not 0.25 <= args.speed <= 4:
        parser.error("--speed must be between 0.25 and 4")
    return args


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    if args.kind == "text":
        return run_text(args)
    if args.kind == "vision":
        return run_vision(args)
    if args.kind == "speech":
        return run_speech(args)
    return run_comfyui(args, video=args.kind == "video")


def main(argv: list[str] | None = None) -> int:
    report = build_report(parse_args(argv))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] in {"dry-run", "completed", "not-configured", "configuration-warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
