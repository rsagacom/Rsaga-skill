import unittest
import asyncio
import base64
import io
import json
import hashlib
import hmac
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from studio_api import main
from studio_api.main import generation_runs_now
from studio_api.service import ServiceError


def _tiny_pdf(text: str) -> bytes:
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
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


def _binary_multipart_body(boundary: str, filename: str, payload: bytes) -> bytes:
    encoded = base64.b64encode(payload).decode("ascii")
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n"
        "Content-Transfer-Encoding: base64\r\n\r\n"
        f"{encoded}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="mode"\r\n\r\n'
        "originalized\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="copyrightAcknowledged"\r\n\r\n'
        "true\r\n"
        f"--{boundary}--\r\n"
    ).encode("ascii")


class MainRuntimeContractTests(unittest.TestCase):
    def test_auth_sessions_route_forwards_current_user_and_token(self):
        request = Request({"type": "http", "method": "GET", "path": "/api/auth/sessions", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main, "request_auth_token", return_value="token-1"):
                with patch.object(main.service, "list_sessions", return_value=[{"id": "session-1"}]) as list_sessions:
                    result = main.auth_sessions(request)
        self.assertEqual(result, [{"id": "session-1"}])
        list_sessions.assert_called_once_with("user-1", "token-1")

    def test_auth_revoke_session_route_forwards_csrf_protected_current_context(self):
        request = Request({"type": "http", "method": "DELETE", "path": "/api/auth/sessions/session-1", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main, "request_auth_token", return_value="token-1"):
                with patch.object(main.service, "revoke_session", return_value={"ok": True, "id": "session-1"}) as revoke:
                    result = main.auth_revoke_session("session-1", request)
        self.assertEqual(result, {"ok": True, "id": "session-1"})
        revoke.assert_called_once_with("session-1", "user-1", "token-1")

    def test_billing_orders_route_maps_checkout_configuration_errors_to_http(self):
        request = Request({"type": "http", "method": "GET", "path": "/api/billing/orders", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "billing_orders", side_effect=ServiceError("billing checkout URL is invalid", 503)):
                with self.assertRaises(HTTPException) as failure:
                    main.billing_orders(request, 50)
        self.assertEqual(failure.exception.status_code, 503)
        self.assertEqual(failure.exception.detail, "billing checkout URL is invalid")

    def test_provider_settings_route_maps_service_validation_errors_to_http(self):
        request = Request({"type": "http", "method": "PATCH", "path": "/api/studio-settings", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "update_settings", side_effect=ServiceError("unsupported image provider", 422)):
                with self.assertRaises(HTTPException) as failure:
                    main.patch_settings(request, {"image": {"provider": "unsupported"}})
        self.assertEqual(failure.exception.status_code, 422)
        self.assertEqual(failure.exception.detail, "unsupported image provider")

    def test_chunked_request_body_reader_enforces_limit_before_joining(self):
        class FakeRequest:
            def __init__(self, chunks):
                self.chunks = chunks

            async def stream(self):
                for chunk in self.chunks:
                    yield chunk

        self.assertEqual(
            asyncio.run(main.read_request_body_limited(FakeRequest([b"one", b"two"]), 6)),
            b"onetwo",
        )
        with self.assertRaises(HTTPException) as failure:
            asyncio.run(main.read_request_body_limited(FakeRequest([b"one", b"two"]), 5))
        self.assertEqual(failure.exception.status_code, 413)

    def test_production_readiness_rejects_direct_start_with_invalid_configuration(self):
        with patch.object(main, "STUDIO_ENV", "production"):
            with patch.object(
                main,
                "check_production_config",
                return_value={"ok": False, "required": True, "errors": ["STUDIO_STORE:must-be-postgres"]},
            ) as check:
                with self.assertRaises(HTTPException) as failure:
                    main.ready()
        self.assertEqual(failure.exception.status_code, 503)
        check.assert_called_once()
        self.assertNotIn("must-be-postgres", str(failure.exception))

    def test_billing_webhook_signature_is_short_lived_and_secret_is_not_returned(self):
        body = b'{"event_id":"evt-1","order_id":"order-1","status":"paid"}'
        timestamp = str(int(main.time.time()))
        secret = "test-billing-secret-with-at-least-32-chars"
        signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
        with patch.dict("os.environ", {"STUDIO_BILLING_WEBHOOK_SECRET": secret}, clear=False):
            main.verify_billing_signature(body, timestamp, f"sha256={signature}")
            with self.assertRaises(HTTPException) as failure:
                main.verify_billing_signature(body, timestamp, "sha256=bad")
        self.assertEqual(failure.exception.status_code, 403)
        self.assertNotIn(secret, str(failure.exception))

    def test_stripe_billing_webhook_uses_raw_body_adapter_and_provider_neutral_payload(self):
        class FakeRequest:
            async def stream(self):
                yield b'{"id":"evt_stripe","type":"checkout.session.completed"}'

        payload = {
            "event_id": "evt_stripe",
            "order_id": "order_1",
            "status": "paid",
            "amount_cents": 9900,
            "currency": "cny",
        }
        with patch.object(main.service, "_billing_provider", return_value="stripe"):
            with patch.object(main.StripeCheckoutAdapter, "from_env") as adapter_factory:
                adapter_factory.return_value.verify_webhook.return_value = {"id": "evt_stripe"}
                with patch.object(main.StripeCheckoutAdapter, "normalize_event", return_value=payload):
                    with patch.object(main.service, "settle_billing_webhook", return_value={"status": "paid"}) as settle:
                        result = asyncio.run(main.billing_webhook(FakeRequest(), stripe_signature="t=1,v1=test"))
        self.assertEqual(result["status"], "paid")
        settle.assert_called_once_with(payload, "stripe")

    def test_asset_route_requires_current_user_and_returns_file_response(self):
        request = Request({"type": "http", "method": "GET", "path": "/assets/asset.svg", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset.svg"
            path.write_text("<svg></svg>", encoding="utf-8")
            with patch.object(main, "current_user", return_value="user-1"):
                with patch.object(main.service, "local_asset_file", return_value=path) as resolve:
                    response = main.serve_asset("asset.svg", request)
            self.assertEqual(Path(response.path), path)
            self.assertEqual(resolve.call_args.args, ("asset.svg", "user-1"))

    def test_source_import_route_forwards_user_scoped_idempotency_key(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects/project-1/source", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.SourceImport(filename="novel.txt", text="她推开门。", copyrightAcknowledged=True)
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "import_source", return_value={"duplicate": False}) as import_call:
                result = main.import_source("project-1", payload, request, "source-key-1")
        self.assertFalse(result["duplicate"])
        self.assertEqual(import_call.call_args.args[-1], "source-key-1")

    def test_source_file_route_extracts_multipart_text_and_preserves_media_type(self):
        boundary = "studio-source-file-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="chapter.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
            "雨夜里，林默推开门。\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="mode"\r\n\r\n'
            "originalized\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="copyrightAcknowledged"\r\n\r\n'
            "true\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")

        class FakeMultipartRequest:
            headers = {"content-type": f"multipart/form-data; boundary={boundary}"}

            async def stream(self):
                yield body

        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "import_source", return_value={"duplicate": False}) as import_call:
                result = asyncio.run(main.import_source_file("project-1", FakeMultipartRequest(), "source-file-key-1"))

        self.assertFalse(result["duplicate"])
        self.assertEqual(
            import_call.call_args.args,
            (
                "project-1",
                "chapter.txt",
                "雨夜里，林默推开门。",
                "originalized",
                True,
                "user-1",
                "text/plain",
                "source-file-key-1",
            ),
        )

    def test_source_file_route_extracts_binary_formats_and_forwards_contract(self):
        docx_xml = (
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>DOCX 正文</w:t></w:r></w:p></w:body></w:document>"
        ).encode()
        docx = io.BytesIO()
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml", docx_xml)

        epub = io.BytesIO()
        with zipfile.ZipFile(epub, "w") as archive:
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
            archive.writestr("OEBPS/chapter.xhtml", "<html><body><p>EPUB 正文</p></body></html>")

        cases = (
            (
                "story.docx",
                docx.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "DOCX 正文",
            ),
            ("story.epub", epub.getvalue(), "application/epub+zip", "EPUB 正文"),
            ("story.pdf", _tiny_pdf("PDF text"), "application/pdf", "PDF text"),
        )
        for filename, payload, media_type, expected_text in cases:
            boundary = f"studio-binary-{filename.split('.', 1)[1]}"
            body = _binary_multipart_body(boundary, filename, payload)

            class FakeMultipartRequest:
                headers = {"content-type": f"multipart/form-data; boundary={boundary}"}

                async def stream(self):
                    yield body

            with self.subTest(filename=filename):
                with patch.object(main, "current_user", return_value="user-1"):
                    with patch.object(main.service, "import_source", return_value={"duplicate": False}) as import_call:
                        result = asyncio.run(main.import_source_file("project-1", FakeMultipartRequest(), f"binary-{filename}"))

                self.assertFalse(result["duplicate"])
                self.assertEqual(import_call.call_args.args[0], "project-1")
                self.assertEqual(import_call.call_args.args[1], filename)
                self.assertIn(expected_text, import_call.call_args.args[2])
                self.assertEqual(import_call.call_args.args[3:], ("originalized", True, "user-1", media_type, f"binary-{filename}"))

    def test_project_creation_route_forwards_idempotency_key(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.ProjectCreate(title="幂等项目", story="故事梗概")
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "create_project", return_value={"id": "project-1"}) as create:
                result = main.create_project(payload, request, "project-key-1")
        self.assertEqual(result["id"], "project-1")
        self.assertEqual(create.call_args.args[-1], "project-key-1")

    def test_project_readiness_route_is_user_scoped(self):
        request = Request({"type": "http", "method": "GET", "path": "/api/projects/project-1/readiness", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "project_readiness", return_value={"project_id": "project-1", "ready_for_video": False}) as readiness:
                result = main.project_readiness("project-1", request)
        self.assertFalse(result["ready_for_video"])
        readiness.assert_called_once_with("project-1", "user-1")

    def test_bullmq_defaults_to_async_generation_but_local_defaults_to_sync(self):
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "local"}, clear=False):
            self.assertTrue(generation_runs_now(None))
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            self.assertFalse(generation_runs_now(None))

    def test_explicit_queue_query_overrides_backend_default(self):
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            self.assertTrue(generation_runs_now(False))
            self.assertFalse(generation_runs_now(True))

    def test_bullmq_route_defaults_to_queued_image_generation(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/shots/shot-1/image", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            with patch.object(main, "current_user", return_value="local-user"):
                with patch.object(main.service, "create_image_asset", return_value={"status": "pending"}) as create:
                    main.shot_image("shot-1", request, None, "smoke-key")
        self.assertFalse(create.call_args.args[2])

    def test_bullmq_story_bible_route_forwards_queue_default_and_idempotency(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects/project-1/story-bible", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            with patch.object(main, "current_user", return_value="local-user"):
                with patch.object(main.service, "request_story_bible", return_value={"project_id": "project-1", "job": {"status": "queued"}}) as request_story:
                    main.generate_story_bible("project-1", request, None, "story-bible-key")
        self.assertFalse(request_story.call_args.args[2])
        self.assertEqual(request_story.call_args.args[3], "story-bible-key")

    def test_bullmq_structure_route_forwards_queue_default_and_idempotency(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects/project-1/structure", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            with patch.object(main, "current_user", return_value="local-user"):
                with patch.object(main.service, "request_project_structure", return_value={"id": "project-1", "job": {"status": "queued"}}) as request_structure:
                    main.project_structure("project-1", request, None, "structure-key")
        self.assertFalse(request_structure.call_args.args[2])
        self.assertEqual(request_structure.call_args.args[3], "structure-key")

    def test_bullmq_legacy_structure_routes_forward_stage_jobs(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects/project-1/characters", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            with patch.object(main, "current_user", return_value="user-1"):
                with patch.object(main.service, "request_structure_stage", return_value={"job": {"status": "queued"}}) as request_stage:
                    main.characters("project-1", request, None, None, "characters-key")
        self.assertEqual(request_stage.call_args.args, ("characters", "project-1", "user-1", False, "characters-key"))

        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "request_structure_stage", return_value={"job": {"status": "queued"}}) as request_stage:
                main.outline("project-1", request, True, "outline-key")
        self.assertEqual(request_stage.call_args.args, ("outline", "project-1", "user-1", False, "outline-key"))

        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "request_structure_stage", return_value={"job": {"status": "queued"}}) as request_stage:
                main.shots("episode-1", request, True, "shots-key")
        self.assertEqual(request_stage.call_args.args, ("shots", "episode-1", "user-1", False, "shots-key"))

    def test_bullmq_asset_retry_route_keeps_queue_default_and_idempotency(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/assets/asset-1/image", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            with patch.object(main, "current_user", return_value="local-user"):
                with patch.object(main.service, "asset_with_job", return_value={"kind": "image", "status": "pending", "shot_id": "shot-1"}):
                    with patch.object(main.service, "create_image_asset", return_value={"status": "pending"}) as create:
                        main.asset_image("asset-1", request, None, "retry-key")
        self.assertFalse(create.call_args.args[2])
        self.assertEqual(create.call_args.args[3], "retry-key")

    def test_bullmq_readiness_requires_healthy_orchestrator(self):
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq", "STUDIO_ORCHESTRATOR_URL": "http://queue.test"}, clear=False):
            with patch("studio_api.main.urllib.request.urlopen", side_effect=OSError("unavailable")):
                with self.assertRaises(HTTPException) as failure:
                    main.ready()
                self.assertEqual(failure.exception.status_code, 503)

            response = MagicMock()
            response.status = 200
            response.__enter__.return_value = response
            response.read.return_value = json.dumps({"status": "ok", "redis": "ok"}).encode()
            with patch("studio_api.main.urllib.request.urlopen", return_value=response):
                ready = main.ready()
                self.assertEqual(ready["status"], "ready")
                self.assertEqual(ready["queue_backend"], "bullmq")

            response.read.return_value = b"x" * (main.ORCHESTRATOR_HEALTH_RESPONSE_MAX_BYTES + 1)
            with patch("studio_api.main.urllib.request.urlopen", return_value=response):
                with self.assertRaises(HTTPException) as failure:
                    main.ready()
                self.assertEqual(failure.exception.status_code, 503)

    def test_readiness_requires_asset_storage(self):
        with patch.object(main.service.storage, "ready", return_value=False):
            with self.assertRaises(HTTPException) as failure:
                main.ready()
            self.assertEqual(failure.exception.status_code, 503)

    def test_readiness_requires_configured_composition_engine(self):
        with patch.dict("os.environ", {"STUDIO_COMPOSE_ENGINE": "unsupported"}, clear=False):
            with self.assertRaises(HTTPException) as failure:
                main.ready()
        self.assertEqual(failure.exception.status_code, 503)

    def test_health_reports_composition_engine(self):
        with patch.dict("os.environ", {"STUDIO_COMPOSE_ENGINE": "playlist"}, clear=False):
            payload = main.health()
        self.assertEqual(payload["composition"], {"engine": "playlist", "ready": True, "reason": "playlist-only"})

    def test_health_exposes_effective_non_secret_speech_voice_allowlist(self):
        with patch.dict("os.environ", {"STUDIO_SPEECH_ALLOWED_VOICES": "marin,cedar"}, clear=False):
            payload = main.health()
        self.assertEqual(payload["speech_voices"], ["marin", "cedar"])

    def test_episode_timeline_routes_preserve_audio_and_subtitle_contract(self):
        request = Request({"type": "http", "method": "PATCH", "path": "/api/episodes/episode-1/composition-settings", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.CompositionSettingsInput(audio_tracks=[{"asset_id": "asset-1", "start_seconds": 0, "volume": 1}], subtitles=[{"start_seconds": 0, "end_seconds": 2, "text": "字幕"}], narration_text="保存的旁白稿")
        with patch.object(main, "current_user", return_value="local-user"):
            with patch.object(main.service, "update_composition_settings", return_value={"episode_id": "episode-1"}) as update:
                result = main.patch_composition_settings("episode-1", payload, request)
        self.assertEqual(result["episode_id"], "episode-1")
        self.assertEqual(update.call_args.args[1]["audio_tracks"][0]["asset_id"], "asset-1")
        self.assertEqual(update.call_args.args[1]["narration_text"], "保存的旁白稿")

    def test_editor_patch_routes_forward_only_declared_fields(self):
        request = Request({"type": "http", "method": "PATCH", "path": "/api/characters/character-1", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.CharacterPatch(name="林默", description="深色风衣")
        with patch.object(main, "current_user", return_value="local-user"):
            with patch.object(main.service, "update_character", return_value={"id": "character-1"}) as update:
                result = main.patch_character("character-1", payload, request)
        self.assertEqual(result["id"], "character-1")
        self.assertEqual(update.call_args.args[1], {"name": "林默", "description": "深色风衣"})

        episode_payload = main.EpisodePatch(title="雨夜来客", target_duration_seconds=90)
        with patch.object(main.service, "update_episode", return_value={"id": "episode-1"}) as update:
            result = main.patch_episode("episode-1", episode_payload, request)
        self.assertEqual(result["id"], "episode-1")
        self.assertEqual(update.call_args.args[1]["target_duration_seconds"], 90)

        shot_payload = main.ShotPatch(description="她推开门。", duration_seconds=4)
        with patch.object(main.service, "update_shot", return_value={"id": "shot-1"}) as update:
            result = main.patch_shot("shot-1", shot_payload, request)
        self.assertEqual(result["id"], "shot-1")
        self.assertEqual(update.call_args.args[1]["description"], "她推开门。")

        prompt_payload = main.ImagePromptPatch(prompt="雨夜电影感", negative_prompt="水印")
        with patch.object(main.service, "update_image_prompt", return_value={"id": "prompt-1"}) as update:
            result = main.patch_image_prompt("prompt-1", prompt_payload, request)
        self.assertEqual(result["id"], "prompt-1")
        self.assertEqual(update.call_args.args[1], {"prompt": "雨夜电影感", "negative_prompt": "水印"})

        story_entity_payload = main.StoryEntityPatch(description="雨夜旧宅", status="approved")
        with patch.object(main.service, "update_story_entity", return_value={"id": "entity-1"}) as update:
            result = main.patch_story_entity("entity-1", story_entity_payload, request)
        self.assertEqual(result["id"], "entity-1")
        self.assertEqual(update.call_args.args[1], {"description": "雨夜旧宅", "status": "approved"})

        story_relationship_payload = main.StoryRelationshipPatch(relation="守护", description="顾言守护旧宅", status="approved")
        with patch.object(main.service, "update_story_relationship", return_value={"id": "relationship-1"}) as update:
            result = main.patch_story_relationship("relationship-1", story_relationship_payload, request)
        self.assertEqual(result["id"], "relationship-1")
        self.assertEqual(update.call_args.args[1], {"relation": "守护", "description": "顾言守护旧宅", "status": "approved"})

    def test_batch_character_reference_route_forwards_project_scope_and_queue_contract(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects/project-1/character-references", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.CharacterReferencesInput(character_ids=["character-1", "character-2"])
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            with patch.object(main, "current_user", return_value="user-1"):
                with patch.object(main.service, "create_character_references", return_value={"status": "queued"}) as create:
                    result = main.character_references_batch("project-1", request, payload, None, "batch-key")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(create.call_args.args[:3], ("project-1", ["character-1", "character-2"], "user-1"))
        self.assertFalse(create.call_args.args[3])
        self.assertEqual(create.call_args.args[4], "batch-key")

    def test_character_reference_upload_route_forwards_view_and_payload(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/characters/character-1/reference-upload", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.CharacterReferenceUploadInput(view="front", filename="front.png", data_base64="fixture")
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "upload_character_reference", return_value={"status": "uploaded"}) as upload:
                result = main.upload_character_reference("character-1", payload, request)
        self.assertEqual(result["status"], "uploaded")
        self.assertEqual(upload.call_args.args, ("character-1", "front", "front.png", "fixture", "user-1"))

    def test_batch_image_route_forwards_project_scope_and_queue_contract(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects/project-1/image-assets", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.ImageAssetsInput(shot_ids=["shot-1", "shot-2"])
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            with patch.object(main, "current_user", return_value="user-1"):
                with patch.object(main.service, "create_image_assets", return_value={"status": "queued"}) as create:
                    result = main.image_assets_batch("project-1", request, payload, None, "image-batch-key")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(create.call_args.args[:3], ("project-1", ["shot-1", "shot-2"], "user-1"))
        self.assertFalse(create.call_args.args[3])
        self.assertEqual(create.call_args.args[4], "image-batch-key")

    def test_batch_video_route_forwards_project_scope_and_queue_contract(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects/project-1/video-assets", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.VideoAssetsInput(asset_ids=["asset-1", "asset-2"])
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            with patch.object(main, "current_user", return_value="user-1"):
                with patch.object(main.service, "create_video_assets", return_value={"status": "queued"}) as create:
                    result = main.video_assets_batch("project-1", request, payload, None, "video-batch-key")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(create.call_args.args[:3], ("project-1", ["asset-1", "asset-2"], "user-1"))
        self.assertFalse(create.call_args.args[3])
        self.assertEqual(create.call_args.args[4], "video-batch-key")

    def test_batch_composition_route_forwards_project_scope_and_queue_contract(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects/project-1/compositions", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.CompositionsInput(episode_ids=["episode-1", "episode-2"])
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            with patch.object(main, "current_user", return_value="user-1"):
                with patch.object(main.service, "create_compositions", return_value={"status": "queued"}) as create:
                    result = main.compositions_batch("project-1", request, payload, None, "composition-batch-key")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(create.call_args.args[:3], ("project-1", ["episode-1", "episode-2"], "user-1"))
        self.assertFalse(create.call_args.args[3])
        self.assertEqual(create.call_args.args[4], "composition-batch-key")

    def test_project_archive_route_returns_scoped_zip_download(self):
        request = Request({"type": "http", "method": "GET", "path": "/api/projects/project-1/archive", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "project.zip"
            archive.write_bytes(b"PK\x05\x06" + b"\0" * 18)
            with patch.object(main, "current_user", return_value="user-1"):
                with patch.object(main.service, "get_project", return_value={"title": "测试/项目"}):
                    with patch.object(main.service, "write_project_archive", return_value=archive) as write:
                        response = main.project_archive("project-1", request)
            self.assertEqual(response.media_type, "application/zip")
            self.assertEqual(response.filename, "测试_项目-project-bundle.zip")
            self.assertEqual(write.call_args.args[0], "project-1")
            self.assertEqual(write.call_args.args[2], "user-1")
            self.assertTrue(str(write.call_args.args[1]).endswith(".zip"))

    def test_bulk_adaptation_review_route_forwards_project_and_unit_scope(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects/project-1/adaptation-units/review", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.AdaptationReviewBatchInput(unit_ids=["unit-1", "unit-2"], status="approved")
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "review_adaptation_units", return_value={"updated": 2}) as review:
                result = main.review_adaptation_units("project-1", payload, request)
        self.assertEqual(result["updated"], 2)
        self.assertEqual(review.call_args.args, ("project-1", ["unit-1", "unit-2"], "approved", "user-1"))

    def test_audio_import_route_passes_base64_payload_to_service(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/episodes/episode-1/audio", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.AudioImportInput(filename="narration.mp3", data_base64="YXVkaW8=")
        with patch.object(main, "current_user", return_value="local-user"):
            with patch.object(main.service, "import_audio_asset", return_value={"kind": "audio"}) as import_audio:
                result = main.import_audio("episode-1", payload, request)
        self.assertEqual(result["kind"], "audio")
        self.assertEqual(import_audio.call_args.args[1:3], ("narration.mp3", "YXVkaW8="))

    def test_narration_route_forwards_voice_and_idempotency_contract(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/episodes/episode-1/narration", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.NarrationInput(text="她推开门。", voice="cedar", speed=1.1, instructions="沉稳叙述", attach_to_timeline=True)
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "request_narration", return_value={"kind": "audio"}) as request_narration:
                result = main.generate_narration("episode-1", payload, request, True, "narration-key")
        self.assertEqual(result["kind"], "audio")
        self.assertEqual(
            request_narration.call_args.args,
            ("episode-1", "她推开门。", "cedar", 1.1, "沉稳叙述", True, "user-1", False, "narration-key"),
        )
        self.assertTrue(request_narration.call_args.kwargs["auto_subtitles"])

    def test_job_events_route_maps_service_not_found_to_http_404(self):
        request = Request({"type": "http", "method": "GET", "path": "/api/jobs/job-1/events", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.object(main, "current_user", return_value="local-user"):
            with patch.object(main.service, "job_events", side_effect=main.ServiceError("job not found", 404)):
                with self.assertRaises(HTTPException) as failure:
                    main.job_events("job-1", request, 100)
        self.assertEqual(failure.exception.status_code, 404)

    def test_single_composition_route_forwards_idempotency_key(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/episodes/episode-1/compose", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "request_composition", return_value={"status": "completed"}) as compose:
                result = main.compose("episode-1", request, None, "single-compose-key")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(compose.call_args.args, ("episode-1", "user-1", True, "single-compose-key"))

    def test_rewrite_route_forwards_idempotency_key(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects/project-1/rewrite", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.RewriteInput(mode="originalized")
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "request_rewrite", return_value={"job": {"status": "queued"}}) as rewrite:
                result = main.rewrite("project-1", payload, request, True, "rewrite-key")
        self.assertEqual(result["job"]["status"], "queued")
        self.assertEqual(rewrite.call_args.args, ("project-1", "originalized", "user-1", False, "rewrite-key"))

    def test_batch_asset_review_route_forwards_project_scope(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects/project-1/asset-reviews", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.AssetReviewsInput(asset_ids=["asset-1"], audit_type="scene", prompt="检查一致性")
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "review_assets", return_value={"status": "completed", "reviewed": 1}) as review:
                result = main.asset_reviews_batch("project-1", request, payload)
        self.assertEqual(result["reviewed"], 1)
        self.assertEqual(review.call_args.args, ("project-1", ["asset-1"], "检查一致性", "scene", "user-1"))

    def test_single_asset_review_route_forwards_queued_request(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/assets/asset-1/review", "headers": [(b"idempotency-key", b"review-key")], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.AssetReviewInput(prompt="检查构图", audit_type="scene")
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "request_asset_review", return_value={"job": {"status": "queued"}}) as review:
                result = main.review_asset("asset-1", payload, request, False)
        self.assertEqual(result["job"]["status"], "queued")
        self.assertEqual(review.call_args.args, ("asset-1", "检查构图", "scene", "user-1", True, "review-key"))

    def test_manual_asset_review_route_forwards_user_decision(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/assets/asset-1/review/decision", "headers": [(b"idempotency-key", b"manual-review-key")], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.ManualAssetReviewInput(status="PASS")
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "manual_review_asset", return_value={"id": "asset-1", "reviews": [{"status": "PASS"}]}) as review:
                result = main.manual_review_asset("asset-1", payload, request)
        self.assertEqual(result["reviews"][0]["status"], "PASS")
        review.assert_called_once_with("asset-1", "PASS", [], "user-1", "manual-review-key")

    def test_asset_qc_route_forwards_evidence_and_idempotency_key(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/assets/asset-1/qc", "headers": [(b"idempotency-key", b"qc-key")], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.AssetQCInput(sha256="a" * 64, decoded_fully=True)
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "submit_asset_qc", return_value={"auto_qc": {"review_status": "UNKNOWN"}}) as qc:
                result = main.asset_qc("asset-1", payload, request)
        self.assertEqual(result["auto_qc"]["review_status"], "UNKNOWN")
        qc.assert_called_once_with("asset-1", {"ffprobe": {}, "decoded_fully": True, "sha256": "a" * 64}, "user-1", "qc-key")

    def test_bullmq_single_asset_review_defaults_to_async_without_explicit_query(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/assets/asset-1/review", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.AssetReviewInput(prompt="检查画面", audit_type="scene")
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            with patch.object(main, "current_user", return_value="user-1"):
                with patch.object(main.service, "request_asset_review", return_value={"job": {"status": "queued"}}) as review:
                    result = main.review_asset("asset-1", payload, request)
        self.assertEqual(result["job"]["status"], "queued")
        self.assertEqual(review.call_args.args, ("asset-1", "检查画面", "scene", "user-1", False, None))

    def test_bullmq_batch_asset_review_defaults_to_async_without_explicit_query(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/projects/project-1/asset-reviews", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.AssetReviewsInput(asset_ids=["asset-1"], audit_type="scene", prompt="检查画面")
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            with patch.object(main, "current_user", return_value="user-1"):
                with patch.object(main.service, "review_assets", return_value={"status": "queued"}) as review:
                    result = main.asset_reviews_batch("project-1", request, payload)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(review.call_args.args, ("project-1", ["asset-1"], "检查画面", "scene", "user-1", False, None))

    def test_bullmq_prompt_route_defaults_to_async_without_explicit_query(self):
        request = Request({"type": "http", "method": "POST", "path": "/api/shots/shot-1/image-prompts", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.dict("os.environ", {"STUDIO_QUEUE_BACKEND": "bullmq"}, clear=False):
            with patch.object(main, "current_user", return_value="user-1"):
                with patch.object(main.service, "request_prompt_generation", return_value={"job": {"status": "queued"}}) as prompt:
                    result = main.image_prompts("shot-1", request)
        self.assertEqual(result["job"]["status"], "queued")
        self.assertEqual(prompt.call_args.args, ("shot-1", "user-1", False, None))

    def test_narrative_unit_and_scene_routes_forward_user_scope(self):
        request = Request({"type": "http", "method": "GET", "path": "/api/episodes/episode-1/narrative-units", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "list_narrative_units", return_value=[{"id": "unit-1"}]) as units:
                result = main.narrative_units("episode-1", request)
        self.assertEqual(result, [{"id": "unit-1"}])
        units.assert_called_once_with("episode-1", "user-1")

        create_request = Request({"type": "http", "method": "POST", "path": "/api/episodes/episode-1/scenes", "headers": [], "client": ("127.0.0.1", 1), "query_string": b"", "server": ("test", 80), "scheme": "http"})
        payload = main.SceneInput(name="旧宅门廊", unit_id="unit-1", shot_ids=["shot-1"])
        with patch.object(main, "current_user", return_value="user-1"):
            with patch.object(main.service, "create_scene", return_value={"id": "scene-1"}) as scene:
                result = main.create_scene("episode-1", payload, create_request)
        self.assertEqual(result, {"id": "scene-1"})
        scene.assert_called_once_with("episode-1", payload.model_dump(), "user-1")


if __name__ == "__main__":
    unittest.main()
