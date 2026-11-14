"""Storage for the work registry: one interface, file and SQLite backends.

The CLI works in whole-registry units — load, decide, commit — so both backends implement that
coarse contract. Writes require the sidecar lock. `SqliteStore` commits the registry and its event
in one transaction, keeps tasks and boards as documents, reads registry+events as one snapshot, and
mirrors the chain head outside the database. `FileStore` is the legacy importer only; its writes
are sequential, not atomic. See `docs/adr/0002-store-is-a-document-log.md`.
"""
from __future__ import annotations

import abc
import contextlib
import datetime as dt
import hashlib
import json
import os
import sqlite3
import tempfile
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


class Store(abc.ABC):
    """Whole-registry storage. Commands load, mutate, commit; events append separately.

    A mutation is only legal while the store lock is held. `commit` writes the registry and the
    event in one transaction where the backend supports it, so a crash cannot leave state without
    its audit record. `snapshot` reads both in one consistent view.
    """

    root: Path
    _lock_held = False

    def _require_lock(self) -> None:
        if not self._lock_held:
            raise WorkError("mutation requires the store lock; use `with store.lock():`")

    @contextlib.contextmanager
    def transaction(self):
        """One atomic unit of registry state; subclasses override with a real transaction."""
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

    def reanchor(self) -> None:
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
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        import fcntl

        self.handle = self.path.open("a+")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        import fcntl

        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
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
    """`registry.yaml` plus `events.ndjson` in a work home. Legacy importer; not atomic."""

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
        self._require_lock()
        self.registry["updated"] = iso(parse_now(None))[:10]
        body = yaml.safe_dump(
            self.registry,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
            width=100,
        )
        mode = (self.registry_path.stat().st_mode & 0o777) if self.registry_path.exists() else 0o644
        fd, tmp = tempfile.mkstemp(dir=str(self.root), prefix=".registry.", suffix=".yaml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(REGISTRY_HEADER)
                handle.write(body)
            os.chmod(tmp, mode)
            os.replace(tmp, self.registry_path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

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
        self._require_lock()
        records = self.read_events()
        seq = len(records) + 1
        prev = hashlib.sha256(records[-1][0]).hexdigest() if records else ZERO_HASH
        event = {"seq": seq, "ts": iso(parse_now(fields.pop("now", None))), **fields, "prev": prev}
        with self.events_path.open("ab") as handle:
            handle.write(event_bytes(event) + b"\n")
        return event


class SqliteStore(Store):
    """One SQLite file. Tasks and boards are documents; edges and meta are relational.

    A task body is the authoritative record. The only structured copies are expression indexes
    derived from it, so the columns cannot disagree with the document. Whole-registry saves rewrite
    every document, which is the price of the coarse CLI contract; intent-level operations can
    replace that later without changing the file format.
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
    def transaction(self):
        """One atomic unit: registry state and any events appended inside it."""
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
        try:
            yield self
            self._write_registry()
            self.connection.execute("COMMIT")
            if self._last_event is not before and self._last_event is not None:
                self._write_checkpoint(self._last_event)
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        finally:
            self._tx_depth = 0

    def load(self) -> dict:
        with self._read_transaction():
            meta = {row["key"]: row["value"]
                    for row in self.connection.execute("SELECT key, value FROM meta")}
            if meta.get("kind") != REGISTRY_KIND:
                raise WorkError(f"missing registry at {self.db_path}")
            data = {
                "kind": REGISTRY_KIND,
                "version": int(meta.get("version", 1)),
                "updated": meta.get("updated", ""),
                "boards": [json.loads(row["body"]) for row in
                           self.connection.execute("SELECT body FROM boards ORDER BY rowid")],
                "tasks": [json.loads(row["body"]) for row in
                          self.connection.execute("SELECT body FROM tasks ORDER BY rowid")],
                "edges": [dict(row) for row in
                          self.connection.execute("SELECT from_task AS \"from\", to_task AS \"to\", kind FROM edges")],
                "claims": [json.loads(row["body"]) for row in
                           self.connection.execute("SELECT body FROM claims ORDER BY rowid")],
            }
            data["edges"] = [{"from": edge["from"], "to": edge["to"], "kind": edge["kind"]}
                             for edge in data["edges"]]
        self.registry = data
        return data

    def _write_registry(self) -> None:
        self.registry["updated"] = iso(parse_now(None))[:10]
        registry = self.registry
        self.connection.execute("DELETE FROM boards")
        self.connection.execute("DELETE FROM tasks")
        self.connection.execute("DELETE FROM edges")
        self.connection.execute("DELETE FROM claims")
        for board in registry.get("boards", []):
            self.connection.execute(
                "INSERT INTO boards (id, body) VALUES (?, ?)", (board["id"], _dump(board)))
        for task in registry.get("tasks", []):
            self.connection.execute(
                "INSERT INTO tasks (id, body) VALUES (?, ?)", (task["id"], _dump(task)))
        for edge in registry.get("edges", []):
            self.connection.execute(
                "INSERT OR IGNORE INTO edges (from_task, to_task, kind) VALUES (?, ?, ?)",
                (edge["from"], edge["to"], edge.get("kind", "depends-on")))
        for claim in registry.get("claims", []):
            self.connection.execute(
                "INSERT OR REPLACE INTO claims (artifact, body) VALUES (?, ?)",
                (claim["artifact"], _dump(claim)))
        for key, value in (("kind", REGISTRY_KIND), ("version", str(registry.get("version", 1))),
                           ("updated", str(registry.get("updated", "")))):
            self.connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))

    def save(self) -> None:
        self._require_lock()
        with self.transaction():
            pass

    def commit(self, **fields) -> dict:
        """Write the registry and append the event in one SQLite transaction."""
        self._require_lock()
        with self.transaction():
            event = self._append_event(**fields)
        return event

    def read_events(self) -> list[tuple[bytes, dict]]:
        rows = self.connection.execute("SELECT body FROM events ORDER BY seq")
        return [(row["body"].encode("utf-8"), json.loads(row["body"])) for row in rows]

    def _append_event(self, **fields) -> dict:
        last = self.connection.execute(
            "SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        seq = (last["seq"] + 1) if last else 1
        prev = last["hash"] if last else ZERO_HASH
        event = {"seq": seq, "ts": iso(parse_now(fields.pop("now", None))), **fields, "prev": prev}
        raw = event_bytes(event)
        self.connection.execute(
            "INSERT INTO events (seq, task, body, prev, hash) VALUES (?, ?, ?, ?, ?)",
            (seq, event.get("task"), raw.decode("utf-8"), prev, hashlib.sha256(raw).hexdigest()))
        self._last_event = event
        return event

    def append_event(self, **fields) -> dict:
        self._require_lock()
        if self._tx_depth:
            return self._append_event(**fields)
        self.connection.execute("BEGIN IMMEDIATE")
        self._tx_depth = 1
        try:
            event = self._append_event(**fields)
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        finally:
            self._tx_depth = 0
        self._write_checkpoint(event)
        return event

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

    def reanchor(self) -> None:
        self._require_lock()
        records = self.read_events()
        if records:
            self._write_checkpoint(records[-1][1])

    def checkpoint(self) -> tuple[int, str] | None:
        head = Path(str(self.db_path) + ".head")
        if not head.is_file():
            return None
        try:
            seq, digest = head.read_text().split()
            return int(seq), digest
        except (OSError, ValueError):
            return None


def make_store(root: Path, backend: str = "files", db_path: Path | None = None) -> Store:
    if backend == "sqlite":
        return SqliteStore(root, db_path)
    if backend != "files":
        raise WorkError(f"unknown store backend {backend!r}; use files or sqlite")
    return FileStore(root)
