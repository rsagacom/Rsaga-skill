"""平台业务服务：将领域合同落到可运行的本地 API。

默认 provider 是可审计的本地预览 provider：它生成 SVG/PPM/视频/playlist 预览，不
冒充真实 AI 结果。真实模型接入只需要替换 provider，不改变 API 和账本。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import inspect
import json
import math
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import uuid
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import Any

from studio_core.models import AdaptationMode, SourceDocument
from studio_core.novel import adapt_source
from studio_core.workflow import persisted_transition_allowed
from studio_core.long_video import LongVideoPlanError, normalize_long_video_plan
from studio_core.audio import AudioTrack, SubtitleCue, TrackKind, audio_gate
from studio_core.qc import MediaEvidence, run_auto_qc

from .documents import DocumentExtractionError, ensure_source_text, media_type_for_filename
from .billing import BillingProviderError, StripeCheckoutAdapter
from .store import StudioStore, json_loads
from .providers import (
    ProviderError,
    ProviderRegistry,
    combine_speech_assets,
    speech_voice_allowed,
    split_speech_text,
)
from .storage import storage_from_env


DEFAULT_USER_ID = "local-user"
DEFAULT_USER_EMAIL = "local@studio"
_UNSET = object()
COSTS = {
    "project": 10,
    "character": 0,
    "outline": 0,
    "shots": 0,
    "prompts": 0,
    "character-reference": 8,
    "image": 3,
    "video": 2,
    "long-video": 2,
    "narration": 1,
    "compose": 0,
}

MAX_NARRATION_TEXT_CHARS = 48_000
MAX_NARRATION_SEGMENTS = 16

# 这是平台侧的积分商品目录，不是任何支付厂商的价格承诺。真实上线时由
# 产品/财务确认价格，并通过 provider adapter 创建外部支付单；账本只信任
# 已签名回调中对应的本地订单。
DEFAULT_CREDIT_PACKAGES = (
    {"code": "starter", "label": "创作入门包", "credits": 1000, "amount_cents": 9900, "currency": "CNY"},
    {"code": "creator", "label": "创作者包", "credits": 5000, "amount_cents": 39900, "currency": "CNY"},
    {"code": "studio", "label": "工作室包", "credits": 20000, "amount_cents": 129900, "currency": "CNY"},
)


class ServiceError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _session_device_label(user_agent: str | None) -> str:
    """把 User-Agent 映射为短设备标签，不保存原始 UA、IP 或认证信息。"""

    normalized = " ".join(str(user_agent or "").split())
    if not normalized:
        return "未知设备"
    if "Edg/" in normalized:
        browser = "Edge"
    elif "CriOS/" in normalized or "Chrome/" in normalized:
        browser = "Chrome"
    elif "FxiOS/" in normalized or "Firefox/" in normalized:
        browser = "Firefox"
    elif "Safari/" in normalized:
        browser = "Safari"
    else:
        browser = "浏览器"
    if "iPhone" in normalized:
        platform = "iPhone"
    elif "iPad" in normalized:
        platform = "iPad"
    elif "Android" in normalized:
        platform = "Android"
    elif "Windows" in normalized:
        platform = "Windows"
    elif "Mac OS X" in normalized or "Macintosh" in normalized:
        platform = "macOS"
    elif "Linux" in normalized:
        platform = "Linux"
    else:
        platform = "未知系统"
    return f"{browser} · {platform}"


def bool_value(value: Any) -> bool:
    return bool(int(value)) if isinstance(value, (int, str)) and str(value).isdigit() else bool(value)


def episode_length_seconds(value: Any) -> int:
    normalized = str(value or "1min").strip().lower()
    presets = {"30s": 30, "1min": 60, "3min": 180, "5min": 300}
    if normalized in presets:
        return presets[normalized]
    match = re.fullmatch(r"(\d+)\s*(?:s|sec|secs|second|seconds)", normalized)
    if match:
        return max(15, min(900, int(match.group(1))))
    match = re.fullmatch(r"(\d+)\s*(?:m|min|mins|minute|minutes)", normalized)
    if match:
        return max(15, min(900, int(match.group(1)) * 60))
    return 60


class StudioService:
    def __init__(self, store: StudioStore, asset_dir: str | Path | None = None, providers: ProviderRegistry | None = None) -> None:
        self.store = store
        self.store.initialize()
        store_root = getattr(store, "path", getattr(store, "runtime_path", Path("runtime")))
        self.asset_dir = Path(asset_dir or Path(store_root).parent / "assets")
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self.storage = storage_from_env(self.asset_dir)
        self.providers = providers or ProviderRegistry.from_env()
        self.ensure_user(DEFAULT_USER_ID)

    def ensure_user(self, user_id: str, balance: int = 100) -> None:
        timestamp = now()
        with self.store.connection() as connection:
            connection.execute(
                "INSERT INTO users(id, email, password_hash, password_salt, created_at) VALUES (?, ?, NULL, NULL, ?) ON CONFLICT(id) DO NOTHING",
                (user_id, DEFAULT_USER_EMAIL if user_id == DEFAULT_USER_ID else f"{user_id}@local", timestamp),
            )
            connection.execute(
                "INSERT INTO credit_accounts(user_id, balance, updated_at) VALUES (?, ?, ?) ON CONFLICT(user_id) DO NOTHING",
                (user_id, balance, timestamp),
            )

    def register_user(self, email: str, password: str, user_agent: str | None = None) -> dict[str, Any]:
        email = email.strip().lower()
        if "@" not in email or len(email) < 5:
            raise ServiceError("valid email is required")
        if len(password) < 8:
            raise ServiceError("password must be at least 8 characters")
        user_id = new_id("user")
        salt = secrets.token_bytes(16)
        password_hash = hashlib.scrypt(password.encode(), salt=salt, n=16_384, r=8, p=1).hex()
        timestamp = now()
        with self.store.connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO users(id, email, password_hash, password_salt, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, email, password_hash, salt.hex(), timestamp),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    raise ServiceError("email is already registered", 409) from exc
                raise
            connection.execute("INSERT INTO credit_accounts(user_id, balance, updated_at) VALUES (?, 100, ?)", (user_id, timestamp))
        return self._session_response(user_id, email, user_agent)

    def login_user(self, email: str, password: str, user_agent: str | None = None) -> dict[str, Any]:
        row = self.store.one("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
        if not row or not row["password_hash"] or not row["password_salt"]:
            raise ServiceError("invalid email or password", 401)
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(row["password_salt"]), n=16_384, r=8, p=1).hex()
        if not secrets.compare_digest(candidate, row["password_hash"]):
            raise ServiceError("invalid email or password", 401)
        return self._session_response(row["id"], row["email"], user_agent)

    def resolve_user(self, token: str | None) -> str:
        if not token:
            return DEFAULT_USER_ID
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = self.store.one("SELECT user_id, expires_at FROM sessions WHERE token_hash = ?", (token_hash,))
        expires_at = row["expires_at"] if row else None
        if isinstance(expires_at, datetime):
            expired = expires_at <= datetime.now(timezone.utc)
        else:
            expired = bool(expires_at and expires_at <= now())
        if not row or expired:
            raise ServiceError("invalid or expired session", 401)
        self.store.write("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (now(), token_hash))
        return row["user_id"]

    def logout(self, token: str | None) -> None:
        """撤销当前 session；服务端登出不能只依赖浏览器删除 token。"""
        if not token:
            return
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.store.write("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def logout_all(self, user_id: str, token: str | None) -> int:
        """撤销用户的全部 session，包含当前 session；只返回撤销数量。"""
        if not token:
            return 0
        with self.store.connection() as connection:
            result = connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return int(result.rowcount or 0)

    def list_sessions(self, user_id: str, current_token: str | None = None) -> list[dict[str, Any]]:
        """返回当前用户的非敏感设备会话摘要，不返回 token 或 token_hash。"""

        current_hash = hashlib.sha256(current_token.encode()).hexdigest() if current_token else None
        cutoff = now()
        with self.store.connection() as connection:
            connection.execute("DELETE FROM sessions WHERE user_id = ? AND expires_at <= ?", (user_id, cutoff))
            rows = connection.execute(
                "SELECT token_hash, device_label, created_at, last_seen_at, expires_at "
                "FROM sessions WHERE user_id = ? ORDER BY last_seen_at DESC, created_at DESC",
                (user_id,),
            ).fetchall()
        sessions: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            token_hash = str(record.pop("token_hash"))
            sessions.append(
                {
                    "id": self._session_public_id(token_hash),
                    "device": record.get("device_label") or "未知设备",
                    "created_at": _timestamp_iso(record.get("created_at")),
                    "last_seen_at": _timestamp_iso(record.get("last_seen_at")),
                    "expires_at": _timestamp_iso(record.get("expires_at")),
                    "current": token_hash == current_hash,
                }
            )
        return sessions

    def revoke_session(self, session_id: str, user_id: str, current_token: str | None = None) -> dict[str, Any]:
        """撤销一个同账户的非当前会话；当前会话必须使用 logout。"""

        current_hash = hashlib.sha256(current_token.encode()).hexdigest() if current_token else None
        rows = self.store.all("SELECT token_hash FROM sessions WHERE user_id = ?", (user_id,))
        target_hash = next(
            (str(row["token_hash"]) for row in rows if self._session_public_id(str(row["token_hash"])) == session_id),
            None,
        )
        if target_hash is None:
            raise ServiceError("session not found", 404)
        if target_hash == current_hash:
            raise ServiceError("current session cannot be revoked; use logout", 409)
        self.store.write("DELETE FROM sessions WHERE user_id = ? AND token_hash = ?", (user_id, target_hash))
        return {"ok": True, "id": session_id}

    def user_profile(self, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        row = self.store.one("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,))
        if not row:
            raise ServiceError("user not found", 404)
        return {**dict(row), "credits": self.credits(user_id)}

    @staticmethod
    def _session_public_id(token_hash: str) -> str:
        return f"session_{hashlib.sha256(('session-id\0' + token_hash).encode()).hexdigest()[:24]}"

    def _session_response(self, user_id: str, email: str, user_agent: str | None = None) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        timestamp = datetime.now(timezone.utc)
        expires = timestamp + timedelta(days=30)
        self.store.write(
            "INSERT INTO sessions(token_hash, user_id, expires_at, created_at, device_label, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                user_id,
                expires.isoformat(),
                timestamp.isoformat(),
                _session_device_label(user_agent),
                timestamp.isoformat(),
            ),
        )
        return {"token": token, "user": {"id": user_id, "email": email}, "expires_at": expires.isoformat(), "credits": self.credits(user_id)}

    def credits(self, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        self.ensure_user(user_id)
        account = self.store.one("SELECT user_id, balance, updated_at FROM credit_accounts WHERE user_id = ?", (user_id,))
        return dict(account) if account else {"user_id": user_id, "balance": 0}

    def credit_transactions(self, user_id: str = DEFAULT_USER_ID, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.store.all(
            "SELECT id, user_id, job_id, kind, amount, balance_after, reason, created_at "
            "FROM credit_transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(row) for row in rows]

    def top_up(self, user_id: str, amount: int, reason: str = "local development top-up") -> dict[str, Any]:
        if amount <= 0:
            raise ServiceError("amount must be positive")
        transaction_id = new_id("txn")
        timestamp = now()
        with self.store.connection() as connection:
            self._ensure_user_connection(connection, user_id)
            connection.execute(
                "UPDATE credit_accounts SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                (amount, timestamp, user_id),
            )
            row = connection.execute("SELECT balance FROM credit_accounts WHERE user_id = ?", (user_id,)).fetchone()
            connection.execute(
                "INSERT INTO credit_transactions(id, user_id, kind, amount, balance_after, reason, created_at) "
                "VALUES (?, ?, 'top_up', ?, ?, ?, ?)",
                (transaction_id, user_id, amount, row["balance"], reason, timestamp),
            )
        return self.credits(user_id)

    @staticmethod
    def _billing_provider() -> str:
        return os.environ.get("STUDIO_BILLING_PROVIDER", "disabled").strip().lower() or "disabled"

    @staticmethod
    def _billing_package(package_code: str) -> dict[str, Any] | None:
        return next((dict(package) for package in DEFAULT_CREDIT_PACKAGES if package["code"] == package_code.strip()), None)

    @staticmethod
    def _billing_order_dict(row: Any) -> dict[str, Any]:
        order = dict(row)
        metadata_json = order.pop("metadata_json", None)
        if metadata_json is not None:
            order["metadata"] = json_loads(metadata_json, {})
        elif not isinstance(order.get("metadata"), dict):
            order["metadata"] = {}
        package = StudioService._billing_package(str(order.get("package_code", "")))
        if package:
            order["package_label"] = package["label"]
        return order

    @staticmethod
    def _billing_checkout_base_url() -> str | None:
        """返回受部署配置控制的外部 checkout 入口，不接受用户输入。"""
        value = os.environ.get("STUDIO_BILLING_CHECKOUT_URL", "").strip()
        if not value:
            return None
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ServiceError("billing checkout URL is invalid", 503)
        if os.environ.get("STUDIO_ENV", "development").strip().lower() == "production" and parsed.scheme != "https":
            raise ServiceError("billing checkout URL must use HTTPS in production", 503)
        return value

    @staticmethod
    def _billing_return_url(name: str, order_id: str | None = None) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise ServiceError(f"{name} is not configured", 503)
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ServiceError(f"{name} is invalid", 503)
        if os.environ.get("STUDIO_ENV", "development").strip().lower() == "production" and parsed.scheme != "https":
            raise ServiceError(f"{name} must use HTTPS in production", 503)
        if not order_id:
            return value
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query = [(key, item) for key, item in query if key != "order_id"]
        query.append(("order_id", str(order_id)))
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), "")
        )

    @classmethod
    def _billing_checkout_url(cls, order: dict[str, Any], base_url: str | None = None) -> str | None:
        metadata = order.get("metadata") if isinstance(order.get("metadata"), dict) else {}
        stored_checkout_url = str(metadata.get("checkout_url", "")).strip()
        if stored_checkout_url:
            parsed_stored = urllib.parse.urlsplit(stored_checkout_url)
            if parsed_stored.scheme in {"http", "https"} and parsed_stored.hostname and not parsed_stored.username and not parsed_stored.password and not parsed_stored.fragment:
                return stored_checkout_url
        base = base_url if base_url is not None else cls._billing_checkout_base_url()
        if not base:
            return None
        parsed = urllib.parse.urlsplit(base)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        protected = {"order_id", "payment_reference", "package_code", "credits", "amount_cents", "currency"}
        query = [(key, value) for key, value in query if key not in protected]
        query.extend(
            (
                ("order_id", str(order["id"])),
                ("payment_reference", str(order["provider_order_id"])),
                ("package_code", str(order["package_code"])),
                ("credits", str(order["credits"])),
                ("amount_cents", str(order["amount_cents"])),
                ("currency", str(order["currency"])),
            )
        )
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), ""))

    def _billing_order_response(self, row: Any) -> dict[str, Any]:
        order = self._billing_order_dict(row)
        checkout_url = self._billing_checkout_url(order)
        if checkout_url:
            order["checkout_url"] = checkout_url
        return order

    @staticmethod
    def _billing_order_lookup(connection: Any, reference: str) -> Any:
        return connection.execute(
            "SELECT * FROM billing_orders WHERE id = ? OR provider_order_id = ? OR provider_payment_id = ?",
            (reference, reference, reference),
        ).fetchone()

    def billing_packages(self) -> dict[str, Any]:
        """返回支付无关的商品目录；不会声称已经接入支付 checkout。"""

        provider = self._billing_provider()
        checkout_available = bool(os.environ.get("STUDIO_BILLING_CHECKOUT_URL", "").strip()) if provider == "signed-webhook" else (
            provider == "stripe"
            and bool(os.environ.get("STRIPE_SECRET_KEY", "").strip())
            and bool(os.environ.get("STUDIO_BILLING_SUCCESS_URL", "").strip())
            and bool(os.environ.get("STUDIO_BILLING_CANCEL_URL", "").strip())
        )
        return {
            "provider": provider,
            "checkout_available": checkout_available,
            "packages": [dict(package) for package in DEFAULT_CREDIT_PACKAGES],
        }

    def billing_orders(self, user_id: str = DEFAULT_USER_ID, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        rows = self.store.all(
            "SELECT id, user_id, package_code, credits, amount_cents, currency, provider, provider_order_id, provider_payment_id, "
            "idempotency_key, status, metadata_json, created_at, paid_at "
            "FROM billing_orders WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        orders = [self._billing_order_response(row) for row in rows]
        if not orders:
            return orders
        order_ids = [str(order["id"]) for order in orders]
        placeholders = ", ".join("?" for _ in order_ids)
        adjustments = self.store.all(
            "SELECT id, order_id, provider, adjustment_key, kind, amount_cents, currency, credits_delta, event_id, created_at "
            f"FROM billing_adjustments WHERE order_id IN ({placeholders}) ORDER BY created_at DESC",
            tuple(order_ids),
        )
        grouped: dict[str, list[dict[str, Any]]] = {order_id: [] for order_id in order_ids}
        for adjustment in adjustments:
            item = dict(adjustment)
            grouped.setdefault(str(item["order_id"]), []).append(item)
        for order in orders:
            order["adjustments"] = grouped.get(str(order["id"]), [])
        return orders

    def create_billing_order(
        self,
        package_code: str,
        user_id: str = DEFAULT_USER_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        provider = self._billing_provider()
        if provider not in {"signed-webhook", "stripe"}:
            raise ServiceError("billing provider is not configured", 503)
        checkout_base_url = self._billing_checkout_base_url() if provider == "signed-webhook" else None
        package = self._billing_package(package_code)
        if not package:
            raise ServiceError("unknown credit package", 400)
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        if not canonical_key:
            raise ServiceError("Idempotency-Key is required", 400)
        existing = self.store.one(
            "SELECT id, user_id, package_code, credits, amount_cents, currency, provider, provider_order_id, provider_payment_id, "
            "idempotency_key, status, metadata_json, created_at, paid_at FROM billing_orders WHERE idempotency_key = ?",
            (canonical_key,),
        )
        if existing:
            if str(existing["package_code"]) != package["code"] or str(existing["user_id"]) != user_id:
                raise ServiceError("Idempotency-Key was already used for another credit package", 409)
            if provider == "stripe":
                order = self._ensure_stripe_checkout(self._billing_order_dict(existing))
            else:
                order = self._billing_order_response(existing)
            order["duplicate"] = True
            order["payment_reference"] = order["provider_order_id"]
            return order

        order_id = new_id("order")
        timestamp = now()
        provider_order_id = f"studio_{order_id}"
        with self.store.connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO billing_orders(id, user_id, package_code, credits, amount_cents, currency, provider, "
                    "provider_order_id, idempotency_key, status, metadata_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (
                        order_id,
                        user_id,
                        package["code"],
                        package["credits"],
                        package["amount_cents"],
                        package["currency"],
                        provider,
                        provider_order_id,
                        canonical_key,
                        "{}",
                        timestamp,
                    ),
                )
            except Exception as exc:
                # 并发重试时让数据库唯一键成为最终幂等裁判，再返回已经创建的订单。
                if "UNIQUE" not in str(exc).upper() and "duplicate key" not in str(exc).lower():
                    raise
                existing = connection.execute(
                    "SELECT id, user_id, package_code, credits, amount_cents, currency, provider, provider_order_id, provider_payment_id, "
                    "idempotency_key, status, metadata_json, created_at, paid_at FROM billing_orders WHERE idempotency_key = ?",
                    (canonical_key,),
                ).fetchone()
                if not existing:
                    raise
                if str(existing["package_code"]) != package["code"] or str(existing["user_id"]) != user_id:
                    raise ServiceError("Idempotency-Key was already used for another credit package", 409) from exc
                if provider == "stripe":
                    order = self._ensure_stripe_checkout(self._billing_order_dict(existing))
                else:
                    order = self._billing_order_response(existing)
                order["duplicate"] = True
                order["payment_reference"] = order["provider_order_id"]
                return order
        order = self._billing_order_dict(self.store.one("SELECT * FROM billing_orders WHERE id = ?", (order_id,)))
        if provider == "stripe":
            order = self._ensure_stripe_checkout(order)
        order["payment_reference"] = order["provider_order_id"]
        order["checkout_required"] = True
        checkout_url = self._billing_checkout_url(order, checkout_base_url)
        if checkout_url:
            order["checkout_url"] = checkout_url
        return order

    def _ensure_stripe_checkout(self, order: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(order.get("metadata") if isinstance(order.get("metadata"), dict) else {})
        stored_checkout_url = str(metadata.get("checkout_url", "")).strip()
        if stored_checkout_url:
            order["metadata"] = metadata
            order["checkout_url"] = stored_checkout_url
            return order
        try:
            result = StripeCheckoutAdapter.from_env().create_checkout(
                order,
                success_url=self._billing_return_url("STUDIO_BILLING_SUCCESS_URL", str(order["id"])),
                cancel_url=self._billing_return_url("STUDIO_BILLING_CANCEL_URL", str(order["id"])),
            )
        except BillingProviderError as exc:
            raise ServiceError("Stripe checkout is unavailable", exc.status_code) from exc
        metadata["checkout_url"] = result["checkout_url"]
        self.store.write(
            "UPDATE billing_orders SET metadata_json = ? WHERE id = ?",
            (json.dumps(metadata, ensure_ascii=False, sort_keys=True), order["id"]),
        )
        order["metadata"] = metadata
        order["checkout_url"] = result["checkout_url"]
        return order

    @staticmethod
    def _billing_adjustment_delta(connection: Any, order: Any, payload: dict[str, Any], provider: str) -> int:
        kind = str(payload["adjustment_type"]).strip().lower()
        amount_cents = int(payload["amount_cents"])
        order_amount_cents = int(order["amount_cents"])
        order_credits = int(order["credits"])
        requested = min(order_credits, max(1, (amount_cents * order_credits + order_amount_cents - 1) // order_amount_cents))
        if kind in {"refund", "chargeback"}:
            applied = connection.execute(
                "SELECT COALESCE(SUM(credits_delta), 0) AS total FROM billing_adjustments WHERE order_id = ?",
                (order["id"],),
            ).fetchone()
            available = max(0, order_credits + int(applied["total"] or 0))
            return -min(requested, available)
        adjustment_id = str(payload["adjustment_id"]).strip()
        withdrawn = connection.execute(
            "SELECT COALESCE(SUM(credits_delta), 0) AS total FROM billing_adjustments "
            "WHERE provider = ? AND order_id = ? AND adjustment_key = ?",
            (provider, order["id"], f"chargeback:{adjustment_id}"),
        ).fetchone()
        withdrawn_delta = int(withdrawn["total"] or 0)
        if withdrawn_delta >= 0:
            raise ServiceError("chargeback withdrawal must be settled before funds reinstated", 409)
        return -withdrawn_delta

    def settle_billing_webhook(self, payload: dict[str, Any], provider: str = "signed-webhook") -> dict[str, Any]:
        """验签后的 provider-neutral settlement：支付、取消和账务调整均幂等。"""

        if self._billing_provider() != provider:
            raise ServiceError("billing provider is not configured", 503)
        if not isinstance(payload, dict):
            raise ServiceError("billing webhook payload must be an object", 400)
        event_id = str(payload.get("event_id", "")).strip()
        order_reference = str(payload.get("order_id", "")).strip()
        status = str(payload.get("status", "")).strip().lower()
        if not event_id or len(event_id) > 256:
            raise ServiceError("billing webhook event_id is required", 400)
        if not order_reference or len(order_reference) > 256:
            raise ServiceError("billing webhook order_id is required", 400)
        if status not in {"paid", "cancelled", "adjustment"}:
            raise ServiceError("billing webhook status must be paid, cancelled or adjustment", 400)
        if status == "paid":
            for field in ("amount_cents", "currency"):
                if field not in payload:
                    raise ServiceError(f"billing webhook {field} is required for paid orders", 400)
        if status == "adjustment":
            for field in ("adjustment_id", "adjustment_type", "amount_cents", "currency"):
                if field not in payload:
                    raise ServiceError(f"billing webhook {field} is required for adjustments", 400)
            if str(payload["adjustment_type"]).strip().lower() not in {"refund", "chargeback", "chargeback_reinstated"}:
                raise ServiceError("billing webhook adjustment_type is invalid", 400)
            try:
                if int(payload["amount_cents"]) <= 0:
                    raise ValueError
            except (TypeError, ValueError, OverflowError) as exc:
                raise ServiceError("billing webhook adjustment amount is invalid", 400) from exc
        canonical_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_sha256 = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        timestamp = now()
        adjustment_result: dict[str, Any] | None = None
        with self.store.connection() as connection:
            existing_event = connection.execute(
                "SELECT event_id, provider, order_id, payload_sha256, received_at FROM billing_webhook_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing_event:
                if str(existing_event["payload_sha256"]) != payload_sha256:
                    raise ServiceError("billing webhook event_id was reused with different payload", 409)
                order = self._billing_order_lookup(connection, order_reference)
                if not order:
                    raise ServiceError("billing order not found", 404)
                result = self._billing_order_response(order)
                result["duplicate"] = True
                return result

            order = self._billing_order_lookup(connection, order_reference)
            if not order:
                raise ServiceError("billing order not found", 404)
            for field in ("amount_cents", "credits", "currency"):
                if field not in payload:
                    continue
                payload_value = str(payload[field]).strip().lower() if field == "currency" else str(payload[field])
                order_value = str(order[field]).strip().lower() if field == "currency" else str(order[field])
                if status == "adjustment" and field in {"amount_cents", "credits"}:
                    continue
                if payload_value != order_value:
                    raise ServiceError(f"billing webhook {field} does not match order", 409)
            connection.execute(
                "INSERT INTO billing_webhook_events(event_id, provider, order_id, payload_sha256, received_at) VALUES (?, ?, ?, ?, ?)",
                (event_id, provider, order["id"], payload_sha256, timestamp),
            )
            current_status = str(order["status"])
            provider_payment_id = str(payload.get("provider_payment_id", "")).strip()
            if provider_payment_id:
                existing_payment_id = str(order["provider_payment_id"] or "").strip()
                if existing_payment_id and existing_payment_id != provider_payment_id:
                    raise ServiceError("billing webhook provider payment reference does not match order", 409)
                if not existing_payment_id:
                    connection.execute("UPDATE billing_orders SET provider_payment_id = ? WHERE id = ?", (provider_payment_id, order["id"]))
            if status == "cancelled":
                if current_status == "pending":
                    connection.execute("UPDATE billing_orders SET status = 'cancelled' WHERE id = ? AND status = 'pending'", (order["id"],))
                elif current_status == "paid":
                    raise ServiceError("paid billing order cannot be cancelled without a refund flow", 409)
            elif status == "adjustment":
                if current_status != "paid":
                    raise ServiceError("billing adjustment requires a paid order", 409)
                adjustment_type = str(payload["adjustment_type"]).strip().lower()
                adjustment_id = str(payload["adjustment_id"]).strip()
                adjustment_key = f"chargeback:{adjustment_id}" if adjustment_type == "chargeback" else (
                    f"chargeback:{adjustment_id}:reinstated" if adjustment_type == "chargeback_reinstated" else f"refund:{adjustment_id}"
                )
                existing_adjustment = connection.execute(
                    "SELECT * FROM billing_adjustments WHERE provider = ? AND adjustment_key = ?",
                    (provider, adjustment_key),
                ).fetchone()
                if existing_adjustment:
                    if (
                        str(existing_adjustment["kind"]).strip().lower() != adjustment_type
                        or int(existing_adjustment["amount_cents"]) != int(payload["amount_cents"])
                        or str(existing_adjustment["currency"]).strip().lower() != str(payload["currency"]).strip().lower()
                    ):
                        raise ServiceError("billing adjustment key was reused with different payload", 409)
                    adjustment_result = dict(existing_adjustment)
                else:
                    credits_delta = self._billing_adjustment_delta(connection, order, payload, provider)
                    connection.execute(
                        "INSERT INTO billing_adjustments(id, provider, order_id, adjustment_key, kind, amount_cents, currency, credits_delta, event_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            new_id("billing_adjustment"),
                            provider,
                            order["id"],
                            adjustment_key,
                            adjustment_type,
                            int(payload["amount_cents"]),
                            str(payload["currency"]).strip().lower(),
                            credits_delta,
                            event_id,
                            timestamp,
                        ),
                    )
                    self._ensure_user_connection(connection, str(order["user_id"]))
                    connection.execute(
                        "UPDATE credit_accounts SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                        (credits_delta, timestamp, order["user_id"]),
                    )
                    balance = connection.execute(
                        "SELECT balance FROM credit_accounts WHERE user_id = ?", (order["user_id"],)
                    ).fetchone()["balance"]
                    transaction_kind = {
                        "refund": "billing_refund",
                        "chargeback": "billing_chargeback",
                        "chargeback_reinstated": "billing_chargeback_reinstated",
                    }[adjustment_type]
                    connection.execute(
                        "INSERT INTO credit_transactions(id, user_id, kind, amount, balance_after, reason, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            new_id("txn"),
                            order["user_id"],
                            transaction_kind,
                            credits_delta,
                            balance,
                            f"{adjustment_type} {adjustment_id} for billing order {order['id']}",
                            timestamp,
                        ),
                    )
                    adjustment_result = dict(
                        connection.execute(
                            "SELECT * FROM billing_adjustments WHERE provider = ? AND adjustment_key = ?",
                            (provider, adjustment_key),
                        ).fetchone()
                    )
            elif current_status == "pending":
                updated = connection.execute(
                    "UPDATE billing_orders SET status = 'paid', paid_at = ? WHERE id = ? AND status = 'pending'",
                    (timestamp, order["id"]),
                )
                if int(updated.rowcount or 0) == 1:
                    self._ensure_user_connection(connection, str(order["user_id"]))
                    connection.execute(
                        "UPDATE credit_accounts SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                        (order["credits"], timestamp, order["user_id"]),
                    )
                    balance = connection.execute(
                        "SELECT balance FROM credit_accounts WHERE user_id = ?", (order["user_id"],)
                    ).fetchone()["balance"]
                    connection.execute(
                        "INSERT INTO credit_transactions(id, user_id, kind, amount, balance_after, reason, created_at) "
                        "VALUES (?, ?, 'purchase', ?, ?, ?, ?)",
                        (new_id("txn"), order["user_id"], order["credits"], balance, f"billing order {order['id']}", timestamp),
                    )
            elif current_status == "cancelled":
                raise ServiceError("cancelled billing order cannot be paid", 409)
            settled = connection.execute("SELECT * FROM billing_orders WHERE id = ?", (order["id"],)).fetchone()
        result = self._billing_order_response(settled)
        result["duplicate"] = current_status != "pending" if status != "adjustment" else adjustment_result is not None and str(adjustment_result.get("event_id")) != event_id
        if adjustment_result is not None:
            result["billing_adjustment"] = adjustment_result
        return result

    def list_projects(self, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
        rows = self.store.all("SELECT * FROM projects WHERE user_id = ? ORDER BY updated_at DESC", (user_id,))
        return [self.project_summary(dict(row)) for row in rows]

    def create_project(
        self,
        title: str,
        story: str = "",
        style: str = "国漫写实",
        episode_length: str = "1min",
        user_id: str = DEFAULT_USER_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ServiceError("title is required")
        story_text = story.strip()
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        if canonical_key:
            existing = self.store.one(
                "SELECT * FROM projects WHERE user_id = ? AND idempotency_key = ?",
                (user_id, canonical_key),
            )
            if existing:
                self._validate_project_idempotency(existing, title, story_text, style, episode_length)
                result = self.get_project(str(existing["id"]), user_id)
                result["duplicate"] = True
                return result
        project_id = new_id("project")
        timestamp = now()
        source_document_id = None
        try:
            with self.store.connection() as connection:
                self._ensure_user_connection(connection, user_id)
                connection.execute(
                    "INSERT INTO projects(id, user_id, title, story, style, episode_length, source_document_id, idempotency_key, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
                    (project_id, user_id, title, story_text, style, episode_length, None, canonical_key, timestamp, timestamp),
                )
                if story_text:
                    source_document_id = new_id("source")
                    digest = hashlib.sha256(story_text.encode("utf-8")).hexdigest()
                    connection.execute(
                        "INSERT INTO source_documents(id, project_id, filename, media_type, content_sha256, text, created_at) "
                        "VALUES (?, ?, 'story-synopsis.txt', 'text/plain', ?, ?, ?)",
                        (source_document_id, project_id, digest, story_text, timestamp),
                    )
                    connection.execute(
                        "UPDATE projects SET source_document_id = ? WHERE id = ?",
                        (source_document_id, project_id),
                    )
        except Exception as exc:
            if canonical_key and ("UNIQUE" in str(exc).upper() or "duplicate key" in str(exc).lower()):
                existing = self.store.one(
                    "SELECT * FROM projects WHERE user_id = ? AND idempotency_key = ?",
                    (user_id, canonical_key),
                )
                if existing:
                    self._validate_project_idempotency(existing, title, story_text, style, episode_length)
                    result = self.get_project(str(existing["id"]), user_id)
                    result["duplicate"] = True
                    return result
            raise
        return self.get_project(project_id, user_id)

    @staticmethod
    def _validate_project_idempotency(
        existing: Any,
        title: str,
        story: str,
        style: str,
        episode_length: str,
    ) -> None:
        if (
            str(existing["title"]) != title
            or str(existing["story"]) != story
            or str(existing["style"]) != style
            or str(existing["episode_length"]) != episode_length
        ):
            raise ServiceError("Idempotency-Key was already used for different project payload", 409)

    def get_project(self, project_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        row = self.store.one("SELECT * FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id))
        if not row:
            raise ServiceError("project not found", 404)
        project = dict(row)
        project["characters"] = []
        for item in self.store.all("SELECT * FROM characters WHERE project_id = ?", (project_id,)):
            character = self._character_dict(dict(item))
            character["references"] = [
                dict(reference)
                for reference in self.store.all(
                    "SELECT * FROM character_references WHERE character_id = ? "
                    "ORDER BY CASE WHEN status = 'ready' THEN 0 "
                    "WHEN status IN ('uploaded', 'pending', 'generating') THEN 1 ELSE 2 END, id DESC",
                    (character["id"],),
                )
            ]
            project["characters"].append(character)
        project["story_bible"] = self._story_bible_data(project_id)
        project["episodes"] = [self.episode_detail(dict(item)) for item in self.store.all("SELECT * FROM episodes WHERE project_id = ? ORDER BY number", (project_id,))]
        project["source_documents"] = [dict(item) for item in self.store.all("SELECT id, filename, media_type, content_sha256, copyright_acknowledged, created_at FROM source_documents WHERE project_id = ? ORDER BY created_at DESC", (project_id,))]
        project["audio_assets"] = [self.asset_with_job(row["id"]) for row in self.store.all("SELECT id FROM assets WHERE project_id = ? AND shot_id IS NULL AND kind = 'audio' ORDER BY created_at", (project_id,))]
        project["adaptation_unit_count"] = self._count("SELECT COUNT(*) AS count FROM adaptation_units WHERE project_id = ?", (project_id,))
        project["asset_count"] = self._count("SELECT COUNT(*) AS count FROM assets WHERE project_id = ?", (project_id,))
        return project

    def _require_approved_adaptation_for_structure(self, project_id: str, user_id: str = DEFAULT_USER_ID) -> None:
        """阻止未完成改编审校的项目进入 AI 结构生成。"""

        self.get_project(project_id, user_id)
        counts = self.store.one(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved "
            "FROM adaptation_units WHERE project_id = ?",
            (project_id,),
        )
        total = int(counts["total"] or 0) if counts else 0
        approved = int(counts["approved"] or 0) if counts else 0
        # 纯故事梗概项目可以直接生成结构；只要已经进入小说改编流程，
        # 就必须让用户明确审校每个改编单元，避免草稿内容进入后续媒体生成。
        if total and approved != total:
            raise ServiceError(
                f"approve all adaptation units before generating project structure ({approved}/{total} approved)",
                409,
            )

    def _require_clear_adaptation_review_for_downstream(self, project_id: str, user_id: str) -> None:
        """改编重新审校或驳回时，禁止继续制造/合成媒体。"""

        self.get_project(project_id, user_id)
        pending = self.store.one(
            "SELECT COUNT(*) AS count FROM adaptation_units "
            "WHERE project_id = ? AND status IN ('review', 'rejected')",
            (project_id,),
        )
        if pending and int(pending["count"] or 0) > 0:
            raise ServiceError(
                "resolve adaptation review before generating or composing downstream media",
                409,
            )

    def _adaptation_content_revision(self, project_id: str) -> str:
        """返回当前改编内容的确定性快照，不把审核状态混入内容版本。"""

        rows = self.store.all(
            "SELECT id, source_segment_id, source_text, adapted_text, mode "
            "FROM adaptation_units WHERE project_id = ? ORDER BY chapter_no, sequence, id",
            (project_id,),
        )
        payload = [
            {
                "id": str(row["id"]),
                "source_segment_id": str(row["source_segment_id"]),
                "source_text": str(row["source_text"] or ""),
                "adapted_text": str(row["adapted_text"] or ""),
                "mode": str(row["mode"] or ""),
            }
            for row in rows
        ]
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _shot_artifact_revision(self, shot: Any, prompt: Any | None = None) -> str:
        """绑定镜头语义和提示词，防止旧关键帧在人工改镜头后继续复用。"""

        shot_data = dict(shot)
        if prompt is None and shot_data.get("image_prompt_id"):
            prompt = self.store.one("SELECT prompt, negative_prompt FROM image_prompts WHERE id = ?", (shot_data["image_prompt_id"],))
        payload = {
            "adaptation_revision": str(shot_data.get("adaptation_revision") or ""),
            "scene": str(shot_data.get("scene") or ""),
            "emotion": str(shot_data.get("emotion") or ""),
            "duration_seconds": str(shot_data.get("duration_seconds") or ""),
            "description": str(shot_data.get("description") or ""),
            "prompt": str(prompt["prompt"] or "") if prompt else "",
            "negative_prompt": str(prompt["negative_prompt"] or "") if prompt else "",
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _require_current_shot_revision(self, shot: Any, user_id: str, action: str) -> str:
        project = self.store.one(
            "SELECT p.id FROM projects p JOIN episodes e ON e.project_id = p.id "
            "WHERE e.id = ? AND p.user_id = ?",
            (shot["episode_id"], user_id),
        )
        if not project:
            raise ServiceError("shot not found", 404)
        self._require_clear_adaptation_review_for_downstream(str(project["id"]), user_id)
        current_revision = self._adaptation_content_revision(str(project["id"]))
        if str(shot["status"] or "active") == "superseded" or str(shot["adaptation_revision"] or "") != current_revision:
            raise ServiceError(
                f"project structure is outdated; regenerate project structure before {action}",
                409,
            )
        return current_revision

    def _current_image_artifact_revision(self, image: Any, shot: Any | None = None) -> str | None:
        image_data = dict(image)
        metadata = json_loads(image_data.get("metadata_json", "{}"), {}) if "metadata_json" in image_data else image_data.get("metadata", {})
        if not isinstance(metadata, dict):
            return None
        if shot is None and image_data.get("shot_id"):
            shot = self.store.one("SELECT * FROM shots WHERE id = ?", (image_data["shot_id"],))
        if not shot:
            return None
        expected = self._shot_artifact_revision(shot)
        return expected if str(metadata.get("artifact_revision") or "") == expected else None

    def _episode_composition_revision(self, episode_id: str, bindings: list[dict[str, Any]]) -> str:
        settings = self.store.one("SELECT audio_tracks_json, subtitles_json, narration_text FROM composition_settings WHERE episode_id = ?", (episode_id,))
        payload = {
            "episode_id": episode_id,
            "bindings": bindings,
            "audio_tracks": json_loads(settings["audio_tracks_json"], []) if settings else [],
            "subtitles": json_loads(settings["subtitles_json"], []) if settings else [],
            "narration_text": str(settings["narration_text"] or "") if settings else "",
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _current_episode_composition_revision(self, episode_id: str) -> str | None:
        """计算当前可合成输入；任一镜头或媒体过期时返回 None。"""

        episode = self.store.one("SELECT id, project_id FROM episodes WHERE id = ?", (episode_id,))
        if not episode:
            return None
        shots = self.store.all(
            "SELECT * FROM shots WHERE episode_id = ? AND COALESCE(status, 'active') <> 'superseded' ORDER BY sequence",
            (episode_id,),
        )
        if not shots:
            return None
        bindings: list[dict[str, Any]] = []
        for shot in shots:
            current_revision = self._adaptation_content_revision(str(episode["project_id"]))
            if str(shot["adaptation_revision"] or "") != current_revision:
                return None
            expected = self._shot_artifact_revision(shot)
            image = None
            for candidate in self.store.all(
                "SELECT * FROM assets WHERE project_id = ? AND shot_id = ? AND kind = 'image' "
                "AND status = 'ready' AND selected = 1 AND consistency_confirmed = 1 "
                "ORDER BY updated_at DESC, created_at DESC, id DESC",
                (episode["project_id"], shot["id"]),
            ):
                metadata = json_loads(candidate["metadata_json"], {})
                if str(metadata.get("artifact_revision") or "") == expected:
                    image = candidate
                    break
            if not image:
                return None
            image_metadata = json_loads(image["metadata_json"], {})
            video = None
            for candidate in self.store.all(
                "SELECT * FROM assets WHERE project_id = ? AND shot_id = ? AND kind = 'video' "
                "AND status = 'ready' AND source_asset_id = ? ORDER BY created_at DESC, id DESC",
                (episode["project_id"], shot["id"], image["id"]),
            ):
                metadata = json_loads(candidate["metadata_json"], {})
                if str(metadata.get("source_artifact_revision") or "") == str(image_metadata.get("artifact_revision") or ""):
                    video = candidate
                    break
            if not video:
                return None
            bindings.append(
                {
                    "shot_id": str(shot["id"]),
                    "sequence": int(shot["sequence"]),
                    "image_asset_id": str(image["id"]),
                    "video_asset_id": str(video["id"]),
                    "artifact_revision": str(image_metadata.get("artifact_revision") or ""),
                }
            )
        return self._episode_composition_revision(
            episode_id,
            sorted(bindings, key=lambda item: (item["sequence"], item["shot_id"])),
        )

    def project_readiness(self, project_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        """汇总项目制作链路的服务端门禁，不把前端按钮状态当作真相。"""

        self.get_project(project_id, user_id)
        source_documents = int(self._count("SELECT COUNT(*) AS count FROM source_documents WHERE project_id = ?", (project_id,)))
        source_segments = int(self._count("SELECT COUNT(*) AS count FROM source_segments WHERE project_id = ?", (project_id,)))
        adaptation_total = int(self._count("SELECT COUNT(*) AS count FROM adaptation_units WHERE project_id = ?", (project_id,)))
        adaptation_approved = int(self._count("SELECT COUNT(*) AS count FROM adaptation_units WHERE project_id = ? AND status = 'approved'", (project_id,)))
        character_total = int(self._count("SELECT COUNT(*) AS count FROM characters WHERE project_id = ?", (project_id,)))
        reference_rows = self.store.all(
            "SELECT c.id, r.status, r.front_url, r.side_url, r.back_url "
            "FROM characters c LEFT JOIN character_references r ON r.character_id = c.id "
            "WHERE c.project_id = ? ORDER BY c.id, r.id DESC",
            (project_id,),
        )
        ready_reference_characters: set[str] = set()
        for row in reference_rows:
            if str(row["id"]) in ready_reference_characters or str(row["status"] or "") != "ready":
                continue
            if all(str(row[column] or "").strip() for column in ("front_url", "side_url", "back_url")):
                ready_reference_characters.add(str(row["id"]))

        episode_total = int(self._count("SELECT COUNT(*) AS count FROM episodes WHERE project_id = ?", (project_id,)))
        current_adaptation_revision = self._adaptation_content_revision(project_id)
        shot_rows = self.store.all(
            "SELECT s.id, s.episode_id, s.sequence, s.scene, s.emotion, s.duration_seconds, s.description, "
            "s.image_prompt_id, s.adaptation_revision, s.status "
            "FROM shots s JOIN episodes e ON e.id = s.episode_id "
            "WHERE e.project_id = ? AND COALESCE(s.status, 'active') <> 'superseded' "
            "ORDER BY e.number, s.sequence",
            (project_id,),
        )
        shot_ids = {str(row["id"]) for row in shot_rows}
        shot_total = len(shot_ids)
        shot_by_id = {str(row["id"]): row for row in shot_rows}
        structure_snapshot_ready = shot_total > 0 and all(
            str(row["adaptation_revision"] or "") == current_adaptation_revision
            and str(row["status"] or "active") == "active"
            for row in shot_rows
        )
        prompt_count = sum(1 for row in shot_rows if structure_snapshot_ready and row["image_prompt_id"])
        image_rows = self.store.all(
            "SELECT a.id, a.shot_id, a.status, a.selected, a.consistency_confirmed, a.metadata_json "
            "FROM assets a WHERE a.project_id = ? AND a.kind = 'image' ORDER BY a.created_at",
            (project_id,),
        )
        selected_images = [
            row for row in image_rows
            if str(row["shot_id"] or "") in shot_ids
            and structure_snapshot_ready
            and bool_value(row["selected"])
            and bool_value(row["consistency_confirmed"])
            and str(row["status"] or "") == "ready"
            and str(json_loads(row["metadata_json"], {}).get("artifact_revision") or "")
            == self._shot_artifact_revision(shot_by_id[str(row["shot_id"])])
        ]
        selected_by_shot = {str(row["shot_id"]): str(row["id"]) for row in selected_images}
        selected_artifact_revisions = {
            str(row["id"]): self._shot_artifact_revision(shot_by_id[str(row["shot_id"])])
            for row in selected_images
        }
        review_count = 0
        pass_count = 0
        for row in selected_images:
            review = self.store.one(
                "SELECT status FROM asset_reviews WHERE asset_id = ? ORDER BY created_at DESC LIMIT 1",
                (row["id"],),
            )
            if not review:
                continue
            review_count += 1
            if str(review["status"] or "").upper() == "PASS":
                pass_count += 1

        video_rows = self.store.all(
            "SELECT id, source_asset_id, shot_id, status, metadata_json FROM assets "
            "WHERE project_id = ? AND kind = 'video' ORDER BY created_at DESC, id DESC",
            (project_id,),
        )
        current_videos_by_image: dict[str, Any] = {}
        for row in video_rows:
            source_image_id = str(row["source_asset_id"] or "")
            if (
                str(row["status"] or "") == "ready"
                and source_image_id in selected_artifact_revisions
                and str(json_loads(row["metadata_json"], {}).get("source_artifact_revision") or "")
                == selected_artifact_revisions[source_image_id]
                and source_image_id not in current_videos_by_image
            ):
                current_videos_by_image[source_image_id] = row
        video_shots = {
            shot_id
            for shot_id, image_id in selected_by_shot.items()
            if image_id in current_videos_by_image
        }
        composition_bindings: dict[str, list[dict[str, Any]]] = {}
        for shot_id, image_id in selected_by_shot.items():
            shot = shot_by_id[shot_id]
            video = current_videos_by_image.get(image_id)
            if not video:
                continue
            composition_bindings.setdefault(str(shot["episode_id"]), []).append(
                {
                    "shot_id": shot_id,
                    "sequence": int(shot["sequence"]),
                    "image_asset_id": image_id,
                    "video_asset_id": str(video["id"]),
                    "artifact_revision": selected_artifact_revisions[image_id],
                }
            )
        current_composition_revisions = {
            episode_id: self._episode_composition_revision(
                episode_id,
                sorted(bindings, key=lambda item: (item["sequence"], item["shot_id"])),
            )
            for episode_id, bindings in composition_bindings.items()
            if len(bindings) == sum(1 for row in shot_rows if str(row["episode_id"]) == episode_id)
        }
        composition_rows = self.store.all(
            "SELECT c.episode_id, c.status, c.playlist_url, c.final_video_url, c.metadata_json "
            "FROM compositions c JOIN episodes e ON e.id = c.episode_id "
            "WHERE e.project_id = ? ORDER BY c.created_at DESC",
            (project_id,),
        )
        completed_compositions = {
            str(row["episode_id"])
            for row in composition_rows
            if str(row["status"] or "") == "completed"
            and (row["playlist_url"] or row["final_video_url"])
            and str(json_loads(row["metadata_json"], {}).get("artifact_revision") or "")
            == current_composition_revisions.get(str(row["episode_id"]))
        }
        latest_story_run = self.store.one(
            "SELECT status FROM story_bible_runs WHERE project_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (project_id,),
        )

        def stage(
            key: str,
            label: str,
            ready: bool,
            count: int,
            total: int,
            detail: str,
            *,
            required: bool = True,
            needs_human_review: bool = False,
            stale: bool = False,
        ) -> dict[str, Any]:
            return {
                "key": key,
                "label": label,
                "status": "needs-review" if needs_human_review else ("ready" if ready else ("pending" if count else "missing")),
                "ready": ready,
                "count": count,
                "total": total,
                "detail": detail,
                "required": required,
                "needs_human_review": needs_human_review,
                "stale": stale,
            }

        source_ready = bool(source_segments or source_documents)
        # Generated adaptation units still require explicit human approval.
        # A project created from a story synopsis has no adaptation units and
        # is intentionally allowed to use the synopsis path directly; do not
        # turn its valid 0/0 stage into a permanent delivery blocker.
        adaptation_required = adaptation_total > 0
        adaptation_ready = not adaptation_required or adaptation_approved == adaptation_total
        adaptation_needs_human_review = adaptation_total > 0 and adaptation_approved < adaptation_total
        structure_ready = bool(character_total and episode_total and shot_total) and structure_snapshot_ready
        references_ready = character_total > 0 and len(ready_reference_characters) == character_total
        prompts_ready = structure_ready and prompt_count == shot_total
        keyframes_ready = shot_total > 0 and len(selected_images) == shot_total
        reviews_ready = keyframes_ready and pass_count == shot_total
        reviews_need_human_review = keyframes_ready and review_count == shot_total and pass_count < shot_total
        videos_ready = shot_total > 0 and len(video_shots) == shot_total
        compositions_ready = episode_total > 0 and len(completed_compositions) == episode_total
        structure_detail = f"{character_total} 角色 / {episode_total} 分集 / {shot_total} 镜头"
        if shot_total and not structure_snapshot_ready:
            structure_detail += "；改编内容已变化，请重新生成项目结构"
        stages = [
            stage("source", "小说来源", source_ready, source_segments, max(source_segments, 1), f"{source_documents} 个来源文档 / {source_segments} 个来源段落"),
            stage(
                "adaptation",
                "改编审校",
                adaptation_ready,
                adaptation_approved,
                adaptation_total,
                "纯故事梗概项目，无需小说改编" if not adaptation_required else f"{adaptation_approved}/{adaptation_total} 条通过",
                required=adaptation_required,
                needs_human_review=adaptation_needs_human_review,
            ),
            stage("story-bible", "故事资产", bool(latest_story_run and str(latest_story_run["status"] or "") == "completed"), 1 if latest_story_run else 0, 1, "已完成抽取" if latest_story_run else "尚未抽取", required=False),
            stage("structure", "项目结构", structure_ready, shot_total, shot_total or 1, structure_detail, stale=shot_total > 0 and not structure_snapshot_ready),
            stage("references", "角色母版", references_ready, len(ready_reference_characters), character_total or 1, f"{len(ready_reference_characters)}/{character_total} 个角色三视图"),
            stage("prompts", "图片提示词", prompts_ready, prompt_count, shot_total or 1, f"{prompt_count}/{shot_total} 个镜头已有提示词"),
            stage("keyframes", "采用关键帧", keyframes_ready, len(selected_images), shot_total or 1, f"{len(selected_images)}/{shot_total} 个镜头已采用并确认"),
            stage("visual-review", "视觉审核", reviews_ready, pass_count, shot_total or 1, f"{pass_count}/{shot_total} 条通过", needs_human_review=reviews_need_human_review),
            stage("videos", "视频片段", videos_ready, len(video_shots), shot_total or 1, f"{len(video_shots)}/{shot_total} 个镜头已有视频"),
            stage("composition", "分集合成", compositions_ready, len(completed_compositions), episode_total or 1, f"{len(completed_compositions)}/{episode_total} 个分集已有成片"),
        ]
        return {
            "project_id": project_id,
            "stages": stages,
            "counts": {
                "source_documents": source_documents,
                "source_segments": source_segments,
                "adaptation_units": adaptation_total,
                "adaptation_approved": adaptation_approved,
                "characters": character_total,
                "episodes": episode_total,
                "shots": shot_total,
                "ready_references": len(ready_reference_characters),
                "prompts": prompt_count,
                "selected_keyframes": len(selected_images),
                "reviewed_keyframes": review_count,
                "passed_keyframes": pass_count,
                "ready_videos": len(video_shots),
                "completed_compositions": len(completed_compositions),
            },
            # 这些布尔值是服务端交付合同，不只是首页展示文案：下游阶段
            # 不能因为存在一份旧成片或部分素材就被误报为可继续/可交付。
            "ready_for_video": structure_ready and references_ready and prompts_ready and keyframes_ready and reviews_ready,
            "ready_for_composition": structure_ready and videos_ready,
            "ready_for_delivery": all(
                (
                    source_ready,
                    adaptation_ready,
                    structure_ready,
                    references_ready,
                    prompts_ready,
                    keyframes_ready,
                    reviews_ready,
                    videos_ready,
                    compositions_ready,
                )
            ),
            "needs_human_review": any(item["needs_human_review"] for item in stages),
        }

    @staticmethod
    def _story_entity_dict(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["attributes"] = json_loads(result.pop("attributes_json", "{}"), {})
        result["source_segment_ids"] = json_loads(result.pop("source_segment_ids_json", "[]"), [])
        return result

    @staticmethod
    def _story_relationship_dict(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["source_segment_ids"] = json_loads(result.pop("source_segment_ids_json", "[]"), [])
        return result

    def _story_bible_data(self, project_id: str) -> dict[str, Any]:
        entities = [
            self._story_entity_dict(dict(row))
            for row in self.store.all(
                "SELECT * FROM story_entities WHERE project_id = ? AND status <> 'superseded' ORDER BY kind, name, id",
                (project_id,),
            )
        ]
        relationships = [
            self._story_relationship_dict(dict(row))
            for row in self.store.all(
                "SELECT * FROM story_relationships WHERE project_id = ? AND status <> 'superseded' ORDER BY created_at, id",
                (project_id,),
            )
        ]
        latest = self.store.one(
            "SELECT id, provider, model, source_sha256, output_json, status, error, created_at "
            "FROM story_bible_runs WHERE project_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (project_id,),
        )
        run = None
        if latest:
            run = dict(latest)
            run["output"] = json_loads(run.pop("output_json", "{}"), {})
        return {
            "project_id": project_id,
            "entities": entities,
            "relationships": relationships,
            "entity_count": len(entities),
            "relationship_count": len(relationships),
            "last_run": run,
        }

    def story_bible_with_job(self, project_id: str, user_id: str | None = None) -> dict[str, Any]:
        """返回故事资产快照及最近一次抽取 Job，供同步和异步 UI 共用。"""
        if user_id is None:
            project = self.store.one("SELECT id, user_id FROM projects WHERE id = ?", (project_id,))
        else:
            project = self.store.one("SELECT id, user_id FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id))
        if not project:
            raise ServiceError("project not found", 404)
        effective_user_id = str(project["user_id"])
        result = self._story_bible_data(project_id)
        job = self.store.one(
            "SELECT * FROM jobs WHERE target_id = ? AND user_id = ? AND kind = 'story-bible' ORDER BY created_at DESC LIMIT 1",
            (project_id, effective_user_id),
        )
        result["job"] = dict(job) if job else None
        return result

    def story_bible(self, project_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        return self.story_bible_with_job(project_id, user_id)

    def structure_with_job(self, project_id: str, user_id: str | None = None) -> dict[str, Any]:
        """返回项目结构快照及最近一次角色/分集/分镜 Job。"""
        if user_id is None:
            project = self.store.one("SELECT id, user_id FROM projects WHERE id = ?", (project_id,))
        else:
            project = self.store.one("SELECT id, user_id FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id))
        if not project:
            raise ServiceError("project not found", 404)
        effective_user_id = str(project["user_id"])
        result = self.get_project(project_id, effective_user_id)
        job = self._find_structure_job(project_id, effective_user_id, "full")
        result["job"] = dict(job) if job else None
        return result

    @staticmethod
    def _structure_stage_from_job(job: Any) -> str:
        """读取结构 Job 的阶段；旧 Job 没有 payload 时视为完整结构。"""
        payload = json_loads(job["payload_json"] if job and "payload_json" in job.keys() else "{}", {})
        stage = str(payload.get("stage") or "full").strip().lower()
        return stage if stage in {"full", "characters", "outline", "shots"} else "full"

    def _find_structure_job(
        self,
        target_id: str,
        user_id: str,
        stage: str,
        statuses: tuple[str, ...] | None = None,
    ) -> Any:
        """跨 SQLite/PostgreSQL 按 JSON payload 筛选结构 Job，避免方言 SQL。"""
        params: tuple[Any, ...] = (target_id, user_id)
        sql = "SELECT * FROM jobs WHERE target_id = ? AND user_id = ? AND kind = 'structure'"
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params += statuses
        sql += " ORDER BY created_at DESC"
        for row in self.store.all(sql, params):
            if self._structure_stage_from_job(row) == stage:
                return row
        return None

    def structure_stage_with_job(
        self,
        target_id: str,
        stage: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """返回兼容结构入口的阶段快照和对应 Job。"""
        if stage == "shots":
            if user_id is None:
                episode = self.store.one("SELECT * FROM episodes WHERE id = ?", (target_id,))
            else:
                episode = self.store.one(
                    "SELECT e.* FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?",
                    (target_id, user_id),
                )
            if not episode:
                raise ServiceError("episode not found", 404)
            effective_user_id = str(user_id or self.store.one("SELECT user_id FROM projects WHERE id = ?", (episode["project_id"],))["user_id"])
            result = self.episode_detail(dict(episode))
            job = self._find_structure_job(target_id, effective_user_id, stage)
        else:
            if user_id is None:
                project = self.store.one("SELECT id, user_id FROM projects WHERE id = ?", (target_id,))
            else:
                project = self.store.one("SELECT id, user_id FROM projects WHERE id = ? AND user_id = ?", (target_id, user_id))
            if not project:
                raise ServiceError("project not found", 404)
            effective_user_id = str(project["user_id"])
            project_snapshot = self.get_project(target_id, effective_user_id)
            result = {
                "project_id": target_id,
                "characters": project_snapshot.get("characters", []) if stage == "characters" else [],
                "episodes": project_snapshot.get("episodes", []) if stage == "outline" else [],
            }
            job = self._find_structure_job(target_id, effective_user_id, stage)
        result["job"] = dict(job) if job else None
        return result

    def _run_structure_stage(self, stage: str, target_id: str, user_id: str) -> list[dict[str, Any]]:
        if stage == "shots":
            episode = self.store.one("SELECT project_id FROM episodes WHERE id = ?", (target_id,))
            if not episode:
                raise ServiceError("episode not found", 404)
            self._require_approved_adaptation_for_structure(str(episode["project_id"]), user_id)
        else:
            self._require_approved_adaptation_for_structure(target_id, user_id)
        if stage == "characters":
            return self.create_characters(target_id, user_id=user_id)
        if stage == "outline":
            return self.create_outline(target_id, user_id)
        if stage == "shots":
            return self.create_shots(target_id, user_id)
        raise ServiceError(f"unsupported structure stage: {stage}")

    def _structure_job_result(self, job: Any, user_id: str | None = None) -> dict[str, Any]:
        stage = self._structure_stage_from_job(job)
        if stage == "full":
            return self.structure_with_job(str(job["target_id"]), user_id)
        return self.structure_stage_with_job(str(job["target_id"]), stage, user_id)

    def request_structure_stage(
        self,
        stage: str,
        target_id: str,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """为兼容结构入口创建可恢复的单阶段 structure Job。"""
        stage = str(stage or "").strip().lower()
        if stage not in {"characters", "outline", "shots"}:
            raise ServiceError(f"unsupported structure stage: {stage}")
        if stage == "shots":
            episode = self.store.one(
                "SELECT e.id, e.project_id FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?",
                (target_id, user_id),
            )
            if not episode:
                raise ServiceError("episode not found", 404)
            structure_project_id = str(episode["project_id"])
        else:
            self.get_project(target_id, user_id)
            structure_project_id = target_id
        self._require_approved_adaptation_for_structure(structure_project_id, user_id)
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        request_key = f"structure-stage:{stage}:{canonical_key}" if canonical_key else None
        if canonical_key:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE user_id = ? AND idempotency_key = ?",
                (user_id, request_key),
            )
            if existing:
                if existing["kind"] != "structure" or existing["target_id"] != target_id or self._structure_stage_from_job(existing) != stage:
                    raise ServiceError("Idempotency-Key was already used for another structure stage request", 409)
                if run_now and existing["status"] == "queued":
                    return self.run_job(str(existing["id"]), user_id)
                if existing["status"] == "queued":
                    self._enqueue_external(existing["id"], user_id, "structure", target_id)
                return self.structure_stage_with_job(target_id, stage, user_id)
        if run_now and not request_key:
            return self._run_structure_stage(stage, target_id, user_id)
        if not request_key:
            active = self._find_structure_job(target_id, user_id, stage, ("queued", "running"))
            if active:
                if active["status"] == "queued":
                    self._enqueue_external(active["id"], user_id, "structure", target_id)
                return self.structure_stage_with_job(target_id, stage, user_id)
        providers = self._providers_for_user(user_id)
        job_id = new_id("job")
        request_key = request_key or f"structure-stage:{stage}:{job_id}"
        with self.store.connection() as connection:
            inserted = self._insert_job(connection, job_id, user_id, "structure", target_id, 0, providers.text.name, request_key)
            if inserted:
                connection.execute("UPDATE jobs SET payload_json = ? WHERE id = ?", (json.dumps({"stage": stage}, ensure_ascii=False), job_id))
        if not inserted:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE idempotency_key = ? AND user_id = ?",
                (request_key, user_id),
            )
            if not existing or existing["kind"] != "structure" or existing["target_id"] != target_id or self._structure_stage_from_job(existing) != stage:
                raise ServiceError("Idempotency-Key was already used for another structure stage request", 409)
            job_id = str(existing["id"])
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "structure", target_id)
        return self.structure_stage_with_job(target_id, stage, user_id)

    def prepare_project_structure(self, project_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        """按可恢复的阶段顺序生成角色卡、分集大纲和每集分镜。"""
        self._require_approved_adaptation_for_structure(project_id, user_id)
        self.create_characters(project_id, user_id=user_id)
        episodes = self.create_outline(project_id, user_id)
        for episode in episodes:
            self.create_shots(episode["id"], user_id)
        return self.get_project(project_id, user_id)

    def request_project_structure(
        self,
        project_id: str,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """同步执行或创建可恢复的项目结构生成 Job。"""
        self._require_approved_adaptation_for_structure(project_id, user_id)
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        request_key = f"structure:{canonical_key}" if canonical_key else None
        if canonical_key:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE user_id = ? AND idempotency_key = ?",
                (user_id, request_key),
            )
            if existing:
                if existing["kind"] != "structure" or existing["target_id"] != project_id:
                    raise ServiceError("Idempotency-Key was already used for another structure request", 409)
                if run_now and existing["status"] == "queued":
                    return self.run_job(str(existing["id"]), user_id)
                if existing["status"] == "queued":
                    self._enqueue_external(existing["id"], user_id, "structure", project_id)
                return self.structure_with_job(project_id, user_id)
        if run_now and not request_key:
            return self.prepare_project_structure(project_id, user_id) | {"job": None}
        if not request_key:
            active = self._find_structure_job(project_id, user_id, "full", ("queued", "running"))
            if active:
                if active["status"] == "queued":
                    self._enqueue_external(active["id"], user_id, "structure", project_id)
                return self.structure_with_job(project_id, user_id)
        providers = self._providers_for_user(user_id)
        job_id = new_id("job")
        request_key = request_key or f"structure:{job_id}"
        with self.store.connection() as connection:
            inserted = self._insert_job(connection, job_id, user_id, "structure", project_id, 0, providers.text.name, request_key)
        if not inserted:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE idempotency_key = ? AND user_id = ?",
                (request_key, user_id),
            )
            if not existing or existing["kind"] != "structure" or existing["target_id"] != project_id:
                raise ServiceError("Idempotency-Key was already used for another structure request", 409)
            job_id = str(existing["id"])
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "structure", project_id)
        return self.structure_with_job(project_id, user_id)

    def request_story_bible(
        self,
        project_id: str,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """同步执行或创建可恢复的故事资产抽取 Job。

        本地队列默认保持同步预览；BullMQ 生产默认异步。幂等键按用户、项目和
        故事资产任务类型隔离，避免重复抽取或把同一个键误用于其他项目。
        """
        project = self.get_project(project_id, user_id)
        if not self.store.one("SELECT id FROM source_segments WHERE project_id = ? LIMIT 1", (project_id,)) and not str(project.get("story") or "").strip():
            raise ServiceError("import source before extracting story bible", 422)
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        request_key = f"story-bible:{canonical_key}" if canonical_key else None
        if canonical_key:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE user_id = ? AND idempotency_key = ?",
                (user_id, request_key),
            )
            if existing:
                if existing["kind"] != "story-bible" or existing["target_id"] != project_id:
                    raise ServiceError("Idempotency-Key was already used for another story bible request", 409)
                if run_now and existing["status"] == "queued":
                    return self.run_job(str(existing["id"]), user_id)
                if existing["status"] == "queued":
                    self._enqueue_external(existing["id"], user_id, "story-bible", project_id)
                return self.story_bible_with_job(project_id, user_id)
        if run_now and not request_key:
            return self.generate_story_bible(project_id, user_id) | {"job": None}
        if not request_key:
            active = self.store.one(
                "SELECT * FROM jobs WHERE target_id = ? AND user_id = ? AND kind = 'story-bible' AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
                (project_id, user_id),
            )
            if active:
                if active["status"] == "queued":
                    self._enqueue_external(active["id"], user_id, "story-bible", project_id)
                return self.story_bible_with_job(project_id, user_id)
        providers = self._providers_for_user(user_id)
        job_id = new_id("job")
        request_key = request_key or f"story-bible:{job_id}"
        with self.store.connection() as connection:
            inserted = self._insert_job(connection, job_id, user_id, "story-bible", project_id, 0, providers.text.name, request_key)
        if not inserted:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE idempotency_key = ? AND user_id = ?",
                (request_key, user_id),
            )
            if not existing or existing["kind"] != "story-bible" or existing["target_id"] != project_id:
                raise ServiceError("Idempotency-Key was already used for another story bible request", 409)
            job_id = str(existing["id"])
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "story-bible", project_id)
        return self.story_bible_with_job(project_id, user_id)

    def generate_story_bible(self, project_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        """从已导入来源抽取可审核的地点、道具和关系，并保留运行快照。"""
        project = self.get_project(project_id, user_id)
        segments = self.store.all(
            "SELECT id, chapter_no, sequence, text FROM source_segments WHERE project_id = ? ORDER BY chapter_no, sequence LIMIT 240",
            (project_id,),
        )
        context_parts = [
            f"[{row['id']}] 第{row['chapter_no']}章/{row['sequence']}段：{str(row['text'])[:1000]}"
            for row in segments
        ]
        if not context_parts and str(project.get("story") or "").strip():
            context_parts = [f"[project-story]：{str(project['story'])[:12000]}"]
        if not context_parts:
            raise ServiceError("import source before extracting story bible", 422)
        context = "\n".join(context_parts)[:90000]
        source_sha256 = hashlib.sha256(context.encode("utf-8")).hexdigest()
        characters = [dict(row) for row in self.store.all("SELECT id, name, role FROM characters WHERE project_id = ? ORDER BY id", (project_id,))]
        character_names = "、".join(str(item["name"]) for item in characters) or "暂无角色卡，请只抽取地点和道具"
        instruction = (
            "你是小说改编的故事资产编辑。请从用户有权使用的来源文本中抽取可审核的故事资产。"
            "只输出 JSON，不要 Markdown。人物沿用已存在角色卡，不要把人物重复写进 entities。"
            "地点和道具必须是文本中明确出现或可直接归纳的对象，不要臆造。"
            "source_segment_ids 只能填写给出的来源段落 ID，不确定时填空数组。"
            "关系端点使用 source_type/target_type（character 或 entity）和 source_name/target_name，"
            "entity 端点的名称必须对应 entities.name。"
            '\nJSON 结构：{"entities":[{"kind":"location|prop","name":"","description":"","attributes":{},"source_segment_ids":[]}],'
            '"relationships":[{"source_type":"character|entity","source_name":"","target_type":"character|entity","target_name":"","relation":"","description":"","source_segment_ids":[]}]}。'
            f"\n已有角色：{character_names}\n来源段落：\n{context}"
        )
        providers = self._providers_for_user(user_id)
        provider_name = str(getattr(providers.text, "name", "unknown"))
        model_name = str(getattr(providers.text, "model", ""))
        run_id = new_id("story-run")
        try:
            generated = self._generate_text_json(user_id, instruction)
            fallback = generated is None
            if generated is None:
                generated = {"entities": [], "relationships": []}
            if not isinstance(generated, dict):
                raise ServiceError("story bible provider returned an object is required", 502)

            valid_segment_ids = {str(row["id"]) for row in segments}
            entity_items: list[dict[str, Any]] = []
            entity_keys: set[tuple[str, str]] = set()
            warnings: list[str] = []
            raw_entities = generated.get("entities", [])
            if not isinstance(raw_entities, list):
                raw_entities = []
                warnings.append("entities must be an array")
            for item in raw_entities[:96]:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or "").strip().lower()
                name = str(item.get("name") or "").strip()[:120]
                if kind not in {"location", "prop"} or not name:
                    continue
                key = (kind, name.casefold())
                if key in entity_keys:
                    continue
                entity_keys.add(key)
                raw_source_ids = item.get("source_segment_ids")
                source_ids = [str(value) for value in raw_source_ids] if isinstance(raw_source_ids, list) else []
                unknown = [value for value in source_ids if value not in valid_segment_ids]
                if unknown:
                    warnings.append(f"ignored {len(unknown)} unknown source segment ids")
                attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
                try:
                    attributes_json = json.dumps(attributes, ensure_ascii=False)
                except (TypeError, ValueError):
                    attributes = {}
                    attributes_json = "{}"
                    warnings.append(f"ignored invalid attributes for {name}")
                if len(attributes_json.encode("utf-8")) > 8000:
                    attributes = {}
                    warnings.append(f"ignored oversized attributes for {name}")
                entity_items.append({
                    "kind": kind,
                    "name": name,
                    "description": str(item.get("description") or "").strip()[:4000],
                    "attributes": attributes,
                    "source_segment_ids": [value for value in source_ids if value in valid_segment_ids][:32],
                })

            entity_by_name: dict[tuple[str, str], str] = {}
            approved_rows = self.store.all(
                "SELECT id, kind, name FROM story_entities WHERE project_id = ? AND status = 'approved'",
                (project_id,),
            )
            for row in approved_rows:
                entity_by_name[(str(row["kind"]), str(row["name"]).casefold())] = str(row["id"])
            for item in entity_items:
                entity_by_name.setdefault((item["kind"], item["name"].casefold()), "")

            character_by_name = {str(row["name"]).casefold(): str(row["id"]) for row in characters}
            character_ids = {str(row["id"]) for row in characters}
            relationship_items: list[dict[str, Any]] = []
            raw_relationships = generated.get("relationships", [])
            if not isinstance(raw_relationships, list):
                raw_relationships = []
                warnings.append("relationships must be an array")

            def endpoint(endpoint_type: str, name: str, direct_id: str = "") -> tuple[str, str] | None:
                normalized_type = "entity" if endpoint_type in {"location", "prop"} else endpoint_type
                if normalized_type == "character":
                    if direct_id in character_ids:
                        return normalized_type, direct_id
                    found = character_by_name.get(name.casefold())
                    return (normalized_type, found) if found else None
                if normalized_type == "entity":
                    if direct_id and self.store.one("SELECT id FROM story_entities WHERE id = ? AND project_id = ? AND status <> 'superseded'", (direct_id, project_id)):
                        return normalized_type, direct_id
                    for (kind, entity_name), entity_id in entity_by_name.items():
                        if entity_name == name.casefold():
                            if entity_id:
                                return normalized_type, entity_id
                            matching = next((item for item in entity_items if item["kind"] == kind and item["name"].casefold() == entity_name), None)
                            if matching:
                                return normalized_type, f"pending:{matching['kind']}:{matching['name'].casefold()}"
                return None

            for item in raw_relationships[:160]:
                if not isinstance(item, dict):
                    continue
                source_type = str(item.get("source_type") or "").strip().lower()
                target_type = str(item.get("target_type") or "").strip().lower()
                source_name = str(item.get("source_name") or "").strip()[:120]
                target_name = str(item.get("target_name") or "").strip()[:120]
                relation = str(item.get("relation") or "").strip()[:120]
                if not source_name or not target_name or not relation:
                    continue
                source = endpoint(source_type, source_name, str(item.get("source_id") or "").strip())
                target = endpoint(target_type, target_name, str(item.get("target_id") or "").strip())
                if not source or not target:
                    warnings.append(f"ignored unresolved relationship: {source_name} -> {target_name}")
                    continue
                raw_source_ids = item.get("source_segment_ids")
                source_ids = [str(value) for value in raw_source_ids] if isinstance(raw_source_ids, list) else []
                relationship_items.append({
                    "source_type": source[0],
                    "source_ref": source[1],
                    "target_type": target[0],
                    "target_ref": target[1],
                    "relation": relation,
                    "description": str(item.get("description") or "").strip()[:2000],
                    "source_segment_ids": [value for value in source_ids if value in valid_segment_ids][:32],
                })

            normalized_output = {"entities": entity_items, "relationships": relationship_items, "warnings": warnings, "fallback": fallback}
            timestamp = now()
            with self.store.connection() as connection:
                connection.execute(
                    "UPDATE story_entities SET status = 'superseded', updated_at = ? WHERE project_id = ? AND status IN ('draft', 'rejected')",
                    (timestamp, project_id),
                )
                connection.execute(
                    "UPDATE story_relationships SET status = 'superseded', updated_at = ? WHERE project_id = ? AND status IN ('draft', 'rejected')",
                    (timestamp, project_id),
                )
                connection.execute(
                    "INSERT INTO story_bible_runs(id, project_id, provider, model, source_sha256, output_json, status, error, created_at) VALUES (?, ?, ?, ?, ?, ?, 'completed', NULL, ?)",
                    (run_id, project_id, provider_name, model_name, source_sha256, json.dumps(normalized_output, ensure_ascii=False), timestamp),
                )
                for item in entity_items:
                    approved_id = entity_by_name.get((item["kind"], item["name"].casefold()), "")
                    if approved_id:
                        entity_by_name[(item["kind"], item["name"].casefold())] = approved_id
                        continue
                    entity_id = new_id("story-entity")
                    entity_by_name[(item["kind"], item["name"].casefold())] = entity_id
                    connection.execute(
                        "INSERT INTO story_entities(id, project_id, kind, name, description, attributes_json, source_segment_ids_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
                        (entity_id, project_id, item["kind"], item["name"], item["description"], json.dumps(item["attributes"], ensure_ascii=False), json.dumps(item["source_segment_ids"], ensure_ascii=False), timestamp, timestamp),
                    )
                for item in relationship_items:
                    def resolve_reference(endpoint_type: str, reference: str) -> str | None:
                        if endpoint_type == "character":
                            return reference if reference in character_ids else None
                        if reference.startswith("pending:"):
                            _, kind, entity_name = reference.split(":", 2)
                            return entity_by_name.get((kind, entity_name))
                        return reference if self.store.one("SELECT id FROM story_entities WHERE id = ? AND project_id = ? AND status <> 'superseded'", (reference, project_id)) else None
                    source_id = resolve_reference(item["source_type"], item["source_ref"])
                    target_id = resolve_reference(item["target_type"], item["target_ref"])
                    if not source_id or not target_id:
                        warnings.append("relationship endpoint was not persisted")
                        continue
                    connection.execute(
                        "INSERT INTO story_relationships(id, project_id, source_type, source_id, target_type, target_id, relation, description, source_segment_ids_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
                        (new_id("story-rel"), project_id, item["source_type"], source_id, item["target_type"], target_id, item["relation"], item["description"], json.dumps(item["source_segment_ids"], ensure_ascii=False), timestamp, timestamp),
                    )
            return self._story_bible_data(project_id)
        except ServiceError as exc:
            try:
                self.store.write(
                    "INSERT INTO story_bible_runs(id, project_id, provider, model, source_sha256, output_json, status, error, created_at) VALUES (?, ?, ?, ?, ?, '{}', 'failed', ?, ?)",
                    (run_id, project_id, provider_name, model_name, source_sha256, str(exc)[:500], now()),
                )
            except Exception:
                pass
            raise

    def update_story_entity(self, entity_id: str, changes: dict[str, Any], user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        row = self.store.one(
            "SELECT e.* FROM story_entities e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?",
            (entity_id, user_id),
        )
        if not row:
            raise ServiceError("story entity not found", 404)
        allowed: dict[str, Any] = {}
        if "kind" in changes:
            kind = str(changes["kind"] or "").strip().lower()
            if kind not in {"location", "prop"}:
                raise ServiceError("story entity kind must be location or prop")
            allowed["kind"] = kind
        if "name" in changes:
            name = str(changes["name"] or "").strip()
            if not name:
                raise ServiceError("story entity name cannot be empty")
            allowed["name"] = name[:120]
        if "description" in changes:
            allowed["description"] = str(changes["description"] or "").strip()[:4000]
        if "attributes" in changes:
            if not isinstance(changes["attributes"], dict):
                raise ServiceError("story entity attributes must be an object")
            attributes_json = json.dumps(changes["attributes"], ensure_ascii=False)
            if len(attributes_json.encode("utf-8")) > 8000:
                raise ServiceError("story entity attributes are too large", 422)
            allowed["attributes_json"] = attributes_json
        if "source_segment_ids" in changes:
            if not isinstance(changes["source_segment_ids"], list):
                raise ServiceError("source_segment_ids must be an array")
            source_ids = [str(value) for value in changes["source_segment_ids"][:32]]
            valid_source_ids = {
                str(item["id"])
                for item in self.store.all(
                    "SELECT id FROM source_segments WHERE project_id = ? AND id IN ({})".format(
                        ",".join("?" for _ in source_ids) or "NULL"
                    ),
                    (str(row["project_id"]), *source_ids),
                )
            } if source_ids else set()
            if any(source_id not in valid_source_ids for source_id in source_ids):
                raise ServiceError("story entity source segment does not belong to project", 422)
            allowed["source_segment_ids_json"] = json.dumps(source_ids, ensure_ascii=False)
        if "status" in changes:
            status = str(changes["status"] or "").strip()
            if status not in {"draft", "approved", "rejected"}:
                raise ServiceError("invalid story entity status")
            allowed["status"] = status
        if not allowed:
            return self._story_entity_dict(dict(row))
        allowed["updated_at"] = now()
        assignments = ", ".join(f"{key} = ?" for key in allowed)
        with self.store.connection() as connection:
            connection.execute(f"UPDATE story_entities SET {assignments} WHERE id = ?", tuple(allowed.values()) + (entity_id,))
        return self._story_entity_dict(dict(self.store.one("SELECT * FROM story_entities WHERE id = ?", (entity_id,))))

    def update_story_relationship(self, relationship_id: str, changes: dict[str, Any], user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        row = self.store.one(
            "SELECT r.* FROM story_relationships r JOIN projects p ON p.id = r.project_id WHERE r.id = ? AND p.user_id = ?",
            (relationship_id, user_id),
        )
        if not row:
            raise ServiceError("story relationship not found", 404)
        allowed: dict[str, Any] = {}
        if "relation" in changes:
            relation = str(changes["relation"] or "").strip()
            if not relation:
                raise ServiceError("relationship label cannot be empty")
            allowed["relation"] = relation[:120]
        if "description" in changes:
            allowed["description"] = str(changes["description"] or "").strip()[:2000]
        if "status" in changes:
            status = str(changes["status"] or "").strip()
            if status not in {"draft", "approved", "rejected"}:
                raise ServiceError("invalid story relationship status")
            allowed["status"] = status
        if not allowed:
            return self._story_relationship_dict(dict(row))
        allowed["updated_at"] = now()
        assignments = ", ".join(f"{key} = ?" for key in allowed)
        with self.store.connection() as connection:
            connection.execute(f"UPDATE story_relationships SET {assignments} WHERE id = ?", tuple(allowed.values()) + (relationship_id,))
        return self._story_relationship_dict(dict(self.store.one("SELECT * FROM story_relationships WHERE id = ?", (relationship_id,))))

    def update_project(self, project_id: str, changes: dict[str, Any], user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        allowed = {key: changes[key] for key in ("title", "story", "style", "episode_length", "status") if key in changes}
        if not allowed:
            return self.get_project(project_id, user_id)
        if "title" in allowed and not str(allowed["title"]).strip():
            raise ServiceError("title cannot be empty")
        assignments = ", ".join(f"{key} = ?" for key in allowed)
        params = tuple(allowed.values()) + (now(), project_id, user_id)
        with self.store.connection() as connection:
            result = connection.execute(
                f"UPDATE projects SET {assignments}, updated_at = ? WHERE id = ? AND user_id = ?",
                params,
            )
            if result.rowcount == 0:
                raise ServiceError("project not found", 404)
        return self.get_project(project_id, user_id)

    def _source_import_result(
        self,
        project_id: str,
        document: SourceDocument,
        bundle: Any,
        copyright_acknowledged: bool,
        duplicate: bool = False,
    ) -> dict[str, Any]:
        segments = len(bundle.segments)
        adaptation_units = len(bundle.units)
        if duplicate:
            segments = self._count(
                "SELECT COUNT(*) AS count FROM source_segments WHERE source_document_id = ?",
                (document.id,),
            )
            adaptation_units = self._count(
                "SELECT COUNT(*) AS count FROM adaptation_units WHERE project_id = ? AND source_segment_id IN "
                "(SELECT id FROM source_segments WHERE source_document_id = ?)",
                (project_id, document.id),
            )
        return {
            "source_document": {
                "id": document.id,
                "filename": document.filename,
                "media_type": document.media_type,
                "content_sha256": document.content_sha256,
                "copyright_acknowledged": copyright_acknowledged,
            },
            "chapters": [chapter.__dict__ for chapter in bundle.chapters],
            "segments": segments,
            "adaptation_units": adaptation_units,
            "duplicate": duplicate,
        }

    def import_source(
        self,
        project_id: str,
        filename: str,
        text: str,
        mode: str = AdaptationMode.FAITHFUL.value,
        copyright_acknowledged: bool = False,
        user_id: str = DEFAULT_USER_ID,
        media_type: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.get_project(project_id, user_id)
        try:
            adaptation_mode = AdaptationMode(mode)
        except ValueError as exc:
            raise ServiceError(f"unsupported adaptation mode: {mode}") from exc
        try:
            text = ensure_source_text(text)
        except DocumentExtractionError as exc:
            raise ServiceError(str(exc), 413 if "too large" in str(exc) else 422) from exc
        if not text.strip():
            raise ServiceError("source text is required")
        if not copyright_acknowledged:
            raise ServiceError("copyright acknowledgement is required before importing source", 400)
        safe_filename = Path(str(filename or "novel.txt").replace("\\", "/")).name or "novel.txt"
        content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        if canonical_key:
            existing = self.store.one(
                "SELECT id, filename, media_type, content_sha256, text, copyright_acknowledged "
                "FROM source_documents WHERE project_id = ? AND idempotency_key = ?",
                (project_id, canonical_key),
            )
            if existing:
                if str(existing["content_sha256"]) != content_sha256:
                    raise ServiceError("Idempotency-Key was already used for different source content", 409)
                existing_document = SourceDocument(
                    id=str(existing["id"]),
                    filename=str(existing["filename"]),
                    text=str(existing["text"]),
                    content_sha256=str(existing["content_sha256"]),
                    media_type=str(existing["media_type"]),
                )
                existing_bundle = adapt_source(existing_document, adaptation_mode)
                return self._source_import_result(
                    project_id,
                    existing_document,
                    existing_bundle,
                    bool(existing["copyright_acknowledged"]),
                    duplicate=True,
                )
        document = SourceDocument(
            id=new_id("source"),
            filename=safe_filename,
            text=text,
            content_sha256=content_sha256,
            media_type=media_type or media_type_for_filename(safe_filename),
        )
        bundle = adapt_source(document, adaptation_mode)
        try:
            with self.store.connection() as connection:
                connection.execute(
                    "INSERT INTO source_documents(id, project_id, filename, media_type, content_sha256, text, copyright_acknowledged, idempotency_key, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (document.id, project_id, document.filename, document.media_type, document.content_sha256, text, bool(copyright_acknowledged), canonical_key, now()),
                )
                for segment in bundle.segments:
                    connection.execute(
                        "INSERT INTO source_segments(id, project_id, source_document_id, chapter_no, sequence, text, start_offset, end_offset, line_start, line_end) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (segment.id, project_id, document.id, segment.chapter_no, segment.sequence, segment.text, segment.start_offset, segment.end_offset, segment.line_start, segment.line_end),
                    )
                for unit in bundle.units:
                    connection.execute(
                        "INSERT INTO adaptation_units(id, project_id, source_segment_id, chapter_no, sequence, source_text, adapted_text, mode, status, traceability_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)",
                        (unit.id, project_id, unit.source_segment_id, unit.chapter_no, unit.sequence, unit.source_text, unit.adapted_text, unit.mode.value, json.dumps(unit.traceability, ensure_ascii=False)),
                    )
                    self._record_adaptation_revision(
                        connection,
                        unit.id,
                        project_id,
                        unit.source_text,
                        unit.adapted_text,
                        unit.mode.value,
                        "local",
                        {"event": "source_import"},
                    )
                connection.execute("UPDATE projects SET source_document_id = ?, updated_at = ? WHERE id = ?", (document.id, now(), project_id))
        except Exception as exc:
            if canonical_key and ("UNIQUE" in str(exc).upper() or "duplicate key" in str(exc).lower()):
                existing = self.store.one(
                    "SELECT id, filename, media_type, content_sha256, text, copyright_acknowledged "
                    "FROM source_documents WHERE project_id = ? AND idempotency_key = ?",
                    (project_id, canonical_key),
                )
                if existing and str(existing["content_sha256"]) != content_sha256:
                    raise ServiceError("Idempotency-Key was already used for different source content", 409) from exc
                if existing:
                    existing_document = SourceDocument(
                        id=str(existing["id"]),
                        filename=str(existing["filename"]),
                        text=str(existing["text"]),
                        content_sha256=str(existing["content_sha256"]),
                        media_type=str(existing["media_type"]),
                    )
                    return self._source_import_result(
                        project_id,
                        existing_document,
                        adapt_source(existing_document, adaptation_mode),
                        bool(existing["copyright_acknowledged"]),
                        duplicate=True,
                    )
            raise
        return self._source_import_result(project_id, document, bundle, copyright_acknowledged)

    def list_adaptation_units(self, project_id: str, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
        self.get_project(project_id, user_id)
        rows = self.store.all("SELECT * FROM adaptation_units WHERE project_id = ? ORDER BY chapter_no, sequence", (project_id,))
        return [self._adaptation_dict(dict(row)) for row in rows]

    def rewrite_adaptation_units(self, project_id: str, mode: str, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
        self.get_project(project_id, user_id)
        try:
            adaptation_mode = AdaptationMode(mode)
        except ValueError as exc:
            raise ServiceError(f"unsupported adaptation mode: {mode}") from exc
        rows = self.store.all("SELECT * FROM adaptation_units WHERE project_id = ? ORDER BY chapter_no, sequence", (project_id,))
        if not rows:
            raise ServiceError("import source before rewriting adaptation units")
        providers = self._providers_for_user(user_id)
        updates: list[tuple[dict[str, Any], str, str, str, dict[str, Any]]] = []
        for row in rows:
            try:
                adapted_text, provider_meta = providers.text.rewrite(row["source_text"], adaptation_mode.value)
            except ProviderError as exc:
                raise ServiceError(str(exc), 502) from exc
            traceability = json_loads(row["traceability_json"], {})
            traceability.update(
                {
                    "adaptation_provider": provider_meta.get("provider", providers.text.name),
                    "adaptation_preview": provider_meta.get("preview", False),
                    "adaptation_quality": self._adaptation_quality(row["source_text"], adapted_text, adaptation_mode.value),
                }
            )
            updates.append((dict(row), adapted_text, adaptation_mode.value, json.dumps(traceability, ensure_ascii=False), dict(provider_meta)))
        with self.store.connection() as connection:
            for row, adapted_text, mode_value, traceability, provider_meta in updates:
                connection.execute("UPDATE adaptation_units SET adapted_text = ?, mode = ?, status = 'review', traceability_json = ? WHERE id = ?", (adapted_text, mode_value, traceability, row["id"]))
                self._record_adaptation_revision(
                    connection,
                    row["id"],
                    project_id,
                    row["source_text"],
                    adapted_text,
                    mode_value,
                    str(provider_meta.get("provider") or providers.text.name),
                    {"event": "rewrite", "preview": bool(provider_meta.get("preview", False)), "model": provider_meta.get("model")},
                )
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now(), project_id))
        return self.list_adaptation_units(project_id, user_id)

    def request_rewrite(
        self,
        project_id: str,
        mode: str,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """同步执行或创建可恢复的小说改编文本 Job。

        提供幂等键时，文本改编和图片/视频/合成使用同一用户作用域合同：
        同一项目、同一改编模式复用原 Job；跨项目或跨模式复用返回 409。
        """
        self.get_project(project_id, user_id)
        try:
            adaptation_mode = AdaptationMode(mode)
        except ValueError as exc:
            raise ServiceError(f"unsupported adaptation mode: {mode}") from exc
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        request_key = f"rewrite:{adaptation_mode.value}:{canonical_key}" if canonical_key else None
        if canonical_key:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE user_id = ? AND idempotency_key LIKE ? ORDER BY created_at DESC LIMIT 1",
                (user_id, f"rewrite:%:{canonical_key}"),
            )
            if existing:
                existing_parts = str(existing["idempotency_key"] or "").split(":")
                existing_mode = existing_parts[1] if len(existing_parts) >= 3 and existing_parts[0] == "rewrite" else None
                if (
                    existing["kind"] != "text"
                    or existing["target_id"] != project_id
                    or existing_mode != adaptation_mode.value
                ):
                    raise ServiceError("Idempotency-Key was already used for another rewrite request", 409)
                if run_now and existing["status"] == "queued":
                    return self.run_job(str(existing["id"]), user_id)
                if existing["status"] == "queued":
                    self._enqueue_external(existing["id"], user_id, "text", project_id)
                return self.text_job_with_job(project_id, user_id)
        if run_now:
            if not request_key:
                return self.rewrite_adaptation_units(project_id, adaptation_mode.value, user_id)
        rows = self.store.all("SELECT id FROM adaptation_units WHERE project_id = ? ORDER BY chapter_no, sequence", (project_id,))
        if not rows:
            raise ServiceError("import source before rewriting adaptation units")
        if not request_key:
            active = self.store.one(
                "SELECT * FROM jobs WHERE target_id = ? AND user_id = ? AND kind = 'text' AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
                (project_id, user_id),
            )
            if active:
                if active["status"] == "queued":
                    self._enqueue_external(active["id"], user_id, "text", project_id)
                return self.text_job_with_job(project_id, user_id)
        providers = self._providers_for_user(user_id)
        job_id = new_id("job")
        # 无幂等键时只承载内部生成的改编模式和 job id，不接受用户输入，也不包含凭据。
        request_key = request_key or f"rewrite:{adaptation_mode.value}:{job_id}"
        with self.store.connection() as connection:
            inserted = self._insert_job(connection, job_id, user_id, "text", project_id, 0, providers.text.name, request_key)
        if not inserted:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE idempotency_key = ? AND user_id = ?",
                (request_key, user_id),
            )
            if not existing or existing["kind"] != "text" or existing["target_id"] != project_id:
                raise ServiceError("Idempotency-Key was already used for another rewrite request", 409)
            job_id = str(existing["id"])
            if run_now and existing["status"] == "queued":
                return self.run_job(job_id, user_id)
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "text", project_id)
        return self.text_job_with_job(project_id, user_id)

    def update_adaptation_unit(self, unit_id: str, changes: dict[str, Any], user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        row = self.store.one(
            "SELECT a.* FROM adaptation_units a JOIN projects p ON p.id = a.project_id WHERE a.id = ? AND p.user_id = ?",
            (unit_id, user_id),
        )
        if not row:
            raise ServiceError("adaptation unit not found", 404)
        adapted_text = changes.get("adapted_text", row["adapted_text"])
        status = changes.get("status", row["status"])
        allowed_statuses = {"draft", "review", "approved", "rejected"}
        if status not in allowed_statuses:
            raise ServiceError(f"unsupported adaptation review status: {status}")
        traceability = json_loads(row["traceability_json"], {})
        traceability["last_reviewed_at"] = now()
        adapted_text_changed = str(adapted_text) != str(row["adapted_text"])
        traceability["adaptation_quality"] = self._adaptation_quality(str(row["source_text"]), str(adapted_text), str(row["mode"]))
        with self.store.connection() as connection:
            connection.execute(
                "UPDATE adaptation_units SET adapted_text = ?, status = ?, traceability_json = ? WHERE id = ?",
                (str(adapted_text), status, json.dumps(traceability, ensure_ascii=False), unit_id),
            )
            if adapted_text_changed:
                self._record_adaptation_revision(
                    connection,
                    unit_id,
                    row["project_id"],
                    row["source_text"],
                    str(adapted_text),
                    str(row["mode"]),
                    "manual",
                    {"event": "manual_edit"},
                )
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now(), row["project_id"]))
        return self._adaptation_dict(dict(self.store.one("SELECT * FROM adaptation_units WHERE id = ?", (unit_id,))))

    def review_adaptation_units(
        self,
        project_id: str,
        unit_ids: list[str] | None = None,
        status: str = "approved",
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        """批量更新改编单元审核状态，不改写原文或当前改编稿。"""
        self.get_project(project_id, user_id)
        if status not in {"approved", "rejected"}:
            raise ServiceError("bulk adaptation review status must be approved or rejected")
        rows = self.store.all(
            "SELECT * FROM adaptation_units WHERE project_id = ? ORDER BY chapter_no, sequence",
            (project_id,),
        )
        by_id = {str(row["id"]): dict(row) for row in rows}
        if unit_ids is None:
            selected_ids = list(by_id)
        else:
            selected_ids = []
            for raw_id in unit_ids:
                normalized = str(raw_id or "").strip()
                if normalized and normalized not in selected_ids:
                    selected_ids.append(normalized)
        if len(selected_ids) > 2000:
            raise ServiceError("a bulk adaptation review cannot exceed 2000 units")
        if any(unit_id not in by_id for unit_id in selected_ids):
            raise ServiceError("adaptation unit not found", 404)
        timestamp = now()
        with self.store.connection() as connection:
            for unit_id in selected_ids:
                traceability = json_loads(by_id[unit_id]["traceability_json"], {})
                traceability["last_reviewed_at"] = timestamp
                connection.execute(
                    "UPDATE adaptation_units SET status = ?, traceability_json = ? WHERE id = ?",
                    (status, json.dumps(traceability, ensure_ascii=False), unit_id),
                )
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (timestamp, project_id))
        updated = self.list_adaptation_units(project_id, user_id)
        selected = {unit_id for unit_id in selected_ids}
        return {
            "project_id": project_id,
            "status": status,
            "updated": len(selected_ids),
            "units": [unit for unit in updated if unit["id"] in selected],
        }

    def create_characters(self, project_id: str, characters: list[dict[str, Any]] | None = None, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
        project = self.get_project(project_id, user_id)
        if not characters:
            existing = self.store.all("SELECT * FROM characters WHERE project_id = ? ORDER BY id", (project_id,))
            if existing:
                return [self._character_dict(dict(row)) for row in existing]
            generated = self._generate_text_json(
                user_id,
                "你是漫剧角色设定编辑。请根据故事生成 3 到 8 个最重要的角色卡。"
                '这是“输出 JSON 角色”任务，只返回 JSON：{"characters":[{"name":"","role":"protagonist|supporting|antagonist","description":"可用于视觉一致性的外貌、服装和性格锁定"}]}。'
                f"故事：{str(project['story'] or '待输入故事内容')[:6000]}",
            )
            if isinstance(generated, dict):
                generated = generated.get("characters")
            generated_characters: list[dict[str, Any]] = []
            if isinstance(generated, list):
                for item in generated[:8]:
                    if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                        continue
                    generated_characters.append(
                        {
                            "name": str(item["name"]).strip()[:120],
                            "role": str(item.get("role") or "supporting")[:40],
                            "description": str(item.get("description") or "待补充视觉设定")[:2000],
                        }
                    )
            characters = generated_characters or [
                {"name": "女主角", "role": "protagonist", "description": "根据故事梗概待补全的主角视觉设定"},
                {"name": "重要关系人", "role": "supporting", "description": "推动主角选择和冲突的关键关系人"},
                {"name": "主要对手", "role": "antagonist", "description": "制造核心阻力的主要对手"},
            ]
        created = []
        with self.store.connection() as connection:
            for item in characters:
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                character_id = new_id("character")
                visual_lock = item.get("visual_lock") or {"style": project["style"], "status": "needs_reference"}
                connection.execute(
                    "INSERT INTO characters(id, project_id, name, role, description, visual_lock_json, status) VALUES (?, ?, ?, ?, ?, ?, 'draft')",
                    (character_id, project_id, name, item.get("role", "supporting"), item.get("description", ""), json.dumps(visual_lock, ensure_ascii=False)),
                )
                created.append(character_id)
        return [self._character_dict(dict(row)) for row in self.store.all("SELECT * FROM characters WHERE id IN ({})".format(",".join("?" for _ in created)), tuple(created))] if created else []

    def update_character(self, character_id: str, changes: dict[str, Any], user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        """保存人工修订的角色卡；不触碰已生成的母版和资产历史。"""
        row = self.store.one(
            "SELECT c.* FROM characters c JOIN projects p ON p.id = c.project_id WHERE c.id = ? AND p.user_id = ?",
            (character_id, user_id),
        )
        if not row:
            raise ServiceError("character not found", 404)
        allowed: dict[str, Any] = {}
        if "name" in changes:
            name = str(changes["name"] or "").strip()
            if not name:
                raise ServiceError("character name cannot be empty")
            allowed["name"] = name[:120]
        if "role" in changes:
            allowed["role"] = str(changes["role"] or "supporting").strip()[:40] or "supporting"
        if "description" in changes:
            allowed["description"] = str(changes["description"] or "").strip()[:2000]
        if "visual_lock" in changes:
            if not isinstance(changes["visual_lock"], dict):
                raise ServiceError("visual_lock must be an object")
            allowed["visual_lock_json"] = json.dumps(changes["visual_lock"], ensure_ascii=False)
        if not allowed:
            return self.character_detail(dict(row), user_id)
        assignments = ", ".join(f"{key} = ?" for key in allowed)
        with self.store.connection() as connection:
            result = connection.execute(
                f"UPDATE characters SET {assignments} WHERE id = ?",
                tuple(allowed.values()) + (character_id,),
            )
            if result.rowcount == 0:
                raise ServiceError("character not found", 404)
        updated = self.store.one("SELECT * FROM characters WHERE id = ?", (character_id,))
        return self.character_detail(dict(updated), user_id)

    def create_character_reference(
        self,
        character_id: str,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        character = self.store.one(
            "SELECT c.*, p.id AS project_id FROM characters c JOIN projects p ON p.id = c.project_id WHERE c.id = ? AND p.user_id = ?",
            (character_id, user_id),
        )
        if not character:
            raise ServiceError("character not found", 404)
        existing = self.store.one("SELECT * FROM character_references WHERE character_id = ? AND status = 'ready'", (character_id,))
        if existing:
            return self.reference_with_job(existing["id"], user_id)
        pending = self.store.one("SELECT * FROM character_references WHERE character_id = ? AND status IN ('pending', 'generating') ORDER BY id DESC LIMIT 1", (character_id,))
        if pending:
            return self.reference_with_job(pending["id"], user_id)
        providers = self._providers_for_user(user_id)
        reference_id = new_id("reference")
        job_id = new_id("job")
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        provider_name = providers.image.name
        model_name = str(getattr(providers.image, "model", "placeholder"))
        existing_target_id: str | None = None
        with self.store.connection() as connection:
            inserted = self._insert_job(
                connection,
                job_id,
                user_id,
                "character-reference",
                reference_id,
                COSTS["character-reference"],
                provider_name,
                canonical_key,
            )
            if inserted:
                self._reserve(connection, user_id, job_id, COSTS["character-reference"], "character reference generation")
                connection.execute(
                    "INSERT INTO character_references(id, character_id, front_url, side_url, back_url, provider, model, status) VALUES (?, ?, NULL, NULL, NULL, ?, ?, ?)",
                    (reference_id, character_id, provider_name, model_name, "generating" if run_now else "pending"),
                )
            else:
                existing_target_id = self._resolve_character_reference_target(connection, canonical_key, user_id, character_id)
        if existing_target_id:
            self._requeue_existing_external(existing_target_id, user_id, "character-reference")
            return self.reference_with_job(existing_target_id, user_id)
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "character-reference", reference_id)
        return self.reference_with_job(reference_id, user_id)

    def upload_character_reference(
        self,
        character_id: str,
        view: str,
        filename: str,
        data_base64: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        """保存用户提供的角色正/侧/背参考图，不调用模型也不扣积分。"""

        character = self.store.one(
            "SELECT c.*, p.id AS project_id FROM characters c "
            "JOIN projects p ON p.id = c.project_id WHERE c.id = ? AND p.user_id = ?",
            (character_id, user_id),
        )
        if not character:
            raise ServiceError("character not found", 404)
        normalized_view = str(view or "").strip().lower()
        view_columns = {"front": "front_url", "side": "side_url", "back": "back_url"}
        column = view_columns.get(normalized_view)
        if column is None:
            raise ServiceError("reference view must be front, side, or back", 422)

        safe_filename = Path(str(filename or "reference.png")).name.strip()
        extension = Path(safe_filename).suffix.lower()
        if extension == ".jpeg":
            extension = ".jpg"
        if extension not in {".png", ".jpg", ".webp"}:
            raise ServiceError("unsupported reference image extension; use png, jpg, or webp", 422)
        if len(safe_filename) > 240:
            raise ServiceError("reference image filename is too long", 422)

        encoded = str(data_base64 or "").strip()
        if encoded.startswith("data:"):
            _header, separator, encoded = encoded.partition(",")
            if not separator:
                raise ServiceError("reference image data URI is invalid", 422)
        try:
            max_bytes = max(
                1024,
                min(int(os.environ.get("STUDIO_MAX_REFERENCE_IMAGE_BYTES", str(16 * 1024 * 1024))), 64 * 1024 * 1024),
            )
        except ValueError:
            max_bytes = 16 * 1024 * 1024
        if len(encoded) > ((max_bytes + 2) // 3) * 4:
            raise ServiceError("reference image is too large", 413)
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ServiceError("reference image data must be valid base64", 422) from exc
        if not image_bytes:
            raise ServiceError("reference image is empty", 422)
        if len(image_bytes) > max_bytes:
            raise ServiceError("reference image is too large", 413)
        signatures = {
            ".png": image_bytes.startswith(b"\x89PNG\r\n\x1a\n"),
            ".jpg": image_bytes.startswith(b"\xff\xd8\xff"),
            ".webp": len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP",
        }
        if not signatures[extension]:
            raise ServiceError("reference image bytes do not match the declared image type", 422)

        reference = self.store.one(
            "SELECT * FROM character_references WHERE character_id = ? "
            "ORDER BY CASE WHEN status = 'ready' THEN 0 "
            "WHEN status IN ('uploaded', 'pending', 'generating') THEN 1 ELSE 2 END, id DESC LIMIT 1",
            (character_id,),
        )
        if reference:
            active_job = self.store.one(
                "SELECT status FROM jobs WHERE target_id = ? AND kind = 'character-reference' "
                "ORDER BY created_at DESC LIMIT 1",
                (reference["id"],),
            )
            if active_job and str(active_job["status"]) in {"queued", "running"}:
                raise ServiceError("wait for or cancel the current character reference generation before uploading", 409)
            reference_id = str(reference["id"])
            old_url = str(reference[column] or "")
            current_urls = {key: str(reference[f"{key}_url"] or "") for key in view_columns}
        else:
            reference_id = new_id("reference")
            old_url = ""
            current_urls = {key: "" for key in view_columns}

        path = self.asset_dir / f"{reference_id}-{normalized_view}{extension}"
        path.write_bytes(image_bytes)
        if old_url:
            old_path = self.asset_dir / Path(urllib.parse.urlsplit(old_url).path).name
            if old_path != path and old_path.name.startswith(f"{reference_id}-"):
                old_path.unlink(missing_ok=True)
        url = f"/assets/{path.name}"
        if self.storage.mode != "local":
            url = self.storage.put_file(path, f"character-references/{reference_id}/{path.name}")
        current_urls[normalized_view] = url
        complete = all(current_urls.values())
        status = "ready" if complete else "uploaded"
        timestamp = now()
        with self.store.connection() as connection:
            if reference:
                connection.execute(
                    f"UPDATE character_references SET {column} = ?, provider = 'user-upload', model = 'user-upload', status = ? WHERE id = ?",
                    (url, status, reference_id),
                )
            else:
                connection.execute(
                    "INSERT INTO character_references(id, character_id, front_url, side_url, back_url, provider, model, status) "
                    "VALUES (?, ?, ?, ?, ?, 'user-upload', 'user-upload', ?)",
                    (
                        reference_id,
                        character_id,
                        current_urls["front"] or None,
                        current_urls["side"] or None,
                        current_urls["back"] or None,
                        status,
                    ),
                )
            connection.execute("UPDATE characters SET status = ? WHERE id = ?", ("reference_ready" if complete else "draft", character_id))
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (timestamp, character["project_id"]))
        return self.reference_with_job(reference_id, user_id)

    def create_character_references(
        self,
        project_id: str,
        character_ids: list[str] | None = None,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """批量提交一个项目的角色三视图，复用逐角色 Job 和积分合同。

        已经 ready、pending 或 generating 的角色不会重复创建 Job；其余角色在
        提交前做一次总积分预检，避免批量操作只扣到中途才因为余额不足而半截
        失败。逐项结果仍然保留，Provider 部分失败不会掩盖已完成的角色。
        """
        self.get_project(project_id, user_id)
        rows = self.store.all("SELECT * FROM characters WHERE project_id = ? ORDER BY id", (project_id,))
        by_id = {str(row["id"]): dict(row) for row in rows}
        if character_ids is None:
            selected_ids = list(by_id)
        else:
            selected_ids = []
            for raw_id in character_ids:
                normalized = str(raw_id or "").strip()
                if normalized and normalized not in selected_ids:
                    selected_ids.append(normalized)
        if len(selected_ids) > 48:
            raise ServiceError("a character reference batch cannot exceed 48 characters")
        missing = [character_id for character_id in selected_ids if character_id not in by_id]
        if missing:
            raise ServiceError("character not found", 404)

        existing_status: dict[str, str] = {}
        chargeable = 0
        for character_id in selected_ids:
            reference = self.store.one(
                "SELECT status FROM character_references WHERE character_id = ? ORDER BY id DESC LIMIT 1",
                (character_id,),
            )
            if reference:
                status = str(reference["status"])
                existing_status[character_id] = status
                if status not in {"ready", "pending", "generating"}:
                    chargeable += 1
            else:
                chargeable += 1
        required_credits = chargeable * COSTS["character-reference"]
        if required_credits:
            balance = int(self.credits(user_id).get("balance", 0))
            if balance < required_credits:
                raise ServiceError(
                    f"insufficient credits for character reference batch: need {required_credits}, have {balance}",
                    402,
                )

        items: list[dict[str, Any]] = []
        failed = 0
        skipped = 0
        submitted = 0
        for character_id in selected_ids:
            character = by_id[character_id]
            action = "skipped" if character_id in existing_status and existing_status[character_id] in {"ready", "pending", "generating"} else "submitted"
            child_key = f"{idempotency_key}:character:{character_id}" if idempotency_key else None
            try:
                reference = self.create_character_reference(character_id, user_id, run_now, child_key)
                if action == "skipped":
                    skipped += 1
                else:
                    submitted += 1
                items.append(
                    {
                        "character_id": character_id,
                        "character_name": character["name"],
                        "action": action,
                        "status": reference.get("status"),
                        "reference": reference,
                    }
                )
            except ServiceError as exc:
                failed += 1
                items.append(
                    {
                        "character_id": character_id,
                        "character_name": character["name"],
                        "action": "failed",
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        statuses = {str(item.get("status")) for item in items}
        batch_status = "failed" if failed and not submitted and not skipped else "partial" if failed else "queued" if statuses & {"pending", "generating"} else "completed"
        return {
            "project_id": project_id,
            "status": batch_status,
            "requested": len(selected_ids),
            "submitted": submitted,
            "skipped": skipped,
            "failed": failed,
            "required_credits": required_credits,
            "items": items,
        }

    def create_outline(self, project_id: str, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
        project = self.get_project(project_id, user_id)
        target_duration_seconds = episode_length_seconds(project.get("episode_length"))
        existing = self.store.all("SELECT * FROM episodes WHERE project_id = ? ORDER BY number", (project_id,))
        if existing:
            return [self.episode_detail(dict(row)) for row in existing]
        documents = self.store.all("SELECT * FROM source_documents WHERE project_id = ? ORDER BY created_at DESC", (project_id,))
        if documents:
            source_text = documents[0]["text"]
            chapters = adapt_source(SourceDocument(documents[0]["id"], documents[0]["filename"], source_text, documents[0]["content_sha256"])).chapters
        else:
            chapters = []
        episode_specs: list[tuple[int, str, str, str, str, int]] = []
        generated = self._generate_text_json(
            user_id,
            "你是短剧编剧。请把故事整理成可制作的分集大纲。"
            '这是“输出 JSON 分集”任务，只返回 JSON：{"episodes":[{"number":1,"title":"","summary":"","conflict":"","hook":"","target_duration_seconds":60}]}。'
            f"每集必须有明确冲突和结尾钩子，时长限定 15 到 900 秒；项目视觉风格为：{project.get('style') or '国漫写实'}；目标单集时长为：{target_duration_seconds} 秒，未提供时长时使用该目标值。"
            f"故事：{str(source_text if documents else project['story'] or '待输入故事内容')[:12000]}",
        )
        generated_episodes = generated.get("episodes") if isinstance(generated, dict) else generated
        seen_numbers: set[int] = set()
        if isinstance(generated_episodes, list):
            for index, item in enumerate(generated_episodes[:24], 1):
                if not isinstance(item, dict):
                    continue
                try:
                    number = int(item.get("number") or index)
                except (TypeError, ValueError, OverflowError):
                    number = index
                if number <= 0 or number in seen_numbers:
                    number = index
                if number in seen_numbers:
                    continue
                title = str(item.get("title") or f"第{number}集").strip()[:240]
                summary = str(item.get("summary") or "").strip()[:4000]
                if not summary:
                    continue
                conflict = str(item.get("conflict") or "待提炼冲突").strip()[:1000]
                hook = str(item.get("hook") or summary[-120:]).strip()[:1000]
                try:
                    duration = max(15, min(900, int(item.get("target_duration_seconds") or target_duration_seconds)))
                except (TypeError, ValueError, OverflowError):
                    duration = target_duration_seconds
                episode_specs.append((number, title, summary, conflict, hook, duration))
                seen_numbers.add(number)
        if not episode_specs and chapters:
            for chapter in chapters:
                body = chapter.text.strip()
                episode_specs.append((chapter.number or len(episode_specs) + 1, chapter.title, body[:240], "待提炼冲突", body[-120:], target_duration_seconds))
        if not episode_specs:
            story = project["story"] or "待输入故事内容"
            episode_specs.append((1, "第1集", story[:240], "待提炼冲突", story[-120:], target_duration_seconds))
        with self.store.connection() as connection:
            for number, title, summary, conflict, hook, duration in episode_specs:
                connection.execute(
                    "INSERT INTO episodes(id, project_id, number, title, summary, conflict, hook, target_duration_seconds, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft')",
                    (new_id("episode"), project_id, number, title, summary, conflict, hook, duration),
                )
        return [self.episode_detail(dict(row)) for row in self.store.all("SELECT * FROM episodes WHERE project_id = ? ORDER BY number", (project_id,))]

    def update_episode(self, episode_id: str, changes: dict[str, Any], user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        """保存人工修订的分集大纲，保持生成的镜头和素材可追溯。"""
        row = self.store.one(
            "SELECT e.* FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?",
            (episode_id, user_id),
        )
        if not row:
            raise ServiceError("episode not found", 404)
        allowed: dict[str, Any] = {}
        for key, limit in (("title", 240), ("summary", 4000), ("conflict", 1000), ("hook", 1000)):
            if key in changes:
                value = str(changes[key] or "").strip()
                if key == "title" and not value:
                    raise ServiceError("episode title cannot be empty")
                allowed[key] = value[:limit]
        if "target_duration_seconds" in changes:
            try:
                duration = int(changes["target_duration_seconds"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ServiceError("target_duration_seconds must be an integer") from exc
            if duration < 15 or duration > 900:
                raise ServiceError("target_duration_seconds must be between 15 and 900")
            allowed["target_duration_seconds"] = duration
        if not allowed:
            return self.episode_detail(dict(row))
        assignments = ", ".join(f"{key} = ?" for key in allowed)
        with self.store.connection() as connection:
            result = connection.execute(f"UPDATE episodes SET {assignments} WHERE id = ?", tuple(allowed.values()) + (episode_id,))
            if result.rowcount == 0:
                raise ServiceError("episode not found", 404)
        updated = self.store.one("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        return self.episode_detail(dict(updated))

    def _owned_episode(self, episode_id: str, user_id: str = DEFAULT_USER_ID) -> Any:
        row = self.store.one(
            "SELECT e.id, e.project_id FROM episodes e "
            "JOIN projects p ON p.id = e.project_id "
            "WHERE e.id = ? AND p.user_id = ?",
            (episode_id, user_id),
        )
        if not row:
            raise ServiceError("episode not found", 404)
        return row

    @staticmethod
    def _id_list(value: Any, field_name: str, limit: int = 128) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > limit:
            raise ServiceError(f"{field_name} must be a list of at most {limit} ids", 422)
        result: list[str] = []
        for item in value:
            normalized = str(item or "").strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    def _episode_shot_ids(self, episode_id: str, ids: list[str]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        rows = self.store.all(
            f"SELECT id FROM shots WHERE episode_id = ? AND id IN ({placeholders})",
            (episode_id, *ids),
        )
        if len(rows) != len(ids):
            raise ServiceError("shot_ids must reference shots in the same episode", 422)

    @staticmethod
    def _narrative_unit_detail(row: dict[str, Any]) -> dict[str, Any]:
        row["scene_ids"] = json_loads(row.pop("scene_ids_json", "[]"), [])
        row["shot_ids"] = json_loads(row.pop("shot_ids_json", "[]"), [])
        return row

    @staticmethod
    def _scene_detail(row: dict[str, Any]) -> dict[str, Any]:
        row["shot_ids"] = json_loads(row.pop("shot_ids_json", "[]"), [])
        return row

    def list_narrative_units(self, episode_id: str, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
        self._owned_episode(episode_id, user_id)
        return [
            self._narrative_unit_detail(dict(row))
            for row in self.store.all(
                "SELECT * FROM narrative_units WHERE episode_id = ? ORDER BY created_at, id",
                (episode_id,),
            )
        ]

    def get_narrative_unit(self, unit_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        row = self.store.one(
            "SELECT n.* FROM narrative_units n JOIN projects p ON p.id = n.project_id "
            "WHERE n.id = ? AND p.user_id = ?",
            (unit_id, user_id),
        )
        if not row:
            raise ServiceError("narrative unit not found", 404)
        return self._narrative_unit_detail(dict(row))

    def create_narrative_unit(
        self,
        episode_id: str,
        payload: dict[str, Any] | None = None,
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        episode = self._owned_episode(episode_id, user_id)
        data = payload or {}
        scene_ids = self._id_list(data.get("scene_ids"), "scene_ids")
        shot_ids = self._id_list(data.get("shot_ids"), "shot_ids")
        if scene_ids:
            placeholders = ",".join("?" for _ in scene_ids)
            rows = self.store.all(
                f"SELECT id FROM scenes WHERE episode_id = ? AND id IN ({placeholders})",
                (episode_id, *scene_ids),
            )
            if len(rows) != len(scene_ids):
                raise ServiceError("scene_ids must reference scenes in the same episode", 422)
        self._episode_shot_ids(episode_id, shot_ids)
        try:
            target_duration = int(data.get("target_duration_seconds") or 0)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ServiceError("target_duration_seconds must be an integer", 422) from exc
        if target_duration < 0 or target_duration > 900:
            raise ServiceError("target_duration_seconds must be between 0 and 900", 422)
        unit_id = new_id("unit")
        timestamp = now()
        values = (
            unit_id,
            episode["project_id"],
            episode_id,
            str(data.get("goal") or "").strip()[:2000],
            str(data.get("enter_state") or "").strip()[:2000],
            str(data.get("exit_state") or "").strip()[:2000],
            target_duration,
            json.dumps(scene_ids, ensure_ascii=False),
            json.dumps(shot_ids, ensure_ascii=False),
            timestamp,
            timestamp,
        )
        with self.store.connection() as connection:
            connection.execute(
                "INSERT INTO narrative_units(id, project_id, episode_id, goal, enter_state, exit_state, "
                "target_duration_seconds, scene_ids_json, shot_ids_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
        return self.get_narrative_unit(unit_id, user_id)

    def update_narrative_unit(
        self,
        unit_id: str,
        changes: dict[str, Any],
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        row = self.store.one(
            "SELECT n.* FROM narrative_units n JOIN projects p ON p.id = n.project_id "
            "WHERE n.id = ? AND p.user_id = ?",
            (unit_id, user_id),
        )
        if not row:
            raise ServiceError("narrative unit not found", 404)
        allowed: dict[str, Any] = {}
        for key, limit in (("goal", 2000), ("enter_state", 2000), ("exit_state", 2000)):
            if key in changes:
                allowed[key] = str(changes[key] or "").strip()[:limit]
        if "target_duration_seconds" in changes:
            try:
                target_duration = int(changes["target_duration_seconds"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ServiceError("target_duration_seconds must be an integer", 422) from exc
            if target_duration < 0 or target_duration > 900:
                raise ServiceError("target_duration_seconds must be between 0 and 900", 422)
            allowed["target_duration_seconds"] = target_duration
        episode_id = str(row["episode_id"])
        if "scene_ids" in changes:
            scene_ids = self._id_list(changes["scene_ids"], "scene_ids")
            if scene_ids:
                placeholders = ",".join("?" for _ in scene_ids)
                scene_rows = self.store.all(
                    f"SELECT id FROM scenes WHERE episode_id = ? AND id IN ({placeholders})",
                    (episode_id, *scene_ids),
                )
                if len(scene_rows) != len(scene_ids):
                    raise ServiceError("scene_ids must reference scenes in the same episode", 422)
            allowed["scene_ids_json"] = json.dumps(scene_ids, ensure_ascii=False)
        if "shot_ids" in changes:
            shot_ids = self._id_list(changes["shot_ids"], "shot_ids")
            self._episode_shot_ids(episode_id, shot_ids)
            allowed["shot_ids_json"] = json.dumps(shot_ids, ensure_ascii=False)
        if not allowed:
            return self._narrative_unit_detail(dict(row))
        allowed["updated_at"] = now()
        assignments = ", ".join(f"{key} = ?" for key in allowed)
        with self.store.connection() as connection:
            result = connection.execute(
                f"UPDATE narrative_units SET {assignments} WHERE id = ?",
                tuple(allowed.values()) + (unit_id,),
            )
            if result.rowcount == 0:
                raise ServiceError("narrative unit not found", 404)
        return self.get_narrative_unit(unit_id, user_id)

    def list_scenes(self, episode_id: str, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
        self._owned_episode(episode_id, user_id)
        return [
            self._scene_detail(dict(row))
            for row in self.store.all(
                "SELECT * FROM scenes WHERE episode_id = ? ORDER BY created_at, id",
                (episode_id,),
            )
        ]

    def get_scene(self, scene_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        row = self.store.one(
            "SELECT s.* FROM scenes s JOIN projects p ON p.id = s.project_id "
            "WHERE s.id = ? AND p.user_id = ?",
            (scene_id, user_id),
        )
        if not row:
            raise ServiceError("scene not found", 404)
        return self._scene_detail(dict(row))

    def _sync_scene_membership(self, connection: Any, unit_id: str | None, scene_id: str, add: bool) -> None:
        if not unit_id:
            return
        row = connection.execute("SELECT scene_ids_json FROM narrative_units WHERE id = ?", (unit_id,)).fetchone()
        if not row:
            return
        scene_ids = json_loads(row["scene_ids_json"], [])
        if not isinstance(scene_ids, list):
            scene_ids = []
        scene_ids = [str(value) for value in scene_ids if str(value)]
        if add and scene_id not in scene_ids:
            scene_ids.append(scene_id)
        if not add:
            scene_ids = [value for value in scene_ids if value != scene_id]
        connection.execute(
            "UPDATE narrative_units SET scene_ids_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(scene_ids, ensure_ascii=False), now(), unit_id),
        )

    def create_scene(
        self,
        episode_id: str,
        payload: dict[str, Any] | None = None,
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        episode = self._owned_episode(episode_id, user_id)
        data = payload or {}
        shot_ids = self._id_list(data.get("shot_ids"), "shot_ids")
        self._episode_shot_ids(episode_id, shot_ids)
        unit_id = str(data.get("unit_id") or "").strip() or None
        if unit_id:
            unit = self.store.one(
                "SELECT id FROM narrative_units WHERE id = ? AND episode_id = ? AND project_id = ?",
                (unit_id, episode_id, episode["project_id"]),
            )
            if not unit:
                raise ServiceError("unit_id must reference a narrative unit in the same episode", 422)
        scene_id = new_id("scene")
        timestamp = now()
        with self.store.connection() as connection:
            connection.execute(
                "INSERT INTO scenes(id, project_id, episode_id, unit_id, location_id, name, time_of_day, weather, summary, "
                "shot_ids_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scene_id,
                    episode["project_id"],
                    episode_id,
                    unit_id,
                    str(data.get("location_id") or "").strip()[:200] or None,
                    str(data.get("name") or "").strip()[:500],
                    str(data.get("time_of_day") or "").strip()[:120],
                    str(data.get("weather") or "").strip()[:120],
                    str(data.get("summary") or "").strip()[:4000],
                    json.dumps(shot_ids, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            self._sync_scene_membership(connection, unit_id, scene_id, True)
        return self.get_scene(scene_id, user_id)

    def update_scene(
        self,
        scene_id: str,
        changes: dict[str, Any],
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        row = self.store.one(
            "SELECT s.* FROM scenes s JOIN projects p ON p.id = s.project_id "
            "WHERE s.id = ? AND p.user_id = ?",
            (scene_id, user_id),
        )
        if not row:
            raise ServiceError("scene not found", 404)
        allowed: dict[str, Any] = {}
        for key, limit in (("location_id", 200), ("name", 500), ("time_of_day", 120), ("weather", 120), ("summary", 4000)):
            if key in changes:
                allowed[key] = str(changes[key] or "").strip()[:limit] or (None if key == "location_id" else "")
        episode_id = str(row["episode_id"])
        project_id = str(row["project_id"])
        if "shot_ids" in changes:
            shot_ids = self._id_list(changes["shot_ids"], "shot_ids")
            self._episode_shot_ids(episode_id, shot_ids)
            allowed["shot_ids_json"] = json.dumps(shot_ids, ensure_ascii=False)
        old_unit_id = str(row["unit_id"] or "") or None
        new_unit_id = old_unit_id
        if "unit_id" in changes:
            new_unit_id = str(changes["unit_id"] or "").strip() or None
            if new_unit_id:
                unit = self.store.one(
                    "SELECT id FROM narrative_units WHERE id = ? AND episode_id = ? AND project_id = ?",
                    (new_unit_id, episode_id, project_id),
                )
                if not unit:
                    raise ServiceError("unit_id must reference a narrative unit in the same episode", 422)
            allowed["unit_id"] = new_unit_id
        if not allowed:
            return self._scene_detail(dict(row))
        allowed["updated_at"] = now()
        assignments = ", ".join(f"{key} = ?" for key in allowed)
        with self.store.connection() as connection:
            result = connection.execute(
                f"UPDATE scenes SET {assignments} WHERE id = ?",
                tuple(allowed.values()) + (scene_id,),
            )
            if result.rowcount == 0:
                raise ServiceError("scene not found", 404)
            if old_unit_id != new_unit_id:
                self._sync_scene_membership(connection, old_unit_id, scene_id, False)
                self._sync_scene_membership(connection, new_unit_id, scene_id, True)
        return self.get_scene(scene_id, user_id)

    def _approved_story_context(self, project_id: str) -> str:
        """将已审核故事资产压缩成下游生成可消费的稳定设定。"""

        entities = self.store.all(
            "SELECT id, kind, name, description, attributes_json FROM story_entities "
            "WHERE project_id = ? AND status = 'approved' ORDER BY kind, name, id LIMIT 96",
            (project_id,),
        )
        relationships = self.store.all(
            "SELECT source_type, source_id, target_type, target_id, relation, description "
            "FROM story_relationships WHERE project_id = ? AND status = 'approved' "
            "ORDER BY created_at, id LIMIT 160",
            (project_id,),
        )
        if not entities and not relationships:
            return "暂无已审核故事资产；不得凭空补写未审核地点、道具或关系。"

        labels: dict[tuple[str, str], str] = {}
        for row in self.store.all("SELECT id, name FROM characters WHERE project_id = ?", (project_id,)):
            labels[("character", str(row["id"]))] = str(row["name"])
        for row in entities:
            labels[("entity", str(row["id"]))] = str(row["name"])

        parts: list[str] = []
        for row in entities:
            kind = "地点" if str(row["kind"]) == "location" else "道具"
            attributes = json_loads(row["attributes_json"], {})
            attribute_text = json.dumps(attributes, ensure_ascii=False, separators=(",", ":")) if isinstance(attributes, dict) and attributes else ""
            detail = f"{kind}{row['name']}：{str(row['description'] or '').strip()}"
            if attribute_text:
                detail += f"；属性={attribute_text[:500]}"
            parts.append(detail[:900])
        for row in relationships:
            source = labels.get((str(row["source_type"]), str(row["source_id"])), str(row["source_id"]))
            target = labels.get((str(row["target_type"]), str(row["target_id"])), str(row["target_id"]))
            relation = str(row["relation"] or "").strip()
            description = str(row["description"] or "").strip()
            detail = f"关系：{source}—{relation}—{target}"
            if description:
                detail += f"（{description}）"
            parts.append(detail[:700])
        return "；".join(parts)[:4200]

    def create_shots(self, episode_id: str, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
        episode = self.store.one("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        if not episode:
            raise ServiceError("episode not found", 404)
        project = self.store.one("SELECT * FROM projects WHERE id = ? AND user_id = ?", (episode["project_id"], user_id))
        if not project:
            raise ServiceError("episode not found", 404)
        project_id = str(project["id"])
        adaptation_revision = self._adaptation_content_revision(project_id)
        existing = self.store.all(
            "SELECT * FROM shots WHERE episode_id = ? AND COALESCE(status, 'active') <> 'superseded' ORDER BY sequence",
            (episode_id,),
        )
        if existing and all(str(row["adaptation_revision"] or "") == adaptation_revision for row in existing):
            return [self.shot_detail(dict(row)) for row in existing]
        units = self.store.all("SELECT * FROM adaptation_units WHERE project_id = ? AND chapter_no = ? ORDER BY sequence", (project_id, episode["number"]))
        story_context = self._approved_story_context(project_id)
        generated = self._generate_text_json(
            user_id,
            "你是漫剧分镜师。请将这一集拆成适合图生视频的镜头，保持事件顺序，每个镜头只表达一个主要动作。"
            '这是“输出 JSON 分镜”任务，只返回 JSON：{"shots":[{"sequence":1,"scene":"","emotion":"","duration_seconds":3,"description":"","adaptation_unit_sequences":[1]}]}。'
            f"分集：{episode['title']}；摘要：{episode['summary']}；冲突：{episode['conflict']}；钩子：{episode['hook']}。"
            f"已审核故事资产与关系：{story_context}。只能使用这些已审核设定，不能把草稿设定当作确定事实。"
            f"可追溯改编单元：{json.dumps([{'sequence': unit['sequence'], 'text': unit['adapted_text']} for unit in units], ensure_ascii=False)[:10000]}",
        )
        generated_shots = generated.get("shots") if isinstance(generated, dict) else generated
        unit_ids_by_sequence = {int(unit["sequence"]): unit["id"] for unit in units}
        descriptions: list[tuple[list[str], str, str, str, float]] = []
        if isinstance(generated_shots, list):
            for item in generated_shots[:48]:
                if not isinstance(item, dict):
                    continue
                description = str(item.get("description") or "").strip()[:4000]
                if not description:
                    continue
                adaptation_ids: list[str] = []
                raw_sequences = item.get("adaptation_unit_sequences") or item.get("adaptation_unit_ids") or []
                if isinstance(raw_sequences, list):
                    for value in raw_sequences:
                        try:
                            unit_id = unit_ids_by_sequence.get(int(value))
                        except (TypeError, ValueError, OverflowError):
                            unit_id = None
                        if unit_id and unit_id not in adaptation_ids:
                            adaptation_ids.append(unit_id)
                try:
                    duration = float(item.get("duration_seconds") or 3)
                    if not math.isfinite(duration):
                        raise ValueError
                    duration = max(1.0, min(30.0, duration))
                except (TypeError, ValueError):
                    duration = 3.0
                descriptions.append(
                    (
                        adaptation_ids,
                        str(item.get("scene") or "待确定场景").strip()[:500],
                        str(item.get("emotion") or "待确定情绪").strip()[:500],
                        description,
                        duration,
                    )
                )
        if not descriptions:
            descriptions = [
                ([unit["id"]] if unit["id"] else [], "待确定场景", "待确定情绪", unit["adapted_text"], 3.0)
                for unit in units
            ]
        if not descriptions:
            text = episode["summary"] or "待补充分镜内容"
            descriptions = [([], "待确定场景", "待确定情绪", sentence.strip(), 3.0) for sentence in re.split(r"(?<=[。！？!?])", text) if sentence.strip()]
        existing_by_sequence = {int(row["sequence"]): dict(row) for row in existing}
        used_sequences: set[int] = set()
        with self.store.connection() as connection:
            for sequence, (adaptation_ids, scene, emotion, description, duration) in enumerate(descriptions, 1):
                old = existing_by_sequence.get(sequence)
                if old:
                    connection.execute(
                        "UPDATE shots SET scene = ?, emotion = ?, duration_seconds = ?, description = ?, "
                        "adaptation_unit_ids_json = ?, image_prompt_id = NULL, adaptation_revision = ?, status = 'active' "
                        "WHERE id = ?",
                        (
                            scene,
                            emotion,
                            duration,
                            description,
                            json.dumps(adaptation_ids, ensure_ascii=False),
                            adaptation_revision,
                            old["id"],
                        ),
                    )
                    used_sequences.add(sequence)
                else:
                    connection.execute(
                        "INSERT INTO shots(id, episode_id, sequence, scene, emotion, duration_seconds, description, "
                        "adaptation_unit_ids_json, adaptation_revision, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')",
                        (
                            new_id("shot"),
                            episode_id,
                            sequence,
                            scene,
                            emotion,
                            duration,
                            description,
                            json.dumps(adaptation_ids, ensure_ascii=False),
                            adaptation_revision,
                        ),
                    )
                    used_sequences.add(sequence)
            for old in existing:
                if int(old["sequence"]) not in used_sequences:
                    connection.execute(
                        "UPDATE shots SET status = 'superseded', image_prompt_id = NULL WHERE id = ?",
                        (old["id"],),
                    )
        return [
            self.shot_detail(dict(row))
            for row in self.store.all(
                "SELECT * FROM shots WHERE episode_id = ? AND COALESCE(status, 'active') <> 'superseded' ORDER BY sequence",
                (episode_id,),
            )
        ]

    def update_shot(self, shot_id: str, changes: dict[str, Any], user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        """保存人工修订的分镜；镜头语义变化会清除旧提示词引用，但保留旧资产。"""
        row = self.store.one(
            "SELECT s.* FROM shots s JOIN episodes e ON e.id = s.episode_id JOIN projects p ON p.id = e.project_id WHERE s.id = ? AND p.user_id = ?",
            (shot_id, user_id),
        )
        if not row:
            raise ServiceError("shot not found", 404)
        allowed: dict[str, Any] = {}
        for key, limit in (("scene", 500), ("emotion", 500), ("description", 4000)):
            if key in changes:
                allowed[key] = str(changes[key] or "").strip()[:limit]
        if "duration_seconds" in changes:
            try:
                duration = float(changes["duration_seconds"])
            except (TypeError, ValueError) as exc:
                raise ServiceError("duration_seconds must be a number") from exc
            if not math.isfinite(duration) or duration < 1 or duration > 30:
                raise ServiceError("duration_seconds must be between 1 and 30")
            allowed["duration_seconds"] = duration
        if "adaptation_unit_ids" in changes:
            raw_ids = changes["adaptation_unit_ids"]
            if not isinstance(raw_ids, list):
                raise ServiceError("adaptation_unit_ids must be an array")
            project_row = self.store.one("SELECT project_id FROM episodes WHERE id = ?", (row["episode_id"],))
            unique_ids: list[str] = []
            for value in raw_ids:
                value = str(value).strip()
                if value and value not in unique_ids:
                    unique_ids.append(value)
            if unique_ids:
                placeholders = ",".join("?" for _ in unique_ids)
                valid = self.store.all(
                    f"SELECT id FROM adaptation_units WHERE project_id = ? AND id IN ({placeholders})",
                    (project_row["project_id"], *unique_ids),
                )
                if len(valid) != len(unique_ids):
                    raise ServiceError("adaptation_unit_ids contains an unknown unit")
            allowed["adaptation_unit_ids_json"] = json.dumps(unique_ids, ensure_ascii=False)
        semantic_changed = any(key in allowed for key in ("scene", "emotion", "description"))
        if semantic_changed:
            allowed["image_prompt_id"] = None
        if not allowed:
            return self.shot_detail(dict(row))
        assignments = ", ".join(f"{key} = ?" for key in allowed)
        with self.store.connection() as connection:
            result = connection.execute(f"UPDATE shots SET {assignments} WHERE id = ?", tuple(allowed.values()) + (shot_id,))
            if result.rowcount == 0:
                raise ServiceError("shot not found", 404)
        updated = self.store.one("SELECT * FROM shots WHERE id = ?", (shot_id,))
        return self.shot_detail(dict(updated))

    def prompt_with_job(self, shot_id: str, user_id: str | None = None) -> dict[str, Any]:
        """返回镜头提示词和最近一次提示词 Job，供同步/异步入口共用。"""
        if user_id is None:
            shot = self.store.one("SELECT s.id, s.image_prompt_id, e.project_id FROM shots s JOIN episodes e ON e.id = s.episode_id WHERE s.id = ?", (shot_id,))
        else:
            shot = self.store.one(
                "SELECT s.id, s.image_prompt_id, e.project_id FROM shots s JOIN episodes e ON e.id = s.episode_id JOIN projects p ON p.id = e.project_id WHERE s.id = ? AND p.user_id = ?",
                (shot_id, user_id),
            )
        if not shot:
            raise ServiceError("shot not found", 404)
        prompt = self.store.one(
            "SELECT * FROM image_prompts WHERE id = ?",
            (shot["image_prompt_id"],),
        ) if shot["image_prompt_id"] else None
        result = dict(prompt) if prompt else {
            "id": None,
            "shot_id": shot_id,
            "prompt": "",
            "negative_prompt": "",
            "provider": "",
            "model": "",
        }
        job = self.store.one(
            "SELECT * FROM jobs WHERE target_id = ? AND user_id = ? AND kind = 'prompt' ORDER BY created_at DESC LIMIT 1",
            (str(shot["id"]), str(user_id or self.store.one("SELECT user_id FROM projects WHERE id = ?", (shot["project_id"],))["user_id"])),
        )
        result["job"] = dict(job) if job else None
        return result

    def request_prompt_generation(
        self,
        shot_id: str,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """同步生成或创建可恢复的图片提示词 Job。"""
        shot = self.store.one(
            "SELECT s.id, e.project_id FROM shots s JOIN episodes e ON e.id = s.episode_id JOIN projects p ON p.id = e.project_id WHERE s.id = ? AND p.user_id = ?",
            (shot_id, user_id),
        )
        if not shot:
            raise ServiceError("shot not found", 404)
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        request_key = f"prompt:{canonical_key}" if canonical_key else None
        if canonical_key:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE user_id = ? AND idempotency_key = ?",
                (user_id, request_key),
            )
            if existing:
                if existing["kind"] != "prompt" or existing["target_id"] != shot_id:
                    raise ServiceError("Idempotency-Key was already used for another prompt request", 409)
                if run_now and existing["status"] == "queued":
                    return self.run_job(str(existing["id"]), user_id)
                if existing["status"] == "queued":
                    self._enqueue_external(existing["id"], user_id, "prompt", shot_id)
                return self.prompt_with_job(shot_id, user_id)
        if run_now and not request_key:
            return self.create_prompts(shot_id, user_id) | {"job": None}
        if not request_key:
            active = self.store.one(
                "SELECT * FROM jobs WHERE target_id = ? AND user_id = ? AND kind = 'prompt' AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
                (shot_id, user_id),
            )
            if active:
                if active["status"] == "queued":
                    self._enqueue_external(active["id"], user_id, "prompt", shot_id)
                return self.prompt_with_job(shot_id, user_id)
        providers = self._providers_for_user(user_id)
        job_id = new_id("job")
        request_key = request_key or f"prompt:{job_id}"
        with self.store.connection() as connection:
            inserted = self._insert_job(connection, job_id, user_id, "prompt", shot_id, 0, providers.text.name, request_key)
        if not inserted:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE idempotency_key = ? AND user_id = ?",
                (request_key, user_id),
            )
            if not existing or existing["kind"] != "prompt" or existing["target_id"] != shot_id:
                raise ServiceError("Idempotency-Key was already used for another prompt request", 409)
            job_id = str(existing["id"])
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "prompt", shot_id)
        return self.prompt_with_job(shot_id, user_id)

    def create_prompts(self, shot_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        shot = self.store.one("SELECT * FROM shots WHERE id = ?", (shot_id,))
        if not shot:
            raise ServiceError("shot not found", 404)
        project = self.store.one(
            "SELECT p.* FROM projects p JOIN episodes e ON e.project_id = p.id JOIN shots s ON s.episode_id = e.id WHERE s.id = ? AND p.user_id = ?",
            (shot_id, user_id),
        )
        if not project:
            raise ServiceError("shot not found", 404)
        self._require_current_shot_revision(shot, user_id, "prompt generation")
        characters = self.store.all("SELECT name, description, visual_lock_json FROM characters WHERE project_id = ? ORDER BY id", (project["id"],))
        lock_parts: list[str] = []
        for character in characters:
            visual_lock = json_loads(character["visual_lock_json"], {})
            lock_text = str(visual_lock.get("prompt") or character["description"] or "待补充外貌").strip()
            lock_parts.append(f"{character['name']}：{lock_text}")
        character_lock = "；".join(lock_parts)[:720] or "角色外貌待补充，避免无关人物和重复人物"
        story_context = self._approved_story_context(str(project["id"]))
        prompt = (
            f"{project['style']}，东亚黑白漫画，2D comic，G笔线条，{shot['scene']}，{shot['description']}，"
            f"镜头情绪：{shot['emotion']}。角色锁定：{character_lock}。已审核故事资产：{story_context}。"
            "保持角色、服装、道具和空间连续性，不使用未审核设定。"
        )
        negative = "低清晰度，重复人物，畸形手指，水印，可读文字，写实照片，真人脸，3D渲染，live-action"
        generated = self._generate_text_json(
            user_id,
            "你是漫剧提示词设计师。请把镜头和角色锁定整理成适用于图像模型的提示词。"
            '这是“输出 JSON 提示词”任务，只返回 JSON：{"prompt":"","negative_prompt":""}。'
            f"风格：{project['style']}；场景：{shot['scene']}；镜头描述：{shot['description']}；情绪：{shot['emotion']}；角色锁定：{character_lock}；"
            f"已审核故事资产与关系：{story_context}。不得把草稿或未审核设定写入提示词。",
        )
        if isinstance(generated, dict) and str(generated.get("prompt") or "").strip():
            prompt = str(generated["prompt"]).strip()[:6000]
            generated_negative = str(generated.get("negative_prompt") or "").strip()
            if generated_negative:
                negative = generated_negative[:2000]
            for constraint in ("角色锁定", "服装", "空间连续性"):
                if constraint not in prompt:
                    prompt += f"；必须保持{constraint}"
            for constraint in ("低清晰度", "重复人物", "可读文字"):
                if constraint not in negative:
                    negative += f"，{constraint}"
            if story_context not in prompt:
                story_constraint = f"；已审核故事资产约束：{story_context}"
                prompt = prompt[: max(0, 6000 - len(story_constraint))] + story_constraint
            else:
                prompt = prompt[:6000]
        prompt_id = shot["image_prompt_id"] or new_id("prompt")
        with self.store.connection() as connection:
            if shot["image_prompt_id"]:
                connection.execute("UPDATE image_prompts SET prompt = ?, negative_prompt = ? WHERE id = ?", (prompt, negative, prompt_id))
            else:
                connection.execute("INSERT INTO image_prompts(id, shot_id, prompt, negative_prompt) VALUES (?, ?, ?, ?)", (prompt_id, shot_id, prompt, negative))
                connection.execute("UPDATE shots SET image_prompt_id = ? WHERE id = ?", (prompt_id, shot_id))
        return self.prompt_detail(dict(self.store.one("SELECT * FROM image_prompts WHERE id = ?", (prompt_id,))))

    def update_image_prompt(self, prompt_id: str, changes: dict[str, Any], user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        """保存人工修订的正/负面提示词，并按项目归属隔离。"""
        row = self.store.one(
            "SELECT ip.* FROM image_prompts ip JOIN shots s ON s.id = ip.shot_id JOIN episodes e ON e.id = s.episode_id JOIN projects p ON p.id = e.project_id WHERE ip.id = ? AND p.user_id = ?",
            (prompt_id, user_id),
        )
        if not row:
            raise ServiceError("image prompt not found", 404)
        allowed: dict[str, str] = {}
        if "prompt" in changes:
            prompt = str(changes["prompt"] or "").strip()
            if not prompt:
                raise ServiceError("prompt cannot be empty")
            allowed["prompt"] = prompt[:6000]
        if "negative_prompt" in changes:
            allowed["negative_prompt"] = str(changes["negative_prompt"] or "").strip()[:2000]
        if not allowed:
            return self.prompt_detail(dict(row))
        assignments = ", ".join(f"{key} = ?" for key in allowed)
        with self.store.connection() as connection:
            result = connection.execute(f"UPDATE image_prompts SET {assignments} WHERE id = ?", tuple(allowed.values()) + (prompt_id,))
            if result.rowcount == 0:
                raise ServiceError("image prompt not found", 404)
        return self.prompt_detail(dict(self.store.one("SELECT * FROM image_prompts WHERE id = ?", (prompt_id,))))

    def create_image_asset(
        self,
        shot_id: str,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        shot = self.store.one("SELECT * FROM shots WHERE id = ?", (shot_id,))
        if not shot:
            raise ServiceError("shot not found", 404)
        project = self.store.one("SELECT p.* FROM projects p JOIN episodes e ON e.project_id = p.id WHERE e.id = ? AND p.user_id = ?", (self.store.one("SELECT episode_id FROM shots WHERE id = ?", (shot_id,))["episode_id"], user_id))
        if not project:
            raise ServiceError("shot not found", 404)
        self._require_current_shot_revision(shot, user_id, "image generation")
        prompt = self.store.one(
            "SELECT * FROM image_prompts WHERE id = ?",
            (shot["image_prompt_id"],),
        ) if shot["image_prompt_id"] else None
        if not prompt and run_now:
            prompt = self.store.one("SELECT * FROM image_prompts WHERE id = ?", (self.create_prompts(shot_id, user_id)["id"],))
        asset_id = new_id("asset")
        job_id = new_id("job")
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        existing_target_id: str | None = None
        cost = COSTS["image"]
        timestamp = now()
        providers = self._providers_for_user(user_id)
        artifact_revision = self._shot_artifact_revision(
            self.store.one("SELECT * FROM shots WHERE id = ?", (shot_id,)),
            prompt,
        )
        with self.store.connection() as connection:
            inserted = self._insert_job(connection, job_id, user_id, "image", asset_id, cost, providers.image.name, canonical_key)
            if inserted:
                self._reserve(connection, user_id, job_id, cost, "image generation")
                connection.execute(
                    "INSERT INTO assets(id, project_id, shot_id, kind, status, provider, model, metadata_json, created_at, updated_at) VALUES (?, ?, ?, 'image', ?, ?, ?, ?, ?, ?)",
                    (
                        asset_id,
                        project["id"],
                        shot_id,
                        "generating" if run_now else "pending",
                        providers.image.name,
                        str(getattr(providers.image, "model", "placeholder")),
                        json.dumps(
                            {
                                "prompt_id": prompt["id"] if prompt else None,
                                "mode": providers.image.name,
                                "artifact_revision": artifact_revision,
                            }
                        ),
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                existing_target_id = self._resolve_idempotent_target(connection, canonical_key, user_id, "image", shot_id)
        if existing_target_id:
            self._requeue_existing_external(existing_target_id, user_id, "image")
            return self.asset_with_job(existing_target_id, user_id)
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "image", asset_id)
        return self.asset_with_job(asset_id, user_id)

    def create_image_assets(
        self,
        project_id: str,
        shot_ids: list[str] | None = None,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """批量提交项目关键帧生成，复用单镜头 Job/积分/幂等合同。

        已有 ready、pending 或 generating 关键帧的镜头默认跳过；重新生成
        仍通过单镜头接口完成，避免批量点击意外覆盖用户已经采用的候选图。
        """
        self.get_project(project_id, user_id)
        rows = self.store.all(
            "SELECT s.*, e.number AS episode_number FROM shots s JOIN episodes e ON e.id = s.episode_id "
            "WHERE e.project_id = ? AND COALESCE(s.status, 'active') <> 'superseded' ORDER BY e.number, s.sequence",
            (project_id,),
        )
        by_id = {str(row["id"]): dict(row) for row in rows}
        if shot_ids is None:
            selected_ids = list(by_id)
        else:
            selected_ids = []
            for raw_id in shot_ids:
                normalized = str(raw_id or "").strip()
                if normalized and normalized not in selected_ids:
                    selected_ids.append(normalized)
        if len(selected_ids) > 128:
            raise ServiceError("an image generation batch cannot exceed 128 shots")
        missing = [shot_id for shot_id in selected_ids if shot_id not in by_id]
        if missing:
            raise ServiceError("shot not found", 404)

        latest: dict[str, dict[str, Any]] = {}
        chargeable = 0
        for shot_id in selected_ids:
            shot = by_id[shot_id]
            expected_artifact_revision = self._shot_artifact_revision(shot)
            for asset in self.store.all(
                "SELECT * FROM assets WHERE shot_id = ? AND project_id = ? AND kind = 'image' ORDER BY created_at DESC",
                (shot_id, project_id),
            ):
                metadata = json_loads(asset["metadata_json"], {})
                if str(metadata.get("artifact_revision") or "") == expected_artifact_revision:
                    latest[shot_id] = dict(asset)
                    break
            if shot_id not in latest or str(latest[shot_id]["status"]) not in {"ready", "pending", "generating"}:
                chargeable += 1
        required_credits = chargeable * COSTS["image"]
        if required_credits:
            balance = int(self.credits(user_id).get("balance", 0))
            if balance < required_credits:
                raise ServiceError(
                    f"insufficient credits for image generation batch: need {required_credits}, have {balance}",
                    402,
                )

        items: list[dict[str, Any]] = []
        failed = 0
        skipped = 0
        submitted = 0
        for shot_id in selected_ids:
            shot = by_id[shot_id]
            existing = latest.get(shot_id)
            existing_status = str(existing["status"]) if existing else ""
            action = "skipped" if existing_status in {"ready", "pending", "generating"} else "submitted"
            child_key = f"{idempotency_key}:shot:{shot_id}" if idempotency_key else None
            try:
                if action == "skipped" and existing:
                    asset = self.asset_with_job(str(existing["id"]), user_id)
                    skipped += 1
                else:
                    asset = self.create_image_asset(shot_id, user_id, run_now, child_key)
                    submitted += 1
                items.append(
                    {
                        "shot_id": shot_id,
                        "episode_number": int(shot["episode_number"]),
                        "sequence": int(shot["sequence"]),
                        "action": action,
                        "status": asset.get("status"),
                        "asset": asset,
                    }
                )
            except ServiceError as exc:
                failed += 1
                items.append(
                    {
                        "shot_id": shot_id,
                        "episode_number": int(shot["episode_number"]),
                        "sequence": int(shot["sequence"]),
                        "action": "failed",
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        statuses = {str(item.get("status")) for item in items}
        batch_status = "failed" if failed and not submitted and not skipped else "partial" if failed else "queued" if statuses & {"pending", "generating"} else "completed"
        return {
            "project_id": project_id,
            "status": batch_status,
            "requested": len(selected_ids),
            "submitted": submitted,
            "skipped": skipped,
            "failed": failed,
            "required_credits": required_credits,
            "items": items,
        }

    def patch_asset(self, asset_id: str, changes: dict[str, Any], user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        asset = self.asset_with_job(asset_id, user_id)
        allowed = {}
        for key in ("selected", "consistency_confirmed", "url"):
            if key in changes:
                if key == "url":
                    value = str(changes[key]).strip()
                    if value and not (value.startswith("/") or value.startswith("https://") or value.startswith("http://")):
                        raise ServiceError("asset url must be an API-relative or http(s) URL")
                    if len(value) > 2048:
                        raise ServiceError("asset url is too long")
                    allowed[key] = value or None
                    continue
                allowed[key] = bool(changes[key]) if key in {"selected", "consistency_confirmed"} else changes[key]
        if "selected" in allowed and not allowed["selected"]:
            # Python bool is accepted by SQLite and maps correctly to PostgreSQL
            # boolean columns; numeric 0/1 does not have a portable cast contract.
            allowed["consistency_confirmed"] = False
        if not allowed:
            return asset
        with self.store.connection() as connection:
            if asset["kind"] == "image" and allowed.get("selected") is True:
                # 每个镜头只能有一个被采用的关键帧。切换候选时同时撤销
                # 兄弟候选的一致性确认，防止批量视频把多个候选都当成输入。
                connection.execute(
                    "UPDATE assets SET selected = ?, consistency_confirmed = ?, updated_at = ? WHERE shot_id = ? AND kind = 'image' AND id <> ?",
                    (False, False, now(), asset["shot_id"], asset_id),
                )
            assignment = ", ".join(f"{key} = ?" for key in allowed)
            connection.execute(f"UPDATE assets SET {assignment}, updated_at = ? WHERE id = ?", tuple(allowed.values()) + (now(), asset_id))
        return self.asset_with_job(asset_id, user_id)

    def attach_character_reference(
        self,
        asset_id: str,
        character_id: str | None = None,
        reference_id: str | None = None,
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        """把关键帧资产绑定到同项目的已完成角色母版，或显式解除绑定。"""
        asset = self.asset_with_job(asset_id, user_id)
        if asset["kind"] != "image":
            raise ServiceError("character references can only be attached to image assets")
        character_id = character_id.strip() if isinstance(character_id, str) and character_id.strip() else None
        reference_id = reference_id.strip() if isinstance(reference_id, str) and reference_id.strip() else None
        if character_id and reference_id:
            raise ServiceError("provide character_id or reference_id, not both")
        reference = None
        if reference_id:
            reference = self.store.one(
                "SELECT r.*, c.project_id FROM character_references r JOIN characters c ON c.id = r.character_id JOIN projects p ON p.id = c.project_id WHERE r.id = ? AND p.user_id = ?",
                (reference_id, user_id),
            )
            if not reference or reference["project_id"] != asset["project_id"]:
                raise ServiceError("character reference not found", 404)
            if reference["status"] != "ready":
                raise ServiceError("character reference is not ready", 409)
            character_id = str(reference["character_id"])
        elif character_id:
            character = self.store.one(
                "SELECT c.id, c.project_id FROM characters c JOIN projects p ON p.id = c.project_id WHERE c.id = ? AND p.user_id = ?",
                (character_id, user_id),
            )
            if not character or character["project_id"] != asset["project_id"]:
                raise ServiceError("character not found", 404)
            reference = self.store.one(
                "SELECT * FROM character_references WHERE character_id = ? AND status = 'ready' ORDER BY id DESC LIMIT 1",
                (character_id,),
            )
            if not reference:
                raise ServiceError("generate a ready character reference before attaching it", 409)
            reference_id = str(reference["id"])
        metadata = dict(asset.get("metadata") or {})
        if reference:
            metadata.update(
                {
                    "character_reference_id": reference_id,
                    "character_reference_provider": reference["provider"],
                    "character_reference_model": reference["model"],
                }
            )
        else:
            for key in ("character_reference_id", "character_reference_provider", "character_reference_model"):
                metadata.pop(key, None)
        with self.store.connection() as connection:
            connection.execute(
                "UPDATE assets SET character_id = ?, metadata_json = ?, updated_at = ? WHERE id = ?",
                (character_id, json.dumps(metadata, ensure_ascii=False), now(), asset_id),
            )
        return self.asset_with_job(asset_id, user_id)

    def _require_visual_pass(self, asset_id: str, action: str) -> None:
        """将服务端 readiness 的视觉审核门禁落实到下游媒体操作。"""
        review = self.store.one(
            "SELECT status FROM asset_reviews WHERE asset_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (asset_id,),
        )
        if not review:
            raise ServiceError(f"visual review is required before {action}", 409)
        if str(review["status"] or "").upper() != "PASS":
            raise ServiceError(f"latest visual review must be PASS before {action}", 409)

    def create_video_asset(
        self,
        image_asset_id: str,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        image = self.asset_with_job(image_asset_id, user_id)
        if image["kind"] != "image":
            raise ServiceError("video must be generated from an image asset")
        if not image["selected"] or not image["consistency_confirmed"]:
            raise ServiceError("select and confirm image consistency before video generation")
        shot = self.store.one("SELECT * FROM shots WHERE id = ?", (image["shot_id"],)) if image.get("shot_id") else None
        if not shot:
            raise ServiceError("shot not found", 404)
        self._require_current_shot_revision(shot, user_id, "video generation")
        expected_artifact_revision = self._shot_artifact_revision(shot)
        if str(image.get("metadata", {}).get("artifact_revision") or "") != expected_artifact_revision:
            raise ServiceError("image asset is outdated; generate a new keyframe before video generation", 409)
        self._require_visual_pass(image_asset_id, "video generation")
        video_id = new_id("asset")
        job_id = new_id("job")
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        existing_target_id: str | None = None
        timestamp = now()
        providers = self._providers_for_user(user_id)
        with self.store.connection() as connection:
            inserted = self._insert_job(connection, job_id, user_id, "video", video_id, COSTS["video"], providers.video.name, canonical_key)
            if inserted:
                self._reserve(connection, user_id, job_id, COSTS["video"], "video generation")
                connection.execute(
                    "INSERT INTO assets(id, project_id, shot_id, kind, status, source_asset_id, provider, model, metadata_json, created_at, updated_at) VALUES (?, ?, ?, 'video', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        video_id,
                        image["project_id"],
                        image["shot_id"],
                        "generating" if run_now else "pending",
                        image_asset_id,
                        providers.video.name,
                        str(getattr(providers.video, "model", "placeholder")),
                        json.dumps(
                            {
                                "mode": providers.video.name,
                                "generation_mode": "r2v",
                                "source_artifact_revision": expected_artifact_revision,
                            }
                        ),
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                existing_target_id = self._resolve_idempotent_target(connection, canonical_key, user_id, "video", image_asset_id)
        if existing_target_id:
            self._requeue_existing_external(existing_target_id, user_id, "video")
            return self.asset_with_job(existing_target_id, user_id)
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "video", video_id)
        return self.asset_with_job(video_id, user_id)

    def create_text_video_asset(
        self,
        shot_id: str,
        prompt: str,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """创建不依赖关键帧的文生视频任务。

        这是 H3 的实验/生产前置入口：工作流仍由部署配置提供，服务层只保存
        镜头、提示词、模型 Provider 和工作流结果，不把远端 ComfyUI 路径写入库。
        图生视频的审核门禁保持在 ``create_video_asset``，两条链路并存。
        """

        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            raise ServiceError("text-to-video prompt is required")
        if len(normalized_prompt) > 12_000:
            raise ServiceError("text-to-video prompt is too long")
        shot = self.store.one(
            "SELECT s.*, e.project_id FROM shots s JOIN episodes e ON e.id = s.episode_id "
            "JOIN projects p ON p.id = e.project_id WHERE s.id = ? AND p.user_id = ?",
            (shot_id, user_id),
        )
        if not shot:
            raise ServiceError("shot not found", 404)
        self._require_current_shot_revision(shot, user_id, "text-to-video generation")
        video_id = new_id("asset")
        job_id = new_id("job")
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        timestamp = now()
        providers = self._providers_for_user(user_id)
        metadata = {
            "mode": providers.video.name,
            "generation_mode": "t2v",
            "prompt": normalized_prompt,
            "shot_artifact_revision": self._shot_artifact_revision(shot),
        }
        existing_target_id: str | None = None
        with self.store.connection() as connection:
            inserted = self._insert_job(connection, job_id, user_id, "video", video_id, COSTS["video"], providers.video.name, canonical_key)
            if inserted:
                self._reserve(connection, user_id, job_id, COSTS["video"], "text-to-video generation")
                connection.execute(
                    "INSERT INTO assets(id, project_id, shot_id, kind, status, source_asset_id, provider, model, metadata_json, created_at, updated_at) VALUES (?, ?, ?, 'video', ?, NULL, ?, ?, ?, ?, ?)",
                    (
                        video_id,
                        shot["project_id"],
                        shot_id,
                        "generating" if run_now else "pending",
                        providers.video.name,
                        str(getattr(providers.video, "model", "placeholder")),
                        json.dumps(metadata, ensure_ascii=False),
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                existing_target_id = self._resolve_idempotent_target(connection, canonical_key, user_id, "video", shot_id)
        if existing_target_id:
            self._requeue_existing_external(existing_target_id, user_id, "video")
            return self.asset_with_job(existing_target_id, user_id)
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "video", video_id)
        return self.asset_with_job(video_id, user_id)

    def create_video_assets(
        self,
        project_id: str,
        asset_ids: list[str] | None = None,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """批量提交已确认关键帧的视频片段生成。"""
        self.get_project(project_id, user_id)
        if asset_ids is None:
            rows = self.store.all(
                "SELECT * FROM assets WHERE project_id = ? AND kind = 'image' AND status = 'ready' AND selected = 1 AND consistency_confirmed = 1 ORDER BY created_at",
                (project_id,),
            )
        else:
            unique_ids: list[str] = []
            for raw_id in asset_ids:
                normalized = str(raw_id or "").strip()
                if normalized and normalized not in unique_ids:
                    unique_ids.append(normalized)
            if len(unique_ids) > 128:
                raise ServiceError("a video generation batch cannot exceed 128 image assets")
            if unique_ids:
                placeholders = ",".join("?" for _ in unique_ids)
                rows = self.store.all(
                    f"SELECT * FROM assets WHERE project_id = ? AND id IN ({placeholders}) ORDER BY created_at",
                    (project_id, *unique_ids),
                )
            else:
                rows = []
            if len(rows) != len(unique_ids):
                raise ServiceError("image asset not found", 404)
        if len(rows) > 128:
            raise ServiceError("a video generation batch cannot exceed 128 image assets")

        selected: list[dict[str, Any]] = []
        chargeable = 0
        for row in rows:
            image = dict(row)
            if image["kind"] != "image":
                raise ServiceError("video batch only accepts image assets")
            if str(image["status"]) != "ready":
                raise ServiceError("video batch requires ready image assets", 409)
            if not bool_value(image["selected"]) or not bool_value(image["consistency_confirmed"]):
                raise ServiceError("select and confirm image consistency before video batch generation", 409)
            shot = self.store.one("SELECT * FROM shots WHERE id = ?", (image["shot_id"],))
            if not shot:
                raise ServiceError("shot not found", 404)
            self._require_current_shot_revision(shot, user_id, "video batch generation")
            expected_artifact_revision = self._shot_artifact_revision(shot)
            image_metadata = json_loads(image.get("metadata_json"), {})
            if str(image_metadata.get("artifact_revision") or "") != expected_artifact_revision:
                raise ServiceError("image asset is outdated; generate a new keyframe before video batch generation", 409)
            self._require_visual_pass(str(image["id"]), "video batch generation")
            existing = None
            for candidate in self.store.all(
                "SELECT * FROM assets WHERE source_asset_id = ? AND kind = 'video' ORDER BY created_at DESC",
                (image["id"],),
            ):
                candidate_metadata = json_loads(candidate["metadata_json"], {})
                if str(candidate_metadata.get("source_artifact_revision") or "") == expected_artifact_revision:
                    existing = candidate
                    break
            item = {"image": image, "video": dict(existing) if existing else None}
            selected.append(item)
            if not existing or str(existing["status"]) not in {"ready", "pending", "generating"}:
                chargeable += 1
        required_credits = chargeable * COSTS["video"]
        if required_credits:
            balance = int(self.credits(user_id).get("balance", 0))
            if balance < required_credits:
                raise ServiceError(
                    f"insufficient credits for video generation batch: need {required_credits}, have {balance}",
                    402,
                )

        items: list[dict[str, Any]] = []
        failed = 0
        skipped = 0
        submitted = 0
        for item in selected:
            image = item["image"]
            existing = item["video"]
            action = "skipped" if existing and str(existing["status"]) in {"ready", "pending", "generating"} else "submitted"
            child_key = f"{idempotency_key}:asset:{image['id']}" if idempotency_key else None
            try:
                if action == "skipped" and existing:
                    video = self.asset_with_job(str(existing["id"]), user_id)
                    skipped += 1
                else:
                    video = self.create_video_asset(str(image["id"]), user_id, run_now, child_key)
                    submitted += 1
                items.append(
                    {
                        "image_asset_id": image["id"],
                        "shot_id": image["shot_id"],
                        "action": action,
                        "status": video.get("status"),
                        "asset": video,
                    }
                )
            except ServiceError as exc:
                failed += 1
                items.append(
                    {
                        "image_asset_id": image["id"],
                        "shot_id": image["shot_id"],
                        "action": "failed",
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        statuses = {str(item.get("status")) for item in items}
        batch_status = "failed" if failed and not submitted and not skipped else "partial" if failed else "queued" if statuses & {"pending", "generating"} else "completed"
        return {
            "project_id": project_id,
            "status": batch_status,
            "requested": len(selected),
            "submitted": submitted,
            "skipped": skipped,
            "failed": failed,
            "required_credits": required_credits,
            "items": items,
        }

    def import_audio_asset(
        self,
        episode_id: str,
        filename: str,
        data_base64: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        """把用户明确提供的音频作为项目素材导入，不调用 provider 或消耗积分。"""
        episode = self.store.one(
            "SELECT e.id, e.project_id FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?",
            (episode_id, user_id),
        )
        if not episode:
            raise ServiceError("episode not found", 404)
        safe_filename = Path(str(filename or "audio.mp3")).name.strip()
        extension = Path(safe_filename).suffix.lower()
        allowed_extensions = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".webm"}
        if extension not in allowed_extensions:
            raise ServiceError("unsupported audio extension; use mp3, wav, m4a, aac, ogg, or webm", 422)
        if len(safe_filename) > 240:
            raise ServiceError("audio filename is too long", 422)
        encoded = str(data_base64 or "").strip()
        if encoded.startswith("data:"):
            _header, separator, encoded = encoded.partition(",")
            if not separator:
                raise ServiceError("audio data URI is invalid", 422)
        try:
            max_bytes = max(1024, min(int(os.environ.get("STUDIO_MAX_AUDIO_BYTES", str(16 * 1024 * 1024))), 64 * 1024 * 1024))
        except ValueError:
            max_bytes = 16 * 1024 * 1024
        if len(encoded) > ((max_bytes + 2) // 3) * 4:
            raise ServiceError("audio file is too large", 413)
        try:
            audio_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ServiceError("audio data must be valid base64", 422) from exc
        if not audio_bytes:
            raise ServiceError("audio data is empty", 422)
        if len(audio_bytes) > max_bytes:
            raise ServiceError("audio file is too large", 413)
        asset_id = new_id("asset")
        path = self.asset_dir / f"{asset_id}{extension}"
        path.write_bytes(audio_bytes)
        metadata: dict[str, Any] = {"imported_audio": True, "original_filename": safe_filename}
        url = f"/assets/{path.name}"
        if self.storage.mode != "local":
            storage_key = f"audio/{episode_id}/{path.name}"
            url = self.storage.put_file(path, storage_key)
            metadata["storage_key"] = storage_key
        timestamp = now()
        with self.store.connection() as connection:
            connection.execute(
                "INSERT INTO assets(id, project_id, shot_id, kind, status, url, selected, consistency_confirmed, provider, model, metadata_json, created_at, updated_at) VALUES (?, ?, NULL, 'audio', 'ready', ?, 0, 0, 'import', 'user-upload', ?, ?, ?)",
                (asset_id, episode["project_id"], url, json.dumps(metadata, ensure_ascii=False), timestamp, timestamp),
            )
        return self.asset_with_job(asset_id, user_id)

    def request_narration(
        self,
        episode_id: str,
        text: str | None = None,
        voice: str = "cedar",
        speed: float = 1.0,
        instructions: str = "",
        attach_to_timeline: bool = True,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
        auto_subtitles: bool = True,
    ) -> dict[str, Any]:
        """从分集文本生成旁白音频，并可自动挂到 Remotion 音轨。"""
        episode = self.store.one(
            "SELECT e.*, p.user_id FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?",
            (episode_id, user_id),
        )
        if not episode:
            raise ServiceError("episode not found", 404)
        normalized_text = str(text or "").strip()
        if not normalized_text:
            saved_settings = self.store.one(
                "SELECT narration_text FROM composition_settings WHERE episode_id = ?",
                (episode_id,),
            )
            normalized_text = str(saved_settings["narration_text"] or "").strip() if saved_settings else ""
        if not normalized_text:
            shot_rows = self.store.all(
                "SELECT sequence, description FROM shots WHERE episode_id = ? ORDER BY sequence",
                (episode_id,),
            )
            normalized_text = "\n".join(
                f"镜头 {int(row['sequence']):02d}：{str(row['description'] or '').strip()}"
                for row in shot_rows
                if str(row["description"] or "").strip()
            ).strip()
        if not normalized_text:
            normalized_text = str(episode["summary"] or episode["title"] or "").strip()
        if not normalized_text:
            raise ServiceError("narration text is empty", 422)
        if len(normalized_text) > MAX_NARRATION_TEXT_CHARS:
            raise ServiceError(f"narration text cannot exceed {MAX_NARRATION_TEXT_CHARS} characters", 422)
        narration_segments = split_speech_text(normalized_text)
        if not narration_segments:
            raise ServiceError("narration text is empty", 422)
        if len(narration_segments) > MAX_NARRATION_SEGMENTS:
            raise ServiceError(f"narration text cannot create more than {MAX_NARRATION_SEGMENTS} provider segments", 422)
        normalized_voice = str(voice or "cedar").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", normalized_voice):
            raise ServiceError("voice must be a short provider voice name", 422)
        if not speech_voice_allowed(normalized_voice):
            raise ServiceError("voice is not an allowed built-in speech voice", 422)
        try:
            normalized_speed = float(speed)
        except (TypeError, ValueError) as exc:
            raise ServiceError("speech speed must be a number", 422) from exc
        if not math.isfinite(normalized_speed) or normalized_speed < 0.25 or normalized_speed > 4:
            raise ServiceError("speech speed must be between 0.25 and 4", 422)
        normalized_instructions = str(instructions or "").strip()
        if len(normalized_instructions) > 1000:
            raise ServiceError("speech instructions cannot exceed 1000 characters", 422)
        attach = bool_value(attach_to_timeline)
        generate_subtitles = bool_value(auto_subtitles)
        if attach:
            settings = self.store.one("SELECT audio_tracks_json FROM composition_settings WHERE episode_id = ?", (episode_id,))
            tracks = json_loads(settings["audio_tracks_json"], []) if settings else []
            if isinstance(tracks, list) and len(tracks) >= 32:
                raise ServiceError("an episode cannot have more than 32 audio tracks", 422)

        providers = self._providers_for_user(user_id)
        asset_id = new_id("asset")
        job_id = new_id("job")
        request_key = self._canonical_idempotency_key(user_id, idempotency_key)
        payload = {
            "episode_id": episode_id,
            "text": normalized_text,
            "voice": normalized_voice,
            "speed": normalized_speed,
            "instructions": normalized_instructions,
            "attach_to_timeline": attach,
            "auto_subtitles": generate_subtitles,
        }
        metadata = {
            "mode": "narration",
            "episode_id": episode_id,
            "voice": normalized_voice,
            "speed": normalized_speed,
            "text_chars": len(normalized_text),
            "text_sha256": hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
            "segment_count": len(narration_segments),
            "attached_to_timeline": attach,
            "auto_subtitles": generate_subtitles,
        }
        existing_target_id: str | None = None
        with self.store.connection() as connection:
            inserted = self._insert_job(
                connection,
                job_id,
                user_id,
                "narration",
                asset_id,
                COSTS["narration"],
                providers.speech.name,
                request_key,
            )
            if inserted:
                self._reserve(connection, user_id, job_id, COSTS["narration"], "narration generation")
                timestamp = now()
                connection.execute(
                    "INSERT INTO assets(id, project_id, shot_id, kind, status, provider, model, metadata_json, created_at, updated_at) VALUES (?, ?, NULL, 'audio', ?, ?, ?, ?, ?, ?)",
                    (
                        asset_id,
                        episode["project_id"],
                        "generating" if run_now else "pending",
                        providers.speech.name,
                        str(getattr(providers.speech, "model", "speech")),
                        json.dumps(metadata, ensure_ascii=False),
                        timestamp,
                        timestamp,
                    ),
                )
                connection.execute("UPDATE jobs SET payload_json = ? WHERE id = ?", (json.dumps(payload, ensure_ascii=False), job_id))
            else:
                existing_target_id = self._resolve_narration_target(connection, request_key, user_id, episode_id)
        if existing_target_id:
            self._requeue_existing_external(existing_target_id, user_id, "narration")
            return self.asset_with_job(existing_target_id, user_id)
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "narration", asset_id)
        return self.asset_with_job(asset_id, user_id)

    @staticmethod
    def _estimate_narration_subtitles(text: str, duration_seconds: Any, speed: Any) -> list[dict[str, Any]]:
        """把旁白文本转成可人工修订的估算字幕，不宣称是 ASR 对齐结果。"""

        segments = [segment.strip() for segment in split_speech_text(text, max_chars=72) if segment.strip()]
        if not segments:
            return []
        try:
            duration = float(duration_seconds)
        except (TypeError, ValueError):
            duration = 0.0
        try:
            normalized_speed = max(0.25, min(4.0, float(speed)))
        except (TypeError, ValueError):
            normalized_speed = 1.0
        if not math.isfinite(duration) or duration <= 0:
            duration = len(text.strip()) / max(1.0, 4.0 * normalized_speed)
        duration = min(900.0, max(1.0, duration))
        weights = [max(1, len(re.sub(r"\s+", "", segment))) for segment in segments]
        total_weight = sum(weights)
        subtitles: list[dict[str, Any]] = []
        cursor = 0.0
        consumed = 0
        for index, segment in enumerate(segments):
            consumed += weights[index]
            if index == len(segments) - 1:
                end = duration
            else:
                end = min(duration, max(cursor + 0.5, duration * consumed / total_weight))
            if end <= cursor:
                continue
            subtitles.append(
                {
                    "start_seconds": round(cursor, 3),
                    "end_seconds": round(end, 3),
                    "text": segment[:500],
                }
            )
            cursor = end
        return subtitles

    @staticmethod
    def _attach_narration_to_timeline(
        connection: Any,
        episode_id: str,
        asset_id: str,
        *,
        text: str = "",
        duration_seconds: Any = None,
        speed: Any = 1.0,
        auto_subtitles: bool = True,
    ) -> None:
        row = connection.execute(
            "SELECT audio_tracks_json, subtitles_json, narration_text FROM composition_settings WHERE episode_id = ?",
            (episode_id,),
        ).fetchone()
        tracks = json_loads(row["audio_tracks_json"], []) if row else []
        subtitles = json_loads(row["subtitles_json"], []) if row else []
        narration_text = str(row["narration_text"] or "") if row else ""
        if not isinstance(tracks, list):
            tracks = []
        if not any(isinstance(track, dict) and str(track.get("asset_id") or "") == asset_id for track in tracks):
            tracks.append({"asset_id": asset_id, "start_seconds": 0.0, "volume": 1.0})
        if not isinstance(subtitles, list):
            subtitles = []
        if auto_subtitles and not subtitles:
            subtitles = StudioService._estimate_narration_subtitles(text, duration_seconds, speed)
        timestamp = now()
        connection.execute(
            "INSERT INTO composition_settings(episode_id, audio_tracks_json, subtitles_json, narration_text, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(episode_id) DO UPDATE SET audio_tracks_json = excluded.audio_tracks_json, subtitles_json = excluded.subtitles_json, narration_text = excluded.narration_text, updated_at = excluded.updated_at",
            (episode_id, json.dumps(tracks, ensure_ascii=False), json.dumps(subtitles, ensure_ascii=False), text.strip() or narration_text, timestamp),
        )

    def text_job_with_job(self, project_id: str, user_id: str | None = None) -> dict[str, Any]:
        if user_id is None:
            project = self.store.one("SELECT id, user_id FROM projects WHERE id = ?", (project_id,))
        else:
            project = self.store.one("SELECT id, user_id FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id))
        if not project:
            raise ServiceError("project not found", 404)
        effective_user_id = str(user_id or project["user_id"])
        job = self.store.one(
            "SELECT * FROM jobs WHERE target_id = ? AND user_id = ? AND kind = 'text' ORDER BY created_at DESC LIMIT 1",
            (project_id, effective_user_id),
        )
        return {
            "project_id": project_id,
            "units": self.list_adaptation_units(project_id, effective_user_id),
            "job": dict(job) if job else None,
        }

    def long_video_with_job(self, episode_id: str, user_id: str | None = None) -> dict[str, Any]:
        """返回长视频计划、逐段状态和最终成片，不把内部主机路径暴露给客户端。"""
        if user_id is None:
            episode = self.store.one("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        else:
            episode = self.store.one(
                "SELECT e.* FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?",
                (episode_id, user_id),
            )
        if not episode:
            raise ServiceError("episode not found", 404)
        job = self.store.one(
            "SELECT * FROM jobs WHERE target_id = ? AND kind = 'long-video' ORDER BY created_at DESC LIMIT 1",
            (episode_id,),
        )
        if job and user_id is not None and job["user_id"] != user_id:
            job = None
        payload = json_loads(job["payload_json"], {}) if job else {}
        if not isinstance(payload, dict):
            payload = {}
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else None
        return {
            "episode_id": episode_id,
            "status": str(job["status"] if job else "pending"),
            "plan": plan,
            "segments": list(plan.get("segments", [])) if plan else [],
            "final_video_url": payload.get("final_video_url"),
            "manifest": payload.get("manifest") or {},
            "job": dict(job) if job else None,
        }

    def request_long_video(
        self,
        episode_id: str,
        payload: dict[str, Any],
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """创建可恢复的 H3 长视频 Job。

        任务只预留一次积分；Worker 每段成功后把计划写回 ``payload_json``，
        因此进程重启或 GPU 失败后重试不会重复生成已经完成的片段。
        """
        episode = self.store.one(
            "SELECT e.*, p.user_id, p.id AS project_id FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?",
            (episode_id, user_id),
        )
        if not episode:
            raise ServiceError("episode not found", 404)
        try:
            plan = normalize_long_video_plan(payload)
        except LongVideoPlanError as exc:
            raise ServiceError(str(exc), 422) from exc
        source_asset_id = plan.get("source_asset_id")
        if source_asset_id:
            source = self.store.one(
                "SELECT id FROM assets WHERE id = ? AND project_id = ? AND kind = 'image' AND status = 'ready'",
                (source_asset_id, episode["project_id"]),
            )
            if not source:
                raise ServiceError("source_asset_id must reference a ready image in this project", 422)
        reference_asset_ids = list(plan.get("reference_asset_ids") or [])
        if reference_asset_ids:
            placeholders = ",".join("?" for _ in reference_asset_ids)
            references = self.store.all(
                f"SELECT id FROM assets WHERE project_id = ? AND kind = 'image' AND status = 'ready' AND id IN ({placeholders})",
                (episode["project_id"], *reference_asset_ids),
            )
            if len(references) != len(reference_asset_ids):
                raise ServiceError("reference_asset_ids must reference ready images in this project", 422)
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        request_key = f"long-video:{canonical_key}" if canonical_key else None
        if request_key:
            existing = self.store.one("SELECT * FROM jobs WHERE user_id = ? AND idempotency_key = ?", (user_id, request_key))
            if existing:
                if existing["kind"] != "long-video" or existing["target_id"] != episode_id:
                    raise ServiceError("Idempotency-Key was already used for another long-video request", 409)
                if run_now and existing["status"] == "queued":
                    return self.run_job(str(existing["id"]), user_id)
                if existing["status"] == "queued":
                    self._enqueue_external(existing["id"], user_id, "long-video", episode_id)
                return self.long_video_with_job(episode_id, user_id)
        active = self.store.one(
            "SELECT * FROM jobs WHERE user_id = ? AND target_id = ? AND kind = 'long-video' AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
            (user_id, episode_id),
        )
        if active:
            if run_now and active["status"] == "queued":
                return self.run_job(str(active["id"]), user_id)
            return self.long_video_with_job(episode_id, user_id)
        job_id = new_id("job")
        stored_payload = {
            "version": 1,
            "plan": plan,
            "final_video_url": None,
            "manifest": {},
        }
        cost = len(plan["segments"]) * COSTS["long-video"]
        with self.store.connection() as connection:
            inserted = self._insert_job(connection, job_id, user_id, "long-video", episode_id, cost, "h3-director-motion-context", request_key)
            if not inserted:
                existing = connection.execute("SELECT * FROM jobs WHERE user_id = ? AND idempotency_key = ?", (user_id, request_key)).fetchone()
                if not existing or existing["kind"] != "long-video" or existing["target_id"] != episode_id:
                    raise ServiceError("Idempotency-Key was already used for another long-video request", 409)
                job_id = str(existing["id"])
            else:
                connection.execute("UPDATE jobs SET payload_json = ? WHERE id = ?", (json.dumps(stored_payload, ensure_ascii=False), job_id))
                self._reserve(connection, user_id, job_id, cost, "H3 long-video segment generation")
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "long-video", episode_id)
        return self.long_video_with_job(episode_id, user_id)

    def _update_long_video_payload(self, job_id: str, payload: dict[str, Any]) -> None:
        self.store.write(
            "UPDATE jobs SET payload_json = ?, updated_at = ? WHERE id = ? AND status IN ('queued', 'running')",
            (json.dumps(payload, ensure_ascii=False), now(), job_id),
        )

    def _long_video_media_evidence(self, relative_url: str) -> dict[str, Any]:
        path = self.asset_dir / Path(relative_url).name
        evidence: dict[str, Any] = {"relative_url": relative_url}
        if not path.is_file():
            evidence["local_file"] = "missing"
            return evidence
        evidence.update({"local_file": "present", "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            try:
                result = subprocess.run(
                    [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                evidence["ffprobe"] = json.loads(result.stdout)
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                evidence["ffprobe"] = "failed"
        return evidence

    def _concat_long_video(
        self,
        job_id: str,
        segment_outputs: list[dict[str, Any]],
        target_duration_seconds: float | None = None,
    ) -> str | None:
        paths = [self.asset_dir / Path(str(item.get("relative_url") or "")).name for item in segment_outputs]
        if not paths or any(not path.is_file() for path in paths):
            return None
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        concat_list = self.asset_dir / f".{job_id}.concat.txt"
        concat_path = self.asset_dir / f".{job_id}.concat.mp4"
        final_path = self.asset_dir / f"{job_id}.final.mp4"
        try:
            concat_list.write_text("".join(f"file '{path.as_posix().replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n" for path in paths), encoding="utf-8")
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", "-movflags", "+faststart", "-y", str(concat_path)],
                check=True,
                capture_output=True,
                timeout=300,
            )
            if target_duration_seconds is not None and float(target_duration_seconds) > 0:
                subprocess.run(
                    [
                        ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(concat_path),
                        "-t", f"{float(target_duration_seconds):.6f}",
                        "-map", "0:v:0", "-map", "0:a?",
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-ar", "32000", "-ac", "2",
                        "-movflags", "+faststart", "-y", str(final_path),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=300,
                )
            else:
                concat_path.replace(final_path)
            return f"/assets/{final_path.name}" if final_path.is_file() and final_path.stat().st_size else None
        except (OSError, subprocess.SubprocessError):
            final_path.unlink(missing_ok=True)
            return None
        finally:
            concat_list.unlink(missing_ok=True)
            concat_path.unlink(missing_ok=True)

    def _save_long_video_composition(self, episode_id: str, final_url: str | None, payload: dict[str, Any]) -> None:
        timestamp = now()
        with self.store.connection() as connection:
            connection.execute(
                "INSERT INTO compositions(id, episode_id, playlist_url, final_video_url, status, metadata_json, created_at) VALUES (?, ?, NULL, ?, ?, ?, ?) ",
                (new_id("composition"), episode_id, final_url, "completed" if final_url else "segments_ready", json.dumps({"kind": "h3-long-video", "manifest": payload.get("manifest", {})}, ensure_ascii=False), timestamp),
            )

    def _run_long_video_job(self, job: Any, user_id: str | None) -> dict[str, Any]:
        payload = json_loads(job["payload_json"], {})
        if not isinstance(payload, dict) or not isinstance(payload.get("plan"), dict):
            raise ServiceError("long-video job payload is invalid", 500)
        plan = payload["plan"]
        episode = self.store.one(
            "SELECT e.*, p.user_id, p.id AS project_id FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ?",
            (job["target_id"],),
        )
        if not episode or (user_id is not None and episode["user_id"] != user_id):
            raise ServiceError("episode not found", 404)
        providers = self._providers_for_user(str(job["user_id"]))
        generate_segment = getattr(providers.video, "generate_segment", None)
        if not callable(generate_segment):
            raise ProviderError("configured video provider does not support H3 long-video segments")
        segments = plan.get("segments", [])
        completed_outputs: list[dict[str, Any]] = []
        for segment in segments:
            if segment.get("status") == "completed" and isinstance(segment.get("output"), dict):
                completed_outputs.append(segment["output"])
        for segment in segments:
            if segment.get("status") == "completed" and isinstance(segment.get("output"), dict):
                continue
            segment["status"] = "running"
            segment["started_at"] = now()
            self._update_long_video_payload(job["id"], payload)
            segment_index = int(segment["index"])
            source_image_path: Path | None = None
            reference_image_paths: list[Path] = []
            if segment_index == 1 and plan.get("source_asset_id"):
                source = self.store.one(
                    "SELECT * FROM assets WHERE id = ? AND project_id = ? AND kind = 'image' AND status = 'ready'",
                    (plan["source_asset_id"], episode["project_id"]),
                )
                if not source:
                    raise ServiceError("long-video source image is not ready", 409)
                source_data = dict(source)
                source_data["metadata"] = json_loads(source_data.pop("metadata_json", "{}"), {})
                source_image_path = self._materialize_asset_file(source_data, prefer_video_source=True)
                if not source_image_path:
                    raise ServiceError("long-video source image bytes are unavailable", 409)
            if segment_index == 1:
                for reference_asset_id in plan.get("reference_asset_ids") or []:
                    reference = self.store.one(
                        "SELECT * FROM assets WHERE id = ? AND project_id = ? AND kind = 'image' AND status = 'ready'",
                        (reference_asset_id, episode["project_id"]),
                    )
                    if not reference:
                        raise ServiceError("long-video reference image is not ready", 409)
                    reference_data = dict(reference)
                    reference_data["metadata"] = json_loads(reference_data.pop("metadata_json", "{}"), {})
                    reference_image_path = self._materialize_asset_file(reference_data)
                    if not reference_image_path:
                        raise ServiceError("long-video reference image bytes are unavailable", 409)
                    reference_image_paths.append(reference_image_path)
            previous = segments[segment_index - 2].get("output") if segment_index > 1 else None
            context_latent_path = None
            context_clip_index = None
            if segment_index > 1:
                if not isinstance(previous, dict):
                    raise ServiceError("previous long-video segment output is missing", 409)
                previous_metadata = previous.get("metadata") if isinstance(previous.get("metadata"), dict) else {}
                context_latent_path = str(
                    previous.get("context_latent_ref")
                    or previous.get("context_latent_relative_url")
                    or previous_metadata.get("context_latent_ref")
                    or previous_metadata.get("context_latent_relative_url")
                    or ""
                ).strip() or None
                context_clip_index = segment_index - 1
                if not context_latent_path:
                    raise ServiceError("previous segment did not expose a Motion Context latent reference", 409)
            frames = max(1, int(round(float(segment["duration_seconds"]) * float(plan["fps"]))))
            generated = generate_segment(
                f"{job['id']}-seg-{segment_index:04d}",
                f"{job['target_id']}-seg-{segment_index:04d}",
                self.asset_dir,
                role=str(segment["role"]),
                prompt=str(segment["prompt"]),
                segment_index=segment_index,
                width=int(plan["width"]),
                height=int(plan["height"]),
                frames=frames,
                fps=float(plan["fps"]),
                steps=int(plan["steps"]),
                seed=int(plan["seed"]) + segment_index - 1,
                sampler=str(plan["sampler"]),
                context_length=int(plan["context_length"]),
                audio_context_length=int(plan["audio_context_length"]),
                source_image_path=source_image_path,
                context_latent_path=context_latent_path,
                context_clip_index=context_clip_index,
                latent_output_prefix=f"long-video/{job['id']}/context/clip",
                reference_image_paths=reference_image_paths,
            )
            output = {
                "relative_url": generated.relative_url,
                "metadata": dict(generated.metadata),
                "evidence": self._long_video_media_evidence(generated.relative_url),
                "completed_at": now(),
            }
            segment["status"] = "completed"
            segment["output"] = output
            segment["finished_at"] = output["completed_at"]
            completed_outputs.append(output)
            done = len([item for item in segments if item.get("status") == "completed"])
            self._set_job_progress(job["id"], min(95, int(done / max(1, len(segments)) * 95)), f"完成长视频片段 {done}/{len(segments)}")
            self._update_long_video_payload(job["id"], payload)
        try:
            target_duration = float(episode["target_duration_seconds"] or 0)
        except (TypeError, ValueError):
            target_duration = 0.0
        final_url = self._concat_long_video(job["id"], completed_outputs, target_duration_seconds=target_duration or None)
        payload["final_video_url"] = final_url
        if final_url:
            payload["manifest"]["final"] = self._long_video_media_evidence(final_url)
            payload["manifest"]["target_duration_seconds"] = target_duration or None
        payload["manifest"]["segment_count"] = len(completed_outputs)
        payload["manifest"]["workflow_chain"] = ["director", "motion-context"]
        self._update_long_video_payload(job["id"], payload)
        self._save_long_video_composition(str(job["target_id"]), final_url, payload)
        with self.store.connection() as connection:
            self._transition_job_status(connection, job["id"], "completed", error=None)
            reservation = connection.execute("SELECT * FROM credit_reservations WHERE job_id = ? AND status = 'reserved'", (job["id"],)).fetchone()
            if reservation:
                self._commit_reservation(connection, reservation["id"])
        return self.long_video_with_job(str(job["target_id"]), user_id)

    def run_job(self, job_id: str, user_id: str | None = None) -> dict[str, Any]:
        job = self.store.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not job:
            raise ServiceError("job not found", 404)
        if user_id is not None and job["user_id"] != user_id:
            raise ServiceError("job not found", 404)
        if job["status"] == "running":
            self.recover_stale_jobs(job_id=job_id)
            job = self.store.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
            if not job:
                raise ServiceError("job not found", 404)
        if job["status"] == "completed":
            if job["kind"] == "text":
                return self.text_job_with_job(job["target_id"], user_id)
            if job["kind"] == "prompt":
                return self.prompt_with_job(job["target_id"], user_id)
            if job["kind"] == "story-bible":
                return self.story_bible_with_job(job["target_id"], user_id)
            if job["kind"] == "structure":
                return self._structure_job_result(job, user_id)
            if job["kind"] == "character-reference":
                return self.reference_with_job(job["target_id"], user_id)
            if job["kind"] == "compose":
                return self.composition_with_job(job["target_id"], user_id)
            if job["kind"] == "long-video":
                return self.long_video_with_job(job["target_id"], user_id)
            return self.asset_with_job(job["target_id"], user_id)
        if job["status"] == "failed":
            raise ServiceError("job failed; call the retry endpoint before running it again", 409)
        if job["status"] not in {"queued"}:
            if job["kind"] == "text":
                return self.text_job_with_job(job["target_id"], user_id)
            if job["kind"] == "prompt":
                return self.prompt_with_job(job["target_id"], user_id)
            if job["kind"] == "story-bible":
                return self.story_bible_with_job(job["target_id"], user_id)
            if job["kind"] == "structure":
                return self._structure_job_result(job, user_id)
            if job["kind"] == "character-reference":
                return self.reference_with_job(job["target_id"], user_id)
            if job["kind"] == "compose":
                return self.composition_with_job(job["target_id"], user_id)
            if job["kind"] == "long-video":
                return self.long_video_with_job(job["target_id"], user_id)
            return self.asset_with_job(job["target_id"], user_id)
        reservation = self.store.one("SELECT * FROM credit_reservations WHERE job_id = ? AND status = 'reserved'", (job_id,))
        claimed = False
        with self.store.connection() as connection:
            claim_result = self._transition_job_status(connection, job_id, "running", expected_status="queued", increment_attempt=True)
            if claim_result:
                claimed = True
                if job["kind"] == "character-reference":
                    connection.execute("UPDATE character_references SET status = 'generating' WHERE id = ?", (job["target_id"],))
                elif job["kind"] in {"image", "video", "narration"}:
                    connection.execute("UPDATE assets SET status = 'generating', updated_at = ? WHERE id = ?", (now(), job["target_id"]))
        if not claimed:
            if job["kind"] == "text":
                return self.text_job_with_job(job["target_id"], user_id)
            if job["kind"] == "prompt":
                return self.prompt_with_job(job["target_id"], user_id)
            if job["kind"] == "story-bible":
                return self.story_bible_with_job(job["target_id"], user_id)
            if job["kind"] == "structure":
                return self._structure_job_result(job, user_id)
            if job["kind"] == "character-reference":
                return self.reference_with_job(job["target_id"], user_id)
            if job["kind"] == "compose":
                return self.composition_with_job(job["target_id"], user_id)
            if job["kind"] == "long-video":
                return self.long_video_with_job(job["target_id"], user_id)
            return self.asset_with_job(job["target_id"], user_id)
        self._set_job_progress(job_id, 1, "开始处理")
        generated_speech_parts: list[Any] = []
        try:
            if job["kind"] == "long-video":
                return self._run_long_video_job(job, user_id)
            if job["kind"] == "text":
                self._set_job_progress(job_id, 15, "准备改编文本")
                key_parts = str(job["idempotency_key"] or "").split(":")
                mode = key_parts[1] if len(key_parts) >= 3 and key_parts[0] == "rewrite" else AdaptationMode.ORIGINALIZED.value
                self.rewrite_adaptation_units(job["target_id"], mode, job["user_id"])
                self._set_job_progress(job_id, 90, "保存改编审校结果")
                with self.store.connection() as connection:
                    self._transition_job_status(connection, job_id, "completed", error=None)
                return self.text_job_with_job(job["target_id"], user_id)
            if job["kind"] == "prompt":
                self._set_job_progress(job_id, 20, "生成图片提示词")
                self.create_prompts(job["target_id"], job["user_id"])
                self._set_job_progress(job_id, 90, "保存提示词")
                with self.store.connection() as connection:
                    self._transition_job_status(connection, job_id, "completed", error=None)
                return self.prompt_with_job(job["target_id"], user_id)
            if job["kind"] == "story-bible":
                self._set_job_progress(job_id, 20, "抽取故事资产")
                self.generate_story_bible(job["target_id"], job["user_id"])
                self._set_job_progress(job_id, 90, "保存故事资产与来源追溯")
                with self.store.connection() as connection:
                    self._transition_job_status(connection, job_id, "completed", error=None)
                return self.story_bible_with_job(job["target_id"], user_id)
            if job["kind"] == "structure":
                stage = self._structure_stage_from_job(job)
                self._set_job_progress(job_id, 15, f"准备结构阶段：{stage}")
                if stage == "full":
                    self._require_approved_adaptation_for_structure(job["target_id"], job["user_id"])
                    self.prepare_project_structure(job["target_id"], job["user_id"])
                else:
                    self._run_structure_stage(stage, job["target_id"], job["user_id"])
                self._set_job_progress(job_id, 90, "保存角色、分集与分镜")
                with self.store.connection() as connection:
                    self._transition_job_status(connection, job_id, "completed", error=None)
                return self._structure_job_result(job, user_id)
            if job["kind"] == "compose":
                self._set_job_progress(job_id, 20, "检查采用素材与时间线")
                composition = self.compose_episode(job["target_id"], job["user_id"])
                self._set_job_progress(job_id, 90, "保存播放清单与成片")
                with self.store.connection() as connection:
                    self._transition_job_status(connection, job_id, "completed", error=None)
                result = dict(composition)
                result["job"] = dict(self.store.one("SELECT * FROM jobs WHERE id = ?", (job_id,)))
                return result
            if job["kind"] == "vision-review":
                payload = json_loads(job["payload_json"], {})
                self._set_job_progress(job_id, 20, "分析关键帧视觉质量")
                self._review_asset_now(
                    job["target_id"],
                    str(payload.get("prompt") or ""),
                    str(payload.get("audit_type") or "scene"),
                    job["user_id"],
                )
                self._set_job_progress(job_id, 90, "保存视觉审核结果")
                with self.store.connection() as connection:
                    self._transition_job_status(connection, job_id, "completed", error=None)
                return self.asset_with_job(job["target_id"], user_id)
            providers = self._providers_for_user(job["user_id"])
            if job["kind"] == "character-reference":
                reference = self.store.one(
                    "SELECT r.*, c.name, c.description, c.visual_lock_json FROM character_references r JOIN characters c ON c.id = r.character_id WHERE r.id = ?",
                    (job["target_id"],),
                )
                if not reference:
                    raise ServiceError("character reference not found", 404)
                visual_lock = json_loads(reference["visual_lock_json"], {})
                lock_text = str(visual_lock.get("prompt") or reference["description"] or "待补充外貌").strip()
                prompts = {
                    "front": f"东亚黑白漫画，2D comic，G笔线条，角色正面立绘，角色名：{reference['name']}，外貌锁定：{lock_text}，白色背景，完整身体，禁止可读文字",
                    "side": f"东亚黑白漫画，2D comic，G笔线条，角色侧面立绘，角色名：{reference['name']}，外貌锁定：{lock_text}，白色背景，完整身体，禁止可读文字",
                    "back": f"东亚黑白漫画，2D comic，G笔线条，角色背面立绘，角色名：{reference['name']}，外貌锁定：{lock_text}，白色背景，完整身体，禁止可读文字",
                }
                urls: dict[str, str] = {}
                metadata: dict[str, Any] = {"views": {}, "provider": providers.image.name}
                for view_index, (view, prompt) in enumerate(prompts.items(), start=1):
                    self._set_job_progress(job_id, min(90, view_index * 25), f"生成角色{view}视图")
                    generated = providers.image.generate(f"{reference['id']}-{view}", reference["description"], prompt, self.asset_dir)
                    asset_url = generated.relative_url
                    generated_path = self.asset_dir / Path(generated.relative_url).name
                    view_metadata = dict(generated.metadata)
                    if generated_path.exists() and self.storage.mode != "local":
                        storage_key = f"character-references/{reference['id']}/{generated_path.name}"
                        asset_url = self.storage.put_file(generated_path, storage_key)
                        view_metadata.update({"storage": self.storage.mode, "storage_key": storage_key})
                    urls[view] = asset_url
                    metadata["views"][view] = view_metadata
                provider_name = str(metadata.get("provider", providers.image.name))
                model_name = str(getattr(providers.image, "model", "placeholder"))
                with self.store.connection() as connection:
                    connection.execute(
                        "UPDATE character_references SET front_url = ?, side_url = ?, back_url = ?, provider = ?, model = ?, status = 'ready' WHERE id = ?",
                        (urls["front"], urls["side"], urls["back"], provider_name, model_name, job["target_id"]),
                    )
                    connection.execute("UPDATE characters SET status = 'reference_ready' WHERE id = ?", (reference["character_id"],))
                    self._transition_job_status(connection, job_id, "completed", error=None)
                    if reservation:
                        self._commit_reservation(connection, reservation["id"])
                return self.reference_with_job(job["target_id"], user_id)
            asset = self.store.one("SELECT * FROM assets WHERE id = ?", (job["target_id"],))
            if not asset:
                raise ServiceError("audio asset not found", 404)
            if job["kind"] == "narration":
                payload = json_loads(job["payload_json"], {})
                self._set_job_progress(job_id, 20, "准备旁白文本")
                text_segments = split_speech_text(str(payload.get("text") or ""))
                if not text_segments or len(text_segments) > MAX_NARRATION_SEGMENTS:
                    raise ServiceError("narration text cannot be segmented safely", 422)
                voice = str(payload.get("voice") or "cedar")
                speed = float(payload.get("speed") or 1.0)
                instructions = str(payload.get("instructions") or "")
                if len(text_segments) == 1:
                    self._set_job_progress(job_id, 45, "调用语音 Provider")
                    generated = providers.speech.synthesize(
                        asset["id"],
                        text_segments[0],
                        self.asset_dir,
                        voice=voice,
                        speed=speed,
                        instructions=instructions,
                    )
                else:
                    for segment_index, segment in enumerate(text_segments, start=1):
                        self._set_job_progress(
                            job_id,
                            min(85, 40 + int(segment_index / len(text_segments) * 45)),
                            f"生成旁白片段 {segment_index}/{len(text_segments)}",
                        )
                        generated_speech_parts.append(
                            providers.speech.synthesize(
                                f"{asset['id']}.segment-{segment_index:02d}",
                                segment,
                                self.asset_dir,
                                voice=voice,
                                speed=speed,
                                instructions=instructions,
                            )
                        )
                    generated = combine_speech_assets(asset["id"], generated_speech_parts, self.asset_dir)
            elif job["kind"] == "image":
                self._set_job_progress(job_id, 20, "准备关键帧提示词")
                shot = self.store.one("SELECT * FROM shots WHERE id = ?", (asset["shot_id"],))
                self._require_current_shot_revision(shot, job["user_id"], "image generation")
                prompt = self.store.one(
                    "SELECT * FROM image_prompts WHERE id = ?",
                    (shot["image_prompt_id"],),
                ) if shot["image_prompt_id"] else None
                if not prompt:
                    prompt = self.create_prompts(asset["shot_id"], job["user_id"])
                artifact_revision = self._shot_artifact_revision(shot, prompt)
                self._set_job_progress(job_id, 45, "调用图片 Provider")
                generated = providers.image.generate(asset["id"], shot["description"], prompt["prompt"] if prompt else shot["description"], self.asset_dir)
            elif job["kind"] == "video":
                self._set_job_progress(job_id, 20, "准备关键帧输入")
                shot = self.store.one("SELECT * FROM shots WHERE id = ?", (asset["shot_id"],))
                self._require_current_shot_revision(shot, job["user_id"], "video generation")
                source_image_path: Path | None = None
                source_asset_id = str(asset["source_asset_id"] or "")
                source_artifact_revision: str | None = None
                asset_metadata = json_loads(asset["metadata_json"], {})
                video_prompt = str(asset_metadata.get("prompt") or (shot["description"] if shot else "") or "")
                if source_asset_id:
                    source_asset = self.store.one(
                        "SELECT * FROM assets WHERE id = ? AND project_id = ? AND kind = 'image' AND status = 'ready'",
                        (source_asset_id, asset["project_id"]),
                    )
                    if not source_asset:
                        raise ServiceError("source image is not ready", 409)
                    source_data = dict(source_asset)
                    source_data["metadata"] = json_loads(source_data.pop("metadata_json", "{}"), {})
                    expected_source_revision = self._shot_artifact_revision(shot)
                    if str(source_data["metadata"].get("artifact_revision") or "") != expected_source_revision:
                        raise ServiceError("source image is outdated; generate a new keyframe before video generation", 409)
                    source_artifact_revision = expected_source_revision
                    source_image_path = self._materialize_asset_file(source_data, prefer_video_source=True)
                    if not source_image_path:
                        raise ServiceError("source image bytes are unavailable for video generation", 409)
                self._set_job_progress(job_id, 45, "调用视频 Provider")
                generate = providers.video.generate
                generate_parameters = inspect.signature(generate).parameters
                generate_kwargs: dict[str, Any] = {"source_image_path": source_image_path}
                # 兼容已有的第三方/测试 Provider：只有明确支持 prompt 的实现才接收
                # 该关键字；ComfyUI H3 Provider 会把它替换到 {{PROMPT}}。
                if "prompt" in generate_parameters or any(
                    parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in generate_parameters.values()
                ):
                    generate_kwargs["prompt"] = video_prompt
                generated = generate(
                    asset["id"],
                    asset["shot_id"] or "",
                    self.asset_dir,
                    **generate_kwargs,
                )
            else:
                raise ServiceError(f"unsupported job kind: {job['kind']}")
            self._set_job_progress(job_id, 90, "保存生成媒体")
            with self.store.connection() as connection:
                asset_url = generated.relative_url
                metadata = json_loads(asset["metadata_json"], {})
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata.update(dict(generated.metadata))
                if job["kind"] == "narration":
                    narration_text = str(json_loads(job["payload_json"], {}).get("text") or "")
                    narration_segments = split_speech_text(narration_text)
                    metadata.setdefault("segment_count", len(narration_segments))
                    metadata.setdefault("segment_char_counts", [len(segment) for segment in narration_segments])
                if job["kind"] == "image":
                    metadata["artifact_revision"] = artifact_revision
                elif job["kind"] == "video":
                    if source_artifact_revision:
                        metadata["source_artifact_revision"] = source_artifact_revision
                    else:
                        metadata.setdefault("generation_mode", "t2v")
                        metadata.setdefault("prompt", video_prompt)
                generated_path = self.asset_dir / Path(generated.relative_url).name
                if generated_path.exists() and self.storage.mode != "local":
                    if job["kind"] == "narration":
                        narration_payload = json_loads(job["payload_json"], {})
                        storage_key = f"audio/{str(narration_payload.get('episode_id') or job['target_id'])}/{generated_path.name}"
                    else:
                        storage_key = f"{job['kind']}/{job['target_id']}/{generated_path.name}"
                    asset_url = self.storage.put_file(generated_path, storage_key)
                    metadata.update({"storage": self.storage.mode, "storage_key": storage_key})
                    if job["kind"] == "image":
                        video_source_filename = str(metadata.get("video_source_filename") or "").strip()
                        if video_source_filename:
                            video_source_path = self.asset_dir / Path(video_source_filename).name
                            if video_source_path.exists():
                                video_source_key = f"{job['kind']}/{job['target_id']}/{video_source_path.name}"
                                self.storage.put_file(video_source_path, video_source_key)
                                metadata["video_source_storage_key"] = video_source_key
                provider_default = providers.speech.name if job["kind"] == "narration" else providers.image.name if job["kind"] == "image" else providers.video.name
                provider_name = str(metadata.get("provider", provider_default))
                connection.execute("UPDATE assets SET status = 'ready', url = ?, provider = ?, model = ?, metadata_json = ?, updated_at = ? WHERE id = ?", (asset_url, provider_name, str(metadata.get("model", "")), json.dumps(metadata, ensure_ascii=False), now(), job["target_id"]))
                if job["kind"] == "narration":
                    narration_payload = json_loads(job["payload_json"], {})
                    if bool_value(narration_payload.get("attach_to_timeline", True)):
                        self._attach_narration_to_timeline(
                            connection,
                            str(narration_payload.get("episode_id") or ""),
                            str(job["target_id"]),
                            text=str(narration_payload.get("text") or ""),
                            duration_seconds=metadata.get("duration_seconds"),
                            speed=narration_payload.get("speed", 1.0),
                            auto_subtitles=bool_value(narration_payload.get("auto_subtitles", True)),
                        )
                self._transition_job_status(connection, job_id, "completed", error=None)
                if reservation:
                    self._commit_reservation(connection, reservation["id"])
        except Exception as exc:
            for generated_part in generated_speech_parts:
                (self.asset_dir / Path(generated_part.relative_url).name).unlink(missing_ok=True)
            with self.store.connection() as connection:
                if job["kind"] == "character-reference":
                    connection.execute("UPDATE character_references SET status = 'failed' WHERE id = ?", (job["target_id"],))
                elif job["kind"] in {"image", "video", "narration"}:
                    connection.execute("UPDATE assets SET status = 'failed', updated_at = ? WHERE id = ?", (now(), job["target_id"]))
                self._transition_job_status(connection, job_id, "failed", error=str(exc))
                if reservation:
                    self._refund(connection, reservation["id"], f"{job['kind']} provider failed")
        if job["kind"] == "text":
            return self.text_job_with_job(job["target_id"], user_id)
        if job["kind"] == "prompt":
            return self.prompt_with_job(job["target_id"], user_id)
        if job["kind"] == "story-bible":
            return self.story_bible_with_job(job["target_id"], user_id)
        if job["kind"] == "structure":
            return self._structure_job_result(job, user_id)
        if job["kind"] == "character-reference":
            return self.reference_with_job(job["target_id"], user_id)
        if job["kind"] == "compose":
            return self.composition_with_job(job["target_id"], user_id)
        if job["kind"] == "long-video":
            return self.long_video_with_job(job["target_id"], user_id)
        return self.asset_with_job(job["target_id"], user_id)

    def recover_stale_jobs(
        self,
        max_age_seconds: float | None = None,
        job_id: str | None = None,
        limit: int = 100,
    ) -> int:
        """回收 worker 崩溃后遗留的 running Job。

        任务的 ``updated_at`` 作为轻量租约时间戳：未过期的 running Job 不会被
        并发 worker 误抢；过期任务重新排队并保留原积分预留，达到最大尝试次数
        时才失败并退款。该逻辑不依赖 Redis，SQLite worker 和 BullMQ 重试都可用。
        """
        if max_age_seconds is None:
            try:
                max_age_seconds = max(30.0, float(os.environ.get("STUDIO_JOB_STALE_SECONDS", "1800")))
            except ValueError:
                max_age_seconds = 1800.0
        max_age_seconds = max(1.0, float(max_age_seconds))
        if job_id:
            rows = self.store.all("SELECT * FROM jobs WHERE id = ? AND status = 'running'", (job_id,))
        else:
            rows = self.store.all("SELECT * FROM jobs WHERE status = 'running' ORDER BY updated_at LIMIT ?", (limit,))
        recovered = 0
        current = datetime.now(timezone.utc)
        for row in rows:
            try:
                updated_at = datetime.fromisoformat(str(row["updated_at"]))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                updated_at = current
            if (current - updated_at).total_seconds() < max_age_seconds:
                continue
            attempts = int(row["attempts"] or 0)
            max_attempts = int(row["max_attempts"] or 3)
            terminal = attempts >= max_attempts
            timestamp = now()
            with self.store.connection() as connection:
                next_status = "failed" if terminal else "queued"
                next_error = "worker lease expired" if terminal else "worker lease expired; requeued"
                result = self._transition_job_status(
                    connection,
                    row["id"],
                    next_status,
                    error=next_error,
                    expected_status="running",
                    expected_updated_at=row["updated_at"],
                )
                if not result:
                    continue
                if row["kind"] == "character-reference":
                    connection.execute(
                        "UPDATE character_references SET status = ? WHERE id = ?",
                        ("failed" if terminal else "pending", row["target_id"]),
                    )
                elif row["kind"] in {"image", "video", "narration"}:
                    connection.execute(
                        "UPDATE assets SET status = ?, updated_at = ? WHERE id = ?",
                        ("failed" if terminal else "pending", timestamp, row["target_id"]),
                    )
                if terminal:
                    reservation = connection.execute(
                        "SELECT * FROM credit_reservations WHERE job_id = ? AND status = 'reserved'",
                        (row["id"],),
                    ).fetchone()
                    if reservation:
                        self._refund(connection, reservation["id"], "worker lease expired")
                recovered += 1
        return recovered

    def retry_job(self, job_id: str, user_id: str = DEFAULT_USER_ID, enqueue_external: bool = True) -> dict[str, Any]:
        """显式重试已退款的失败任务，避免隐式重复扣费。"""
        job = self.store.one("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
        if not job:
            raise ServiceError("job not found", 404)
        if job["status"] == "completed":
            if job["kind"] == "text":
                return self.text_job_with_job(job["target_id"], user_id)
            if job["kind"] == "prompt":
                return self.prompt_with_job(job["target_id"], user_id)
            if job["kind"] == "story-bible":
                return self.story_bible_with_job(job["target_id"], user_id)
            if job["kind"] == "structure":
                return self._structure_job_result(job, user_id)
            if job["kind"] == "character-reference":
                return self.reference_with_job(job["target_id"], user_id)
            if job["kind"] == "compose":
                return self.composition_with_job(job["target_id"], user_id)
            if job["kind"] == "long-video":
                return self.long_video_with_job(job["target_id"], user_id)
            return self.asset_with_job(job["target_id"], user_id)
        if job["status"] != "failed":
            raise ServiceError("only failed jobs can be retried", 409)
        if int(job["attempts"] or 0) >= int(job["max_attempts"] or 3):
            raise ServiceError("job retry limit reached", 409)
        with self.store.connection() as connection:
            self._transition_job_status(connection, job_id, "queued", error=None, expected_status="failed")
            self._reserve(connection, user_id, job_id, int(job["cost_credits"]), f"retry {job['kind']} generation")
            if job["kind"] == "character-reference":
                connection.execute("UPDATE character_references SET status = 'pending' WHERE id = ?", (job["target_id"],))
            elif job["kind"] in {"image", "video", "narration"}:
                connection.execute("UPDATE assets SET status = 'pending', updated_at = ? WHERE id = ?", (now(), job["target_id"]))
        if enqueue_external:
            self._enqueue_external(job_id, user_id, job["kind"], job["target_id"])
        if job["kind"] == "text":
            return self.text_job_with_job(job["target_id"], user_id)
        if job["kind"] == "prompt":
            return self.prompt_with_job(job["target_id"], user_id)
        if job["kind"] == "story-bible":
            return self.story_bible_with_job(job["target_id"], user_id)
        if job["kind"] == "structure":
            return self._structure_job_result(job, user_id)
        if job["kind"] == "character-reference":
            return self.reference_with_job(job["target_id"], user_id)
        if job["kind"] == "compose":
            return self.composition_with_job(job["target_id"], user_id)
        if job["kind"] == "long-video":
            return self.long_video_with_job(job["target_id"], user_id)
        return self.asset_with_job(job["target_id"], user_id)

    def cancel_job(self, job_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        """取消尚未被 worker 抢占的任务，并幂等退回其积分预留。"""
        job = self.store.one("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
        if not job:
            raise ServiceError("job not found", 404)
        if job["status"] == "cancelled":
            if job["kind"] == "text":
                return self.text_job_with_job(job["target_id"], user_id)
            if job["kind"] == "prompt":
                return self.prompt_with_job(job["target_id"], user_id)
            if job["kind"] == "story-bible":
                return self.story_bible_with_job(job["target_id"], user_id)
            if job["kind"] == "structure":
                return self._structure_job_result(job, user_id)
            if job["kind"] == "character-reference":
                return self.reference_with_job(job["target_id"], user_id)
            if job["kind"] == "compose":
                return self.composition_with_job(job["target_id"], user_id)
            if job["kind"] == "long-video":
                return self.long_video_with_job(job["target_id"], user_id)
            return self.asset_with_job(job["target_id"], user_id)
        if job["status"] != "queued":
            raise ServiceError("only queued jobs can be cancelled", 409)
        with self.store.connection() as connection:
            claimed = self._transition_job_status(connection, job_id, "cancelled", error="cancelled by user", expected_status="queued")
            if claimed == 0:
                raise ServiceError("job is already running", 409)
            reservation = connection.execute("SELECT * FROM credit_reservations WHERE job_id = ? AND status = 'reserved'", (job_id,)).fetchone()
            if reservation:
                self._refund(connection, reservation["id"], "job cancelled")
            if job["kind"] == "character-reference":
                connection.execute("UPDATE character_references SET status = 'cancelled' WHERE id = ?", (job["target_id"],))
            elif job["kind"] in {"image", "video", "narration"}:
                connection.execute("UPDATE assets SET status = 'cancelled', updated_at = ? WHERE id = ?", (now(), job["target_id"]))
        if job["kind"] == "character-reference":
            return self.reference_with_job(job["target_id"], user_id)
        if job["kind"] == "compose":
            return self.composition_with_job(job["target_id"], user_id)
        if job["kind"] == "text":
            return self.text_job_with_job(job["target_id"], user_id)
        if job["kind"] == "prompt":
            return self.prompt_with_job(job["target_id"], user_id)
        if job["kind"] == "story-bible":
            return self.story_bible_with_job(job["target_id"], user_id)
        if job["kind"] == "structure":
            return self._structure_job_result(job, user_id)
        if job["kind"] == "long-video":
            return self.long_video_with_job(job["target_id"], user_id)
        return self.asset_with_job(job["target_id"], user_id)

    def composition_with_job(self, episode_id: str, user_id: str | None = None) -> dict[str, Any]:
        """返回分集最近一次合成和对应 Job；没有成片时仍保留可轮询的合同。"""
        if user_id is None:
            episode = self.store.one("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        else:
            episode = self.store.one(
                "SELECT e.* FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?",
                (episode_id, user_id),
            )
        if not episode:
            raise ServiceError("episode not found", 404)
        composition = self.store.one("SELECT * FROM compositions WHERE episode_id = ? ORDER BY created_at DESC LIMIT 1", (episode_id,))
        job = self.store.one("SELECT * FROM jobs WHERE target_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1", (episode_id, "compose"))
        result = dict(composition) if composition else {"id": None, "episode_id": episode_id, "status": "pending"}
        if composition and self.storage.mode != "local":
            metadata = json_loads(result.get("metadata_json"), {})
            result["playlist_url"] = self._media_url(result.get("playlist_url"), metadata.get("playlist_storage_key"))
            result["final_video_url"] = self._media_url(result.get("final_video_url"), metadata.get("final_storage_key"))
        if not composition and job and job["status"] in {"failed", "cancelled"}:
            result["status"] = job["status"]
        result["job"] = dict(job) if job else None
        return result

    @staticmethod
    def _audio_contract(tracks: Any, subtitles: Any) -> dict[str, Any]:
        """把 API 的轻量时间线映射到 studio_core.audio 的结构门禁。

        这里不调用 TTS/ASR，也不把本地预览当成真实音频验收；返回值只说明
        结构合同是否通过，最终响度、口型和人耳听审仍属于渲染/QC 阶段。
        """
        issues: list[str] = []
        audio_tracks: list[AudioTrack] = []
        subtitle_cues: list[SubtitleCue] = []
        raw_tracks = tracks if isinstance(tracks, list) else []
        raw_subtitles = subtitles if isinstance(subtitles, list) else []
        for index, item in enumerate(raw_tracks, 1):
            if not isinstance(item, dict):
                issues.append(f"audio track {index} is not an object")
                continue
            raw_kind = str(item.get("kind") or TrackKind.DIALOGUE.value).strip().lower()
            try:
                kind = TrackKind(raw_kind)
            except ValueError:
                issues.append(f"audio track {index} has unsupported kind")
                continue
            try:
                start_sec = float(item.get("start_seconds", 0))
                duration_sec = float(item.get("duration_seconds", 0))
                volume = float(item.get("volume", 1))
            except (TypeError, ValueError):
                issues.append(f"audio track {index} timing or volume is invalid")
                continue
            audio_tracks.append(
                AudioTrack(
                    track_id=str(item.get("track_id") or f"track-{index}"),
                    kind=kind,
                    media_asset_id=str(item.get("asset_id") or "") or None,
                    start_sec=start_sec,
                    duration_sec=duration_sec,
                    gain_db=volume,
                    dip_to_background=bool(item.get("dip_to_background", False)),
                    source_note=str(item.get("source_note") or "studio-settings"),
                    license_ref=str(item.get("license_ref") or "") or None,
                )
            )
        for index, item in enumerate(raw_subtitles, 1):
            if not isinstance(item, dict):
                issues.append(f"subtitle {index} is not an object")
                continue
            try:
                start_sec = float(item.get("start_seconds"))
                end_sec = float(item.get("end_seconds"))
            except (TypeError, ValueError):
                issues.append(f"subtitle {index} timing is invalid")
                continue
            subtitle_cues.append(
                SubtitleCue(
                    cue_id=str(item.get("cue_id") or f"subtitle-{index}"),
                    start_sec=start_sec,
                    end_sec=end_sec,
                    text=str(item.get("text") or ""),
                    speaker=str(item.get("speaker") or "unknown"),
                    language=str(item.get("language") or "zh"),
                    source_text=str(item.get("source_text") or "") or None,
                    revised=bool(item.get("revised", False)),
                )
            )
        issues.extend(audio_gate(audio_tracks, subtitle_cues))
        unique_issues = list(dict.fromkeys(issues))
        return {
            "status": "pass" if not unique_issues else "draft",
            "issues": unique_issues,
            "checked": "structure-only",
            "provider": "studio_core.audio.audio_gate",
        }

    def composition_settings(self, episode_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        episode = self.store.one(
            "SELECT e.id FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?",
            (episode_id, user_id),
        )
        if not episode:
            raise ServiceError("episode not found", 404)
        row = self.store.one("SELECT * FROM composition_settings WHERE episode_id = ?", (episode_id,))
        tracks = json_loads(row["audio_tracks_json"], []) if row else []
        subtitles = json_loads(row["subtitles_json"], []) if row else []
        return {
            "episode_id": episode_id,
            "audio_tracks": tracks,
            "subtitles": subtitles,
            "narration_text": str(row["narration_text"] or "") if row else "",
            "updated_at": row["updated_at"] if row else None,
            "audio_contract": self._audio_contract(tracks, subtitles),
        }

    def update_composition_settings(self, episode_id: str, payload: dict[str, Any], user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        episode = self.store.one(
            "SELECT e.id, e.project_id FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?",
            (episode_id, user_id),
        )
        if not episode:
            raise ServiceError("episode not found", 404)
        raw_tracks = payload.get("audio_tracks", [])
        raw_subtitles = payload.get("subtitles", [])
        existing_settings = self.store.one(
            "SELECT narration_text FROM composition_settings WHERE episode_id = ?",
            (episode_id,),
        )
        raw_narration_text = payload.get("narration_text")
        narration_text = (
            str(existing_settings["narration_text"] or "")
            if raw_narration_text is None and existing_settings
            else str(raw_narration_text or "").strip()
        )
        if len(narration_text) > MAX_NARRATION_TEXT_CHARS:
            raise ServiceError(f"narration text cannot exceed {MAX_NARRATION_TEXT_CHARS} characters", 422)
        if not isinstance(raw_tracks, list) or len(raw_tracks) > 32:
            raise ServiceError("audio_tracks must be a list of at most 32 items")
        if not isinstance(raw_subtitles, list) or len(raw_subtitles) > 500:
            raise ServiceError("subtitles must be a list of at most 500 items")
        tracks: list[dict[str, Any]] = []
        for item in raw_tracks:
            if not isinstance(item, dict):
                raise ServiceError("each audio track must be an object")
            asset_id = str(item.get("asset_id") or "").strip()
            asset = self.store.one(
                "SELECT id FROM assets WHERE id = ? AND project_id = ? AND kind = 'audio' AND status = 'ready'",
                (asset_id, episode["project_id"]),
            )
            if not asset:
                raise ServiceError("audio track must reference a ready audio asset", 422)
            try:
                start_seconds = float(item.get("start_seconds", 0))
                volume = float(item.get("volume", 1))
            except (TypeError, ValueError) as exc:
                raise ServiceError("audio track timing and volume must be numbers", 422) from exc
            if not math.isfinite(start_seconds) or start_seconds < 0 or start_seconds > 900:
                raise ServiceError("audio track start_seconds must be between 0 and 900", 422)
            if not math.isfinite(volume) or volume < 0 or volume > 2:
                raise ServiceError("audio track volume must be between 0 and 2", 422)
            kind = str(item.get("kind") or TrackKind.DIALOGUE.value).strip().lower()
            if kind not in {track_kind.value for track_kind in TrackKind}:
                raise ServiceError("audio track kind is unsupported", 422)
            track = {"asset_id": asset_id, "start_seconds": start_seconds, "volume": volume}
            # 保持旧版导出 JSON 的形状；只有调用方显式提供扩展字段时才落盘。
            if any(key in item for key in ("track_id", "kind", "source_note", "license_ref", "dip_to_background")):
                track_id = str(item.get("track_id") or f"track-{len(tracks) + 1}").strip()
                if not track_id or len(track_id) > 128:
                    raise ServiceError("audio track track_id must contain 1 to 128 characters", 422)
                track.update(
                    {
                        "track_id": track_id,
                        "kind": kind,
                        "source_note": str(item.get("source_note") or "studio-settings").strip()[:240],
                        "license_ref": str(item.get("license_ref") or "").strip()[:500] or None,
                        "dip_to_background": bool(item.get("dip_to_background", False)),
                    }
                )
            tracks.append(track)
        subtitles: list[dict[str, Any]] = []
        for item in raw_subtitles:
            if not isinstance(item, dict):
                raise ServiceError("each subtitle must be an object")
            text = str(item.get("text") or "").strip()
            try:
                start_seconds = float(item.get("start_seconds"))
                end_seconds = float(item.get("end_seconds"))
            except (TypeError, ValueError) as exc:
                raise ServiceError("subtitle timing must be numbers", 422) from exc
            if not text or len(text) > 500:
                raise ServiceError("subtitle text must contain 1 to 500 characters", 422)
            if not math.isfinite(start_seconds) or not math.isfinite(end_seconds) or start_seconds < 0 or end_seconds <= start_seconds or end_seconds > 900:
                raise ServiceError("subtitle timing is invalid", 422)
            subtitle = {"start_seconds": start_seconds, "end_seconds": end_seconds, "text": text}
            if any(key in item for key in ("cue_id", "speaker", "language", "source_text", "revised")):
                subtitle.update(
                    {
                        "cue_id": str(item.get("cue_id") or f"subtitle-{len(subtitles) + 1}").strip()[:128],
                        "speaker": str(item.get("speaker") or "unknown").strip()[:200],
                        "language": str(item.get("language") or "zh").strip()[:16],
                        "source_text": str(item.get("source_text") or "").strip()[:500] or None,
                        "revised": bool(item.get("revised", False)),
                    }
                )
            subtitles.append(subtitle)
        timestamp = now()
        with self.store.connection() as connection:
            connection.execute(
                "INSERT INTO composition_settings(episode_id, audio_tracks_json, subtitles_json, narration_text, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(episode_id) DO UPDATE SET audio_tracks_json = excluded.audio_tracks_json, subtitles_json = excluded.subtitles_json, narration_text = excluded.narration_text, updated_at = excluded.updated_at",
                (episode_id, json.dumps(tracks, ensure_ascii=False), json.dumps(subtitles, ensure_ascii=False), narration_text, timestamp),
            )
        return self.composition_settings(episode_id, user_id)

    def request_composition(
        self,
        episode_id: str,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """本地同步合成；BullMQ 部署下创建可恢复的 compose Job。"""
        episode = self.store.one(
            "SELECT e.* FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?",
            (episode_id, user_id),
        )
        if not episode:
            raise ServiceError("episode not found", 404)
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        if canonical_key:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE idempotency_key = ? AND user_id = ?",
                (canonical_key, user_id),
            )
            if existing:
                if existing["kind"] != "compose" or existing["target_id"] != episode_id:
                    raise ServiceError("Idempotency-Key was already used for another composition request", 409)
                if run_now and existing["status"] == "queued":
                    return self.run_job(str(existing["id"]), user_id)
                if existing["status"] == "queued":
                    self._enqueue_external(existing["id"], user_id, "compose", episode_id)
                return self.composition_with_job(episode_id, user_id)
        if run_now and not canonical_key:
            return self.compose_episode(episode_id, user_id)
        active = self.store.one(
            "SELECT * FROM jobs WHERE target_id = ? AND kind = 'compose' AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
            (episode_id,),
        )
        if active:
            if active["status"] == "queued":
                self._enqueue_external(active["id"], user_id, "compose", episode_id)
            return self.composition_with_job(episode_id, user_id)
        job_id = new_id("job")
        with self.store.connection() as connection:
            inserted = self._insert_job(connection, job_id, user_id, "compose", episode_id, COSTS["compose"], "ffmpeg", canonical_key)
        if not inserted:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE idempotency_key = ? AND user_id = ?",
                (canonical_key, user_id),
            )
            if not existing or existing["kind"] != "compose" or existing["target_id"] != episode_id:
                raise ServiceError("Idempotency-Key was already used for another composition request", 409)
            job_id = str(existing["id"])
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "compose", episode_id)
        return self.composition_with_job(episode_id, user_id)

    def create_compositions(
        self,
        project_id: str,
        episode_ids: list[str] | None = None,
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """批量提交项目分集合成，复用单集 Job 和合成引擎合同。

        已完成或正在运行的分集默认跳过；单集按钮仍可显式重新合成。
        批量入口不收积分，但必须按项目归属校验分集，避免跨项目提交。
        """
        self.get_project(project_id, user_id)
        rows = self.store.all(
            "SELECT e.* FROM episodes e WHERE e.project_id = ? ORDER BY e.number",
            (project_id,),
        )
        by_id = {str(row["id"]): dict(row) for row in rows}
        if episode_ids is None:
            selected_ids = list(by_id)
        else:
            selected_ids = []
            for raw_id in episode_ids:
                normalized = str(raw_id or "").strip()
                if normalized and normalized not in selected_ids:
                    selected_ids.append(normalized)
        if len(selected_ids) > 64:
            raise ServiceError("a composition batch cannot exceed 64 episodes")
        missing = [episode_id for episode_id in selected_ids if episode_id not in by_id]
        if missing:
            raise ServiceError("episode not found", 404)

        items: list[dict[str, Any]] = []
        failed = 0
        skipped = 0
        submitted = 0
        for episode_id in selected_ids:
            episode = by_id[episode_id]
            active = self.store.one(
                "SELECT * FROM jobs WHERE target_id = ? AND kind = 'compose' AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
                (episode_id,),
            )
            latest = self.store.one(
                "SELECT * FROM compositions WHERE episode_id = ? ORDER BY created_at DESC LIMIT 1",
                (episode_id,),
            )
            latest_metadata = json_loads(latest["metadata_json"], {}) if latest else {}
            current_composition_revision = self._current_episode_composition_revision(episode_id)
            latest_is_current = bool(
                latest
                and str(latest["status"]) == "completed"
                and str(latest_metadata.get("artifact_revision") or "") == current_composition_revision
            )
            if active or latest_is_current:
                skipped += 1
                current = self.composition_with_job(episode_id, user_id)
                job = current.get("job") or {}
                items.append(
                    {
                        "episode_id": episode_id,
                        "episode_number": int(episode["number"]),
                        "title": episode["title"],
                        "action": "skipped",
                        "skip_reason": "active" if active else "completed-current",
                        "status": str(job.get("status") or current.get("status") or "pending"),
                        "composition": current,
                    }
                )
                continue
            try:
                child_key = f"{idempotency_key}:episode:{episode_id}" if idempotency_key else None
                composition = self.request_composition(episode_id, user_id, run_now, child_key)
                submitted += 1
                job = composition.get("job") or {}
                items.append(
                    {
                        "episode_id": episode_id,
                        "episode_number": int(episode["number"]),
                        "title": episode["title"],
                        "action": "submitted",
                        "status": str(job.get("status") or composition.get("status") or "pending"),
                        "composition": composition,
                    }
                )
            except ServiceError as exc:
                failed += 1
                items.append(
                    {
                        "episode_id": episode_id,
                        "episode_number": int(episode["number"]),
                        "title": episode["title"],
                        "action": "failed",
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        statuses = {str(item.get("status")) for item in items}
        batch_status = (
            "failed"
            if failed and not submitted and not skipped
            else "partial"
            if failed
            else "queued"
            if statuses & {"pending", "queued", "running"}
            else "completed"
        )
        return {
            "project_id": project_id,
            "status": batch_status,
            "requested": len(selected_ids),
            "submitted": submitted,
            "skipped": skipped,
            "failed": failed,
            "items": items,
        }

    def _render_remotion(self, manifest_path: Path, output_path: Path) -> None:
        """调用独立 Remotion worker；渲染器缺失时由调用方决定是否回退。"""
        renderer_root = self.remotion_renderer_root()
        if not (renderer_root / "package.json").exists():
            raise RuntimeError(f"Remotion renderer package not found: {renderer_root}")
        configured_command = os.environ.get("STUDIO_REMOTION_RENDER_COMMAND", "").strip()
        if configured_command:
            command = shlex.split(configured_command)
        else:
            npm = shutil.which("npm")
            if not npm:
                raise RuntimeError("npm is required for the default Remotion renderer command")
            command = [npm, "run", "render", "--"]
        if not command:
            raise RuntimeError("STUDIO_REMOTION_RENDER_COMMAND is empty")
        command.extend(["--manifest", str(manifest_path), "--output", str(output_path)])
        try:
            completed = subprocess.run(
                command,
                cwd=renderer_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=max(30, min(3600, int(os.environ.get("STUDIO_REMOTION_RENDER_TIMEOUT_SECONDS", "900")))),
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = ""
            if isinstance(exc, subprocess.CalledProcessError):
                detail = (exc.stderr or exc.stdout or "").strip()
            elif isinstance(exc, subprocess.TimeoutExpired):
                detail = "render timed out"
            else:
                detail = str(exc)
            raise RuntimeError(f"Remotion render failed: {detail[-1000:]}") from exc
        if not output_path.exists() or output_path.stat().st_size == 0:
            stdout = (completed.stdout or "").strip()
            raise RuntimeError(f"Remotion renderer did not create output: {stdout[-500:]}")

    @staticmethod
    def remotion_renderer_root() -> Path:
        return Path(
            os.environ.get("STUDIO_REMOTION_ROOT", str(Path(__file__).resolve().parents[1] / "rendering"))
        ).expanduser()

    @staticmethod
    def composition_engine() -> str:
        return os.environ.get("STUDIO_COMPOSE_ENGINE", "ffmpeg").strip().lower()

    @classmethod
    def composition_engine_status(cls) -> dict[str, Any]:
        engine = cls.composition_engine()
        if engine == "playlist":
            return {"engine": engine, "ready": True, "reason": "playlist-only"}
        if engine == "ffmpeg":
            ready = shutil.which("ffmpeg") is not None
            return {"engine": engine, "ready": ready, "reason": "ffmpeg-available" if ready else "ffmpeg-not-found"}
        if engine == "remotion":
            root = cls.remotion_renderer_root()
            configured_command = os.environ.get("STUDIO_REMOTION_RENDER_COMMAND", "").strip()
            try:
                command_parts = shlex.split(configured_command) if configured_command else []
            except ValueError:
                command_parts = []
            if configured_command:
                command_name = command_parts[0] if command_parts else ""
                command_ready = bool(command_name and (Path(command_name).is_file() or shutil.which(command_name)))
            else:
                command_ready = bool(Path("npm").is_file() or shutil.which("npm"))
            package_ready = (root / "package.json").is_file()
            renderer_ready = (root / "node_modules" / "@remotion" / "renderer" / "package.json").is_file()
            # The default command executes the package's `render` script, which
            # is `tsx src/render.ts`. Merely having npm and @remotion/renderer
            # installed is not enough: a partially restored volume would pass
            # readiness and fail only after a paid composition Job starts.
            runner_ready = (
                bool(configured_command and command_parts and command_ready)
                if configured_command
                else bool(
                    (root / "node_modules" / ".bin" / "tsx").is_file()
                    or (root / "node_modules" / ".bin" / "tsx.cmd").is_file()
                )
            )
            ready = command_ready and package_ready and renderer_ready and runner_ready
            return {
                "engine": engine,
                "ready": ready,
                "reason": "remotion-runtime-available" if ready else "remotion-runtime-incomplete",
            }
        return {"engine": engine, "ready": False, "reason": "unsupported-engine"}

    def compose_episode(self, episode_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        episode = self.store.one("SELECT e.*, p.user_id, p.id AS project_id FROM episodes e JOIN projects p ON p.id = e.project_id WHERE e.id = ? AND p.user_id = ?", (episode_id, user_id))
        if not episode:
            raise ServiceError("episode not found", 404)
        shots = self.store.all(
            "SELECT * FROM shots WHERE episode_id = ? AND COALESCE(status, 'active') <> 'superseded' ORDER BY sequence",
            (episode_id,),
        )
        if not shots:
            raise ServiceError("generate shots before composing")
        for shot in shots:
            self._require_current_shot_revision(shot, user_id, "composition")
        compose_engine = self.composition_engine()
        if compose_engine not in {"ffmpeg", "remotion", "playlist"}:
            raise ServiceError("unsupported STUDIO_COMPOSE_ENGINE; use ffmpeg, remotion, or playlist")
        settings = self.composition_settings(episode_id, user_id)
        render_audio_tracks: list[dict[str, Any]] = []
        for track in settings["audio_tracks"]:
            asset = self.store.one("SELECT * FROM assets WHERE id = ? AND project_id = ? AND kind = 'audio' AND status = 'ready'", (track["asset_id"], episode["project_id"]))
            if not asset:
                raise ServiceError("composition settings reference a missing audio asset", 409)
            audio_data = dict(asset)
            audio_data["metadata"] = json_loads(audio_data.pop("metadata_json", "{}"), {})
            materialized = self._materialize_asset_file(audio_data)
            if not materialized:
                raise ServiceError("audio asset bytes are unavailable for rendering", 409)
            render_audio_tracks.append({
                "id": asset["id"],
                "url": asset["url"],
                "source_path": str(materialized) if materialized else None,
                "start_seconds": track["start_seconds"],
                "volume": track["volume"],
            })
        if compose_engine in {"ffmpeg", "playlist"} and (render_audio_tracks or settings["subtitles"]):
            raise ServiceError("audio tracks or subtitles require STUDIO_COMPOSE_ENGINE=remotion", 409)
        playlist = []
        render_items = []
        video_paths: list[Path] = []
        composition_bindings: list[dict[str, Any]] = []
        for shot in shots:
            expected_artifact_revision = self._shot_artifact_revision(shot)
            adopted_image = None
            for candidate in self.store.all(
                "SELECT * FROM assets WHERE project_id = ? AND shot_id = ? AND kind = 'image' "
                "AND status = 'ready' AND selected = 1 AND consistency_confirmed = 1 "
                "ORDER BY updated_at DESC, created_at DESC, id DESC",
                (episode["project_id"], shot["id"]),
            ):
                candidate_metadata = json_loads(candidate["metadata_json"], {})
                if str(candidate_metadata.get("artifact_revision") or "") == expected_artifact_revision:
                    adopted_image = candidate
                    break
            if not adopted_image:
                raise ServiceError(f"shot {shot['sequence']} has no adopted keyframe", 409)
            self._require_visual_pass(str(adopted_image["id"]), "composition")
            image_metadata = json_loads(adopted_image["metadata_json"], {})
            video = None
            for candidate in self.store.all(
                "SELECT video.* FROM assets video JOIN assets image ON image.id = video.source_asset_id "
                "WHERE video.shot_id = ? AND video.kind = 'video' AND video.status = 'ready' "
                "AND image.kind = 'image' AND image.status = 'ready' AND image.selected = 1 "
                "AND image.consistency_confirmed = 1 AND video.source_asset_id = ? "
                "ORDER BY video.created_at DESC, video.id DESC",
                (shot["id"], adopted_image["id"]),
            ):
                candidate_metadata = json_loads(candidate["metadata_json"], {})
                if str(candidate_metadata.get("source_artifact_revision") or "") == str(image_metadata.get("artifact_revision") or ""):
                    video = candidate
                    break
            if not video:
                raise ServiceError(f"shot {shot['sequence']} has no ready video for the adopted keyframe")
            video_data = dict(video)
            video_data["metadata"] = json_loads(video_data.pop("metadata_json", "{}"), {})
            materialized = self._materialize_asset_file(video_data)
            if materialized:
                video_paths.append(materialized)
            playlist_item = {
                "sequence": shot["sequence"],
                "shot_id": shot["id"],
                "asset_id": video["id"],
                "duration_seconds": float(shot["duration_seconds"] or 3),
                "url": video["url"],
            }
            playlist.append(playlist_item)
            render_items.append({**playlist_item, "source_path": str(materialized) if materialized else None, "title": shot["description"]})
            composition_bindings.append(
                {
                    "shot_id": str(shot["id"]),
                    "sequence": int(shot["sequence"]),
                    "image_asset_id": str(adopted_image["id"]),
                    "video_asset_id": str(video["id"]),
                    "artifact_revision": str(image_metadata.get("artifact_revision") or ""),
                }
            )
        composition_id = new_id("composition")
        composition_artifact_revision = self._episode_composition_revision(
            episode_id,
            sorted(composition_bindings, key=lambda item: (item["sequence"], item["shot_id"])),
        )
        playlist_path = self.asset_dir / f"{composition_id}.json"
        playlist_path.write_text(json.dumps(playlist, ensure_ascii=False, indent=2), encoding="utf-8")
        final_video_url = None
        remotion_error = None
        if compose_engine == "remotion":
            manifest_dir = self.asset_dir.parent / "remotion-manifests"
            manifest_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = manifest_dir / f"{composition_id}.json"
            final_path = self.asset_dir / f"{composition_id}.mp4"
            manifest_path.write_text(
                json.dumps(
                    {
                        "composition_id": composition_id,
                        "title": episode["title"],
                        "items": render_items,
                        "audio_tracks": render_audio_tracks,
                        "subtitles": settings["subtitles"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            try:
                self._render_remotion(manifest_path, final_path)
                final_video_url = f"/assets/{final_path.name}"
            except Exception as exc:
                remotion_error = str(exc)[-1000:]
            finally:
                manifest_path.unlink(missing_ok=True)
        can_fallback_to_video_only = not render_audio_tracks and not settings["subtitles"]
        if final_video_url is None and compose_engine in {"ffmpeg", "remotion"} and can_fallback_to_video_only and shutil.which("ffmpeg") and all(item["url"] and item["url"].endswith(".mp4") for item in playlist):
            candidate_paths = video_paths
            if len(candidate_paths) == len(playlist) and all(path.exists() for path in candidate_paths):
                concat_path = self.asset_dir / f"{composition_id}.concat.txt"
                concat_path.write_text("\n".join(f"file '{path.as_posix().replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for path in candidate_paths) + "\n", encoding="utf-8")
                final_path = self.asset_dir / f"{composition_id}.mp4"
                try:
                    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_path), "-c", "copy", "-y", str(final_path)], check=True)
                    final_video_url = f"/assets/{final_path.name}"
                except (OSError, subprocess.CalledProcessError):
                    final_video_url = None
        mode = "remotion" if compose_engine == "remotion" and final_video_url else ("ffmpeg" if final_video_url else "local-playlist")
        composition_metadata: dict[str, Any] = {
            "mode": mode,
            "requested_engine": compose_engine,
            "items": len(playlist),
            "audio_tracks": len(render_audio_tracks),
            "subtitles": len(settings["subtitles"]),
            "artifact_revision": composition_artifact_revision,
        }
        if remotion_error:
            composition_metadata["remotion_fallback"] = remotion_error
        playlist_url = f"/assets/{playlist_path.name}"
        if self.storage.mode != "local":
            playlist_key = f"compositions/{composition_id}/{playlist_path.name}"
            playlist_url = self.storage.put_file(playlist_path, playlist_key)
            composition_metadata.update({"storage": self.storage.mode, "playlist_storage_key": playlist_key})
            if final_video_url and (self.asset_dir / Path(final_video_url).name).exists():
                final_path = self.asset_dir / Path(final_video_url).name
                final_key = f"compositions/{composition_id}/{final_path.name}"
                final_video_url = self.storage.put_file(final_path, final_key)
                composition_metadata["final_storage_key"] = final_key
        with self.store.connection() as connection:
            connection.execute(
                "INSERT INTO compositions(id, episode_id, playlist_url, final_video_url, status, metadata_json, created_at) VALUES (?, ?, ?, ?, 'completed', ?, ?)",
                (composition_id, episode_id, playlist_url, final_video_url, json.dumps(composition_metadata, ensure_ascii=False), now()),
            )
        return dict(self.store.one("SELECT * FROM compositions WHERE id = ?", (composition_id,)))

    def graph(self, project_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        self.get_project(project_id, user_id)
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        project_node = f"project:{project_id}"
        nodes.append({"id": project_node, "type": "project", "entity_id": project_id, "label": self.store.one("SELECT title FROM projects WHERE id = ?", (project_id,))["title"], "status": "normal"})
        for character in self.store.all("SELECT * FROM characters WHERE project_id = ?", (project_id,)):
            node_id = f"character:{character['id']}"
            nodes.append({"id": node_id, "type": "character", "entity_id": character["id"], "label": character["name"], "status": character["status"]})
            edges.append({"id": f"edge:{project_node}:{node_id}", "source": project_node, "target": node_id, "relation": "contains"})
            for reference in self.store.all("SELECT * FROM character_references WHERE character_id = ? ORDER BY id", (character["id"],)):
                reference_node = f"character-reference:{reference['id']}"
                nodes.append({"id": reference_node, "type": "character-reference", "entity_id": reference["id"], "label": f"{character['name']} 三视图", "status": reference["status"]})
                edges.append({"id": f"edge:{node_id}:{reference_node}", "source": node_id, "target": reference_node, "relation": "reference"})
        for entity in self.store.all(
            "SELECT * FROM story_entities WHERE project_id = ? AND status <> 'superseded' ORDER BY kind, name, id",
            (project_id,),
        ):
            entity_node = f"story-entity:{entity['id']}"
            nodes.append({"id": entity_node, "type": entity["kind"], "entity_id": entity["id"], "label": entity["name"], "status": entity["status"]})
            edges.append({"id": f"edge:{project_node}:{entity_node}", "source": project_node, "target": entity_node, "relation": "contains"})
        for relationship in self.store.all(
            "SELECT * FROM story_relationships WHERE project_id = ? AND status <> 'superseded' ORDER BY created_at, id",
            (project_id,),
        ):
            source_node = f"character:{relationship['source_id']}" if relationship["source_type"] == "character" else f"story-entity:{relationship['source_id']}"
            target_node = f"character:{relationship['target_id']}" if relationship["target_type"] == "character" else f"story-entity:{relationship['target_id']}"
            node_ids = {node["id"] for node in nodes}
            if source_node in node_ids and target_node in node_ids:
                edges.append({"id": f"story-edge:{relationship['id']}", "source": source_node, "target": target_node, "relation": relationship["relation"]})
        for episode in self.store.all("SELECT * FROM episodes WHERE project_id = ? ORDER BY number", (project_id,)):
            episode_node = f"episode:{episode['id']}"
            nodes.append({"id": episode_node, "type": "episode", "entity_id": episode["id"], "label": episode["title"], "status": episode["status"]})
            edges.append({"id": f"edge:{project_node}:{episode_node}", "source": project_node, "target": episode_node, "relation": "contains"})
            for shot in self.store.all("SELECT * FROM shots WHERE episode_id = ? ORDER BY sequence", (episode["id"],)):
                shot_node = f"shot:{shot['id']}"
                nodes.append({"id": shot_node, "type": "shot", "entity_id": shot["id"], "label": f"镜头 {shot['sequence']}", "status": "normal"})
                edges.append({"id": f"edge:{episode_node}:{shot_node}", "source": episode_node, "target": shot_node, "relation": "contains"})
                assets = self.store.all("SELECT * FROM assets WHERE shot_id = ? ORDER BY created_at", (shot["id"],))
                for asset in assets:
                    asset_node = f"asset:{asset['id']}"
                    status = "ready" if asset["status"] == "ready" else ("missing" if asset["status"] == "failed" else asset["status"])
                    nodes.append({"id": asset_node, "type": asset["kind"], "entity_id": asset["id"], "label": f"{asset['kind']} {asset['id'][-4:]}", "status": status})
                    edges.append({"id": f"edge:{shot_node}:{asset_node}", "source": shot_node, "target": asset_node, "relation": "produces"})
                    if asset["character_id"]:
                        character_node = f"character:{asset['character_id']}"
                        if any(node["id"] == character_node for node in nodes):
                            edges.append({"id": f"edge:{character_node}:{asset_node}:reference", "source": character_node, "target": asset_node, "relation": "uses-reference"})
        return {"nodes": nodes, "edges": edges}

    def export_project(self, project_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        """输出不含凭据的项目交换包，供 CLI/文件优先工作流继续处理。"""
        project = self.get_project(project_id, user_id)
        documents = [dict(row) for row in self.store.all("SELECT * FROM source_documents WHERE project_id = ? ORDER BY created_at", (project_id,))]
        segments = [dict(row) for row in self.store.all("SELECT * FROM source_segments WHERE project_id = ? ORDER BY chapter_no, sequence", (project_id,))]
        units = [self._adaptation_dict(dict(row)) for row in self.store.all("SELECT * FROM adaptation_units WHERE project_id = ? ORDER BY chapter_no, sequence", (project_id,))]
        revisions = [self._revision_dict(dict(row)) for row in self.store.all("SELECT * FROM adaptation_revisions WHERE project_id = ? ORDER BY adaptation_unit_id, version", (project_id,))]
        return {
            "schema": "ai-manhua-studio/project-export/v1",
            "exported_at": now(),
            "project": project,
            "source_documents": documents,
            "source_segments": segments,
            "adaptation_units": units,
            "adaptation_revisions": revisions,
            "characters": project.get("characters", []),
            "story_entities": project.get("story_bible", {}).get("entities", []),
            "story_relationships": project.get("story_bible", {}).get("relationships", []),
            "story_bible_runs": [
                dict(row)
                for row in self.store.all(
                    "SELECT id, provider, model, source_sha256, output_json, status, error, created_at FROM story_bible_runs WHERE project_id = ? ORDER BY created_at, id",
                    (project_id,),
                )
            ],
            "episodes": project.get("episodes", []),
            "audio_assets": project.get("audio_assets", []),
        }

    def import_project_bundle(
        self,
        bundle: dict[str, Any],
        user_id: str = DEFAULT_USER_ID,
        mappings: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """将不含凭据的交换包导入为一个全新的用户项目。

        导入永远重新生成实体 ID，不复制 jobs、积分预留、session 或 provider
        credential；生成结果只作为可审计的资产快照保留，后续生成需要重新创建 Job。
        `mappings` 只供归档导入在写回二进制素材时使用，不会进入 API 响应。
        """
        if not isinstance(bundle, dict) or bundle.get("schema") != "ai-manhua-studio/project-export/v1":
            raise ServiceError("unsupported project export schema", 422)
        source_project = bundle.get("project")
        if not isinstance(source_project, dict):
            raise ServiceError("project export is missing project", 422)
        title = str(source_project.get("title") or "导入项目").strip()[:240]
        if not title:
            raise ServiceError("imported project title is required", 422)

        def items(name: str) -> list[dict[str, Any]]:
            value = bundle.get(name, [])
            if value is None:
                return []
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ServiceError(f"project export field {name} must be an object list", 422)
            return value

        def bounded_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError, OverflowError):
                return default

        def json_object(value: Any) -> dict[str, Any]:
            return value if isinstance(value, dict) else {}

        def safe_url(value: Any) -> str | None:
            candidate = str(value or "").strip()
            if not candidate:
                return None
            if candidate.startswith("/") or candidate.startswith("https://") or candidate.startswith("http://"):
                return candidate[:2048]
            return None

        def flag(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value != 0
            return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

        def status(value: Any, allowed: set[str], default: str) -> str:
            candidate = str(value or default)
            return candidate if candidate in allowed else default

        documents = items("source_documents")
        segments = items("source_segments")
        units = items("adaptation_units")
        revisions = items("adaptation_revisions")
        characters = items("characters")
        story_entities = items("story_entities")
        story_relationships = items("story_relationships")
        story_bible_runs = items("story_bible_runs")
        episodes = items("episodes")
        audio_assets = items("audio_assets")
        project_id = new_id("project")
        source_project_id = str(source_project.get("id") or "")
        document_ids: dict[str, str] = {}
        segment_ids: dict[str, str] = {}
        unit_ids: dict[str, str] = {}
        character_ids: dict[str, str] = {}
        story_entity_ids: dict[str, str] = {}
        reference_ids: dict[str, str] = {}
        episode_ids: dict[str, str] = {}
        shot_ids: dict[str, str] = {}
        asset_ids: dict[str, str] = {}
        composition_ids: dict[str, str] = {}
        pending_source_asset_ids: list[tuple[str, str]] = []
        selected_image_ids: dict[str, str] = {}
        for episode in episodes:
            for shot in episode.get("shots", []) if isinstance(episode.get("shots"), list) else []:
                if not isinstance(shot, dict):
                    continue
                candidates = [
                    asset
                    for asset in shot.get("assets", []) if isinstance(shot.get("assets"), list)
                    if isinstance(asset, dict)
                    and str(asset.get("kind") or "") == "image"
                    and flag(asset.get("selected"))
                    and str(asset.get("url") or "").strip()
                ]
                if candidates:
                    winner = max(
                        candidates,
                        key=lambda asset: (
                            str(asset.get("updated_at") or ""),
                            str(asset.get("created_at") or ""),
                            str(asset.get("id") or ""),
                        ),
                    )
                    selected_image_ids[str(shot.get("id") or "")] = str(winner.get("id") or "")
        timestamp = now()
        self.ensure_user(user_id)

        with self.store.connection() as connection:
            self._ensure_user_connection(connection, user_id)
            connection.execute(
                "INSERT INTO projects(id, user_id, title, story, style, episode_length, source_document_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    project_id,
                    user_id,
                    title,
                    str(source_project.get("story") or ""),
                    str(source_project.get("style") or "国漫写实")[:120],
                    str(source_project.get("episode_length") or source_project.get("episodeLength") or "1min")[:40],
                    status(source_project.get("status"), {"draft", "active", "archived"}, "draft"),
                    timestamp,
                    timestamp,
                ),
            )

            for document in documents:
                old_id = str(document.get("id") or new_id("source-old"))
                text = str(document.get("text") or "")
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                declared_digest = str(document.get("content_sha256") or digest)
                if declared_digest != digest:
                    raise ServiceError(f"source document hash mismatch: {document.get('filename', old_id)}", 422)
                new_document_id = new_id("source")
                document_ids[old_id] = new_document_id
                filename = Path(str(document.get("filename") or "source.txt")).name or "source.txt"
                connection.execute(
                    "INSERT INTO source_documents(id, project_id, filename, media_type, content_sha256, text, copyright_acknowledged, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_document_id,
                        project_id,
                        filename[:512],
                        str(document.get("media_type") or "text/plain")[:120],
                        digest,
                        text,
                        flag(document.get("copyright_acknowledged")),
                        str(document.get("created_at") or timestamp),
                    ),
                )

            for segment in segments:
                old_id = str(segment.get("id") or new_id("segment-old"))
                source_document_id = document_ids.get(str(segment.get("source_document_id") or ""))
                if not source_document_id:
                    raise ServiceError("source segment references an unknown document", 422)
                new_segment_id = new_id("segment")
                segment_ids[old_id] = new_segment_id
                connection.execute(
                    "INSERT INTO source_segments(id, project_id, source_document_id, chapter_no, sequence, text, start_offset, end_offset, line_start, line_end) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_segment_id,
                        project_id,
                        source_document_id,
                        bounded_int(segment.get("chapter_no"), 1),
                        bounded_int(segment.get("sequence"), 1),
                        str(segment.get("text") or ""),
                        max(0, bounded_int(segment.get("start_offset"))),
                        max(0, bounded_int(segment.get("end_offset"))),
                        max(1, bounded_int(segment.get("line_start"), 1)),
                        max(1, bounded_int(segment.get("line_end"), 1)),
                    ),
                )

            fallback_document_id = next(iter(document_ids.values()), None)
            for unit in units:
                old_unit_id = str(unit.get("id") or new_id("unit-old"))
                old_segment_id = str(unit.get("source_segment_id") or "")
                new_segment_id = segment_ids.get(old_segment_id)
                traceability = json_object(unit.get("traceability"))
                if not new_segment_id:
                    fallback_document_id = document_ids.get(str(traceability.get("source_document_id") or ""), fallback_document_id)
                    if not fallback_document_id:
                        raise ServiceError("adaptation unit has no importable source document", 422)
                    new_segment_id = new_id("segment")
                    segment_ids[old_segment_id or f"unit:{old_unit_id}"] = new_segment_id
                    source_text = str(unit.get("source_text") or "")
                    connection.execute(
                        "INSERT INTO source_segments(id, project_id, source_document_id, chapter_no, sequence, text, start_offset, end_offset, line_start, line_end) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            new_segment_id,
                            project_id,
                            fallback_document_id,
                            bounded_int(unit.get("chapter_no"), 1),
                            bounded_int(unit.get("sequence"), 1),
                            source_text,
                            max(0, bounded_int(traceability.get("start_offset"))),
                            max(0, bounded_int(traceability.get("end_offset"), len(source_text))),
                            max(1, bounded_int(traceability.get("line_start"), 1)),
                            max(1, bounded_int(traceability.get("line_end"), 1)),
                        ),
                    )
                new_unit_id = new_id("unit")
                unit_ids[old_unit_id] = new_unit_id
                traceability = dict(traceability)
                traceability["adaptation_quality"] = self._adaptation_quality(
                    str(unit.get("source_text") or ""),
                    str(unit.get("adapted_text") or unit.get("source_text") or ""),
                    status(unit.get("mode"), {"faithful", "condensed", "originalized"}, "faithful"),
                )
                if old_segment_id and old_segment_id in segment_ids:
                    traceability["source_segment_id"] = segment_ids[old_segment_id]
                traceability["imported_from_project_id"] = source_project_id
                connection.execute(
                    "INSERT INTO adaptation_units(id, project_id, source_segment_id, chapter_no, sequence, source_text, adapted_text, mode, status, traceability_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_unit_id,
                        project_id,
                        new_segment_id,
                        bounded_int(unit.get("chapter_no"), 1),
                        bounded_int(unit.get("sequence"), 1),
                        str(unit.get("source_text") or ""),
                        str(unit.get("adapted_text") or unit.get("source_text") or ""),
                        status(unit.get("mode"), {"faithful", "condensed", "originalized"}, "faithful"),
                        status(unit.get("status"), {"draft", "review", "approved", "rejected"}, "draft"),
                        json.dumps(traceability, ensure_ascii=False),
                    ),
                )
                unit_revisions = [
                    revision for revision in revisions
                    if str(revision.get("adaptation_unit_id") or "") == old_unit_id
                ]
                if not unit_revisions and isinstance(unit.get("revisions"), list):
                    unit_revisions = [revision for revision in unit["revisions"] if isinstance(revision, dict)]
                if not unit_revisions:
                    self._record_adaptation_revision(
                        connection,
                        new_unit_id,
                        project_id,
                        str(unit.get("source_text") or ""),
                        str(unit.get("adapted_text") or unit.get("source_text") or ""),
                        status(unit.get("mode"), {"faithful", "condensed", "originalized"}, "faithful"),
                        "imported",
                        {"event": "project_import"},
                    )
                else:
                    used_versions: set[int] = set()
                    for order, revision in enumerate(sorted(unit_revisions, key=lambda value: bounded_int(value.get("version"), 0)), 1):
                        version = max(1, bounded_int(revision.get("version"), order))
                        while version in used_versions:
                            version += 1
                        used_versions.add(version)
                        self._record_adaptation_revision(
                            connection,
                            new_unit_id,
                            project_id,
                            str(revision.get("source_text") or unit.get("source_text") or ""),
                            str(revision.get("adapted_text") or unit.get("source_text") or ""),
                            status(revision.get("mode"), {"faithful", "condensed", "originalized"}, "faithful"),
                            str(revision.get("provider") or "imported"),
                            json_object(revision.get("metadata")),
                            version,
                        )

            source_document_id = document_ids.get(str(source_project.get("source_document_id") or ""))
            if source_document_id:
                connection.execute("UPDATE projects SET source_document_id = ? WHERE id = ?", (source_document_id, project_id))

            for character in characters:
                name = str(character.get("name") or "").strip()[:120]
                if not name:
                    continue
                old_id = str(character.get("id") or new_id("character-old"))
                new_character_id = new_id("character")
                character_ids[old_id] = new_character_id
                connection.execute(
                    "INSERT INTO characters(id, project_id, name, role, description, visual_lock_json, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_character_id,
                        project_id,
                        name,
                        str(character.get("role") or "supporting")[:40],
                        str(character.get("description") or "")[:2000],
                        json.dumps(json_object(character.get("visual_lock")), ensure_ascii=False),
                        status(character.get("status"), {"draft", "reference_ready"}, "draft"),
                    ),
                )
                references = character.get("references") if isinstance(character.get("references"), list) else []
                for reference in references:
                    if not isinstance(reference, dict):
                        continue
                    old_reference_id = str(reference.get("id") or new_id("reference-old"))
                    new_reference_id = new_id("reference")
                    reference_ids[old_reference_id] = new_reference_id
                    front_url = safe_url(reference.get("front_url"))
                    side_url = safe_url(reference.get("side_url"))
                    back_url = safe_url(reference.get("back_url"))
                    reference_status = "ready" if front_url and side_url and back_url else "pending"
                    connection.execute(
                        "INSERT INTO character_references(id, character_id, front_url, side_url, back_url, provider, model, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            new_reference_id,
                            new_character_id,
                            front_url,
                            side_url,
                            back_url,
                            str(reference.get("provider") or "imported")[:120],
                            str(reference.get("model") or "imported")[:256],
                            reference_status,
                        ),
                    )

            for entity in story_entities:
                old_entity_id = str(entity.get("id") or new_id("story-entity-old"))
                kind = str(entity.get("kind") or "").strip().lower()
                name = str(entity.get("name") or "").strip()[:120]
                if kind not in {"location", "prop"} or not name:
                    continue
                new_entity_id = new_id("story-entity")
                story_entity_ids[old_entity_id] = new_entity_id
                raw_source_ids = entity.get("source_segment_ids")
                if not isinstance(raw_source_ids, list):
                    raw_source_ids = json_loads(entity.get("source_segment_ids_json"), [])
                source_ids = [segment_ids[str(value)] for value in raw_source_ids if str(value) in segment_ids]
                attributes = entity.get("attributes")
                if not isinstance(attributes, dict):
                    attributes = json_loads(entity.get("attributes_json"), {})
                connection.execute(
                    "INSERT INTO story_entities(id, project_id, kind, name, description, attributes_json, source_segment_ids_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_entity_id,
                        project_id,
                        kind,
                        name,
                        str(entity.get("description") or "")[:4000],
                        json.dumps(attributes if isinstance(attributes, dict) else {}, ensure_ascii=False),
                        json.dumps(source_ids, ensure_ascii=False),
                        status(entity.get("status"), {"draft", "approved", "rejected", "superseded"}, "draft"),
                        str(entity.get("created_at") or timestamp),
                        str(entity.get("updated_at") or timestamp),
                    ),
                )

            for relationship in story_relationships:
                source_type = str(relationship.get("source_type") or "").strip()
                target_type = str(relationship.get("target_type") or "").strip()
                source_old_id = str(relationship.get("source_id") or "")
                target_old_id = str(relationship.get("target_id") or "")
                source_id = character_ids.get(source_old_id) if source_type == "character" else story_entity_ids.get(source_old_id)
                target_id = character_ids.get(target_old_id) if target_type == "character" else story_entity_ids.get(target_old_id)
                relation = str(relationship.get("relation") or "").strip()[:120]
                if source_type not in {"character", "entity"} or target_type not in {"character", "entity"} or not source_id or not target_id or not relation:
                    continue
                raw_source_ids = relationship.get("source_segment_ids")
                if not isinstance(raw_source_ids, list):
                    raw_source_ids = json_loads(relationship.get("source_segment_ids_json"), [])
                source_ids = [segment_ids[str(value)] for value in raw_source_ids if str(value) in segment_ids]
                connection.execute(
                    "INSERT INTO story_relationships(id, project_id, source_type, source_id, target_type, target_id, relation, description, source_segment_ids_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id("story-rel"),
                        project_id,
                        source_type,
                        source_id,
                        target_type,
                        target_id,
                        relation,
                        str(relationship.get("description") or "")[:2000],
                        json.dumps(source_ids, ensure_ascii=False),
                        status(relationship.get("status"), {"draft", "approved", "rejected", "superseded"}, "draft"),
                        str(relationship.get("created_at") or timestamp),
                        str(relationship.get("updated_at") or timestamp),
                    ),
                )

            for run in story_bible_runs:
                output = run.get("output")
                if not isinstance(output, dict):
                    output = json_loads(run.get("output_json"), {})
                output = dict(output) if isinstance(output, dict) else {}
                output["imported_from_project_id"] = source_project_id
                connection.execute(
                    "INSERT INTO story_bible_runs(id, project_id, provider, model, source_sha256, output_json, status, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id("story-run"),
                        project_id,
                        str(run.get("provider") or "imported")[:120],
                        str(run.get("model") or "")[:256],
                        str(run.get("source_sha256") or "")[:128],
                        json.dumps(output, ensure_ascii=False),
                        status(run.get("status"), {"completed", "failed"}, "completed"),
                        str(run.get("error") or "")[:500] or None,
                        str(run.get("created_at") or timestamp),
                    ),
                )

            for asset in audio_assets:
                old_asset_id = str(asset.get("id") or new_id("asset-old"))
                new_asset_id = new_id("asset")
                asset_ids[old_asset_id] = new_asset_id
                asset_url = safe_url(asset.get("url"))
                asset_status = status(asset.get("status"), {"pending", "ready", "failed", "cancelled"}, "pending")
                if asset_status == "ready" and not asset_url:
                    asset_status = "pending"
                metadata = json_object(asset.get("metadata"))
                metadata["imported_snapshot"] = True
                connection.execute(
                    "INSERT INTO assets(id, project_id, shot_id, character_id, kind, status, url, selected, consistency_confirmed, source_asset_id, provider, model, metadata_json, created_at, updated_at) VALUES (?, ?, NULL, NULL, 'audio', ?, ?, 0, 0, NULL, ?, ?, ?, ?, ?)",
                    (
                        new_asset_id,
                        project_id,
                        asset_status,
                        asset_url,
                        str(asset.get("provider") or "imported")[:120],
                        str(asset.get("model") or "imported")[:256],
                        json.dumps(metadata, ensure_ascii=False),
                        str(asset.get("created_at") or timestamp),
                        str(asset.get("updated_at") or timestamp),
                    ),
                )

            used_episode_numbers: set[int] = set()
            for episode in episodes:
                title_value = str(episode.get("title") or f"第{bounded_int(episode.get('number'), 1)}集")[:240]
                old_episode_id = str(episode.get("id") or new_id("episode-old"))
                new_episode_id = new_id("episode")
                episode_ids[old_episode_id] = new_episode_id
                episode_number = max(1, bounded_int(episode.get("number"), len(episode_ids)))
                while episode_number in used_episode_numbers:
                    episode_number += 1
                used_episode_numbers.add(episode_number)
                connection.execute(
                    "INSERT INTO episodes(id, project_id, number, title, summary, conflict, hook, target_duration_seconds, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_episode_id,
                        project_id,
                        episode_number,
                        title_value,
                        str(episode.get("summary") or "")[:4000],
                        str(episode.get("conflict") or "")[:1000],
                        str(episode.get("hook") or "")[:1000],
                        max(15, min(900, bounded_int(episode.get("target_duration_seconds"), 60))),
                        status(episode.get("status"), {"draft", "ready", "composed"}, "draft"),
                    ),
                )
                used_shot_sequences: set[int] = set()
                for shot in episode.get("shots", []) if isinstance(episode.get("shots"), list) else []:
                    if not isinstance(shot, dict):
                        continue
                    old_shot_id = str(shot.get("id") or new_id("shot-old"))
                    new_shot_id = new_id("shot")
                    shot_ids[old_shot_id] = new_shot_id
                    shot_sequence = max(1, bounded_int(shot.get("sequence"), len(used_shot_sequences) + 1))
                    while shot_sequence in used_shot_sequences:
                        shot_sequence += 1
                    used_shot_sequences.add(shot_sequence)
                    adapted_ids = [unit_ids[str(value)] for value in (shot.get("adaptation_unit_ids") or []) if str(value) in unit_ids]
                    try:
                        duration_seconds = float(shot.get("duration_seconds") or 3)
                        if not math.isfinite(duration_seconds):
                            raise ValueError
                        duration_seconds = max(1.0, min(30.0, duration_seconds))
                    except (TypeError, ValueError):
                        duration_seconds = 3.0
                    connection.execute(
                        "INSERT INTO shots(id, episode_id, sequence, scene, emotion, duration_seconds, description, adaptation_unit_ids_json, image_prompt_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                        (
                            new_shot_id,
                            new_episode_id,
                            shot_sequence,
                            str(shot.get("scene") or "")[:500],
                            str(shot.get("emotion") or "")[:500],
                            duration_seconds,
                            str(shot.get("description") or "")[:4000],
                            json.dumps(adapted_ids, ensure_ascii=False),
                        ),
                    )
                    prompt = shot.get("image_prompt")
                    if isinstance(prompt, dict) and str(prompt.get("prompt") or "").strip():
                        prompt_id = new_id("prompt")
                        connection.execute(
                            "INSERT INTO image_prompts(id, shot_id, prompt, negative_prompt, provider, model) VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                prompt_id,
                                new_shot_id,
                                str(prompt.get("prompt") or "")[:6000],
                                str(prompt.get("negative_prompt") or "")[:2000],
                                str(prompt.get("provider") or "imported")[:120],
                                str(prompt.get("model") or "imported")[:256],
                            ),
                        )
                        connection.execute("UPDATE shots SET image_prompt_id = ? WHERE id = ?", (prompt_id, new_shot_id))
                    for asset in shot.get("assets", []) if isinstance(shot.get("assets"), list) else []:
                        if not isinstance(asset, dict):
                            continue
                        old_asset_id = str(asset.get("id") or new_id("asset-old"))
                        new_asset_id = new_id("asset")
                        asset_ids[old_asset_id] = new_asset_id
                        asset_kind = status(asset.get("kind"), {"image", "video", "audio"}, "image")
                        asset_url = safe_url(asset.get("url"))
                        asset_status = status(asset.get("status"), {"pending", "ready", "failed", "cancelled"}, "pending")
                        if asset_status == "ready" and not asset_url:
                            asset_status = "pending"
                        metadata = json_object(asset.get("metadata"))
                        metadata["imported_snapshot"] = True
                        old_source_asset_id = str(asset.get("source_asset_id") or "")
                        if old_source_asset_id:
                            pending_source_asset_ids.append((new_asset_id, old_source_asset_id))
                        is_selected = asset_kind == "image" and old_asset_id == selected_image_ids.get(old_shot_id) and bool(asset_url)
                        connection.execute(
                            "INSERT INTO assets(id, project_id, shot_id, character_id, kind, status, url, selected, consistency_confirmed, source_asset_id, provider, model, metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                new_asset_id,
                                project_id,
                                new_shot_id,
                                character_ids.get(str(asset.get("character_id") or "")),
                                asset_kind,
                                asset_status,
                                asset_url,
                                is_selected,
                                flag(asset.get("consistency_confirmed")) and is_selected,
                                None,
                                str(asset.get("provider") or "imported")[:120],
                                str(asset.get("model") or "imported")[:256],
                                json.dumps(metadata, ensure_ascii=False),
                                str(asset.get("created_at") or timestamp),
                                str(asset.get("updated_at") or timestamp),
                            ),
                        )
                        for review in asset.get("reviews", []) if isinstance(asset.get("reviews"), list) else []:
                            if not isinstance(review, dict):
                                continue
                            connection.execute(
                                "INSERT INTO asset_reviews(id, asset_id, status, issues_json, raw_text, provider, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    new_id("review"),
                                    new_asset_id,
                                    str(review.get("status") or "UNKNOWN")[:40],
                                    json.dumps(review.get("issues") if isinstance(review.get("issues"), list) else [], ensure_ascii=False),
                                    str(review.get("raw_text") or "")[:4000],
                                    str(review.get("provider") or "imported")[:120],
                                    str(review.get("model") or "imported")[:256],
                                    str(review.get("created_at") or timestamp),
                                ),
                            )
                settings = episode.get("composition_settings")
                if isinstance(settings, dict):
                    imported_tracks: list[dict[str, Any]] = []
                    for track in settings.get("audio_tracks", []) if isinstance(settings.get("audio_tracks"), list) else []:
                        if not isinstance(track, dict):
                            continue
                        remapped_asset_id = asset_ids.get(str(track.get("asset_id") or ""))
                        if not remapped_asset_id:
                            continue
                        try:
                            start_seconds = float(track.get("start_seconds", 0))
                            volume = float(track.get("volume", 1))
                        except (TypeError, ValueError):
                            continue
                        if math.isfinite(start_seconds) and 0 <= start_seconds <= 900 and math.isfinite(volume) and 0 <= volume <= 2:
                            imported_tracks.append({"asset_id": remapped_asset_id, "start_seconds": start_seconds, "volume": volume})
                    imported_subtitles: list[dict[str, Any]] = []
                    for subtitle in settings.get("subtitles", []) if isinstance(settings.get("subtitles"), list) else []:
                        if not isinstance(subtitle, dict):
                            continue
                        text = str(subtitle.get("text") or "").strip()
                        try:
                            start_seconds = float(subtitle.get("start_seconds"))
                            end_seconds = float(subtitle.get("end_seconds"))
                        except (TypeError, ValueError):
                            continue
                        if text and len(text) <= 500 and math.isfinite(start_seconds) and math.isfinite(end_seconds) and 0 <= start_seconds < end_seconds <= 900:
                            imported_subtitles.append({"start_seconds": start_seconds, "end_seconds": end_seconds, "text": text})
                    imported_narration_text = str(settings.get("narration_text") or "").strip()
                    if len(imported_narration_text) > MAX_NARRATION_TEXT_CHARS:
                        raise ServiceError(f"narration text cannot exceed {MAX_NARRATION_TEXT_CHARS} characters", 422)
                    connection.execute(
                        "INSERT INTO composition_settings(episode_id, audio_tracks_json, subtitles_json, narration_text, updated_at) VALUES (?, ?, ?, ?, ?)",
                        (new_episode_id, json.dumps(imported_tracks, ensure_ascii=False), json.dumps(imported_subtitles, ensure_ascii=False), imported_narration_text, str(settings.get("updated_at") or timestamp)),
                    )
                for composition in episode.get("compositions", []) if isinstance(episode.get("compositions"), list) else []:
                    if not isinstance(composition, dict):
                        continue
                    old_composition_id = str(composition.get("id") or new_id("composition-old"))
                    new_composition_id = new_id("composition")
                    composition_ids[old_composition_id] = new_composition_id
                    metadata_value = composition.get("metadata")
                    metadata = metadata_value if isinstance(metadata_value, dict) else json_loads(composition.get("metadata_json"), {})
                    connection.execute(
                        "INSERT INTO compositions(id, episode_id, playlist_url, final_video_url, status, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            new_composition_id,
                            new_episode_id,
                            safe_url(composition.get("playlist_url")),
                            safe_url(composition.get("final_video_url")),
                            status(composition.get("status"), {"pending", "completed", "failed"}, "pending"),
                            json.dumps({**metadata, "imported_snapshot": True}, ensure_ascii=False),
                            str(composition.get("created_at") or timestamp),
                        ),
                    )
            for new_asset_id, old_source_asset_id in pending_source_asset_ids:
                new_source_asset_id = asset_ids.get(old_source_asset_id)
                if new_source_asset_id:
                    connection.execute("UPDATE assets SET source_asset_id = ? WHERE id = ?", (new_source_asset_id, new_asset_id))

        if mappings is not None:
            mappings.update(
                {
                    "project_ids": {source_project_id: project_id} if source_project_id else {},
                    "character_ids": character_ids,
                    "reference_ids": reference_ids,
                    "episode_ids": episode_ids,
                    "shot_ids": shot_ids,
                    "asset_ids": asset_ids,
                    "composition_ids": composition_ids,
                }
            )
        imported = self.get_project(project_id, user_id)
        imported["imported_from_project_id"] = source_project_id or None
        return imported

    def write_project_bundle(self, project_id: str, output_dir: str | Path, user_id: str = DEFAULT_USER_ID) -> Path:
        """将交换包拆为可读文件；目标目录由调用方管理，可重复导出更新。"""
        bundle = self.export_project(project_id, user_id)
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        (root / "project.json").write_text(json.dumps(bundle["project"], ensure_ascii=False, indent=2), encoding="utf-8")
        (root / "adaptation_units.json").write_text(json.dumps(bundle["adaptation_units"], ensure_ascii=False, indent=2), encoding="utf-8")
        manifest = {key: bundle[key] for key in ("schema", "exported_at")}
        binary_assets: list[dict[str, Any]] = []
        materialized_ids: set[str] = set()

        def bundle_component(value: Any, fallback: str) -> str:
            candidate = Path(str(value or "")).name
            candidate = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff.]+", "_", candidate).strip("._")
            return candidate[:120] or fallback

        def local_url_path(value: Any) -> Path | None:
            """只解析服务端已有的本地缓存，不从归档请求任意外部 URL。"""
            raw_url = str(value or "").strip()
            if not raw_url:
                return None
            filename = Path(urllib.parse.urlparse(raw_url).path).name
            if not filename:
                return None
            candidate = self.asset_dir / filename
            return candidate if candidate.is_file() else None

        def register_file(source: Path | None, relative_path: str, item: dict[str, Any]) -> str | None:
            if source is None or not source.is_file():
                return None
            try:
                resolved_source = source.resolve()
                resolved_root = self.asset_dir.resolve()
                resolved_source.relative_to(resolved_root)
            except (OSError, ValueError):
                # 归档只允许复制服务端资产根目录内的文件，避免把任意路径带入下载包。
                return None
            target = root / Path(relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(resolved_source, target)
            record = {**item, "path": target.relative_to(root).as_posix(), "bytes": target.stat().st_size}
            binary_assets.append(record)
            return record["path"]

        def register_asset(asset: dict[str, Any]) -> None:
            asset_id = str(asset.get("id") or "").strip()
            if not asset_id or asset_id in materialized_ids:
                return
            materialized_ids.add(asset_id)
            kind = bundle_component(asset.get("kind"), "asset")
            source = self._materialize_asset_file(asset)
            suffix = Path(source).suffix.lower() if source else ""
            relative_path = f"assets/{kind}/{bundle_component(asset_id, 'asset')}{suffix}"
            copied_path = register_file(
                source,
                relative_path,
                {
                    "id": asset_id,
                    "kind": str(asset.get("kind") or "asset"),
                    "status": str(asset.get("status") or "unknown"),
                    "source_url": asset.get("url"),
                },
            )
            if copied_path is None:
                binary_assets.append(
                    {
                        "id": asset_id,
                        "kind": str(asset.get("kind") or "asset"),
                        "status": str(asset.get("status") or "unknown"),
                        "source_url": asset.get("url"),
                        "missing": True,
                    }
                )

        def register_character_reference(reference: dict[str, Any], character: dict[str, Any], view: str, url: Any) -> None:
            source = local_url_path(url)
            reference_id = bundle_component(reference.get("id"), "reference")
            storage_key: str | None = None
            temporary_source: Path | None = None
            if source is None and self.storage.mode != "local" and url:
                filename = PurePosixPath(urllib.parse.urlsplit(str(url)).path).name
                if filename:
                    storage_key = f"character-references/{reference_id}/{filename}"
                    temporary_source = self.asset_dir / f".bundle-cache-{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
                    try:
                        source = self.storage.get_file(storage_key, temporary_source)
                    except Exception:
                        source = None
            suffix = Path(source).suffix.lower() if source else ""
            relative_path = f"character-references/{reference_id}-{view}{suffix}"
            copied_path = register_file(
                source,
                relative_path,
                {
                    "id": reference.get("id"),
                    "kind": "character-reference",
                    "character_id": character.get("id"),
                    "view": view,
                    "source_url": url,
                    "storage_key": storage_key,
                },
            )
            if copied_path is None and url:
                binary_assets.append(
                    {
                        "id": reference.get("id"),
                        "kind": "character-reference",
                        "character_id": character.get("id"),
                        "view": view,
                        "source_url": url,
                        "storage_key": storage_key,
                        "missing": True,
                    }
                )
            if temporary_source is not None:
                temporary_source.unlink(missing_ok=True)

        def register_composition_file(composition: dict[str, Any], kind: str, url: Any, storage_key: Any) -> None:
            source = local_url_path(url)
            temporary_source: Path | None = None
            if source is None and storage_key:
                raw_url = str(url or "")
                suffix = Path(urllib.parse.urlparse(raw_url).path).suffix.lower() or (".json" if kind == "playlist" else ".mp4")
                temporary_source = self.asset_dir / f".bundle-cache-{uuid.uuid4().hex}{suffix}"
                try:
                    source = self.storage.get_file(str(storage_key), temporary_source)
                except Exception:
                    source = None
            suffix = Path(source).suffix.lower() if source else ""
            composition_id = bundle_component(composition.get("id"), "composition")
            relative_path = f"compositions/{composition_id}-{kind}{suffix}"
            copied_path = register_file(
                source,
                relative_path,
                {
                    "id": composition.get("id"),
                    "kind": f"composition-{kind}",
                    "status": composition.get("status"),
                    "source_url": url,
                    "storage_key": storage_key,
                },
            )
            if copied_path is None and url:
                binary_assets.append(
                    {
                        "id": composition.get("id"),
                        "kind": f"composition-{kind}",
                        "status": composition.get("status"),
                        "source_url": url,
                        "storage_key": storage_key,
                        "missing": True,
                    }
                )
            if temporary_source is not None:
                temporary_source.unlink(missing_ok=True)

        for asset in bundle.get("audio_assets", []):
            if isinstance(asset, dict):
                register_asset(asset)
        for character in bundle.get("characters", []):
            if not isinstance(character, dict):
                continue
            for reference in character.get("references", []) if isinstance(character.get("references"), list) else []:
                if not isinstance(reference, dict):
                    continue
                for view in ("front", "side", "back"):
                    register_character_reference(reference, character, view, reference.get(f"{view}_url"))
        for episode in bundle.get("episodes", []):
            if not isinstance(episode, dict):
                continue
            for shot in episode.get("shots", []) if isinstance(episode.get("shots"), list) else []:
                if not isinstance(shot, dict):
                    continue
                for asset in shot.get("assets", []) if isinstance(shot.get("assets"), list) else []:
                    if isinstance(asset, dict):
                        register_asset(asset)

        source_dir = root / "sources"
        source_dir.mkdir(exist_ok=True)
        for document in bundle["source_documents"]:
            filename = Path(document["filename"]).name or f"{document['id']}.txt"
            target = source_dir / filename
            if target.exists():
                target = source_dir / f"{document['id']}-{filename}"
            target.write_text(document["text"], encoding="utf-8")
        episode_dir = root / "episodes"
        episode_dir.mkdir(exist_ok=True)
        storyboard_dir = root / "storyboards"
        storyboard_dir.mkdir(exist_ok=True)
        storyboard_files: list[str] = []
        for episode in bundle["episodes"]:
            (episode_dir / f"episode-{int(episode['number']):03d}.json").write_text(json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8")
            panels = []
            source_map: dict[str, list[str]] = {}
            bubbles: dict[str, list[tuple[str, str, str]]] = {}
            asset_map: dict[str, list[dict[str, Any]]] = {}
            for shot in episode.get("shots", []):
                description = str(shot.get("description", "")).strip()
                short = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]+", "_", description[:24]).strip("_") or "shot"
                panel_name = f"P{int(shot['sequence']):02d}_{short}"
                prompt_data = shot.get("image_prompt") or {}
                prompt = str(prompt_data.get("prompt") or description)
                panels.append((panel_name, prompt, "scene"))
                source_map[panel_name] = list(shot.get("adaptation_unit_ids", []))
                bubbles[panel_name] = [("narration", description, "bottom")] if description else []
                asset_map[panel_name] = [
                    {"id": asset.get("id"), "url": asset.get("url"), "status": asset.get("status"), "selected": asset.get("selected", False)}
                    for asset in shot.get("assets", []) if asset.get("kind") == "image"
                ]
            episode_number = int(episode["number"])
            storyboard_name = f"episode-{episode_number:03d}_storyboard.py"
            storyboard_path = storyboard_dir / storyboard_name
            storyboard_source = "\n".join(
                [
                    "#!/usr/bin/env python3",
                    f"\"\"\"{episode.get('title', f'第{episode_number}集')} 平台导出分镜\"\"\"",
                    "",
                    f"PANELS = {panels!r}",
                    f"SOURCE_UNIT_IDS = {source_map!r}",
                    f"BUBBLE_CONFIG = {bubbles!r}",
                    "BUBBLES = BUBBLE_CONFIG",
                    "",
                    f"ASSET_MAP = {asset_map!r}",
                    "",
                ]
            )
            storyboard_path.write_text(storyboard_source, encoding="utf-8")
            (storyboard_dir / f"episode-{episode_number:03d}_assets.json").write_text(json.dumps(asset_map, ensure_ascii=False, indent=2), encoding="utf-8")
            storyboard_files.append(f"storyboards/{storyboard_name}")
            for composition in episode.get("compositions", []) if isinstance(episode.get("compositions"), list) else []:
                if not isinstance(composition, dict):
                    continue
                metadata = json_loads(composition.get("metadata_json", "{}"), {})
                register_composition_file(composition, "playlist", composition.get("playlist_url"), metadata.get("playlist_storage_key"))
                register_composition_file(composition, "final", composition.get("final_video_url"), metadata.get("final_storage_key"))
        manifest["storyboards"] = storyboard_files
        manifest["binary_assets"] = binary_assets
        manifest["binary_asset_count"] = sum(1 for item in binary_assets if not item.get("missing"))
        manifest["missing_binary_asset_count"] = sum(1 for item in binary_assets if item.get("missing"))
        manifest["archive_notes"] = [
            "JSON 中的原始 URL 保持不变，binary_assets.path 是归档内可用的本地副本。",
            "归档不会请求任意外部 URL；对象存储素材仅使用已记录的 storage key 回读。",
        ]
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (root / "project-export.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        return root

    def write_project_archive(self, project_id: str, output_path: str | Path, user_id: str = DEFAULT_USER_ID) -> Path:
        """生成可下载 ZIP；归档流式由 API 返回，临时文件由路由响应完成后删除。"""
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="project-bundle-", dir=str(self.asset_dir.parent)) as staging:
            root = self.write_project_bundle(project_id, Path(staging) / "bundle", user_id)
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(root).as_posix())
        return destination

    def import_project_archive(self, archive_path: str | Path, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        """安全导入 ZIP 项目包，并把归档内二进制素材重挂载到新项目。"""
        source = Path(archive_path)
        if not source.is_file():
            raise ServiceError("project archive not found", 404)
        try:
            max_members = max(1, min(int(os.environ.get("STUDIO_MAX_ARCHIVE_MEMBERS", "4096")), 20000))
        except ValueError:
            max_members = 4096
        try:
            max_uncompressed_bytes = max(1 << 20, min(int(os.environ.get("STUDIO_MAX_ARCHIVE_UNCOMPRESSED_BYTES", str(512 * 1024 * 1024))), 2 * 1024 * 1024 * 1024))
        except ValueError:
            max_uncompressed_bytes = 512 * 1024 * 1024

        def safe_member_name(name: str) -> bool:
            if not name or "\\" in name or "\x00" in name:
                return False
            if name.endswith("/"):
                return True
            path = PurePosixPath(name)
            return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)

        def safe_component(value: Any, fallback: str) -> str:
            candidate = re.sub(r"[^0-9A-Za-z_\-]+", "_", Path(str(value or "")).name).strip("._")
            return candidate[:120] or fallback

        try:
            with zipfile.ZipFile(source) as archive:
                infos = archive.infolist()
                if len(infos) > max_members:
                    raise ServiceError("project archive has too many members", 422)
                members: dict[str, zipfile.ZipInfo] = {}
                total_size = 0
                for info in infos:
                    if not safe_member_name(info.filename):
                        raise ServiceError("project archive contains an unsafe path", 422)
                    if info.filename.endswith("/"):
                        continue
                    if info.filename in members:
                        raise ServiceError("project archive contains duplicate members", 422)
                    file_type = (info.external_attr >> 16) & 0o170000
                    if file_type == 0o120000:
                        raise ServiceError("project archive cannot contain symbolic links", 422)
                    total_size += max(0, int(info.file_size))
                    if total_size > max_uncompressed_bytes:
                        raise ServiceError("project archive is too large", 413)
                    members[info.filename] = info

                def read_json(name: str, limit: int) -> dict[str, Any]:
                    info = members.get(name)
                    if info is None:
                        raise ServiceError(f"project archive is missing {name}", 422)
                    if info.file_size > limit:
                        raise ServiceError(f"project archive member is too large: {name}", 413)
                    try:
                        value = json.loads(archive.read(info).decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ServiceError(f"invalid project archive JSON: {name}", 422) from exc
                    if not isinstance(value, dict):
                        raise ServiceError(f"project archive JSON must be an object: {name}", 422)
                    return value

                bundle = read_json("project-export.json", 32 * 1024 * 1024)
                manifest = read_json("manifest.json", 8 * 1024 * 1024)
                if bundle.get("schema") != "ai-manhua-studio/project-export/v1":
                    raise ServiceError("unsupported project export schema", 422)
                binary_records = manifest.get("binary_assets", [])
                if not isinstance(binary_records, list):
                    raise ServiceError("project archive manifest binary_assets must be a list", 422)

                with tempfile.TemporaryDirectory(prefix="project-archive-import-", dir=str(self.asset_dir.parent)) as staging:
                    staged_records: list[tuple[dict[str, Any], Path]] = []
                    staging_root = Path(staging)
                    for index, record in enumerate(binary_records):
                        if not isinstance(record, dict) or record.get("missing"):
                            continue
                        member_name = str(record.get("path") or "")
                        if not safe_member_name(member_name) or member_name not in members:
                            raise ServiceError("manifest references a missing or unsafe binary member", 422)
                        info = members[member_name]
                        staged_path = staging_root / f"{index:08d}.asset"
                        copied = 0
                        with archive.open(info, "r") as reader, staged_path.open("wb") as writer:
                            while True:
                                chunk = reader.read(1024 * 1024)
                                if not chunk:
                                    break
                                copied += len(chunk)
                                if copied > max_uncompressed_bytes:
                                    raise ServiceError("project archive member expanded beyond limit", 413)
                                writer.write(chunk)
                        staged_records.append((record, staged_path))

                    mappings: dict[str, dict[str, str]] = {}
                    imported = self.import_project_bundle(bundle, user_id, mappings)
                    new_project_id = imported["id"]
                    mount_updates: list[dict[str, Any]] = []
                    failed_mounts = 0

                    def target_for(record: dict[str, Any], suffix: str) -> tuple[str, str, str] | None:
                        kind = str(record.get("kind") or "")
                        old_id = str(record.get("id") or "")
                        if kind in {"image", "video", "audio"}:
                            entity_id = mappings.get("asset_ids", {}).get(old_id)
                            return ("asset", entity_id, f"{entity_id}{suffix}") if entity_id else None
                        if kind == "character-reference":
                            entity_id = mappings.get("reference_ids", {}).get(old_id)
                            view = safe_component(record.get("view"), "view")
                            return ("reference", entity_id, f"{entity_id}-{view}{suffix}") if entity_id else None
                        if kind in {"composition-playlist", "composition-final"}:
                            entity_id = mappings.get("composition_ids", {}).get(old_id)
                            part = "playlist" if kind.endswith("playlist") else "final"
                            return ("composition", entity_id, f"{entity_id}-{part}{suffix}") if entity_id else None
                        return None

                    for record, staged_path in staged_records:
                        relative = str(record.get("path") or "")
                        target_info = target_for(record, PurePosixPath(relative).suffix.lower())
                        if target_info is None:
                            failed_mounts += 1
                            continue
                        target_kind, entity_id, filename = target_info
                        destination = self.asset_dir / filename
                        shutil.copyfile(staged_path, destination)
                        stored_url = f"/assets/{destination.name}"
                        storage_key: str | None = None
                        if self.storage.mode != "local":
                            prefix = {
                                "asset": f"assets/{new_project_id}",
                                "reference": f"character-references/{entity_id}",
                                "composition": f"compositions/{entity_id}",
                            }[target_kind]
                            storage_key = f"{prefix}/{destination.name}"
                            stored_url = self.storage.put_file(destination, storage_key)
                        mount_updates.append({"kind": target_kind, "entity_id": entity_id, "record": record, "url": stored_url, "storage_key": storage_key})

                    with self.store.connection() as connection:
                        for update in mount_updates:
                            kind = update["kind"]
                            entity_id = update["entity_id"]
                            record = update["record"]
                            stored_url = update["url"]
                            storage_key = update["storage_key"]
                            if kind == "asset":
                                row = connection.execute("SELECT metadata_json FROM assets WHERE id = ? AND project_id = ?", (entity_id, new_project_id)).fetchone()
                                if not row:
                                    failed_mounts += 1
                                    continue
                                metadata = json_loads(row["metadata_json"], {})
                                metadata["imported_archive"] = True
                                if storage_key:
                                    metadata["storage_key"] = storage_key
                                connection.execute(
                                    "UPDATE assets SET url = ?, status = 'ready', metadata_json = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                                    (stored_url, json.dumps(metadata, ensure_ascii=False), now(), entity_id, new_project_id),
                                )
                            elif kind == "reference":
                                column = {"front": "front_url", "side": "side_url", "back": "back_url"}.get(str(record.get("view") or ""))
                                if not column:
                                    failed_mounts += 1
                                    continue
                                connection.execute(f"UPDATE character_references SET {column} = ?, status = 'ready' WHERE id = ?", (stored_url, entity_id))
                            else:
                                column = "playlist_url" if str(record.get("kind")) == "composition-playlist" else "final_video_url"
                                row = connection.execute("SELECT metadata_json FROM compositions WHERE id = ?", (entity_id,)).fetchone()
                                if not row:
                                    failed_mounts += 1
                                    continue
                                metadata = json_loads(row["metadata_json"], {})
                                if storage_key:
                                    metadata["storage"] = self.storage.mode
                                    metadata[f"{'playlist' if column == 'playlist_url' else 'final'}_storage_key"] = storage_key
                                connection.execute(
                                    f"UPDATE compositions SET {column} = ?, metadata_json = ? WHERE id = ?",
                                    (stored_url, json.dumps(metadata, ensure_ascii=False), entity_id),
                                )

                    imported = self.get_project(new_project_id, user_id)
                    imported["archive_import"] = {
                        "status": "completed" if failed_mounts == 0 else "partial",
                        "mounted": len(mount_updates) - failed_mounts,
                        "failed": failed_mounts,
                        "declared_missing": sum(1 for record in binary_records if isinstance(record, dict) and record.get("missing")),
                    }
                    return imported
        except zipfile.BadZipFile as exc:
            raise ServiceError("invalid project archive ZIP", 422) from exc

    def settings(self, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        """读取用户作用域的非敏感 provider 偏好，不返回任何 credential。"""
        defaults: dict[str, Any] = {
            "settingsVersion": 1,
            "text": {"provider": self.providers.text.name, "model": str(getattr(self.providers.text, "model", "template"))},
            "image": {"provider": self.providers.image.name, "model": str(getattr(self.providers.image, "model", "placeholder"))},
            "video": {"provider": self.providers.video.name, "model": str(getattr(self.providers.video, "model", "placeholder"))},
            "vision": {"provider": self.providers.vision.name, "model": str(getattr(self.providers.vision, "model", "manual-review"))},
            "speech": {"provider": self.providers.speech.name, "model": str(getattr(self.providers.speech, "model", "speech"))},
        }
        prefix = f"{user_id}:"
        rows = self.store.all("SELECT key, value_json FROM studio_settings WHERE key LIKE ?", (f"{prefix}%",))
        if not rows:
            return defaults
        for row in rows:
            key = row["key"][len(prefix):]
            value = json_loads(row["value_json"], {})
            if key in {"text", "image", "video", "vision", "speech"} and isinstance(value, dict):
                defaults[key] = {**defaults[key], **value}
            elif key in defaults:
                defaults[key] = value
        return defaults

    def update_settings(self, changes: dict[str, Any], user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        allowed = {key: changes[key] for key in ("settingsVersion", "text", "image", "video", "vision", "speech") if key in changes}
        provider_choices = {
            "text": {"local", "openai-compatible"},
            "vision": {"local", "openai-compatible"},
            "image": {"local", "comfyui"},
            "video": {"local", "comfyui"},
            "speech": {"local", "openai"},
        }
        prefix = f"{user_id}:"
        for key, value in allowed.items():
            if isinstance(value, dict):
                value = {subkey: value[subkey] for subkey in ("provider", "model", "base_url") if subkey in value}
                if "provider" in value and value["provider"] not in provider_choices[key]:
                    raise ServiceError(f"unsupported {key} provider: {value['provider']}")
                self._validate_production_provider_preference(key, value)
                if "model" in value and len(str(value["model"])) > 256:
                    raise ServiceError(f"{key} model is too long")
                if "base_url" in value:
                    base_url = str(value["base_url"]).strip()
                    if base_url:
                        self._validate_provider_base_url(key, base_url)
                    value["base_url"] = base_url
            self.store.write("INSERT INTO studio_settings(key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json", (f"{prefix}{key}", json.dumps(value, ensure_ascii=False)))
        return self.settings(user_id)

    def _providers_for_user(self, user_id: str) -> ProviderRegistry:
        """把用户的非敏感路由偏好解析成运行时 provider；凭据仍由环境变量提供。"""
        preferences = self.settings(user_id)
        for kind in ("text", "image", "video", "vision", "speech"):
            value = preferences.get(kind)
            if isinstance(value, dict):
                self._validate_production_provider_preference(kind, value)
        for kind in ("text", "image", "video", "vision", "speech"):
            value = preferences.get(kind)
            if isinstance(value, dict) and value.get("base_url"):
                self._validate_provider_base_url(kind, str(value["base_url"]))
        try:
            return ProviderRegistry.from_env(preferences)
        except ProviderError as exc:
            raise ServiceError(str(exc), 422) from exc

    @staticmethod
    def _validate_production_provider_preference(kind: str, value: dict[str, Any]) -> None:
        """生产环境不允许用户偏好把真实 Provider 降级为本地预览。

        Provider 的 key 和默认路由由部署环境控制；用户偏好只能在部署方
        明确允许的范围内覆盖模型/地址。这样即使数据库中残留旧的 local
        偏好，也不会在生产配置 gate 通过后悄悄生成预览资产。
        """
        if os.environ.get("STUDIO_ENV", "development").strip().lower() != "production":
            return
        provider_env = {
            "text": "STUDIO_TEXT_PROVIDER",
            "image": "STUDIO_IMAGE_PROVIDER",
            "video": "STUDIO_VIDEO_PROVIDER",
            "vision": "STUDIO_VISION_PROVIDER",
            "speech": "STUDIO_SPEECH_PROVIDER",
        }[kind]
        configured_provider = os.environ.get(provider_env, "").strip()
        requested_provider = str(value.get("provider") or "").strip()
        if requested_provider and requested_provider != configured_provider:
            raise ServiceError(f"{kind} provider preference cannot override the production provider", 409)

        base_url = str(value.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            return
        configured_url_env = {
            "text": "STUDIO_TEXT_BASE_URL",
            "image": "COMFYUI_BASE_URL",
            "video": "COMFYUI_BASE_URL",
            "vision": "STUDIO_VISION_BASE_URL",
            "speech": "STUDIO_SPEECH_BASE_URL",
        }[kind]
        configured_url = os.environ.get(configured_url_env, "").strip().rstrip("/")
        allowlist = {
            item.strip().rstrip("/")
            for item in os.environ.get("STUDIO_ALLOWED_PROVIDER_BASE_URLS", "").split(",")
            if item.strip()
        }
        if base_url != configured_url and base_url not in allowlist:
            raise ServiceError(f"{kind} base_url preference is not allowed in production", 409)

    def _generate_text_json(self, user_id: str, instruction: str) -> dict[str, Any] | list[Any] | None:
        """让外部文本 provider 参与结构化创作；本地预览继续走确定性回退。"""
        providers = self._providers_for_user(user_id)
        if providers.text.name == "local":
            return None
        try:
            raw, _metadata = providers.text.complete(instruction, json_mode=True)
        except ProviderError as exc:
            raise ServiceError(str(exc), 502) from exc
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ServiceError("text provider returned invalid JSON", 502) from exc
        if not isinstance(payload, (dict, list)):
            raise ServiceError("text provider returned unsupported JSON", 502)
        return payload

    @staticmethod
    def _validate_provider_base_url(kind: str, base_url: str) -> None:
        """禁止用户借服务端凭据把 provider 请求转发到任意地址。"""
        if len(base_url) > 1024:
            raise ServiceError(f"{kind} base_url is too long")
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ServiceError(f"{kind} base_url must be a credential-free http(s) URL")
        normalized = base_url.rstrip("/")
        allowlist = [item.strip().rstrip("/") for item in os.environ.get("STUDIO_ALLOWED_PROVIDER_BASE_URLS", "").split(",") if item.strip()]
        if allowlist:
            if normalized not in allowlist:
                raise ServiceError(f"{kind} base_url is not in the provider allowlist")
            return
        env_names = {
            "text": "STUDIO_TEXT_BASE_URL",
            "image": "COMFYUI_BASE_URL",
            "video": "COMFYUI_BASE_URL",
            "vision": "STUDIO_VISION_BASE_URL",
            "speech": "STUDIO_SPEECH_BASE_URL",
        }
        configured = os.environ.get(env_names[kind], "").strip().rstrip("/")
        if not configured or normalized != configured:
            raise ServiceError(f"{kind} base_url requires STUDIO_ALLOWED_PROVIDER_BASE_URLS or the matching server environment URL")

    def jobs(self, user_id: str = DEFAULT_USER_ID, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(row) for row in self.store.all("SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit))]

    def job_events(self, job_id: str, user_id: str = DEFAULT_USER_ID, limit: int = 100) -> list[dict[str, Any]]:
        """返回用户可见的 Job 状态时间线，事件由存储层触发器保证写入。"""
        job = self.store.one("SELECT id FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
        if not job:
            raise ServiceError("job not found", 404)
        bounded_limit = max(1, min(int(limit), 200))
        rows = self.store.all(
            "SELECT id, job_id, user_id, from_status, to_status, event_type, message, attempt, created_at "
            "FROM job_events WHERE job_id = ? AND user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (job_id, user_id, bounded_limit),
        )
        return [dict(row) for row in rows]

    def reference_with_job(self, reference_id: str, user_id: str | None = None) -> dict[str, Any]:
        if user_id is None:
            row = self.store.one(
                "SELECT r.*, c.name, c.project_id FROM character_references r JOIN characters c ON c.id = r.character_id WHERE r.id = ?",
                (reference_id,),
            )
        else:
            row = self.store.one(
                "SELECT r.*, c.name, c.project_id FROM character_references r JOIN characters c ON c.id = r.character_id JOIN projects p ON p.id = c.project_id WHERE r.id = ? AND p.user_id = ?",
                (reference_id, user_id),
            )
        if not row:
            raise ServiceError("character reference not found", 404)
        reference = dict(row)
        if self.storage.mode != "local":
            for view in ("front_url", "side_url", "back_url"):
                reference[view] = self._media_url(reference.get(view))
        job = self.store.one("SELECT * FROM jobs WHERE target_id = ? ORDER BY created_at DESC LIMIT 1", (reference_id,))
        reference["job"] = dict(job) if job else None
        return reference

    def _media_url(self, value: Any, storage_key: Any = None) -> str | None:
        """把对象存储 URL 统一收敛到受保护的 API 媒体路由。"""
        raw = str(value or "").strip()
        if not raw:
            return None
        if self.storage.mode == "local":
            return raw
        filename = PurePosixPath(urllib.parse.urlsplit(raw).path).name
        if filename and (storage_key or raw.startswith("/assets/") or urllib.parse.urlsplit(raw).scheme in {"http", "https"}):
            return f"/assets/{filename}"
        return raw

    def local_asset_file(self, asset_name: str, user_id: str = DEFAULT_USER_ID) -> Path:
        """返回当前用户有权读取的本地媒体文件。

        资产 URL 仍保持 ``/assets/<filename>``，这样浏览器的 img/video/audio
        标签不需要额外改造；但文件不再由 StaticFiles 无条件公开。只有在
        资产元数据、角色母版或分集成片记录中找到同名文件，并且其项目属于
        当前用户时才允许读取。对象存储模式下，若本地缓存不存在则使用记录的
        storage key 回读到受控缓存目录；对象存储本身保持私有。
        """
        raw_name = str(asset_name or "").strip().replace("\\", "/")
        path = PurePosixPath(raw_name)
        if not raw_name or path.is_absolute() or len(path.parts) != 1 or path.parts[0] in {".", ".."}:
            raise ServiceError("asset not found", 404)
        filename = path.parts[0]
        root = self.asset_dir.resolve()
        candidate = (self.asset_dir / filename).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ServiceError("asset not found", 404) from exc
        public_url = f"/assets/{filename}"
        def url_matches(value: Any) -> bool:
            raw = str(value or "").strip()
            if raw == public_url:
                return True
            return PurePosixPath(urllib.parse.urlsplit(raw).path).name == filename

        storage_key: str | None = None
        authorized = False
        asset_rows = self.store.all(
            "SELECT a.id, a.url, a.metadata_json FROM assets a JOIN projects p ON p.id = a.project_id WHERE p.user_id = ?",
            (user_id,),
        )
        for row in asset_rows:
            if url_matches(row["url"]) and filename.startswith(str(row["id"])):
                authorized = True
                metadata = json_loads(row["metadata_json"], {})
                storage_key = str(metadata.get("storage_key") or "").strip() or None
                break
        if storage_key is None:
            reference_rows = self.store.all(
                """
                SELECT r.id, r.front_url, r.side_url, r.back_url
                FROM character_references r
                JOIN characters c ON c.id = r.character_id
                JOIN projects p ON p.id = c.project_id
                WHERE p.user_id = ?
                """,
                (user_id,),
            )
            for row in reference_rows:
                for view in ("front_url", "side_url", "back_url"):
                    if url_matches(row[view]) and filename.startswith(str(row["id"])):
                        authorized = True
                        storage_key = f"character-references/{row['id']}/{filename}"
                        break
                if storage_key is not None:
                    break
        if storage_key is None:
            composition_rows = self.store.all(
                """
                SELECT c.id, c.playlist_url, c.final_video_url, c.metadata_json
                FROM compositions c
                JOIN episodes e ON e.id = c.episode_id
                JOIN projects p ON p.id = e.project_id
                WHERE p.user_id = ?
                """,
                (user_id,),
            )
            for row in composition_rows:
                metadata = json_loads(row["metadata_json"], {})
                if url_matches(row["playlist_url"]) and filename.startswith(str(row["id"])):
                    authorized = True
                    storage_key = str(metadata.get("playlist_storage_key") or "").strip() or None
                    break
                if url_matches(row["final_video_url"]) and filename.startswith(str(row["id"])):
                    authorized = True
                    storage_key = str(metadata.get("final_storage_key") or "").strip() or None
                    break
        if not authorized:
            raise ServiceError("asset not found", 404)
        if candidate.is_file():
            return candidate
        if self.storage.mode == "local" or not storage_key:
            raise ServiceError("asset not found", 404)
        try:
            return self.storage.get_file(storage_key, candidate)
        except Exception as exc:
            raise ServiceError("asset not found", 404) from exc

    def _materialize_asset_file(self, asset: dict[str, Any], prefer_video_source: bool = False) -> Path | None:
        """确保视觉审核/合成在进程重启后也能从对象存储取回素材。"""
        metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        preferred_filename = str(metadata.get("video_source_filename") or "").strip()
        preferred_storage_key = str(metadata.get("video_source_storage_key") or "").strip()
        if prefer_video_source and preferred_filename:
            preferred_path = self.asset_dir / Path(preferred_filename).name
            if preferred_path.exists():
                return preferred_path
            if preferred_storage_key:
                try:
                    return self.storage.get_file(preferred_storage_key, preferred_path)
                except Exception:
                    pass
        url = str(asset.get("url") or "")
        if not url:
            return None
        path = self.asset_dir / Path(url).name
        if path.exists():
            return path
        storage_key = metadata.get("storage_key")
        if not storage_key:
            return None
        try:
            return self.storage.get_file(str(storage_key), path)
        except Exception:
            return None

    def asset_with_job(self, asset_id: str, user_id: str | None = None) -> dict[str, Any]:
        if user_id is None:
            row = self.store.one("SELECT * FROM assets WHERE id = ?", (asset_id,))
        else:
            row = self.store.one("SELECT a.* FROM assets a JOIN projects p ON p.id = a.project_id WHERE a.id = ? AND p.user_id = ?", (asset_id, user_id))
        if not row:
            raise ServiceError("asset not found", 404)
        asset = dict(row)
        asset["selected"] = bool_value(asset["selected"])
        asset["consistency_confirmed"] = bool_value(asset["consistency_confirmed"])
        asset["metadata"] = json_loads(asset.pop("metadata_json", "{}"), {})
        asset["url"] = self._media_url(asset.get("url"), asset["metadata"].get("storage_key"))
        reviews = []
        for review in self.store.all("SELECT * FROM asset_reviews WHERE asset_id = ? ORDER BY created_at DESC", (asset_id,)):
            item = dict(review)
            item["issues"] = json_loads(item.pop("issues_json", "[]"), [])
            reviews.append(item)
        asset["reviews"] = reviews
        job = self.store.one("SELECT * FROM jobs WHERE target_id = ? ORDER BY created_at DESC LIMIT 1", (asset_id,))
        asset["job"] = dict(job) if job else None
        return asset

    def _review_asset_now(
        self,
        asset_id: str,
        prompt: str = "",
        audit_type: str = "scene",
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        """在当前 worker 中执行一次视觉审核；API 队列入口不应直接调用此方法。"""
        asset = self.asset_with_job(asset_id, user_id)
        if asset["kind"] != "image":
            raise ServiceError("only image assets can be visually reviewed")
        if not asset.get("url"):
            raise ServiceError("image asset is not ready", 409)
        image_path = self._materialize_asset_file(asset)
        if not image_path:
            raise ServiceError("image bytes are not available in the local asset store", 409)
        shot = self.store.one("SELECT description, scene, emotion FROM shots WHERE id = ?", (asset["shot_id"],)) if asset.get("shot_id") else None
        review_prompt = prompt.strip() or (
            f"请审核这张 AI 漫剧关键帧，审核类型：{audit_type}。"
            f"镜头描述：{shot['description'] if shot else ''}。"
            "检查画面质量、角色/场景一致性、畸形、重复人物和可读水印。只输出 PASS 或逐条问题。"
        )
        try:
            result = self._providers_for_user(user_id).vision.review(image_path, review_prompt)
        except ProviderError as exc:
            raise ServiceError(str(exc), 502) from exc
        review_id = new_id("review")
        with self.store.connection() as connection:
            connection.execute(
                "INSERT INTO asset_reviews(id, asset_id, status, issues_json, raw_text, provider, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (review_id, asset_id, result.get("status", "UNKNOWN"), json.dumps(result.get("issues", []), ensure_ascii=False), result.get("raw", ""), result.get("provider", self.providers.vision.name), result.get("model", "manual-review"), now()),
            )
            if result.get("status") == "PASS" and asset["selected"]:
                connection.execute("UPDATE assets SET consistency_confirmed = ?, updated_at = ? WHERE id = ?", (True, now(), asset_id))
            elif result.get("status") == "FAIL":
                # 视觉审核失败必须阻断下游视频生成，即使此前曾人工确认过。
                connection.execute("UPDATE assets SET consistency_confirmed = ?, updated_at = ? WHERE id = ?", (False, now(), asset_id))
        return self.asset_with_job(asset_id, user_id)

    def manual_review_asset(
        self,
        asset_id: str,
        status: str,
        issues: list[str] | None = None,
        user_id: str = DEFAULT_USER_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """持久化人工视觉审核决策，完成 local UNKNOWN 的制作门禁。"""
        asset = self.asset_with_job(asset_id, user_id)
        if asset["kind"] != "image":
            raise ServiceError("only image assets can be visually reviewed")
        if asset["status"] != "ready" or not asset.get("url"):
            raise ServiceError("image asset is not ready", 409)
        normalized_status = str(status or "").strip().upper()
        if normalized_status not in {"PASS", "FAIL"}:
            raise ServiceError("manual review status must be PASS or FAIL", 422)
        normalized_issues: list[str] = []
        for issue in issues or []:
            text = str(issue or "").strip()
            if text and text not in normalized_issues:
                normalized_issues.append(text[:500])
        if len(normalized_issues) > 32:
            raise ServiceError("manual review cannot contain more than 32 issues", 422)
        if normalized_status == "PASS":
            normalized_issues = []
        elif not normalized_issues:
            normalized_issues = ["人工复核判定不通过"]
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        review_id = new_id("review")
        with self.store.connection() as connection:
            replay = False
            if canonical_key:
                decision_cursor = connection.execute(
                    "INSERT INTO asset_review_decisions(id, user_id, asset_id, idempotency_key, status, issues_json, review_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(user_id, idempotency_key) DO NOTHING",
                    (
                        new_id("review-decision"),
                        user_id,
                        asset_id,
                        canonical_key,
                        normalized_status,
                        json.dumps(normalized_issues, ensure_ascii=False),
                        review_id,
                        now(),
                    ),
                )
                if getattr(decision_cursor, "rowcount", 1) == 0:
                    existing = connection.execute(
                        "SELECT asset_id, status, issues_json FROM asset_review_decisions WHERE user_id = ? AND idempotency_key = ?",
                        (user_id, canonical_key),
                    ).fetchone()
                    if not existing:
                        raise ServiceError("manual review idempotency request could not be recovered", 409)
                    existing_issues = json_loads(existing["issues_json"], [])
                    if (
                        str(existing["asset_id"]) != asset_id
                        or str(existing["status"]).upper() != normalized_status
                        or existing_issues != normalized_issues
                    ):
                        raise ServiceError("Idempotency-Key was already used for another manual review decision", 409)
                    replay = True
            if not replay:
                connection.execute(
                    "INSERT INTO asset_reviews(id, asset_id, status, issues_json, raw_text, provider, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        review_id,
                        asset_id,
                        normalized_status,
                        json.dumps(normalized_issues, ensure_ascii=False),
                        "",
                        "manual",
                        "human-review",
                        now(),
                    ),
                )
                if normalized_status == "PASS" and asset["selected"]:
                    connection.execute(
                        "UPDATE assets SET consistency_confirmed = ?, updated_at = ? WHERE id = ?",
                        (True, now(), asset_id),
                    )
                elif normalized_status == "FAIL":
                    connection.execute(
                        "UPDATE assets SET consistency_confirmed = ?, updated_at = ? WHERE id = ?",
                        (False, now(), asset_id),
                    )
        return self.asset_with_job(asset_id, user_id)

    def submit_asset_qc(
        self,
        asset_id: str,
        evidence: dict[str, Any],
        user_id: str = DEFAULT_USER_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """持久化视频资产的机器 QC 结果。

        ffprobe/ffmpeg 由任务 worker 或外部媒体适配器执行；服务层只接受带
        SHA-256 的证据，先与当前资产字节核对，再把 ``studio_core.qc`` 的
        结构化结果写入既有 asset_reviews 表。视觉类 finding 为 unknown 时
        不能伪装成 PASS，人工审片仍是后续放行门槛。
        """

        asset = self.asset_with_job(asset_id, user_id)
        if asset["kind"] != "video":
            raise ServiceError("automatic media QC currently accepts video assets only")
        if asset["status"] != "ready" or not asset.get("url"):
            raise ServiceError("video asset is not ready", 409)
        if not isinstance(evidence, dict):
            raise ServiceError("QC evidence must be an object", 422)
        try:
            evidence_request_sha256 = hashlib.sha256(
                json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError) as exc:
            raise ServiceError("QC evidence must be JSON-serializable", 422) from exc
        media_path = self._materialize_asset_file(asset, prefer_video_source=True)
        if not media_path or not media_path.is_file():
            raise ServiceError("video bytes are not available in the local asset store", 409)

        declared_sha256 = str(evidence.get("sha256") or "").strip().lower()
        if len(declared_sha256) != 64 or any(char not in "0123456789abcdef" for char in declared_sha256):
            raise ServiceError("QC evidence sha256 is invalid", 422)
        digest = hashlib.sha256()
        with media_path.open("rb") as media_handle:
            for block in iter(lambda: media_handle.read(1024 * 1024), b""):
                digest.update(block)
        actual_sha256 = digest.hexdigest()
        if not secrets.compare_digest(declared_sha256, actual_sha256):
            raise ServiceError("QC evidence sha256 does not match the current video bytes", 409)

        def bounded_numbers(value: Any, field_name: str) -> list[float] | None:
            if value is None:
                return None
            if not isinstance(value, list) or len(value) > 10000:
                raise ServiceError(f"{field_name} must be a list with at most 10000 values", 422)
            numbers: list[float] = []
            for item in value:
                try:
                    number = float(item)
                except (TypeError, ValueError) as exc:
                    raise ServiceError(f"{field_name} contains a non-numeric value", 422) from exc
                if not math.isfinite(number):
                    raise ServiceError(f"{field_name} contains a non-finite value", 422)
                numbers.append(number)
            return numbers

        def finite_number(value: Any, field_name: str) -> float | None:
            if value is None:
                return None
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ServiceError(f"{field_name} must be numeric", 422) from exc
            if not math.isfinite(number):
                raise ServiceError(f"{field_name} must be finite", 422)
            return number

        resolution = evidence.get("resolution_contract")
        normalized_resolution: tuple[int, int] | None = None
        if resolution is not None:
            if not isinstance(resolution, (list, tuple)) or len(resolution) != 2:
                raise ServiceError("resolution_contract must contain width and height", 422)
            try:
                normalized_resolution = (int(resolution[0]), int(resolution[1]))
            except (TypeError, ValueError) as exc:
                raise ServiceError("resolution_contract must contain integers", 422) from exc

        media_evidence = MediaEvidence(
            asset_id=asset_id,
            ffprobe=evidence.get("ffprobe") if isinstance(evidence.get("ffprobe"), dict) else {},
            decoded_fully=evidence.get("decoded_fully") if isinstance(evidence.get("decoded_fully"), bool) else None,
            frame_luma_sequence=bounded_numbers(evidence.get("frame_luma_sequence"), "frame_luma_sequence"),
            audio_rms_sequence=bounded_numbers(evidence.get("audio_rms_sequence"), "audio_rms_sequence"),
            duration_contract_sec=finite_number(evidence.get("duration_contract_sec"), "duration_contract_sec"),
            fps_contract=finite_number(evidence.get("fps_contract"), "fps_contract"),
            resolution_contract=normalized_resolution,
            audio_duration_sec=finite_number(evidence.get("audio_duration_sec"), "audio_duration_sec"),
            sha256=actual_sha256,
        )
        report = run_auto_qc(media_evidence)
        has_unknown = any(finding.severity == "unknown" for finding in report.findings)
        review_status = "FAIL" if report.has_gate_failures else "UNKNOWN" if has_unknown else "PASS"
        issues = [
            f"{finding.code}: {finding.detail}"
            for finding in report.findings
            if finding.severity != "pass"
        ]
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        review_id = new_id("review")
        timestamp = now()
        with self.store.connection() as connection:
            replay = False
            if canonical_key:
                decision = connection.execute(
                    "SELECT asset_id, status, issues_json, review_id FROM asset_review_decisions WHERE user_id = ? AND idempotency_key = ?",
                    (user_id, canonical_key),
                ).fetchone()
                if decision:
                    if str(decision["asset_id"]) != asset_id or str(decision["status"]).upper() != review_status:
                        raise ServiceError("Idempotency-Key was already used for another QC result", 409)
                    previous_review = connection.execute(
                        "SELECT raw_text FROM asset_reviews WHERE id = ?",
                        (decision["review_id"],),
                    ).fetchone()
                    previous_payload = json_loads(previous_review["raw_text"], {}) if previous_review else {}
                    if (
                        str(previous_payload.get("evidence_sha256") or "") != actual_sha256
                        or str(previous_payload.get("evidence_request_sha256") or "") != evidence_request_sha256
                    ):
                        raise ServiceError("Idempotency-Key was already used for another QC evidence", 409)
                    replay = True
                    review_id = str(decision["review_id"])
            if not replay:
                report_payload = report.to_dict()
                report_payload["evidence_sha256"] = actual_sha256
                report_payload["evidence_request_sha256"] = evidence_request_sha256
                connection.execute(
                    "INSERT INTO asset_reviews(id, asset_id, status, issues_json, raw_text, provider, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        review_id,
                        asset_id,
                        review_status,
                        json.dumps(issues, ensure_ascii=False),
                        json.dumps(report_payload, ensure_ascii=False),
                        "auto-qc",
                        "studio_core.qc/v1",
                        timestamp,
                    ),
                )
                if canonical_key:
                    connection.execute(
                        "INSERT INTO asset_review_decisions(id, user_id, asset_id, idempotency_key, status, issues_json, review_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            new_id("review-decision"),
                            user_id,
                            asset_id,
                            canonical_key,
                            review_status,
                            json.dumps(issues, ensure_ascii=False),
                            review_id,
                            timestamp,
                        ),
                    )
                if report.has_gate_failures:
                    connection.execute(
                        "UPDATE assets SET consistency_confirmed = ?, updated_at = ? WHERE id = ?",
                        (False, timestamp, asset_id),
                    )
        result = self.asset_with_job(asset_id, user_id)
        result["auto_qc"] = report.to_dict()
        result["auto_qc"]["review_status"] = review_status
        result["auto_qc"]["evidence_sha256"] = actual_sha256
        return result

    def review_asset(self, asset_id: str, prompt: str = "", audit_type: str = "scene", user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
        """本地同步兼容入口；生产/显式 queued 请求使用 request_asset_review。"""
        return self._review_asset_now(asset_id, prompt, audit_type, user_id)

    def request_asset_review(
        self,
        asset_id: str,
        prompt: str = "",
        audit_type: str = "scene",
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """同步执行或创建可恢复的视觉审核 Job。

        审核本身不消耗积分，但 prompt/audit_type 必须随 Job 持久化，确保
        BullMQ 重试、SQLite worker 恢复或 API 重启后不会丢失审核上下文。
        """
        asset = self.asset_with_job(asset_id, user_id)
        if asset["kind"] != "image":
            raise ServiceError("only image assets can be visually reviewed")
        if not asset.get("url"):
            raise ServiceError("image asset is not ready", 409)
        normalized_prompt = str(prompt or "").strip()
        normalized_audit_type = str(audit_type or "scene").strip() or "scene"
        if len(normalized_prompt) > 12000:
            raise ServiceError("visual review prompt is too long", 422)
        if len(normalized_audit_type) > 120:
            raise ServiceError("visual review audit type is too long", 422)
        payload = {"prompt": normalized_prompt, "audit_type": normalized_audit_type}
        canonical_key = self._canonical_idempotency_key(user_id, idempotency_key)
        request_key = f"vision-review:{canonical_key}" if canonical_key else None
        if canonical_key:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE user_id = ? AND idempotency_key = ?",
                (user_id, request_key),
            )
            if existing:
                if existing["kind"] != "vision-review" or existing["target_id"] != asset_id:
                    raise ServiceError("Idempotency-Key was already used for another visual review request", 409)
                if json_loads(existing["payload_json"], {}) != payload:
                    raise ServiceError("Idempotency-Key was already used with different visual review input", 409)
                if run_now and existing["status"] == "queued":
                    return self.run_job(str(existing["id"]), user_id)
                if existing["status"] == "queued":
                    self._enqueue_external(existing["id"], user_id, "vision-review", asset_id)
                return self.asset_with_job(asset_id, user_id)
        if run_now and not request_key:
            return self._review_asset_now(asset_id, normalized_prompt, normalized_audit_type, user_id)
        if not request_key:
            active = self.store.one(
                "SELECT * FROM jobs WHERE target_id = ? AND user_id = ? AND kind = 'vision-review' AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
                (asset_id, user_id),
            )
            if active:
                if active["status"] == "queued":
                    self._enqueue_external(active["id"], user_id, "vision-review", asset_id)
                return self.asset_with_job(asset_id, user_id)
        providers = self._providers_for_user(user_id)
        job_id = new_id("job")
        request_key = request_key or f"vision-review:{job_id}"
        with self.store.connection() as connection:
            inserted = self._insert_job(connection, job_id, user_id, "vision-review", asset_id, 0, providers.vision.name, request_key)
            if inserted:
                connection.execute("UPDATE jobs SET payload_json = ? WHERE id = ?", (json.dumps(payload, ensure_ascii=False), job_id))
        if not inserted:
            existing = self.store.one(
                "SELECT * FROM jobs WHERE idempotency_key = ? AND user_id = ?",
                (request_key, user_id),
            )
            if not existing or existing["kind"] != "vision-review" or existing["target_id"] != asset_id:
                raise ServiceError("Idempotency-Key was already used for another visual review request", 409)
            job_id = str(existing["id"])
        if run_now:
            return self.run_job(job_id, user_id)
        self._enqueue_external(job_id, user_id, "vision-review", asset_id)
        return self.asset_with_job(asset_id, user_id)

    def review_assets(
        self,
        project_id: str,
        asset_ids: list[str] | None = None,
        prompt: str = "",
        audit_type: str = "scene",
        user_id: str = DEFAULT_USER_ID,
        run_now: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """提交项目关键帧视觉审核，保留逐资产结果并支持队列恢复。"""
        self.get_project(project_id, user_id)
        rows = self.store.all(
            "SELECT * FROM assets WHERE project_id = ? AND kind = 'image' ORDER BY created_at, id",
            (project_id,),
        )
        by_id = {str(row["id"]): dict(row) for row in rows}
        if asset_ids is None:
            selected_ids = list(by_id)
        else:
            selected_ids = []
            for raw_id in asset_ids:
                normalized = str(raw_id or "").strip()
                if normalized and normalized not in selected_ids:
                    selected_ids.append(normalized)
        if len(selected_ids) > 128:
            raise ServiceError("a visual review batch cannot exceed 128 image assets")
        missing = [asset_id for asset_id in selected_ids if asset_id not in by_id]
        if missing:
            raise ServiceError("image asset not found", 404)

        items: list[dict[str, Any]] = []
        reviewed = 0
        queued = 0
        skipped = 0
        failed = 0
        for asset_id in selected_ids:
            asset = by_id[asset_id]
            if str(asset["status"]) != "ready" or not asset.get("url"):
                skipped += 1
                items.append({"asset_id": asset_id, "action": "skipped", "status": str(asset["status"]), "reason": "asset is not ready"})
                continue
            try:
                child_key = f"{idempotency_key}:asset:{asset_id}" if idempotency_key else None
                result = self.request_asset_review(asset_id, prompt, audit_type, user_id, run_now, child_key)
                job = result.get("job") or {}
                if str(job.get("status")) in {"queued", "running"}:
                    queued += 1
                    items.append({"asset_id": asset_id, "action": "queued", "status": str(job.get("status")), "asset": result})
                else:
                    latest_review = (result.get("reviews") or [{}])[0]
                    reviewed += 1
                    items.append({"asset_id": asset_id, "action": "reviewed", "status": latest_review.get("status", "UNKNOWN"), "asset": result})
            except ServiceError as exc:
                failed += 1
                items.append({"asset_id": asset_id, "action": "failed", "status": "failed", "error": str(exc)})
        batch_status = "failed" if failed and not reviewed and not queued and not skipped else "partial" if failed else "queued" if queued else "completed"
        return {
            "project_id": project_id,
            "status": batch_status,
            "requested": len(selected_ids),
            "reviewed": reviewed,
            "queued": queued,
            "skipped": skipped,
            "failed": failed,
            "items": items,
        }

    def project_summary(self, project: dict[str, Any]) -> dict[str, Any]:
        project["character_count"] = self._count("SELECT COUNT(*) AS count FROM characters WHERE project_id = ?", (project["id"],))
        project["episode_count"] = self._count("SELECT COUNT(*) AS count FROM episodes WHERE project_id = ?", (project["id"],))
        project["asset_count"] = self._count("SELECT COUNT(*) AS count FROM assets WHERE project_id = ?", (project["id"],))
        return project

    def character_detail(self, character: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        """返回角色卡及其母版；权限已经由调用方的查询条件保证。"""
        character = self._character_dict(character)
        character["references"] = [
            dict(reference)
            for reference in self.store.all(
                "SELECT * FROM character_references WHERE character_id = ? "
                "ORDER BY CASE WHEN status = 'ready' THEN 0 "
                "WHEN status IN ('uploaded', 'pending', 'generating') THEN 1 ELSE 2 END, id DESC",
                (character["id"],),
            )
        ]
        if self.storage.mode != "local":
            for reference in character["references"]:
                for view in ("front_url", "side_url", "back_url"):
                    reference[view] = self._media_url(reference.get(view))
        return character

    def episode_detail(self, episode: dict[str, Any]) -> dict[str, Any]:
        episode["narrative_units"] = [
            self._narrative_unit_detail(dict(row))
            for row in self.store.all(
                "SELECT * FROM narrative_units WHERE episode_id = ? ORDER BY created_at, id",
                (episode["id"],),
            )
        ]
        episode["scenes"] = [
            self._scene_detail(dict(row))
            for row in self.store.all(
                "SELECT * FROM scenes WHERE episode_id = ? ORDER BY created_at, id",
                (episode["id"],),
            )
        ]
        episode["shots"] = [
            self.shot_detail(dict(row))
            for row in self.store.all(
                "SELECT * FROM shots WHERE episode_id = ? AND COALESCE(status, 'active') <> 'superseded' ORDER BY sequence",
                (episode["id"],),
            )
        ]
        episode["compositions"] = [dict(row) for row in self.store.all("SELECT * FROM compositions WHERE episode_id = ? ORDER BY created_at DESC", (episode["id"],))]
        if self.storage.mode != "local":
            for composition in episode["compositions"]:
                metadata = json_loads(composition.get("metadata_json"), {})
                composition["playlist_url"] = self._media_url(composition.get("playlist_url"), metadata.get("playlist_storage_key"))
                composition["final_video_url"] = self._media_url(composition.get("final_video_url"), metadata.get("final_storage_key"))
        settings = self.store.one("SELECT * FROM composition_settings WHERE episode_id = ?", (episode["id"],))
        tracks = json_loads(settings["audio_tracks_json"], []) if settings else []
        subtitles = json_loads(settings["subtitles_json"], []) if settings else []
        episode["composition_settings"] = {
            "episode_id": episode["id"],
            "audio_tracks": tracks,
            "subtitles": subtitles,
            "narration_text": str(settings["narration_text"] or "") if settings else "",
            "updated_at": settings["updated_at"] if settings else None,
            "audio_contract": self._audio_contract(tracks, subtitles),
        }
        return episode

    def shot_detail(self, shot: dict[str, Any]) -> dict[str, Any]:
        shot["adaptation_unit_ids"] = json_loads(shot.pop("adaptation_unit_ids_json", "[]"), [])
        shot["image_prompt"] = self.prompt_detail(dict(self.store.one("SELECT * FROM image_prompts WHERE id = ?", (shot["image_prompt_id"],)))) if shot.get("image_prompt_id") else None
        shot["assets"] = [self.asset_with_job(row["id"]) for row in self.store.all("SELECT id FROM assets WHERE shot_id = ? ORDER BY created_at", (shot["id"],))]
        return shot

    def prompt_detail(self, prompt: dict[str, Any]) -> dict[str, Any]:
        return prompt

    def _record_adaptation_revision(
        self,
        connection: Any,
        unit_id: str,
        project_id: str,
        source_text: str,
        adapted_text: str,
        mode: str,
        provider: str,
        metadata: dict[str, Any] | None = None,
        version: int | None = None,
    ) -> dict[str, Any]:
        if version is None:
            current = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM adaptation_revisions WHERE adaptation_unit_id = ?",
                (unit_id,),
            ).fetchone()
            version = int(current["version"] or 0) + 1
        version = max(1, int(version))
        safe_metadata = {
            key: value
            for key, value in (metadata or {}).items()
            if key in {"event", "preview", "model"} and value is not None
        }
        safe_metadata["quality"] = self._adaptation_quality(source_text, adapted_text, mode)
        revision = {
            "id": new_id("adaptation-revision"),
            "adaptation_unit_id": unit_id,
            "project_id": project_id,
            "version": version,
            "mode": str(mode)[:64],
            "source_text": str(source_text),
            "adapted_text": str(adapted_text),
            "diff": self._adaptation_diff(str(source_text), str(adapted_text)),
            "provider": str(provider or "local")[:120],
            "metadata": safe_metadata,
            "created_at": now(),
        }
        connection.execute(
            "INSERT INTO adaptation_revisions(id, adaptation_unit_id, project_id, version, mode, source_text, adapted_text, diff_json, provider, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision["id"],
                unit_id,
                project_id,
                version,
                revision["mode"],
                revision["source_text"],
                revision["adapted_text"],
                json.dumps(revision["diff"], ensure_ascii=False),
                revision["provider"],
                json.dumps(safe_metadata, ensure_ascii=False),
                revision["created_at"],
            ),
        )
        return revision

    def _revision_dict(self, item: dict[str, Any]) -> dict[str, Any]:
        item["diff"] = json_loads(item.pop("diff_json", "[]"), [])
        item["metadata"] = json_loads(item.pop("metadata_json", "{}"), {})
        return item

    def _adaptation_dict(self, item: dict[str, Any]) -> dict[str, Any]:
        item["traceability"] = json_loads(item.pop("traceability_json", "{}"), {})
        item["traceability"].setdefault(
            "adaptation_quality",
            self._adaptation_quality(str(item.get("source_text") or ""), str(item.get("adapted_text") or ""), str(item.get("mode") or "faithful")),
        )
        item["diff"] = self._adaptation_diff(str(item.get("source_text") or ""), str(item.get("adapted_text") or ""))
        item["revisions"] = [
            self._revision_dict(dict(row))
            for row in self.store.all("SELECT * FROM adaptation_revisions WHERE adaptation_unit_id = ? ORDER BY version", (item["id"],))
        ]
        return item

    @staticmethod
    def _adaptation_diff(source_text: str, adapted_text: str) -> list[dict[str, str]]:
        """生成不覆盖原文的最小可审计差异片段。"""

        parts: list[dict[str, str]] = []
        matcher = SequenceMatcher(None, source_text, adapted_text, autojunk=False)
        for tag, source_start, source_end, adapted_start, adapted_end in matcher.get_opcodes():
            if tag == "equal":
                parts.append({"type": "equal", "text": source_text[source_start:source_end]})
            elif tag == "delete":
                parts.append({"type": "delete", "text": source_text[source_start:source_end]})
            elif tag == "insert":
                parts.append({"type": "insert", "text": adapted_text[adapted_start:adapted_end]})
            else:
                if source_start != source_end:
                    parts.append({"type": "delete", "text": source_text[source_start:source_end]})
                if adapted_start != adapted_end:
                    parts.append({"type": "insert", "text": adapted_text[adapted_start:adapted_end]})
        return parts

    @staticmethod
    def _adaptation_quality(source_text: str, adapted_text: str, mode: str) -> dict[str, Any]:
        """计算改编审校提示指标，不宣称这是法律或抄袭判定。"""

        source = re.sub(r"\s+", "", str(source_text or ""))
        adapted = re.sub(r"\s+", "", str(adapted_text or ""))
        ratio = SequenceMatcher(None, source, adapted, autojunk=False).ratio() if source or adapted else 1.0

        def ngrams(value: str) -> set[str]:
            if len(value) < 2:
                return {value} if value else set()
            return {value[index : index + 2] for index in range(len(value) - 1)}

        source_grams = ngrams(source)
        adapted_grams = ngrams(adapted)
        overlap = len(source_grams & adapted_grams) / len(source_grams) if source_grams else (1.0 if not adapted_grams else 0.0)
        if mode == AdaptationMode.FAITHFUL.value:
            risk = "not_applicable"
        elif not source or not adapted:
            risk = "high"
        elif ratio >= 0.85 or overlap >= 0.75:
            risk = "high"
        elif ratio >= 0.60 or overlap >= 0.45:
            risk = "medium"
        else:
            risk = "low"
        return {
            "method": "character-sequence-v1",
            "sequence_ratio": round(ratio, 4),
            "ngram_overlap": round(overlap, 4),
            "source_chars": len(source),
            "adapted_chars": len(adapted),
            "risk": risk,
            "requires_human_review": mode != AdaptationMode.FAITHFUL.value,
            "disclaimer": "启发式提示，不是法律意见或抄袭结论",
        }

    def _character_dict(self, item: dict[str, Any]) -> dict[str, Any]:
        item["visual_lock"] = json_loads(item.pop("visual_lock_json", "{}"), {})
        return item

    def _count(self, sql: str, params: tuple[Any, ...]) -> int:
        row = self.store.one(sql, params)
        return int(row["count"]) if row else 0

    @staticmethod
    def _ensure_user_connection(connection: Any, user_id: str) -> None:
        connection.execute("INSERT INTO credit_accounts(user_id, balance, updated_at) VALUES (?, 100, ?) ON CONFLICT(user_id) DO NOTHING", (user_id, now()))

    @staticmethod
    def _canonical_idempotency_key(user_id: str, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if len(normalized) > 256:
            raise ServiceError("Idempotency-Key must be at most 256 characters")
        return hashlib.sha256(f"{user_id}\0{normalized}".encode("utf-8")).hexdigest()

    @staticmethod
    def _transition_job_status(
        connection: Any,
        job_id: str,
        new_status: str,
        *,
        error: Any = _UNSET,
        expected_status: str | tuple[str, ...] | None = None,
        expected_updated_at: str | None = None,
        increment_attempt: bool = False,
    ) -> int:
        """唯一的服务层 Job 状态写入口；SQLite/PostgreSQL trigger 再做数据库级兜底。"""
        current = connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if current and error is not _UNSET and not persisted_transition_allowed(str(current["status"]), new_status, error):
            raise ServiceError(f"invalid job status transition: {current['status']} -> {new_status}", 409)
        if current and error is _UNSET and not persisted_transition_allowed(str(current["status"]), new_status):
            raise ServiceError(f"invalid job status transition: {current['status']} -> {new_status}", 409)
        assignments = ["status = ?", "updated_at = ?"]
        params: list[Any] = [new_status, now()]
        if increment_attempt:
            assignments.append("attempts = attempts + 1")
        if error is not _UNSET:
            assignments.append("error = ?")
            params.append(error)
        if new_status == "completed":
            assignments.extend(["progress_percent = ?", "progress_message = ?"])
            params.extend([100, "已完成"])
        elif new_status == "queued":
            assignments.extend(["progress_percent = ?", "progress_message = ?"])
            params.extend([0, "等待处理"])
        elif new_status == "cancelled":
            assignments.append("progress_message = ?")
            params.append("已取消")
        elif new_status == "failed":
            assignments.append("progress_message = ?")
            params.append("处理失败")
        clauses = ["id = ?"]
        params.append(job_id)
        if expected_status is not None:
            if isinstance(expected_status, tuple):
                placeholders = ", ".join("?" for _ in expected_status)
                clauses.append(f"status IN ({placeholders})")
                params.extend(expected_status)
            else:
                clauses.append("status = ?")
                params.append(expected_status)
        if expected_updated_at is not None:
            clauses.append("updated_at = ?")
            params.append(expected_updated_at)
        result = connection.execute(
            f"UPDATE jobs SET {', '.join(assignments)} WHERE {' AND '.join(clauses)}",
            tuple(params),
        )
        return int(result.rowcount)

    def _set_job_progress(self, job_id: str, percent: int, message: str) -> None:
        """写入可恢复的服务端进度；完成状态只由状态迁移统一写到 100。"""

        bounded_percent = max(0, min(99, int(percent)))
        bounded_message = str(message or "").strip()[:200]
        self.store.write(
            "UPDATE jobs SET progress_percent = ?, progress_message = ?, updated_at = ? "
            "WHERE id = ? AND status IN ('queued', 'running')",
            (bounded_percent, bounded_message, now(), job_id),
        )

    @staticmethod
    def _insert_job(
        connection: Any,
        job_id: str,
        user_id: str,
        kind: str,
        target_id: str,
        cost: int,
        provider: str,
        idempotency_key: str | None = None,
    ) -> bool:
        timestamp = now()
        if idempotency_key:
            result = connection.execute(
                "INSERT INTO jobs(id, user_id, kind, target_id, status, cost_credits, provider, idempotency_key, created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?) ON CONFLICT(idempotency_key) DO NOTHING",
                (job_id, user_id, kind, target_id, cost, provider, idempotency_key, timestamp, timestamp),
            )
            return bool(result.rowcount)
        connection.execute(
            "INSERT INTO jobs(id, user_id, kind, target_id, status, cost_credits, provider, created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
            (job_id, user_id, kind, target_id, cost, provider, timestamp, timestamp),
        )
        return True

    @staticmethod
    def _resolve_idempotent_target(connection: Any, idempotency_key: str | None, user_id: str, kind: str, requested_target_id: str) -> str:
        if not idempotency_key:
            raise ServiceError("idempotent job conflict", 409)
        job = connection.execute("SELECT * FROM jobs WHERE idempotency_key = ? AND user_id = ?", (idempotency_key, user_id)).fetchone()
        if not job:
            raise ServiceError("idempotent job could not be resolved", 409)
        asset = connection.execute("SELECT * FROM assets WHERE id = ?", (job["target_id"],)).fetchone()
        asset_metadata = json_loads(asset["metadata_json"], {}) if asset else {}
        matches = bool(
            asset
            and job["kind"] == kind
            and (
                (kind == "image" and asset["shot_id"] == requested_target_id)
                or (
                    kind == "video"
                    and (
                        asset["source_asset_id"] == requested_target_id
                        or (
                            asset["source_asset_id"] is None
                            and asset["shot_id"] == requested_target_id
                            and asset_metadata.get("generation_mode") == "t2v"
                        )
                    )
                )
            )
        )
        if not matches:
            raise ServiceError("Idempotency-Key was already used for another generation request", 409)
        return str(job["target_id"])

    @staticmethod
    def _resolve_narration_target(connection: Any, idempotency_key: str | None, user_id: str, requested_episode_id: str) -> str:
        if not idempotency_key:
            raise ServiceError("idempotent job conflict", 409)
        job = connection.execute(
            "SELECT * FROM jobs WHERE idempotency_key = ? AND user_id = ?",
            (idempotency_key, user_id),
        ).fetchone()
        if not job:
            raise ServiceError("idempotent job could not be resolved", 409)
        asset = connection.execute("SELECT * FROM assets WHERE id = ?", (job["target_id"],)).fetchone()
        metadata = json_loads(asset["metadata_json"], {}) if asset else {}
        matches = bool(
            asset
            and job["kind"] == "narration"
            and asset["kind"] == "audio"
            and str(metadata.get("episode_id") or "") == requested_episode_id
        )
        if not matches:
            raise ServiceError("Idempotency-Key was already used for another narration request", 409)
        return str(job["target_id"])

    @staticmethod
    def _resolve_character_reference_target(connection: Any, idempotency_key: str | None, user_id: str, requested_character_id: str) -> str:
        if not idempotency_key:
            raise ServiceError("idempotent job conflict", 409)
        job = connection.execute("SELECT * FROM jobs WHERE idempotency_key = ? AND user_id = ?", (idempotency_key, user_id)).fetchone()
        if not job:
            raise ServiceError("idempotent job could not be resolved", 409)
        reference = connection.execute("SELECT * FROM character_references WHERE id = ?", (job["target_id"],)).fetchone()
        if not reference or job["kind"] != "character-reference" or reference["character_id"] != requested_character_id:
            raise ServiceError("Idempotency-Key was already used for another generation request", 409)
        return str(job["target_id"])

    @staticmethod
    def _reserve(connection: Any, user_id: str, job_id: str, amount: int, reason: str) -> str:
        if amount <= 0:
            return "no-reservation"
        updated = connection.execute(
            "UPDATE credit_accounts SET balance = balance - ?, updated_at = ? WHERE user_id = ? AND balance >= ?",
            (amount, now(), user_id, amount),
        )
        if updated.rowcount == 0:
            StudioService._transition_job_status(connection, job_id, "failed", error="insufficient credits", expected_status="queued")
            raise ServiceError("insufficient credits", 402)
        reservation_id = new_id("reservation")
        balance = connection.execute("SELECT balance FROM credit_accounts WHERE user_id = ?", (user_id,)).fetchone()["balance"]
        connection.execute("INSERT INTO credit_reservations(id, user_id, job_id, amount, status, created_at) VALUES (?, ?, ?, ?, 'reserved', ?)", (reservation_id, user_id, job_id, amount, now()))
        connection.execute("INSERT INTO credit_transactions(id, user_id, job_id, kind, amount, balance_after, reason, created_at) VALUES (?, ?, ?, 'usage_reserved', ?, ?, ?, ?)", (new_id("txn"), user_id, job_id, -amount, balance, reason, now()))
        return reservation_id

    @staticmethod
    def _commit_reservation(connection: Any, reservation_id: str) -> None:
        if reservation_id != "no-reservation":
            connection.execute("UPDATE credit_reservations SET status = 'committed' WHERE id = ? AND status = 'reserved'", (reservation_id,))

    @staticmethod
    def _refund(connection: Any, reservation_id: str, reason: str) -> None:
        if reservation_id == "no-reservation":
            return
        reservation = connection.execute("SELECT * FROM credit_reservations WHERE id = ?", (reservation_id,)).fetchone()
        if not reservation or reservation["status"] != "reserved":
            return
        updated = connection.execute("UPDATE credit_reservations SET status = 'refunded' WHERE id = ? AND status = 'reserved'", (reservation_id,))
        if updated.rowcount == 0:
            return
        connection.execute("UPDATE credit_accounts SET balance = balance + ?, updated_at = ? WHERE user_id = ?", (reservation["amount"], now(), reservation["user_id"]))
        balance = connection.execute("SELECT balance FROM credit_accounts WHERE user_id = ?", (reservation["user_id"],)).fetchone()["balance"]
        connection.execute("INSERT INTO credit_transactions(id, user_id, job_id, kind, amount, balance_after, reason, created_at) VALUES (?, ?, ?, 'usage_refunded', ?, ?, ?, ?)", (new_id("txn"), reservation["user_id"], reservation["job_id"], reservation["amount"], balance, reason, now()))

    def _requeue_existing_external(self, target_id: str, user_id: str, kind: str) -> None:
        """幂等重试时重新投递尚未完成的外部任务，避免首轮队列故障留下孤儿 Job。"""
        if os.environ.get("STUDIO_QUEUE_BACKEND", "local") != "bullmq":
            return
        job = self.store.one(
            "SELECT * FROM jobs WHERE target_id = ? AND user_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
            (target_id, user_id, kind),
        )
        if job and job["status"] == "queued":
            self._enqueue_external(job["id"], user_id, kind, target_id)

    @staticmethod
    def _enqueue_external(job_id: str, user_id: str, kind: str, target_id: str) -> None:
        if os.environ.get("STUDIO_QUEUE_BACKEND", "local") != "bullmq":
            return
        endpoint = os.environ.get("STUDIO_ORCHESTRATOR_URL", "http://127.0.0.1:8790").rstrip("/") + "/jobs"
        payload = json.dumps({"jobId": job_id, "userId": user_id, "kind": kind, "targetId": target_id}).encode()
        headers = {"Content-Type": "application/json"}
        if os.environ.get("STUDIO_ORCHESTRATOR_TOKEN"):
            headers["X-Orchestrator-Token"] = os.environ["STUDIO_ORCHESTRATOR_TOKEN"]
        request = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status >= 300:
                    raise ServiceError("external queue rejected job", 503)
        except (urllib.error.URLError, TimeoutError) as exc:
            # 保留 queued 状态，管理员可用本地 worker 恢复；不伪造已入队。
            raise ServiceError("external queue is unavailable; queued job can be recovered by local worker", 503) from exc

    def _write_character_svg(self, character_id: str, name: str, view: str) -> str:
        path = self.asset_dir / f"{character_id}-{view}.svg"
        label = html.escape(name[:24])
        view_label = html.escape(view.upper())
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="420" height="560" viewBox="0 0 420 560"><rect width="420" height="560" fill="#181624"/><rect x="18" y="18" width="384" height="524" rx="18" fill="#292743" stroke="#b6a7ff" stroke-width="2"/><text x="210" y="55" text-anchor="middle" fill="#b6a7ff" font-size="20" font-family="sans-serif">{view_label} VIEW</text><circle cx="210" cy="190" r="64" fill="#f0b7a4"/><path d="M100 470 Q210 260 320 470" fill="#4d78c4"/><text x="210" y="505" text-anchor="middle" fill="white" font-size="18" font-family="sans-serif">{label}</text></svg>'''
        path.write_text(svg, encoding="utf-8")
        return f"/assets/{path.name}"
