"""The version gate: pinned, unpinned, mismatched, drifted and absent."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Home  # noqa: E402

from loom_ui import version  # noqa: E402


class VersionTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.close)

    def test_pinned_release_opens_the_gate(self):
        self.home.publish_release(commit="b" * 40)
        status = version.inspect()
        self.assertTrue(status.ok, status.detail)
        self.assertEqual(status.verdict, "PINNED")
        self.assertTrue(status.pinned)
        self.assertEqual(status.release_commit, "b" * 40)
        self.assertEqual(status.expected_digest, status.actual_digest)
        self.assertIn("capabilities/work/loom", status.front_door)

    def test_no_release_is_unpinned_and_reported(self):
        status = version.inspect()
        self.assertTrue(status.ok, status.detail)
        self.assertEqual(status.verdict, "UNPINNED")
        self.assertFalse(status.pinned)
        self.assertIn("development checkout", status.detail)

    def test_engine_sources_that_differ_from_the_front_door_close_the_gate(self):
        self.home.drift_engine("store.py")
        status = version.inspect()
        self.assertFalse(status.ok)
        self.assertEqual(status.verdict, "MISMATCH")
        self.assertNotEqual(status.expected_digest, status.actual_digest)
        self.assertIn("start the UI from the promoted release", status.detail)

    def test_a_release_tree_that_no_longer_matches_its_digest_is_drifted(self):
        self.home.publish_release()
        self.home.drift_engine("engine.py")
        status = version.inspect()
        self.assertFalse(status.ok)
        self.assertEqual(status.verdict, "DRIFTED")
        self.assertIn("no longer matches", status.detail)

    def test_a_recorded_release_whose_front_door_is_gone_is_absent(self):
        self.home.publish_release()
        (self.home.work_home / "loom").unlink()
        status = version.inspect()
        self.assertFalse(status.ok)
        self.assertEqual(status.verdict, "ABSENT")

    def test_incomplete_engine_sources_are_absent(self):
        (self.home.work_home / "engine.py").unlink()
        status = version.inspect()
        self.assertFalse(status.ok)
        self.assertEqual(status.verdict, "ABSENT")

    def test_require_raises_a_gate_error_carrying_the_status(self):
        self.home.drift_engine("store.py")
        with self.assertRaises(version.VersionGateClosed) as caught:
            version.require()
        self.assertEqual(caught.exception.status.verdict, "MISMATCH")

    def test_serialises(self):
        payload = version.inspect().to_dict()
        for key in ("ok", "verdict", "front_door", "running_root", "expected_digest",
                    "actual_digest", "detail", "as_of"):
            self.assertIn(key, payload)


if __name__ == "__main__":
    unittest.main()
