"""UI-6 — acceptance verification over a live server and the real front door.

This module is the executable half of ``ui/VERIFICATION.md``. It is deliberately not a second unit
suite: every check below asks the question UI-6 asks — does the surface a person uses preserve the
boundary? — against a real SQLite registry, the real ``loom`` executable, and HTTP as a browser
speaks it (cookies, redirects, form posts).

Ten classes, one per acceptance dimension:

* :class:`ReadModelParity` — the UI read model is the front door's data, not a copy of it.
* :class:`IdentityBoundary` — the browser cannot name, inherit or smuggle an actor.
* :class:`VersionBoundary` — a drifted engine closes every data surface.
* :class:`NetworkBoundary` — loopback bind, Host, Origin and the absence of CORS.
* :class:`StaleWriteBoundary` — a moved revision or epoch is refused, once, with no retry.
* :class:`ContentSafety` — untrusted registry text is escaped on every surface; JSON is lossless.
* :class:`GraphAccessibility` — the picture has a complete, labelled text equivalent.
* :class:`TaskInspection` — inspection facts are separate and traceable to the detail payload.
* :class:`BlockedWork` — a blocked row explains the gate and names what unblocks it.
* :class:`OwnerJourneys` — a paired reviewer answers and a paired owner accepts, over HTTP.

The browser journeys use a real cookie jar and redirect following. They are not a rendering check:
CSS layout, zoom, screen-reader output and responsive breakpoints are not observable from here. That
limit is named in the verification document rather than implied by silence.
"""
from __future__ import annotations

import html
import http.client
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ActionHome, ControlHome, Home, SOURCE_B, T0  # noqa: E402

import engine  # noqa: E402
from loom_ui import identity, server  # noqa: E402

FORM = "application/x-www-form-urlencoded"

#: Payloads that would become script, an event handler or a pseudo-protocol if a page ever stopped
#: escaping. ``&lt;script&gt;`` is included because double escaping is the correct rendering of
#: text that already looked escaped: it must never become live markup.
PAYLOADS = (
    "<script>alert('ui-6')</script>",
    '<img src=x onerror="alert(1)">',
    '"><svg/onload=alert(1)>',
    "</textarea><script>alert(1)</script>",
    "javascript:alert(1)",
    "&lt;script&gt;alert(1)&lt;/script&gt;",
)

#: Elements that must never be produced from registry text. The graph's own ``<svg>`` is the one
#: legitimate drawing and is checked separately.
FORBIDDEN_TAGS = frozenset({"script", "img", "iframe", "object", "embed"})


