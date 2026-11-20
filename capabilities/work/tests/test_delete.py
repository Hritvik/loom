#!/usr/bin/env python3
"""Deletion as a recorded event: a tombstone that leaves the log foldable.

`loom delete` retires a task, or every task on a board, without touching a file and without row
surgery. These tests cover the five promises: the task leaves every view while the hash chain still
verifies under foldcheck, a board's work is retired in one event and `board remove` then succeeds,
the validator stops reporting the leftovers of retired work, a live claim or a stranded dependent
refuses the delete with the offending ids named, and no evidence or body file is ever removed.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from test_work import T0, Workspace, run, validate


class DeleteTest(unittest.TestCase):
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

    def delete(self, *args, expect=0):
        return self.invoke("delete", *args, "--agent", "agent-a", "--now", T0, expect=expect)

    def delete_events(self):
        return [record for record in self.ws.events() if record.get("event") == "delete"]

    # -- one task ---------------------------------------------------------

    def test_a_deleted_task_leaves_every_view_and_the_chain_still_folds(self):
        self.invoke("--agent", "setup", "snapshot", "--now", T0)

        deleted = self.delete("demo/W-3", "--reason", "superseded by W-4")
        self.assertIn("deleted demo/W-3 (1 task)", deleted.stdout)

        self.assertNotIn("demo/W-3", self.invoke("list").stdout)
        self.assertNotIn("demo/W-3", self.invoke("graph").stdout)
        self.assertNotIn("demo/W-3", self.invoke("export").stdout)
        self.assertNotIn("demo/W-3", self.invoke("search", "Task", "demo/W-3").stdout)
        missing = self.invoke("show", "demo/W-3", expect=2)
        self.assertIn("unknown task", missing.stderr)

        self.invoke("events", "--verify")
        self.invoke("foldcheck")
        clean = validate(self.ws.root)
        self.assertEqual(clean.returncode, 0, clean.stdout)

    def test_the_tombstone_records_agent_reason_and_prior_state_but_no_status(self):
        self.delete("demo/W-3", "--reason", "duplicate of W-4")
        events = self.delete_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["agent"], "agent-a")
        self.assertEqual(event["reason"], "duplicate of W-4")
        self.assertEqual(event["tasks"], ["demo/W-3"])
        self.assertEqual(event["prior_status"], {"demo/W-3": "draft"})
        self.assertEqual(event["count"], 1)
        # A tombstone is the absence of a task, not a status assertion: a status here would make
        # the event the "last word" on a task the registry no longer holds.
        self.assertNotIn("status", event)

    def test_a_deleted_packet_is_not_reported_as_unregistered(self):
        self.delete("demo/W-3", "--reason", "duplicate")
        after = validate(self.ws.root)
        self.assertEqual(after.returncode, 0, after.stdout)
        self.assertNotIn("not registered centrally", after.stdout)
        self.assertNotIn("demo/W-3", after.stdout)

    def test_edges_and_task_scoped_claims_are_dropped_with_the_task(self):
        self.invoke("edge", "add", "--from", "demo/W-4", "--to", "demo/W-3",
                    "--kind", "provider-parity", "--agent", "agent-a", "--now", T0)
        self.ws.registry["claims"].append({"artifact": "src/shared", "owner": "agent-a",
                                           "repo": str(self.ws.repo), "task": "demo/W-3"})
        (self.ws.repo / "src").mkdir(exist_ok=True)
        (self.ws.repo / "src" / "shared").write_text("shared\n")
        self.ws.save()

        self.delete("demo/W-3", "--reason", "retired with its edge")

        state = self.ws.load()
        self.assertEqual(state["edges"], [])
        self.assertEqual(state["claims"], [])
        clean = validate(self.ws.root)
        self.assertEqual(clean.returncode, 0, clean.stdout)

    # -- refusals ---------------------------------------------------------

    def test_a_live_claim_blocks_the_delete_and_is_named(self):
        self.invoke("claim", "demo/W-1", "--revision", "0", "--agent", "agent-a", "--now", T0)
        refused = self.delete("demo/W-1", "--reason", "no longer needed", expect=2)
        self.assertIn("refusing to delete demo/W-1", refused.stderr)
        self.assertIn("live claim held by demo/W-1 (agent-a)", refused.stderr)
        self.assertIn("dependents not also retired: demo/W-2", refused.stderr)
        self.assertIn("demo/W-1", self.invoke("list").stdout)
        self.assertEqual(self.delete_events(), [])

    def test_dependents_that_are_not_retired_block_the_delete(self):
        refused = self.delete("demo/W-1", "--reason", "cleanup", expect=2)
        self.assertIn("dependents not also retired: demo/W-2", refused.stderr)
        self.assertEqual(self.ws.task_record("demo/W-1")["status"], "ready")

    def test_delete_needs_exactly_one_target_and_a_reason(self):
        both = self.delete("demo/W-3", "--board", "demo.board", "--reason", "x", expect=2)
        self.assertIn("exactly one of <task> or --board", both.stderr)
        neither = self.delete("--reason", "x", expect=2)
        self.assertIn("exactly one of <task> or --board", neither.stderr)
        unreasoned = run("--root", str(self.ws.root), "delete", "demo/W-3")
        self.assertEqual(unreasoned.returncode, 2)
        self.assertIn("--reason", unreasoned.stderr)
        unknown = self.delete("demo/W-404", "--reason", "x", expect=2)
        self.assertIn("unknown task", unknown.stderr)

    # -- a board ----------------------------------------------------------

    def test_a_board_is_retired_in_one_event_and_then_removes(self):
        self.invoke("--agent", "setup", "snapshot", "--now", T0)

        deleted = self.delete("--board", "demo.board", "--reason", "the programme is dead")
        self.assertIn("deleted board demo.board (4 tasks)", deleted.stdout)
        self.assertIn("loom board remove --board demo.board", deleted.stdout)

        events = self.delete_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["deleted_board"], "demo.board")
        self.assertEqual(sorted(events[0]["tasks"]),
                         ["demo/W-1", "demo/W-2", "demo/W-3", "demo/W-4"])
        self.assertEqual(self.ws.load()["tasks"], [])
        self.assertEqual(self.ws.load()["edges"], [])

        removed = self.invoke("board", "remove", "--board", "demo.board",
                              "--agent", "agent-a", "--now", T0)
        self.assertIn("removed board demo.board", removed.stdout)
        self.assertNotIn("demo.board", self.invoke("board", "list").stdout)
        self.invoke("foldcheck")
        clean = validate(self.ws.root)
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)

    def test_a_retired_board_stops_a_missing_checkout_being_reported(self):
        gone = self.ws.root / "gone"
        registry = self.ws.registry
        registry["boards"].append({"id": "cal.program", "kind": "program", "role": "provider",
                                   "repo": str(gone), "path": "work-items.md"})
        registry["tasks"].append({"id": "cal/1", "board": "cal.program", "program": "cal",
                                  "owner_repo": str(gone), "title": "Rejected thing",
                                  "status": "rejected", "revision": 4})
        self.ws.save()

        broken = validate(self.ws.root)
        self.assertEqual(broken.returncode, 1, broken.stdout)
        self.assertIn("cal.program: missing", broken.stdout)
        self.assertIn("cal/1: heading 1 missing from cal.program", broken.stdout)

        self.delete("--board", "cal.program", "--reason", "the checkout was deleted")
        retired = validate(self.ws.root)
        self.assertEqual(retired.returncode, 0, retired.stdout)
        self.assertNotIn("missing", retired.stdout)

        self.invoke("board", "remove", "--board", "cal.program", "--agent", "agent-a", "--now", T0)
        self.assertNotIn("cal.program", validate(self.ws.root).stdout)
        self.assertEqual(validate(self.ws.root).returncode, 0)

    def test_a_board_with_no_tasks_points_at_board_remove(self):
        self.delete("--board", "demo.board", "--reason", "first pass")
        again = self.delete("--board", "demo.board", "--reason", "second pass", expect=2)
        self.assertIn("has no tasks", again.stderr)
        self.assertIn("loom board remove --board demo.board", again.stderr)

    # -- files ------------------------------------------------------------

    def test_no_evidence_or_body_file_is_ever_removed(self):
        evidence = self.ws.repo / "evidence" / "W-3.md"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("checked\n", encoding="utf-8")
        next(task for task in self.ws.registry["tasks"] if task["id"] == "demo/W-3")["evidence"] = \
            "evidence/W-3.md"
        self.ws.save()
        board_file = self.ws.repo / "docs" / "board.json"

        deleted = self.delete("demo/W-3", "--reason", "duplicate")

        self.assertIn(str(evidence), deleted.stdout)
        self.assertIn(str(board_file), deleted.stdout)
        self.assertIn("delete them by hand", deleted.stdout)
        self.assertTrue(evidence.is_file())
        self.assertTrue(board_file.is_file())
        event = self.delete_events()[0]
        self.assertIn(str(evidence), event["kept_evidence"])
        self.assertIn(str(board_file), event["kept_bodies"])


if __name__ == "__main__":
    unittest.main()
