"""Read-only operations visibility.

The workers, the tick and the schedule are not part of the registry snapshot. They are observed
from the event log, the tick's own decision record, the launch agent and the tick log, and each is
sampled at a different moment. This module keeps them separate: capacity and leases come from the
same snapshot as the rest of the read model, process liveness is probed now, the last tick is read
from the record it left behind, and the schedule is read from launchd and its plist.

Every displayed value is derived through the engine's own folds:

* live workers and capacity are ``engine.attempt_views`` + ``engine.live_attempts``, the same fold
  ``loom tick`` and ``loom workers`` use;
* expired leases use the predicate in ``engine.cmd_tick`` (a claim without a live expiry is
  expired), not a second definition;
* stuck attempts use the tick's own ``stuck_after`` setting and age calculation;
* limits come from the front door's ``load_config``, so a limit is never restated here.

There is no apply, dispatch, stop, start, promote, rollback or reanchor path in this module. It
reads; it does not run a policy pass. A tick is only ever the *recorded* pass left by the
scheduler.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import engine
import store

from . import provenance
from .model import attempt_start_grace_expired
from .paths import front_door
from .snapshot import Snapshot
from .version import EngineVersion

OK = provenance.OK
DEGRADED = provenance.DEGRADED
UNAVAILABLE = provenance.UNAVAILABLE

MAX_ATTEMPTS = 25
MAX_FAILURES = 20
MAX_LOG_LINES = 20
MAX_TAIL_BYTES = 64 * 1024
LAUNCHCTL_TIMEOUT = 5
TICK_STALE_INTERVALS = 3
# `loom schedule install` writes StartInterval from config schedule.tick_interval with this literal
# default (front door, `loom:470`). The key is not in the front door's DEFAULTS, so a config without
# it plus a plist we cannot read leaves the interval assumed rather than observed; callers record
# which of the three it was.
DEFAULT_TICK_INTERVAL = 120


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(value) -> datetime | None:
    try:
        return store.parse_time(value) if value else None
    except (TypeError, ValueError):
        return None


def _seconds(now: datetime, then: datetime | None) -> int | None:
    return int((now - then).total_seconds()) if then else None


def _count(value) -> int:
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    return 1 if value else 0


def _tail(path: Path, lines: int, limit: int = MAX_TAIL_BYTES) -> list[str]:
    """The last ``lines`` lines of a file, reading at most ``limit`` bytes.

    The tick log is appended by a scheduler and is not rotated; a whole-file read would grow
    without bound. ``errors="replace"`` keeps a malformed byte from failing the sample.
    """
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - limit))
        raw = handle.read()
    text = raw.decode("utf-8", errors="replace")
    return text.splitlines()[-lines:]


def _config() -> dict:
    """The front door's configuration and defaults, as ``loom tick`` would load them."""
    return front_door().load_config()


def _tick_decisions_path() -> Path:
    # The same environment variable and default as engine.cmd_tick's apply record.
    return Path(os.environ.get("LOOM_TICK_DECISIONS")
                or Path.home() / ".local/state/loom/tick.decisions.jsonl")


def _tick_log_path() -> Path:
    # `loom schedule install` writes both streams to this file.
    return Path.home() / ".local/state/loom/tick.log"


# -- typed views -------------------------------------------------------------


@dataclass(frozen=True)
class CapacityView:
    limits: dict
    active: dict
    available: dict
    live_attempts: int
    live_reviewers: int
    open_attempts: int

    def to_dict(self) -> dict:
        return {**asdict(self), "limits": dict(self.limits), "active": dict(self.active),
                "available": dict(self.available)}


@dataclass(frozen=True)
class AttemptView:
    attempt: str
    task: str
    role: str
    state: str
    live: bool
    pid: int | None
    branch: str | None
    worktree: str | None
    opened_at: str | None
    started_at: str | None
    age_seconds: int | None
    stale: bool
    last_seq: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LeaseView:
    task: str
    agent: str | None
    epoch: int | None
    revision: int | None
    expired_at: str | None
    overdue_seconds: int | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FailureView:
    kind: str
    task: str
    attempt: str
    role: str
    state: str
    reason: str | None
    at: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TickView:
    path: str
    record: dict | None
    age_seconds: int | None
    stale: bool
    stale_after_seconds: int
    status: str
    as_of: str
    detail: str

    def to_dict(self) -> dict:
        return {**asdict(self), "record": dict(self.record) if self.record else None}


