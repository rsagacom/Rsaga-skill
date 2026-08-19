"""GOLDEN_SHOTS 清单、路径与媒体证据门禁测试。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from studio_core.golden_shots import (
    GoldenShotError,
    GoldenShotManifest,
    GoldenShotPathError,
    verify_record,
)


class GoldenShotContractTests(unittest.TestCase):
    def test_project_manifest_has_three_historical_samples_with_safe_paths(self):
        root = Path(__file__).resolve().parents[1]
        manifest = GoldenShotManifest.load(root / "GOLDEN_SHOTS" / "manifest.json")
        self.assertEqual(manifest.project_id, "destiny-model")
        self.assertEqual(len(manifest.records), 3)
        self.assertTrue(all(record.review_status == "accepted" for record in manifest.records))
        self.assertTrue(all(not Path(record.artifact_path).is_absolute() for record in manifest.records))

    def test_manifest_rejects_duplicate_record_ids(self):
        valid = {
            "manifest_version": "1.0",
            "project_id": "p",
            "records": [
                {"id": "G1", "artifact_path": "a.mp4", "sha256": "a" * 64, "review_status": "accepted"},
                {"id": "G1", "artifact_path": "b.mp4", "sha256": "b" * 64, "review_status": "accepted"},
            ],
        }
        with self.assertRaises(GoldenShotError):
            GoldenShotManifest.from_dict(valid)

    def test_manifest_rejects_path_escape(self):
        with self.assertRaises(GoldenShotPathError):
            GoldenShotManifest.from_dict(
                {
                    "manifest_version": "1.0",
                    "project_id": "p",
                    "records": [
                        {
                            "id": "G1",
                            "artifact_path": "../outside.mp4",
                            "sha256": "a" * 64,
                            "review_status": "accepted",
                        }
                    ],
                }
            )

    def test_verifier_requires_sha_probe_and_full_decode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "sample.mp4"
            artifact.write_bytes(b"sample-media")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            record = GoldenShotManifest.from_dict(
                {
                    "manifest_version": "1.0",
                    "project_id": "p",
                    "records": [
                        {
                            "id": "G1",
                            "artifact_path": "sample.mp4",
                            "sha256": digest,
                            "review_status": "accepted",
                            "expected_media": {
                                "duration_sec": 1.0,
                                "duration_tolerance_sec": 0.01,
                                "width": 640,
                                "height": 384,
                                "fps_num": 24,
                                "fps_den": 1,
                                "video_codec": "h264",
                                "audio_codec": "aac",
                            },
                        }
                    ],
                }
            ).records[0]
            result = verify_record(
                record,
                root,
                probe=lambda _: {
                    "duration_sec": 1.0,
                    "width": 640,
                    "height": 384,
                    "fps_num": 24,
                    "fps_den": 1,
                    "video_codec": "h264",
                    "audio_codec": "aac",
                },
                decode=lambda _: True,
            )
            self.assertEqual(result.status, "passed")
            self.assertEqual(result.actual_sha256, digest)

    def test_missing_external_evidence_is_unknown_not_pass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "sample.mp4"
            artifact.write_bytes(b"sample-media")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            record = GoldenShotManifest.from_dict(
                {
                    "manifest_version": "1.0",
                    "project_id": "p",
                    "records": [
                        {"id": "G1", "artifact_path": "sample.mp4", "sha256": digest, "review_status": "accepted"}
                    ],
                }
            ).records[0]
            result = verify_record(record, root)
            self.assertEqual(result.status, "unknown")
            self.assertIn("ffprobe", json.dumps(result.to_dict(), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
