"""Canon 影响分析引擎（P0）。

只读模块：加载 canon/ 目录与镜头清单，构建引用索引，回答一个问题——
"修改 X 后，哪些下游资产失效、哪些任务需要重跑？"

不改动任何数据库表；它是 P0 控制平面的确定性计算层，
后续 Scene/NarrativeUnit 持久化后由同一函数族复用。

输出约定（对齐 AI_FILM_FACTORY_BLUEPRINT §11.1）：
    affected: 受影响资产/镜头/任务，按类别分组；
    stale:    应标记 stale 的实体 ID；
    rerun:    需要重跑/重生成的任务建议。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 模块级延迟导入，避免 yaml 不可用时中断模块加载（文档层仍可被 import）。
try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


@dataclass(frozen=True)
class AffectedGroup:
    """一类直接影响资产/镜头/任务的一组 ID。"""

    category: str  # asset: 资产; shot: 镜头; video: 视频段; audio: 音频; rerun: 待重跑
    item_ids: tuple[str, ...]


@dataclass
class CanonChange:
    """外部结构：一次角色/世界/风格/映射变更。"""

    entity_id: str
    fields: tuple[str, ...] = ()
    description: str = ""


@dataclass
class ImpactReport:
    """影响分析输出。"""

    changed_entity: CanonChange
    affected_groups: list[AffectedGroup] = field(default_factory=list)
    stale_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "changed_entity": {
                "entity_id": self.changed_entity.entity_id,
                "fields": list(self.changed_entity.fields),
                "description": self.changed_entity.description,
            },
            "affected": [
                {"category": g.category, "item_ids": list(g.item_ids)}
                for g in self.affected_groups
            ],
            "stale_ids": self.stale_ids,
        }


# ---------------------------------------------------------------------------
# 资产 ID 解析
# ---------------------------------------------------------------------------

_ENTITY_PREFIX_RE = re.compile(
    r"^(?P<base>(?:CHAR|SCENE|PROP|SHOT|EP|NU)_[0-9A-Za-z]+)"
    r"(?:_[A-Za-z]+)?(?:[_-]v\d+)?$"
)


def _entity_family(asset_id: str) -> str:
    """资产 ID → 家族前缀：CHAR_001_FACE_v02 → CHAR_001。

    规则：取前两段下划线组合。无法解析时按原 ID 前缀匹配。
    """
    parts = asset_id.split("_")
    if len(parts) >= 2 and parts[0] in {"CHAR", "SCENE", "PROP", "SHOT", "EP", "NU"}:
        return f"{parts[0]}_{parts[1]}"
    return asset_id


def _family_matches(target_family: str, reference: str) -> bool:
    """引用是否命中目标家族：CHAR_001 命中 CHAR_001 及 CHAR_001_FACE_v02。"""
    if target_family == reference:
        return True
    return reference.startswith(target_family + "_")


def parse_asset_reference(raw: str) -> str:
    """资产引用规整：去掉渲染 job、manifest UUID 与环境符号。"""
    return raw.strip()


# ---------------------------------------------------------------------------
# Canon 加载
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("pyyaml required to load canon YAML")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"canon YAML root must be a mapping: {path}")
    return data


def load_characters(canon_dir: Path) -> dict:
    return _load_yaml(canon_dir / "characters.yaml")


def load_world(canon_dir: Path) -> dict:
    return _load_yaml(canon_dir / "world.yaml")


def load_shotlist(shotlist_path: Path) -> dict:
    return _load_yaml(shotlist_path)


def _shots_from_shotlist(shotlist: dict) -> list[dict]:
    shots = shotlist.get("shots", [])
    if not isinstance(shots, list):
        raise ValueError("shotlist 'shots' must be a list")
    return shots


# ---------------------------------------------------------------------------
# 引用索引
# ---------------------------------------------------------------------------

@dataclass
class ReferenceIndex:
    """ID → 引用它的实体集合。

    shots: 分镜清单里的 shot id → 引用该 ID 的 shot
    assets: 每个资产 ID 在 shots、canon 中被引用的位置
    """

    shot_refs: dict[str, set[str]] = field(default_factory=dict)
    asset_refs: dict[str, set[str]] = field(default_factory=dict)
    entity_assets: dict[str, set[str]] = field(default_factory=dict)


def build_reference_index(
    characters: dict,
    world: dict,
    episode_map: dict,
    shotlist: dict,
) -> ReferenceIndex:
    """从 canon 与 shotlist 构建引用关系。

    - shotlist.shots[].id 引用自身的 assets/reference_asset_ids 列表；
    - characters[].costume_versions[].id 视为 CHAR 家族资产；
    - episode_map 的 shot id 与 shotlist 联通。
    """
    index = ReferenceIndex()

    # 角色家族 ⇒ 资产（三视图、表情包等可能出现于未来 manifest；先注册现有服装/面部）
    for char in characters.get("characters", []):
        char_id = char.get("id", "")
        asset_ids: list[str] = []
        for costume in char.get("costume_versions", []):
            cid = costume.get("id")
            if cid:
                asset_ids.append(cid)
        face_ref = char.get("core_identity", {}).get("default_face_ref")
        if face_ref:
            asset_ids.append(face_ref)
        index.entity_assets.setdefault(_entity_family(char_id), set()).update(asset_ids)

    # 场景 ⇒ 锚点与道具
    for loc in world.get("locations", []):
        loc_id = loc.get("id", "")
        asset_ids = [a.get("id") for a in loc.get("anchors", []) if a.get("id")]
        asset_ids += [p.get("id") for p in loc.get("props", []) if p.get("id")]
        index.entity_assets.setdefault(_entity_family(loc_id), set()).update(asset_ids)

    # 镜头清单
    shots = _shots_from_shotlist(shotlist)
    for shot in shots:
        shot_id = shot.get("id", "")
        for raw in list(shot.get("assets", [])) + list(shot.get("reference_asset_ids", [])):
            ref = parse_asset_reference(str(raw))
            index.asset_refs.setdefault(ref, set()).add(shot_id)
            # 资产家族反向映射到实体
            family = _entity_family(ref)
            index.entity_assets.setdefault(family, set()).add(ref)
    return index


# ---------------------------------------------------------------------------
# 影响传播
# ---------------------------------------------------------------------------

def _affected_shots(
    index: ReferenceIndex,
    changed_id: str,
    shots: list[dict],
) -> set[str]:
    """实体 ID/家族直接或间接引用到的 shot 集合。

    场景变更走结构化归属传播：shot.scene_id == SCENE_012 全部命中，
    不依赖资产引用链（CU 特写可能不引用场景锚点，但仍在场景内拍摄）。
    """
    affected: set[str] = set()
    if changed_id.startswith("SCENE_"):
        for shot in shots:
            if shot.get("scene_id") == changed_id:
                shot_id = shot.get("id")
                if shot_id:
                    affected.add(shot_id)
        return affected

    stack = [changed_id]
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        # 资产引用 → shot
        if current in index.asset_refs:
            affected.update(index.asset_refs[current])
        # canonical 家族归属扩展：CHAR_001 ⇒ CHAR_001_FACE_v02 等
        for ref in list(index.asset_refs.keys()):
            if _family_matches(current, ref) and ref != current:
                stack.append(ref)
    return affected


def analyze_impact(
    canon_dir: str | Path,
    change: CanonChange,
    *,
    shotlist_path: str | Path | None = None,
) -> ImpactReport:
    """对 canon 变更执行影响分析。

    Args:
        canon_dir: canon/ 目录（含 characters.yaml/world.yaml/style.yaml/episode_map.yaml）
        change: CanonChange（entity_id 可为 CHAR_001、SCENE_012、WORLD_RULE_001 等）
        shotlist_path: SHOTLIST yaml 路径；缺省时回退 canon_dir 同级 SHOTLIST.yaml
    """
    root = Path(canon_dir)
    characters = load_characters(root)
    world = load_world(root)
    episode_path = root / "episode_map.yaml"
    episode_map = _load_yaml(episode_path) if episode_path.exists() else {}
    shotlist_file = Path(shotlist_path) if shotlist_path else root / "SHOTLIST.yaml"
    shotlist = _load_yaml(shotlist_file) if shotlist_file.exists() else {"shots": []}

    index = build_reference_index(characters, world, episode_map, shotlist)
    report = ImpactReport(changed_entity=change)

    shots_hit = _affected_shots(index, change.entity_id, _shots_from_shotlist(shotlist))
    if shots_hit:
        report.affected_groups.append(
            AffectedGroup(category="shot", item_ids=tuple(sorted(shots_hit)))
        )
        report.stale_ids.extend(sorted(shots_hit))

    # 通过 episode_map 传播到分集/叙事单元
    unit_hits: set[str] = set()
    ep_hits: set[str] = set()
    shot_family = set()
    for shot_id in shots_hit:
        shot_family.add(_entity_family(shot_id))
    for unit in episode_map.get("narrative_units", []):
        unit_shots = set(unit.get("shots", []))
        if unit_shots & shots_hit:
            unit_hits.add(unit.get("unit_id", ""))
            ep_hits.add(unit.get("episode_id", ""))
    if unit_hits:
        report.affected_groups.append(
            AffectedGroup(category="episode", item_ids=tuple(sorted(ep_hits)))
        )
        report.stale_ids.extend(sorted(unit_hits))

    # 家族资产：变更实体自己的资产
    own_assets = sorted(index.entity_assets.get(_entity_family(change.entity_id), set()))
    if own_assets:
        report.affected_groups.append(
            AffectedGroup(category="asset", item_ids=tuple(own_assets))
        )
        report.stale_ids.extend(own_assets)

    # 风格/世界规则变更 ⇒ 全 shot stale（保守）
    if change.entity_id.startswith(("STYLE", "WORLD_RULE")):
        extra = sorted({s.get("id", "") for s in _shots_from_shotlist(shotlist)} - shots_hit)
        if extra:
            report.affected_groups.append(
                AffectedGroup(category="style_or_rule", item_ids=tuple(extra))
            )
            report.stale_ids.extend(extra)

    # 重跑任务建议（有受影响 shot 就有 rerun）
    if shots_hit or unit_hits:
        report.affected_groups.append(
            AffectedGroup(category="rerun", item_ids=tuple(sorted(shots_hit)))
        )

    return report


# ---------------------------------------------------------------------------
# CLI / 自检
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Canon 影响分析")
    parser.add_argument("--canon-dir", required=True, help="canon/ 目录")
    parser.add_argument("--change", required=True, help="发生变更的实体 ID，如 CHAR_001")
    parser.add_argument("--fields", default="", help="逗号分隔的变更字段")
    parser.add_argument("--shotlist", default=None, help="SHOTLIST yaml 路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    change = CanonChange(
        entity_id=args.change,
        fields=tuple(f for f in args.fields.split(",") if f),
        description="",
    )
    report = analyze_impact(args.canon_dir, change, shotlist_path=args.shotlist)
    if args.json:
        import json

        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"changed: {change.entity_id}")
        for group in report.affected_groups:
            print(f"  {group.category}: {', '.join(group.item_ids)}")


if __name__ == "__main__":
    _cli()