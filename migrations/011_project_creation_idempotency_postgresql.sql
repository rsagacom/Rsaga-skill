-- 项目创建幂等：网络重试不得重复创建项目及其故事梗概来源。
BEGIN;

ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS idempotency_key text;

CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_user_idempotency
  ON projects(user_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

COMMIT;
