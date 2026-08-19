-- 为账户安全页补充非敏感的设备标签与最近活跃时间。
BEGIN;

ALTER TABLE sessions
  ADD COLUMN IF NOT EXISTS device_label text NOT NULL DEFAULT '';

ALTER TABLE sessions
  ADD COLUMN IF NOT EXISTS last_seen_at timestamptz;

UPDATE sessions
SET last_seen_at = created_at
WHERE last_seen_at IS NULL;

ALTER TABLE sessions
  ALTER COLUMN last_seen_at SET DEFAULT CURRENT_TIMESTAMP,
  ALTER COLUMN last_seen_at SET NOT NULL;

COMMIT;
