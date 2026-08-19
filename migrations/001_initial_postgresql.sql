-- AI 漫剧工作台 PostgreSQL 初始迁移
-- 应用层 ID 保持 text，便于从当前 SQLite 本地项目迁移而不改 API 合同。

BEGIN;

CREATE TABLE IF NOT EXISTS users (
  id text PRIMARY KEY,
  email text NOT NULL UNIQUE,
  password_hash text,
  password_salt text,
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title text NOT NULL,
  story text NOT NULL DEFAULT '',
  style text NOT NULL DEFAULT '国漫写实',
  episode_length text NOT NULL DEFAULT '1min',
  source_document_id text,
  status text NOT NULL DEFAULT 'draft',
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS source_documents (
  id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  filename text NOT NULL,
  media_type text NOT NULL DEFAULT 'text/plain',
  content_sha256 text NOT NULL,
  text text NOT NULL,
  copyright_acknowledged boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'projects_source_document_fk'
  ) THEN
    ALTER TABLE projects
      ADD CONSTRAINT projects_source_document_fk
      FOREIGN KEY (source_document_id) REFERENCES source_documents(id)
      DEFERRABLE INITIALLY DEFERRED;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS source_segments (
  id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source_document_id text NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
  chapter_no integer NOT NULL,
  sequence integer NOT NULL,
  text text NOT NULL,
  start_offset integer NOT NULL,
  end_offset integer NOT NULL,
  line_start integer NOT NULL,
  line_end integer NOT NULL
);

CREATE TABLE IF NOT EXISTS adaptation_units (
  id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source_segment_id text NOT NULL REFERENCES source_segments(id) ON DELETE CASCADE,
  chapter_no integer NOT NULL,
  sequence integer NOT NULL,
  source_text text NOT NULL,
  adapted_text text NOT NULL,
  mode text NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  traceability_json jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS characters (
  id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name text NOT NULL,
  role text NOT NULL,
  description text NOT NULL DEFAULT '',
  visual_lock_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'draft'
);

CREATE TABLE IF NOT EXISTS character_references (
  id text PRIMARY KEY,
  character_id text NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  front_url text,
  side_url text,
  back_url text,
  provider text NOT NULL DEFAULT 'local',
  model text NOT NULL DEFAULT 'placeholder',
  status text NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS episodes (
  id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  number integer NOT NULL,
  title text NOT NULL DEFAULT '',
  summary text NOT NULL DEFAULT '',
  conflict text NOT NULL DEFAULT '',
  hook text NOT NULL DEFAULT '',
  target_duration_seconds integer NOT NULL DEFAULT 60,
  status text NOT NULL DEFAULT 'draft',
  UNIQUE(project_id, number)
);

CREATE TABLE IF NOT EXISTS shots (
  id text PRIMARY KEY,
  episode_id text NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  sequence integer NOT NULL,
  scene text NOT NULL DEFAULT '',
  emotion text NOT NULL DEFAULT '',
  duration_seconds numeric NOT NULL DEFAULT 3,
  description text NOT NULL DEFAULT '',
  adaptation_unit_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  image_prompt_id text,
  UNIQUE(episode_id, sequence)
);

CREATE TABLE IF NOT EXISTS image_prompts (
  id text PRIMARY KEY,
  shot_id text NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
  prompt text NOT NULL,
  negative_prompt text NOT NULL DEFAULT '',
  provider text NOT NULL DEFAULT 'local',
  model text NOT NULL DEFAULT 'template'
);

CREATE TABLE IF NOT EXISTS assets (
  id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  shot_id text REFERENCES shots(id) ON DELETE SET NULL,
  character_id text REFERENCES characters(id) ON DELETE SET NULL,
  kind text NOT NULL CHECK (kind IN ('image', 'video', 'audio')),
  status text NOT NULL DEFAULT 'pending',
  url text,
  selected boolean NOT NULL DEFAULT false,
  consistency_confirmed boolean NOT NULL DEFAULT false,
  source_asset_id text,
  provider text NOT NULL DEFAULT 'local',
  model text NOT NULL DEFAULT 'placeholder',
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS asset_reviews (
  id text PRIMARY KEY,
  asset_id text NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  status text NOT NULL,
  issues_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  raw_text text NOT NULL DEFAULT '',
  provider text NOT NULL DEFAULT 'local',
  model text NOT NULL DEFAULT 'manual-review',
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind text NOT NULL,
  target_id text NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  cost_credits integer NOT NULL DEFAULT 0,
  attempts integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 3,
  error text,
  provider text NOT NULL DEFAULT 'local',
  idempotency_key text UNIQUE,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS credit_accounts (
  user_id text PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  balance integer NOT NULL DEFAULT 0 CHECK (balance >= 0),
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS credit_reservations (
  id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  job_id text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  amount integer NOT NULL CHECK (amount > 0),
  status text NOT NULL DEFAULT 'reserved',
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS credit_transactions (
  id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  job_id text,
  kind text NOT NULL,
  amount integer NOT NULL,
  balance_after integer NOT NULL,
  reason text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS compositions (
  id text PRIMARY KEY,
  episode_id text NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  playlist_url text,
  final_video_url text,
  status text NOT NULL DEFAULT 'pending',
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS studio_settings (
  key text PRIMARY KEY,
  value_json jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_user_updated ON projects(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user_expiry ON sessions(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_segments_project_order ON source_segments(project_id, chapter_no, sequence);
CREATE INDEX IF NOT EXISTS idx_units_project_order ON adaptation_units(project_id, chapter_no, sequence);
CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_user_created ON credit_transactions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assets_shot_created ON assets(shot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_asset_reviews_asset_created ON asset_reviews(asset_id, created_at DESC);

COMMIT;
