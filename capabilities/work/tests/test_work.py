#!/usr/bin/env python3
"""End-to-end tests for the work-coordination CLI and validator.

Each test builds a disposable work home with a synthetic packet board, so the
real product checkouts are never touched.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

WORK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORK))
from store import SqliteStore  # noqa: E402
TASK = WORK / "task"
VALIDATOR = WORK / "validate_work.py"
T0 = "2030-01-01T10:00:00Z"


def run(*args, cwd=None):
    """Run the CLI against the fixture database selected by WORK_DB."""
    return subprocess.run(
        [sys.executable, str(TASK), *args],
        capture_output=True, text=True, cwd=cwd,
    )


def validate(root: Path, *extra):
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(root), *extra],
        capture_output=True, text=True,
    )


def packet(packet_id, status, paths, depends_on=None):
    return {
        "id": packet_id,
        "title": f"Packet {packet_id}",
        "kind": "implementation",
        "status": status,
        "packet": f"packets/{packet_id}.md",
        "evidence": f"evidence/{packet_id}.md",
        "depends_on": depends_on or [],
        "dependency_gate": "reviewed",
        "write_paths": paths,
        "shared_paths": [],
        "lock_groups": [],
        "execution_lock_groups": [],
        "claim": None,
        "checkpoint": None,
        "custom_note": "preserve me",
    }


class Workspace:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "work.db"
        self._previous_db = os.environ.get("WORK_DB")
        os.environ["WORK_DB"] = str(self.db)
        self.repo = self.root / "repo"
        (self.repo / "docs").mkdir(parents=True)

        self.board = {
            "schema_version": 1,
            "revision": 0,
            "project": "Demo board",
            "packets": [
                packet("W-1", "ready", ["src/one"]),
                packet("W-2", "ready", ["src/one"], ["W-1"]),
                packet("W-3", "draft", ["src/three"]),
                packet("W-4", "draft", ["src/four"]),
            ],
        }
        (self.repo / "docs" / "board.json").write_text(json.dumps(self.board, indent=2) + "\n")

        self.registry = {
            "kind": "work-coordination-registry",
            "version": 1,
            "updated": "2026-10-31",
            "boards": [{
                "id": "demo.board",
                "kind": "board",
                "role": "provider",
                "repo": str(self.repo),
                "path": "docs/board.json",
            }],
            "tasks": [
                self.task("demo/W-1", "ready", ["src/one"]),
                self.task("demo/W-2", "ready", ["src/one"], deps=["demo/W-1"]),
                self.task("demo/W-3", "draft", ["src/three"]),
                self.task("demo/W-4", "draft", ["src/four"]),
            ],
            "edges": [],
            "claims": [],
        }
        self.save()
        (self.root / "schemas").mkdir()
        shutil.copy(WORK / "schemas" / "task.schema.json", self.root / "schemas" / "task.schema.json")
        result = run("--root", str(self.root), "--agent", "setup", "genesis")
        assert result.returncode == 0, result.stderr

    def task(self, task_id, status, paths, deps=None):
        record = {
            "id": task_id,
            "board": "demo.board",
            "program": "demo",
            "owner_repo": str(self.repo),
            "title": f"Task {task_id}",
            "goal": "Fixture task",
            "status": status,
            "revision": 0,
        }
        if paths:
            record["write_paths"] = paths
        if deps:
            record["depends_on"] = deps
        return record

    def store(self):
        store = SqliteStore(self.root, self.db)
        store.registry = self.registry
        return store

    def save(self):
        store = self.store()
        try:
            store.save()
        finally:
            store.close()

    def load(self):
        store = SqliteStore(self.root, self.db)
        try:
            return store.load()
        finally:
            store.close()

    def task_record(self, task_id):
        return next(t for t in self.load()["tasks"] if t["id"] == task_id)

    def events(self):
        store = SqliteStore(self.root, self.db)
        try:
            return [record for _, record in store.read_events()]
        finally:
            store.close()

    def board_data(self):
        return json.loads((self.repo / "docs" / "board.json").read_text())

    def close(self):
        if self._previous_db is None:
            os.environ.pop("WORK_DB", None)
        else:
            os.environ["WORK_DB"] = self._previous_db
        self.tmp.cleanup()


class WorkCLITest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()

    def tearDown(self):
        self.ws.close()

    def invoke(self, *args, expect=0):
        result = run("--root", str(self.ws.root), *args)
        self.assertEqual(
            result.returncode, expect,
            f"exit {result.returncode} (expected {expect})\nstdout:{result.stdout}\nstderr:{result.stderr}",
        )
        return result

    # -- create -----------------------------------------------------------

    def test_create_is_central_and_validates(self):
        result = self.invoke(
            "create", "--agent", "agent-a", "--now", T0,
            "--id", "demo/W-5", "--board", "demo.board", "--title", "Fifth task",
            "--goal", "Prove central authoring", "--status", "ready",
            "--write-path", "src/five", "--depends-on", "demo/W-1",
        )
        self.assertIn("created demo/W-5", result.stdout)
        record = self.ws.task_record("demo/W-5")
        self.assertEqual(record["revision"], 0)
        self.assertEqual(record["program"], "demo")
        self.assertEqual(record["owner_repo"], str(self.ws.repo))

        duplicate = self.invoke(
            "create", "--agent", "agent-a", "--id", "demo/W-5", "--board", "demo.board",
            "--title", "Duplicate", expect=2,
        )
        self.assertIn("never reused", duplicate.stderr)

        unknown_dep = self.invoke(
            "create", "--agent", "agent-a", "--id", "demo/W-6", "--board", "demo.board",
            "--title", "Bad", "--depends-on", "demo/NOPE", expect=2,
        )
        self.assertIn("unknown task", unknown_dep.stderr)

    # -- claim / renew / release -----------------------------------------

    def test_claim_renew_release_with_cas(self):
        self.invoke("claim", "demo/W-1", "--revision", "0", "--ttl", "30m",
                    "--agent", "agent-a", "--now", T0)
        record = self.ws.task_record("demo/W-1")
        self.assertEqual(record["status"], "claimed")
        self.assertEqual(record["revision"], 1)
        self.assertEqual(record["claim"]["agent"], "agent-a")
        self.assertEqual(record["claim"]["lease_expires"], "2030-01-01T10:30:00Z")

        stale = self.invoke("claim", "demo/W-1", "--revision", "0", "--agent", "agent-b", expect=2)
        self.assertIn("revision conflict", stale.stderr)

        held = self.invoke("claim", "demo/W-1", "--revision", "1", "--agent", "agent-b", expect=2)
        self.assertIn("leased by agent-a", held.stderr)

        wrong_owner = self.invoke("renew", "demo/W-1", "--revision", "1", "--agent", "agent-b",
                                  "--now", "2030-01-01T10:10:00Z", expect=2)
        self.assertIn("claimed by 'agent-a'", wrong_owner.stderr)

        self.invoke("renew", "demo/W-1", "--revision", "1", "--ttl", "1h",
                    "--agent", "agent-a", "--now", "2030-01-01T10:10:00Z")
        record = self.ws.task_record("demo/W-1")
        self.assertEqual(record["revision"], 2)
        self.assertEqual(record["claim"]["lease_expires"], "2030-01-01T11:10:00Z")

        self.invoke("release", "demo/W-1", "--revision", "2", "--reason", "handing back",
                    "--agent", "agent-a", "--now", "2030-01-01T10:20:00Z")
        record = self.ws.task_record("demo/W-1")
        self.assertEqual(record["status"], "ready")
        self.assertIsNone(record["claim"])
        self.assertEqual(record["revision"], 3)

    def test_takeover_requires_expiry_and_reason(self):
        self.invoke("claim", "demo/W-1", "--revision", "0", "--agent", "agent-a", "--now", T0)

        early = self.invoke("takeover", "demo/W-1", "--revision", "1", "--reason", "stale",
                            "--agent", "agent-b", "--now", "2030-01-01T10:10:00Z", expect=2)
        self.assertIn("still live", early.stderr)

        no_reason = self.invoke("takeover", "demo/W-1", "--revision", "1",
                                "--agent", "agent-b", "--now", "2030-01-01T10:31:00Z", expect=2)
        self.assertIn("required", no_reason.stderr)

        self.invoke("takeover", "demo/W-1", "--revision", "1", "--reason", "agent-a went silent",
                    "--agent", "agent-b", "--now", "2030-01-01T10:31:00Z")
        record = self.ws.task_record("demo/W-1")
        self.assertEqual(record["claim"]["agent"], "agent-b")
        self.assertEqual(record["revision"], 2)
        last = self.ws.events()[-1]
        self.assertEqual(last["event"], "takeover")
        self.assertEqual(last["previous_agent"], "agent-a")
        self.assertEqual(last["reason"], "agent-a went silent")

    def test_write_path_conflict_blocks_second_claim(self):
        # Remove the dependency so only the path overlap can block the claim.
        self.ws.registry["tasks"][1].pop("depends_on")
        self.ws.save()
        self.invoke("claim", "demo/W-1", "--revision", "0", "--agent", "agent-a", "--now", T0)
        conflict = self.invoke("claim", "demo/W-2", "--revision", "0",
                               "--agent", "agent-b", "--now", T0, expect=2)
        self.assertIn("write-path conflict", conflict.stderr)
        self.assertIn("demo/W-1", conflict.stderr)

    def test_claim_blocks_on_unmet_dependency(self):
        result = self.invoke("claim", "demo/W-2", "--revision", "0",
                             "--agent", "agent-b", "--now", T0, expect=2)
        self.assertIn("unmet dependencies", result.stderr)
        self.assertIn("demo/W-1", result.stderr)

    # -- state machine ----------------------------------------------------

    def commit(self, message="Review candidate", *, path=None, content=None):
        def git(*args):
            return subprocess.run(
                ["git", "-C", str(self.ws.repo), *args],
                check=True, capture_output=True, text=True,
            ).stdout.strip()

        identity = (
            "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgSign=false", "-c", "core.hooksPath=/dev/null",
        )
        if not (self.ws.repo / ".git").exists():
            git("init", "-q", "-b", "main")
            git(*identity, "commit", "--allow-empty", "-qm", "Initial fixture")
        if path:
            target = self.ws.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            git("add", "--", path)
        git(*identity, "commit", "--allow-empty", "-qm", message)
        return git("rev-parse", "HEAD")

    def event_seq(self):
        return str(self.ws.events()[-1]["seq"])

    def request_review(self, source, reviewer="reviewer-r", task_id="demo/W-1", *,
                       base=None, evidence="evidence/W-1.md"):
        options = []
        if base:
            options.extend(("--review-base", base))
        if evidence:
            options.extend(("--review-evidence", evidence))
        self.invoke("ask", task_id, "--to", reviewer, "--question", "Review the submitted revision",
                    "--review-source", source, "--agent", "agent-b", "--now", T0, *options)
        return self.event_seq()

    def approve_review(self, request, reviewer="reviewer-r", task_id="demo/W-1"):
        self.invoke("answer", task_id, "--question", request, "--decision", "approve",
                    "--note", "Confirmed exact commit and regression checks",
                    "--agent", reviewer, "--now", T0)
        return self.event_seq()

    def mark_green(self, *options):
        self.invoke("claim", "demo/W-1", "--revision", "0", "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "1", "--to", "green",
                    "--agent", "agent-a", "--now", T0, *options)


    def test_reviewer_cannot_be_an_earlier_implementer(self):
        self.invoke("claim", "demo/W-1", "--revision", "0", "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "1", "--to", "red",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "2", "--to", "green",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("release", "demo/W-1", "--revision", "3", "--reason", "handing over",
                    "--agent", "agent-a", "--now", T0)

        self.invoke("claim", "demo/W-1", "--revision", "4", "--agent", "agent-b", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "5", "--to", "red",
                    "--agent", "agent-b", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "6", "--to", "green",
                    "--agent", "agent-b", "--now", T0)

        laundering = self.invoke(
            "transition", "demo/W-1", "--revision", "7", "--to", "reviewed",
            "--reviewer", "agent-a", "--decision", "approve",
            "--agent", "agent-b", "--now", T0, expect=2,
        )
        self.assertIn("held a claim on demo/W-1", laundering.stderr)

        approval = self.approve_review(self.request_review(self.commit()))
        self.invoke("transition", "demo/W-1", "--revision", "7", "--to", "reviewed",
                    "--reviewer", "reviewer-r", "--decision", "approve",
                    "--review-answer", approval, "--evidence", "evidence/W-1.md",
                    "--agent", "agent-b", "--now", T0)

    def test_acting_agent_cannot_name_itself_as_reviewer(self):
        registry = self.ws.registry
        record = next(t for t in registry["tasks"] if t["id"] == "demo/W-4")
        record["status"] = "green"
        record["revision"] = 1
        self.ws.save()

        self_review = self.invoke(
            "transition", "demo/W-4", "--revision", "1", "--to", "reviewed",
            "--reviewer", "agent-a", "--decision", "approve",
            "--agent", "agent-a", "--now", T0, expect=2,
        )
        self.assertIn("cannot name itself as reviewer", self_review.stderr)

    def test_full_state_machine_and_dependency_release(self):
        self.invoke("claim", "demo/W-1", "--revision", "0", "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "1", "--to", "red",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "2", "--to", "green",
                    "--agent", "agent-a", "--now", T0)

        same_agent = self.invoke(
            "transition", "demo/W-1", "--revision", "3", "--to", "reviewed",
            "--reviewer", "agent-a", "--decision", "approve",
            "--agent", "agent-a", "--now", T0, expect=2,
        )
        self.assertIn("independent", same_agent.stderr)

        source = self.commit()
        approval = self.approve_review(self.request_review(source))
        self.invoke("transition", "demo/W-1", "--revision", "3", "--to", "reviewed",
                    "--reviewer", "reviewer-r", "--decision", "approve",
                    "--review-answer", approval, "--evidence", "evidence/W-1.md",
                    "--agent", "agent-a", "--now", T0)
        record = self.ws.task_record("demo/W-1")
        self.assertEqual(record["status"], "reviewed")
        self.assertIsNone(record["claim"])

        missing_evidence = self.invoke(
            "transition", "demo/W-1", "--revision", "4", "--to", "integrated",
            "--agent", "agent-a", "--now", T0, expect=2,
        )
        self.assertIn("evidence", missing_evidence.stderr)

        self.invoke("transition", "demo/W-1", "--revision", "4", "--to", "integrated",
                    "--evidence", "evidence/W-1.md", "--integrated-source", source,
                    "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "5", "--to", "accepted",
                    "--evidence", "evidence/W-1.md", "--integrated-source", source,
                    "--agent", "agent-a", "--now", T0)
        self.assertEqual(self.ws.task_record("demo/W-1")["status"], "accepted")

        self.invoke("claim", "demo/W-2", "--revision", "0", "--agent", "agent-b", "--now", T0)
        self.assertEqual(self.ws.task_record("demo/W-2")["status"], "claimed")

        illegal = self.invoke("transition", "demo/W-2", "--revision", "1", "--to", "accepted",
                              "--evidence", "x", "--agent", "agent-b", "--now", T0, expect=2)
        self.assertIn("illegal transition", illegal.stderr)

    def test_review_requires_an_approval_for_the_latest_submission(self):
        self.mark_green("--evidence", "evidence/W-1.md")
        source = self.commit()
        review_args = (
            "transition", "demo/W-1", "--revision", "2", "--to", "reviewed",
            "--reviewer", "reviewer-r", "--decision", "approve", "--agent", "agent-b", "--now", T0,
        )
        self.invoke(*review_args, expect=2)

        old_approval = self.approve_review(self.request_review(source))
        request = self.request_review(self.commit("Second submission"))
        self.invoke(*review_args, "--review-answer", old_approval, expect=2)
        self.invoke(*review_args, "--review-answer", request, expect=2)
        approval = self.approve_review(request)
        self.invoke(*review_args, "--review-answer", approval)

    def test_approval_from_an_earlier_green_revision_is_stale(self):
        self.mark_green()
        approval = self.approve_review(self.request_review(self.commit()))
        self.invoke("transition", "demo/W-1", "--revision", "2", "--to", "red",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "3", "--to", "green",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "4", "--to", "reviewed",
                    "--reviewer", "reviewer-r", "--decision", "approve",
                    "--review-answer", approval, "--evidence", "evidence/W-1.md",
                    "--agent", "agent-b", "--now", T0, expect=2)
        self.assertEqual(self.ws.task_record("demo/W-1")["status"], "green")

    def test_review_rejects_mismatched_reviewer_and_non_approval(self):
        self.mark_green()
        source = self.commit()
        request = self.request_review(source)
        self.invoke("answer", "demo/W-1", "--question", request, "--decision", "changes requested",
                    "--note", "Regression found", "--agent", "reviewer-r", "--now", T0)
        review_args = (
            "transition", "demo/W-1", "--revision", "2", "--to", "reviewed",
            "--decision", "approve", "--agent", "agent-b", "--now", T0,
        )
        self.invoke(*review_args, "--reviewer", "reviewer-r",
                    "--review-answer", self.event_seq(), expect=2)

        self.invoke("ask", "demo/W-1", "--to", "other-reviewer", "--question", "New reviewer",
                    "--review-source", source, "--review-evidence", "evidence/W-1.md",
                    "--agent", "agent-a", "--now", T0, expect=2)
        self.invoke("renew", "demo/W-1", "--revision", "2", "--ttl", "1h",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("ask", "demo/W-1", "--to", "other-reviewer", "--question", "Renewed lease",
                    "--review-source", source, "--review-evidence", "evidence/W-1.md",
                    "--agent", "agent-a", "--now", T0, expect=2)
        self.invoke("transition", "demo/W-1", "--revision", "3", "--to", "red",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "4", "--to", "green",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("ask", "demo/W-1", "--to", "other-reviewer", "--question", "Same rejected commit",
                    "--review-source", source, "--review-evidence", "evidence/W-1.md",
                    "--agent", "agent-a", "--now", T0, expect=2)
        approval = self.approve_review(self.request_review(self.commit("Address reviewer findings")))
        self.invoke("transition", "demo/W-1", "--revision", "5", "--to", "reviewed",
                    "--reviewer", "other-reviewer", "--decision", "approve", "--review-answer", approval,
                    "--agent", "agent-b", "--now", T0, expect=2)

    def test_unstructured_and_other_task_approvals_do_not_authorize_review(self):
        self.mark_green("--evidence", "evidence/W-1.md")
        self.ws.registry = self.ws.load()
        self.ws.registry["tasks"][3].update(status="green", revision=2)
        self.ws.save()
        source = self.commit()
        self.invoke("ask", "demo/W-1", "--to", "reviewer-r", "--question", "Ordinary question",
                    "--agent", "agent-b", "--now", T0)
        ordinary = self.approve_review(self.event_seq())
        other = self.approve_review(self.request_review(source, task_id="demo/W-4"),
                                    task_id="demo/W-4")
        review = ("transition", "demo/W-1", "--revision", "2", "--to", "reviewed",
                  "--reviewer", "reviewer-r", "--decision", "approve",
                  "--agent", "agent-b", "--now", T0)
        self.invoke(*review, "--review-answer", ordinary, expect=2)
        self.invoke(*review, "--review-answer", other, expect=2)

    def test_integration_requires_the_approved_source_on_integration_branch(self):
        self.mark_green()
        earlier = self.commit("Earlier source")
        approved = self.commit("Approved source")
        approval = self.approve_review(self.request_review(approved))
        self.invoke("transition", "demo/W-1", "--revision", "2", "--to", "reviewed",
                    "--reviewer", "reviewer-r", "--decision", "approve",
                    "--review-answer", approval, "--evidence", "evidence/W-1.md",
                    "--agent", "agent-b", "--now", T0)
        integrated = (
            "transition", "demo/W-1", "--revision", "3", "--to", "integrated",
            "--evidence", "evidence/W-1.md", "--agent", "agent-a", "--now", T0,
        )
        self.invoke(*integrated, expect=2)
        self.invoke(*integrated, "--integrated-source", earlier, expect=2)
        subprocess.run(["git", "-C", str(self.ws.repo), "checkout", "-B", "main", earlier],
                       check=True, capture_output=True)
        self.invoke(*integrated, "--integrated-source", approved, expect=2)
        subprocess.run(["git", "-C", str(self.ws.repo), "merge", "--ff-only", approved],
                       check=True, capture_output=True)
        self.invoke(*integrated, "--integrated-source", approved)

    def test_integration_rejects_later_changes_to_approved_paths(self):
        self.mark_green()
        self.commit("Base")
        approved = self.commit("Approved source", path="src/one/value.txt", content="reviewed")
        approval = self.approve_review(self.request_review(approved))
        self.invoke("transition", "demo/W-1", "--revision", "2", "--to", "reviewed",
                    "--reviewer", "reviewer-r", "--decision", "approve",
                    "--review-answer", approval, "--evidence", "evidence/W-1.md",
                    "--agent", "agent-b", "--now", T0)
        unrelated = self.commit("Unrelated change", path="src/two/value.txt", content="elsewhere")
        self.commit("Unreviewed change", path="src/one/value.txt", content="changed after approval")
        integrated = (
            "transition", "demo/W-1", "--revision", "3", "--to", "integrated",
            "--evidence", "evidence/W-1.md", "--integrated-source", approved,
            "--agent", "agent-a", "--now", T0,
        )
        self.invoke(*integrated, expect=2)
        subprocess.run(["git", "-C", str(self.ws.repo), "checkout", "-B", "main", unrelated],
                       check=True, capture_output=True)
        self.invoke(*integrated)

    def test_multicommit_review_checks_paths_from_the_whole_range(self):
        self.mark_green()
        base = self.commit("Base")
        earlier = "src/one/earlier.txt"
        self.commit("Earlier change", path=earlier, content="approved")
        source = self.commit("Later change", path="src/one/later.txt", content="approved")
        approval = self.approve_review(self.request_review(source, base=base))
        self.invoke("transition", "demo/W-1", "--revision", "2", "--to", "reviewed",
                    "--reviewer", "reviewer-r", "--decision", "approve",
                    "--review-answer", approval, "--agent", "agent-b", "--now", T0)
        self.commit("Unreviewed change to earlier path", path=earlier, content="changed")
        integrated = (
            "transition", "demo/W-1", "--revision", "3", "--to", "integrated",
            "--evidence", "evidence/W-1.md", "--integrated-source", source,
            "--agent", "agent-a", "--now", T0,
        )
        self.invoke(*integrated, expect=2)
        self.commit("Restore approved content", path=earlier, content="approved")
        self.invoke(*integrated)

    def test_integration_uses_main_and_rechecks_dependencies(self):
        self.mark_green()
        self.commit("Main before review")
        subprocess.run(["git", "-C", str(self.ws.repo), "checkout", "-b", "feature"],
                       check=True, capture_output=True)
        source = self.commit("Feature change")
        approval = self.approve_review(self.request_review(source))
        self.invoke("transition", "demo/W-1", "--revision", "2", "--to", "reviewed",
                    "--reviewer", "reviewer-r", "--decision", "approve",
                    "--review-answer", approval, "--agent", "agent-b", "--now", T0)
        integrated = (
            "transition", "demo/W-1", "--revision", "3", "--to", "integrated",
            "--evidence", "evidence/W-1.md", "--integrated-source", source,
            "--agent", "agent-a", "--now", T0,
        )
        self.invoke(*integrated, expect=2)
        subprocess.run(["git", "-C", str(self.ws.repo), "checkout", "main"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.ws.repo), "merge", "--ff-only", source],
                       check=True, capture_output=True)
        self.invoke("edge", "add", "--from", "demo/W-1", "--to", "demo/W-3",
                    "--agent", "agent-b", "--now", T0)
        self.invoke(*integrated, expect=2)
        self.invoke("edge", "remove", "--from", "demo/W-1", "--to", "demo/W-3",
                    "--agent", "agent-b", "--now", T0)
        self.invoke(*integrated)

    def test_historical_review_without_receipt_must_be_reopened(self):
        self.ws.registry["tasks"][0].update(
            status="reviewed", revision=1, review={"reviewer": "old-reviewer", "decision": "approve"},
        )
        self.ws.save()
        self.invoke("transition", "demo/W-1", "--revision", "1", "--to", "integrated",
                    "--evidence", "evidence/W-1.md", "--agent", "agent-a", "--now", T0, expect=2)
        self.invoke("transition", "demo/W-1", "--revision", "1", "--to", "ready",
                    "--reason", "Collect a structured approval", "--agent", "agent-a", "--now", T0)
        self.assertIsNone(self.ws.task_record("demo/W-1").get("review"))

    def test_imported_in_progress_task_cannot_bypass_review_on_acceptance(self):
        self.ws.registry["tasks"][0].update(status="in-progress")
        self.ws.save()
        self.invoke("transition", "demo/W-1", "--revision", "0", "--to", "accepted",
                    "--evidence", "evidence/W-1.md", "--agent", "agent-a", "--now", T0, expect=2)

    def test_reopening_review_clears_approval_and_requires_resubmission(self):
        self.mark_green("--evidence", "evidence/W-1.md")
        source = self.commit()
        approval = self.approve_review(self.request_review(source))
        self.invoke("transition", "demo/W-1", "--revision", "2", "--to", "reviewed",
                    "--reviewer", "reviewer-r", "--decision", "approve",
                    "--review-answer", approval, "--agent", "agent-b", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "3", "--to", "ready",
                    "--reason", "Source changed", "--agent", "agent-a", "--now", T0)
        self.assertIsNone(self.ws.task_record("demo/W-1").get("review"))
        self.invoke("claim", "demo/W-1", "--revision", "4", "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "5", "--to", "green",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "6", "--to", "reviewed",
                    "--reviewer", "reviewer-r", "--decision", "approve",
                    "--review-answer", approval, "--agent", "agent-b", "--now", T0, expect=2)

    def test_review_request_requires_a_real_full_commit(self):
        self.mark_green()
        source = self.commit()
        request = ("ask", "demo/W-1", "--to", "reviewer-r", "--question", "Review source",
                   "--agent", "agent-b", "--now", T0, "--review-evidence", "evidence/W-1.md",
                   "--review-source")
        self.invoke(*request, source[:7], expect=2)
        self.invoke(*request, "0" * len(source), expect=2)
        self.invoke(*request, source)

    def test_review_binds_evidence_and_requires_reviewer_note(self):
        self.mark_green()
        source = self.commit()
        evidence = "evidence/W-1.md"
        self.invoke("ask", "demo/W-1", "--to", "reviewer-r", "--question", "Review source",
                    "--review-source", source, "--agent", "agent-b", "--now", T0, expect=2)
        request = self.request_review(source, evidence=evidence)
        self.invoke("answer", "demo/W-1", "--question", request, "--decision", "approve",
                    "--note", " ", "--agent", "reviewer-r", "--now", T0, expect=2)
        approval = self.approve_review(request)
        review = ("transition", "demo/W-1", "--revision", "2", "--to", "reviewed",
                  "--reviewer", "reviewer-r", "--decision", "approve",
                  "--review-answer", approval, "--agent", "agent-b", "--now", T0)
        self.invoke(*review, "--evidence", "evidence/unreviewed.md", expect=2)
        self.invoke(*review)
        self.assertEqual(self.ws.task_record("demo/W-1")["evidence"], evidence)

    def test_review_submission_can_inherit_green_evidence(self):
        evidence = "evidence/W-1.md"
        self.mark_green("--evidence", evidence)
        approval = self.approve_review(self.request_review(self.commit(), evidence=None))
        self.invoke("transition", "demo/W-1", "--revision", "2", "--to", "reviewed",
                    "--reviewer", "reviewer-r", "--decision", "approve",
                    "--review-answer", approval, "--agent", "agent-b", "--now", T0)
        self.assertEqual(self.ws.task_record("demo/W-1")["evidence"], evidence)

    def test_claim_requires_ready_state(self):
        result = self.invoke("claim", "demo/W-3", "--revision", "0",
                             "--agent", "agent-a", "--now", T0, expect=2)
        self.assertIn("status is 'draft'", result.stderr)

    def test_concurrent_claims_only_one_wins(self):
        from concurrent.futures import ThreadPoolExecutor

        def attempt(index):
            return run("--root", str(self.ws.root), "claim", "demo/W-1",
                       "--revision", "0", "--agent", f"agent-{index}", "--now", T0)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))

        winners = [result for result in results if result.returncode == 0]
        self.assertEqual(len(winners), 1, [r.stderr for r in results])
        self.assertTrue(all(result.returncode in {0, 2} for result in results))
        record = self.ws.task_record("demo/W-1")
        self.assertEqual(record["status"], "claimed")
        self.assertEqual(record["revision"], 1)

        events = self.ws.events()
        claims = [event for event in events if event.get("event") == "claim"]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["agent"], record["claim"]["agent"])
        self.invoke("events", "--verify")

    # -- event log --------------------------------------------------------

    def test_event_chain_detects_tampering(self):
        self.invoke("claim", "demo/W-1", "--revision", "0", "--agent", "agent-a", "--now", T0)
        verified = self.invoke("events", "--verify")
        self.assertIn("event chain OK", verified.stdout)
        self.assertEqual(validate(self.ws.root).returncode, 0)

        connection = sqlite3.connect(self.ws.db)
        seq, body = connection.execute(
            "SELECT seq, body FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        record = json.loads(body)
        record["status"] = "accepted"
        connection.execute(
            "UPDATE events SET body = ? WHERE seq = ?",
            (json.dumps(record, sort_keys=True, separators=(",", ":")), seq))
        connection.commit()
        connection.close()

        tampered = self.invoke("events", "--verify", expect=1)
        self.assertIn("event chain FAIL", tampered.stdout)
        self.assertNotEqual(validate(self.ws.root).returncode, 0)

    # -- board materialisation -------------------------------------------

    def test_board_sync_creates_missing_packets_and_preserves_fields(self):
        board = self.ws.board_data()
        board["packets"] = [p for p in board["packets"] if p["id"] != "W-2"]
        (self.ws.repo / "docs" / "board.json").write_text(json.dumps(board, indent=2) + "\n")

        check = self.invoke("board", "sync", "--board", "demo.board", "--check", expect=1)
        self.assertIn("create packet W-2", check.stdout)
        self.assertIn("update central block on W-1", check.stdout)

        self.invoke("board", "sync", "--board", "demo.board", "--now", T0)
        synced = self.ws.board_data()
        by_id = {p["id"]: p for p in synced["packets"]}
        self.assertIn("W-2", by_id)
        self.assertEqual(by_id["W-2"]["write_paths"], ["src/one"])
        self.assertEqual(by_id["W-2"]["status"], "ready")
        self.assertEqual(by_id["W-1"]["custom_note"], "preserve me")
        self.assertEqual(by_id["W-1"]["central"]["task"], "demo/W-1")
        self.assertEqual(by_id["W-1"]["central"]["revision"], 0)

        self.invoke("board", "sync", "--board", "demo.board", "--check")
        self.assertEqual(validate(self.ws.root).returncode, 0)

    def test_board_sync_materialises_packet_status(self):
        board = self.ws.board_data()
        next(p for p in board["packets"] if p["id"] == "W-1")["status"] = "draft"
        (self.ws.repo / "docs" / "board.json").write_text(json.dumps(board, indent=2) + "\n")

        check = self.invoke("board", "sync", "--board", "demo.board", "--check", expect=1)
        self.assertIn("update status on W-1 (draft -> ready)", check.stdout)

        self.invoke("board", "sync", "--board", "demo.board", "--now", T0)
        synced = {p["id"]: p for p in self.ws.board_data()["packets"]}
        self.assertEqual(synced["W-1"]["status"], "ready")

        self.invoke("board", "sync", "--board", "demo.board", "--check")
        self.assertEqual(validate(self.ws.root).returncode, 0)

    def test_board_sync_reports_imported_drift_instead_of_overwriting(self):
        board = self.ws.board_data()
        board["packets"].append(packet("W-5", "ready", ["src/five"]))
        (self.ws.repo / "docs" / "board.json").write_text(json.dumps(board, indent=2) + "\n")
        self.invoke("board", "import", "--board", "demo.board", "--program", "demo",
                    "--agent", "agent-a", "--now", T0)

        board = self.ws.board_data()
        next(p for p in board["packets"] if p["id"] == "W-5")["status"] = "green"
        (self.ws.repo / "docs" / "board.json").write_text(json.dumps(board, indent=2) + "\n")

        check = self.invoke("board", "sync", "--board", "demo.board", "--check", expect=1)
        self.assertIn("report status drift on W-5 (board green vs central ready)", check.stdout)

        self.invoke("board", "sync", "--board", "demo.board", "--now", T0)
        synced = {p["id"]: p for p in self.ws.board_data()["packets"]}
        self.assertEqual(synced["W-5"]["status"], "green")

    # -- messages ---------------------------------------------------------

    def test_ask_inbox_answer_round_trip(self):
        asked = self.invoke("ask", "demo/W-1", "--to", "agent-b",
                            "--question", "Which fixture is authoritative?",
                            "--needed-by", "2026-11-07", "--agent", "agent-a", "--now", T0)
        self.assertIn("asked agent-b about demo/W-1", asked.stdout)

        inbox = self.invoke("inbox", "--agent", "agent-b", "--json")
        questions = json.loads(inbox.stdout)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["question"], "Which fixture is authoritative?")
        self.assertEqual(questions[0]["neededBy"], "2026-11-07")
        self.assertFalse(questions[0]["answered"])
        seq = questions[0]["seq"]

        self.assertEqual(self.invoke("inbox", "--agent", "agent-c").stdout.strip(),
                         "inbox empty for agent-c")

        answered = self.invoke("answer", "demo/W-1", "--question", str(seq),
                               "--decision", "Use src/one", "--note", "checked the board",
                               "--agent", "agent-b", "--now", T0)
        self.assertIn("answered agent-a on demo/W-1", answered.stdout)

        self.assertEqual(self.invoke("inbox", "--agent", "agent-b").stdout.strip(),
                         "inbox empty for agent-b")
        closed = json.loads(self.invoke("inbox", "--agent", "agent-b", "--all", "--json").stdout)
        self.assertTrue(closed[0]["answered"])

        events = self.invoke("events", "--task", "demo/W-1")
        self.assertIn("to agent-b: Which fixture is authoritative?", events.stdout)
        self.assertIn(f"re {seq}: Use src/one", events.stdout)
        self.invoke("events", "--verify")
        self.assertEqual(validate(self.ws.root).returncode, 0)

    def test_only_the_addressee_may_answer_once(self):
        self.invoke("ask", "demo/W-1", "--to", "agent-b", "--question", "Which fixture?",
                    "--agent", "agent-a", "--now", T0)
        seq = json.loads(self.invoke("inbox", "--agent", "agent-b", "--json").stdout)[0]["seq"]

        wrong = self.invoke("answer", "demo/W-1", "--question", str(seq), "--decision", "mine",
                            "--agent", "agent-c", expect=2)
        self.assertIn("addressed to agent-b", wrong.stderr)

        wrong_task = self.invoke("answer", "demo/W-2", "--question", str(seq),
                                 "--decision", "mine", "--agent", "agent-b", expect=2)
        self.assertIn("asks about demo/W-1", wrong_task.stderr)

        self.invoke("answer", "demo/W-1", "--question", str(seq), "--decision", "Use src/one",
                    "--agent", "agent-b", "--now", T0)
        twice = self.invoke("answer", "demo/W-1", "--question", str(seq),
                            "--decision", "changed my mind", "--agent", "agent-b", expect=2)
        self.assertIn("already answered", twice.stderr)

    def test_ask_requires_a_distinct_addressee_and_agent(self):
        self.invoke("ask", "demo/W-1", "--to", "agent-a", "--question", "Self?", "--agent",
                    "agent-a", expect=2)
        self.invoke("ask", "demo/W-1", "--to", "agent-b", "--question", "   ", "--agent",
                    "agent-a", expect=2)
        self.invoke("ask", "demo/W-1", "--to", "agent-b", "--question", "Who?", expect=2)
        self.invoke("inbox", expect=2)

    # -- discovery --------------------------------------------------------

    def test_search_finds_tasks_without_knowing_the_namespace(self):
        self.invoke("create", "--agent", "agent-a", "--now", T0,
                    "--id", "demo/W-7", "--board", "demo.board",
                    "--title", "Avatar attachment reference", "--status", "ready",
                    "--goal", "Store the asset reference", "--write-path", "src/seven")
        found = self.invoke("search", "avatar")
        self.assertIn("demo/W-7", found.stdout)
        self.assertNotIn("demo/W-1 ", found.stdout)

        narrowed = self.invoke("search", "avatar", "asset")
        self.assertIn("demo/W-7", narrowed.stdout)

        self.assertIn("no tasks match", self.invoke("search", "nothing-here").stdout)
        self.invoke("search", expect=2)

        as_json = json.loads(self.invoke("search", "avatar", "--json").stdout)
        self.assertEqual([task["id"] for task in as_json], ["demo/W-7"])

    def test_dependents_and_ready_ranking(self):
        blocked = self.invoke("dependents", "demo/W-1")
        self.assertIn("demo/W-2", blocked.stdout)
        self.assertIn("blocks 1 directly", blocked.stdout)

        self.assertIn("blocks nothing", self.invoke("dependents", "demo/W-4").stdout)

        ready = self.invoke("list", "--ready")
        self.assertIn("demo/W-1", ready.stdout)
        self.assertIn("+1 downstream", ready.stdout)

        detail = self.invoke("show", "demo/W-1")
        self.assertIn("blocks:   demo/W-2", detail.stdout)

    def test_expired_lease_does_not_block_a_write_path(self):
        self.invoke("create", "--agent", "agent-a", "--now", T0,
                    "--id", "demo/W-8", "--board", "demo.board", "--title", "Second src/one",
                    "--status", "ready", "--write-path", "src/one")

        self.invoke("claim", "demo/W-1", "--revision", "0", "--ttl", "1m",
                    "--agent", "agent-a", "--now", T0)
        live = self.invoke("claim", "demo/W-8", "--revision", "0", "--ttl", "1m",
                           "--agent", "agent-b", "--now", T0, expect=2)
        self.assertIn("write-path conflict", live.stderr)

        after = self.invoke("claim", "demo/W-8", "--revision", "0", "--ttl", "1m",
                            "--agent", "agent-b", "--now", "2030-01-01T10:05:00Z")
        self.assertIn("claimed demo/W-8", after.stdout)

    def test_blocking_a_claim_releases_the_lease(self):
        self.invoke("claim", "demo/W-1", "--revision", "0", "--ttl", "1m",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "1", "--to", "blocked",
                    "--needs", "human", "--reason", "owner must name a provider",
                    "--agent", "agent-a", "--now", T0)
        record = self.ws.task_record("demo/W-1")
        self.assertEqual(record["status"], "blocked")
        self.assertIsNone(record.get("claim"))

    def test_blocked_records_who_can_resolve_it(self):
        self.invoke("transition", "demo/W-3", "--revision", "0", "--to", "blocked",
                    "--needs", "human", "--reason", "owner must decide", "--agent", "agent-a",
                    "--now", T0)
        human = self.invoke("list", "--awaiting", "human")
        self.assertIn("demo/W-3", human.stdout)
        self.assertNotIn("demo/W-3", self.invoke("list", "--awaiting", "agent").stdout)

        self.invoke("transition", "demo/W-3", "--revision", "1", "--to", "ready",
                    "--agent", "agent-a", "--now", T0)
        self.assertIsNone(self.ws.task_record("demo/W-3").get("blocked_needs"))

    def test_board_titles_backfills_missing_titles(self):
        (self.ws.repo / "docs" / "work-items.md").write_text(
            "## W-9 — Ship the thing\n\n**Status.** ready\n", encoding="utf-8")
        registry = self.ws.registry
        registry["boards"].append({
            "id": "demo.program", "kind": "program", "role": "provider",
            "repo": str(self.ws.repo), "path": "docs/work-items.md",
        })
        registry["tasks"].append({
            "id": "demo/W-9", "board": "demo.program", "program": "demo",
            "owner_repo": str(self.ws.repo), "status": "ready", "revision": 0,
            "status_source": "manual",
        })
        self.ws.save()

        check = self.invoke("board", "titles", "--board", "demo.program", "--check", expect=1)
        self.assertIn("would set demo/W-9: title -> Ship the thing", check.stdout)

        self.invoke("board", "titles", "--board", "demo.program", "--now", T0)
        self.assertEqual(self.ws.task_record("demo/W-9")["title"], "Ship the thing")

    # -- graph ------------------------------------------------------------

    def test_graph_text_shows_blockers_and_chains(self):
        self.invoke("transition", "demo/W-3", "--revision", "0", "--to", "blocked",
                    "--needs", "human", "--reason", "owner must decide", "--agent", "agent-a",
                    "--now", T0)
        text = self.invoke("graph", "--open").stdout
        self.assertIn("Blocked, and what each one releases:", text)
        self.assertIn("demo/W-3", text)
        self.assertIn("needs human", text)
        self.assertIn("Chains", text)
        self.assertIn("demo/W-1", text)
        self.assertIn("-> demo/W-2", text)

    def test_graph_formats_and_focus(self):
        mermaid = self.invoke("graph", "--format", "mermaid").stdout
        self.assertTrue(mermaid.startswith("graph LR"))
        self.assertIn("n_demo_W-1", mermaid)
        self.assertIn("-->", mermaid)
        self.assertIn("classDef", mermaid)

        dot = self.invoke("graph", "--format", "dot").stdout
        self.assertIn("digraph work {", dot)
        self.assertIn('"demo/W-1" -> "demo/W-2"', dot)

        out = self.ws.root / "graph.html"
        written = self.invoke("graph", "--format", "html", "--out", str(out)).stdout
        self.assertIn("wrote", written)
        html = out.read_text(encoding="utf-8")
        self.assertIn("<svg", html)
        self.assertIn("demo/W-1", html)
        self.assertIn("class=\"node\"", html)

        focus = self.invoke("graph", "--task", "demo/W-1", "--format", "dot").stdout
        self.assertIn('"demo/W-1"', focus)
        self.assertNotIn('"demo/W-3"', focus)

    # -- coordination policy ----------------------------------------------

    def test_decisions_lists_only_the_owner_queue(self):
        self.invoke("transition", "demo/W-3", "--revision", "0", "--to", "blocked",
                    "--needs", "human", "--reason", "owner must decide", "--agent", "agent-a",
                    "--now", T0)
        self.invoke("ask", "demo/W-1", "--to", "owner", "--question", "Which fixture wins?",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("ask", "demo/W-1", "--to", "agent-b", "--question", "Not for the owner",
                    "--agent", "agent-a", "--now", T0)

        out = self.invoke("decisions").stdout
        self.assertIn("Decision queue for owner: 1 decision(s), 1 question(s)", out)
        self.assertIn("demo/W-3", out)
        self.assertIn("owner must decide", out)
        self.assertIn("Which fixture wins?", out)
        self.assertNotIn("Not for the owner", out)

        seq = json.loads(self.invoke("inbox", "--agent", "owner", "--json").stdout)[0]["seq"]
        self.invoke("answer", "demo/W-1", "--question", str(seq), "--decision", "src/one",
                    "--agent", "owner", "--now", T0)
        after = self.invoke("decisions").stdout
        self.assertNotIn("Which fixture wins?", after)

        as_json = json.loads(self.invoke("decisions", "--json").stdout)
        self.assertEqual([item["task"] for item in as_json["decisions"]], ["demo/W-3"])
        self.assertEqual(as_json["questions"], [])

    def test_tick_respects_capacity_and_ranks_by_unblocks(self):
        self.invoke("create", "--agent", "agent-a", "--now", T0,
                    "--id", "demo/W-5", "--board", "demo.board", "--title", "Second ready",
                    "--status", "ready", "--write-path", "src/five")

        preview = self.invoke("tick", "--max-implementer", "1").stdout
        self.assertIn("capacity: implementer 0/1", preview)
        self.assertIn("demo/W-1", preview)
        self.assertIn("+1 downstream", preview)
        self.assertIn("no implementer slot (limit 1): demo/W-5", preview)
        self.assertIn("Preview only", preview)

        chosen = json.loads(self.invoke("tick", "--json", "--max-implementer", "1").stdout)["chosen"]
        self.assertEqual([item["task"] for item in chosen], ["demo/W-1"])

        wide = json.loads(self.invoke("tick", "--json", "--max-implementer", "5").stdout)["chosen"]
        self.assertEqual({item["task"] for item in wide}, {"demo/W-1", "demo/W-5"})

    def test_tick_reports_expired_leases(self):
        self.invoke("claim", "demo/W-1", "--revision", "0", "--ttl", "1m",
                    "--agent", "agent-a", "--now", T0)
        later = self.invoke("tick", "--now", "2030-01-01T10:30:00Z").stdout
        self.assertIn("Expired leases to release or take over:", later)
        self.assertIn("demo/W-1", later)
        self.assertIn("agent-a", later)

    def test_green_does_not_hold_a_write_path(self):
        self.invoke("claim", "demo/W-1", "--revision", "0", "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "1", "--to", "red",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("transition", "demo/W-1", "--revision", "2", "--to", "green",
                    "--agent", "agent-a", "--now", T0)

        self.invoke("create", "--agent", "agent-a", "--now", T0,
                    "--id", "demo/W-6", "--board", "demo.board", "--title", "Overlapping path",
                    "--status", "ready", "--write-path", "src/one")
        claimed = self.invoke("claim", "demo/W-6", "--revision", "0", "--ttl", "45m",
                              "--agent", "agent-b", "--now", T0)
        self.assertIn("claimed demo/W-6", claimed.stdout)

    def test_board_add_and_remove(self):
        (self.ws.repo / "docs" / "program.md").write_text(
            "## W-1 — Thing\n\n**Status.** ready\n", encoding="utf-8")
        added = self.invoke("board", "add", "--board", "demo.program", "--repo", str(self.ws.repo),
                            "--path", "docs/program.md", "--agent", "agent-a", "--now", T0)
        self.assertIn("added board demo.program", added.stdout)
        self.assertIn("demo.program", self.invoke("board", "list").stdout)

        duplicate = self.invoke("board", "add", "--board", "demo.program", "--repo",
                                str(self.ws.repo), "--path", "docs/program.md",
                                "--agent", "agent-a", expect=2)
        self.assertIn("already exists", duplicate.stderr)

        missing = self.invoke("board", "add", "--board", "demo.other", "--repo", str(self.ws.repo),
                              "--path", "docs/nope.md", "--agent", "agent-a", expect=2)
        self.assertIn("board file missing", missing.stderr)

        removed = self.invoke("board", "remove", "--board", "demo.program", "--agent", "agent-a",
                              "--now", T0)
        self.assertIn("removed board demo.program", removed.stdout)
        self.assertNotIn("demo.program", self.invoke("board", "list").stdout)

    def test_board_import_registers_unlisted_packets(self):
        board = self.ws.board_data()
        board["packets"].append(packet("W-5", "ready", ["src/five"]))
        (self.ws.repo / "docs" / "board.json").write_text(json.dumps(board, indent=2) + "\n")

        warned = validate(self.ws.root)
        self.assertEqual(warned.returncode, 0)
        self.assertIn("not registered centrally", warned.stdout)

        self.invoke("board", "import", "--board", "demo.board", "--program", "demo",
                    "--agent", "agent-a", "--now", T0)
        record = self.ws.task_record("demo/W-5")
        self.assertEqual(record["status"], "ready")
        self.assertEqual(record["revision"], 0)
        self.assertEqual(record["write_paths"], ["src/five"])
        self.assertEqual(record["status_source"], "board")

        clean = validate(self.ws.root)
        self.assertEqual(clean.returncode, 0)
        self.assertNotIn("not registered centrally", clean.stdout)

    def test_board_import_and_reconcile_cannot_bypass_review(self):
        board = self.ws.board_data()
        for status in ("reviewed", "integrated", "accepted", "done"):
            with self.subTest(status=status):
                candidate = dict(board, packets=[*board["packets"], packet("W-5", status, ["src/five"])])
                (self.ws.repo / "docs" / "board.json").write_text(json.dumps(candidate) + "\n")
                self.invoke("board", "import", "--board", "demo.board", "--program", "demo",
                            "--agent", "agent-a", "--now", T0, expect=2)
                self.assertFalse(any(task["id"] == "demo/W-5" for task in self.ws.load()["tasks"]))

                candidate = dict(board, packets=[dict(row) for row in board["packets"]])
                candidate["packets"][2]["status"] = status
                (self.ws.repo / "docs" / "board.json").write_text(json.dumps(candidate) + "\n")
                self.invoke("reconcile", "--board", "demo.board", "--apply", "--reason",
                            "Adopt board status", "--agent", "agent-a", "--now", T0, expect=2)
                self.assertEqual(self.ws.task_record("demo/W-3")["status"], "draft")

    def test_reconcile_reports_then_adopts_board_status(self):
        board = self.ws.board_data()
        next(p for p in board["packets"] if p["id"] == "W-3")["status"] = "blocked"
        (self.ws.repo / "docs" / "board.json").write_text(json.dumps(board, indent=2) + "\n")

        report = self.invoke("reconcile", "--board", "demo.board", expect=1)
        self.assertIn("registry draft -> board blocked", report.stdout)

        self.invoke("reconcile", "--board", "demo.board", "--apply", "--reason",
                    "board is authoritative", "--agent", "agent-a", "--now", T0)
        record = self.ws.task_record("demo/W-3")
        self.assertEqual(record["status"], "blocked")
        self.assertEqual(record["revision"], 1)
        self.assertEqual(record["status_source"], "board")
        self.assertEqual(validate(self.ws.root).returncode, 0)

    # -- validator --------------------------------------------------------

    def test_edge_add_list_remove_and_cycle(self):
        self.invoke("edge", "add", "--from", "demo/W-3", "--to", "demo/W-1",
                    "--agent", "agent-a")
        self.assertIn("demo/W-3", self.invoke("edge", "list").stdout)

        duplicate = self.invoke("edge", "add", "--from", "demo/W-3", "--to", "demo/W-1",
                                "--agent", "agent-a", expect=2)
        self.assertIn("already exists", duplicate.stderr)

        cycle = self.invoke("edge", "add", "--from", "demo/W-1", "--to", "demo/W-3",
                            "--agent", "agent-a", expect=2)
        self.assertIn("dependency cycle", cycle.stderr)

        self.invoke("edge", "remove", "--from", "demo/W-3", "--to", "demo/W-1",
                    "--agent", "agent-a")
        self.assertIn("no edges", self.invoke("edge", "list").stdout)

    def test_integration_plan_references_are_validated(self):
        programs = self.ws.root / "programs"
        programs.mkdir()
        path = programs / "demo.yaml"
        path.write_text(yaml.safe_dump({
            "kind": "integration-plan",
            "version": 1,
            "slices": [{"id": "demo", "state": "startable", "tasks": ["demo/NOPE"]}],
        }))
        invalid = validate(self.ws.root)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("references unknown task demo/NOPE", invalid.stdout)

        path.write_text(yaml.safe_dump({
            "kind": "integration-plan",
            "version": 1,
            "slices": [{"id": "demo", "state": "startable", "tasks": ["demo/W-1"]}],
        }))
        self.assertEqual(validate(self.ws.root).returncode, 0)

    def test_validator_detects_status_drift_and_schema_violation(self):
        registry = self.ws.registry
        next(t for t in registry["tasks"] if t["id"] == "demo/W-4")["status"] = "green"
        self.ws.save()
        drifted = validate(self.ws.root)
        self.assertNotEqual(drifted.returncode, 0)
        self.assertIn("imported registry status green differs from board draft", drifted.stdout)

        next(t for t in registry["tasks"] if t["id"] == "demo/W-4")["status"] = "draft"
        del next(t for t in registry["tasks"] if t["id"] == "demo/W-4")["revision"]
        self.ws.save()
        invalid = validate(self.ws.root)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("missing required property 'revision'", invalid.stdout)


    def test_capability_registry_is_cross_checked(self):
        caps = self.ws.root / "caps"
        (caps / "efforts").mkdir(parents=True)
        (caps / "efforts" / "good.yaml").write_text("id: good\n")
        (caps / "registry.yaml").write_text(yaml.safe_dump({
            "kind": "capability-steward-registry",
            "efforts": [
                {"id": "good", "path": "efforts/good.yaml", "status": "exploring",
                 "owner": "unassigned"},
                {"id": "dormant-one", "path": "efforts/good.yaml", "status": "dormant",
                 "owner": "unassigned"},
            ],
            "contracts": [
                {"id": "missing", "path": "contracts/nope.yaml", "status": "identified",
                 "owner": "ghost-effort"},
                {"id": "zombie", "path": "efforts/good.yaml", "status": "identified",
                 "owner": "dormant-one"},
            ],
            "decisions": [],
        }, sort_keys=False))

        result = validate(self.ws.root, "--capabilities", str(caps))
        self.assertEqual(result.returncode, 1)
        self.assertIn("artifact missing at capabilities/contracts/nope.yaml", result.stdout)
        self.assertIn("owner 'ghost-effort' is not a registered effort", result.stdout)
        self.assertIn("owned by dormant effort 'dormant-one'", result.stdout)
        self.assertIn("1 exploring/identified with no owner", result.stdout)

    def test_contract_registry_is_validated(self):
        caps = self.ws.root / "wire"
        artifact = caps / "http/demo.thing.v1/v1/openapi.yaml"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("openapi: 3.1.0\n", encoding="utf-8")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        registry = {
            "kind": "wire-contract-registry",
            "version": 1,
            "updated": "2026-11-05",
            "contracts": [{
                "id": "demo.thing.v1",
                "kind": "http",
                "version": 1,
                "owner": "demo",
                "status": "accepted",
                "artifact": "http/demo.thing.v1/v1/openapi.yaml",
                "sha256": digest,
                "fixtures": {"valid": [], "invalid": []},
                "consumers": [{"service": "gamma",
                               "pin": {"sha256": digest, "commit": "abc1234"}}],
                "gate": "demo/W-1",
                "source": {"repo": "~/repositories/demo", "path": "contracts/x.yaml",
                           "commit": "abc1234"},
            }],
        }
        (caps / "registry.yaml").write_text(yaml.safe_dump(registry, sort_keys=False),
                                            encoding="utf-8")

        clean = validate(self.ws.root, "--contracts", str(caps))
        self.assertIn("contracts: 1 (1 accepted)", clean.stdout)
        self.assertNotIn("contracts/demo.thing.v1", clean.stdout)

        artifact.write_text("tampered\n", encoding="utf-8")
        tampered = validate(self.ws.root, "--contracts", str(caps))
        self.assertNotEqual(tampered.returncode, 0)
        self.assertIn("artifact hash does not match sha256", tampered.stdout)

        registry["contracts"][0]["gate"] = "demo/NOPE"
        (caps / "registry.yaml").write_text(yaml.safe_dump(registry, sort_keys=False),
                                            encoding="utf-8")
        unknown_gate = validate(self.ws.root, "--contracts", str(caps))
        self.assertIn("gate references unknown task demo/NOPE", unknown_gate.stdout)

    def test_program_map_cannot_invent_a_contract(self):
        programs = self.ws.root / "programs"
        programs.mkdir()
        caps = self.ws.root / "wire"
        caps.mkdir()
        (caps / "registry.yaml").write_text(
            yaml.safe_dump({"kind": "wire-contract-registry", "version": 1,
                            "updated": "2026-11-05", "contracts": []}), encoding="utf-8")
        (programs / "demo.yaml").write_text(yaml.safe_dump({
            "kind": "integration-plan", "version": 1,
            "contracts": [{"id": "ghost.thing.v1", "kind": "http", "owner": "demo",
                           "producers": ["demo"], "consumers": ["gamma"]}],
        }), encoding="utf-8")
        result = validate(self.ws.root, "--contracts", str(caps))
        self.assertIn("ghost.thing.v1 is not in contracts/registry.yaml", result.stdout)

    def test_validator_flags_stale_readme_counts(self):
        readme = self.ws.root / "README.md"
        readme.write_text("Current registry: **1 boards, 1 tasks, 1 edges**.\n")
        stale = validate(self.ws.root)
        self.assertIn("README.md claims 1 boards, 1 tasks, 1 edges", stale.stdout)

        readme.write_text("Current registry: **1 boards, 4 tasks, 0 edges**.\n")
        self.assertEqual(validate(self.ws.root).returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
