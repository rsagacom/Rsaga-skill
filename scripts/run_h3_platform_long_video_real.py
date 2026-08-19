#!/usr/bin/env python3
"""Run one isolated, real-ComfyUI H3 platform long-video acceptance job.

This is an evidence runner, not a production daemon.  It deliberately creates
a fresh SQLite/asset directory per run and injects only the real H3 video
provider; it does not alter the Linux WeChat/Qwen services or the shared
vanilla ComfyUI instance.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio_api.providers import ComfyUIVideoProvider, ProviderRegistry
from studio_api.service import StudioService, now
from studio_api.store import StudioStore


RUN_DIR = Path(os.environ.get("H3_PLATFORM_RUN_DIR", str(ROOT / "test-runs" / "2026-08-17-h3-platform-long-video-15s-real-gpu-v3"))).resolve()
BASE_URL = os.environ.get("H3_COMFYUI_BASE_URL", "http://192.168.1.6:8192")
FIRST_WORKFLOW = ROOT / "workflows/h3-production/h3-director-first-segment-av-latent-640x384-8steps.json"
CONTEXT_WORKFLOW = ROOT / "workflows/h3-production/h3-motion-context-segment-640x384-8steps.json"


def main() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    asset_dir = RUN_DIR / "assets"
    store = StudioStore(RUN_DIR / "platform.sqlite3")
    service = StudioService(store, asset_dir)
    project = service.create_project(
        "H3平台真实长链验收v3",
        "《命运模型》第一章 3DCG 试镜",
        style="3DCG",
        episode_length="15s",
        idempotency_key="h3-platform-real-v3",
    )
    episode_id = "episode-platform-h3-15s-real-gpu-v3"
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO episodes(id, project_id, number, title, summary, conflict, hook, target_duration_seconds, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (episode_id, project["id"], 1, "第一章真实平台长链v3", "3DCG人物沿废弃石桥向前走，符文亮起", "", "", 15, "draft"),
        )

    registry = ProviderRegistry.local()
    registry.video = ComfyUIVideoProvider(
        BASE_URL,
        str(FIRST_WORKFLOW),
        timeout=2400,
        poll_interval=2.0,
        long_video_first_workflow_path=str(FIRST_WORKFLOW),
        long_video_context_workflow_path=str(CONTEXT_WORKFLOW),
    )
    plan = {
        "mode": "director-motion-context",
        "width": 640,
        "height": 384,
        "fps": 24,
        "steps": 8,
        "seed": 20260819,
        "sampler": "euler",
        "context_length": 22,
        "audio_context_length": 24,
        "segments": [
            {"index": 1, "duration_seconds": 5.167, "role": "director", "prompt": "3DCG国漫风格，静默表演，无字幕无可读文字。远景：青年探勘者穿深蓝机能外套，沿废弃石桥缓慢向前，冷雾、青绿色符文、稳定的24fps电影运镜；保持人物脸型、发型、服装、背包和环境空间关系一致。"},
            {"index": 2, "duration_seconds": 4.25, "role": "motion-context", "prompt": "接续上一段结尾：同一青年、同一深蓝机能外套与背包、同一废弃石桥和冷雾，保持行走方向与光线。镜头从远景推进到中景，青年停步观察桥侧亮起的青绿色符文，手部动作自然，静默表演，无字幕无可读文字。"},
            {"index": 3, "duration_seconds": 4.25, "role": "motion-context", "prompt": "接续上一段结尾：同一青年保持服装、发型、脸型和站位，符文光从桥侧映到面部。镜头推进到中近景，青年抬头、呼吸、目光转向桥下，雾气缓慢流动，面部五官清楚稳定，静默表演，无字幕无可读文字。"},
            {"index": 4, "duration_seconds": 4.25, "role": "motion-context", "prompt": "接续上一段结尾：同一青年、同一场景和光向。镜头切换为稳定近景但保持动作连续，青年轻轻握紧手中的探测器，符文在眼中反光，随后向镜头右侧看去；3DCG国漫质感，面部比例稳定，静默表演，无字幕无可读文字。"},
        ],
    }
    (RUN_DIR / "run-config.json").write_text(
        json.dumps({"created_at": now(), "base_url": BASE_URL, "provider": "comfyui", "model_profile": "pruned-int8-drbaph-rawkey", "first_workflow": str(FIRST_WORKFLOW), "context_workflow": str(CONTEXT_WORKFLOW), "target_duration_seconds": 15, "plan": plan}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with patch.object(service, "_providers_for_user", return_value=registry):
        result = service.request_long_video(episode_id, plan, run_now=True, idempotency_key="h3-platform-long-v3")
    (RUN_DIR / "platform-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"run_dir": str(RUN_DIR), "status": result.get("status"), "job_id": result.get("job", {}).get("id"), "final_video_url": result.get("final_video_url"), "segments": result.get("segments")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
