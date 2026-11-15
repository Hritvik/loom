"""Front-door resolution and the fail-closed version gate.

The UI must behave like the engine the scheduler actually runs, not like whatever checkout happened
to start it. Two questions decide that:

* **Which front door is active?** ``loom.running_root()`` — the promoted release if one is installed,
  otherwise the development checkout. The UI reports that path and never invents its own.
* **Is the code this process loaded the same code?** A digest over the engine sources. When the UI
  imports ``engine`` from a different tree than the active front door, the display could disagree
  with the engine's own decisions, so the gate closes instead of guessing.

Verdicts:

===========  ======  ==================================================================
``PINNED``   open    a release exists, its recorded tree digest verifies, and the UI's engine
                     sources match the release's
``UNPINNED`` open     no promoted release exists, so the front door is the development checkout;
                     reported as degraded, not hidden
``MISMATCH`` closed  the UI loaded engine sources that differ from the active front door
``DRIFTED``  closed  the release tree no longer matches the digest recorded at promotion
``ABSENT``   closed  a release is recorded but its tree (or front door) is gone
===========  ======  ==================================================================
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .paths import engine_home, front_door

# The engine files whose behaviour the UI depends on. `loom` routes verbs, `engine` decides them,
# `store` reads and writes them. A change to any of the three can move a displayed value.
ENGINE_SOURCES = ("loom", "engine.py", "store.py")

OPEN_VERDICTS = {"PINNED", "UNPINNED"}


class VersionGateClosed(RuntimeError):
    """The UI refused to serve because it cannot prove it matches the active front door."""

    def __init__(self, status: "EngineVersion"):
        super().__init__(status.detail)
        self.status = status


@dataclass(frozen=True)
class EngineVersion:
    ok: bool
    verdict: str
    front_door: str
    running_root: str
    ui_root: str
    release_commit: str | None
    release_promoted_at: str | None
    release_digest: str | None
    expected_digest: str | None
    actual_digest: str | None
    detail: str
    as_of: str

    @property
    def pinned(self) -> bool:
        return self.verdict == "PINNED"

    def to_dict(self) -> dict:
        return asdict(self)


def _digest(work_home: Path) -> str | None:
    """Digest the engine sources under ``work_home``; ``None`` when any is missing."""
    digest = hashlib.sha256()
    for name in ENGINE_SOURCES:
        path = work_home / name
        if not path.is_file():
            return None
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _imported_roots(ui_root: Path) -> tuple[Path, ...]:
    """Where `engine` and `store` were actually imported from.

    The digest checks files on disk; this checks that those files are the ones loaded. Without it a
    future entry point could put a different engine on ``sys.path`` and still hash the reviewed tree.
    """
    import engine as engine_module
    import store as store_module

    roots = []
    for module in (engine_module, store_module):
        location = getattr(module, "__file__", None)
        if location:
            roots.append(Path(location).resolve().parent)
    return tuple(roots)


def inspect(now: datetime | None = None) -> EngineVersion:
    """Resolve the active front door and decide whether this process may serve."""
    sampled = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    as_of = sampled.isoformat(timespec="seconds").replace("+00:00", "Z")

    ui_root = engine_home()
    actual = _digest(ui_root)

    try:
        front = front_door()
        release_root = Path(front.engine_dir())
        running = Path(front.running_root())
        recorded = front.read_release() or {}
        tree_digest = front.tree_digest
    except Exception as exc:  # a broken front door cannot be trusted to describe itself
        return EngineVersion(
            ok=False,
            verdict="ABSENT",
            front_door="",
            running_root="",
            ui_root=str(ui_root),
            release_commit=None,
            release_promoted_at=None,
            release_digest=None,
            expected_digest=None,
            actual_digest=actual,
            detail=f"cannot load the loom front door: {exc}",
            as_of=as_of,
        )

    release = (recorded.get("current") or {}) if isinstance(recorded, dict) else {}
    commit = release.get("commit")
    promoted = release.get("promoted_at")
    expected = _digest(running / "capabilities/work")

    base = dict(
        front_door=str(running / "capabilities/work" / "loom"),
        running_root=str(running),
        ui_root=str(ui_root),
        release_commit=commit,
        release_promoted_at=promoted,
        release_digest=release.get("tree"),
        expected_digest=expected,
        actual_digest=actual,
        as_of=as_of,
    )

    # The digest is over files; this is over the modules that were loaded. A UI whose `engine`
    # came from somewhere other than its own engine home is not describing its own tree.
    imported = _imported_roots(ui_root)
    if any(root != ui_root.resolve() for root in imported) or len(imported) != 2:
        return EngineVersion(
            ok=False,
            verdict="MISMATCH",
            detail=(f"engine modules were imported from {[str(root) for root in imported]}, not "
                    f"from the UI's engine home {ui_root}"),
            **base,
        )

    # A recorded release whose tree is gone cannot be checked against anything.
    if commit and not (release_root / "capabilities/work" / "loom").is_file():
        return EngineVersion(
            ok=False,
            verdict="ABSENT",
            detail=(f"release {str(commit)[:12]} is recorded at {release_root} but its front door is "
                    "missing; promote again or roll back"),
            **base,
        )

    # A release must still be the tree that was promoted. `loom deploy status` treats this as
    # DRIFTED; the UI treats it as fatal, because a modified release is not the reviewed code.
    if commit and release.get("tree"):
        try:
            actual_tree = tree_digest(release_root)
        except OSError as exc:
            return EngineVersion(
                ok=False,
                verdict="DRIFTED",
                detail=f"cannot digest the release tree {release_root}: {exc}",
                **base,
            )
        if actual_tree != release["tree"]:
            return EngineVersion(
                ok=False,
                verdict="DRIFTED",
                detail=(f"release tree {release_root} no longer matches the digest recorded at "
                        f"promotion ({str(release['tree'])[:12]} recorded, {actual_tree[:12]} now); "
                        "promote again from a reviewed commit"),
                **base,
            )

    if expected is None:
        return EngineVersion(
            ok=False,
            verdict="ABSENT",
            detail=f"the active front door at {running / 'capabilities/work'} is incomplete; "
                   "cannot verify the engine version",
            **base,
        )

    if actual is None:
        return EngineVersion(
            ok=False,
            verdict="ABSENT",
            detail=f"the UI's engine home {ui_root} is incomplete; cannot verify the engine version",
            **base,
        )

    if expected != actual:
        return EngineVersion(
            ok=False,
            verdict="MISMATCH",
            detail=(f"the UI loaded engine sources from {ui_root} but the active front door is "
                    f"{running} ({commit[:12] if commit else 'unreleased'}); "
                    "start the UI from the promoted release"),
            **base,
        )

    if not commit:
        return EngineVersion(
            ok=True,
            verdict="UNPINNED",
            detail=(f"no promoted release is installed; the front door is the development checkout "
                    f"{running}. Displayed values are still derived from the engine, but the "
                    "scheduler is not running this tree"),
            **base,
        )

    return EngineVersion(
        ok=True,
        verdict="PINNED",
        detail=(f"release {commit[:12]} at {release_root} is intact and matches the engine sources "
                f"this UI loaded"),
        **base,
    )


def require() -> EngineVersion:
    """Inspect and raise when the gate is closed."""
    status = inspect()
    if not status.ok:
        raise VersionGateClosed(status)
    return status
