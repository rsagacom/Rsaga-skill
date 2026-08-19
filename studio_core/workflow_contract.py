"""ComfyUI API-format workflow 的无网络结构校验。

ComfyUI 的 ``Save (API Format)`` 输出是一个节点 ID 到节点对象的映射。
这里只校验不会随模型/自定义节点变化的结构合同，不猜测具体 checkpoint、
采样器或 Wan/LTX 节点语义；真实节点兼容性仍由 Provider smoke 和 GPU
benchmark 验收。
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


SUPPORTED_PLACEHOLDERS = frozenset(
    {
        "{{PROMPT}}",
        "{{NEGATIVE_PROMPT}}",
        "{{CLIENT_ID}}",
        "{{ASSET_ID}}",
        "{{SHOT_ID}}",
        "{{IMAGE_REF}}",
        "{{IMAGE_FILENAME}}",
        "{{SOURCE_IMAGE_FILENAME}}",
        "{{IMAGE_SUBFOLDER}}",
        "{{IMAGE_TYPE}}",
        "{{REFERENCE_IMAGE_0_REF}}",
        "{{REFERENCE_IMAGE_1_REF}}",
        "{{REFERENCE_IMAGE_2_REF}}",
        "{{REFERENCE_IMAGE_3_REF}}",
        "{{REFERENCE_IMAGE_4_REF}}",
        "{{REFERENCE_IMAGE_5_REF}}",
        "{{REFERENCE_IMAGE_6_REF}}",
        "{{REFERENCE_IMAGE_7_REF}}",
        # H3 长视频分段：只允许由服务端计划生成，不能由客户端注入任意路径。
        "{{SEGMENT_INDEX}}",
        "{{SEGMENT_PROMPT}}",
        "{{TIMELINE_DATA}}",
        "{{CONTEXT_LATENT_PATH}}",
        "{{CONTEXT_CLIP_INDEX}}",
        "{{LATENT_OUTPUT_PREFIX}}",
        "{{OUTPUT_PREFIX}}",
        "{{WIDTH}}",
        "{{HEIGHT}}",
        "{{FRAMES}}",
        "{{FPS}}",
        "{{STEPS}}",
        "{{SEED}}",
        "{{SAMPLER}}",
        "{{CONTEXT_LENGTH}}",
        "{{AUDIO_CONTEXT_LENGTH}}",
    }
)
VIDEO_IDENTITY_PLACEHOLDERS = frozenset({"{{ASSET_ID}}", "{{SHOT_ID}}", "{{CLIENT_ID}}"})
_PLACEHOLDER_PATTERN = re.compile(r"\{\{[A-Z0-9_]+\}\}")


def workflow_placeholders(payload: Any) -> list[str]:
    """返回 workflow 值中出现的占位符，不返回原始文本。"""

    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, str):
            found.update(_PLACEHOLDER_PATTERN.findall(value))
        elif isinstance(value, Mapping):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return sorted(found)


def validate_comfyui_api_workflow(payload: Any, *, role: str | None = None) -> list[str]:
    """返回稳定、脱敏的错误代码；空列表表示 workflow 合同有效。

    ``role`` 只增加图片/视频业务输入合同，不猜测 checkpoint、采样器或
    自定义节点。未声明的占位符会被拒绝，避免 Provider 静默把模板原样
    透传给 ComfyUI。
    """

    if not isinstance(payload, dict):
        return ["workflow-must-be-object"]
    if not payload:
        return ["workflow-must-be-non-empty-object"]

    errors: list[str] = []
    node_ids = {str(node_id) for node_id in payload}
    for node_id, node in payload.items():
        if not isinstance(node_id, str) or not node_id.strip():
            errors.append("workflow-node-id-invalid")
            continue
        if not isinstance(node, Mapping):
            errors.append("workflow-node-must-be-object")
            continue
        class_type = node.get("class_type")
        if not isinstance(class_type, str) or not class_type.strip():
            errors.append("workflow-node-class-type-required")
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            errors.append("workflow-node-inputs-required")
            continue
        for value in inputs.values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                referenced_node = value[0].strip()
                if referenced_node and referenced_node not in node_ids:
                    errors.append("workflow-node-reference-missing")

    placeholders = set(workflow_placeholders(payload))
    if placeholders - SUPPORTED_PLACEHOLDERS:
        errors.append("workflow-placeholder-unsupported")
    if role not in {None, "image", "video", "long_video_first", "long_video_context"}:
        errors.append("workflow-role-invalid")
    elif role == "image" and "{{PROMPT}}" not in placeholders:
        errors.append("workflow-image-prompt-placeholder-required")
    elif role == "video" and not placeholders.intersection(VIDEO_IDENTITY_PLACEHOLDERS):
        errors.append("workflow-video-identity-placeholder-required")
    elif role == "long_video_first":
        if not placeholders.intersection(VIDEO_IDENTITY_PLACEHOLDERS):
            errors.append("workflow-long-video-identity-placeholder-required")
        if "{{SEGMENT_PROMPT}}" not in placeholders and "{{PROMPT}}" not in placeholders:
            errors.append("workflow-long-video-prompt-placeholder-required")
    elif role == "long_video_context":
        if not placeholders.intersection(VIDEO_IDENTITY_PLACEHOLDERS):
            errors.append("workflow-long-video-identity-placeholder-required")
        if "{{CONTEXT_LATENT_PATH}}" not in placeholders:
            errors.append("workflow-long-video-context-latent-placeholder-required")
        if "{{CONTEXT_CLIP_INDEX}}" not in placeholders:
            errors.append("workflow-long-video-context-index-placeholder-required")

    # Keep reports deterministic and bounded even if a malformed document repeats
    # the same problem across thousands of nodes.
    return sorted(set(errors))[:8]
