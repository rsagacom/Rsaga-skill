-- 关键帧候选唯一采用：把 v0.47 的服务层不变量下沉到 PostgreSQL。
-- 已有脏数据保留最近更新的候选，其余候选撤销采用和一致性确认。
BEGIN;

WITH ranked_selected AS (
  SELECT
    id,
    ROW_NUMBER() OVER (
      PARTITION BY shot_id
      ORDER BY updated_at DESC, created_at DESC, id DESC
    ) AS position
  FROM assets
  WHERE kind = 'image' AND selected = TRUE AND shot_id IS NOT NULL
)
UPDATE assets AS asset
SET selected = FALSE, consistency_confirmed = FALSE
FROM ranked_selected AS ranked
WHERE asset.id = ranked.id AND ranked.position > 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_single_selected_image
  ON assets(shot_id)
  WHERE kind = 'image' AND selected = TRUE;

COMMIT;
