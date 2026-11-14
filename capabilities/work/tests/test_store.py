#!/usr/bin/env python3
"""Conformance tests for the SQLite store and the legacy file import.

SQLite is the only runtime backend. `FileStore` survives as the importer that `loom migrate` reads
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

from store import (FileStore, SqliteStore, WorkError, event_bytes, make_store)  # noqa: E402


def normalised(value):
    """Dates and paths differ by backend; compare the wire shape."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def legacy_home(root: Path, registry: dict, events: int = 1) -> FileStore:
    """A pre-SQLite work home: registry.yaml plus a hash-chained events.ndjson."""
    root.mkdir(parents=True, exist_ok=True)
    store = FileStore(root)
    store.registry = normalised(registry)
    with store.lock():
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
            with sqlite.lock():
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
            with sqlite.lock():
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

    def test_a_write_without_the_lock_is_refused(self):
        sqlite = self.sqlite()
        try:
            sqlite.registry = self.ws.load()
            with self.assertRaises(WorkError):
                sqlite.save()
            with self.assertRaises(WorkError):
                sqlite.commit(event="note", agent="agent-a", task="demo/W-1", now=T0)
            with self.assertRaises(WorkError):
                sqlite.append_event(event="note", agent="agent-a", task="demo/W-1", now=T0)
        finally:
            sqlite.close()

    def test_commit_rolls_back_state_when_the_event_fails(self):
        sqlite = self.sqlite()
        try:
            sqlite.registry = self.ws.load()
            with sqlite.lock():
                sqlite.save()
            registry = sqlite.load()
            task = registry["tasks"][0]
            original = sqlite._append_event

            def boom(**fields):
                raise RuntimeError("event append failed")

            sqlite._append_event = boom
            try:
                with sqlite.lock():
                    task["title"] = "changed"
                    with self.assertRaises(RuntimeError):
                        sqlite.commit(event="transition", task=task["id"], status="red",
                                      revision=task.get("revision", 0) + 1, now=T0)
            finally:
                sqlite._append_event = original
            fresh = sqlite.load()
            self.assertEqual(fresh["tasks"][0]["title"], "Task demo/W-1")
            self.assertEqual(len(sqlite.read_events()), 0)
        finally:
            sqlite.close()

    def test_append_event_writes_an_external_checkpoint(self):
        import hashlib

        sqlite = self.sqlite()
        try:
            with sqlite.lock():
                event = sqlite.append_event(event="note", agent="agent-a",
                                            task="demo/W-1", now=T0)
            seq, digest = sqlite.checkpoint()
            self.assertEqual(seq, event["seq"])
            self.assertEqual(digest, hashlib.sha256(event_bytes(event)).hexdigest())
        finally:
            sqlite.close()

    def test_events_verify_detects_a_rewritten_event(self):
        sqlite = self.sqlite()
        try:
            with sqlite.lock():
                sqlite.append_event(event="note", agent="agent-a", task="demo/W-1", now=T0)
            sqlite.connection.execute(
                "UPDATE events SET body = replace(body, 'agent-a', 'agent-x') WHERE seq = 1")
        finally:
            sqlite.close()
        result = run("--root", str(self.ws.root), "--db", str(self.db), "events", "--verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("external checkpoint", result.stdout)

    def test_events_reanchor_repairs_a_stale_checkpoint(self):
        sqlite = self.sqlite()
        try:
            with sqlite.lock():
                sqlite.append_event(event="note", agent="agent-a", task="demo/W-1", now=T0)
            head = Path(str(self.db) + ".head")
            head.write_text("1 " + "0" * 64 + "\n")
        finally:
            sqlite.close()
        repaired = run("--root", str(self.ws.root), "--db", str(self.db),
                       "events", "--verify", "--reanchor")
        self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
        self.assertIn("re-anchored", repaired.stdout)
        verified = run("--root", str(self.ws.root), "--db", str(self.db), "events", "--verify")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def test_tasks_are_documents_without_duplicate_columns(self):
        sqlite = self.sqlite()
        try:
            sqlite.registry = self.ws.load()
            with sqlite.lock():
                sqlite.save()
            task_columns = {row["name"] for row in
                            sqlite.connection.execute("PRAGMA table_info(tasks)")}
            board_columns = {row["name"] for row in
                             sqlite.connection.execute("PRAGMA table_info(boards)")}
        finally:
            sqlite.close()
        self.assertEqual(task_columns, {"id", "body"})
        self.assertEqual(board_columns, {"id", "body"})

    def test_an_old_wide_task_table_is_rebuilt_as_a_document(self):
        sqlite = self.sqlite()
        try:
            sqlite.registry = self.ws.load()
            with sqlite.lock():
                sqlite.save()
            sqlite.connection.execute("ALTER TABLE tasks ADD COLUMN status TEXT")
            sqlite.connection.execute("UPDATE tasks SET status = 'ready'")
        finally:
            sqlite.close()
        reopened = self.sqlite()
        try:
            columns = {row["name"] for row in reopened.connection.execute("PRAGMA table_info(tasks)")}
            self.assertEqual(columns, {"id", "body"})
            self.assertEqual(len(reopened.load()["tasks"]), 4)
        finally:
            reopened.close()

    def test_a_batch_transaction_anchors_its_last_event(self):
        import hashlib

        sqlite = self.sqlite()
        try:
            sqlite.registry = self.ws.load()
            with sqlite.lock():
                with sqlite.transaction():
                    sqlite.append_event(event="note", agent="agent-a", task="demo/W-1", now=T0)
                    sqlite.append_event(event="note", agent="agent-b", task="demo/W-2", now=T0)
            checkpoint = sqlite.checkpoint()
            last_raw, last = sqlite.read_events()[-1]
        finally:
            sqlite.close()
        self.assertEqual(checkpoint, (last["seq"], hashlib.sha256(last_raw).hexdigest()))

    def test_snapshot_reads_registry_and_events_together(self):
        sqlite = self.sqlite()
        try:
            sqlite.registry = self.ws.load()
            with sqlite.lock():
                sqlite.commit(event="create", task="demo/W-9", agent="agent-a",
                              status="ready", revision=0, now=T0)
            registry, events = sqlite.snapshot()
        finally:
            sqlite.close()
        self.assertEqual([event["event"] for _, event in events], ["create"])
        self.assertEqual(len(registry["tasks"]), 4)

    def test_factory_returns_sqlite(self):
        self.assertIsInstance(make_store(self.ws.root, "sqlite", self.db), SqliteStore)
        with self.assertRaises(Exception):
            make_store(self.ws.root, "postgres")


if __name__ == "__main__":
    unittest.main(verbosity=2)
