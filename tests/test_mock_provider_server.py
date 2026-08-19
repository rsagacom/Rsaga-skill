import json
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import urlencode

from scripts.mock_provider_server import MockServer, ProviderState
from studio_api.billing import StripeCheckoutAdapter


class MockStripeServerTests(unittest.TestCase):
    def setUp(self):
        self.server = MockServer(("127.0.0.1", 0), ProviderState("stripe"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_checkout_session_is_form_encoded_and_idempotent(self):
        form = urlencode(
            {
                "mode": "payment",
                "client_reference_id": "order-1",
                "line_items[0][price_data][currency]": "cny",
                "line_items[0][price_data][unit_amount]": "9900",
                "metadata[order_id]": "order-1",
                "metadata[provider_order_id]": "studio-order-1",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/checkout/sessions",
            data=form,
            method="POST",
            headers={"Authorization": "Basic fixture", "Idempotency-Key": "studio-order-1"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            first = json.loads(response.read())
        with urllib.request.urlopen(request, timeout=2) as response:
            second = json.loads(response.read())
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["amount_total"], 9900)
        self.assertEqual(first["currency"], "cny")
        self.assertEqual(first["metadata"]["order_id"], "order-1")
        self.assertTrue(first["url"].startswith("https://checkout.ci.example.test/"))

    def test_health_is_public_but_checkout_requires_basic_auth(self):
        with urllib.request.urlopen(f"{self.base_url}/health", timeout=2) as response:
            self.assertEqual(json.loads(response.read())["status"], "ok")
        request = urllib.request.Request(f"{self.base_url}/v1/checkout/sessions", data=b"", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(failure.exception.code, 401)
        failure.exception.close()

    def test_stripe_adapter_reaches_mock_over_real_http(self):
        result = StripeCheckoutAdapter(
            api_base_url=self.base_url,
            secret_key="stripe-ci-secret-with-at-least-32-characters-123",
        ).create_checkout(
            {
                "id": "order-adapter-1",
                "provider_order_id": "studio-order-adapter-1",
                "package_code": "starter",
                "package_label": "创作入门包",
                "amount_cents": 9900,
                "currency": "CNY",
            },
            success_url="https://studio.example.test/billing/success",
            cancel_url="https://studio.example.test/billing/cancel",
        )
        self.assertTrue(result["session_id"].startswith("cs_ci_"))
        self.assertTrue(result["checkout_url"].startswith("https://checkout.ci.example.test/"))


if __name__ == "__main__":
    unittest.main()
