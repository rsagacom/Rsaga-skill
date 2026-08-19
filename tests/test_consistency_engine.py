"""P2 一致性视觉 adapter 的 fail-closed 合同测试。"""

from __future__ import annotations

import unittest

from studio_core.consistency import (
    ConsistencyAdapter,
    ConsistencyDimension,
    ConsistencyFinding,
    ConsistencyRequest,
    ConsistencyStatus,
    UnknownConsistencyProvider,
)


class ConsistencyAdapterTests(unittest.TestCase):
    def request(self) -> ConsistencyRequest:
        return ConsistencyRequest(
            candidate_asset_id="ASSET_candidate",
            reference_asset_ids=("ASSET_front", "ASSET_side"),
            dimensions=(ConsistencyDimension.FACE, ConsistencyDimension.COSTUME),
            shot_id="SHOT_001",
            style="3DCG 国漫",
            shot_size="close-up",
        )

    def test_local_provider_is_unknown_and_not_adoptable(self):
        report = ConsistencyAdapter(UnknownConsistencyProvider()).inspect(self.request())
        self.assertEqual(report.overall_status, ConsistencyStatus.UNKNOWN)
        self.assertFalse(report.ready_for_adoption)
        self.assertEqual([finding.status for finding in report.findings], [ConsistencyStatus.UNKNOWN] * 2)
        self.assertEqual(report.to_dict()["ready_for_adoption"], False)

    def test_pass_without_evidence_is_downgraded_to_unknown(self):
        class FakeProvider:
            name = "fake"
            model = "fake-vision"

            def inspect(self, request):
                return [
                    ConsistencyFinding(
                        dimension=dimension,
                        status=ConsistencyStatus.PASS,
                        detail="看起来一致",
                        provider="fake",
                        model="fake-vision",
                    )
                    for dimension in request.dimensions
                ]

        report = ConsistencyAdapter(FakeProvider()).inspect(self.request())
        self.assertEqual(report.overall_status, ConsistencyStatus.UNKNOWN)
        self.assertTrue(all(finding.status == ConsistencyStatus.UNKNOWN for finding in report.findings))
        self.assertTrue(all("evidence_refs" in finding.detail for finding in report.findings))

    def test_evidenced_passes_are_adoptable(self):
        class EvidenceProvider:
            name = "openai-compatible"
            model = "vision-model-v1"

            def inspect(self, request):
                return [
                    ConsistencyFinding(
                        dimension=dimension,
                        status=ConsistencyStatus.PASS,
                        detail="特征与参考资产一致",
                        provider=self.name,
                        model=self.model,
                        evidence_refs=(f"sha256://{dimension.value}-evidence",),
                        score=0.94,
                        threshold=0.8,
                    )
                    for dimension in request.dimensions
                ]

        report = ConsistencyAdapter(EvidenceProvider()).inspect(self.request())
        self.assertEqual(report.overall_status, ConsistencyStatus.PASS)
        self.assertTrue(report.ready_for_adoption)
        self.assertEqual(report.findings[0].provider, "openai-compatible")

    def test_provider_failure_is_unknown_not_pass(self):
        class BrokenProvider:
            name = "comfyui"
            model = "workflow-v1"

            def inspect(self, request):
                raise RuntimeError("workflow unavailable")

        report = ConsistencyAdapter(BrokenProvider()).inspect(self.request())
        self.assertEqual(report.overall_status, ConsistencyStatus.UNKNOWN)
        self.assertFalse(report.ready_for_adoption)
        self.assertTrue(all("执行失败" in finding.detail for finding in report.findings))

    def test_request_requires_reference_asset(self):
        with self.assertRaisesRegex(ValueError, "reference_asset_id"):
            ConsistencyRequest(candidate_asset_id="ASSET_candidate")


if __name__ == "__main__":
    unittest.main()
