"""P2 自动 QC 规则层：媒体证据驱动的确定性检查。

对齐蓝图 §15：自动 QC 只输出结构化 finding；人脸/手指/背景漂移等
视觉检查必须由视觉模型给出（本模块暴露 expected 接口，返回值
unknown 表示本地规则无法判定）。人工结果统一为
Pass / Retake / Regenerate / Patch in edit / Blocked。

媒体证据（ffprobe、完整解码、抽帧）必须由上游提供；
本模块绝不把"文件存在"当成有效采样。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ManualDecision(str, Enum):
    PASS = "Pass"
    RETAKE = "Retake"
    REGENERATE = "Regenerate"
    PATCH_IN_EDIT = "Patch in edit"
    BLOCKED = "Blocked"


@dataclass
class MediaEvidence:
    """媒体机器验收证据（不可省略，else fail-closed）。"""

    asset_id: str
    ffprobe: dict[str, Any] = field(default_factory=dict)
    decoded_fully: bool | None = None
    frame_luma_sequence: list[float] | None = None  # 抽帧平均亮度
    audio_rms_sequence: list[float] | None = None  # 分窗 RMS
    head_frames: list[str] | None = None
    tail_frames: list[str] | None = None
    duration_contract_sec: float | None = None
    fps_contract: float | None = None
    resolution_contract: tuple[int, int] | None = None
    audio_duration_sec: float | None = None
    sha256: str | None = None


@dataclass
class QCFinding:
    code: str
    severity: str  # pass | warn | fail | unknown
    detail: str


@dataclass
class QCResult:
    asset_id: str
    findings: list[QCFinding] = field(default_factory=list)

    @property
    def has_gate_failures(self) -> bool:
        return any(f.severity == "fail" for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "findings": [{"code": f.code, "severity": f.severity, "detail": f.detail} for f in self.findings],
            "has_gate_failures": self.has_gate_failures,
        }


def _luma_black_ratio(values: list[float], threshold: float = 0.02) -> float:
    if not values:
        return 0.0
    black = sum(1 for v in values if v <= threshold)
    return black / len(values)


def _luma_delta_ratio(values: list[float], threshold: float = 0.001) -> float:
    """相邻帧亮度差过小的比例 → 静帧/重复帧风险。"""
    if len(values) < 2:
        return 0.0
    near_static = sum(1 for a, b in zip(values, values[1:]) if abs(a - b) <= threshold)
    return near_static / (len(values) - 1)


def _clipping_ratio(values: list[float], ceiling: float = 1.0) -> float:
    if not values:
        return 0.0
    clipped = sum(1 for v in values if abs(v) >= ceiling)
    return clipped / len(values)


def run_auto_qc(evidence: MediaEvidence) -> QCResult:
    """媒体证据 → 机器 QC 检查。证据缺失按 fail-closed 处理。"""
    result = QCResult(asset_id=evidence.asset_id)

    if not evidence.ffprobe and evidence.decoded_fully is None:
        result.findings.append(QCFinding("no-media-evidence", "fail", "无 ffprobe 且无解码声明"))
        return result

    if evidence.decoded_fully is False:
        result.findings.append(QCFinding("decode-failed", "fail", "完整解码未通过"))
    elif evidence.decoded_fully is None:
        result.findings.append(QCFinding("decode-unverified", "fail", "完整解码未验证"))

    # 时长合同
    if evidence.duration_contract_sec is not None:
        media_duration = (evidence.ffprobe.get("duration_sec")) if evidence.ffprobe else None
        if media_duration is not None:
            drift = abs(media_duration - evidence.duration_contract_sec)
            if drift > 0.5:
                result.findings.append(
                    QCFinding("duration-mismatch", "fail", f"{media_duration}s vs contract {evidence.duration_contract_sec}s")
                )

    # 音画偏移（音频时长 vs 容器时长）
    if evidence.audio_duration_sec is not None and evidence.ffprobe:
        container_duration = evidence.ffprobe.get("duration_sec")
        if container_duration is not None:
            drift_ms = abs(evidence.audio_duration_sec - container_duration) * 1000
            if drift_ms > 100:
                result.findings.append(
                    QCFinding("av-offset", "warn", f"audio/timeline drift {drift_ms:.1f}ms")
                )

    # 黑帧
    if evidence.frame_luma_sequence:
        ratio = _luma_black_ratio(evidence.frame_luma_sequence)
        if ratio > 0.30:
            result.findings.append(QCFinding("black-frame", "fail", f"{ratio:.0%} frames near-black"))
        elif ratio > 0.05:
            result.findings.append(QCFinding("black-frame", "warn", f"{ratio:.0%} frames near-black"))

    # 静帧
    if evidence.frame_luma_sequence:
        ratio = _luma_delta_ratio(evidence.frame_luma_sequence)
        if ratio > 0.80:
            result.findings.append(QCFinding("static-frame", "fail", f"{ratio:.0%} near-static consecutive frames"))

    # 静音 / 爆音
    if evidence.audio_rms_sequence:
        if all(v <= 1e-4 for v in evidence.audio_rms_sequence):
            result.findings.append(QCFinding("silent-audio", "fail", "音频全程静音"))
        if _clipping_ratio(evidence.audio_rms_sequence) > 0.02:
            result.findings.append(QCFinding("clipping-audio", "warn", "存在削波窗口"))

    # 视觉类检查：本地规则无法判定，显式 unknown（不得伪造视觉结论）
    result.findings.append(
        QCFinding(
            "visual-face-region",
            "unknown",
            "人脸/手指/背景漂移需视觉模型或人工审片，本地规则不判定",
        )
    )

    return result


def draft_defect_taxonomy(report: QCResult) -> list[str]:
    """失败 → 缺陷分类建议（供 DEFECT_TAXONOMY.md 归并）。"""
    return [f.code for f in report.findings if f.severity == "fail"]