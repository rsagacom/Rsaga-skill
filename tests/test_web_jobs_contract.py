import unittest
from pathlib import Path


class WebJobsContractTests(unittest.TestCase):
    def test_task_center_renders_server_persisted_progress(self):
        source = (Path(__file__).parents[1] / "web" / "app" / "page.tsx").read_text(encoding="utf-8")
        for marker in (
            "progress_percent",
            "progress_message",
            'role="progressbar"',
            "aria-valuenow",
            "任务进度",
            "data-job-id",
            "data-job-kind",
        ):
            self.assertIn(marker, source)

    def test_episode_timeline_exposes_narration_generation_and_ai_disclosure(self):
        source = (Path(__file__).parents[1] / "web" / "app" / "page.tsx").read_text(encoding="utf-8")
        for marker in (
            "/narration",
            "生成旁白",
            "SPEECH_VOICES",
            "/api/health",
            "allowedSpeechVoiceValues",
            "speechVoiceOptions",
            "旁白音色",
            "cedar（推荐）",
            "旁白语速",
            "narrationSpeed",
            "表达指令",
            "narrationInstructions",
            'instructions: narrationInstructions.trim()',
            "narrationDrafts",
            "分集旁白稿",
            "保存旁白稿",
            "narration_text",
            "生成旁白会使用当前稿件",
            "saveNarrationDraft",
            'Idempotency-Key',
            "data-narration-button",
            "真实 TTS 为 AI 生成语音",
            "auto_subtitles",
            "估算字幕",
            "audio-track-preview",
            "试听音轨",
        ):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
