from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from studio_api.store import PostgresConnection, PostgresStore
from scripts.compose_static_check import check as check_compose_static


class ComposeContractTests(unittest.TestCase):
    def test_standalone_static_compose_checker_passes(self):
        self.assertEqual(check_compose_static(), [])

    def test_development_compose_isolated_local_preview_stack(self):
        compose = (Path(__file__).parents[1] / "infra" / "docker-compose.dev.yml").read_text(encoding="utf-8")
        self.assertIn("STUDIO_ENV: development", compose)
        self.assertIn("STUDIO_STORE: sqlite", compose)
        self.assertIn("STUDIO_QUEUE_BACKEND: local", compose)
        self.assertIn("STUDIO_STORAGE: local", compose)
        self.assertIn("STUDIO_BILLING_PROVIDER: disabled", compose)
        self.assertIn("STUDIO_TEXT_PROVIDER: local", compose)
        self.assertIn("STUDIO_IMAGE_PROVIDER: local", compose)
        self.assertIn("STUDIO_VIDEO_PROVIDER: local", compose)
        self.assertIn("STUDIO_VISION_PROVIDER: local", compose)
        self.assertIn("STUDIO_SPEECH_ALLOWED_VOICES", compose)
        self.assertIn("STUDIO_INTERNAL_TOKEN: ${STUDIO_INTERNAL_TOKEN:-local-dev-internal-token}", compose)
        self.assertIn("manhua-dev-runtime:/app/runtime", compose)
        self.assertIn("NEXT_PUBLIC_API_BASE: http://localhost:8787", compose)
        self.assertIn("condition: service_healthy", compose)
        self.assertNotIn("postgres:", compose)
        self.assertNotIn("redis:", compose)
        self.assertNotIn("minio:", compose)

    def test_no_container_local_preview_smoke_owns_build_runtime_and_processes(self):
        script = (Path(__file__).parents[1] / "scripts" / "local_preview_smoke.sh").read_text(encoding="utf-8")
        self.assertIn("NEXT_PUBLIC_API_BASE=\"${API_URL}\" npm run build", script)
        self.assertIn("NEXT_PUBLIC_API_BASE=\"${API_URL}\" npm start", script)
        self.assertIn('STUDIO_RUNTIME_DIR=\"${RUNTIME_DIR}\"', script)
        self.assertIn('STUDIO_INTERNAL_TOKEN=\"${INTERNAL_TOKEN}\"', script)
        self.assertIn('STUDIO_SMOKE_INTERNAL_TOKEN=\"${INTERNAL_TOKEN}\"', script)
        self.assertIn("--include-source-files", script)
        self.assertIn("--include-av", script)
        self.assertIn("--include-billing", script)
        self.assertIn("trap finish EXIT", script)
        self.assertIn("PIDS=()", script)
        self.assertIn("STUDIO_LOCAL_SMOKE_BROWSER", script)
        self.assertNotIn("rm -rf", script)
        self.assertNotIn("git reset", script)

    def test_ci_runs_real_development_compose_smoke(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("container-smoke:", workflow)
        self.assertIn("docker compose -f infra/docker-compose.dev.yml up -d --build", workflow)
        self.assertIn("python scripts/stack_smoke.py", workflow)
        self.assertIn("docker compose -f infra/docker-compose.dev.yml down -v --remove-orphans", workflow)

    def test_ci_production_compose_smoke_runs_source_file_contract(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        production = workflow.split("  production-compose-smoke:\n", 1)[1]
        self.assertIn("--expect-store postgres", production)
        self.assertIn("--expect-queue-backend bullmq", production)
        self.assertIn("--expect-storage s3", production)
        self.assertIn("--include-source-files", production)

    def test_ci_runs_real_browser_adaptation_smoke(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("browser-smoke:", workflow)
        self.assertIn("Install Chromium for Playwright CLI", workflow)
        self.assertIn("scripts/browser_adaptation_smoke.sh", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)

    def test_ci_runs_all_source_file_browser_smokes_and_uploads_one_artifact(self):
        root = Path(__file__).parents[1]
        workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for script in (
            "scripts/browser_source_file_import_smoke.sh",
            "scripts/browser_docx_source_file_smoke.sh",
            "scripts/browser_epub_source_file_smoke.sh",
            "scripts/browser_pdf_source_file_smoke.sh",
        ):
            self.assertIn(script, workflow)
        for evidence_dir in (
            "browser-source-file-import-smoke",
            "browser-docx-source-file-smoke",
            "browser-epub-source-file-smoke",
            "browser-pdf-source-file-smoke",
        ):
            self.assertIn(f"output/playwright/{evidence_dir}", workflow)
        self.assertIn("name: browser-source-file-smokes", workflow)
        self.assertIn("STUDIO_PYTHON_BIN: python3", workflow)

    def test_ci_runs_real_browser_job_progress_smoke_with_local_worker(self):
        root = Path(__file__).parents[1]
        workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        compose = (root / "infra" / "docker-compose.dev.yml").read_text(encoding="utf-8")
        script = (root / "scripts" / "browser_job_progress_smoke.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/browser_job_progress_smoke.sh", workflow)
        self.assertIn("browser-job-progress-smoke", workflow)
        self.assertIn("  worker:", compose)
        self.assertIn('"studio_api.worker"', compose)
        self.assertIn("?queued=true", script)
        self.assertIn("browser_job_progress_id", script)
        self.assertIn("aria-valuenow", script)
        self.assertIn("pw reload", script)

    def test_ci_runs_strict_real_browser_full_pipeline_smoke(self):
        root = Path(__file__).parents[1]
        workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        script = (root / "scripts" / "browser_full_pipeline_smoke.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/browser_full_pipeline_smoke.sh", workflow)
        self.assertIn("browser-full-pipeline-smoke", workflow)
        self.assertIn("register/create/import/rewrite/story-bible/structure/character-reference/keyframe/review/video/compose", script)
        self.assertIn("11-backend-audit.json", script)
        self.assertIn("audit.selected !== 3", script)
        self.assertIn("audit.video_ready !== 3", script)
        self.assertIn("打开成片 ↗", script)
        self.assertIn('getByTestId("project-readiness")', script)
        self.assertIn('getByText("打开成片 ↗", { exact: true })', script)
        self.assertIn('getByRole("button", { name: "退出账户" })', script)
        self.assertIn('getByRole("button", { name: "重新抽取故事资产" }).first()', script)
        self.assertIn('getByRole("button", { name: /批量通过/ }).first()', script)
        self.assertIn("04b-adaptation-approved.yml", script)
        self.assertIn('getByRole("button", { name: "一键准备项目结构" }).first()', script)
        self.assertIn('getByRole("button", { name: "批量生成三视图" }).first()', script)
        self.assertIn("### Error", script)
        self.assertNotIn("pw eval", script)

    def test_ci_runs_real_browser_character_reference_upload_smoke(self):
        root = Path(__file__).parents[1]
        workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        script = (root / "scripts" / "browser_character_reference_upload_smoke.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/browser_character_reference_upload_smoke.sh", workflow)
        self.assertIn("browser-character-reference-upload-smoke", workflow)
        self.assertIn("setInputFiles", script)
        self.assertIn("upload-front-side-back", script)
        self.assertIn("credit_unchanged", script)
        self.assertIn('fill("")', script)
        self.assertNotIn("pw eval", script)

    def test_ci_runs_real_browser_narration_smoke(self):
        root = Path(__file__).parents[1]
        workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        script = (root / "scripts" / "browser_narration_smoke.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/browser_narration_smoke.sh", workflow)
        self.assertIn("browser-narration-smoke", workflow)
        self.assertIn("data-narration-button", script)
        self.assertIn("subtitle_lines", script)
        self.assertIn('STUDIO_SMOKE_EXPECTED_VOICE_COUNT', script)
        self.assertIn('availableVoices.length !== expectedVoiceCount', script)
        self.assertIn('result.voice !== smokeVoice', script)
        self.assertIn('STUDIO_SMOKE_SPEED', script)
        self.assertIn('STUDIO_SMOKE_INSTRUCTIONS', script)
        self.assertIn('result.speed !== smokeSpeed', script)
        self.assertIn('result.instructions_configured !== Boolean(smokeInstructions.trim())', script)
        self.assertIn("credit_delta", script)
        self.assertIn("url_suffix !== \"wav\"", script)
        self.assertNotIn("pw eval", script)

    def test_full_browser_pipeline_evidence_contract_does_not_store_temp_credentials(self):
        script = (Path(__file__).parents[1] / "scripts" / "browser_full_pipeline_smoke.sh").read_text(encoding="utf-8")
        self.assertIn('fill("")', script)
        self.assertIn("browser-full-pipeline-", script)
        self.assertIn("FullPipeline-", script)
        self.assertIn("credentials: \"include\"", script)

    def test_browser_adaptation_smoke_preserves_ui_evidence_without_credentials(self):
        script = (Path(__file__).parents[1] / "scripts" / "browser_adaptation_smoke.sh").read_text(encoding="utf-8")
        self.assertIn("pw snapshot >", script)
        self.assertIn("pw run-code", script)
        self.assertIn("pw reload", script)
        self.assertIn("批量通过", script)
        self.assertIn("fill(\"\")", script)
        self.assertNotIn("pw eval", script)

    def test_ci_runs_real_postgres_migration_and_schema_data_smoke(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("postgres-schema-smoke:", workflow)
        self.assertIn("image: postgres:16.4", workflow)
        self.assertIn("python -m studio_api.migrate", workflow)
        self.assertIn("python scripts/postgres_schema_check.py --check-only --data-smoke --json", workflow)

    def test_orchestrator_healthcheck_satisfies_api_dependency(self):
        compose = (Path(__file__).parents[1] / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
        orchestrator = compose.split("\n  orchestrator:\n", 1)[1].split("\n  orchestrator-worker:\n", 1)[0]
        api = compose.split("\n  api:\n", 1)[1].split("\n  orchestrator:\n", 1)[0]
        self.assertIn("healthcheck:", orchestrator)
        self.assertIn("127.0.0.1:8790/health", orchestrator)
        self.assertIn("orchestrator:", api)
        self.assertIn("condition: service_healthy", api)
        self.assertIn('ORCHESTRATOR_REQUIRE_AUTH: "true"', orchestrator)
        self.assertIn("STUDIO_ENV: production", orchestrator)
        self.assertIn("ORCHESTRATOR_TOKEN", orchestrator)
        self.assertIn("ORCHESTRATOR_MAX_BODY_BYTES", orchestrator)

    def test_job_event_migration_and_sqlite_trigger_contract(self):
        root = Path(__file__).parents[1]
        migration = (root / "migrations" / "003_job_events_postgresql.sql").read_text(encoding="utf-8")
        sqlite_store = (root / "studio_api" / "store.py").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS job_events", migration)
        self.assertIn("CREATE OR REPLACE FUNCTION studio_record_job_event", migration)
        self.assertIn("AFTER INSERT OR UPDATE OF status ON jobs", migration)
        self.assertIn("trg_jobs_insert_event", sqlite_store)
        self.assertIn("trg_jobs_status_event", sqlite_store)

    def test_job_status_transition_guard_contract(self):
        root = Path(__file__).parents[1]
        migration = (root / "migrations" / "004_job_status_transitions_postgresql.sql").read_text(encoding="utf-8")
        sqlite_store = (root / "studio_api" / "store.py").read_text(encoding="utf-8")
        workflow = (root / "studio_core" / "workflow.py").read_text(encoding="utf-8")
        service = (root / "studio_api" / "service.py").read_text(encoding="utf-8")
        self.assertIn("trg_jobs_validate_status", migration)
        self.assertIn("trg_jobs_validate_status", sqlite_store)
        self.assertIn("persisted_transition_allowed", workflow)
        self.assertIn("唯一的服务层 Job 状态写入口", service)

    def test_job_progress_migration_and_documentation_contract(self):
        root = Path(__file__).parents[1]
        migration = (root / "migrations" / "013_job_progress_postgresql.sql").read_text(encoding="utf-8")
        schema_check = (root / "scripts" / "postgres_schema_check.py").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")
        blueprint = (root / "docs" / "AI_MANHUA_STUDIO_BLUEPRINT.md").read_text(encoding="utf-8")
        runbook = (root / "docs" / "PRODUCTION_RUNBOOK.md").read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS progress_percent", migration)
        self.assertIn("ADD COLUMN IF NOT EXISTS progress_message", migration)
        self.assertIn("jobs_progress_percent_range", migration)
        self.assertIn("CHECK (progress_percent >= 0 AND progress_percent <= 100)", migration)
        self.assertIn('"progress_percent"', schema_check)
        self.assertIn('"progress_message"', schema_check)
        self.assertIn("progress_percent", readme)
        self.assertIn("progress_percent", blueprint)
        self.assertIn("progress_percent", runbook)

    def test_composition_settings_migration_contract(self):
        root = Path(__file__).parents[1]
        migration = (root / "migrations" / "005_composition_settings_postgresql.sql").read_text(encoding="utf-8")
        sqlite_store = (root / "studio_api" / "store.py").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS composition_settings", migration)
        self.assertIn("audio_tracks_json jsonb", migration)
        self.assertIn("subtitles_json jsonb", migration)
        self.assertIn("narration_text text", migration)
        narration_migration = (root / "migrations" / "017_narration_draft_postgresql.sql").read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS narration_text", narration_migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS composition_settings", sqlite_store)
        self.assertIn("ALTER TABLE composition_settings", sqlite_store)

    def test_selected_keyframe_invariant_exists_in_both_stores(self):
        root = Path(__file__).parents[1]
        migration = (root / "migrations" / "006_selected_keyframe_invariant_postgresql.sql").read_text(encoding="utf-8")
        sqlite_store = (root / "studio_api" / "store.py").read_text(encoding="utf-8")
        self.assertIn("ROW_NUMBER() OVER", migration)
        self.assertIn("consistency_confirmed = FALSE", migration)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_single_selected_image", migration)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_single_selected_image", sqlite_store)
        self.assertIn("SET selected = 0, consistency_confirmed = 0", sqlite_store)

    def test_api_image_contains_optional_remotion_runtime_contract(self):
        root = Path(__file__).parents[1]
        dockerfile = (root / "infra" / "api.Dockerfile").read_text(encoding="utf-8")
        compose = (root / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
        env_example = (root / "infra" / ".env.example").read_text(encoding="utf-8")
        self.assertIn("FROM node:22-bookworm-slim AS node-runtime", dockerfile)
        self.assertIn("COPY rendering ./rendering", dockerfile)
        self.assertIn("npm ci --omit=dev", dockerfile)
        self.assertIn("ensureBrowser", dockerfile)
        self.assertIn("STUDIO_COMPOSE_ENGINE", compose)
        self.assertIn("STUDIO_REMOTION_ROOT: /app/rendering", compose)
        self.assertIn("STUDIO_REMOTION_BROWSER_EXECUTABLE", compose)
        self.assertIn("STUDIO_COMPOSE_ENGINE=ffmpeg", env_example)

    def test_api_receives_provider_secrets_and_read_only_workflows_contract(self):
        root = Path(__file__).parents[1]
        compose = (root / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
        env_example = (root / "infra" / ".env.example").read_text(encoding="utf-8")
        workflow_doc = (root / "workflows" / "README.md").read_text(encoding="utf-8")
        gitignore = (root / ".gitignore").read_text(encoding="utf-8")
        api = compose.split("\n  api:\n", 1)[1].split("\n  migrate:\n", 1)[0]
        self.assertIn("../workflows:/app/workflows:ro", api)
        self.assertIn("STUDIO_TEXT_API_KEY: ${STUDIO_TEXT_API_KEY:-}", api)
        self.assertIn("STUDIO_VISION_API_KEY: ${STUDIO_VISION_API_KEY:-}", api)
        self.assertIn("STUDIO_SPEECH_API_KEY_ENV", api)
        self.assertIn("OPENAI_API_KEY: ${OPENAI_API_KEY:-}", api)
        self.assertIn("STUDIO_ALLOWED_PROVIDER_BASE_URLS", api)
        self.assertIn("OTEL_EXPORTER_OTLP_HEADERS", api)
        self.assertIn("/app/workflows", workflow_doc)
        self.assertIn("workflows/*.json", gitignore)
        self.assertIn("STUDIO_TEXT_API_KEY_ENV", env_example)
        self.assertIn("STUDIO_SPEECH_MODEL", env_example)
        self.assertIn("STUDIO_SPEECH_ALLOWED_VOICES", env_example)
        self.assertIn("OTEL_EXPORTER_OTLP_HEADERS", env_example)

    def test_production_app_containers_are_non_root_and_hardened(self):
        root = Path(__file__).parents[1]
        compose = (root / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
        for service in ("config-check", "api", "migrate", "schema-check", "orchestrator", "orchestrator-worker", "web"):
            section = compose.split(f"\n  {service}:\n", 1)[1]
            next_services = [name for name in ("config-check", "api", "migrate", "schema-check", "orchestrator", "orchestrator-worker", "web", "volumes") if name != service]
            end = len(section)
            for next_service in next_services:
                marker = f"\n  {next_service}:\n"
                if marker in section:
                    end = min(end, section.index(marker))
            section = section[:end]
            self.assertIn('user: "10001:10001"', section, service)
            self.assertIn("read_only: true", section, service)
            self.assertIn("no-new-privileges:true", section, service)
            self.assertIn("/tmp:rw,noexec,nosuid", section, service)
        for dockerfile, user in (
            (root / "infra" / "api.Dockerfile", "USER studio:studio"),
            (root / "web" / "Dockerfile", "USER nextjs:nextjs"),
            (root / "orchestrator" / "Dockerfile", "USER studio:studio"),
        ):
            contents = dockerfile.read_text(encoding="utf-8")
            self.assertIn(user, contents)
            self.assertIn("10001", contents)

    def test_production_images_have_explicit_upgradeable_version_baselines(self):
        root = Path(__file__).parents[1]
        compose = (root / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
        env_example = (root / "infra" / ".env.example").read_text(encoding="utf-8")
        self.assertNotIn(":latest", compose)
        for variable in ("POSTGRES_IMAGE", "REDIS_IMAGE", "MINIO_IMAGE", "MINIO_MC_IMAGE"):
            self.assertIn(variable, compose)
            self.assertIn(variable, env_example)
        self.assertIn("RELEASE.2025-04-22T22-12-26Z", compose)
        self.assertIn("RELEASE.2025-04-16T18-13-26Z", compose)

    def test_production_config_gate_blocks_default_credentials_before_dependents(self):
        root = Path(__file__).parents[1]
        compose = (root / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
        checker = (root / "scripts" / "production_config_check.py").read_text(encoding="utf-8")
        api = compose.split("\n  api:\n", 1)[1].split("\n  migrate:\n", 1)[0]
        self.assertIn("config-check:", compose)
        self.assertIn('command: ["python", "scripts/production_config_check.py", "--require-production", "--require-workflow-files", "--json"]', compose)
        self.assertIn("condition: service_completed_successfully", compose.split("  api:\n", 1)[1].split("  migrate:\n", 1)[0])
        self.assertIn("condition: service_completed_successfully", compose.split("  migrate:\n", 1)[1].split("  schema-check:\n", 1)[0])
        self.assertIn("known-placeholder", checker)
        self.assertIn("STUDIO_ALLOW_LOCAL_TOP_UP:must-be-false", checker)
        self.assertIn("STUDIO_BILLING_PROVIDER:must-be-signed-webhook-or-stripe", checker)
        self.assertIn("STUDIO_BILLING_WEBHOOK_SECRET", checker)
        self.assertIn("STUDIO_BILLING_CHECKOUT_URL", checker)
        self.assertIn("STRIPE_SECRET_KEY", checker)
        self.assertIn("STUDIO_BILLING_SUCCESS_URL", api)
        self.assertIn("STUDIO_BILLING_CHECKOUT_URL", api)
        self.assertIn("def require_provider", checker)
        self.assertIn('require_provider("STUDIO_TEXT_PROVIDER"', checker)
        self.assertIn('require_provider("STUDIO_IMAGE_PROVIDER"', checker)
        self.assertIn("STUDIO_TEXT_API_KEY_ENV", checker)
        self.assertIn("--require-workflow-files", checker)

    def test_minio_bucket_stays_private_and_media_is_served_by_api(self):
        root = Path(__file__).parents[1]
        compose = (root / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
        storage = (root / "studio_api" / "storage.py").read_text(encoding="utf-8")
        self.assertNotIn("mc anonymous set", compose)
        self.assertNotIn("STUDIO_S3_PUBLIC_BASE_URL", compose)
        self.assertIn('return f"/assets/{PurePosixPath(key).name}"', storage)

    def test_existing_postgres_volumes_have_a_migration_job_before_api(self):
        root = Path(__file__).parents[1]
        compose = (root / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
        migrate = compose.split("\n  migrate:\n", 1)[1].split("\n  orchestrator:\n", 1)[0]
        api = compose.split("\n  api:\n", 1)[1].split("\n  migrate:\n", 1)[0]
        migration_module = (root / "studio_api" / "migrate.py").read_text(encoding="utf-8")
        self.assertIn('command: ["python", "-m", "studio_api.migrate"]', migrate)
        self.assertIn("condition: service_healthy", migrate)
        self.assertIn("migrate:", api)
        self.assertIn("condition: service_completed_successfully", api)
        self.assertIn("PostgresStore(args.database_url, args.runtime_dir).initialize()", migration_module)

    def test_postgres_schema_gate_runs_after_migration_before_api(self):
        root = Path(__file__).parents[1]
        compose = (root / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
        schema_check = compose.split("\n  schema-check:\n", 1)[1].split("\n  orchestrator:\n", 1)[0]
        api = compose.split("\n  api:\n", 1)[1].split("\n  migrate:\n", 1)[0]
        script = (root / "scripts" / "postgres_schema_check.py").read_text(encoding="utf-8")
        self.assertIn('command: ["python", "scripts/postgres_schema_check.py", "--check-only", "--data-smoke", "--json"]', schema_check)
        self.assertIn("migrate:", schema_check)
        self.assertIn("condition: service_completed_successfully", schema_check)
        self.assertIn("schema-check:", api)
        self.assertIn("studio_schema_migrations", script)
        self.assertIn("不会打印 DSN", script)
        self.assertIn("ROLLBACK TO SAVEPOINT", script)

    def test_postgres_migrations_are_serialized_across_api_and_migration_job(self):
        store = (Path(__file__).parents[1] / "studio_api" / "store.py").read_text(encoding="utf-8")
        self.assertIn("pg_advisory_lock(hashtext('ai-manhua-studio-migrations'))", store)
        self.assertIn("pg_advisory_unlock(hashtext('ai-manhua-studio-migrations'))", store)
        self.assertIn("session-level advisory lock", store)
        self.assertIn("studio_schema_migrations", store)
        self.assertIn("migration files are immutable after being applied", store)

    def test_postgres_initialize_records_and_skips_unchanged_migrations(self):
        class Cursor:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class FakeConnection:
            def __init__(self):
                self.calls = []
                self.applied = {}

            def execute(self, sql, params=()):
                self.calls.append((sql, params))
                if "SELECT checksum FROM studio_schema_migrations" in sql:
                    return Cursor(
                        {"checksum": self.applied.get(params[0])}
                        if params[0] in self.applied
                        else None
                    )
                if "INSERT INTO studio_schema_migrations" in sql:
                    self.applied[params[0]] = params[1]
                return Cursor(None)

            def commit(self):
                self.calls.append(("COMMIT", ()))

            def rollback(self):
                self.calls.append(("ROLLBACK", ()))

            def close(self):
                pass

        raw = FakeConnection()
        with TemporaryDirectory() as runtime_dir, patch.object(PostgresStore, "_connect", return_value=raw):
            with patch.object(PostgresStore, "_migration_files", return_value=[]):
                PostgresStore("postgresql://redacted", runtime_dir).initialize()

            migration = Path(runtime_dir) / "001_test.sql"
            migration.write_text("CREATE TABLE test_table (id text PRIMARY KEY);\n", encoding="utf-8")
            with patch.object(PostgresStore, "_migration_files", return_value=[migration]):
                PostgresStore("postgresql://redacted", runtime_dir).initialize()
                first_apply_count = sum("CREATE TABLE test_table" in sql for sql, _ in raw.calls)
                PostgresStore("postgresql://redacted", runtime_dir).initialize()
                second_apply_count = sum("CREATE TABLE test_table" in sql for sql, _ in raw.calls)

        self.assertEqual(first_apply_count, 1)
        self.assertEqual(second_apply_count, 1)
        self.assertIn("001_test.sql", raw.applied)

    def test_postgres_initialize_rejects_changed_applied_migration(self):
        class Cursor:
            def fetchone(self):
                return {"checksum": "old-checksum"}

        class FakeConnection:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=()):
                self.calls.append((sql, params))
                if "SELECT checksum FROM studio_schema_migrations" in sql:
                    return Cursor()
                return Cursor()

            def commit(self):
                self.calls.append(("COMMIT", ()))

            def rollback(self):
                self.calls.append(("ROLLBACK", ()))

            def close(self):
                pass

        raw = FakeConnection()
        with TemporaryDirectory() as runtime_dir:
            migration = Path(runtime_dir) / "001_test.sql"
            migration.write_text("CREATE TABLE changed_table (id text PRIMARY KEY);\n", encoding="utf-8")
            with patch.object(PostgresStore, "_connect", return_value=raw), patch.object(
                PostgresStore, "_migration_files", return_value=[migration]
            ):
                with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                    PostgresStore("postgresql://redacted", runtime_dir).initialize()

        self.assertTrue(any("pg_advisory_unlock" in sql for sql, _ in raw.calls))

    def test_postgres_initialize_releases_migration_lock_after_all_files(self):
        class FakeConnection:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=()):
                self.calls.append(sql)
                result = MagicMock()
                result.fetchone.return_value = None
                return result

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        raw = FakeConnection()
        with TemporaryDirectory() as runtime_dir, patch.object(PostgresStore, "_connect", return_value=raw):
            PostgresStore("postgresql://redacted", runtime_dir).initialize()
        self.assertTrue(raw.calls)
        self.assertIn("pg_advisory_lock(hashtext('ai-manhua-studio-migrations'))", raw.calls[0])
        self.assertIn("pg_advisory_unlock(hashtext('ai-manhua-studio-migrations'))", raw.calls[-1])

    def test_postgres_jsonb_adapter_covers_audio_timeline_and_reviews(self):
        try:
            from psycopg.types.json import Jsonb
        except ImportError:
            self.skipTest("optional psycopg is not installed")
        connection = MagicMock()
        wrapped = PostgresConnection(connection)
        wrapped.execute(
            "INSERT INTO composition_settings(episode_id, audio_tracks_json, subtitles_json) VALUES (?, ?, ?)",
            ("episode-1", "[]", "[]"),
        )
        params = connection.execute.call_args.args[1]
        self.assertIsInstance(params[1], Jsonb)
        self.assertIsInstance(params[2], Jsonb)

        wrapped.execute(
            "INSERT INTO asset_reviews(asset_id, issues_json) VALUES (?, ?)",
            ("asset-1", "[]"),
        )
        review_params = connection.execute.call_args.args[1]
        self.assertIsInstance(review_params[1], Jsonb)


if __name__ == "__main__":
    unittest.main()
