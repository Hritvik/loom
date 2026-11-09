"""Tests for the read-only board projection used by the Linear sync."""
from __future__ import annotations

import datetime as dt
import json
import unittest

from linear_projection import project_cards

NOW = dt.datetime(2030, 1, 1, 10, tzinfo=dt.timezone.utc)
FOCUS = "alpha/W-1"
BEFORE_NOW = "2030-01-01T09:59:00Z"
AFTER_NOW = "2030-01-01T10:01:00Z"
COMMIT = "a98765e" + "0" * 33
NEW_COMMIT = "b98765e" + "0" * 33


def task(task_id, status="ready", **fields):
    return {
        "id": task_id, "title": f"Task {task_id}", "status": status,
        "program": task_id.split("/")[0], "revision": 3, **fields,
    }


def event(task_id, seq, kind, **fields):
    return {
        "task": task_id, "seq": seq, "event": kind,
        "status": "green", "revision": 3, **fields,
    }


def review_request(task_id, seq, source=COMMIT, **fields):
    return event(task_id, seq, "ask", to="independent-reviewer", review_source=source,
                 review_base="c" * 40, review_evidence="evidence/checks.md", **fields)


def review_answer(request, seq, decision="approve", **fields):
    return event(request["task"], seq, "answer", in_reply_to=request["seq"],
                 decision=decision, note="private test details", **{
                     "agent": request["to"],
                     **{key: request[key] for key in ("review_source", "review_base", "review_evidence")
                        if key in request},
                     **fields,
                 })