@dataclass(frozen=True)
class ScheduleView:
    label: str | None
    plist: str | None
    job: str
    launchctl: str | None
    interval_seconds: int | None
    interval_source: str | None
    program_arguments: tuple[str, ...]
    log: str
    status: str
    as_of: str
    detail: str

    def to_dict(self) -> dict:
        return {**asdict(self), "program_arguments": list(self.program_arguments)}


@dataclass(frozen=True)
class ReleaseView:
    gate_verdict: str
    detail: str
    front_door: str | None
    running_root: str | None
    commit: str | None
    digest: str | None
    status: str
    as_of: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LogView:
    path: str
    status: str
    as_of: str
    detail: str
    bytes: int | None
    lines: tuple[str, ...]

    def to_dict(self) -> dict:
        return {**asdict(self), "lines": list(self.lines)}


@dataclass(frozen=True)
class OperationsView:
    as_of: str
    snapshot_as_of: str
    capacity: CapacityView
    attempts: tuple[AttemptView, ...]
    expired_leases: tuple[LeaseView, ...]
    stuck: tuple[FailureView, ...]
    failures: tuple[FailureView, ...]
    tick: TickView
    schedule: ScheduleView
    release: ReleaseView
    log: LogView
    failures_total: int = 0
    sources: tuple[provenance.Signal, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "snapshot_as_of": self.snapshot_as_of,
            "read_only": True,
            "capacity": self.capacity.to_dict(),
            "attempts": [item.to_dict() for item in self.attempts],
            "expired_leases": [item.to_dict() for item in self.expired_leases],
            "stuck": [item.to_dict() for item in self.stuck],
            "failures": [item.to_dict() for item in self.failures],
            "failures_total": self.failures_total,
            "tick": self.tick.to_dict(),
            "schedule": self.schedule.to_dict(),
            "release": self.release.to_dict(),
            "log": self.log.to_dict(),
            "sources": [signal.to_dict() for signal in self.sources],
        }

    def source(self, name: str) -> provenance.Signal | None:
        return next((signal for signal in self.sources if signal.source == name), None)


# -- sources -----------------------------------------------------------------


def _leases(snapshot: Snapshot, now: datetime,
            sampled: str) -> tuple[provenance.Signal, tuple[LeaseView, ...]]:
    expired: list[LeaseView] = []
    for task in snapshot.registry.get("tasks", []):
        claim = task.get("claim") or {}
        if not claim:
            continue
        expiry = engine.lease_expiry(task)
        if expiry and expiry > now:
            continue
        # This is the predicate in engine.cmd_tick: a claim with no parseable expiry is also
        # expired, because no lease can be proven live.
        expired.append(LeaseView(
            task=task.get("id", ""),
            agent=claim.get("agent"),
            epoch=claim.get("epoch"),
            revision=task.get("revision"),
            expired_at=claim.get("lease_expires"),
            overdue_seconds=_seconds(now, expiry),
        ))
    detail = (f"{len(expired)} expired lease(s) across "
              f"{len(snapshot.registry.get('tasks', []))} task(s)")
    return (provenance.Signal(source="database", status=OK, as_of=snapshot.as_of,
                              detail=detail,
                              data={"expired_leases": len(expired),
                                    "sampled_at": sampled}),
            tuple(sorted(expired, key=lambda item: (item.expired_at or "", item.task))))


