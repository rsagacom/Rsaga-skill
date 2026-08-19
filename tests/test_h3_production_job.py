import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_h3_production_job import find_media, preflight, workflow_class_types


class H3ProductionJobTests(unittest.TestCase):
    def test_workflow_nodes_are_deterministic(self):
        workflow = {
            "2": {"class_type": "SaveVideo", "inputs": {}},
            "1": {"class_type": "MiniMaxH3TurboLoRA", "inputs": {}},
        }
        self.assertEqual(workflow_class_types(workflow), ["MiniMaxH3TurboLoRA", "SaveVideo"])

    def test_find_media_only_returns_supported_media(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "clip.txt").write_text("ignore", encoding="utf-8")
            (root / "clip.mp4").write_bytes(b"video")
            self.assertEqual(find_media(root, "clip"), root / "clip.mp4")

    def test_preflight_requires_workflow_nodes_and_records_queue(self):
        workflow = {
            "1": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "{{ASSET_ID}}"}},
        }
        responses = {
            "/system_stats": {"system": "ok"},
            "/queue": {"queue_running": [], "queue_pending": []},
            "/object_info": {"SaveVideo": {}},
        }

        def fake_get(base_url, path):
            return responses[path]

        with patch("scripts.run_h3_production_job.get_json", side_effect=fake_get):
            result = preflight("http://comfyui:8192", workflow)
        self.assertEqual(result["queue_running"], 0)
        self.assertEqual(result["queue_pending"], 0)
        self.assertEqual(result["required_nodes"], ["SaveVideo"])


if __name__ == "__main__":
    unittest.main()
