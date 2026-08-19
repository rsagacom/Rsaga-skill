-- P0 控制平面：叙事单元与场景结构化模型
-- 对应 studio_core/models.py 的 NarrativeUnit / Scene dataclass。
BEGIN;

CREATE TABLE IF NOT EXISTS narrative_units (
  id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  episode_id text NOT NULL,
  goal text NOT NULL DEFAULT '',
  enter_state text NOT NULL DEFAULT '',
  exit_state text NOT NULL DEFAULT '',
  target_duration_seconds integer NOT NULL DEFAULT 0,
  scene_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  shot_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scenes (
  id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  episode_id text NOT NULL,
  unit_id text,
  location_id text,
  name text NOT NULL DEFAULT '',
  time_of_day text NOT NULL DEFAULT '',
  weather text NOT NULL DEFAULT '',
  summary text NOT NULL DEFAULT '',
  shot_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_narrative_units_project_episode
  ON narrative_units(project_id, episode_id);
CREATE INDEX IF NOT EXISTS idx_scenes_project_episode
  ON scenes(project_id, episode_id);

COMMIT;