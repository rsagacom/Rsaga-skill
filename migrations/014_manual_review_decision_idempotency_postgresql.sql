-- 人工视觉审核决策幂等：网络重试不得重复追加人工审核记录。
BEGIN;

CREATE TABLE IF NOT EXISTS asset_review_decisions (
  id text PRIMARY KEY,
  user_id text NOT NULL,
  asset_id text NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  idempotency_key text NOT NULL,
  status text NOT NULL,
  issues_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  review_id text NOT NULL,
  created_at timestamptz NOT NULL,
  UNIQUE(user_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_asset_review_decisions_asset
  ON asset_review_decisions(asset_id, created_at DESC);

COMMIT;
