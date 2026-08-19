import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio_api.providers import ComfyUIVideoProvider, LocalPreviewVideoProvider, ProviderError, ProviderRegistry
from studio_api.service import StudioService, ServiceError
from studio_api.store import StudioStore
from studio_core.long_video import LongVideoPlanError, normalize_long_video_plan


class CountingVideoProvider(LocalPreviewVideoProvider):
    def __init__(self) -> None:
        self.calls = []
        self.fail_context_once = True

    def generate_segment(self, segment_id, shot_id, output_dir, **kwargs):
        self.calls.append((kwargs["segment_index"], kwargs["role"], kwargs.get("context_latent_path")))
        if kwargs["role"] == "motion-context" and self.fail_context_once:
            self.fail_context_once = False
            raise ProviderError("simulated Motion Context interruption")
        return super().generate_segment(segment_id, shot_id, output_dir, **kwargs)


class LongVideoPlanTests(unittest.TestCase):
    def test_comfyui_long_video_typed_placeholders_and_latent_discovery(self):
        workflow = {
            "1": {
                "class_type": "MiniMaxH3MotionContext",
                "inputs": {
                    "prompt": "{{SEGMENT_PROMPT}}",
                    "width": "{{WIDTH}}",
                    "steps": "{{STEPS}}",
                    "context_length": "{{CONTEXT_LENGTH}}",
                    "latent_path": "{{CONTEXT_LATENT_PATH}}",
                    "clip_index": "{{CONTEXT_CLIP_INDEX}}",
                },
            }
        }
        replaced = ComfyUIVideoProvider._replace_typed(
            workflow,
            {
                "{{SEGMENT_PROMPT}}": "接续",
                "{{WIDTH}}": 640,
                "{{STEPS}}": 8,
                "{{CONTEXT_LATENT_PATH}}": "long-video/job/context",
                "{{CONTEXT_CLIP_INDEX}}": 2,
                "{{CONTEXT_LENGTH}}": "22",
            },
        )
        self.assertEqual(replaced["1"]["inputs"]["width"], 640)
        self.assertEqual(replaced["1"]["inputs"]["clip_index"], 2)
        self.assertEqual(replaced["1"]["inputs"]["context_length"], "22")
        latent = ComfyUIVideoProvider._find_latent(
            {"19": {"latents": [{"filename": "clip_00002.safetensors", "subfolder": "output", "type": "output"}]}}
        )
        self.assertEqual(latent["filename"], "clip_00002.safetensors")
        self.assertEqual(
            ComfyUIVideoProvider._motion_context_folder("long-video/job/context/clip"),
            "long-video/job/context",
        )
        self.assertEqual(
            ComfyUIVideoProvider._motion_context_folder("long-video/job/context/clip_00001.safetensors"),
            "long-video/job/context",
        )
        self.assertEqual(
            ComfyUIVideoProvider._motion_context_folder("long-video/job/context"),
            "long-video/job/context",
        )

    def test_plan_forces_director_then_motion_context(self):
        plan = normalize_long_video_plan(
            {
                "width": 640,
                "height": 384,
                "segments": [
                    {"index": 1, "duration_seconds": 5, "prompt": "第一段", "role": "director"},
                    {"index": 2, "duration_seconds": 5, "prompt": "接续", "role": "motion-context"},
                ],
            }
        )
        self.assertEqual([item["role"] for item in plan["segments"]], ["director", "motion-context"])
        with self.assertRaises(LongVideoPlanError):
            normalize_long_video_plan(
                {"segments": [{"index": 1, "duration_seconds": 5, "prompt": "第一段", "role": "motion-context"}]}
            )

    def test_plan_normalizes_reference_asset_ids_for_first_segment(self):
        plan = normalize_long_video_plan(
            {
                "reference_asset_ids": ["front", "side", "front", "face"],
                "segments": [{"index": 1, "duration_seconds": 5, "prompt": "参考角色", "role": "director"}],
            }
        )
        self.assertEqual(plan["reference_asset_ids"], ["front", "side", "face"])
        with self.assertRaises(LongVideoPlanError):
            normalize_long_video_plan(
                {
                    "reference_asset_ids": [f"ref-{index}" for index in range(9)],
                    "segments": [{"index": 1, "duration_seconds": 5, "prompt": "超过上限", "role": "director"}],
                }
            )

    def make_service(self):
        directory = tempfile.TemporaryDirectory()
        service = StudioService(StudioStore(Path(directory.name) / "studio.sqlite3"), Path(directory.name) / "assets")
        project = service.create_project("长视频测试", "测试故事", style="3DCG", episode_length="30s")
        with service.store.connection() as connection:
            connection.execute(
                "INSERT INTO episodes(id, project_id, number, title, summary, conflict, hook, target_duration_seconds, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("episode-long-test", project["id"], 1, "长视频", "", "", "", 2, "draft"),
            )
        return service, directory

    def test_local_long_video_persists_segments_manifest_and_final_video(self):
        service, directory = self.make_service()
        self.addCleanup(directory.cleanup)
        result = service.request_long_video(
            "episode-long-test",
            {
                "segments": [
                    {"index": 1, "duration_seconds": 1, "prompt": "第一段", "role": "director"},
                    {"index": 2, "duration_seconds": 1, "prompt": "接续", "role": "motion-context"},
                ]
            },
            run_now=True,
            idempotency_key="long-video-local-1",
        )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["final_video_url"].endswith(".final.mp4"))
        self.assertEqual([segment["status"] for segment in result["segments"]], ["completed", "completed"])
        self.assertEqual(result["manifest"]["workflow_chain"], ["director", "motion-context"])
        self.assertEqual(result["manifest"]["segment_count"], 2)
        self.assertIn("sha256", result["manifest"]["final"])

    def test_failed_context_segment_retries_without_regenerating_completed_segment(self):
        service, directory = self.make_service()
        self.addCleanup(directory.cleanup)
        provider = CountingVideoProvider()
        registry = ProviderRegistry.local()
        registry.video = provider
        with patch.object(service, "_providers_for_user", return_value=registry):
            queued = service.request_long_video(
                "episode-long-test",
                {
                    "segments": [
                        {"index": 1, "duration_seconds": 1, "prompt": "第一段", "role": "director"},
                        {"index": 2, "duration_seconds": 1, "prompt": "接续", "role": "motion-context"},
                    ]
                },
                run_now=False,
                idempotency_key="long-video-retry-1",
            )
            job_id = queued["job"]["id"]
            failed = service.run_job(job_id)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual([call[0] for call in provider.calls], [1, 2])
            service.retry_job(job_id, enqueue_external=False)
            completed = service.run_job(job_id)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual([call[0] for call in provider.calls], [1, 2, 2])
        self.assertEqual(completed["segments"][0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
