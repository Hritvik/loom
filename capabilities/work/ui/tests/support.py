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
from datetime import datetime, timedelta, timezone
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


SOURCE_A = "a" * 40
SOURCE_B = "b" * 40
SOURCE_C = "c" * 40
DIGEST_A = "d" * 64
REVIEW_PATHS = [{"path": "src/twelve.py", "blob": "f" * 40},
                {"path": "tests/test_twelve.py", "blob": "0" * 40}]


class ControlHome(Home):
    """A work home with one task in every owner queue, and real review/receipt history.

    ``Home`` stays the smallest fixture the foundation tests need. This one adds the states the
    control room and inspector must tell apart: a human-addressed review request, a review verdict,
    an integration receipt, rework, a stale request, a missing evidence file and a live run.
    """

    def __init__(self):
        super().__init__()
        self.add_task("demo/W-11", "green", ["src/eleven"],
                      evidence="evidence/demo-W-11.md")
        self.add_task("demo/W-12", "reviewed", ["src/twelve"],
                      evidence="evidence/demo-W-12.md")
        self.add_task("demo/W-13", "integrated", ["src/thirteen"],
                      evidence="evidence/demo-W-13.md")
        self.add_task("demo/W-14", "released")
        self.add_task("demo/W-15", "blocked", blocked_needs="human")
        self.add_task("demo/W-16", "blocked", blocked_needs="agent")
        self.add_task("demo/W-17", "rejected")
        self.add_task("demo/W-18", "green", ["src/eighteen"])
        self.add_task("demo/W-19", "planned")
        self.add_task("demo/W-20", "ready", ["src/twenty"], deps=["demo/W-19"])
        self.add_task("demo/W-21", "green", ["src/twentyone"])
        self.add_task("demo/W-22", "ready", ["src/twentytwo"])
        self.add_task("demo/W-23", "claimed", ["src/twentythree"], claim={
            "agent": "opencode/worker",
            "epoch": 5,
            "base_revision": 1,
            "lease_expires": "2030-01-01T00:00:00Z",
            "heartbeat": T0,
        })
        self.add_task("demo/W-24", "reviewed", ["src/twentyfour"],
                      evidence="evidence/missing-W-24.md")
        self.add_task("demo/W-25", "reviewed", ["src/twentyfive"],
                      evidence="evidence/demo-W-25.md")
        self.add_task("demo/W-26", "integrated", evidence="evidence/demo-W-26.md")
        self.add_task("demo/W-27", "ready", ["src/twentyseven"])
        self.add_task("demo/W-28", "green", ["src/twentyeight"])
        self.add_task("demo/W-29", "green", ["src/twentynine"])
        self.add_task("demo/W-30", "ready", ["src/thirty"], program="other",
                      board="demo.other")
        self.add_task("demo/W-31", "green", ["src/thirtyone"])
        self.add_task("demo/W-32", "green", ["src/thirtytwo"], claim={
            "agent": "opencode/old-worker",
            "epoch": 2,
            "base_revision": 1,
            "lease_expires": "2026-11-15T09:00:00Z",
            "heartbeat": "2026-11-15T08:00:00Z",
        })
        for name in ("W-11", "W-12", "W-13", "W-25", "W-26"):
            path = self.repo / "evidence" / f"demo-{name}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {name} evidence\n")

        self._history()
        self.save()

    # -- fixture helpers ------------------------------------------------------

    def add_task(self, task_id, status, paths=None, **extra):
        record = task(task_id, status, paths, **extra)
        record.setdefault("owner_repo", str(self.repo))
        self.registry["tasks"].append(record)
        return record

    def record(self, task_id: str) -> dict:
        return next(item for item in self.registry["tasks"] if item["id"] == task_id)

    def review(self, task_id: str, *, evidence: str, source: str = SOURCE_B,
               decision: str = "approve", note: str = "Approved.") -> dict:
        """A review request, the reviewer's answer, and the verdict the task carries."""
        ask = self.ask(task_id, to="opencode/reviewer-agent", question=f"Review {task_id}",
                       review_source=source, review_evidence=evidence)
        answer = self.append(event="answer", task=task_id, agent="opencode/reviewer-agent",
                             to="opencode/worker", in_reply_to=ask["seq"], decision=decision,
                             note=note, revision=3, status="green", review_source=source,
                             now=T0)
        verdict = {"reviewer": "opencode/reviewer-agent", "decision": decision,
                   "source": source, "base": SOURCE_A, "evidence": evidence,
                   "answer": answer["seq"]}
        self.record(task_id)["review"] = verdict
        return verdict

    def ask(self, task_id, *, to, question, revision=3, status="green",
            review_source=None, review_evidence=None) -> dict:
        fields = {"event": "ask", "task": task_id, "agent": "opencode/worker", "to": to,
                  "question": question, "revision": revision, "status": status, "now": T0}
        if review_source:
            fields.update({"review_source": review_source,
                           "review_base": SOURCE_A,
                           "review_evidence": review_evidence or "evidence/demo-W-5.md"})
        return self.append(**fields)

    def _history(self) -> None:
        # A green task with a current review request addressed to the owner.
        self.ask("demo/W-11", to="owner", question="Is this the right cut?",
                 review_source=SOURCE_B, review_evidence="evidence/demo-W-11.md")

        # Reviewed with a bound approval: the integration queue, receipt complete.
        verdict = self.review("demo/W-12", evidence="evidence/demo-W-12.md")
        verdict.update({"paths": list(REVIEW_PATHS), "digest": DIGEST_A})
        self.append(event="transition", task="demo/W-12", agent="opencode/worker",
                    status="reviewed", previous_status="green", revision=3,
                    reviewer=verdict["reviewer"], decision="approve",
                    review_answer=verdict["answer"], review_source=SOURCE_B,
                    review_digest=DIGEST_A, review_path_count=2, now=T0)

        # Integrated with a real receipt: the acceptance queue.
        verdict = self.review("demo/W-13", evidence="evidence/demo-W-13.md")
        verdict.update({"paths": list(REVIEW_PATHS), "digest": DIGEST_A})
        self.append(event="transition", task="demo/W-13", agent="opencode/worker",
                    status="integrated", previous_status="reviewed", revision=3,
                    evidence="evidence/demo-W-13.md", integrated_source=SOURCE_B,
                    integration_tip=SOURCE_C, approved_digest=DIGEST_A,
                    approved_path_count=2, now=T0)

        # Review passed but the evidence file is missing: partial data, not a healthy row.
        verdict = self.review("demo/W-24", evidence="evidence/missing-W-24.md")
        verdict.update({"paths": list(REVIEW_PATHS), "digest": DIGEST_A})

        # Review passed with no approved content: the engine calls this receipt incomplete.
        self.review("demo/W-25", evidence="evidence/demo-W-25.md")

        # Two decisions that only a person can make, and one that needs an agent.
        self.append(event="transition", task="demo/W-15", agent="opencode/worker",
                    status="blocked", previous_status="green", revision=3, needs="human",
                    reason="Choose between option A and option B", now=T0)
        self.append(event="transition", task="demo/W-16", agent="opencode/worker",
                    status="blocked", previous_status="green", revision=3, needs="agent",
                    reason="waiting for an upstream credential", now=T0)
        self.append(event="transition", task="demo/W-17", agent="opencode/worker",
                    status="rejected", previous_status="green", revision=3,
                    reason="gate failed", now=T0)

        # Rework: the reviewer asked for changes and nobody has started.
        ask = self.ask("demo/W-18", to="opencode/reviewer-agent", question="Review demo/W-18",
                       review_source=SOURCE_C, review_evidence="evidence/demo-W-5.md")
        self.append(event="answer", task="demo/W-18", agent="opencode/reviewer-agent",
                    to="opencode/worker", in_reply_to=ask["seq"],
                    decision="changes requested", note="Handle the empty case.", revision=3,
                    status="green", review_source=SOURCE_C, now=T0)

        # A stale request: it names revision 2, so it cannot be re-asked for revision 3.
        self.ask("demo/W-29", to="opencode/reviewer-agent", question="Review revision 2",
                 revision=2, review_source=SOURCE_C)

        # Ordinary questions, one is not addressed to a configured owner identity.
        self.ask("demo/W-22", to="owner", question="Which program owns this change?")
        self.ask("demo/W-27", to="hritvik",
                 question="Can you confirm the acceptance wording?")

        # A current request to an agent reviewer: waiting, not stalled.
        self.ask("demo/W-28", to="opencode/reviewer-agent", question="Review demo/W-28",
                 review_source=SOURCE_C, review_evidence="evidence/demo-W-5.md")

        # Review approved but the coordinator has not recorded `reviewed` yet.
        ask = self.ask("demo/W-31", to="opencode/reviewer-agent", question="Review demo/W-31",
                       review_source=SOURCE_C, review_evidence="evidence/demo-W-5.md")
        self.append(event="answer", task="demo/W-31", agent="opencode/reviewer-agent",
                    to="opencode/worker", in_reply_to=ask["seq"], decision="approve",
                    note="Looks right.", revision=3, status="green", review_source=SOURCE_C,
                    now=T0)

        # A green task whose worker's lease lapsed after submitting: review still stands.
        self.ask("demo/W-32", to="opencode/reviewer-agent", question="Review demo/W-32",
                 review_source=SOURCE_C, review_evidence="evidence/demo-W-5.md")

        # A live run: an attempt opened by a claim, with a real process attached.
        self.append(event="attempt", task="demo/W-23", attempt="demo/W-23#a1",
                    agent="opencode/worker", role="implementer", state="starting", epoch=5,
                    revision=1, now=T0)
        self.append(event="launch", task="demo/W-23", attempt="demo/W-23#a1",
                    agent="opencode/worker", role="implementer", pid=os.getpid(),
                    worktree=str(self.root / "worktree"), branch="loom/demo-w-23-a1", now=T0)


