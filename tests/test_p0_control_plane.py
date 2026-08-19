"""P0 控制平面闭环与 OTIO 时间线测试。"""

from __future__ import annotations

from pathlib import Path
import json
import unittest

from studio_core.pipeline import assemble_pipeline, validate_closed_loop
from studio_core.timeline import (
    Timeline,
    TimelineClip,
    build_timeline_from_shotlist,
    export_timeline_json,
    import_timeline,
    timeline_to_dict,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANON_DIR = PROJECT_ROOT / "canon"


class PipelineClosedLoopTests(unittest.TestCase):
    def test_destiny_ch01_assembly_has_no_dangling_refs(self):
        graph = assemble_pipeline(CANON_DIR)
        self.assertEqual(set(graph.units), {"NU_001", "NU_002", "NU_003"})
        self.assertIn("SCENE_012", graph.scenes)
        self.assertEqual(len(graph.shots), 4)
        self.assertEqual(validate_closed_loop(graph), [])

    def test_dangling_shot_ref_detected(self):
        graph = assemble_pipeline(CANON_DIR)
        # 人工注入断链：引一个不存在的镜头
        graph.units["NU_001"].shot_ids.append("SHOT_909_909_909")
        errors = validate_closed_loop(graph)
        self.assertTrue(any("SHOT_909_909_909" in e for e in errors))


class TimelineTests(unittest.TestCase):
    def test_otio_roundtrip_preserves_clips(self):
        shots = [
            {
                "id": "SHOT_014_003_007",
                "duration_sec": 6.0,
                "dialogue": "你早就知道了？",
                "audio": {"sfx": ["rain", "distant_thunder"], "bgm": "tension_low"},
            },
            {
                "id": "SHOT_014_003_008",
                "duration_sec": 4.0,
                "dialogue": None,
                "audio": {"sfx": []},
            },
        ]
        timeline = build_timeline_from_shotlist("destiny-model", 24.0, shots)
        data = timeline_to_dict(timeline)

        # 结构契约
        self.assertEqual(data["OTIO_SCHEMA"], "Timeline.1")
        self.assertEqual(data["tracks"]["OTIO_SCHEMA"], "Stack.1")
        kinds = [t["kind"] for t in data["tracks"]["children"]]
        self.assertIn("Video", kinds)
        self.assertIn("Audio", kinds)

        # 往返：导出 → 导入，clip 数与时间不变
        export_timeline_json(timeline, "/tmp/otio-test-timeline.json")
        with open("/tmp/otio-test-timeline.json", encoding="utf-8") as fh:
            roundtrip = import_timeline(json.load(fh))
        self.assertEqual(len(roundtrip.clips), 2)
        self.assertAlmostEqual(roundtrip.clips[0].duration_sec, 6.0)
        self.assertAlmostEqual(roundtrip.clips[1].start_sec, 6.0)
        self.assertEqual(roundtrip.clips[0].dialogue, "你早就知道了？")

    def test_otio_uses_relative_media_refs(self):
        """时间线禁止绝对路径：media 引用必须是相对路径。"""
        timeline = build_timeline_from_shotlist("t", 24.0, [{"id": "SHOT_001_001_001", "duration_sec": 2.0}])
        data = timeline_to_dict(timeline)
        for track in data["tracks"]["children"]:
            for clip in track["children"]:
                target = clip["media_references"]["DEFAULT_MEDIA"]["target_url"]
                self.assertFalse(
                    target.startswith("/") or "://" in target,
                    f"media reference must be relative, got {target}",
                )

    def test_otio_rejects_non_timeline_document(self):
        with self.assertRaises(ValueError):
            import_timeline({"OTIO_SCHEMA": "Clip.1"})


if __name__ == "__main__":
    unittest.main()