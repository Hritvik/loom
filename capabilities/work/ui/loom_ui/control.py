"""Owner queues and per-task inspection, derived from one registry snapshot.

The control room answers one question: what needs a person, and what is merely waiting on a
machine? That distinction is not invented here. It is read from the engine's own vocabulary — a
``blocked`` task whose ``blocked_needs`` is ``"human"``, a review request addressed to an identity
in ``engine.HUMAN_IDENTITIES``, and an unanswered ``ask`` event. Everything else is a lifecycle
state, a lease, or a dependency, and each of those comes from :mod:`model`, which derives it from
:mod:`engine`.

Two rules keep this module honest:

* **The event log is read once and folded the way the engine folds it.** Review requests use
  ``engine.latest_review_request``, receipts use ``engine.integration_receipt``, and attempts use
  ``engine.attempt_views`` + ``engine.attempt_payload``. Nothing here re-decides whether a request
  is current or a receipt is complete.
* **A task appears in exactly one queue.** Queues are ordered from "a person must act" to "settled",
  and the first rule that matches owns the row. A second view of the same task would let two
  counters disagree, so there is none.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import engine
import store

from . import model, provenance
from .model import RegistryView, TaskView
from .snapshot import Snapshot

# The queue a task is assigned to, in precedence order. `settled` and `waiting` are terminal
# buckets: every task lands in exactly one, so the counts always add up to the registry total.
QUEUE_ORDER = (
    "needs-you",
    "stalled",
    "review",
    "integration",
    "acceptance",
    "in-progress",
    "claimable",
    "waiting",
    "settled",
)

QUEUE_TITLES = {
    "needs-you": "Needs you",
    "stalled": "Stalled or expired",
    "review": "Awaiting review",
    "integration": "Awaiting integration",
    "acceptance": "Ready for acceptance",
    "in-progress": "In progress",
    "claimable": "Claimable now",
    "waiting": "Not yet claimable",
    "settled": "Settled",
}

#: Every queue except this one is rendered on the control room; settled work is only counted.
DAILY_QUEUES = frozenset(queue_id for queue_id in QUEUE_ORDER if queue_id != "settled")

QUEUE_DESCRIPTIONS = {
    "needs-you": "A decision, an answer or a review that only a human can give.",
    "stalled": "Nothing will move without someone stepping in: expired leases, agent-blocked "
               "work, rejected work and reviews that cannot proceed.",
    "review": "Green work with a current review request waiting for its reviewer.",
    "integration": "Review passed; the approved commit is waiting for an integration receipt.",
    "acceptance": "Integrated work waiting for the owner to accept it.",
    "in-progress": "A live lease is held and the worker is expected to be acting.",
    "claimable": "Ready, dependencies satisfied and no conflicting lease — `loom claim` would "
                 "succeed. Ranked by how much work each task unblocks.",
    "waiting": "Ready, planned or draft work that cannot be claimed yet. The reason is beside it.",
    "settled": "Accepted, done or released. Listed in All tasks, not in the daily queues.",
}

#: A page rendered from a snapshot older than this says so instead of presenting it as current.
STALE_AFTER_SECONDS = 60
#: How many events of one task the inspector shows before naming the remainder.
EVENT_LIMIT = 50
#: Fields worth showing per event, in a stable order. Anything else stays in the JSON API.
EVENT_DETAIL_FIELDS = ("to", "question", "decision", "note", "reason", "evidence", "reviewer",
                       "in_reply_to", "needed_by", "edit", "title")
INLINE_FIELDS = {"to", "reviewer", "in_reply_to"}


class _SnapshotEvents:
    """The one method ``engine.task_history`` needs, over the snapshot's events.

    Duck-typed on purpose: the engine's own history/receipt functions are reused unchanged, so
    "which event is a receipt" has one answer in the codebase.
    """

    def __init__(self, events: tuple[dict, ...]):
        self._events = events

    def read_events(self) -> list[tuple[None, dict]]:
        return [(None, event) for event in self._events]


@dataclass(frozen=True)
class QuestionView:
    seq: int
    asked_by: str
    asked_to: str
    question: str
    needed_by: str | None
    asked_at: str | None
    revision: int | None
    status: str | None
    review_source: str | None
    review_base: str | None
    review_evidence: str | None
    answered: bool
    current: bool
    answer_seq: int | None
    answer_decision: str | None
    answer_note: str | None
    answered_by: str | None

    @property
    def state(self) -> str:
        if self.answered:
            return "answered"
        return "open" if self.current else "superseded"

    def to_dict(self) -> dict:
        return {
            "seq": self.seq, "from": self.asked_by, "to": self.asked_to,
            "question": self.question, "needed_by": self.needed_by, "asked_at": self.asked_at,
            "revision": self.revision, "status": self.status,
            "review_source": self.review_source, "review_base": self.review_base,
            "review_evidence": self.review_evidence, "answered": self.answered,
            "current": self.current, "state": self.state,
            "answer": None if not self.answered else {
                "seq": self.answer_seq, "decision": self.answer_decision,
                "note": self.answer_note, "agent": self.answered_by,
            },
        }


@dataclass(frozen=True)
class ReviewView:
    reviewer: str
    decision: str
    source: str | None
    base: str | None
    evidence: str | None
    answer_seq: int | None
    digest: str | None
    path_count: int
    severity: str | None

    @property
    def bound(self) -> bool:
        """The engine's rule: a receipt without a content digest is incomplete."""
        return bool(self.digest)

    def to_dict(self) -> dict:
        return {
            "reviewer": self.reviewer, "decision": self.decision, "source": self.source,
            "base": self.base, "evidence": self.evidence, "answer": self.answer_seq,
            "digest": self.digest, "path_count": self.path_count, "severity": self.severity,
            "bound": self.bound,
        }


