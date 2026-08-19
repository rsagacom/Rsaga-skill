#!/usr/bin/env python3
"""对运行中的 API/Web/BullMQ 栈执行一次不回显凭据的业务 smoke test。

默认使用本地预览 provider，因此不需要 GPU；若部署环境启用了真实 provider，
可用 --skip-generation 先验证账户、数据库、导出和 Web 边界，再单独验收 GPU。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import os
import time
import urllib.error
import urllib.request
import wave
import zipfile
from typing import Any


class SmokeClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.internal_token = os.environ.get("STUDIO_SMOKE_INTERNAL_TOKEN", "").strip()

    def call(self, path: str, method: str = "GET", payload: Any | None = None, extra_headers: dict[str, str] | None = None) -> Any:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra_headers:
            headers.update(extra_headers)
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{method} {path} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"{method} {path} could not be reached") from exc

    def expect_http_status(
        self,
        path: str,
        status: int,
        method: str = "GET",
        payload: Any | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """验证预期的 HTTP 错误，不把错误响应内容写入 smoke 报告。"""
        try:
            self.call(path, method, payload, extra_headers)
        except RuntimeError as exc:
            marker = f"{method} {path} returned HTTP {status}"
            if marker in str(exc):
                return
            raise
        raise RuntimeError(f"{method} {path} unexpectedly succeeded; expected HTTP {status}")

    def download(self, path: str, accept: str = "application/octet-stream") -> bytes:
        headers = {"Accept": accept}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(f"{self.base_url}{path}", headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GET {path} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"GET {path} could not be reached") from exc

    def upload_bytes(self, path: str, data: bytes, content_type: str, extra_headers: dict[str, str] | None = None) -> Any:
        headers = {"Accept": "application/json", "Content-Type": content_type}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"POST {path} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"POST {path} could not be reached") from exc

    def upload_multipart(
        self,
        path: str,
        filename: str,
        data: bytes,
        file_content_type: str,
        fields: dict[str, str],
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        boundary = f"studio-stack-smoke-{time.time_ns()}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("ascii"),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"),
                f"Content-Type: {file_content_type}\r\n\r\n".encode("ascii"),
                data,
                b"\r\n",
                f"--{boundary}--\r\n".encode("ascii"),
            ]
        )
        return self.upload_bytes(
            path,
            b"".join(chunks),
            f"multipart/form-data; boundary={boundary}",
            extra_headers=extra_headers,
        )

    def run_local_job(self, job_id: str) -> Any:
        """在明确提供 smoke 专用内部令牌时，驱动本地 queued Job worker。"""
        if not self.internal_token:
            raise RuntimeError("STUDIO_SMOKE_INTERNAL_TOKEN is required to drive a local queued job")
        request = urllib.request.Request(
            f"{self.base_url}/api/internal/jobs/{job_id}/run",
            data=b"{}",
            headers={"Accept": "application/json", "Content-Type": "application/json", "X-Studio-Internal-Token": self.internal_token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"POST /api/internal/jobs/{job_id}/run returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"POST /api/internal/jobs/{job_id}/run could not be reached") from exc


def wait_for_job(client: SmokeClient, job_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    local_triggered = False
    while time.monotonic() < deadline:
        job = client.call(f"/api/jobs/{job_id}")
        if job.get("status") == "completed":
            return job
        if job.get("status") in {"failed", "cancelled"}:
            raise RuntimeError(f"job {job_id} ended {job.get('status')}")
        if job.get("status") == "queued" and client.internal_token and not local_triggered:
            client.run_local_job(job_id)
            local_triggered = True
        time.sleep(1)
    raise RuntimeError(f"job {job_id} did not complete within {timeout:.0f}s")


def get_url(url: str) -> int:
    request = urllib.request.Request(url, headers={"Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read(256)
            return response.status
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"public endpoint returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("public endpoint could not be reached") from exc


def get_json_url(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"public health endpoint returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("public health endpoint request failed") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("public health endpoint returned invalid object")
    return payload


def smoke_wav_base64() -> str:
    """生成很小的合法 WAV，验证音频上传/存储合同而不依赖本地样本文件。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(44100)
        wav_file.writeframes(b"\0\0" * 22050)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def smoke_docx_bytes() -> bytes:
    document_xml = (
        '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>DOCX 第一段。</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>DOCX 第二段。</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>DOCX 第三段。</w:t></w:r></w:p></w:body></w:document>"
    ).encode("utf-8")
    document = io.BytesIO()
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return document.getvalue()


def smoke_epub_bytes() -> bytes:
    document = io.BytesIO()
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/package.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/package.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="chapter"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            "<html><body><p>EPUB 第一段。</p><p>EPUB 第二段。</p><p>EPUB 第三段。</p></body></html>",
        )
    return document.getvalue()


def smoke_pdf_bytes() -> bytes:
    stream = b"BT /F1 14 Tf 20 120 Td (PDF first.) Tj 0 -30 Td (PDF second.) Tj 0 -30 Td (PDF third.) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 240 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode("ascii"))
        document.extend(body)
        document.extend(b"\nendobj\n")
    xref = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        f"trailer\n<< /Root 1 0 R /Size {len(objects) + 1} >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(document)


def smoke_source_file_fixtures() -> tuple[tuple[str, str, str, bytes], ...]:
    return (
        ("smoke.txt", "text/plain", "text/plain", "TXT 第一段。\nTXT 第二段。\nTXT 第三段。\n".encode("utf-8")),
        ("smoke.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", smoke_docx_bytes()),
        ("smoke.epub", "application/epub+zip", "application/epub+zip", smoke_epub_bytes()),
        ("smoke.pdf", "application/pdf", "application/pdf", smoke_pdf_bytes()),
    )


