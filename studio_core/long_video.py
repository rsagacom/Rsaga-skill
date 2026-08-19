"""可恢复的 H3 长视频计划合同。

这里故意只描述生产计划，不把 ComfyUI 节点 ID、宿主机路径或密钥暴露到
API。Director 负责上层分段编排，Motion Context 负责后续段落的 AV latent
接力；两者是不同的 workflow role，不允许把运行时补丁混成一个进程合同。
"""

from __future__ import annotations

import math
from typing import Any


MAX_LONG_VIDEO_SEGMENTS = 120
MAX_SEGMENT_SECONDS = 15.0
MIN_SEGMENT_SECONDS = 1.0
ALLOWED_LONG_VIDEO_MODES = frozenset({"director", "director-motion-context"})
ALLOWED_ROLES = frozenset({"director", "motion-context"})
ALLOWED_SAMPLERS = frozenset({"euler", "euler_a", "res_multistep"})


class LongVideoPlanError(ValueError):
    """输入计划不满足生产门禁。"""


def _number(value: Any, name: str, *, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LongVideoPlanError(f"{name} must be a number") from exc
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise LongVideoPlanError(f"{name} must be between {minimum:g} and {maximum:g}")
    return result


def _integer(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise LongVideoPlanError(f"{name} must be an integer") from exc
    if result < minimum or result > maximum:
        raise LongVideoPlanError(f"{name} must be between {minimum} and {maximum}")
    return result


def normalize_long_video_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化平台侧长视频计划。

    ``segments`` 是 Director 已经确定的叙事单元；平台不会偷偷重写提示词。
    第二段及之后必须由 Motion Context 承接，避免把“长视频”误做成互不相关
    的独立抽卡片段。
    """

    if not isinstance(payload, dict):
        raise LongVideoPlanError("long-video plan must be an object")
    mode = str(payload.get("mode") or "director-motion-context").strip().lower()
    if mode not in ALLOWED_LONG_VIDEO_MODES:
        raise LongVideoPlanError("unsupported long-video mode")
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise LongVideoPlanError("segments must be a non-empty list")
    if len(raw_segments) > MAX_LONG_VIDEO_SEGMENTS:
        raise LongVideoPlanError(f"segments cannot exceed {MAX_LONG_VIDEO_SEGMENTS}")

    width = _integer(payload.get("width", 640), "width", minimum=64, maximum=2048)
    height = _integer(payload.get("height", 384), "height", minimum=64, maximum=2048)
    fps = _number(payload.get("fps", 24), "fps", minimum=1, maximum=60)
    steps = _integer(payload.get("steps", 8), "steps", minimum=1, maximum=50)
    seed = _integer(payload.get("seed", 0), "seed", minimum=0, maximum=2**63 - 1)
    sampler = str(payload.get("sampler", "euler")).strip().lower()
    if sampler not in ALLOWED_SAMPLERS:
        raise LongVideoPlanError("unsupported sampler")
    context_length = _integer(payload.get("context_length", 22), "context_length", minimum=1, maximum=256)
    audio_context_length = _integer(payload.get("audio_context_length", 24), "audio_context_length", minimum=1, maximum=256)
    source_asset_id = str(payload.get("source_asset_id") or "").strip() or None
    raw_reference_asset_ids = payload.get("reference_asset_ids") or []
    if not isinstance(raw_reference_asset_ids, list):
        raise LongVideoPlanError("reference_asset_ids must be a list")
    reference_asset_ids: list[str] = []
    for raw_reference_asset_id in raw_reference_asset_ids:
        reference_asset_id = str(raw_reference_asset_id or "").strip()
        if reference_asset_id and reference_asset_id not in reference_asset_ids:
            reference_asset_ids.append(reference_asset_id)
    if len(reference_asset_ids) > 8:
        raise LongVideoPlanError("reference_asset_ids cannot exceed 8 images")

    segments: list[dict[str, Any]] = []
    total_duration = 0.0
    for position, raw_segment in enumerate(raw_segments, start=1):
        if not isinstance(raw_segment, dict):
            raise LongVideoPlanError("each segment must be an object")
        index = _integer(raw_segment.get("index", position), f"segments[{position - 1}].index", minimum=1, maximum=MAX_LONG_VIDEO_SEGMENTS)
        if index != position:
            raise LongVideoPlanError("segment indexes must be contiguous and start at 1")
        duration = _number(raw_segment.get("duration_seconds", 5), f"segments[{position - 1}].duration_seconds", minimum=MIN_SEGMENT_SECONDS, maximum=MAX_SEGMENT_SECONDS)
        prompt = str(raw_segment.get("prompt") or "").strip()
        if not prompt or len(prompt) > 12_000:
            raise LongVideoPlanError("each segment prompt must contain 1 to 12000 characters")
        role = "director" if position == 1 else "motion-context"
        requested_role = str(raw_segment.get("role") or role).strip().lower()
        if requested_role != role or requested_role not in ALLOWED_ROLES:
            raise LongVideoPlanError("the first segment must use director and later segments must use motion-context")
        item = {
            "index": index,
            "duration_seconds": round(duration, 3),
            "prompt": prompt,
            "role": role,
            "status": "pending",
            "output": None,
        }
        segments.append(item)
        total_duration += duration

    if total_duration > 900:
        raise LongVideoPlanError("total long-video duration cannot exceed 900 seconds")
    return {
        "version": 1,
        "mode": mode,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "steps": steps,
        "seed": seed,
        "sampler": sampler,
        "context_length": context_length,
        "audio_context_length": audio_context_length,
        "source_asset_id": source_asset_id,
        "reference_asset_ids": reference_asset_ids,
        "total_duration_seconds": round(total_duration, 3),
        "segment_count": len(segments),
        "segments": segments,
    }


__all__ = ["LongVideoPlanError", "normalize_long_video_plan", "MAX_LONG_VIDEO_SEGMENTS"]
