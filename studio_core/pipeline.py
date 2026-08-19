"""P0 最小闭环组装：章节 → 分集 → 叙事单元 → 场景 → 镜头。

从 canon/episode_map.yaml + canon/SHOTLIST.yaml 组装结构化生产链，
把 narrative_units 的 shot 引用解析成 Scene/NarrativeUnit 领域对象。

设计约束：
- 只读 canon 文件，不写回；
- 缺失引用显式报错，不静默跳过（闭环验证的目的就是发现断链）；
- shotlist 中每个 shot 必须归属某个 unit，unit 必须归属某个 episode。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

from .models import NarrativeUnit, Scene


@dataclass
class PipelineGraph:
    """组装结果：分集、单元、场景、镜头及其断链。"""

    episodes: dict[str, dict] = field(default_factory=dict)
    units: dict[str, NarrativeUnit] = field(default_factory=dict)
    scenes: dict[str, Scene] = field(default_factory=dict)
    shots: dict[str, dict] = field(default_factory=dict)
    dangling_shot_refs: list[str] = field(default_factory=list)
    dangling_scene_refs: list[str] = field(default_factory=list)


def _load_yaml(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("pyyaml required")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def assemble_pipeline(
    canon_dir: str | Path,
    *,
    shotlist_path: str | Path | None = None,
) -> PipelineGraph:
    """组装 canon 下的最小生产闭环。"""
    root = Path(canon_dir)
    episode_map = _load_yaml(root / "episode_map.yaml")
    shotlist_file = Path(shotlist_path) if shotlist_path else root / "SHOTLIST.yaml"
    shotlist = _load_yaml(shotlist_file) if shotlist_file.exists() else {"shots": []}

    graph = PipelineGraph()
    shot_by_id = {s.get("id"): s for s in shotlist.get("shots", [])}
    graph.shots = shot_by_id

    project_id = shotlist.get("project_id") or episode_map.get("project") or "unknown"

    # 分集
    for ep_id, ep_data in (episode_map.get("episode_map") or {}).items():
        graph.episodes[ep_id] = ep_data

    # 叙事单元
    for raw_unit in episode_map.get("narrative_units", []):
        unit_id = raw_unit.get("unit_id")
        if not unit_id:
            continue
        unit = NarrativeUnit(
            id=unit_id,
            project_id=project_id,
            episode_id=raw_unit.get("episode_id", ""),
            goal=raw_unit.get("goal", ""),
            enter_state=raw_unit.get("enter_state", ""),
            exit_state=raw_unit.get("exit_state", ""),
            target_duration_seconds=int(raw_unit.get("target_duration_seconds", 0) or 0),
            scene_ids=list(raw_unit.get("scenes", [])),
            shot_ids=list(raw_unit.get("shots", [])),
        )
        graph.units[unit_id] = unit
        for shot_id in unit.shot_ids:
            if shot_id not in shot_by_id:
                graph.dangling_shot_refs.append(f"{unit_id} -> {shot_id}")

    # 场景：从 episode_map 的顶层 scenes 清单读取（若存在），
    # 否则从 narrative_units 内嵌 scene 列表推导。
    raw_scenes: dict[str, dict] = {}
    for item in episode_map.get("scenes", []):
        sid = item.get("scene_id")
        if sid:
            raw_scenes[sid] = item
    for raw_unit in episode_map.get("narrative_units", []):
        for scene_entry in raw_unit.get("scene_entries", []):
            sid = scene_entry.get("scene_id")
            if sid and sid not in raw_scenes:
                raw_scenes[sid] = scene_entry
    for sid, entry in raw_scenes.items():
        graph.scenes[sid] = Scene(
            id=sid,
            project_id=project_id,
            episode_id=entry.get("episode_id", ""),
            location_id=entry.get("location_id") or sid,
            name=entry.get("name", ""),
            time_of_day=entry.get("time_of_day", ""),
            weather=entry.get("weather", ""),
            summary=entry.get("summary", ""),
            shot_ids=list(entry.get("shots", [])),
        )
        for shot_id in graph.scenes[sid].shot_ids:
            if shot_id not in shot_by_id:
                graph.dangling_shot_refs.append(f"{sid} -> {shot_id}")

    # 场景引用断链：unit.scene_ids 未在 scenes 中出现
    for unit in graph.units.values():
        for scene_id in unit.scene_ids:
            if scene_id not in graph.scenes:
                graph.dangling_scene_refs.append(f"{unit.id} -> {scene_id}")

    return graph


def validate_closed_loop(graph: PipelineGraph) -> list[str]:
    """闭环验证：从当前 graph 状态实时重算断链（纯函数，不读缓存字段）。"""
    errors: list[str] = []
    owned: set[str] = set()
    for unit in graph.units.values():
        for shot_id in unit.shot_ids:
            if shot_id not in graph.shots:
                errors.append(f"dangling shot ref: {unit.id} -> {shot_id}")
            owned.add(shot_id)
        for scene_id in unit.scene_ids:
            if scene_id not in graph.scenes:
                errors.append(f"dangling scene ref: {unit.id} -> {scene_id}")
    for scene in graph.scenes.values():
        for shot_id in scene.shot_ids:
            if shot_id not in graph.shots:
                errors.append(f"dangling shot ref: {scene.id} -> {shot_id}")
    orphan_shots = [sid for sid in graph.shots if sid not in owned]
    errors.extend(f"orphan shot (no unit): {sid}" for sid in sorted(orphan_shots))
    return errors