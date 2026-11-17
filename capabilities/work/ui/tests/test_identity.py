"""The identity and role policy: server-side, validated, and empty by default."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Home  # noqa: F401  (puts the package on sys.path)

from loom_ui.identity import Human, Policy, PolicyError


class PolicyTest(unittest.TestCase):
    def test_no_configuration_means_no_actor(self):
        policy = Policy(())
        self.assertFalse(policy.configured)
        self.assertEqual(policy.identities(), frozenset())
        self.assertFalse(policy.allows("hritvik", "claim"))
        self.assertFalse(policy.allows(None, "claim"))

    def test_parse_roles(self):
        policy = Policy.parse(["hritvik:owner,implementer", "rev:reviewer"])
        self.assertTrue(policy.configured)
        self.assertEqual(policy.identities(), frozenset({"hritvik", "rev"}))
        self.assertEqual(policy.get("hritvik").roles, frozenset({"owner", "implementer"}))
        self.assertTrue(policy.allows("hritvik", "claim"))
        self.assertFalse(policy.allows("rev", "claim"))
        self.assertTrue(policy.allows("rev", "answer"))

    def test_owner_only_targets(self):
        policy = Policy.parse(["impl:implementer", "boss:owner"])
        self.assertTrue(policy.allows("boss", "transition", "accepted"))
        self.assertFalse(policy.allows("impl", "transition", "accepted"))
        self.assertTrue(policy.allows("impl", "transition", "green"))

    def test_unknown_role_is_refused(self):
        with self.assertRaises(PolicyError):
            Policy.parse(["hritvik:admin"])
        with self.assertRaises(PolicyError):
            Policy.parse(["hritvik"])

    def test_a_name_that_is_not_a_token_is_refused(self):
        for spec in ("hrit:::owner", ":owner", 'a"b:owner', "x y:owner"):
            with self.subTest(spec=spec):
                with self.assertRaises(PolicyError):
                    Policy.parse([spec])

    def test_duplicate_identity_is_refused(self):
        with self.assertRaises(PolicyError):
            Policy.parse(["hritvik:owner", "hritvik:reviewer"])

    def test_configuration_file(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "ui.json"
        path.write_text(json.dumps({"humans": {"hritvik": ["owner", "implementer"]}}))
        policy = Policy.load(path, ())
        self.assertEqual(policy.identities(), frozenset({"hritvik"}))

    def test_explicit_arguments_win_over_the_file(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "ui.json"
        path.write_text(json.dumps({"humans": {"someone": ["owner"]}}))
        policy = Policy.load(path, ["hritvik:reviewer"])
        self.assertEqual(policy.identities(), frozenset({"hritvik"}))

    def test_a_broken_file_is_an_error_not_a_silent_default(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "ui.json"
        path.write_text("{not json")
        with self.assertRaises(PolicyError):
            Policy.load(path, ())
        path.write_text(json.dumps({"humans": {"hritvik": ["admin"]}}))
        with self.assertRaises(PolicyError):
            Policy.load(path, ())

    def test_a_missing_file_is_an_empty_policy(self):
        policy = Policy.load(Path("/nonexistent/ui.json"), ())
        self.assertFalse(policy.configured)

    def test_role_capability_table_is_explicit(self):
        human = Human("x", frozenset({"reviewer"}))
        self.assertTrue(human.allows("answer"))
        self.assertTrue(human.allows("ask"))
        self.assertFalse(human.allows("release"))
        self.assertFalse(human.allows("tick"))

    def test_environment_override_names_the_file(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "custom.json"
        path.write_text(json.dumps({"humans": {"env": ["owner"]}}))
        previous = os.environ.get("LOOM_UI_CONFIG")
        os.environ["LOOM_UI_CONFIG"] = str(path)
        try:
            policy = Policy.load(None, ())
        finally:
            if previous is None:
                os.environ.pop("LOOM_UI_CONFIG", None)
            else:
                os.environ["LOOM_UI_CONFIG"] = previous
        self.assertEqual(policy.identities(), frozenset({"env"}))


if __name__ == "__main__":
    unittest.main()
