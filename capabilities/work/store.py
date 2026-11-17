"""Storage for the work registry: one interface, file and SQLite backends.

The CLI works in whole-registry units — load, decide, commit — so both backends implement that
coarse contract. Writes require the sidecar lock. `SqliteStore` commits the registry and its event
in one transaction, keeps tasks and boards as documents, writes only the documents a mutation
changed, reads registry+events as one snapshot, and mirrors the chain head outside the database.
`FileStore` is the legacy importer only; its writes are sequential, not atomic. See
`docs/adr/0002-store-is-a-document-log.md`.

From a baseline snapshot forward, every event that changes state carries that change as a
`state_delta` document in its own body (see `state_delta`). The registry is then a checked
projection of the log rather than a second source of truth: `loom foldcheck` replays the deltas
and reports a field the registry lost or forged. Events written before a snapshot are history the
fold does not need, so there is no backfill.
"""
from __future__ import annotations

import abc
import contextlib
import copy
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from collections import namedtuple
from pathlib import Path

import yaml

DEFAULT_DB = Path.home() / ".local/state/loom/work.db"
ZERO_HASH = "0" * 64
REGISTRY_KIND = "work-coordination-registry"
REGISTRY_HEADER = (
    "# Work coordination registry — authored centrally.\n"
    "# Mutate with `./loom`; verify with `validate_work.py`. Do not hand-edit claims.\n"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS boards (id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS edges (
    from_task TEXT NOT NULL,
    to_task TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (from_task, to_task, kind)
);
CREATE TABLE IF NOT EXISTS claims (artifact TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY,
    task TEXT,
    body TEXT NOT NULL,
    prev TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    seq INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    digest TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(json_extract(body, '$.status'));
CREATE INDEX IF NOT EXISTS tasks_program ON tasks(json_extract(body, '$.program'));
CREATE INDEX IF NOT EXISTS events_task ON events(task);
"""


class WorkError(Exception):
    """A safe, user-facing error."""


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def parse_now(value: str | None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    return parse_time(value).replace(microsecond=0)


def _normalise_dates(value):
    """YAML parses `2026-11-30` into a date; every backend returns the ISO string instead."""
    if isinstance(value, dict):
        return {key: _normalise_dates(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise_dates(item) for item in value]
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def _dump(value) -> str:
    """JSON with dates as ISO strings and insertion order preserved, so exports stay diffable."""
    return json.dumps(value, sort_keys=False, default=str)


def event_bytes(event: dict) -> bytes:
    return json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")


# A task, board and claim are documents keyed differently; a delta names the key it uses.
COLLECTION_KEYS = {"tasks": "id", "boards": "id", "claims": "artifact"}


def merge_state_delta(existing: dict | None, added: dict) -> dict:
    """Combine two `state_delta` documents, keeping the order each change was made in."""
    if not existing:
        return copy.deepcopy(added)
    merged = copy.deepcopy(existing)
    for collection in COLLECTION_KEYS:
        if added.get(collection):
            merged[collection] = merged.get(collection, []) + copy.deepcopy(added[collection])
    if added.get("edges"):
        edges = merged.get("edges") or {}
        merged["edges"] = {
            "added": edges.get("added", []) + copy.deepcopy(added["edges"].get("added", [])),
            "removed": edges.get("removed", []) + copy.deepcopy(added["edges"].get("removed", [])),
        }
    return merged


# An event is hashed when it reaches the database, not when it is created: a delta recorded at
# the end of a transaction still has to land in the body the chain will protect.
_BufferedEvent = namedtuple("_BufferedEvent", "event prev digest")


class Store(abc.ABC):
    """Whole-registry storage. Commands load, mutate, commit; events append separately.

    A mutation is only legal while the store lock is held. `commit` writes the registry and the
    event in one transaction where the backend supports it, so a crash cannot leave state without
    its audit record. `snapshot` reads both in one consistent view.

    `latest_snapshot` is a different thing: the baseline a fold starts from. It is the registry as
    a document at one event sequence, written once and never edited, so historical tasks arrive
    through it instead of being reconstructed from events that predate the delta format.
    """

    root: Path
    _lock_held = False

    def _require_lock(self) -> None:
        if not self._lock_held:
            raise WorkError("mutation requires the store lock; use `with store.lock():`")

    @contextlib.contextmanager
    def transaction(self, record_state: bool = True):
        """One atomic unit of registry state; subclasses override with a real transaction.

        `record_state=False` suppresses delta capture. It is for a write that restores state to
        what the log already says (a rebuild); recording it again would double-apply the change.
        """
        self._require_lock()
        yield self
        self.save()

    def commit(self, **fields) -> dict:
        """Write the registry and append one event. Overridden atomically by SQLite."""
        self._require_lock()
        self.save()
        return self.append_event(**fields)

    def snapshot(self) -> tuple[dict, list[tuple[bytes, dict]]]:
        """A consistent (registry, events) pair. Overridden by SQLite."""
        return self.load(), self.read_events()

    def checkpoint(self) -> tuple[int, str] | None:
        """The external chain head, where the backend keeps one."""
        return None

    def latest_snapshot(self) -> dict | None:
        """The fold baseline: `{seq, ts, digest, registry}`, or None when none was taken."""
        return None

    def snapshots(self) -> list[dict]:
        """Every baseline snapshot, oldest first, without the stored registry body."""
        return []

    def write_snapshot(self, seq: int, ts: str, digest: str, body: str) -> None:
        self._require_lock()
        raise WorkError(f"{type(self).__name__} cannot hold a snapshot")

    def backup_to(self, path: Path) -> Path:
        """Write a consistent copy of the database, for a restore point."""
        self._require_lock()
        raise WorkError(f"{type(self).__name__} cannot write a restore point")

    def reanchor(self, allow_rewind: bool = False) -> None:
        """Re-write the external chain head after a verified audit."""
        self._require_lock()

    @abc.abstractmethod
    def load(self) -> dict: ...

    @abc.abstractmethod
    def save(self) -> None: ...

    @abc.abstractmethod
    def has_registry(self) -> bool: ...

    @abc.abstractmethod
    def read_events(self) -> list[tuple[bytes, dict]]: ...

    @abc.abstractmethod
    def append_event(self, **fields) -> dict: ...

    @abc.abstractmethod
    def lock(self): ...

    def describe(self) -> str:
        return type(self).__name__

    def export_bytes(self) -> bytes:
        """A stable rendering of the registry, used for genesis digests and review."""
        return yaml.safe_dump(self.load(), sort_keys=False, default_flow_style=False,
                              allow_unicode=True, width=100).encode("utf-8")


class _FileLock:
    """An advisory whole-store lock that fails loudly instead of waiting forever.

    `flock(LOCK_EX)` with no timeout queues indefinitely. One command that hangs while holding the
    lock would then stall the scheduler silently: launchd reports exit 0 for the passes that never
    ran, and every other writer waits behind it. A bounded non-blocking retry turns that into a
    named error with the holder recorded in the lock file.
    """

    def __init__(self, path: Path, timeout: float | None = None):
        self.path = path
        if timeout is None:
            try:
                timeout = float(os.environ.get("WORK_LOCK_TIMEOUT", "20"))
            except ValueError:
                timeout = 20.0
        self.timeout = max(0.0, timeout)
        self.handle = None

    def _holder(self) -> str:
        try:
            lines = [line for line in self.path.read_text().splitlines() if line.strip()]
        except OSError:
            return ""
        return lines[-1] if lines else ""

    def _record_holder(self) -> None:
        try:
            self.handle.seek(0)
            self.handle.truncate()
            self.handle.write(f"{os.getpid()} {' '.join(sys.argv[:3])}\n")
            self.handle.flush()
        except OSError:
            pass

    def __enter__(self):
        import fcntl

        self.handle = self.path.open("a+")
        deadline = time.monotonic() + self.timeout
        delay = 0.01
        while True:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    holder = self._holder()
                    self.handle.close()
                    self.handle = None
                    detail = f"; held by {holder}" if holder else ""
                    raise WorkError(
                        f"timed out after {self.timeout:g}s waiting for the store lock at "
                        f"{self.path}{detail}. Another loom command is holding it, or a stale "
                        "process never released it. Raise WORK_LOCK_TIMEOUT or stop that process."
                    ) from None
                time.sleep(delay)
                delay = min(delay * 2, 0.25)
        self._record_holder()
        return self

    def __exit__(self, *exc):
        import fcntl

        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
        return False


class _TrackedLock:
    """The file lock, plus the store's knowledge that a mutation is legal."""

    def __init__(self, store: Store, lock):
        self.store = store
        self.lock = lock

    def __enter__(self):
        self.lock.__enter__()
        self.store._lock_held = True
        return self

    def __exit__(self, *exc):
        self.store._lock_held = False
        return self.lock.__exit__(*exc)


class FileStore(Store):
    """`registry.yaml` plus `events.ndjson` in a work home. Read-only legacy importer.

    SQLite is the only writable backend. This class exists so `loom migrate` can read a home written
    by an older release; it deliberately has no write path, because the file backend cannot write
    state and its event together and was never atomic.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.registry_path = self.root / "registry.yaml"
        self.events_path = self.root / "events.ndjson"
        self.lock_path = self.root / ".work.lock"
        self.registry: dict = {}
        self._lock_held = False

    def describe(self) -> str:
        return f"files:{self.root}"

    def load(self) -> dict:
        if not self.registry_path.is_file():
            raise WorkError(f"missing registry at {self.registry_path}")
        with self.registry_path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if data.get("kind") != REGISTRY_KIND:
            raise WorkError("registry kind must be work-coordination-registry")
        data.setdefault("boards", [])
        data.setdefault("tasks", [])
        data.setdefault("edges", [])
        data.setdefault("claims", [])
        data = _normalise_dates(data)
        self.registry = data
        return data

    def save(self) -> None:
        raise WorkError(
            "the file backend is read-only; import a pre-SQLite home with `loom migrate`")

    def lock(self):
        return _TrackedLock(self, _FileLock(self.lock_path))

    def has_registry(self) -> bool:
        return self.registry_path.is_file()

    def export_bytes(self) -> bytes:
        return self.registry_path.read_bytes() if self.registry_path.is_file() else b""

    def read_events(self) -> list[tuple[bytes, dict]]:
        if not self.events_path.is_file():
            return []
        records = []
        for raw in self.events_path.read_bytes().splitlines():
            if raw.strip():
                records.append((raw, json.loads(raw)))
        return records

    def append_event(self, **fields) -> dict:
        raise WorkError(
            "the file backend is read-only; import a pre-SQLite home with `loom migrate`")


class SqliteStore(Store):
    """One SQLite file. Tasks and boards are documents; edges and meta are relational.

    A task body is the authoritative record. The only structured copies are expression indexes
    derived from it, so the columns cannot disagree with the document. The CLI still hands over a
    whole registry, but the write is differential: a save compares each document against the body
    recorded when it was read and touches only the rows that differ, so commit cost follows the
    change set rather than the size of the registry.
    """

    def __init__(self, root: Path, db_path: Path | None = None):
        self.root = Path(root)
        self.db_path = (Path(db_path).expanduser() if db_path else DEFAULT_DB).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(SCHEMA)
        self._upgrade()
        self.registry: dict = {}
        self._lock_held = False
        self._tx_depth = 0
        self._read_depth = 0
        self._last_event: dict | None = None
        # What the database holds, as `load()` or the first write found it. A save diffs against
        # this, so only changed documents reach the disk; `None` means "not read yet".
        self._stored: dict | None = None
        # Events staged inside the open transaction, and the documents the delta diffs against.
        self._event_buffer: list = []
        self._delta_base: dict | None = None
        self._record_state = True

    def _upgrade(self) -> None:
        """Bring an existing database up to the current schema without losing events."""
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(events)")}
        if "task" not in columns:
            self.connection.execute("ALTER TABLE events ADD COLUMN task TEXT")
            self.connection.execute(
                "UPDATE events SET task = json_extract(body, '$.task') WHERE task IS NULL")
            self.connection.execute("DROP INDEX IF EXISTS events_task")
            self.connection.execute("CREATE INDEX IF NOT EXISTS events_task ON events(task)")
        if {row["name"] for row in self.connection.execute("PRAGMA table_info(boards)")} - {"id", "body"}:
            self._rebuild_document_table("boards", "id, body")
        if {row["name"] for row in self.connection.execute("PRAGMA table_info(tasks)")} - {"id", "body"}:
            self._rebuild_document_table("tasks", "id, body")
            self.connection.execute("DROP INDEX IF EXISTS tasks_status")
            self.connection.execute("DROP INDEX IF EXISTS tasks_program")
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS tasks_status ON tasks(json_extract(body, '$.status'))")
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS tasks_program ON tasks(json_extract(body, '$.program'))")

    def _rebuild_document_table(self, table: str, columns: str) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                f"CREATE TABLE {table}_document (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            self.connection.execute(
                f"INSERT INTO {table}_document ({columns}) SELECT {columns} FROM {table}")
            self.connection.execute(f"DROP TABLE {table}")
            self.connection.execute(f"ALTER TABLE {table}_document RENAME TO {table}")
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def describe(self) -> str:
        return f"sqlite:{self.db_path}"

    def close(self) -> None:
        self.connection.close()

    def has_registry(self) -> bool:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key = 'kind'").fetchone()
        return bool(row and row["value"] == REGISTRY_KIND)

    def lock(self):
        """Serialise whole commands across processes.

        SQLite makes each write durable, but the CLI reads a revision, decides, and writes it back;
        that read-modify-write needs one writer at a time or two claims both "succeed". Every write
        method refuses to run unless this lock is held.
        """
        return _TrackedLock(self, _FileLock(Path(str(self.db_path) + ".lock")))

    @contextlib.contextmanager
    def _read_transaction(self):
        """A consistent multi-query read; nested reads join the outer transaction."""
        if self._tx_depth or self._read_depth:
            self._read_depth += 1
            try:
                yield
            finally:
                self._read_depth -= 1
            return
        self.connection.execute("BEGIN DEFERRED")
        self._read_depth = 1
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        finally:
            self._read_depth = 0

    @contextlib.contextmanager
    def transaction(self, record_state: bool = True):
        """One atomic unit: registry state and any events appended inside it.

        State and event are written together, and from a snapshot forward the event also carries
        the state change it made. A single `commit` records its own task's delta; a batch that
        changes several documents leaves the remainder on its last event.
        """
        self._require_lock()
        if self._tx_depth:
            self._tx_depth += 1
            try:
                yield self
            finally:
                self._tx_depth -= 1
            return
        self.connection.execute("BEGIN IMMEDIATE")
        self._tx_depth = 1
        before = self._last_event
        checkpoint_event = None
        self._event_buffer = []
        self._record_state = record_state
        self._delta_base = self._documents()
        try:
            yield self
            if record_state:
                self._record_delta(self._state_delta())
            self._flush_events()
            pending = self._write_registry()
            self.connection.execute("COMMIT")
            self._stored = pending
            if self._last_event is not before and self._last_event is not None:
                checkpoint_event = self._last_event
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise
        finally:
            self._tx_depth = 0
            self._event_buffer = []
            self._delta_base = None
            self._record_state = True
        if checkpoint_event is not None:
            try:
                self._write_checkpoint(checkpoint_event)
            except OSError as exc:
                raise WorkError(
                    f"mutation committed but the audit head could not be written: {exc}") from exc

    def load(self) -> dict:
        with self._read_transaction():
            meta = {row["key"]: row["value"]
                    for row in self.connection.execute("SELECT key, value FROM meta")}
            if meta.get("kind") != REGISTRY_KIND:
                raise WorkError(f"missing registry at {self.db_path}")
            board_rows = [(row["id"], row["body"]) for row in
                          self.connection.execute("SELECT id, body FROM boards ORDER BY rowid")]
            task_rows = [(row["id"], row["body"]) for row in
                         self.connection.execute("SELECT id, body FROM tasks ORDER BY rowid")]
            # Edges are read in rowid order, like the documents. Registry order is part of what
            # `export` renders and what `genesis` digests, so it must not depend on set iteration
            # (which PYTHONHASHSEED randomises per process). The set below is membership only.
            edge_rows = [(row["from_task"], row["to_task"], row["kind"]) for row in
                         self.connection.execute(
                             "SELECT from_task, to_task, kind FROM edges ORDER BY rowid")]
            claim_rows = [(row["artifact"], row["body"]) for row in
                          self.connection.execute("SELECT artifact, body FROM claims ORDER BY rowid")]
            data = {
                "kind": REGISTRY_KIND,
                "version": int(meta.get("version", 1)),
                "updated": meta.get("updated", ""),
                "boards": [json.loads(body) for _, body in board_rows],
                "tasks": [json.loads(body) for _, body in task_rows],
                "edges": [{"from": source, "to": target, "kind": kind}
                          for source, target, kind in edge_rows],
                "claims": [json.loads(body) for _, body in claim_rows],
            }
            self._stored = {
                "meta": meta,
                "boards": dict(board_rows),
                "tasks": dict(task_rows),
                "edges": set(edge_rows),
                "claims": dict(claim_rows),
            }
        self.registry = data
        return data

    def _read_stored(self) -> dict:
        """The rows on disk, for a caller that set `registry` without reading it first."""
        return {
            "meta": {row["key"]: row["value"]
                     for row in self.connection.execute("SELECT key, value FROM meta")},
            "boards": {row["id"]: row["body"]
                       for row in self.connection.execute("SELECT id, body FROM boards")},
            "tasks": {row["id"]: row["body"]
                      for row in self.connection.execute("SELECT id, body FROM tasks")},
            "edges": {(row["from_task"], row["to_task"], row["kind"])
                      for row in self.connection.execute("SELECT from_task, to_task, kind FROM edges")},
            "claims": {row["artifact"]: row["body"]
                       for row in self.connection.execute("SELECT artifact, body FROM claims")},
        }

    def _sync_documents(self, documents, key: str, table: str, column: str,
                        stored: dict[str, str]) -> dict[str, str]:
        """Write only the documents whose serialised body differs from what is stored.

        `UPDATE` keeps an existing row where it is, so a task keeps its place in the registry
        order when it changes; only a new document is appended.
        """
        current: dict[str, str] = {}
        for document in documents:
            identifier = str(document[key])
            body = _dump(document)
            current[identifier] = body
            if stored.get(identifier) == body:
                continue
            # The row decides whether it exists, so a stale cache cannot turn a change into a
            # lost or a duplicated document.
            updated = self.connection.execute(
                f"UPDATE {table} SET body = ? WHERE {column} = ?", (body, identifier))
            if updated.rowcount == 0:
                self.connection.execute(
                    f"INSERT INTO {table} ({column}, body) VALUES (?, ?)", (identifier, body))
        for identifier in stored:
            if identifier not in current:
                self.connection.execute(f"DELETE FROM {table} WHERE {column} = ?", (identifier,))
        return current

    def _sync_edges(self, edges, stored: set) -> set:
        """Write only the edges the registry added or dropped, in registry order.

        The difference is a set, but the inserts walk the registry list, so newly added edges keep
        the order the command gave them instead of the arbitrary order `set` iteration produced.
        """
        current: list[tuple[str, str, str]] = []
        seen: set = set()
        for edge in edges:
            key = (edge["from"], edge["to"], edge.get("kind", "depends-on"))
            if key in seen:
                continue
            seen.add(key)
            current.append(key)
        for key in current:
            if key in stored:
                continue
            self.connection.execute(
                "INSERT OR IGNORE INTO edges (from_task, to_task, kind) VALUES (?, ?, ?)", key)
        for key in stored:
            if key not in seen:
                self.connection.execute(
                    "DELETE FROM edges WHERE from_task = ? AND to_task = ? AND kind = ?", key)
        return seen

    def _write_registry(self) -> dict:
        """Persist the registry difference and return the rows the database now holds."""
        self.registry["updated"] = iso(parse_now(None))[:10]
        registry = self.registry
        stored = self._stored if self._stored is not None else self._read_stored()
        pending = {
            "meta": stored["meta"],
            "boards": self._sync_documents(
                registry.get("boards", []), "id", "boards", "id", stored["boards"]),
            "tasks": self._sync_documents(
                registry.get("tasks", []), "id", "tasks", "id", stored["tasks"]),
            "edges": self._sync_edges(registry.get("edges", []), stored["edges"]),
            "claims": self._sync_documents(
                registry.get("claims", []), "artifact", "claims", "artifact", stored["claims"]),
        }
        for key, value in (("kind", REGISTRY_KIND), ("version", str(registry.get("version", 1))),
                           ("updated", str(registry.get("updated", "")))):
            if stored["meta"].get(key) == value:
                continue
            self.connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
            pending["meta"] = {**pending["meta"], key: value}
        return pending

    def save(self) -> None:
        self._require_lock()
        with self.transaction():
            pass

    def commit(self, **fields) -> dict:
        """Write the registry and append the event in one SQLite transaction.

        The event carries the task's state change (`state_delta`) when the registry document
        differs from what the transaction read, so a mutation cannot reach the state without
        leaving the change in the log.
        """
        self._require_lock()
        with self.transaction():
            event = self.append_event(**fields)
            task_id = fields.get("task")
            if self._record_state and task_id:
                self._record_committed_task(str(task_id))
        return event

    def read_events(self) -> list[tuple[bytes, dict]]:
        rows = [(row["body"].encode("utf-8"), json.loads(row["body"])) for row in
                self.connection.execute("SELECT body FROM events ORDER BY seq")]
        # A command reads the log back inside its own transaction (the reconciler closes attempts
        # and then folds them), so buffered events count as written until COMMIT.
        rows.extend((event_bytes(item.event), item.event) for item in self._event_buffer)
        return rows

    def _next_seq(self) -> int:
        if self._event_buffer:
            return self._event_buffer[-1].event["seq"] + 1
        last = self.connection.execute(
            "SELECT seq FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return (last["seq"] + 1) if last else 1

    def _buffer_event(self, **fields) -> dict:
        """Stage one event. Its hash is computed at flush, after any delta is attached."""
        event = {"seq": self._next_seq(), "ts": iso(parse_now(fields.pop("now", None))), **fields}
        self._event_buffer.append(_BufferedEvent(event=event, prev=None, digest=None))
        return event

    def _flush_events(self) -> None:
        if not self._event_buffer:
            return
        last = self.connection.execute(
            "SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = last["hash"] if last else ZERO_HASH
        for item in self._event_buffer:
            item.event["prev"] = prev
            raw = event_bytes(item.event)
            digest = hashlib.sha256(raw).hexdigest()
            self.connection.execute(
                "INSERT INTO events (seq, task, body, prev, hash) VALUES (?, ?, ?, ?, ?)",
                (item.event["seq"], item.event.get("task"), raw.decode("utf-8"), prev, digest))
            prev = digest
            self._last_event = item.event
        self._event_buffer = []

    def append_event(self, **fields) -> dict:
        """Append an audit record that does not change registry state (ask, launch, attempt…)."""
        self._require_lock()
        if self._tx_depth:
            return self._buffer_event(**fields)
        self.connection.execute("BEGIN IMMEDIATE")
        self._tx_depth = 1
        self._event_buffer = []
        self._record_state = False
        try:
            event = self._buffer_event(**fields)
            self._flush_events()
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise
        finally:
            self._tx_depth = 0
            self._event_buffer = []
            self._record_state = True
        try:
            self._write_checkpoint(event)
        except OSError as exc:
            raise WorkError(
                f"event committed but the audit head could not be written: {exc}") from exc
        return event

    # -- state deltas ----------------------------------------------------

    def _documents(self) -> dict:
        """The documents as the database holds them, for a delta to diff against."""
        stored = self._stored if self._stored is not None else self._read_stored()

        def parsed(section: str) -> dict:
            return {identifier: json.loads(body)
                    for identifier, body in stored[section].items()}

        return {
            "tasks": parsed("tasks"),
            "boards": parsed("boards"),
            "claims": parsed("claims"),
            "edges": set(stored["edges"]),
        }

    def _registry_documents(self) -> dict | None:
        """The registry in memory, or None when this command never loaded one."""
        registry = self.registry
        if not registry or "tasks" not in registry:
            return None
        return {
            "tasks": {str(task["id"]): task for task in registry.get("tasks", [])},
            "boards": {str(board["id"]): board for board in registry.get("boards", [])},
            "claims": {str(claim["artifact"]): claim for claim in registry.get("claims", [])},
            "edges": {(edge["from"], edge["to"], edge.get("kind", "depends-on"))
                      for edge in registry.get("edges", [])},
        }

    def _state_delta(self, only_task: str | None = None) -> dict:
        """The field-level difference between the documents on disk and the registry.

        Field-level, because "the registry changed" is not evidence: a report has to name the
        field that moved. A whole document is named only when it was created or deleted.
        """
        current = self._registry_documents()
        if current is None:
            return {}
        base = self._delta_base or {"tasks": {}, "boards": {}, "claims": {}, "edges": set()}
        delta: dict = {}
        for collection in COLLECTION_KEYS:
            if only_task is not None and collection != "tasks":
                continue
            changes = []
            for identifier, document in current[collection].items():
                if only_task is not None and identifier != only_task:
                    continue
                before = base[collection].get(identifier)
                if before is None:
                    changes.append({"id": identifier, "created": copy.deepcopy(document)})
                    continue
                set_fields = {}
                for field, value in document.items():
                    if field not in before or before[field] != value:
                        set_fields[field] = copy.deepcopy(value)
                unset = [field for field in before if field not in document]
                if set_fields or unset:
                    change = {"id": identifier}
                    if set_fields:
                        change["set"] = set_fields
                    if unset:
                        change["unset"] = unset
                    changes.append(change)
            if only_task is None:
                changes.extend(
                    {"id": identifier, "deleted": True}
                    for identifier in base[collection] if identifier not in current[collection])
            if changes:
                delta[collection] = changes
        if only_task is None:
            added = sorted(current["edges"] - base["edges"])
            removed = sorted(base["edges"] - current["edges"])
            if added or removed:
                delta["edges"] = {
                    "added": [{"from": source, "to": target, "kind": kind}
                              for source, target, kind in added],
                    "removed": [{"from": source, "to": target, "kind": kind}
                                for source, target, kind in removed],
                }
        return delta

    def _record_delta(self, delta: dict) -> None:
        """Attach a delta to the event that carries the mutation, if there is one."""
        if not delta or not self._event_buffer:
            return
        event = self._event_buffer[-1].event
        event["state_delta"] = merge_state_delta(event.get("state_delta"), delta)

    def _record_committed_task(self, task_id: str) -> None:
        """Record one task's change on its own event, and move the delta baseline past it."""
        delta = self._state_delta(only_task=task_id)
        self._record_delta(delta)
        current = self._registry_documents()
        if current is None or self._delta_base is None:
            return
        for change in delta.get("tasks", []):
            identifier = change["id"]
            if identifier in current["tasks"]:
                self._delta_base["tasks"][identifier] = copy.deepcopy(current["tasks"][identifier])
            else:
                self._delta_base["tasks"].pop(identifier, None)

    def snapshot(self) -> tuple[dict, list[tuple[bytes, dict]]]:
        with self._read_transaction():
            registry = self.load()
            events = self.read_events()
        return registry, events

    def _write_checkpoint(self, event: dict) -> None:
        """Write the chain head outside the database, so a rewrite of the DB is visible."""
        head = Path(str(self.db_path) + ".head")
        payload = f"{event['seq']} {hashlib.sha256(event_bytes(event)).hexdigest()}\n"
        tmp = Path(str(head) + ".tmp")
        tmp.write_text(payload)
        os.replace(tmp, head)

    # -- baseline snapshots and restore points ---------------------------

    def write_snapshot(self, seq: int, ts: str, digest: str, body: str) -> None:
        """Store the registry as the baseline for the event at `seq`.

        Called inside the transaction that writes the marking event, so the baseline and the
        point it is taken from cannot disagree.
        """
        self._require_lock()
        self.connection.execute(
            "INSERT INTO snapshots (seq, ts, digest, body) VALUES (?, ?, ?, ?)",
            (seq, ts, digest, body))

    def snapshots(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT seq, ts, digest, body FROM snapshots ORDER BY seq")
        return [{"seq": row["seq"], "ts": row["ts"], "digest": row["digest"],
                 "tasks": len(json.loads(row["body"]).get("tasks", []))}
                for row in rows]

    def latest_snapshot(self) -> dict | None:
        row = self.connection.execute(
            "SELECT seq, ts, digest, body FROM snapshots ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return {"seq": row["seq"], "ts": row["ts"], "digest": row["digest"],
                "registry": json.loads(row["body"])}

    def backup_to(self, path: Path) -> Path:
        """A consistent copy of the database via `VACUUM INTO`, for a rebuild's restore point."""
        self._require_lock()
        if self.connection.in_transaction:
            raise WorkError("a restore point must be written outside a transaction")
        target = Path(path).expanduser()
        if target.exists():
            raise WorkError(f"restore point {target} already exists; refusing to overwrite it")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.connection.execute("VACUUM INTO ?", (str(target),))
        return target

    def reanchor(self, allow_rewind: bool = False) -> None:
        self._require_lock()
        records = self.read_events()
        if not records:
            return
        current = self.checkpoint()
        last = records[-1][1]
        if (current and current[0] > int(last.get("seq") or 0) and not allow_rewind):
            raise WorkError(
                f"refusing to lower the audit head from {current[0]} to {last.get('seq')}; "
                "pass --allow-rewind if the truncation is intentional")
        self._write_checkpoint(last)

    def checkpoint(self) -> tuple[int, str] | None:
        head = Path(str(self.db_path) + ".head")
        if not head.is_file():
            return None
        try:
            seq, digest = head.read_text().split()
            return int(seq), digest
        except (OSError, ValueError):
            return None


def make_store(root: Path, backend: str = "sqlite", db_path: Path | None = None) -> Store:
    if backend == "sqlite":
        return SqliteStore(root, db_path)
    if backend != "files":
        raise WorkError(f"unknown store backend {backend!r}; use files or sqlite")
    return FileStore(root)
