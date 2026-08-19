import base64
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import shutil
import wave
import zipfile
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch
from pathlib import Path

from studio_api.service import ServiceError, StudioService
from studio_api.store import PostgresConnection, StudioStore
from studio_api.worker import LocalJobWorker
from studio_api.providers import ComfyUIImageProvider, ComfyUIVideoProvider, GeneratedAsset, LocalPreviewTextProvider, OpenAICompatibleTextProvider, ProviderError, ProviderRegistry
from studio_api.storage import LocalAssetStorage, S3AssetStorage, safe_key
from comic_engine.utils import load_storyboard


class StudioServiceTests(unittest.TestCase):
    def make_service(self):
        directory = tempfile.TemporaryDirectory()
        service = StudioService(StudioStore(Path(directory.name) / "studio.sqlite3"), Path(directory.name) / "assets")
        self.addCleanup(directory.cleanup)
        return service

    @staticmethod
    def approve_visual_review(service, asset_id):
        service.review_asset(asset_id)
        service.manual_review_asset(asset_id, "PASS")

    def test_billing_order_is_provider_neutral_and_webhook_settlement_is_idempotent(self):
        service = self.make_service()
        with patch.dict(os.environ, {"STUDIO_BILLING_PROVIDER": "signed-webhook"}, clear=False):
            order = service.create_billing_order("starter", idempotency_key="billing-smoke-1")
            duplicate = service.create_billing_order("starter", idempotency_key="billing-smoke-1")
            self.assertEqual(order["status"], "pending")
            self.assertEqual(duplicate["id"], order["id"])
            self.assertTrue(duplicate["duplicate"])
            with self.assertRaisesRegex(ServiceError, "another credit package"):
                service.create_billing_order("creator", idempotency_key="billing-smoke-1")

            payload = {
                "event_id": "evt-billing-smoke-1",
                "order_id": order["id"],
                "status": "paid",
                "amount_cents": order["amount_cents"],
                "currency": order["currency"],
            }
            settled = service.settle_billing_webhook(payload)
            replay = service.settle_billing_webhook(payload)
            self.assertEqual(settled["status"], "paid")
            self.assertFalse(settled["duplicate"])
            self.assertTrue(replay["duplicate"])
            self.assertEqual(service.credits()["balance"], 1100)
            purchase_rows = service.store.all("SELECT * FROM credit_transactions WHERE kind = 'purchase'")
            self.assertEqual(len(purchase_rows), 1)

    def test_billing_webhook_rejects_reused_event_id_with_changed_payload(self):
        service = self.make_service()
        with patch.dict(os.environ, {"STUDIO_BILLING_PROVIDER": "signed-webhook"}, clear=False):
            order = service.create_billing_order("starter", idempotency_key="billing-smoke-2")
            service.settle_billing_webhook({"event_id": "evt-billing-smoke-2", "order_id": order["id"], "status": "cancelled"})
            with self.assertRaisesRegex(ServiceError, "different payload"):
                service.settle_billing_webhook(
                    {
                        "event_id": "evt-billing-smoke-2",
                        "order_id": order["id"],
                        "status": "paid",
                        "amount_cents": order["amount_cents"],
                        "currency": order["currency"],
                    }
                )
            self.assertEqual(service.credits()["balance"], 100)

    def test_paid_billing_webhook_requires_order_amount_and_currency(self):
        service = self.make_service()
        with patch.dict(os.environ, {"STUDIO_BILLING_PROVIDER": "signed-webhook"}, clear=False):
            order = service.create_billing_order("starter", idempotency_key="billing-smoke-required-fields")
            with self.assertRaisesRegex(ServiceError, "amount_cents is required"):
                service.settle_billing_webhook({"event_id": "evt-required-fields", "order_id": order["id"], "status": "paid"})
            self.assertEqual(service.credits()["balance"], 100)

    def test_billing_webhook_rejects_infinite_adjustment_amount_without_server_error(self):
        service = self.make_service()
        with patch.dict(os.environ, {"STUDIO_BILLING_PROVIDER": "signed-webhook"}, clear=False):
            order = service.create_billing_order("starter", idempotency_key="billing-infinite-adjustment")
            service.settle_billing_webhook(
                {
                    "event_id": "evt-infinite-adjustment-paid",
                    "order_id": order["id"],
                    "status": "paid",
                    "amount_cents": order["amount_cents"],
                    "currency": order["currency"],
                }
            )
            with self.assertRaisesRegex(ServiceError, "adjustment amount is invalid"):
                service.settle_billing_webhook(
                    {
                        "event_id": "evt-infinite-adjustment",
                        "order_id": order["id"],
                        "status": "adjustment",
                        "adjustment_id": "re_infinite",
                        "adjustment_type": "refund",
                        "amount_cents": float("inf"),
                        "currency": order["currency"],
                    }
                )
            self.assertEqual(service.credits()["balance"], 1100)

    def test_billing_order_exposes_only_server_configured_checkout_url(self):
        service = self.make_service()
        with patch.dict(
            os.environ,
            {
                "STUDIO_BILLING_PROVIDER": "signed-webhook",
                "STUDIO_BILLING_CHECKOUT_URL": "https://pay.example.test/checkout?source=studio&order_id=attacker-value",
            },
            clear=False,
        ):
            order = service.create_billing_order("starter", idempotency_key="billing-checkout-url")
        checkout = urlsplit(order["checkout_url"])
        query = parse_qs(checkout.query)
        self.assertEqual(checkout.scheme, "https")
        self.assertEqual(query["source"], ["studio"])
        self.assertEqual(query["order_id"], [order["id"]])
        self.assertEqual(query["payment_reference"], [order["provider_order_id"]])
        self.assertEqual(query["amount_cents"], [str(order["amount_cents"])])
        self.assertNotIn("attacker-value", order["checkout_url"])

    def test_billing_checkout_service_rejects_http_in_production(self):
        service = self.make_service()
        with patch.dict(
            os.environ,
            {
                "STUDIO_ENV": "production",
                "STUDIO_BILLING_PROVIDER": "signed-webhook",
                "STUDIO_BILLING_CHECKOUT_URL": "http://pay.example.test/checkout",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(ServiceError, "must use HTTPS in production"):
                service.create_billing_order("starter", idempotency_key="billing-production-http")

    def test_stripe_checkout_is_created_once_and_reuses_local_order_idempotency(self):
        service = self.make_service()
        with patch.dict(
            os.environ,
            {
                "STUDIO_BILLING_PROVIDER": "stripe",
                "STRIPE_SECRET_KEY": "sk_test_fixture_secret_that_is_not_a_real_key",
                "STUDIO_BILLING_SUCCESS_URL": "https://studio.example.test/billing/success",
                "STUDIO_BILLING_CANCEL_URL": "https://studio.example.test/billing/cancel",
            },
            clear=False,
        ):
            with patch("studio_api.service.StripeCheckoutAdapter.create_checkout", return_value={"session_id": "cs_test_123", "checkout_url": "https://checkout.stripe.test/cs_test_123"}) as create_checkout:
                order = service.create_billing_order("starter", idempotency_key="stripe-order-1")
                duplicate = service.create_billing_order("starter", idempotency_key="stripe-order-1")
        self.assertEqual(order["checkout_url"], "https://checkout.stripe.test/cs_test_123")
        self.assertEqual(duplicate["id"], order["id"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(create_checkout.call_count, 1)
        create_checkout.assert_called_once()
        self.assertIn(
            f"order_id={order['id']}",
            create_checkout.call_args.kwargs["success_url"],
        )
        self.assertIn(
            f"order_id={order['id']}",
            create_checkout.call_args.kwargs["cancel_url"],
        )

    def test_stripe_webhook_uses_same_provider_neutral_settlement_contract(self):
        service = self.make_service()
        with patch.dict(
            os.environ,
            {
                "STUDIO_BILLING_PROVIDER": "stripe",
                "STRIPE_SECRET_KEY": "sk_test_fixture_secret_that_is_not_a_real_key",
                "STUDIO_BILLING_SUCCESS_URL": "https://studio.example.test/billing/success",
                "STUDIO_BILLING_CANCEL_URL": "https://studio.example.test/billing/cancel",
            },
            clear=False,
        ):
            with patch("studio_api.service.StripeCheckoutAdapter.create_checkout", return_value={"session_id": "cs_test_456", "checkout_url": "https://checkout.stripe.test/cs_test_456"}):
                order = service.create_billing_order("starter", idempotency_key="stripe-order-2")
            payload = {
                "event_id": "evt_stripe_paid_1",
                "order_id": order["id"],
                "status": "paid",
                "amount_cents": order["amount_cents"],
                "currency": order["currency"].lower(),
            }
            settled = service.settle_billing_webhook(payload, "stripe")
            replay = service.settle_billing_webhook(payload, "stripe")
        self.assertEqual(settled["status"], "paid")
        self.assertTrue(replay["duplicate"])
        self.assertEqual(service.credits()["balance"], 1100)

    def test_billing_refund_chargeback_and_reinstatement_are_idempotent_ledger_adjustments(self):
        service = self.make_service()
        with patch.dict(os.environ, {"STUDIO_BILLING_PROVIDER": "signed-webhook"}, clear=False):
            order = service.create_billing_order("starter", idempotency_key="billing-adjustment-refund")
            paid = service.settle_billing_webhook(
                {
                    "event_id": "evt-adjustment-paid",
                    "order_id": order["id"],
                    "status": "paid",
                    "amount_cents": order["amount_cents"],
                    "currency": order["currency"],
                }
            )
            refund = service.settle_billing_webhook(
                {
                    "event_id": "evt-adjustment-refund",
                    "order_id": order["id"],
                    "status": "adjustment",
                    "adjustment_id": "re_adjustment_1",
                    "adjustment_type": "refund",
                    "amount_cents": order["amount_cents"],
                    "currency": order["currency"],
                }
            )
            refund_replay = service.settle_billing_webhook(
                {
                    "event_id": "evt-adjustment-refund",
                    "order_id": order["id"],
                    "status": "adjustment",
                    "adjustment_id": "re_adjustment_1",
                    "adjustment_type": "refund",
                    "amount_cents": order["amount_cents"],
                    "currency": order["currency"],
                }
            )
            self.assertEqual(paid["status"], "paid")
            self.assertEqual(refund["billing_adjustment"]["credits_delta"], -order["credits"])
            self.assertTrue(refund_replay["duplicate"])
            self.assertEqual(service.credits()["balance"], 100)

            chargeback_order = service.create_billing_order("starter", idempotency_key="billing-adjustment-dispute")
            service.settle_billing_webhook(
                {
                    "event_id": "evt-adjustment-dispute-paid",
                    "order_id": chargeback_order["id"],
                    "status": "paid",
                    "amount_cents": chargeback_order["amount_cents"],
                    "currency": chargeback_order["currency"],
                }
            )
            withdrawn_payload = {
                "event_id": "evt-adjustment-withdrawn",
                "order_id": chargeback_order["id"],
                "status": "adjustment",
                "adjustment_id": "dp_adjustment_1",
                "adjustment_type": "chargeback",
                "amount_cents": chargeback_order["amount_cents"],
                "currency": chargeback_order["currency"],
            }
            withdrawn = service.settle_billing_webhook(withdrawn_payload)
            withdrawn_replay = service.settle_billing_webhook(dict(withdrawn_payload, event_id="evt-adjustment-withdrawn-replay"))
            reinstated_payload = {
                "event_id": "evt-adjustment-reinstated",
                "order_id": chargeback_order["id"],
                "status": "adjustment",
                "adjustment_id": "dp_adjustment_1",
                "adjustment_type": "chargeback_reinstated",
                "amount_cents": chargeback_order["amount_cents"],
                "currency": chargeback_order["currency"],
            }
            reinstated = service.settle_billing_webhook(reinstated_payload)
            reinstated_replay = service.settle_billing_webhook(dict(reinstated_payload, event_id="evt-adjustment-reinstated-replay"))
            self.assertEqual(withdrawn["billing_adjustment"]["credits_delta"], -chargeback_order["credits"])
            self.assertTrue(withdrawn_replay["duplicate"])
            self.assertEqual(reinstated["billing_adjustment"]["credits_delta"], chargeback_order["credits"])
            self.assertTrue(reinstated_replay["duplicate"])
            self.assertEqual(service.credits()["balance"], 1100)
            kinds = {row["kind"] for row in service.credit_transactions()}
            self.assertTrue({"billing_refund", "billing_chargeback", "billing_chargeback_reinstated"}.issubset(kinds))
            listed = {row["id"]: row for row in service.billing_orders()}
            self.assertEqual(len(listed[order["id"]]["adjustments"]), 1)
            self.assertEqual(len(listed[chargeback_order["id"]]["adjustments"]), 2)
            self.assertTrue(all(item["order_id"] in listed for item in listed[order["id"]]["adjustments"] + listed[chargeback_order["id"]]["adjustments"]))
            with self.assertRaisesRegex(ServiceError, "different payload"):
                service.settle_billing_webhook(
                    {
                        "event_id": "evt-adjustment-refund-conflict",
                        "order_id": order["id"],
                        "status": "adjustment",
                        "adjustment_id": "re_adjustment_1",
                        "adjustment_type": "refund",
                        "amount_cents": order["amount_cents"] - 1,
                        "currency": order["currency"],
                    }
                )

    def test_persistent_novel_to_composition_pipeline(self):
        service = self.make_service()
        project = service.create_project("服务层验收", "故事梗概")
        source = service.import_source(project["id"], "chapter.md", "# 第1章\n她推开门。雨声停了！", copyright_acknowledged=True)
        self.assertEqual(source["adaptation_units"], 2)
        self.assertEqual(len(service.list_adaptation_units(project["id"])), 2)
        self.assertEqual(len(service.create_characters(project["id"])), 3)
        episode = service.create_outline(project["id"])[0]
        shots = service.create_shots(episode["id"])
        for shot in shots:
            service.create_prompts(shot["id"])
            image = service.create_image_asset(shot["id"])
            self.assertEqual(image["status"], "ready")
            selected = service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
            self.assertTrue(selected["consistency_confirmed"])
            self.approve_visual_review(service, image["id"])
            video = service.create_video_asset(image["id"])
            self.assertEqual(video["status"], "ready")
        composition = service.compose_episode(episode["id"])
        self.assertEqual(composition["status"], "completed")
        if shutil.which("ffmpeg"):
            self.assertTrue(composition["final_video_url"].endswith(".mp4"))
        self.assertEqual(service.credits()["balance"], 90)

    def test_image_generation_uses_current_prompt_after_shot_edit(self):
        service = self.make_service()
        project = service.create_project("提示词版本门禁", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        first_prompt = service.create_prompts(shot["id"])

        service.update_shot(shot["id"], {"description": "她猛然推开门。"})
        current_prompt = service.create_prompts(shot["id"])
        current_shot = service.store.one("SELECT * FROM shots WHERE id = ?", (shot["id"],))
        self.assertNotEqual(first_prompt["id"], current_prompt["id"])
        self.assertEqual(current_shot["image_prompt_id"], current_prompt["id"])

        image = service.create_image_asset(shot["id"])
        self.assertEqual(image["metadata"]["prompt_id"], current_prompt["id"])
        self.assertEqual(
            image["metadata"]["artifact_revision"],
            service._shot_artifact_revision(current_shot),
        )

        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        self.approve_visual_review(service, image["id"])
        batch = service.create_video_assets(project["id"], [image["id"]])
        self.assertEqual(batch["status"], "completed")
        self.assertEqual(batch["submitted"], 1)

    def test_project_readiness_reports_server_side_dependency_gates(self):
        service = self.make_service()
        project = service.create_project("制作就绪度", "")
        service.import_source(project["id"], "chapter.txt", "她推开门。", copyright_acknowledged=True)
        initial = service.project_readiness(project["id"])
        initial_stages = {item["key"]: item for item in initial["stages"]}
        self.assertTrue(initial_stages["source"]["ready"])
        self.assertFalse(initial_stages["adaptation"]["ready"])
        self.assertTrue(initial_stages["adaptation"]["needs_human_review"])
        self.assertFalse(initial["ready_for_video"])

        service.create_characters(project["id"])
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        before_review = service.project_readiness(project["id"])
        before_stages = {item["key"]: item for item in before_review["stages"]}
        self.assertTrue(before_stages["structure"]["ready"])
        self.assertTrue(before_stages["keyframes"]["ready"])
        self.assertFalse(before_stages["visual-review"]["ready"])

        service.review_asset(image["id"])
        pending_review = service.project_readiness(project["id"])
        pending_review_stage = {item["key"]: item for item in pending_review["stages"]}["visual-review"]
        self.assertFalse(pending_review_stage["ready"])
        self.assertEqual(pending_review_stage["status"], "needs-review")
        self.assertTrue(pending_review_stage["needs_human_review"])
        service.manual_review_asset(image["id"], "PASS")
        service.create_video_asset(image["id"])
        service.compose_episode(episode["id"])
        completed = service.project_readiness(project["id"])
        completed_stages = {item["key"]: item for item in completed["stages"]}
        self.assertTrue(completed_stages["visual-review"]["ready"])
        self.assertTrue(completed_stages["videos"]["ready"])
        self.assertTrue(completed_stages["composition"]["ready"])
        self.assertTrue(completed["ready_for_composition"])
        self.assertFalse(completed["ready_for_delivery"])
        self.assertTrue(completed["needs_human_review"])

        service.review_adaptation_units(project["id"], status="approved")
        approved_without_reference = service.project_readiness(project["id"])
        approved_without_reference_stages = {item["key"]: item for item in approved_without_reference["stages"]}
        self.assertTrue(approved_without_reference_stages["adaptation"]["ready"])
        self.assertFalse(approved_without_reference_stages["adaptation"]["needs_human_review"])
        self.assertFalse(approved_without_reference_stages["references"]["ready"])
        self.assertFalse(approved_without_reference["ready_for_delivery"])
        self.assertFalse(approved_without_reference["needs_human_review"])

        service.create_character_references(project["id"])
        approved = service.project_readiness(project["id"])
        approved_stages = {item["key"]: item for item in approved["stages"]}
        self.assertTrue(approved_stages["references"]["ready"])
        self.assertTrue(approved["ready_for_video"])
        self.assertTrue(approved["ready_for_composition"])
        self.assertTrue(approved["ready_for_delivery"])

    def test_story_synopsis_project_can_reach_delivery_without_adaptation_units(self):
        service = self.make_service()
        project = service.create_project("故事梗概直达交付", "旧宅门后传来呼唤，她决定推门查看。")
        initial = service.project_readiness(project["id"])
        adaptation_stage = next(item for item in initial["stages"] if item["key"] == "adaptation")
        self.assertTrue(adaptation_stage["ready"])
        self.assertFalse(adaptation_stage["required"])
        self.assertEqual(adaptation_stage["detail"], "纯故事梗概项目，无需小说改编")

        service.create_characters(project["id"])
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        self.approve_visual_review(service, image["id"])
        service.create_character_references(project["id"])
        service.create_video_asset(image["id"])
        service.compose_episode(episode["id"])

        completed = service.project_readiness(project["id"])
        self.assertTrue(completed["ready_for_video"])
        self.assertTrue(completed["ready_for_composition"])
        self.assertTrue(completed["ready_for_delivery"])

    def test_adaptation_edit_invalidates_structure_and_downstream_media(self):
        service = self.make_service()
        project = service.create_project("改编版本门禁", "")
        service.import_source(project["id"], "chapter.txt", "她推开门。", copyright_acknowledged=True)
        service.review_adaptation_units(project["id"], status="approved")
        service.create_characters(project["id"])
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        self.approve_visual_review(service, image["id"])
        service.create_video_asset(image["id"])
        service.compose_episode(episode["id"])
        original_revision = str(service.store.one("SELECT adaptation_revision FROM shots WHERE id = ?", (shot["id"],))["adaptation_revision"])
        original_artifact_revision = str(image["metadata"]["artifact_revision"])

        unit = service.list_adaptation_units(project["id"])[0]
        service.update_adaptation_unit(
            unit["id"],
            {"adapted_text": "她猛然推开那扇沉重的门。", "status": "approved"},
        )
        stale = service.project_readiness(project["id"])
        stale_stages = {item["key"]: item for item in stale["stages"]}
        self.assertFalse(stale_stages["structure"]["ready"])
        self.assertTrue(stale_stages["structure"]["stale"])
        self.assertFalse(stale_stages["keyframes"]["ready"])
        self.assertFalse(stale_stages["videos"]["ready"])
        self.assertFalse(stale_stages["composition"]["ready"])
        self.assertFalse(stale["ready_for_delivery"])
        with self.assertRaisesRegex(ServiceError, "structure is outdated"):
            service.create_prompts(shot["id"])

        rebuilt = service.create_shots(episode["id"])
        self.assertEqual(len(rebuilt), 1)
        rebuilt_row = service.store.one("SELECT adaptation_revision, image_prompt_id, status FROM shots WHERE id = ?", (shot["id"],))
        self.assertNotEqual(str(rebuilt_row["adaptation_revision"]), original_revision)
        self.assertIsNone(rebuilt_row["image_prompt_id"])
        self.assertEqual(rebuilt_row["status"], "active")
        self.assertEqual(str(image["metadata"]["artifact_revision"]), original_artifact_revision)
        with self.assertRaisesRegex(ServiceError, "image asset is outdated"):
            service.create_video_asset(image["id"])

    def test_adaptation_review_or_rejection_blocks_downstream_media_until_resolved(self):
        service = self.make_service()
        project = service.create_project("改编审校下游门禁", "")
        service.import_source(project["id"], "chapter.txt", "她推开门。", copyright_acknowledged=True)
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        self.approve_visual_review(service, image["id"])
        service.create_video_asset(image["id"])
        unit = service.list_adaptation_units(project["id"])[0]

        for status in ("review", "rejected"):
            service.update_adaptation_unit(unit["id"], {"status": status})
            with self.assertRaisesRegex(ServiceError, "resolve adaptation review"):
                service.create_prompts(shot["id"])
            with self.assertRaisesRegex(ServiceError, "resolve adaptation review"):
                service.create_video_asset(image["id"])
            with self.assertRaisesRegex(ServiceError, "resolve adaptation review"):
                service.compose_episode(episode["id"])

        service.update_adaptation_unit(unit["id"], {"status": "approved"})
        composition = service.compose_episode(episode["id"])
        self.assertEqual(composition["status"], "completed")

    def test_story_bible_generation_persists_assets_relations_and_source_traceability(self):
        service = self.make_service()
        project = service.create_project("故事资产抽取", "雨夜里林默回到旧宅，握着祖传怀表。")
        imported = service.import_source(
            project["id"],
            "chapter.txt",
            "# 第1章\n雨夜里林默回到旧宅。\n她握着祖传怀表，等待顾言。",
            copyright_acknowledged=True,
        )
        source_segment_id = service.store.one(
            "SELECT id FROM source_segments WHERE project_id = ? ORDER BY chapter_no, sequence LIMIT 1",
            (project["id"],),
        )["id"]
        character = service.create_characters(project["id"], [{"name": "林默", "role": "protagonist"}])[0]

        class StoryBibleProvider:
            name = "fake-text"
            model = "story-bible-test"

            def complete(self, instruction, json_mode=False):
                self.instruction = instruction
                return json.dumps(
                    {
                        "entities": [
                            {
                                "kind": "location",
                                "name": "旧宅",
                                "description": "林默在雨夜返回的老宅。",
                                "attributes": {"weather": "雨夜"},
                                "source_segment_ids": [source_segment_id],
                            },
                            {
                                "kind": "prop",
                                "name": "祖传怀表",
                                "description": "推动等待与秘密线索的道具。",
                                "source_segment_ids": ["unknown-segment"],
                            },
                        ],
                        "relationships": [
                            {
                                "source_type": "character",
                                "source_name": "林默",
                                "target_type": "entity",
                                "target_name": "旧宅",
                                "relation": "返回",
                                "description": "林默回到旧宅。",
                                "source_segment_ids": [],
                            },
                            {
                                "source_type": "character",
                                "source_name": "不存在的人",
                                "target_type": "entity",
                                "target_name": "旧宅",
                                "relation": "错误关系",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ), {}

        registry = ProviderRegistry.local()
        provider = StoryBibleProvider()
        registry.text = provider
        with patch.object(service, "_providers_for_user", return_value=registry):
            bible = service.generate_story_bible(project["id"])

        self.assertEqual(bible["entity_count"], 2)
        self.assertEqual(bible["relationship_count"], 1)
        self.assertEqual({item["kind"] for item in bible["entities"]}, {"location", "prop"})
        self.assertEqual(bible["relationships"][0]["source_id"], character["id"])
        self.assertEqual(len(bible["entities"][0]["source_segment_ids"]), 1)
        self.assertTrue(any("unknown source segment" in warning for warning in bible["last_run"]["output"]["warnings"]))
        graph = service.graph(project["id"])
        self.assertTrue(any(node["type"] == "location" and node["label"] == "旧宅" for node in graph["nodes"]))
        self.assertTrue(any(edge["relation"] == "返回" for edge in graph["edges"]))

        exported = service.export_project(project["id"])
        self.assertEqual(len(exported["story_entities"]), 2)
        imported_project = service.import_project_bundle(exported)
        imported_bible = imported_project["story_bible"]
        self.assertEqual(imported_bible["entity_count"], 2)
        self.assertEqual(imported_bible["relationship_count"], 1)
        self.assertNotEqual(imported_bible["entities"][0]["id"], bible["entities"][0]["id"])

    def test_story_bible_local_preview_is_explicit_and_entity_review_is_persisted(self):
        service = self.make_service()
        project = service.create_project("本地故事资产", "她走进旧宅。")
        service.import_source(project["id"], "chapter.txt", "她走进旧宅。", copyright_acknowledged=True)
        bible = service.generate_story_bible(project["id"])
        self.assertEqual(bible["last_run"]["provider"], "local")
        self.assertTrue(bible["last_run"]["output"]["fallback"])
        with service.store.connection() as connection:
            connection.execute(
                "INSERT INTO story_entities(id, project_id, kind, name, description, attributes_json, source_segment_ids_json, status, created_at, updated_at) VALUES (?, ?, 'location', '旧宅', '', '{}', '[]', 'draft', '2026-01-01', '2026-01-01')",
                ("story-entity-manual", project["id"]),
            )
        with self.assertRaisesRegex(ServiceError, "does not belong to project"):
            service.update_story_entity("story-entity-manual", {"source_segment_ids": ["foreign-segment"]})
        updated = service.update_story_entity("story-entity-manual", {"status": "approved", "description": "雨夜旧宅"})
        self.assertEqual(updated["status"], "approved")
        self.assertEqual(updated["description"], "雨夜旧宅")
        with self.assertRaisesRegex(ServiceError, "attributes are too large"):
            service.update_story_entity("story-entity-manual", {"attributes": {"oversized": "x" * 8000}})

    def test_story_bible_can_run_as_idempotent_persistent_job(self):
        service = self.make_service()
        project = service.create_project("故事资产异步任务", "雨夜里她走进旧宅。")
        service.import_source(project["id"], "novel.txt", "雨夜里她走进旧宅，握着怀表。", copyright_acknowledged=True)
        first = service.request_story_bible(project["id"], run_now=False, idempotency_key="story-bible-click")
        second = service.request_story_bible(project["id"], run_now=False, idempotency_key="story-bible-click")
        self.assertEqual(first["job"]["kind"], "story-bible")
        self.assertEqual(first["job"]["status"], "queued")
        self.assertEqual(first["job"]["id"], second["job"]["id"])
        self.assertEqual(LocalJobWorker(service.store.path.parent).process_once(), 1)
        completed = service.story_bible(project["id"])
        self.assertEqual(completed["job"]["status"], "completed")
        self.assertEqual(completed["last_run"]["status"], "completed")

    def test_story_bible_job_supports_failure_retry_and_queue_cancel(self):
        service = self.make_service()
        project = service.create_project("故事资产任务控制", "她走进旧宅。")
        service.import_source(project["id"], "novel.txt", "她走进旧宅。", copyright_acknowledged=True)
        pending = service.request_story_bible(project["id"], run_now=False)
        with patch.object(service, "_generate_text_json", side_effect=RuntimeError("provider timeout")):
            failed = service.run_job(pending["job"]["id"])
        self.assertEqual(failed["job"]["status"], "failed")
        retried = service.retry_job(pending["job"]["id"], enqueue_external=False)
        self.assertEqual(retried["job"]["status"], "queued")
        completed = service.run_job(pending["job"]["id"])
        self.assertEqual(completed["job"]["status"], "completed")

        cancellable = service.request_story_bible(project["id"], run_now=False, idempotency_key="story-bible-cancel")
        cancelled = service.cancel_job(cancellable["job"]["id"])
        self.assertEqual(cancelled["job"]["status"], "cancelled")

    def test_project_structure_can_run_as_idempotent_persistent_job(self):
        service = self.make_service()
        project = service.create_project("项目结构异步任务", "她推开门，雨声停了。")
        first = service.request_project_structure(project["id"], run_now=False, idempotency_key="structure-click")
        second = service.request_project_structure(project["id"], run_now=False, idempotency_key="structure-click")
        self.assertEqual(first["job"]["kind"], "structure")
        self.assertEqual(first["job"]["status"], "queued")
        self.assertEqual(first["job"]["id"], second["job"]["id"])
        self.assertEqual(LocalJobWorker(service.store.path.parent).process_once(), 1)
        completed = service.structure_with_job(project["id"])
        self.assertEqual(completed["job"]["status"], "completed")
        self.assertGreaterEqual(len(completed["characters"]), 1)
        self.assertGreaterEqual(len(completed["episodes"]), 1)
        self.assertTrue(all(episode["shots"] for episode in completed["episodes"]))

    def test_structure_generation_requires_adaptation_approval_at_request_and_worker(self):
        service = self.make_service()
        project = service.create_project("结构审校门禁", "")
        service.import_source(project["id"], "chapter.txt", "她推开门。", copyright_acknowledged=True)

        with self.assertRaisesRegex(ServiceError, "approve all adaptation units before generating project structure") as project_error:
            service.request_project_structure(project["id"])
        self.assertEqual(project_error.exception.status_code, 409)
        with self.assertRaisesRegex(ServiceError, "approve all adaptation units before generating project structure"):
            service.request_structure_stage("characters", project["id"])
        self.assertEqual(service.store.one("SELECT COUNT(*) AS count FROM jobs", ()) ["count"], 0)

        service.review_adaptation_units(project["id"], status="approved")
        pending = service.request_project_structure(project["id"], run_now=False, idempotency_key="structure-review-gate")
        service.review_adaptation_units(project["id"], status="rejected")
        failed = service.run_job(pending["job"]["id"])
        self.assertEqual(failed["job"]["status"], "failed")
        self.assertIn("approve all adaptation units before generating project structure", failed["job"]["error"])

    def test_job_progress_is_persisted_and_completion_sets_one_hundred(self):
        service = self.make_service()
        project = service.create_project("任务进度", "她推开门。")
        pending = service.request_project_structure(project["id"], run_now=False, idempotency_key="progress-structure")
        job_id = pending["job"]["id"]
        self.assertEqual(pending["job"]["progress_percent"], 0)
        self.assertEqual(pending["job"]["progress_message"], "")
        service.run_job(job_id)
        completed = service.store.one("SELECT status, progress_percent, progress_message FROM jobs WHERE id = ?", (job_id,))
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["progress_percent"], 100)
        self.assertEqual(completed["progress_message"], "已完成")

    def test_failed_and_retried_job_progress_resets_to_waiting(self):
        service = self.make_service()
        project = service.create_project("任务进度重试", "她走进旧宅。")
        service.import_source(project["id"], "novel.txt", "她走进旧宅。", copyright_acknowledged=True)
        pending = service.request_story_bible(project["id"], run_now=False, idempotency_key="progress-retry")
        with patch.object(service, "_generate_text_json", side_effect=RuntimeError("provider timeout")):
            failed = service.run_job(pending["job"]["id"])
        self.assertEqual(failed["job"]["status"], "failed")
        failed_row = service.store.one("SELECT progress_percent, progress_message FROM jobs WHERE id = ?", (pending["job"]["id"],))
        self.assertEqual(failed_row["progress_message"], "处理失败")
        retried = service.retry_job(pending["job"]["id"], enqueue_external=False)
        self.assertEqual(retried["job"]["status"], "queued")
        retried_row = service.store.one("SELECT progress_percent, progress_message FROM jobs WHERE id = ?", (pending["job"]["id"],))
        self.assertEqual(dict(retried_row), {"progress_percent": 0, "progress_message": "等待处理"})

    def test_legacy_structure_stage_jobs_are_idempotent_and_recoverable(self):
        service = self.make_service()
        project = service.create_project("兼容结构阶段任务", "她推开门，雨声停了。")

        characters = service.request_structure_stage("characters", project["id"], run_now=False, idempotency_key="legacy-characters")
        replayed_characters = service.request_structure_stage("characters", project["id"], run_now=False, idempotency_key="legacy-characters")
        self.assertEqual(characters["job"]["kind"], "structure")
        self.assertEqual(json.loads(characters["job"]["payload_json"])["stage"], "characters")
        self.assertEqual(characters["job"]["id"], replayed_characters["job"]["id"])
        completed_characters = service.run_job(characters["job"]["id"])
        self.assertTrue(completed_characters["characters"])
        self.assertEqual(completed_characters["job"]["status"], "completed")

        outline = service.request_structure_stage("outline", project["id"], run_now=False, idempotency_key="legacy-outline")
        completed_outline = service.run_job(outline["job"]["id"])
        self.assertTrue(completed_outline["episodes"])
        episode_id = completed_outline["episodes"][0]["id"]

        shots = service.request_structure_stage("shots", episode_id, run_now=False, idempotency_key="legacy-shots")
        completed_shots = service.run_job(shots["job"]["id"])
        self.assertEqual(completed_shots["id"], episode_id)
        self.assertTrue(completed_shots["shots"])
        self.assertEqual(completed_shots["job"]["status"], "completed")

    def test_async_image_job_defers_missing_prompt_provider_call_to_worker(self):
        service = self.make_service()
        project = service.create_project("图片提示词延迟任务", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        with patch.object(service, "create_prompts", wraps=service.create_prompts) as create_prompt:
            pending = service.create_image_asset(shot["id"], run_now=False)
            create_prompt.assert_not_called()
            metadata = json.loads(service.store.one("SELECT metadata_json FROM assets WHERE id = ?", (pending["id"],))["metadata_json"])
            self.assertIsNone(metadata["prompt_id"])
            completed = service.run_job(pending["job"]["id"])
            self.assertEqual(completed["status"], "ready")
            create_prompt.assert_called_once_with(shot["id"], "local-user")
        self.assertIsNotNone(service.store.one("SELECT id FROM image_prompts WHERE shot_id = ?", (shot["id"],)))

    def test_async_prompt_job_defers_text_provider_call_to_worker(self):
        service = self.make_service()
        project = service.create_project("提示词异步任务", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        with patch.object(service, "create_prompts", wraps=service.create_prompts) as create_prompt:
            pending = service.request_prompt_generation(shot["id"], run_now=False, idempotency_key="prompt-click")
            self.assertEqual(pending["job"]["kind"], "prompt")
            self.assertEqual(pending["job"]["status"], "queued")
            create_prompt.assert_not_called()
            completed = service.run_job(pending["job"]["id"])
            self.assertEqual(completed["job"]["status"], "completed")
            self.assertEqual(completed["shot_id"], shot["id"])
            create_prompt.assert_called_once_with(shot["id"], "local-user")

    def test_source_import_normalizes_filename_and_preserves_media_type(self):
        service = self.make_service()
        project = service.create_project("来源格式", "")
        with self.assertRaisesRegex(ServiceError, "copyright acknowledgement is required"):
            service.import_source(project["id"], "novel.txt", "第一段。", copyright_acknowledged=False)
        imported = service.import_source(
            project["id"],
            "../novel.docx",
            "第一段。",
            copyright_acknowledged=True,
        )
        self.assertEqual(imported["source_document"]["media_type"], "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        document = service.store.one("SELECT filename, media_type FROM source_documents WHERE project_id = ?", (project["id"],))
        self.assertEqual(document["filename"], "novel.docx")
        self.assertEqual(document["media_type"], "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        with self.assertRaisesRegex(ServiceError, "NUL"):
            service.import_source(project["id"], "novel.txt", "不可见\x00字符", copyright_acknowledged=True)

    def test_project_creation_idempotency_reuses_project_and_rejects_changed_payload(self):
        service = self.make_service()
        first = service.create_project(
            "  项目幂等  ",
            "她推开门。",
            style="国漫写实",
            episode_length="1min",
            idempotency_key="project-create-1",
        )
        duplicate = service.create_project(
            "项目幂等",
            "她推开门。",
            style="国漫写实",
            episode_length="1min",
            idempotency_key="project-create-1",
        )
        self.assertEqual(duplicate["id"], first["id"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(
            service._count("SELECT COUNT(*) AS count FROM projects WHERE user_id = ?", ("local-user",)),
            1,
        )
        with self.assertRaisesRegex(ServiceError, "different project payload") as failure:
            service.create_project(
                "另一个项目",
                "她推开门。",
                idempotency_key="project-create-1",
            )
        self.assertEqual(failure.exception.status_code, 409)

    def test_project_creation_idempotency_is_user_scoped(self):
        service = self.make_service()
        owner = service.create_project("用户 A", idempotency_key="same-project-key", user_id="user-a")
        other = service.create_project("用户 B", idempotency_key="same-project-key", user_id="user-b")
        self.assertNotEqual(owner["id"], other["id"])

    def test_source_import_idempotency_reuses_document_and_rejects_changed_content(self):
        service = self.make_service()
        project = service.create_project("来源幂等", "")
        first = service.import_source(
            project["id"],
            "chapter.txt",
            "她推开门。雨声停了。",
            copyright_acknowledged=True,
            idempotency_key="source-import-1",
        )
        duplicate = service.import_source(
            project["id"],
            "different-name.txt",
            "她推开门。雨声停了。",
            copyright_acknowledged=True,
            idempotency_key="source-import-1",
        )
        self.assertEqual(duplicate["source_document"]["id"], first["source_document"]["id"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(
            service._count("SELECT COUNT(*) AS count FROM source_documents WHERE project_id = ?", (project["id"],)),
            1,
        )
        self.assertEqual(
            service._count("SELECT COUNT(*) AS count FROM adaptation_units WHERE project_id = ?", (project["id"],)),
            first["adaptation_units"],
        )
        with self.assertRaisesRegex(ServiceError, "different source content"):
            service.import_source(
                project["id"],
                "chapter.txt",
                "另一段内容。",
                copyright_acknowledged=True,
                idempotency_key="source-import-1",
            )

    def test_source_import_idempotency_collision_maps_changed_content_to_409(self):
        service = self.make_service()
        project = service.create_project("来源并发冲突", "")
        existing = service.import_source(project["id"], "first.txt", "首个内容。", copyright_acknowledged=True)
        canonical_key = service._canonical_idempotency_key("local-user", "race-key")
        with service.store.connection() as connection:
            connection.execute(
                "UPDATE source_documents SET idempotency_key = ? WHERE id = ?",
                (canonical_key, existing["source_document"]["id"]),
            )
        original_one = service.store.one
        source_lookup_count = 0

        def hide_existing_on_first_lookup(sql, params=()):
            nonlocal source_lookup_count
            if "FROM source_documents WHERE project_id = ? AND idempotency_key = ?" in sql:
                source_lookup_count += 1
                if source_lookup_count == 1:
                    return None
            return original_one(sql, params)

        with patch.object(service.store, "one", side_effect=hide_existing_on_first_lookup):
            with self.assertRaisesRegex(ServiceError, "different source content") as failure:
                service.import_source(
                    project["id"],
                    "second.txt",
                    "并发写入的另一份内容。",
                    copyright_acknowledged=True,
                    idempotency_key="race-key",
                )
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(source_lookup_count, 2)

    def test_local_asset_file_is_scoped_to_project_owner_and_rejects_traversal(self):
        service = self.make_service()
        owner_project = service.create_project("媒体归属", "她推开门。", user_id="user-owner")
        other_project = service.create_project("另一个项目", "他转身。", user_id="user-other")
        filename = "asset-owned.svg"
        (service.asset_dir / filename).write_text("<svg></svg>", encoding="utf-8")
        timestamp = "2026-08-02T00:00:00+00:00"
        with service.store.connection() as connection:
            connection.execute(
                "INSERT INTO assets(id, project_id, kind, status, url, provider, model, metadata_json, created_at, updated_at) "
                "VALUES (?, ?, 'image', 'ready', ?, 'test', 'test', '{}', ?, ?)",
                ("asset-owned", owner_project["id"], f"/assets/{filename}", timestamp, timestamp),
            )
        self.assertEqual(service.local_asset_file(filename, "user-owner").name, filename)
        with self.assertRaisesRegex(ServiceError, "asset not found"):
            service.local_asset_file(filename, "user-other")
        with self.assertRaisesRegex(ServiceError, "asset not found"):
            service.local_asset_file("../" + filename, "user-owner")
        with self.assertRaisesRegex(ServiceError, "asset not found"):
            service.local_asset_file("missing.svg", "user-owner")
        self.assertNotEqual(owner_project["id"], other_project["id"])

    def test_audio_asset_timeline_and_project_exchange_roundtrip(self):
        service = self.make_service()
        project = service.create_project("音频时间线", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        audio = service.import_audio_asset(
            episode["id"],
            "narration.mp3",
            base64.b64encode(b"audio-placeholder").decode("ascii"),
        )
        self.assertEqual(audio["kind"], "audio")
        self.assertEqual(audio["status"], "ready")
        settings = service.update_composition_settings(
            episode["id"],
            {
                "audio_tracks": [{"asset_id": audio["id"], "start_seconds": 1, "volume": 0.8}],
                "subtitles": [{"start_seconds": 0, "end_seconds": 2.5, "text": "她推开门。"}],
                "narration_text": "雨夜里，她推开旧宅的门。",
            },
        )
        self.assertEqual(settings["audio_tracks"][0]["asset_id"], audio["id"])
        self.assertEqual(settings["narration_text"], "雨夜里，她推开旧宅的门。")
        self.assertEqual(service.get_project(project["id"])["episodes"][0]["composition_settings"]["subtitles"][0]["text"], "她推开门。")
        bundle = service.export_project(project["id"])
        self.assertEqual(len(bundle["audio_assets"]), 1)
        self.assertEqual(bundle["episodes"][0]["composition_settings"]["narration_text"], "雨夜里，她推开旧宅的门。")
        imported = service.import_project_bundle(bundle)
        imported_episode = imported["episodes"][0]
        self.assertEqual(len(imported["audio_assets"]), 1)
        self.assertEqual(len(imported_episode["composition_settings"]["audio_tracks"]), 1)
        self.assertNotEqual(imported_episode["composition_settings"]["audio_tracks"][0]["asset_id"], audio["id"])
        self.assertEqual(imported_episode["composition_settings"]["narration_text"], "雨夜里，她推开旧宅的门。")

    def test_narration_draft_persists_and_drives_generation_when_text_is_omitted(self):
        service = self.make_service()
        project = service.create_project("旁白稿持久化", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        service.create_shots(episode["id"])
        draft = "保存的旁白稿。雨声落在旧宅屋檐。"

        service.update_composition_settings(
            episode["id"],
            {"audio_tracks": [], "subtitles": [], "narration_text": draft},
        )
        self.assertEqual(service.composition_settings(episode["id"])["narration_text"], draft)

        service.update_composition_settings(episode["id"], {"audio_tracks": [], "subtitles": []})
        self.assertEqual(service.composition_settings(episode["id"])["narration_text"], draft)

        narration = service.request_narration(episode["id"], text=None, idempotency_key="narration-draft-contract-1")
        persisted_job = service.store.one("SELECT payload_json FROM jobs WHERE id = ?", (narration["job"]["id"],))
        self.assertEqual(json.loads(persisted_job["payload_json"])["text"], draft)
        self.assertEqual(narration["metadata"]["text_chars"], len(draft))
        self.assertEqual(service.composition_settings(episode["id"])["narration_text"], draft)

    def test_narration_generates_local_wav_commits_credit_and_attaches_timeline(self):
        service = self.make_service()
        project = service.create_project("旁白生成", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        service.create_shots(episode["id"])

        narration = service.request_narration(
            episode["id"],
            text="她推开门，雨声停了。",
            speed=1.25,
            instructions="沉稳、低声叙述",
            idempotency_key="narration-contract-1",
        )
        self.assertEqual(narration["kind"], "audio")
        self.assertEqual(narration["status"], "ready")
        self.assertEqual(narration["provider"], "local")
        self.assertTrue(narration["url"].endswith(".wav"))
        self.assertEqual(service.credits()["balance"], 99)
        job = narration["job"]
        self.assertEqual(job["kind"], "narration")
        self.assertEqual(job["status"], "completed")
        self.assertEqual(narration["metadata"]["speed"], 1.25)
        self.assertTrue(narration["metadata"]["instructions_configured"])
        persisted_job = service.store.one("SELECT payload_json FROM jobs WHERE id = ?", (job["id"],))
        persisted_payload = json.loads(persisted_job["payload_json"])
        self.assertEqual(persisted_payload["speed"], 1.25)
        self.assertEqual(persisted_payload["instructions"], "沉稳、低声叙述")
        settings = service.get_project(project["id"])["episodes"][0]["composition_settings"]
        self.assertEqual(settings["audio_tracks"][0]["asset_id"], narration["id"])
        self.assertEqual(settings["subtitles"][0]["text"], "她推开门，雨声停了。")
        self.assertEqual(settings["subtitles"][0]["start_seconds"], 0.0)
        self.assertGreater(settings["subtitles"][0]["end_seconds"], 0.0)
        with wave.open(str(service.asset_dir / Path(narration["url"]).name), "rb") as audio_file:
            self.assertEqual(audio_file.getnchannels(), 1)
            self.assertEqual(audio_file.getframerate(), 16000)
            self.assertGreater(audio_file.getnframes(), 0)

        duplicate = service.request_narration(
            episode["id"],
            text="她推开门，雨声停了。",
            idempotency_key="narration-contract-1",
        )
        self.assertEqual(duplicate["id"], narration["id"])
        self.assertEqual(service.credits()["balance"], 99)

    def test_narration_rejects_unallowlisted_voice_before_charging(self):
        service = self.make_service()
        project = service.create_project("旁白音色校验", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        service.create_shots(episode["id"])

        with self.assertRaisesRegex(ServiceError, "voice is not an allowed built-in speech voice"):
            service.request_narration(episode["id"], text="旁白", voice="custom-voice")
        self.assertEqual(service.credits()["balance"], 100)

    def test_narration_can_leave_manual_subtitle_timeline_untouched(self):
        service = self.make_service()
        project = service.create_project("手工字幕优先", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        service.create_shots(episode["id"])
        service.update_composition_settings(
            episode["id"],
            {"audio_tracks": [], "subtitles": [{"start_seconds": 0, "end_seconds": 1, "text": "人工字幕"}]},
        )

        service.request_narration(episode["id"], text="旁白不会覆盖人工字幕。", auto_subtitles=True)
        subtitles = service.get_project(project["id"])["episodes"][0]["composition_settings"]["subtitles"]
        self.assertEqual(subtitles, [{"start_seconds": 0.0, "end_seconds": 1.0, "text": "人工字幕"}])

        project_two = service.create_project("关闭自动字幕", "她推开门。")
        episode_two = service.create_outline(project_two["id"])[0]
        service.create_shots(episode_two["id"])
        service.request_narration(episode_two["id"], text="不生成字幕。", auto_subtitles=False)
        subtitles_two = service.get_project(project_two["id"])["episodes"][0]["composition_settings"]["subtitles"]
        self.assertEqual(subtitles_two, [])

    def test_long_narration_is_split_and_merged_into_one_audio_asset(self):
        service = self.make_service()
        project = service.create_project("长旁白", "雨夜。")
        episode = service.create_outline(project["id"])[0]
        service.create_shots(episode["id"])
        text = "她推开门。" * 900

        narration = service.request_narration(
            episode["id"],
            text=text,
            idempotency_key="narration-long-contract-1",
        )

        self.assertEqual(narration["status"], "ready")
        self.assertEqual(narration["provider"], "local")
        self.assertEqual(narration["metadata"]["segment_count"], 2)
        self.assertEqual(sum(narration["metadata"]["segment_char_counts"]), len(text))
        self.assertTrue(narration["metadata"]["merged"])
        self.assertEqual(service.credits()["balance"], 99)
        settings = service.get_project(project["id"])["episodes"][0]["composition_settings"]
        self.assertEqual(len(settings["audio_tracks"]), 1)
        self.assertEqual(settings["audio_tracks"][0]["asset_id"], narration["id"])
        with wave.open(str(service.asset_dir / Path(narration["url"]).name), "rb") as audio_file:
            self.assertGreater(audio_file.getnframes(), 60 * audio_file.getframerate())

        duplicate = service.request_narration(
            episode["id"],
            text=text,
            idempotency_key="narration-long-contract-1",
        )
        self.assertEqual(duplicate["id"], narration["id"])
        self.assertEqual(service.credits()["balance"], 99)

    def test_audio_timeline_requires_remotion_composition_engine(self):
        service = self.make_service()
        project = service.create_project("音频引擎", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        audio = service.import_audio_asset(episode["id"], "narration.wav", base64.b64encode(b"audio-placeholder").decode("ascii"))
        service.update_composition_settings(episode["id"], {"audio_tracks": [{"asset_id": audio["id"]}], "subtitles": []})
        with patch.dict(os.environ, {"STUDIO_COMPOSE_ENGINE": "ffmpeg"}, clear=False):
            with self.assertRaisesRegex(ServiceError, "require STUDIO_COMPOSE_ENGINE=remotion"):
                service.compose_episode(episode["id"])
        self.assertEqual(shot["episode_id"], episode["id"])

    def test_remotion_audio_failure_does_not_fallback_to_silent_video(self):
        service = self.make_service()
        project = service.create_project("音频失败回退", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        self.approve_visual_review(service, image["id"])
        service.create_video_asset(image["id"])
        audio = service.import_audio_asset(episode["id"], "narration.mp3", base64.b64encode(b"audio-placeholder").decode("ascii"))
        service.update_composition_settings(episode["id"], {"audio_tracks": [{"asset_id": audio["id"]}], "subtitles": [{"start_seconds": 0, "end_seconds": 1, "text": "字幕"}]})
        with patch.dict(os.environ, {"STUDIO_COMPOSE_ENGINE": "remotion"}, clear=False):
            with patch.object(service, "_render_remotion", side_effect=RuntimeError("renderer failed")):
                composition = service.compose_episode(episode["id"])
        self.assertIsNone(composition["final_video_url"])
        metadata = json.loads(composition["metadata_json"])
        self.assertEqual(metadata["mode"], "local-playlist")
        self.assertEqual(metadata["audio_tracks"], 1)
        self.assertIn("renderer failed", metadata["remotion_fallback"])

    def test_video_requires_selection_and_confirmation(self):
        service = self.make_service()
        project = service.create_project("状态验收", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        image = service.create_image_asset(shot["id"])
        with self.assertRaises(ServiceError):
            service.create_video_asset(image["id"])

    def test_video_requires_latest_visual_review_pass(self):
        service = self.make_service()
        project = service.create_project("视觉审核门禁", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        with self.assertRaisesRegex(ServiceError, "visual review is required before video generation"):
            service.create_video_asset(image["id"])
        service.review_asset(image["id"])
        with self.assertRaisesRegex(ServiceError, "latest visual review must be PASS"):
            service.create_video_asset(image["id"])
        service.manual_review_asset(image["id"], "PASS")
        video = service.create_video_asset(image["id"])
        self.assertEqual(video["status"], "ready")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for local video preview")
    def test_local_video_preview_uses_adopted_keyframe_bytes(self):
        service = self.make_service()
        project = service.create_project("关键帧视频预览", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        self.approve_visual_review(service, image["id"])
        video = service.create_video_asset(image["id"])
        metadata = service.asset_with_job(video["id"])["metadata"]
        self.assertEqual(metadata["mode"], "local-video-preview")
        self.assertTrue(metadata["source_image_used"])
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            video_path = service.asset_dir / Path(video["url"]).name
            probe = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("video", probe.stdout.split())

    def test_single_composition_idempotency_key_reuses_completed_job(self):
        service = self.make_service()
        project = service.create_project("单集合成幂等", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        self.approve_visual_review(service, image["id"])
        service.create_video_asset(image["id"])
        first = service.request_composition(episode["id"], run_now=True, idempotency_key="single-compose-key")
        second = service.request_composition(episode["id"], run_now=True, idempotency_key="single-compose-key")
        self.assertEqual(first["job"]["id"], second["job"]["id"])
        self.assertEqual(first["id"], second["id"])

    def test_adopting_a_new_keyframe_is_exclusive_and_composition_uses_it(self):
        service = self.make_service()
        project = service.create_project("关键帧候选", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        first = service.create_image_asset(shot["id"])
        second = service.create_image_asset(shot["id"])

        adopted_first = service.patch_asset(first["id"], {"selected": True, "consistency_confirmed": True})
        self.assertTrue(adopted_first["selected"])
        self.assertTrue(adopted_first["consistency_confirmed"])
        self.approve_visual_review(service, first["id"])
        first_video = service.create_video_asset(first["id"])
        adopted_second = service.patch_asset(second["id"], {"selected": True, "consistency_confirmed": True})

        self.assertFalse(service.asset_with_job(first["id"])["selected"])
        self.assertFalse(service.asset_with_job(first["id"])["consistency_confirmed"])
        self.assertTrue(adopted_second["selected"])
        self.assertTrue(adopted_second["consistency_confirmed"])
        self.approve_visual_review(service, second["id"])
        second_video = service.create_video_asset(second["id"])
        self.assertNotEqual(first_video["id"], second_video["id"])

        composition = service.compose_episode(episode["id"])
        playlist = json.loads(Path(service.asset_dir / Path(composition["playlist_url"]).name).read_text(encoding="utf-8"))
        self.assertEqual(playlist[0]["asset_id"], second_video["id"])

    def test_selected_keyframe_has_database_guard_and_repairs_legacy_duplicates(self):
        service = self.make_service()
        project = service.create_project("关键帧数据约束", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        first = service.create_image_asset(shot["id"])
        second = service.create_image_asset(shot["id"])

        with service.store.connection() as connection:
            connection.execute("UPDATE assets SET selected = 1, consistency_confirmed = 1 WHERE id = ?", (first["id"],))
        with self.assertRaises(Exception):
            with service.store.connection() as connection:
                connection.execute("UPDATE assets SET selected = 1 WHERE id = ?", (second["id"],))

        # 模拟 v0.47 之前的旧库：移除索引后写入两个 adopted 值，再让初始化修复。
        with service.store.connection() as connection:
            connection.execute("DROP INDEX idx_assets_single_selected_image")
            connection.execute("UPDATE assets SET selected = 1, consistency_confirmed = 1, updated_at = '2000-01-01T00:00:00Z' WHERE id = ?", (first["id"],))
            connection.execute("UPDATE assets SET selected = 1, consistency_confirmed = 1, updated_at = '2099-01-01T00:00:00Z' WHERE id = ?", (second["id"],))
        service.store.initialize()
        rows = service.store.all("SELECT id, selected, consistency_confirmed FROM assets WHERE shot_id = ? AND kind = 'image' ORDER BY id", (shot["id"],))
        self.assertEqual(sum(bool(row["selected"]) for row in rows), 1)
        adopted = next(row for row in rows if row["selected"])
        self.assertEqual(adopted["id"], second["id"])
        self.assertTrue(adopted["consistency_confirmed"])

    def test_project_settings_update_is_persistent_and_user_scoped(self):
        service = self.make_service()
        project = service.create_project("旧标题", "旧梗概", style="国漫写实", episode_length="1min")
        updated = service.update_project(project["id"], {"title": "新标题", "story": "新的故事梗概", "style": "黑白水墨", "episode_length": "3min"})
        self.assertEqual(updated["title"], "新标题")
        self.assertEqual(updated["story"], "新的故事梗概")
        self.assertEqual(updated["style"], "黑白水墨")
        self.assertEqual(updated["episode_length"], "3min")
        self.assertEqual(service.get_project(project["id"])["title"], "新标题")
        with self.assertRaises(ServiceError):
            service.update_project(project["id"], {"title": "越权标题"}, user_id="another-user")

    def test_creator_can_edit_character_episode_and_shot_with_ownership_checks(self):
        service = self.make_service()
        project = service.create_project("可编辑工作台", "她在雨夜推开门。")
        character = service.create_characters(project["id"], [{"name": "林默", "description": "短发"}])[0]
        updated_character = service.update_character(
            character["id"],
            {"name": "林默（修订）", "description": "短发、深色风衣", "visual_lock": {"prompt": "短发，深色风衣"}},
        )
        self.assertEqual(updated_character["name"], "林默（修订）")
        self.assertEqual(updated_character["visual_lock"]["prompt"], "短发，深色风衣")

        episode = service.create_outline(project["id"])[0]
        updated_episode = service.update_episode(
            episode["id"],
            {"title": "雨夜来客", "conflict": "门外的人知道她的秘密", "target_duration_seconds": 90},
        )
        self.assertEqual(updated_episode["title"], "雨夜来客")
        self.assertEqual(updated_episode["target_duration_seconds"], 90)

        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        updated_shot = service.update_shot(shot["id"], {"description": "她握紧怀表，缓慢推开门。", "duration_seconds": 4.5})
        self.assertEqual(updated_shot["description"], "她握紧怀表，缓慢推开门。")
        self.assertEqual(updated_shot["duration_seconds"], 4.5)
        self.assertIsNone(updated_shot["image_prompt"])

        with self.assertRaises(ServiceError):
            service.update_character(character["id"], {"name": "越权"}, user_id="another-user")
        with self.assertRaises(ServiceError):
            service.update_episode(episode["id"], {"target_duration_seconds": 10})
        with self.assertRaises(ServiceError):
            service.update_shot(shot["id"], {"duration_seconds": 31})
        with self.assertRaises(ServiceError):
            service.update_shot(shot["id"], {"duration_seconds": float("nan")})

    def test_creator_can_edit_image_prompt_and_prompt_is_user_scoped(self):
        service = self.make_service()
        project = service.create_project("提示词编辑", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        prompt = service.create_prompts(shot["id"])
        updated = service.update_image_prompt(prompt["id"], {"prompt": "电影感雨夜，角色握紧怀表。", "negative_prompt": "水印，畸形手指"})
        self.assertEqual(updated["prompt"], "电影感雨夜，角色握紧怀表。")
        self.assertEqual(updated["negative_prompt"], "水印，畸形手指")
        with self.assertRaises(ServiceError):
            service.update_image_prompt(prompt["id"], {"prompt": "越权"}, user_id="another-user")
        with self.assertRaises(ServiceError):
            service.update_image_prompt(prompt["id"], {"prompt": ""})

    def test_narrative_units_and_scenes_form_an_owned_episode_control_plane(self):
        service = self.make_service()
        project = service.create_project("叙事控制平面", "她在雨夜推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]

        unit = service.create_narrative_unit(
            episode["id"],
            {
                "goal": "让主角确认门后的异常",
                "enter_state": "主角尚未察觉危险",
                "exit_state": "主角决定进入旧宅",
                "target_duration_seconds": 12,
                "shot_ids": [shot["id"]],
            },
        )
        scene = service.create_scene(
            episode["id"],
            {
                "unit_id": unit["id"],
                "location_id": "location-old-house",
                "name": "旧宅门廊",
                "time_of_day": "夜",
                "weather": "暴雨",
                "summary": "门廊被冷白闪电照亮。",
                "shot_ids": [shot["id"]],
            },
        )

        self.assertEqual(service.list_narrative_units(episode["id"])[0]["id"], unit["id"])
        self.assertEqual(service.list_scenes(episode["id"])[0]["unit_id"], unit["id"])
        self.assertIn(scene["id"], service.get_narrative_unit(unit["id"])["scene_ids"])
        self.assertEqual(service.get_project(project["id"])["episodes"][0]["scenes"][0]["id"], scene["id"])

        detached = service.update_scene(scene["id"], {"unit_id": None, "weather": "小雨"})
        self.assertIsNone(detached["unit_id"])
        self.assertNotIn(scene["id"], service.get_narrative_unit(unit["id"])["scene_ids"])
        updated_unit = service.update_narrative_unit(unit["id"], {"scene_ids": [scene["id"]]})
        self.assertEqual(updated_unit["scene_ids"], [scene["id"]])

        other_project = service.create_project("其他项目", "另一扇门。")
        other_episode = service.create_outline(other_project["id"])[0]
        with self.assertRaisesRegex(ServiceError, "same episode"):
            service.create_scene(other_episode["id"], {"unit_id": unit["id"]})
        with self.assertRaises(ServiceError):
            service.get_scene(scene["id"], user_id="another-user")

    def test_composition_settings_exposes_audio_structure_gate_without_rejecting_draft(self):
        service = self.make_service()
        project = service.create_project("音频合同接入", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        audio = service.import_audio_asset(
            episode["id"],
            "dialogue.wav",
            base64.b64encode(b"audio-placeholder").decode("ascii"),
        )
        settings = service.update_composition_settings(
            episode["id"],
            {
                "audio_tracks": [
                    {
                        "asset_id": audio["id"],
                        "kind": "dialogue",
                        "source_note": "CosyVoice3",
                    }
                ],
                "subtitles": [{"start_seconds": 0, "end_seconds": 1, "text": "她推开门。", "speaker": "主角"}],
            },
        )
        self.assertEqual(settings["audio_contract"]["status"], "pass")
        self.assertEqual(settings["audio_contract"]["checked"], "structure-only")

        draft = service.update_composition_settings(episode["id"], {"audio_tracks": [], "subtitles": []})
        self.assertEqual(draft["audio_contract"]["status"], "draft")
        self.assertTrue(any("no dialogue" in issue for issue in draft["audio_contract"]["issues"]))
        with self.assertRaisesRegex(ServiceError, "kind is unsupported"):
            service.update_composition_settings(
                episode["id"],
                {"audio_tracks": [{"asset_id": audio["id"], "kind": "not-a-track"}], "subtitles": []},
            )

    def test_project_settings_drive_outline_style_and_default_duration(self):
        service = self.make_service()
        project = service.create_project("配置驱动", "她在雨夜找到一枚旧怀表。", style="黑白水墨", episode_length="3min")

        class CapturingTextProvider:
            name = "capturing"
            model = "capturing-model"

            def complete(self, instruction, json_mode=False):
                self.instruction = instruction
                return json.dumps({"episodes": []}, ensure_ascii=False), {}

        registry = ProviderRegistry.local()
        provider = CapturingTextProvider()
        registry.text = provider
        with patch.object(service, "_providers_for_user", return_value=registry):
            episodes = service.create_outline(project["id"])

        self.assertEqual(episodes[0]["target_duration_seconds"], 180)
        self.assertIn("项目视觉风格为：黑白水墨", provider.instruction)
        self.assertIn("目标单集时长为：180 秒", provider.instruction)

    def test_image_asset_can_bind_and_unbind_ready_character_reference(self):
        service = self.make_service()
        project = service.create_project("角色绑定", "她推开门。")
        character = service.create_characters(project["id"], [{"name": "林默", "role": "protagonist", "description": "短发、深色风衣"}])[0]
        reference = service.create_character_reference(character["id"])
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        image = service.create_image_asset(shot["id"])
        bound = service.attach_character_reference(image["id"], character_id=character["id"])
        self.assertEqual(bound["character_id"], character["id"])
        self.assertEqual(bound["metadata"]["character_reference_id"], reference["id"])
        graph = service.graph(project["id"])
        self.assertTrue(any(edge["relation"] == "uses-reference" for edge in graph["edges"]))
        unbound = service.attach_character_reference(image["id"])
        self.assertIsNone(unbound["character_id"])
        self.assertNotIn("character_reference_id", unbound["metadata"])

    def test_character_reference_is_a_provider_job_with_credit_commit(self):
        service = self.make_service()
        project = service.create_project("角色母版任务", "她推开门。")
        character = service.create_characters(project["id"], [{"name": "林默", "role": "protagonist", "description": "短发、深色风衣", "visual_lock": {"prompt": "短发、深色风衣"}}])[0]
        reference = service.create_character_reference(character["id"])
        self.assertEqual(reference["status"], "ready")
        self.assertEqual(reference["provider"], "local")
        self.assertTrue(reference["front_url"].endswith(".svg"))
        self.assertTrue(reference["side_url"].endswith(".svg"))
        self.assertTrue(reference["back_url"].endswith(".svg"))
        self.assertEqual(reference["job"]["kind"], "character-reference")
        self.assertEqual(reference["job"]["status"], "completed")
        self.assertEqual(service.credits()["balance"], 92)

    def test_user_uploaded_character_reference_views_are_scoped_and_free(self):
        service = self.make_service()
        project = service.create_project("用户角色参考图", "她推开门。")
        character = service.create_characters(project["id"], [{"name": "林默"}])[0]
        image = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
        before = service.credits()["balance"]

        partial = service.upload_character_reference(character["id"], "front", "front.png", image)
        self.assertEqual(partial["status"], "uploaded")
        self.assertEqual(partial["provider"], "user-upload")
        self.assertEqual(service.credits()["balance"], before)

        for view in ("side", "back"):
            uploaded = service.upload_character_reference(character["id"], view, f"{view}.png", image)
        self.assertEqual(uploaded["status"], "ready")
        self.assertTrue(uploaded["front_url"].startswith("/assets/"))
        self.assertTrue((service.asset_dir / Path(uploaded["back_url"]).name).is_file())
        self.assertTrue(service.local_asset_file(Path(uploaded["front_url"]).name, "local-user").is_file())
        service.ensure_user("other-user")
        with self.assertRaisesRegex(ServiceError, "asset not found"):
            service.local_asset_file(Path(uploaded["front_url"]).name, "other-user")

        with self.assertRaisesRegex(ServiceError, "bytes do not match"):
            service.upload_character_reference(character["id"], "front", "front.jpg", image)

    def test_character_reference_batch_reuses_per_character_jobs_and_is_idempotent(self):
        service = self.make_service()
        project = service.create_project("批量角色母版", "她推开门。")
        characters = service.create_characters(project["id"], [{"name": "林默"}, {"name": "顾言"}, {"name": "沈砚"}])
        character_ids = [character["id"] for character in characters]

        first = service.create_character_references(project["id"], character_ids, idempotency_key="batch-click")
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["requested"], 3)
        self.assertEqual(first["submitted"], 3)
        self.assertEqual(first["failed"], 0)
        self.assertTrue(all(item["reference"]["status"] == "ready" for item in first["items"]))
        self.assertEqual(service.credits()["balance"], 76)

        second = service.create_character_references(project["id"], character_ids, idempotency_key="batch-click")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["skipped"], 3)
        self.assertEqual(second["submitted"], 0)
        self.assertEqual(service.credits()["balance"], 76)

        with self.assertRaisesRegex(ServiceError, "character not found"):
            service.create_character_references(project["id"], ["character-from-another-project"])

    def test_character_reference_retry_returns_reference_and_re_reserves_credits(self):
        service = self.make_service()
        project = service.create_project("角色母版重试", "她推开门。")
        character = service.create_characters(project["id"], [{"name": "林默", "description": "深色风衣"}])[0]
        queued = service.create_character_reference(character["id"], run_now=False)

        class FailingImageProvider:
            name = "failing-image"

            def generate(self, asset_id, description, prompt, output_dir):
                raise RuntimeError("provider timeout")

        providers = ProviderRegistry.local()
        providers.image = FailingImageProvider()
        with patch.object(service, "_providers_for_user", return_value=providers):
            failed = service.run_job(queued["job"]["id"])
        self.assertEqual(failed["status"], "failed")
        retried = service.retry_job(queued["job"]["id"])
        self.assertEqual(retried["status"], "pending")
        self.assertEqual(retried["job"]["status"], "queued")
        self.assertEqual(service.credits()["balance"], 92)

    def test_image_batch_preflights_credits_skips_active_assets_and_reuses_child_idempotency(self):
        service = self.make_service()
        project = service.create_project("批量关键帧", "她推开门。雨声停了。")
        episode = service.create_outline(project["id"])[0]
        shots = service.create_shots(episode["id"])
        first = service.create_image_assets(
            project["id"],
            [shot["id"] for shot in shots],
            run_now=False,
            idempotency_key="image-batch-click",
        )
        self.assertEqual(first["status"], "queued")
        self.assertEqual(first["submitted"], len(shots))
        self.assertEqual(first["required_credits"], len(shots) * 3)
        self.assertTrue(all(item["status"] == "pending" for item in first["items"]))
        self.assertEqual(service.credits()["balance"], 100 - len(shots) * 3)

        second = service.create_image_assets(
            project["id"],
            [shot["id"] for shot in shots],
            run_now=False,
            idempotency_key="image-batch-click",
        )
        self.assertEqual(second["skipped"], len(shots))
        self.assertEqual(second["submitted"], 0)
        self.assertEqual(second["required_credits"], 0)
        self.assertEqual(service.credits()["balance"], 100 - len(shots) * 3)

        child_key = f"image-batch-click:shot:{shots[0]['id']}"
        reused = service.create_image_asset(shots[0]["id"], run_now=False, idempotency_key=child_key)
        self.assertEqual(reused["id"], first["items"][0]["asset"]["id"])
        other_project = service.create_project("批量关键帧余额", "她推开门。")
        other_episode = service.create_outline(other_project["id"])[0]
        other_shot = service.create_shots(other_episode["id"])[0]
        with service.store.connection() as connection:
            connection.execute("UPDATE credit_accounts SET balance = 0 WHERE user_id = ?", ("local-user",))
        with self.assertRaisesRegex(ServiceError, "insufficient credits for image generation batch"):
            service.create_image_assets(other_project["id"], [other_shot["id"]], run_now=False)

    def test_video_batch_requires_confirmed_images_preflights_credits_and_skips_existing(self):
        service = self.make_service()
        project = service.create_project("批量视频", "她推开门。雨声停了。")
        episode = service.create_outline(project["id"])[0]
        shots = service.create_shots(episode["id"])
        images = []
        for shot in shots:
            image = service.create_image_asset(shot["id"])
            images.append(service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True}))
            self.approve_visual_review(service, image["id"])
        first = service.create_video_assets(
            project["id"],
            [image["id"] for image in images],
            run_now=False,
            idempotency_key="video-batch-click",
        )
        self.assertEqual(first["status"], "queued")
        self.assertEqual(first["submitted"], len(images))
        self.assertEqual(first["required_credits"], len(images) * 2)
        self.assertTrue(all(item["status"] == "pending" for item in first["items"]))
        self.assertEqual(service.credits()["balance"], 100 - len(images) * 3 - len(images) * 2)

        second = service.create_video_assets(
            project["id"],
            [image["id"] for image in images],
            run_now=False,
            idempotency_key="video-batch-click",
        )
        self.assertEqual(second["skipped"], len(images))
        self.assertEqual(second["submitted"], 0)
        self.assertEqual(second["required_credits"], 0)
        reused = service.create_video_asset(
            images[0]["id"], run_now=False, idempotency_key=f"video-batch-click:asset:{images[0]['id']}"
        )
        self.assertEqual(reused["id"], first["items"][0]["asset"]["id"])

        unconfirmed = service.create_image_asset(shots[0]["id"])
        with self.assertRaisesRegex(ServiceError, "select and confirm image consistency"):
            service.create_video_assets(project["id"], [unconfirmed["id"]], run_now=False)

    def test_composition_batch_submits_skips_completed_and_preserves_project_scope(self):
        service = self.make_service()
        project = service.create_project("批量合成", "她推开门。雨声停了。")
        episodes = service.create_outline(project["id"])
        for episode in episodes:
            shots = service.create_shots(episode["id"])
            for shot in shots:
                image = service.create_image_asset(shot["id"])
                service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
                self.approve_visual_review(service, image["id"])
                service.create_video_asset(image["id"])

        first = service.create_compositions(
            project["id"],
            [episode["id"] for episode in episodes],
            run_now=False,
        )
        self.assertEqual(first["status"], "queued")
        self.assertEqual(first["submitted"], len(episodes))
        self.assertTrue(all(item["status"] == "queued" for item in first["items"]))

        completed = []
        for item in first["items"]:
            result = service.run_job(item["composition"]["job"]["id"])
            completed.append(result)
        self.assertTrue(all(item["status"] == "completed" for item in completed))

        second = service.create_compositions(project["id"], run_now=False)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["submitted"], 0)
        self.assertEqual(second["skipped"], len(episodes))
        other = service.create_project("另一个项目", "她推开门。")
        other_episode = service.create_outline(other["id"])[0]
        with self.assertRaisesRegex(ServiceError, "episode not found"):
            service.create_compositions(project["id"], [other_episode["id"]])

    def test_composition_request_reuses_idempotency_key_and_rejects_cross_episode_reuse(self):
        service = self.make_service()
        project = service.create_project("合成幂等", "她推开门。")
        episodes = service.create_outline(project["id"])
        first = service.request_composition(episodes[0]["id"], run_now=False, idempotency_key="compose-click")
        second = service.request_composition(episodes[0]["id"], run_now=False, idempotency_key="compose-click")
        self.assertEqual(first["job"]["id"], second["job"]["id"])
        other_project = service.create_project("另一个合成项目", "她推开门。")
        other_episode = service.create_outline(other_project["id"])[0]
        with self.assertRaisesRegex(ServiceError, "another composition request"):
            service.request_composition(other_episode["id"], run_now=False, idempotency_key="compose-click")

    def test_character_reference_idempotency_reuses_same_reference_and_rejects_cross_character_reuse(self):
        service = self.make_service()
        project = service.create_project("角色母版幂等", "她推开门。")
        characters = service.create_characters(project["id"], [{"name": "林默"}, {"name": "顾言"}])
        first = service.create_character_reference(characters[0]["id"], run_now=False, idempotency_key="reference-click")
        second = service.create_character_reference(characters[0]["id"], run_now=False, idempotency_key="reference-click")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["job"]["id"], second["job"]["id"])
        with self.assertRaises(ServiceError):
            service.create_character_reference(characters[1]["id"], run_now=False, idempotency_key="reference-click")
        self.assertEqual(service.credits()["balance"], 92)

    def test_registration_sessions_and_project_ownership(self):
        service = self.make_service()
        registered = service.register_user("owner@example.com", "correct-horse")
        user_id = registered["user"]["id"]
        self.assertEqual(service.resolve_user(registered["token"]), user_id)
        logged_in = service.login_user("owner@example.com", "correct-horse")
        project = service.create_project("私有项目", "只属于用户", user_id=user_id)
        self.assertEqual(service.get_project(project["id"], user_id)["id"], project["id"])
        with self.assertRaises(ServiceError):
            service.get_project(project["id"], "another-user")
        with self.assertRaises(ServiceError):
            service.login_user("owner@example.com", "wrong-password")
        self.assertEqual(service.resolve_user(logged_in["token"]), user_id)
        service.logout(logged_in["token"])
        with self.assertRaises(ServiceError):
            service.resolve_user(logged_in["token"])

    def test_logout_all_revokes_every_session_for_user(self):
        service = self.make_service()
        first = service.register_user("sessions@example.com", "correct-horse")
        second = service.login_user("sessions@example.com", "correct-horse")
        self.assertEqual(service.logout_all(first["user"]["id"], first["token"]), 2)
        with self.assertRaises(ServiceError):
            service.resolve_user(first["token"])
        with self.assertRaises(ServiceError):
            service.resolve_user(second["token"])

    def test_session_listing_marks_current_device_and_revoke_is_user_scoped(self):
        service = self.make_service()
        first = service.register_user(
            "devices@example.com",
            "correct-horse",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        )
        second = service.login_user(
            "devices@example.com",
            "correct-horse",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile Safari/604.1",
        )
        sessions = service.list_sessions(first["user"]["id"], first["token"])
        self.assertEqual(len(sessions), 2)
        self.assertEqual({session["device"] for session in sessions}, {"Chrome · macOS", "Safari · iPhone"})
        self.assertEqual(sum(session["current"] for session in sessions), 1)
        self.assertTrue(next(session["current"] for session in sessions if session["device"] == "Chrome · macOS"))
        self.assertTrue(all("token" not in session and "token_hash" not in session for session in sessions))

        second_id = next(session["id"] for session in sessions if session["device"] == "Safari · iPhone")
        self.assertEqual(service.revoke_session(second_id, first["user"]["id"], first["token"]), {"ok": True, "id": second_id})
        with self.assertRaises(ServiceError):
            service.resolve_user(second["token"])
        with self.assertRaisesRegex(ServiceError, "current session cannot be revoked"):
            service.revoke_session(next(session["id"] for session in sessions if session["current"]), first["user"]["id"], first["token"])

        other = service.register_user("other-devices@example.com", "correct-horse")
        with self.assertRaisesRegex(ServiceError, "session not found"):
            service.revoke_session(next(session["id"] for session in sessions if session["current"]), other["user"]["id"], other["token"])
        self.assertEqual(service.resolve_user(first["token"]), first["user"]["id"])

    def test_existing_sqlite_sessions_table_receives_device_metadata_columns(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "legacy.sqlite3"
        with StudioStore(path).connection() as connection:
            connection.executescript(
                """
                CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT, password_salt TEXT, created_at TEXT NOT NULL);
                CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
                """
            )
        store = StudioStore(path)
        store.initialize()
        columns = {row[1] for row in store.all("PRAGMA table_info(sessions)")}
        self.assertTrue({"device_label", "last_seen_at"}.issubset(columns))

    def test_existing_sqlite_composition_settings_receives_narration_draft_column(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "legacy-composition.sqlite3"
        with StudioStore(path).connection() as connection:
            connection.executescript(
                """
                CREATE TABLE composition_settings (
                  episode_id TEXT PRIMARY KEY,
                  audio_tracks_json TEXT NOT NULL DEFAULT '[]',
                  subtitles_json TEXT NOT NULL DEFAULT '[]',
                  updated_at TEXT NOT NULL
                );
                """
            )
        store = StudioStore(path)
        store.initialize()
        columns = {row[1] for row in store.all("PRAGMA table_info(composition_settings)")}
        self.assertIn("narration_text", columns)
        self.assertEqual(store.one("SELECT narration_text FROM composition_settings"), None)

    def test_existing_sqlite_jobs_table_receives_progress_columns(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "legacy-jobs.sqlite3"
        with StudioStore(path).connection() as connection:
            connection.executescript(
                """
                CREATE TABLE jobs (
                  id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  target_id TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending',
                  cost_credits INTEGER NOT NULL DEFAULT 0,
                  attempts INTEGER NOT NULL DEFAULT 0,
                  max_attempts INTEGER NOT NULL DEFAULT 3,
                  error TEXT,
                  provider TEXT NOT NULL DEFAULT 'local',
                  payload_json TEXT NOT NULL DEFAULT '{}',
                  idempotency_key TEXT UNIQUE,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                INSERT INTO jobs(id, user_id, kind, target_id, status, created_at, updated_at)
                VALUES ('legacy-job', 'user-1', 'text', 'project-1', 'completed', '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00');
                """
            )
        store = StudioStore(path)
        store.initialize()
        columns = {row[1] for row in store.all("PRAGMA table_info(jobs)")}
        self.assertTrue({"progress_percent", "progress_message"}.issubset(columns))
        row = store.one("SELECT progress_percent, progress_message FROM jobs WHERE id = 'legacy-job'")
        self.assertEqual(dict(row), {"progress_percent": 100, "progress_message": ""})

    def test_adaptation_rewrite_preview_keeps_source_traceability(self):
        service = self.make_service()
        project = service.create_project("改编预览", "")
        service.import_source(project["id"], "novel.txt", "她走进雨里。", copyright_acknowledged=True)
        service.import_source(project["id"], "novel-v2.txt", "她回头看去。", copyright_acknowledged=True)
        units = service.rewrite_adaptation_units(project["id"], "originalized")
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0]["mode"], "originalized")
        self.assertEqual(units[0]["status"], "review")
        self.assertEqual(units[0]["source_text"], "她走进雨里。")
        self.assertTrue(units[0]["traceability"]["adaptation_preview"])
        self.assertTrue(any(part["type"] == "insert" for part in units[0]["diff"]))
        self.assertNotEqual(units[0]["source_segment_id"], units[1]["source_segment_id"])
        reviewed = service.update_adaptation_unit(units[0]["id"], {"adapted_text": "审核后的镜头表达。", "status": "approved"})
        self.assertEqual(reviewed["status"], "approved")
        self.assertEqual(reviewed["adapted_text"], "审核后的镜头表达。")
        self.assertTrue(reviewed["diff"])
        self.assertIn("last_reviewed_at", reviewed["traceability"])

    def test_adaptation_quality_is_a_heuristic_audit_and_survives_revision_history(self):
        service = self.make_service()
        project = service.create_project("改编相似度审计", "")
        service.import_source(project["id"], "novel.txt", "她走进雨里，握紧旧伞。", copyright_acknowledged=True)
        imported = service.list_adaptation_units(project["id"])[0]
        self.assertEqual(imported["traceability"]["adaptation_quality"]["risk"], "not_applicable")
        rewritten = service.rewrite_adaptation_units(project["id"], "originalized")[0]
        quality = rewritten["traceability"]["adaptation_quality"]
        self.assertEqual(quality["method"], "character-sequence-v1")
        self.assertEqual(quality["risk"], "high")
        self.assertTrue(quality["requires_human_review"])
        self.assertIn("quality", rewritten["revisions"][-1]["metadata"])
        self.assertEqual(rewritten["revisions"][-1]["metadata"]["quality"]["method"], "character-sequence-v1")

        edited = service.update_adaptation_unit(
            rewritten["id"],
            {"adapted_text": "雨幕中的侦探追逐银色列车。", "status": "draft"},
        )
        self.assertEqual(edited["traceability"]["adaptation_quality"]["risk"], "low")
        self.assertTrue(all("disclaimer" in revision["metadata"]["quality"] for revision in edited["revisions"]))

    def test_bulk_adaptation_review_preserves_text_and_updates_selected_units(self):
        service = self.make_service()
        project = service.create_project("批量审校", "")
        service.import_source(project["id"], "novel.txt", "她走进雨里。她没有回头。", copyright_acknowledged=True)
        units = service.rewrite_adaptation_units(project["id"], "originalized")
        original_text = units[0]["adapted_text"]
        reviewed = service.review_adaptation_units(project["id"], [units[0]["id"]])
        self.assertEqual(reviewed["updated"], 1)
        self.assertEqual(reviewed["units"][0]["status"], "approved")
        self.assertEqual(reviewed["units"][0]["adapted_text"], original_text)
        remaining = service.list_adaptation_units(project["id"])[1]
        self.assertNotEqual(remaining["status"], "approved")
        with self.assertRaisesRegex(ServiceError, "adaptation unit not found"):
            service.review_adaptation_units(project["id"], ["missing-unit"])
        with self.assertRaisesRegex(ServiceError, "must be approved or rejected"):
            service.review_adaptation_units(project["id"], status="draft")

    def test_adaptation_revision_history_survives_manual_edit_and_exchange(self):
        service = self.make_service()
        project = service.create_project("改编版本审计", "")
        service.import_source(project["id"], "novel.txt", "她走进雨里。", copyright_acknowledged=True)
        imported = service.list_adaptation_units(project["id"])[0]
        self.assertEqual([revision["version"] for revision in imported["revisions"]], [1])
        rewritten = service.rewrite_adaptation_units(project["id"], "originalized")[0]
        self.assertEqual([revision["version"] for revision in rewritten["revisions"]], [1, 2])
        reviewed = service.update_adaptation_unit(rewritten["id"], {"adapted_text": "人工审核后的镜头表达。", "status": "approved"})
        self.assertEqual([revision["version"] for revision in reviewed["revisions"]], [1, 2, 3])
        self.assertEqual(reviewed["revisions"][-1]["provider"], "manual")
        bundle = service.export_project(project["id"])
        self.assertEqual(len(bundle["adaptation_revisions"]), 3)
        imported_project = service.import_project_bundle(bundle)
        roundtrip = service.list_adaptation_units(imported_project["id"])[0]
        self.assertEqual([revision["version"] for revision in roundtrip["revisions"]], [1, 2, 3])
        self.assertEqual(roundtrip["revisions"][-1]["adapted_text"], "人工审核后的镜头表达。")

    def test_text_rewrite_can_run_as_persistent_job(self):
        service = self.make_service()
        project = service.create_project("改编异步任务", "")
        service.import_source(project["id"], "novel.txt", "她走进雨里。", copyright_acknowledged=True)
        pending = service.request_rewrite(project["id"], "originalized", run_now=False)
        self.assertEqual(pending["job"]["kind"], "text")
        self.assertEqual(pending["job"]["status"], "queued")
        self.assertEqual(LocalJobWorker(service.store.path.parent).process_once(), 1)
        completed = service.text_job_with_job(project["id"])
        self.assertEqual(completed["job"]["status"], "completed")
        self.assertEqual(completed["units"][0]["status"], "review")
        self.assertIn("改编预览", completed["units"][0]["adapted_text"])

    def test_text_rewrite_idempotency_reuses_job_and_rejects_scope_mismatch(self):
        service = self.make_service()
        project = service.create_project("改编幂等", "")
        service.import_source(project["id"], "novel.txt", "她走进雨里。", copyright_acknowledged=True)
        first = service.request_rewrite(project["id"], "originalized", run_now=False, idempotency_key="rewrite-click")
        second = service.request_rewrite(project["id"], "originalized", run_now=False, idempotency_key="rewrite-click")
        self.assertEqual(first["job"]["id"], second["job"]["id"])

        other = service.create_project("另一项目", "")
        service.import_source(other["id"], "novel.txt", "他转身离开。", copyright_acknowledged=True)
        with self.assertRaisesRegex(ServiceError, "another rewrite request"):
            service.request_rewrite(other["id"], "originalized", run_now=False, idempotency_key="rewrite-click")
        with self.assertRaisesRegex(ServiceError, "another rewrite request"):
            service.request_rewrite(project["id"], "faithful", run_now=False, idempotency_key="rewrite-click")

    def test_active_text_job_is_requeued_after_external_handoff_retry(self):
        service = self.make_service()
        project = service.create_project("改编队列恢复", "")
        service.import_source(project["id"], "novel.txt", "她走进雨里。", copyright_acknowledged=True)
        response = MagicMock()
        response.status = 202
        response.__enter__.return_value = response
        with patch.dict(os.environ, {"STUDIO_QUEUE_BACKEND": "bullmq", "STUDIO_ORCHESTRATOR_URL": "http://queue.test"}, clear=False):
            with patch("studio_api.service.urllib.request.urlopen", return_value=response) as enqueue:
                first = service.request_rewrite(project["id"], "originalized", run_now=False)
                second = service.request_rewrite(project["id"], "originalized", run_now=False)
        self.assertEqual(first["job"]["id"], second["job"]["id"])
        self.assertEqual(enqueue.call_count, 2)

    def test_adaptation_diff_keeps_equal_delete_insert_order(self):
        parts = StudioService._adaptation_diff("甲乙丙", "甲丁丙")
        self.assertEqual(parts, [
            {"type": "equal", "text": "甲"},
            {"type": "delete", "text": "乙"},
            {"type": "insert", "text": "丁"},
            {"type": "equal", "text": "丙"},
        ])

    def test_queued_job_is_completed_by_persistent_worker(self):
        service = self.make_service()
        runtime = service.store.path.parent
        project = service.create_project("异步任务", "她走进雨里。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        asset = service.create_image_asset(shot["id"], run_now=False)
        self.assertEqual(asset["status"], "pending")
        self.assertEqual(asset["job"]["status"], "queued")
        self.assertEqual(LocalJobWorker(runtime).process_once(), 1)
        completed = service.asset_with_job(asset["id"])
        self.assertEqual(completed["status"], "ready")
        self.assertEqual(completed["job"]["status"], "completed")

    def test_video_job_passes_materialized_source_keyframe_to_provider(self):
        service = self.make_service()
        project = service.create_project("图生视频输入", "她走进雨里。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        self.approve_visual_review(service, image["id"])

        class RecordingVideoProvider:
            name = "recording-video"

            def __init__(self):
                self.source_image_path = None

            def generate(self, asset_id, shot_id, output_dir, source_image_path=None):
                self.source_image_path = source_image_path
                path = output_dir / f"{asset_id}.mp4"
                path.write_bytes(b"video")
                return GeneratedAsset(f"/assets/{path.name}", {"provider": self.name, "source_image_received": bool(source_image_path)})

        registry = ProviderRegistry.local()
        recorder = RecordingVideoProvider()
        registry.video = recorder
        with patch.object(service, "_providers_for_user", return_value=registry):
            video = service.create_video_asset(image["id"], run_now=False)
            completed = service.run_job(video["job"]["id"])
        self.assertEqual(completed["status"], "ready")
        self.assertIsNotNone(recorder.source_image_path)
        self.assertTrue(recorder.source_image_path.exists())
        self.assertEqual(completed["metadata"]["source_image_received"], True)

    def test_text_video_job_passes_prompt_without_source_keyframe(self):
        service = self.make_service()
        project = service.create_project("文生视频入口", "祁思远走进废弃石桥。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]

        class RecordingTextVideoProvider:
            name = "recording-text-video"

            def __init__(self):
                self.prompt = None
                self.source_image_path = "unset"

            def generate(self, asset_id, shot_id, output_dir, source_image_path=None, prompt=None):
                self.prompt = prompt
                self.source_image_path = source_image_path
                path = output_dir / f"{asset_id}.mp4"
                path.write_bytes(b"text-video")
                return GeneratedAsset(
                    f"/assets/{path.name}",
                    {"provider": self.name, "prompt_received": prompt, "source_image_received": bool(source_image_path)},
                )

        registry = ProviderRegistry.local()
        recorder = RecordingTextVideoProvider()
        registry.video = recorder
        with patch.object(service, "_providers_for_user", return_value=registry):
            video = service.create_text_video_asset(
                shot["id"],
                "3DCG 国风夜雨，青年站在废弃石桥前，镜头缓慢推进",
                run_now=False,
            )
            completed = service.run_job(video["job"]["id"])

        self.assertEqual(completed["status"], "ready")
        self.assertEqual(recorder.prompt, "3DCG 国风夜雨，青年站在废弃石桥前，镜头缓慢推进")
        self.assertIsNone(recorder.source_image_path)
        self.assertEqual(completed["metadata"]["generation_mode"], "t2v")
        self.assertEqual(completed["metadata"]["prompt"], recorder.prompt)

    def test_stale_running_job_is_requeued_and_keeps_its_reservation(self):
        service = self.make_service()
        project = service.create_project("租约恢复", "她走进雨里。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        asset = service.create_image_asset(shot["id"], run_now=False)
        old_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        service.store.write(
            "UPDATE jobs SET status = 'running', attempts = 1, updated_at = ? WHERE id = ?",
            (old_timestamp, asset["job"]["id"]),
        )
        service.store.write("UPDATE assets SET status = 'generating' WHERE id = ?", (asset["id"],))
        self.assertEqual(service.recover_stale_jobs(max_age_seconds=60), 1)
        recovered = service.asset_with_job(asset["id"])
        self.assertEqual(recovered["status"], "pending")
        self.assertEqual(recovered["job"]["status"], "queued")
        self.assertEqual(service.credits()["balance"], 97)
        self.assertEqual(LocalJobWorker(service.store.path.parent).process_once(), 1)
        self.assertEqual(service.asset_with_job(asset["id"])["status"], "ready")

    def test_stale_job_at_retry_limit_fails_and_refunds(self):
        service = self.make_service()
        project = service.create_project("租约终止", "她走进雨里。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        asset = service.create_image_asset(shot["id"], run_now=False)
        old_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        service.store.write(
            "UPDATE jobs SET status = 'running', attempts = 3, updated_at = ? WHERE id = ?",
            (old_timestamp, asset["job"]["id"]),
        )
        service.store.write("UPDATE assets SET status = 'generating' WHERE id = ?", (asset["id"],))
        self.assertEqual(service.recover_stale_jobs(max_age_seconds=60), 1)
        recovered = service.asset_with_job(asset["id"])
        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(recovered["job"]["status"], "failed")
        self.assertEqual(service.credits()["balance"], 100)

    def test_failed_job_retry_re_reserves_credits_explicitly(self):
        service = self.make_service()
        project = service.create_project("失败重试", "她走进雨里。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        asset = service.create_image_asset(shot["id"], run_now=False)
        reservation = service.store.one("SELECT * FROM credit_reservations WHERE job_id = ? AND status = 'reserved'", (asset["job"]["id"],))
        with service.store.connection() as connection:
            service._refund(connection, reservation["id"], "test provider timeout")
        service.store.write("UPDATE jobs SET status = 'running', attempts = 1 WHERE id = ?", (asset["job"]["id"],))
        service.store.write("UPDATE jobs SET status = 'failed', error = 'provider timeout' WHERE id = ?", (asset["job"]["id"],))
        service.store.write("UPDATE assets SET status = 'failed' WHERE id = ?", (asset["id"],))
        retried = service.retry_job(asset["job"]["id"])
        self.assertEqual(retried["status"], "pending")
        self.assertEqual(retried["job"]["status"], "queued")
        self.assertEqual(service.credits()["balance"], 97)
        self.assertEqual(len(service.store.all("SELECT * FROM credit_reservations WHERE job_id = ? AND status = 'reserved'", (asset["job"]["id"],))), 1)

    def test_internal_style_retry_can_requeue_without_recursive_external_enqueue(self):
        service = self.make_service()
        project = service.create_project("内部重试", "她走进雨里。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        asset = service.create_image_asset(shot["id"], run_now=False)
        reservation = service.store.one("SELECT * FROM credit_reservations WHERE job_id = ? AND status = 'reserved'", (asset["job"]["id"],))
        with service.store.connection() as connection:
            service._refund(connection, reservation["id"], "provider failure")
            connection.execute("UPDATE jobs SET status = 'running', attempts = 1 WHERE id = ?", (asset["job"]["id"],))
            connection.execute("UPDATE jobs SET status = 'failed', attempts = 1 WHERE id = ?", (asset["job"]["id"],))
            connection.execute("UPDATE assets SET status = 'failed' WHERE id = ?", (asset["id"],))
        retried = service.retry_job(asset["job"]["id"], enqueue_external=False)
        self.assertEqual(retried["job"]["status"], "queued")
        self.assertEqual(service.credits()["balance"], 97)

    def test_queued_job_can_be_cancelled_and_refunded_once(self):
        service = self.make_service()
        project = service.create_project("取消任务", "她走进雨里。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        asset = service.create_image_asset(shot["id"], run_now=False)
        cancelled = service.cancel_job(asset["job"]["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["job"]["status"], "cancelled")
        self.assertEqual(service.credits()["balance"], 100)
        self.assertEqual(service.cancel_job(asset["job"]["id"])["status"], "cancelled")
        self.assertEqual(service.credits()["balance"], 100)

    def test_job_events_capture_lifecycle_and_enforce_ownership(self):
        service = self.make_service()
        project = service.create_project("任务时间线", "她走进雨里。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        asset = service.create_image_asset(shot["id"], run_now=False)
        job_id = asset["job"]["id"]

        created_events = service.job_events(job_id)
        self.assertEqual(len(created_events), 1)
        self.assertEqual(created_events[0]["event_type"], "created")
        self.assertEqual(created_events[0]["to_status"], "queued")

        service.cancel_job(job_id)
        events = service.job_events(job_id)
        self.assertEqual(
            {(event["from_status"], event["to_status"]) for event in events},
            {(None, "queued"), ("queued", "cancelled")},
        )
        self.assertTrue(any(event["message"] == "cancelled by user" for event in events))
        with self.assertRaises(ServiceError):
            service.job_events(job_id, user_id="another-user")

    def test_composition_can_run_as_a_persistent_job(self):
        service = self.make_service()
        project = service.create_project("异步合成", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        self.approve_visual_review(service, image["id"])
        service.create_video_asset(image["id"])
        pending = service.request_composition(episode["id"], run_now=False)
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["job"]["kind"], "compose")
        self.assertEqual(LocalJobWorker(service.store.path.parent).process_once(), 1)
        completed = service.composition_with_job(episode["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["job"]["status"], "completed")

    def test_generation_idempotency_reuses_asset_and_credit_reservation(self):
        service = self.make_service()
        project = service.create_project("幂等生成", "她走进雨里。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        first = service.create_image_asset(shot["id"], run_now=False, idempotency_key="image-click-1")
        second = service.create_image_asset(shot["id"], run_now=False, idempotency_key="image-click-1")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["job"]["id"], second["job"]["id"])
        self.assertEqual(service.credits()["balance"], 97)
        self.assertEqual(service.store.one("SELECT COUNT(*) AS count FROM jobs WHERE kind = 'image'")["count"], 1)

    def test_idempotent_external_retry_requeues_existing_queued_job(self):
        service = self.make_service()
        project = service.create_project("外部队列恢复", "她走进雨里。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        first = service.create_image_asset(shot["id"], run_now=False, idempotency_key="external-retry-1")
        with patch.dict(os.environ, {"STUDIO_QUEUE_BACKEND": "bullmq"}):
            with patch.object(service, "_enqueue_external") as enqueue:
                second = service.create_image_asset(shot["id"], run_now=False, idempotency_key="external-retry-1")
        self.assertEqual(first["id"], second["id"])
        enqueue.assert_called_once_with(first["job"]["id"], "local-user", "image", first["id"])

    def test_credit_reservation_is_atomic_and_refund_is_idempotent(self):
        service = self.make_service()
        project = service.create_project("积分边界", "她走进雨里。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        service.store.write("UPDATE credit_accounts SET balance = 3 WHERE user_id = ?", ("local-user",))
        first = service.create_image_asset(shot["id"], run_now=False)
        self.assertEqual(service.credits()["balance"], 0)
        with self.assertRaises(ServiceError):
            service.create_image_asset(shot["id"], run_now=False)
        reservation = service.store.one("SELECT id FROM credit_reservations WHERE job_id = ?", (first["job"]["id"],))
        with service.store.connection() as connection:
            service._refund(connection, reservation["id"], "test refund")
            service._refund(connection, reservation["id"], "duplicate test refund")
        self.assertEqual(service.credits()["balance"], 3)

    def test_comfyui_workflow_template_replacement_is_deterministic(self):
        workflow = {"1": {"inputs": {"text": "{{PROMPT}}", "negative": "{{NEGATIVE_PROMPT}}", "client": "{{CLIENT_ID}}"}}, "list": ["{{PROMPT}}"]}
        replaced = ComfyUIImageProvider._replace(workflow, {"{{PROMPT}}": "雨夜", "{{NEGATIVE_PROMPT}}": "低清", "{{CLIENT_ID}}": "asset-1"})
        self.assertEqual(replaced["1"]["inputs"]["text"], "雨夜")
        self.assertEqual(replaced["1"]["inputs"]["client"], "asset-1")
        self.assertEqual(replaced["list"][0], "雨夜")

    def test_comfyui_video_output_discovery_supports_video_nodes(self):
        outputs = {"9": {"videos": [{"filename": "clip.mp4", "subfolder": "", "type": "output"}]}}
        media = ComfyUIVideoProvider._find_media(outputs)
        self.assertEqual(media["filename"], "clip.mp4")

    def test_comfyui_video_uploads_selected_keyframe_before_prompt(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "wan22.json"
            workflow.write_text(
                json.dumps({
                    "load": {"class_type": "LoadImage", "inputs": {"image": "{{IMAGE_REF}}"}},
                    "save": {"class_type": "SaveVideo", "inputs": {"filename": "{{ASSET_ID}}"}},
                }),
                encoding="utf-8",
            )
            source = root / "keyframe.png"
            source.write_bytes(b"fake-png-bytes")
            output_dir = root / "assets"
            requests = []

            def fake_urlopen(request, timeout=0):
                requests.append(request)
                url = getattr(request, "full_url", str(request))
                if url.endswith("/upload/image"):
                    return Response({"name": "uploaded.png", "subfolder": "input", "type": "input"})
                if url.endswith("/prompt"):
                    return Response({"prompt_id": "prompt-1"})
                if "/history/" in url:
                    return Response({"prompt-1": {"status": {"status_str": "success"}, "outputs": {"node": {"videos": [{"filename": "clip.mp4", "subfolder": "", "type": "output"}]}}}})
                return Response(b"fake-mp4")

            provider = ComfyUIVideoProvider("http://comfyui:8188", str(workflow), timeout=1, poll_interval=0.001)
            with patch("studio_api.providers.urllib.request.urlopen", side_effect=fake_urlopen):
                generated = provider.generate("asset-1", "shot-1", output_dir, source_image_path=source)

            self.assertEqual(generated.relative_url, "/assets/asset-1.mp4")
            self.assertEqual(generated.metadata["input_image"]["name"], "uploaded.png")
            upload_body = requests[0].data
            prompt_body = requests[1].data
            self.assertIn(b"fake-png-bytes", upload_body)
            self.assertIn(b"uploaded.png", prompt_body)
            self.assertIn(b"input/uploaded.png", prompt_body)
            self.assertNotIn(b"{{IMAGE_REF}}", prompt_body)

    def test_comfyui_video_rejects_image_workflow_without_source_keyframe(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "ltx.json"
            workflow.write_text(json.dumps({"load": {"inputs": {"image": "{{IMAGE_REF}}"}}}), encoding="utf-8")
            provider = ComfyUIVideoProvider("http://comfyui:8188", str(workflow), timeout=1, poll_interval=0.001)
            with self.assertRaisesRegex(ProviderError, "requires a ready source image"):
                provider.generate("asset-1", "shot-1", Path(directory) / "assets")

    def test_local_asset_storage_keeps_api_url_and_rejects_traversal(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "asset.svg"
        path.write_text("preview", encoding="utf-8")
        storage = LocalAssetStorage(directory.name)
        self.assertEqual(storage.put_file(path, "image/asset.svg"), "/assets/asset.svg")
        restored = Path(directory.name) / "restored.svg"
        self.assertEqual(storage.get_file("image/asset.svg", restored), restored)
        self.assertEqual(restored.read_text(encoding="utf-8"), "preview")
        with self.assertRaises(ValueError):
            safe_key("../secret.txt")

    def test_object_storage_readiness_checks_bucket_access(self):
        storage = object.__new__(S3AssetStorage)
        storage.bucket = "manhua-assets"
        storage.client = MagicMock()
        self.assertTrue(storage.ready())
        storage.client.head_bucket.side_effect = RuntimeError("bucket unavailable")
        self.assertFalse(storage.ready())

    def test_s3_storage_returns_private_api_media_url(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "asset.svg"
        path.write_text("preview", encoding="utf-8")
        storage = object.__new__(S3AssetStorage)
        storage.bucket = "manhua-assets"
        storage.client = MagicMock()
        self.assertEqual(storage.put_file(path, "assets/project-1/asset.svg"), "/assets/asset.svg")
        storage.client.upload_file.assert_called_once()

    def test_object_storage_media_route_is_scoped_and_materializes_private_object(self):
        class MemoryStorage:
            mode = "s3"

            def get_file(self, key, destination):
                self.key = key
                destination.write_text("private object", encoding="utf-8")
                return destination

        service = self.make_service()
        service.storage = MemoryStorage()
        project = service.create_project("私有对象", "她推开门。", user_id="user-owner")
        timestamp = "2026-08-02T00:00:00+00:00"
        with service.store.connection() as connection:
            connection.execute(
                "INSERT INTO assets(id, project_id, kind, status, url, provider, model, metadata_json, created_at, updated_at) "
                "VALUES (?, ?, 'image', 'ready', ?, 'test', 'test', ?, ?, ?)",
                ("asset-private", project["id"], "https://private-storage.test/assets/asset-private.svg", json.dumps({"storage_key": "assets/project-1/asset-private.svg"}), timestamp, timestamp),
            )
        resolved = service.local_asset_file("asset-private.svg", "user-owner")
        self.assertEqual(resolved.read_text(encoding="utf-8"), "private object")
        self.assertEqual(service.storage.key, "assets/project-1/asset-private.svg")
        with self.assertRaisesRegex(ServiceError, "asset not found"):
            service.local_asset_file("asset-private.svg", "user-other")

    def test_asset_url_patch_rejects_unsafe_schemes(self):
        service = self.make_service()
        project = service.create_project("资产 URL", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        asset = service.create_image_asset(shot["id"])
        with self.assertRaises(ServiceError):
            service.patch_asset(asset["id"], {"url": "javascript:alert(1)"})

    def test_object_storage_metadata_supports_review_after_local_cache_eviction(self):
        class MemoryStorage:
            mode = "s3"

            def __init__(self):
                self.objects = {}

            def put_file(self, path, key):
                self.objects[key] = path.read_bytes()
                return f"https://storage.test/{key}"

            def get_file(self, key, destination):
                destination.write_bytes(self.objects[key])
                return destination

        service = self.make_service()
        service.storage = MemoryStorage()
        project = service.create_project("对象存储回读", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        asset = service.create_image_asset(shot["id"])
        local_path = service.asset_dir / Path(asset["url"]).name
        local_path.unlink()
        reviewed = service.review_asset(asset["id"])
        self.assertEqual(reviewed["reviews"][0]["status"], "UNKNOWN")
        self.assertTrue(local_path.exists())

    def test_imported_audio_round_trips_through_object_storage_cache(self):
        class MemoryStorage:
            mode = "s3"

            def __init__(self):
                self.objects = {}

            def put_file(self, path, key):
                self.objects[key] = path.read_bytes()
                return f"https://storage.test/{key}"

            def get_file(self, key, destination):
                destination.write_bytes(self.objects[key])
                return destination

        service = self.make_service()
        service.storage = MemoryStorage()
        project = service.create_project("对象存储音频", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        audio = service.import_audio_asset(episode["id"], "narration.wav", base64.b64encode(b"audio-bytes").decode("ascii"))
        self.assertTrue(audio["url"].startswith("/assets/"))
        self.assertTrue(audio["metadata"]["storage_key"].startswith("audio/"))
        local_path = service.asset_dir / Path(audio["url"]).name
        self.assertTrue(local_path.exists())
        local_path.unlink()
        restored = service._materialize_asset_file(audio)
        self.assertEqual(restored.read_bytes(), b"audio-bytes")

    def test_project_export_contains_traceable_units_without_credentials(self):
        service = self.make_service()
        project = service.create_project("交换包", "故事梗概")
        service.import_source(project["id"], "chapter.txt", "她推开门。", copyright_acknowledged=True)
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        export = service.export_project(project["id"])
        self.assertEqual(export["schema"], "ai-manhua-studio/project-export/v1")
        source_ids = {document["id"] for document in export["source_documents"]}
        self.assertIn(export["adaptation_units"][0]["traceability"]["source_document_id"], source_ids)
        self.assertNotIn("password_hash", json.dumps(export, ensure_ascii=False))
        output = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, output)
        service.write_project_bundle(project["id"], output)
        self.assertTrue((output / "project.json").exists())
        self.assertTrue((output / "project-export.json").exists())
        self.assertTrue((output / "sources" / "chapter.txt").exists())
        storyboard = output / "storyboards" / "episode-001_storyboard.py"
        self.assertTrue(storyboard.exists())
        compile(storyboard.read_text(encoding="utf-8"), str(storyboard), "exec")
        self.assertEqual(len(load_storyboard(storyboard)), 1)
        self.assertIn("storyboards/episode-001_storyboard.py", json.loads((output / "manifest.json").read_text(encoding="utf-8"))["storyboards"])

    def test_project_archive_contains_readable_bundle_and_materialized_media(self):
        service = self.make_service()
        project = service.create_project("可下载/项目", "她推开门。")
        service.import_source(project["id"], "chapter.txt", "她推开门。", copyright_acknowledged=True)
        character = service.create_characters(project["id"], [{"name": "林默", "description": "深色风衣"}])[0]
        reference = service.create_character_reference(character["id"])
        self.assertEqual(reference["status"], "ready")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        self.approve_visual_review(service, image["id"])
        service.create_video_asset(image["id"])
        service.import_audio_asset(episode["id"], "narration.mp3", base64.b64encode(b"audio-placeholder").decode("ascii"))
        service.compose_episode(episode["id"])

        output = Path(tempfile.mkdtemp()) / "project-bundle.zip"
        self.addCleanup(shutil.rmtree, output.parent)
        service.write_project_archive(project["id"], output)
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
            self.assertIn("project-export.json", names)
            self.assertIn("manifest.json", names)
            self.assertIn("storyboards/episode-001_storyboard.py", names)
            self.assertTrue(any(name.startswith("assets/image/") for name in names))
            self.assertTrue(any(name.startswith("assets/video/") for name in names))
            self.assertTrue(any(name.startswith("assets/audio/") for name in names))
            self.assertTrue(any(name.startswith("character-references/") for name in names))
            self.assertTrue(any(name.startswith("compositions/") for name in names))
            self.assertTrue(all(not name.startswith("/") and ".." not in Path(name).parts for name in names))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertGreaterEqual(manifest["binary_asset_count"], 5)
            self.assertNotIn("password_hash", archive.read("project-export.json").decode("utf-8"))

    def test_project_archive_import_rehydrates_binary_assets_and_rekeys_urls(self):
        service = self.make_service()
        project = service.create_project("归档导入", "她推开门。")
        service.import_source(project["id"], "chapter.txt", "她推开门。", copyright_acknowledged=True)
        character = service.create_characters(project["id"], [{"name": "林默", "description": "深色风衣"}])[0]
        reference = service.create_character_reference(character["id"])
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        self.approve_visual_review(service, image["id"])
        service.create_video_asset(image["id"])
        service.import_audio_asset(episode["id"], "narration.mp3", base64.b64encode(b"audio-placeholder").decode("ascii"))
        service.compose_episode(episode["id"])

        output = Path(tempfile.mkdtemp()) / "project-bundle.zip"
        self.addCleanup(shutil.rmtree, output.parent)
        service.write_project_archive(project["id"], output)
        imported = service.import_project_archive(output)

        self.assertNotEqual(imported["id"], project["id"])
        self.assertEqual(imported["archive_import"]["status"], "completed")
        self.assertGreaterEqual(imported["archive_import"]["mounted"], 5)
        imported_project = service.get_project(imported["id"])
        imported_assets = [asset for episode_item in imported_project["episodes"] for shot_item in episode_item["shots"] for asset in shot_item["assets"]]
        self.assertTrue(imported_assets)
        for asset in imported_assets + imported_project["audio_assets"]:
            self.assertTrue(asset["url"].startswith("/assets/"))
            self.assertTrue((service.asset_dir / Path(asset["url"]).name).exists())
        imported_reference = imported_project["characters"][0]["references"][0]
        for view in ("front_url", "side_url", "back_url"):
            self.assertTrue(imported_reference[view].startswith("/assets/"))
            self.assertTrue((service.asset_dir / Path(imported_reference[view]).name).exists())
        imported_composition = imported_project["episodes"][0]["compositions"][0]
        self.assertTrue(imported_composition["playlist_url"].startswith("/assets/"))
        self.assertTrue((service.asset_dir / Path(imported_composition["playlist_url"]).name).exists())
        self.assertTrue(imported_composition["final_video_url"].startswith("/assets/"))
        self.assertTrue((service.asset_dir / Path(imported_composition["final_video_url"]).name).exists())

    def test_project_archive_import_rejects_unsafe_manifest_member_before_import(self):
        service = self.make_service()
        project = service.create_project("不安全归档", "故事梗概")
        bundle = service.export_project(project["id"])
        output = Path(tempfile.mkdtemp()) / "unsafe.zip"
        self.addCleanup(shutil.rmtree, output.parent)
        manifest = {
            "schema": "ai-manhua-studio/project-export/v1",
            "binary_assets": [{"kind": "image", "id": "asset-1", "path": "../escape.png"}],
        }
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("project-export.json", json.dumps(bundle, ensure_ascii=False))
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        with self.assertRaisesRegex(ServiceError, "unsafe"):
            service.import_project_archive(output)
        self.assertEqual(len(service.list_projects()), 1)

    def test_project_export_import_roundtrip_rekeys_entities_and_preserves_traceability(self):
        service = self.make_service()
        project = service.create_project("交换包往返", "故事梗概")
        service.import_source(project["id"], "chapter.txt", "她推开门。雨声停了。", copyright_acknowledged=True)
        character = service.create_characters(project["id"], [{"name": "林默", "role": "protagonist", "description": "深色风衣"}])[0]
        reference = service.create_character_reference(character["id"])
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        exported = service.export_project(project["id"])

        self.assertIn("source_segments", exported)
        imported = service.import_project_bundle(exported)
        self.assertNotEqual(imported["id"], project["id"])
        self.assertEqual(imported["title"], project["title"])
        self.assertEqual(imported["adaptation_unit_count"], len(exported["adaptation_units"]))
        self.assertEqual(len(imported["characters"]), 1)
        self.assertEqual(imported["characters"][0]["name"], "林默")
        self.assertEqual(imported["characters"][0]["references"][0]["status"], "ready")
        self.assertEqual(len(imported["episodes"]), 1)
        self.assertEqual(len(imported["episodes"][0]["shots"]), len(exported["episodes"][0]["shots"]))
        imported_units = service.list_adaptation_units(imported["id"])
        self.assertEqual(imported_units[0]["source_text"], "她推开门。")
        self.assertEqual(imported_units[0]["traceability"]["imported_from_project_id"], project["id"])
        self.assertEqual(imported["imported_from_project_id"], project["id"])
        self.assertNotEqual(reference["id"], imported["characters"][0]["references"][0]["id"])

    def test_project_import_repairs_duplicate_selected_images_and_remaps_video_source(self):
        service = self.make_service()
        project = service.create_project("交换包素材顺序", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        first = service.create_image_asset(shot["id"])
        second = service.create_image_asset(shot["id"])
        service.patch_asset(second["id"], {"selected": True, "consistency_confirmed": True})
        self.approve_visual_review(service, second["id"])
        video = service.create_video_asset(second["id"])
        service.compose_episode(episode["id"])
        bundle = service.export_project(project["id"])
        exported_shot = bundle["episodes"][0]["shots"][0]
        assets = {asset["id"]: asset for asset in exported_shot["assets"]}
        assets[first["id"]]["selected"] = True
        assets[first["id"]]["consistency_confirmed"] = True
        assets[first["id"]]["updated_at"] = "2026-01-01T00:00:00+00:00"
        assets[second["id"]]["updated_at"] = "2026-01-02T00:00:00+00:00"
        exported_shot["assets"] = [assets[video["id"]], assets[first["id"]], assets[second["id"]]]

        imported = service.import_project_bundle(bundle)
        imported_shot = imported["episodes"][0]["shots"][0]
        selected = [asset for asset in imported_shot["assets"] if asset["kind"] == "image" and asset["selected"]]
        imported_video = next(asset for asset in imported_shot["assets"] if asset["kind"] == "video")
        self.assertEqual(len(selected), 1)
        self.assertEqual(imported_video["source_asset_id"], selected[0]["id"])
        imported_composition = imported["episodes"][0]["compositions"][0]
        self.assertIn(json.loads(imported_composition["metadata_json"])["mode"], {"ffmpeg", "local-playlist"})

    def test_project_import_rejects_tampered_source_hash_before_creating_project(self):
        service = self.make_service()
        project = service.create_project("交换包哈希", "故事梗概")
        service.import_source(project["id"], "chapter.txt", "原文内容。", copyright_acknowledged=True)
        exported = service.export_project(project["id"])
        exported["source_documents"][0]["text"] = "被篡改的原文。"
        with self.assertRaises(ServiceError):
            service.import_project_bundle(exported)
        self.assertEqual(len(service.list_projects()), 1)

    def test_local_vision_review_is_unknown_and_does_not_fake_consistency(self):
        service = self.make_service()
        project = service.create_project("视觉审核", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        image = service.create_image_asset(shot["id"])
        reviewed = service.review_asset(image["id"])
        self.assertEqual(reviewed["reviews"][0]["status"], "UNKNOWN")
        self.assertFalse(reviewed["consistency_confirmed"])

    def test_video_qc_persists_machine_evidence_and_keeps_visual_unknown_explicit(self):
        service = self.make_service()
        project = service.create_project("视频自动质检", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        video_path = service.asset_dir / "qc-video.mp4"
        video_path.write_bytes(b"verified-video-bytes")
        timestamp = "2026-08-19T00:00:00+00:00"
        with service.store.connection() as connection:
            connection.execute(
                "INSERT INTO assets(id, project_id, shot_id, kind, status, url, provider, model, metadata_json, created_at, updated_at) "
                "VALUES (?, ?, ?, 'video', 'ready', ?, 'test', 'test', '{}', ?, ?)",
                ("video-qc-1", project["id"], shot["id"], "/assets/qc-video.mp4", timestamp, timestamp),
            )
        digest = hashlib.sha256(video_path.read_bytes()).hexdigest()
        evidence = {
            "sha256": digest,
            "ffprobe": {"duration_sec": 2.0},
            "decoded_fully": True,
            "frame_luma_sequence": [0.2, 0.3, 0.25],
            "audio_rms_sequence": [0.1, 0.2],
            "duration_contract_sec": 2.0,
            "audio_duration_sec": 2.0,
            "resolution_contract": [640, 384],
        }
        result = service.submit_asset_qc("video-qc-1", evidence, idempotency_key="qc-1")
        self.assertEqual(result["auto_qc"]["review_status"], "UNKNOWN")
        self.assertEqual(result["reviews"][0]["provider"], "auto-qc")
        self.assertEqual(result["reviews"][0]["status"], "UNKNOWN")
        replay = service.submit_asset_qc("video-qc-1", evidence, idempotency_key="qc-1")
        self.assertEqual(replay["reviews"][0]["id"], result["reviews"][0]["id"])
        with self.assertRaisesRegex(ServiceError, "another QC evidence"):
            changed = dict(evidence)
            changed["ffprobe"] = {"duration_sec": 2.0, "source": "different-evidence"}
            service.submit_asset_qc("video-qc-1", changed, idempotency_key="qc-1")

    def test_video_qc_fails_closed_on_black_and_silent_media(self):
        service = self.make_service()
        project = service.create_project("视频 QC 失败", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        video_path = service.asset_dir / "qc-fail.mp4"
        video_path.write_bytes(b"fail-video-bytes")
        timestamp = "2026-08-19T00:00:00+00:00"
        with service.store.connection() as connection:
            connection.execute(
                "INSERT INTO assets(id, project_id, shot_id, kind, status, url, provider, model, metadata_json, created_at, updated_at) "
                "VALUES (?, ?, ?, 'video', 'ready', ?, 'test', 'test', '{}', ?, ?)",
                ("video-qc-fail", project["id"], shot["id"], "/assets/qc-fail.mp4", timestamp, timestamp),
            )
        digest = hashlib.sha256(video_path.read_bytes()).hexdigest()
        result = service.submit_asset_qc(
            "video-qc-fail",
            {
                "sha256": digest,
                "ffprobe": {"duration_sec": 2.0},
                "decoded_fully": True,
                "frame_luma_sequence": [0.0, 0.0, 0.0, 0.0],
                "audio_rms_sequence": [0.0, 0.0, 0.0],
                "duration_contract_sec": 2.0,
            },
        )
        self.assertEqual(result["auto_qc"]["review_status"], "FAIL")
        self.assertFalse(result["consistency_confirmed"])
        self.assertTrue(any("black-frame" in issue for issue in result["reviews"][0]["issues"]))

    def test_manual_visual_review_decision_persists_audited_pass(self):
        service = self.make_service()
        project = service.create_project("人工视觉审核", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
        service.review_asset(image["id"])
        decided = service.manual_review_asset(image["id"], "PASS")
        self.assertEqual(decided["reviews"][0]["status"], "PASS")
        self.assertEqual(decided["reviews"][0]["provider"], "manual")
        self.assertEqual(decided["reviews"][0]["model"], "human-review")
        self.assertTrue(decided["consistency_confirmed"])
        replay = service.manual_review_asset(image["id"], "PASS", user_id="local-user", idempotency_key="manual-pass-1")
        replay_again = service.manual_review_asset(image["id"], "PASS", user_id="local-user", idempotency_key="manual-pass-1")
        self.assertEqual(replay_again["reviews"][0]["id"], replay["reviews"][0]["id"])
        self.assertEqual(len([review for review in replay_again["reviews"] if review["provider"] == "manual"]), 2)
        with self.assertRaisesRegex(ServiceError, "another manual review decision"):
            service.manual_review_asset(image["id"], "FAIL", user_id="local-user", idempotency_key="manual-pass-1")
        with self.assertRaises(ServiceError):
            service.manual_review_asset(image["id"], "UNKNOWN")

    def test_project_visual_review_batch_keeps_per_asset_results(self):
        service = self.make_service()
        project = service.create_project("批量视觉审核", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shots = service.create_shots(episode["id"])
        images = []
        for shot in shots:
            images.append(service.create_image_asset(shot["id"]))
        result = service.review_assets(project["id"], [image["id"] for image in images])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reviewed"], len(images))
        self.assertEqual({item["status"] for item in result["items"]}, {"UNKNOWN"})

    def test_async_visual_review_defers_vision_provider_call_to_worker(self):
        service = self.make_service()
        project = service.create_project("异步视觉审核", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        image = service.create_image_asset(shot["id"])

        class CountingVisionProvider:
            name = "counting-vision"
            model = "counting-model"

            def __init__(self):
                self.calls = 0

            def review(self, image_path, prompt):
                self.calls += 1
                self.last_prompt = prompt
                return {"status": "PASS", "issues": [], "raw": "PASS", "provider": self.name, "model": self.model}

        providers = ProviderRegistry.local()
        vision = CountingVisionProvider()
        providers.vision = vision
        with patch.object(service, "_providers_for_user", return_value=providers):
            queued = service.request_asset_review(
                image["id"],
                prompt="检查角色母版一致性",
                audit_type="character-consistency",
                run_now=False,
                idempotency_key="visual-review-click",
            )
            self.assertEqual(queued["job"]["kind"], "vision-review")
            self.assertEqual(queued["job"]["status"], "queued")
            self.assertEqual(vision.calls, 0)
            payload = json.loads(service.store.one("SELECT payload_json FROM jobs WHERE id = ?", (queued["job"]["id"],))["payload_json"])
            self.assertEqual(payload, {"prompt": "检查角色母版一致性", "audit_type": "character-consistency"})

            completed = service.run_job(queued["job"]["id"])
            self.assertEqual(completed["job"]["status"], "completed")
            self.assertEqual(completed["reviews"][0]["status"], "PASS")
            self.assertEqual(vision.calls, 1)
            self.assertIn("检查角色母版一致性", vision.last_prompt)

            replay = service.request_asset_review(
                image["id"],
                prompt="检查角色母版一致性",
                audit_type="character-consistency",
                run_now=False,
                idempotency_key="visual-review-click",
            )
            self.assertEqual(replay["job"]["id"], completed["job"]["id"])
            self.assertEqual(vision.calls, 1)

    def test_failed_visual_review_clears_consistency_confirmation(self):
        service = self.make_service()
        project = service.create_project("视觉审核阻断", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        image = service.create_image_asset(shot["id"])
        service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})

        class FailingVisionProvider:
            name = "test-vision"

            def review(self, image_path, prompt):
                return {"status": "FAIL", "issues": ["角色不一致"], "raw": "FAIL", "provider": self.name, "model": "test"}

        providers = ProviderRegistry.local()
        providers.vision = FailingVisionProvider()
        with patch.object(service, "_providers_for_user", return_value=providers):
            reviewed = service.review_asset(image["id"])
        self.assertEqual(reviewed["reviews"][0]["status"], "FAIL")
        self.assertFalse(reviewed["consistency_confirmed"])

    def test_postgres_store_keeps_placeholder_contract(self):
        self.assertEqual(PostgresConnection._translate("SELECT * FROM jobs WHERE id = ? AND user_id = ?"), "SELECT * FROM jobs WHERE id = %s AND user_id = %s")

    def test_asset_boolean_updates_use_cross_database_values(self):
        service = self.make_service()
        project = service.create_project("跨数据库布尔值", "她推开门。")
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        service.create_prompts(shot["id"])
        asset = service.create_image_asset(shot["id"])
        selected = service.patch_asset(asset["id"], {"selected": True, "consistency_confirmed": True})
        self.assertTrue(selected["selected"])
        self.assertTrue(selected["consistency_confirmed"])
        deselected = service.patch_asset(asset["id"], {"selected": False})
        self.assertFalse(deselected["selected"])
        self.assertFalse(deselected["consistency_confirmed"])

    def test_provider_settings_are_user_scoped_and_non_sensitive(self):
        with patch.dict(os.environ, {"STUDIO_TEXT_PROVIDER": "local"}):
            service = self.make_service()
            service.update_settings({"image": {"provider": "comfyui", "model": "sdxl", "api_key": "must-not-persist"}}, "user-a")
            own = service.settings("user-a")
            other = service.settings("user-b")
        self.assertEqual(own["image"], {"provider": "comfyui", "model": "sdxl"})
        self.assertEqual(own["text"]["provider"], "local")
        self.assertEqual(other["image"]["provider"], "local")
        self.assertNotIn("api_key", json.dumps(own))

    def test_provider_preferences_change_runtime_provider_without_persisting_credentials(self):
        with patch.dict(os.environ, {"STUDIO_TEXT_PROVIDER": "local", "STUDIO_TEXT_BASE_URL": "http://127.0.0.1:9999/v1"}):
            service = self.make_service()
            service.update_settings(
                {"text": {"provider": "openai-compatible", "model": "qwen-test", "base_url": "http://127.0.0.1:9999/v1", "api_key": "drop-me"}},
                "user-a",
            )
            own = service._providers_for_user("user-a")
            other = service._providers_for_user("user-b")
        self.assertIsInstance(own.text, OpenAICompatibleTextProvider)
        self.assertEqual(own.text.model, "qwen-test")
        self.assertEqual(own.text.base_url, "http://127.0.0.1:9999/v1")
        self.assertIsInstance(other.text, LocalPreviewTextProvider)
        self.assertNotIn("drop-me", json.dumps(service.settings("user-a")))

    def test_provider_base_url_rejects_unallowlisted_destination(self):
        with patch.dict(os.environ, {"STUDIO_TEXT_PROVIDER": "local", "STUDIO_TEXT_BASE_URL": "http://127.0.0.1:8000/v1"}):
            service = self.make_service()
            with self.assertRaises(ServiceError):
                service.update_settings({"text": {"base_url": "http://169.254.169.254/latest/meta-data"}}, "user-a")

    def test_production_provider_preferences_cannot_downgrade_or_redirect_runtime_provider(self):
        production_env = {
            "STUDIO_ENV": "production",
            "STUDIO_TEXT_PROVIDER": "openai-compatible",
            "STUDIO_TEXT_BASE_URL": "https://text.example.test/v1",
            "STUDIO_TEXT_MODEL": "text-production-model",
            "STUDIO_TEXT_API_KEY_ENV": "STUDIO_TEXT_API_KEY",
            "STUDIO_TEXT_API_KEY": "text-secret",
            "STUDIO_IMAGE_PROVIDER": "comfyui",
            "COMFYUI_BASE_URL": "http://comfyui:8188",
            "COMFYUI_IMAGE_WORKFLOW": "/app/workflows/image.json",
            "STUDIO_VIDEO_PROVIDER": "comfyui",
            "COMFYUI_VIDEO_WORKFLOW": "/app/workflows/video.json",
            "STUDIO_VISION_PROVIDER": "openai-compatible",
            "STUDIO_VISION_BASE_URL": "https://vision.example.test/v1",
            "STUDIO_VISION_MODEL": "vision-production-model",
            "STUDIO_VISION_API_KEY_ENV": "STUDIO_VISION_API_KEY",
            "STUDIO_VISION_API_KEY": "vision-secret",
        }
        with patch.dict(os.environ, production_env, clear=False):
            service = self.make_service()
            with self.assertRaisesRegex(ServiceError, "cannot override"):
                service.update_settings({"text": {"provider": "local"}}, "user-a")
            with self.assertRaisesRegex(ServiceError, "not allowed"):
                service.update_settings({"text": {"base_url": "https://attacker.example.test/v1"}}, "user-a")

            # 兼容部署升级前已写入数据库的旧偏好：首次解析 provider 时也必须阻断。
            service.store.write(
                "INSERT INTO studio_settings(key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                ("user-a:text", json.dumps({"provider": "local"})),
            )
            with self.assertRaisesRegex(ServiceError, "cannot override"):
                service._providers_for_user("user-a")

    def test_image_prompt_contains_character_lock_and_safe_style_constraints(self):
        service = self.make_service()
        project = service.create_project("角色锁定提示词", "她推开门。")
        service.create_characters(project["id"], [{"name": "林默", "role": "protagonist", "description": "短发、细金属框眼镜、深色风衣", "visual_lock": {"prompt": "短发，细金属框眼镜，深色风衣"}}])
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        prompt = service.create_prompts(shot["id"])
        self.assertIn("林默", prompt["prompt"])
        self.assertIn("细金属框眼镜", prompt["prompt"])
        self.assertIn("东亚黑白漫画", prompt["prompt"])
        self.assertIn("可读文字", prompt["negative_prompt"])

    def test_approved_story_assets_flow_into_shots_and_image_prompts(self):
        service = self.make_service()
        project = service.create_project("故事资产下游约束", "她在雨夜回到旧宅，握着祖传怀表。")
        with service.store.connection() as connection:
            connection.execute(
                "INSERT INTO story_entities(id, project_id, kind, name, description, attributes_json, source_segment_ids_json, status, created_at, updated_at) VALUES (?, ?, 'location', '旧宅', '雨夜中的祖宅', '{\"weather\":\"雨夜\"}', '[]', 'approved', '2026-01-01', '2026-01-01')",
                ("approved-story-entity", project["id"]),
            )
        episode = service.create_outline(project["id"])[0]
        shot = service.create_shots(episode["id"])[0]
        prompt = service.create_prompts(shot["id"])
        self.assertIn("旧宅", prompt["prompt"])
        self.assertIn("雨夜中的祖宅", prompt["prompt"])
        self.assertIn("weather", prompt["prompt"])

        with patch.object(
            service,
            "_generate_text_json",
            return_value={"prompt": "模型忽略了故事设定", "negative_prompt": ""},
        ):
            provider_prompt = service.create_prompts(shot["id"])
        self.assertIn("雨夜中的祖宅", provider_prompt["prompt"])
        self.assertIn("已审核故事资产约束", provider_prompt["prompt"])

        with patch.object(
            service,
            "_generate_text_json",
            return_value={"prompt": "模型输出" * 6000, "negative_prompt": ""},
        ):
            long_provider_prompt = service.create_prompts(shot["id"])
        self.assertLessEqual(len(long_provider_prompt["prompt"]), 6000)
        self.assertIn("雨夜中的祖宅", long_provider_prompt["prompt"])

        with service.store.connection() as connection:
            connection.execute("UPDATE story_entities SET status = 'draft' WHERE id = ?", ("approved-story-entity",))
        shot_after_draft = service.update_shot(shot["id"], {"description": "她握紧怀表。"})
        prompt_after_draft = service.create_prompts(shot_after_draft["id"])
        self.assertIn("不得凭空补写未审核地点、道具或关系", prompt_after_draft["prompt"])
        self.assertNotIn("雨夜中的祖宅", prompt_after_draft["prompt"])

    def test_external_text_provider_drives_structured_creation_with_local_safety_fallbacks(self):
        service = self.make_service()
        project = service.create_project("外部文本创作", "一位侦探在雨夜追查失踪案。")

        class StructuredTextProvider:
            name = "fake-llm"
            model = "fake-model"

            def complete(self, instruction, json_mode=False):
                self.last_instruction = instruction
                self.json_mode = json_mode
                if "输出 JSON 角色" in instruction:
                    return json.dumps({"characters": [{"name": "沈岚", "role": "protagonist", "description": "短发、深色风衣、银色怀表"}]}, ensure_ascii=False), {}
                if "输出 JSON 分集" in instruction:
                    return json.dumps({"episodes": [{"number": 1, "title": "雨夜追踪", "summary": "侦探发现失踪案与旧怀表有关。", "conflict": "线索即将被幕后人销毁。", "hook": "怀表突然响起。", "target_duration_seconds": 45}]}, ensure_ascii=False), {}
                if "输出 JSON 分镜" in instruction:
                    return json.dumps({"shots": [{"sequence": 1, "scene": "雨夜巷口", "emotion": "紧张", "duration_seconds": 4, "description": "沈岚在雨幕中捡起银色怀表。", "adaptation_unit_sequences": []}]}, ensure_ascii=False), {}
                return json.dumps({"prompt": "雨夜巷口，沈岚捡起银色怀表，电影感构图", "negative_prompt": "过曝"}, ensure_ascii=False), {}

        registry = ProviderRegistry.local()
        registry.text = StructuredTextProvider()
        with patch.object(service, "_providers_for_user", return_value=registry):
            characters = service.create_characters(project["id"])
            episode = service.create_outline(project["id"])[0]
            shot = service.create_shots(episode["id"])[0]
            prompt = service.create_prompts(shot["id"])

        self.assertEqual(characters[0]["name"], "沈岚")
        self.assertEqual(episode["title"], "雨夜追踪")
        self.assertEqual(episode["target_duration_seconds"], 45)
        self.assertEqual(shot["scene"], "雨夜巷口")
        self.assertEqual(shot["duration_seconds"], 4)
        self.assertIn("沈岚捡起银色怀表", prompt["prompt"])
        self.assertIn("角色锁定", prompt["prompt"])
        self.assertIn("可读文字", prompt["negative_prompt"])

    def test_structured_shot_generation_replaces_non_finite_duration_with_safe_default(self):
        service = self.make_service()
        project = service.create_project("异常时长响应", "侦探在雨夜追查失踪案。")
        episode = service.create_outline(project["id"])[0]
        with patch.object(
            service,
            "_generate_text_json",
            return_value={
                "shots": [
                    {
                        "sequence": 1,
                        "scene": "雨夜巷口",
                        "emotion": "紧张",
                        "duration_seconds": float("nan"),
                        "description": "侦探在雨幕中停下脚步。",
                        "adaptation_unit_sequences": [float("inf")],
                    }
                ]
            },
        ):
            shot = service.create_shots(episode["id"])[0]
        self.assertEqual(shot["duration_seconds"], 3.0)

    def test_structured_episode_generation_replaces_infinite_number_and_duration(self):
        service = self.make_service()
        project = service.create_project("异常分集响应", "侦探在雨夜追查失踪案。")
        with patch.object(
            service,
            "_generate_text_json",
            return_value={
                "episodes": [
                    {
                        "number": float("inf"),
                        "title": "雨夜追踪",
                        "summary": "侦探在雨幕中发现新的线索。",
                        "conflict": "线索即将被销毁。",
                        "hook": "收据上出现了第二个名字。",
                        "target_duration_seconds": float("-inf"),
                    }
                ]
            },
        ):
            episode = service.create_outline(project["id"])[0]
        self.assertEqual(episode["number"], 1)
        self.assertEqual(episode["target_duration_seconds"], 60)
        with self.assertRaisesRegex(ServiceError, "target_duration_seconds must be an integer"):
            service.update_episode(episode["id"], {"target_duration_seconds": float("inf")})

    def test_project_import_replaces_infinite_episode_numbers_and_durations(self):
        service = self.make_service()
        bundle = {
            "schema": "ai-manhua-studio/project-export/v1",
            "project": {"id": "source-project", "title": "异常交换包", "story": "故事"},
            "source_documents": [],
            "source_segments": [],
            "adaptation_units": [],
            "adaptation_revisions": [],
            "characters": [],
            "character_references": [],
            "episodes": [
                {
                    "id": "episode-1",
                    "number": float("inf"),
                    "title": "第 1 集",
                    "summary": "摘要",
                    "shots": [],
                    "target_duration_seconds": float("inf"),
                }
            ],
            "audio_assets": [],
            "story_entities": [],
            "story_relationships": [],
            "story_bible_runs": [],
        }
        imported = service.import_project_bundle(bundle)
        episode = imported["episodes"][0]
        self.assertEqual(episode["number"], 1)
        self.assertEqual(episode["target_duration_seconds"], 60)

    def test_openai_compatible_text_provider_sends_json_mode_without_persisting_key(self):
        provider = OpenAICompatibleTextProvider("http://127.0.0.1:8000/v1", "test-model", "STUDIO_TEST_TEXT_KEY")
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"choices": [{"message": {"content": "{\"ok\":true}"}}]}).encode()
        with patch.dict(os.environ, {"STUDIO_TEST_TEXT_KEY": "test-key"}), patch("studio_api.providers.urllib.request.urlopen", return_value=response) as request_call:
            content, metadata = provider.complete("输出 JSON", json_mode=True)
        request = request_call.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(json.loads(content), {"ok": True})
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(metadata["provider"], "openai-compatible")

    def test_openai_compatible_rewrite_prompt_has_mode_specific_originalization_guardrail(self):
        provider = OpenAICompatibleTextProvider("http://127.0.0.1:8000/v1", "test-model", "STUDIO_TEST_TEXT_KEY")
        with patch.dict(os.environ, {"STUDIO_TEST_TEXT_KEY": "test-key"}), patch.object(
            provider,
            "complete",
            return_value=("重新组织后的镜头表达。", {"provider": "openai-compatible", "model": "test-model"}),
        ) as complete:
            rewritten, metadata = provider.rewrite("她走进雨里。", "originalized")
        self.assertEqual(rewritten, "重新组织后的镜头表达。")
        self.assertEqual(metadata["mode"], "originalized")
        instruction = complete.call_args.args[0]
        self.assertIn("避免逐句复述", instruction)
        self.assertIn("必须保留来源可追溯性", instruction)


if __name__ == "__main__":
    unittest.main()
