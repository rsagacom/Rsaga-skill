"""视觉一致性检查的领域合同与 provider adapter。

平台不自研人脸识别、服装特征提取或场景 embedding。视觉模型、ComfyUI
工作流或外部视觉服务只需要实现 ``ConsistencyVisionProvider``；本地预览
provider 明确只能返回 ``unknown``，不能因为图片文件存在或接口可达就伪造
通过结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class ConsistencyDimension(str, Enum):
    FACE = "face"
    COSTUME = "costume"
    SCENE = "scene"
    POSE = "pose"


class ConsistencyStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConsistencyRequest:
    candidate_asset_id: str
    reference_asset_ids: tuple[str, ...] = ()
    dimensions: tuple[ConsistencyDimension, ...] = (
        ConsistencyDimension.FACE,
        ConsistencyDimension.COSTUME,
        ConsistencyDimension.SCENE,
    )
    shot_id: str | None = None
    style: str = ""
    shot_size: str = ""

    def __post_init__(self) -> None:
        if not str(self.candidate_asset_id).strip():
            raise ValueError("candidate_asset_id is required")
        references = tuple(dict.fromkeys(str(value).strip() for value in self.reference_asset_ids if str(value).strip()))
        if not references:
            raise ValueError("at least one reference_asset_id is required")
        dimensions = tuple(dict.fromkeys(self.dimensions))
        if not dimensions:
            raise ValueError("at least one consistency dimension is required")
        if any(not isinstance(value, ConsistencyDimension) for value in dimensions):
            raise ValueError("dimensions must use ConsistencyDimension")
        object.__setattr__(self, "candidate_asset_id", str(self.candidate_asset_id).strip())
        object.__setattr__(self, "reference_asset_ids", references)
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "shot_id", str(self.shot_id).strip() or None if self.shot_id else None)
        object.__setattr__(self, "style", str(self.style or "").strip())
        object.__setattr__(self, "shot_size", str(self.shot_size or "").strip())


@dataclass(frozen=True)
class ConsistencyFinding:
    dimension: ConsistencyDimension
    status: ConsistencyStatus
    detail: str
    provider: str
    model: str
    evidence_refs: tuple[str, ...] = ()
    score: float | None = None
    threshold: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "status": self.status.value,
            "detail": self.detail,
            "provider": self.provider,
            "model": self.model,
            "evidence_refs": list(self.evidence_refs),
            "score": self.score,
            "threshold": self.threshold,
        }


@dataclass
class ConsistencyReport:
    request: ConsistencyRequest
    findings: list[ConsistencyFinding] = field(default_factory=list)

    @property
    def overall_status(self) -> ConsistencyStatus:
        statuses = {finding.status for finding in self.findings}
        if ConsistencyStatus.FAIL in statuses:
            return ConsistencyStatus.FAIL
        if ConsistencyStatus.UNKNOWN in statuses or not statuses:
            return ConsistencyStatus.UNKNOWN
        if ConsistencyStatus.WARN in statuses:
            return ConsistencyStatus.WARN
        return ConsistencyStatus.PASS

    @property
    def ready_for_adoption(self) -> bool:
        """只有所有请求维度均有证据且通过，才允许进入采用门禁。"""
        return bool(self.findings) and all(
            finding.status == ConsistencyStatus.PASS and bool(finding.evidence_refs)
            for finding in self.findings
        ) and len({finding.dimension for finding in self.findings}) == len(self.request.dimensions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_asset_id": self.request.candidate_asset_id,
            "reference_asset_ids": list(self.request.reference_asset_ids),
            "shot_id": self.request.shot_id,
            "style": self.request.style,
            "shot_size": self.request.shot_size,
            "overall_status": self.overall_status.value,
            "ready_for_adoption": self.ready_for_adoption,
            "findings": [finding.to_dict() for finding in self.findings],
        }


class ConsistencyVisionProvider(Protocol):
    name: str
    model: str

    def inspect(self, request: ConsistencyRequest) -> list[ConsistencyFinding]:
        """返回每个请求维度的结果；不得以 HTTP 200 或文件存在代替证据。"""


class UnknownConsistencyProvider:
    """本地/fake provider：永远 unknown，供开发和离线测试使用。"""

    name = "local"
    model = "unknown-visual-provider"

    def inspect(self, request: ConsistencyRequest) -> list[ConsistencyFinding]:
        return [
            ConsistencyFinding(
                dimension=dimension,
                status=ConsistencyStatus.UNKNOWN,
                detail="本地 provider 不具备视觉判断能力，需要真实视觉模型或人工审片",
                provider=self.name,
                model=self.model,
            )
            for dimension in request.dimensions
        ]


class ConsistencyAdapter:
    """把可替换视觉 provider 收敛为 fail-closed 的平台报告。"""

    def __init__(self, provider: ConsistencyVisionProvider | None = None) -> None:
        self.provider = provider or UnknownConsistencyProvider()

    def inspect(self, request: ConsistencyRequest) -> ConsistencyReport:
        provider_name = str(getattr(self.provider, "name", "unknown-provider"))
        provider_model = str(getattr(self.provider, "model", "unknown-model"))
        try:
            raw_findings = self.provider.inspect(request)
        except Exception as exc:  # provider failure is an unknown gate, never a pass
            return ConsistencyReport(request, [
                self._unknown(
                    dimension,
                    provider_name,
                    provider_model,
                    f"视觉 provider 执行失败，需重试或人工审片：{type(exc).__name__}",
                )
                for dimension in request.dimensions
            ])
        by_dimension: dict[ConsistencyDimension, ConsistencyFinding] = {}
        if isinstance(raw_findings, list):
            for finding in raw_findings:
                normalized = self._normalize(finding, provider_name, provider_model)
                if normalized is not None and normalized.dimension not in by_dimension:
                    by_dimension[normalized.dimension] = normalized
        findings = [
            by_dimension.get(
                dimension,
                self._unknown(dimension, provider_name, provider_model, "视觉 provider 未返回该维度结果"),
            )
            for dimension in request.dimensions
        ]
        return ConsistencyReport(request, findings)

    @staticmethod
    def _unknown(
        dimension: ConsistencyDimension,
        provider: str,
        model: str,
        detail: str,
    ) -> ConsistencyFinding:
        return ConsistencyFinding(dimension, ConsistencyStatus.UNKNOWN, detail, provider, model)

    @classmethod
    def _normalize(
        cls,
        finding: Any,
        provider: str,
        model: str,
    ) -> ConsistencyFinding | None:
        if not isinstance(finding, ConsistencyFinding):
            return None
        if finding.status in {ConsistencyStatus.PASS, ConsistencyStatus.WARN, ConsistencyStatus.FAIL} and not finding.evidence_refs:
            return cls._unknown(
                finding.dimension,
                provider,
                model,
                "provider 返回了结论但没有 evidence_refs，已按 fail-closed 降级为 unknown",
            )
        if finding.score is not None and not 0 <= finding.score <= 1:
            return cls._unknown(
                finding.dimension,
                provider,
                model,
                "provider score 不在 0..1，已按 fail-closed 降级为 unknown",
            )
        return ConsistencyFinding(
            dimension=finding.dimension,
            status=finding.status,
            detail=str(finding.detail or ""),
            provider=provider,
            model=model,
            evidence_refs=tuple(str(value) for value in finding.evidence_refs if str(value).strip()),
            score=finding.score,
            threshold=finding.threshold,
        )


def local_consistency_adapter() -> ConsistencyAdapter:
    """显式返回开发默认 adapter，方便 API/worker 注入而不绑定视觉模型。"""
    return ConsistencyAdapter(UnknownConsistencyProvider())
