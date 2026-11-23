#!/usr/bin/env python3
"""Adoption: closing work that already exists, without inventing a receipt.

Some tasks describe work that landed before the registry did. There is no diff to review and no
candidate commit, so an integration receipt would be a fabrication. `loom adopt` records the
disposition instead: terminal, evidenced, and honest about why it is not an integration. These tests
cover the promises: evidence and a reason are mandatory, the event records both plus the prior state,
an adopted task satisfies its dependents, it cannot be claimed, and the validator stays clean.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from test_work import Workspace, run, validate


class AdoptTest(unittest.TestCase):
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

    def adopt(self, *args, expect=0):
        return self.invoke("adopt", *args, "--agent", "agent-a", expect=expect)

    def adopt_events(self):
        return [record for record in self.ws.events() if record.get("event") == "adopt"]

    def test_adopting_records_evidence_and_reason_and_is_terminal(self):
        out = self.adopt("demo/W-3", "--revision", "0",
                         "--evidence", "src/three present since the initial commit",
                         "--reason", "inherited work; no diff to review")
        self.assertIn("adopted", out.stdout)

        record = self.ws.task_record("demo/W-3")
        self.assertEqual(record["status"], "adopted")
        self.assertEqual(record["adopted"]["reason"], "inherited work; no diff to review")
        self.assertEqual(record["adopted"]["by"], "agent-a")

        events = self.adopt_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["previous_status"], "draft")
        self.assertEqual(events[0]["reason"], "inherited work; no diff to review")

        # Terminal: an adopted task is never claimable again.
        refused = run("--root", str(self.ws.root), "claim", "demo/W-3",
                      "--revision", "1", "--agent", "agent-b")
        self.assertNotEqual(refused.returncode, 0)
        self.assertNotIn("demo/W-3", self.invoke("list", "--ready").stdout)

    def test_evidence_and_reason_are_both_required(self):
        missing_evidence = self.adopt("demo/W-3", "--revision", "0",
                                      "--reason", "no evidence", expect=2)
        self.assertIn("evidence", missing_evidence.stderr)
        self.assertEqual(self.ws.task_record("demo/W-3")["status"], "draft")

    def test_adoption_satisfies_dependents(self):
        self.adopt("demo/W-1", "--revision", "0",
                   "--evidence", "src/one present on main since the initial commit",
                   "--reason", "inherited work")
        listing = self.invoke("list", "--ready").stdout
        self.assertIn("demo/W-2", listing)

    def test_adopting_a_claimed_task_is_refused(self):
        self.invoke("claim", "demo/W-1", "--revision", "0", "--agent", "agent-b")
        refused = run("--root", str(self.ws.root), "adopt", "demo/W-1",
                      "--revision", "1", "--evidence", "x", "--reason", "y",
                      "--agent", "agent-a")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("illegal transition", refused.stderr)

    def test_the_validator_accepts_an_adopted_task(self):
        self.adopt("demo/W-3", "--revision", "0",
                   "--evidence", "src/three present at HEAD", "--reason", "inherited work")
        clean = validate(self.ws.root)
        self.assertEqual(clean.returncode, 0, clean.stdout)


if __name__ == "__main__":
    unittest.main()