def billing_signature(secret: str, timestamp: str, payload: Any) -> str:
    """生成与 FastAPI webhook 合同一致的短时 HMAC，不返回 secret。"""
    raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    signed = f"{timestamp}.".encode("utf-8") + raw_body
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def stripe_signature(secret: str, timestamp: str, payload: Any) -> str:
    """生成与 Stripe-Signature 原始 body 合同一致的 v1 签名，不返回 secret。"""
    raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    signed = f"{timestamp}.".encode("utf-8") + raw_body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.environ.get("STUDIO_SMOKE_API_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--web-url", default=os.environ.get("STUDIO_SMOKE_WEB_URL", "http://127.0.0.1:3000"))
    parser.add_argument("--orchestrator-url", default=os.environ.get("STUDIO_SMOKE_ORCHESTRATOR_URL", ""))
    parser.add_argument("--expect-store", default="", help="可选：要求 health.store 等于 postgres 或 sqlite")
    parser.add_argument("--expect-queue-backend", default="", help="可选：要求 health.queue_backend 等于 bullmq 或 local")
    parser.add_argument("--expect-storage", default="", help="可选：要求 health.storage 等于 local 或 s3")
    parser.add_argument("--require-orchestrator", action="store_true", help="要求 orchestrator health 返回 200 且 Redis 为 ok")
    parser.add_argument("--generation-timeout", type=float, default=300, help="异步生成 Job 的最大等待秒数")
    parser.add_argument("--password", default=os.environ.get("STUDIO_SMOKE_PASSWORD", "smoke-local-password"))
    parser.add_argument("--skip-generation", action="store_true", help="只验收 API/Web/账户/导出基础边界")
    parser.add_argument("--include-source-files", action="store_true", help="验收 TXT/DOCX/EPUB/PDF multipart 来源文件抽取、媒体类型、幂等和改编单元")
    parser.add_argument("--include-av", action="store_true", help="验收分集音频上传、字幕时间线和 Remotion A/V 合成；要求组合引擎为 remotion")
    parser.add_argument("--include-billing", action="store_true", help="验收充值订单、signed-webhook/Stripe 原始签名 paid 回调、异步取消和一次性积分入账；按 provider 注入对应 smoke secret")
    args = parser.parse_args()

    anonymous = SmokeClient(args.api_url)
    health = anonymous.call("/api/health")
    ready = anonymous.call("/api/ready")
    if health.get("status") != "ok" or ready.get("status") != "ready":
        raise RuntimeError("API health/readiness contract failed")
    if args.expect_store and health.get("store") != args.expect_store:
        raise RuntimeError(f"expected store {args.expect_store}, got {health.get('store')}")
    if args.expect_queue_backend and health.get("queue_backend") != args.expect_queue_backend:
        raise RuntimeError(f"expected queue backend {args.expect_queue_backend}, got {health.get('queue_backend')}")
    if args.expect_storage and health.get("storage") != args.expect_storage:
        raise RuntimeError(f"expected storage {args.expect_storage}, got {health.get('storage')}")
    if args.include_av and health.get("composition", {}).get("engine") != "remotion":
        raise RuntimeError("--include-av requires STUDIO_COMPOSE_ENGINE=remotion")

    billing_contract = "skipped"
    billing_cancellation_contract = "skipped"
    billing_adjustment_contract = "skipped"
    adaptation_rejection_contract = "skipped"
    source_file_contract = "skipped"
    ownership_contract = "skipped"
    asset_ownership_contract = "skipped"
    billing_provider = str(health.get("billing", {}).get("provider", "")).strip().lower()
    billing_secret = os.environ.get("STUDIO_SMOKE_BILLING_WEBHOOK_SECRET", "")
    stripe_webhook_secret = os.environ.get("STUDIO_SMOKE_STRIPE_WEBHOOK_SECRET", "")
    if args.include_billing:
        if billing_provider == "signed-webhook" and len(billing_secret) >= 32:
            pass
        elif billing_provider == "stripe" and len(stripe_webhook_secret) >= 32:
            pass
        else:
            raise RuntimeError("--include-billing requires a supported billing provider and its smoke webhook secret")

    email = f"stack-smoke-{time.time_ns()}@example.test"
    registered = anonymous.call("/api/auth/register", "POST", {"email": email, "password": args.password})
    client = SmokeClient(args.api_url, registered["token"])
    if args.include_billing:
        before_billing = client.call("/api/credits")["balance"]
        order = client.call(
            "/api/billing/orders",
            "POST",
            {"package_code": "starter"},
            extra_headers={"Idempotency-Key": f"stack-smoke-billing-v1-{email}"},
        )
        if not order.get("checkout_url"):
            raise RuntimeError("billing checkout URL contract failed")
        if billing_provider == "stripe":
            billing_payload = {
                "id": f"evt_stack_smoke_{email}",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": f"cs_stack_smoke_{email}",
                        "client_reference_id": order["id"],
                        "payment_status": "paid",
                        "amount_total": order["amount_cents"],
                        "currency": order["currency"],
                        "metadata": {
                            "order_id": order["id"],
                            "provider_order_id": order.get("provider_order_id", ""),
                        },
                    }
                },
            }
        else:
            billing_payload = {
                "event_id": f"stack-smoke-billing-event-v1-{email}",
                "order_id": order["id"],
                "status": "paid",
                "amount_cents": order["amount_cents"],
                "currency": order["currency"],
            }
        billing_timestamp = str(int(time.time()))
        if billing_provider == "stripe":
            billing_headers = {"Stripe-Signature": stripe_signature(stripe_webhook_secret, billing_timestamp, billing_payload)}
        else:
            billing_headers = {
                "X-Billing-Timestamp": billing_timestamp,
                "X-Billing-Signature": billing_signature(billing_secret, billing_timestamp, billing_payload),
            }
        settled = client.call("/api/billing/webhook", "POST", billing_payload, extra_headers=billing_headers)
        replay = client.call("/api/billing/webhook", "POST", billing_payload, extra_headers=billing_headers)
        after_billing = client.call("/api/credits")["balance"]
        if settled.get("status") != "paid" or not replay.get("duplicate") or after_billing - before_billing != order["credits"]:
            raise RuntimeError("billing order/webhook/idempotent credit contract failed")
        cancellation_event_types = (
            ["checkout.session.async_payment_failed", "checkout.session.expired"]
            if billing_provider == "stripe"
            else [None]
        )
        for cancellation_index, cancellation_event_type in enumerate(cancellation_event_types, start=1):
            cancellation_order = client.call(
                "/api/billing/orders",
                "POST",
                {"package_code": "starter"},
                extra_headers={"Idempotency-Key": f"stack-smoke-billing-cancel-v1-{cancellation_index}-{email}"},
            )
            if not cancellation_order.get("checkout_url"):
                raise RuntimeError("billing cancellation checkout URL contract failed")
            if billing_provider == "stripe":
                cancellation_payload = {
                    "id": f"evt_stack_smoke_cancel_{cancellation_index}_{email}",
                    "type": cancellation_event_type,
                    "data": {
                        "object": {
                            "id": f"cs_stack_smoke_cancel_{cancellation_index}_{email}",
                            "client_reference_id": cancellation_order["id"],
                            "metadata": {
                                "order_id": cancellation_order["id"],
                                "provider_order_id": cancellation_order.get("provider_order_id", ""),
                            },
                        }
                    },
                }
            else:
                cancellation_payload = {
                    "event_id": f"stack-smoke-billing-cancel-event-v1-{email}",
                    "order_id": cancellation_order["id"],
                    "status": "cancelled",
                }
            cancellation_timestamp = str(int(time.time()))
            if billing_provider == "stripe":
                cancellation_headers = {
                    "Stripe-Signature": stripe_signature(
                        stripe_webhook_secret, cancellation_timestamp, cancellation_payload
                    )
                }
            else:
                cancellation_headers = {
                    "X-Billing-Timestamp": cancellation_timestamp,
                    "X-Billing-Signature": billing_signature(
                        billing_secret, cancellation_timestamp, cancellation_payload
                    ),
                }
            cancellation_settled = client.call(
                "/api/billing/webhook",
                "POST",
                cancellation_payload,
                extra_headers=cancellation_headers,
            )
            cancellation_replay = client.call(
                "/api/billing/webhook",
                "POST",
                cancellation_payload,
                extra_headers=cancellation_headers,
            )
            if (
                cancellation_settled.get("status") != "cancelled"
                or cancellation_settled.get("duplicate")
                or not cancellation_replay.get("duplicate")
            ):
                raise RuntimeError("billing async cancellation/idempotent replay contract failed")
        after_cancellation = client.call("/api/credits")["balance"]
        if after_cancellation != after_billing:
            raise RuntimeError("billing cancellation must not issue credits")
        if billing_provider == "stripe":
            refund_payload = {
                "id": f"evt_stack_smoke_refund_{email}",
                "type": "refund.created",
                "data": {
                    "object": {
                        "id": f"re_stack_smoke_{email}",
                        "amount": order["amount_cents"],
                        "currency": order["currency"],
                        "payment_intent": f"pi_stack_smoke_{email}",
                        "metadata": {
                            "order_id": order["id"],
                            "provider_order_id": order.get("provider_order_id", ""),
                        },
                    }
                },
            }
        else:
            refund_payload = {
                "event_id": f"stack-smoke-billing-refund-event-v1-{email}",
                "order_id": order["id"],
                "status": "adjustment",
                "adjustment_id": f"refund_{email}",
                "adjustment_type": "refund",
                "amount_cents": order["amount_cents"],
                "currency": order["currency"],
            }
        refund_timestamp = str(int(time.time()))
        if billing_provider == "stripe":
            refund_headers = {"Stripe-Signature": stripe_signature(stripe_webhook_secret, refund_timestamp, refund_payload)}
        else:
            refund_headers = {
                "X-Billing-Timestamp": refund_timestamp,
                "X-Billing-Signature": billing_signature(billing_secret, refund_timestamp, refund_payload),
            }
        refund_settled = client.call("/api/billing/webhook", "POST", refund_payload, extra_headers=refund_headers)
        refund_replay = client.call("/api/billing/webhook", "POST", refund_payload, extra_headers=refund_headers)
        after_refund = client.call("/api/credits")["balance"]
        refund_delta = int(refund_settled.get("billing_adjustment", {}).get("credits_delta", 0))
        if (
            refund_settled.get("status") != "paid"
            or not refund_settled.get("billing_adjustment")
            or refund_delta != -order["credits"]
            or not refund_replay.get("duplicate")
            or after_refund != after_cancellation - order["credits"]
        ):
            raise RuntimeError("billing refund/idempotent credit reversal contract failed")

        chargeback_order = client.call(
            "/api/billing/orders",
            "POST",
            {"package_code": "starter"},
            extra_headers={"Idempotency-Key": f"stack-smoke-billing-chargeback-v1-{email}"},
        )
        if billing_provider == "stripe":
            chargeback_paid_payload = {
                "id": f"evt_stack_smoke_chargeback_paid_{email}",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": f"cs_stack_smoke_chargeback_{email}",
                        "client_reference_id": chargeback_order["id"],
                        "payment_intent": f"pi_stack_smoke_chargeback_{email}",
                        "payment_status": "paid",
                        "amount_total": chargeback_order["amount_cents"],
                        "currency": chargeback_order["currency"],
                        "metadata": {"order_id": chargeback_order["id"]},
                    }
                },
            }
        else:
            chargeback_paid_payload = {
                "event_id": f"stack-smoke-billing-chargeback-paid-v1-{email}",
                "order_id": chargeback_order["id"],
                "status": "paid",
                "amount_cents": chargeback_order["amount_cents"],
                "currency": chargeback_order["currency"],
            }
        chargeback_paid_timestamp = str(int(time.time()))
        if billing_provider == "stripe":
            chargeback_paid_headers = {
                "Stripe-Signature": stripe_signature(stripe_webhook_secret, chargeback_paid_timestamp, chargeback_paid_payload)
            }
        else:
            chargeback_paid_headers = {
                "X-Billing-Timestamp": chargeback_paid_timestamp,
                "X-Billing-Signature": billing_signature(billing_secret, chargeback_paid_timestamp, chargeback_paid_payload),
            }
        client.call("/api/billing/webhook", "POST", chargeback_paid_payload, extra_headers=chargeback_paid_headers)
        chargeback_paid_balance = client.call("/api/credits")["balance"]
        dispute_id = f"dp_stack_smoke_{email}"
        if billing_provider == "stripe":
            withdrawn_payload = {
                "id": f"evt_stack_smoke_withdrawn_{email}",
                "type": "charge.dispute.funds_withdrawn",
                "data": {
                    "object": {
                        "id": dispute_id,
                        "amount": chargeback_order["amount_cents"],
                        "currency": chargeback_order["currency"],
                        "payment_intent": f"pi_stack_smoke_chargeback_{email}",
                        "metadata": {"order_id": chargeback_order["id"]},
                    }
                },
            }
        else:
            withdrawn_payload = {
                "event_id": f"stack-smoke-billing-withdrawn-v1-{email}",
                "order_id": chargeback_order["id"],
                "status": "adjustment",
                "adjustment_id": dispute_id,
                "adjustment_type": "chargeback",
                "amount_cents": chargeback_order["amount_cents"],
                "currency": chargeback_order["currency"],
            }
        withdrawn_timestamp = str(int(time.time()))
        if billing_provider == "stripe":
            withdrawn_headers = {"Stripe-Signature": stripe_signature(stripe_webhook_secret, withdrawn_timestamp, withdrawn_payload)}
        else:
            withdrawn_headers = {
                "X-Billing-Timestamp": withdrawn_timestamp,
                "X-Billing-Signature": billing_signature(billing_secret, withdrawn_timestamp, withdrawn_payload),
            }
        withdrawn_settled = client.call("/api/billing/webhook", "POST", withdrawn_payload, extra_headers=withdrawn_headers)
        withdrawn_replay = client.call("/api/billing/webhook", "POST", withdrawn_payload, extra_headers=withdrawn_headers)
        after_withdrawn = client.call("/api/credits")["balance"]
        if (
            withdrawn_settled.get("billing_adjustment", {}).get("credits_delta") != -chargeback_order["credits"]
            or not withdrawn_replay.get("duplicate")
            or after_withdrawn != chargeback_paid_balance - chargeback_order["credits"]
        ):
            raise RuntimeError("billing chargeback withdrawal/idempotent credit reversal contract failed")
        if billing_provider == "stripe":
            reinstated_payload = {
                "id": f"evt_stack_smoke_reinstated_{email}",
                "type": "charge.dispute.funds_reinstated",
                "data": {
                    "object": {
                        "id": dispute_id,
                        "amount": chargeback_order["amount_cents"],
                        "currency": chargeback_order["currency"],
                        "payment_intent": f"pi_stack_smoke_chargeback_{email}",
                        "metadata": {"order_id": chargeback_order["id"]},
                    }
                },
            }
        else:
            reinstated_payload = {
                "event_id": f"stack-smoke-billing-reinstated-v1-{email}",
                "order_id": chargeback_order["id"],
                "status": "adjustment",
                "adjustment_id": dispute_id,
                "adjustment_type": "chargeback_reinstated",
                "amount_cents": chargeback_order["amount_cents"],
                "currency": chargeback_order["currency"],
            }
        reinstated_timestamp = str(int(time.time()))
        if billing_provider == "stripe":
            reinstated_headers = {"Stripe-Signature": stripe_signature(stripe_webhook_secret, reinstated_timestamp, reinstated_payload)}
        else:
            reinstated_headers = {
                "X-Billing-Timestamp": reinstated_timestamp,
                "X-Billing-Signature": billing_signature(billing_secret, reinstated_timestamp, reinstated_payload),
            }
        reinstated_settled = client.call("/api/billing/webhook", "POST", reinstated_payload, extra_headers=reinstated_headers)
        reinstated_replay = client.call("/api/billing/webhook", "POST", reinstated_payload, extra_headers=reinstated_headers)
        after_reinstated = client.call("/api/credits")["balance"]
        if (
            reinstated_settled.get("billing_adjustment", {}).get("credits_delta") != chargeback_order["credits"]
            or not reinstated_replay.get("duplicate")
            or after_reinstated != chargeback_paid_balance
        ):
            raise RuntimeError("billing chargeback reinstatement/idempotent credit restoration contract failed")
        billing_adjustment_contract = "completed"
        billing_contract = "completed"
        billing_cancellation_contract = "completed"
    project_key = f"stack-smoke-project-v1-{email}"
    project = client.call(
        "/api/projects",
        "POST",
        {"title": "全栈 smoke 项目", "story": "她推开门。雨声停了。"},
        extra_headers={"Idempotency-Key": project_key},
    )
    project_again = client.call(
        "/api/projects",
        "POST",
        {"title": "全栈 smoke 项目", "story": "她推开门。雨声停了。"},
        extra_headers={"Idempotency-Key": project_key},
    )
    if project_again.get("id") != project.get("id") or not project_again.get("duplicate"):
        raise RuntimeError("project creation idempotency contract failed")
    project_creation_idempotency = "completed"
    project_id = project["id"]
    if args.include_source_files:
        source_file_project = client.call(
            "/api/projects",
            "POST",
            {"title": "来源文件 smoke 项目", "story": "用于生产拓扑来源文件合同验收。"},
            extra_headers={"Idempotency-Key": f"stack-smoke-source-files-v1-{email}"},
        )
        source_file_project_id = source_file_project["id"]
        expected_documents: dict[str, str] = {}
        for filename, file_content_type, expected_media_type, fixture in smoke_source_file_fixtures():
            key = f"stack-smoke-source-file-v1-{filename}-{source_file_project_id}"
            imported = client.upload_multipart(
                f"/api/projects/{source_file_project_id}/source-file",
                filename,
                fixture,
                file_content_type,
                {"mode": "faithful", "copyrightAcknowledged": "true"},
                extra_headers={"Idempotency-Key": key},
            )
            source_document = (imported or {}).get("source_document") if isinstance(imported, dict) else None
            if (
                not isinstance(source_document, dict)
                or source_document.get("filename") != filename
                or source_document.get("media_type") != expected_media_type
                or int((imported or {}).get("segments") or 0) < 3
            ):
                raise RuntimeError("source file extraction contract failed")
            replay = client.upload_multipart(
                f"/api/projects/{source_file_project_id}/source-file",
                filename,
                fixture,
                file_content_type,
                {"mode": "faithful", "copyrightAcknowledged": "true"},
                extra_headers={"Idempotency-Key": key},
            )
            if not replay.get("duplicate"):
                raise RuntimeError("source file idempotency contract failed")
            expected_documents[filename] = expected_media_type
        source_file_detail = client.call(f"/api/projects/{source_file_project_id}")
        persisted_documents = {
            str(document.get("filename")): str(document.get("media_type"))
            for document in source_file_detail.get("source_documents", [])
        }
        if any(persisted_documents.get(filename) != media_type for filename, media_type in expected_documents.items()):
            raise RuntimeError("source file media type persistence contract failed")
        source_file_contract = "completed"
    second_email = f"stack-smoke-isolation-{time.time_ns()}@example.test"
    second_registered = anonymous.call("/api/auth/register", "POST", {"email": second_email, "password": args.password})
    second_client = SmokeClient(args.api_url, second_registered["token"])
    second_projects = second_client.call("/api/projects")
    if any(item.get("id") == project_id for item in second_projects):
        raise RuntimeError("cross-user project leaked into project list")
    second_client.expect_http_status(f"/api/projects/{project_id}", 404)
    ownership_contract = "completed"
    client.call(f"/api/projects/{project_id}/source", "POST", {"filename": "smoke.txt", "text": "她推开门。雨声停了。", "mode": "faithful", "copyrightAcknowledged": True})
    adaptation_units = client.call(f"/api/projects/{project_id}/adaptation-units")
    if not adaptation_units:
        raise RuntimeError("adaptation units are empty")
    rewrite_key = f"stack-smoke-rewrite-v1-{project_id}"
    rewrite_result = client.call(
        f"/api/projects/{project_id}/rewrite",
        "POST",
        {"mode": "originalized"},
        extra_headers={"Idempotency-Key": rewrite_key},
    )
    rewrite_job = (rewrite_result or {}).get("job") if isinstance(rewrite_result, dict) else None
    rewrite_job_id = rewrite_job.get("id") if rewrite_job else None
    if rewrite_job_id and rewrite_job.get("status") != "completed":
        wait_for_job(client, rewrite_job_id, args.generation_timeout)
    rewrite_again = client.call(
        f"/api/projects/{project_id}/rewrite",
        "POST",
        {"mode": "originalized"},
        extra_headers={"Idempotency-Key": rewrite_key},
    )
    rewrite_again_job = (rewrite_again or {}).get("job") if isinstance(rewrite_again, dict) else None
    if not rewrite_job_id or not rewrite_again_job or rewrite_again_job.get("id") != rewrite_job_id or rewrite_again_job.get("status") != "completed":
        raise RuntimeError("rewrite generation/idempotency contract failed")
    adaptation_rewrite_idempotency = "completed"
    draft_unit = client.call(
        f"/api/adaptation-units/{adaptation_units[0]['id']}",
        "PATCH",
        {"adapted_text": "她在烟雨中推开门。", "status": "draft"},
    )
    if draft_unit.get("status") != "draft" or draft_unit.get("adapted_text") != "她在烟雨中推开门。":
        raise RuntimeError("adaptation draft contract failed")
    adaptation_quality = (draft_unit.get("traceability") or {}).get("adaptation_quality") or {}
    if adaptation_quality.get("method") != "character-sequence-v1" or not adaptation_quality.get("requires_human_review"):
        raise RuntimeError("adaptation quality audit contract failed")
    adaptation_quality_contract = "completed"
    rejection_target = adaptation_units[1] if len(adaptation_units) > 1 else adaptation_units[0]
    rejected_unit = client.call(
        f"/api/adaptation-units/{rejection_target['id']}",
        "PATCH",
        {"adapted_text": rejection_target.get("adapted_text", ""), "status": "rejected"},
    )
    if rejected_unit.get("status") != "rejected":
        raise RuntimeError("single adaptation rejection contract failed")
    adaptation_rejection_contract = "single-rejected"
    review_ids = [draft_unit["id"]]
    if rejected_unit["id"] not in review_ids:
        review_ids.append(rejected_unit["id"])
    reviewed_units = client.call(
        f"/api/projects/{project_id}/adaptation-units/review",
        "POST",
        {"unit_ids": review_ids, "status": "approved"},
    )
    if reviewed_units.get("updated") != len(review_ids) or any(unit.get("status") != "approved" for unit in reviewed_units.get("units", [])):
        raise RuntimeError("bulk adaptation review contract failed")
    adaptation_review_contract = True
    adaptation_rejection_contract = "completed"
    structure_key = f"stack-smoke-structure-v1-{project_id}"
    structure_result = client.call(
        f"/api/projects/{project_id}/structure",
        "POST",
        extra_headers={"Idempotency-Key": structure_key},
    )
    structure_job = (structure_result or {}).get("job") if isinstance(structure_result, dict) else None
    structure_job_id = structure_job.get("id") if structure_job else None
    if structure_job_id and structure_job.get("status") != "completed":
        wait_for_job(client, structure_job_id, args.generation_timeout)
        structure_result = client.call(f"/api/projects/{project_id}")
    structure_again = client.call(
        f"/api/projects/{project_id}/structure",
        "POST",
        extra_headers={"Idempotency-Key": structure_key},
    )
    structure_again_job = (structure_again or {}).get("job") if isinstance(structure_again, dict) else None
    if not structure_job_id or not structure_again_job or structure_again_job.get("id") != structure_job_id or structure_again_job.get("status") != "completed":
        raise RuntimeError("project structure generation/idempotency contract failed")
    characters = (structure_result or {}).get("characters") if isinstance(structure_result, dict) else None
    episodes = (structure_result or {}).get("episodes") if isinstance(structure_result, dict) else None
    if not characters or not episodes or not all(episode.get("shots") for episode in episodes):
        raise RuntimeError("project structure is incomplete")
    legacy_structure_stage_status: dict[str, str] = {}
    legacy_stage_requests = (
        ("characters", f"/api/projects/{project_id}/characters?queued=true", f"stack-smoke-legacy-characters-v1-{project_id}"),
        ("outline", f"/api/projects/{project_id}/outline?queued=true", f"stack-smoke-legacy-outline-v1-{project_id}"),
        ("shots", f"/api/episodes/{episodes[0]['id']}/shots?queued=true", f"stack-smoke-legacy-shots-v1-{episodes[0]['id']}"),
    )
    for stage, path, stage_key in legacy_stage_requests:
        stage_result = client.call(path, "POST", extra_headers={"Idempotency-Key": stage_key})
        stage_job = (stage_result or {}).get("job") if isinstance(stage_result, dict) else None
        if not stage_job or stage_job.get("kind") != "structure":
            raise RuntimeError(f"legacy {stage} structure stage did not create a structure Job")
        payload_json = stage_job.get("payload_json") or {}
        if isinstance(payload_json, str):
            payload_json = json.loads(payload_json)
        if not isinstance(payload_json, dict) or payload_json.get("stage") != stage:
            raise RuntimeError(f"legacy {stage} structure stage payload contract failed")
        if stage_job.get("status") != "completed":
            wait_for_job(client, stage_job["id"], args.generation_timeout)
        replay = client.call(path, "POST", extra_headers={"Idempotency-Key": stage_key})
        replay_job = (replay or {}).get("job") if isinstance(replay, dict) else None
        if not replay_job or replay_job.get("id") != stage_job.get("id") or replay_job.get("status") != "completed":
            raise RuntimeError(f"legacy {stage} structure stage idempotency contract failed")
        legacy_structure_stage_status[stage] = replay_job.get("status", "unknown")
    story_bible_key = f"stack-smoke-story-bible-v1-{project_id}"
    story_bible_result = client.call(
        f"/api/projects/{project_id}/story-bible",
        "POST",
        extra_headers={"Idempotency-Key": story_bible_key},
    )
    story_bible_job = (story_bible_result or {}).get("job") if isinstance(story_bible_result, dict) else None
    story_bible_job_id = story_bible_job.get("id") if story_bible_job else None
    if story_bible_job_id and story_bible_job.get("status") != "completed":
        wait_for_job(client, story_bible_job_id, args.generation_timeout)
        story_bible_result = client.call(f"/api/projects/{project_id}/story-bible")
    story_bible = story_bible_result
    story_bible_again = client.call(
        f"/api/projects/{project_id}/story-bible",
        "POST",
        extra_headers={"Idempotency-Key": story_bible_key},
    )
    story_bible_again_job = (story_bible_again or {}).get("job") if isinstance(story_bible_again, dict) else None
    if not story_bible_job_id or not story_bible_again_job or story_bible_again_job.get("id") != story_bible_job_id or story_bible_again_job.get("status") != "completed":
        raise RuntimeError("story bible generation/idempotency contract failed")
    if not isinstance(story_bible, dict) or story_bible.get("project_id") != project_id or not isinstance(story_bible.get("entities"), list) or not isinstance(story_bible.get("relationships"), list):
        raise RuntimeError("story bible contract failed")
    story_bible_contract = "completed"
    edited_character = client.call(
        f"/api/characters/{characters[0]['id']}",
        "PATCH",
        {
            "name": "烟雨主角（smoke）",
            "description": "短发、深色风衣，镜头连续性验收角色",
            "visual_lock": {"prompt": "短发，深色风衣，银色怀表"},
        },
    )
    if edited_character.get("name") != "烟雨主角（smoke）" or edited_character.get("visual_lock", {}).get("prompt") != "短发，深色风衣，银色怀表":
        raise RuntimeError("character editor contract failed")
    edited_episode = client.call(
        f"/api/episodes/{episodes[0]['id']}",
        "PATCH",
        {"title": "雨夜来客（smoke）", "conflict": "门外的人知道她的秘密", "target_duration_seconds": 90},
    )
    if edited_episode.get("title") != "雨夜来客（smoke）" or edited_episode.get("target_duration_seconds") != 90:
        raise RuntimeError("episode editor contract failed")
    shots = client.call(f"/api/episodes/{episodes[0]['id']}/shots", "POST")
    if not shots:
        raise RuntimeError("shots are empty")
    editor_contract = True
    first_prompt = client.call(f"/api/shots/{shots[0]['id']}/image-prompts", "POST")
    edited_prompt = client.call(
        f"/api/image-prompts/{first_prompt['id']}",
        "PATCH",
        {"prompt": "电影感雨夜，角色握紧怀表。", "negative_prompt": "水印，畸形手指"},
    )
    if edited_prompt.get("prompt") != "电影感雨夜，角色握紧怀表。" or edited_prompt.get("negative_prompt") != "水印，畸形手指":
        raise RuntimeError("image prompt editor contract failed")
    invalidated_shot = client.call(
        f"/api/shots/{shots[0]['id']}",
        "PATCH",
        {"description": "她握紧怀表，缓慢推开门。", "duration_seconds": 4.5},
    )
    if invalidated_shot.get("image_prompt") is not None:
        raise RuntimeError("shot edit did not invalidate image prompt")
    for shot in shots:
        client.call(f"/api/shots/{shot['id']}/image-prompts", "POST")
    prompt_async_status = "skipped"
    prompt_async_key = f"stack-smoke-prompt-v1-{shots[0]['id']}"
    prompt_async = client.call(
        f"/api/shots/{shots[0]['id']}/image-prompts?queued=true",
        "POST",
        extra_headers={"Idempotency-Key": prompt_async_key},
    )
    prompt_async_job = (prompt_async or {}).get("job") if isinstance(prompt_async, dict) else None
    if not prompt_async_job or prompt_async_job.get("kind") != "prompt":
        raise RuntimeError("asynchronous image prompt job contract failed")
    if prompt_async_job.get("status") != "completed":
        wait_for_job(client, prompt_async_job["id"], args.generation_timeout)
    prompt_async_replay = client.call(
        f"/api/shots/{shots[0]['id']}/image-prompts?queued=true",
        "POST",
        extra_headers={"Idempotency-Key": prompt_async_key},
    )
    if (prompt_async_replay or {}).get("job", {}).get("id") != prompt_async_job.get("id"):
        raise RuntimeError("asynchronous image prompt idempotency contract failed")
    prompt_async_status = (prompt_async_replay or {}).get("job", {}).get("status", "unknown")

    audio_timeline = "skipped"
    if args.include_av:
        audio = client.call(
            f"/api/episodes/{episodes[0]['id']}/audio",
            "POST",
            {"filename": "stack-smoke.wav", "data_base64": smoke_wav_base64()},
        )
        if audio.get("kind") != "audio" or audio.get("status") != "ready":
            raise RuntimeError("audio import contract failed")
        settings = client.call(
            f"/api/episodes/{episodes[0]['id']}/composition-settings",
            "PATCH",
            {
                "audio_tracks": [{"asset_id": audio["id"], "start_seconds": 0, "volume": 0.8}],
                "subtitles": [{"start_seconds": 0.1, "end_seconds": 0.9, "text": "她推开门。"}],
            },
        )
        if len(settings.get("audio_tracks", [])) != 1 or len(settings.get("subtitles", [])) != 1:
            raise RuntimeError("audio/subtitle timeline contract failed")
        audio_timeline = "configured"

    generated = False
    character_reference = "skipped"
    character_reference_binding = "skipped"
    composition_status = "skipped"
    character_reference_batch = "skipped"
    image_batch_status = "skipped"
    visual_review_batch_status = "skipped"
    visual_review_async_status = "skipped"
    manual_review_status = "skipped"
    video_batch_status = "skipped"
    composition_batch_status = "skipped"
    if not args.skip_generation:
        reference_batch = client.call(
            f"/api/projects/{project_id}/character-references",
            "POST",
            {"character_ids": [character["id"] for character in characters]},
            extra_headers={"Idempotency-Key": f"stack-smoke-reference-batch-v1-{project_id}"},
        )
        if reference_batch.get("failed"):
            raise RuntimeError("character reference batch contract failed")
        character_reference_batch = reference_batch.get("status", "unknown")
        reference_item = next((item for item in reference_batch.get("items", []) if item.get("character_id") == characters[0]["id"]), None)
        reference = (reference_item or {}).get("reference") or {}
        if reference.get("job", {}).get("status") != "completed":
            wait_for_job(client, reference["job"]["id"], args.generation_timeout)
            reference = client.call(f"/api/characters/{characters[0]['id']}/reference-image", "POST", extra_headers={"Idempotency-Key": f"stack-smoke-reference-v1-{characters[0]['id']}"})
        if reference.get("status") != "ready" or not all(reference.get(key) for key in ("front_url", "side_url", "back_url")):
            raise RuntimeError("character reference generation contract failed")
        character_reference = "completed"
        image_batch_key = f"stack-smoke-image-batch-v1-{project_id}"
        image_batch = client.call(
            f"/api/projects/{project_id}/image-assets",
            "POST",
            {"shot_ids": [shot["id"] for shot in shots]},
            extra_headers={"Idempotency-Key": image_batch_key},
        )
        if image_batch.get("failed") or image_batch.get("submitted") != len(shots):
            raise RuntimeError("image generation batch contract failed")
        image_batch_status = image_batch.get("status", "unknown")
        batch_items = {item.get("shot_id"): item for item in image_batch.get("items", [])}
        images_for_video = []
        for index, shot in enumerate(shots, 1):
            image_key = f"{image_batch_key}:shot:{shot['id']}"
            first = client.call(f"/api/shots/{shot['id']}/image", "POST", extra_headers={"Idempotency-Key": image_key})
            if first.get("job", {}).get("status") != "completed":
                wait_for_job(client, first["job"]["id"], args.generation_timeout)
                first = client.call(f"/api/assets/{first['id']}")
            second = client.call(f"/api/shots/{shot['id']}/image", "POST", extra_headers={"Idempotency-Key": image_key})
            batch_asset = (batch_items.get(shot["id"]) or {}).get("asset") or {}
            if first["id"] != second["id"] or first["id"] != batch_asset.get("id") or first.get("status") != "ready":
                raise RuntimeError("image generation/idempotency contract failed")
            bound = client.call(f"/api/assets/{first['id']}/character-reference", "POST", {"character_id": characters[0]["id"]})
            if bound.get("character_id") != characters[0]["id"] or bound.get("metadata", {}).get("character_reference_id") != reference["id"]:
                raise RuntimeError("character reference binding contract failed")
            character_reference_binding = "completed"
            client.call(f"/api/assets/{first['id']}", "PATCH", {"selected": True, "consistency_confirmed": True})
            images_for_video.append(first)
        second_client.expect_http_status(f"/api/assets/{images_for_video[0]['id']}", 404)
        asset_ownership_contract = "completed"
        visual_review_batch = client.call(
            f"/api/projects/{project_id}/asset-reviews",
            "POST",
            {"asset_ids": [image["id"] for image in images_for_video], "audit_type": "scene"},
        )
        if visual_review_batch.get("failed") or visual_review_batch.get("reviewed") != len(images_for_video):
            raise RuntimeError("visual review batch contract failed")
        visual_review_batch_status = visual_review_batch.get("status", "unknown")
        async_review_key = f"stack-smoke-vision-review-v1-{images_for_video[0]['id']}"
        async_review = client.call(
            f"/api/assets/{images_for_video[0]['id']}/review?queued=true",
            "POST",
            {"audit_type": "scene", "prompt": "检查异步视觉审核合同"},
            extra_headers={"Idempotency-Key": async_review_key},
        )
        async_review_job = async_review.get("job") or {}
        if async_review_job.get("kind") != "vision-review":
            raise RuntimeError("asynchronous visual review job contract failed")
        if async_review_job.get("status") != "completed":
            wait_for_job(client, async_review_job["id"], args.generation_timeout)
        replayed_review = client.call(
            f"/api/assets/{images_for_video[0]['id']}/review?queued=true",
            "POST",
            {"audit_type": "scene", "prompt": "检查异步视觉审核合同"},
            extra_headers={"Idempotency-Key": async_review_key},
        )
        if replayed_review.get("job", {}).get("id") != async_review_job.get("id"):
            raise RuntimeError("asynchronous visual review idempotency contract failed")
        visual_review_async_status = replayed_review.get("job", {}).get("status", "unknown")
        manual_review_ids = []
        for image in images_for_video:
            manual_key = f"stack-smoke-manual-review-v1-{image['id']}"
            decision = client.call(
                f"/api/assets/{image['id']}/review/decision",
                "POST",
                {"status": "PASS", "issues": []},
                extra_headers={"Idempotency-Key": manual_key},
            )
            latest = (decision.get("reviews") or [{}])[0]
            if latest.get("status") != "PASS" or latest.get("provider") != "manual":
                raise RuntimeError("manual visual review decision contract failed")
            replayed_decision = client.call(
                f"/api/assets/{image['id']}/review/decision",
                "POST",
                {"status": "PASS", "issues": []},
                extra_headers={"Idempotency-Key": manual_key},
            )
            replayed_latest = (replayed_decision.get("reviews") or [{}])[0]
            if replayed_latest.get("id") != latest.get("id"):
                raise RuntimeError("manual visual review idempotency contract failed")
            manual_review_ids.append(latest.get("id"))
        manual_review_status = "completed"
        video_batch_key = f"stack-smoke-video-batch-v1-{project_id}"
        video_batch = client.call(
            f"/api/projects/{project_id}/video-assets",
            "POST",
            {"asset_ids": [image["id"] for image in images_for_video]},
            extra_headers={"Idempotency-Key": video_batch_key},
        )
        if video_batch.get("failed") or video_batch.get("submitted") != len(images_for_video):
            raise RuntimeError("video generation batch contract failed")
        video_batch_status = video_batch.get("status", "unknown")
        video_items = {item.get("image_asset_id"): item for item in video_batch.get("items", [])}
        for image in images_for_video:
            video_key = f"{video_batch_key}:asset:{image['id']}"
            video = client.call(f"/api/assets/{image['id']}/video", "POST", extra_headers={"Idempotency-Key": video_key})
            if video.get("job", {}).get("status") != "completed":
                wait_for_job(client, video["job"]["id"], args.generation_timeout)
                video = client.call(f"/api/assets/{video['id']}")
            batch_asset = (video_items.get(image["id"]) or {}).get("asset") or {}
            if video.get("status") != "ready" or video.get("id") != batch_asset.get("id"):
                raise RuntimeError("video generation contract failed")
        composition_batch = client.call(
            f"/api/projects/{project_id}/compositions",
            "POST",
            {"episode_ids": [episodes[0]["id"]]},
            extra_headers={"Idempotency-Key": f"stack-smoke-composition-batch-v1-{project_id}"},
        )
        if composition_batch.get("failed") or composition_batch.get("submitted") != 1:
            raise RuntimeError("composition generation batch contract failed")
        composition_batch_status = composition_batch.get("status", "unknown")
        composition = (composition_batch.get("items") or [{}])[0].get("composition") or {}
        composition_job = composition.get("job")
        if composition_job and composition_job.get("status") != "completed":
            wait_for_job(client, composition_job["id"], args.generation_timeout)
            composition = client.call(f"/api/episodes/{episodes[0]['id']}")
            composition = (composition.get("compositions") or [{}])[0]
        if args.include_av:
            metadata = json.loads(composition.get("metadata_json") or "{}")
            if composition.get("status") != "completed" or not composition.get("final_video_url") or metadata.get("mode") != "remotion" or metadata.get("audio_tracks") != 1 or metadata.get("subtitles") != 1:
                raise RuntimeError("Remotion A/V composition contract failed")
            audio_timeline = "completed"
        composition_status = composition.get("status", "unknown")
        generated = True

    export = client.call(f"/api/projects/{project_id}/export")
    archive_bytes = client.download(f"/api/projects/{project_id}/archive", "application/zip")
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            archive_names = set(archive.namelist())
    except zipfile.BadZipFile as exc:
        raise RuntimeError("project archive is not a valid ZIP") from exc
    if "project-export.json" not in archive_names or "manifest.json" not in archive_names:
        raise RuntimeError("project archive is missing exchange files")
    project_archive = "completed"
    archive_imported = client.upload_bytes("/api/projects/import-archive", archive_bytes, "application/zip")
    archive_import = archive_imported.get("archive_import") if isinstance(archive_imported, dict) else None
    if not isinstance(archive_import, dict) or archive_import.get("status") not in {"completed", "partial"}:
        raise RuntimeError("project archive import contract failed")
    imported_project = client.call("/api/projects/import", "POST", export)
    if imported_project.get("id") == project_id or imported_project.get("adaptation_unit_count") != len(export.get("adaptation_units", [])):
        raise RuntimeError("project export/import contract failed")
    if args.include_av:
        imported_episode = (imported_project.get("episodes") or [{}])[0]
        if len(imported_project.get("audio_assets") or []) != 1 or len((imported_episode.get("composition_settings") or {}).get("audio_tracks", [])) != 1:
            raise RuntimeError("audio project exchange contract failed")
    graph = client.call(f"/api/projects/{project_id}/graph")
    jobs = client.call("/api/jobs")
    settings = client.call("/api/studio-settings")
    web_status = get_url(args.web_url)
    orchestrator_status = None
    orchestrator_redis = None
    if args.orchestrator_url:
        orchestrator_payload = get_json_url(args.orchestrator_url.rstrip("/") + "/health")
        orchestrator_status = 200
        orchestrator_redis = orchestrator_payload.get("redis")
        if args.require_orchestrator and orchestrator_payload.get("status") != "ok":
            raise RuntimeError("orchestrator health is not ok")
    elif args.require_orchestrator:
        raise RuntimeError("--require-orchestrator needs --orchestrator-url")
    client.call("/api/auth/logout", "POST")
    second_client.call("/api/auth/logout", "POST")

    print(json.dumps({
        "status": "passed",
        "api_health": health.get("status"),
        "api_ready": ready.get("status"),
        "web_status": web_status,
        "orchestrator_status": orchestrator_status,
        "orchestrator_redis": orchestrator_redis,
        "storage": health.get("storage"),
        "source_and_outline": True,
        "source_file_contract": source_file_contract,
        "project_creation_idempotency": project_creation_idempotency,
        "ownership_contract": ownership_contract,
        "asset_ownership_contract": asset_ownership_contract,
        "adaptation_rewrite_idempotency": adaptation_rewrite_idempotency,
        "adaptation_review_contract": adaptation_review_contract,
        "adaptation_rejection_contract": adaptation_rejection_contract,
        "adaptation_quality_contract": adaptation_quality_contract,
        "story_bible_contract": story_bible_contract,
        "legacy_structure_stage_status": legacy_structure_stage_status,
        "editor_contract": editor_contract,
        "prompt_async_status": prompt_async_status,
        "graph_nodes": len(graph.get("nodes", [])),
        "export_schema": export.get("schema"),
        "project_archive": project_archive,
        "archive_import": archive_import,
        "project_import": True,
        "jobs": len(jobs),
        "settings_version": settings.get("settingsVersion"),
        "billing_contract": billing_contract,
        "billing_cancellation_contract": billing_cancellation_contract,
        "billing_adjustment_contract": billing_adjustment_contract,
        "generation": generated,
        "character_reference": character_reference,
        "character_reference_batch": character_reference_batch,
        "image_batch_status": image_batch_status,
        "visual_review_batch_status": visual_review_batch_status,
        "visual_review_async_status": visual_review_async_status,
        "manual_review_status": manual_review_status,
        "video_batch_status": video_batch_status,
        "composition_batch_status": composition_batch_status,
        "character_reference_binding": character_reference_binding,
        "composition_status": composition_status,
        "audio_timeline": audio_timeline,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
