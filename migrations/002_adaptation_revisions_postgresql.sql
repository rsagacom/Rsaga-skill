-- 改编单元版本审计：保留模型生成、人工编辑和导入后的每次文本版本。
BEGIN;

CREATE TABLE IF NOT EXISTS adaptation_revisions (
  id text PRIMARY KEY,
  adaptation_unit_id text NOT NULL REFERENCES adaptation_units(id) ON DELETE CASCADE,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  version integer NOT NULL,
  mode text NOT NULL,
  source_text text NOT NULL,
  adapted_text text NOT NULL,
  diff_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  provider text NOT NULL DEFAULT 'local',
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL,
  UNIQUE(adaptation_unit_id, version)
);

CREATE INDEX IF NOT EXISTS idx_adaptation_revisions_unit ON adaptation_revisions(adaptation_unit_id, version);

COMMIT;
