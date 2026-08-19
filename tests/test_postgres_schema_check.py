import re
from pathlib import Path
import unittest

from scripts.postgres_schema_check import (
    REQUIRED_COLUMNS,
    REQUIRED_INDEXES,
    REQUIRED_TABLES,
    REQUIRED_TRIGGERS,
    database_target,
    run_data_smoke,
    validate_schema,
)


class PostgresSchemaCheckTests(unittest.TestCase):
    def _complete_snapshot(self):
        return {
            "tables": set(REQUIRED_TABLES),
            "columns": {table: set(columns) for table, columns in REQUIRED_COLUMNS.items()},
            "indexes": set(REQUIRED_INDEXES),
            "triggers": set(REQUIRED_TRIGGERS),
            "migrations": {"001_initial_postgresql.sql": "checksum-1"},
        }

    def test_database_target_never_contains_dsn_credentials(self):
        self.assertEqual(
            database_target("postgresql://report-user:do-not-print@db.example:5433/studio"),
            "db.example:5433",
        )

    def test_complete_schema_and_migration_ledger_pass(self):
        report = validate_schema(self._complete_snapshot(), {"001_initial_postgresql.sql": "checksum-1"})
        self.assertTrue(report["ok"])
        self.assertEqual(report["migrations"]["missing"], [])

    def test_missing_production_contract_is_reported(self):
        snapshot = self._complete_snapshot()
        snapshot["columns"]["jobs"].remove("idempotency_key")
        snapshot["triggers"].remove("trg_jobs_validate_status")
        snapshot["migrations"]["001_initial_postgresql.sql"] = "changed"
        report = validate_schema(snapshot, {"001_initial_postgresql.sql": "checksum-1"})
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing_columns"]["jobs"], ["idempotency_key"])
        self.assertEqual(report["missing_triggers"], ["trg_jobs_validate_status"])
        self.assertEqual(report["migrations"]["changed"], ["001_initial_postgresql.sql"])

    def test_missing_named_migration_index_is_reported(self):
        snapshot = self._complete_snapshot()
        snapshot["indexes"].remove("idx_billing_orders_provider_order")
        report = validate_schema(snapshot, {"001_initial_postgresql.sql": "checksum-1"})
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing_indexes"], ["idx_billing_orders_provider_order"])

    def test_missing_narration_draft_column_is_reported(self):
        snapshot = self._complete_snapshot()
        snapshot["columns"]["composition_settings"].remove("narration_text")
        report = validate_schema(snapshot, {"001_initial_postgresql.sql": "checksum-1"})
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing_columns"]["composition_settings"], ["narration_text"])

    def test_missing_migration_backed_columns_are_reported_across_pipeline_stages(self):
        required = {
            "projects": "story",
            "source_documents": "filename",
            "adaptation_revisions": "adapted_text",
            "characters": "role",
            "episodes": "status",
            "shots": "adaptation_revision",
            "assets": "provider",
            "jobs": "cost_credits",
            "story_bible_runs": "error",
            "compositions": "status",
        }

        for table, column in required.items():
            with self.subTest(table=table, column=column):
                snapshot = self._complete_snapshot()
                snapshot["columns"][table].remove(column)
                report = validate_schema(snapshot, {"001_initial_postgresql.sql": "checksum-1"})
                self.assertFalse(report["ok"])
                self.assertEqual(report["missing_columns"][table], [column])

    def test_gate_covers_every_column_declared_by_postgres_migrations(self):
        migration_dir = Path(__file__).parents[1] / "migrations"
        declared: dict[str, set[str]] = {}
        for migration in migration_dir.glob("*.sql"):
            sql = migration.read_text(encoding="utf-8")
            for table, body in re.findall(
                r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-z_]\w*)\s*\((.*?)\);",
                sql,
                flags=re.IGNORECASE | re.DOTALL,
            ):
                for line in body.splitlines():
                    line = line.strip().rstrip(",")
                    match = re.match(r"([a-z_]\w*)\s+", line, flags=re.IGNORECASE)
                    if match and match.group(1).lower() not in {
                        "constraint",
                        "primary",
                        "unique",
                        "foreign",
                        "check",
                    }:
                        declared.setdefault(table, set()).add(match.group(1))
            for table, column in re.findall(
                r"ALTER\s+TABLE\s+([a-z_]\w*)\s+ADD\s+COLUMN(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-z_]\w*)",
                sql,
                flags=re.IGNORECASE,
            ):
                declared.setdefault(table, set()).add(column)

        covered = {
            f"{table}.{column}"
            for table, columns in REQUIRED_COLUMNS.items()
            for column in columns
            if table in declared
        }
        migration_columns = {
            f"{table}.{column}"
            for table, columns in declared.items()
            for column in columns
        }
        self.assertEqual(migration_columns - covered, set())

    def test_repository_migration_directory_is_not_empty(self):
        migration_dir = Path(__file__).parents[1] / "migrations"
        self.assertTrue(list(migration_dir.glob("*.sql")))

    def test_gate_covers_every_named_migration_index(self):
        migration_dir = Path(__file__).parents[1] / "migrations"
        declared = set()
        for migration in migration_dir.glob("*.sql"):
            declared.update(
                re.findall(
                    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+([a-z_]\w*)",
                    migration.read_text(encoding="utf-8"),
                    flags=re.IGNORECASE,
                )
            )
        self.assertEqual(declared, set(REQUIRED_INDEXES))

    def test_data_smoke_checks_runtime_invariants_and_rolls_back(self):
        class Cursor:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class FakeConnection:
            def __init__(self):
                self.event_queries = 0
                self.savepoint = None
                self.rollback_count = 0
                self.closed = False

            def execute(self, sql, params=()):
                if "SELECT COUNT(*) AS count FROM job_events" in sql:
                    self.event_queries += 1
                    return Cursor({"count": 1 if self.event_queries == 1 else 3})
                if "SELECT metadata_json FROM assets" in sql:
                    return Cursor({"metadata_json": {"probe": True}})
                if "SELECT idempotency_key FROM source_documents" in sql:
                    return Cursor({"idempotency_key": "schema-smoke-source-key"})
                if "SELECT idempotency_key FROM projects" in sql:
                    return Cursor({"idempotency_key": "schema-smoke-project-key"})
                if "SELECT idempotency_key FROM asset_review_decisions" in sql:
                    return Cursor({"idempotency_key": "schema-smoke-manual-review-key"})
                if "SELECT audio_tracks_json, subtitles_json" in sql:
                    return Cursor({"audio_tracks_json": [], "subtitles_json": [{"text": "probe"}]})
                if sql == "SAVEPOINT selected_unique_probe":
                    self.savepoint = "selected"
                elif sql == "SAVEPOINT source_idempotency_probe":
                    self.savepoint = "source_idempotency"
                elif sql == "SAVEPOINT project_idempotency_probe":
                    self.savepoint = "project_idempotency"
                elif sql == "SAVEPOINT manual_review_idempotency_probe":
                    self.savepoint = "manual_review_idempotency"
                elif sql == "SAVEPOINT invalid_status_probe":
                    self.savepoint = "status"
                elif self.savepoint == "selected" and "INSERT INTO assets" in sql:
                    error = RuntimeError("unique violation")
                    error.sqlstate = "23505"
                    raise error
                elif self.savepoint == "source_idempotency" and "INSERT INTO source_documents" in sql:
                    error = RuntimeError("unique violation")
                    error.sqlstate = "23505"
                    raise error
                elif self.savepoint == "project_idempotency" and "INSERT INTO projects" in sql:
                    error = RuntimeError("unique violation")
                    error.sqlstate = "23505"
                    raise error
                elif self.savepoint == "manual_review_idempotency" and "INSERT INTO asset_review_decisions" in sql:
                    error = RuntimeError("unique violation")
                    error.sqlstate = "23505"
                    raise error
                elif self.savepoint == "status" and "UPDATE jobs SET status = 'queued'" in sql:
                    error = RuntimeError("invalid transition")
                    error.sqlstate = "23514"
                    raise error
                return Cursor(None)

            def rollback(self):
                self.rollback_count += 1

            def close(self):
                self.closed = True

        class FakeStore:
            def __init__(self, connection):
                self.connection = connection

            def _connect(self):
                return self.connection

        connection = FakeConnection()
        report = run_data_smoke(FakeStore(connection))
        self.assertTrue(report["ok"])
        self.assertTrue(report["rolled_back"])
        self.assertEqual(connection.rollback_count, 1)
        self.assertTrue(connection.closed)
        self.assertEqual(
            report["checks"],
            {
                "project_idempotency_roundtrip": True,
                "project_idempotency_unique_index": True,
                "source_idempotency_roundtrip": True,
                "source_idempotency_unique_index": True,
                "manual_review_idempotency_roundtrip": True,
                "manual_review_idempotency_unique": True,
                "job_created_event": True,
                "job_status_events": True,
                "jsonb_roundtrip": True,
                "composition_jsonb_roundtrip": True,
                "selected_image_unique_index": True,
                "job_status_guard": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
