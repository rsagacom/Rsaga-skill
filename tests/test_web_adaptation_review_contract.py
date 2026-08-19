import unittest
from pathlib import Path


WEB_SOURCE = Path(__file__).resolve().parents[1].joinpath("web", "app", "page.tsx").read_text(encoding="utf-8")


class WebAdaptationReviewContractTests(unittest.TestCase):
    def test_workbench_exposes_approve_and_reject_review_actions(self):
        self.assertIn('status: "approved" | "rejected"', WEB_SOURCE)
        self.assertIn('status === "approved" ? "通过改编稿" : "驳回改编稿"', WEB_SOURCE)
        self.assertIn('onClick={() => void reviewUnit(unit, "rejected")}', WEB_SOURCE)
        self.assertIn('批量驳回 {reviewableAdaptationCount} 条', WEB_SOURCE)

    def test_bulk_rejection_does_not_reject_already_approved_or_rejected_units(self):
        self.assertIn(
            'unit.status !== "approved" && unit.status !== "rejected"',
            WEB_SOURCE,
        )
        self.assertIn('body: JSON.stringify({ unit_ids: unitIds, status })', WEB_SOURCE)


if __name__ == "__main__":
    unittest.main()
