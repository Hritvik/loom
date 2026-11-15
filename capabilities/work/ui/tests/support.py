"""Disposable work homes and a fake promoted release for the UI tests.

Nothing here touches the live registry or the real release tree. Every test gets its own SQLite
database, its own owner repository, and its own ``LOOM_ENGINE`` tree containing byte-identical
copies of ``loom``, ``engine.py`` and ``store.py`` — so the version gate can be opened, closed and
drifted deterministically.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

UI = Path(__file__).resolve().parents[1]
WORK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(UI))

from loom_ui import ENGINE_HOME  # noqa: E402
from store import SqliteStore  # noqa: E402

T0 = "2026-11-15T12:00:00Z"


def task(task_id, status, paths=None, deps=None, claim=None, **extra):
    record = {
        "id": task_id,
        "board": "demo.board",
        "program": "demo",
        "title": f"Task {task_id}",
        "goal": f"Prove {task_id}",
        "status": status,
        "revision": 3,
        "epoch": 1,
        "updated_at": T0,
        "created_at": T0,
    }
    if paths is not None:
        record["write_paths"] = paths
    if deps:
        record["depends_on"] = deps
    if claim:
        record["claim"] = claim
    record.update(extra)
    return record


class Home:
    """A temporary work home whose front door is a matching fake release."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "work.db"
        self.repo = self.root / "repo"
        (self.repo / "evidence").mkdir(parents=True)
        self.engine_root = self.root / "engine"
        self.work_home = self.engine_root / "capabilities" / "work"
        self.work_home.mkdir(parents=True)
        for name in ("loom", "engine.py", "store.py"):
            shutil.copy(ENGINE_HOME / name, self.work_home / name)

        self._env: dict[str, str | None] = {}
        self.setenv("WORK_DB", str(self.db))
        self.setenv("LOOM_ENGINE", str(self.engine_root))
        self.home_dir = self.root / "home"
        self.home_dir.mkdir()
        self.setenv("HOME", str(self.home_dir))

        self.registry = {
            "kind": "work-coordination-registry",
            "version": 1,
            "updated": "2026-11-15",
            "boards": [{
                "id": "demo.board",
                "kind": "board",
                "role": "provider",
                "repo": str(self.repo),
                "path": "docs/board.json",
            }],
            "tasks": [
                task("demo/W-1", "ready", ["src/one"]),
                task("demo/W-2", "ready", ["src/two"], deps=["demo/W-1"]),
                task("demo/W-3", "claimed", ["src/three"], claim={
                    "agent": "opencode/other",
                    "epoch": 2,
                    "base_revision": 1,
                    "lease_expires": "2030-01-01T00:00:00Z",
                    "heartbeat": T0,
                }),
                task("demo/W-4", "ready", ["src/three"]),
                task("demo/W-5", "green", ["src/five"], claim={
                    "agent": "opencode/worker",
                    "epoch": 4,
                    "base_revision": 2,
                    "lease_expires": "2030-01-01T00:00:00Z",
                    "heartbeat": T0,
                }, evidence="evidence/demo-W-5.md"),
                task("demo/W-6", "accepted"),
                task("demo/W-7", "ready", ["src/seven"], claim={
                    "agent": "opencode/lapsed",
                    "epoch": 1,
                    "base_revision": 0,
                    "lease_expires": "2026-11-15T09:00:00Z",
                    "heartbeat": "2026-11-15T08:00:00Z",
                }),
            ],
            "edges": [{"from": "demo/W-2", "to": "demo/W-1", "kind": "depends-on"}],
            "claims": [],
        }
        for record in self.registry["tasks"]:
            record.setdefault("owner_repo", str(self.repo))
        self.save()
        (self.repo / "evidence" / "demo-W-5.md").write_text("# evidence\n")
        self.genesis()

    # -- environment ---------------------------------------------------------

    def setenv(self, name: str, value: str) -> None:
        self._env[name] = os.environ.get(name)
        os.environ[name] = value

    def close(self) -> None:
        for name, previous in self._env.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self._tmp.cleanup()

    # -- store ---------------------------------------------------------------

    def store(self) -> SqliteStore:
        store = SqliteStore(self.root, self.db)
        store.registry = json.loads(json.dumps(self.registry))
        return store

    def save(self) -> None:
        store = self.store()
        try:
            with store.lock():
                store.save()
        finally:
            store.close()

    def genesis(self) -> None:
        store = SqliteStore(self.root, self.db)
        try:
            with store.lock():
                store.append_event(event="genesis", agent="setup", registry_sha256="0" * 64,
                                   tasks=len(self.registry["tasks"]), now=T0)
        finally:
            store.close()

    def append(self, **fields) -> dict:
        store = SqliteStore(self.root, self.db)
        try:
            with store.lock():
                return store.append_event(**fields)
        finally:
            store.close()

    def load(self) -> dict:
        store = SqliteStore(self.root, self.db)
        try:
            return store.load()
        finally:
            store.close()

    def publish_release(self, commit: str = "a" * 40, tree: str | None = None) -> dict:
        """Write the release record the front door reads; defaults to the real tree digest."""
        from loom_ui.paths import front_door

        digest = tree if tree is not None else front_door().tree_digest(self.engine_root)
        payload = {"current": {"commit": commit, "promoted_at": T0, "tree": digest},
                   "previous": None}
        (self.root / "engine.release").write_text(json.dumps(payload, indent=2) + "\n")
        return payload

    def drift_engine(self, name: str = "store.py") -> None:
        """Make the fake release disagree with the engine the UI loaded."""
        path = self.work_home / name
        path.write_text(path.read_text() + "\n# drifted\n")

    def launch_agent(self, label: str | None = None, interval: int = 120) -> Path:
        """Write a launch agent plist under the temporary HOME."""
        import plistlib

        label = label or "local.loom-tick"
        directory = self.home_dir / "Library" / "LaunchAgents"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{label}.plist"
        payload = {
            "Label": label,
            "ProgramArguments": ["/usr/bin/python3", str(self.work_home / "loom"),
                                 "tick", "--apply", "--quiet"],
            "StartInterval": interval,
        }
        path.write_bytes(plistlib.dumps(payload))
        return path
