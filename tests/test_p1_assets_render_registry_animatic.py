"""P1 资产包 / RenderJob / Workflow Registry / Animatic 测试。"""

from __future__ import annotations

from pathlib import Path
import unittest

from studio_core.animatic import review_animatic
from studio_core.assets import (
    AssetPack,
    AssetSlot,
    CharacterDNA,
    ConsistencyVerdict,
    PackStatus,
    build_expression_pack,
    build_turnaround_pack,
)
from studio_core.render import (
    FailureClass,
    JobState,
    RenderJob,
    RenderJobSpec,
    classify_failure,
    compute_cache_key,
    mark_failed,
)
from studio_core.registry import gate_report, load_registry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
H3_PRODUCTION_DIR = PROJECT_ROOT / "workflows" / "h3-production"


class CharacterDNATests(unittest.TestCase):
    def test_dna_fail_without_any_assets(self):
        dna = CharacterDNA(character_id="CHAR_001", name="祁元思远")
        self.assertEqual(dna.check_dna_contract([]), ConsistencyVerdict.FAIL)

    def test_dna_warn_without_turnaround(self):
        dna = CharacterDNA(character_id="CHAR_001", name="祁元思远", expression_pack_id="CHAR_001_FACE_v01")
        pack = build_expression_pack("CHAR_001")
        pack.status = PackStatus.ADOPTED
        self.assertEqual(dna.check_dna_contract([pack]), ConsistencyVerdict.WARN)

    def test_dna_unknown_without_golden_shots(self):
        dna = CharacterDNA(
            character_id="CHAR_001",
            name="祁元思远",
            turnaround_asset_ids=["CHAR_001_TURN_v01"],
        )
        self.assertEqual(dna.check_dna_contract([]), ConsistencyVerdict.UNKNOWN)

    def test_dna_pass_with_golden_shot(self):
        dna = CharacterDNA(
            character_id="CHAR_001",
            name="祁元思远",
            turnaround_asset_ids=["CHAR_001_TURN_v01"],
            golden_shots=["SHOT_001_003_001"],
        )
        self.assertEqual(dna.check_dna_contract([]), ConsistencyVerdict.PASS)

    def test_pack_completeness(self):
        pack = build_turnaround_pack("CHAR_001")
        self.assertEqual(pack.completeness(), (0, 0, 3))
        pack.slots[0].asset_id = "CHAR_001_TURN_front"
        pack.slots[0].status = PackStatus.ADOPTED
        self.assertEqual(pack.completeness(), (1, 1, 3))


class RenderJobTests(unittest.TestCase):
    def _spec(self) -> RenderJobSpec:
        return RenderJobSpec(
            project="destiny-model",
            shot="SHOT_001_001_001",
            canon_revision="CANON_v1",
            asset_revision="CHAR_001_FACE_v01",
            prompt_revision="PROMPT_v1",
            workflow_revision="h3-t2v-640x384-8steps",
            model_revision="pruned-int8",
            lora_revision="drbaph-rawkey",
            seed=20260817,
            width=640,
            height=384,
            fps=24,
            frames=124,
            steps=8,
            provider_profile="minimax-h3-pruned-int8-drbaph-v1",
        )

    def test_cache_key_stable_for_same_spec(self):
        self.assertEqual(compute_cache_key(self._spec()), compute_cache_key(self._spec()))

    def test_cache_key_changes_on_every_critical_field(self):
        spec = self._spec()
        base = compute_cache_key(spec)
        # 任一成分变化必须失效（蓝图 §13.2 缓存键合同）
        for field_name, value in {
            "canon_revision": "CANON_v2",
            "asset_revision": "CHAR_001_FACE_v02",
            "prompt_revision": "PROMPT_v2",
            "workflow_revision": "other-workflow",
            "model_revision": "non-pruned-int8",
            "lora_revision": "other-lora",
            "seed": 999,
            "width": 832,
            "height": 480,
            "fps": 30,
            "frames": 125,
            "steps": 20,
            "provider_profile": "other-profile",
        }.items():
            import dataclasses as dc

            mutated = dc.replace(spec, **{field_name: value})
            self.assertNotEqual(
                base,
                compute_cache_key(mutated),
                f"cache key must change when {field_name} changes",
            )

    def test_failure_policy_no_retry_for_face_collapse(self):
        job = RenderJob(job_id="j1", spec=self._spec())
        result = mark_failed(job, FailureClass.FACE_COLLAPSE)
        self.assertFalse(result["retry"])
        self.assertEqual(job.state, JobState.FAILED)

    def test_failure_policy_retries_network(self):
        job = RenderJob(job_id="j2", spec=self._spec())
        result = mark_failed(job, FailureClass.NETWORK)
        self.assertTrue(result["retry"])
        self.assertEqual(job.max_retries, 3)

    def test_classify_failure_conservative(self):
        self.assertEqual(classify_failure("CUDA out of memory"), FailureClass.OOM)
        self.assertEqual(classify_failure("some mystery error"), FailureClass.UNKNOWN)


class WorkflowRegistryTests(unittest.TestCase):
    @unittest.skipUnless(H3_PRODUCTION_DIR.exists(), "需要 workflows/h3-production/")
    def test_registry_gate_has_no_missing_or_invalid(self):
        registry = load_registry(H3_PRODUCTION_DIR)
        report = gate_report(registry)
        self.assertEqual(report["missing"], [], f"missing workflows: {report['missing']}")
        self.assertEqual(
            report["structurally_invalid"],
            [],
            f"invalid workflows: {report['structurally_invalid']}",
        )
        self.assertGreater(len(report["valid"]), 0)

    def test_registry_reports_missing_profile(self):
        registry = load_registry("/nonexistent-dir")
        self.assertEqual(gate_report(registry)["profile_errors"], ["profile missing: production-profile.json"])


class AnimaticTests(unittest.TestCase):
    def test_dialogue_density_warns(self):
        shots = [
            {
                "id": "SHOT_001_003_001",
                "duration_sec": 2.0,
                "dialogue": "刚才那里，明明有人，你看到了吗，不要走",
                "audio": {"sfx": [], "ambient_bed": []},
                "camera": {"axis": "180_left"},
                "continuity_from": None,
            }
        ]
        review = review_animatic(shots)
        self.assertTrue(any(f.code == "dialogue-too-dense" for f in review.warnings))

    def test_axis_jump_warns(self):
        shots = [
            {"id": "S1", "duration_sec": 2, "camera": {"axis": "180_left"}, "audio": {}, "continuity_from": None},
            {"id": "S2", "duration_sec": 2, "camera": {"axis": "180_right"}, "audio": {}, "continuity_from": "S1"},
        ]
        review = review_animatic(shots)
        self.assertTrue(any(f.code == "axis-jump" for f in review.warnings))

    def test_continuity_broken_fails(self):
        shots = [
            {"id": "S1", "duration_sec": 2, "camera": {}, "audio": {}, "continuity_from": None},
            {"id": "S2", "duration_sec": 2, "camera": {}, "audio": {}, "continuity_from": "S9"},
        ]
        review = review_animatic(shots)
        self.assertTrue(any(f.code == "continuity-broken" for f in review.fails))

    def test_shot_too_long_warns_against_h3_gate(self):
        shots = [
            {"id": "S1", "duration_sec": 15.0, "camera": {}, "audio": {}, "continuity_from": None},
        ]
        review = review_animatic(shots)
        self.assertTrue(any(f.code == "shot-too-long" for f in review.warnings))


if __name__ == "__main__":
    unittest.main()