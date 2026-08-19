import unittest
import tempfile
from pathlib import Path

from scripts.production_config_check import check_production_config


class ProductionConfigTests(unittest.TestCase):
    @staticmethod
    def valid_config() -> dict[str, str]:
        return {
            "STUDIO_ENV": "production",
            "STUDIO_REQUIRE_AUTH": "true",
            "STUDIO_ALLOW_LOCAL_TOP_UP": "false",
            "STUDIO_BILLING_PROVIDER": "signed-webhook",
            "STUDIO_BILLING_WEBHOOK_SECRET": "billing-webhook-secret-with-at-least-32-chars",
            "STUDIO_BILLING_CHECKOUT_URL": "https://pay.example.test/checkout",
            "STUDIO_TEXT_PROVIDER": "openai-compatible",
            "STUDIO_TEXT_BASE_URL": "https://text.example.test/v1",
            "STUDIO_TEXT_MODEL": "text-production-model",
            "STUDIO_TEXT_API_KEY_ENV": "STUDIO_TEXT_API_KEY",
            "STUDIO_TEXT_API_KEY": "text-provider-secret-with-at-least-32-chars",
            "STUDIO_IMAGE_PROVIDER": "comfyui",
            "COMFYUI_BASE_URL": "http://comfyui:8188",
            "COMFYUI_IMAGE_WORKFLOW": "/app/workflows/image.json",
            "STUDIO_VIDEO_PROVIDER": "comfyui",
            "COMFYUI_VIDEO_WORKFLOW": "/app/workflows/video.json",
            "STUDIO_VISION_PROVIDER": "openai-compatible",
            "STUDIO_VISION_BASE_URL": "https://vision.example.test/v1",
            "STUDIO_VISION_MODEL": "vision-production-model",
            "STUDIO_VISION_API_KEY_ENV": "STUDIO_VISION_API_KEY",
            "STUDIO_VISION_API_KEY": "vision-provider-secret-with-at-least-32-chars",
            "STUDIO_SPEECH_PROVIDER": "openai",
            "STUDIO_SPEECH_BASE_URL": "https://api.openai.com/v1",
            "STUDIO_SPEECH_MODEL": "gpt-4o-mini-tts-2025-12-15",
            "STUDIO_SPEECH_API_KEY_ENV": "OPENAI_API_KEY",
            "OPENAI_API_KEY": "speech-provider-secret-with-at-least-32-chars",
            "STUDIO_SPEECH_ALLOWED_VOICES": "cedar,marin",
            "STUDIO_AUTH_COOKIE_SECURE": "true",
            "STUDIO_STORE": "postgres",
            "STUDIO_QUEUE_BACKEND": "bullmq",
            "STUDIO_STORAGE": "minio",
            "STUDIO_ALLOWED_HOSTS": "studio.example.test",
            "STUDIO_CORS_ORIGINS": "https://studio.example.test",
            "STUDIO_DATABASE_URL": "postgresql://manhua:strong-password@postgres:5432/manhua_studio",
            "POSTGRES_PASSWORD": "a-production-password-123",
            "MINIO_ROOT_PASSWORD": "another-production-password-456",
            "ORCHESTRATOR_TOKEN": "orchestrator-token-with-at-least-32-chars",
            "STUDIO_INTERNAL_TOKEN": "internal-token-with-at-least-32-chars",
        }

    def test_production_defaults_fail_without_exposing_values(self):
        report = check_production_config(
            {
                "STUDIO_ENV": "production",
                "STUDIO_REQUIRE_AUTH": "true",
                "STUDIO_STORE": "postgres",
                "STUDIO_QUEUE_BACKEND": "bullmq",
                "STUDIO_STORAGE": "minio",
                "POSTGRES_PASSWORD": "change-me-local-only",
                "MINIO_ROOT_PASSWORD": "change-me-local-only",
                "ORCHESTRATOR_TOKEN": "local-orchestrator-only-change-me",
                "STUDIO_INTERNAL_TOKEN": "local-internal-only-change-me",
            },
            required=True,
        )
        self.assertFalse(report["ok"])
        self.assertIn("POSTGRES_PASSWORD:known-placeholder", report["errors"])
        self.assertIn("ORCHESTRATOR_TOKEN:known-placeholder", report["errors"])
        self.assertIn("STUDIO_TEXT_PROVIDER:missing", report["errors"])
        self.assertIn("STUDIO_IMAGE_PROVIDER:missing", report["errors"])
        self.assertNotIn("change-me-local-only", str(report))
        self.assertNotIn("local-orchestrator-only-change-me", str(report))

    def test_valid_production_config_passes_without_returning_secret_values(self):
        config = self.valid_config()
        report = check_production_config(config, required=True)
        self.assertTrue(report["ok"])
        self.assertEqual(report["state"], "configured")
        self.assertNotIn(config["POSTGRES_PASSWORD"], str(report))
        self.assertNotIn(config["ORCHESTRATOR_TOKEN"], str(report))

    def test_production_provider_key_reference_is_explicitly_wired_by_compose(self):
        config = self.valid_config()
        config["STUDIO_TEXT_API_KEY_ENV"] = "CUSTOM_TEXT_KEY"
        config["CUSTOM_TEXT_KEY"] = "custom-provider-secret-with-at-least-32-chars"
        report = check_production_config(config, required=True)
        self.assertFalse(report["ok"])
        self.assertIn("STUDIO_TEXT_API_KEY_ENV:must-be-STUDIO_TEXT_API_KEY", report["errors"])

    def test_production_speech_voice_override_has_a_bounded_shape(self):
        config = self.valid_config()
        config["STUDIO_SPEECH_ALLOWED_VOICES"] = "cedar,cedar"
        duplicate = check_production_config(config, required=True)
        self.assertFalse(duplicate["ok"])
        self.assertIn("STUDIO_SPEECH_ALLOWED_VOICES:duplicate-voice", duplicate["errors"])

        config["STUDIO_SPEECH_ALLOWED_VOICES"] = "cedar,not a voice"
        invalid = check_production_config(config, required=True)
        self.assertFalse(invalid["ok"])
        self.assertIn("STUDIO_SPEECH_ALLOWED_VOICES:invalid-list", invalid["errors"])

        config["STUDIO_SPEECH_ALLOWED_VOICES"] = "cedar,custom-voice"
        unknown = check_production_config(config, required=True)
        self.assertFalse(unknown["ok"])
        self.assertIn("STUDIO_SPEECH_ALLOWED_VOICES:unknown-voice", unknown["errors"])

    def test_production_host_and_cors_wildcards_are_rejected(self):
        config = self.valid_config()
        config["STUDIO_ALLOWED_HOSTS"] = "*"
        host_report = check_production_config(config, required=True)
        self.assertFalse(host_report["ok"])
        self.assertIn("STUDIO_ALLOWED_HOSTS:wildcard-not-allowed", host_report["errors"])

        config = self.valid_config()
        config["STUDIO_CORS_ORIGINS"] = "null"
        cors_report = check_production_config(config, required=True)
        self.assertFalse(cors_report["ok"])
        self.assertIn("STUDIO_CORS_ORIGINS:wildcard-or-null-not-allowed", cors_report["errors"])

    def test_production_web_api_origin_defaults_to_same_origin_or_requires_https(self):
        config = self.valid_config()
        same_origin = check_production_config(config, required=True)
        self.assertTrue(same_origin["ok"], same_origin["errors"])

        config["NEXT_PUBLIC_API_BASE"] = "http://api.example.test"
        insecure = check_production_config(config, required=True)
        self.assertFalse(insecure["ok"])
        self.assertIn("NEXT_PUBLIC_API_BASE:must-use-https-or-empty", insecure["errors"])

        config["NEXT_PUBLIC_API_BASE"] = "https://api.example.test"
        explicit_https = check_production_config(config, required=True)
        self.assertTrue(explicit_https["ok"], explicit_https["errors"])

        config["NEXT_PUBLIC_API_BASE"] = "https://user:password@api.example.test"
        credentialed = check_production_config(config, required=True)
        self.assertFalse(credentialed["ok"])
        self.assertIn("NEXT_PUBLIC_API_BASE:must-use-https-or-empty", credentialed["errors"])
        self.assertNotIn("password", str(credentialed))

    def test_public_edge_host_must_match_trusted_host_and_https_cors_origin(self):
        config = self.valid_config()
        config["STUDIO_PUBLIC_HOST"] = "studio.example.test"
        configured = check_production_config(config, required=True)
        self.assertTrue(configured["ok"], configured["errors"])

        config["STUDIO_ALLOWED_HOSTS"] = "other.example.test,localhost,127.0.0.1"
        host_mismatch = check_production_config(config, required=True)
        self.assertFalse(host_mismatch["ok"])
        self.assertIn("STUDIO_PUBLIC_HOST:must-be-in-STUDIO_ALLOWED_HOSTS", host_mismatch["errors"])

        config = self.valid_config()
        config["STUDIO_PUBLIC_HOST"] = "https://studio.example.test"
        invalid_host = check_production_config(config, required=True)
        self.assertFalse(invalid_host["ok"])
        self.assertIn("STUDIO_PUBLIC_HOST:must-be-hostname", invalid_host["errors"])

        config = self.valid_config()
        config["STUDIO_CORS_ORIGINS"] = "http://studio.example.test"
        insecure_cors = check_production_config(config, required=True)
        self.assertFalse(insecure_cors["ok"])
        self.assertIn("STUDIO_CORS_ORIGINS:must-be-https-origin", insecure_cors["errors"])

    def test_non_production_config_is_not_mistaken_for_a_production_pass(self):
        report = check_production_config({"STUDIO_ENV": "development"})
        self.assertTrue(report["ok"])
        self.assertFalse(report["required"])
        self.assertEqual(report["state"], "not-production")

    def test_force_mode_checks_production_contract(self):
        report = check_production_config({}, force=True, required=True)
        self.assertFalse(report["ok"])
        self.assertTrue(report["required"])
        self.assertIn("STUDIO_DATABASE_URL:missing", report["errors"])

    def test_production_checkout_url_is_required_and_credential_free(self):
        config = self.valid_config()
        config.pop("STUDIO_BILLING_CHECKOUT_URL")
        missing = check_production_config(config, required=True)
        self.assertFalse(missing["ok"])
        self.assertIn("STUDIO_BILLING_CHECKOUT_URL:missing", missing["errors"])
        config["STUDIO_BILLING_CHECKOUT_URL"] = "http://pay.example.test/checkout"
        insecure = check_production_config(config, required=True)
        self.assertFalse(insecure["ok"])
        self.assertIn("STUDIO_BILLING_CHECKOUT_URL:must-use-https", insecure["errors"])
        config["STUDIO_BILLING_CHECKOUT_URL"] = "https://user:password@pay.example.test/checkout"
        invalid = check_production_config(config, required=True)
        self.assertFalse(invalid["ok"])
        self.assertIn("STUDIO_BILLING_CHECKOUT_URL:invalid-url", invalid["errors"])
        self.assertNotIn("password", str(invalid))

    def test_stripe_production_checkout_contract_is_accepted(self):
        config = self.valid_config()
        config.pop("STUDIO_BILLING_WEBHOOK_SECRET")
        config.pop("STUDIO_BILLING_CHECKOUT_URL")
        config.update(
            {
                "STUDIO_BILLING_PROVIDER": "stripe",
                "STRIPE_SECRET_KEY": "stripe-secret-with-at-least-32-characters-123",
                "STRIPE_WEBHOOK_SECRET": "stripe-webhook-secret-with-at-least-32-chars",
                "STRIPE_API_BASE_URL": "https://api.stripe.com",
                "STUDIO_BILLING_SUCCESS_URL": "https://studio.example.test/billing/success",
                "STUDIO_BILLING_CANCEL_URL": "https://studio.example.test/billing/cancel",
            }
        )
        report = check_production_config(config, force=True, required=True)
        self.assertTrue(report["ok"], report["errors"])

    def test_stripe_production_checkout_requires_secret_and_https_return_urls(self):
        config = self.valid_config()
        config.pop("STUDIO_BILLING_WEBHOOK_SECRET")
        config.pop("STUDIO_BILLING_CHECKOUT_URL")
        config.update(
            {
                "STUDIO_BILLING_PROVIDER": "stripe",
                "STRIPE_SECRET_KEY": "short",
                "STRIPE_WEBHOOK_SECRET": "short",
                "STUDIO_BILLING_SUCCESS_URL": "http://studio.example.test/billing/success",
                "STUDIO_BILLING_CANCEL_URL": "",
            }
        )
        report = check_production_config(config, force=True, required=True)
        self.assertIn("STRIPE_SECRET_KEY:too-short", report["errors"])
        self.assertIn("STRIPE_WEBHOOK_SECRET:too-short", report["errors"])
        self.assertIn("STUDIO_BILLING_SUCCESS_URL:must-use-https", report["errors"])
        self.assertIn("STUDIO_BILLING_CANCEL_URL:missing", report["errors"])

    def test_ci_only_stripe_http_endpoint_requires_explicit_ci_mode(self):
        config = self.valid_config()
        config.pop("STUDIO_BILLING_WEBHOOK_SECRET")
        config.pop("STUDIO_BILLING_CHECKOUT_URL")
        config.update(
            {
                "CI": "true",
                "STUDIO_BILLING_CI_MODE": "true",
                "STUDIO_BILLING_PROVIDER": "stripe",
                "STRIPE_SECRET_KEY": "stripe-ci-secret-with-at-least-32-characters-123",
                "STRIPE_WEBHOOK_SECRET": "stripe-ci-webhook-with-at-least-32-characters-123",
                "STRIPE_API_BASE_URL": "http://mock-stripe:8090",
                "STUDIO_BILLING_SUCCESS_URL": "https://studio.example.test/billing/success",
                "STUDIO_BILLING_CANCEL_URL": "https://studio.example.test/billing/cancel",
            }
        )
        accepted = check_production_config(config, required=True)
        self.assertTrue(accepted["ok"], accepted["errors"])

        config.pop("STUDIO_BILLING_CI_MODE")
        rejected = check_production_config(config, required=True)
        self.assertFalse(rejected["ok"])
        self.assertIn("STRIPE_API_BASE_URL:must-use-https-and-no-credentials", rejected["errors"])

        config["STUDIO_BILLING_CI_MODE"] = "true"
        config["CI"] = "false"
        rejected_without_ci = check_production_config(config, required=True)
        self.assertFalse(rejected_without_ci["ok"])
        self.assertIn("STUDIO_BILLING_CI_MODE:requires-CI", rejected_without_ci["errors"])

    def test_workflow_file_gate_checks_mounted_files_without_returning_paths(self):
        config = self.valid_config()
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "image.json"
            video = Path(directory) / "video.json"
            image.write_text(
                '{"1":{"class_type":"SaveImage","inputs":{"prompt":"{{PROMPT}}"}}}',
                encoding="utf-8",
            )
            video.write_text(
                '{"1":{"class_type":"SaveVideo","inputs":{"filename":"{{ASSET_ID}}"}}}',
                encoding="utf-8",
            )
            config["COMFYUI_IMAGE_WORKFLOW"] = str(image)
            config["COMFYUI_VIDEO_WORKFLOW"] = str(video)
            report = check_production_config(config, required=True, check_workflow_files=True)
            self.assertTrue(report["ok"])
            config["COMFYUI_VIDEO_WORKFLOW"] = str(Path(directory) / "missing.json")
            failed = check_production_config(config, required=True, check_workflow_files=True)
        self.assertFalse(failed["ok"])
        self.assertIn("COMFYUI_VIDEO_WORKFLOW:file-not-found", failed["errors"])
        self.assertNotIn("missing.json", str(failed))

    def test_workflow_file_gate_accepts_separate_h3_t2v_and_r2v_files(self):
        config = self.valid_config()
        config.pop("COMFYUI_VIDEO_WORKFLOW")
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "image.json"
            t2v = Path(directory) / "h3-t2v.json"
            r2v = Path(directory) / "h3-r2v.json"
            image.write_text('{"1":{"class_type":"SaveImage","inputs":{"prompt":"{{PROMPT}}"}}}', encoding="utf-8")
            t2v.write_text('{"1":{"class_type":"SaveVideo","inputs":{"filename":"{{ASSET_ID}}","prompt":"{{PROMPT}}"}}}', encoding="utf-8")
            r2v.write_text('{"1":{"class_type":"SaveVideo","inputs":{"filename":"{{ASSET_ID}}","image":"{{IMAGE_REF}}"}}}', encoding="utf-8")
            config["COMFYUI_IMAGE_WORKFLOW"] = str(image)
            config["COMFYUI_T2V_VIDEO_WORKFLOW"] = str(t2v)
            config["COMFYUI_R2V_VIDEO_WORKFLOW"] = str(r2v)
            report = check_production_config(config, required=True, check_workflow_files=True)
        self.assertTrue(report["ok"])
        self.assertNotIn("COMFYUI_VIDEO_WORKFLOW:missing", report["errors"])

    def test_workflow_file_gate_rejects_invalid_json_and_empty_objects(self):
        config = self.valid_config()
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "image.json"
            video = Path(directory) / "video.json"
            image.write_text("not-json", encoding="utf-8")
            video.write_text("{}", encoding="utf-8")
            config["COMFYUI_IMAGE_WORKFLOW"] = str(image)
            config["COMFYUI_VIDEO_WORKFLOW"] = str(video)
            report = check_production_config(config, required=True, check_workflow_files=True)
        self.assertFalse(report["ok"])
        self.assertIn("COMFYUI_IMAGE_WORKFLOW:invalid-json", report["errors"])
        self.assertIn("COMFYUI_VIDEO_WORKFLOW:must-be-non-empty-object", report["errors"])
        self.assertNotIn("not-json", str(report))


if __name__ == "__main__":
    unittest.main()
