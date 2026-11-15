"""Read models derive from the engine, and state is never confused with claimability."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Home  # noqa: E402

import engine  # noqa: E402
import store  # noqa: E402
from loom_ui import model, snapshot  # noqa: E402


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.close)
        self.snapshot = snapshot.read(self.home.db)
        self.view = model.build(self.snapshot)
        self.now = store.parse_time(self.snapshot.as_of)
        self.registry = self.snapshot.registry

    def task(self, task_id):
        found = model.find(self.view, task_id)
        self.assertIsNotNone(found, task_id)
        return found

    def test_claimability_facts_come_from_the_engine(self):
        for view in self.view.tasks:
            record = engine.find_task(self.registry, view.id)
            satisfied, unmet = engine.dependencies_met(self.registry, record)
            self.assertEqual(view.claimability.dependencies_satisfied, satisfied, view.id)
            self.assertEqual(list(view.claimability.unmet_dependencies), unmet, view.id)
            self.assertEqual(list(view.claimability.path_conflicts),
                             engine.write_path_conflicts(self.registry, record, self.now), view.id)
            self.assertEqual(view.claimability.lease_held,
                             engine.active_claim(record, self.now), view.id)
            self.assertEqual(view.claimability.state_ok,
                             record["status"] in engine.CLAIMABLE_FROM, view.id)

    def test_claimable_means_claim_would_succeed(self):
        """`loom claim` refuses a state, dependency, path or lease exception; so do we."""
        for view in self.view.tasks:
            record = engine.find_task(self.registry, view.id)
            satisfied, _ = engine.dependencies_met(self.registry, record)
            expected = (record["status"] in engine.CLAIMABLE_FROM
                        and satisfied
                        and not engine.write_path_conflicts(self.registry, record, self.now)
                        and not record.get("claim"))
            self.assertEqual(view.claimability.claimable, expected, view.id)

    def test_ready_is_not_the_same_as_claimable(self):
        self.assertEqual(self.task("demo/W-1").status, "ready")
        self.assertTrue(self.task("demo/W-1").claimability.claimable)
        blocked = self.task("demo/W-2")
        self.assertEqual(blocked.status, "ready")
        self.assertFalse(blocked.claimability.claimable)
        self.assertIn("unmet dependencies", " ".join(blocked.claimability.reasons))

    def test_path_conflict_with_a_live_claim_is_a_blocker(self):
        conflicted = self.task("demo/W-4")
        self.assertFalse(conflicted.claimability.claimable)
        self.assertTrue(conflicted.claimability.path_conflicts)
        self.assertIn("demo/W-3", conflicted.claimability.path_conflicts[0])
        kinds = {blocker.kind for blocker in conflicted.blockers}
        self.assertIn("path-conflict", kinds)

    def test_expired_lease_needs_takeover_not_claim(self):
        lapsed = self.task("demo/W-7")
        self.assertTrue(lapsed.claim.expired)
        self.assertFalse(lapsed.claim.active)
        self.assertFalse(lapsed.claimability.claimable)
        self.assertIn("takeover", " ".join(lapsed.claimability.reasons))

    def test_live_lease_is_reported_with_its_epoch(self):
        held = self.task("demo/W-3")
        self.assertTrue(held.claim.active)
        self.assertEqual(held.claim.agent, "opencode/other")
        self.assertEqual(held.claim.epoch, 2)
        self.assertGreater(held.claim.remaining_seconds or 0, 0)
        self.assertFalse(held.claimability.claimable)

    def test_downstream_uses_the_dependency_graph(self):
        self.assertIn("demo/W-2", self.task("demo/W-1").downstream)
        self.assertEqual(self.task("demo/W-6").downstream, ())

    def test_claimable_list_is_ranked_by_downstream(self):
        self.assertEqual([task.id for task in model.claimable(self.view)], ["demo/W-1"])

    def test_state_and_claimability_are_separate_fields(self):
        payload = self.view.to_dict()
        for task in payload["tasks"]:
            self.assertIn("status", task)
            self.assertIn("claimability", task)
            self.assertIn("reasons", task["claimability"])
        accepted = next(task for task in payload["tasks"] if task["id"] == "demo/W-6")
        self.assertEqual(accepted["status"], "accepted")
        self.assertFalse(accepted["claimability"]["claimable"])

    def test_counts_cover_every_state(self):
        self.assertEqual(self.view.counts["total"], len(self.view.tasks))
        self.assertEqual(self.view.counts["claimable"], 1)
        self.assertEqual(sum(count for status, count in self.view.counts.items()
                             if status not in {"claimable", "total"}),
                         len(self.view.tasks))

    def test_boards_count_their_tasks(self):
        board = self.view.boards[0]
        self.assertEqual(board.id, "demo.board")
        self.assertEqual(board.tasks, len(self.view.tasks))

    def test_expired_leases_are_surfaced(self):
        expired = model.expired_leases(self.view)
        self.assertEqual([task.id for task in expired], ["demo/W-7"])

    def test_view_serialises(self):
        payload = self.view.to_dict()
        self.assertEqual(payload["head"]["seq"], self.snapshot.head_seq)
        self.assertIsInstance(payload["tasks"][0]["write_paths"], list)

    def test_now_comes_from_the_snapshot_not_the_wall_clock(self):
        # The same snapshot built twice at different wall-clock times gives the same answer.
        again = model.build(self.snapshot)
        self.assertEqual([task.claimability.claimable for task in again.tasks],
                         [task.claimability.claimable for task in self.view.tasks])


if __name__ == "__main__":
    unittest.main()
