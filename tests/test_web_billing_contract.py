from pathlib import Path
import unittest


class WebBillingContractTests(unittest.TestCase):
    def test_web_workbench_treats_stripe_as_external_billing_provider(self):
        source = (Path(__file__).parents[1] / "web" / "app" / "page.tsx").read_text(encoding="utf-8")
        self.assertIn('billing.provider === "stripe"', source)
        self.assertIn('billing.provider === "signed-webhook" || billing.provider === "stripe"', source)
        self.assertIn("billing.checkout_available === false", source)
        self.assertIn("Stripe Checkout 完成付款", source)
        self.assertIn("Stripe Checkout 尚未完成部署配置", source)
        self.assertIn("billingAdjustmentLabel", source)
        self.assertIn("billing_chargeback_reinstated", source)
        self.assertIn("退款/拒付已超过当前余额", source)

    def test_local_top_up_branch_is_reserved_for_non_external_provider(self):
        source = (Path(__file__).parents[1] / "web" / "app" / "page.tsx").read_text(encoding="utf-8")
        branch = source.split("{externalBillingProvider ? (", 1)[1].split(") : (", 1)[0]
        self.assertNotIn("localTopUp", branch)
        self.assertIn("localTopUp", source)
        self.assertIn("本地开发 +20", source)

    def test_billing_return_pages_exist_and_defer_settlement_to_webhook(self):
        root = Path(__file__).parents[1] / "web" / "app" / "billing"
        success = (root / "success" / "page.tsx").read_text(encoding="utf-8")
        cancelled = (root / "cancel" / "page.tsx").read_text(encoding="utf-8")
        self.assertIn("以支付 Webhook 验证为准", success)
        self.assertIn("/?billing=success", success)
        self.assertIn("encodeURIComponent(orderId)", success)
        self.assertIn("不会入账", cancelled)
        self.assertIn("/?billing=cancelled", cancelled)
        self.assertIn("encodeURIComponent(orderId)", cancelled)

    def test_workbench_reconciles_bounded_payment_return_status(self):
        source = (Path(__file__).parents[1] / "web" / "app" / "page.tsx").read_text(encoding="utf-8")
        self.assertIn('params.get("billing")', source)
        self.assertIn('params.get("order_id")', source)
        self.assertIn("window.setInterval(() => void reconcileBillingReturn(), 2000)", source)
        self.assertIn("billingPolls >= 6", source)
        self.assertIn("支付已由 Webhook 验证，积分已入账", source)


if __name__ == "__main__":
    unittest.main()
