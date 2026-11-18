"""The loopback HTTP shell: read pages, guarded task actions, and fail-closed errors.

Four boundaries are enforced here rather than documented and hoped for:

* **Loopback only.** The server refuses to bind anything that is not a loopback address, and every
  request must address it by a loopback ``Host``. A LAN IP or a reverse proxy is a different
  security design, not a flag.
* **A named actor, never a supplied one.** A mutation needs a session that the server paired from a
  startup token. The identity comes from that session; a body field that tries to name an actor is
  rejected (:mod:`loom_ui.actions`). Nobody is paired unless someone configured them at startup, so
  an unconfigured server is read-only by construction rather than by a flag being remembered.
* **Loom is the only writer.** Every action runs the supported ``loom`` front door as a subprocess
  with an argument array, as the paired identity, with the revision and epoch the page showed. The
  engine owns compare-and-swap, leases, fencing, dependency gates and the event log. A refusal is
  reported; nothing is retried. There is no route for ``tick``, worker dispatch, the scheduler or a
  deployment.
* **Fail closed.** If the version gate is closed or the registry cannot be read, the response is
  ``503`` and explains why. An unexpected error is reported as an unavailable page rather than a
  traceback or a half-rendered view.

No subprocess is invoked with a shell. Each read page is produced from one snapshot.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, unquote, urlsplit

import engine

from . import (action_pages, actions, control, graph, graph_page, model, operations,
               pages, provenance, templates)
from .identity import Policy, PolicyError
from .paths import default_db, FRONT_DOOR
from .session import COOKIE_NAME, SessionStore, parse_cookie
from .snapshot import SnapshotUnavailable, read
from .version import EngineVersion, VersionGateClosed, inspect

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
READ_METHODS = "GET, HEAD"
ACTION_METHODS = "GET, HEAD, POST"
CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
MAX_FILTER_LENGTH = 80
MAX_BODY_BYTES = 16 * 1024
ACTION_PATH = re.compile(r"^/task/(?P<task>.+)/action/(?P<action>[a-z][a-z-]*)$")
RESULT_STATUS = {"success": 200, "conflict": 409, "denied": 422, "failed": 502,
                 "timeout": 504}


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


class Application:
    """One read model, one identity policy, one session store, one front door."""

    def __init__(self, db_path: Path | None = None, version_ttl: float = 60.0,
                 owners: frozenset[str] | None = None, *, policy: Policy | None = None,
                 front_door: Path | str | None = None,
                 session_ttl: float = 8 * 3600, session_idle: float = 3600,
                 session_secret: bytes | None = None, runner=None,
                 action_timeout: float = actions.DEFAULT_TIMEOUT,
                 clock=time.monotonic):
        self.policy = policy or Policy(())
        owners = frozenset(owners or engine.HUMAN_IDENTITIES) | self.policy.identities()
        self.model = ReadModel(db_path, owners)
        self.owners = self.model.owners
        self.versions = VersionCache(version_ttl)
        self.sessions = SessionStore(self.policy, session_secret, session_ttl, session_idle,
                                     clock=clock)
        self.front_door = Path(front_door or FRONT_DOOR)
        self.runner = runner
        self.action_timeout = action_timeout

    # -- reads ---------------------------------------------------------------

    def snapshot_payload(self, version: EngineVersion) -> dict:
        if not version.ok:
            raise VersionGateClosed(version)
        snapshot, view = self.read()
        signals = provenance.collect(snapshot, view, version)
        return {
            "version": version.to_dict(),
            "snapshot": view.to_dict(),
            "provenance": [signal.to_dict() for signal in signals],
            "actions": self.capability(),
        }

    def read(self) -> tuple:
        return self.model.read()

    def detail(self, snapshot, view, task_id: str):
        return control.detail(snapshot, view, task_id, self.owners)

    def catalogue(self, detail, identity: str | None):
        return actions.catalogue(detail, self.policy, identity, _now())

    def capability(self) -> dict:
        return {"enabled": self.policy.configured,
                "humans": [human.to_dict() for human in self.policy.humans()]}


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

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return
        self._dispatch_post()

    def _unsupported(self) -> None:
        self.close_connection = True
        # The method set belongs to the route that was addressed, not to the server: only an
        # action route accepts a POST, and saying otherwise would misdescribe every read page.
        path = urlsplit(self.path).path.rstrip("/") or "/"
        allow = ACTION_METHODS if ACTION_PATH.match(path) else READ_METHODS
        self._json(405, {
            "error": "method_not_allowed",
            "message": ("This UI routes GET, HEAD and — for a confirmed task action — POST. "
                        "No other method exists."),
            "allow": allow,
        }, allow=allow)

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _unsupported

    # -- dispatch -------------------------------------------------------------

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
        if path == "/pair":
            return self._pair(query, body)
        if path == "/tasks":
            return self._tasks_page(query, body)
        if path == "/api/health":
            return self._health(body)
        if path == "/api/session":
            return self._session(body)
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
            return self._task_route(path[len("/api/tasks/"):], body)
        if path == "/api/snapshot":
            return self._snapshot(body)
        action = ACTION_PATH.match(path)
        if action:
            return self._confirm(unquote(action.group("task")), action.group("action"),
                                 query, body)
        if path.startswith("/task/"):
            return self._task_page(unquote(path[len("/task/"):]), body)
        return self._json(404, {"error": "not_found", "message": f"no route for {path}"},
                          body=body)

    def _dispatch_post(self) -> None:
        split = urlsplit(self.path)
        path = split.path.rstrip("/") or "/"
        action = ACTION_PATH.match(path)
        if not action:
            return self._not_allowed(READ_METHODS, "this route does not accept a POST")
        if not self._mutation_origin_ok():
            return
        return self._perform(unquote(action.group("task")), action.group("action"))

    # -- HTML pages -----------------------------------------------------------

    def _index(self, body: bool) -> None:
        program = self._program()
        loaded = self._load()
        if loaded is None:
            return
        version, snapshot, view = loaded
        try:
            room = control.build(snapshot, view, self.app.owners, program)
            signals = provenance.collect(snapshot, view, version)
            page = pages.control_page(room, view, version, signals, _now(),
                                      actions=self.app.policy.configured)
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
            room = control.build(snapshot, view, self.app.owners, program)
            signals = provenance.collect(snapshot, view, version)
            page = pages.tasks_page(room, view, version, signals, _now(),
                                    actions=self.app.policy.configured)
        except Exception:
            return self._crash(body)
        self._html(page, body)

    def _task_page(self, task_id: str, body: bool) -> None:
        if not self._task_id_ok(task_id):
            page = templates.missing("That is not a task id.",
                                     "A task id looks like <program>/<id>.")
            return self._send(404, "text/html; charset=utf-8", page.encode("utf-8"), body)
        loaded = self._load()
        if loaded is None:
            return
        version, snapshot, view = loaded
        try:
            detail = self.app.detail(snapshot, view, task_id)
            if detail is None:
                page = templates.missing(f"No task {task_id}.",
                                         "It may have been renamed; search All tasks instead.")
                return self._send(404, "text/html; charset=utf-8", page.encode("utf-8"), body)
            session = self._session_of_request()
            panel = self.app.catalogue(detail, session.identity if session else None)
            signals = provenance.collect(snapshot, view, version)
            page = pages.task_page(detail, view, version, signals, _now(), panel=panel)
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
            page = graph_page.page(view, version, actions=self.app.policy.configured)
        except Exception:
            return self._crash(body)
        status = 404 if view.mode == "missing" else 200
        self._send(status, "text/html; charset=utf-8", page.encode("utf-8"), body)

    # -- pairing and session ---------------------------------------------------

    def _pair(self, query: dict, body: bool) -> None:
        token = (query.get("token") or [""])[0]
        session = self.app.sessions.pair(token)
        if session is None:
            page = action_pages.notice(
                "Pairing refused", 403,
                "That pairing token is not one this server issued. Tokens are printed when the "
                "server starts and are replaced when it restarts.",
                links=(("/", "Focus"),))
            return self._send(403, "text/html; charset=utf-8", page.encode("utf-8"), body)
        self._send(303, "text/html; charset=utf-8",
                   b"<!doctype html><title>Paired</title><p>Session opened. "
                   b'<a href="/">Continue</a>.</p>', body,
                   extra_headers=[("Location", "/"),
                                  ("Set-Cookie", self._cookie_header(session))])

    def _session(self, body: bool) -> None:
        session = self._session_of_request()
        payload = {
            "authenticated": session is not None,
            "identity": session.identity if session else None,
            "roles": (sorted(self.app.policy.get(session.identity).roles)
                      if session and self.app.policy.get(session.identity) else []),
            "actions_enabled": self.app.policy.configured,
            "pairing": "present a token from the server's startup notice at /pair",
        }
        self._json(200, payload, body=body)

    # -- JSON handlers --------------------------------------------------------

    def _operations_page(self, body: bool) -> None:
        loaded = self._load(body=body)
        if loaded is None:
            return
        version, snapshot, _view = loaded
        try:
            ops = operations.collect(snapshot, version)
            page = templates.operations_page(ops, version, actions=self.app.policy.configured)
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
        payload = {"ok": version.ok, "actions": self.app.capability(),
                   "version": version.to_dict()}
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

    def _task_route(self, rest: str, body: bool) -> None:
        """``/api/tasks/<id>`` or ``/api/tasks/<id>/actions``, with the exact id winning.

        A legal task id may itself end in ``/actions`` (``demo/actions``), so the suffix route
        must not claim the path before an existing task of that exact name does. One read
        settles both, and the panel stays reachable for such a task at ``<id>/actions``.
        """
        loaded = self._load(json_response=True, body=body)
        if loaded is None:
            return
        _version, snapshot, view = loaded
        exact = unquote(rest)
        found = model.find(view, exact)
        if found is not None:
            detail = self.app.detail(snapshot, view, found.id)
            return self._json(200, {
                "task": _task_payload(view, found.id),
                "detail": detail.to_dict(),
                "as_of": snapshot.as_of,
            }, body=body)
        suffix = "/actions"
        if rest.endswith(suffix):
            return self._task_actions(unquote(rest[:-len(suffix)]), snapshot, view, body)
        return self._json(404, {"error": "unknown_task", "message": f"no task {exact}"},
                          body=body)

    def _task_actions(self, task_id: str, snapshot, view, body: bool) -> None:
        detail = self.app.detail(snapshot, view, task_id)
        if detail is None:
            return self._json(404, {"error": "unknown_task", "message": f"no task {task_id}"},
                              body=body)
        session = self._session_of_request()
        panel = self.app.catalogue(detail, session.identity if session else None)
        self._json(200, {"task": task_id, "as_of": snapshot.as_of, "panel": panel.to_dict()},
                   body=body)

    def _control(self, query: dict, body: bool) -> None:
        program = self._program(query)
        loaded = self._load(json_response=True, body=body)
        if loaded is None:
            return
        _version, snapshot, view = loaded
        room = control.build(snapshot, view, self.app.owners, program)
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
            payload = self.app.snapshot_payload(version)
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

    # -- the confirmation page ------------------------------------------------

    def _confirm(self, task_id: str, action_id: str, query: dict, body: bool) -> None:
        if not self._task_id_ok(task_id):
            page = templates.missing("That is not a task id.",
                                     "A task id looks like <program>/<id>.")
            return self._send(404, "text/html; charset=utf-8", page.encode("utf-8"), body)
        if action_id not in actions.ENGINE_VERBS:
            page = action_pages.notice("No such action", 404,
                                       f"{action_id!r} is not an action this UI offers.",
                                       links=((f"/task/{task_id}", "Back to the task"),))
            return self._send(404, "text/html; charset=utf-8", page.encode("utf-8"), body)
        if not self.app.policy.configured:
            page = action_pages.notice(
                "Actions are disabled", 403,
                "This server has no configured human identity, so it cannot act on a task. "
                "Start it with --human identity:roles (or an identity configuration file).",
                links=((f"/task/{task_id}", "Back to the task"), ("/", "Focus")))
            return self._send(403, "text/html; charset=utf-8", page.encode("utf-8"), body)
        session = self._session_of_request()
        if session is None:
            page = action_pages.notice(
                "Not signed in", 401,
                "A task action needs a paired session. Open the pairing link this server printed "
                "when it started; the token names the identity, so the browser never chooses it.",
                links=((f"/task/{task_id}", "Back to the task"), ("/", "Focus")))
            return self._send(401, "text/html; charset=utf-8", page.encode("utf-8"), body)

        loaded = self._load()
        if loaded is None:
            return
        version, snapshot, view = loaded
        try:
            detail = self.app.detail(snapshot, view, task_id)
            if detail is None:
                page = templates.missing(f"No task {task_id}.",
                                         "It may have been renamed; search All tasks instead.")
                return self._send(404, "text/html; charset=utf-8", page.encode("utf-8"), body)
            panel = self.app.catalogue(detail, session.identity)
            target = _first(query, "to")
            question = _first(query, "question")
            option = actions.find_option(panel, action_id, target, question)
            if option is None:
                page = action_pages.notice(
                    "That action does not apply", 404,
                    f"{action_id!r} is not offered for {task_id} in its current state.",
                    links=((f"/task/{task_id}", "Back to the task"),))
                return self._send(404, "text/html; charset=utf-8", page.encode("utf-8"), body)
            if not option.available:
                page = action_pages.not_available_page(detail, option, panel, version)
                return self._send(409, "text/html; charset=utf-8", page.encode("utf-8"), body)
            csrf = session.csrf
            form = self.app.sessions.issue_form(session)
            page = action_pages.confirm_page(detail, option, panel, version, _now(), csrf, form)
        except Exception:
            return self._crash(body)
        self._html(page, body)

    # -- the action POST ------------------------------------------------------

    def _perform(self, task_id: str, action_id: str) -> None:
        form = self._read_form()
        if form is None:
            return
        if not self._task_id_ok(task_id):
            return self._notice("That is not a task id", 400,
                                "A task id looks like <program>/<id>.")
        if action_id not in actions.ENGINE_VERBS:
            return self._notice("No such action", 404,
                                f"{action_id!r} is not an action this UI offers.")
        try:
            version = self.app.versions.get()
            if not version.ok:
                raise VersionGateClosed(version)
        except VersionGateClosed as exc:
            page = templates.closed(exc.status, str(exc), 503)
            return self._send(503, "text/html; charset=utf-8", page.encode("utf-8"))

        if not self.app.policy.configured:
            return self._notice("Actions are disabled", 403,
                                "This server has no configured human identity.", status_close=True)
        session = self._session_of_request()
        if session is None:
            return self._notice(
                "Not signed in", 401,
                "A task action needs a paired session. Open the pairing link this server printed "
                "when it started.")
        if not self.app.sessions.csrf_ok(session, form.get("csrf")):
            return self._notice("Request rejected", 403,
                                "The CSRF token does not match this session. Reload the task and "
                                "confirm again from the form the server rendered.")
        # A rendered confirmation form is single-use. Consuming it before anything else means a
        # reloaded or resubmitted form is refused unambiguously, whatever the task's state is now.
        if not self.app.sessions.consume_form(session, form.get("form")):
            return self._notice(
                "This form was already submitted", 409,
                "The one-shot form token has been spent, so nothing was executed again. Reload "
                "the task for its current state.",
                links=((f"/task/{task_id}", "Re-read the task"),))

        loaded = self._load()
        if loaded is None:
            return
        _version, snapshot, view = loaded
        try:
            detail = self.app.detail(snapshot, view, task_id)
            if detail is None:
                return self._notice("Unknown task", 404, f"No task {task_id} exists.")
            panel = self.app.catalogue(detail, session.identity)
            option = actions.find_option(panel, action_id, form.get("to"), form.get("question"))
            if option is None:
                return self._notice(
                    "That action does not apply", 409,
                    f"{action_id!r} is not offered for {task_id} in its current state. Nothing "
                    "was executed; reload the task for the actions that do apply.")
            if not option.available:
                page = action_pages.not_available_page(detail, option, panel,
                                                       self.app.versions.get())
                return self._send(409, "text/html; charset=utf-8", page.encode("utf-8"))
            presented = _presented_revision(form)
            if presented is None:
                return self._notice(
                    "The form was not accepted", 400,
                    "The revision being presented is missing or malformed. Nothing was executed.",
                    links=((self._action_url(task_id, option), "Back to the form"),))
            if presented != detail.task.revision:
                # The page and the registry disagree before anything ran. Say so; do not retry,
                # and do not judge the rest of a form that was written for older state.
                return self._notice(
                    "The task moved before your action ran", 409,
                    f"The form presented revision {presented}; the registry is at "
                    f"revision {detail.task.revision}. Nothing was executed.",
                    links=((f"/task/{task_id}", "Re-read the task"),))
            claim = detail.task.claim
            submission, problems = actions.validate(option, form,
                                                    claim.epoch if claim else None)
            if submission is None:
                return self._notice("The form was not accepted", 400,
                                    "Nothing was executed. Correct the problem and confirm again.",
                                    details=problems,
                                    links=((self._action_url(task_id, option), "Back to the form"),
                                           (f"/task/{task_id}", "Back to the task")))
        except Exception:
            return self._crash(True)

        try:
            command = actions.command_for(option, task_id, submission)
        except (KeyError, ValueError) as exc:  # a mapping bug must not become a dropped socket
            traceback.print_exc(file=sys.stderr)
            return self._notice(
                "The action could not be built", 400,
                "Nothing was executed. This is a defect in the UI, not a refusal by Loom.",
                details=(str(exc),),
                links=((f"/task/{task_id}", "Back to the task"),))
        run = actions.execute(self.app.front_door, self.app.model.db_path, session.identity,
                              command, timeout=self.app.action_timeout, runner=self.app.runner)

        revision_after = status_after = None
        event = None
        offered_again = False
        try:
            after_snapshot, after_view = self.app.read()
            after = self.app.detail(after_snapshot, after_view, task_id)
            if after is not None:
                revision_after = after.task.revision
                status_after = after.task.status
                later = actions.find_option(self.app.catalogue(after, session.identity),
                                            option.id, option.target, option.param("question"))
                offered_again = bool(later and later.available)
            event = actions.audit_event(after_snapshot.events, task_id,
                                        snapshot.head_seq, session.identity)
        except SnapshotUnavailable:
            event = None
        except Exception:
            traceback.print_exc(file=sys.stderr)

        outcome = action_pages.Outcome(
            task_id=task_id, option=option, actor=session.identity, run=run, event=event,
            revision_before=detail.task.revision, status_before=detail.task.status,
            revision_after=revision_after, status_after=status_after,
            offered_again=offered_again)
        try:
            page = action_pages.outcome_page(outcome, version, _now(), panel)
        except Exception:
            return self._crash(True)
        status = RESULT_STATUS.get(run.kind, 502)
        self._send(status, "text/html; charset=utf-8", page.encode("utf-8"))

    def _read_form(self) -> dict | None:
        encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in encoding:
            self._notice("Request rejected", 411,
                         "A chunked body is not accepted; send a form with a Content-Length.")
            return None
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            self._notice("Request rejected", 415,
                         "Task actions are submitted as a URL-encoded form from this UI's own "
                         "confirmation page.")
            return None
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "")
        except ValueError:
            self._notice("Request rejected", 411, "A Content-Length is required.")
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            self._notice("Request rejected", 413,
                         f"A task action body must be at most {MAX_BODY_BYTES} bytes.")
            return None
        raw = self.rfile.read(length)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self._notice("Request rejected", 400, "The form body is not valid UTF-8.")
            return None
        pairs = parse_qsl(text, keep_blank_values=True)
        form: dict[str, str] = {}
        for name, value in pairs:
            if name in form:
                self._notice("Request rejected", 400,
                             f"The field {name!r} was sent more than once.")
                return None
            form[name] = value
        return form

    # -- helpers --------------------------------------------------------------

    def _session_of_request(self):
        return self.app.sessions.open(parse_cookie(self.headers.get("Cookie")))

    def _cookie_header(self, session) -> str:
        return (f"{COOKIE_NAME}={self.app.sessions.cookie(session)}; Path=/; HttpOnly; "
                f"SameSite=Strict; Max-Age={int(self.app.sessions.ttl)}")

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
            snapshot, view = self.app.read()
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

    def _notice(self, title: str, status: int, message: str,
                details: tuple[str, ...] = (),
                links: tuple[tuple[str, str], ...] = (),
                status_close: bool = False) -> None:
        page = action_pages.notice(title, status, message, details, links)
        self._send(status, "text/html; charset=utf-8", page.encode("utf-8"),
                   close=status_close or status >= 400)

    def _not_allowed(self, allow: str, message: str) -> None:
        self.close_connection = True
        self._json(405, {"error": "method_not_allowed", "message": message, "allow": allow},
                   allow=allow)

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

    def _mutation_origin_ok(self) -> bool:
        """A POST must come from this exact origin. A missing Origin is refused, not assumed."""
        origin = (self.headers.get("Origin") or "").strip().rstrip("/").lower()
        host = (self.headers.get("Host") or "").strip().lower()
        if origin and origin == f"http://{host}":
            return True
        self._json(403, {"error": "origin_rejected",
                         "message": ("a task action must be submitted from this UI's own origin; "
                                     f"Origin {origin or '(missing)'!r} does not match Host "
                                     f"{host!r}")})
        return False

    def _task_id_ok(self, task_id: str) -> bool:
        return bool(task_id) and ".." not in task_id and bool(engine.ID_PATTERN.match(task_id))

    def _action_url(self, task_id: str, option) -> str:
        return action_pages.confirm_url(task_id, option)

    def _json(self, status: int, payload: dict, body: bool = True, allow: str | None = None) -> None:
        raw = json.dumps(payload, indent=2, sort_keys=False, allow_nan=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", raw, body, allow=allow)

    def _send(self, status: int, content_type: str, raw: bytes, body: bool = True,
              extra_headers: list[tuple[str, str]] | None = None,
              allow: str | None = None, close: bool | None = None) -> None:
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
        if allow:
            self.send_header("Allow", allow)
        for name, value in extra_headers or ():
            self.send_header(name, value)
        if close is None:
            close = status >= 400
        if close:
            self.close_connection = True
        self.end_headers()
        if body:
            self.wfile.write(raw)


def _presented_revision(form: dict) -> int | None:
    try:
        value = int(str(form.get("revision") or ""))
    except ValueError:
        return None
    return value if value >= 0 else None


def _first(query: dict, name: str) -> str | None:
    values = query.get(name)
    return str(values[0]) if values else None


def _task_payload(view: model.RegistryView, task_id: str) -> dict:
    """The same task shape ``/api/snapshot`` publishes, for one task."""
    return next(item for item in view.to_dict()["tasks"] if item["id"] == task_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
                version_ttl: float = 60.0, owners: frozenset[str] | None = None, *,
                policy: Policy | None = None, front_door: Path | str | None = None,
                session_ttl: float = 8 * 3600, session_idle: float = 3600,
                session_secret: bytes | None = None, runner=None,
                action_timeout: float = actions.DEFAULT_TIMEOUT) -> Server:
    app = Application(db_path, version_ttl, owners, policy=policy, front_door=front_door,
                      session_ttl=session_ttl, session_idle=session_idle,
                      session_secret=session_secret, runner=runner,
                      action_timeout=action_timeout)
    return Server((_loopback(host), int(port)), app)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="loom-ui",
        description="Local view of the Loom work registry, with guarded task actions "
                    "(loopback only).")
    parser.add_argument("--host", default="127.0.0.1", help="loopback address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8788, help="TCP port (0 picks a free port)")
    parser.add_argument("--db", default=None, help="registry database (default: $WORK_DB or "
                                                   "~/.local/state/loom/work.db)")
    parser.add_argument("--version-ttl", type=float, default=60.0,
                        help="seconds to cache the version gate (default 60)")
    parser.add_argument("--owner", action="append", default=None, metavar="IDENTITY",
                        help="extra identity treated as the owner for queue routing; repeatable "
                             "(the engine's human identities are always included)")
    parser.add_argument("--human", action="append", default=None, metavar="IDENTITY:ROLE[,ROLE]",
                        help="a human identity this server may act as, with its roles "
                             "(implementer, owner, reviewer); repeatable. Without one, the server "
                             "is read-only.")
    parser.add_argument("--identity-config", default=None, metavar="PATH",
                        help="JSON identity configuration (default: $LOOM_UI_CONFIG or "
                             "~/.config/loom/ui.json); --human takes precedence")
    parser.add_argument("--read-only", action="store_true",
                        help="ignore any configured identity and serve read-only")
    parser.add_argument("--front-door", default=None, metavar="PATH",
                        help="the loom front door actions are executed through "
                             "(default: this UI's engine home)")
    parser.add_argument("--session-ttl", type=float, default=8 * 3600,
                        help="seconds a paired session may live (default 28800)")
    parser.add_argument("--action-timeout", type=float, default=actions.DEFAULT_TIMEOUT,
                        help="seconds before a task action is reported as unconfirmed")
    args = parser.parse_args(argv)

    try:
        policy = Policy(()) if args.read_only else Policy.load(args.identity_config, args.human or ())
    except PolicyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    owners = frozenset(engine.HUMAN_IDENTITIES) | frozenset(args.owner or ())
    try:
        server = make_server(args.host, args.port, args.db, args.version_ttl, owners,
                             policy=policy, front_door=args.front_door,
                             session_ttl=args.session_ttl, action_timeout=args.action_timeout)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    host, port = server.server_address[:2]
    mode = "task actions enabled" if policy.configured else "read-only"
    print(f"loom ui listening on http://{host}:{port} ({mode}, loopback only)")
    print(f"owner identities: {', '.join(sorted(server.app.owners))}")
    if policy.configured:
        print("pair a browser (one link per configured identity):")
        for identity, token in server.app.sessions.pairing():
            print(f"  http://{host}:{port}/pair?token={token}   as {identity} "
                  f"({', '.join(sorted(server.app.policy.get(identity).roles))})")
    else:
        print("no human identity configured: the UI will not offer any action")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