def _process(snapshot: Snapshot, now: datetime, sampled: str, config: dict,
             config_error: str | None
             ) -> tuple[provenance.Signal, CapacityView, tuple[AttemptView, ...],
                        tuple[FailureView, ...], tuple[FailureView, ...]]:
    views = engine.attempt_views(list(snapshot.events))
    live = engine.live_attempts(views)
    open_attempts = [item for item in views.values()
                     if item.get("state") not in engine.ATTEMPT_TERMINAL]

    roles = tuple(engine.ROLES)
    configured = (config.get("worker", {}) or {}).get("limits", {}) or {}
    limits = {role: int(configured.get(role, 0) or 0) for role in roles}
    active = {role: 0 for role in roles}
    for item in live:
        role = item.get("role") or "implementer"
        active[role] = active.get(role, 0) + 1
    available = {role: limits[role] - active[role] for role in roles}
    live_reviewers = sum(1 for item in live if item.get("role") == "reviewer")

    stuck_after = engine.parse_duration(
        (config.get("worker", {}) or {}).get("stuck_after", "3h"))
    grace = engine.parse_duration(engine.ATTEMPT_START_GRACE)

    ordered = sorted(views.values(), key=lambda entry: entry.get("last_seq") or 0,
                     reverse=True)
    attempts: list[AttemptView] = []
    stuck: list[FailureView] = []
    failures: list[FailureView] = []
    # Failures are counted over every attempt and truncated only by the display limit, so a
    # headline never reports a capped list as if it were the whole record.
    for item in ordered:
        started = _parse(item.get("started_at") or item.get("opened_at") or item.get("ts"))
        age = _seconds(now, started)
        alive = engine.attempt_is_live(item)
        state = str(item.get("state") or "")
        stale = bool(state == "starting" and attempt_start_grace_expired(item, now, grace))
        if len(attempts) < MAX_ATTEMPTS:
            attempts.append(AttemptView(
                attempt=str(item.get("attempt") or ""),
                task=str(item.get("task") or ""),
                role=str(item.get("role") or "implementer"),
                state=state,
                live=alive,
                pid=item.get("pid"),
                branch=item.get("branch"),
                worktree=item.get("worktree"),
                opened_at=item.get("opened_at"),
                started_at=item.get("started_at"),
                age_seconds=age,
                stale=stale,
                last_seq=int(item.get("last_seq") or 0),
            ))
        at = item.get("started_at") or item.get("opened_at")
        if state == "failed":
            failures.append(FailureView(kind="failed", task=str(item.get("task") or ""),
                                        attempt=str(item.get("attempt") or ""),
                                        role=str(item.get("role") or "implementer"),
                                        state=state, reason=item.get("reason"),
                                        at=item.get("closed_at") or at))
        elif state not in engine.ATTEMPT_TERMINAL and item.get("launches") and not alive:
            failures.append(FailureView(kind="lost-process", task=str(item.get("task") or ""),
                                        attempt=str(item.get("attempt") or ""),
                                        role=str(item.get("role") or "implementer"),
                                        state=state, reason=item.get("reason"), at=at))
        elif stale:
            failures.append(FailureView(kind="stale-start", task=str(item.get("task") or ""),
                                        attempt=str(item.get("attempt") or ""),
                                        role=str(item.get("role") or "implementer"),
                                        state=state,
                                        reason=item.get("reason")
                                        or "attempt never attached a process",
                                        at=at))

    for item in live:
        started = _parse(item.get("started_at") or item.get("opened_at") or item.get("ts"))
        age = _seconds(now, started)
        if age is not None and age > stuck_after.total_seconds():
            stuck.append(FailureView(
                kind="stuck", task=str(item.get("task") or ""),
                attempt=str(item.get("attempt") or ""),
                role=str(item.get("role") or "implementer"),
                state=str(item.get("state") or ""),
                reason=f"running {age // 60} minute(s), over {stuck_after}",
                at=item.get("started_at") or item.get("opened_at")))

    status = OK
    # A pid-less open attempt is only unreadable once its start grace has run out: before that the
    # claim is simply waiting for the spawn. Both this and the stale-start failure use the one age
    # predicate, so the process and launches sources cannot disagree about the same attempt.
    unreadable = [item for item in open_attempts
                  if not item.get("pid") and attempt_start_grace_expired(item, now, grace)]
    detail = (f"{len(live)} live worker process(es) of {len(open_attempts)} open attempt(s); "
              f"capacity " + ", ".join(f"{role} {active[role]}/{limits[role]}" for role in roles))
    if config_error:
        status = DEGRADED
        detail += f"; limits unavailable ({config_error})"
    if unreadable:
        status = DEGRADED
        detail += (f"; {len(unreadable)} open attempt(s) past the {engine.ATTEMPT_START_GRACE} "
                   f"start grace carry no pid")

    capacity = CapacityView(limits=limits, active=active, available=available,
                            live_attempts=len(live), live_reviewers=live_reviewers,
                            open_attempts=len(open_attempts))
    return (provenance.Signal(source="process", status=status, as_of=sampled, detail=detail,
                              data={"capacity": capacity.to_dict(),
                                    "derived_at": _iso(now),
                                    "live": len(live), "open": len(open_attempts),
                                    "unreadable": [item.get("task") for item in unreadable]}),
            capacity, tuple(attempts), tuple(sorted(stuck, key=lambda item: item.task)),
            tuple(sorted(failures, key=lambda item: (item.at or "", item.task), reverse=True)))


