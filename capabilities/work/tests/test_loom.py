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
        args = loom.build_parser().parse_args(["tick", "--apply", "--quiet"])
        config = loom.load_config(Path("/nonexistent"))
        captured = {}
        original = loom.delegate
        loom.delegate = lambda command, env=None: captured.setdefault("command", command) and 0
        try:
            loom.cmd_tick(args, config)
        finally:
            loom.delegate = original
        command = captured["command"]
        self.assertIn("--apply", command)
        self.assertIn("--quiet", command)
        self.assertIn("--max-implementer", command)
        self.assertIn(config["worker"]["command"], command)
        self.assertIn(config["db"], command)

    def test_pid_alive_rejects_junk(self):
        self.assertFalse(loom.pid_alive(None))
        self.assertFalse(loom.pid_alive("not-a-pid"))
        self.assertTrue(loom.pid_alive(__import__("os").getpid()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
