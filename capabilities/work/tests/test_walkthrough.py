#!/usr/bin/env python3
"""The worked example is part of the documentation, so the suite runs it.

`docs/adopting.md` tells a new adopter to run `examples/walkthrough/walkthrough.py` and look for
`event chain OK`. Nothing executed that command, and it drifted: the transitions in the example
stopped passing the fencing epoch the engine now requires, so a first-time adopter died at step 3
of 7. A guide whose example is broken is worse than no example, because it is trusted.

This test is deliberately end to end. It shells out to the example exactly as the guide says, so an
engine change that invalidates the example fails here instead of in someone's first afternoon.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

LOOM = Path(__file__).resolve().parents[3]
WALKTHROUGH = LOOM / "examples" / "walkthrough" / "walkthrough.py"

STEPS = (
    "Author work centrally",
    "Claim with a lease, against the revision you read",
    "Move it through the implementation loop",
    "An independent reviewer, never a claimant",
    "Evidence, then integration",
    "The dependency gate opens on its own",
    "The audit log proves the sequence",
)


class WalkthroughTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            [sys.executable, str(WALKTHROUGH)], cwd=str(LOOM),
            capture_output=True, text=True, timeout=180,
        )
        cls.output = result.stdout + result.stderr
        cls.returncode = result.returncode

    def test_the_worked_example_completes_the_whole_cycle(self):
        self.assertEqual(self.returncode, 0, self.output)
        for step in STEPS:
            self.assertIn(step, self.output)
        self.assertIn("reviewed -> integrated", self.output)
        self.assertIn("event chain OK", self.output)

    def test_the_worked_example_shows_the_refusals_it_claims_to_show(self):
        self.assertIn("is leased by", self.output)          # a second claim is refused
        self.assertIn("review must be independent", self.output)  # a claimant cannot review

    def test_the_worked_example_registers_its_boards_through_the_cli(self):
        self.assertIn("initialized alpha.program", self.output)
        self.assertIn("added board beta.program", self.output)


if __name__ == "__main__":
    unittest.main()
