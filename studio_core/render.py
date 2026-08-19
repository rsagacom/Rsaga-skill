"""P1 RenderJob 合同：缓存键、失败分类与幂等状态机。

对齐蓝图 §13.2：缓存键必须包含 project、shot、canon/asset/prompt/
workflow/model/lora revision、seed、分辨率、fps、frames、steps 与
provider_profile；任何版本变化必须自然失效。

失败分类（蓝图 §13.2 完整清单）用于重试策略路由，不允许统一"再抽一张"。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobState(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEDOUT = "timedout"


class FailureClass(str, Enum):
    """失败分类（蓝图 §13.2）。不同类别对应不同重跑策略。"""

    OOM = "OOM"
    SAMPLE_CORRUPTION = "sampling_corruption"
    FACE_COLLAPSE = "face_collapse"
    MOTION_DRIFT = "motion_drift"
    BACKGROUND_DRIFT = "background_drift"
    AV_SYNC = "av_sync"
    SUBTITLE_OFFSET = "subtitle_offset"
    TIMEOUT = "timeout"
    NETWORK = "network"
    REMOTE_GPU = "remote_gpu"
    LICENSE_REJECT = "license_reject"
    CONTENT_SAFETY_REJECT = "content_safety_reject"
    UNKNOWN = "unknown"


# 不同失败类别的重跑策略模板（蓝图 §13.2：不能统一重抽）
RETRY_POLICY: dict[FailureClass, dict[str, Any]] = {
    FailureClass.OOM: {"max_retries": 1, "backoff": "reset_gpu", "action": "retry_local_gpu"},
    FailureClass.SAMPLE_CORRUPTION: {"max_retries": 2, "backoff": "linear", "action": "retry_same_seed"},
    FailureClass.FACE_COLLAPSE: {"max_retries": 0, "backoff": None, "action": "manual_reference_rebuild"},
    FailureClass.MOTION_DRIFT: {"max_retries": 1, "backoff": "linear", "action": "retry_with_reference"},
    FailureClass.BACKGROUND_DRIFT: {"max_retries": 1, "backoff": "linear", "action": "rewrite_prompt_and_retry"},
    FailureClass.AV_SYNC: {"max_retries": 0, "backoff": None, "action": "re_edit_audio_track"},
    FailureClass.SUBTITLE_OFFSET: {"max_retries": 0, "backoff": None, "action": "fix_subtitle_and_remux"},
    FailureClass.TIMEOUT: {"max_retries": 1, "backoff": "exponential", "action": "retry_with_longer_timeout"},
    FailureClass.NETWORK: {"max_retries": 3, "backoff": "exponential", "action": "retry"},
    FailureClass.REMOTE_GPU: {"max_retries": 2, "backoff": "exponential", "action": "requeue_remote"},
    FailureClass.LICENSE_REJECT: {"max_retries": 0, "backoff": None, "action": "blocked"},
    FailureClass.CONTENT_SAFETY_REJECT: {"max_retries": 0, "backoff": None, "action": "blocked"},
    FailureClass.UNKNOWN: {"max_retries": 1, "backoff": "linear", "action": "manual_triage"},
}


@dataclass
class RenderJobSpec:
    """渲染任务的不可变输入（缓存键来源）。"""

    project: str
    shot: str
    canon_revision: str
    asset_revision: str
    prompt_revision: str
    workflow_revision: str
    model_revision: str
    lora_revision: str
    seed: int | None
    width: int
    height: int
    fps: int
    frames: int
    steps: int
    provider_profile: str


@dataclass
class RenderJob:
    """可恢复渲染任务（蓝图 §13.2 完整字段）。"""

    job_id: str
    spec: RenderJobSpec
    state: JobState = JobState.PENDING
    priority: int = 0
    depends_on: list[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int | None = None
    timeout_seconds: int | None = None
    cost_limit: float | None = None
    gpu_lease: dict[str, Any] = field(default_factory=dict)
    vram_reserve_mb: int | None = None
    failure_class: FailureClass | None = None
    evidence_dir: str | None = None
    output_asset_ids: list[str] = field(default_factory=list)
    auto_qc_summary: dict[str, Any] = field(default_factory=dict)


def compute_cache_key(spec: RenderJobSpec) -> str:
    """缓存键（蓝图 §13.2 成分表，顺序固定）。"""
    parts = [
        spec.project,
        spec.shot,
        spec.canon_revision,
        spec.asset_revision,
        spec.prompt_revision,
        spec.workflow_revision,
        spec.model_revision,
        spec.lora_revision,
        str(spec.seed) if spec.seed is not None else "seed_none",
        str(spec.width),
        str(spec.height),
        str(spec.fps),
        str(spec.frames),
        str(spec.steps),
        spec.provider_profile,
    ]
    payload = "\x1f".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_key_stability(spec: RenderJobSpec, mutate: dict[str, Any]) -> str:
    """构造一个受 mutate 字段影响的变体，用于验证"任一版本变化必须失效"。"""
    import dataclasses as _dc

    changed = _dc.replace(spec, **mutate)
    return compute_cache_key(changed)


def mark_failed(job: RenderJob, failure: FailureClass) -> dict[str, Any]:
    """失败分类落账并返回对应重试策略。"""
    job.failure_class = failure
    policy = RETRY_POLICY[failure]
    job.max_retries = policy["max_retries"]
    if job.retry_count < policy["max_retries"]:
        return {"retry": True, "action": policy["action"], "backoff": policy["backoff"]}
    job.state = JobState.FAILED
    return {"retry": False, "action": policy["action"], "backoff": policy["backoff"]}


def classify_failure(reason: str, default: FailureClass = FailureClass.UNKNOWN) -> FailureClass:
    """从平台日志/错误串做保守分类；未知一律 UNKNOWN 而非猜测。"""
    table = {
        "out of memory": FailureClass.OOM,
        "cuda oom": FailureClass.OOM,
        "null": FailureClass.SAMPLE_CORRUPTION,
        "decode": FailureClass.SAMPLE_CORRUPTION,
    }
    lowered = reason.lower()
    for needle, failure in table.items():
        if needle in lowered:
            return failure
    return default