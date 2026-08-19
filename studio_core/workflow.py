"""生成任务和积分账本的可持久化前状态语义。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .models import CreditTransaction, Job, JobStatus, utc_now


class InvalidTransition(ValueError):
    pass


class LedgerError(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.REVIEW, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.REVIEW: frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


def persisted_transition_allowed(from_status: str, to_status: str, error: str | None = None) -> bool:
    """数据库 Job 合同的纯函数版本，包含租约回收和预扣失败两个特例。"""
    if from_status == to_status:
        return True
    allowed = {
        "pending": {"queued", "cancelled"},
        "queued": {"running", "cancelled"},
        "running": {"review", "completed", "failed", "cancelled"},
        "review": {"completed", "failed", "cancelled"},
        "failed": {"queued", "cancelled"},
        "completed": set(),
        "cancelled": set(),
    }
    if to_status in allowed.get(from_status, set()):
        return True
    return (from_status, to_status, error) in {
        ("queued", "failed", "insufficient credits"),
        ("running", "queued", "worker lease expired; requeued"),
    }


@dataclass
class CreditReservation:
    id: str
    user_id: str
    job_id: str
    amount: int
    status: str = "reserved"


class CreditLedger:
    """内存账本原型，作为数据库 ledger 的行为合同。

    扣减发生在 reserve，失败退款只允许成功执行一次；生产实现需要把
    reservation、transaction 和余额更新放进同一数据库事务/幂等键。
    """

    def __init__(self, starting_balance: int = 0) -> None:
        if starting_balance < 0:
            raise LedgerError("starting balance cannot be negative")
        self.balance = starting_balance
        self.transactions: list[CreditTransaction] = []
        self.reservations: dict[str, CreditReservation] = {}

    def reserve(self, user_id: str, job_id: str, amount: int, reason: str = "") -> CreditReservation:
        if amount <= 0:
            raise LedgerError("reservation amount must be positive")
        if amount > self.balance:
            raise LedgerError("insufficient credits")
        reservation = CreditReservation(uuid4().hex, user_id, job_id, amount)
        self.balance -= amount
        self.reservations[reservation.id] = reservation
        self.transactions.append(
            CreditTransaction(
                id=uuid4().hex,
                user_id=user_id,
                amount=-amount,
                kind="usage_reserved",
                job_id=job_id,
                reason=reason,
            )
        )
        return reservation

    def commit(self, reservation_id: str) -> CreditReservation:
        reservation = self._get(reservation_id)
        if reservation.status == "refunded":
            raise LedgerError("cannot commit a refunded reservation")
        reservation.status = "committed"
        return reservation

    def refund(self, reservation_id: str, reason: str = "generation_failed") -> CreditReservation:
        reservation = self._get(reservation_id)
        if reservation.status == "refunded":
            # 重复请求安全返回原结果，不重复增加余额或写交易。
            return reservation
        if reservation.status != "reserved":
            raise LedgerError("only a reserved usage can be refunded")
        reservation.status = "refunded"
        self.balance += reservation.amount
        self.transactions.append(
            CreditTransaction(
                id=uuid4().hex,
                user_id=reservation.user_id,
                amount=reservation.amount,
                kind="usage_refunded",
                job_id=reservation.job_id,
                reason=reason,
            )
        )
        return reservation

    def _get(self, reservation_id: str) -> CreditReservation:
        try:
            return self.reservations[reservation_id]
        except KeyError as exc:
            raise LedgerError(f"unknown reservation: {reservation_id}") from exc


def transition_job(job: Job, new_status: JobStatus) -> Job:
    if new_status not in ALLOWED_TRANSITIONS[job.status]:
        raise InvalidTransition(f"{job.status.value} -> {new_status.value} is not allowed")
    job.status = new_status
    job.updated_at = utc_now()
    return job


def fail_job(job: Job, error: str) -> Job:
    if job.status not in {JobStatus.RUNNING, JobStatus.REVIEW}:
        raise InvalidTransition(f"cannot fail job in {job.status.value} state")
    job.error = error
    return transition_job(job, JobStatus.FAILED)


def retry_job(job: Job) -> Job:
    if job.status != JobStatus.FAILED:
        raise InvalidTransition("only failed jobs can be retried")
    if job.attempts >= job.max_attempts:
        raise InvalidTransition("job retry limit reached")
    job.attempts += 1
    job.error = None
    return transition_job(job, JobStatus.QUEUED)


def refund_failed_job(job: Job, ledger: CreditLedger, reservation_id: str, reason: str = "generation_failed") -> Job:
    if job.status != JobStatus.FAILED:
        raise InvalidTransition("only failed jobs can trigger a refund")
    ledger.refund(reservation_id, reason)
    return job