def _launches(sampled: str, stuck: tuple[FailureView, ...],
              failures: tuple[FailureView, ...], shown: int
              ) -> tuple[provenance.Signal, provenance.Signal]:
    status = DEGRADED if (stuck or failures) else OK
    total = len(failures)
    detail = (f"{total} failed, stale or lost launch(es); "
              f"{len(stuck)} attempt(s) over the stuck threshold")
    if shown < total:
        detail += f"; the most recent {shown} are listed"
    signal = provenance.Signal(source="launches", status=status, as_of=sampled, detail=detail,
                               data={"failures": total, "shown": min(shown, total),
                                     "not_shown": max(total - shown, 0), "stuck": len(stuck)})
    return signal, signal


def _tick(now: datetime, sampled: str, interval: int, interval_source: str | None
          ) -> tuple[provenance.Signal, TickView]:
    path = _tick_decisions_path()
    stale_after = max(interval, 1) * TICK_STALE_INTERVALS
    if not path.is_file():
        detail = f"no recorded tick --apply pass at {path}"
        view = TickView(path=str(path), record=None, age_seconds=None, stale=False,
                        stale_after_seconds=stale_after, status=UNAVAILABLE, as_of=sampled,
                        detail=detail)
        return (provenance.Signal(source="tick", status=UNAVAILABLE, as_of=sampled,
                                  detail=detail, data={"path": str(path), "record": None}),
                view)
    try:
        lines = [line for line in _tail(path, 5) if line.strip()]
        if not lines:
            raise ValueError("the decision record is empty")
        record = json.loads(lines[-1])
        if not isinstance(record, dict) or not record.get("ts"):
            raise ValueError("the last line is not a tick decision record")
        at = _parse(record["ts"])
        if at is None:
            raise ValueError(f"unreadable tick timestamp {record['ts']!r}")
    except (OSError, ValueError) as exc:
        detail = f"cannot read the last tick --apply pass at {path}: {exc}"
        view = TickView(path=str(path), record=None, age_seconds=None, stale=False,
                        stale_after_seconds=stale_after, status=UNAVAILABLE, as_of=sampled,
                        detail=detail)
        return (provenance.Signal(source="tick", status=UNAVAILABLE, as_of=sampled,
                                  detail=detail, data={"path": str(path), "record": None}),
                view)

    age = _seconds(now, at)
    stale = age is not None and age > stale_after
    launched = _count(record.get("launched"))
    reviews = _count(record.get("reviews"))
    problems = _count(record.get("problems"))
    held = _count(record.get("held"))
    status = DEGRADED if stale else OK
    if age is None:
        when = "age unknown"
    elif age >= 0:
        when = f"{age}s before the snapshot"
    else:
        when = f"{-age}s after the snapshot read"
    detail = (f"last recorded tick --apply pass {record['ts']} ({when})"
              f": {launched} launch(es), {reviews} review(s), {held} held, "
              f"{problems} problem(s)")
    if stale:
        basis = f"3 x the {interval}s schedule"
        if interval_source != "plist":
            basis += f" ({interval_source} interval, not the installed plist)"
        detail += f"; older than {stale_after}s ({basis})"
    view = TickView(path=str(path), record=record, age_seconds=age, stale=stale,
                    stale_after_seconds=stale_after, status=status, as_of=str(record["ts"]),
                    detail=detail)
    return (provenance.Signal(source="tick", status=status, as_of=str(record["ts"]),
                              detail=detail,
                              data={"path": str(path), "record": record, "age_seconds": age,
                                    "stale": stale, "stale_after_seconds": stale_after,
                                    "interval_seconds": interval,
                                    "interval_source": interval_source}),
            view)


def _launchctl_listing() -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True,
                                timeout=LAUNCHCTL_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, result.stderr.strip() or f"launchctl exited {result.returncode}"
    return result.stdout, None


