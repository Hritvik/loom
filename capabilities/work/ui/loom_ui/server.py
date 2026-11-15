"""The loopback HTTP shell.

Three boundaries are enforced here rather than documented and hoped for:

* **Loopback only.** The server refuses to bind anything that is not a loopback address, and every
  request must address it by a loopback ``Host``. A LAN IP or a reverse proxy is a different
  security design, not a flag.
* **Read-only.** Only ``GET`` and ``HEAD`` exist. There is no dispatch, transition, tick or deploy
  route to forget to guard. Every other method is answered ``405`` with no body processing.
* **Fail closed.** If the version gate is closed or the registry cannot be read, the response is
  ``503`` and explains why. It never serves a partially trusted page.

No subprocess is invoked with a shell. The read model is produced from one snapshot.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import model, provenance, templates
from .paths import default_db
from .snapshot import SnapshotUnavailable, read
from .version import EngineVersion, VersionGateClosed, inspect

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
SAFE_METHODS = {"GET", "HEAD"}
ALLOWED_METHODS = "GET, HEAD"
CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'")


class ReadModel:
    """Builds the page payload from one snapshot plus independently sampled sources."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path).expanduser() if db_path else default_db()

    def version(self) -> EngineVersion:
        return inspect()

    def build(self, version: EngineVersion | None = None) -> dict:
        version = version or self.version()
        if not version.ok:
            raise VersionGateClosed(version)
        snapshot = read(self.db_path)
        view = model.build(snapshot)
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

    def log_message(self, fmt: str, *args) -> None:
        # Path only: never the query string, which could carry a task id chosen by a caller.
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

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
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/":
            return self._index(body)
        if path == "/api/health":
            return self._health(body)
        if path == "/api/version":
            return self._version(body)
        if path == "/api/provenance":
            return self._provenance(body)
        if path == "/api/boards":
            return self._boards(body)
        if path == "/api/tasks":
            return self._tasks(body)
        if path.startswith("/api/tasks/"):
            task_id = path[len("/api/tasks/"):]
            return self._task(task_id, body)
        if path == "/api/snapshot":
            return self._snapshot(body)
        return self._json(404, {"error": "not_found", "message": f"no route for {path}"}, body=body)

    # -- handlers -------------------------------------------------------------

    def _index(self, body: bool) -> None:
        try:
            version = self.app.versions.get()
            if not version.ok:
                raise VersionGateClosed(version)
            snapshot = read(self.app.model.db_path)
            view = model.build(snapshot)
            signals = provenance.collect(snapshot, view, version)
        except VersionGateClosed as exc:
            return self._closed(exc.status, str(exc), body)
        except SnapshotUnavailable as exc:
            return self._closed(None, str(exc), body)
        html = templates.page(view, version, signals)
        self._send(200, "text/html; charset=utf-8", html.encode("utf-8"), body)

    def _health(self, body: bool) -> None:
        version = self.app.versions.get()
        payload = {"ok": version.ok, "read_only": True, "version": version.to_dict()}
        self._json(200 if version.ok else 503, payload, body=body)

    def _version(self, body: bool) -> None:
        self._json(200, self.app.versions.get().to_dict(), body=body)

    def _provenance(self, body: bool) -> None:
        try:
            payload = self.app.build()
        except VersionGateClosed as exc:
            return self._json(503, {"error": "version_gate_closed", "version": exc.status.to_dict(),
                                    "message": str(exc)}, body=body)
        except SnapshotUnavailable as exc:
            return self._json(503, {"error": "registry_unavailable", "message": str(exc)},
                              body=body)
        self._json(200, {"provenance": payload["provenance"],
                         "snapshot": payload["snapshot"]["as_of"]}, body=body)

    def _boards(self, body: bool) -> None:
        payload = self._read_model()
        if payload is None:
            return
        self._json(200, {"boards": payload["snapshot"]["boards"],
                         "as_of": payload["snapshot"]["as_of"]}, body=body)

    def _tasks(self, body: bool) -> None:
        payload = self._read_model()
        if payload is None:
            return
        self._json(200, {"tasks": payload["snapshot"]["tasks"],
                         "as_of": payload["snapshot"]["as_of"]}, body=body)

    def _task(self, task_id: str, body: bool) -> None:
        payload = self._read_model()
        if payload is None:
            return
        found = next((task for task in payload["snapshot"]["tasks"] if task["id"] == task_id), None)
        if found is None:
            return self._json(404, {"error": "unknown_task", "message": f"no task {task_id}"},
                              body=body)
        self._json(200, {"task": found, "as_of": payload["snapshot"]["as_of"]}, body=body)

    def _snapshot(self, body: bool) -> None:
        payload = self._read_model()
        if payload is None:
            return
        self._json(200, payload, body=body)

    # -- helpers --------------------------------------------------------------

    def _read_model(self) -> dict | None:
        try:
            return self.app.build()
        except VersionGateClosed as exc:
            self._json(503, {"error": "version_gate_closed", "version": exc.status.to_dict(),
                             "message": str(exc)})
        except SnapshotUnavailable as exc:
            self._json(503, {"error": "registry_unavailable", "message": str(exc)})
        return None

    def _closed(self, version: EngineVersion | None, reason: str, body: bool) -> None:
        page = templates.closed(version, reason, 503)
        self._send(503, "text/html; charset=utf-8", page.encode("utf-8"), body)

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


class Application:
    def __init__(self, db_path: Path | None = None, version_ttl: float = 60.0):
        self.model = ReadModel(db_path)
        self.versions = VersionCache(version_ttl)

    def build(self) -> dict:
        """One read model, using the cached gate decision."""
        return self.model.build(self.versions.get())


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, app: Application):
        self.app = app
        super().__init__(address, RequestHandler)


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
                version_ttl: float = 60.0) -> Server:
    return Server((_loopback(host), int(port)), Application(db_path, version_ttl))


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
    args = parser.parse_args(argv)

    try:
        server = make_server(args.host, args.port, args.db, args.version_ttl)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    host, port = server.server_address[:2]
    print(f"loom ui listening on http://{host}:{port} (read-only, loopback only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
