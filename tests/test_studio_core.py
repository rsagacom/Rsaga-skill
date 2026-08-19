import unittest

from studio_core.models import AdaptationMode, Job, JobKind, JobStatus, Project, SourceDocument, to_primitive
from studio_core.novel import adapt_source, split_chapters
from studio_core.workflow import CreditLedger, InvalidTransition, LedgerError, fail_job, refund_failed_job, retry_job, transition_job


class NovelAdaptationTests(unittest.TestCase):
    def test_chapters_and_units_keep_source_offsets(self):
        text = "# 第1章 雨夜\n林默走进巷子。雨声盖过了脚步！\n\n第2章 合约\n她递来一份合约？"
        document = SourceDocument("doc-1", "demo.md", text, "sha256-demo")

        chapters = split_chapters(text)
        bundle = adapt_source(document)

        self.assertEqual([chapter.number for chapter in chapters], [1, 2])
        self.assertEqual(len(bundle.units), 3)
        for unit, segment in zip(bundle.units, bundle.segments):
            self.assertEqual(text[segment.start_offset:segment.end_offset], unit.source_text)
            self.assertEqual(document.id, unit.traceability["source_document_id"])
            self.assertGreaterEqual(segment.line_end, segment.line_start)

    def test_transform_is_explicit_and_traceable(self):
        document = SourceDocument("doc-2", "demo.txt", "她推开门。", "sha256-demo")
        bundle = adapt_source(
            document,
            mode=AdaptationMode.ORIGINALIZED,
            transform=lambda source, _segment: f"改编：{source}",
        )

        self.assertEqual(bundle.units[0].adapted_text, "改编：她推开门。")
        self.assertEqual(bundle.units[0].mode, AdaptationMode.ORIGINALIZED)


class WorkflowTests(unittest.TestCase):
    def test_job_transitions_retry_and_refund(self):
        job = Job("job-1", JobKind.IMAGE, "shot-1", cost_credits=3)
        ledger = CreditLedger(5)
        reservation = ledger.reserve("user-1", job.id, job.cost_credits, "image generation")

        transition_job(job, JobStatus.QUEUED)
        transition_job(job, JobStatus.RUNNING)
        fail_job(job, "provider timeout")
        refund_failed_job(job, ledger, reservation.id)
        self.assertEqual(ledger.balance, 5)
        self.assertEqual(reservation.status, "refunded")

        retry_job(job)
        self.assertEqual(job.status, JobStatus.QUEUED)
        self.assertEqual(job.attempts, 1)
        ledger.refund(reservation.id)
        self.assertEqual(ledger.balance, 5)

    def test_invalid_transition_and_insufficient_balance(self):
        job = Job("job-2", JobKind.VIDEO, "shot-2")
        with self.assertRaises(InvalidTransition):
            transition_job(job, JobStatus.RUNNING)
        with self.assertRaises(LedgerError):
            CreditLedger(1).reserve("user-1", job.id, 2)


class SerializationTests(unittest.TestCase):
    def test_domain_objects_are_json_ready(self):
        value = to_primitive(Project("p-1", "雨夜合约"))
        self.assertEqual(value["id"], "p-1")
        self.assertEqual(value["title"], "雨夜合约")
        self.assertEqual(value["status"], "draft")


if __name__ == "__main__":
    unittest.main()
