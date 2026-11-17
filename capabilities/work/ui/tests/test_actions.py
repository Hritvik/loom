"""Action gating, submission validation and execution against the real engine."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ActionHome  # noqa: E402

from loom_ui import actions, control, model, snapshot  # noqa: E402
from loom_ui.identity import Policy  # noqa: E402

OWNERS = frozenset({"human", "owner", ActionHome.HRITVIK, ActionHome.REVIEWER})


class ActionTest(unittest.TestCase):
    def setUp(self):
        self.home = ActionHome()
        self.addCleanup(self.home.close)
        self.policy = Policy.parse([f"{ActionHome.HRITVIK}:owner,implementer",
                                    f"{ActionHome.REVIEWER}:reviewer"])
        self.front_door = self.home.work_home / "loom"

    # -- helpers --------------------------------------------------------------

    def panel(self, task_id: str, identity: str | None = ActionHome.HRITVIK):
        snap = snapshot.read(self.home.db)
        view = model.build(snap)
        detail = control.detail(snap, view, task_id, OWNERS)
        self.assertIsNotNone(detail, f"no task {task_id}")
        return actions.catalogue(detail, self.policy, identity)

    def option(self, task_id: str, action: str, target: str | None = None,
               question: str | None = None, identity: str | None = ActionHome.HRITVIK):
        found = actions.find_option(self.panel(task_id, identity), action, target, question)
        self.assertIsNotNone(found, f"{action} {target or ''} not offered on {task_id}")
        return found

    def available(self, task_id: str, identity: str | None = ActionHome.HRITVIK):
        return {option.id for option in self.panel(task_id, identity).available}

    def perform(self, task_id: str, action: str, values: dict, *, target=None, question=None,
            identity: str = ActionHome.HRITVIK, revision: int | None = None,
            epoch: int | None = None):
        option = self.option(task_id, action, target, question, identity)
        self.assertTrue(option.available, option.reason)
        if epoch is None:
            claim = self.home.record_of(task_id).get("claim") or {}
            epoch = claim.get("epoch")
        if revision is None:
            revision = int(self.home.record_of(task_id).get("revision", 1))
        submission = actions.Submission(values=self.complete(option, values),
                                        revision=revision, epoch=epoch)
        command = actions.command_for(option, task_id, submission)
        return actions.execute(self.front_door, self.home.db, identity, command)

    def complete(self, option, values: dict) -> dict:
        merged = {field.name: field.default for field in option.fields}
        merged.update(values)
        return merged

    # -- the catalogue --------------------------------------------------------

    def test_claim_is_offered_only_when_the_engine_would_accept_it(self):
        claim = self.option("demo/A-1", "claim")
        self.assertTrue(claim.available)
        self.assertIn("revision 1 → 2", claim.effect)
        self.assertEqual([field.name for field in claim.fields], ["ttl"])

        conflicted = self.option("demo/W-4", "claim")
        self.assertFalse(conflicted.available)
        self.assertIn("write-path conflict", conflicted.reason)

        unmet = self.option("demo/W-2", "claim")
        self.assertFalse(unmet.available)
        self.assertIn("unmet dependencies", unmet.reason)

    def test_a_role_that_may_not_act_is_told_why(self):
        claim = self.option("demo/A-1", "claim", identity=ActionHome.REVIEWER)
        self.assertFalse(claim.available)
        self.assertIn("role does not include implementer", claim.reason)

    def test_renew_requires_a_live_lease_you_hold(self):
        self.assertTrue(self.option("demo/A-2", "renew").available)
        foreign = self.option("demo/A-8", "renew")
        self.assertFalse(foreign.available)
        self.assertIn("held by opencode/other", foreign.reason)
        lapsed = self.option("demo/A-5", "renew")
        self.assertFalse(lapsed.available)
        self.assertIn("takeover", lapsed.reason)

    def test_release_requires_the_lease_you_hold_and_a_reason(self):
        release = self.option("demo/A-2", "release")
        self.assertTrue(release.available)
        self.assertEqual([field.name for field in release.fields], ["reason"])
        self.assertNotIn("release", self.available("demo/A-1"))

    def test_takeover_waits_for_the_lock_delay(self):
        expired = self.option("demo/A-5", "takeover")
        self.assertTrue(expired.available)
        self.assertIn("epoch 1 → 2", expired.effect)

        inside = self.option("demo/A-6", "takeover")
        self.assertFalse(inside.available)
        self.assertIn("lock delay runs until", inside.reason)

    def test_the_ui_never_offers_reviewed_or_integrated(self):
        offered = {option.target for option in self.panel("demo/A-3").options
                   if option.id == "transition"}
        self.assertNotIn("reviewed", offered)
        self.assertNotIn("integrated", offered)
        self.assertIn("red", offered)

    def test_a_claimed_task_can_only_be_moved_by_its_holder(self):
        targets = [option for option in self.panel("demo/A-8").options
                   if option.id == "transition"]
        self.assertTrue(targets)
        for option in targets:
            self.assertFalse(option.available, option.target)
            self.assertIn("claimed by opencode/other", option.reason)

    def test_blocked_work_asks_who_can_unblock_it(self):
        ready = self.option("demo/A-4", "transition", "ready")
        self.assertIn("reason", [field.name for field in ready.fields])
        self.assertNotIn("accepted", self.available("demo/A-4"))

    def test_acceptance_is_the_owners_and_asks_for_evidence(self):
        accepted = self.option("demo/A-7", "transition", "accepted")
        self.assertTrue(accepted.available)
        self.assertEqual([field.name for field in accepted.fields], ["evidence"])

        implementer = Policy.parse(["impl:implementer"])
        snap = snapshot.read(self.home.db)
        view = model.build(snap)
        detail = control.detail(snap, view, "demo/A-7", OWNERS)
        panel = actions.catalogue(detail, implementer, "impl")
        refused = actions.find_option(panel, "transition", "accepted")
        self.assertFalse(refused.available)
        self.assertIn("owner", refused.reason)

    def test_answer_is_offered_per_question_to_its_addressee(self):
        owner_questions = [option for option in self.panel("demo/A-1").options
                           if option.id == "answer"]
        self.assertEqual(len(owner_questions), 1)
        decision = next(f for f in owner_questions[0].fields if f.name == "decision")
        self.assertEqual([value for value, _ in decision.options], ["answer"])

        review = [option for option in self.panel("demo/A-3", ActionHome.REVIEWER).options
                  if option.id == "answer"]
        self.assertEqual(len(review), 1)
        decision = next(f for f in review[0].fields if f.name == "decision")
        self.assertEqual([value for value, _ in decision.options],
                         ["approve", "changes requested"])

        self.assertNotIn("answer", self.available("demo/A-3", ActionHome.HRITVIK))

    def test_an_unpaired_session_is_offered_nothing(self):
        panel = self.panel("demo/A-1", None)
        self.assertTrue(panel.enabled)
        self.assertIsNone(panel.identity)
        self.assertFalse(panel.available)
        for option in panel.options:
            self.assertEqual(option.reason, "you are not signed in")

    def test_an_unconfigured_server_offers_nothing(self):
        snap = snapshot.read(self.home.db)
        view = model.build(snap)
        detail = control.detail(snap, view, "demo/A-1", OWNERS)
        panel = actions.catalogue(detail, Policy(()), None)
        self.assertFalse(panel.enabled)
        self.assertFalse(panel.available)
        self.assertIn("no configured human identity",
                      " ".join(option.reason or "" for option in panel.options))

    # -- validation -----------------------------------------------------------

    def test_the_browser_may_not_name_the_actor(self):
        claim = self.option("demo/A-1", "claim")
        base = {"revision": "1", "ttl": "30m"}
        for name in ("agent", "actor", "identity", "user", "owner", "acting_as"):
            with self.subTest(name=name):
                submission, problems = actions.validate(claim, {**base, name: "root"})
                self.assertIsNone(submission)
                self.assertIn("may not name the acting identity", " ".join(problems))

    def test_unknown_fields_are_refused(self):
        claim = self.option("demo/A-1", "claim")
        submission, problems = actions.validate(claim, {"revision": "1", "shell": "rm -rf /"})
        self.assertIsNone(submission)
        self.assertIn("unknown field 'shell'", " ".join(problems))

    def test_required_fields_are_required(self):
        release = self.option("demo/A-2", "release")
        submission, problems = actions.validate(release, {"revision": "1"})
        self.assertIsNone(submission)
        self.assertIn("Reason is required", " ".join(problems))

    def test_field_bounds_and_patterns(self):
        claim = self.option("demo/A-1", "claim")
        _, problems = actions.validate(claim, {"revision": "1", "ttl": "1y"})
        self.assertIn("Lease length is not in the expected form", " ".join(problems))
        _, problems = actions.validate(claim, {"revision": "1", "ttl": "99999d"})
        self.assertIn("not in the expected form", " ".join(problems))

        ask = self.option("demo/A-1", "ask")
        _, problems = actions.validate(ask, {"revision": "1", "to": "a b", "question": "why"})
        self.assertIn("Ask is not in the expected form", " ".join(problems))
        _, problems = actions.validate(ask, {"revision": "1", "to": "x", "question": "   "})
        self.assertIn("Question is required", " ".join(problems))

        accepted = self.option("demo/A-7", "transition", "accepted")
        _, problems = actions.validate(accepted, {"revision": "1",
                                                  "evidence": "../../etc/passwd"})
        self.assertIn("Evidence locator is not in the expected form", " ".join(problems))

    def test_a_decision_must_be_one_of_the_offered_values(self):
        option = [o for o in self.panel("demo/A-3", ActionHome.REVIEWER).options
                  if o.id == "answer"][0]
        submission, problems = actions.validate(
            option, {"revision": "1", "decision": "maybe", "note": "hmm"})
        self.assertIsNone(submission)
        self.assertIn("must be one of the offered values", " ".join(problems))

    def test_revision_and_epoch_are_mandatory_and_numeric(self):
        claim = self.option("demo/A-1", "claim")
        _, problems = actions.validate(claim, {"ttl": "30m"})
        self.assertIn("revision being presented", " ".join(problems))
        _, problems = actions.validate(claim, {"revision": "one", "ttl": "30m"})
        self.assertIn("revision being presented", " ".join(problems))
        _, problems = actions.validate(claim, {"revision": "1", "epoch": "x", "ttl": "30m"})
        self.assertIn("epoch being presented", " ".join(problems))

    def test_optional_fields_fall_back_to_the_engine_default(self):
        claim = self.option("demo/A-1", "claim")
        submission, problems = actions.validate(claim, {"revision": "1"})
        self.assertEqual(problems, ())
        self.assertEqual(submission.values["ttl"], "30m")

    # -- argument building ----------------------------------------------------

    def test_commands_are_argument_arrays(self):
        claim = self.option("demo/A-1", "claim")
        submission = actions.Submission(values={"ttl": "30m"}, revision=1, epoch=None)
        self.assertEqual(actions.command_for(claim, "demo/A-1", submission),
                         ["claim", "demo/A-1", "--revision", "1", "--ttl", "30m"])

        transition = self.option("demo/A-4", "transition", "ready")
        submission = actions.Submission(values={"reason": "decision taken"},
                                        revision=1, epoch=None)
        self.assertEqual(actions.command_for(transition, "demo/A-4", submission),
                         ["transition", "demo/A-4", "--revision", "1", "--to", "ready",
                          "--reason", "decision taken"])

        answer = [o for o in self.panel("demo/A-1").options if o.id == "answer"][0]
        submission = actions.Submission(
            values={"decision": "answer", "note": "use the second wording"},
            revision=1, epoch=None)
        command = actions.command_for(answer, "demo/A-1", submission)
        self.assertEqual(command[:2], ["answer", "demo/A-1"])
        self.assertEqual(command[2], "--question")

    def test_an_unknown_verb_cannot_be_executed(self):
        option = actions.Option(id="tick", label="Tick", summary="", effect="",
                                available=True, reason=None, roles=())
        with self.assertRaises(ValueError):
            actions.command_for(option, "demo/A-1",
                                actions.Submission(values={}, revision=1, epoch=None))

    def test_operational_verbs_have_no_action(self):
        self.assertEqual(actions.ENGINE_VERBS,
                         frozenset({"claim", "renew", "release", "takeover", "transition",
                                    "ask", "answer"}))
        for verb in ("tick", "workers", "schedule", "deploy", "linear", "annotate", "create",
                     "reconcile", "migrate"):
            self.assertNotIn(verb, actions.ENGINE_VERBS)

    # -- classification -------------------------------------------------------

    def test_exit_codes_are_classified_without_guessing(self):
        self.assertEqual(actions.classify(0, ""), "success")
        self.assertEqual(actions.classify(2, "error: revision conflict on x"), "conflict")
        self.assertEqual(actions.classify(2, "error: stale epoch on x"), "conflict")
        self.assertEqual(actions.classify(2, "error: lease on x expired at y"), "conflict")
        self.assertEqual(actions.classify(2, "error: illegal transition ready -> accepted"),
                         "denied")
        self.assertEqual(actions.classify(1, "Traceback"), "failed")
        self.assertEqual(actions.classify(None, ""), "failed")

    # -- execution against the engine -----------------------------------------

    def test_claim_succeeds_and_is_attributed_to_the_session_identity(self):
        run = self.perform("demo/A-1", "claim", {"ttl": "30m"})
        self.assertTrue(run.ok, run.stderr)
        record = self.home.record_of("demo/A-1")
        self.assertEqual(record["status"], "claimed")
        self.assertEqual(record["claim"]["agent"], ActionHome.HRITVIK)
        self.assertEqual(record["claim"]["epoch"], 2)
        events = [pair[1] for pair in self._events()]
        claim_events = [event for event in events if event.get("event") == "claim"
                        and event.get("task") == "demo/A-1"]
        self.assertEqual(len(claim_events), 1)
        self.assertEqual(claim_events[0]["agent"], ActionHome.HRITVIK)

    def test_an_ambient_agent_variable_cannot_change_the_actor(self):
        previous = os.environ.get("WORK_AGENT")
        os.environ["WORK_AGENT"] = "impostor"
        try:
            run = self.perform("demo/A-1", "claim", {})
        finally:
            if previous is None:
                os.environ.pop("WORK_AGENT", None)
            else:
                os.environ["WORK_AGENT"] = previous
        self.assertTrue(run.ok, run.stderr)
        self.assertEqual(self.home.record_of("demo/A-1")["claim"]["agent"], ActionHome.HRITVIK)

    def test_a_stale_revision_is_refused_by_the_engine(self):
        run = self.perform("demo/A-1", "claim", {}, revision=99)
        self.assertEqual(run.kind, "conflict")
        self.assertIn("revision conflict", run.stderr)
        self.assertEqual(self.home.record_of("demo/A-1")["status"], "ready")

    def test_gating_agrees_with_the_engine_on_every_refusal(self):
        # Each of these is shown as unavailable; the engine refuses the same command, so the
        # gate is not a stricter or looser copy of the rule.
        cases = [
            ("demo/W-4", ["claim", "demo/W-4", "--revision", "3", "--ttl", "30m"]),
            ("demo/W-2", ["claim", "demo/W-2", "--revision", "3", "--ttl", "30m"]),
            ("demo/A-5", ["renew", "demo/A-5", "--revision", "1", "--epoch", "1",
                          "--ttl", "30m"]),
            ("demo/A-8", ["renew", "demo/A-8", "--revision", "1", "--epoch", "4",
                          "--ttl", "30m"]),
            ("demo/A-8", ["transition", "demo/A-8", "--revision", "1", "--to", "red",
                          "--epoch", "4"]),
        ]
        for task_id, command in cases:
            with self.subTest(task=task_id, command=command[0]):
                panel = self.panel(task_id)
                option = actions.find_option(panel, command[0],
                                             command[command.index("--to") + 1]
                                             if "--to" in command else None)
                self.assertFalse(option.available, f"{task_id} {command[0]} was offered")
                run = actions.execute(self.front_door, self.home.db, ActionHome.HRITVIK, command)
                self.assertFalse(run.ok, f"the engine accepted {command}")

    def test_every_offered_action_actually_works(self):
        run = self.perform("demo/A-2", "renew", {"ttl": "45m"})
        self.assertTrue(run.ok, run.stderr)
        run = self.perform("demo/A-2", "transition", {"reason": "step one"}, target="red")
        self.assertTrue(run.ok, run.stderr)
        self.assertEqual(self.home.record_of("demo/A-2")["status"], "red")
        run = self.perform("demo/A-2", "release", {"reason": "handing back"}, epoch=2)
        self.assertTrue(run.ok, run.stderr)
        self.assertEqual(self.home.record_of("demo/A-2")["status"], "ready")

        run = self.perform("demo/A-5", "takeover", {"reason": "previous worker stopped",
                                                "ttl": "30m"})
        self.assertTrue(run.ok, run.stderr)
        tally = self.home.record_of("demo/A-5")
        self.assertEqual(tally["claim"]["agent"], ActionHome.HRITVIK)
        self.assertEqual(tally["claim"]["epoch"], 2)

        run = self.perform("demo/A-4", "transition", {"reason": "decided", "needs": "agent"},
                       target="ready")
        self.assertTrue(run.ok, run.stderr)
        self.assertEqual(self.home.record_of("demo/A-4")["status"], "ready")

        run = self.perform("demo/A-7", "transition", {"evidence": "evidence/demo-A-7.md"},
                       target="accepted")
        self.assertTrue(run.ok, run.stderr)
        self.assertEqual(self.home.record_of("demo/A-7")["status"], "accepted")

        run = self.perform("demo/A-1", "ask", {"to": "opencode/worker", "question": "Is this cut ok?"})
        self.assertTrue(run.ok, run.stderr)

        answer = [o for o in self.panel("demo/A-3", ActionHome.REVIEWER).options
                  if o.id == "answer"][0]
        submission = actions.Submission(
            values={"decision": "approve", "note": "checked the diff"},
            revision=1, epoch=None)
        run = actions.execute(self.front_door, self.home.db, ActionHome.REVIEWER,
                              actions.command_for(answer, "demo/A-3", submission))
        self.assertTrue(run.ok, run.stderr)

    def test_a_failing_front_door_is_reported_not_hidden(self):
        run = actions.execute(Path("/nonexistent/loom"), self.home.db, "hritvik",
                              ["claim", "demo/A-1", "--revision", "1"])
        self.assertEqual(run.kind, "failed")
        self.assertIn("does not exist", run.stderr)

    def test_a_timeout_is_reported_as_unknown_outcome(self):
        def slow(*args, **kwargs):
            raise actions.subprocess.TimeoutExpired(cmd="loom", timeout=1)

        run = actions.execute(self.front_door, self.home.db, "hritvik",
                              ["claim", "demo/A-1", "--revision", "1"], timeout=1, runner=slow)
        self.assertEqual(run.kind, "timeout")
        self.assertIn("may or may not have changed", run.stderr)

    # -- audit -----------------------------------------------------------------

    def test_the_audit_event_is_the_one_that_was_written(self):
        before = snapshot.read(self.home.db)
        old_head = before.head_seq
        self.assertTrue(self.perform("demo/A-1", "claim", {}).ok)
        after = snapshot.read(self.home.db)
        event = actions.audit_event(after.events, "demo/A-1", old_head, ActionHome.HRITVIK)
        self.assertIsNotNone(event)
        self.assertEqual(event["event"], "claim")
        self.assertEqual(event["agent"], ActionHome.HRITVIK)
        self.assertGreater(event["seq"], old_head)

    def test_the_audit_event_is_not_an_attempt_record(self):
        before = snapshot.read(self.home.db)
        self.assertTrue(self.perform("demo/A-1", "claim", {}).ok)
        after = snapshot.read(self.home.db)
        event = actions.audit_event(after.events, "demo/A-1", before.head_seq,
                                    ActionHome.HRITVIK)
        self.assertNotEqual(event["event"], "attempt")

    def test_no_event_means_no_event(self):
        after = snapshot.read(self.home.db)
        self.assertIsNone(actions.audit_event(after.events, "demo/A-1", after.head_seq,
                                              ActionHome.HRITVIK))
        self.assertIsNone(actions.audit_event(after.events, "demo/A-1", 0, "someone-else"))

    # -- helpers ---------------------------------------------------------------

    def _events(self):
        from store import SqliteStore

        store = SqliteStore(self.home.root, self.home.db)
        try:
            return store.read_events()
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
