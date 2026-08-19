-- 分集音频轨道与字幕时间线设置；只保存 asset_id/时间参数，不保存服务器绝对路径。
BEGIN;

CREATE TABLE IF NOT EXISTS composition_settings (
  episode_id text PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
  audio_tracks_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  subtitles_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  narration_text text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL
);

COMMIT;
