"""P1 Animatic 节奏检查：静态分镜 + 对白时码的规则级预审。

对齐蓝图 §13.1：Animatic 的目标不是画质，而是提前发现镜头过长、
对白过密、动作接不上、轴线跳切、声音硬切等问题。
本模块输出结构化 finding，供人工审阅，不自动改 shot。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AnimaticFinding:
    code: str
    shot_id: str
    detail: str
    severity: str  # info | warn | fail


@dataclass
class AnimaticReview:
    findings: list[AnimaticFinding] = field(default_factory=list)

    @property
    def fails(self) -> list[AnimaticFinding]:
        return [f for f in self.findings if f.severity == "fail"]

    @property
    def warnings(self) -> list[AnimaticFinding]:
        return [f for f in self.findings if f.severity == "warn"]


# 对白密度阈值：中文对白每秒字数（超出则 "对白过密"）
MAX_DIALOGUE_CPS = 6.0
MIN_SHOT_DURATION_SEC = 0.5
MAX_SHOT_DURATION_SEC = 12.0  # 3060 H3 单段门禁（832×480 10-12s 上限）


def _dialogue_cps(shot: dict[str, Any]) -> float | None:
    dialogue = shot.get("dialogue")
    duration = shot.get("duration_sec")
    if not dialogue or not duration:
        return None
    try:
        dur = float(duration)
    except (TypeError, ValueError):
        return None
    if dur <= 0:
        return None
    # 中文按字符数计算（不含空白）
    chars = len("".join(str(dialogue).split()))
    return chars / dur


def review_animatic(shots: list[dict[str, Any]]) -> AnimaticReview:
    """对 shot 序列做规则级 Animatic 检查。"""
    review = AnimaticReview()
    for i, shot in enumerate(shots):
        shot_id = str(shot.get("id", f"shot-{i}"))

        # 镜头时长
        try:
            duration = float(shot.get("duration_sec", 0))
        except (TypeError, ValueError):
            duration = 0.0
        if duration < MIN_SHOT_DURATION_SEC:
            review.findings.append(
                AnimaticFinding("shot-too-short", shot_id, f"{duration}s < {MIN_SHOT_DURATION_SEC}s", "fail")
            )
        elif duration > MAX_SHOT_DURATION_SEC:
            review.findings.append(
                AnimaticFinding(
                    "shot-too-long",
                    shot_id,
                    f"{duration}s > {MAX_SHOT_DURATION_SEC}s（H3 单段门禁上限）",
                    "warn",
                )
            )

        # 对白密度
        cps = _dialogue_cps(shot)
        if cps is not None and cps > MAX_DIALOGUE_CPS:
            review.findings.append(
                AnimaticFinding(
                    "dialogue-too-dense",
                    shot_id,
                    f"{cps:.1f} 字/秒 > {MAX_DIALOGUE_CPS}（对白过密，建议拆镜或删词）",
                    "warn",
                )
            )

        # 轴线跳切：相邻镜头从側翼切到另一侧且无跨轴标记
        if i > 0:
            prev = shots[i - 1]
            prev_axis = (prev.get("camera") or {}).get("axis")
            cur_axis = (shot.get("camera") or {}).get("axis")
            if (
                prev_axis in {"180_left", "180_right"}
                and cur_axis in {"180_left", "180_right"}
                and prev_axis != cur_axis
                and cur_axis != "cross_axis_flagged"
            ):
                review.findings.append(
                    AnimaticFinding(
                        "axis-jump",
                        shot_id,
                        f"轴线从 {prev_axis} 切到 {cur_axis}，未标记 cross_axis_flagged",
                        "warn",
                    )
                )

        # 连续性链：continuity_from 必须指向前一个镜头
        continuity_from = shot.get("continuity_from")
        if i == 0:
            if continuity_from not in (None, ""):
                review.findings.append(
                    AnimaticFinding(
                        "continuity-orphan-head", shot_id, f"首镜 continuity_from={continuity_from}", "warn"
                    )
                )
        else:
            prev_id = shots[i - 1].get("id")
            if continuity_from != prev_id:
                review.findings.append(
                    AnimaticFinding(
                        "continuity-broken",
                        shot_id,
                        f"continuity_from={continuity_from} != 前镜 {prev_id}",
                        "fail",
                    )
                )

        # 声音硬切：相邻镜头 sfx 完全不重叠且无 ambient_bed 承接
        if i > 0:
            prev_audio = (shots[i - 1].get("audio") or {})
            cur_audio = (shot.get("audio") or {})
            prev_sfx = set(prev_audio.get("sfx", []))
            cur_sfx = set(cur_audio.get("sfx", []))
            prev_bed = set(prev_audio.get("ambient_bed", []))
            cur_bed = set(cur_audio.get("ambient_bed", []))
            if (prev_sfx or cur_sfx) and not (prev_sfx & cur_sfx) and not (prev_bed & cur_bed):
                review.findings.append(
                    AnimaticFinding(
                        "audio-hard-cut",
                        shot_id,
                        "相邻镜头 SFX 无重叠且无共同环境底，可能硬切",
                        "warn",
                    )
                )

    return review