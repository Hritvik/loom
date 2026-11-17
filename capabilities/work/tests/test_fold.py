#!/usr/bin/env python3
"""Conformance tests for the registry as a checked projection of the log.

From `loom snapshot` forward every state-changing event carries the fields it changed, so the
registry can be recomputed and compared. These tests cover the three promises: foldcheck names a
field instead of quietly repairing it, a rebuild refuses to write without a restore point and a
passing diff, and historical tasks arrive through the snapshot rather than a backfill.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from pathlib import Path

from test_work import T0, Workspace, run, validate

from store import SqliteStore


class FoldTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()

    def tearDown(self):
        self.ws.close()

    def invoke(self, *args, expect=0):
        result = run("--root", str(self.ws.root), *args)
        self.assertEqual(
            result.returncode, expect,
            f"exit {result.returncode} (expected {expect})\n"
            f"stdout:{result.stdout}\nstderr:{result.stderr}")
        return result

    def snapshot(self, *extra, expect=0):
        return self.invoke("--agent", "setup", "--now", T0, "snapshot", *extra, expect=expect)

    def claim(self, task_id="demo/W-1", now="2030-01-01T10:01:00Z"):
        self.invoke("claim", task_id, "--revision", "0", "--agent", "agent-a", "--now", now)
        return self.ws.task_record(task_id)

    def transition(self, task_id, target, now="2030-01-01T10:02:00Z"):
        task = self.ws.task_record(task_id)
        epoch = (task.get("claim") or {}).get("epoch")
        return self.invoke(
            "transition", task_id, "--revision", str(task["revision"]), "--to", target,
            *(["--epoch", str(epoch)] if epoch is not None else []),
            "--agent", "agent-a", "--now", now)

    def edit_task(self, task_id, mutate):
        """A direct row edit, standing in for a hand edit or a writer that skipped the log."""
        connection = sqlite3.connect(self.ws.db)
        try:
            row = connection.execute(
                "SELECT body FROM tasks WHERE id = ?", (task_id,)).fetchone()
            body = json.loads(row[0])
            mutate(body)
            connection.execute(
                "UPDATE tasks SET body = ? WHERE id = ?", (json.dumps(body), task_id))
            connection.commit()
        finally:
            connection.close()

    def snapshots(self):
        store = SqliteStore(self.ws.root, self.ws.db)
        try:
            return store.snapshots()
        finally:
            store.close()

    # -- the clean case ---------------------------------------------------

    def test_foldcheck_reports_zero_divergence_after_a_snapshot(self):
        self.snapshot()
        self.claim()
        self.transition("demo/W-1", "red")

        result = self.invoke("foldcheck")
        self.assertIn("no divergence from snapshot", result.stdout)
        self.assertIn("state delta(s)", result.stdout)

        # The check is not reading the registry back to itself: the deltas reconstruct the state.
        flagged = self.invoke("foldcheck", "--json")
        report = json.loads(flagged.stdout)
        self.assertEqual(report["divergences"], [])
        self.assertEqual(report["problems"], [])
        self.assertGreaterEqual(report["deltas"], 2)

    def test_a_database_copied_after_the_snapshot_reports_zero_divergence(self):
        self.snapshot()
        self.claim()
        self.transition("demo/W-1", "red")
        copied = Path(self.ws.root) / "copied.db"
        connection = sqlite3.connect(self.ws.db)
        try:
            connection.execute("VACUUM INTO ?", (str(copied),))
        finally:
            connection.close()

        result = run("--root", str(self.ws.root), "--db", str(copied), "foldcheck")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no divergence", result.stdout)

    def test_a_claim_carries_its_delta_and_the_attempt_does_not(self):
        self.snapshot()
        self.claim()

        events = [event for event in self.ws.events() if event.get("task") == "demo/W-1"]
        claim = next(event for event in events if event.get("event") == "claim")
        attempt = next(event for event in events if event.get("event") == "attempt")
        delta = claim["state_delta"]
        self.assertEqual([change["id"] for change in delta["tasks"]], ["demo/W-1"])
        self.assertEqual(delta["tasks"][0]["set"]["status"], "claimed")
        self.assertIn("claim", delta["tasks"][0]["set"])
        # A run record is process audit, not a state change, so it must not claim the delta.
        self.assertNotIn("state_delta", attempt)

    def test_a_hand_deleted_edge_is_named(self):
        self.snapshot()
        self.invoke("edge", "add", "--from", "demo/W-2", "--to", "demo/W-1",
                    "--agent", "agent-a", "--now", T0)
        self.invoke("foldcheck")

        connection = sqlite3.connect(self.ws.db)
        try:
            connection.execute(
                "DELETE FROM edges WHERE from_task = 'demo/W-2' AND to_task = 'demo/W-1'")
            connection.commit()
        finally:
            connection.close()
        result = self.invoke("foldcheck", expect=1)
        self.assertIn("edge demo/W-2 -> demo/W-1", result.stdout)
        self.assertIn("the log records it, the registry does not", result.stdout)

    # -- naming what moved, instead of repairing it ----------------------

    def test_a_dropped_field_is_named_and_the_record_is_not_deleted(self):
        self.snapshot()
        self.edit_task("demo/W-1", lambda body: body.pop("title"))

        result = self.invoke("foldcheck", expect=1)
        self.assertIn("field 'title' is missing from the registry", result.stdout)
        self.assertIn("demo/W-1", result.stdout)
        self.assertIn("1 divergence(s)", result.stdout)
        # The check is read-only: the record survives with the fields it still has.
        record = self.ws.task_record("demo/W-1")
        self.assertNotIn("title", record)
        self.assertEqual(record["status"], "ready")
        self.assertEqual(len(self.ws.load()["tasks"]), 4)

    def test_a_forged_field_is_named_with_both_values(self):
        self.snapshot()
        self.edit_task("demo/W-1", lambda body: body.update({"status": "accepted", "revision": 7}))

        result = self.invoke("foldcheck", expect=1)
        self.assertIn("field 'status' differs", result.stdout)
        self.assertIn("log 'ready'", result.stdout)
        self.assertIn("registry 'accepted'", result.stdout)
        self.assertIn("field 'revision' differs", result.stdout)

    def test_a_record_with_no_event_is_named(self):
        self.snapshot()
        connection = sqlite3.connect(self.ws.db)
        try:
            body = {"id": "demo/W-9", "board": "demo.board", "program": "demo",
                    "title": "Ghost", "status": "ready", "revision": 0}
            connection.execute("INSERT INTO tasks (id, body) VALUES (?, ?)",
                               ("demo/W-9", json.dumps(body)))
            connection.commit()
        finally:
            connection.close()

        result = self.invoke("foldcheck", expect=1)
        self.assertIn("task demo/W-9: the registry has it, no create event since the snapshot",
                      result.stdout)

    def test_the_validator_reports_a_dropped_field(self):
        without = validate(self.ws.root)
        self.assertEqual(without.returncode, 0, without.stdout)
        self.assertIn("no fold baseline", without.stdout)

        self.snapshot()
        self.claim()
        self.assertEqual(validate(self.ws.root).returncode, 0)

        self.edit_task("demo/W-1", lambda body: body.pop("title"))
        result = validate(self.ws.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fold: task demo/W-1: field 'title' is missing", result.stdout)

    # -- the baseline -----------------------------------------------------

    def test_historical_tasks_arrive_through_the_snapshot_and_nothing_is_backfilled(self):
        before = self.ws.events()
        self.assertFalse([event for event in before if "state_delta" in event],
                         "events written before a baseline must not be rewritten with deltas")

        self.snapshot()
        taken = self.snapshots()
        self.assertEqual(len(taken), 1)

        connection = sqlite3.connect(self.ws.db)
        try:
            body = json.loads(connection.execute("SELECT body FROM snapshots").fetchone()[0])
        finally:
            connection.close()
        self.assertEqual([task["id"] for task in body["tasks"]],
                         ["demo/W-1", "demo/W-2", "demo/W-3", "demo/W-4"])

        # The snapshot is the only carrier of those tasks: the fold starts after it.
        self.assertEqual([event for event in self.ws.events()
                          if event.get("event") == "snapshot"
                          and event.get("state_delta")], [])
        self.invoke("foldcheck")

    def test_foldcheck_refuses_without_a_snapshot(self):
        result = self.invoke("foldcheck", expect=2)
        self.assertIn("no snapshot", result.stderr)
        self.assertIn("does not backfill", result.stderr)

        rebuilt = self.invoke("rebuild", "--apply", "--agent", "agent-a", expect=2)
        self.assertIn("no snapshot", rebuilt.stderr)
        self.assertEqual(self.snapshots(), [])

    def test_a_second_snapshot_refuses_to_bless_a_divergent_registry(self):
        self.snapshot()
        self.edit_task("demo/W-1", lambda body: body.pop("title"))

        refused = self.snapshot(expect=2)
        self.assertIn("would accept them as truth", refused.stderr)
        self.assertEqual(len(self.snapshots()), 1)

        accepted = self.snapshot("--force", "--reason", "known drift", expect=0)
        self.assertIn("snapshot", accepted.stdout)
        self.assertEqual(len(self.snapshots()), 2)
        # The new baseline is the current state, so the drift is now what the log folds to.
        self.invoke("foldcheck")

    def test_a_tampered_snapshot_body_is_reported(self):
        self.snapshot()
        connection = sqlite3.connect(self.ws.db)
        try:
            body = json.loads(connection.execute("SELECT body FROM snapshots").fetchone()[0])
            body["tasks"][0]["title"] = "Forged at the baseline"
            connection.execute("UPDATE snapshots SET body = ?", (json.dumps(body),))
            connection.commit()
        finally:
            connection.close()

        result = self.invoke("foldcheck", expect=1)
        self.assertIn("BROKEN", result.stdout)
        self.assertIn("does not match the digest recorded in the event chain", result.stdout)

    def forged_row_text(self, body):
        """Render a forged baseline exactly as the engine would, so the row agrees with itself.

        The point of these tests is that a self-consistent row is still caught: whoever edited the
        body can recompute the row digest too. Building the text with `json.dumps` defaults would
        make the row internally inconsistent and let a row-digest comparison claim the catch, so
        the test would pass against the vulnerable implementation.
        """
        from engine import canonical_registry
        return canonical_registry(body)

    def test_a_baseline_whose_row_digest_was_forged_with_it_is_still_broken(self):
        self.snapshot()
        connection = sqlite3.connect(self.ws.db)
        try:
            body = json.loads(connection.execute("SELECT body FROM snapshots").fetchone()[0])
            body["tasks"][0]["title"] = "Forged with a matching row digest"
            text = self.forged_row_text(body)
            connection.execute(
                "UPDATE snapshots SET body = ?, digest = ?",
                (text, hashlib.sha256(text.encode("utf-8")).hexdigest()))
            connection.commit()
        finally:
            connection.close()

        result = self.invoke("foldcheck", expect=1)
        self.assertIn("BROKEN", result.stdout)
        self.assertIn("does not match the digest recorded in the event chain", result.stdout)

    def test_a_snapshot_row_with_no_event_at_its_sequence_is_broken(self):
        self.snapshot()
        connection = sqlite3.connect(self.ws.db)
        try:
            body = json.loads(connection.execute("SELECT body FROM snapshots").fetchone()[0])
            body["tasks"][0]["title"] = "Smuggled in without an event"
            text = self.forged_row_text(body)
            connection.execute(
                "INSERT INTO snapshots (seq, ts, digest, body) VALUES (?, ?, ?, ?)",
                (99, T0, hashlib.sha256(text.encode("utf-8")).hexdigest(), text))
            connection.commit()
        finally:
            connection.close()

        result = self.invoke("foldcheck", expect=1)
        self.assertIn("BROKEN", result.stdout)
        self.assertIn("snapshot 99: no event at that sequence", result.stdout)

    def test_the_validator_reports_a_tampered_baseline(self):
        self.snapshot()
        connection = sqlite3.connect(self.ws.db)
        try:
            body = json.loads(connection.execute("SELECT body FROM snapshots").fetchone()[0])
            body["tasks"][0]["title"] = "Forged at the baseline"
            text = self.forged_row_text(body)
            connection.execute(
                "UPDATE snapshots SET body = ?, digest = ?",
                (text, hashlib.sha256(text.encode("utf-8")).hexdigest()))
            connection.commit()
        finally:
            connection.close()

        result = validate(self.ws.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fold: BROKEN snapshot", result.stdout)
        self.assertIn("does not match the digest recorded in the event chain", result.stdout)

    # -- rebuild ----------------------------------------------------------

    def test_a_rebuild_refuses_to_delete_a_record_the_log_cannot_explain(self):
        self.snapshot()
        connection = sqlite3.connect(self.ws.db)
        try:
            body = {"id": "demo/W-9", "board": "demo.board", "program": "demo",
                    "title": "Ghost", "status": "ready", "revision": 0}
            connection.execute("INSERT INTO tasks (id, body) VALUES (?, ?)",
                               ("demo/W-9", json.dumps(body)))
            connection.commit()
        finally:
            connection.close()

        result = self.invoke("rebuild", "--apply", "--agent", "agent-a", "--now", T0, expect=2)
        self.assertIn("the diff does not pass", result.stderr)
        self.assertIn("demo/W-9", result.stderr)
        self.assertEqual(len(self.ws.load()["tasks"]), 5, "the rebuild deleted the ghost")
        self.assertEqual(list(Path(self.ws.root).glob("work.db.restore-*")), [],
                         "a refused rebuild wrote a restore point")

    def test_a_rebuild_needs_a_restore_point_and_a_passing_diff(self):
        self.snapshot()
        self.claim()
        self.edit_task("demo/W-1", lambda body: (body.pop("write_paths"),
                                                 body.update({"status": "green"})))

        # Without --apply the diff is printed and nothing is written.
        dry = self.invoke("rebuild", expect=1)
        self.assertIn("field 'write_paths' is missing from the registry", dry.stdout)
        self.assertIn("field 'status' differs", dry.stdout)
        self.assertIn("run again with --apply", dry.stdout)
        self.assertEqual(self.ws.task_record("demo/W-1")["status"], "green")
        self.assertEqual(list(Path(self.ws.root).glob("work.db.restore-*")), [])

        applied = self.invoke("rebuild", "--apply", "--agent", "agent-a",
                              "--now", "2030-01-01T10:03:00Z")
        self.assertIn("repaired 2 divergence(s)", applied.stdout)
        repaired = self.ws.task_record("demo/W-1")
        self.assertEqual(repaired["status"], "claimed")
        self.assertEqual(repaired["write_paths"], ["src/one"])
        self.invoke("foldcheck")

        restores = sorted(Path(self.ws.root).glob("work.db.restore-*.db"))
        self.assertEqual(len(restores), 1)
        self.assertIn(str(restores[0]), applied.stdout)
        connection = sqlite3.connect(str(restores[0]))
        try:
            body = json.loads(connection.execute(
                "SELECT body FROM tasks WHERE id = 'demo/W-1'").fetchone()[0])
        finally:
            connection.close()
        self.assertEqual(body["status"], "green", "the restore point is the pre-rebuild state")

        rebuild = next(event for event in self.ws.events() if event.get("event") == "rebuild")
        self.assertEqual(Path(rebuild["restore_point"]).resolve(), restores[0].resolve())
        self.assertEqual(rebuild["divergences"], 2)
        self.assertEqual(rebuild["snapshot"], self.snapshots()[0]["seq"])
        self.assertEqual(len(rebuild["registry_sha256"]), 64)
        # The rebuild restores what the log says; it does not write a second delta that would
        # double-apply the state on the next fold.
        self.assertNotIn("state_delta", rebuild)

    def test_an_applied_rebuild_with_nothing_to_repair_writes_nothing(self):
        self.snapshot()
        self.claim()
        result = self.invoke("rebuild", "--apply", "--agent", "agent-a", "--now", T0)
        self.assertIn("nothing to write", result.stdout)
        self.assertEqual(list(Path(self.ws.root).glob("work.db.restore-*")), [])
        self.assertFalse([event for event in self.ws.events() if event.get("event") == "rebuild"])

    # -- deltas and the store --------------------------------------------

    def test_a_batch_mutation_puts_its_remaining_delta_on_the_last_event(self):
        store = SqliteStore(self.ws.root, self.ws.db)
        try:
            with store.lock():
                registry = store.load()
                with store.transaction():
                    for task in registry["tasks"][:2]:
                        previous = task["status"]
                        task["status"] = "planned"
                        store.append_event(event="reconcile", task=task["id"], agent="agent-a",
                                           status="planned", previous_status=previous,
                                           revision=task["revision"], reason="board", now=T0)
                events = [event for _, event in store.read_events()]
        finally:
            store.close()

        first = next(event for event in events if event.get("task") == "demo/W-1")
        last = next(event for event in events if event.get("task") == "demo/W-2")
        self.assertNotIn("state_delta", first)
        self.assertEqual({change["id"] for change in last["state_delta"]["tasks"]},
                         {"demo/W-1", "demo/W-2"})

    def test_an_unlocked_snapshot_is_refused(self):
        from store import WorkError

        store = SqliteStore(self.ws.root, self.ws.db)
        try:
            with self.assertRaises(WorkError):
                store.write_snapshot(1, T0, "0" * 64, "{}")
            with self.assertRaises(WorkError):
                store.backup_to(Path(self.ws.root) / "copy.db")
            target = Path(self.ws.root) / "restore.db"
            with store.lock():
                store.backup_to(target)
                self.assertTrue(target.is_file())
                # A restore point is evidence; a second rebuild must not write over it.
                with self.assertRaises(WorkError):
                    store.backup_to(target)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
