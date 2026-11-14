#!/usr/bin/env python3
"""Tests for the loom front door: config resolution and the pure dispatch helpers."""
from __future__ import annotations

import importlib.util
import json
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
