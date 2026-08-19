"""P0 影响分析引擎测试（unittest 风格，跟随仓库测试约定）。

覆盖：角色/场景/风格变更的引用传播、stale 标记、重跑任务建议。
数据源：canon/ 目录（《命运模型》第一章示例）。
"""

from __future__ import annotations

from pathlib import Path
import unittest

from studio_core.impact import CanonChange, _entity_family, _family_matches, analyze_impact

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANON_DIR = PROJECT_ROOT / "canon"


class EntityFamilyTests(unittest.TestCase):
    def test_family_parsing(self):
        self.assertEqual(_entity_family("CHAR_001_FACE_v02"), "CHAR_001")
        self.assertEqual(_entity_family("CHAR_001"), "CHAR_001")
        self.assertEqual(_entity_family("SCENE_012"), "SCENE_012")
        self.assertEqual(_entity_family("PROP_012_004"), "PROP_012")

    def test_family_matches(self):
        self.assertTrue(_family_matches("CHAR_001", "CHAR_001_FACE_v02"))
        self.assertFalse(_family_matches("CHAR_001", "CHAR_002"))
        self.assertTrue(_family_matches("PROP_012", "PROP_012_004"))
        self.assertFalse(_family_matches("PROP_012", "PROP_0120_004"))


class CharacterImpactTests(unittest.TestCase):
    def test_character_change_affects_only_referencing_shots(self):
        report = analyze_impact(
            CANON_DIR,
            CanonChange(entity_id="CHAR_001", fields=("hair",), description="发型变更"),
        )
        shot_groups = [g for g in report.affected_groups if g.category == "shot"]
        self.assertTrue(shot_groups, "必须存在 affected shot 分组")
        affected_shots = set(shot_groups[0].item_ids)
        self.assertIn("SHOT_001_001_001", affected_shots)
        self.assertIn("SHOT_001_001_002", affected_shots)
        self.assertIn("SHOT_001_003_001", affected_shots)
        self.assertNotIn("SHOT_001_002_001", affected_shots)
        self.assertIn("SHOT_001_003_001", report.stale_ids)
        self.assertTrue(any(g.category == "rerun" for g in report.affected_groups))

    def test_scene_change_stales_all_referencing_shots(self):
        report = analyze_impact(
            CANON_DIR,
            CanonChange(entity_id="SCENE_012", fields=("weather",), description="天气由晴转雨"),
        )
        shot_groups = [g for g in report.affected_groups if g.category == "shot"]
        self.assertTrue(shot_groups)
        affected = {s for g in shot_groups for s in g.item_ids}
        self.assertEqual(
            affected,
            {
                "SHOT_001_001_001",
                "SHOT_001_001_002",
                "SHOT_001_002_001",
                "SHOT_001_003_001",
            },
        )

    def test_style_change_stales_all_shots(self):
        report = analyze_impact(
            CANON_DIR,
            CanonChange(entity_id="STYLE_SHOT_ESTABLISHING", fields=("camera",)),
        )
        stale = set(report.stale_ids)
        self.assertIn("SHOT_001_001_001", stale)
        self.assertIn("SHOT_001_003_001", stale)

    def test_unrelated_entity_has_no_impact(self):
        report = analyze_impact(
            CANON_DIR,
            CanonChange(entity_id="PROP_999", fields=(), description="无关变更"),
        )
        self.assertEqual(report.affected_groups, [])
        self.assertEqual(report.stale_ids, [])

    def test_report_serialization(self):
        report = analyze_impact(
            CANON_DIR,
            CanonChange(entity_id="CHAR_001", fields=("age",)),
        )
        data = report.to_dict()
        self.assertEqual(data["changed_entity"]["entity_id"], "CHAR_001")
        self.assertTrue(all("category" in g and "item_ids" in g for g in data["affected"]))


if __name__ == "__main__":
    unittest.main()