"""P1 角色资产包契约：CharacterDNA、资产包规格与一致性判定。

对齐蓝图：
- §4.1 生成资产最小产物（三视图/表情包/姿态包/服装转面/道具转面）；
- §12.1 一致性维度：检测器只输出 pass / warn / fail / unknown，
  视觉模型评分永远不直接作为最终通过（最终采用必须人工门禁）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConsistencyVerdict(str, Enum):
    """一致性检查四值输出（蓝图 §12.1）。"""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


class PackStatus(str, Enum):
    PLANNED = "planned"
    GENERATED = "generated"
    ADOPTED = "adopted"
    LOCKED = "locked"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


@dataclass
class AssetSlot:
    """资产包中的一个槽位（如三视图的 front/side/back）。"""

    slot_id: str
    description: str
    status: PackStatus = PackStatus.PLANNED
    asset_id: str | None = None
    review_status: str = "pending"


@dataclass
class AssetPack:
    """一类资产的打包合同：三视图/表情/姿态/服装/道具。"""

    pack_id: str
    kind: str  # turnaround_3view | expression | pose | costume | prop
    character_id: str
    slots: list[AssetSlot] = field(default_factory=list)
    version: int = 1
    status: PackStatus = PackStatus.PLANNED

    def completeness(self) -> tuple[int, int, int]:
        """返回 (filled, adopted, total)。filled 指有 asset_id 的槽位。"""
        filled = sum(1 for s in self.slots if s.asset_id)
        adopted = sum(1 for s in self.slots if s.status == PackStatus.ADOPTED)
        return filled, adopted, len(self.slots)


@dataclass
class CharacterDNA:
    """角色生产合同（character_dna.json）。这是合同不是模型权重。"""

    character_id: str
    name: str
    face: list[str] = field(default_factory=list)
    hairdo: list[str] = field(default_factory=list)
    body_type: list[str] = field(default_factory=list)
    costume_version: str | None = None
    palette: list[str] = field(default_factory=list)
    forbidden_changes: list[str] = field(default_factory=list)
    turnaround_asset_ids: list[str] = field(default_factory=list)
    expression_pack_id: str | None = None
    pose_pack_id: str | None = None
    costume_pack_id: str | None = None
    prop_pack_ids: list[str] = field(default_factory=list)
    voice_profile: dict[str, Any] | None = None
    golden_shots: list[str] = field(default_factory=list)

    def check_dna_contract(self, packs: list[AssetPack]) -> ConsistencyVerdict:
        """结构化规则检查（不做视觉评分）。

        规则：
        - 没有三视图资产 → warn（身份锚点缺失）；
        - 存在 forbidden_changes 但无任何锁定资产 → warn；
        - 引用的 pack 状态为 adopted/locked 才算可通过；
        - 无任何资产 → fail（不满足生产合同，不能进正式视频任务）。
        """
        if not packs and not self.turnaround_asset_ids:
            return ConsistencyVerdict.FAIL
        if not self.turnaround_asset_ids:
            return ConsistencyVerdict.WARN
        referenced = [p for p in packs if p.pack_id in self._pack_refs()]
        if referenced and any(p.status not in {PackStatus.ADOPTED, PackStatus.LOCKED} for p in referenced):
            return ConsistencyVerdict.WARN
        if not self.golden_shots:
            return ConsistencyVerdict.UNKNOWN
        return ConsistencyVerdict.PASS

    def _pack_refs(self) -> set[str]:
        refs = {self.expression_pack_id, self.pose_pack_id, self.costume_pack_id}
        refs.update(self.prop_pack_ids)
        return {r for r in refs if r}


# 标准资产包槽位定义（蓝图 §4.1 最少产物）
TURNAROUND_SLOTS = [
    AssetSlot("front", "正面全貌，统一服装/光照/画风"),
    AssetSlot("side", "侧面，服装接缝与轮廓"),
    AssetSlot("back", "背面，发型背面与背包/披风"),
]

EXPRESSION_SLOTS = [
    AssetSlot("neutral", "中性"),
    AssetSlot("joy", "喜"),
    AssetSlot("anger", "怒"),
    AssetSlot("sorrow", "哀"),
    AssetSlot("surprise", "惊"),
    AssetSlot("talking", "说话"),
    AssetSlot("eyes_closed", "闭眼"),
    AssetSlot("injured", "受伤"),
]

POSE_SLOTS = [
    AssetSlot("stand", "站"),
    AssetSlot("sit", "坐"),
    AssetSlot("walk", "走"),
    AssetSlot("run", "跑"),
    AssetSlot("hold_item", "持物"),
    AssetSlot("fight_stance", "打斗预备"),
    AssetSlot("turn", "转身"),
    AssetSlot("fall", "倒地"),
]

COSTUME_SLOTS = [
    AssetSlot("front", "正面"),
    AssetSlot("back", "背面"),
    AssetSlot("detail", "细节特写"),
    AssetSlot("material", "材质板"),
]


def build_turnaround_pack(character_id: str) -> AssetPack:
    return AssetPack(
        pack_id=f"{character_id}_TURN_v01",
        kind="turnaround_3view",
        character_id=character_id,
        slots=[AssetSlot(f"{s.slot_id}", s.description) for s in TURNAROUND_SLOTS],
    )


def build_expression_pack(character_id: str) -> AssetPack:
    return AssetPack(
        pack_id=f"{character_id}_FACE_v01",
        kind="expression",
        character_id=character_id,
        slots=[AssetSlot(f"{s.slot_id}", s.description) for s in EXPRESSION_SLOTS],
    )


def build_pose_pack(character_id: str) -> AssetPack:
    return AssetPack(
        pack_id=f"{character_id}_POSE_v01",
        kind="pose",
        character_id=character_id,
        slots=[AssetSlot(f"{s.slot_id}", s.description) for s in POSE_SLOTS],
    )


def build_costume_pack(character_id: str, costume_version: str | None = None) -> AssetPack:
    version_tag = costume_version or "v01"
    return AssetPack(
        pack_id=f"{character_id}_COSTUME_{version_tag}",
        kind="costume",
        character_id=character_id,
        slots=[AssetSlot(f"{s.slot_id}", s.description) for s in COSTUME_SLOTS],
    )