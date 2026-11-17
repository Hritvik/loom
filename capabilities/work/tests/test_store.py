#!/usr/bin/env python3
"""Conformance tests for the SQLite store and the legacy file import.

SQLite is the only runtime backend. `FileStore` survives as the importer that `loom migrate` reads
a pre-SQLite work home with, so the tests cover two things: SQLite round-trips state and builds the
same hash-chained event log, and a legacy file home migrates without losing events.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORK))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_work import T0, Workspace, run  # noqa: E402

import yaml  # noqa: E402

from store import (  # noqa: E402
    REGISTRY_HEADER,
    ZERO_HASH,
    FileStore,
    SqliteStore,
    WorkError,
    event_bytes,
    make_store,
)


def normalised(value):
    """Dates and paths differ by backend; compare the wire shape."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def legacy_home(root: Path, registry: dict, events: int = 1) -> FileStore:
    """A pre-SQLite work home: registry.yaml plus a hash-chained events.ndjson.

    Written directly. `FileStore` is read-only by design, so a fixture cannot use it to
    manufacture the state `loom migrate` is meant to import.
    """
    root.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(normalised(registry), sort_keys=False, default_flow_style=False,
                          allow_unicode=True, width=100)
    (root / "registry.yaml").write_text(REGISTRY_HEADER + body, encoding="utf-8")
    lines, previous = [], ZERO_HASH
    for index in range(events):
        event = {"seq": index + 1, "ts": T0, "event": "note", "agent": "agent-a",
                 "task": "demo/W-1", "revision": 0, "status": "ready", "prev": previous}
        raw = event_bytes(event)
        previous = hashlib.sha256(raw).hexdigest()
        lines.append(raw)
    if lines:
        (root / "events.ndjson").write_bytes(b"\n".join(lines) + b"\n")
    store = FileStore(root)
    store.load()
    return store


