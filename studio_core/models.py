"""AI 漫剧工作台的核心领域对象。

对象刻意保持为 dataclass：它们可以被 CLI、HTTP API、队列 worker 和未来
的数据库映射层复用，而不会把某个 ORM 或 provider 绑定进核心逻辑。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdaptationMode(str, Enum):
    FAITHFUL = "faithful"
    CONDENSED = "condensed"
    ORIGINALIZED = "originalized"


class AssetKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class AssetStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class JobKind(str, Enum):
    PROJECT = "project"
    CHARACTER = "character"
    CHARACTER_REFERENCE = "character_reference"
    OUTLINE = "outline"
    SHOTS = "shots"
    IMAGE_PROMPTS = "image_prompts"
    IMAGE = "image"
    VIDEO = "video"
    LONG_VIDEO = "long_video"
    COMPOSE = "compose"


class JobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    REVIEW = "review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SourceDocument:
    id: str
    filename: str
    text: str
    content_sha256: str
    media_type: str = "text/plain"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class SourceSegment:
    id: str
    source_document_id: str
    chapter_no: int
    sequence: int
    text: str
    start_offset: int
    end_offset: int
    line_start: int = 1
    line_end: int = 1


@dataclass
class AdaptationUnit:
    id: str
    source_segment_id: str
    chapter_no: int
    sequence: int
    source_text: str
    adapted_text: str
    mode: AdaptationMode = AdaptationMode.FAITHFUL
    status: str = "draft"
    traceability: dict[str, Any] = field(default_factory=dict)


@dataclass
class Project:
    id: str
    title: str
    story: str = ""
    style: str = "国漫写实"
    episode_length: str = "1min"
    source_document_id: str | None = None
    status: str = "draft"


@dataclass
class CharacterCard:
    id: str
    project_id: str
    name: str
    role: str
    description: str = ""
    visual_lock: dict[str, Any] = field(default_factory=dict)
    reference_asset_ids: list[str] = field(default_factory=list)


@dataclass
class Episode:
    id: str
    project_id: str
    number: int
    title: str = ""
    summary: str = ""
    conflict: str = ""
    hook: str = ""
    target_duration_seconds: int = 60


@dataclass
class Shot:
    id: str
    episode_id: str
    sequence: int
    scene: str = ""
    emotion: str = ""
    duration_seconds: float = 3.0
    description: str = ""
    adaptation_unit_ids: list[str] = field(default_factory=list)
    image_prompt_id: str | None = None


@dataclass
class ImagePrompt:
    id: str
    shot_id: str
    prompt: str
    negative_prompt: str = ""
    provider: str = ""
    model: str = ""


@dataclass
class Asset:
    id: str
    kind: AssetKind
    status: AssetStatus = AssetStatus.PENDING
    shot_id: str | None = None
    character_id: str | None = None
    url: str | None = None
    selected: bool = False
    consistency_confirmed: bool = False
    source_asset_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Job:
    id: str
    kind: JobKind
    target_id: str
    status: JobStatus = JobStatus.PENDING
    cost_credits: int = 0
    attempts: int = 0
    max_attempts: int = 3
    error: str | None = None
    provider: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class CreditTransaction:
    id: str
    user_id: str
    amount: int
    kind: str
    job_id: str | None = None
    reason: str = ""
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class GraphNode:
    id: str
    node_type: str
    entity_id: str
    label: str
    status: str = "normal"
    position: dict[str, float] = field(default_factory=dict)

@dataclass
class Scene:
    """叙事单元内的场景，连接分集镜头层级与空间锚点。

    与 canon/world.yaml 的 location 一一对应（scene_id 引用其 ID）。
    """

    id: str
    project_id: str
    episode_id: str
    unit_id: str | None = None
    location_id: str | None = None
    name: str = ""
    time_of_day: str = ""
    weather: str = ""
    summary: str = ""
    shot_ids: list[str] = field(default_factory=list)


@dataclass
class NarrativeUnit:
    """一段完整戏：目标、冲突、起止状态与前后镜头依赖。"""

    id: str
    project_id: str
    episode_id: str
    goal: str = ""
    enter_state: str = ""
    exit_state: str = ""
    target_duration_seconds: int = 0
    scene_ids: list[str] = field(default_factory=list)
    shot_ids: list[str] = field(default_factory=list)


@dataclass
class GraphEdge:
    id: str
    source: str
    target: str
    relation: str


def to_primitive(value: Any) -> Any:
    """将领域对象转换为可 JSON 化的基础类型。"""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {key: to_primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    return value
