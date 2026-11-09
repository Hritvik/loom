#!/usr/bin/env python3
"""Tests for the pre-commit coordination guard.

The guard is what stops one agent's `git add -A` from committing another agent's in-flight work.
Each case builds a small registry and a repo root, then checks whether the commit is refused.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]

loader = SourceFileLoader("coordination_hook", str(WORK / "hooks" / "pre-commit"))
spec = importlib.util.spec_from_loader("coordination_hook", loader)
hook = importlib.util.module_from_spec(spec)
loader.exec_module(hook)

NOW = dt.datetime(2026, 11, 5, 12, 0, tzinfo=dt.timezone.utc)


def task(task_id, repo, paths, agent, expires="2026-11-05T13:00:00Z", status="claimed"):
    return {
        "id": task_id,
        "owner_repo": str(repo),
        "status": status,
        "write_paths": paths,
        "claim": {"agent": agent, "base_revision": 0, "lease_expires": expires},
    }


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.alpha = Path(self.tmp.name) / "alpha"
        self.beta = Path(self.tmp.name) / "beta"
        self.alpha.mkdir()
        self.beta.mkdir()
        self.registry = {
            "tasks": [
                task("alpha/W-1", self.alpha, ["src/one", "docs/one"], "alpha-agent"),
                task("alpha/W-2", self.alpha, ["src/two"], "other-agent"),
                task("beta/W-1", self.beta, ["src/one"], "beta-agent"),
                task("alpha/W-3", self.alpha, ["src/three"], "stale-agent",
                     expires="2026-11-05T09:00:00Z"),
            ]
        }

    def tearDown(self):
        self.tmp.cleanup()

    def evaluate(self, staged, agent=None, root=None):
        return hook.evaluate(staged, agent, self.registry, root or self.alpha, NOW)

    def test_agent_inside_its_own_claim_is_allowed(self):
        self.assertEqual(self.evaluate(["src/one/Controller.kt", "docs/one/README.md"],
                                       agent="alpha-agent"), [])

    def test_agent_with_stray_paths_is_refused(self):
        problems = self.evaluate(["src/one/Controller.kt", "src/two/Other.kt"], agent="alpha-agent")
        self.assertTrue(problems)
        self.assertIn("outside the write paths of 'alpha-agent'", problems[0])
        self.assertIn("    src/two/Other.kt", problems)

    def test_unclaimed_agent_cannot_stage_someone_elses_work(self):
        problems = self.evaluate(["src/one/Controller.kt"], agent="coordinator")
        self.assertTrue(problems)
        self.assertIn("no live claim", problems[0])
        self.assertIn("alpha-agent", problems[0])

    def test_unclaimed_agent_may_stage_unclaimed_paths(self):
        self.assertEqual(self.evaluate(["plans/alpha/new.md"], agent="coordinator"), [])

    def test_unattributed_commit_spanning_two_claims_is_refused(self):
        problems = self.evaluate(["src/one/Controller.kt", "src/two/Other.kt"])
        self.assertTrue(problems)
        self.assertIn("span 2 live claims", problems[0])
        self.assertIn("alpha/W-1", problems[0])
        self.assertIn("alpha/W-2", problems[0])

    def test_unattributed_commit_within_one_claim_is_allowed(self):
        self.assertEqual(self.evaluate(["src/one/Controller.kt"]), [])

    def test_shared_paths_are_exempt(self):
        self.assertEqual(
            self.evaluate(["capabilities/work/store.py", "AGENTS.md", ".ecc/memory/team/x.md"]),
            [])

    def test_expired_lease_is_not_a_claim(self):
        self.assertEqual(self.evaluate(["src/three/Old.kt"]), [])

    def test_another_repositorys_claims_are_ignored(self):
        # beta-agent's claim is in beta; a commit in alpha must not be attributed to it
        self.assertEqual(self.evaluate(["src/one/Controller.kt"], root=self.alpha), [])

    def test_commit_agent_reads_the_trailer(self):
        with tempfile.TemporaryDirectory() as tmp:
            message = Path(tmp) / "COMMIT_EDITMSG"
            message.write_text("feat: thing\n\nAgent: alpha-agent\n", encoding="utf-8")
            self.assertEqual(hook.commit_agent(message), "alpha-agent")
            message.write_text("feat: thing\n", encoding="utf-8")
            self.assertIsNone(hook.commit_agent(message))


if __name__ == "__main__":
    unittest.main(verbosity=2)
