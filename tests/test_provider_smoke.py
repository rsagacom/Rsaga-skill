import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.provider_smoke import build_report, parse_args


class ProviderSmokeTests(unittest.TestCase):
    def test_speech_dry_run_is_deidentified_and_uses_openai_contract(self):
        args = parse_args(["--kind", "speech"])
        with patch.dict(
            "os.environ",
            {
                "STUDIO_SPEECH_BASE_URL": "https://api.openai.com/v1",
                "STUDIO_SPEECH_MODEL": "gpt-4o-mini-tts-2025-12-15",
                "STUDIO_SPEECH_API_KEY_ENV": "OPENAI_API_KEY",
                "OPENAI_API_KEY": "secret-value",
            },
            clear=False,
        ):
            report = build_report(args)
        self.assertEqual(report["status"], "dry-run")
        self.assertEqual(report["provider"], "openai")
        self.assertTrue(report["credential_configured"])
        self.assertNotIn("secret-value", json.dumps(report))

    def test_speech_run_fails_closed_without_key_before_sdk_call(self):
        args = parse_args(["--kind", "speech", "--run"])
        with patch.dict(
            "os.environ",
            {
                "STUDIO_SPEECH_BASE_URL": "https://api.openai.com/v1",
                "STUDIO_SPEECH_MODEL": "gpt-4o-mini-tts-2025-12-15",
                "STUDIO_SPEECH_API_KEY_ENV": "OPENAI_API_KEY",
            },
            clear=True,
        ), patch("scripts.provider_smoke.OpenAISpeechProvider") as provider:
            report = build_report(args)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "incomplete-speech-provider-config")
        provider.assert_not_called()
    def test_text_dry_run_is_deidentified(self):
        args = parse_args(["--kind", "text"])
        with patch.dict(
            "os.environ",
            {
                "STUDIO_TEXT_BASE_URL": "https://text.example.test/v1",
                "STUDIO_TEXT_MODEL": "qwen-test",
                "STUDIO_TEXT_API_KEY_ENV": "TEXT_SMOKE_KEY",
                "TEXT_SMOKE_KEY": "secret-value",
            },
            clear=False,
        ):
            report = build_report(args)
        self.assertEqual(report["status"], "dry-run")
        self.assertTrue(report["credential_configured"])
        self.assertNotIn("secret-value", json.dumps(report))

    def test_video_run_requires_source_image_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "wan22.json"
            workflow.write_text(json.dumps({"load": {"class_type": "LoadImage", "inputs": {"image": "{{IMAGE_REF}}", "asset": "{{ASSET_ID}}"}}}), encoding="utf-8")
            args = parse_args(["--kind", "video", "--base-url", "http://comfyui:8188", "--workflow", str(workflow), "--run"])
            with patch("scripts.provider_smoke.ComfyUIVideoProvider") as provider:
                report = build_report(args)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "source-image-not-configured")
        provider.assert_not_called()

    def test_image_dry_run_validates_workflow_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "image.json"
            workflow.write_text(json.dumps({"save": {"class_type": "SaveImage", "inputs": {"prompt": "{{PROMPT}}"}}}), encoding="utf-8")
            args = parse_args(["--kind", "image", "--base-url", "http://comfyui:8188", "--workflow", str(workflow)])
            with patch("scripts.provider_smoke.ComfyUIImageProvider") as provider:
                report = build_report(args)
        self.assertEqual(report["status"], "dry-run")
        provider.assert_not_called()

    def test_video_run_forwards_prompt_to_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "h3.json"
            workflow.write_text(
                json.dumps({
                    "video": {
                        "class_type": "SaveVideo",
                        "inputs": {
                            "filename_prefix": "{{ASSET_ID}}",
                            "prompt": "{{PROMPT}}",
                        },
                    }
                }),
                encoding="utf-8",
            )
            output_dir = Path(directory) / "output"
            fake_provider = MagicMock()
            fake_provider.generate.return_value = MagicMock(relative_url="/assets/provider-smoke-video.mp4", metadata={"provider": "comfyui", "prompt_id": "prompt-1"})
            output_path = output_dir / "provider-smoke-video.mp4"
            output_dir.mkdir()
            output_path.write_bytes(b"video")
            args = parse_args([
                "--kind", "video",
                "--base-url", "http://comfyui:8188",
                "--workflow", str(workflow),
                "--output-dir", str(output_dir),
                "--prompt", "a 3D CG hero walks through a rain-soaked alley",
                "--run",
            ])
            with patch("scripts.provider_smoke.ComfyUIVideoProvider", return_value=fake_provider):
                report = build_report(args)
        self.assertEqual(report["status"], "completed")
        fake_provider.generate.assert_called_once()
        self.assertEqual(fake_provider.generate.call_args.kwargs["prompt"], "a 3D CG hero walks through a rain-soaked alley")

    def test_comfyui_smoke_rejects_malformed_workflow_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "malformed.json"
            workflow.write_text("not-json", encoding="utf-8")
            args = parse_args(["--kind", "image", "--base-url", "http://comfyui:8188", "--workflow", str(workflow)])
            with patch("scripts.provider_smoke.ComfyUIImageProvider") as provider:
                report = build_report(args)
        self.assertEqual(report["status"], "configuration-warning")
        self.assertEqual(report["reason"], "workflow-file-invalid-json")
        self.assertFalse(report["workflow_valid"])
        self.assertNotIn(str(workflow), json.dumps(report))
        provider.assert_not_called()

    def test_comfyui_smoke_rejects_empty_or_non_object_workflow(self):
        for payload, reason in (({}, "workflow-must-be-non-empty-object"), ([], "workflow-must-be-object")):
            with tempfile.TemporaryDirectory() as directory:
                workflow = Path(directory) / "workflow.json"
                workflow.write_text(json.dumps(payload), encoding="utf-8")
                args = parse_args(["--kind", "image", "--base-url", "http://comfyui:8188", "--workflow", str(workflow)])
                with patch("scripts.provider_smoke.ComfyUIImageProvider") as provider:
                    report = build_report(args)
            self.assertEqual(report["status"], "configuration-warning")
            self.assertEqual(report["reason"], reason)
            self.assertFalse(report["workflow_valid"])
            provider.assert_not_called()

    def test_text_run_reports_contract_without_secret_or_raw_response(self):
        args = parse_args(["--kind", "text", "--run"])
        fake_provider = MagicMock()
        fake_provider.complete.return_value = ('{"status":"ok"}', {"provider": "openai-compatible", "model": "qwen-test"})
        with patch.dict(
            "os.environ",
            {
                "STUDIO_TEXT_BASE_URL": "https://text.example.test/v1",
                "STUDIO_TEXT_MODEL": "qwen-test",
                "STUDIO_TEXT_API_KEY_ENV": "TEXT_SMOKE_KEY",
                "TEXT_SMOKE_KEY": "secret-value",
            },
            clear=False,
        ), patch("scripts.provider_smoke.OpenAICompatibleTextProvider", return_value=fake_provider):
            report = build_report(args)
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["response_json"])
        self.assertNotIn("secret-value", json.dumps(report))
        self.assertNotIn("raw", report)


if __name__ == "__main__":
    unittest.main()
