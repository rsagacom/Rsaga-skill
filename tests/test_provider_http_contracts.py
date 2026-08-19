import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

from studio_api.providers import (
    ComfyUIImageProvider,
    ComfyUIVideoProvider,
    OpenAICompatibleTextProvider,
    OpenAICompatibleVisionProvider,
    OpenAISpeechProvider,
    LocalPreviewSpeechProvider,
    ProviderError,
)


class _ProviderHandler(BaseHTTPRequestHandler):
    server_version = "ProviderContract/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def state(self) -> dict[str, Any]:
        return self.server.state  # type: ignore[attr-defined]

    def _send_json(self, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_raw(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        path = urlsplit(self.path).path
        self.state.setdefault("posts", []).append(
            {"path": path, "body": body, "headers": dict(self.headers.items())}
        )
        if path.endswith("/chat/completions"):
            self._send_json(
                {
                    "choices": [
                        {
                            "message": {
                                "content": self.state.get("chat_content", '{"status":"ok"}')
                            }
                        }
                    ]
                }
            )
            return
        if path == "/prompt":
            if "prompt_raw" in self.state:
                self._send_raw(self.state["prompt_raw"])
                return
            self._send_json({"prompt_id": self.state.get("prompt_id", "prompt-1")})
            return
        if path == "/upload/image":
            self.state["upload_body"] = body
            if "upload_response" in self.state:
                self._send_json(self.state["upload_response"])
                return
            self._send_json({"name": "source.png", "subfolder": "", "type": "input"})
            return
        self.send_error(404)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/history/"):
            if "history_raw" in self.state:
                self._send_raw(self.state["history_raw"])
                return
            self._send_json(self.state.get("history", {}))
            return
        if path == "/view":
            self._send_bytes(self.state.get("media_body", b"media"), "application/octet-stream")
            return
        self.send_error(404)


class ProviderHTTPContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state: dict[str, Any] = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
        self.server.state = self.state  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def test_local_speech_preview_writes_bounded_deterministic_wav(self):
        with TemporaryDirectory() as directory:
            generated = LocalPreviewSpeechProvider().synthesize(
                "speech-1", "她推开门。", Path(directory), voice="cedar", speed=1.0
            )
            output = Path(directory) / Path(generated.relative_url).name
            self.assertTrue(output.exists())
            self.assertEqual(generated.metadata["mode"], "local-speech-preview")
            self.assertEqual(generated.metadata["text_chars"], 5)
            self.assertGreater(output.stat().st_size, 44)

    def test_speech_provider_rejects_unallowlisted_voice_and_honors_deployment_narrowing(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ProviderError, "voice is not allowlisted"):
                LocalPreviewSpeechProvider().synthesize(
                    "speech-invalid-voice", "旁白", Path(directory), voice="custom-voice", speed=1.0
                )
        with TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"STUDIO_SPEECH_ALLOWED_VOICES": "marin"}, clear=False):
                with self.assertRaisesRegex(ProviderError, "voice is not allowlisted"):
                    LocalPreviewSpeechProvider().synthesize(
                        "speech-narrowed-voice", "旁白", Path(directory), voice="cedar", speed=1.0
                    )

        with TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"STUDIO_SPEECH_ALLOWED_VOICES": "custom-voice"}, clear=False):
                with self.assertRaisesRegex(ProviderError, "voice is not allowlisted"):
                    LocalPreviewSpeechProvider().synthesize(
                        "speech-custom-voice", "旁白", Path(directory), voice="custom-voice", speed=1.0
                    )

    def test_openai_speech_provider_does_not_call_without_configured_key(self):
        provider = OpenAISpeechProvider(self.base_url, "contract-speech-model", "MISSING_SPEECH_KEY")
        with TemporaryDirectory() as directory:
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(ProviderError, "credential is not configured"):
                    provider.synthesize("speech-2", "旁白", Path(directory), voice="cedar", speed=1.0)
        self.assertEqual(self.state.get("posts", []), [])

    def test_openai_compatible_text_sends_json_mode_and_reads_structured_response(self):
        self.state["chat_content"] = '{"episodes":[{"title":"雨夜"}]}'
        provider = OpenAICompatibleTextProvider(
            f"{self.base_url}/v1", "contract-text-model", "CONTRACT_TEXT_KEY"
        )
        with patch.dict("os.environ", {"CONTRACT_TEXT_KEY": "test-secret"}, clear=False):
            result, metadata = provider.complete("生成结构化分集", json_mode=True)

        self.assertEqual(json.loads(result), {"episodes": [{"title": "雨夜"}]})
        self.assertEqual(metadata["model"], "contract-text-model")
        request = self.state["posts"][0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        body = json.loads(request["body"])
        self.assertEqual(body["model"], "contract-text-model")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-secret")

    def test_openai_compatible_text_normalizes_string_and_text_content_parts(self):
        self.state["chat_content"] = ["第一段", {"type": "text", "text": "第二段"}]
        provider = OpenAICompatibleTextProvider(
            f"{self.base_url}/v1", "contract-text-model", "CONTRACT_TEXT_KEY"
        )
        with patch.dict("os.environ", {"CONTRACT_TEXT_KEY": "test-secret"}, clear=False):
            result, _metadata = provider.complete("生成文本")
        self.assertEqual(result, "第一段\n第二段")

    def test_openai_compatible_text_rejects_oversized_response(self):
        self.state["chat_content"] = "x" * 2048
        provider = OpenAICompatibleTextProvider(
            f"{self.base_url}/v1", "contract-text-model", "CONTRACT_TEXT_KEY"
        )
        with patch.dict(
            "os.environ",
            {"CONTRACT_TEXT_KEY": "test-secret", "STUDIO_PROVIDER_RESPONSE_MAX_BYTES": "1024"},
            clear=False,
        ):
            with self.assertRaisesRegex(ProviderError, "response exceeds size limit"):
                provider.complete("生成文本")

    def test_openai_compatible_vision_maps_pass_response(self):
        self.state["chat_content"] = "PASS"
        provider = OpenAICompatibleVisionProvider(
            f"{self.base_url}/v1", "contract-vision-model", "CONTRACT_VISION_KEY"
        )
        with TemporaryDirectory() as directory:
            image = Path(directory) / "frame.png"
            image.write_bytes(b"fake-png-for-http-contract")
            with patch.dict("os.environ", {"CONTRACT_VISION_KEY": "test-vision-secret"}, clear=False):
                result = provider.review(image, "检查画面一致性")

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["issues"], [])
        body = json.loads(self.state["posts"][0]["body"])
        content = body["messages"][0]["content"]
        self.assertIn("检查画面一致性", content[0]["text"])
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_openai_compatible_vision_normalizes_text_content_parts(self):
        self.state["chat_content"] = ["PASS"]
        provider = OpenAICompatibleVisionProvider(
            f"{self.base_url}/v1", "contract-vision-model", "CONTRACT_VISION_KEY"
        )
        with TemporaryDirectory() as directory:
            image = Path(directory) / "frame.png"
            image.write_bytes(b"fake-png-for-http-contract")
            with patch.dict("os.environ", {"CONTRACT_VISION_KEY": "test-vision-secret"}, clear=False):
                result = provider.review(image, "检查画面一致性")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["issues"], [])

    def test_openai_compatible_vision_rejects_oversized_response(self):
        self.state["chat_content"] = "x" * 2048
        provider = OpenAICompatibleVisionProvider(
            f"{self.base_url}/v1", "contract-vision-model", "CONTRACT_VISION_KEY"
        )
        with TemporaryDirectory() as directory:
            image = Path(directory) / "frame.png"
            image.write_bytes(b"fake-png-for-http-contract")
            with patch.dict(
                "os.environ",
                {"CONTRACT_VISION_KEY": "test-vision-secret", "STUDIO_PROVIDER_RESPONSE_MAX_BYTES": "1024"},
                clear=False,
            ):
                with self.assertRaisesRegex(ProviderError, "response exceeds size limit"):
                    provider.review(image, "检查画面一致性")

    def test_openai_compatible_vision_rejects_content_parts_without_text(self):
        self.state["chat_content"] = [{"type": "image_url", "image_url": {"url": "data:"}}]
        provider = OpenAICompatibleVisionProvider(
            f"{self.base_url}/v1", "contract-vision-model", "CONTRACT_VISION_KEY"
        )
        with TemporaryDirectory() as directory:
            image = Path(directory) / "frame.png"
            image.write_bytes(b"fake-png-for-http-contract")
            with patch.dict("os.environ", {"CONTRACT_VISION_KEY": "test-vision-secret"}, clear=False):
                with self.assertRaisesRegex(ProviderError, "invalid response"):
                    provider.review(image, "检查画面一致性")

    def test_openai_compatible_vision_sanitizes_unavailable_input(self):
        provider = OpenAICompatibleVisionProvider(
            f"{self.base_url}/v1", "contract-vision-model", "CONTRACT_VISION_KEY"
        )
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.png"
            with patch.dict("os.environ", {"CONTRACT_VISION_KEY": "test-vision-secret"}, clear=False):
                with self.assertRaisesRegex(ProviderError, "input image is unavailable"):
                    provider.review(missing, "检查画面一致性")

    def test_openai_compatible_vision_rejects_non_string_content(self):
        self.state["chat_content"] = {"unexpected": "object"}
        provider = OpenAICompatibleVisionProvider(
            f"{self.base_url}/v1", "contract-vision-model", "CONTRACT_VISION_KEY"
        )
        with TemporaryDirectory() as directory:
            image = Path(directory) / "frame.png"
            image.write_bytes(b"fake-png-for-http-contract")
            with patch.dict("os.environ", {"CONTRACT_VISION_KEY": "test-vision-secret"}, clear=False):
                with self.assertRaisesRegex(ProviderError, "invalid response"):
                    provider.review(image, "检查画面一致性")

    def test_comfyui_image_submits_workflow_and_downloads_output(self):
        self.state.update(
            {
                "prompt_id": "image-prompt-1",
                "history": {
                    "image-prompt-1": {
                        "outputs": {"save": {"images": [{"filename": "frame.png", "subfolder": "", "type": "output"}]}}
                    }
                },
                "media_body": b"png-bytes",
            }
        )
        with TemporaryDirectory() as directory:
            workflow = Path(directory) / "image.json"
            workflow.write_text(
                json.dumps({"1": {"inputs": {"text": "{{PROMPT}}", "client": "{{CLIENT_ID}}"}}}),
                encoding="utf-8",
            )
            output_dir = Path(directory) / "assets"
            provider = ComfyUIImageProvider(
                self.base_url, str(workflow), timeout=1, poll_interval=0.01
            )
            generated = provider.generate("asset-image-1", "雨夜街道", "雨中霓虹", output_dir)

            output = output_dir / "asset-image-1.png"
            self.assertEqual(generated.relative_url, "/assets/asset-image-1.png")
            self.assertEqual(output.read_bytes(), b"png-bytes")
            prompt = json.loads(self.state["posts"][0]["body"])
            self.assertEqual(prompt["prompt"]["1"]["inputs"]["text"], "雨中霓虹")
            self.assertEqual(prompt["prompt"]["1"]["inputs"]["client"], "asset-image-1")

    def test_comfyui_video_uploads_source_image_before_download(self):
        self.state.update(
            {
                "prompt_id": "video-prompt-1",
                "history": {
                    "video-prompt-1": {
                        "outputs": {"save": {"gifs": [{"filename": "clip.mp4", "subfolder": "", "type": "output"}]}}
                    }
                },
                "media_body": b"video-bytes",
            }
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "video.json"
            workflow.write_text(
                json.dumps({"1": {"inputs": {"image": "{{IMAGE_REF}}", "client": "{{CLIENT_ID}}"}}}),
                encoding="utf-8",
            )
            source = root / "source.png"
            source.write_bytes(b"source-image-bytes")
            output_dir = root / "assets"
            provider = ComfyUIVideoProvider(
                self.base_url, str(workflow), timeout=1, poll_interval=0.01
            )
            generated = provider.generate("asset-video-1", "shot-1", output_dir, source)

            self.assertEqual(generated.relative_url, "/assets/asset-video-1.mp4")
            self.assertEqual((output_dir / "asset-video-1.mp4").read_bytes(), b"video-bytes")
            self.assertIn(b'filename="asset-video-1.png"', self.state["upload_body"])
            prompt = json.loads(self.state["posts"][1]["body"])
            self.assertEqual(prompt["prompt"]["1"]["inputs"]["image"], "source.png")
            self.assertEqual(prompt["prompt"]["1"]["inputs"]["client"], "asset-video-1")
            self.assertNotIn(str(source).encode(), self.state["posts"][1]["body"])

    def test_comfyui_video_supports_text_to_video_prompt_without_source_image(self):
        self.state.update(
            {
                "prompt_id": "t2v-prompt-1",
                "history": {
                    "t2v-prompt-1": {
                        "outputs": {"save": {"gifs": [{"filename": "t2v.mp4", "subfolder": "", "type": "output"}]}}
                    }
                },
                "media_body": b"t2v-video-bytes",
            }
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "t2v.json"
            workflow.write_text(
                json.dumps({"1": {"inputs": {"text": "{{PROMPT}}", "negative": "{{NEGATIVE_PROMPT}}", "client": "{{CLIENT_ID}}"}}}),
                encoding="utf-8",
            )
            output_dir = root / "assets"
            provider = ComfyUIVideoProvider(self.base_url, str(workflow), timeout=1, poll_interval=0.01)
            generated = provider.generate(
                "asset-t2v-1",
                "shot-1",
                output_dir,
                prompt="3DCG 国风夜雨，祁思远站在废弃石桥前，镜头缓慢推进",
            )

            self.assertEqual(generated.relative_url, "/assets/asset-t2v-1.mp4")
            prompt = json.loads(self.state["posts"][0]["body"])
            inputs = prompt["prompt"]["1"]["inputs"]
            self.assertEqual(inputs["text"], "3DCG 国风夜雨，祁思远站在废弃石桥前，镜头缓慢推进")
            self.assertTrue(inputs["negative"])
            self.assertEqual(len(self.state["posts"]), 1)
            self.assertEqual(self.state["posts"][0]["path"], "/prompt")

    def test_comfyui_video_routes_t2v_and_r2v_to_separate_workflows(self):
        self.state.update(
            {
                "prompt_id": "route-prompt-1",
                "history": {
                    "route-prompt-1": {
                        "outputs": {"save": {"gifs": [{"filename": "route.mp4", "subfolder": "", "type": "output"}]}}
                    }
                },
                "media_body": b"route-video-bytes",
            }
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            t2v = root / "t2v.json"
            t2v.write_text(
                json.dumps({"1": {"inputs": {"route": "t2v", "text": "{{PROMPT}}", "client": "{{CLIENT_ID}}"}}}),
                encoding="utf-8",
            )
            r2v = root / "r2v.json"
            r2v.write_text(
                json.dumps({"1": {"inputs": {"route": "r2v", "image": "{{IMAGE_REF}}", "client": "{{CLIENT_ID}}"}}}),
                encoding="utf-8",
            )
            source = root / "source.png"
            source.write_bytes(b"source-image-bytes")
            output_dir = root / "assets"
            provider = ComfyUIVideoProvider(
                self.base_url,
                str(t2v),
                timeout=1,
                poll_interval=0.01,
                t2v_workflow_path=str(t2v),
                r2v_workflow_path=str(r2v),
            )
            t2v_result = provider.generate("route-t2v", "shot-1", output_dir, prompt="T2V prompt")
            t2v_payload = json.loads(self.state["posts"][0]["body"])
            self.assertEqual(t2v_payload["prompt"]["1"]["inputs"]["route"], "t2v")
            self.assertEqual(t2v_result.metadata["generation_mode"], "t2v")
            self.assertEqual(t2v_result.metadata["workflow_file"], "t2v.json")

            self.state["prompt_id"] = "route-prompt-2"
            self.state["history"]["route-prompt-2"] = {
                "outputs": {"save": {"gifs": [{"filename": "route-r2v.mp4", "subfolder": "", "type": "output"}]}}
            }
            r2v_result = provider.generate("route-r2v", "shot-2", output_dir, source, prompt="R2V prompt")
            r2v_payload = json.loads(self.state["posts"][2]["body"])
            self.assertEqual(r2v_payload["prompt"]["1"]["inputs"]["route"], "r2v")
            self.assertEqual(r2v_result.metadata["generation_mode"], "r2v")
            self.assertEqual(r2v_result.metadata["workflow_file"], "r2v.json")

    def test_comfyui_image_download_is_bounded(self):
        self.state.update(
            {
                "prompt_id": "image-limit-1",
                "history": {
                    "image-limit-1": {
                        "outputs": {"save": {"images": [{"filename": "frame.png", "subfolder": "", "type": "output"}]}}
                    }
                },
                "media_body": b"too-large",
            }
        )
        with TemporaryDirectory() as directory:
            workflow = Path(directory) / "image.json"
            workflow.write_text(json.dumps({"1": {"inputs": {"text": "{{PROMPT}}"}}}), encoding="utf-8")
            provider = ComfyUIImageProvider(self.base_url, str(workflow), timeout=1, poll_interval=0.01)
            with patch.dict("os.environ", {"COMFYUI_OUTPUT_MAX_BYTES": "4"}, clear=False):
                with self.assertRaisesRegex(ProviderError, "ComfyUI image download exceeds size limit"):
                    provider.generate("asset-image-limit-1", "雨夜街道", "雨中霓虹", Path(directory) / "assets")

    def test_comfyui_video_download_is_bounded(self):
        self.state.update(
            {
                "prompt_id": "video-limit-1",
                "history": {
                    "video-limit-1": {
                        "outputs": {"save": {"gifs": [{"filename": "clip.mp4", "subfolder": "", "type": "output"}]}}
                    }
                },
                "media_body": b"too-large",
            }
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "video.json"
            workflow.write_text(json.dumps({"1": {"inputs": {"client": "{{CLIENT_ID}}"}}}), encoding="utf-8")
            provider = ComfyUIVideoProvider(self.base_url, str(workflow), timeout=1, poll_interval=0.01)
            with patch.dict("os.environ", {"COMFYUI_OUTPUT_MAX_BYTES": "4"}, clear=False):
                with self.assertRaisesRegex(ProviderError, "ComfyUI video download exceeds size limit"):
                    provider.generate("asset-video-limit-1", "shot-limit-1", root / "assets")

    def test_comfyui_workflow_read_errors_are_sanitized(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing-workflow.json"
            provider = ComfyUIImageProvider(self.base_url, str(missing), timeout=1, poll_interval=0.01)
            with self.assertRaisesRegex(ProviderError, "workflow is not available") as failure:
                provider.generate("asset-image-1", "雨夜街道", "雨中霓虹", root / "assets")
            self.assertNotIn(str(missing), str(failure.exception))

            invalid = root / "invalid-workflow.json"
            invalid.write_text("{", encoding="utf-8")
            provider = ComfyUIImageProvider(self.base_url, str(invalid), timeout=1, poll_interval=0.01)
            with self.assertRaisesRegex(ProviderError, "workflow is invalid"):
                provider.generate("asset-image-2", "雨夜街道", "雨中霓虹", root / "assets")

    def test_comfyui_history_and_output_shapes_do_not_raise_type_errors(self):
        self.assertIsNone(ComfyUIImageProvider._find_image({"node": "malformed"}))
        self.assertIsNone(ComfyUIImageProvider._find_image({"node": {"images": "malformed"}}))
        self.assertIsNone(ComfyUIVideoProvider._find_media({"node": {"videos": "malformed"}}))

        self.state["history"] = []
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "image.json"
            workflow.write_text(json.dumps({"1": {"inputs": {"text": "{{PROMPT}}"}}}), encoding="utf-8")
            provider = ComfyUIImageProvider(self.base_url, str(workflow), timeout=1, poll_interval=0.01)
            with self.assertRaisesRegex(ProviderError, "history response is invalid"):
                provider.generate("asset-image-3", "雨夜街道", "雨中霓虹", root / "assets")

    def test_comfyui_malformed_json_responses_are_sanitized(self):
        self.state["prompt_raw"] = b"not-json"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "image.json"
            workflow.write_text(json.dumps({"1": {"inputs": {"text": "{{PROMPT}}"}}}), encoding="utf-8")
            provider = ComfyUIImageProvider(self.base_url, str(workflow), timeout=1, poll_interval=0.01)
            with self.assertRaisesRegex(ProviderError, "prompt submission returned invalid response"):
                provider.generate("asset-image-4", "雨夜街道", "雨中霓虹", root / "assets")

        self.state.pop("prompt_raw")
        self.state["history_raw"] = b"not-json"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "image.json"
            workflow.write_text(json.dumps({"1": {"inputs": {"text": "{{PROMPT}}"}}}), encoding="utf-8")
            provider = ComfyUIImageProvider(self.base_url, str(workflow), timeout=1, poll_interval=0.01)
            with self.assertRaisesRegex(ProviderError, "history response is invalid"):
                provider.generate("asset-image-5", "雨夜街道", "雨中霓虹", root / "assets")

    def test_comfyui_control_responses_are_bounded(self):
        self.state["prompt_raw"] = b"{" + b"x" * 2048
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "image.json"
            workflow.write_text(json.dumps({"1": {"inputs": {"text": "{{PROMPT}}"}}}), encoding="utf-8")
            provider = ComfyUIImageProvider(self.base_url, str(workflow), timeout=1, poll_interval=0.01)
            with patch.dict("os.environ", {"STUDIO_PROVIDER_RESPONSE_MAX_BYTES": "1024"}, clear=False):
                with self.assertRaisesRegex(ProviderError, "prompt submission returned invalid response"):
                    provider.generate("asset-image-control-limit-1", "雨夜街道", "雨中霓虹", root / "assets")

        self.state.pop("prompt_raw")
        self.state["history_raw"] = b"{" + b"x" * 2048
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "image.json"
            workflow.write_text(json.dumps({"1": {"inputs": {"text": "{{PROMPT}}"}}}), encoding="utf-8")
            provider = ComfyUIImageProvider(self.base_url, str(workflow), timeout=1, poll_interval=0.01)
            with patch.dict("os.environ", {"STUDIO_PROVIDER_RESPONSE_MAX_BYTES": "1024"}, clear=False):
                with self.assertRaisesRegex(ProviderError, "history response is invalid"):
                    provider.generate("asset-image-control-limit-2", "雨夜街道", "雨中霓虹", root / "assets")

    def test_comfyui_source_upload_reads_and_bounds_the_actual_bytes(self):
        self.state["upload_response"] = {"name": "", "subfolder": "", "type": "input"}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "video.json"
            workflow.write_text(json.dumps({"1": {"inputs": {"image": "{{IMAGE_REF}}"}}}), encoding="utf-8")
            source = root / "source.png"
            source.write_bytes(b"source-image-bytes")
            provider = ComfyUIVideoProvider(self.base_url, str(workflow), timeout=1, poll_interval=0.01)
            with patch.dict("os.environ", {"COMFYUI_INPUT_MAX_BYTES": "4"}, clear=False):
                with self.assertRaisesRegex(ProviderError, "source image exceeds upload limit"):
                    provider.generate("asset-video-2", "shot-2", root / "assets", source)

        self.state["upload_response"] = {"unexpected": "object"}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "video.json"
            workflow.write_text(json.dumps({"1": {"inputs": {"image": "{{IMAGE_REF}}"}}}), encoding="utf-8")
            source = root / "source.png"
            source.write_bytes(b"source-image-bytes")
            provider = ComfyUIVideoProvider(self.base_url, str(workflow), timeout=1, poll_interval=0.01)
            with self.assertRaisesRegex(ProviderError, "source image upload returned no filename"):
                provider.generate("asset-video-3", "shot-3", root / "assets", source)


if __name__ == "__main__":
    unittest.main()
