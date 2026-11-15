"""Provenance: six sources, six independent answers."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Home  # noqa: E402

from loom_ui import model, provenance, snapshot, version  # noqa: E402

SOURCES = ("database", "process", "scheduler", "release", "git", "evidence")


def iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).isoformat(timespec="seconds").replace("+00:00", "Z")


class ProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.close)

    # -- helpers -------------------------------------------------------------

    def sample(self):
        snap = snapshot.read(self.home.db)
        view = model.build(snap)
        signals = provenance.collect(snap, view, version.inspect())
        return {signal.source: signal for signal in signals}

    # -- tests ---------------------------------------------------------------

    def test_every_source_reports_its_own_as_of(self):
        signals = self.sample()
        self.assertEqual(tuple(signals), SOURCES)
        for signal in signals.values():
            self.assertTrue(signal.as_of, signal.source)
            self.assertIn(signal.status, {"ok", "degraded", "unavailable"}, signal.source)
            self.assertTrue(signal.detail, signal.source)

    def test_database_as_of_is_the_snapshot_time(self):
        snap = snapshot.read(self.home.db)
        view = model.build(snap)
        signal = next(item for item in provenance.collect(snap, view, version.inspect())
                      if item.source == "database")
        self.assertEqual(signal.status, "ok")
        self.assertEqual(signal.as_of, snap.as_of)
        self.assertEqual(signal.data["head"]["seq"], snap.head_seq)

    def test_process_reports_no_workers_when_none_were_launched(self):
        signal = self.sample()["process"]
        self.assertEqual(signal.status, "ok")
        self.assertEqual(signal.data["live"], [])

    def test_process_detects_a_live_launch(self):
        child = subprocess.Popen(["sleep", "30"])

        def reap():
            child.kill()
            child.wait(timeout=5)

        self.addCleanup(reap)
        self.home.append(event="launch", task="demo/W-1", agent="opencode/worker",
                         role="implementer", pid=child.pid, state="pending",
                         started_at=iso(), now=iso())
        signal = self.sample()["process"]
        self.assertEqual(signal.status, "ok")
        self.assertEqual([item["pid"] for item in signal.data["live"]], [child.pid])

    def test_process_reads_the_attempt_model_not_a_launch_state(self):
        child = subprocess.Popen(["sleep", "30"])

        def reap():
            child.kill()
            child.wait(timeout=5)

        self.addCleanup(reap)
        # The engine no longer writes a state on a launch; the run's state lives on the attempt.
        self.home.append(event="attempt", task="demo/W-1", agent="opencode/worker",
                         role="implementer", attempt="demo/W-1#a1", state="running", now=iso())
        self.home.append(event="launch", task="demo/W-1", agent="opencode/worker",
                         role="implementer", attempt="demo/W-1#a1", pid=child.pid,
                         started_at=iso(), now=iso())
        signal = self.sample()["process"]
        self.assertEqual(signal.status, "ok")
        self.assertEqual([item["pid"] for item in signal.data["live"]], [child.pid])

    def test_process_flags_a_pending_launch_without_a_pid(self):
        self.home.append(event="launch", task="demo/W-2", agent="opencode/worker",
                         role="implementer", state="pending", now=iso())
        signal = self.sample()["process"]
        self.assertEqual(signal.status, "degraded")
        self.assertEqual(signal.data["unreadable"], ["demo/W-2"])

    def test_scheduler_is_degraded_without_a_launch_agent(self):
        signal = self.sample()["scheduler"]
        self.assertEqual(signal.status, "degraded")
        self.assertIn("not scheduled", signal.detail)

    def test_scheduler_reads_the_plist_when_present(self):
        self.home.launch_agent(interval=120)
        signal = self.sample()["scheduler"]
        self.assertEqual(signal.status, "ok")
        self.assertEqual(signal.data["interval_seconds"], 120)
        self.assertIn("loom", " ".join(signal.data["program_arguments"]))

    def test_release_is_degraded_when_unpinned(self):
        signal = self.sample()["release"]
        self.assertEqual(signal.status, "degraded")
        self.assertEqual(signal.data["verdict"], "UNPINNED")

    def test_release_is_ok_when_pinned(self):
        self.home.publish_release()
        signal = self.sample()["release"]
        self.assertEqual(signal.status, "ok")
        self.assertEqual(signal.data["verdict"], "PINNED")

    def test_git_is_degraded_for_a_repository_that_is_not_a_repository(self):
        signal = self.sample()["git"]
        self.assertEqual(signal.status, "degraded")
        self.assertEqual(len(signal.data["failures"]), 1)
        self.assertIn("repo", signal.data["failures"][0])

    def test_git_reports_head_for_a_real_repository(self):
        subprocess.run(["git", "-C", str(self.home.repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.home.repo), "-c", "user.email=t@example.com",
                        "-c", "user.name=Test", "commit", "-q", "--allow-empty", "-m", "init"],
                       check=True)
        signal = self.sample()["git"]
        self.assertEqual(signal.status, "ok")
        self.assertEqual(len(signal.data["repositories"]), 1)
        self.assertEqual(len(signal.data["repositories"][0]["head"]), 40)

    def test_evidence_is_ok_when_the_file_exists(self):
        signal = self.sample()["evidence"]
        self.assertEqual(signal.status, "ok")
        self.assertEqual([item["task"] for item in signal.data["present"]], ["demo/W-5"])

    def test_evidence_is_degraded_when_the_file_is_missing(self):
        (self.home.repo / "evidence" / "demo-W-5.md").unlink()
        signal = self.sample()["evidence"]
        self.assertEqual(signal.status, "degraded")
        self.assertEqual([item["task"] for item in signal.data["missing"]], ["demo/W-5"])

    def test_a_broken_source_does_not_hide_the_others(self):
        snap = snapshot.read(self.home.db)
        view = model.build(snap)

        def explode(*_args, **_kwargs):
            raise RuntimeError("boom")

        with unittest.mock.patch.object(provenance, "git", explode):
            signals = provenance.collect(snap, view, version.inspect())
        by_source = {signal.source: signal for signal in signals}
        self.assertEqual(by_source["git"].status, "unavailable")
        self.assertIn("boom", by_source["git"].detail)
        self.assertEqual(by_source["database"].status, "ok")

    def test_degraded_helper(self):
        signals = provenance.collect(
            snapshot.read(self.home.db),
            model.build(snapshot.read(self.home.db)),
            version.inspect())
        self.assertEqual({signal.source for signal in provenance.degraded(signals)},
                         {"scheduler", "release", "git"})


if __name__ == "__main__":
    unittest.main()