class LinearProjectionTest(unittest.TestCase):
    def cards(self, tasks, events=(), edges=(), focus=FOCUS):
        registry = {"tasks": tasks, "edges": list(edges)}
        return {card["id"]: card for card in project_cards(registry, list(events), NOW, focus)}

    def test_scope_is_focus_and_transitive_prerequisites(self):
        cards = self.cards([
            task(FOCUS, depends_on=["alpha/DEP-1"]),
            task("alpha/DEP-1"),
            task("beta/W-1", status="accepted"),
            task("chat/W-1"),
        ], edges=[
            {"from": "alpha/DEP-1", "to": "beta/W-1"},
            {"from": FOCUS, "to": "chat/W-1", "kind": "provider-parity"},
        ])
        self.assertEqual(set(cards), {FOCUS, "alpha/DEP-1", "beta/W-1"})
        self.assertEqual(cards[FOCUS]["lane"], "blocked")
        self.assertEqual(cards[FOCUS]["blockers"], ["alpha/DEP-1"])
        self.assertEqual(cards["alpha/DEP-1"]["lane"], "claimable")
        self.assertEqual(cards["beta/W-1"]["lane"], "integrated")
        self.assertEqual(sorted(cards["beta/W-1"]["dependents"]), sorted([FOCUS, "alpha/DEP-1"]))

    def test_all_tasks_include_groups_dependencies_and_last_implementers(self):
        cards = self.cards([
            task(FOCUS, status="green", depends_on=["alpha/DEP-1"]),
            task("alpha/DEP-1", status="integrated"),
            task("chat/W-1"),
        ], [event(FOCUS, 1, "transition", agent="author")], focus=None)
        self.assertEqual(set(cards), {FOCUS, "alpha/DEP-1", "chat/W-1"})
        self.assertEqual(cards[FOCUS]["dependencies"], ["alpha/DEP-1"])
        self.assertEqual(cards[FOCUS]["blockers"], [])
        self.assertEqual(cards[FOCUS]["owner"], "alpha")
        self.assertEqual(cards[FOCUS]["implementer"], "author")
        self.assertEqual(cards["chat/W-1"]["lane"], "claimable")

    def test_write_conflicts_use_live_claims_outside_focus_without_exporting_paths(self):
        target = task(FOCUS, write_paths=["private/source/path"])
        other = task("alpha/DEP-1", status="claimed", write_paths=["private/source"],
                     claim={"agent": "implementer-a", "lease_expires": AFTER_NOW})
        cards = self.cards([target, other])
        self.assertEqual(cards[FOCUS]["lane"], "blocked")
        self.assertEqual(cards[FOCUS]["blockers"], ["alpha/DEP-1"])
        self.assertNotIn("private/source", json.dumps(cards))
        other["claim"]["lease_expires"] = BEFORE_NOW
        self.assertEqual(self.cards([target, other])[FOCUS]["lane"], "claimable")

    def test_human_gate_and_lease_expiry_are_distinct(self):
        blocked = self.cards([task(FOCUS, status="blocked", blocked_needs="human")])[FOCUS]
        self.assertEqual((blocked["lane"], blocked["next_actor"]), ("blocked", "human"))
        active = task(FOCUS, status="red", claim={"agent": "person-a", "lease_expires": AFTER_NOW})
        self.assertEqual(self.cards([active])[FOCUS]["next_actor"], "person-a")
        self.assertEqual(self.cards([active])[FOCUS]["lane"], "implementing")
        active["claim"]["lease_expires"] = BEFORE_NOW
        expired = self.cards([active])[FOCUS]
        self.assertEqual((expired["lane"], expired["next_actor"]), ("blocked", "coordinator"))

    def test_green_submission_does_not_depend_on_lease_and_approval_needs_board_transition(self):
        green = task(FOCUS, status="green", claim={"agent": "author", "lease_expires": BEFORE_NOW})
        self.assertEqual(self.cards([green])[FOCUS]["lane"], "needs_review")
        submission = review_request(FOCUS, 2)
        pending = self.cards([green], [submission])[FOCUS]
        self.assertEqual((pending["lane"], pending["next_actor"]), ("in_review", "independent-reviewer"))
        approved = self.cards([green], [submission, review_answer(submission, 3)])[FOCUS]
        self.assertEqual((approved["lane"], approved["next_actor"]), ("in_review", "coordinator"))
        self.assertNotIn("private test details", json.dumps(approved))
        self.assertNotIn(COMMIT, json.dumps(approved))

    def test_reviewed_without_structured_receipt_remains_review_work(self):
        historical = task(FOCUS, status="reviewed", review={"reviewer": "reviewer", "decision": "approve"})
        self.assertEqual(self.cards([historical])[FOCUS]["lane"], "needs_review")
        current = task(FOCUS, status="reviewed", review={
            "reviewer": "reviewer", "decision": "approve", "source": COMMIT, "answer": 4,
        })
        self.assertEqual(self.cards([current])[FOCUS]["lane"], "awaiting_integration")

    def test_legacy_changes_request_survives_same_candidate_structured_replacement(self):
        green = task(FOCUS, status="green", claim={"agent": "author", "lease_expires": BEFORE_NOW})
        legacy = event(FOCUS, 1, "ask", to="independent-reviewer",
                       question=f"Review {COMMIT[:7]} relative to deadbee")
        changes = review_answer(legacy, 2, "changes requested")
        latest = review_request(FOCUS, 3)
        card = self.cards([green], [legacy, changes, latest])[FOCUS]
        self.assertEqual(card["lane"], "changes_requested")
        self.assertEqual(card["next_actor"], "author")
        self.assertEqual(card["reviewer"], "independent-reviewer")
        self.assertNotIn("deadbee", json.dumps(card))

    def test_legacy_changes_request_survives_unstructured_fixes_but_new_candidate_can_be_reviewed(self):
        green = task(FOCUS, status="green", revision=5)
        legacy = event(FOCUS, 1, "ask", to="independent-reviewer",
                       question=f"Review {COMMIT[:7]}")
        changes = review_answer(legacy, 2, "changes requested")
        new_green = event(FOCUS, 3, "transition", revision=5, agent="author")
        ordinary = event(FOCUS, 4, "ask", revision=5, to="independent-reviewer",
                         question="Fixed, please take a look")
        history = [legacy, changes, new_green, ordinary]
        self.assertEqual(self.cards([green], history)[FOCUS]["lane"], "changes_requested")
        latest = review_request(FOCUS, 5, source=NEW_COMMIT, revision=5)
        self.assertEqual(self.cards([green], [*history, latest])[FOCUS]["lane"], "in_review")

    def test_structured_changes_request_points_to_implementer(self):
        green = task(FOCUS, status="green", claim={"agent": "author", "lease_expires": AFTER_NOW})
        request = review_request(FOCUS, 1)
        response = review_answer(request, 2, "changes requested")
        card = self.cards([green], [request, response])[FOCUS]
        self.assertEqual((card["lane"], card["next_actor"]), ("changes_requested", "author"))

    def test_stale_or_mismatched_approval_does_not_clear_latest_request(self):
        green = task(FOCUS, status="green")
        old = review_request(FOCUS, 1, revision=2)
        latest = review_request(FOCUS, 2)
        history = [old, latest, review_answer(latest, 3, agent="not-the-reviewer")]
        self.assertEqual(self.cards([green], history)[FOCUS]["next_actor"], "independent-reviewer")
        self.assertEqual(self.cards([green], [old])[FOCUS]["lane"], "needs_review")


if __name__ == "__main__":
    unittest.main()