class LiveServer(unittest.TestCase):
    """One loopback server on a disposable work home, with the helpers a check needs."""

    home_factory = Home

    def setUp(self):
        self.home = self.home_factory()
        self.addCleanup(self.home.close)
        self.prepare()
        self.start()

    def prepare(self) -> None:
        """Let a subclass change the fixture before the server reads it."""

    def policy(self) -> identity.Policy:
        return identity.Policy(())

    def start(self, **overrides):
        options = {"version_ttl": 0, "policy": self.policy(),
                   "front_door": self.home.work_home / "loom"}
        options.update(overrides)
        self.server = server.make_server("127.0.0.1", 0, self.home.db, **options)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    # -- HTTP -----------------------------------------------------------------

    def request(self, method="GET", path="/", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def page(self, path, headers=None):
        status, _headers, raw = self.request("GET", path, headers)
        return status, raw.decode("utf-8")

    def page_full(self, path, headers=None):
        status, response_headers, raw = self.request("GET", path, headers)
        return status, response_headers, raw.decode("utf-8")

    def json(self, path, headers=None):
        status, _, raw = self.request("GET", path, headers)
        return status, json.loads(raw)

    def raw_get(self, path, *, host=None):
        """A GET with no ``Host`` header unless one is given; ``http.client`` adds one otherwise."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            connection.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
            if host is not None:
                connection.putheader("Host", host)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    # -- CLI ------------------------------------------------------------------

    def cli(self, *args):
        result = subprocess.run([str(self.home.work_home / "loom"), *args],
                                capture_output=True, text=True, timeout=90,
                                env=dict(os.environ))
        self.assertEqual(result.returncode, 0, f"loom {' '.join(args)}: {result.stderr}")
        return result.stdout

    def cli_json(self, *args):
        return json.loads(self.cli(*args))

    # -- registry -------------------------------------------------------------

    def events(self) -> list[dict]:
        from store import SqliteStore

        store = SqliteStore(self.home.root, self.home.db)
        try:
            return [pair[1] for pair in store.read_events()]
        finally:
            store.close()

    def record(self, task_id: str) -> dict:
        """The in-memory fixture record, for tests that mutate the registry on purpose."""
        return next(item for item in self.home.registry["tasks"] if item["id"] == task_id)

    def fresh(self, task_id: str) -> dict:
        """The record as the front door wrote it; use this after a submitted action."""
        return next(item for item in self.home.load()["tasks"] if item["id"] == task_id)


class ActionServer(LiveServer):
    """The same server, with a paired person and a reviewer, and a counting front-door runner."""

    home_factory = ActionHome

    def setUp(self):
        self.calls = []
        super().setUp()

    def policy(self):
        return identity.Policy.parse([
            f"{ActionHome.HRITVIK}:owner,implementer,reviewer",
            f"{ActionHome.REVIEWER}:reviewer",
        ])

    def start(self, **overrides):
        overrides.setdefault("runner", self.runner)
        super().start(**overrides)

    def runner(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.run(argv, **kwargs)

    # -- pairing and forms ----------------------------------------------------

    def token_for(self, who: str) -> str:
        return next(token for name, token in self.server.app.sessions.pairing() if name == who)

    def pair(self, who: str = ActionHome.HRITVIK) -> str:
        status, headers, _ = self.request("GET", f"/pair?token={self.token_for(who)}")
        self.assertEqual(status, 303)
        return headers["Set-Cookie"].split(";", 1)[0]

    def session_headers(self, cookie: str, origin: str | None = None) -> dict:
        return {"Cookie": cookie, "Origin": self.origin if origin is None else origin}

    def hidden(self, body: str, name: str) -> str:
        match = re.search(rf'name="{re.escape(name)}" value="([^"]*)"', body)
        self.assertIsNotNone(match, f"no hidden {name} in the form")
        return match.group(1)

    def confirm(self, task: str, action: str, cookie: str | None = None,
                query: str = "") -> tuple[int, str]:
        cookie = cookie if cookie is not None else self.pair()
        path = f"/task/{task}/action/{action}" + (f"?{query}" if query else "")
        status, body = self.page(path, {"Cookie": cookie})
        return status, body

    def form_fields(self, body: str, extra: dict | None = None) -> dict:
        fields = {"csrf": self.hidden(body, "csrf"), "form": self.hidden(body, "form"),
                  "revision": self.hidden(body, "revision")}
        for name in ("epoch", "to", "question"):
            try:
                fields[name] = self.hidden(body, name)
            except AssertionError:
                continue
        fields.update(extra or {})
        return fields

    def submit(self, task: str, action: str, fields: dict, cookie: str, *,
               origin: str | None = None, content_type: str = FORM,
               query: str = "") -> tuple[int, str]:
        path = f"/task/{task}/action/{action}" + (f"?{query}" if query else "")
        headers = {"Cookie": cookie, "Content-Type": content_type,
                   "Origin": self.origin if origin is None else origin}
        status, _, raw = self.request("POST", path, headers, urllib.parse.urlencode(fields))
        return status, raw.decode("utf-8")


# --------------------------------------------------------------------------
# 1. Read-model parity
# --------------------------------------------------------------------------


class ReadModelParity(LiveServer):
    """The pages and the JSON are the front door's data, not a second interpretation."""

    home_factory = Home

    def test_every_task_matches_loom_list(self):
        status, payload = self.json("/api/snapshot")
        self.assertEqual(status, 200)
        ui = {task["id"]: task for task in payload["snapshot"]["tasks"]}
        cli = {task["id"]: task for task in self.cli_json("list", "--json")}

        self.assertEqual(set(ui), set(cli), "the UI and the CLI see different tasks")
        for task_id, record in cli.items():
            task = ui[task_id]
            for field in ("title", "goal", "status", "revision", "epoch", "evidence",
                          "blocked_needs", "owner_repo"):
                self.assertEqual(task.get(field), record.get(field),
                                 f"{task_id}.{field} differs between the UI and `loom list`")
            for field in ("write_paths", "depends_on"):
                self.assertEqual(task.get(field) or [], record.get(field) or [],
                                 f"{task_id}.{field} differs between the UI and `loom list`")
            ui_claim = task.get("claim") or {}
            cli_claim = record.get("claim") or {}
            for field in ("agent", "epoch", "base_revision", "lease_expires", "heartbeat"):
                self.assertEqual(ui_claim.get(field), cli_claim.get(field),
                                 f"{task_id}.claim.{field} differs from `loom list`")

    def test_claimability_matches_a_real_claim(self):
        """The UI says a task is claimable exactly when `loom claim` would accept it.

        ``loom list --ready`` is not the oracle: it does not consider a lapsed lease, so a task
        with an expired claim is listed there and still refused by ``claim``. Each probe runs the
        real front door against a throwaway copy of the registry, so the fixture is never mutated.
        """
        status, payload = self.json("/api/snapshot")
        self.assertEqual(status, 200)
        tasks = payload["snapshot"]["tasks"]
        ui_claimable = {task["id"] for task in tasks if task["claimability"]["claimable"]}
        probed = {task["id"] for task in tasks if self.claim_would_succeed(task["id"],
                                                                          task["revision"])}
        self.assertEqual(ui_claimable, probed,
                         "the UI offers a different claimable set than `loom claim` accepts")
        by_id = {task["id"]: task for task in tasks}
        # The distinguishing cases the registry fixture was built for.
        self.assertFalse(by_id["demo/W-2"]["claimability"]["claimable"])
        self.assertIn("demo/W-1", " ".join(by_id["demo/W-2"]["claimability"]["reasons"]))
        self.assertFalse(by_id["demo/W-7"]["claimability"]["claimable"])
        self.assertIn("takeover", " ".join(by_id["demo/W-7"]["claimability"]["reasons"]))

    def claim_would_succeed(self, task_id: str, revision: int) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "work.db"
            shutil.copy(self.home.db, copy)
            result = subprocess.run(
                [str(self.home.work_home / "loom"), "--db", str(copy),
                 "--agent", "parity/probe", "claim", task_id,
                 "--revision", str(revision), "--ttl", "5m"],
                capture_output=True, text=True, timeout=60, env=dict(os.environ))
            return result.returncode == 0

    def test_task_detail_matches_loom_show(self):
        for task_id in ("demo/W-2", "demo/W-5", "demo/W-7"):
            status, payload = self.json(f"/api/tasks/{task_id}")
            self.assertEqual(status, 200)
            record = self.cli_json("show", task_id, "--json")
            task = payload["task"]
            for field in ("id", "title", "status", "revision", "epoch", "evidence"):
                self.assertEqual(task.get(field), record.get(field),
                                 f"{task_id}.{field} differs from `loom show`")
            claim = task.get("claim") or {}
            self.assertEqual(claim.get("agent"), (record.get("claim") or {}).get("agent"))
            self.assertEqual(claim.get("lease_expires"),
                             (record.get("claim") or {}).get("lease_expires"))
            detail = payload["detail"]
            self.assertEqual(detail["evidence"]["locator"], record.get("evidence"))
            self.assertEqual([item["id"] for item in detail["upstream"]],
                             list(record.get("depends_on") or []))

    def test_graph_edges_are_the_front_door_dependencies(self):
        program = "demo"
        status, payload = self.json(f"/api/graph?program={program}")
        self.assertEqual(status, 200)
        graph = payload["graph"]
        cli = self.cli_json("list", "--json")
        expected = {(record["id"], dependency)
                    for record in cli if record.get("program") == program
                    for dependency in (record.get("depends_on") or [])}
        pictured = {(edge["dependent"], edge["dependency"]) for edge in graph["edges"]
                    if edge["kind"] == "depends-on"}
        self.assertEqual(pictured, expected,
                         "the graph is not the engine's dependency edges")
        self.assertEqual({node["id"] for node in graph["nodes"]},
                         {record["id"] for record in cli if record.get("program") == program})


# --------------------------------------------------------------------------
# 2. Identity boundary
# --------------------------------------------------------------------------


class IdentityBoundary(ActionServer):
    """No browser input becomes the acting identity; the session is the only source of it."""

    def test_every_actor_field_is_refused_before_anything_runs(self):
        cookie = self.pair(ActionHome.HRITVIK)
        _, body = self.confirm("demo/A-1", "claim", cookie)
        before = len(self.events())
        for name in ("agent", "actor", "acting_as", "acting-as", "identity", "as", "user",
                     "username", "owner", "impersonate"):
            fields = self.form_fields(body, {"ttl": "30m", name: "opencode/evil"})
            status, page = self.submit("demo/A-1", "claim", fields, cookie)
            self.assertEqual(status, 400, f"{name!r} was not refused")
            self.assertIn("may not name the acting identity", page)
            self.assertEqual(self.record("demo/A-1")["status"], "ready")
            self.assertEqual(len(self.events()), before)
            self.assertEqual(self.calls, [], f"{name!r} reached the engine")
            # Each refusal spent the form nonce, so re-render for the next field.
            _, body = self.confirm("demo/A-1", "claim", cookie)

    def test_a_query_or_header_cannot_choose_the_actor(self):
        cookie = self.pair(ActionHome.HRITVIK)
        _, body = self.confirm("demo/A-1", "claim", cookie,
                               query="agent=opencode/evil&as=opencode/evil")
        fields = self.form_fields(body, {"ttl": "30m"})
        status, _, raw = self.request("POST", "/task/demo/A-1/action/claim?agent=opencode/evil",
                                      {"Cookie": cookie, "Content-Type": FORM,
                                       "Origin": self.origin,
                                       "X-Agent": "opencode/evil",
                                       "X-Loom-Agent": "opencode/evil",
                                       "X-Forwarded-User": "opencode/evil",
                                       "Authorization": "Bearer opencode/evil"},
                                      urllib.parse.urlencode(fields))
        self.assertEqual(status, 200, raw.decode("utf-8")[:400])
        claim = self.fresh("demo/A-1")["claim"]
        self.assertEqual(claim["agent"], ActionHome.HRITVIK)
        answer = [event for event in self.events() if event.get("event") == "claim"]
        self.assertTrue(answer)
        self.assertEqual(answer[-1]["agent"], ActionHome.HRITVIK)

    def test_an_ambient_agent_environment_does_not_become_the_actor(self):
        os.environ["LOOM_AGENT"] = "opencode/evil"
        os.environ["WORK_AGENT"] = "opencode/evil"
        try:
            cookie = self.pair(ActionHome.HRITVIK)
            _, body = self.confirm("demo/A-1", "claim", cookie)
            status, page = self.submit("demo/A-1", "claim",
                                       self.form_fields(body, {"ttl": "30m"}), cookie)
        finally:
            os.environ.pop("LOOM_AGENT", None)
            os.environ.pop("WORK_AGENT", None)
        self.assertEqual(status, 200, page[:400])
        claim = self.fresh("demo/A-1")["claim"]
        self.assertEqual(claim["agent"], ActionHome.HRITVIK)
        self.assertIn("--agent", self.calls[-1])
        self.assertEqual(self.calls[-1][self.calls[-1].index("--agent") + 1],
                         ActionHome.HRITVIK)

    def test_a_forged_or_absent_session_cannot_act(self):
        status, page = self.confirm("demo/A-1", "claim", cookie="")
        self.assertIn(status, (401, 403))
        self.assertIn("paired session", page.lower())

        forged = "loom_session=00" + "11" * 16
        status, page = self.confirm("demo/A-1", "claim", cookie=forged)
        self.assertIn(status, (401, 403))
        self.assertEqual(self.record("demo/A-1")["status"], "ready")
        self.assertEqual(self.calls, [])

    def test_a_role_cannot_lend_its_session_another_role(self):
        # The reviewer identity may answer reviews, not claim implementation work.
        reviewer = self.pair(ActionHome.REVIEWER)
        _, body = self.confirm("demo/A-1", "claim", reviewer)
        self.assertNotIn('<form class="action"', body)
        self.assertIn("implementer", body)
        self.assertEqual(self.calls, [])


# --------------------------------------------------------------------------
# 3. Version boundary
# --------------------------------------------------------------------------


class VersionBoundary(ActionServer):
    """A UI whose engine is not the promoted engine serves no registry data at all."""

    home_factory = Home

    DATA_SURFACES = (
        "/", "/tasks", "/graph", "/graph?program=demo", "/operations",
        "/task/demo/W-1", "/api/snapshot", "/api/tasks", "/api/tasks/demo/W-1",
        "/api/control", "/api/graph?program=demo", "/api/operations", "/api/boards",
        "/api/provenance", "/api/health",
    )

    def test_every_data_surface_closes_on_a_drifted_engine(self):
        self.home.drift_engine("store.py")
        for path in self.DATA_SURFACES:
            status, headers, raw = self.request("GET", path)
            body = raw.decode("utf-8")
            self.assertEqual(status, 503, f"{path} served {status} with a drifted engine")
            self.assertNotIn("Task demo/W-1", body, f"{path} leaked registry content")
            self.assertNotIn('"demo/W-1"', body, f"{path} leaked registry content")
            if path == "/api/health":
                payload = json.loads(body)
                self.assertFalse(payload["ok"], path)
                self.assertNotEqual(payload["version"]["verdict"], "PINNED", path)
            elif path.startswith("/api/"):
                payload = json.loads(body)
                self.assertEqual(payload["error"], "version_gate_closed", path)
                self.assertNotEqual(payload["version"]["verdict"], "PINNED", path)
            else:
                self.assertIn("gate", body.lower(), path)

    def test_the_action_confirmation_and_post_are_closed_too(self):
        # A paired session is needed to get past the identity gate: the version gate must be the
        # thing that closes the surface, not a missing cookie.
        cookie = self.pair()
        self.home.drift_engine("engine.py")
        status, _, raw = self.request("GET", "/task/demo/W-1/action/claim",
                                      {"Cookie": cookie})
        body = raw.decode("utf-8")
        self.assertEqual(status, 503)
        self.assertNotIn("<form", body)
        self.assertNotIn("csrf", body)

        status, _, _ = self.request("POST", "/task/demo/W-1/action/claim",
                                    {"Cookie": cookie, "Content-Type": FORM,
                                     "Origin": self.origin}, "x=1")
        self.assertEqual(status, 503)

    def test_the_version_report_itself_stays_available(self):
        self.home.drift_engine("store.py")
        status, payload = self.json("/api/version")
        self.assertEqual(status, 200)
        self.assertNotEqual(payload["verdict"], "PINNED")
        self.assertIn("MISMATCH", json.dumps(payload))


# --------------------------------------------------------------------------
# 4. Network boundary
# --------------------------------------------------------------------------


class NetworkBoundary(ActionServer):
    """Loopback, an exact Host, an exact Origin for writes, and no CORS at all."""

    def test_the_listener_is_loopback_only(self):
        self.assertEqual(self.server.socket.getsockname()[0], "127.0.0.1")
        with self.assertRaises(ValueError):
            server.make_server("0.0.0.0", 0, self.home.db)
        with self.assertRaises(ValueError):
            server.make_server("192.168.1.20", 0, self.home.db)

    def test_a_non_loopback_or_foreign_host_is_refused(self):
        for host in ("192.168.1.20", "evil.example", "10.0.0.5"):
            status, _, raw = self.raw_get("/", host=f"{host}:{self.port}")
            self.assertEqual(status, 421, host)
            self.assertEqual(json.loads(raw)["error"], "host_not_loopback")

    def test_a_missing_or_wrong_port_host_is_refused(self):
        status, _, raw = self.raw_get("/")
        self.assertEqual(status, 421)
        self.assertEqual(json.loads(raw)["error"], "host_required")

        status, _, raw = self.raw_get("/", host=f"127.0.0.1:{self.port + 1}")
        self.assertEqual(status, 421)
        self.assertEqual(json.loads(raw)["error"], "host_port_mismatch")

    def test_a_cross_origin_read_is_refused_and_same_origin_is_allowed(self):
        status, _, _ = self.request("GET", "/api/tasks",
                                    {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

        status, _, _ = self.request("GET", "/api/tasks",
                                    {"Origin": self.origin})
        self.assertEqual(status, 200)

    def test_a_write_requires_this_exact_origin(self):
        for origin in (None, "", "null", "http://evil.example",
                       f"https://127.0.0.1:{self.port}",
                       f"http://localhost:{self.port}"):
            headers = {"Content-Type": FORM}
            if origin is not None:
                headers["Origin"] = origin
            status, _, raw = self.request("POST", "/task/demo/A-1/action/claim", headers,
                                          "x=1")
            self.assertEqual(status, 403, f"Origin {origin!r} was accepted")
            self.assertEqual(json.loads(raw)["error"], "origin_rejected")

    def test_no_response_grants_cors(self):
        for path in ("/", "/api/tasks", "/task/demo/W-1"):
            _, headers, _ = self.request("GET", path, {"Origin": "http://evil.example"})
            self.assertNotIn("Access-Control-Allow-Origin", headers, path)
        _, headers, _ = self.request("POST", "/task/demo/A-1/action/claim",
                                     {"Content-Type": FORM, "Origin": "http://evil.example"},
                                     "x=1")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_security_headers_are_on_pages_and_json(self):
        for path in ("/", "/api/tasks"):
            _, headers, _ = self.request("GET", path)
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(headers["X-Frame-Options"], "DENY")
            self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
            self.assertEqual(headers["Referrer-Policy"], "no-referrer")
            self.assertEqual(headers["Cache-Control"], "no-store")


# --------------------------------------------------------------------------
# 5. Stale writes
# --------------------------------------------------------------------------


class StaleWriteBoundary(ActionServer):
    """A confirmation page is a sample, not an authority. A stale one is refused, never retried."""

    def test_a_moved_revision_is_refused_without_running_the_engine(self):
        cookie = self.pair(ActionHome.HRITVIK)
        _, body = self.confirm("demo/A-1", "claim", cookie)
        fields = self.form_fields(body, {"ttl": "30m"})
        before = len(self.events())

        record = self.record("demo/A-1")
        record["revision"] = 2
        self.home.save()

        status, page = self.submit("demo/A-1", "claim", fields, cookie)
        self.assertEqual(status, 409)
        self.assertIn("revision", page.lower())
        self.assertNotIn("Action recorded", page)
        self.assertEqual(self.calls, [], "a stale page still reached the engine")
        self.assertEqual(len(self.events()), before)
        self.assertEqual(self.record("demo/A-1")["status"], "ready")

    def test_a_changed_fencing_epoch_is_refused_by_the_engine_once(self):
        cookie = self.pair(ActionHome.HRITVIK)
        _, body = self.confirm("demo/A-2", "renew", cookie)
        fields = self.form_fields(body, {"ttl": "45m"})
        before = len(self.events())

        record = self.record("demo/A-2")
        record["claim"]["epoch"] = 3
        record["epoch"] = 3
        self.home.save()

        status, page = self.submit("demo/A-2", "renew", fields, cookie)
        self.assertEqual(status, 409, page[:600])
        self.assertIn("moved", page.lower())
        self.assertEqual(len(self.calls), 1, "the refusal was retried or skipped")
        self.assertEqual(len(self.events()), before)
        self.assertEqual(self.record("demo/A-2")["claim"]["epoch"], 3)

    def test_an_expired_lease_removes_the_action_and_explains_takeover(self):
        cookie = self.pair(ActionHome.HRITVIK)
        _, body = self.confirm("demo/A-2", "renew", cookie)
        fields = self.form_fields(body, {"ttl": "45m"})

        record = self.record("demo/A-2")
        record["claim"]["lease_expires"] = self.home.lease_expired
        record["claim"]["heartbeat"] = self.home.lease_expired
        self.home.save()

        status, page = self.submit("demo/A-2", "renew", fields, cookie)
        self.assertEqual(status, 409)
        self.assertIn("takeover", page.lower())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.record("demo/A-2")["status"], "claimed")

    def test_a_timeout_is_an_unknown_outcome_not_a_guess(self):
        def timeout(argv, **kwargs):
            self.calls.append(list(argv))
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 90))

        self.start(runner=timeout)
        cookie = self.pair(ActionHome.HRITVIK)
        _, body = self.confirm("demo/A-1", "claim", cookie)
        status, page = self.submit("demo/A-1", "claim",
                                   self.form_fields(body, {"ttl": "30m"}), cookie)
        self.assertEqual(status, 504)
        self.assertIn("could not be confirmed", page.lower())
        self.assertIn("outcome", page.lower())
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.record("demo/A-1").get("claim"))

    def test_a_lapsed_lease_is_taken_over_only_after_the_lock_delay(self):
        cookie = self.pair(ActionHome.HRITVIK)
        status, body = self.confirm("demo/A-6", "takeover", cookie)
        self.assertEqual(status, 409)
        self.assertIn("lock delay", body)
        self.assertNotIn("Confirm take over", body)

        _, body = self.confirm("demo/A-5", "takeover", cookie)
        fields = self.form_fields(body, {"reason": "the worker is gone", "ttl": "30m"})
        status, page = self.submit("demo/A-5", "takeover", fields, cookie)
        self.assertEqual(status, 200, page[:600])
        self.assertEqual(self.fresh("demo/A-5")["claim"]["agent"], ActionHome.HRITVIK)
        self.assertEqual(self.fresh("demo/A-5")["epoch"], 2)