@dataclass(frozen=True)
class ReceiptView:
    kind: str
    event_seq: int
    recorded_at: str | None
    source: str | None
    tip: str | None
    digest: str | None
    path_count: int | None
    reason: str | None

    @property
    def complete(self) -> bool:
        return bool(self.digest)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "event": self.event_seq, "recorded_at": self.recorded_at,
            "source": self.source, "tip": self.tip, "digest": self.digest,
            "path_count": self.path_count, "reason": self.reason, "complete": self.complete,
        }


@dataclass(frozen=True)
class AttemptView:
    attempt: str | None
    role: str
    agent: str | None
    state: str | None
    live: bool
    epoch: int | None
    revision: int | None
    pid: int | None
    worktree: str | None
    branch: str | None
    opened: str | None
    closed: str | None
    reason: str | None

    def to_dict(self) -> dict:
        return {
            "attempt": self.attempt, "role": self.role, "agent": self.agent, "state": self.state,
            "live": self.live, "epoch": self.epoch, "revision": self.revision, "pid": self.pid,
            "worktree": self.worktree, "branch": self.branch, "opened": self.opened,
            "closed": self.closed, "reason": self.reason,
        }


@dataclass(frozen=True)
class EventView:
    seq: int
    at: str | None
    event: str
    agent: str | None
    revision: int | None
    epoch: int | None
    summary: str

    def to_dict(self) -> dict:
        return {"seq": self.seq, "ts": self.at, "event": self.event, "agent": self.agent,
                "revision": self.revision, "epoch": self.epoch, "summary": self.summary}


@dataclass(frozen=True)
class EvidenceView:
    locator: str | None
    resolved: str | None
    exists: bool
    size: int | None
    modified: str | None
    error: str | None

    def to_dict(self) -> dict:
        return {"locator": self.locator, "resolved": self.resolved, "exists": self.exists,
                "size": self.size, "modified": self.modified, "error": self.error}


@dataclass(frozen=True)
class LinkView:
    """A dependency or dependent, enough to trace a blocker without a second request."""

    id: str
    title: str
    status: str
    revision: int
    claimable: bool

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "status": self.status,
                "revision": self.revision, "claimable": self.claimable}


@dataclass(frozen=True)
class Bucket:
    id: str
    reasons: tuple[str, ...]
    detail: str
    questions: tuple[QuestionView, ...] = ()
    review_request: QuestionView | None = None

    @property
    def title(self) -> str:
        return QUEUE_TITLES.get(self.id, self.id)

    def to_dict(self) -> dict:
        return {
            "queue": self.id, "title": self.title, "reasons": list(self.reasons),
            "detail": self.detail,
            "questions": [question.to_dict() for question in self.questions],
            "review_request": (self.review_request.to_dict() if self.review_request else None),
        }


