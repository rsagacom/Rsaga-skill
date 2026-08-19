"""P1 Workflow Registry：从 production-profile 加载已登记工作流并做结构校验。

对齐蓝图 §13.2：每个 ComfyUI workflow 登记 workflow_id / 版本 / 输入输出
schema / 分辨率 / 显存 / 节点 / LoRA / workflow revision 与真实证据。
本模块只做"登记与结构合同校验"，不猜测节点语义（真实节点兼容性由
Provider smoke 与 GPU benchmark 验收，见 workflow_contract.py 注释）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .workflow_contract import validate_comfyui_api_workflow


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data



@dataclass
class WorkflowRegistration:
    """一条已登记 workflow 的可验证事实。"""

    workflow_id: str
    role: str  # image | video | long_video_first | long_video_context
    file_name: str
    absolute_path: str
    registry_claims: dict = field(default_factory=dict)
    structural_errors: list[str] = field(default_factory=list)
    exists: bool = False
    sha256: str | None = None

    @property
    def valid(self) -> bool:
        return self.exists and not self.structural_errors


@dataclass
class WorkflowRegistry:
    """profile + 工作流文件的登记视图。"""

    profile: dict = field(default_factory=dict)
    registrations: list[WorkflowRegistration] = field(default_factory=list)
    profile_errors: list[str] = field(default_factory=list)


def load_registry(
    production_dir: str | Path,
    *,
    profile_name: str = "production-profile.json",
) -> WorkflowRegistry:
    """加载 production 目录的 profile 并登记所有 workflow 文件。"""
    root = Path(production_dir)
    profile_path = root / profile_name
    registry = WorkflowRegistry()

    if not profile_path.exists():
        registry.profile_errors.append(f"profile missing: {profile_name}")
        return registry
    registry.profile = _load_json(profile_path)

    workflows = registry.profile.get("workflows", {})
    long_video = registry.profile.get("long_video", {})
    mode_to_role = {"t2v": "video", "r2v": "video", "i2v": "video", "v2v": "video"}
    checked: dict[str, str] = {}
    for mode, file_name in workflows.items():
        if isinstance(file_name, str):
            checked[file_name] = mode_to_role.get(mode, "video")
    for key, role in (
        ("first_segment_workflow", "long_video_first"),
        ("continuation_workflow", "long_video_context"),
    ):
        file_name = long_video.get(key)
        if isinstance(file_name, str):
            checked[file_name] = role


    import hashlib

    for file_name in sorted(set(checked.keys())):
        path = root / file_name
        reg = WorkflowRegistration(
            workflow_id=file_name.removesuffix(".json"),
            role=str(checked[file_name]),
            file_name=file_name,
            absolute_path=str(path),
        )
        if path.exists():
            reg.exists = True
            reg.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                reg.structural_errors = validate_comfyui_api_workflow(
                    payload, role=reg.role
                )
            except (json.JSONDecodeError, OSError) as exc:
                reg.structural_errors = [f"workflow-json-invalid: {exc.__class__.__name__}"]
        registry.registrations.append(reg)

    return registry


def gate_report(registry: WorkflowRegistry) -> dict:
    """登记门禁报告：有哪些 workflow 可用、哪些缺文件、哪些结构不合格。"""
    missing = [r.file_name for r in registry.registrations if not r.exists]
    invalid = [
        {"file": r.file_name, "errors": r.structural_errors}
        for r in registry.registrations
        if r.exists and r.structural_errors
    ]
    return {
        "profile_errors": registry.profile_errors,
        "registered": [r.file_name for r in registry.registrations],
        "valid": [r.file_name for r in registry.registrations if r.valid],
        "missing": missing,
        "structurally_invalid": invalid,
    }