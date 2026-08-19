import json
from pathlib import Path
import unittest

from scripts.compose_static_check import check as check_compose
from studio_core.workflow_contract import validate_comfyui_api_workflow


ROOT = Path(__file__).resolve().parents[1]


class ProductionComposeContractTests(unittest.TestCase):
    def test_ci_override_is_explicitly_separate_from_production_compose(self):
        source = (ROOT / "infra" / "docker-compose.ci.yml").read_text(encoding="utf-8")
        self.assertIn("CI-only production integration override", source)
        self.assertIn("STUDIO_TEXT_PROVIDER: openai-compatible", source)
        self.assertIn("STUDIO_IMAGE_PROVIDER: comfyui", source)
        self.assertIn("mock-stripe:", source)
        self.assertIn("STUDIO_BILLING_PROVIDER: stripe", source)
        self.assertIn("STUDIO_BILLING_CI_MODE: \"true\"", source)
        self.assertIn("STRIPE_API_BASE_URL: http://mock-stripe:8090", source)
        self.assertIn("STUDIO_QUEUE_BACKEND", (ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8"))

    def test_production_web_defaults_to_same_origin_and_ci_has_explicit_local_origin(self):
        production = (ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "web" / "Dockerfile").read_text(encoding="utf-8")
        ci = (ROOT / "infra" / "docker-compose.ci.yml").read_text(encoding="utf-8")
        env_example = (ROOT / "infra" / ".env.example").read_text(encoding="utf-8")
        self.assertIn("NEXT_PUBLIC_API_BASE: ${NEXT_PUBLIC_API_BASE:-}", production)
        self.assertIn("STUDIO_PUBLIC_HOST: ${STUDIO_PUBLIC_HOST:-}", production)
        self.assertIn("ARG NEXT_PUBLIC_API_BASE=", dockerfile)
        self.assertIn("NEXT_PUBLIC_API_BASE: http://localhost:8787", ci)
        self.assertIn("NEXT_PUBLIC_API_BASE=", env_example)
        self.assertIn("STUDIO_CORS_ORIGINS=https://studio.example.com", env_example)

    def test_web_source_has_production_same_origin_fallback(self):
        home = (ROOT / "web" / "app" / "page.tsx").read_text(encoding="utf-8")
        board = (ROOT / "web" / "app" / "board" / "[projectId]" / "page.tsx").read_text(encoding="utf-8")
        self.assertIn("process.env.NODE_ENV === \"production\" ? \"\"", home)
        self.assertIn("process.env.NODE_ENV === 'production' ? ''", board)

    def test_ci_workflows_pass_shared_workflow_contract(self):
        for name, role in (("ci-image.json", "image"), ("ci-video.json", "video")):
            payload = json.loads((ROOT / "workflows" / name).read_text(encoding="utf-8"))
            self.assertEqual(validate_comfyui_api_workflow(payload, role=role), [])

    def test_ci_workflows_are_not_accidentally_ignored(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("!workflows/ci-image.json", gitignore)
        self.assertIn("!workflows/ci-video.json", gitignore)

    def test_base_static_compose_check_still_passes_with_override_present(self):
        self.assertEqual(check_compose(ROOT), [])

    def test_production_edge_overlay_routes_api_media_and_web_without_public_internal_ports(self):
        production = (ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
        edge = (ROOT / "infra" / "docker-compose.edge.yml").read_text(encoding="utf-8")
        caddyfile = (ROOT / "infra" / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:8787:8787"', production)
        self.assertIn('"127.0.0.1:8790:8790"', production)
        self.assertIn('"127.0.0.1:3000:3000"', production)
        self.assertIn('"80:80"', edge)
        self.assertIn('"443:443"', edge)
        self.assertIn("reverse_proxy api:8787", caddyfile)
        self.assertIn("reverse_proxy web:3000", caddyfile)
        self.assertIn("/assets /assets/*", caddyfile)
        self.assertIn("/_edge_api_health", caddyfile)
        self.assertIn("rewrite * /api/health", caddyfile)
        self.assertIn("/_edge_web_health", caddyfile)
        self.assertIn("/_edge_api_health", edge)
        self.assertIn("/_edge_web_health", edge)

    def test_production_edge_overlay_requires_non_secret_domain_and_acme_email(self):
        edge = (ROOT / "infra" / "docker-compose.edge.yml").read_text(encoding="utf-8")
        env_example = (ROOT / "infra" / ".env.example").read_text(encoding="utf-8")
        self.assertIn("STUDIO_PUBLIC_HOST: ${STUDIO_PUBLIC_HOST:?", edge)
        self.assertIn("ACME_EMAIL: ${ACME_EMAIL:?", edge)
        self.assertIn("STUDIO_PUBLIC_HOST=studio.example.com", env_example)
        self.assertIn("ACME_EMAIL=ops@example.com", env_example)

    def test_quality_compose_check_includes_edge_overlay(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertGreaterEqual(makefile.count("infra/docker-compose.edge.yml"), 2)
        self.assertIn("STUDIO_PUBLIC_HOST=studio.example.com ACME_EMAIL=ops@example.com", makefile)
        self.assertIn("edge-smoke:", makefile)
        self.assertIn("scripts/edge_runtime_smoke.py", makefile)
        self.assertIn("production-preflight:", makefile)
        self.assertIn("production-edge-preflight: production-preflight edge-smoke", makefile)
        self.assertIn("PROBE_DATA_SERVICES", makefile)
        self.assertIn("--probe-data-services", makefile)
        self.assertIn("--require-production-config", makefile)
        self.assertIn("--require-container-runtime", makefile)

    def test_ci_runs_production_configured_compose_smoke(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("production-compose-smoke:", workflow)
        self.assertIn("-f infra/docker-compose.ci.yml", workflow)
        self.assertIn("--expect-store postgres", workflow)
        self.assertIn("--expect-queue-backend bullmq", workflow)
        self.assertIn("--expect-storage s3", workflow)
        self.assertIn("--include-av", workflow)
        self.assertIn("--include-billing", workflow)
        self.assertIn("STRIPE_WEBHOOK_SECRET", workflow)
        self.assertIn("STUDIO_SMOKE_STRIPE_WEBHOOK_SECRET", workflow)
        self.assertIn("production-compose-smoke", workflow)
        self.assertIn("Validate production Caddy edge config", workflow)
        self.assertIn("caddy:2.11.4-alpine", workflow)

    def test_runbook_and_readme_bound_mock_smoke_to_ci_only(self):
        runbook = (ROOT / "docs" / "PRODUCTION_RUNBOOK.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("production-compose-smoke", runbook)
        self.assertIn("CI-only mock", runbook)
        self.assertIn("production-compose-smoke", readme)
        self.assertIn("mock Provider", readme)
        self.assertIn("docker-compose.edge.yml", runbook)
        self.assertIn("Caddy", runbook)
        self.assertIn("API `/api/health` 与 Web upstream", runbook)
        self.assertIn("make edge-smoke", runbook)
        self.assertIn("docker-compose.edge.yml", readme)
        self.assertIn("Edge healthcheck", readme)
        self.assertIn("make edge-smoke", readme)
        workflow_doc = (ROOT / "workflows" / "README.md").read_text(encoding="utf-8")
        self.assertIn("ci-image.json", workflow_doc)
        self.assertIn("CI mock workflow", workflow_doc)


if __name__ == "__main__":
    unittest.main()
