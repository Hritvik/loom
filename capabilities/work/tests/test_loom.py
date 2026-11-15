#!/usr/bin/env python3
"""Tests for the loom CLI: config resolution and the pure dispatch helpers."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
loader = SourceFileLoader("loom_cli", str(WORK / "loom"))
spec = importlib.util.spec_from_loader("loom_cli", loader)
loom = importlib.util.module_from_spec(spec)
loader.exec_module(loom)


class LoomConfigTest(unittest.TestCase):
    def test_defaults_apply_when_no_config_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = loom.load_config(Path(tmp) / "missing.json")
        self.assertEqual(config["worker"]["limits"]["implementer"], 2)
        self.assertIn("deepseek-v4.1-flash", config["worker"]["command"])

    def test_a_config_file_overrides_only_what_it_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "loom.json"
            path.write_text(json.dumps({"worker": {"limits": {"implementer": 5}}}))
            config = loom.load_config(path)
        self.assertEqual(config["worker"]["limits"]["implementer"], 5)
        self.assertEqual(config["worker"]["limits"]["reviewer"], 2)
        self.assertIn("deepseek", config["worker"]["command"])

    def test_tick_builds_its_command_from_config(self):
        config = loom.load_config(Path("/nonexistent"))
        command = loom.build_tick_args(["--apply", "--quiet"], [], config)
        self.assertIn("--apply", command)
        self.assertIn("--quiet", command)
        self.assertIn("--max-implementer", command)
        self.assertIn(config["worker"]["command"], command)
        self.assertIn(config["db"], command)
        self.assertEqual(command[-2], "--max-integrator")

    def test_an_explicit_limit_beats_the_config(self):
        config = loom.load_config(Path("/nonexistent"))
        command = loom.build_tick_args(["--max-implementer", "5"], [], config)
        self.assertEqual(command[command.index("--max-implementer") + 1], "5")

    def test_global_flags_are_separated_from_the_command(self):
        globals_, rest = loom.split_globals(["--root", "/tmp/x", "claim", "demo/W-1", "--now", "2030-01-01T00:00:00Z"])
        self.assertEqual(globals_, ["--root", "/tmp/x", "--now", "2030-01-01T00:00:00Z"])
        self.assertEqual(rest, ["claim", "demo/W-1"])

    def test_pid_alive_rejects_junk(self):
        self.assertFalse(loom.pid_alive(None))
        self.assertFalse(loom.pid_alive("not-a-pid"))
        self.assertTrue(loom.pid_alive(__import__("os").getpid()))


class DeployTest(unittest.TestCase):
    """Promotion is a deployment: development commits must not reach the scheduler."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.repo = root / "repo"
        (self.repo / "capabilities" / "work").mkdir(parents=True)
        (self.repo / "capabilities" / "work" / "loom").write_text("#!/usr/bin/env python3\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-qm", "engine")
        self.env = dict(os.environ, HOME=str(self.home))

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo),
             "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgSign=false", *args],
            capture_output=True, text=True, check=True).stdout.strip()

    def run_loom(self, *args):
        return subprocess.run(
            [sys.executable, str(WORK / "loom"), *args],
            capture_output=True, text=True, env=self.env, cwd=str(self.repo))

    def deploy(self, *args):
        return self.run_loom("deploy", *args, "--repo", str(self.repo))

    def released(self) -> dict:
        path = self.home / ".local/state/loom/engine.release"
        return json.loads(path.read_text())

    def test_an_unpromoted_engine_is_reported_as_unpinned(self):
        result = self.deploy("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UNPINNED", result.stdout)

    def test_promote_copies_the_commit_and_status_notices_drift(self):
        first = self.git("rev-parse", "HEAD")
        promoted = self.deploy("promote")
        self.assertEqual(promoted.returncode, 0, promoted.stderr)
        self.assertEqual(self.released()["current"]["commit"], first)
        copied = self.home / ".local/state/loom/engine/capabilities/work/loom"
        self.assertTrue(copied.is_file())
        (self.repo / "capabilities" / "work" / "loom").write_text("#!/usr/bin/env python3\n# changed\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "unreviewed change")
        status = self.deploy("status")
        self.assertIn("STALE", status.stdout)
        # The release still carries the reviewed tree, not the new commit.
        self.assertEqual(copied.read_text(), "#!/usr/bin/env python3\n")

    def test_promote_refuses_an_uncommitted_tree(self):
        (self.repo / "capabilities" / "work" / "loom").write_text("#!/usr/bin/env python3\n# dirty\n")
        result = self.deploy("promote")
        self.assertEqual(result.returncode, 2)
        self.assertIn("uncommitted", result.stderr)

    def test_rollback_restores_the_previous_release(self):
        first = self.git("rev-parse", "HEAD")
        self.deploy("promote")
        (self.repo / "capabilities" / "work" / "loom").write_text("#!/usr/bin/env python3\n# two\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "second")
        second = self.git("rev-parse", "HEAD")
        self.deploy("promote")
        self.assertEqual(self.released()["current"]["commit"], second)
        rolled = self.deploy("rollback")
        self.assertEqual(rolled.returncode, 0, rolled.stderr)
        self.assertEqual(self.released()["current"]["commit"], first)
        copied = self.home / ".local/state/loom/engine/capabilities/work/loom"
        self.assertEqual(copied.read_text(), "#!/usr/bin/env python3\n")

    def test_install_points_the_schedule_at_the_release(self):
        self.deploy("promote")
        installed = self.run_loom("schedule", "install")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        plist = self.home / "Library/LaunchAgents/local.loom-tick.plist"
        self.assertIn(str(self.home / ".local/state/loom/engine"), plist.read_text())
        status = self.deploy("status")
        self.assertIn("PINNED", status.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
