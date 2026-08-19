import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.runtime_preflight import (
    authenticated_dependency_probe,
    build_report,
    command_check,
    container_runtime_check,
    env_presence,
    gpu_runtime_check,
    http_check,
    parse_args,
    probe_postgres,
    probe_redis,
    probe_s3,
)


class RuntimePreflightTests(unittest.TestCase):
    def test_environment_report_never_contains_secret_values(self):
        report = env_presence({"STUDIO_DATABASE_URL": "postgresql://user:secret@example.test/db"})
        self.assertEqual(report["STUDIO_DATABASE_URL"], {"configured": True, "required": False})
        self.assertNotIn("secret", str(report))

    def test_missing_command_is_reported_without_failing_report_mode(self):
        result = command_check("command-that-does-not-exist-for-preflight")
        self.assertFalse(result["available"])
        self.assertFalse(result["required"])

    def test_container_runtime_reports_missing_daemon_without_secret_output(self):
        with patch("scripts.runtime_preflight.shutil.which", return_value=None):
            result = container_runtime_check(required=True)
        self.assertFalse(result["ok"])
        self.assertTrue(result["required"])
        self.assertEqual(result["error"], "docker-or-podman-not-found")
        self.assertNotIn("DOCKER", str(result))

    def test_gpu_runtime_reports_nvidia_memory_without_command_output(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": "12288\n", "stderr": ""})()
        with patch("scripts.runtime_preflight.shutil.which", return_value="/usr/bin/nvidia-smi"), patch(
            "scripts.runtime_preflight.subprocess.run", return_value=completed
        ) as run:
            result = gpu_runtime_check(required=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["required"])
        self.assertEqual(result["device_count"], 1)
        self.assertEqual(result["memory_total_mb"], [12288])
        self.assertNotIn("nvidia-smi", str(result))
        self.assertEqual(run.call_args.args[0][1], "--query-gpu=memory.total")

    def test_gpu_runtime_missing_driver_is_a_safe_failure(self):
        with patch("scripts.runtime_preflight.shutil.which", return_value=None):
            result = gpu_runtime_check(required=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "nvidia-smi-not-found")

    def test_comfyui_health_rejects_non_object_json(self):
        class Response:
            status = 200

            def read(self, _limit):
                return b"[]"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch("scripts.runtime_preflight.urllib.request.urlopen", return_value=Response()):
            result = http_check("comfyui", "http://comfyui.example.test:8188", "/system_stats", required=True, require_json_object=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid-json-object")

    def test_build_report_applies_json_health_contract_to_comfyui(self):
        args = parse_args(["--require-comfyui"])
        calls = []

        def fake_http(*call_args, **call_kwargs):
            calls.append((call_args, call_kwargs))
            return {"ok": True, "required": call_kwargs["required"], "state": "ok"}

        with patch("scripts.runtime_preflight.http_check", side_effect=fake_http), patch(
            "scripts.runtime_preflight.tcp_check", return_value={"ok": True, "required": False, "state": "ok"}
        ):
            build_report(args, {"STUDIO_IMAGE_PROVIDER": "comfyui", "COMFYUI_BASE_URL": "http://comfyui:8188"})
        comfy_calls = [kwargs for call_args, kwargs in calls if call_args and call_args[0] == "comfyui"]
        self.assertEqual(len(comfy_calls), 1)
        self.assertTrue(comfy_calls[0]["require_json_object"])

    def test_require_live_marks_explicit_dependencies_as_required(self):
        args = parse_args([
            "--require-live",
            "--require-postgres",
            "--require-redis",
            "--require-minio",
            "--require-orchestrator",
            "--skip-comfyui",
            "--orchestrator-url",
            "http://127.0.0.1:8790",
        ])
        ok = lambda *unused_args, **kwargs: {"ok": True, "required": kwargs["required"], "state": "ok"}
        with patch("scripts.runtime_preflight.http_check", side_effect=ok), patch("scripts.runtime_preflight.tcp_check", side_effect=ok):
            report = build_report(args, {"STUDIO_QUEUE_BACKEND": "bullmq", "STUDIO_STORE": "postgres", "STUDIO_STORAGE": "s3"})
        self.assertEqual(report["status"], "passed")
        for name in ("api_health", "api_ready", "web", "orchestrator", "postgres", "redis", "minio"):
            self.assertTrue(report["checks"][name]["required"], name)
        self.assertFalse(report["checks"]["comfyui"]["required"])

    def test_composition_engine_is_a_preflight_contract(self):
        args = parse_args(["--json", "--require-live"])
        with patch("scripts.runtime_preflight.shutil.which", side_effect=lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None):
            with patch("scripts.runtime_preflight.http_check", return_value={"ok": True, "required": True, "state": "ok"}):
                with patch("scripts.runtime_preflight.tcp_check", return_value={"ok": True, "required": True, "state": "ok"}):
                    report = build_report(args, {"STUDIO_COMPOSE_ENGINE": "ffmpeg"})
        self.assertTrue(report["checks"]["composition_engine"]["ok"])

    def test_remotion_preflight_rejects_missing_default_tsx_runner(self):
        args = parse_args(["--require-live"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "node_modules" / "@remotion" / "renderer").mkdir(parents=True)
            (root / "node_modules" / "@remotion" / "renderer" / "package.json").write_text("{}", encoding="utf-8")
            (root / "package.json").write_text('{"scripts":{"render":"tsx src/render.ts"}}', encoding="utf-8")
            which = lambda name: "/usr/bin/node" if name in {"node", "npm"} else None
            with patch("scripts.runtime_preflight.shutil.which", side_effect=which), patch(
                "scripts.runtime_preflight.http_check", return_value={"ok": True, "required": True, "state": "ok"}
            ), patch("scripts.runtime_preflight.tcp_check", return_value={"ok": True, "required": False, "state": "ok"}):
                report = build_report(
                    args,
                    {"STUDIO_COMPOSE_ENGINE": "remotion", "STUDIO_REMOTION_ROOT": str(root)},
                )
        self.assertFalse(report["checks"]["composition_engine"]["ok"])
        self.assertEqual(report["checks"]["composition_engine"]["error"], "remotion-runtime-incomplete")

    def test_remotion_preflight_rejects_malformed_custom_runner_command(self):
        args = parse_args(["--require-live"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "node_modules" / "@remotion" / "renderer").mkdir(parents=True)
            (root / "node_modules" / "@remotion" / "renderer" / "package.json").write_text("{}", encoding="utf-8")
            (root / "package.json").write_text("{}", encoding="utf-8")
            which = lambda name: "/usr/bin/node" if name in {"node", "npm"} else None
            with patch("scripts.runtime_preflight.shutil.which", side_effect=which), patch(
                "scripts.runtime_preflight.http_check", return_value={"ok": True, "required": True, "state": "ok"}
            ), patch("scripts.runtime_preflight.tcp_check", return_value={"ok": True, "required": False, "state": "ok"}):
                report = build_report(
                    args,
                    {
                        "STUDIO_COMPOSE_ENGINE": "remotion",
                        "STUDIO_REMOTION_ROOT": str(root),
                        "STUDIO_REMOTION_RENDER_COMMAND": "unterminated'",
                    },
                )
        self.assertFalse(report["checks"]["composition_engine"]["ok"])
        self.assertEqual(report["checks"]["composition_engine"]["error"], "remotion-runtime-incomplete")

    def test_remotion_preflight_accepts_executable_custom_runner_without_tsx(self):
        args = parse_args(["--require-live"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "node_modules" / "@remotion" / "renderer").mkdir(parents=True)
            (root / "node_modules" / "@remotion" / "renderer" / "package.json").write_text("{}", encoding="utf-8")
            (root / "package.json").write_text("{}", encoding="utf-8")
            runner = root / "render-worker"
            runner.write_text("#!/bin/sh\n", encoding="utf-8")
            runner.chmod(0o755)
            which = lambda name: "/usr/bin/node" if name in {"node", "npm"} else None
            with patch("scripts.runtime_preflight.shutil.which", side_effect=which), patch(
                "scripts.runtime_preflight.http_check", return_value={"ok": True, "required": True, "state": "ok"}
            ), patch("scripts.runtime_preflight.tcp_check", return_value={"ok": True, "required": False, "state": "ok"}):
                report = build_report(
                    args,
                    {
                        "STUDIO_COMPOSE_ENGINE": "remotion",
                        "STUDIO_REMOTION_ROOT": str(root),
                        "STUDIO_REMOTION_RENDER_COMMAND": str(runner),
                    },
                )
        self.assertTrue(report["checks"]["composition_engine"]["ok"])

    def test_invalid_http_target_is_safe_and_actionable(self):
        result = http_check("api", "not-a-url", "/api/health", required=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "invalid")
        self.assertEqual(result["error"], "invalid-url")

    def test_real_provider_configuration_is_a_required_gate_without_exposing_secret(self):
        args = parse_args(["--require-live", "--skip-comfyui"])
        report = build_report(
            args,
            {
                "STUDIO_TEXT_PROVIDER": "openai-compatible",
                "STUDIO_TEXT_BASE_URL": "https://text.example.test/v1",
                "STUDIO_TEXT_MODEL": "qwen-test",
                "STUDIO_TEXT_API_KEY_ENV": "STUDIO_TEXT_API_KEY",
            },
        )
        provider_check = report["checks"]["provider_configuration"]
        self.assertTrue(provider_check["required"])
        self.assertFalse(provider_check["ok"])
        self.assertIn("missing-api-key", provider_check["providers"]["text"]["errors"])
        self.assertNotIn("secret", str(report))

    def test_provider_configuration_accepts_secret_injected_by_named_environment(self):
        args = parse_args(["--require-provider-config"])
        report = build_report(
            args,
            {
                "STUDIO_TEXT_PROVIDER": "openai-compatible",
                "STUDIO_TEXT_BASE_URL": "https://text.example.test/v1",
                "STUDIO_TEXT_MODEL": "qwen-test",
                "STUDIO_TEXT_API_KEY_ENV": "TEXT_RUNTIME_KEY",
                "TEXT_RUNTIME_KEY": "secret-value-that-must-not-be-reported",
                "STUDIO_IMAGE_PROVIDER": "comfyui",
                "COMFYUI_BASE_URL": "http://comfyui.example.test:8188",
                "COMFYUI_IMAGE_WORKFLOW": "/app/workflows/image.json",
            },
        )
        provider_check = report["checks"]["provider_configuration"]
        self.assertTrue(provider_check["ok"])
        self.assertTrue(provider_check["required"])
        self.assertNotIn("secret-value-that-must-not-be-reported", str(report))

    def test_production_config_is_exposed_as_a_preflight_gate(self):
        args = parse_args(["--require-production-config"])
        report = build_report(args, {"STUDIO_ENV": "production"})
        production_check = report["checks"]["production_config"]
        self.assertTrue(production_check["required"])
        self.assertFalse(production_check["ok"])
        self.assertIn("POSTGRES_PASSWORD:missing", production_check["errors"])
        self.assertEqual(report["status"], "failed")

    def test_production_preflight_reuses_workflow_file_gate(self):
        args = parse_args(["--require-production-config"])
        gate_report = {"ok": True, "required": True, "state": "configured"}
        with patch("scripts.runtime_preflight.check_production_config", return_value=gate_report) as gate:
            build_report(args, {"STUDIO_ENV": "production"})
        self.assertTrue(gate.call_args.kwargs["check_workflow_files"])

    def test_authenticated_dependency_probe_is_opt_in_and_does_not_change_report_mode(self):
        report = build_report(parse_args([]), {})
        self.assertEqual(report["checks"]["authenticated_dependencies"]["state"], "not-requested")
        self.assertFalse(report["checks"]["authenticated_dependencies"]["required"])

    def test_authenticated_dependency_probe_reports_fixed_missing_configuration_errors(self):
        secret_dsn = "postgresql://user:secret-value@example.test/db"
        report = authenticated_dependency_probe(
            {"STUDIO_DATABASE_URL": secret_dsn},
            required=True,
        )
        probe = report["services"]
        self.assertFalse(report["ok"])
        self.assertEqual(probe["postgres"]["error"], "postgres-authenticated-probe-failed")
        self.assertEqual(probe["redis"]["error"], "missing-redis-url")
        self.assertEqual(probe["s3"]["error"], "missing-s3-bucket")
        self.assertNotIn("secret-value", str(report))

    def test_authenticated_dependency_probe_uses_existing_adapters(self):
        ok = {"ok": True, "required": True, "state": "authenticated"}
        with patch("scripts.runtime_preflight.probe_postgres", return_value=ok) as postgres:
            with patch("scripts.runtime_preflight.probe_redis", return_value=ok) as redis:
                with patch("scripts.runtime_preflight.probe_s3", return_value=ok) as s3:
                    report = build_report(
                        parse_args(["--probe-data-services"]),
                        {
                            "STUDIO_DATABASE_URL": "postgresql://redacted.example.test/db",
                            "STUDIO_RATE_LIMIT_REDIS_URL": "redis://redacted.example.test:6379",
                            "STUDIO_S3_ENDPOINT_URL": "http://minio.example.test:9000",
                            "STUDIO_S3_BUCKET": "manhua-assets",
                            "AWS_ACCESS_KEY_ID": "access-key-not-output",
                            "AWS_SECRET_ACCESS_KEY": "secret-key-not-output",
                        },
                    )
        self.assertTrue(report["checks"]["authenticated_dependencies"]["ok"])
        self.assertTrue(report["checks"]["authenticated_dependencies"]["required"])
        postgres.assert_called_once()
        redis.assert_called_once()
        s3.assert_called_once()
        self.assertNotIn("secret-key-not-output", str(report))

    def test_individual_probes_fail_closed_without_connection_configuration(self):
        self.assertEqual(probe_postgres("", required=True)["error"], "missing-database-url")
        self.assertEqual(probe_redis("", required=True)["error"], "missing-redis-url")
        self.assertEqual(
            probe_s3(
                endpoint_url="",
                bucket="",
                access_key_id="",
                secret_access_key="",
                region="us-east-1",
                required=True,
            )["error"],
            "missing-s3-bucket",
        )


if __name__ == "__main__":
    unittest.main()
