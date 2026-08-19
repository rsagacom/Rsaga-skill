"""SQLite 本地存储。

SQLite 是开发/单机默认实现；表结构保持 PostgreSQL 可迁移，不把 SQLite
细节泄漏到领域模型或 API 合同中。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  title TEXT NOT NULL,
  story TEXT NOT NULL DEFAULT '',
  style TEXT NOT NULL DEFAULT '国漫写实',
  episode_length TEXT NOT NULL DEFAULT '1min',
  source_document_id TEXT,
  idempotency_key TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT,
  password_salt TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  device_label TEXT NOT NULL DEFAULT '',
  last_seen_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS source_documents (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  media_type TEXT NOT NULL DEFAULT 'text/plain',
  content_sha256 TEXT NOT NULL,
  text TEXT NOT NULL,
  copyright_acknowledged INTEGER NOT NULL DEFAULT 0,
  idempotency_key TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_segments (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source_document_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
  chapter_no INTEGER NOT NULL,
  sequence INTEGER NOT NULL,
  text TEXT NOT NULL,
  start_offset INTEGER NOT NULL,
  end_offset INTEGER NOT NULL,
  line_start INTEGER NOT NULL,
  line_end INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS adaptation_units (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source_segment_id TEXT NOT NULL REFERENCES source_segments(id) ON DELETE CASCADE,
  chapter_no INTEGER NOT NULL,
  sequence INTEGER NOT NULL,
  source_text TEXT NOT NULL,
  adapted_text TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  traceability_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS adaptation_revisions (
  id TEXT PRIMARY KEY,
  adaptation_unit_id TEXT NOT NULL REFERENCES adaptation_units(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  mode TEXT NOT NULL,
  source_text TEXT NOT NULL,
  adapted_text TEXT NOT NULL,
  diff_json TEXT NOT NULL DEFAULT '[]',
  provider TEXT NOT NULL DEFAULT 'local',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(adaptation_unit_id, version)
);

CREATE TABLE IF NOT EXISTS characters (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  role TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  visual_lock_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'draft'
);

-- 故事资产与关系：角色沿用 characters 表，地点/道具单独建模；关系端点
-- 使用 type + id，既能连接角色，也能连接故事资产，且可保留来源段落追溯。
CREATE TABLE IF NOT EXISTS story_entities (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('location', 'prop')),
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  attributes_json TEXT NOT NULL DEFAULT '{}',
  source_segment_ids_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected', 'superseded')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS story_relationships (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source_type TEXT NOT NULL CHECK (source_type IN ('character', 'entity')),
  source_id TEXT NOT NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('character', 'entity')),
  target_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  source_segment_ids_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected', 'superseded')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS story_bible_runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  source_sha256 TEXT NOT NULL,
  output_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
  error TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS character_references (
  id TEXT PRIMARY KEY,
  character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  front_url TEXT,
  side_url TEXT,
  back_url TEXT,
  provider TEXT NOT NULL DEFAULT 'local',
  model TEXT NOT NULL DEFAULT 'placeholder',
  status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  conflict TEXT NOT NULL DEFAULT '',
  hook TEXT NOT NULL DEFAULT '',
  target_duration_seconds INTEGER NOT NULL DEFAULT 60,
  status TEXT NOT NULL DEFAULT 'draft'
);

-- 叙事单元和场景是“分集 → 叙事单元 → 场景 → 镜头”的控制平面层。
-- JSON 数组只保存同一分集内的 ID，具体归属仍由服务层按用户/分集校验。
CREATE TABLE IF NOT EXISTS narrative_units (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  goal TEXT NOT NULL DEFAULT '',
  enter_state TEXT NOT NULL DEFAULT '',
  exit_state TEXT NOT NULL DEFAULT '',
  target_duration_seconds INTEGER NOT NULL DEFAULT 0,
  scene_ids_json TEXT NOT NULL DEFAULT '[]',
  shot_ids_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenes (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  unit_id TEXT REFERENCES narrative_units(id) ON DELETE SET NULL,
  location_id TEXT,
  name TEXT NOT NULL DEFAULT '',
  time_of_day TEXT NOT NULL DEFAULT '',
  weather TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  shot_ids_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shots (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  scene TEXT NOT NULL DEFAULT '',
  emotion TEXT NOT NULL DEFAULT '',
  duration_seconds REAL NOT NULL DEFAULT 3,
  description TEXT NOT NULL DEFAULT '',
  adaptation_unit_ids_json TEXT NOT NULL DEFAULT '[]',
  image_prompt_id TEXT,
  adaptation_revision TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS image_prompts (
  id TEXT PRIMARY KEY,
  shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
  prompt TEXT NOT NULL,
  negative_prompt TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT 'local',
  model TEXT NOT NULL DEFAULT 'template'
);

CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  shot_id TEXT REFERENCES shots(id) ON DELETE SET NULL,
  character_id TEXT REFERENCES characters(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  url TEXT,
  selected INTEGER NOT NULL DEFAULT 0,
  consistency_confirmed INTEGER NOT NULL DEFAULT 0,
  source_asset_id TEXT,
  provider TEXT NOT NULL DEFAULT 'local',
  model TEXT NOT NULL DEFAULT 'placeholder',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asset_reviews (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  issues_json TEXT NOT NULL DEFAULT '[]',
  raw_text TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT 'local',
  model TEXT NOT NULL DEFAULT 'manual-review',
  created_at TEXT NOT NULL
);

-- 人工视觉审核决策是非队列写操作，单独保存幂等请求映射，避免浏览器
-- 重试重复追加审核记录；审计正文仍保留在 asset_reviews。
CREATE TABLE IF NOT EXISTS asset_review_decisions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL,
  issues_json TEXT NOT NULL DEFAULT '[]',
  review_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(user_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  target_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  cost_credits INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  progress_percent INTEGER NOT NULL DEFAULT 0 CHECK (progress_percent >= 0 AND progress_percent <= 100),
  progress_message TEXT NOT NULL DEFAULT '',
  error TEXT,
  provider TEXT NOT NULL DEFAULT 'local',
  payload_json TEXT NOT NULL DEFAULT '{}',
  idempotency_key TEXT UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_events (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  event_type TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  attempt INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_jobs_insert_event
AFTER INSERT ON jobs
BEGIN
  INSERT INTO job_events(id, job_id, user_id, from_status, to_status, event_type, message, attempt, created_at)
  VALUES ('job_event_' || lower(hex(randomblob(16))), NEW.id, NEW.user_id, NULL, NEW.status, 'created', COALESCE(NEW.error, ''), NEW.attempts, NEW.created_at);
END;

CREATE TRIGGER IF NOT EXISTS trg_jobs_validate_status
BEFORE UPDATE OF status ON jobs
WHEN OLD.status IS NOT NEW.status
 AND NOT (
   (OLD.status = 'pending' AND NEW.status IN ('queued', 'cancelled'))
   OR (OLD.status = 'queued' AND NEW.status IN ('running', 'cancelled'))
   OR (OLD.status = 'queued' AND NEW.status = 'failed' AND NEW.error = 'insufficient credits')
   OR (OLD.status = 'running' AND NEW.status IN ('review', 'completed', 'failed', 'cancelled'))
   OR (OLD.status = 'running' AND NEW.status = 'queued' AND NEW.error = 'worker lease expired; requeued')
   OR (OLD.status = 'review' AND NEW.status IN ('completed', 'failed', 'cancelled'))
   OR (OLD.status = 'failed' AND NEW.status IN ('queued', 'cancelled'))
 )
BEGIN
  SELECT RAISE(ABORT, 'invalid job status transition');
END;

CREATE TRIGGER IF NOT EXISTS trg_jobs_status_event
AFTER UPDATE OF status ON jobs
WHEN OLD.status IS NOT NEW.status
BEGIN
  INSERT INTO job_events(id, job_id, user_id, from_status, to_status, event_type, message, attempt, created_at)
  VALUES ('job_event_' || lower(hex(randomblob(16))), NEW.id, NEW.user_id, OLD.status, NEW.status, 'status_change', COALESCE(NEW.error, ''), NEW.attempts, NEW.updated_at);
END;

CREATE TABLE IF NOT EXISTS credit_accounts (
  user_id TEXT PRIMARY KEY,
  balance INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credit_reservations (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  amount INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'reserved',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credit_transactions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  job_id TEXT,
  kind TEXT NOT NULL,
  amount INTEGER NOT NULL,
  balance_after INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_orders (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  package_code TEXT NOT NULL,
  credits INTEGER NOT NULL CHECK (credits > 0),
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  currency TEXT NOT NULL DEFAULT 'CNY',
  provider TEXT NOT NULL,
  provider_order_id TEXT NOT NULL UNIQUE,
  provider_payment_id TEXT UNIQUE,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'cancelled')),
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  paid_at TEXT
);

CREATE TABLE IF NOT EXISTS billing_webhook_events (
  event_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  order_id TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_adjustments (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  order_id TEXT NOT NULL REFERENCES billing_orders(id) ON DELETE CASCADE,
  adjustment_key TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('refund', 'chargeback', 'chargeback_reinstated')),
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  currency TEXT NOT NULL,
  credits_delta INTEGER NOT NULL,
  event_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(provider, adjustment_key)
);

CREATE TABLE IF NOT EXISTS compositions (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  playlist_url TEXT,
  final_video_url TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS composition_settings (
  episode_id TEXT PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
  audio_tracks_json TEXT NOT NULL DEFAULT '[]',
  subtitles_json TEXT NOT NULL DEFAULT '[]',
  narration_text TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS studio_settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_segments_project ON source_segments(project_id, chapter_no, sequence);
CREATE INDEX IF NOT EXISTS idx_units_project ON adaptation_units(project_id, chapter_no, sequence);
CREATE INDEX IF NOT EXISTS idx_adaptation_revisions_unit ON adaptation_revisions(adaptation_unit_id, version);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON credit_transactions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_billing_orders_user ON billing_orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_billing_orders_provider_order ON billing_orders(provider_order_id);
CREATE INDEX IF NOT EXISTS idx_billing_adjustments_order_created ON billing_adjustments(order_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_asset_reviews_asset ON asset_reviews(asset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_asset_review_decisions_asset ON asset_review_decisions(asset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_story_entities_project_kind ON story_entities(project_id, kind, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_story_relationships_project ON story_relationships(project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_story_bible_runs_project_created ON story_bible_runs(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_narrative_units_project_episode ON narrative_units(project_id, episode_id);
CREATE INDEX IF NOT EXISTS idx_scenes_project_episode ON scenes(project_id, episode_id);

-- 旧版本只在服务层保证同一镜头的唯一采用。初始化时先保留最近更新的候选，
-- 撤销其余历史重复值，再由部分唯一索引把不变量下沉到 SQLite 数据层。
UPDATE assets AS current_asset
SET selected = 0, consistency_confirmed = 0
WHERE current_asset.kind = 'image'
  AND current_asset.selected = 1
  AND current_asset.shot_id IS NOT NULL
  AND current_asset.id <> (
    SELECT candidate.id
    FROM assets AS candidate
    WHERE candidate.kind = 'image'
      AND candidate.selected = 1
      AND candidate.shot_id = current_asset.shot_id
    ORDER BY candidate.updated_at DESC, candidate.created_at DESC, candidate.id DESC
    LIMIT 1
  );

CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_single_selected_image
  ON assets(shot_id)
  WHERE kind = 'image' AND selected = 1;
"""


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class StudioStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            # 兼容已经创建的 SQLite 开发库：CREATE TABLE IF NOT EXISTS 不会
            # 为旧表补列，但来源导入幂等不能因本地升级丢失。
            columns = {row[1] for row in connection.execute("PRAGMA table_info(source_documents)").fetchall()}
            if "idempotency_key" not in columns:
                connection.execute("ALTER TABLE source_documents ADD COLUMN idempotency_key TEXT")
            project_columns = {row[1] for row in connection.execute("PRAGMA table_info(projects)").fetchall()}
            if "idempotency_key" not in project_columns:
                connection.execute("ALTER TABLE projects ADD COLUMN idempotency_key TEXT")
            shot_columns = {row[1] for row in connection.execute("PRAGMA table_info(shots)").fetchall()}
            if "adaptation_revision" not in shot_columns:
                connection.execute("ALTER TABLE shots ADD COLUMN adaptation_revision TEXT NOT NULL DEFAULT ''")
            if "status" not in shot_columns:
                connection.execute("ALTER TABLE shots ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
            job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
            if "payload_json" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")
            if "progress_percent" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN progress_percent INTEGER NOT NULL DEFAULT 0")
            if "progress_message" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN progress_message TEXT NOT NULL DEFAULT ''")
            connection.execute("UPDATE jobs SET progress_percent = 100 WHERE status = 'completed' AND progress_percent < 100")
            session_columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()}
            if "device_label" not in session_columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN device_label TEXT NOT NULL DEFAULT ''")
            if "last_seen_at" not in session_columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN last_seen_at TEXT NOT NULL DEFAULT ''")
            connection.execute("UPDATE sessions SET last_seen_at = created_at WHERE last_seen_at = ''")
            billing_order_columns = {row[1] for row in connection.execute("PRAGMA table_info(billing_orders)").fetchall()}
            if "provider_payment_id" not in billing_order_columns:
                connection.execute("ALTER TABLE billing_orders ADD COLUMN provider_payment_id TEXT")
            composition_columns = {row[1] for row in connection.execute("PRAGMA table_info(composition_settings)").fetchall()}
            if "narration_text" not in composition_columns:
                connection.execute("ALTER TABLE composition_settings ADD COLUMN narration_text TEXT NOT NULL DEFAULT ''")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_orders_provider_payment "
                "ON billing_orders(provider_payment_id) WHERE provider_payment_id IS NOT NULL"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_source_documents_project_idempotency "
                "ON source_documents(project_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_user_idempotency "
                "ON projects(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
            )

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return connection.execute(sql, params).fetchall()

    def write(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.connection() as connection:
            connection.execute(sql, params)

    def write_many(self, statements: list[tuple[str, tuple[Any, ...]]]) -> None:
        with self.connection() as connection:
            for sql, params in statements:
                connection.execute(sql, params)


class PostgresConnection:
    """将领域层使用的 SQLite 风格占位符映射到 psycopg。"""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @staticmethod
    def _translate(sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        translated = self._translate(sql)
        if any(column in sql for column in ("traceability_json", "visual_lock_json", "adaptation_unit_ids_json", "diff_json", "metadata_json", "payload_json", "value_json", "issues_json", "audio_tracks_json", "subtitles_json", "attributes_json", "source_segment_ids_json", "output_json")):
            try:
                from psycopg.types.json import Jsonb
                params = tuple(Jsonb(json.loads(value)) if isinstance(value, str) and _is_json_literal(value) else value for value in params)
            except (ImportError, json.JSONDecodeError):
                pass
        return self._connection.execute(translated, params)

    def executescript(self, sql: str) -> Any:
        return self._connection.execute(sql)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()


def _is_json_literal(value: str) -> bool:
    try:
        json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return False
    return True

class PostgresStore:
    """可选 PostgreSQL runtime Store。

    需要额外安装 `psycopg[binary]`。不在基础 requirements 中强制安装，
    但服务层、API 和积分账本合同与 SQLite Store 相同。
    """

    def __init__(self, dsn: str, runtime_path: str | Path = "runtime") -> None:
        self.dsn = dsn
        self.runtime_path = Path(runtime_path)
        self.runtime_path.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL runtime requires optional psycopg[binary]; see requirements-postgres.txt") from exc
        return psycopg.connect(self.dsn, row_factory=dict_row)

    @staticmethod
    def _migration_checksum(sql: str) -> str:
        return hashlib.sha256(sql.encode("utf-8")).hexdigest()

    @staticmethod
    def _migration_files() -> list[Path]:
        return sorted((Path(__file__).resolve().parent.parent / "migrations").glob("*.sql"))

    @contextmanager
    def connection(self) -> Iterator[PostgresConnection]:
        connection = self._connect()
        wrapped = PostgresConnection(connection)
        try:
            yield wrapped
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        migrations = self._migration_files()
        with self.connection() as connection:
            # API 进程启动和 Compose migration job 都会调用 initialize()。
            # session-level advisory lock 保证多个发布副本不会交叉执行迁移；
            # 使用 session lock 而不是 transaction lock，是因为迁移文件自身
            # 包含 BEGIN/COMMIT，可能在单次初始化中提交多个事务。
            connection.execute("SELECT pg_advisory_lock(hashtext('ai-manhua-studio-migrations'))")
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS studio_schema_migrations (
                      migration_name text PRIMARY KEY,
                      checksum text NOT NULL,
                      applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.commit()
                for migration in migrations:
                    migration_name = migration.name
                    migration_sql = migration.read_text(encoding="utf-8")
                    checksum = self._migration_checksum(migration_sql)
                    row = connection.execute(
                        "SELECT checksum FROM studio_schema_migrations WHERE migration_name = ?",
                        (migration_name,),
                    ).fetchone()
                    # 结束查询事务后再执行包含 BEGIN 的迁移文件；同样也让
                    # 已完成迁移在下一轮初始化时安全跳过。
                    connection.commit()
                    if row is not None:
                        applied_checksum = row["checksum"] if isinstance(row, dict) else row[0]
                        if applied_checksum != checksum:
                            raise RuntimeError(
                                f"PostgreSQL migration checksum mismatch: {migration_name}; "
                                "migration files are immutable after being applied"
                            )
                        continue

                    connection.executescript(migration_sql)
                    connection.execute(
                        "INSERT INTO studio_schema_migrations(migration_name, checksum) VALUES (?, ?)",
                        (migration_name, checksum),
                    )
                    connection.commit()
            except Exception:
                # 迁移文件可能已经提交自己的事务；这里至少结束当前失败事务，
                # 保证 finally 中的 advisory unlock 不会被 PostgreSQL 拒绝。
                connection.rollback()
                raise
            finally:
                connection.execute("SELECT pg_advisory_unlock(hashtext('ai-manhua-studio-migrations'))")

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        with self.connection() as connection:
            return connection.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        with self.connection() as connection:
            return connection.execute(sql, params).fetchall()

    def write(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.connection() as connection:
            connection.execute(sql, params)

    def write_many(self, statements: list[tuple[str, tuple[Any, ...]]]) -> None:
        with self.connection() as connection:
            for sql, params in statements:
                connection.execute(sql, params)
