"""可选 OpenTelemetry FastAPI instrumentation。

默认关闭，避免本地预览引入 exporter 依赖或外发数据。启用时只发送
OpenTelemetry span，业务正文、Authorization 和 provider secret 不由本模块记录。
"""

from __future__ import annotations

import logging
import os
from urllib.parse import unquote
from typing import Any, Callable


logger = logging.getLogger("studio_api.observability")


def parse_otlp_headers(raw: str | None) -> dict[str, str]:
    """Parse the OTEL comma-separated header contract without logging values."""

    headers: dict[str, str] = {}
    for item in str(raw or "").split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        if not name or any(character.isspace() for character in name):
            continue
        headers[name] = unquote(value.strip())
    return headers


def configure_otel(app: Any) -> tuple[dict[str, Any], Callable[[], None]]:
    enabled = os.environ.get("STUDIO_OTEL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"enabled": False, "backend": "disabled"}, lambda: None

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logger.error("STUDIO_OTEL_ENABLED is true but OTEL_EXPORTER_OTLP_ENDPOINT is not configured")
        return {"enabled": False, "backend": "misconfigured"}, lambda: None

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        service_name = os.environ.get("OTEL_SERVICE_NAME", "ai-manhua-studio-api")
        exporter_headers = parse_otlp_headers(os.environ.get("OTEL_EXPORTER_OTLP_HEADERS"))
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=endpoint, headers=exporter_headers or None)
            )
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
    except ImportError:
        logger.error("OpenTelemetry enabled but optional observability dependencies are unavailable")
        return {"enabled": False, "backend": "dependencies-missing"}, lambda: None
    except Exception as exc:  # exporter setup must not expose configuration details to clients
        logger.exception("OpenTelemetry setup failed error_type=%s", type(exc).__name__)
        return {"enabled": False, "backend": "setup-failed"}, lambda: None

    def shutdown() -> None:
        try:
            provider.shutdown()
        except Exception as exc:
            logger.warning("OpenTelemetry shutdown failed error_type=%s", type(exc).__name__)

    return {"enabled": True, "backend": "otlp-http", "endpoint_configured": True}, shutdown
