import base64
import io
import hashlib
import hmac
import json
from pathlib import Path
import unittest
import wave

from studio_api.documents import extract_document
from scripts.stack_smoke import billing_signature, smoke_source_file_fixtures, smoke_wav_base64, stripe_signature


class StackSmokeContractTests(unittest.TestCase):
    STACK_SMOKE_SOURCE = (Path(__file__).resolve().parents[1] / "scripts" / "stack_smoke.py").read_text(encoding="utf-8")
    README_SOURCE = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    RUNBOOK_SOURCE = (Path(__file__).resolve().parents[1] / "docs" / "PRODUCTION_RUNBOOK.md").read_text(encoding="utf-8")

    def test_smoke_audio_fixture_is_a_small_valid_wav(self):
        decoded = base64.b64decode(smoke_wav_base64(), validate=True)
        with wave.open(io.BytesIO(decoded), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getframerate(), 44100)
            self.assertEqual(wav_file.getnframes(), 22050)
        self.assertLess(len(decoded), 64 * 1024)

    def test_smoke_source_file_fixtures_round_trip_through_document_extractors(self):
        expected = {
            "smoke.txt": "text/plain",
            "smoke.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "smoke.epub": "application/epub+zip",
            "smoke.pdf": "application/pdf",
        }
        for filename, _file_content_type, media_type, fixture in smoke_source_file_fixtures():
            extracted = extract_document(filename, fixture)
            self.assertEqual(extracted.media_type, expected[filename])
            self.assertEqual(media_type, expected[filename])
            self.assertGreaterEqual(len(extracted.text), 12)

    def test_billing_signature_matches_timestamp_and_exact_json_body(self):
        payload = {"event_id": "evt-1", "order_id": "order-1", "status": "paid"}
        timestamp = "1700000000"
        expected = hmac.new(
            b"billing-secret-with-at-least-32-chars",
            timestamp.encode() + b"." + json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(billing_signature("billing-secret-with-at-least-32-chars", timestamp, payload), expected)

    def test_stripe_signature_matches_timestamp_and_exact_json_body(self):
        payload = {"id": "evt-1", "type": "checkout.session.completed"}
        timestamp = "1700000000"
        expected_digest = hmac.new(
            b"stripe-webhook-secret-with-at-least-32-chars",
            timestamp.encode() + b"." + json.dumps(payload, ensure_ascii=False).encode(),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(
            stripe_signature("stripe-webhook-secret-with-at-least-32-chars", timestamp, payload),
            f"t={timestamp},v1={expected_digest}",
        )

    def test_adaptation_review_smoke_covers_single_rejection_and_bulk_approval(self):
        self.assertIn('"status": "rejected"', self.STACK_SMOKE_SOURCE)
        self.assertIn('"status": "approved"', self.STACK_SMOKE_SOURCE)
        self.assertIn('/api/projects/{project_id}/adaptation-units/review', self.STACK_SMOKE_SOURCE)
        self.assertIn('"adaptation_rejection_contract": adaptation_rejection_contract', self.STACK_SMOKE_SOURCE)

    def test_billing_smoke_covers_async_cancellation_and_no_credit_issue(self):
        self.assertIn("checkout.session.async_payment_failed", self.STACK_SMOKE_SOURCE)
        self.assertIn("checkout.session.expired", self.STACK_SMOKE_SOURCE)
        self.assertIn('"billing_cancellation_contract": billing_cancellation_contract', self.STACK_SMOKE_SOURCE)
        self.assertIn("billing cancellation must not issue credits", self.STACK_SMOKE_SOURCE)

    def test_billing_smoke_covers_refund_chargeback_and_reinstatement(self):
        self.assertIn("refund.created", self.STACK_SMOKE_SOURCE)
        self.assertIn("charge.dispute.funds_withdrawn", self.STACK_SMOKE_SOURCE)
        self.assertIn("charge.dispute.funds_reinstated", self.STACK_SMOKE_SOURCE)
        self.assertIn('"billing_adjustment_contract": billing_adjustment_contract', self.STACK_SMOKE_SOURCE)
        self.assertIn("billing refund/idempotent credit reversal contract failed", self.STACK_SMOKE_SOURCE)

    def test_smoke_covers_cross_user_project_isolation(self):
        self.assertIn('second_client.expect_http_status(f"/api/projects/{project_id}", 404)', self.STACK_SMOKE_SOURCE)
        self.assertIn('"ownership_contract": ownership_contract', self.STACK_SMOKE_SOURCE)
        self.assertIn('second_client.expect_http_status(f"/api/assets/{images_for_video[0][\'id\']}", 404)', self.STACK_SMOKE_SOURCE)
        self.assertIn('"asset_ownership_contract": asset_ownership_contract', self.STACK_SMOKE_SOURCE)

    def test_smoke_covers_production_source_file_contract(self):
        self.assertIn("--include-source-files", self.STACK_SMOKE_SOURCE)
        self.assertIn("source_file_contract", self.STACK_SMOKE_SOURCE)
        self.assertIn("source file idempotency contract failed", self.STACK_SMOKE_SOURCE)

    def test_smoke_covers_manual_visual_pass_before_video_generation(self):
        self.assertIn('/api/assets/{image[\'id\']}/review/decision', self.STACK_SMOKE_SOURCE)
        self.assertIn('"manual_review_status": manual_review_status', self.STACK_SMOKE_SOURCE)
        self.assertIn('manual visual review idempotency contract failed', self.STACK_SMOKE_SOURCE)

    def test_docs_explain_local_queued_and_billing_smoke_configuration(self):
        for source in (self.README_SOURCE, self.RUNBOOK_SOURCE):
            self.assertIn("STUDIO_INTERNAL_TOKEN", source)
            self.assertIn("STUDIO_SMOKE_INTERNAL_TOKEN", source)
            self.assertIn("STUDIO_BILLING_CHECKOUT_URL", source)
            self.assertIn("STUDIO_SMOKE_BILLING_WEBHOOK_SECRET", source)
            self.assertIn("STUDIO_SMOKE_STRIPE_WEBHOOK_SECRET", source)
            self.assertIn("refund.created", source)
            self.assertIn("charge.dispute.funds_withdrawn", source)
            self.assertIn("charge.dispute.funds_reinstated", source)

    def test_smoke_failure_output_is_fixed_and_redacted(self):
        self.assertIn('raise RuntimeError(f"{method} {path} returned HTTP {exc.code}")', self.STACK_SMOKE_SOURCE)
        self.assertNotIn("detail = exc.read", self.STACK_SMOKE_SOURCE)
        self.assertNotIn("job.get('error')", self.STACK_SMOKE_SOURCE)


if __name__ == "__main__":
    unittest.main()
