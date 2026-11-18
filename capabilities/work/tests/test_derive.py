#!/usr/bin/env python3
"""Tests for the derive handlers: one event each, shadowed before they write, safe to run twice.

The tick used to be a fixed sequence of phases, so a phase that ran twice could act twice and the
order they ran in was load-bearing. These tests hold the handlers to the three properties that
replace that: their projection equals the tick's before they are allowed to write, the receipt goes
through the verified review path and writes nothing when it refuses, and the rework handler records
a flag without ever touching a claim.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
WORK = TESTS.parent
sys.path.insert(0, str(WORK))
sys.path.insert(0, str(TESTS))

import engine  # noqa: E402 — the module under test
from store import SqliteStore  # noqa: E402
import test_work  # noqa: E402
from test_work import T0  # noqa: E402

REVIEWER = "opencode/reviewer-agent"


def event_shape(events):
    """What the log says, without the bookkeeping that makes two runs differ."""
    keys = ("event", "task", "status", "revision", "request", "answer", "reviewer",
            "decision", "to", "role", "agent")
    return sorted(json.dumps({key: event.get(key) for key in keys}, sort_keys=True)
                  for event in events)


class DeriveTest(unittest.TestCase):
    """A disposable work home, driven through the CLI the way a coordinator drives it.

    The harness is the one in `test_work`, repeated here rather than inherited so this module's
    tests are only this module's tests when the suite is filtered to it.
    """

    def setUp(self):
        self.ws = test_work.Workspace()

    def tearDown(self):
        self.ws.close()

    def invoke(self, *args, expect=0):
        result = test_work.run("--root", str(self.ws.root), *self._with_epoch(args))
        self.assertEqual(
            result.returncode, expect,
            f"exit {result.returncode} (expected {expect})\n"
            f"stdout:{result.stdout}\nstderr:{result.stderr}",
        )
        return result

    def _with_epoch(self, args):
        """Present the epoch the claim was issued, the way a well-behaved client does."""
        if not args or args[0] not in {"transition", "renew", "release"} or "--epoch" in args:
            return args
        task_id = next((arg for arg in args[1:] if not arg.startswith("--")), None)
        agent = args[args.index("--agent") + 1] if "--agent" in args else None
        if not task_id or not agent:
            return args
        try:
            claim = self.ws.task_record(task_id).get("claim") or {}
        except (StopIteration, KeyError):
            return args
        if claim.get("agent") == agent and claim.get("epoch") is not None:
            return (*args, "--epoch", str(claim["epoch"]))
        return args

    def commit(self, message="Review candidate", *, path=None, content=None):
        """A real commit in the fixture repository, so a review has something to verify."""
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

    # -- fixtures ---------------------------------------------------------

    def reviewable(self, task_id, agent="agent-a"):
        """A task claimed, moved green, and given a commit a reviewer can look at."""
        path = f"src/{task_id.rsplit('/', 1)[-1].lower()}"
        evidence = f"evidence/{task_id.rsplit('/', 1)[-1]}.md"
        self.invoke("create", "--agent", "agent-a", "--now", T0,
                    "--id", task_id, "--board", "demo.board",
                    "--title", f"Task {task_id}", "--goal", "Fixture task",
                    "--status", "ready", "--write-path", path)
        self.invoke("claim", task_id, "--revision", "0", "--ttl", "30m",
                    "--agent", agent, "--now", T0)
        self.invoke("transition", task_id, "--revision", "1", "--to", "green",
                    "--agent", agent, "--evidence", evidence, "--now", T0)
        return self.commit(f"Work for {task_id}", path=path, content=f"{task_id}\n")

    def ask_review(self, task_id, source, reviewer=REVIEWER):
        self.invoke("ask", task_id, "--to", reviewer, "--question", f"Review {task_id}",
                    "--review-source", source,
                    "--review-evidence", f"evidence/{task_id.rsplit('/', 1)[-1]}.md",
                    "--agent", "agent-b", "--now", T0)
        return str(self.ws.events()[-1]["seq"])

    def answer_review(self, task_id, request, decision, reviewer=REVIEWER):
        self.invoke("answer", task_id, "--question", str(request), "--decision", decision,
                    "--note", f"{decision} after reading the change",
                    "--agent", reviewer, "--now", T0)
        return str(self.ws.events()[-1]["seq"])

    def three_handlers(self):
        """One task per handler: an approval, a stale request, and a changes-requested answer."""
        approved = self.reviewable("demo/W-5")
        approval = self.answer_review("demo/W-5", self.ask_review("demo/W-5", approved), "approve")
        stale = self.reviewable("demo/W-6")
        self.ask_review("demo/W-6", stale, reviewer="opencode/reviewer")
        reworked = self.reviewable("demo/W-7")
        rework_request = self.ask_review("demo/W-7", reworked)
        rework_answer = self.answer_review("demo/W-7", rework_request, "changes requested")
        return {"approval": approval, "rework_request": rework_request,
                "rework_answer": rework_answer}

    def derive_args(self, **overrides):
        values = {"reviewer_identity": REVIEWER, "coordinator": engine.REVIEW_COORDINATOR,
                  "humans": None, "known_agents": None, "now": T0}
        values.update(overrides)
        return argparse.Namespace(**values)

    def apply_derive(self, order=engine.DERIVE_KINDS, db=None):
        store = SqliteStore(self.ws.root, db or self.ws.db)
        try:
            with store.lock():
                return engine.apply_derive_pass(store, self.derive_args(), order)
        finally:
            store.close()

    def projection(self, db=None):
        store = SqliteStore(self.ws.root, db or self.ws.db)
        try:
            return engine.derive_projection(store, store.load(),
                                            engine.derive_policy(self.derive_args()))
        finally:
            store.close()

    def events(self, db=None):
        store = SqliteStore(self.ws.root, db or self.ws.db)
        try:
            return [record for _, record in store.read_events()]
        finally:
            store.close()

    def tick_namespace(self, *extra):
        return engine.build_parser().parse_args(
            ["--root", str(self.ws.root), "--db", str(self.ws.db), "tick",
             "--now", T0, "--reviewer-identity", REVIEWER, *extra])

    # -- the tick runs the phases as handlers ------------------------------

    def test_an_applying_tick_runs_the_phases_as_handlers(self):
        self.three_handlers()
        payload = json.loads(self.invoke(
            "tick", "--apply", "--max-implementer", "0", "--max-reviewer", "0",
            "--no-integrate", "--worker-command", "/usr/bin/true",
            "--reviewer-identity", REVIEWER, "--json").stdout)

        self.assertTrue(payload["deriveShadow"]["equal"], payload["deriveShadow"]["divergences"])
        self.assertEqual(payload["deriveShadow"]["divergences"], [])
        # The three handlers did the work, not the phases they replaced: the receipt recorded a
        # review, the stale request was re-issued, and the changes-requested answer left a flag.
        self.assertEqual(self.ws.task_record("demo/W-5")["status"], "reviewed")
        self.assertEqual([item["task"] for item in payload["reviewReissued"]], ["demo/W-6"])
        self.assertEqual(payload["reworkFlagged"], ["demo/W-7"])
        flags = [event for event in self.events() if event.get("event") == "rework"]
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["task"], "demo/W-7")
        self.assertIn("review_digest", [event for event in self.events()
                                        if event.get("event") == "transition"
                                        and event.get("task") == "demo/W-5"
                                        and event.get("status") == "reviewed"][-1])

    def test_an_applying_tick_does_not_flag_a_rework_twice(self):
        self.three_handlers()
        tick = ["tick", "--apply", "--max-implementer", "0", "--max-reviewer", "0",
                "--no-integrate", "--worker-command", "/usr/bin/true",
                "--reviewer-identity", REVIEWER, "--now", T0]
        self.invoke(*tick)
        first = len([event for event in self.events() if event.get("event") == "rework"])
        self.invoke(*tick)
        second = len([event for event in self.events() if event.get("event") == "rework"])
        self.assertEqual(first, 1)
        self.assertEqual(second, 1, "a second pass flagged the same answer again")

    # -- the projection the handlers agree on -----------------------------

    def test_the_shadow_projection_equals_the_tick_projection_for_consecutive_passes(self):
        self.three_handlers()
        human = self.invoke("foldcheck", "--derive", "--reviewer-identity", REVIEWER)
        self.assertIn("equals the tick projection (3 intent(s))", human.stdout)

        passes = []
        for _ in range(2):
            result = self.invoke("foldcheck", "--derive", "--json",
                                 "--reviewer-identity", REVIEWER)
            passes.append(json.loads(result.stdout))

        self.assertTrue(passes[0]["equal"], passes[0]["divergences"])
        self.assertEqual(passes[0]["divergences"], [])
        # A second pass over the same log derives the same thing: shadowing writes nothing, so it
        # cannot quietly change its own answer between passes.
        self.assertEqual(passes[0]["handlers"], passes[1]["handlers"])
        self.assertEqual([item["task"] for item in passes[0]["handlers"]["receipt"]], ["demo/W-5"])
        self.assertEqual([item["task"] for item in passes[0]["handlers"]["review"]], ["demo/W-6"])
        self.assertEqual([item["task"] for item in passes[0]["handlers"]["rework"]], ["demo/W-7"])

    def test_the_comparison_can_fail_in_either_direction(self):
        intent = {"kind": "receipt", "task": "demo/W-1", "revision": 2,
                  "reviewer": "reviewer-r", "answer": 9}
        empty = {kind: [] for kind in engine.DERIVE_KINDS}
        derived = {**empty, "receipt": [intent]}
        self.assertEqual(engine.compare_projections(derived, empty),
                         ["receipt demo/W-1: the handler derives it, the tick does not"])
        self.assertEqual(engine.compare_projections(empty, derived),
                         ["receipt demo/W-1: the tick derives it, the handler does not"])
        self.assertEqual(engine.compare_projections(derived, dict(derived)), [])

    def test_a_divergent_projection_blocks_the_handlers_and_names_itself(self):
        self.three_handlers()
        original = engine.HANDLERS["receipt"].derive
        # The failure this guards: a handler reading the log differently from the phase it replaces.
        engine.HANDLERS["receipt"].derive = lambda records, registry, policy: []
        try:
            store = SqliteStore(self.ws.root, self.ws.db)
            try:
                with store.lock():
                    with contextlib.redirect_stdout(io.StringIO()) as out:
                        engine.tick_pass(self.tick_namespace(
                            "--apply", "--max-implementer", "0", "--max-reviewer", "0",
                            "--no-integrate", "--worker-command", "/usr/bin/true"), store)
            finally:
                store.close()
        finally:
            engine.HANDLERS["receipt"].derive = original

        self.assertIn("derive: the handler projection disagrees with the tick", out.getvalue())
        self.assertIn("receipt demo/W-5", out.getvalue())
        # The pass refused to write through the divergent handler and did the work the way it
        # always has, so a bad derivation costs a reported line, not a stalled review.
        self.assertEqual(self.ws.task_record("demo/W-5")["status"], "reviewed")
        self.assertEqual([event for event in self.events() if event.get("event") == "rework"],
                         [])

    def test_a_divergent_projection_is_reported_by_the_check_with_a_failing_exit(self):
        self.three_handlers()
        original = engine.HANDLERS["rework"].derive
        engine.HANDLERS["rework"].derive = lambda records, registry, policy: []
        try:
            store = SqliteStore(self.ws.root, self.ws.db)
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    code = engine.derive_check(
                        argparse.Namespace(json=False, reviewer_identity=REVIEWER,
                                           coordinator=engine.REVIEW_COORDINATOR,
                                           humans=None, known_agents=None, now=T0), store)
            finally:
                store.close()
        finally:
            engine.HANDLERS["rework"].derive = original
        self.assertEqual(code, 1)
        self.assertIn("DIVERGED rework demo/W-7: the tick derives it, the handler does not",
                      out.getvalue())

    # -- the receipt handler ----------------------------------------------

    def test_the_receipt_handler_writes_through_the_verified_review_path(self):
        answers = self.three_handlers()
        report = self.apply_derive()

        task = self.ws.task_record("demo/W-5")
        self.assertEqual(task["status"], "reviewed")
        transition = [event for event in self.events()
                      if event.get("event") == "transition"
                      and event.get("task") == "demo/W-5"
                      and event.get("status") == "reviewed"][-1]
        # The digest is `verified_review`'s own receipt: it can only be present if the verified
        # review path ran and re-read the approved commit.
        self.assertTrue(transition.get("review_digest"))
        self.assertEqual(str(transition.get("review_answer")), answers["approval"])
        self.assertEqual(task["review"]["digest"], transition["review_digest"])
        self.assertEqual(len(task["review"]["paths"]), 1)
        self.assertEqual([item["task"] for item in report["receipt"]["applied"]], ["demo/W-5"])

    def test_the_receipt_handler_writes_nothing_when_the_verified_path_refuses(self):
        source = self.reviewable("demo/W-5")
        request = self.ask_review("demo/W-5", source)
        # An approval the log shows but the verified path cannot accept: it names an agent the
        # request was never addressed to. `answer` refuses to write that; a hand append does not,
        # which is the writer the verification exists for.
        store = SqliteStore(self.ws.root, self.ws.db)
        try:
            with store.lock():
                store.append_event(event="answer", task="demo/W-5", agent="agent-b", to="agent-b",
                                   in_reply_to=int(request), decision="approve",
                                   note="rubber stamp", revision=2, status="green",
                                   review_source=source, review_evidence="evidence/W-5.md",
                                   now=T0)
        finally:
            store.close()

        before = len(self.events())
        report = self.apply_derive()

        self.assertEqual(self.ws.task_record("demo/W-5")["status"], "green")
        self.assertNotIn("review", self.ws.task_record("demo/W-5"))
        self.assertEqual(len(self.events()), before, "the refused receipt wrote an event")
        refused = report["receipt"]["refused"]
        self.assertEqual([item["task"] for item in refused], ["demo/W-5"])
        self.assertIn("approval must match the reviewer", refused[0]["error"])
        self.assertEqual(report["receipt"]["applied"], [])

    # -- the rework handler ------------------------------------------------
    def test_the_rework_flag_is_invisible_to_the_event_validator(self):
        source = self.reviewable("demo/W-5")
        request = self.ask_review("demo/W-5", source)
        self.answer_review("demo/W-5", request, "changes requested")
        before = test_work.validate(self.ws.root)
        self.apply_derive()
        after = test_work.validate(self.ws.root)
        # A flag is not a state change: the validator sees the same registry and the same log
        # contract after it as before it. (The fixture's own error — a centrally created task the
        # packet board does not carry — is present in both.)
        self.assertEqual(after.stdout.replace("events: 8", "events: 7"),
                         before.stdout)

    def test_the_rework_handler_flags_and_never_touches_the_claim(self):
        source = self.reviewable("demo/W-5")
        request = self.ask_review("demo/W-5", source)
        answer = self.answer_review("demo/W-5", request, "changes requested")
        before = self.ws.task_record("demo/W-5")

        report = self.apply_derive()
        after = self.ws.task_record("demo/W-5")

        self.assertEqual(after["claim"], before["claim"])
        self.assertEqual(after["status"], "green")
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual([event for event in self.events()
                          if event.get("event") in {"release", "takeover"}], [])
        flags = [event for event in self.events() if event.get("event") == "rework"]
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["task"], "demo/W-5")
        self.assertEqual(flags[0]["request"], int(request))
        self.assertEqual(flags[0]["answer"], int(answer))
        self.assertEqual(flags[0]["owner"], before["claim"]["agent"])
        self.assertEqual([item["task"] for item in report["rework"]["applied"]], ["demo/W-5"])

    def test_an_intent_that_carries_authority_is_refused(self):
        self.three_handlers()
        for kind, intents in self.projection().items():
            for intent in intents:
                self.assertEqual(engine.AUTHORITY_KEYS & set(intent), set(), intent)
        with self.assertRaises(engine.WorkError):
            engine.assert_no_authority({"kind": "rework", "task": "demo/W-7", "action": "release"})
        store = SqliteStore(self.ws.root, self.ws.db)
        try:
            with store.lock():
                with self.assertRaises(engine.WorkError):
                    engine.HANDLERS["rework"].apply(store, [{
                        "kind": "rework", "task": "demo/W-7", "revision": 2,
                        "request": 1, "answer": 2, "action": "release"}], self.derive_args())
        finally:
            store.close()

    # -- run twice ---------------------------------------------------------

    def test_a_second_run_of_the_handlers_changes_nothing(self):
        self.three_handlers()
        first = self.apply_derive()
        after_first = len(self.events())
        registry_after_first = engine.canonical_registry(self.ws.load())
        flags_after_first = len([event for event in self.events()
                                 if event.get("event") == "rework"])

        second = self.apply_derive()

        self.assertEqual(len(self.events()), after_first, "a second run appended events")
        self.assertEqual(len([event for event in self.events()
                              if event.get("event") == "rework"]), flags_after_first)
        self.assertEqual(engine.canonical_registry(self.ws.load()), registry_after_first)
        self.assertEqual(sum(len(second[kind]["applied"]) for kind in engine.DERIVE_KINDS), 0)
        self.assertEqual(sum(len(first[kind]["applied"]) for kind in engine.DERIVE_KINDS), 3)

    def test_the_handlers_reach_the_same_state_in_any_order(self):
        self.three_handlers()
        store = SqliteStore(self.ws.root, self.ws.db)
        try:
            with store.lock():
                base = store.backup_to(self.ws.root / "baseline.db")
        finally:
            store.close()

        forward = ("receipt", "review", "rework")
        reverse = ("rework", "review", "receipt")
        self.apply_derive(order=forward)
        forward_registry = engine.canonical_registry(self.ws.load())
        forward_events = event_shape(self.events())

        self.apply_derive(order=reverse, db=base)
        reverse_registry = engine.canonical_registry(
            SqliteStore(self.ws.root, base).load())
        reverse_events = event_shape(self.events(db=base))

        self.assertEqual(reverse_registry, forward_registry)
        self.assertEqual(reverse_events, forward_events)


if __name__ == "__main__":
    unittest.main(verbosity=2)