@dataclass(frozen=True)
class QueueItem:
    task: TaskView
    bucket: Bucket

    def to_dict(self) -> dict:
        return {"task": self.task.id, "state": self.task.status, **self.bucket.to_dict()}


@dataclass(frozen=True)
class TaskRow:
    """One row of the task index: the task, its queue, and its last recorded event."""

    task: TaskView
    bucket: Bucket
    last_event: EventView | None

    def to_dict(self) -> dict:
        return {
            "task": self.task.id, "state": self.task.status, "revision": self.task.revision,
            "claimable": self.task.claimability.claimable, "queue": self.bucket.id,
            "reasons": list(self.bucket.reasons), "detail": self.bucket.detail,
            "last_event": self.last_event.to_dict() if self.last_event else None,
        }


@dataclass(frozen=True)
class Queue:
    id: str
    title: str
    description: str
    items: tuple[QueueItem, ...]
    #: Settled work is counted and listed in the index, not rendered as a daily queue.
    in_daily_queues: bool = True

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "description": self.description,
                "count": len(self.items), "in_daily_queues": self.in_daily_queues,
                "items": [item.to_dict() for item in self.items]}


@dataclass(frozen=True)
class ControlRoom:
    as_of: str
    program: str | None
    owner_identities: tuple[str, ...]
    queues: tuple[Queue, ...]
    rows: tuple[TaskRow, ...]
    counts: dict
    gaps: tuple[str, ...] = ()

    @property
    def settled(self) -> Queue | None:
        """The settled bucket, if this room has one; no caller re-derives it by name."""
        return next((queue for queue in self.queues if not queue.in_daily_queues), None)

    def to_dict(self, include_items: bool = True) -> dict:
        payload = {
            "as_of": self.as_of,
            "program": self.program,
            "owner_identities": list(self.owner_identities),
            "counts": dict(self.counts),
            "queues": [
                (queue.to_dict() if include_items
                 else {"id": queue.id, "title": queue.title,
                       "description": queue.description, "count": len(queue.items)})
                for queue in self.queues
            ],
            "rows": [row.to_dict() for row in self.rows],
            "read_only": True,
        }
        if self.gaps:
            payload["gaps"] = list(self.gaps)
        return payload


@dataclass(frozen=True)
class TaskDetail:
    task: TaskView
    bucket: Bucket
    questions: tuple[QuestionView, ...]
    review_request: QuestionView | None
    review: ReviewView | None
    receipt: ReceiptView | None
    attempts: tuple[AttemptView, ...]
    events: tuple[EventView, ...]
    events_total: int
    evidence: EvidenceView
    upstream: tuple[LinkView, ...]
    downstream: tuple[LinkView, ...]
    as_of: str

    def to_dict(self) -> dict:
        return {
            "task": self.task.id,
            "as_of": self.as_of,
            "bucket": self.bucket.to_dict(),
            "review": self.review.to_dict() if self.review else None,
            "receipt": self.receipt.to_dict() if self.receipt else None,
            "questions": [question.to_dict() for question in self.questions],
            "review_request": (self.review_request.to_dict() if self.review_request else None),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "events": [event.to_dict() for event in self.events],
            "events_total": self.events_total,
            "evidence": self.evidence.to_dict(),
            "upstream": [link.to_dict() for link in self.upstream],
            "downstream": [link.to_dict() for link in self.downstream],
        }


# -- event reading ---------------------------------------------------------------------------

def _history_by_task(events: tuple[dict, ...]) -> dict[str, list[dict]]:
    history: dict[str, list[dict]] = {}
    for event in events:
        task_id = event.get("task")
        if task_id:
            history.setdefault(str(task_id), []).append(event)
    return history


def _answer_index(history: list[dict]) -> dict[int, dict]:
    answers: dict[int, dict] = {}
    for event in history:
        if event.get("event") != "answer" or event.get("in_reply_to") is None:
            continue
        answers[int(event["in_reply_to"])] = event
    return answers


