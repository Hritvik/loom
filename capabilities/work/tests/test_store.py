#!/usr/bin/env python3
"""Conformance tests for the SQLite store and the legacy file import.

SQLite is the only runtime backend. `FileStore` survives as the importer that `task migrate` reads
a pre-SQLite work home with, so the tests cover two things: SQLite round-trips state and builds the
same hash-chained event log, and a legacy file home migrates without losing events.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORK))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_work import T0, Workspace, run  # noqa: E402

from store import FileStore, SqliteStore, make_store  # noqa: E402


def normalised(value):
    """Dates and paths differ by backend; compare the wire shape."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def legacy_home(root: Path, registry: dict, events: int = 1) -> FileStore:
    """A pre-SQLite work home: registry.yaml plus a hash-chained events.ndjson."""
    root.mkdir(parents=True, exist_ok=True)
    store = FileStore(root)
    store.registry = normalised(registry)
    store.save()
    for index in range(events):
        store.append_event(event="note", agent="agent-a", task="demo/W-1",
                           revision=0, status="ready", now=T0)
    return store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "work.db"

    def tearDown(self):
        self.tmp.cleanup()
        self.ws.close()

    def sqlite(self) -> SqliteStore:
        return SqliteStore(self.ws.root, self.db)

    def test_sqlite_round_trips_the_registry(self):
        expected = normalised(self.ws.load())

        sqlite = self.sqlite()
        try:
            sqlite.registry = expected
            sqlite.save()
            loaded = sqlite.load()
        finally:
            sqlite.close()

        loaded, expected = normalised(loaded), normalised(expected)
        loaded.pop("updated", None)
        expected.pop("updated", None)
        self.assertEqual(loaded, expected)

    def test_event_chain_is_hash_linked(self):
        sqlite = self.sqlite()
        try:
            sqlite.append_event(event="note", agent="agent-a", task="demo/W-1", now=T0)
            sqlite.append_event(event="note", agent="agent-b", task="demo/W-2", now=T0)
            records = sqlite.read_events()
        finally:
            sqlite.close()

        self.assertEqual([record["seq"] for _, record in records], [1, 2])
        self.assertEqual(records[0][1]["prev"], "0" * 64)
        import hashlib

        self.assertEqual(records[1][1]["prev"], hashlib.sha256(records[0][0]).hexdigest())

    def test_cli_runs_against_the_store(self):
        shown = run("--root", str(self.ws.root), "--db", str(self.ws.db), "show", "demo/W-1")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("demo/W-1", shown.stdout)

        listed = run("--root", str(self.ws.root), "--db", str(self.ws.db), "list", "--ready")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("demo/W-1", listed.stdout)

        unknown = run("--root", str(self.ws.root), "--db", str(self.ws.db), "show", "demo/NOPE")
        self.assertNotEqual(unknown.returncode, 0)

    def test_a_write_is_visible_to_the_next_process(self):
        claimed = run("--root", str(self.ws.root), "--db", str(self.ws.db),
                      "--agent", "agent-a", "--now", T0,
                      "claim", "demo/W-1", "--revision", "0", "--ttl", "45m")
        self.assertEqual(claimed.returncode, 0, claimed.stderr)

        task = next(item for item in self.ws.load()["tasks"] if item["id"] == "demo/W-1")
        self.assertEqual(task["status"], "claimed")
        self.assertEqual(task["claim"]["agent"], "agent-a")
        self.assertEqual(task["revision"], 1)

    def test_legacy_file_home_migrates_and_verifies(self):
        legacy = Path(self.tmp.name) / "legacy"
        legacy_home(legacy, self.ws.load(), events=2)

        result = run("--root", str(legacy), "migrate", "--to", str(self.db))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("migrated 4 tasks", result.stdout)

        verified = run("--root", str(legacy), "--db", str(self.db), "events", "--verify")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertIn("event chain OK", verified.stdout)

        sqlite = self.sqlite()
        try:
            self.assertEqual(len(sqlite.load()["tasks"]), 4)
            self.assertEqual(len(sqlite.read_events()), 2)
        finally:
            sqlite.close()

    def test_migrate_refuses_an_occupied_target(self):
        legacy = Path(self.tmp.name) / "legacy"
        legacy_home(legacy, self.ws.load())

        first = run("--root", str(legacy), "migrate", "--to", str(self.db))
        self.assertEqual(first.returncode, 0, first.stderr)

        second = run("--root", str(legacy), "migrate", "--to", str(self.db))
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("already holds", second.stderr)

        forced = run("--root", str(legacy), "migrate", "--to", str(self.db), "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)

    def test_export_round_trips_to_a_loadable_registry(self):
        out = Path(self.tmp.name) / "exported" / "registry.yaml"
        out.parent.mkdir()
        result = run("--root", str(self.ws.root), "--db", str(self.ws.db),
                     "export", "--out", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)

        reloaded = FileStore(out.parent).load()
        self.assertEqual([task["id"] for task in reloaded["tasks"]],
                         [task["id"] for task in self.ws.load()["tasks"]])

        as_json = run("--root", str(self.ws.root), "--db", str(self.ws.db),
                      "export", "--format", "json")
        self.assertIn('"tasks"', as_json.stdout)

    def test_factory_returns_sqlite(self):
        self.assertIsInstance(make_store(self.ws.root, "sqlite", self.db), SqliteStore)
        with self.assertRaises(Exception):
            make_store(self.ws.root, "postgres")


if __name__ == "__main__":
    unittest.main(verbosity=2)
