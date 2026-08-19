-- 已支付订单的退款、拒付扣款和拒付资金恢复，统一进入可审计积分调整账本。
BEGIN;

ALTER TABLE billing_orders
  ADD COLUMN IF NOT EXISTS provider_payment_id text;

CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_orders_provider_payment
  ON billing_orders(provider_payment_id)
  WHERE provider_payment_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS billing_adjustments (
  id text PRIMARY KEY,
  provider text NOT NULL,
  order_id text NOT NULL REFERENCES billing_orders(id) ON DELETE CASCADE,
  adjustment_key text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('refund', 'chargeback', 'chargeback_reinstated')),
  amount_cents integer NOT NULL CHECK (amount_cents > 0),
  currency text NOT NULL,
  credits_delta integer NOT NULL,
  event_id text NOT NULL,
  created_at timestamptz NOT NULL,
  UNIQUE(provider, adjustment_key)
);

CREATE INDEX IF NOT EXISTS idx_billing_adjustments_order_created
  ON billing_adjustments(order_id, created_at DESC);

-- 退款/拒付可能发生在积分已被消费之后；允许账本余额进入负数，
-- 由现有 reservation 的 balance >= amount gate 阻止继续消费，形成可追踪欠账。
ALTER TABLE credit_accounts
  DROP CONSTRAINT IF EXISTS credit_accounts_balance_check;

COMMIT;