def _last(history: list[dict], predicate) -> dict:
    return next((event for event in reversed(history) if predicate(event)), {})


def questions_for(record: dict, history: list[dict]) -> tuple[QuestionView, ...]:
    """Every question asked about the task, with the engine's own idea of "current"."""
    answers = _answer_index(history)
    current = engine.latest_review_request(record, history)
    current_seq = current.get("seq") if current else None
    views: list[QuestionView] = []
    for event in history:
        if event.get("event") != "ask":
            continue
        seq = int(event.get("seq") or 0)
        answer = answers.get(seq) or {}
        is_review = bool(event.get("review_source"))
        # A review request is current only if it is the one the engine would act on; an ordinary
        # question is current until answered.
        still_current = (seq == current_seq) if is_review else not answer
        views.append(QuestionView(
            seq=seq,
            asked_by=str(event.get("agent") or ""),
            asked_to=str(event.get("to") or ""),
            question=str(event.get("question") or ""),
            needed_by=event.get("needed_by"),
            asked_at=event.get("ts"),
            revision=event.get("revision"),
            status=event.get("status"),
            review_source=event.get("review_source"),
            review_base=event.get("review_base"),
            review_evidence=event.get("review_evidence"),
            answered=bool(answer),
            current=still_current,
            answer_seq=int(answer["seq"]) if answer.get("seq") is not None else None,
            answer_decision=answer.get("decision"),
            answer_note=answer.get("note"),
            answered_by=answer.get("agent"),
        ))
    return tuple(views)


def review_view(record: dict) -> ReviewView | None:
    review = record.get("review") or {}
    if not review:
        return None
    return ReviewView(
        reviewer=str(review.get("reviewer") or ""),
        decision=str(review.get("decision") or ""),
        source=review.get("source"),
        base=review.get("base"),
        evidence=review.get("evidence"),
        answer_seq=review.get("answer"),
        digest=review.get("digest"),
        path_count=len(review.get("paths") or []),
        severity=review.get("severity"),
    )


def receipt_view(history: list[dict]) -> ReceiptView | None:
    receipt = engine.integration_receipt(history)
    if not receipt:
        return None
    count = receipt.get("approved_path_count")
    if count is None:
        paths = receipt.get("approved_paths") or []
        count = len(paths) if paths else None
    return ReceiptView(
        kind=str(receipt.get("event") or ""),
        event_seq=int(receipt.get("seq") or 0),
        recorded_at=receipt.get("ts"),
        source=receipt.get("integrated_source"),
        tip=receipt.get("integration_tip"),
        digest=receipt.get("approved_digest"),
        path_count=count,
        reason=receipt.get("reason"),
    )


def attempts_for(snapshot: Snapshot, task_id: str) -> tuple[AttemptView, ...]:
    views = engine.attempt_views(list(snapshot.events))
    items = [item for item in views.values() if item.get("task") == task_id]
    items.sort(key=lambda item: (engine.attempt_number(item.get("attempt")),
                                 item.get("opened") or 0))
    attempts: list[AttemptView] = []
    for item in items:
        payload = engine.attempt_payload(item)
        attempts.append(AttemptView(
            attempt=payload.get("attempt"),
            role=str(payload.get("role") or "implementer"),
            agent=payload.get("agent"),
            state=payload.get("state"),
            live=bool(payload.get("live")),
            epoch=payload.get("epoch"),
            revision=payload.get("revision"),
            pid=payload.get("pid"),
            worktree=payload.get("worktree"),
            branch=payload.get("branch"),
            opened=payload.get("opened"),
            closed=payload.get("closed"),
            reason=payload.get("reason"),
        ))
    return tuple(attempts)


def _summarise(event: dict) -> str:
    parts: list[str] = []
    if event.get("previous_status") and event.get("status"):
        parts.append(f"{event['previous_status']} -> {event['status']}")
    elif event.get("status"):
        parts.append(str(event["status"]))
    for key in EVENT_DETAIL_FIELDS:
        value = event.get(key)
        if value in (None, ""):
            continue
        if key in INLINE_FIELDS:
            parts.append(f"{key} {value}")
        else:
            parts.append(f"{key}: {value}")
    return "; ".join(parts)


