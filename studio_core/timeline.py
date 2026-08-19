"""P0 OTIO 时间线基础：从 SHOTLIST 导出 OpenTimelineIO JSON，并支持最小导入。

纯 stdlib 实现，不依赖 opentimelineio 包（P0 只要求时间线结构可复用；
完整 OTIO 契约验证在 P3 引入官方 SDK 时补上媒体引用自愈）。

OTIO JSON 序列化约定（OpenTimelineIO 1.x）：
- 顶层 "OTIO_SCHEMA": "Timeline.1"，"tracks" 是 Stack.1；
- 每条 Track 分 Video/Audio，children 是 Clip.1；
- Clip 的 source_range 是 TimeRange.1，start_time/duration 是 RationalTime.1；
- media_references.DEFAULT_MEDIA 是 ExternalReference.1（用相对路径，禁止绝对路径）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TimelineClip:
    """镜头到时间线 clip 的映射。"""

    shot_id: str
    start_sec: float
    duration_sec: float
    media_rel_path: str
    sfx: list[str] = field(default_factory=list)
    bgm: str | None = None
    dialogue: str | None = None


@dataclass
class Timeline:
    """可导出的 P0 时间线。"""

    name: str
    fps: float
    clips: list[TimelineClip] = field(default_factory=list)


def _rational_time(value: float, rate: float) -> dict[str, Any]:
    """浮点秒 → OTIO RationalTime。

    24fps 时 0.0416667 秒误差异常，直接按 rate*value 四舍五入到整数帧。
    """
    return {
        "OTIO_SCHEMA": "RationalTime.1",
        "value": round(value * rate),
        "rate": rate,
    }


def _clip_time(clip: TimelineClip, fps: float, offset_sec: float) -> dict[str, Any]:
    duration = _rational_time(clip.duration_sec, fps)
    start = _rational_time(offset_sec + clip.start_sec, fps)
    return {
        "OTIO_SCHEMA": "Clip.1",
        "name": clip.shot_id,
        "source_range": {
            "OTIO_SCHEMA": "TimeRange.1",
            "start_time": _rational_time(0.0, fps),
            "duration": duration,
        },
        "media_references": {
            "DEFAULT_MEDIA": {
                "OTIO_SCHEMA": "ExternalReference.1",
                "target_url": clip.media_rel_path,
            }
        },
        "metadata": {
            "shot_id": clip.shot_id,
            "dialogue": clip.dialogue or "",
            "sfx": list(clip.sfx),
            "bgm": clip.bgm or "",
        },
        "effects": [],
        "markers": [],
        "enabled": True,
    }


def timeline_to_dict(timeline: Timeline) -> dict[str, Any]:
    """Timeline → OTIO JSON 字典。"""
    offset = 0.0
    video_clips = []
    audio_clips = []
    for clip in timeline.clips:
        video_clips.append(_clip_time(clip, timeline.fps, offset))
        audio_clips.append(_clip_time(clip, timeline.fps, offset))
        offset += clip.duration_sec

    return {
        "OTIO_SCHEMA": "Timeline.1",
        "name": timeline.name,
        "global_start_time": None,
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "tracks",
            "children": [
                {
                    "OTIO_SCHEMA": "Track.1",
                    "name": "V1",
                    "kind": "Video",
                    "children": video_clips,
                    "effects": [],
                    "markers": [],
                    "enabled": True,
                },
                {
                    "OTIO_SCHEMA": "Track.1",
                    "name": "A1",
                    "kind": "Audio",
                    "children": audio_clips,
                    "effects": [],
                    "markers": [],
                    "enabled": True,
                },
            ],
            "effects": [],
            "markers": [],
            "enabled": True,
        },
    }


def timeline_to_json(timeline: Timeline, indent: int | None = 2) -> str:
    import json

    return json.dumps(timeline_to_dict(timeline), ensure_ascii=False, indent=indent)


def export_timeline_json(timeline: Timeline, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(timeline_to_json(timeline))


def import_timeline(data: dict[str, Any]) -> Timeline:
    """从 OTIO JSON 字典恢复 P0 Timeline（媒体引用不验证 URL）。

    只支持本模块导出的结构：Timeline.1 → Stack → Track → Clip.1。
    """
    if data.get("OTIO_SCHEMA") != "Timeline.1":
        raise ValueError("not an OTIO Timeline.1 document")
    fps = 24.0
    clips: list[TimelineClip] = []
    tracks = data.get("tracks", {})
    for track in tracks.get("children", []):
        if track.get("kind") != "Video":
            continue
        offset_frames = 0
        rate = 24.0
        for child in track.get("children", []):
            name = child.get("name", "")
            source_range = child.get("source_range", {})
            duration_rt = source_range.get("duration", {})
            rate = float(duration_rt.get("rate", 24.0))
            duration_sec = round(
                float(duration_rt.get("value", 0)) / rate if rate else 0.0,
                6,
            )
            media = child.get("media_references", {}).get("DEFAULT_MEDIA", {})
            media_rel = media.get("target_url", "") or ""
            metadata = child.get("metadata", {})
            clips.append(
                TimelineClip(
                    shot_id=name,
                    start_sec=round(offset_frames / rate, 6),
                    duration_sec=duration_sec,
                    media_rel_path=media_rel,
                    sfx=[s for s in metadata.get("sfx", []) if isinstance(s, str)],
                    bgm=metadata.get("bgm") or None,
                    dialogue=metadata.get("dialogue") or None,
                )
            )
            offset_frames += duration_rt.get("value", 0)
        fps = rate
    return Timeline(name=data.get("name", ""), fps=fps, clips=clips)


def build_timeline_from_shotlist(
    name: str,
    fps: float,
    shots: list[dict[str, Any]],
    *,
    media_root: str = "assets",
) -> Timeline:
    """SHOTLIST shots → Timeline。

    每个 shot 一个 clip，media_rel_path = <media_root>/<shot_id>.mp4。
    """
    clips = []
    start = 0.0
    for shot in shots:
        duration = float(shot.get("duration_sec", 3.0))
        shot_id = str(shot.get("id", f"SHOT_{len(clips) + 1:03d}"))
        audio = shot.get("audio") or {}
        clips.append(
            TimelineClip(
                shot_id=shot_id,
                start_sec=start,
                duration_sec=duration,
                media_rel_path=f"{media_root}/{shot_id}.mp4",
                sfx=[str(s) for s in audio.get("sfx", [])],
                bgm=audio.get("bgm") if isinstance(audio, dict) else None,
                dialogue=shot.get("dialogue"),
            )
        )
        start += duration
    return Timeline(name=name, fps=fps, clips=clips)