"""FastAPI 入口，API 路径对齐参考平台并扩展小说改编能力。"""

from __future__ import annotations

import os
import secrets
import logging
import hashlib
import hmac
import time
import uuid
import json
import re
import tempfile
import urllib.error
import urllib.request
from email.parser import BytesParser
from email.policy import default as email_default
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .service import DEFAULT_USER_ID, ServiceError, StudioService
from .billing import BillingProviderError, StripeCheckoutAdapter
from .store import PostgresStore, StudioStore
from .documents import DocumentExtractionError, extract_document, media_type_for_filename
from .observability import configure_otel
from .ratelimit import RateLimiter
from .providers import configured_speech_voices
from scripts.production_config_check import check_production_config


ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = Path(os.environ.get("STUDIO_RUNTIME_DIR", ROOT / "runtime"))
logger = logging.getLogger("studio_api")
request_counts: dict[tuple[str, int], int] = {}
request_duration_ms: dict[str, float] = {}
STUDIO_ENV = os.environ.get("STUDIO_ENV", "development").lower()
AUTH_COOKIE_NAME = "studio_session"
CSRF_COOKIE_NAME = "studio_csrf"
AUTH_COOKIE_MAX_AGE = 30 * 24 * 60 * 60


def auth_cookie_secure() -> bool:
    configured = os.environ.get("STUDIO_AUTH_COOKIE_SECURE")
    if configured is not None:
        return configured.lower() in {"1", "true", "yes", "on"}
    return STUDIO_ENV == "production"


def set_auth_cookies(response: Response, session_token: str) -> str:
    csrf_token = secrets.token_urlsafe(32)
    secure = auth_cookie_secure()
    response.set_cookie(AUTH_COOKIE_NAME, session_token, max_age=AUTH_COOKIE_MAX_AGE, httponly=True, secure=secure, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE_NAME, csrf_token, max_age=AUTH_COOKIE_MAX_AGE, httponly=False, secure=secure, samesite="lax", path="/")
    return csrf_token


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


def require_cookie_csrf(request: Request) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    header_token = request.headers.get("x-csrf-token", "")
    if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="csrf validation required")


