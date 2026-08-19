#!/usr/bin/env python3
"""阻断生产 Compose 使用已知占位凭据或开发模式配置。

这个 gate 只读取环境变量，不打印任何环境值、DSN 或 token。它不连接
PostgreSQL、Redis、MinIO，也不调用 Provider；真实依赖仍由 runtime preflight
和 stack smoke 验收。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from studio_core.workflow_contract import validate_comfyui_api_workflow
from studio_api.providers import DEFAULT_SPEECH_VOICES


PLACEHOLDERS = {
    "change-me-local-only",
    "local-internal-only-change-me",
    "local-orchestrator-only-change-me",
}


def _result(ok: bool, *, required: bool, state: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": ok, "required": required, "state": state}
    payload.update(extra)
    return payload


def check_production_config(
    environ: Mapping[str, str] | None = None,
    *,
    force: bool = False,
    required: bool = False,
    check_workflow_files: bool = False,
) -> dict[str, Any]:
    """检查生产启动所需的非默认配置，永远不返回秘密值。"""

    source = os.environ if environ is None else environ
    environment = str(source.get("STUDIO_ENV", "development")).strip().lower()
    if not force and environment != "production":
        return _result(True, required=False, state="not-production", production=False)

    errors: list[str] = []

    ci_billing_mode_value = str(source.get("STUDIO_BILLING_CI_MODE", "")).strip().lower()
    ci_billing_mode = ci_billing_mode_value in {"1", "true", "yes", "on"}
    if ci_billing_mode_value and ci_billing_mode_value not in {"0", "1", "false", "true", "no", "yes", "off", "on"}:
        errors.append("STUDIO_BILLING_CI_MODE:must-be-boolean")
    if ci_billing_mode and str(source.get("CI", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        errors.append("STUDIO_BILLING_CI_MODE:requires-CI")

    def require_value(name: str, *, min_length: int = 1, reject_placeholder: bool = False) -> None:
        value = str(source.get(name, "")).strip()
        if not value:
            errors.append(f"{name}:missing")
            return
        if reject_placeholder and (value in PLACEHOLDERS or "change-me" in value.lower()):
            errors.append(f"{name}:known-placeholder")
        if len(value) < min_length:
            errors.append(f"{name}:too-short")

    require_value("POSTGRES_PASSWORD", min_length=16, reject_placeholder=True)
    require_value("MINIO_ROOT_PASSWORD", min_length=16, reject_placeholder=True)
    require_value("ORCHESTRATOR_TOKEN", min_length=32, reject_placeholder=True)
    require_value("STUDIO_INTERNAL_TOKEN", min_length=32, reject_placeholder=True)
    require_value("STUDIO_DATABASE_URL", reject_placeholder=True)
    require_value("STUDIO_ALLOWED_HOSTS")
    require_value("STUDIO_CORS_ORIGINS")
    allowed_hosts = [host.strip().lower() for host in str(source.get("STUDIO_ALLOWED_HOSTS", "")).split(",") if host.strip()]
    cors_origins = [origin.strip() for origin in str(source.get("STUDIO_CORS_ORIGINS", "")).split(",") if origin.strip()]
    public_host = str(source.get("STUDIO_PUBLIC_HOST", "")).strip().lower()
    if public_host:
        public_host_url = urlparse(f"https://{public_host}")
        if (
            "*" in public_host
            or any(character.isspace() for character in public_host)
            or not public_host_url.hostname
            or public_host_url.hostname != public_host
            or public_host_url.path not in {"", "/"}
            or public_host_url.query
            or public_host_url.fragment
        ):
            errors.append("STUDIO_PUBLIC_HOST:must-be-hostname")
        elif public_host not in allowed_hosts:
            errors.append("STUDIO_PUBLIC_HOST:must-be-in-STUDIO_ALLOWED_HOSTS")
    public_api_base = str(source.get("NEXT_PUBLIC_API_BASE", "")).strip()
    if public_api_base:
        parsed_public_api = urlparse(public_api_base)
        if (
            not parsed_public_api.hostname
            or parsed_public_api.username
            or parsed_public_api.password
            or parsed_public_api.fragment
            or parsed_public_api.scheme != "https"
        ):
            errors.append("NEXT_PUBLIC_API_BASE:must-use-https-or-empty")
    for host in str(source.get("STUDIO_ALLOWED_HOSTS", "")).split(","):
        if "*" in host.strip():
            errors.append("STUDIO_ALLOWED_HOSTS:wildcard-not-allowed")
            break
    normalized_cors_origins: set[str] = set()
    for origin in cors_origins:
        normalized_origin = origin.strip().lower()
        normalized_cors_origins.add(normalized_origin.rstrip("/"))
        if normalized_origin == "null" or "*" in normalized_origin:
            errors.append("STUDIO_CORS_ORIGINS:wildcard-or-null-not-allowed")
            break
        parsed_origin = urlparse(origin)
        if (
            parsed_origin.scheme != "https"
            or not parsed_origin.hostname
            or parsed_origin.username
            or parsed_origin.password
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            errors.append("STUDIO_CORS_ORIGINS:must-be-https-origin")
            break
    if public_host and f"https://{public_host}" not in normalized_cors_origins:
        errors.append("STUDIO_CORS_ORIGINS:must-include-public-origin")
    billing_provider = str(source.get("STUDIO_BILLING_PROVIDER", "")).strip().lower()
    if billing_provider not in {"signed-webhook", "stripe"}:
        errors.append("STUDIO_BILLING_PROVIDER:must-be-signed-webhook-or-stripe")
    if billing_provider == "signed-webhook":
        require_value("STUDIO_BILLING_WEBHOOK_SECRET", min_length=32, reject_placeholder=True)
        checkout_url = str(source.get("STUDIO_BILLING_CHECKOUT_URL", "")).strip()
        if not checkout_url:
            errors.append("STUDIO_BILLING_CHECKOUT_URL:missing")
        else:
            parsed_checkout = urlparse(checkout_url)
            if not parsed_checkout.hostname or parsed_checkout.username or parsed_checkout.password or parsed_checkout.fragment or parsed_checkout.scheme not in {"http", "https"}:
                errors.append("STUDIO_BILLING_CHECKOUT_URL:invalid-url")
            elif parsed_checkout.scheme != "https":
                errors.append("STUDIO_BILLING_CHECKOUT_URL:must-use-https")
    elif billing_provider == "stripe":
        require_value("STRIPE_SECRET_KEY", min_length=32, reject_placeholder=True)
        require_value("STRIPE_WEBHOOK_SECRET", min_length=32, reject_placeholder=True)
        for name in ("STUDIO_BILLING_SUCCESS_URL", "STUDIO_BILLING_CANCEL_URL"):
            value = str(source.get(name, "")).strip()
            parsed = urlparse(value)
            if not value:
                errors.append(f"{name}:missing")
            elif not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.scheme not in {"http", "https"}:
                errors.append(f"{name}:invalid-url")
            elif parsed.scheme != "https":
                errors.append(f"{name}:must-use-https")
        stripe_api_base = str(source.get("STRIPE_API_BASE_URL", "https://api.stripe.com")).strip()
        parsed_stripe_api = urlparse(stripe_api_base)
        stripe_http_allowed_for_ci = ci_billing_mode and str(source.get("CI", "")).strip().lower() in {"1", "true", "yes", "on"}
        if not parsed_stripe_api.hostname or parsed_stripe_api.username or parsed_stripe_api.password or parsed_stripe_api.fragment or (
            parsed_stripe_api.scheme != "https" and not (stripe_http_allowed_for_ci and parsed_stripe_api.scheme == "http")
        ):
            errors.append("STRIPE_API_BASE_URL:must-use-https-and-no-credentials")

    if str(source.get("STUDIO_REQUIRE_AUTH", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        errors.append("STUDIO_REQUIRE_AUTH:must-be-true")
    if str(source.get("STUDIO_ALLOW_LOCAL_TOP_UP", "false")).strip().lower() in {"1", "true", "yes", "on"}:
        errors.append("STUDIO_ALLOW_LOCAL_TOP_UP:must-be-false")
    if str(source.get("STUDIO_AUTH_COOKIE_SECURE", "true")).strip().lower() not in {"1", "true", "yes", "on"}:
        errors.append("STUDIO_AUTH_COOKIE_SECURE:must-be-true")
    if str(source.get("STUDIO_STORE", "")).strip().lower() != "postgres":
        errors.append("STUDIO_STORE:must-be-postgres")
    if str(source.get("STUDIO_QUEUE_BACKEND", "")).strip().lower() != "bullmq":
        errors.append("STUDIO_QUEUE_BACKEND:must-be-bullmq")
    if str(source.get("STUDIO_STORAGE", "")).strip().lower() not in {"s3", "minio"}:
        errors.append("STUDIO_STORAGE:must-be-s3-or-minio")

    def require_provider(name: str, allowed: set[str]) -> str:
        value = str(source.get(name, "")).strip().lower()
        if not value:
            errors.append(f"{name}:missing")
        elif value not in allowed:
            errors.append(f"{name}:must-be-{'-or-'.join(sorted(allowed))}")
        return value

    def require_provider_url(name: str) -> None:
        value = str(source.get(name, "")).strip()
        if not value:
            errors.append(f"{name}:missing")
            return
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            errors.append(f"{name}:invalid-url")

    def require_provider_key(env_name: str, default_env_name: str) -> None:
        key_env = str(source.get(env_name, default_env_name)).strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
            errors.append(f"{env_name}:invalid-environment-name")
            return
        if key_env != default_env_name:
            errors.append(f"{env_name}:must-be-{default_env_name}")
            return
        require_value(key_env, min_length=1, reject_placeholder=True)

    def require_workflow_file(name: str) -> None:
        if not check_workflow_files:
            return
        value = str(source.get(name, "")).strip()
        if not value:
            return
        if not os.path.isfile(value):
            errors.append(f"{name}:file-not-found")
            return
        try:
            with open(value, "r", encoding="utf-8") as workflow_file:
                workflow = json.load(workflow_file)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            errors.append(f"{name}:invalid-json")
            return
        role = "image" if name == "COMFYUI_IMAGE_WORKFLOW" else "video"
        if name == "COMFYUI_H3_LONG_VIDEO_FIRST_WORKFLOW":
            role = "long_video_first"
        elif name == "COMFYUI_H3_LONG_VIDEO_CONTEXT_WORKFLOW":
            role = "long_video_context"
        for reason in validate_comfyui_api_workflow(workflow, role=role):
            errors.append(f"{name}:{reason.removeprefix('workflow-')}")

    text_provider = require_provider("STUDIO_TEXT_PROVIDER", {"openai-compatible"})
    if text_provider == "openai-compatible":
        require_provider_url("STUDIO_TEXT_BASE_URL")
        require_value("STUDIO_TEXT_MODEL")
        require_provider_key("STUDIO_TEXT_API_KEY_ENV", "STUDIO_TEXT_API_KEY")

    vision_provider = require_provider("STUDIO_VISION_PROVIDER", {"openai-compatible"})
    if vision_provider == "openai-compatible":
        require_provider_url("STUDIO_VISION_BASE_URL")
        require_value("STUDIO_VISION_MODEL")
        require_provider_key("STUDIO_VISION_API_KEY_ENV", "STUDIO_VISION_API_KEY")

    speech_provider = require_provider("STUDIO_SPEECH_PROVIDER", {"openai"})
    if speech_provider == "openai":
        require_provider_url("STUDIO_SPEECH_BASE_URL")
        require_value("STUDIO_SPEECH_MODEL")
        require_provider_key("STUDIO_SPEECH_API_KEY_ENV", "OPENAI_API_KEY")

    configured_speech_voices = str(source.get("STUDIO_SPEECH_ALLOWED_VOICES", "")).strip()
    if configured_speech_voices:
        voice_values = [item.strip().lower() for item in configured_speech_voices.split(",")]
        if not voice_values or any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item) for item in voice_values):
            errors.append("STUDIO_SPEECH_ALLOWED_VOICES:invalid-list")
        elif len(set(voice_values)) != len(voice_values):
            errors.append("STUDIO_SPEECH_ALLOWED_VOICES:duplicate-voice")
        elif any(item not in DEFAULT_SPEECH_VOICES for item in voice_values):
            errors.append("STUDIO_SPEECH_ALLOWED_VOICES:unknown-voice")

    image_provider = require_provider("STUDIO_IMAGE_PROVIDER", {"comfyui"})
    if image_provider == "comfyui":
        require_provider_url("COMFYUI_BASE_URL")
        require_value("COMFYUI_IMAGE_WORKFLOW")
        require_workflow_file("COMFYUI_IMAGE_WORKFLOW")

    video_provider = require_provider("STUDIO_VIDEO_PROVIDER", {"comfyui"})
    if video_provider == "comfyui":
        require_provider_url("COMFYUI_BASE_URL")
        configured_video_workflows = [
            name for name in (
                "COMFYUI_VIDEO_WORKFLOW",
                "COMFYUI_T2V_VIDEO_WORKFLOW",
                "COMFYUI_R2V_VIDEO_WORKFLOW",
                "COMFYUI_H3_LONG_VIDEO_FIRST_WORKFLOW",
                "COMFYUI_H3_LONG_VIDEO_CONTEXT_WORKFLOW",
            ) if str(source.get(name, "")).strip()
        ]
        if not configured_video_workflows:
            require_value("COMFYUI_VIDEO_WORKFLOW")
        for workflow_name in configured_video_workflows:
            require_workflow_file(workflow_name)

    return _result(
        not errors,
        required=required,
        state="configured" if not errors else "configuration-error",
        production=True,
        errors=errors,
        checked=[
            "POSTGRES_PASSWORD",
            "MINIO_ROOT_PASSWORD",
            "ORCHESTRATOR_TOKEN",
            "STUDIO_INTERNAL_TOKEN",
            "STUDIO_DATABASE_URL",
            "STUDIO_ALLOWED_HOSTS",
            "STUDIO_CORS_ORIGINS",
            "STUDIO_PUBLIC_HOST",
            "NEXT_PUBLIC_API_BASE",
            "STUDIO_BILLING_PROVIDER",
            "STUDIO_BILLING_WEBHOOK_SECRET",
            "STUDIO_BILLING_CHECKOUT_URL",
            "STRIPE_SECRET_KEY",
            "STRIPE_WEBHOOK_SECRET",
            "STRIPE_API_BASE_URL",
            "STUDIO_BILLING_SUCCESS_URL",
            "STUDIO_BILLING_CANCEL_URL",
            "STUDIO_TEXT_PROVIDER",
            "STUDIO_TEXT_BASE_URL",
            "STUDIO_TEXT_MODEL",
            "STUDIO_TEXT_API_KEY_ENV",
            "STUDIO_TEXT_API_KEY",
            "STUDIO_IMAGE_PROVIDER",
            "COMFYUI_BASE_URL",
            "COMFYUI_IMAGE_WORKFLOW",
            "STUDIO_VIDEO_PROVIDER",
            "COMFYUI_VIDEO_WORKFLOW",
            "COMFYUI_T2V_VIDEO_WORKFLOW",
            "COMFYUI_R2V_VIDEO_WORKFLOW",
            "COMFYUI_H3_LONG_VIDEO_FIRST_WORKFLOW",
            "COMFYUI_H3_LONG_VIDEO_CONTEXT_WORKFLOW",
            "STUDIO_VISION_PROVIDER",
            "STUDIO_VISION_BASE_URL",
            "STUDIO_VISION_MODEL",
            "STUDIO_VISION_API_KEY_ENV",
            "STUDIO_VISION_API_KEY",
            "STUDIO_SPEECH_PROVIDER",
            "STUDIO_SPEECH_BASE_URL",
            "STUDIO_SPEECH_MODEL",
            "STUDIO_SPEECH_API_KEY_ENV",
            "STUDIO_SPEECH_ALLOWED_VOICES",
            "OPENAI_API_KEY",
            "STUDIO_REQUIRE_AUTH",
            "STUDIO_ALLOW_LOCAL_TOP_UP",
            "STUDIO_AUTH_COOKIE_SECURE",
            "STUDIO_STORE",
            "STUDIO_QUEUE_BACKEND",
            "STUDIO_STORAGE",
        ],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--require-production", action="store_true", help="即使 STUDIO_ENV 未设置为 production 也执行生产 gate")
    parser.add_argument("--require-workflow-files", action="store_true", help="同时检查 ComfyUI workflow 文件已挂载")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = check_production_config(
        force=args.require_production,
        required=args.require_production or os.environ.get("STUDIO_ENV", "").lower() == "production",
        check_workflow_files=args.require_workflow_files,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"production-config: {report['state']}")
        if report.get("errors"):
            print("errors: " + ", ".join(report["errors"]))
    return 1 if report["required"] and not report["ok"] else 0


if __name__ == "__main__":
    sys.exit(main())
