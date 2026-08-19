#!/usr/bin/env python3
"""Run the selected-base H3 platform reference-pack long-video gate.

The first Director segment consumes four independently uploaded identity
images through ``reference_asset_ids``.  Later Motion Context segments only
consume the previous AV latent.  This is intentionally a fresh evidence run;
it does not touch the shared service database or protected Linux processes.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio_api.providers import ComfyUIVideoProvider, ProviderRegistry
from studio_api.service import StudioService, now
from studio_api.store import StudioStore


RUN_DIR = Path(
    os.environ.get(
        "H3_PLATFORM_RUN_DIR",
        str(ROOT / "test-runs" / "2026-08-17-h3-platform-reference-long-video-15s-real-gpu"),
    )
).resolve()
BASE_URL = os.environ.get("H3_COMFYUI_BASE_URL", "http://192.168.1.6:8192")
FIRST_WORKFLOW = Path(
    os.environ.get(
        "H3_FIRST_WORKFLOW",
        str(ROOT / "workflows/h3-production/h3-reference-first-segment-av-latent-640x384-8steps.json"),
    )
)
CONTEXT_WORKFLOW = Path(
    os.environ.get(
        "H3_CONTEXT_WORKFLOW",
        str(ROOT / "workflows/h3-production/h3-motion-context-segment-640x384-8steps.json"),
    )
)
MODEL_PROFILE = os.environ.get("H3_MODEL_PROFILE", "pruned-int8-drbaph-rawkey")
REFERENCE_SOURCES = (
    ("front", ROOT / "test-runs/2026-08-14-h3-long-director-r2v-character/character_front_reference.png"),
    ("side", ROOT / "test-runs/2026-08-14-h3-long-director-r2v-character/character_side_reference.png"),
    ("back", ROOT / "test-runs/2026-08-14-h3-long-director-r2v-character/character_back_reference.png"),
    ("face-closeup", ROOT / "test-runs/2026-08-15-h3-3dcg-identity-ab/references/character_3dcg_face_closeup.png"),
)


def _register_reference_assets(service: StudioService, project_id: str) -> list[str]:
    asset_dir = service.asset_dir
    asset_dir.mkdir(parents=True, exist_ok=True)
    asset_ids: list[str] = []
    timestamp = now()
    with service.store.connection() as connection:
        for index, (label, source) in enumerate(REFERENCE_SOURCES, start=1):
            if not source.is_file():
                raise FileNotFoundError(source)
            filename = f"reference-{index:02d}-{label}{source.suffix.lower()}"
            destination = asset_dir / filename
            shutil.copy2(source, destination)
            asset_id = f"asset-reference-{index:02d}-{label}"
            asset_ids.append(asset_id)
            connection.execute(
                "INSERT INTO assets(id, project_id, shot_id, kind, status, url, provider, model, metadata_json, created_at, updated_at) VALUES (?, ?, NULL, 'image', 'ready', ?, ?, ?, ?, ?, ?)",
                (
                    asset_id,
                    project_id,
                    f"/assets/{filename}",
                    "fixture",
                    "h3-identity-reference-pack",
                    json.dumps({"reference_role": label, "source_filename": source.name}, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
    return asset_ids


def main() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    asset_dir = RUN_DIR / "assets"
    store = StudioStore(RUN_DIR / "platform.sqlite3")
    service = StudioService(store, asset_dir)
    project = service.create_project(
        "H3平台真实三视图长链验收",
        "《命运模型》第一章 3DCG人物一致性生产门禁",
        style="3DCG",
        episode_length="15s",
        idempotency_key="h3-platform-reference-real-15s-v1",
    )
    episode_id = "episode-platform-h3-reference-15s-real-gpu-v1"
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO episodes(id, project_id, number, title, summary, conflict, hook, target_duration_seconds, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                episode_id,
                project["id"],
                1,
                "第一章三视图参考长链",
                "同一位3DCG国漫角色在废弃石桥上完成从远景到近景的连续表演",
                "",
                "",
                15,
                "draft",
            ),
        )
    reference_asset_ids = _register_reference_assets(service, project["id"])
    registry = ProviderRegistry.local()
    registry.video = ComfyUIVideoProvider(
        BASE_URL,
        str(FIRST_WORKFLOW),
        timeout=2400,
        poll_interval=2.0,
        long_video_first_workflow_path=str(FIRST_WORKFLOW),
        long_video_context_workflow_path=str(CONTEXT_WORKFLOW),
    )
    identity_prompt = (
        "subject_definitions: <Picture 1>正面参考；<Picture 2>侧面参考；<Picture 3>背面参考；"
        "<Picture 4>面部特写参考。四张图是同一位成年中国女性3DCG角色，只用于锁定身份，"
        "绝不渲染参考板、白底或工作室背景。"
        "identity_lock: 椭圆脸、杏眼、黑色半扎发、青玉发簪、朱红金绣汉服、深青腰封、"
        "黑色漆剑鞘；保持同一脸型、发型、服装、身材比例与材质。"
        "negative_constraints: 真人摄影、2D赛璐璐、参考图拼贴、字幕、可读文字、水印、重复人物、"
        "换脸、年龄变化、五官融化、歪眼、歪嘴、额外肢体、服装变化、硬切。"
        "画面为高端国漫3DCG游戏渲染，旧石桥、暖色夕阳、山雾和灯笼保持空间关系一致；"
        "加入连续山风、远处铜铃、布料摩擦的环境声，不要对白，不要音乐。"
    )
    plan = {
        "mode": "director-motion-context",
        "width": 640,
        "height": 384,
        "fps": 24,
        "steps": 8,
        "seed": 20260822,
        "sampler": "euler",
        "context_length": 22,
        "audio_context_length": 24,
        "reference_asset_ids": reference_asset_ids,
        "segments": [
            {
                "index": 1,
                "duration_seconds": 5.167,
                "role": "director",
                "prompt": identity_prompt + "镜头从稳定远景缓慢推进到中远景，角色沿旧石桥向灯笼走一步，动作克制。",
            },
            {
                "index": 2,
                "duration_seconds": 4.25,
                "role": "motion-context",
                "prompt": identity_prompt + "接续上一段最后一帧和声音，保持同一角色、桥、光线和行走方向，推进到中景；她停步看向桥侧灯笼。",
            },
            {
                "index": 3,
                "duration_seconds": 4.25,
                "role": "motion-context",
                "prompt": identity_prompt + "接续上一段，不换脸不换装；镜头进入中近景，角色轻微呼吸并转头看向山雾，眼睛、鼻梁、嘴唇和发簪保持清楚。",
            },
            {
                "index": 4,
                "duration_seconds": 4.25,
                "role": "motion-context",
                "prompt": identity_prompt + "接续上一段，保持动作和声音连续；稳定近景，角色握紧剑鞘并向画面右侧看，最终帧仍保持同一张脸和服装材质。",
            },
        ],
    }
    (RUN_DIR / "run-config.json").write_text(
        json.dumps(
            {
                "created_at": now(),
                "base_url": BASE_URL,
                "provider": "comfyui",
                "model_profile": MODEL_PROFILE,
                "first_workflow": str(FIRST_WORKFLOW),
                "context_workflow": str(CONTEXT_WORKFLOW),
                "reference_sources": [{"label": label, "path": str(source)} for label, source in REFERENCE_SOURCES],
                "reference_asset_ids": reference_asset_ids,
                "target_duration_seconds": 15,
                "plan": plan,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    with patch.object(service, "_providers_for_user", return_value=registry):
        result = service.request_long_video(
            episode_id,
            plan,
            run_now=True,
            idempotency_key="h3-platform-reference-long-v1",
        )
    (RUN_DIR / "platform-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "run_dir": str(RUN_DIR),
                "status": result.get("status"),
                "job_id": result.get("job", {}).get("id"),
                "final_video_url": result.get("final_video_url"),
                "segments": result.get("segments"),
                "reference_asset_ids": reference_asset_ids,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
