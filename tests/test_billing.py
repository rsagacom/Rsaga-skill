import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs

from studio_api.billing import BillingProviderError, StripeCheckoutAdapter


class StripeBillingAdapterTests(unittest.TestCase):
    def test_checkout_uses_form_contract_and_idempotency_key_without_returning_secret(self):
        secret = "sk_test_secret_that_is_only_a_fixture_value"
        response = MagicMock()
        response.read.return_value = json.dumps({"id": "cs_test_123", "url": "https://checkout.stripe.test/cs_test_123"}).encode()
        response.__enter__.return_value = response
        order = {
            "id": "order_123",
            "provider_order_id": "studio_order_123",
            "package_code": "starter",
            "package_label": "创作入门包",
            "amount_cents": 9900,
            "currency": "CNY",
        }
        with patch("studio_api.billing.urllib.request.urlopen", return_value=response) as urlopen:
            result = StripeCheckoutAdapter(api_base_url="https://api.stripe.test", secret_key=secret).create_checkout(
                order,
                success_url="https://studio.test/billing/success",
                cancel_url="https://studio.test/billing/cancel",
            )
        request = urlopen.call_args.args[0]
        form = parse_qs(request.data.decode())
        self.assertEqual(result, {"session_id": "cs_test_123", "checkout_url": "https://checkout.stripe.test/cs_test_123"})
        self.assertEqual(form["mode"], ["payment"])
        self.assertEqual(form["line_items[0][price_data][unit_amount]"], ["9900"])
        self.assertEqual(form["line_items[0][price_data][currency]"], ["cny"])
        self.assertEqual(form["metadata[order_id]"], ["order_123"])
        self.assertEqual(form["payment_intent_data[metadata][order_id]"], ["order_123"])
        self.assertEqual(form["payment_intent_data[metadata][provider_order_id]"], ["studio_order_123"])
        self.assertEqual(request.get_header("Idempotency-key"), "studio_order_123")
        self.assertNotIn(secret, repr(result))

    def test_webhook_signature_is_raw_body_bound_and_time_limited(self):
        secret = "whsec_fixture_secret_that_is_only_a_test_value"
        body = b'{"id":"evt_test_123","type":"checkout.session.completed"}'
        timestamp = 1_700_000_000
        digest = hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()
        adapter = StripeCheckoutAdapter(webhook_secret=secret)
        event = adapter.verify_webhook(body, f"t={timestamp},v1={digest}", now_epoch=timestamp)
        self.assertEqual(event["id"], "evt_test_123")
        with self.assertRaisesRegex(BillingProviderError, "invalid"):
            adapter.verify_webhook(body + b" ", f"t={timestamp},v1={digest}", now_epoch=timestamp)
        with self.assertRaisesRegex(BillingProviderError, "expired"):
            adapter.verify_webhook(body, f"t={timestamp},v1={digest}", now_epoch=timestamp + 301)

    def test_supported_events_normalize_and_unrelated_events_are_ignored(self):
        paid = StripeCheckoutAdapter.normalize_event(
            {
                "id": "evt_paid",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "client_reference_id": "order_123",
                        "payment_status": "paid",
                        "amount_total": 9900,
                        "currency": "cny",
                        "metadata": {},
                    }
                },
            }
        )
        cancelled = StripeCheckoutAdapter.normalize_event(
            {
                "id": "evt_expired",
                "type": "checkout.session.expired",
                "data": {"object": {"metadata": {"order_id": "order_123"}}},
            }
        )
        self.assertEqual(paid["status"], "paid")
        self.assertEqual(paid["amount_cents"], 9900)
        self.assertEqual(cancelled, {"event_id": "evt_expired", "order_id": "order_123", "status": "cancelled"})
        refund = StripeCheckoutAdapter.normalize_event(
            {
                "id": "evt_refund",
                "type": "refund.created",
                "data": {
                    "object": {
                        "id": "re_123",
                        "amount": 9900,
                        "currency": "cny",
                        "payment_intent": "pi_123",
                        "metadata": {"order_id": "order_123"},
                    }
                },
            }
        )
        dispute = StripeCheckoutAdapter.normalize_event(
            {
                "id": "evt_dispute_withdrawn",
                "type": "charge.dispute.funds_withdrawn",
                "data": {
                    "object": {
                        "id": "dp_123",
                        "amount": 9900,
                        "currency": "cny",
                        "payment_intent": "pi_123",
                        "metadata": {"order_id": "order_123"},
                    }
                },
            }
        )
        self.assertEqual(refund["adjustment_type"], "refund")
        self.assertEqual(refund["adjustment_id"], "re_123")
        self.assertEqual(refund["provider_payment_id"], "pi_123")
        self.assertEqual(dispute["adjustment_type"], "chargeback")
        self.assertEqual(dispute["adjustment_id"], "dp_123")
        self.assertIsNone(StripeCheckoutAdapter.normalize_event({"id": "evt_other", "type": "charge.succeeded"}))


if __name__ == "__main__":
    unittest.main()