def event_views(history: list[dict], limit: int = EVENT_LIMIT) -> tuple[EventView, ...]:
    shown = history[-limit:] if limit else history
    return tuple(EventView(
        seq=int(event.get("seq") or 0),
        at=event.get("ts"),
        event=str(event.get("event") or ""),
        agent=event.get("agent"),
        revision=event.get("revision"),
        epoch=event.get("epoch"),
        summary=_summarise(event),
    ) for event in shown)


def evidence_view(record: dict) -> EvidenceView:
    locator = record.get("evidence")
    if not locator:
        return EvidenceView(locator=None, resolved=None, exists=False, size=None,
                            modified=None, error="no evidence locator recorded")
    location, error = provenance.evidence_location(record)
    if location is None:
        return EvidenceView(locator=str(locator), resolved=None, exists=False, size=None,
                            modified=None, error=error)
    if not location.is_file():
        return EvidenceView(locator=str(locator), resolved=str(location), exists=False,
                            size=None, modified=None, error="file not found")
    stat = location.stat()
    return EvidenceView(
        locator=str(locator),
        resolved=str(location),
        exists=True,
        size=stat.st_size,
        modified=store.iso(datetime.fromtimestamp(int(stat.st_mtime), timezone.utc)),
        error=None,
    )


# -- classification --------------------------------------------------------------------------

def _owner_blocked_reason(history: list[dict]) -> str:
    event = _last(history, lambda item: item.get("event") == "transition"
                  and item.get("status") == "blocked")
    return str(event.get("reason") or "no reason recorded")


