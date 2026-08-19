from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from studio_api.service import StudioService
from studio_api.store import StudioStore


class RemotionContractTests(unittest.TestCase):
    def test_renderer_is_a_separate_optional_worker_with_ffmpeg_fallback(self):
        root = Path(__file__).parents[1]
        package = (root / "rendering" / "package.json").read_text(encoding="utf-8")
        service = (root / "studio_api" / "service.py").read_text(encoding="utf-8")
        self.assertIn('"@remotion/bundler"', package)
        self.assertIn('"@remotion/renderer"', package)
        self.assertIn('STUDIO_COMPOSE_ENGINE', service)
        self.assertIn('STUDIO_REMOTION_RENDER_COMMAND', service)
        self.assertIn('compose_engine in {"ffmpeg", "remotion"}', service)

    def test_remotion_readiness_rejects_missing_default_tsx_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "node_modules" / "@remotion" / "renderer").mkdir(parents=True)
            (root / "node_modules" / "@remotion" / "renderer" / "package.json").write_text("{}", encoding="utf-8")
            (root / "package.json").write_text('{"scripts":{"render":"tsx src/render.ts"}}', encoding="utf-8")
            with patch.dict(
                "os.environ",
                {
                    "STUDIO_COMPOSE_ENGINE": "remotion",
                    "STUDIO_REMOTION_ROOT": str(root),
                    "STUDIO_REMOTION_RENDER_COMMAND": "",
                },
                clear=False,
            ):
                status = StudioService.composition_engine_status()
        self.assertEqual(status["engine"], "remotion")
        self.assertFalse(status["ready"])
        self.assertEqual(status["reason"], "remotion-runtime-incomplete")

    def test_remotion_readiness_accepts_configured_custom_runner_without_tsx(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "node_modules" / "@remotion" / "renderer").mkdir(parents=True)
            (root / "node_modules" / "@remotion" / "renderer" / "package.json").write_text("{}", encoding="utf-8")
            (root / "package.json").write_text("{}", encoding="utf-8")
            custom_runner = root / "render-worker"
            custom_runner.write_text("#!/bin/sh\n", encoding="utf-8")
            custom_runner.chmod(0o755)
            with patch.dict(
                "os.environ",
                {
                    "STUDIO_COMPOSE_ENGINE": "remotion",
                    "STUDIO_REMOTION_ROOT": str(root),
                    "STUDIO_REMOTION_RENDER_COMMAND": str(custom_runner),
                },
                clear=False,
            ):
                status = StudioService.composition_engine_status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["reason"], "remotion-runtime-available")

    def test_remotion_readiness_rejects_malformed_custom_runner_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "node_modules" / "@remotion" / "renderer").mkdir(parents=True)
            (root / "node_modules" / "@remotion" / "renderer" / "package.json").write_text("{}", encoding="utf-8")
            (root / "package.json").write_text("{}", encoding="utf-8")
            with patch.dict(
                "os.environ",
                {
                    "STUDIO_COMPOSE_ENGINE": "remotion",
                    "STUDIO_REMOTION_ROOT": str(root),
                    "STUDIO_REMOTION_RENDER_COMMAND": "unterminated'",
                },
                clear=False,
            ):
                status = StudioService.composition_engine_status()
        self.assertFalse(status["ready"])
        self.assertEqual(status["reason"], "remotion-runtime-incomplete")

    def test_internal_render_manifest_does_not_pollute_public_playlist_with_source_paths(self):
        service = (Path(__file__).parents[1] / "studio_api" / "service.py").read_text(encoding="utf-8")
        self.assertIn('render_items.append({**playlist_item, "source_path":', service)
        self.assertIn('json.dumps(playlist, ensure_ascii=False, indent=2)', service)

    def test_remotion_failure_records_fallback_and_keeps_public_playlist_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = StudioService(StudioStore(root / "studio.sqlite3"), root / "assets")
            project = service.create_project("Remotion 回退", "她推开门。")
            episode = service.create_outline(project["id"])[0]
            shot = service.create_shots(episode["id"])[0]
            service.create_prompts(shot["id"])
            image = service.create_image_asset(shot["id"])
            service.patch_asset(image["id"], {"selected": True, "consistency_confirmed": True})
            service.review_asset(image["id"])
            service.manual_review_asset(image["id"], "PASS")
            service.create_video_asset(image["id"])
            with patch.dict("os.environ", {"STUDIO_COMPOSE_ENGINE": "remotion"}), patch.object(
                service, "_render_remotion", side_effect=RuntimeError("renderer unavailable")
            ):
                composition = service.compose_episode(episode["id"])
            metadata = json.loads(composition["metadata_json"])
            playlist = (root / "assets" / Path(composition["playlist_url"]).name).read_text(encoding="utf-8")
            self.assertEqual(metadata["requested_engine"], "remotion")
            self.assertIn("remotion_fallback", metadata)
            self.assertNotIn("source_path", playlist)


if __name__ == "__main__":
    unittest.main()
