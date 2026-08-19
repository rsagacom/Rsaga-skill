-- 故事资产抽取：地点、道具、关系和每次抽取运行的可审计快照。
BEGIN;

CREATE TABLE IF NOT EXISTS story_entities (
  id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('location', 'prop')),
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  attributes_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_segment_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected', 'superseded')),
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS story_relationships (
  id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source_type text NOT NULL CHECK (source_type IN ('character', 'entity')),
  source_id text NOT NULL,
  target_type text NOT NULL CHECK (target_type IN ('character', 'entity')),
  target_id text NOT NULL,
  relation text NOT NULL,
  description text NOT NULL DEFAULT '',
  source_segment_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected', 'superseded')),
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS story_bible_runs (
  id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  provider text NOT NULL,
  model text NOT NULL DEFAULT '',
  source_sha256 text NOT NULL,
  output_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL CHECK (status IN ('completed', 'failed')),
  error text,
  created_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_story_entities_project_kind
  ON story_entities(project_id, kind, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_story_relationships_project
  ON story_relationships(project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_story_bible_runs_project_created
  ON story_bible_runs(project_id, created_at DESC);

COMMIT;