def _schedule(sampled: str, config: dict) -> tuple[provenance.Signal, ScheduleView]:
    label = (config.get("schedule", {}) or {}).get("tick_label", "local.loom-tick")
    base = provenance.scheduler("scheduler", sampled)
    listing, error = _launchctl_listing()
    row = None
    if listing is not None:
        row = next((line.strip() for line in listing.splitlines() if label in line), None)
    if row:
        job = "loaded"
    elif listing is not None:
        job = "not loaded"
    else:
        job = "unknown"

    interval, interval_source = _tick_interval(base, config)

    status = base.status
    detail = base.detail
    if job == "loaded":
        detail += "; launchd job loaded"
    elif job == "not loaded":
        status = DEGRADED
        detail += "; launchd job not loaded"
    else:
        status = DEGRADED
        detail += f"; cannot read launchctl list ({error})"
    if interval_source != "plist":
        detail += (f"; tick interval assumed {interval}s from the {interval_source}, "
                   f"not read from the plist")

    view = ScheduleView(
        label=label,
        plist=base.data.get("plist"),
        job=job,
        launchctl=row,
        interval_seconds=interval,
        interval_source=interval_source,
        program_arguments=tuple(base.data.get("program_arguments") or ()),
        log=str(_tick_log_path()),
        status=status,
        as_of=sampled,
        detail=detail,
    )
    return (provenance.Signal(source="scheduler", status=status, as_of=sampled, detail=detail,
                              data={**base.data, "job": job, "launchctl": row,
                                    "interval_seconds": interval,
                                    "interval_source": interval_source, "log": view.log}), view)


def _tick_interval(base: provenance.Signal, config: dict) -> tuple[int, str]:
    """The tick interval and where it came from: the plist, the config, or the installer default.

    ``loom schedule install`` writes ``StartInterval`` from the front door's
    ``schedule.tick_interval``, so a plist that records one is the measured value. Without it the
    engine's own configuration is the next best observation — and when the front door cannot be
    read at all, the installer's literal 120s is an assumption. Naming which of the three the
    threshold rests on keeps the tick's "3 x the schedule" freshness from reading as measured.
    """
    observed = base.data.get("interval_seconds")
    if observed:
        try:
            return int(observed), "plist"
        except (TypeError, ValueError):
            pass
    configured = (config.get("schedule", {}) or {}).get("tick_interval")
    if configured:
        try:
            return int(configured), "config"
        except (TypeError, ValueError):
            pass
    return DEFAULT_TICK_INTERVAL, "default"


def _release(version: EngineVersion, sampled: str
             ) -> tuple[provenance.Signal, ReleaseView]:
    signal = provenance.release("release", sampled, version)
    data = signal.data
    view = ReleaseView(
        gate_verdict=str(data.get("verdict") or version.verdict),
        detail=signal.detail,
        front_door=data.get("front_door") or version.front_door,
        running_root=data.get("running_root") or version.running_root,
        commit=data.get("commit") or version.release_commit,
        digest=data.get("release_digest") or version.release_digest,
        status=signal.status,
        as_of=signal.as_of,
    )
    return signal, view


def _log(sampled: str) -> tuple[provenance.Signal, LogView]:
    path = _tick_log_path()
    if not path.is_file():
        detail = f"no tick log at {path}"
        view = LogView(path=str(path), status=UNAVAILABLE, as_of=sampled, detail=detail,
                       bytes=None, lines=())
        return (provenance.Signal(source="logs", status=UNAVAILABLE, as_of=sampled,
                                  detail=detail, data={"path": str(path)}), view)
    try:
        stat = path.stat()
        lines = tuple(_tail(path, MAX_LOG_LINES))
    except OSError as exc:
        detail = f"cannot read {path}: {exc}"
        view = LogView(path=str(path), status=DEGRADED, as_of=sampled, detail=detail,
                       bytes=None, lines=())
        return (provenance.Signal(source="logs", status=DEGRADED, as_of=sampled,
                                  detail=detail, data={"path": str(path)}), view)
    at = _iso(datetime.fromtimestamp(stat.st_mtime, timezone.utc))
    detail = (f"{len(lines)} of the last {MAX_LOG_LINES} line(s) of {path}; "
              f"last written {at}")
    view = LogView(path=str(path), status=OK, as_of=at, detail=detail, bytes=stat.st_size,
                   lines=lines)
    return (provenance.Signal(source="logs", status=OK, as_of=at, detail=detail,
                              data={"path": str(path), "bytes": stat.st_size,
                                    "lines": len(lines)}), view)


# -- assembly ----------------------------------------------------------------


def _empty_capacity() -> CapacityView:
    roles = tuple(engine.ROLES)
    zero = {role: 0 for role in roles}
    return CapacityView(limits=dict(zero), active=dict(zero), available=dict(zero),
                        live_attempts=0, live_reviewers=0, open_attempts=0)


def _empty_tick(sampled: str, interval: int) -> TickView:
    return TickView(path=str(_tick_decisions_path()), record=None, age_seconds=None, stale=False,
                    stale_after_seconds=interval * TICK_STALE_INTERVALS, status=UNAVAILABLE,
                    as_of=sampled, detail="not sampled")