def classify(task: TaskView, record: dict, history: list[dict], owners: frozenset[str],
             by_id: dict[str, dict] | None = None) -> Bucket:
    """Assign one task to one queue, with the reasons a person needs to see."""
    questions = questions_for(record, history)
    answers = _answer_index(history)
    current = engine.latest_review_request(record, history)
    current_seq = int(current.get("seq") or 0) if current else 0
    current_open = bool(current) and current_seq not in answers
    human_review = bool(current_open and str(current.get("to") or "") in owners)
    owner_questions = tuple(question for question in questions
                            if not question.answered and not question.review_source
                            and question.asked_to in owners)
    human_blocked = (task.status == "blocked"
                     and str(record.get("blocked_needs") or "agent") == "human")

    reasons: list[str] = []
    details: list[str] = []
    if human_blocked:
        reasons.append("blocked, needs a human")
        details.append(_owner_blocked_reason(history))
    if human_review:
        reasons.append("review addressed to you")
        details.append(f"event {current_seq}: {current.get('question') or 'review request'} "
                       f"from {current.get('agent') or 'unknown'}")
    if owner_questions:
        reasons.append("question for you")
        details.extend(f"event {question.seq}: {question.question}" for question in owner_questions)
    if reasons:
        return Bucket("needs-you", tuple(reasons), " · ".join(details),
                      questions=owner_questions,
                      review_request=next((question for question in questions
                                           if question.seq == current_seq), None))

    claim = task.claim
    # A lapsed lease blocks a claim wherever the task still needs one. Green work is different:
    # the review request is bound to a commit, not to the lease, so it stands on its own.
    if claim and claim.expired and task.status in {"ready", "claimed", "red", "in-progress"}:
        return Bucket(
            "stalled", ("lease expired",),
            f"{claim.agent or 'a worker'} held epoch {claim.epoch} until "
            f"{claim.lease_expires}; a claim is refused and takeover is the only way to continue")

    if task.status == "blocked":
        return Bucket("stalled", ("blocked, needs an agent",),
                      f"{record.get('blocked_needs') or 'agent'}: "
                      f"{_owner_blocked_reason(history)}")

    if task.status == "rejected":
        return Bucket("stalled", ("rejected",),
                      f"rejected at revision {task.revision}; decide whether to reopen it")

    if task.status == "green":
        latest_request = _last(history, lambda item: item.get("event") == "ask"
                               and item.get("review_source"))
        answer = answers.get(int(latest_request.get("seq") or 0), {})
        if latest_request and answer.get("decision") == "changes requested":
            return Bucket("stalled", ("changes requested",),
                          f"reviewer {answer.get('agent')} requested changes "
                          f"(event {answer.get('seq')}); rework has not started")
        if not latest_request:
            return Bucket("stalled", ("review never requested",),
                          "green work with no review request; submit the branch tip or reopen it")
        if not current:
            return Bucket(
                "stalled", ("review request is stale",),
                f"the latest request (event {latest_request.get('seq')}) was for revision "
                f"{latest_request.get('revision')}; the task is at revision {task.revision}")
        if answer:
            reasons = ("approved by reviewer",)
            detail = (f"approved by {answer.get('agent')} at event {answer.get('seq')}; the "
                      "reviewed transition has not been recorded")
        else:
            reasons = ("awaiting reviewer",)
            detail = (f"requested from {current.get('to')} at event {current_seq}, "
                      f"source {(current.get('review_source') or '')[:12]}")
        if claim and claim.expired:
            reasons += ("expired lease",)
            detail += "; the previous lease expired, but the review request still stands"
        return Bucket("review", reasons, detail)

    review = review_view(record)
    if task.status == "reviewed":
        if review and review.bound:
            detail = (f"approved by {review.reviewer} at {str(review.source or '')[:12]}; "
                      f"content {str(review.digest or '')[:12]} over {review.path_count} path(s)")
        elif review:
            detail = (f"approved by {review.reviewer} but the receipt records no content digest; "
                      "integration cannot verify it")
        else:
            detail = "no review verdict recorded on the task"
        return Bucket("integration", ("reviewed",), detail)

    if task.status == "integrated":
        receipt = receipt_view(history)
        if receipt and receipt.complete:
            detail = (f"integrated {str(receipt.source or '')[:12]} at event {receipt.event_seq}; "
                      f"content {str(receipt.digest or '')[:12]}")
        elif receipt:
            detail = (f"integrated {str(receipt.source or '')[:12]} at event {receipt.event_seq}, "
                      "but the receipt records no content digest")
        else:
            detail = "integrated with no receipt in the event log"
        return Bucket("acceptance", ("integrated",), detail)

    if task.status in {"claimed", "red", "in-progress"}:
        if claim and claim.active:
            detail = (f"{claim.agent} epoch {claim.epoch}, "
                      f"{claim.remaining_seconds}s left")
        elif claim:
            detail = f"{claim.agent} holds an expired lease"
        else:
            detail = "no claim recorded"
        return Bucket("in-progress", (task.status,), detail)

    if task.claimability.claimable:
        return Bucket("claimable", ("claimable",),
                      f"unblocks {len(task.downstream)} task(s)")

    if task.status in {"accepted", "done", "released"}:
        return Bucket("settled", (task.status,), "no action")

    # ready / planned / draft that cannot be claimed: name the blocker, and say when the blocker
    # is itself a decision (a planned or draft prerequisite) rather than a grant.
    blockers = [reason for reason in task.claimability.reasons]
    by_id = by_id or {}
    for dependency in task.claimability.unmet_dependencies:
        dep_id = dependency.split()[0].rstrip(",")
        record_dep = by_id.get(dep_id, {})
        if record_dep.get("status") in {"planned", "draft"}:
            blockers.append(f"decision: promote or plan {dep_id}")
    return Bucket("waiting", (task.status,), "; ".join(blockers) or "not claimable")


# -- control room ----------------------------------------------------------------------------

def _links(view: RegistryView, ids: tuple[str, ...]) -> tuple[LinkView, ...]:
    by_id = {task.id: task for task in view.tasks}
    links: list[LinkView] = []
    for task_id in ids:
        task = by_id.get(task_id)
        if task is None:
            links.append(LinkView(id=task_id, title="(not in the registry)", status="absent",
                                  revision=0, claimable=False))
            continue
        links.append(LinkView(id=task.id, title=task.title, status=task.status,
                              revision=task.revision, claimable=task.claimability.claimable))
    return tuple(links)


def _task_records(snapshot: Snapshot) -> dict[str, dict]:
    return {task["id"]: task for task in snapshot.registry.get("tasks", [])}


