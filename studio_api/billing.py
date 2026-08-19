"""支付厂商适配器。

这里保留平台内部的 provider-neutral 订单/结算合同，具体厂商只负责：

* 创建外部 checkout session；
* 验证原始 webhook body 的签名；
* 将厂商事件归一化为 ``settle_billing_webhook`` 可消费的 payload。

不使用 SDK 是为了让 API 镜像继续保持轻量；生产 secret 只从环境或 secret
manager 注入，不进入订单、日志或异常文本。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any


class BillingProviderError(RuntimeError):
    """可安全展示给 API 层的支付适配器错误。"""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def _safe_url(value: str, *, require_https: bool = False) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise BillingProviderError("billing URL is invalid", 503)
    if require_https and parsed.scheme != "https":
        raise BillingProviderError("billing URL must use HTTPS", 503)
    return str(value).strip().rstrip("/")


def _required_secret(value: str | None, label: str) -> str:
    secret = str(value or "").strip()
    if not secret:
        raise BillingProviderError(f"{label} is not configured", 503)
    return secret


class StripeCheckoutAdapter:
    """Stripe Checkout Session/API-format adapter。

    The adapter intentionally returns only the provider session id and hosted URL;
    it never returns or embeds the API key.
    """

    SUPPORTED_EVENT_TYPES = frozenset(
        {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
            "checkout.session.async_payment_failed",
            "checkout.session.expired",
            "refund.created",
            "charge.dispute.funds_withdrawn",
            "charge.dispute.funds_reinstated",
        }
    )

    def __init__(
        self,
        *,
        api_base_url: str | None = None,
        secret_key: str | None = None,
        webhook_secret: str | None = None,
        timeout: float | None = None,
    ) -> None:
        configured_base = api_base_url if api_base_url is not None else os.environ.get("STRIPE_API_BASE_URL", "https://api.stripe.com")
        self.api_base_url = _safe_url(configured_base)
        self.secret_key = secret_key if secret_key is not None else os.environ.get("STRIPE_SECRET_KEY", "")
        self.webhook_secret = webhook_secret if webhook_secret is not None else os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        try:
            self.timeout = max(1.0, min(float(timeout if timeout is not None else os.environ.get("STRIPE_HTTP_TIMEOUT", "15")), 60.0))
        except (TypeError, ValueError):
            self.timeout = 15.0

    @classmethod
    def from_env(cls) -> "StripeCheckoutAdapter":
        return cls()

    def create_checkout(self, order: Mapping[str, Any], *, success_url: str, cancel_url: str) -> dict[str, str]:
        secret = _required_secret(self.secret_key, "Stripe secret key")
        success = _safe_url(success_url)
        cancel = _safe_url(cancel_url)
        try:
            amount_cents = int(order["amount_cents"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BillingProviderError("billing order amount is invalid", 400) from exc
        if amount_cents <= 0:
            raise BillingProviderError("billing order amount is invalid", 400)
        currency = str(order.get("currency", "")).strip().lower()
        if not re.fullmatch(r"[a-z]{3}", currency):
            raise BillingProviderError("billing order currency is invalid", 400)
        order_id = str(order.get("id", "")).strip()
        provider_order_id = str(order.get("provider_order_id", "")).strip()
        if not order_id or not provider_order_id:
            raise BillingProviderError("billing order reference is invalid", 400)
        product_name = str(order.get("package_label") or order.get("package_code") or "AI 漫剧创作积分").strip()[:200]
        fields = {
            "mode": "payment",
            "client_reference_id": order_id,
            "success_url": success,
            "cancel_url": cancel,
            "line_items[0][price_data][currency]": currency,
            "line_items[0][price_data][unit_amount]": str(amount_cents),
            "line_items[0][price_data][product_data][name]": product_name,
            "line_items[0][quantity]": "1",
            "metadata[order_id]": order_id,
            "metadata[provider_order_id]": provider_order_id,
            "metadata[package_code]": str(order.get("package_code", ""))[:64],
            "payment_intent_data[metadata][order_id]": order_id,
            "payment_intent_data[metadata][provider_order_id]": provider_order_id,
            "payment_intent_data[metadata][credits]": str(order.get("credits", "")),
        }
        payload = urllib.parse.urlencode(fields).encode("utf-8")
        authorization = base64.b64encode(f"{secret}:".encode("utf-8")).decode("ascii")
        request = urllib.request.Request(
            f"{self.api_base_url}/v1/checkout/sessions",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Basic {authorization}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Idempotency-Key": provider_order_id,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise BillingProviderError("Stripe checkout request failed", 502) from exc
        except (OSError, TimeoutError) as exc:
            raise BillingProviderError("Stripe checkout service is unavailable", 502) from exc
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BillingProviderError("Stripe checkout response is invalid", 502) from exc
        if not isinstance(result, dict) or not str(result.get("id", "")).strip() or not str(result.get("url", "")).strip():
            raise BillingProviderError("Stripe checkout response is incomplete", 502)
        hosted_url = _safe_url(str(result["url"]))
        return {"session_id": str(result["id"]).strip(), "checkout_url": hosted_url}

    def verify_webhook(self, raw_body: bytes, signature: str | None, *, now_epoch: int | None = None) -> dict[str, Any]:
        secret = _required_secret(self.webhook_secret, "Stripe webhook secret")
        header = str(signature or "").strip()
        if not header:
            raise BillingProviderError("Stripe webhook signature is required", 400)
        timestamp: int | None = None
        signatures: list[str] = []
        for component in header.split(","):
            key, separator, value = component.strip().partition("=")
            if not separator or not value:
                continue
            if key == "t" and timestamp is None:
                try:
                    timestamp = int(value)
                except ValueError as exc:
                    raise BillingProviderError("Stripe webhook timestamp is invalid", 400) from exc
            elif key == "v1":
                signatures.append(value)
        if timestamp is None or not signatures:
            raise BillingProviderError("Stripe webhook signature is invalid", 403)
        current = int(time.time() if now_epoch is None else now_epoch)
        tolerance = 300
        try:
            tolerance = max(1, min(int(os.environ.get("STRIPE_WEBHOOK_TOLERANCE_SECONDS", "300")), 3600))
        except ValueError:
            pass
        if abs(current - timestamp) > tolerance:
            raise BillingProviderError("Stripe webhook timestamp has expired", 403)
        expected = hmac.new(secret.encode("utf-8"), str(timestamp).encode("ascii") + b"." + raw_body, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
            raise BillingProviderError("Stripe webhook signature is invalid", 403)
        try:
            event = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BillingProviderError("Stripe webhook body must be valid JSON", 400) from exc
        if not isinstance(event, dict):
            raise BillingProviderError("Stripe webhook body must be an object", 400)
        return event

    @classmethod
    def normalize_event(cls, event: Mapping[str, Any]) -> dict[str, Any] | None:
        event_id = str(event.get("id", "")).strip()
        event_type = str(event.get("type", "")).strip()
        if not event_id:
            raise BillingProviderError("Stripe webhook event id is required", 400)
        if event_type not in cls.SUPPORTED_EVENT_TYPES:
            return None
        data = event.get("data")
        session = data.get("object") if isinstance(data, dict) else None
        if not isinstance(session, dict):
            raise BillingProviderError("Stripe checkout session is missing", 400)
        metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
        order_id = str(metadata.get("order_id") or metadata.get("provider_order_id") or session.get("client_reference_id") or "").strip()
        provider_payment_id = str(session.get("payment_intent") or session.get("charge") or "").strip()
        if not order_id:
            order_id = provider_payment_id
        if not order_id:
            raise BillingProviderError("Stripe billing order reference is missing", 400)
        if event_type in {"checkout.session.async_payment_failed", "checkout.session.expired"}:
            return {"event_id": event_id, "order_id": order_id, "status": "cancelled"}
        if event_type in {"refund.created", "charge.dispute.funds_withdrawn", "charge.dispute.funds_reinstated"}:
            adjustment_type = {
                "refund.created": "refund",
                "charge.dispute.funds_withdrawn": "chargeback",
                "charge.dispute.funds_reinstated": "chargeback_reinstated",
            }[event_type]
            adjustment_id = str(session.get("id", "")).strip()
            try:
                amount_cents = int(session.get("amount"))
            except (TypeError, ValueError) as exc:
                raise BillingProviderError("Stripe adjustment amount is missing", 400) from exc
            currency = str(session.get("currency", "")).strip().lower()
            if not adjustment_id or amount_cents <= 0 or not re.fullmatch(r"[a-z]{3}", currency):
                raise BillingProviderError("Stripe adjustment amount or reference is invalid", 400)
            return {
                "event_id": event_id,
                "order_id": order_id,
                "status": "adjustment",
                "adjustment_id": adjustment_id,
                "adjustment_type": adjustment_type,
                "amount_cents": amount_cents,
                "currency": currency,
                "provider_payment_id": provider_payment_id,
            }
        if event_type == "checkout.session.completed" and str(session.get("payment_status", "")).strip().lower() not in {"paid", "no_payment_required"}:
            return None
        try:
            amount_cents = int(session.get("amount_total"))
        except (TypeError, ValueError) as exc:
            raise BillingProviderError("Stripe checkout amount is missing", 400) from exc
        currency = str(session.get("currency", "")).strip().lower()
        if amount_cents <= 0 or not re.fullmatch(r"[a-z]{3}", currency):
            raise BillingProviderError("Stripe checkout amount or currency is invalid", 400)
        return {
            "event_id": event_id,
            "order_id": order_id,
            "status": "paid",
            "amount_cents": amount_cents,
            "currency": currency,
            "provider_payment_id": provider_payment_id,
        }
