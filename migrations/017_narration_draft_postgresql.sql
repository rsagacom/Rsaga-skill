-- 分集旁白稿：允许用户编辑后跨刷新、导出/导入和重新生成旁白。
BEGIN;

ALTER TABLE composition_settings
  ADD COLUMN IF NOT EXISTS narration_text text NOT NULL DEFAULT '';

COMMIT;
