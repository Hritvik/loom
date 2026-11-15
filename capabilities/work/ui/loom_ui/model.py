"""Typed read models derived through the engine's own rules.

Nothing here re-implements a Loom decision. Claimability is exactly what ``loom claim`` would
decide: the task is in ``engine.CLAIMABLE_FROM``, ``engine.dependencies_met`` is satisfied, no other
live claim overlaps a write path, and no live lease is held. Dependency satisfaction, downstream
counts and path overlap come from ``engine`` as well. If the engine's rules change, the UI follows
them; if they diverge, the parity tests fail rather than the page lying.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import engine
import store

from .snapshot import Snapshot


@dataclass(frozen=True)
class ClaimView:
    agent: str
    epoch: int | None
    base_revision: int | None
    heartbeat: str | None
    lease_expires: str | None
    active: bool
    expired: bool
    remaining_seconds: int | None


@dataclass(frozen=True)
class BlockerView:
    kind: str
    detail: str
    reference: str | None = None


@dataclass(frozen=True)
class ClaimabilityView:
    claimable: bool
    state_ok: bool
    dependencies_satisfied: bool
    unmet_dependencies: tuple[str, ...] = ()
    path_conflicts: tuple[str, ...] = ()
    lease_held: bool = False
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskView:
    id: str
    title: str
    goal: str
    status: str
    revision: int
    program: str
    board: str
    owner_repo: str | None
    write_paths: tuple[str, ...]
    depends_on: tuple[str, ...]
    downstream: tuple[str, ...]
    acceptance: tuple[str, ...]
    evidence: str | None
    created_at: str | None
    updated_at: str | None
    epoch: int
    blocked_needs: str | None
    claim: ClaimView | None
    claimability: ClaimabilityView
    blockers: tuple[BlockerView, ...]


@dataclass(frozen=True)
class BoardView:
    id: str
    repo: str | None
    path: str | None
    kind: str | None
    role: str | None
    tasks: int


@dataclass(frozen=True)
class RegistryView:
    as_of: str
    digest: str
    db: str
    head_seq: int
    head_hash: str
    counts: dict
    statuses: tuple[str, ...]
    boards: tuple[BoardView, ...]
    tasks: tuple[TaskView, ...]

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "digest": self.digest,
            "db": self.db,
            "head": {"seq": self.head_seq, "hash": self.head_hash},
            "counts": dict(self.counts),
            "statuses": list(self.statuses),
            "boards": [asdict(board) for board in self.boards],
            "tasks": [_task_dict(task) for task in self.tasks],
        }


def _task_dict(task: TaskView) -> dict:
    payload = asdict(task)
    for key in ("write_paths", "depends_on", "downstream", "acceptance", "blockers"):
        payload[key] = list(payload[key])
    payload["claimability"] = asdict(task.claimability)
    payload["claimability"]["unmet_dependencies"] = list(task.claimability.unmet_dependencies)
    payload["claimability"]["path_conflicts"] = list(task.claimability.path_conflicts)
    payload["claimability"]["reasons"] = list(task.claimability.reasons)
    return payload


def _now(snapshot: Snapshot) -> datetime:
    return store.parse_time(snapshot.as_of)


def claim_view(task: dict, now: datetime) -> ClaimView | None:
    claim = task.get("claim") or {}
    if not claim:
        return None
    expiry = engine.lease_expiry(task)
    remaining = int((expiry - now).total_seconds()) if expiry else None
    return ClaimView(
        agent=str(claim.get("agent") or ""),
        epoch=claim.get("epoch"),
        base_revision=claim.get("base_revision"),
        heartbeat=claim.get("heartbeat"),
        lease_expires=claim.get("lease_expires"),
        active=engine.active_claim(task, now),
        expired=bool(expiry and expiry <= now),
        remaining_seconds=remaining,
    )


def claimability(registry: dict, task: dict, now: datetime) -> ClaimabilityView:
    reasons: list[str] = []
    blockers: list[BlockerView] = []

    state_ok = task.get("status") in engine.CLAIMABLE_FROM
    if not state_ok:
        reasons.append(
            f"state is {task.get('status')!r}; only {sorted(engine.CLAIMABLE_FROM)} can be claimed")

    satisfied, unmet = engine.dependencies_met(registry, task)
    if not satisfied:
        reasons.append("unmet dependencies: " + ", ".join(unmet))
        for item in unmet:
            blockers.append(BlockerView(kind="dependency", detail=item, reference=item.split()[0]))

    conflicts = engine.write_path_conflicts(registry, task, now)
    if conflicts:
        reasons.append("write-path conflict: " + "; ".join(conflicts))

    lease_held = engine.active_claim(task, now)
    if lease_held:
        claim = task.get("claim") or {}
        reasons.append(
            f"live lease held by {claim.get('agent')} until {claim.get('lease_expires')}")
    elif task.get("claim"):
        expiry = engine.lease_expiry(task)
        if expiry and expiry <= now:
            reasons.append(
                f"expired lease held by {(task.get('claim') or {}).get('agent')}; "
                "takeover is required after the lock delay")

    return ClaimabilityView(
        claimable=not reasons,
        state_ok=state_ok,
        dependencies_satisfied=satisfied,
        unmet_dependencies=tuple(unmet),
        path_conflicts=tuple(conflicts),
        lease_held=lease_held,
        reasons=tuple(reasons),
    )


def build(snapshot: Snapshot) -> RegistryView:
    now = _now(snapshot)
    registry = snapshot.registry
    tasks = registry.get("tasks", [])
    index = engine.dependents_index(registry)
    downstream = {task["id"]: tuple(engine.transitive_dependents(index, task["id"]))
                  for task in tasks}

    views: list[TaskView] = []
    for task in tasks:
        eligibility = claimability(registry, task, now)
        blockers = [BlockerView(kind="lease", detail=reason)
                    for reason in eligibility.reasons if reason.startswith("live lease")]
        blockers.extend(BlockerView(kind="path-conflict", detail=item)
                        for item in eligibility.path_conflicts)
        blockers.extend(BlockerView(kind="dependency", detail=item,
                                    reference=item.split()[0])
                        for item in eligibility.unmet_dependencies)
        if not eligibility.state_ok:
            blockers.append(BlockerView(
                kind="state",
                detail=f"status {task.get('status')!r} is not claimable"))
        views.append(TaskView(
            id=task["id"],
            title=str(task.get("title") or ""),
            goal=str(task.get("goal") or ""),
            status=str(task.get("status") or ""),
            revision=int(task.get("revision", 0)),
            program=str(task.get("program") or ""),
            board=str(task.get("board") or ""),
            owner_repo=task.get("owner_repo"),
            write_paths=tuple(task.get("write_paths") or ()),
            depends_on=tuple(task.get("depends_on") or ()),
            downstream=downstream[task["id"]],
            acceptance=tuple(task.get("acceptance") or ()),
            evidence=task.get("evidence"),
            created_at=task.get("created_at"),
            updated_at=task.get("updated_at"),
            epoch=int(task.get("epoch", 0) or 0),
            blocked_needs=task.get("blocked_needs"),
            claim=claim_view(task, now),
            claimability=eligibility,
            blockers=tuple(blockers),
        ))

    counts: dict[str, int] = {}
    for view in views:
        counts[view.status] = counts.get(view.status, 0) + 1
    counts["total"] = len(views)
    counts["claimable"] = sum(1 for view in views if view.claimability.claimable)

    board_tasks: dict[str, int] = {}
    for task in tasks:
        board_tasks[task.get("board", "")] = board_tasks.get(task.get("board", ""), 0) + 1
    boards = tuple(BoardView(
        id=board.get("id", ""),
        repo=board.get("repo"),
        path=board.get("path"),
        kind=board.get("kind"),
        role=board.get("role"),
        tasks=board_tasks.get(board.get("id", ""), 0),
    ) for board in registry.get("boards", []))

    return RegistryView(
        as_of=snapshot.as_of,
        digest=snapshot.digest,
        db=snapshot.db_path,
        head_seq=snapshot.head_seq,
        head_hash=snapshot.head_hash,
        counts=counts,
        statuses=tuple(sorted({view.status for view in views})),
        boards=boards,
        tasks=tuple(views),
    )


def claimable(view: RegistryView) -> list[TaskView]:
    """Claimable tasks, ranked the way ``loom list --ready`` ranks them."""
    selected = [task for task in view.tasks if task.claimability.claimable]
    return sorted(selected, key=lambda task: (-len(task.downstream), task.id))


def find(view: RegistryView, task_id: str) -> TaskView | None:
    return next((task for task in view.tasks if task.id == task_id), None)


def expired_leases(view: RegistryView) -> list[TaskView]:
    return [task for task in view.tasks if task.claim and task.claim.expired]
