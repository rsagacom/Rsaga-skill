#!/usr/bin/env python3
"""执行脱敏的 PostgreSQL schema 验收。

默认先复用 ``PostgresStore.initialize()``，再只读检查生产 API 依赖的表、列、
关键索引、Job 触发器和迁移 SHA-256 账本。脚本不会打印 DSN、用户名、密码、
环境变量值或业务数据；``--check-only`` 可用于只读核对已经完成迁移的数据库，
``--data-smoke`` 会在单个事务中验证 JSONB、Job 事件/状态保护和关键帧唯一索引，
最后回滚探针数据。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import uuid4

# 允许按 Runbook 的方式从仓库根目录直接执行 ``python scripts/...py``。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from studio_api.store import PostgresStore


REQUIRED_TABLES = frozenset(
    {
        "users",
        "sessions",
        "projects",
        "source_documents",
        "source_segments",
        "adaptation_units",
        "adaptation_revisions",
        "characters",
        "story_entities",
        "story_relationships",
        "story_bible_runs",
        "character_references",
        "episodes",
        "shots",
        "image_prompts",
        "assets",
        "asset_reviews",
        "asset_review_decisions",
        "jobs",
        "job_events",
        "credit_accounts",
        "credit_reservations",
        "credit_transactions",
        "billing_orders",
        "billing_webhook_events",
        "billing_adjustments",
        "compositions",
        "composition_settings",
        "studio_settings",
        "narrative_units",
        "scenes",
        "studio_schema_migrations",
    }
)

REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    # 这些列来自 migrations/*.sql 的完整持久化合同，而不是只挑选当前
    # smoke test 会访问的列。这样迁移成功但遗漏服务字段时，schema gate
    # 会在 API/Worker 启动前失败。
    "users": frozenset({"id", "email", "password_hash", "password_salt", "created_at"}),
    "sessions": frozenset({"token_hash", "user_id", "expires_at", "created_at", "device_label", "last_seen_at"}),
    "projects": frozenset({"id", "user_id", "title", "story", "style", "episode_length", "source_document_id", "status", "created_at", "updated_at", "idempotency_key"}),
    "source_documents": frozenset({"id", "project_id", "filename", "media_type", "content_sha256", "text", "copyright_acknowledged", "created_at", "idempotency_key"}),
    "source_segments": frozenset({"id", "project_id", "source_document_id", "chapter_no", "sequence", "text", "start_offset", "end_offset", "line_start", "line_end"}),
    "adaptation_units": frozenset({"id", "project_id", "source_segment_id", "chapter_no", "sequence", "source_text", "adapted_text", "mode", "status", "traceability_json"}),
    "adaptation_revisions": frozenset({"id", "adaptation_unit_id", "project_id", "version", "mode", "source_text", "adapted_text", "diff_json", "provider", "metadata_json", "created_at"}),
    "characters": frozenset({"id", "project_id", "name", "role", "description", "visual_lock_json", "status"}),
    "story_entities": frozenset({"id", "project_id", "kind", "name", "description", "attributes_json", "source_segment_ids_json", "status", "created_at", "updated_at"}),
    "story_relationships": frozenset({"id", "project_id", "source_type", "source_id", "target_type", "target_id", "relation", "description", "source_segment_ids_json", "status", "created_at", "updated_at"}),
    "story_bible_runs": frozenset({"id", "project_id", "provider", "model", "source_sha256", "output_json", "status", "error", "created_at"}),
    "character_references": frozenset({"id", "character_id", "front_url", "side_url", "back_url", "provider", "model", "status"}),
    "episodes": frozenset({"id", "project_id", "number", "title", "summary", "conflict", "hook", "target_duration_seconds", "status"}),
    "shots": frozenset({"id", "episode_id", "sequence", "scene", "emotion", "duration_seconds", "description", "adaptation_unit_ids_json", "image_prompt_id", "adaptation_revision", "status"}),
    "image_prompts": frozenset({"id", "shot_id", "prompt", "negative_prompt", "provider", "model"}),
    "assets": frozenset({"id", "project_id", "shot_id", "character_id", "kind", "status", "url", "selected", "consistency_confirmed", "source_asset_id", "provider", "model", "metadata_json", "created_at", "updated_at"}),
    "asset_reviews": frozenset({"id", "asset_id", "status", "issues_json", "raw_text", "provider", "model", "created_at"}),
    "asset_review_decisions": frozenset({"id", "user_id", "asset_id", "idempotency_key", "status", "issues_json", "review_id", "created_at"}),
    "jobs": frozenset({"id", "user_id", "kind", "target_id", "status", "cost_credits", "attempts", "max_attempts", "error", "provider", "idempotency_key", "created_at", "updated_at", "payload_json", "progress_percent", "progress_message"}),
    "job_events": frozenset({"id", "job_id", "user_id", "from_status", "to_status", "event_type", "message", "attempt", "created_at"}),
    "credit_accounts": frozenset({"user_id", "balance", "updated_at"}),
    "credit_reservations": frozenset({"id", "user_id", "job_id", "amount", "status", "created_at"}),
    "credit_transactions": frozenset({"id", "user_id", "job_id", "kind", "amount", "balance_after", "reason", "created_at"}),
    "billing_orders": frozenset({"id", "user_id", "package_code", "credits", "amount_cents", "currency", "provider", "provider_order_id", "provider_payment_id", "idempotency_key", "status", "metadata_json", "created_at", "paid_at"}),
    "billing_webhook_events": frozenset({"event_id", "provider", "order_id", "payload_sha256", "received_at"}),
    "billing_adjustments": frozenset({"id", "provider", "order_id", "adjustment_key", "kind", "amount_cents", "currency", "credits_delta", "event_id", "created_at"}),
    "compositions": frozenset({"id", "episode_id", "playlist_url", "final_video_url", "status", "metadata_json", "created_at"}),
    "composition_settings": frozenset({"episode_id", "audio_tracks_json", "subtitles_json", "narration_text", "updated_at"}),
    "studio_settings": frozenset({"key", "value_json"}),
    "narrative_units": frozenset({"id", "project_id", "episode_id", "goal", "enter_state", "exit_state", "target_duration_seconds", "scene_ids_json", "shot_ids_json", "created_at", "updated_at"}),
    "scenes": frozenset({"id", "project_id", "episode_id", "unit_id", "location_id", "name", "time_of_day", "weather", "summary", "shot_ids_json", "created_at", "updated_at"}),
    "studio_schema_migrations": frozenset({"migration_name", "checksum", "applied_at"}),
}

# 这些索引都由 migrations/*.sql 明确命名创建。它们既承载高频用户/项目查询，
# 也承载关键数据不变量；只检查表和列会让“迁移账本已完成、索引实际缺失”的
# 半完成数据库错误地通过 readiness。
REQUIRED_INDEXES = frozenset(
    {
        "idx_projects_user_updated",
        "idx_sessions_user_expiry",
        "idx_segments_project_order",
        "idx_units_project_order",
        "idx_adaptation_revisions_unit",
        "idx_job_events_job",
        "idx_jobs_user_created",
        "idx_transactions_user_created",
        "idx_assets_shot_created",
        "idx_asset_reviews_asset_created",
        "idx_asset_review_decisions_asset",
        "idx_billing_orders_user_created",
        "idx_billing_orders_provider_order",
        "idx_billing_orders_provider_payment",
        "idx_billing_adjustments_order_created",
        "idx_assets_single_selected_image",
        "idx_source_documents_project_idempotency",
        "idx_projects_user_idempotency",
        "idx_story_entities_project_kind",
        "idx_story_relationships_project",
        "idx_story_bible_runs_project_created",
        "idx_narrative_units_project_episode",
        "idx_scenes_project_episode",
    }
)
REQUIRED_TRIGGERS = frozenset({"trg_jobs_record_event", "trg_jobs_validate_status"})


def _value(row: Any, key: str, position: int = 0) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return row[position]


def _migration_checksums(migration_dir: Path | None = None) -> dict[str, str]:
    root = migration_dir or Path(__file__).resolve().parent.parent / "migrations"
    return {
        migration.name: PostgresStore._migration_checksum(migration.read_text(encoding="utf-8"))
        for migration in sorted(root.glob("*.sql"))
    }


def inspect_schema(connection: Any) -> dict[str, Any]:
    """只读取 PostgreSQL catalog，不读取业务表内容。"""

    tables = {
        str(_value(row, "table_name"))
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        ).fetchall()
    }
    columns: dict[str, set[str]] = {}
    for row in connection.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public'"
    ).fetchall():
        table = str(_value(row, "table_name"))
        columns.setdefault(table, set()).add(str(_value(row, "column_name", 1)))
    indexes = {
        str(_value(row, "indexname"))
        for row in connection.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
        ).fetchall()
    }
    triggers = {
        str(_value(row, "tgname"))
        for row in connection.execute(
            "SELECT t.tgname FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND NOT t.tgisinternal"
        ).fetchall()
    }
    migrations = {
        str(_value(row, "migration_name")): str(_value(row, "checksum", 1))
        for row in connection.execute(
            "SELECT migration_name, checksum FROM studio_schema_migrations "
            "ORDER BY migration_name"
        ).fetchall()
    }
    return {
        "tables": tables,
        "columns": columns,
        "indexes": indexes,
        "triggers": triggers,
        "migrations": migrations,
    }


def validate_schema(snapshot: Mapping[str, Any], expected_migrations: Mapping[str, str]) -> dict[str, Any]:
    tables = set(snapshot.get("tables", set()))
    columns = snapshot.get("columns", {})
    indexes = set(snapshot.get("indexes", set()))
    triggers = set(snapshot.get("triggers", set()))
    applied_migrations = dict(snapshot.get("migrations", {}))

    missing_columns = {
        table: sorted(required - set(columns.get(table, set())))
        for table, required in REQUIRED_COLUMNS.items()
        if required - set(columns.get(table, set()))
    }
    missing_migrations = sorted(set(expected_migrations) - set(applied_migrations))
    unexpected_migrations = sorted(set(applied_migrations) - set(expected_migrations))
    changed_migrations = sorted(
        name
        for name in set(expected_migrations) & set(applied_migrations)
        if applied_migrations[name] != expected_migrations[name]
    )
    report = {
        "ok": not (
            REQUIRED_TABLES - tables
            or missing_columns
            or REQUIRED_INDEXES - indexes
            or REQUIRED_TRIGGERS - triggers
            or missing_migrations
            or unexpected_migrations
            or changed_migrations
        ),
        "missing_tables": sorted(REQUIRED_TABLES - tables),
        "missing_columns": missing_columns,
        "missing_indexes": sorted(REQUIRED_INDEXES - indexes),
        "missing_triggers": sorted(REQUIRED_TRIGGERS - triggers),
        "migrations": {
            "expected_count": len(expected_migrations),
            "applied_count": len(applied_migrations),
            "missing": missing_migrations,
            "unexpected": unexpected_migrations,
            "changed": changed_migrations,
        },
    }
    return report


def database_target(database_url: str) -> str:
    """只返回 host:port，避免把 DSN 中的凭据带入报告。"""

    parsed = urlparse(database_url)
    if parsed.hostname:
        try:
            port = parsed.port or 5432
        except ValueError:
            return f"{parsed.hostname}:configured-port"
        return f"{parsed.hostname}:{port}"
    return "configured-dsn"


def run_data_smoke(store: PostgresStore) -> dict[str, Any]:
    """在单个可回滚事务中验证关键 PostgreSQL 数据合同。"""

    connection = store._connect()
    timestamp = datetime.now(timezone.utc).isoformat()
    suffix = uuid4().hex
    user_id = f"schema-smoke-user-{suffix}"
    project_id = f"schema-smoke-project-{suffix}"
    document_id = f"schema-smoke-document-{suffix}"
    episode_id = f"schema-smoke-episode-{suffix}"
    shot_id = f"schema-smoke-shot-{suffix}"
    job_id = f"schema-smoke-job-{suffix}"
    image_id = f"schema-smoke-image-{suffix}"
    duplicate_image_id = f"schema-smoke-image-duplicate-{suffix}"
    review_decision_id = f"schema-smoke-review-decision-{suffix}"
    review_id = f"schema-smoke-review-{suffix}"
    manual_review_idempotency_key = f"schema-smoke-manual-review-key-{suffix}"
    source_idempotency_key = f"schema-smoke-source-key-{suffix}"
    duplicate_document_id = f"schema-smoke-document-duplicate-{suffix}"
    project_idempotency_key = f"schema-smoke-project-key-{suffix}"
    duplicate_project_id = f"schema-smoke-project-duplicate-{suffix}"
    checks: dict[str, bool] = {}
    try:
        connection.execute("BEGIN")
        connection.execute(
            "INSERT INTO users(id, email, password_hash, password_salt, created_at) VALUES (%s, %s, NULL, NULL, %s)",
            (user_id, f"{user_id}@example.invalid", timestamp),
        )
        connection.execute(
            "INSERT INTO projects(id, user_id, title, story, style, episode_length, source_document_id, idempotency_key, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, 'draft', %s, %s)",
            (project_id, user_id, "schema smoke", "probe", "test", "1min", project_idempotency_key, timestamp, timestamp),
        )
        project_row = connection.execute(
            "SELECT idempotency_key FROM projects WHERE id = %s", (project_id,)
        ).fetchone()
        checks["project_idempotency_roundtrip"] = bool(project_row and project_row["idempotency_key"])
        connection.execute("SAVEPOINT project_idempotency_probe")
        try:
            connection.execute(
                "INSERT INTO projects(id, user_id, title, story, style, episode_length, source_document_id, idempotency_key, status, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, 'draft', %s, %s)",
                (duplicate_project_id, user_id, "schema smoke duplicate", "probe", "test", "1min", project_idempotency_key, timestamp, timestamp),
            )
        except Exception as exc:
            connection.execute("ROLLBACK TO SAVEPOINT project_idempotency_probe")
            checks["project_idempotency_unique_index"] = getattr(exc, "sqlstate", None) == "23505"
        else:
            checks["project_idempotency_unique_index"] = False
        connection.execute("RELEASE SAVEPOINT project_idempotency_probe")
        connection.execute(
            "INSERT INTO source_documents(id, project_id, filename, media_type, content_sha256, text, copyright_acknowledged, idempotency_key, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s)",
            (document_id, project_id, "schema-smoke.txt", "text/plain", "0" * 64, "probe。", source_idempotency_key, timestamp),
        )
        source_row = connection.execute(
            "SELECT idempotency_key FROM source_documents WHERE id = %s", (document_id,)
        ).fetchone()
        checks["source_idempotency_roundtrip"] = bool(source_row and source_row["idempotency_key"])
        connection.execute("SAVEPOINT source_idempotency_probe")
        try:
            connection.execute(
                "INSERT INTO source_documents(id, project_id, filename, media_type, content_sha256, text, copyright_acknowledged, idempotency_key, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s)",
                (duplicate_document_id, project_id, "schema-smoke-duplicate.txt", "text/plain", "1" * 64, "probe duplicate。", source_idempotency_key, timestamp),
            )
        except Exception as exc:
            connection.execute("ROLLBACK TO SAVEPOINT source_idempotency_probe")
            checks["source_idempotency_unique_index"] = getattr(exc, "sqlstate", None) == "23505"
        else:
            checks["source_idempotency_unique_index"] = False
        connection.execute("RELEASE SAVEPOINT source_idempotency_probe")
        connection.execute("UPDATE projects SET source_document_id = %s WHERE id = %s", (document_id, project_id))
        connection.execute(
            "INSERT INTO episodes(id, project_id, number, title, summary, conflict, hook, target_duration_seconds, status) "
            "VALUES (%s, %s, 1, %s, '', '', '', 60, 'draft')",
            (episode_id, project_id, "schema smoke"),
        )
        connection.execute(
            "INSERT INTO shots(id, episode_id, sequence, scene, emotion, duration_seconds, description, adaptation_unit_ids_json, image_prompt_id) "
            "VALUES (%s, %s, 1, '', '', 3, %s, %s::jsonb, NULL)",
            (shot_id, episode_id, "probe", "[]"),
        )
        connection.execute(
            "INSERT INTO jobs(id, user_id, kind, target_id, status, cost_credits, provider, created_at, updated_at) "
            "VALUES (%s, %s, 'image', %s, 'queued', 0, 'schema-smoke', %s, %s)",
            (job_id, user_id, image_id, timestamp, timestamp),
        )
        created_events = connection.execute(
            "SELECT COUNT(*) AS count FROM job_events WHERE job_id = %s", (job_id,)
        ).fetchone()
        checks["job_created_event"] = int(created_events["count"]) == 1
        connection.execute("UPDATE jobs SET status = 'running', updated_at = %s WHERE id = %s", (timestamp, job_id))
        connection.execute("UPDATE jobs SET status = 'completed', updated_at = %s WHERE id = %s", (timestamp, job_id))
        completed_events = connection.execute(
            "SELECT COUNT(*) AS count FROM job_events WHERE job_id = %s", (job_id,)
        ).fetchone()
        checks["job_status_events"] = int(completed_events["count"]) == 3

        metadata = json.dumps({"probe": True, "source": "postgres-schema-check"})
        connection.execute(
            "INSERT INTO assets(id, project_id, shot_id, kind, status, selected, consistency_confirmed, provider, model, metadata_json, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'image', 'ready', TRUE, FALSE, 'schema-smoke', 'probe', %s::jsonb, %s, %s)",
            (image_id, project_id, shot_id, metadata, timestamp, timestamp),
        )
        metadata_row = connection.execute("SELECT metadata_json FROM assets WHERE id = %s", (image_id,)).fetchone()
        metadata_value = metadata_row["metadata_json"] if metadata_row else None
        if isinstance(metadata_value, str):
            metadata_value = json.loads(metadata_value)
        checks["jsonb_roundtrip"] = isinstance(metadata_value, dict) and metadata_value.get("probe") is True
        connection.execute(
            "INSERT INTO asset_review_decisions(id, user_id, asset_id, idempotency_key, status, issues_json, review_id, created_at) "
            "VALUES (%s, %s, %s, %s, 'PASS', %s::jsonb, %s, %s)",
            (review_decision_id, user_id, image_id, manual_review_idempotency_key, "[]", review_id, timestamp),
        )
        decision_row = connection.execute(
            "SELECT idempotency_key FROM asset_review_decisions WHERE id = %s", (review_decision_id,)
        ).fetchone()
        checks["manual_review_idempotency_roundtrip"] = bool(decision_row and decision_row["idempotency_key"])
        connection.execute("SAVEPOINT manual_review_idempotency_probe")
        try:
            connection.execute(
                "INSERT INTO asset_review_decisions(id, user_id, asset_id, idempotency_key, status, issues_json, review_id, created_at) "
                "VALUES (%s, %s, %s, %s, 'PASS', %s::jsonb, %s, %s)",
                (f"{review_decision_id}-duplicate", user_id, image_id, manual_review_idempotency_key, "[]", f"{review_id}-duplicate", timestamp),
            )
        except Exception as exc:
            connection.execute("ROLLBACK TO SAVEPOINT manual_review_idempotency_probe")
            checks["manual_review_idempotency_unique"] = getattr(exc, "sqlstate", None) == "23505"
        else:
            checks["manual_review_idempotency_unique"] = False
        connection.execute("RELEASE SAVEPOINT manual_review_idempotency_probe")
        connection.execute(
            "INSERT INTO composition_settings(episode_id, audio_tracks_json, subtitles_json, updated_at) VALUES (%s, %s::jsonb, %s::jsonb, %s)",
            (episode_id, "[]", json.dumps([{"start_seconds": 0, "end_seconds": 1, "text": "probe"}]), timestamp),
        )
        settings_row = connection.execute(
            "SELECT audio_tracks_json, subtitles_json FROM composition_settings WHERE episode_id = %s", (episode_id,)
        ).fetchone()
        subtitle_value = settings_row["subtitles_json"] if settings_row else None
        if isinstance(subtitle_value, str):
            subtitle_value = json.loads(subtitle_value)
        checks["composition_jsonb_roundtrip"] = isinstance(subtitle_value, list) and len(subtitle_value) == 1

        connection.execute("SAVEPOINT selected_unique_probe")
        try:
            connection.execute(
                "INSERT INTO assets(id, project_id, shot_id, kind, status, selected, consistency_confirmed, provider, model, metadata_json, created_at, updated_at) "
                "VALUES (%s, %s, %s, 'image', 'ready', TRUE, FALSE, 'schema-smoke', 'probe', '{}'::jsonb, %s, %s)",
                (duplicate_image_id, project_id, shot_id, timestamp, timestamp),
            )
        except Exception as exc:
            connection.execute("ROLLBACK TO SAVEPOINT selected_unique_probe")
            checks["selected_image_unique_index"] = getattr(exc, "sqlstate", None) == "23505"
        else:
            checks["selected_image_unique_index"] = False
        connection.execute("RELEASE SAVEPOINT selected_unique_probe")

        connection.execute("SAVEPOINT invalid_status_probe")
        try:
            connection.execute("UPDATE jobs SET status = 'queued' WHERE id = %s", (job_id,))
        except Exception as exc:
            connection.execute("ROLLBACK TO SAVEPOINT invalid_status_probe")
            checks["job_status_guard"] = getattr(exc, "sqlstate", None) == "23514"
        else:
            checks["job_status_guard"] = False
        connection.execute("RELEASE SAVEPOINT invalid_status_probe")
        connection.rollback()
        return {"ok": all(checks.values()), "checks": checks, "rolled_back": True}
    except Exception as exc:  # pragma: no cover - exercised against real DB
        try:
            connection.rollback()
        finally:
            connection.close()
        return {"ok": False, "checks": checks, "rolled_back": True, "error": f"data-operation-failed:{type(exc).__name__}"}
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _print_report(report: Mapping[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return
    status = "PASS" if report.get("ok") else "FAIL"
    print(f"postgres schema gate: {status}")
    print(f"database target: {report.get('database_target', 'unknown')}")
    migrations = report.get("migrations", {})
    print(f"migrations: {migrations.get('applied_count', 0)}/{migrations.get('expected_count', 0)}")
    for key in ("missing_tables", "missing_columns", "missing_indexes", "missing_triggers"):
        value = report.get(key)
        if value:
            print(f"{key}: {value}")
    for key in ("missing", "unexpected", "changed"):
        value = migrations.get(key)
        if value:
            print(f"migrations.{key}: {value}")
    if report.get("error"):
        print(f"error: {report['error']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("STUDIO_DATABASE_URL", ""),
        help="PostgreSQL DSN；默认读取 STUDIO_DATABASE_URL，不会打印其值",
    )
    parser.add_argument("--runtime-dir", default=os.environ.get("STUDIO_RUNTIME_DIR", "/app/runtime"))
    parser.add_argument("--check-only", action="store_true", help="只读核对，不执行迁移")
    parser.add_argument("--data-smoke", action="store_true", help="在事务中验证 JSONB、Job 触发器和唯一索引，结束时回滚")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("STUDIO_DATABASE_URL or --database-url is required")

    report: dict[str, Any] = {"database_target": database_target(args.database_url)}
    try:
        store = PostgresStore(args.database_url, args.runtime_dir)
        if not args.check_only:
            store.initialize()
        with store.connection() as connection:
            report.update(
                validate_schema(
                    inspect_schema(connection),
                    _migration_checksums(),
                )
            )
        if args.data_smoke and report.get("ok"):
            data_report = run_data_smoke(store)
            report["data_smoke"] = data_report
            report["ok"] = bool(report.get("ok") and data_report.get("ok"))
    except ImportError:
        report.update({"ok": False, "error": "postgres-driver-not-installed"})
    except Exception as exc:  # pragma: no cover - exercised against real DB
        report.update({"ok": False, "error": f"database-operation-failed:{type(exc).__name__}"})

    _print_report(report, args.json)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