class Writes:
    """Record the statements a block issues against the document tables.

    The store's promise is that a commit touches the rows it changed. Counting statements is the
    direct test of that: a trace of the connection shows exactly which rows a commit rewrote.
    """

    def __init__(self, store: SqliteStore):
        self.store = store
        self.statements: list[str] = []

    def __enter__(self):
        self.store.connection.set_trace_callback(self._record)
        return self

    def __exit__(self, *exc):
        self.store.connection.set_trace_callback(None)
        return False

    def _record(self, statement: str) -> None:
        if statement.split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            self.statements.append(statement)

    def to(self, table: str) -> list[str]:
        return [statement for statement in self.statements
                if re.search(rf"\b(?:INTO|UPDATE|FROM)\s+{table}\b", statement)]

    def touched(self) -> int:
        """Write statements against the registry documents, ignoring the event log."""
        return sum(len(self.to(table)) for table in ("tasks", "boards", "edges", "claims"))


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

    def test_a_held_lock_fails_loudly_instead_of_waiting_forever(self):
        import fcntl
        import os as os_module
        import time as time_module

        from store import _FileLock

        path = Path(self.tmp.name) / "held.lock"
        handle = path.open("a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            started = time_module.monotonic()
            with self.assertRaises(WorkError) as caught:
                with _FileLock(path, timeout=0.05):
                    pass
            elapsed = time_module.monotonic() - started
            self.assertLess(elapsed, 2.0, "the lock wait was not bounded")
            self.assertIn("store lock", str(caught.exception))
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def test_a_contended_lock_is_recorded_and_then_released(self):
        from store import _FileLock

        path = Path(self.tmp.name) / "shared.lock"
        with _FileLock(path, timeout=1.0):
            pass
        with _FileLock(path, timeout=1.0) as acquired:
            self.assertIsNotNone(acquired.handle)

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
            original = sqlite._flush_events

            def boom():
                raise RuntimeError("event append failed")

            sqlite._flush_events = boom
            try:
                with sqlite.lock():
                    task["title"] = "changed"
                    with self.assertRaises(RuntimeError):
                        sqlite.commit(event="transition", task=task["id"], status="red",
                                      revision=task.get("revision", 0) + 1, now=T0)
            finally:
                sqlite._flush_events = original
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

    def test_init_creates_a_registry_and_first_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            (repo / "docs").mkdir(parents=True)
            (repo / "docs" / "board.json").write_text(json.dumps({"packets": []}))
            db = root / "work.db"
            initialized = run("--root", str(root), "--db", str(db), "init",
                              "--id", "demo.board", "--repo", str(repo),
                              "--path", "docs/board.json", "--agent", "setup")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            created = run("--root", str(root), "--db", str(db), "create",
                          "--id", "demo/W-1", "--board", "demo.board", "--title", "First",
                          "--status", "ready", "--write-path", "src/one", "--agent", "setup")
            self.assertEqual(created.returncode, 0, created.stderr)
            claimed = run("--root", str(root), "--db", str(db), "claim", "demo/W-1",
                          "--revision", "0", "--agent", "setup", "--now", T0)
            self.assertEqual(claimed.returncode, 0, claimed.stderr)

    def test_factory_returns_sqlite(self):
        self.assertIsInstance(make_store(self.ws.root, "sqlite", self.db), SqliteStore)
        with self.assertRaises(Exception):
            make_store(self.ws.root, "postgres")

    # --- differential writes -------------------------------------------------------------
    #
    # The CLI still hands the store a whole registry. What must not grow is what the store
    # *writes*: a mutation touches only the documents it changed and leaves the rest alone.

    def _saved(self) -> SqliteStore:
        """A store whose database already holds the fixture registry."""
        sqlite = self.sqlite()
        sqlite.registry = self.ws.load()
        with sqlite.lock():
            sqlite.save()
        return sqlite

    def _scaled(self, count: int) -> dict:
        """The fixture registry padded with synthetic tasks, for size sensitivity."""
        registry = self.ws.load()
        template = registry["tasks"][0]
        for index in range(count - len(registry["tasks"])):
            task = json.loads(json.dumps(template))
            task["id"] = f"demo/S-{index}"
            task["title"] = f"Synthetic {index}"
            registry["tasks"].append(task)
        return registry

    def test_a_claim_rewrites_only_the_changed_task_document(self):
        sqlite = self._saved()
        try:
            with sqlite.lock():
                registry = sqlite.load()
                task = next(item for item in registry["tasks"] if item["id"] == "demo/W-1")
                task["status"] = "claimed"
                task["revision"] = 1
                with Writes(sqlite) as writes:
                    sqlite.commit(event="claim", task="demo/W-1", agent="agent-a",
                                  status="claimed", revision=1, now=T0)
            task_writes = writes.to("tasks")
            self.assertEqual(len(task_writes), 1, task_writes)
            self.assertTrue(task_writes[0].startswith("UPDATE tasks"), task_writes[0])
            for table in ("boards", "edges", "claims"):
                self.assertEqual(writes.to(table), [], f"{table} were rewritten")
        finally:
            sqlite.close()

    def test_write_amplitude_does_not_grow_with_the_registry(self):
        touched, stored = {}, {}
        for size in (4, 400):
            sqlite = SqliteStore(self.ws.root, Path(self.tmp.name) / f"scale-{size}.db")
            try:
                sqlite.registry = self._scaled(size)
                with sqlite.lock():
                    sqlite.save()
                with sqlite.lock():
                    registry = sqlite.load()
                    first = registry["tasks"][0]
                    first["status"] = "claimed"
                    with Writes(sqlite) as writes:
                        sqlite.commit(event="claim", task=first["id"], agent="agent-a",
                                      status="claimed", revision=1, now=T0)
                touched[size] = writes.touched()
                stored[size] = len(sqlite.load()["tasks"])
            finally:
                sqlite.close()
        self.assertEqual(stored, {4: 4, 400: 400})
        self.assertEqual(touched[4], 1)
        self.assertEqual(touched[400], touched[4],
                         "commit amplitude grew with the registry")

    def test_an_unchanged_registry_is_not_rewritten(self):
        sqlite = self._saved()
        try:
            with sqlite.lock():
                sqlite.load()
                with Writes(sqlite) as writes:
                    sqlite.save()
            self.assertEqual(writes.touched(), 0, writes.statements)
        finally:
            sqlite.close()

    def test_a_changed_document_keeps_its_place_in_the_registry(self):
        sqlite = self._saved()
        try:
            with sqlite.lock():
                registry = sqlite.load()
                registry["tasks"][0]["title"] = "Renamed"
                sqlite.save()
                after = sqlite.load()
        finally:
            sqlite.close()
        self.assertEqual([task["id"] for task in after["tasks"]],
                         ["demo/W-1", "demo/W-2", "demo/W-3", "demo/W-4"])
        self.assertEqual(after["tasks"][0]["title"], "Renamed")

    def test_a_document_dropped_from_the_registry_is_deleted(self):
        sqlite = self._saved()
        try:
            with sqlite.lock():
                registry = sqlite.load()
                registry["tasks"] = [task for task in registry["tasks"]
                                     if task["id"] != "demo/W-2"]
                with Writes(sqlite) as writes:
                    sqlite.save()
                after = sqlite.load()
            self.assertEqual(len(writes.to("tasks")), 1, writes.to("tasks"))
            self.assertTrue(writes.to("tasks")[0].startswith("DELETE FROM tasks"))
        finally:
            sqlite.close()
        self.assertEqual([task["id"] for task in after["tasks"]],
                         ["demo/W-1", "demo/W-3", "demo/W-4"])

    def test_an_edge_change_writes_only_that_edge(self):
        sqlite = self._saved()
        try:
            with sqlite.lock():
                registry = sqlite.load()
                registry["edges"].append({"from": "demo/W-2", "to": "demo/W-3",
                                          "kind": "depends-on"})
                with Writes(sqlite) as writes:
                    sqlite.save()
                after = sqlite.load()
            self.assertEqual(len(writes.to("edges")), 1, writes.to("edges"))
            self.assertEqual(writes.to("tasks"), [])
        finally:
            sqlite.close()
        self.assertEqual([edge["from"] for edge in after["edges"]], ["demo/W-2"])

    def _with_edges(self, edges: list[dict]) -> SqliteStore:
        """A saved store whose registry holds `edges`, in that list order."""
        sqlite = self._saved()
        try:
            with sqlite.lock():
                registry = sqlite.load()
                registry["edges"] = edges
                sqlite.save()
        except BaseException:
            sqlite.close()
            raise
        return sqlite

    def test_edge_order_follows_the_registry_and_not_a_set(self):
        """Registry order is rendered by `export` and digested by `genesis`; keep it stable."""
        edges = [{"from": f"demo/W-{index}", "to": "demo/W-4", "kind": "depends-on"}
                 for index in range(1, 4)]
        sqlite = self._with_edges(edges)
        try:
            first = sqlite.export_bytes()
            self.assertEqual([(edge["from"], edge["to"]) for edge in sqlite.load()["edges"]],
                             [(edge["from"], edge["to"]) for edge in edges])
            with sqlite.lock():
                sqlite.load()
                with Writes(sqlite) as writes:
                    sqlite.save()
                again = sqlite.export_bytes()
            self.assertEqual(writes.to("edges"), [])
        finally:
            sqlite.close()
        self.assertEqual(again, first, "an unchanged edge set changed the export")

    def test_edge_order_survives_a_reload_in_list_order(self):
        """An edge added before the others in registry order must not leapfrog the rows on disk."""
        sqlite = self._with_edges([{"from": "demo/W-1", "to": "demo/W-2", "kind": "depends-on"}])
        try:
            with sqlite.lock():
                registry = sqlite.load()
                registry["edges"] = [
                    {"from": "demo/W-2", "to": "demo/W-3", "kind": "depends-on"},
                    {"from": "demo/W-3", "to": "demo/W-4", "kind": "depends-on"},
                ] + registry["edges"]
                sqlite.save()
                after = sqlite.load()
        finally:
            sqlite.close()
        self.assertEqual([(edge["from"], edge["to"]) for edge in after["edges"]],
                         [("demo/W-1", "demo/W-2"), ("demo/W-2", "demo/W-3"),
                          ("demo/W-3", "demo/W-4")],
                         "new edges landed out of order")

    def test_export_bytes_do_not_depend_on_the_process(self):
        """`export` is the diffable state and the genesis digest; PYTHONHASHSEED must not reach it.

        Each reader is a fresh interpreter with a different hash seed, which is what randomises
        set iteration order between commands.
        """
        edges = [{"from": f"demo/W-{index}", "to": "demo/W-4", "kind": "depends-on"}
                 for index in range(1, 4)]
        sqlite = self._with_edges(edges)
        try:
            expected = hashlib.sha256(sqlite.export_bytes()).hexdigest()
            database = str(sqlite.db_path)
        finally:
            sqlite.close()
        roots = []
        for seed in ("0", "1", "2", "3"):
            reader = subprocess.run(
                [sys.executable, "-c",
                 "import hashlib, sys\n"
                 f"sys.path.insert(0, {str(WORK)!r})\n"
                 "from pathlib import Path\n"
                 "from store import SqliteStore\n"
                 f"store = SqliteStore(Path({str(self.ws.root)!r}), Path({database!r}))\n"
                 "print(hashlib.sha256(store.export_bytes()).hexdigest())\n"],
                capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed},
            )
            self.assertEqual(reader.returncode, 0, reader.stderr)
            roots.append((seed, reader.stdout.strip()))
        self.assertEqual({digest for _, digest in roots}, {expected}, roots)

    def test_a_registry_assigned_without_a_load_replaces_the_stored_documents(self):
        """`loom migrate --force` sets `registry` directly; stale rows must still go."""
        sqlite = self._saved()
        try:
            replacement = self.ws.load()
            replacement["tasks"] = replacement["tasks"][:1]
            replacement["boards"] = []
            with sqlite.lock():
                sqlite.registry = replacement
                sqlite.save()
            after = sqlite.load()
        finally:
            sqlite.close()
        self.assertEqual([task["id"] for task in after["tasks"]], ["demo/W-1"])
        self.assertEqual(after["boards"], [])

    def test_a_stale_second_store_does_not_duplicate_a_document(self):
        """Two stores in one process share the file lock, so neither may clobber the other's rows."""
        first = self._saved()
        second = SqliteStore(self.ws.root, self.db)
        try:
            second.load()
            task = json.loads(json.dumps(first.load()["tasks"][0]))
            task["id"] = "demo/W-5"
            with first.lock():
                registry = first.load()
                registry["tasks"].append(task)
                first.save()
            with second.lock():
                second.registry["tasks"].append(json.loads(json.dumps(task)))
                second.save()
            after = second.load()
        finally:
            second.close()
            first.close()
        self.assertEqual([item["id"] for item in after["tasks"]],
                         ["demo/W-1", "demo/W-2", "demo/W-3", "demo/W-4", "demo/W-5"])

    def test_a_rolled_back_commit_leaves_the_change_pending(self):
        sqlite = self._saved()
        try:
            with sqlite.lock():
                registry = sqlite.load()
                task = registry["tasks"][0]
                task["title"] = "Renamed"
                original = sqlite._flush_events

                def boom():
                    raise RuntimeError("event append failed")

                sqlite._flush_events = boom
                try:
                    with self.assertRaises(RuntimeError):
                        sqlite.commit(event="annotate", task=task["id"], status="ready", now=T0)
                finally:
                    sqlite._flush_events = original
                stored = json.loads(sqlite.connection.execute(
                    "SELECT body FROM tasks WHERE id = 'demo/W-1'").fetchone()["body"])
                self.assertEqual(stored["title"], "Task demo/W-1", "the rollback lost a document")
                # A retry must still write: the store kept no memory of the rolled-back attempt.
                registry = sqlite.load()
                registry["tasks"][0]["title"] = "Renamed"
                with Writes(sqlite) as writes:
                    sqlite.commit(event="annotate", task="demo/W-1", status="ready", now=T0)
            self.assertEqual(len(writes.to("tasks")), 1, writes.to("tasks"))
            self.assertEqual(sqlite.load()["tasks"][0]["title"], "Renamed")
        finally:
            sqlite.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
