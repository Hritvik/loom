"""Owner queues and the task inspector: one bucket per task, engine-derived facts, no guesses."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ControlHome, DIGEST_A, SOURCE_B, T0  # noqa: E402

import engine  # noqa: E402
from loom_ui import control, model, operations, pages, provenance, snapshot, templates, version  # noqa: E402


class _Log:
    """The engine's own history functions, over the snapshot's events."""

    def __init__(self, events):
        self.events = events

    def read_events(self):
        return [(None, event) for event in self.events]


class ControlTest(unittest.TestCase):
    def setUp(self):
        self.home = ControlHome()
        self.addCleanup(self.home.close)
        self.snapshot = snapshot.read(self.home.db)
        self.view = model.build(self.snapshot)
        self.room = control.build(self.snapshot, self.view)

    def queue(self, queue_id: str):
        return next(queue for queue in self.room.queues if queue.id == queue_id)

    def ids(self, queue_id: str) -> list[str]:
        return [item.task.id for item in self.queue(queue_id).items]

    def bucket_of(self, task_id: str) -> str:
        row = next(row for row in self.room.rows if row.task.id == task_id)
        return row.bucket.id

    def detail(self, task_id: str):
        found = control.detail(self.snapshot, self.view, task_id)
        self.assertIsNotNone(found)
        return found

    # -- one bucket per task -------------------------------------------------

    def test_every_task_is_in_exactly_one_queue(self):
        seen = [task_id for queue in self.room.queues for task_id in
                [item.task.id for item in queue.items]]
        self.assertEqual(len(seen), len(set(seen)), "a task was counted in two queues")
        self.assertEqual(sorted(seen), sorted(task.id for task in self.view.tasks))
        self.assertEqual(self.room.counts["queued"], self.room.counts["total"])
        self.assertEqual(self.room.counts["total"], len(self.view.tasks))
        self.assertEqual(self.room.gaps, ())

    def test_queue_order_puts_human_work_first(self):
        self.assertEqual([queue.id for queue in self.room.queues], list(control.QUEUE_ORDER))

    # -- the owner queue -----------------------------------------------------

    def test_needs_you_holds_only_what_a_human_must_do(self):
        self.assertEqual(set(self.ids("needs-you")),
                         {"demo/W-11", "demo/W-15", "demo/W-22"})
        blocked_human = next(item for item in self.queue("needs-you").items
                             if item.task.id == "demo/W-15")
        self.assertIn("blocked, needs a human", blocked_human.bucket.reasons)
        self.assertIn("Choose between option A and option B", blocked_human.bucket.detail)
        # An agent-blocked task is stalled, not owner work.
        self.assertEqual(self.bucket_of("demo/W-16"), "stalled")

    def test_human_review_request_names_its_event(self):
        item = next(item for item in self.queue("needs-you").items
                    if item.task.id == "demo/W-11")
        self.assertIn("review addressed to you", item.bucket.reasons)
        request = item.bucket.review_request
        self.assertIsNotNone(request)
        self.assertEqual(request.asked_to, "owner")
        self.assertEqual(request.review_source, SOURCE_B)
        self.assertFalse(request.answered)
        self.assertTrue(request.current)
        self.assertIn(f"event {request.seq}", item.bucket.detail)

    def test_owner_identity_is_configuration_not_a_guess(self):
        # A question to an identity outside the engine's human set is not owner work...
        self.assertEqual(self.bucket_of("demo/W-27"), "claimable")
        # ...until the operator names that identity as the owner.
        room = control.build(self.snapshot, self.view, frozenset({"owner", "hritvik"}))
        item = next(item for item in
                    next(queue for queue in room.queues if queue.id == "needs-you").items
                    if item.task.id == "demo/W-27")
        self.assertIn("question for you", item.bucket.reasons)
        self.assertIn("acceptance wording", item.bucket.detail)
        self.assertIn("hritvik", room.owner_identities)

    # -- stalled and review --------------------------------------------------

    def test_stalled_covers_every_cannot_proceed_state(self):
        stalled = set(self.ids("stalled"))
        self.assertEqual(stalled, {"demo/W-5", "demo/W-7", "demo/W-16", "demo/W-17",
                                   "demo/W-18", "demo/W-21", "demo/W-29"})
        reasons = {item.task.id: item.bucket.reasons[0] for item in
                   self.queue("stalled").items}
        self.assertEqual(reasons["demo/W-7"], "lease expired")
        self.assertEqual(reasons["demo/W-16"], "blocked, needs an agent")
        self.assertEqual(reasons["demo/W-17"], "rejected")
        self.assertEqual(reasons["demo/W-18"], "changes requested")
        self.assertEqual(reasons["demo/W-21"], "review never requested")
        self.assertEqual(reasons["demo/W-29"], "review request is stale")
        self.assertIn("takeover", next(item.bucket.detail for item in
                                       self.queue("stalled").items
                                       if item.task.id == "demo/W-7"))

    def test_review_queue_holds_current_requests_and_says_which_stage_they_are_at(self):
        self.assertEqual(set(self.ids("review")), {"demo/W-28", "demo/W-31", "demo/W-32"})
        buckets = {item.task.id: item.bucket for item in self.queue("review").items}
        self.assertEqual(buckets["demo/W-28"].reasons, ("awaiting reviewer",))
        self.assertIn("opencode/reviewer-agent", buckets["demo/W-28"].detail)
        self.assertIn("event", buckets["demo/W-28"].detail)
        # An approval that has not become `reviewed` is not presented as still being reviewed.
        self.assertEqual(buckets["demo/W-31"].reasons, ("approved by reviewer",))
        self.assertIn("reviewed transition has not been recorded", buckets["demo/W-31"].detail)
        # A lapsed lease does not hide a review that is still valid.
        self.assertIn("expired lease", buckets["demo/W-32"].reasons)
        self.assertIn("still stands", buckets["demo/W-32"].detail)

    def test_stale_request_cannot_be_acted_on(self):
        detail = self.detail("demo/W-29")
        self.assertEqual(detail.bucket.id, "stalled")
        self.assertIn("revision 2", detail.bucket.detail)
        question = detail.questions[0]
        self.assertFalse(question.current)
        self.assertFalse(question.answered)
        self.assertEqual(question.state, "superseded")

    # -- integration and acceptance -----------------------------------------

    def test_integration_queue_separates_bound_from_incomplete(self):
        self.assertEqual(set(self.ids("integration")),
                         {"demo/W-12", "demo/W-24", "demo/W-25"})
        details = {item.task.id: item.bucket.detail for item in
                   self.queue("integration").items}
        self.assertIn("content", details["demo/W-12"])
        self.assertIn("content", details["demo/W-24"])
        self.assertIn("no content digest", details["demo/W-25"])

    def test_acceptance_queue_reports_the_receipt_it_has(self):
        self.assertEqual(set(self.ids("acceptance")), {"demo/W-13", "demo/W-26"})
        details = {item.task.id: item.bucket.detail for item in
                   self.queue("acceptance").items}
        self.assertIn(SOURCE_B[:12], details["demo/W-13"])
        self.assertIn("no receipt", details["demo/W-26"])

    # -- work and claimability ----------------------------------------------

    def test_state_and_claimability_are_separate(self):
        waiting = next(item for item in self.queue("waiting").items
                       if item.task.id == "demo/W-2")
        self.assertEqual(waiting.task.status, "ready")
        self.assertFalse(waiting.task.claimability.claimable)
        self.assertIn("unmet dependencies", waiting.bucket.detail)
        self.assertEqual(self.bucket_of("demo/W-4"), "waiting")
        self.assertIn("write-path conflict", self.detail("demo/W-4").bucket.detail)

    def test_planned_prerequisite_is_named_as_a_decision(self):
        detail = self.detail("demo/W-20")
        self.assertIn("decision: promote or plan demo/W-19", detail.bucket.detail)

    def test_claimable_rows_are_ranked_by_downstream(self):
        claimable = [item.task.id for item in self.queue("claimable").items]
        self.assertEqual(set(claimable), {"demo/W-1", "demo/W-27", "demo/W-30"})
        ranks = [len(model.find(self.view, task_id).downstream) for task_id in claimable]
        self.assertEqual(ranks, sorted(ranks, reverse=True))

    def test_live_leases_are_in_progress_with_epoch_and_remaining_time(self):
        self.assertEqual(set(self.ids("in-progress")), {"demo/W-3", "demo/W-23"})
        item = next(item for item in self.queue("in-progress").items
                    if item.task.id == "demo/W-23")
        self.assertIn("epoch 5", item.bucket.detail)
        self.assertIn("left", item.bucket.detail)
        self.assertEqual(item.task.claim.epoch, 5)

    # -- inspector -----------------------------------------------------------

    def test_inspector_exposes_state_claimability_and_fencing_facts(self):
        detail = self.detail("demo/W-23")
        self.assertEqual(detail.task.status, "claimed")
        self.assertFalse(detail.task.claimability.claimable)
        self.assertEqual(detail.task.revision, 3)
        self.assertEqual(detail.task.epoch, 1)
        self.assertEqual(detail.task.claim.epoch, 5)
        self.assertIn("live lease", " ".join(detail.task.claimability.reasons))
        self.assertEqual(detail.bucket.id, "in-progress")

    def test_review_verdict_is_traceable_to_its_answer(self):
        detail = self.detail("demo/W-12")
        review = detail.review
        self.assertIsNotNone(review)
        self.assertEqual(review.reviewer, "opencode/reviewer-agent")
        self.assertEqual(review.decision, "approve")
        self.assertEqual(review.source, SOURCE_B)
        self.assertTrue(review.bound)
        self.assertEqual(review.path_count, 2)
        answered = [question for question in detail.questions if question.answered]
        self.assertEqual(len(answered), 1)
        self.assertEqual(answered[0].answer_seq, review.answer_seq)
        self.assertEqual(answered[0].answer_decision, "approve")

    def test_integration_receipt_is_the_engine_receipt(self):
        detail = self.detail("demo/W-13")
        record = engine.find_task(self.snapshot.registry, "demo/W-13")
        history = engine.task_history(_Log(self.snapshot.events), record)
        expected = engine.integration_receipt(history)
        self.assertIsNotNone(detail.receipt)
        self.assertEqual(detail.receipt.event_seq, expected["seq"])
        self.assertEqual(detail.receipt.source, expected["integrated_source"])
        self.assertEqual(detail.receipt.digest, DIGEST_A)
        self.assertEqual(detail.receipt.path_count, 2)
        self.assertTrue(detail.receipt.complete)
        self.assertIsNone(self.detail("demo/W-12").receipt)

    def test_evidence_presence_and_its_absence_are_reported(self):
        present = self.detail("demo/W-12").evidence
        self.assertTrue(present.exists)
        self.assertGreater(present.size or 0, 0)
        missing = self.detail("demo/W-24").evidence
        self.assertFalse(missing.exists)
        self.assertEqual(missing.error, "file not found")
        absent = self.detail("demo/W-14").evidence
        self.assertEqual(absent.error, "no evidence locator recorded")

    def test_attempts_come_from_the_engine_fold(self):
        detail = self.detail("demo/W-23")
        self.assertEqual(len(detail.attempts), 1)
        attempt = detail.attempts[0]
        self.assertEqual(attempt.attempt, "demo/W-23#a1")
        self.assertEqual(attempt.state, "running")
        self.assertTrue(attempt.live)
        self.assertEqual(attempt.pid, os.getpid())
        folded = engine.attempt_views(list(self.snapshot.events))
        self.assertEqual(folded["demo/W-23#a1"]["state"], attempt.state)

    def test_event_history_is_chronological_and_append_only(self):
        detail = self.detail("demo/W-12")
        self.assertEqual([event.seq for event in detail.events],
                         sorted(event.seq for event in detail.events))
        self.assertEqual(detail.events_total, len(detail.events))
        self.assertIn("green -> reviewed", detail.events[-1].summary)
        self.assertIn("question: Review demo/W-12", detail.events[0].summary)

    def test_event_history_is_capped_and_names_the_total(self):
        for index in range(60):
            self.home.append(event="annotate", task="demo/W-16", agent="opencode/worker",
                             reason=f"note {index}", now=T0)
        snap = snapshot.read(self.home.db)
        view = model.build(snap)
        detail = control.detail(snap, view, "demo/W-16")
        self.assertEqual(len(detail.events), control.EVENT_LIMIT)
        self.assertEqual(detail.events_total, 61)  # the blocked transition plus 60 notes

    def test_dependencies_carry_state_and_revision(self):
        detail = self.detail("demo/W-2")
        self.assertEqual([link.id for link in detail.upstream], ["demo/W-1"])
        self.assertEqual(detail.upstream[0].status, "ready")
        self.assertEqual(detail.upstream[0].revision, 3)
        gates = self.detail("demo/W-1").downstream
        self.assertIn("demo/W-2", [link.id for link in gates])

    def test_unknown_task_has_no_detail(self):
        self.assertIsNone(control.detail(self.snapshot, self.view, "demo/nope"))

    # -- filters and serialisation ------------------------------------------

    def test_program_filter_scopes_rows_and_counts(self):
        room = control.build(self.snapshot, self.view, program="other")
        self.assertEqual([row.task.id for row in room.rows], ["demo/W-30"])
        self.assertEqual(room.counts["total"], 1)
        self.assertEqual(room.counts["claimable"], 1)
        self.assertEqual(sum(len(queue.items) for queue in room.queues), 1)

    def test_control_room_serialises_without_html(self):
        payload = self.room.to_dict()
        self.assertEqual([queue["id"] for queue in payload["queues"]],
                         list(control.QUEUE_ORDER))
        self.assertEqual(sum(queue["count"] for queue in payload["queues"]),
                         payload["counts"]["total"])
        self.assertEqual(len(payload["rows"]), payload["counts"]["total"])
        self.assertNotIn("<", str(payload["rows"][0]["reasons"]))

    def test_detail_serialises(self):
        payload = self.detail("demo/W-13").to_dict()
        for key in ("review", "receipt", "questions", "attempts", "events", "evidence",
                    "upstream", "downstream", "bucket"):
            self.assertIn(key, payload)


class RenderTest(unittest.TestCase):
    """Page-level facts that do not need an HTTP socket."""

    def setUp(self):
        self.home = ControlHome()
        self.addCleanup(self.home.close)
        self.snapshot = snapshot.read(self.home.db)
        self.view = model.build(self.snapshot)
        self.room = control.build(self.snapshot, self.view)
        self.version = version.inspect()
        self.signals = provenance.collect(self.snapshot, self.view, self.version)
        self.now = self.snapshot.as_of
        from datetime import datetime, timezone
        self.moment = datetime.fromisoformat(self.now.replace("Z", "+00:00")).astimezone(
            timezone.utc)

    def test_control_page_names_every_queue_and_its_count(self):
        page = pages.control_page(self.room, self.view, self.version, self.signals, self.moment)
        for queue in self.room.queues:
            if queue.id == "settled":
                continue
            self.assertIn(f'id="q-{queue.id}"', page)
            self.assertIn(f'<span class="count">({len(queue.items)})</span>', page)
        self.assertIn("Owner control room", page)
        self.assertIn("Partial data", page)  # git cannot be sampled in the fixture

    def test_a_stale_sample_is_labelled_stale(self):
        later = self.moment + timedelta(minutes=45)
        page = pages.control_page(self.room, self.view, self.version, self.signals, later)
        self.assertIn("Stale sample", page)
        self.assertIn("STALE SAMPLE", page)
        self.assertIn("45m", page)

    def test_freshness_is_per_source_not_one_timestamp(self):
        label, stale = templates.freshness("2026-11-15T12:00:00Z", self.moment, 60)
        self.assertTrue(stale)
        self.assertIn("ago", label)
        label, stale = templates.freshness(self.now, self.moment, 60)
        self.assertFalse(stale)

    def test_task_page_separates_state_from_claimability(self):
        detail = control.detail(self.snapshot, self.view, "demo/W-2")
        page = pages.task_page(detail, self.view, self.version, self.signals, self.moment)
        self.assertIn("State and claimability", page)
        self.assertIn("Why it can or cannot be claimed", page)
        self.assertIn("unmet dependencies", page)
        self.assertIn("fencing epoch (claim)", page)
        self.assertIn("Integration receipt", page)
        self.assertIn("Event history", page)
        reviewed = control.detail(self.snapshot, self.view, "demo/W-12")
        reviewed_page = pages.task_page(reviewed, self.view, self.version, self.signals,
                                        self.moment)
        self.assertIn("Append-only", reviewed_page)
        self.assertIn("green -&gt; reviewed", reviewed_page)

    def test_task_page_renders_review_and_receipt_as_separate_sections(self):
        detail = control.detail(self.snapshot, self.view, "demo/W-13")
        page = pages.task_page(detail, self.view, self.version, self.signals, self.moment)
        self.assertIn("<h2 id=\"review-h\">Review</h2>", page)
        self.assertIn("<h2 id=\"receipt-h\">Integration receipt</h2>", page)
        self.assertIn(SOURCE_B, page)
        self.assertIn(DIGEST_A, page)

    def test_every_page_has_the_accessibility_scaffold(self):
        room_page = pages.control_page(self.room, self.view, self.version, self.signals,
                                       self.moment)
        tasks_page = pages.tasks_page(self.room, self.view, self.version, self.signals,
                                      self.moment)
        detail = control.detail(self.snapshot, self.view, "demo/W-12")
        task_page = pages.task_page(detail, self.view, self.version, self.signals, self.moment)
        ops = operations.collect(self.snapshot, version.inspect())
        operations_page = templates.operations_page(ops, version.inspect())
        for name, page in (("control", room_page), ("tasks", tasks_page),
                           ("task", task_page), ("operations", operations_page)):
            with self.subTest(page=name):
                self.assertEqual(page.count("<h1"), 1)
                self.assertIn('class="skip" href="#main"', page)
                self.assertIn('<main id="main" tabindex="-1"', page)
                self.assertIn('<nav class="views" aria-label="Views">', page)
                self.assertIn('lang="en"', page)
                self.assertIn("<caption>", page)
                self.assertIn('scope="col"', page)
                self.assertIn('role="region"', page)
                self.assertIn('tabindex="0"', page)
                self.assertNotIn("<script", page)

    def test_task_text_is_escaped_everywhere(self):
        self.home.registry["tasks"].append({
            "id": "demo/W-99", "board": "demo.board", "program": "demo",
            "title": '<script>alert("t")</script>',
            "goal": "<img src=x onerror=alert(2)>",
            "status": "blocked", "blocked_needs": "human", "revision": 1, "epoch": 1,
            "owner_repo": str(self.home.repo),
        })
        self.home.append(event="transition", task="demo/W-99", agent="opencode/worker",
                         status="blocked", previous_status="green", revision=1,
                         needs="human", reason="<b>decide</b>", now=T0)
        self.home.save()
        snap = snapshot.read(self.home.db)
        view = model.build(snap)
        room = control.build(snap, view)
        version_ = version.inspect()
        signals = provenance.collect(snap, view, version_)
        page = pages.control_page(room, view, version_, signals, self.moment)
        self.assertNotIn("<script", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;", page)
        detail = control.detail(snap, view, "demo/W-99")
        task_page = pages.task_page(detail, view, version_, signals, self.moment)
        self.assertNotIn("<b>decide</b>", task_page)
        self.assertIn("&lt;b&gt;decide&lt;/b&gt;", task_page)


if __name__ == "__main__":
    unittest.main()
