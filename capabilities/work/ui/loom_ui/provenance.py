"""Source-specific provenance and freshness.

There is no single portfolio-wide snapshot. The registry database, worker processes, the scheduler,
the promoted release, Git refs and evidence files are sampled independently, and each can be stale
or broken while the others are healthy. Collapsing them into one "last updated" would present
agreement the system cannot guarantee, so every signal carries its own ``as_of``, its own status and
its own detail.

A signal never raises: a source that cannot be read becomes ``unavailable`` (it could not be sampled
at all) or ``degraded`` (it was sampled and found wanting), and the rest of the page still renders.
"""
from __future__ import annotations

import plistlib
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import engine

from .paths import front_door
from .model import RegistryView
from .snapshot import Snapshot
from .version import EngineVersion

MAX_GIT_REPOS = 20
GIT_TIMEOUT = 5

OK = "ok"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Signal:
    source: str
    status: str
    as_of: str
    detail: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stamp() -> str:
    return _iso(datetime.now(timezone.utc))


def _sample(source: str, fn) -> Signal:
    """Run one probe; turn any failure into a signal instead of a 500."""
    started = _stamp()
    try:
        return fn(source, started)
    except Exception as exc:  # a broken source must not hide the others
        return Signal(source=source, status=UNAVAILABLE, as_of=started,
                      detail=f"could not sample: {exc}")


def database(_source: str, sampled: str, snapshot: Snapshot) -> Signal:
    return Signal(
        source="database",
        status=OK,
        as_of=snapshot.as_of,
        detail=(f"one consistent read of {snapshot.db_path}: {snapshot.task_count} tasks, "
                f"{snapshot.board_count} boards, head event {snapshot.head_seq}"),
        data={**snapshot.summary(), "sampled_at": sampled},
    )


def process(_source: str, sampled: str, snapshot: Snapshot) -> Signal:
    """Live workers, folded the way the engine folds them.

    The engine stopped writing a state on `launch` records; the run's state lives on an `attempt`.
    Reading `state == "pending"` here would report zero live processes while workers run, which is
    the one thing this surface exists to catch.
    """
    views = engine.attempt_views(snapshot.events)
    live = engine.live_attempts(views)
    open_attempts = [item for item in views.values()
                     if item.get("state") not in engine.ATTEMPT_TERMINAL]
    unreadable = [item.get("task", "") for item in open_attempts if not item.get("pid")]
    detail = f"{len(live)} live worker process(es) of {len(open_attempts)} open attempt(s)"
    status = OK
    if unreadable:
        status = DEGRADED
        detail += f"; {len(unreadable)} open attempt(s) carry no pid"
    return Signal(
        source="process",
        status=status,
        as_of=sampled,
        detail=detail,
        data={
            "live": [
                {"task": item.get("task"), "role": item.get("role"), "pid": item.get("pid"),
                 "branch": item.get("branch")}
                for item in live
            ],
            "open": len(open_attempts),
            "unreadable": unreadable,
        },
    )


def scheduler(_source: str, sampled: str) -> Signal:
    front = front_door()
    config = front.load_config()
    label = config.get("schedule", {}).get("tick_label", "local.loom-tick")
    path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    if not path.is_file():
        return Signal(
            source="scheduler", status=DEGRADED, as_of=sampled,
            detail=f"no launch agent at {path}; the tick is not scheduled",
            data={"label": label, "plist": str(path)},
        )
    arguments: list[str] = []
    interval = None
    try:
        payload = plistlib.loads(path.read_bytes())
        arguments = list(payload.get("ProgramArguments") or [])
        interval = payload.get("StartInterval")
    except Exception as exc:
        return Signal(
            source="scheduler", status=DEGRADED, as_of=sampled,
            detail=f"cannot parse {path}: {exc}",
            data={"label": label, "plist": str(path)},
        )
    stat = path.stat()
    return Signal(
        source="scheduler",
        status=OK,
        as_of=sampled,
        detail=(f"{label} runs every {interval or 'unknown'}s; "
                f"plist last changed {_iso(datetime.fromtimestamp(stat.st_mtime, timezone.utc))}"),
        data={
            "label": label,
            "plist": str(path),
            "interval_seconds": interval,
            "program_arguments": arguments,
            "plist_mtime": _iso(datetime.fromtimestamp(stat.st_mtime, timezone.utc)),
        },
    )


