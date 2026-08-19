"""P1 镜头候选与采用机制（蓝图 §10 不变量 1）。

一个镜头同时只有一个"当前采用的关键帧候选"和"当前采用的视频候选"。
采用、驳回、锁定都必须显式操作，并让旧候选 superseded。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CandidateStatus(str, Enum):
    PENDING = "pending"
    ADOPTED = "adopted"
    LOCKED = "locked"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class CandidateKind(str, Enum):
    KEYFRAME = "keyframe"
    VIDEO = "video"


class InvariantViolation(Exception):
    """采用不变量被破坏。"""


@dataclass
class ShotCandidate:
    candidate_id: str
    kind: CandidateKind
    status: CandidateStatus = CandidateStatus.PENDING
    asset_id: str | None = None
    generation_attempt_id: str | None = None


@dataclass
class ShotAdoption:
    """一个镜头的候选池。"""

    shot_id: str
    candidates: list[ShotCandidate] = field(default_factory=list)
    def current(self, kind: CandidateKind) -> ShotCandidate | None:
        for candidate in self.candidates:
            if candidate.kind == kind and candidate.status in {
                CandidateStatus.ADOPTED,
                CandidateStatus.LOCKED,
            }:
                return candidate
        return None

    def adopt(self, candidate_id: str) -> ShotCandidate:
        """采用一个候选：同 kind 的旧 adopted 候选 superseded。"""
        candidate = self._get(candidate_id)
        locked_sibling = next(
            (
                other
                for other in self.candidates
                if other.kind == candidate.kind
                and other.candidate_id != candidate_id
                and other.status == CandidateStatus.LOCKED
            ),
            None,
        )
        if locked_sibling is not None:
            raise InvariantViolation(
                f"locked candidate {locked_sibling.candidate_id} blocks adopting {candidate_id}"
            )
        for other in self.candidates:
            if (
                other.kind == candidate.kind
                and other.candidate_id != candidate_id
                and other.status == CandidateStatus.ADOPTED
            ):
                other.status = CandidateStatus.SUPERSEDED
        if candidate.status == CandidateStatus.LOCKED:
            raise InvariantViolation(f"locked candidate cannot be re-adopted: {candidate_id}")
        candidate.status = CandidateStatus.ADOPTED
        return candidate

    def lock(self, candidate_id: str) -> ShotCandidate:
        """锁定当前采用候选：不可被 adopt 覆盖。"""
        candidate = self._get(candidate_id)
        if candidate.status != CandidateStatus.ADOPTED:
            raise InvariantViolation(f"only adopted candidate can be locked: {candidate_id}")
        candidate.status = CandidateStatus.LOCKED
        return candidate

    def reject(self, candidate_id: str) -> ShotCandidate:
        candidate = self._get(candidate_id)
        if candidate.status == CandidateStatus.LOCKED:
            raise InvariantViolation(f"locked candidate cannot be rejected: {candidate_id}")
        candidate.status = CandidateStatus.REJECTED
        return candidate

    def _get(self, candidate_id: str) -> ShotCandidate:
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise KeyError(f"candidate not found: {candidate_id}")


def assert_adoption_invariants(adoption: ShotAdoption) -> list[str]:
    """显式检查不变量，返回违规清单（空 = 满足）。"""
    issues: list[str] = []
    for kind in (CandidateKind.KEYFRAME, CandidateKind.VIDEO):
        adopted = [
            c
            for c in adoption.candidates
            if c.kind == kind and c.status in {CandidateStatus.ADOPTED, CandidateStatus.LOCKED}
        ]
        if len(adopted) > 1:
            issues.append(f"{adoption.shot_id} has {len(adopted)} adopted {kind.value} candidates")
    return issues