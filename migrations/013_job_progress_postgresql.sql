-- 持久化 Worker 进度，供刷新后的 Web 任务中心恢复真实进度。
BEGIN;

ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS progress_percent integer;

ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS progress_message text;

UPDATE jobs
SET progress_percent = CASE WHEN status = 'completed' THEN 100 ELSE COALESCE(progress_percent, 0) END,
    progress_message = COALESCE(progress_message, '');

ALTER TABLE jobs
  ALTER COLUMN progress_percent SET DEFAULT 0,
  ALTER COLUMN progress_percent SET NOT NULL,
  ALTER COLUMN progress_message SET DEFAULT '',
  ALTER COLUMN progress_message SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'jobs_progress_percent_range'
  ) THEN
    ALTER TABLE jobs
      ADD CONSTRAINT jobs_progress_percent_range
      CHECK (progress_percent >= 0 AND progress_percent <= 100);
  END IF;
END $$;

COMMIT;
