"""AI 漫剧工作台的无外部依赖领域层。

这里先承载稳定的数据合同和状态语义，Web API、队列、GPU worker
以及具体模型 provider 可以在其上逐步接入。
"""

from .models import (
    AdaptationMode,
    AdaptationUnit,
    Asset,
    AssetKind,
    AssetStatus,
    CharacterCard,
    CreditTransaction,
    Episode,
    GraphEdge,
    GraphNode,
    ImagePrompt,
    Job,
    JobKind,
    JobStatus,
    Project,
    Shot,
    SourceDocument,
    SourceSegment,
    to_primitive,
)
from .novel import AdaptationBundle, Chapter, adapt_source, load_source_document
from .workflow import CreditLedger, InvalidTransition, LedgerError, fail_job, persisted_transition_allowed, refund_failed_job, retry_job, transition_job

__all__ = [
    "AdaptationBundle",
    "AdaptationMode",
    "AdaptationUnit",
    "Asset",
    "AssetKind",
    "AssetStatus",
    "Chapter",
    "CharacterCard",
    "CreditLedger",
    "CreditTransaction",
    "Episode",
    "GraphEdge",
    "GraphNode",
    "ImagePrompt",
    "InvalidTransition",
    "Job",
    "JobKind",
    "JobStatus",
    "LedgerError",
    "Project",
    "Shot",
    "SourceDocument",
    "SourceSegment",
    "adapt_source",
    "fail_job",
    "load_source_document",
    "persisted_transition_allowed",
    "refund_failed_job",
    "retry_job",
    "to_primitive",
    "transition_job",
]