async def read_request_body_limited(request: Request, limit: int) -> bytes:
    """按流读取请求体，避免 chunked 请求在超限前一次性进入内存。"""

    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise HTTPException(status_code=413, detail="request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


try:
    MAX_REQUEST_BODY_BYTES = max(1024, int(os.environ.get("STUDIO_MAX_BODY_BYTES", str(16 * 1024 * 1024))))
except ValueError:
    MAX_REQUEST_BODY_BYTES = 16 * 1024 * 1024
try:
    MAX_ARCHIVE_BODY_BYTES = max(
        MAX_REQUEST_BODY_BYTES,
        min(int(os.environ.get("STUDIO_MAX_ARCHIVE_BYTES", str(256 * 1024 * 1024))), 2 * 1024 * 1024 * 1024),
    )
except ValueError:
    MAX_ARCHIVE_BODY_BYTES = max(MAX_REQUEST_BODY_BYTES, 256 * 1024 * 1024)
ORCHESTRATOR_HEALTH_RESPONSE_MAX_BYTES = 64 * 1024
if os.environ.get("STUDIO_DATABASE_URL") or os.environ.get("STUDIO_STORE") == "postgres":
    database_url = os.environ.get("STUDIO_DATABASE_URL")
    if not database_url:
        raise RuntimeError("STUDIO_DATABASE_URL is required when STUDIO_STORE=postgres")
    service = StudioService(PostgresStore(database_url, RUNTIME_DIR), RUNTIME_DIR / "assets")
else:
    service = StudioService(StudioStore(RUNTIME_DIR / "studio.sqlite3"), RUNTIME_DIR / "assets")
app = FastAPI(
    title="AI 漫剧创作工作台 API",
    version="0.1.0",
    docs_url=None if STUDIO_ENV == "production" else "/docs",
    redoc_url=None if STUDIO_ENV == "production" else "/redoc",
    openapi_url=None if STUDIO_ENV == "production" else "/openapi.json",
)
observability_status, shutdown_otel = configure_otel(app)
cors_origins = [origin.strip() for origin in os.environ.get("STUDIO_CORS_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=True)
configured_hosts = [host.strip() for host in os.environ.get("STUDIO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]
if STUDIO_ENV == "production" or os.environ.get("STUDIO_ALLOWED_HOSTS"):
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=configured_hosts)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """记录安全的请求元数据，不记录 Authorization/body/secret。"""

    def __init__(self, app: Any, rate_limiter: RateLimiter | None = None) -> None:
        super().__init__(app)
        try:
            limit = max(0, int(os.environ.get("STUDIO_RATE_LIMIT_PER_MINUTE", "0")))
        except ValueError:
            limit = 0
        fail_closed = os.environ.get("STUDIO_RATE_LIMIT_FAIL_CLOSED", "false").lower() in {"1", "true", "yes", "on"}
        self.rate_limiter = rate_limiter or RateLimiter(
            limit=limit,
            redis_url=os.environ.get("STUDIO_RATE_LIMIT_REDIS_URL"),
            fail_closed=fail_closed,
        )

    @staticmethod
    def _secure_headers(response: Any, request_id: str) -> None:
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        request_id = request.headers.get("x-request-id", "")[:96] or uuid.uuid4().hex
        request.state.request_id = request_id
        client_key = request.client.host if request.client else "unknown"
        if request.url.path not in {"/api/health", "/api/ready", "/api/metrics"}:
            if not await self.rate_limiter.allow(client_key):
                request_counts[(request.method, 429)] = request_counts.get((request.method, 429), 0) + 1
                response = JSONResponse({"detail": "rate limit exceeded", "request_id": request_id}, status_code=429, headers={"Retry-After": "60", "X-Request-ID": request_id})
                self._secure_headers(response, request_id)
                return response
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                body_limit = MAX_ARCHIVE_BODY_BYTES if request.url.path == "/api/projects/import-archive" else MAX_REQUEST_BODY_BYTES
                if int(content_length) > body_limit:
                    request_counts[(request.method, 413)] = request_counts.get((request.method, 413), 0) + 1
                    response = JSONResponse({"detail": "request body too large", "request_id": request_id}, status_code=413)
                    self._secure_headers(response, request_id)
                    return response
            except ValueError:
                pass
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_failed request_id=%s method=%s path=%s", request_id, request.method, request.url.path)
            raise
        self._secure_headers(response, request_id)
        request_counts[(request.method, response.status_code)] = request_counts.get((request.method, response.status_code), 0) + 1
        request_duration_ms[request.method] = request_duration_ms.get(request.method, 0.0) + (time.perf_counter() - started) * 1000
        logger.info("request_complete request_id=%s method=%s path=%s status=%s duration_ms=%.1f", request_id, request.method, request.url.path, response.status_code, (time.perf_counter() - started) * 1000)
        return response


try:
    configured_rate_limit = max(0, int(os.environ.get("STUDIO_RATE_LIMIT_PER_MINUTE", "0") or 0))
except ValueError:
    configured_rate_limit = 0
rate_limiter = RateLimiter(
    limit=configured_rate_limit,
    redis_url=os.environ.get("STUDIO_RATE_LIMIT_REDIS_URL"),
    fail_closed=os.environ.get("STUDIO_RATE_LIMIT_FAIL_CLOSED", "false").lower() in {"1", "true", "yes", "on"},
)
app.add_middleware(RequestContextMiddleware, rate_limiter=rate_limiter)


@app.on_event("shutdown")
async def close_runtime_resources() -> None:
    await rate_limiter.close()
    shutdown_otel()
@app.get("/assets/{asset_name:path}")
def serve_asset(asset_name: str, request: Request) -> FileResponse:
    """按项目归属返回本地媒体，避免 StaticFiles 绕过用户隔离。"""
    path = call(service.local_asset_file, asset_name, current_user(request))
    return FileResponse(path)


class ProjectCreate(BaseModel):
    title: str
    story: str = ""
    style: str = "国漫写实"
    episodeLength: str = Field(default="1min")


class AuthCredentials(BaseModel):
    email: str
    password: str


class SourceImport(BaseModel):
    filename: str = "novel.txt"
    text: str
    mode: str = "faithful"
    copyrightAcknowledged: bool = False


class RewriteInput(BaseModel):
    mode: str = "originalized"


class AdaptationUnitPatch(BaseModel):
    adapted_text: str | None = None
    status: str | None = None


class AdaptationReviewBatchInput(BaseModel):
    unit_ids: list[str] | None = None
    status: str = "approved"


class CharacterInput(BaseModel):
    name: str
    role: str = "supporting"
    description: str = ""
    visual_lock: dict[str, Any] = Field(default_factory=dict)


class CharactersInput(BaseModel):
    characters: list[CharacterInput] = Field(default_factory=list)


class CharacterReferencesInput(BaseModel):
    character_ids: list[str] | None = None


class ImageAssetsInput(BaseModel):
    shot_ids: list[str] | None = None


class VideoAssetsInput(BaseModel):
    asset_ids: list[str] | None = None


class TextVideoInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=12_000)


class LongVideoInput(BaseModel):
    """Director 已拆好的长视频分段计划；细节门禁由 service 统一执行。"""

    mode: str = "director-motion-context"
    width: int = 640
    height: int = 384
    fps: float = 24
    steps: int = 8
    seed: int = 0
    sampler: str = "euler"
    context_length: int = 22
    audio_context_length: int = 24
    source_asset_id: str | None = None
    reference_asset_ids: list[str] | None = None
    segments: list[dict[str, Any]] = Field(default_factory=list)


class CompositionsInput(BaseModel):
    episode_ids: list[str] | None = None


class AssetReviewsInput(BaseModel):
    asset_ids: list[str] | None = None
    prompt: str = ""
    audit_type: str = "scene"


class CharacterPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    description: str | None = None
    visual_lock: dict[str, Any] | None = None


class StoryEntityPatch(BaseModel):
    kind: str | None = None
    name: str | None = None
    description: str | None = None
    attributes: dict[str, Any] | None = None
    source_segment_ids: list[str] | None = None
    status: str | None = None


class StoryRelationshipPatch(BaseModel):
    relation: str | None = None
    description: str | None = None
    status: str | None = None


class EpisodePatch(BaseModel):
    title: str | None = None
    summary: str | None = None
    conflict: str | None = None
    hook: str | None = None
    target_duration_seconds: int | None = None


class NarrativeUnitInput(BaseModel):
    goal: str = ""
    enter_state: str = ""
    exit_state: str = ""
    target_duration_seconds: int = Field(default=0, ge=0, le=900)
    scene_ids: list[str] = Field(default_factory=list)
    shot_ids: list[str] = Field(default_factory=list)


class NarrativeUnitPatch(BaseModel):
    goal: str | None = None
    enter_state: str | None = None
    exit_state: str | None = None
    target_duration_seconds: int | None = Field(default=None, ge=0, le=900)
    scene_ids: list[str] | None = None
    shot_ids: list[str] | None = None


class SceneInput(BaseModel):
    unit_id: str | None = None
    location_id: str | None = None
    name: str = ""
    time_of_day: str = ""
    weather: str = ""
    summary: str = ""
    shot_ids: list[str] = Field(default_factory=list)


class ScenePatch(BaseModel):
    unit_id: str | None = None
    location_id: str | None = None
    name: str | None = None
    time_of_day: str | None = None
    weather: str | None = None
    summary: str | None = None
    shot_ids: list[str] | None = None


class ShotPatch(BaseModel):
    scene: str | None = None
    emotion: str | None = None
    duration_seconds: float | None = None
    description: str | None = None
    adaptation_unit_ids: list[str] | None = None


class ImagePromptPatch(BaseModel):
    prompt: str | None = None
    negative_prompt: str | None = None


class AssetPatch(BaseModel):
    selected: bool | None = None
    consistency_confirmed: bool | None = None
    url: str | None = None


class AssetReviewInput(BaseModel):
    prompt: str = ""
    audit_type: str = "scene"


class ManualAssetReviewInput(BaseModel):
    status: str
    issues: list[str] = Field(default_factory=list)


class AssetQCInput(BaseModel):
    ffprobe: dict[str, Any] = Field(default_factory=dict)
    decoded_fully: bool | None = None
    frame_luma_sequence: list[float] | None = None
    audio_rms_sequence: list[float] | None = None
    duration_contract_sec: float | None = None
    fps_contract: float | None = None
    resolution_contract: list[int] | None = None
    audio_duration_sec: float | None = None
    sha256: str


class CharacterReferenceAttachInput(BaseModel):
    character_id: str | None = None
    reference_id: str | None = None


class CharacterReferenceUploadInput(BaseModel):
    view: str
    filename: str = "reference.png"
    data_base64: str


class CompositionSettingsInput(BaseModel):
    audio_tracks: list[dict[str, Any]] = Field(default_factory=list)
    subtitles: list[dict[str, Any]] = Field(default_factory=list)
    narration_text: str | None = None


class AudioImportInput(BaseModel):
    filename: str = "audio.mp3"
    data_base64: str


class NarrationInput(BaseModel):
    text: str | None = None
    voice: str = "cedar"
    speed: float = 1.0
    instructions: str = ""
    attach_to_timeline: bool = True
    auto_subtitles: bool = True


class TopUp(BaseModel):
    amount: int
    reason: str = "local development top-up"


class BillingOrderInput(BaseModel):
    package_code: str = Field(..., min_length=1, max_length=64)


def call(function: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def verify_billing_signature(raw_body: bytes, timestamp: str | None, signature: str | None) -> None:
    """验证 payment adapter 发来的短时 HMAC；不把 webhook 当成 cookie 会话。"""

    secret = os.environ.get("STUDIO_BILLING_WEBHOOK_SECRET", "").strip()
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="billing webhook secret is not configured")
    try:
        signed_at = int(str(timestamp or "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="billing webhook timestamp is invalid") from exc
    if abs(time.time() - signed_at) > 300:
        raise HTTPException(status_code=403, detail="billing webhook timestamp has expired")
    provided = str(signature or "").strip()
    if provided.startswith("sha256="):
        provided = provided.removeprefix("sha256=")
    expected = hmac.new(secret.encode("utf-8"), f"{signed_at}.".encode("ascii") + raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="billing webhook signature is invalid")


def request_auth_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    bearer_token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else None
    return bearer_token or request.cookies.get(AUTH_COOKIE_NAME)


def current_user(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    bearer_token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else None
    cookie_token = request.cookies.get(AUTH_COOKIE_NAME)
    token = bearer_token or cookie_token
    if cookie_token and not bearer_token and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        require_cookie_csrf(request)
    if not token and os.environ.get("STUDIO_REQUIRE_AUTH", "false").lower() in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=401, detail="authentication required")
    return call(service.resolve_user, token)


def generation_runs_now(queued: bool | None) -> bool:
    """本地默认同步；BullMQ 部署默认异步，避免 API 进程执行长 GPU 任务。"""
    if queued is not None:
        return not queued
    return os.environ.get("STUDIO_QUEUE_BACKEND", "local").lower() != "bullmq"


def external_queue_ready() -> bool:
    """BullMQ 模式下确认编排器和 Redis 健康，避免 API 假 ready。"""
    if os.environ.get("STUDIO_QUEUE_BACKEND", "local").lower() != "bullmq":
        return True
    endpoint = os.environ.get("STUDIO_ORCHESTRATOR_URL", "http://127.0.0.1:8790").rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(endpoint, timeout=2) as response:
            if response.status != 200:
                return False
            raw = response.read(ORCHESTRATOR_HEALTH_RESPONSE_MAX_BYTES + 1)
            if not isinstance(raw, (bytes, bytearray)) or len(raw) > ORCHESTRATOR_HEALTH_RESPONSE_MAX_BYTES:
                return False
            payload = json.loads(raw)
            return payload.get("status") == "ok" and payload.get("redis") == "ok"
    except (urllib.error.URLError, TimeoutError, OSError, TypeError, ValueError, AttributeError):
        return False


def asset_storage_ready() -> bool:
    checker = getattr(service.storage, "ready", None)
    return bool(checker()) if callable(checker) else True


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "ai-manhua-studio",
        "store": "postgres" if isinstance(service.store, PostgresStore) else "sqlite",
        "queue_backend": os.environ.get("STUDIO_QUEUE_BACKEND", "local"),
        "storage": service.storage.mode,
        "providers": {
            "text": service.providers.text.name,
            "image": service.providers.image.name,
            "video": service.providers.video.name,
            "vision": service.providers.vision.name,
            "speech": service.providers.speech.name,
        },
        "speech_voices": list(configured_speech_voices()),
        "billing": {"provider": service.billing_packages()["provider"]},
        "observability": observability_status,
        "rate_limit": {"enabled": bool(rate_limiter.limit), "backend": rate_limiter.backend},
        "composition": service.composition_engine_status(),
    }


def production_config_ready() -> None:
    """让直接启动的 production API 也遵守 Compose config-check 合同。"""

    if STUDIO_ENV != "production":
        return
    report = check_production_config(os.environ, force=True, required=True, check_workflow_files=True)
    if not report["ok"]:
        raise HTTPException(status_code=503, detail="production configuration gate failed")


@app.get("/api/ready")
def ready() -> dict[str, Any]:
    production_config_ready()
    try:
        service.store.one("SELECT 1")
    except Exception as exc:
        logger.warning("readiness_failed store=%s error_type=%s", type(service.store).__name__, type(exc).__name__)
        raise HTTPException(status_code=503, detail="store is not ready") from exc
    if not external_queue_ready():
        raise HTTPException(status_code=503, detail="external queue is not ready")
    if not asset_storage_ready():
        raise HTTPException(status_code=503, detail="asset storage is not ready")
    if not service.composition_engine_status()["ready"]:
        raise HTTPException(status_code=503, detail="composition engine is not ready")
    return {
        "status": "ready",
        "store": "postgres" if isinstance(service.store, PostgresStore) else "sqlite",
        "queue_backend": os.environ.get("STUDIO_QUEUE_BACKEND", "local"),
        "composition": service.composition_engine_status(),
    }


@app.get("/api/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    """输出无用户数据的 Prometheus 文本指标，供单实例/容器探针抓取。"""
    lines = [
        "# HELP studio_http_requests_total Total HTTP requests by method and status.",
        "# TYPE studio_http_requests_total counter",
    ]
    for (method, status_code), count in sorted(request_counts.items()):
        lines.append(f'studio_http_requests_total{{method="{method}",status="{status_code}"}} {count}')
    lines.extend([
        "# HELP studio_http_request_duration_ms_sum Sum of request durations in milliseconds by method.",
        "# TYPE studio_http_request_duration_ms_sum counter",
    ])
    for method, duration in sorted(request_duration_ms.items()):
        lines.append(f'studio_http_request_duration_ms_sum{{method="{method}"}} {duration:.3f}')
    lines.append('studio_build_info{version="0.1.0"} 1')
    return "\n".join(lines) + "\n"


@app.post("/api/auth/register")
def auth_register(payload: AuthCredentials, request: Request, response: Response) -> dict[str, Any]:
    result = call(service.register_user, payload.email, payload.password, request.headers.get("user-agent", ""))
    csrf_token = set_auth_cookies(response, result["token"])
    return {**result, "csrf_token": csrf_token}


@app.post("/api/auth/login")
def auth_login(payload: AuthCredentials, request: Request, response: Response) -> dict[str, Any]:
    result = call(service.login_user, payload.email, payload.password, request.headers.get("user-agent", ""))
    csrf_token = set_auth_cookies(response, result["token"])
    return {**result, "csrf_token": csrf_token}


@app.get("/api/auth/csrf")
def auth_csrf(request: Request, response: Response) -> dict[str, Any]:
    """为 HttpOnly cookie session 提供非敏感的双提交 CSRF token。"""
    session_token = request.cookies.get(AUTH_COOKIE_NAME)
    if not session_token:
        return {"csrf_token": None}
    call(service.resolve_user, session_token)
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(CSRF_COOKIE_NAME, csrf_token, max_age=AUTH_COOKIE_MAX_AGE, httponly=False, secure=auth_cookie_secure(), samesite="lax", path="/")
    return {"csrf_token": csrf_token}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, bool]:
    authorization = request.headers.get("authorization", "")
    bearer_token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else None
    cookie_token = request.cookies.get(AUTH_COOKIE_NAME)
    if cookie_token and not bearer_token:
        require_cookie_csrf(request)
    token = bearer_token or cookie_token
    service.logout(token)
    clear_auth_cookies(response)
    return {"ok": True}


@app.post("/api/auth/logout-all")
def auth_logout_all(request: Request, response: Response) -> dict[str, Any]:
    user_id = current_user(request)
    revoked = service.logout_all(user_id, request_auth_token(request))
    clear_auth_cookies(response)
    return {"ok": True, "revoked_sessions": revoked}


@app.get("/api/auth/sessions")
def auth_sessions(request: Request) -> list[dict[str, Any]]:
    return call(service.list_sessions, current_user(request), request_auth_token(request))


@app.delete("/api/auth/sessions/{session_id}")
def auth_revoke_session(session_id: str, request: Request) -> dict[str, Any]:
    return call(service.revoke_session, session_id, current_user(request), request_auth_token(request))


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    return call(service.user_profile, current_user(request))


@app.get("/api/projects")
def projects(request: Request) -> list[dict[str, Any]]:
    return service.list_projects(current_user(request))


@app.post("/api/projects")
def create_project(
    payload: ProjectCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    return call(
        service.create_project,
        payload.title,
        payload.story,
        payload.style,
        payload.episodeLength,
        current_user(request),
        idempotency_key,
    )


@app.post("/api/projects/import")
def import_project(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return call(service.import_project_bundle, payload, current_user(request))


@app.post("/api/projects/import-archive")
async def import_project_archive(request: Request) -> dict[str, Any]:
    """接收原始 ZIP，先落盘再由服务层做完整校验和安全挂载。"""
    user_id = current_user(request)
    content_type = request.headers.get("content-type", "").lower()
    if not (content_type.startswith("application/zip") or content_type.startswith("application/x-zip-compressed")):
        raise HTTPException(status_code=415, detail="project archive requires application/zip")
    import_dir = service.asset_dir.parent / "imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    archive_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="project-import-", suffix=".zip", dir=import_dir, delete=False) as temporary:
            archive_path = Path(temporary.name)
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > MAX_ARCHIVE_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="project archive too large")
                temporary.write(chunk)
        return call(service.import_project_archive, archive_path, user_id)
    finally:
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)


@app.get("/api/projects/{project_id}/readiness")
def project_readiness(project_id: str, request: Request) -> dict[str, Any]:
    return call(service.project_readiness, project_id, current_user(request))


@app.get("/api/projects/{project_id}")
def project(project_id: str, request: Request) -> dict[str, Any]:
    return call(service.get_project, project_id, current_user(request))


@app.patch("/api/projects/{project_id}")
def patch_project(project_id: str, request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return call(service.update_project, project_id, payload, current_user(request))


@app.post("/api/projects/{project_id}/source")
def import_source(
    project_id: str,
    payload: SourceImport,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if len(payload.text.encode("utf-8")) > MAX_REQUEST_BODY_BYTES:
        raise HTTPException(status_code=413, detail="source text too large")
    return call(
        service.import_source,
        project_id,
        payload.filename,
        payload.text,
        payload.mode,
        payload.copyrightAcknowledged,
        current_user(request),
        media_type_for_filename(payload.filename),
        idempotency_key,
    )


@app.post("/api/projects/{project_id}/source-file")
async def import_source_file(
    project_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(status_code=400, detail="source-file requires multipart/form-data")
    raw = await read_request_body_limited(request, MAX_REQUEST_BODY_BYTES)
    envelope = (f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw
    message = BytesParser(policy=email_default).parsebytes(envelope)
    filename = "source.txt"
    payload = b""
    fields: dict[str, str] = {}
    for part in message.iter_parts():
        disposition = dict(part.get_params(header="content-disposition", failobj=[]))
        name = disposition.get("name")
        value = part.get_payload(decode=True) or b""
        if name == "file":
            filename = disposition.get("filename") or filename
            payload = value
        elif name:
            fields[name] = value.decode("utf-8", errors="replace")
    if not payload:
        raise HTTPException(status_code=400, detail="source file is empty or missing")
    try:
        extracted = extract_document(filename, payload)
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    acknowledged = fields.get("copyrightAcknowledged", "false").lower() == "true"
    return call(
        service.import_source,
        project_id,
        filename,
        extracted.text,
        fields.get("mode", "faithful"),
        acknowledged,
        current_user(request),
        extracted.media_type,
        idempotency_key,
    )


@app.get("/api/projects/{project_id}/adaptation-units")
def adaptation_units(project_id: str, request: Request) -> list[dict[str, Any]]:
    return call(service.list_adaptation_units, project_id, current_user(request))


@app.post("/api/projects/{project_id}/rewrite")
def rewrite(
    project_id: str,
    payload: RewriteInput,
    request: Request,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> list[dict[str, Any]] | dict[str, Any]:
    return call(service.request_rewrite, project_id, payload.mode, current_user(request), generation_runs_now(queued), idempotency_key)


@app.patch("/api/adaptation-units/{unit_id}")
def patch_adaptation_unit(unit_id: str, payload: AdaptationUnitPatch, request: Request) -> dict[str, Any]:
    return call(service.update_adaptation_unit, unit_id, payload.model_dump(exclude_none=True), current_user(request))


@app.post("/api/projects/{project_id}/adaptation-units/review")
def review_adaptation_units(project_id: str, payload: AdaptationReviewBatchInput, request: Request) -> dict[str, Any]:
    return call(
        service.review_adaptation_units,
        project_id,
        payload.unit_ids,
        payload.status,
        current_user(request),
    )


@app.post("/api/projects/{project_id}/characters")
def characters(
    project_id: str,
    request: Request,
    payload: CharactersInput | None = None,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> list[dict[str, Any]] | dict[str, Any]:
    items = [item.model_dump() for item in (payload.characters if payload else [])]
    user_id = current_user(request)
    # 带有明确角色卡的调用是人工数据写入，不触发 Provider；只有空 payload
    # 才走可恢复的结构阶段 Job。
    if items:
        return call(service.create_characters, project_id, items, user_id)
    return call(
        service.request_structure_stage,
        "characters",
        project_id,
        user_id,
        generation_runs_now(queued),
        idempotency_key,
    )


@app.post("/api/projects/{project_id}/structure")
def project_structure(
    project_id: str,
    request: Request,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    return call(service.request_project_structure, project_id, current_user(request), generation_runs_now(queued), idempotency_key)


@app.get("/api/projects/{project_id}/story-bible")
def story_bible(project_id: str, request: Request) -> dict[str, Any]:
    return call(service.story_bible, project_id, current_user(request))


@app.post("/api/projects/{project_id}/story-bible")
def generate_story_bible(
    project_id: str,
    request: Request,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    return call(service.request_story_bible, project_id, current_user(request), generation_runs_now(queued), idempotency_key)


@app.patch("/api/story-entities/{entity_id}")
def patch_story_entity(entity_id: str, payload: StoryEntityPatch, request: Request) -> dict[str, Any]:
    return call(service.update_story_entity, entity_id, payload.model_dump(exclude_none=True), current_user(request))


@app.patch("/api/story-relationships/{relationship_id}")
def patch_story_relationship(relationship_id: str, payload: StoryRelationshipPatch, request: Request) -> dict[str, Any]:
    return call(service.update_story_relationship, relationship_id, payload.model_dump(exclude_none=True), current_user(request))


@app.patch("/api/characters/{character_id}")
def patch_character(character_id: str, payload: CharacterPatch, request: Request) -> dict[str, Any]:
    return call(service.update_character, character_id, payload.model_dump(exclude_none=True), current_user(request))


@app.post("/api/characters/{character_id}/reference-image")
def character_reference(
    character_id: str,
    request: Request,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    return call(service.create_character_reference, character_id, current_user(request), generation_runs_now(queued), idempotency_key)


@app.post("/api/characters/{character_id}/reference-upload")
def upload_character_reference(
    character_id: str,
    payload: CharacterReferenceUploadInput,
    request: Request,
) -> dict[str, Any]:
    return call(
        service.upload_character_reference,
        character_id,
        payload.view,
        payload.filename,
        payload.data_base64,
        current_user(request),
    )


@app.post("/api/projects/{project_id}/character-references")
def character_references_batch(
    project_id: str,
    request: Request,
    payload: CharacterReferencesInput | None = None,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    character_ids = payload.character_ids if payload else None
    return call(
        service.create_character_references,
        project_id,
        character_ids,
        current_user(request),
        generation_runs_now(queued),
        idempotency_key,
    )


@app.post("/api/projects/{project_id}/image-assets")
def image_assets_batch(
    project_id: str,
    request: Request,
    payload: ImageAssetsInput | None = None,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    shot_ids = payload.shot_ids if payload else None
    return call(
        service.create_image_assets,
        project_id,
        shot_ids,
        current_user(request),
        generation_runs_now(queued),
        idempotency_key,
    )


@app.post("/api/projects/{project_id}/video-assets")
def video_assets_batch(
    project_id: str,
    request: Request,
    payload: VideoAssetsInput | None = None,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    asset_ids = payload.asset_ids if payload else None
    return call(
        service.create_video_assets,
        project_id,
        asset_ids,
        current_user(request),
        generation_runs_now(queued),
        idempotency_key,
    )


@app.post("/api/projects/{project_id}/compositions")
def compositions_batch(
    project_id: str,
    request: Request,
    payload: CompositionsInput | None = None,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    episode_ids = payload.episode_ids if payload else None
    return call(
        service.create_compositions,
        project_id,
        episode_ids,
        current_user(request),
        generation_runs_now(queued),
        idempotency_key,
    )


@app.post("/api/projects/{project_id}/asset-reviews")
def asset_reviews_batch(project_id: str, request: Request, payload: AssetReviewsInput | None = None, queued: bool | None = None) -> dict[str, Any]:
    asset_ids = payload.asset_ids if payload else None
    prompt = payload.prompt if payload else ""
    audit_type = payload.audit_type if payload else "scene"
    user_id = current_user(request)
    idempotency_key = request.headers.get("Idempotency-Key")
    queue_default_async = os.environ.get("STUDIO_QUEUE_BACKEND", "local").lower() == "bullmq"
    if queued is None and not idempotency_key and not queue_default_async:
        return call(service.review_assets, project_id, asset_ids, prompt, audit_type, user_id)
    return call(service.review_assets, project_id, asset_ids, prompt, audit_type, user_id, generation_runs_now(queued), idempotency_key)


@app.post("/api/projects/{project_id}/outline")
def outline(
    project_id: str,
    request: Request,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> list[dict[str, Any]] | dict[str, Any]:
    return call(
        service.request_structure_stage,
        "outline",
        project_id,
        current_user(request),
        generation_runs_now(queued),
        idempotency_key,
    )


@app.get("/api/episodes/{episode_id}")
def episode(episode_id: str, request: Request) -> dict[str, Any]:
    row = service.store.one("SELECT e.* FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?", (episode_id, current_user(request)))
    if not row:
        raise HTTPException(status_code=404, detail="episode not found")
    return service.episode_detail(dict(row))


@app.patch("/api/episodes/{episode_id}")
def patch_episode(episode_id: str, payload: EpisodePatch, request: Request) -> dict[str, Any]:
    return call(service.update_episode, episode_id, payload.model_dump(exclude_none=True), current_user(request))


@app.get("/api/episodes/{episode_id}/narrative-units")
def narrative_units(episode_id: str, request: Request) -> list[dict[str, Any]]:
    return call(service.list_narrative_units, episode_id, current_user(request))


@app.post("/api/episodes/{episode_id}/narrative-units")
def create_narrative_unit(episode_id: str, payload: NarrativeUnitInput, request: Request) -> dict[str, Any]:
    return call(service.create_narrative_unit, episode_id, payload.model_dump(), current_user(request))


@app.get("/api/narrative-units/{unit_id}")
def narrative_unit(unit_id: str, request: Request) -> dict[str, Any]:
    return call(service.get_narrative_unit, unit_id, current_user(request))


@app.patch("/api/narrative-units/{unit_id}")
def patch_narrative_unit(unit_id: str, payload: NarrativeUnitPatch, request: Request) -> dict[str, Any]:
    return call(service.update_narrative_unit, unit_id, payload.model_dump(exclude_none=True), current_user(request))


@app.get("/api/episodes/{episode_id}/scenes")
def scenes(episode_id: str, request: Request) -> list[dict[str, Any]]:
    return call(service.list_scenes, episode_id, current_user(request))


@app.post("/api/episodes/{episode_id}/scenes")
def create_scene(episode_id: str, payload: SceneInput, request: Request) -> dict[str, Any]:
    return call(service.create_scene, episode_id, payload.model_dump(), current_user(request))


@app.get("/api/scenes/{scene_id}")
def scene(scene_id: str, request: Request) -> dict[str, Any]:
    return call(service.get_scene, scene_id, current_user(request))


@app.patch("/api/scenes/{scene_id}")
def patch_scene(scene_id: str, payload: ScenePatch, request: Request) -> dict[str, Any]:
    return call(service.update_scene, scene_id, payload.model_dump(exclude_none=True), current_user(request))


@app.post("/api/episodes/{episode_id}/shots")
def shots(
    episode_id: str,
    request: Request,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> list[dict[str, Any]] | dict[str, Any]:
    return call(
        service.request_structure_stage,
        "shots",
        episode_id,
        current_user(request),
        generation_runs_now(queued),
        idempotency_key,
    )


@app.get("/api/episodes/{episode_id}/composition-settings")
def composition_settings(episode_id: str, request: Request) -> dict[str, Any]:
    return call(service.composition_settings, episode_id, current_user(request))


@app.patch("/api/episodes/{episode_id}/composition-settings")
def patch_composition_settings(episode_id: str, payload: CompositionSettingsInput, request: Request) -> dict[str, Any]:
    return call(service.update_composition_settings, episode_id, payload.model_dump(), current_user(request))


@app.post("/api/episodes/{episode_id}/audio")
def import_audio(episode_id: str, payload: AudioImportInput, request: Request) -> dict[str, Any]:
    return call(service.import_audio_asset, episode_id, payload.filename, payload.data_base64, current_user(request))


@app.post("/api/episodes/{episode_id}/narration")
def generate_narration(
    episode_id: str,
    payload: NarrationInput,
    request: Request,
    queued: bool | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if not isinstance(idempotency_key, str):
        idempotency_key = request.headers.get("Idempotency-Key")
    return call(
        service.request_narration,
        episode_id,
        payload.text,
        payload.voice,
        payload.speed,
        payload.instructions,
        payload.attach_to_timeline,
        current_user(request),
        generation_runs_now(queued),
        idempotency_key,
        auto_subtitles=payload.auto_subtitles,
    )


@app.post("/api/shots/{shot_id}/image-prompts")
def image_prompts(
    shot_id: str,
    request: Request,
    queued: bool | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    user_id = current_user(request)
    if not isinstance(idempotency_key, str):
        idempotency_key = request.headers.get("Idempotency-Key")
    queue_default_async = os.environ.get("STUDIO_QUEUE_BACKEND", "local").lower() == "bullmq"
    if queued is None and not idempotency_key and not queue_default_async:
        return call(service.create_prompts, shot_id, user_id)
    return call(service.request_prompt_generation, shot_id, user_id, generation_runs_now(queued), idempotency_key)


@app.patch("/api/shots/{shot_id}")
def patch_shot(shot_id: str, payload: ShotPatch, request: Request) -> dict[str, Any]:
    return call(service.update_shot, shot_id, payload.model_dump(exclude_none=True), current_user(request))


@app.patch("/api/image-prompts/{prompt_id}")
def patch_image_prompt(prompt_id: str, payload: ImagePromptPatch, request: Request) -> dict[str, Any]:
    return call(service.update_image_prompt, prompt_id, payload.model_dump(exclude_none=True), current_user(request))


@app.post("/api/shots/{shot_id}/image")
def shot_image(shot_id: str, request: Request, queued: bool | None = Query(default=None), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    return call(service.create_image_asset, shot_id, current_user(request), generation_runs_now(queued), idempotency_key)


@app.get("/api/assets/{asset_id}")
def asset(asset_id: str, request: Request) -> dict[str, Any]:
    return call(service.asset_with_job, asset_id, current_user(request))


@app.patch("/api/assets/{asset_id}")
def patch_asset(asset_id: str, payload: AssetPatch, request: Request) -> dict[str, Any]:
    return call(service.patch_asset, asset_id, payload.model_dump(exclude_none=True), current_user(request))


@app.post("/api/assets/{asset_id}/review")
def review_asset(asset_id: str, payload: AssetReviewInput, request: Request, queued: bool | None = None) -> dict[str, Any]:
    user_id = current_user(request)
    idempotency_key = request.headers.get("Idempotency-Key")
    queue_default_async = os.environ.get("STUDIO_QUEUE_BACKEND", "local").lower() == "bullmq"
    if queued is None and not idempotency_key and not queue_default_async:
        return call(service.review_asset, asset_id, payload.prompt, payload.audit_type, user_id)
    return call(service.request_asset_review, asset_id, payload.prompt, payload.audit_type, user_id, generation_runs_now(queued), idempotency_key)


@app.post("/api/assets/{asset_id}/review/decision")
def manual_review_asset(asset_id: str, payload: ManualAssetReviewInput, request: Request) -> dict[str, Any]:
    return call(
        service.manual_review_asset,
        asset_id,
        payload.status,
        payload.issues,
        current_user(request),
        request.headers.get("Idempotency-Key"),
    )


@app.post("/api/assets/{asset_id}/qc")
def asset_qc(asset_id: str, payload: AssetQCInput, request: Request) -> dict[str, Any]:
    return call(
        service.submit_asset_qc,
        asset_id,
        payload.model_dump(exclude_none=True),
        current_user(request),
        request.headers.get("Idempotency-Key"),
    )


@app.post("/api/assets/{asset_id}/character-reference")
def attach_character_reference(asset_id: str, payload: CharacterReferenceAttachInput, request: Request) -> dict[str, Any]:
    return call(service.attach_character_reference, asset_id, payload.character_id, payload.reference_id, current_user(request))


@app.post("/api/assets/{asset_id}/image")
def asset_image(
    asset_id: str,
    request: Request,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    user_id = current_user(request)
    asset_row = call(service.asset_with_job, asset_id, user_id)
    if asset_row["kind"] == "image" and asset_row["status"] == "ready":
        return asset_row
    if not asset_row.get("shot_id"):
        raise HTTPException(status_code=400, detail="asset is not attached to a shot")
    return call(service.create_image_asset, asset_row["shot_id"], user_id, generation_runs_now(queued), idempotency_key)


@app.post("/api/assets/{asset_id}/video")
def asset_video(asset_id: str, request: Request, queued: bool | None = Query(default=None), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    return call(service.create_video_asset, asset_id, current_user(request), generation_runs_now(queued), idempotency_key)


@app.post("/api/shots/{shot_id}/text-video")
def shot_text_video(
    shot_id: str,
    payload: TextVideoInput,
    request: Request,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """提交不依赖首帧的文生视频任务；工作流仍由 H3 Provider 配置决定。"""

    return call(
        service.create_text_video_asset,
        shot_id,
        payload.prompt,
        current_user(request),
        generation_runs_now(queued),
        idempotency_key,
    )


@app.post("/api/episodes/{episode_id}/compose")
def compose(
    episode_id: str,
    request: Request,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    return call(service.request_composition, episode_id, current_user(request), generation_runs_now(queued), idempotency_key)


@app.post("/api/episodes/{episode_id}/long-video")
def long_video(
    episode_id: str,
    payload: LongVideoInput,
    request: Request,
    queued: bool | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """提交 Director → Motion Context 的可恢复长视频任务。"""

    return call(
        service.request_long_video,
        episode_id,
        payload.model_dump(),
        current_user(request),
        generation_runs_now(queued),
        idempotency_key,
    )


@app.get("/api/episodes/{episode_id}/long-video")
def long_video_status(episode_id: str, request: Request) -> dict[str, Any]:
    return call(service.long_video_with_job, episode_id, current_user(request))


@app.post("/api/internal/jobs/{job_id}/run")
def run_internal_job(job_id: str, request: Request) -> dict[str, Any]:
    expected = os.environ.get("STUDIO_INTERNAL_TOKEN")
    provided = request.headers.get("x-studio-internal-token")
    if not expected or not provided or not secrets.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail="internal worker authorization required")
    return call(service.run_job, job_id)


@app.post("/api/internal/jobs/{job_id}/retry")
def retry_internal_job(job_id: str, request: Request) -> dict[str, Any]:
    expected = os.environ.get("STUDIO_INTERNAL_TOKEN")
    provided = request.headers.get("x-studio-internal-token")
    if not expected or not provided or not secrets.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail="internal worker authorization required")
    row = service.store.one("SELECT user_id FROM jobs WHERE id = ?", (job_id,))
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    return call(service.retry_job, job_id, row["user_id"], False)


@app.get("/api/projects/{project_id}/graph")
def graph(project_id: str, request: Request) -> dict[str, Any]:
    return call(service.graph, project_id, current_user(request))


@app.get("/api/projects/{project_id}/export")
def export_project(project_id: str, request: Request) -> dict[str, Any]:
    return call(service.export_project, project_id, current_user(request))


@app.get("/api/projects/{project_id}/archive")
def project_archive(project_id: str, request: Request) -> FileResponse:
    user_id = current_user(request)
    project = call(service.get_project, project_id, user_id)
    download_dir = service.asset_dir.parent / "downloads"
    archive_path = call(
        service.write_project_archive,
        project_id,
        download_dir / f"project-bundle-{uuid.uuid4().hex}.zip",
        user_id,
    )
    title = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff.]+", "_", str(project.get("title") or "project")).strip("._")[:120] or "project"
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"{title}-project-bundle.zip",
        background=BackgroundTask(lambda: archive_path.unlink(missing_ok=True)),
    )


@app.get("/api/jobs")
def jobs(request: Request, limit: int = Query(default=100, ge=1, le=200)) -> list[dict[str, Any]]:
    return service.jobs(current_user(request), limit)


@app.get("/api/jobs/{job_id}")
def job(job_id: str, request: Request) -> dict[str, Any]:
    row = service.store.one("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, current_user(request)))
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    return dict(row)


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str, request: Request, limit: int = Query(default=100, ge=1, le=200)) -> list[dict[str, Any]]:
    return call(service.job_events, job_id, current_user(request), limit)


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Request) -> dict[str, Any]:
    return call(service.retry_job, job_id, current_user(request))


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
    return call(service.cancel_job, job_id, current_user(request))


@app.get("/api/credits")
def credits(request: Request) -> dict[str, Any]:
    return service.credits(current_user(request))


@app.get("/api/credits/transactions")
def credit_transactions(request: Request, limit: int = Query(default=100, ge=1, le=200)) -> list[dict[str, Any]]:
    return service.credit_transactions(current_user(request), limit)


@app.post("/api/credits/top-up")
def top_up(payload: TopUp, request: Request) -> dict[str, Any]:
    if STUDIO_ENV == "production" and os.environ.get("STUDIO_ALLOW_LOCAL_TOP_UP", "false").lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=403, detail="local credit top-up is disabled in production")
    return call(service.top_up, current_user(request), payload.amount, payload.reason)


@app.get("/api/billing/packages")
def billing_packages(request: Request) -> dict[str, Any]:
    current_user(request)
    return service.billing_packages()


@app.get("/api/billing/orders")
def billing_orders(request: Request, limit: int = Query(default=50, ge=1, le=100)) -> list[dict[str, Any]]:
    return call(service.billing_orders, current_user(request), limit)


@app.post("/api/billing/orders")
def create_billing_order(
    payload: BillingOrderInput,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    return call(service.create_billing_order, payload.package_code, current_user(request), idempotency_key)


@app.post("/api/billing/webhook")
async def billing_webhook(
    request: Request,
    x_billing_timestamp: str | None = Header(default=None, alias="X-Billing-Timestamp"),
    x_billing_signature: str | None = Header(default=None, alias="X-Billing-Signature"),
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict[str, Any]:
    raw_body = await read_request_body_limited(request, MAX_REQUEST_BODY_BYTES)
    if service._billing_provider() == "stripe":
        try:
            event = StripeCheckoutAdapter.from_env().verify_webhook(raw_body, stripe_signature)
            payload = StripeCheckoutAdapter.normalize_event(event)
        except BillingProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        if payload is None:
            return {"status": "ignored", "event_id": str(event.get("id", ""))}
        return call(service.settle_billing_webhook, payload, "stripe")
    verify_billing_signature(raw_body, x_billing_timestamp, x_billing_signature)
    try:
        payload = json.loads(raw_body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="billing webhook body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="billing webhook body must be an object")
    return call(service.settle_billing_webhook, payload, "signed-webhook")


@app.get("/api/studio-settings")
def settings(request: Request) -> dict[str, Any]:
    return service.settings(current_user(request))


@app.patch("/api/studio-settings")
def patch_settings(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return call(service.update_settings, payload, current_user(request))
