-- 持久化非敏感的 Job 输入：让异步视觉审核在 worker 重启后仍能还原审核类型和提示词。
BEGIN;

ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS payload_json jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