def build(snapshot: Snapshot, view: RegistryView, owners: frozenset[str] | None = None,
          program: str | None = None) -> ControlRoom:
    owners = owners or frozenset(engine.HUMAN_IDENTITIES)
    history = _history_by_task(snapshot.events)
    records = _task_records(snapshot)
    gaps: list[str] = []

    queues: dict[str, list[QueueItem]] = {queue_id: [] for queue_id in QUEUE_ORDER}
    rows: list[TaskRow] = []
    for task in view.tasks:
        if program and task.program != program:
            continue
        record = records.get(task.id)
        if record is None:
            gaps.append(f"{task.id}: the read model names a task the registry does not contain")
            continue
        task_history = history.get(task.id, [])
        bucket = classify(task, record, task_history, owners, records)
        queues[bucket.id].append(QueueItem(task=task, bucket=bucket))
        last = event_views(task_history, limit=1)
        rows.append(TaskRow(task=task, bucket=bucket, last_event=last[0] if last else None))

    rows.sort(key=lambda row: (row.task.program, row.task.id))
    built = tuple(Queue(
        id=queue_id,
        title=QUEUE_TITLES[queue_id],
        description=QUEUE_DESCRIPTIONS[queue_id],
        items=tuple(queues[queue_id]),
        in_daily_queues=queue_id in DAILY_QUEUES,
    ) for queue_id in QUEUE_ORDER)

    counts = {queue.id: len(queue.items) for queue in built}
    counts["total"] = len(rows)
    # A task can be in no queue only if the loop skipped it; that is a gap, not a settled task.
    counts["queued"] = sum(counts[queue_id] for queue_id in QUEUE_ORDER)
    if counts["queued"] != counts["total"]:
        gaps.append(f"{counts['total'] - counts['queued']} task(s) could not be classified")
    return ControlRoom(
        as_of=snapshot.as_of,
        program=program,
        owner_identities=tuple(sorted(owners)),
        queues=built,
        rows=tuple(rows),
        counts=counts,
        gaps=tuple(gaps),
    )


def detail(snapshot: Snapshot, view: RegistryView, task_id: str,
           owners: frozenset[str] | None = None) -> TaskDetail | None:
    task = model.find(view, task_id)
    if task is None:
        return None
    owners = owners or frozenset(engine.HUMAN_IDENTITIES)
    history_all = _history_by_task(snapshot.events)
    task_history = history_all.get(task.id, [])
    records = _task_records(snapshot)
    record = records.get(task.id, {"id": task.id, "revision": task.revision,
                                   "status": task.status})
    bucket = classify(task, record, task_history, owners, records)
    questions = questions_for(record, task_history)
    current = engine.latest_review_request(record, task_history)
    current_seq = int(current.get("seq") or 0) if current else 0
    review_request = next((question for question in questions if question.seq == current_seq),
                          None)
    receipt = receipt_view(task_history)
    return TaskDetail(
        task=task,
        bucket=bucket,
        questions=questions,
        review_request=review_request,
        review=review_view(record),
        receipt=receipt,
        attempts=attempts_for(snapshot, task.id),
        events=event_views(task_history),
        events_total=len(task_history),
        evidence=evidence_view(record),
        upstream=_links(view, task.depends_on),
        downstream=_links(view, task.downstream),
        as_of=snapshot.as_of,
    )


def programs(view: RegistryView) -> tuple[str, ...]:
    return tuple(sorted({task.program for task in view.tasks if task.program}))


def statuses(view: RegistryView) -> tuple[str, ...]:
    return tuple(sorted({task.status for task in view.tasks}))


__all__ = [
    "QUEUE_ORDER", "QUEUE_TITLES", "QUEUE_DESCRIPTIONS", "STALE_AFTER_SECONDS",
    "QuestionView", "ReviewView", "ReceiptView", "AttemptView", "EventView", "EvidenceView",
    "LinkView", "Bucket", "QueueItem", "TaskRow", "Queue", "ControlRoom", "TaskDetail",
    "build", "detail", "classify", "questions_for", "evidence_view", "event_views",
    "attempts_for", "receipt_view", "review_view", "programs", "statuses",
]
