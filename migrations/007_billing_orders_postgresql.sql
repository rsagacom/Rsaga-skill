-- 生产积分订单、签名支付回调和幂等入账账本。
BEGIN;

CREATE TABLE IF NOT EXISTS billing_orders (
  id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  package_code text NOT NULL,
  credits integer NOT NULL CHECK (credits > 0),
  amount_cents integer NOT NULL CHECK (amount_cents > 0),
  currency text NOT NULL DEFAULT 'CNY',
  provider text NOT NULL,
  provider_order_id text NOT NULL UNIQUE,
  idempotency_key text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'cancelled')),
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL,
  paid_at timestamptz
);

CREATE TABLE IF NOT EXISTS billing_webhook_events (
  event_id text PRIMARY KEY,
  provider text NOT NULL,
  order_id text NOT NULL,
  payload_sha256 text NOT NULL,
  received_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_billing_orders_user_created
  ON billing_orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_billing_orders_provider_order
  ON billing_orders(provider_order_id);

COMMIT;
