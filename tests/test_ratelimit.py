import asyncio
import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI

from studio_api.observability import configure_otel, parse_otlp_headers
from studio_api.ratelimit import RateLimiter


class RateLimiterTests(unittest.TestCase):
    def test_local_window_enforces_limit_and_expires(self):
        limiter = RateLimiter(limit=2, window_seconds=60)
        self.assertEqual(limiter.backend, "local")
        self.assertTrue(asyncio.run(limiter.allow("client-a")))
        self.assertTrue(asyncio.run(limiter.allow("client-a")))
        self.assertFalse(asyncio.run(limiter.allow("client-a")))
        self.assertTrue(asyncio.run(limiter.allow("client-b")))

    def test_disabled_limiter_allows_without_state(self):
        limiter = RateLimiter(limit=0, redis_url="redis://127.0.0.1:6399")
        self.assertEqual(limiter.backend, "disabled")
        self.assertTrue(asyncio.run(limiter.allow("client-a")))

    def test_otel_is_disabled_by_default_without_exporter_configuration(self):
        with patch.dict(os.environ, {"STUDIO_OTEL_ENABLED": "false"}, clear=False):
            status, shutdown = configure_otel(FastAPI())
        self.assertEqual(status, {"enabled": False, "backend": "disabled"})
        shutdown()

    def test_otel_headers_parse_encoded_values_without_network(self):
        parsed = parse_otlp_headers("Authorization=Bearer%20collector-token, x-tenant=studio, malformed")
        self.assertEqual(parsed, {"Authorization": "Bearer collector-token", "x-tenant": "studio"})
        self.assertEqual(parse_otlp_headers(None), {})

    def test_otel_headers_are_forwarded_to_exporter_without_network(self):
        try:
            import opentelemetry  # noqa: F401
        except ImportError:
            self.skipTest("optional observability dependencies are not installed")
        exporter = MagicMock()
        provider = MagicMock()
        with patch.dict(
            os.environ,
            {
                "STUDIO_OTEL_ENABLED": "true",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.invalid/v1/traces",
                "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer%20opaque-token,x-tenant=studio",
            },
            clear=False,
        ), patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
            return_value=exporter,
        ) as exporter_constructor, patch(
            "opentelemetry.sdk.trace.TracerProvider", return_value=provider
        ), patch("opentelemetry.sdk.trace.export.BatchSpanProcessor"), patch(
            "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"
        ):
            status, shutdown = configure_otel(FastAPI())
            self.assertTrue(status["enabled"])
            self.assertEqual(
                exporter_constructor.call_args.kwargs["headers"],
                {"Authorization": "Bearer opaque-token", "x-tenant": "studio"},
            )
            shutdown()

    def test_redis_window_uses_atomic_script_contract(self):
        class FakeRedis:
            calls = 0

            @classmethod
            def from_url(cls, *_args, **_kwargs):
                return cls()

            async def eval(self, script, key_count, key, window_seconds):
                self.calls += 1
                self.script = script
                self.key_count = key_count
                self.key = key
                self.window_seconds = window_seconds
                return self.calls

            async def close(self):
                return None

        with patch("studio_api.ratelimit.Redis", FakeRedis):
            limiter = RateLimiter(limit=1, redis_url="redis://test")
            self.assertEqual(limiter.backend, "redis-with-local-fallback")
            self.assertTrue(asyncio.run(limiter.allow("client-a")))
            self.assertFalse(asyncio.run(limiter.allow("client-a")))
            self.assertIn("INCR", limiter._redis.script)
            self.assertEqual(limiter._redis.key_count, 1)
            self.assertEqual(limiter._redis.window_seconds, 60)
            asyncio.run(limiter.close())


if __name__ == "__main__":
    unittest.main()