class ActionHome(Home):
    """A work home with one task per guarded-action state, owned by a configured person.

    Revision and epoch are stated rather than inherited from ``task()`` so a claim's
    compare-and-swap has a known starting point, and the lease stamps are relative to the wall
    clock because the takeover gate is a real-time window.
    """

    HRITVIK = "hritvik"
    REVIEWER = "rev"

    def __init__(self):
        super().__init__()
        now = datetime.now(timezone.utc)
        live = iso(now + timedelta(hours=2))
        soon = iso(now - timedelta(seconds=20))
        lapsed = iso(now - timedelta(minutes=10))
        self.lease_live = live
        self.lease_inside_lock_delay = soon
        self.lease_expired = lapsed

        def claim(agent, epoch, expiry):
            return {"agent": agent, "epoch": epoch, "base_revision": 1,
                    "lease_expires": expiry, "heartbeat": iso(now)}

        self.add("demo/A-1", "ready", ["src/a1"], revision=1, epoch=1)
        self.add("demo/A-2", "claimed", ["src/a2"], revision=1, epoch=2,
                 claim=claim(self.HRITVIK, 2, live))
        self.add("demo/A-3", "green", ["src/a3"], revision=1, epoch=3,
                 claim=claim(self.HRITVIK, 3, live), evidence="evidence/demo-A-3.md")
        self.add("demo/A-4", "blocked", [], revision=1, epoch=1, blocked_needs="human")
        self.add("demo/A-5", "claimed", ["src/a5"], revision=1, epoch=1,
                 claim=claim("opencode/lapsed", 1, lapsed))
        self.add("demo/A-6", "claimed", ["src/a6"], revision=1, epoch=1,
                 claim=claim("opencode/lapsed", 1, soon))
        self.add("demo/A-7", "integrated", ["src/a7"], revision=1, epoch=1,
                 evidence="evidence/demo-A-7.md")
        self.add("demo/A-8", "green", ["src/a8"], revision=1, epoch=4,
                 claim=claim("opencode/other", 4, live))
        (self.repo / "evidence" / "demo-A-3.md").write_text("# A-3 evidence\n")
        (self.repo / "evidence" / "demo-A-7.md").write_text("# A-7 evidence\n")
        self.save()

        # A current review request for the reviewer identity, addressed on the green task.
        self.append(event="ask", task="demo/A-3", agent=self.HRITVIK, to=self.REVIEWER,
                    question="Review demo/A-3", revision=1, status="green",
                    review_source=SOURCE_B, review_base=SOURCE_A,
                    review_evidence="evidence/demo-A-3.md", now=T0)
        # An ordinary question addressed to the owner.
        self.append(event="ask", task="demo/A-1", agent="opencode/worker", to=self.HRITVIK,
                    question="Which acceptance wording do you want?", revision=1,
                    status="ready", now=T0)

    def add(self, task_id, status, paths=None, **extra):
        record = task(task_id, status, paths, **extra)
        record.setdefault("owner_repo", str(self.repo))
        self.registry["tasks"].append(record)
        return record

    def status_of(self, task_id: str) -> str:
        return next(item for item in self.load()["tasks"] if item["id"] == task_id)["status"]

    def record_of(self, task_id: str) -> dict:
        return next(item for item in self.load()["tasks"] if item["id"] == task_id)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
