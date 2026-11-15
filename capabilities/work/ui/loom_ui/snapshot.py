"""One consistent, read-only read of the registry.

Every read model is built from a single :class:`Snapshot`. The UI never issues two queries for one
page: a task list and the claimability derived from it must describe the same instant, or a task can
appear claimable on one half of the page and claimed on the other.

The connection is opened ``mode=ro`` and ``query_only`` so a UI bug cannot become a write. Inside
one ``BEGIN DEFERRED`` transaction the snapshot reads the same tables ``store.SqliteStore.load``
does — documents, edges and the event log — and the event head at the same time. Two read models
therefore cannot disagree about which event produced which document.

Schema drift is caught rather than guessed: a missing table or a registry that does not identify
itself raises :class:`SnapshotUnavailable`, which the server renders as a degraded page.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import store

REGISTRY_KIND = store.REGISTRY_KIND


class SnapshotUnavailable(RuntimeError):
    """The registry could not be read as one consistent snapshot."""


@dataclass(frozen=True)
class Snapshot:
    db_path: str
    as_of: str
    registry: dict
    events: tuple[dict, ...]
    head_seq: int
    head_hash: str
    digest: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def task_count(self) -> int:
        return len(self.registry.get("tasks", []))

    @property
    def board_count(self) -> int:
        return len(self.registry.get("boards", []))

    def summary(self) -> dict:
        return {
            "db": self.db_path,
            "as_of": self.as_of,
            "digest": self.digest,
            "tasks": self.task_count,
            "boards": self.board_count,
            "events": len(self.events),
            "head": {"seq": self.head_seq, "hash": self.head_hash},
        }


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _connect(path: Path) -> sqlite3.Connection:
    quoted = urllib.parse.quote(str(path))
    try:
        connection = sqlite3.connect(f"file:{quoted}?mode=ro", uri=True, timeout=5,
                                     isolation_level=None)
    except sqlite3.Error as exc:
        raise SnapshotUnavailable(f"cannot open {path}: {exc}") from exc
    connection.row_factory = sqlite3.Row
    return connection


def read(db_path: Path | str | None = None, now: datetime | None = None) -> Snapshot:
    """Read registry documents and the event head in one transaction."""
    path = Path(db_path).expanduser() if db_path else _default_db()
    if not path.exists():
        raise SnapshotUnavailable(f"registry database {path} does not exist")
    if not path.is_file():
        raise SnapshotUnavailable(f"registry database {path} is not a file")

    connection = _connect(path)
    try:
        connection.execute("PRAGMA query_only=ON")
        try:
            connection.execute("BEGIN DEFERRED")
            meta = {row["key"]: row["value"]
                    for row in connection.execute("SELECT key, value FROM meta")}
            if meta.get("kind") != REGISTRY_KIND:
                raise SnapshotUnavailable(
                    f"{path} does not identify itself as a work coordination registry")
            boards = [json.loads(row["body"]) for row in
                      connection.execute("SELECT body FROM boards ORDER BY rowid")]
            tasks = [json.loads(row["body"]) for row in
                     connection.execute("SELECT body FROM tasks ORDER BY rowid")]
            edges = [{"from": row["from_task"], "to": row["to_task"], "kind": row["kind"]}
                     for row in connection.execute(
                         "SELECT from_task, to_task, kind FROM edges ORDER BY rowid")]
            claims = [json.loads(row["body"]) for row in
                      connection.execute("SELECT artifact, body FROM claims ORDER BY rowid")]
            events = tuple(json.loads(row["body"]) for row in
                           connection.execute("SELECT body FROM events ORDER BY seq"))
            head = connection.execute(
                "SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            connection.execute("COMMIT")
        except sqlite3.Error as exc:
            raise SnapshotUnavailable(f"cannot read {path}: {exc}") from exc
    finally:
        connection.close()

    registry = {
        "kind": REGISTRY_KIND,
        "version": int(meta.get("version", 1)),
        "updated": meta.get("updated", ""),
        "boards": boards,
        "tasks": tasks,
        "edges": edges,
        "claims": claims,
    }
    head_seq = int(head["seq"]) if head else 0
    head_hash = str(head["hash"]) if head else store.ZERO_HASH
    digest = hashlib.sha256(
        _canonical({"registry": registry, "head": [head_seq, head_hash]})).hexdigest()
    return Snapshot(
        db_path=str(path),
        as_of=_iso(now or datetime.now(timezone.utc)),
        registry=registry,
        events=events,
        head_seq=head_seq,
        head_hash=head_hash,
        digest=digest,
        meta=meta,
    )


def _default_db() -> Path:
    from .paths import default_db

    return default_db()
