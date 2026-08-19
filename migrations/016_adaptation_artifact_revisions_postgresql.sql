-- 改编内容快照：镜头和下游媒体必须绑定同一份当前改编内容。
BEGIN;

ALTER TABLE shots
  ADD COLUMN IF NOT EXISTS adaptation_revision text NOT NULL DEFAULT '';

ALTER TABLE shots
  ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active';

COMMIT;
