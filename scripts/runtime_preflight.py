#!/usr/bin/env python3
"""只读检查 AI 漫剧工作台的运行时边界。

这个脚本不登录、不写数据库、不调用 Provider，也不会打印任何环境变量值。
默认是报告模式；配合 ``--require-live`` 和显式依赖开关可作为部署前 gate。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Mapping
from urllib.parse import urlparse

try:
    from scripts.production_config_check import check_production_config
except ModuleNotFoundError:  # direct ``python scripts/runtime_preflight.py`` execution
    from production_config_check import check_production_config


DEFAULT_TIMEOUT = 3.0
CONFIG_ENV_NAMES = (
    "STUDIO_STORE",
    "STUDIO_DATABASE_URL",
    "STUDIO_QUEUE_BACKEND",
    "STUDIO_RATE_LIMIT_REDIS_URL",
    "STUDIO_STORAGE",
    "STUDIO_S3_BUCKET",
    "STUDIO_S3_ENDPOINT_URL",
    "STUDIO_TEXT_PROVIDER",
    "STUDIO_TEXT_BASE_URL",
    "STUDIO_TEXT_MODEL",
    "STUDIO_TEXT_API_KEY_ENV",
    "STUDIO_IMAGE_PROVIDER",
    "STUDIO_IMAGE_API_KEY_ENV",
    "STUDIO_VIDEO_PROVIDER",
    "STUDIO_VIDEO_API_KEY_ENV",
    "STUDIO_VISION_PROVIDER",
    "STUDIO_VISION_BASE_URL",
    "STUDIO_VISION_MODEL",
    "STUDIO_VISION_API_KEY_ENV",
    "STUDIO_SPEECH_PROVIDER",
    "STUDIO_SPEECH_BASE_URL",
    "STUDIO_SPEECH_MODEL",
    "STUDIO_SPEECH_API_KEY_ENV",
    "STUDIO_SPEECH_ALLOWED_VOICES",
    "STUDIO_COMPOSE_ENGINE",
    "COMFYUI_BASE_URL",
    "COMFYUI_IMAGE_WORKFLOW",
    "COMFYUI_VIDEO_WORKFLOW",
    "STUDIO_OTEL_ENABLED",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "STUDIO_ENV",
    "STUDIO_REQUIRE_AUTH",
    "STUDIO_ALLOW_LOCAL_TOP_UP",
    "STUDIO_BILLING_PROVIDER",
    "STUDIO_BILLING_WEBHOOK_SECRET",
    "STUDIO_BILLING_CHECKOUT_URL",
    "STRIPE_API_BASE_URL",
    "STUDIO_BILLING_SUCCESS_URL",
    "STUDIO_BILLING_CANCEL_URL",
    "STUDIO_AUTH_COOKIE_SECURE",
    "STUDIO_ALLOWED_HOSTS",
    "STUDIO_CORS_ORIGINS",
    "STUDIO_PUBLIC_HOST",
    "POSTGRES_PASSWORD",
    "MINIO_ROOT_PASSWORD",
    "ORCHESTRATOR_TOKEN",
    "STUDIO_INTERNAL_TOKEN",
)


def command_check(name: str) -> dict[str, Any]:
    """返回命令是否可用，不暴露本机绝对路径。"""

    return {
        "available": shutil.which(name) is not None,
        "required": False,
    }


def container_runtime_check(*, required: bool, timeout: float = 5.0) -> dict[str, Any]:
    """只读探测 Docker/Podman daemon，不返回命令输出。"""

    runtime = next((name for name in ("docker", "podman") if shutil.which(name)), None)
    if not runtime:
        return _result(False, required=required, error="docker-or-podman-not-found")
    try:
        result = subprocess.run(
            [runtime, "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _result(False, required=required, state="timeout", runtime=runtime, error="daemon-timeout")
    except OSError:
        return _result(False, required=required, state="unavailable", runtime=runtime, error="daemon-unavailable")
    if result.returncode != 0:
        return _result(False, required=required, state="unavailable", runtime=runtime, error="daemon-unavailable")
    return _result(True, required=required, runtime=runtime)


def gpu_runtime_check(*, required: bool, timeout: float = 5.0) -> dict[str, Any]:
    """只读检查 NVIDIA GPU/驱动是否能被当前运行时发现。

    这里只执行 ``nvidia-smi`` 的固定查询，不返回 GPU 型号、主机名或命令
    原文；ComfyUI 节点仍需另外通过 HTTP 和真实 workflow smoke 验收。
    """

    executable = shutil.which("nvidia-smi")
    if not executable:
        return _result(False, required=required, state="not-installed", error="nvidia-smi-not-found")
    try:
        result = subprocess.run(
            [executable, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _result(False, required=required, state="timeout", error="nvidia-smi-timeout")
    except OSError:
        return _result(False, required=required, state="unavailable", error="nvidia-smi-unavailable")
    if result.returncode != 0:
        return _result(False, required=required, state="unavailable", error="nvidia-smi-query-failed")
    memories: list[int] = []
    for line in result.stdout.splitlines():
        value = line.strip()
        if not value:
            continue
        try:
            memories.append(int(value))
        except ValueError:
            return _result(False, required=required, state="invalid", error="nvidia-smi-invalid-query")
    if not memories:
        return _result(False, required=required, state="unavailable", error="nvidia-gpu-not-found")
    return _result(
        True,
        required=required,
        accelerator="nvidia",
        device_count=len(memories),
        memory_total_mb=memories,
    )


def env_presence(environ: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """只报告配置是否存在，永远不返回配置值。"""

    source = os.environ if environ is None else environ
    return {
        name: {"configured": bool(source.get(name)), "required": False}
        for name in CONFIG_ENV_NAMES
    }


def provider_configuration(
    environ: Mapping[str, str] | None = None,
    *,
    required: bool = False,
) -> dict[str, Any]:
    """检查已启用 Provider 的非秘密配置，不调用 Provider。

    这里只返回 URL 主机、配置布尔值和错误类型，不返回 API key、workflow
    内容或环境变量值。``required`` 通常由 ``--require-live`` 打开，避免
    本地报告模式把未配置的真实 Provider 当成失败。
    """

    source = os.environ if environ is None else environ
    checks: dict[str, dict[str, Any]] = {}

    def valid_url(value: str) -> tuple[bool, str | None, str | None]:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return False, None, "invalid-base-url"
        return True, _url_target(value), None

    def openai_check(kind: str, provider: str, base_env: str, model_env: str, key_env_name: str) -> dict[str, Any]:
        base_url = source.get(base_env, "").strip()
        model = source.get(model_env, "").strip()
        default_key_env = {
            "text": "STUDIO_TEXT_API_KEY",
            "vision": "STUDIO_VISION_API_KEY",
            "speech": "OPENAI_API_KEY",
        }.get(kind, "STUDIO_TEXT_API_KEY")
        api_key_env = source.get(key_env_name, default_key_env).strip()
        url_ok, target, url_error = valid_url(base_url)
        key_name_ok = bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env))
        key_ok = bool(source.get(api_key_env)) if key_name_ok else False
        errors = []
        if not base_url:
            errors.append("missing-base-url")
        elif url_error:
            errors.append(url_error)
        if not model:
            errors.append("missing-model")
        if not key_name_ok:
            errors.append("invalid-api-key-env")
        elif not key_ok:
            errors.append("missing-api-key")
        result = _result(
            not errors,
            required=required,
            state="configured" if not errors else "configuration-error",
            provider=provider,
            target=target,
            model_configured=bool(model),
            credential_configured=key_ok,
            errors=errors,
        )
        return result

    def comfyui_check(kind: str, provider: str, workflow_env: str) -> dict[str, Any]:
        base_url = source.get("COMFYUI_BASE_URL", "").strip()
        workflow = source.get(workflow_env, "").strip()
        url_ok, target, url_error = valid_url(base_url)
        errors = []
        if not base_url:
            errors.append("missing-base-url")
        elif url_error:
            errors.append(url_error)
        if not workflow:
            errors.append("missing-workflow")
        return _result(
            not errors,
            required=required,
            state="configured" if not errors else "configuration-error",
            provider=provider,
            target=target,
            workflow_configured=bool(workflow),
            errors=errors,
        )

    provider_specs = {
        "text": ("STUDIO_TEXT_PROVIDER", "openai-compatible", "STUDIO_TEXT_BASE_URL", "STUDIO_TEXT_MODEL", "STUDIO_TEXT_API_KEY_ENV", None),
        "image": ("STUDIO_IMAGE_PROVIDER", "comfyui", None, None, None, "COMFYUI_IMAGE_WORKFLOW"),
        "video": ("STUDIO_VIDEO_PROVIDER", "comfyui", None, None, None, "COMFYUI_VIDEO_WORKFLOW"),
        "vision": ("STUDIO_VISION_PROVIDER", "openai-compatible", "STUDIO_VISION_BASE_URL", "STUDIO_VISION_MODEL", "STUDIO_VISION_API_KEY_ENV", None),
        "speech": ("STUDIO_SPEECH_PROVIDER", "openai", "STUDIO_SPEECH_BASE_URL", "STUDIO_SPEECH_MODEL", "STUDIO_SPEECH_API_KEY_ENV", None),
    }
    for kind, (provider_env, real_provider, base_env, model_env, key_env_name, workflow_env) in provider_specs.items():
        provider = source.get(provider_env, "local").strip().lower()
        if provider == "local":
            checks[kind] = _result(True, required=False, state="local-preview", provider=provider)
        elif provider in {"openai-compatible", "openai"} and base_env and model_env and key_env_name:
            checks[kind] = openai_check(kind, provider, base_env, model_env, key_env_name)
        elif provider == "comfyui" and workflow_env:
            checks[kind] = comfyui_check(kind, provider, workflow_env)
        elif provider == real_provider:
            checks[kind] = _result(False, required=required, state="configuration-error", provider=provider, errors=["provider-specification-error"])
        else:
            checks[kind] = _result(False, required=required, state="configuration-error", provider=provider or "missing", errors=["unsupported-provider"])

    active = {name: result for name, result in checks.items() if result.get("provider") != "local"}
    overall_required = required and bool(active)
    overall_ok = all(result["ok"] for result in active.values()) if active else True
    return _result(
        overall_ok,
        required=overall_required,
        state="local-only" if not active else ("configured" if overall_ok else "configuration-error"),
        providers=checks,
    )


def _result(ok: bool, *, required: bool, state: str | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": ok,
        "required": required,
        "state": state or ("ok" if ok else "unavailable"),
    }
    payload.update(extra)
    return payload


def tcp_check(label: str, host: str, port: int, *, required: bool, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """只做 TCP 可达性检查；不会发送数据库、Redis 或对象存储凭据。"""

    target = f"{host}:{port}"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return _result(True, required=required, target=target)
    except socket.timeout:
        return _result(False, required=required, target=target, error="timeout")
    except OSError as exc:
        error = "connection-refused" if getattr(exc, "errno", None) in {61, 111, 10061} else "unreachable"
        return _result(False, required=required, target=target, error=error)
    except (TypeError, ValueError):
        return _result(False, required=required, state="invalid", target=target, error="invalid-target")


def probe_postgres(dsn: str, *, required: bool, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """使用现有 psycopg 依赖执行只读 ``SELECT 1``，不回显 DSN 或异常正文。"""

    if not str(dsn or "").strip():
        return _result(False, required=required, state="configuration-error", error="missing-database-url")
    try:
        import psycopg
    except ModuleNotFoundError:
        return _result(False, required=required, state="unavailable", error="psycopg-not-installed")
    try:
        with psycopg.connect(str(dsn), connect_timeout=max(1, int(timeout))) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
        if not result or result[0] != 1:
            return _result(False, required=required, state="contract-error", error="select-one-failed")
    except Exception:
        return _result(False, required=required, state="unavailable", error="postgres-authenticated-probe-failed")
    return _result(True, required=required, state="authenticated", operation="select-1")


def probe_redis(url: str, *, required: bool, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """使用现有 redis 依赖执行只读 ``PING``，不回显连接 URL 或异常正文。"""

    if not str(url or "").strip():
        return _result(False, required=required, state="configuration-error", error="missing-redis-url")
    try:
        import redis
    except ModuleNotFoundError:
        return _result(False, required=required, state="unavailable", error="redis-client-not-installed")
    client = None
    try:
        client = redis.Redis.from_url(
            str(url),
            socket_connect_timeout=max(1, int(timeout)),
            socket_timeout=max(1, int(timeout)),
            retry_on_timeout=False,
        )
        if client.ping() is not True:
            return _result(False, required=required, state="contract-error", error="redis-ping-failed")
    except Exception:
        return _result(False, required=required, state="unavailable", error="redis-authenticated-probe-failed")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return _result(True, required=required, state="authenticated", operation="ping")


def probe_s3(
    *,
    endpoint_url: str | None,
    bucket: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
    region: str,
    required: bool,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """使用现有 boto3 依赖执行只读 ``HeadBucket``，不回显凭据或异常正文。"""

    if not str(bucket or "").strip():
        return _result(False, required=required, state="configuration-error", error="missing-s3-bucket")
    try:
        import boto3
        from botocore.config import Config
    except ModuleNotFoundError:
        return _result(False, required=required, state="unavailable", error="boto3-not-installed")
    client = None
    try:
        client_kwargs: dict[str, Any] = {
            "region_name": str(region or "us-east-1"),
            "config": Config(
                connect_timeout=max(1, int(timeout)),
                read_timeout=max(1, int(timeout)),
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        }
        if str(endpoint_url or "").strip():
            client_kwargs["endpoint_url"] = str(endpoint_url).strip()
        if str(access_key_id or "").strip() and str(secret_access_key or "").strip():
            client_kwargs["aws_access_key_id"] = str(access_key_id).strip()
            client_kwargs["aws_secret_access_key"] = str(secret_access_key).strip()
        client = boto3.client("s3", **client_kwargs)
        client.head_bucket(Bucket=str(bucket).strip())
    except Exception:
        return _result(False, required=required, state="unavailable", error="s3-authenticated-probe-failed")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return _result(True, required=required, state="authenticated", operation="head-bucket")


def authenticated_dependency_probe(
    environ: Mapping[str, str],
    *,
    required: bool,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """对生产数据服务执行显式、只读、认证后的连接合同。"""

    services = {
        "postgres": probe_postgres(
            environ.get("STUDIO_DATABASE_URL", ""),
            required=required,
            timeout=timeout,
        ),
        "redis": probe_redis(
            environ.get("STUDIO_RATE_LIMIT_REDIS_URL", "") or environ.get("REDIS_URL", ""),
            required=required,
            timeout=timeout,
        ),
        "s3": probe_s3(
            endpoint_url=environ.get("STUDIO_S3_ENDPOINT_URL", ""),
            bucket=environ.get("STUDIO_S3_BUCKET", ""),
            access_key_id=environ.get("AWS_ACCESS_KEY_ID", ""),
            secret_access_key=environ.get("AWS_SECRET_ACCESS_KEY", ""),
            region=environ.get("STUDIO_S3_REGION", "us-east-1"),
            required=required,
            timeout=timeout,
        ),
    }
    ok = all(result["ok"] for result in services.values())
    return _result(
        ok,
        required=required,
        state="authenticated" if ok else "probe-failed",
        services=services,
    )


def _url_target(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "?"
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return f"{host}:{port}"


def http_check(
    label: str,
    url: str,
    path: str,
    *,
    required: bool,
    expected_json: Mapping[str, Any] | None = None,
    require_json_object: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """GET 一个健康端点并只返回状态码/合同结果，不返回响应正文。"""

    if not url:
        return _result(False, required=required, state="not-configured", error="missing-url")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return _result(False, required=required, state="invalid", error="invalid-url")
    request_url = url.rstrip("/") + "/" + path.lstrip("/")
    request = urllib.request.Request(request_url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = response.status
            raw = response.read(128 * 1024)
        if status_code < 200 or status_code >= 300:
            return _result(False, required=required, target=_url_target(url), status_code=status_code, error="unexpected-status")
        if expected_json or require_json_object:
            try:
                body = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return _result(False, required=required, target=_url_target(url), status_code=status_code, error="invalid-json")
            if require_json_object and not isinstance(body, dict):
                return _result(False, required=required, target=_url_target(url), status_code=status_code, error="invalid-json-object")
            if expected_json and (not isinstance(body, dict) or any(body.get(key) != value for key, value in expected_json.items())):
                return _result(False, required=required, target=_url_target(url), status_code=status_code, error="contract-mismatch")
        return _result(True, required=required, target=_url_target(url), status_code=status_code)
    except urllib.error.HTTPError as exc:
        return _result(False, required=required, target=_url_target(url), status_code=exc.code, error="unexpected-status")
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
        return _result(False, required=required, target=_url_target(url), error="unreachable")


def _endpoint_parts(url: str, default_host: str, default_port: int) -> tuple[str, int]:
    parsed = urlparse(url)
    return parsed.hostname or default_host, parsed.port or default_port


def build_report(args: argparse.Namespace, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = os.environ if environ is None else environ
    require_live = bool(args.require_live)
    commands = {name: command_check(name) for name in ("docker", "podman", "ffmpeg", "node", "npm", "redis-server", "postgres", "minio", "nvidia-smi")}
    env = env_presence(source)

    store = source.get("STUDIO_STORE", "sqlite").lower()
    queue_backend = source.get("STUDIO_QUEUE_BACKEND", "local").lower()
    storage = source.get("STUDIO_STORAGE", "local").lower()
    image_provider = source.get("STUDIO_IMAGE_PROVIDER", "local").lower()
    video_provider = source.get("STUDIO_VIDEO_PROVIDER", "local").lower()
    composition_engine = source.get("STUDIO_COMPOSE_ENGINE", "ffmpeg").lower()

    require_postgres = bool(args.require_postgres)
    require_redis = bool(args.require_redis)
    require_minio = bool(args.require_minio)
    require_comfyui = bool(args.require_comfyui)
    require_orchestrator = bool(args.require_orchestrator)
    require_container_runtime = bool(args.require_container_runtime)
    require_gpu = bool(args.require_gpu)
    require_provider_config = bool(args.require_provider_config or require_live)
    require_production_config = bool(args.require_production_config or (require_live and source.get("STUDIO_ENV", "").lower() == "production"))

    container_runtime = container_runtime_check(required=require_container_runtime)
    gpu = gpu_runtime_check(required=require_gpu)

    if composition_engine == "playlist":
        composition_engine_check = _result(True, required=require_live, engine=composition_engine, reason="playlist-only")
    elif composition_engine == "ffmpeg":
        ffmpeg_ready = commands["ffmpeg"]["available"]
        composition_engine_check = _result(
            ffmpeg_ready,
            required=require_live,
            engine=composition_engine,
            error=None if ffmpeg_ready else "ffmpeg-not-found",
        )
    elif composition_engine == "remotion":
        renderer_root = source.get("STUDIO_REMOTION_ROOT", "") or os.path.join(os.path.dirname(__file__), "..", "rendering")
        renderer_root = os.path.abspath(renderer_root)
        configured_command = source.get("STUDIO_REMOTION_RENDER_COMMAND", "").strip()
        try:
            command_parts = shlex.split(configured_command) if configured_command else []
        except ValueError:
            command_parts = []
        if configured_command:
            command_name = command_parts[0] if command_parts else ""
            command_ready = bool(command_name and (os.path.isfile(command_name) or shutil.which(command_name)))
            runner_ready = bool(command_parts and command_ready)
        else:
            command_ready = commands["npm"]["available"]
            runner_ready = (
                os.path.isfile(os.path.join(renderer_root, "node_modules", ".bin", "tsx"))
                or os.path.isfile(os.path.join(renderer_root, "node_modules", ".bin", "tsx.cmd"))
            )
        renderer_ready = (
            command_ready
            and runner_ready
            and os.path.isfile(os.path.join(renderer_root, "package.json"))
            and os.path.isfile(os.path.join(renderer_root, "node_modules", "@remotion", "renderer", "package.json"))
        )
        composition_engine_check = _result(
            renderer_ready,
            required=require_live,
            engine=composition_engine,
            error=None if renderer_ready else "remotion-runtime-incomplete",
        )
    else:
        composition_engine_check = _result(False, required=require_live, engine=composition_engine, error="unsupported-engine")

    api_health = http_check("api_health", args.api_url, "/api/health", required=require_live, expected_json={"status": "ok"})
    api_ready = http_check("api_ready", args.api_url, "/api/ready", required=require_live, expected_json={"status": "ready"})
    web = http_check("web", args.web_url, "/", required=require_live)

    orchestrator_url = args.orchestrator_url or source.get("STUDIO_ORCHESTRATOR_URL", "")
    if require_live and queue_backend == "bullmq" and not args.skip_orchestrator:
        require_orchestrator = True
    orchestrator = http_check(
        "orchestrator",
        orchestrator_url,
        "/health",
        required=require_orchestrator and not args.skip_orchestrator,
        expected_json={"status": "ok"},
    )

    if require_live and store == "postgres" and not args.skip_postgres:
        require_postgres = True
    postgres = tcp_check("postgres", args.postgres_host, args.postgres_port, required=require_postgres and not args.skip_postgres)

    if require_live and queue_backend == "bullmq" and not args.skip_redis:
        require_redis = True
    redis = tcp_check("redis", args.redis_host, args.redis_port, required=require_redis and not args.skip_redis)

    if require_live and storage in {"s3", "minio"} and not args.skip_minio:
        require_minio = True
    minio_url = args.minio_url or source.get("STUDIO_S3_ENDPOINT_URL", "http://127.0.0.1:9000")
    minio = http_check(
        "minio",
        minio_url,
        "/minio/health/live",
        required=require_minio and not args.skip_minio,
    )

    if require_live and (image_provider == "comfyui" or video_provider == "comfyui") and not args.skip_comfyui:
        require_comfyui = True
    comfy_url = args.comfyui_url or source.get("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    comfyui = http_check(
        "comfyui",
        comfy_url,
        "/system_stats",
        required=require_comfyui and not args.skip_comfyui,
        require_json_object=True,
    )
    providers = provider_configuration(source, required=require_provider_config)
    production_config = check_production_config(
        source,
        force=bool(args.require_production_config),
        required=require_production_config,
        check_workflow_files=require_production_config,
    )
    authenticated_dependencies = (
        authenticated_dependency_probe(source, required=True)
        if args.probe_data_services
        else _result(True, required=False, state="not-requested", services={})
    )

    checks = {
        "container_runtime": container_runtime,
        "gpu": gpu,
        "composition_engine": composition_engine_check,
        "api_health": api_health,
        "api_ready": api_ready,
        "web": web,
        "orchestrator": orchestrator,
        "postgres": postgres,
        "redis": redis,
        "minio": minio,
        "comfyui": comfyui,
        "provider_configuration": providers,
        "production_config": production_config,
        "authenticated_dependencies": authenticated_dependencies,
    }
    required_failures = [name for name, result in checks.items() if result["required"] and not result["ok"]]
    status = "failed" if required_failures else "passed"
    if not required_failures and not require_live and any(not result["ok"] and result["state"] not in {"not-configured", "unavailable"} for result in checks.values()):
        status = "warning"

    notes = [
        "默认只做命令、端口和 HTTP 健康合同检查；不会登录数据库/Redis/MinIO，也不会调用 Provider。",
        "通过本预检不等于真实生成、GPU 推理、对象存储读写或容器 build/up 已完成。",
    ]
    if args.probe_data_services:
        notes = [
            "已使用环境变量执行 PostgreSQL SELECT 1、Redis PING 和 S3/MinIO HeadBucket 只读认证探针；不会打印 DSN、URL、凭据或异常正文。",
            "认证探针通过仍不等于真实生成、GPU 推理、Provider workflow、容器 build/up 或公网验收完成。",
        ]
    return {
        "status": status,
        "mode": "require-live" if require_live else "report",
        "required_failures": required_failures,
        "commands": commands,
        "environment": env,
        "checks": checks,
        "notes": notes,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--require-live", action="store_true", help="将 API/Web 和已配置的生产依赖缺失视为失败")
    parser.add_argument("--api-url", default=os.environ.get("STUDIO_PREFLIGHT_API_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--web-url", default=os.environ.get("STUDIO_PREFLIGHT_WEB_URL", "http://127.0.0.1:3000"))
    parser.add_argument("--orchestrator-url", default=os.environ.get("STUDIO_PREFLIGHT_ORCHESTRATOR_URL", ""))
    parser.add_argument("--postgres-host", default=os.environ.get("STUDIO_PREFLIGHT_POSTGRES_HOST", "127.0.0.1"))
    parser.add_argument("--postgres-port", type=int, default=int(os.environ.get("STUDIO_PREFLIGHT_POSTGRES_PORT", "5433")))
    parser.add_argument("--redis-host", default=os.environ.get("STUDIO_PREFLIGHT_REDIS_HOST", "127.0.0.1"))
    parser.add_argument("--redis-port", type=int, default=int(os.environ.get("STUDIO_PREFLIGHT_REDIS_PORT", "6379")))
    parser.add_argument("--minio-url", default=os.environ.get("STUDIO_PREFLIGHT_MINIO_URL", ""))
    parser.add_argument("--comfyui-url", default=os.environ.get("STUDIO_PREFLIGHT_COMFYUI_URL", ""))
    for name, help_text in (
        ("postgres", "要求 PostgreSQL TCP 可达"),
        ("redis", "要求 Redis TCP 可达"),
        ("minio", "要求 MinIO health/live 返回 2xx"),
        ("comfyui", "要求 ComfyUI system_stats 返回 2xx"),
        ("orchestrator", "要求 BullMQ 编排器 health 返回 status=ok"),
    ):
        parser.add_argument(f"--require-{name}", action="store_true", help=help_text)
        parser.add_argument(f"--skip-{name}", action="store_true", help=f"跳过 {name} 检查")
    parser.add_argument("--require-container-runtime", action="store_true", help="要求 Docker/Podman daemon 可用")
    parser.add_argument("--require-gpu", action="store_true", help="要求当前主机可通过 nvidia-smi 发现 NVIDIA GPU")
    parser.add_argument("--require-provider-config", action="store_true", help="要求已启用的真实 Provider 具备 URL、模型、凭据引用或 workflow 配置")
    parser.add_argument("--require-production-config", action="store_true", help="要求生产配置拒绝默认凭据、开发存储和未认证模式")
    parser.add_argument(
        "--probe-data-services",
        action="store_true",
        help="使用环境变量执行 PostgreSQL SELECT 1、Redis PING 和 S3/MinIO HeadBucket 只读认证探针",
    )
    return parser.parse_args(argv)


def print_human(report: Mapping[str, Any]) -> None:
    print(f"runtime-preflight: {report['status']} ({report['mode']})")
    for name, result in report["checks"].items():
        if result["ok"]:
            marker = "OK"
        elif result["required"]:
            marker = "FAIL"
        elif result.get("state") == "not-configured":
            marker = "N/A"
        else:
            marker = "WARN"
        detail = result.get("error") or result.get("state") or "checked"
        print(f"{marker:4} {name}: {detail}")
    if report["required_failures"]:
        print("required failures: " + ", ".join(report["required_failures"]))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print_human(report)
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