# --------------------------------------------------------------------------
# 6. Content safety
# --------------------------------------------------------------------------


class _Audit(HTMLParser):
    """The accessibility facts a static page can prove, collected without a browser."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html_lang = None
        self.title_parts: list[str] = []
        self._in_title = False
        self.h1 = 0
        self.tables = 0
        self.table_missing_caption: list[int] = []
        self._table_depth = 0
        self._table_has_caption: list[bool] = []
        self.th_without_scope = 0
        self.controls: list[dict] = []
        self.labels: set[str] = set()
        self.scripts = 0
        self.images_without_alt = 0
        self.forbidden: list[str] = []
        self.event_handlers: list[str] = []
        self.javascript_hrefs: list[str] = []
        self.svgs = 0
        self.positive_tabindex: list[str] = []
        self.ids: list[str] = []
        self.main = False
        self.skip_link = False
        self.first_link = None
        self.navs = 0
        self.svg_role = None
        self.svg_labelled = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in FORBIDDEN_TAGS:
            self.forbidden.append(tag)
        if tag == "svg":
            self.svgs += 1
        for name in attrs:
            if name.lower().startswith("on"):
                self.event_handlers.append(f"{tag}[{name}]")
        if tag == "a" and str(attrs.get("href") or "").lower().startswith("javascript:"):
            self.javascript_hrefs.append(attrs.get("href"))
        if tag == "html":
            self.html_lang = attrs.get("lang")
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self.h1 += 1
        elif tag == "script":
            self.scripts += 1
        elif tag == "img" and "alt" not in attrs:
            self.images_without_alt += 1
        elif tag == "main" and attrs.get("id") == "main":
            self.main = True
        elif tag == "nav":
            self.navs += 1
        elif tag == "a":
            href = attrs.get("href")
            if self.first_link is None:
                self.first_link = href
            if href == "#main" and "skip" in (attrs.get("class") or ""):
                self.skip_link = True
        elif tag == "table":
            self.tables += 1
            self._table_depth += 1
            self._table_has_caption.append(False)
        elif tag == "caption" and self._table_depth:
            self._table_has_caption[-1] = True
        elif tag == "th" and "scope" not in attrs:
            self.th_without_scope += 1
        elif tag in ("input", "textarea", "select"):
            if attrs.get("type") != "hidden":
                self.controls.append(attrs)
        elif tag == "label" and attrs.get("for"):
            self.labels.add(attrs["for"])
        elif tag == "svg":
            self.svg_role = attrs.get("role")
            self.svg_labelled = bool(attrs.get("aria-labelledby") or attrs.get("aria-label"))
        tabindex = attrs.get("tabindex")
        if tabindex and tabindex.lstrip("-").isdigit() and int(tabindex) > 0:
            self.positive_tabindex.append(tag)
        if attrs.get("id"):
            self.ids.append(attrs["id"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "table":
            if self._table_depth:
                has_caption = self._table_has_caption.pop()
                if not has_caption:
                    self.table_missing_caption.append(self.tables)
                self._table_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)


class ContentSafety(ActionServer):
    """Registry text is data. It is escaped on every page, and lossless over JSON."""

    home_factory = ControlHome

    def prepare(self):
        first = self.home.record("demo/W-15")
        first["title"] = PAYLOADS[0]
        first["goal"] = PAYLOADS[1]
        second = self.home.record("demo/W-3")
        second["claim"]["agent"] = PAYLOADS[2]
        third = self.home.record("demo/W-22")
        third["title"] = PAYLOADS[1]
        self.home.append(event="ask", task="demo/W-11", agent="opencode/worker",
                         to="owner", question=PAYLOADS[3], revision=3, status="green", now=T0)
        self.home.append(event="transition", task="demo/W-15", agent="opencode/worker",
                         status="blocked", previous_status="green", revision=3, needs="human",
                         reason=PAYLOADS[4], now=T0)
        self.home.append(event="answer", task="demo/W-18", agent="opencode/reviewer-agent",
                         to="opencode/worker", in_reply_to=1, decision="changes requested",
                         note=PAYLOADS[5], revision=3, status="green", now=T0)
        self.home.save()

    PAGES = ("/", "/tasks", "/task/demo/W-15", "/task/demo/W-11", "/task/demo/W-3",
             "/task/demo/W-18", "/graph?program=demo", "/graph?task=demo/W-20",
             "/operations")

    def test_every_page_escapes_untrusted_text(self):
        for path in self.PAGES:
            status, headers, body = self.page_full(path)
            self.assertEqual(status, 200, path)
            audit = _Audit()
            audit.feed(body)
            self.assertEqual(audit.forbidden, [], f"{path} rendered live markup")
            self.assertEqual(audit.event_handlers, [], f"{path} rendered an event handler")
            self.assertEqual(audit.javascript_hrefs, [], f"{path} rendered a javascript: link")
            self.assertIn("text/html", headers["Content-Type"])

    def test_each_payload_is_rendered_as_escaped_text(self):
        pages = {path: self.page(path)[1] for path in self.PAGES}
        combined = "\n".join(pages.values())
        for payload in PAYLOADS:
            self.assertTrue(html.escape(payload) in combined,
                            f"{payload!r} was not preserved as text")
        for payload in PAYLOADS:
            if payload.startswith(("javascript:", "&lt;")):
                # Plain text is rendered as text, and an already-escaped-looking string is
                # exactly what another payload's escape produces. The parser check above is
                # what proves neither became live markup.
                continue
            self.assertFalse(payload in combined, f"{payload!r} appeared unescaped")

    def test_the_confirmation_page_escapes_the_same_text(self):
        cookie = self.pair(ActionHome.HRITVIK)
        status, body = self.confirm("demo/W-22", "claim", cookie)
        self.assertEqual(status, 200)
        audit = _Audit()
        audit.feed(body)
        self.assertEqual(audit.forbidden, [])
        self.assertEqual(audit.event_handlers, [])
        self.assertTrue(html.escape(PAYLOADS[1]) in body)

    def test_json_preserves_the_text_it_escapes_for_html(self):
        status, payload = self.json("/api/tasks/demo/W-15")
        self.assertEqual(status, 200)
        self.assertEqual(payload["task"]["title"], PAYLOADS[0])
        self.assertEqual(payload["task"]["goal"], PAYLOADS[1])
        status, payload = self.json("/api/tasks/demo/W-3")
        self.assertEqual(status, 200)
        self.assertEqual(payload["task"]["claim"]["agent"], PAYLOADS[2])

    def test_every_page_has_the_accessibility_scaffold(self):
        cookie = self.pair(ActionHome.HRITVIK)
        pages = {path: self.page(path)[1] for path in self.PAGES}
        pages["/task/demo/W-22/action/claim"] = self.confirm("demo/W-22", "claim", cookie)[1]
        for path, body in pages.items():
            audit = _Audit()
            audit.feed(body)
            self.assertEqual(audit.html_lang, "en", path)
            self.assertTrue("".join(audit.title_parts).strip(), f"{path} has no title")
            self.assertEqual(audit.h1, 1, f"{path} has {audit.h1} h1 elements")
            self.assertTrue(audit.main, f"{path} has no main landmark")
            self.assertTrue(audit.skip_link, f"{path} has no skip link")
            self.assertEqual(audit.first_link, "#main", f"{path}'s first link is not the skip")
            self.assertGreaterEqual(audit.navs, 1, path)
            self.assertEqual(audit.scripts, 0, f"{path} contains script")
            self.assertEqual(audit.images_without_alt, 0, path)
            self.assertEqual(audit.positive_tabindex, [], f"{path} uses positive tabindex")
            self.assertEqual(audit.th_without_scope, 0, f"{path} has an unscoped header cell")
            self.assertEqual(audit.table_missing_caption, [], f"{path} has an uncaptioned table")
            for control in audit.controls:
                ident = control.get("id")
                self.assertTrue(ident, f"{path} has a control without an id")
                self.assertTrue(ident in audit.labels or control.get("aria-label"),
                                f"{path} control {ident!r} has no label")
            duplicates = {value for value in audit.ids if audit.ids.count(value) > 1}
            self.assertEqual(duplicates, set(), f"{path} repeats id(s) {duplicates}")


# --------------------------------------------------------------------------
# 7. Graph accessibility
# --------------------------------------------------------------------------


class GraphAccessibility(ActionServer):
    """The drawing is a convenience; the table is the graph."""

    home_factory = ControlHome

    def prepare(self):
        # Above the drawing limit the picture is replaced by an instruction, and the text
        # equivalent must still carry every task. 29 fixture tasks + 35 = 64 > 60.
        for number in range(1, 36):
            self.home.add_task(f"demo/B-{number}", "ready", [f"src/b{number}"])
        self.home.save()

    def graph(self, query=""):
        status, payload = self.json(f"/api/graph{query}")
        self.assertEqual(status, 200)
        return payload["graph"]

    def test_the_default_view_is_a_program_chooser_not_a_graph(self):
        status, body = self.page("/graph")
        self.assertEqual(status, 200)
        self.assertIn("Programs", body)
        self.assertNotIn("<svg", body)
        self.assertIn("<table>", body)

    def test_every_node_and_edge_has_a_table_row(self):
        graph = self.graph("?task=demo/W-20")
        status, body = self.page("/graph?task=demo/W-20")
        self.assertEqual(status, 200)
        for node in graph["nodes"]:
            encoded = urllib.parse.quote(node["id"], safe="")
            self.assertTrue(f'href="/graph?task={encoded}"' in body, node["id"])
        for edge in graph["edges"]:
            self.assertIn(edge["dependent"], body)
            self.assertIn(edge["dependency"], body)

        audit = _Audit()
        audit.feed(body)
        self.assertEqual(audit.table_missing_caption, [])
        self.assertEqual(audit.th_without_scope, 0)
        self.assertEqual(audit.svg_role, "img")
        self.assertTrue(audit.svg_labelled)
        self.assertIn('role="group"', body)
        self.assertIn('aria-label="Dependency graph, scrollable.', body)

    def test_a_large_program_lists_what_it_does_not_draw(self):
        graph = self.graph("?program=demo")
        self.assertFalse(graph["drawn"], "the fixture is meant to exceed the drawing limit")
        self.assertGreater(graph["omitted"], 0)
        status, body = self.page("/graph?program=demo")
        self.assertEqual(status, 200)
        self.assertNotIn("<svg", body, "a program over the limit must not be drawn")
        self.assertIn("drawing limit", body)
        self.assertIn("Text equivalent", body)
        for node in graph["nodes"]:
            encoded = urllib.parse.quote(node["id"], safe="")
            self.assertTrue(f'href="/graph?task={encoded}"' in body, node["id"])
        self.assertIn("focus", body.lower())

    def test_the_focus_closure_is_complete_in_text(self):
        graph = self.graph("?task=demo/W-20")
        upstream = {node["id"] for node in graph["nodes"]
                    if node["relation"] in {"upstream", "both"}}
        downstream = {node["id"] for node in graph["nodes"]
                      if node["relation"] in {"downstream", "both"}}
        status, body = self.page("/graph?task=demo/W-20")
        self.assertEqual(status, 200)
        self.assertIn(f"Focus {graph['focus']}", body)
        for task_id in upstream | downstream | {graph["focus"]}:
            self.assertIn(task_id, body)
        self.assertIn("Upstream — must finish first", body)
        self.assertIn("Downstream — impacted by this task", body)
        self.assertIn("Every edge in this closure", body)


# --------------------------------------------------------------------------
# 8. Task inspection
# --------------------------------------------------------------------------


class TaskInspection(ActionServer):
    """Inspection presents separate, traceable facts rather than one verdict."""

    home_factory = ControlHome

    def test_the_inspector_separates_every_inspection_fact(self):
        status, body = self.page("/task/demo/W-13")
        self.assertEqual(status, 200)
        for heading in ("State and claimability", "Why it can or cannot be claimed",
                        "Claim and lease", "Dependencies and dependents", "Evidence", "Review",
                        "Integration receipt", "Attempts", "Event history", "Source provenance"):
            self.assertIn(heading, body)
        self.assertIn("integrated", body)
        self.assertIn(SOURCE_B, body)         # the integrated commit
        self.assertIn("event-", body)         # the audit anchors

    def test_review_verdict_and_receipt_are_traceable_to_the_detail_payload(self):
        for task_id in ("demo/W-12", "demo/W-13", "demo/W-24"):
            status, payload = self.json(f"/api/tasks/{task_id}")
            self.assertEqual(status, 200)
            detail = payload["detail"]
            status, body = self.page(f"/task/{task_id}")
            self.assertEqual(status, 200)
            if detail["review"]:
                self.assertIn(detail["review"]["decision"], body)
                self.assertIn(detail["review"]["reviewer"], body)
                self.assertIn(detail["review"]["source"], body)
            if detail["receipt"] and detail["receipt"].get("source"):
                self.assertIn(detail["receipt"]["source"], body)
            if detail["evidence"]["exists"]:
                self.assertIn("present", body)
            else:
                self.assertIn("missing", body)
            for event in detail["events"]:
                self.assertIn(f'id="event-{event["seq"]}"', body)

    def test_a_missing_evidence_file_is_a_fact_not_a_fill_in(self):
        status, payload = self.json("/api/tasks/demo/W-24")
        self.assertEqual(status, 200)
        self.assertFalse(payload["detail"]["evidence"]["exists"])
        self.assertIn("missing-W-24.md", payload["detail"]["evidence"]["locator"])
        status, body = self.page("/task/demo/W-24")
        self.assertEqual(status, 200)
        self.assertIn("missing", body)
        self.assertIn("missing-W-24.md", body)

    def test_the_inspector_matches_the_detail_payload(self):
        status, payload = self.json("/api/tasks/demo/W-5")
        self.assertEqual(status, 200)
        task, detail = payload["task"], payload["detail"]
        status, body = self.page("/task/demo/W-5")
        self.assertEqual(status, 200)
        self.assertIn(task["title"], body)
        self.assertIn(task["status"], body)
        self.assertIn(str(task["revision"]), body)
        self.assertIn(task["claim"]["agent"], body)
        for link in detail["upstream"]:
            self.assertIn(link["id"], body)
        for link in detail["downstream"]:
            self.assertIn(link["id"], body)


# --------------------------------------------------------------------------
# 9. Blocked work
# --------------------------------------------------------------------------


class BlockedWork(ActionServer):
    """A row that cannot proceed says which gate stopped it and what would open it."""

    home_factory = ControlHome

    def test_a_dependency_blocked_task_names_its_prerequisite(self):
        status, payload = self.json("/api/tasks/demo/W-2")
        self.assertEqual(status, 200)
        reasons = " ".join(payload["task"]["claimability"]["reasons"])
        self.assertIn("unmet dependencies", reasons)
        self.assertIn("demo/W-1", reasons)

        status, body = self.page("/task/demo/W-2")
        self.assertEqual(status, 200)
        self.assertIn("ready", body)
        self.assertIn("demo/W-1", body)
        self.assertIn("unmet dependencies", body)
        self.assertNotIn("Every claim check passes", body)

    def test_a_write_path_conflict_names_the_task_that_holds_the_path(self):
        status, payload = self.json("/api/tasks/demo/W-4")
        self.assertEqual(status, 200)
        reasons = " ".join(payload["task"]["claimability"]["reasons"])
        self.assertIn("write-path conflict", reasons)
        self.assertIn("src/three", reasons)
        self.assertIn("opencode/other", reasons)

    def test_an_expired_lease_explains_takeover_not_a_plain_retry(self):
        status, payload = self.json("/api/tasks/demo/W-7")
        self.assertEqual(status, 200)
        reasons = " ".join(payload["task"]["claimability"]["reasons"])
        self.assertIn("expired lease", reasons)
        self.assertIn("takeover", reasons)
        status, body = self.page("/task/demo/W-7")
        self.assertEqual(status, 200)
        self.assertIn("Takeover", body)

    def test_human_and_agent_blockers_land_in_different_queues(self):
        status, payload = self.json("/api/control")
        self.assertEqual(status, 200)
        queues = {queue["id"]: queue for queue in payload["control"]["queues"]}
        needs_you = {item["task"]: item for item in queues["needs-you"]["items"]}
        stalled = {item["task"]: item for item in queues["stalled"]["items"]}
        self.assertIn("demo/W-15", needs_you)
        self.assertIn("demo/W-16", stalled)
        self.assertIn("Choose between option A and option B", needs_you["demo/W-15"]["detail"])
        self.assertIn("agent", stalled["demo/W-16"]["detail"].lower())

    def test_the_owner_queue_contains_every_human_blocked_task_the_front_door_reports(self):
        awaiting = {task["id"] for task in self.cli_json("list", "--awaiting", "human",
                                                         "--json")}
        status, payload = self.json("/api/control")
        self.assertEqual(status, 200)
        needs_you = {item["task"] for queue in payload["control"]["queues"]
                     if queue["id"] == "needs-you" for item in queue["items"]}
        self.assertTrue(awaiting, "the fixture has no human-blocked task")
        self.assertLessEqual(awaiting, needs_you,
                             "the owner queue hides a task `loom list --awaiting human` names")


# --------------------------------------------------------------------------
# 10. Owner journeys, browser-shaped
# --------------------------------------------------------------------------


class Browser:
    """A cookie jar and redirect following: the parts of a browser a server can observe."""

    def __init__(self, origin: str):
        self.origin = origin
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, path: str) -> tuple[int, str]:
        try:
            response = self.opener.open(self.origin + path, timeout=30)
            return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def post(self, path: str, fields: dict) -> tuple[int, str]:
        request = urllib.request.Request(
            self.origin + path,
            data=urllib.parse.urlencode(fields).encode("utf-8"),
            headers={"Content-Type": FORM, "Origin": self.origin},
            method="POST")
        try:
            response = self.opener.open(request, timeout=30)
            return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")


class OwnerJourneys(ActionServer):
    """The two workflows the owner acceptance argument rests on, walked over HTTP."""

    def pair_browser(self, who: str) -> Browser:
        browser = Browser(self.origin)
        status, body = browser.get(f"/pair?token={self.token_for(who)}")
        self.assertEqual(status, 200)
        self.assertIn("Owner control room", body)
        self.assertTrue(list(browser.jar), "pairing left no session cookie")
        return browser

    def test_a_reviewer_answers_a_question_from_the_owner_queue(self):
        browser = self.pair_browser(ActionHome.REVIEWER)

        status, home = browser.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Needs you", home)
        self.assertIn("demo/A-3", home)

        status, task_page = browser.get("/task/demo/A-3")
        self.assertEqual(status, 200)
        match = re.search(r'href="(/task/demo/A-3/action/answer\?question=\d+)"', task_page)
        self.assertIsNotNone(match, "an addressable question has no answer link")

        status, form = browser.get(match.group(1))
        self.assertEqual(status, 200)
        self.assertIn("Answer question", form)
        self.assertIn("acting as", form.lower())
        self.assertIn(ActionHome.REVIEWER, form)

        fields = {name: value for name, value in
                  re.findall(r'name="([^"]+)" value="([^"]*)"', form)}
        fields.update({"decision": "approve",
                       "note": "Verified from the paired browser journey."})
        status, outcome = browser.post(match.group(1), fields)
        self.assertEqual(status, 200, outcome[:800])
        self.assertIn("Action recorded", outcome)
        self.assertIn("acting as", outcome.lower())

        event_match = re.search(r'id="event-(\d+)"|href="/task/demo/A-3#event-(\d+)"', outcome)
        self.assertIsNotNone(event_match)
        seq = int(next(group for group in event_match.groups() if group))
        answer = next(event for event in self.events() if event["seq"] == seq)
        self.assertEqual(answer["event"], "answer")
        self.assertEqual(answer["agent"], ActionHome.REVIEWER)
        self.assertEqual(answer["decision"], "approve")
        self.assertEqual(answer["task"], "demo/A-3")

        status, detail = self.json("/api/tasks/demo/A-3")
        self.assertEqual(status, 200)
        answered = [question for question in detail["detail"]["questions"]
                    if question["seq"] == answer["in_reply_to"]]
        self.assertTrue(answered and answered[0]["answered"])

        status, _ = browser.get(f"/task/demo/A-3#event-{seq}")
        self.assertEqual(status, 200)

    def test_an_owner_accepts_integrated_work_from_the_acceptance_queue(self):
        browser = self.pair_browser(ActionHome.HRITVIK)

        status, home = browser.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Ready for acceptance", home)
        self.assertIn("demo/A-7", home)

        status, task_page = browser.get("/task/demo/A-7")
        self.assertEqual(status, 200)
        self.assertIn("Integration receipt", task_page)
        link = re.search(r'href="(/task/demo/A-7/action/transition\?to=accepted)"', task_page)
        self.assertIsNotNone(link, "integrated work offers no owner acceptance")

        status, form = browser.get(link.group(1))
        self.assertEqual(status, 200)
        self.assertIn("revision presented", form.lower())
        fields = {name: value for name, value in
                  re.findall(r'name="([^"]+)" value="([^"]*)"', form)}
        fields["evidence"] = "evidence/demo-A-7.md"
        status, outcome = browser.post(link.group(1), fields)
        self.assertEqual(status, 200, outcome[:800])
        self.assertIn("Action recorded", outcome)

        record = self.fresh("demo/A-7")
        self.assertEqual(record["status"], "accepted")
        self.assertEqual(record["revision"], 2)
        accepted = [event for event in self.events()
                    if event.get("event") == "transition" and event.get("status") == "accepted"]
        self.assertTrue(accepted)
        self.assertEqual(accepted[-1]["agent"], ActionHome.HRITVIK)
        self.assertEqual(accepted[-1]["evidence"], "evidence/demo-A-7.md")

        status, payload = self.json("/api/control")
        self.assertEqual(status, 200)
        acceptance = next(queue for queue in payload["control"]["queues"]
                          if queue["id"] == "acceptance")
        self.assertNotIn("demo/A-7", {item["task"] for item in acceptance["items"]})


if __name__ == "__main__":
    unittest.main()
