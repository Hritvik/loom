"""The loopback HTTP shell.

Three boundaries are enforced here rather than documented and hoped for:

* **Loopback only.** The server refuses to bind anything that is not a loopback address, and every
  request must address it by a loopback ``Host``. A LAN IP or a reverse proxy is a different
  security design, not a flag.
* **Read-only.** Only ``GET`` and ``HEAD`` exist. There is no dispatch, transition, tick or deploy
  route to forget to guard. Every other method is answered ``405`` with no body processing.
* **Fail closed.** If the version gate is closed or the registry cannot be read, the response is
  ``503`` and explains why. It never serves a partially trusted page, and an unexpected error is
  reported as an unavailable page rather than a traceback or a half-rendered view.

No subprocess is invoked with a shell. Each page is produced from one snapshot.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import engine

from . import control, graph, graph_page, model, operations, pages, provenance, templates
from .paths import default_db
from .snapshot import SnapshotUnavailable, read
from .version import EngineVersion, VersionGateClosed, inspect

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
SAFE_METHODS = {"GET", "HEAD"}
ALLOWED_METHODS = "GET, HEAD"
CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'")
MAX_FILTER_LENGTH = 80


class ReadModel:
    """Builds pages and payloads from one snapshot plus independently sampled sources."""

    def __init__(self, db_path: Path | None = None,
                 owners: frozenset[str] | None = None):
        self.db_path = Path(db_path).expanduser() if db_path else default_db()
        self.owners = frozenset(owners or engine.HUMAN_IDENTITIES)

    def version(self) -> EngineVersion:
        return inspect()

    def read(self) -> tuple:
        """One snapshot and its read model; the caller shares both with every view."""
        snapshot = read(self.db_path)
        return snapshot, model.build(snapshot)

    # -- payloads -----------------------------------------------------------

    def snapshot_payload(self, version: EngineVersion) -> dict:
        if not version.ok:
            raise VersionGateClosed(version)
        snapshot, view = self.read()
        signals = provenance.collect(snapshot, view, version)
        return {
            "version": version.to_dict(),
            "snapshot": view.to_dict(),
            "provenance": [signal.to_dict() for signal in signals],
            "read_only": True,
        }


class VersionCache:
    """The release digest walk is not free; re-inspect at most once per interval."""

    def __init__(self, ttl: float = 60.0):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._value: EngineVersion | None = None
        self._at = 0.0

    def get(self) -> EngineVersion:
        with self._lock:
            now = time.monotonic()
            if self._value is None or now - self._at >= self.ttl:
                self._value = inspect()
                self._at = now
            return self._value


class RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "loom-ui"
    sys_version = ""

    @property
    def app(self) -> "Application":
        return self.server.app  # type: ignore[attr-defined]

    def log_request(self, code="-", size="-") -> None:
        # Path only: never the query string, which can carry a task or program chosen by a caller.
        self.log_message('"%s %s" %s %s', self.command, urlsplit(self.path).path, code, size)

    # -- request entry points -------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._origin_ok():
            return
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802
        if not self._origin_ok():
            return
        self._dispatch(body=False)

    def _unsupported(self) -> None:
        self.close_connection = True
        self._json(405, {
            "error": "read_only",
            "message": ("This UI has no mutation endpoint. Task actions belong to the loom "
                        "front door; browser-supplied actor values are never accepted."),
            "allow": ALLOWED_METHODS,
        })

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _unsupported

    # -- routing --------------------------------------------------------------

    def _dispatch(self, body: bool = True) -> None:
        if not self._host_ok():
            return
        split = urlsplit(self.path)
        path = split.path.rstrip("/") or "/"
        query = parse_qs(split.query, keep_blank_values=True)

        if path == "/":
            return self._index(body)
        if path == "/graph":
            return self._graph_page(query, body)
        if path == "/operations":
            return self._operations_page(body)
        if path == "/tasks":
            return self._tasks_page(query, body)
        if path.startswith("/task/"):
            return self._task_page(unquote(path[len("/task/"):]), body)
        if path == "/api/health":
            return self._health(body)
        if path == "/api/version":
            return self._version(body)
        if path == "/api/provenance":
            return self._provenance(body)
        if path == "/api/operations":
            return self._operations(body)
        if path == "/api/boards":
            return self._boards(body)
        if path == "/api/control":
            return self._control(query, body)
        if path == "/api/graph":
            return self._graph(query, body)
        if path == "/api/tasks":
            return self._tasks(body)
        if path.startswith("/api/tasks/"):
            task_id = unquote(path[len("/api/tasks/"):])
            return self._task(task_id, body)
        if path == "/api/snapshot":
            return self._snapshot(body)
        return self._json(404, {"error": "not_found", "message": f"no route for {path}"},
                          body=body)

    # -- HTML pages -----------------------------------------------------------

    def _index(self, body: bool) -> None:
        program = self._program()
        loaded = self._load()
        if loaded is None:
            return
        version, snapshot, view = loaded
        try:
            room = control.build(snapshot, view, self.app.model.owners, program)
            signals = provenance.collect(snapshot, view, version)
            page = pages.control_page(room, view, version, signals, _now())
        except Exception:  # a render bug must not become a traceback or a half page
            return self._crash(body)
        self._html(page, body)

    def _tasks_page(self, query: dict, body: bool) -> None:
        program = self._program(query)
        loaded = self._load()
        if loaded is None:
            return
        version, snapshot, view = loaded
        try:
            room = control.build(snapshot, view, self.app.model.owners, program)
            signals = provenance.collect(snapshot, view, version)
            page = pages.tasks_page(room, view, version, signals, _now())
        except Exception:
            return self._crash(body)
        self._html(page, body)

    def _task_page(self, task_id: str, body: bool) -> None:
        if not task_id or "/" not in task_id or ".." in task_id:
            page = templates.missing("That is not a task id.",
                                     "A task id looks like <program>/<id>.")
            return self._send(404, "text/html; charset=utf-8", page.encode("utf-8"), body)
        loaded = self._load()
        if loaded is None:
            return
        version, snapshot, view = loaded
        try:
            detail = control.detail(snapshot, view, task_id, self.app.model.owners)
            if detail is None:
                page = templates.missing(f"No task {task_id}.",
                                         "It may have been renamed; search All tasks instead.")
                return self._send(404, "text/html; charset=utf-8", page.encode("utf-8"), body)
            signals = provenance.collect(snapshot, view, version)
            page = pages.task_page(detail, view, version, signals, _now())
        except Exception:
            return self._crash(body)
        self._html(page, body)

    def _graph_page(self, query: dict, body: bool) -> None:
        loaded = self._load(body=body)
        if loaded is None:
            return
        version, snapshot, _view = loaded
        try:
            view = self._graph_view(snapshot, query)
            page = graph_page.page(view, version)
        except Exception:
            return self._crash(body)
        status = 404 if view.mode == "missing" else 200
        self._send(status, "text/html; charset=utf-8", page.encode("utf-8"), body)

    # -- JSON handlers --------------------------------------------------------

    def _operations_page(self, body: bool) -> None:
        loaded = self._load(body=body)
        if loaded is None:
            return
        version, snapshot, _view = loaded
        try:
            ops = operations.collect(snapshot, version)
            page = templates.operations_page(ops, version)
        except Exception:
            return self._crash(body)
        self._html(page, body)

    def _operations(self, body: bool) -> None:
        loaded = self._load(json_response=True, body=body)
        if loaded is None:
            return
        version, snapshot, _view = loaded
        try:
            ops = operations.collect(snapshot, version)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            return self._json(500, {"error": "internal_error",
                                    "message": "the operations view could not be built"},
                              body=body)
        self._json(200, {"version": version.to_dict(), "operations": ops.to_dict(),
                         "read_only": True}, body=body)

    def _health(self, body: bool) -> None:
        version = self.app.versions.get()
        payload = {"ok": version.ok, "read_only": True, "version": version.to_dict()}
        self._json(200 if version.ok else 503, payload, body=body)

    def _version(self, body: bool) -> None:
        self._json(200, self.app.versions.get().to_dict(), body=body)

    def _provenance(self, body: bool) -> None:
        loaded = self._load(json_response=True, body=body)
        if loaded is None:
            return
        version, snapshot, view = loaded
        signals = provenance.collect(snapshot, view, version)
        self._json(200, {"provenance": [signal.to_dict() for signal in signals],
                         "snapshot": snapshot.as_of}, body=body)

    def _boards(self, body: bool) -> None:
        loaded = self._load(json_response=True, body=body)
        if loaded is None:
            return
        _version, snapshot, view = loaded
        self._json(200, {"boards": view.to_dict()["boards"], "as_of": snapshot.as_of},
                   body=body)

    def _tasks(self, body: bool) -> None:
        loaded = self._load(json_response=True, body=body)
        if loaded is None:
            return
        _version, snapshot, view = loaded
        self._json(200, {"tasks": view.to_dict()["tasks"], "as_of": snapshot.as_of}, body=body)

    def _task(self, task_id: str, body: bool) -> None:
        loaded = self._load(json_response=True, body=body)
        if loaded is None:
            return
        _version, snapshot, view = loaded
        found = model.find(view, task_id)
        if found is None:
            return self._json(404, {"error": "unknown_task", "message": f"no task {task_id}"},
                              body=body)
        detail = control.detail(snapshot, view, task_id, self.app.model.owners)
        self._json(200, {
            "task": _task_payload(view, found.id),
            "detail": detail.to_dict(),
            "as_of": snapshot.as_of,
        }, body=body)

    def _control(self, query: dict, body: bool) -> None:
        program = self._program(query)
        loaded = self._load(json_response=True, body=body)
        if loaded is None:
            return
        _version, snapshot, view = loaded
        room = control.build(snapshot, view, self.app.model.owners, program)
        self._json(200, {"control": room.to_dict(), "as_of": snapshot.as_of}, body=body)

    def _graph(self, query: dict, body: bool) -> None:
        loaded = self._load(json_response=True, body=body)
        if loaded is None:
            return
        _version, snapshot, _view = loaded
        try:
            view = self._graph_view(snapshot, query)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            return self._json(500, {"error": "internal_error",
                                    "message": "the graph could not be built"}, body=body)
        if view.mode == "missing":
            return self._json(404, {"error": "unknown_scope", "message": view.message,
                                    "as_of": view.as_of}, body=body)
        self._json(200, {"graph": view.to_dict(), "as_of": view.as_of, "read_only": True},
                   body=body)

    def _snapshot(self, body: bool) -> None:
        version = self.app.versions.get()
        try:
            payload = self.app.model.snapshot_payload(version)
        except VersionGateClosed as exc:
            return self._json(503, {"error": "version_gate_closed", "version": exc.status.to_dict(),
                                    "message": str(exc)}, body=body)
        except SnapshotUnavailable as exc:
            return self._json(503, {"error": "registry_unavailable", "message": str(exc)},
                              body=body)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            return self._json(500, {"error": "internal_error",
                                    "message": "the read model could not be built"}, body=body)
        self._json(200, payload, body=body)

    # -- helpers --------------------------------------------------------------

    def _program(self, query: dict | None = None) -> str | None:
        parsed = query if query is not None else parse_qs(urlsplit(self.path).query)
        values = parsed.get("program")
        if not values:
            return None
        value = str(values[0]).strip()
        if not value or len(value) > MAX_FILTER_LENGTH:
            return None
        return value

    def _load(self, json_response: bool = False, body: bool = True):
        """The version gate and one read, or the right failure response and ``None``."""
        try:
            version = self.app.versions.get()
            if not version.ok:
                raise VersionGateClosed(version)
            snapshot, view = self.app.model.read()
            return version, snapshot, view
        except VersionGateClosed as exc:
            if json_response:
                self._json(503, {"error": "version_gate_closed",
                                 "version": exc.status.to_dict(), "message": str(exc)},
                           body=body)
            else:
                page = templates.closed(exc.status, str(exc), 503)
                self._send(503, "text/html; charset=utf-8", page.encode("utf-8"), body)
            return None
        except SnapshotUnavailable as exc:
            if json_response:
                self._json(503, {"error": "registry_unavailable", "message": str(exc)}, body=body)
            else:
                page = templates.unavailable(str(exc), 503)
                self._send(503, "text/html; charset=utf-8", page.encode("utf-8"), body)
            return None
        except Exception:
            traceback.print_exc(file=sys.stderr)
            if json_response:
                self._json(500, {"error": "internal_error",
                                 "message": "the read model could not be built"}, body=body)
            else:
                self._crash(body)
            return None

    def _crash(self, body: bool) -> None:
        traceback.print_exc(file=sys.stderr)
        page = templates.unavailable(
            "The page could not be rendered. The failure was logged to the server's stderr; "
            "no partial view is shown.", 500, title="Loom UI could not render this page")
        self._send(500, "text/html; charset=utf-8", page.encode("utf-8"), body)

    def _html(self, page: str, body: bool) -> None:
        self._send(200, "text/html; charset=utf-8", page.encode("utf-8"), body)

    def _graph_view(self, snapshot, query: dict):
        """One graph view from an already-read snapshot; a bad scope is data, not an exception."""
        program = _param(query, "program")
        focus = _param(query, "task") or _param(query, "focus")
        try:
            return graph.build(snapshot, program=program, focus=focus)
        except graph.GraphScopeError as exc:
            return graph.missing(snapshot, str(exc))

    def _host_ok(self) -> bool:
        header = (self.headers.get("Host") or "").strip().lower()
        if not header:
            self._json(421, {"error": "host_required",
                             "message": "requests must address a loopback host"})
            return False
        name, _, port = header.partition(":")
        if header.startswith("["):
            name, _, rest = header[1:].partition("]")
            port = rest[1:] if rest.startswith(":") else ""
        if name not in LOOPBACK_HOSTS:
            self._json(421, {"error": "host_not_loopback",
                             "message": f"Host {header!r} is not a loopback address"})
            return False
        if port and port.isdigit() and int(port) != self.server.server_port:
            self._json(421, {"error": "host_port_mismatch",
                             "message": "Host port does not match the listener"})
            return False
        return True

    def _origin_ok(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        port = self.server.server_port
        allowed = {f"http://{name}:{port}" for name in LOOPBACK_HOSTS}
        allowed |= {f"https://{name}:{port}" for name in LOOPBACK_HOSTS}
        if origin.rstrip("/").lower() in allowed:
            return True
        self._json(403, {"error": "origin_rejected",
                         "message": "cross-origin requests are not accepted"})
        return False

    def _json(self, status: int, payload: dict, body: bool = True) -> None:
        raw = json.dumps(payload, indent=2, sort_keys=False, allow_nan=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", raw, body)

    def _send(self, status: int, content_type: str, raw: bytes, body: bool = True) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CSP)
        if status == 405:
            self.send_header("Allow", ALLOWED_METHODS)
        if status >= 400:
            self.close_connection = True
        self.end_headers()
        if body:
            self.wfile.write(raw)


def _task_payload(view: model.RegistryView, task_id: str) -> dict:
    """The same task shape ``/api/snapshot`` publishes, for one task."""
    return next(item for item in view.to_dict()["tasks"] if item["id"] == task_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Application:
    def __init__(self, db_path: Path | None = None, version_ttl: float = 60.0,
                 owners: frozenset[str] | None = None):
        self.model = ReadModel(db_path, owners)
        self.versions = VersionCache(version_ttl)

    def build(self) -> dict:
        """One read model, using the cached gate decision."""
        return self.model.snapshot_payload(self.versions.get())


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, app: Application):
        self.app = app
        super().__init__(address, RequestHandler)


def _param(query: dict, name: str) -> str | None:
    """One query parameter, stripped, or ``None``. Repeated values use the first."""
    values = query.get(name) or []
    value = str(values[0]).strip() if values else ""
    return value or None


def _loopback(address: str) -> str:
    value = address.strip().lower()
    if value in {"localhost", "127.0.0.1"}:
        return "127.0.0.1"
    if value in {"::1", "[::1]"}:
        return "::1"
    raise ValueError(
        f"refusing to bind {address!r}: this UI is loopback-only by design, and a LAN or proxy "
        "deployment needs a separate authentication and transport design")


def make_server(host: str = "127.0.0.1", port: int = 0, db_path: Path | None = None,
                version_ttl: float = 60.0, owners: frozenset[str] | None = None) -> Server:
    return Server((_loopback(host), int(port)), Application(db_path, version_ttl, owners))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="loom-ui",
        description="Read-only local view of the Loom work registry (loopback only).")
    parser.add_argument("--host", default="127.0.0.1", help="loopback address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8788, help="TCP port (0 picks a free port)")
    parser.add_argument("--db", default=None, help="registry database (default: $WORK_DB or "
                                                   "~/.local/state/loom/work.db)")
    parser.add_argument("--version-ttl", type=float, default=60.0,
                        help="seconds to cache the version gate (default 60)")
    parser.add_argument("--owner", action="append", default=None, metavar="IDENTITY",
                        help="extra identity treated as the owner for queue routing; repeatable "
                             "(the engine's human identities are always included)")
    args = parser.parse_args(argv)

    owners = frozenset(engine.HUMAN_IDENTITIES) | frozenset(args.owner or ())
    try:
        server = make_server(args.host, args.port, args.db, args.version_ttl, owners)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    host, port = server.server_address[:2]
    print(f"loom ui listening on http://{host}:{port} (read-only, loopback only)")
    print(f"owner identities: {', '.join(sorted(owners))}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