def _empty_schedule(sampled: str) -> ScheduleView:
    return ScheduleView(label=None, plist=None, job="unknown", launchctl=None,
                        interval_seconds=None, interval_source=None, program_arguments=(),
                        log=str(_tick_log_path()), status=UNAVAILABLE, as_of=sampled,
                        detail="not sampled")


def _empty_release(version: EngineVersion, sampled: str) -> ReleaseView:
    return ReleaseView(gate_verdict=version.verdict, detail=version.detail,
                       front_door=version.front_door, running_root=version.running_root,
                       commit=version.release_commit, digest=version.release_digest,
                       status=UNAVAILABLE, as_of=sampled)


def _empty_log(sampled: str) -> LogView:
    return LogView(path=str(_tick_log_path()), status=UNAVAILABLE, as_of=sampled,
                   detail="not sampled", bytes=None, lines=())


def _safe(source: str, sampled: str, fn, fallback):
    """Run one source; a broken source becomes a signal instead of hiding the others."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - a source failure must not hide the page
        signal = provenance.Signal(source=source, status=UNAVAILABLE, as_of=sampled,
                                   detail=f"could not sample: {exc}", data={})
        return signal, fallback


def collect(snapshot: Snapshot, version: EngineVersion) -> OperationsView:
    """Sample every operations source, keeping each one's own time and state.

    ``now`` is the snapshot's read time, not the wall clock: every derived value (lease expiry,
    attempt age, stuck threshold, tick freshness) describes the same instant as the task list, so
    two halves of one page cannot disagree. The liveness probe and each external source carry
    their own ``as_of``.
    """
    sampled = _iso(datetime.now(timezone.utc))
    now = store.parse_time(snapshot.as_of)

    config: dict = {}
    config_error: str | None = None
    try:
        config = _config()
    except Exception as exc:  # noqa: BLE001 - the front door is a source, not a precondition here
        config_error = str(exc)

    database_signal, leases = _safe(
        "database", sampled, lambda: _leases(snapshot, now, sampled), ())
    process_signal, capacity, attempts, stuck, failures = _safe(
        "process", sampled,
        lambda: _process(snapshot, now, sampled, config, config_error),
        (provenance.Signal(source="process", status=UNAVAILABLE, as_of=sampled,
                           detail=f"could not sample: {config_error or 'no event log'}",
                           data={}), _empty_capacity(), (), (), ()))
    launches_signal, _ = _safe(
        "launches", sampled, lambda: _launches(sampled, stuck, failures,
                                               min(len(failures), MAX_FAILURES)),
        provenance.Signal(source="launches", status=UNAVAILABLE, as_of=sampled,
                          detail="could not sample the launch record", data={}))
    schedule_signal, schedule = _safe(
        "scheduler", sampled, lambda: _schedule(sampled, config),
        (provenance.Signal(source="scheduler", status=UNAVAILABLE, as_of=sampled,
                           detail="could not sample the schedule", data={}),
         _empty_schedule(sampled)))
    interval = schedule.interval_seconds or DEFAULT_TICK_INTERVAL
    interval_source = schedule.interval_source or "default"
    tick_signal, tick = _safe(
        "tick", sampled, lambda: _tick(now, sampled, interval, interval_source),
        (provenance.Signal(source="tick", status=UNAVAILABLE, as_of=sampled,
                           detail="could not sample the tick record", data={}),
         _empty_tick(sampled, interval)))
    release_signal, release = _safe(
        "release", sampled, lambda: _release(version, sampled),
        (provenance.Signal(source="release", status=UNAVAILABLE, as_of=sampled,
                           detail="could not sample the release", data={}),
         _empty_release(version, sampled)))
    log_signal, log = _safe(
        "logs", sampled, lambda: _log(sampled),
        (provenance.Signal(source="logs", status=UNAVAILABLE, as_of=sampled,
                           detail="could not sample the tick log", data={}),
         _empty_log(sampled)))

    return OperationsView(
        as_of=sampled,
        snapshot_as_of=snapshot.as_of,
        capacity=capacity,
        attempts=attempts,
        expired_leases=leases,
        stuck=stuck,
        failures=tuple(failures[:MAX_FAILURES]),
        tick=tick,
        schedule=schedule,
        release=release,
        log=log,
        failures_total=len(failures),
        sources=(database_signal, process_signal, launches_signal, tick_signal,
                 schedule_signal, release_signal, log_signal),
    )


def degraded(view: OperationsView) -> list[provenance.Signal]:
    return [signal for signal in view.sources if signal.status != OK]
