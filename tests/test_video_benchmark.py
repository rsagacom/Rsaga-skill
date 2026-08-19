import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.benchmark_video_provider import build_report, parse_args
from studio_api.providers import ProviderError


class VideoBenchmarkTests(unittest.TestCase):
    def test_dry_run_validates_workflow_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "wan22.json"
            workflow.write_text(
                json.dumps({"1": {"class_type": "SaveVideo", "inputs": {"filename": "{{ASSET_ID}}", "shot": "{{SHOT_ID}}", "image": "{{IMAGE_REF}}"}}}),
                encoding="utf-8",
            )
            args = parse_args(["--label", "wan22", "--base-url", "http://127.0.0.1:8188", "--workflow", str(workflow)])
            report = build_report(args)
        self.assertEqual(report["status"], "dry-run")
        self.assertEqual(report["workflow"]["node_count"], 1)
        self.assertIn("{{ASSET_ID}}", report["workflow"]["placeholders"])
        self.assertTrue(report["workflow"]["requires_source_image"])
        self.assertFalse(report["source_image_configured"])

    def test_run_requires_source_image_for_image_to_video_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "ltx.json"
            workflow.write_text(json.dumps({"1": {"class_type": "LoadImage", "inputs": {"image": "{{IMAGE_REF}}", "asset": "{{ASSET_ID}}"}}}), encoding="utf-8")
            args = parse_args(["--base-url", "http://127.0.0.1:8188", "--workflow", str(workflow), "--run"])
            report = build_report(args)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "source-image-not-configured")
        self.assertNotIn("runs", report)

    def test_unconfigured_benchmark_does_not_claim_a_provider_result(self):
        report = build_report(parse_args([]))
        self.assertEqual(report["status"], "not-configured")
        self.assertNotIn("runs_completed", report)

    def test_run_count_is_bounded(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_args(["--runs", "6"])

    def test_completed_run_drops_untrusted_provider_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "ltx.json"
            workflow.write_text(
                json.dumps({"1": {"class_type": "SaveVideo", "inputs": {"filename": "{{ASSET_ID}}", "shot": "{{SHOT_ID}}"}}}),
                encoding="utf-8",
            )
            output_dir = Path(directory) / "output"

            class FakeGenerated:
                relative_url = "/assets/benchmark.mp4"
                metadata = {
                    "provider": "comfyui",
                    "source": "comfyui",
                    "prompt_id": "provider-internal-id",
                    "input_image": {"name": "/secret/provider/path/keyframe.png"},
                }

            class FakeProvider:
                def __init__(self, *_args, **_kwargs):
                    pass

                def generate(self, *_args, **_kwargs):
                    output_dir.mkdir(parents=True, exist_ok=True)
                    (output_dir / f"{_args[0]}.mp4").write_bytes(b"mp4")
                    return FakeGenerated()

            args = parse_args([
                "--label", "wan22",
                "--base-url", "http://127.0.0.1:8188",
                "--workflow", str(workflow),
                "--output-dir", str(output_dir),
                "--run",
            ])
            with patch("scripts.benchmark_video_provider.ComfyUIVideoProvider", FakeProvider):
                report = build_report(args)

        self.assertEqual(report["status"], "completed")
        item = report["runs"][0]
        self.assertEqual(item["provider_metadata"], {"provider": "comfyui", "source": "comfyui"})
        self.assertNotIn("provider-internal-id", str(report))
        self.assertNotIn("/secret/provider/path", str(report))
        self.assertNotIn("metadata", item)

    def test_provider_failure_uses_fixed_reason_without_exception_text(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "ltx.json"
            workflow.write_text(
                json.dumps({"1": {"class_type": "SaveVideo", "inputs": {"filename": "{{ASSET_ID}}", "shot": "{{SHOT_ID}}"}}}),
                encoding="utf-8",
            )

            class FakeProvider:
                def __init__(self, *_args, **_kwargs):
                    pass

                def generate(self, *_args, **_kwargs):
                    raise ProviderError("raw provider response with /private/provider/path")

            args = parse_args([
                "--base-url", "http://127.0.0.1:8188",
                "--workflow", str(workflow),
                "--run",
            ])
            with patch("scripts.benchmark_video_provider.ComfyUIVideoProvider", FakeProvider):
                report = build_report(args)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["runs"][0]["reason"], "comfyui-provider-contract-failed")
        self.assertNotIn("raw provider response", str(report))
        self.assertNotIn("/private/provider/path", str(report))


if __name__ == "__main__":
    unittest.main()