def release(_source: str, sampled: str, version: EngineVersion) -> Signal:
    status = OK if version.verdict == "PINNED" else DEGRADED
    return Signal(
        source="release",
        status=status,
        as_of=sampled,
        detail=version.detail,
        data={
            "verdict": version.verdict,
            "front_door": version.front_door,
            "running_root": version.running_root,
            "ui_root": version.ui_root,
            "commit": version.release_commit,
            "promoted_at": version.release_promoted_at,
            "release_digest": version.release_digest,
            "engine_digest": version.expected_digest,
        },
    )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                            timeout=GIT_TIMEOUT, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git exited {result.returncode}")
    return result.stdout.strip()


def git(_source: str, sampled: str, view: RegistryView) -> Signal:
    repos: list[str] = []
    for task in view.tasks:
        if task.owner_repo and task.owner_repo not in repos:
            repos.append(task.owner_repo)
    truncated = repos[MAX_GIT_REPOS:]
    repos = repos[:MAX_GIT_REPOS]
    samples, failures = [], []
    for value in repos:
        repo = Path(value).expanduser()
        if not repo.is_dir():
            failures.append({"repo": str(repo), "error": "directory does not exist"})
            continue
        try:
            head = _git(repo, "rev-parse", "HEAD")
            branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
            dirty = _git(repo, "status", "--porcelain")
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            failures.append({"repo": str(repo), "error": str(exc)})
            continue
        samples.append({"repo": str(repo), "head": head, "branch": branch,
                        "dirty_files": len(dirty.splitlines())})
    status = OK if not failures else DEGRADED
    detail = f"{len(samples)} of {len(repos) + len(truncated)} owner repositories sampled"
    if failures:
        detail += f"; {len(failures)} unreadable"
    if truncated:
        detail += f"; {len(truncated)} beyond the sampling limit"
    return Signal(
        source="git",
        status=status,
        as_of=sampled,
        detail=detail,
        data={"repositories": samples, "failures": failures,
              "truncated": [str(Path(item).expanduser()) for item in truncated]},
    )


def evidence_location(task: dict) -> tuple[Path | None, str | None]:
    """Where a task's evidence locator resolves, and why it cannot when it cannot.

    One rule for the whole UI: an absolute locator is used as is, a relative one is resolved
    against the owning repository, and a relative locator with no owner repository is unreadable
    rather than guessed. Both the portfolio-wide evidence signal and a single task's inspector
    call this, so they cannot disagree about where a file is or why it is missing.
    """
    locator = task.get("evidence")
    if not locator:
        return None, "no evidence locator recorded"
    location = Path(str(locator)).expanduser()
    if location.is_absolute():
        return location, None
    owner = task.get("owner_repo")
    if not owner:
        return None, "relative path with no owner repository"
    return Path(str(owner)).expanduser() / location, None


def evidence(_source: str, sampled: str, view: RegistryView) -> Signal:
    present, missing = [], []
    for task in view.tasks:
        if not task.evidence:
            continue
        location, error = evidence_location({"evidence": task.evidence,
                                             "owner_repo": task.owner_repo})
        if location is None:
            missing.append({"task": task.id, "path": task.evidence, "error": error})
            continue
        if location.is_file():
            present.append({"task": task.id, "path": str(location)})
        else:
            missing.append({"task": task.id, "path": str(location), "error": "file not found"})
    status = OK if not missing else DEGRADED
    detail = f"{len(present)} evidence file(s) present"
    if missing:
        detail += f"; {len(missing)} missing"
    return Signal(
        source="evidence",
        status=status,
        as_of=sampled,
        detail=detail,
        data={"present": present, "missing": missing},
    )


def collect(snapshot: Snapshot, view: RegistryView, version: EngineVersion) -> tuple[Signal, ...]:
    """Sample every source independently, keeping the order stable for rendering."""
    return (
        _sample("database", lambda source, sampled: database(source, sampled, snapshot)),
        _sample("process", lambda source, sampled: process(source, sampled, snapshot)),
        _sample("scheduler", scheduler),
        _sample("release", lambda source, sampled: release(source, sampled, version)),
        _sample("git", lambda source, sampled: git(source, sampled, view)),
        _sample("evidence", lambda source, sampled: evidence(source, sampled, view)),
    )


def degraded(signals: tuple[Signal, ...]) -> list[Signal]:
    return [signal for signal in signals if signal.status != OK]
