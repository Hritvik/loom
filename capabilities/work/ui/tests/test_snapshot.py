"""The snapshot is one read, and it agrees with the engine's own loader."""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Home  # noqa: E402

from loom_ui import snapshot  # noqa: E402


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.close)

    def test_matches_the_engine_loader(self):
        expected = self.home.load()
        actual = snapshot.read(self.home.db).registry
        self.assertEqual([task["id"] for task in actual["tasks"]],
                         [task["id"] for task in expected["tasks"]])
        self.assertEqual(actual["tasks"], expected["tasks"])
        self.assertEqual(actual["boards"], expected["boards"])
        self.assertEqual(actual["edges"], expected["edges"])
        self.assertEqual(actual["claims"], expected["claims"])

    def test_event_head_is_in_the_same_read(self):
        result = snapshot.read(self.home.db)
        self.assertEqual(result.head_seq, len(result.events))
        self.assertGreater(result.head_seq, 0)
        self.assertEqual(len(result.head_hash), 64)

    def test_digest_is_stable_without_writes(self):
        first = snapshot.read(self.home.db)
        second = snapshot.read(self.home.db)
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first.head_seq, second.head_seq)

    def test_digest_changes_when_the_registry_changes(self):
        first = snapshot.read(self.home.db)
        self.home.registry["tasks"][0]["title"] = "A different title"
        self.home.save()
        second = snapshot.read(self.home.db)
        self.assertNotEqual(first.digest, second.digest)

    def test_missing_database_is_unavailable(self):
        with self.assertRaises(snapshot.SnapshotUnavailable):
            snapshot.read(self.home.root / "absent.db")

    def test_foreign_database_is_unavailable(self):
        other = self.home.root / "other.db"
        connection = sqlite3.connect(other)
        connection.executescript(snapshot.store.SCHEMA)
        connection.execute("INSERT INTO meta (key, value) VALUES ('kind', 'something-else')")
        connection.commit()
        connection.close()
        with self.assertRaises(snapshot.SnapshotUnavailable):
            snapshot.read(other)

    def test_empty_database_is_unavailable(self):
        empty = self.home.root / "empty.db"
        sqlite3.connect(empty).close()
        with self.assertRaises(snapshot.SnapshotUnavailable):
            snapshot.read(empty)

    def test_reader_cannot_write(self):
        connection = sqlite3.connect(f"file:{self.home.db}?mode=ro", uri=True,
                                     isolation_level=None)
        try:
            connection.execute("PRAGMA query_only=ON")
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM tasks")
        finally:
            connection.close()
        self.assertEqual(len(self.home.load()["tasks"]), len(self.home.registry["tasks"]))

    def test_summary_reports_the_snapshot_identity(self):
        result = snapshot.read(self.home.db)
        summary = result.summary()
        self.assertEqual(summary["tasks"], len(self.home.registry["tasks"]))
        self.assertEqual(summary["head"]["seq"], result.head_seq)
        self.assertEqual(summary["db"], str(self.home.db))


if __name__ == "__main__":
    unittest.main()
