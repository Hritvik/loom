"""HTTP boundaries for guarded actions: pairing, CSRF, Origin, staleness, replay, roles."""
from __future__ import annotations

import http.client
import json
import re
import subprocess
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ActionHome  # noqa: E402

from loom_ui import actions, identity, server  # noqa: E402

FORM = "application/x-www-form-urlencoded"


class ActionServerTest(unittest.TestCase):
    configure = True

    def setUp(self):
        self.home = ActionHome()
        self.addCleanup(self.home.close)
        self.policy = identity.Policy.parse(
            [f"{ActionHome.HRITVIK}:owner,implementer",
             f"{ActionHome.REVIEWER}:reviewer"]) if self.configure else identity.Policy(())
        self.start()

    def start(self, ttl: float = 0.0, runner=None):
        self.server = server.make_server(
            "127.0.0.1", 0, self.home.db, version_ttl=ttl, policy=self.policy,
            front_door=self.home.work_home / "loom", runner=runner)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    # -- plumbing -------------------------------------------------------------

    def request(self, method="GET", path="/", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def page(self, path, headers=None):
        status, response_headers, raw = self.request("GET", path, headers)
        return status, response_headers, raw.decode("utf-8")

    def json(self, path, headers=None):
        status, _, raw = self.request("GET", path, headers)
        return status, json.loads(raw)

    def token_for(self, who: str) -> str:
        return next(token for name, token in self.server.app.sessions.pairing()
                    if name == who)

    def pair(self, who: str = ActionHome.HRITVIK) -> str:
        status, headers, _ = self.request("GET", f"/pair?token={self.token_for(who)}")
        self.assertEqual(status, 303)
        cookie = headers["Set-Cookie"]
        return cookie.split(";", 1)[0]

    def session_headers(self, cookie: str, origin: str | None = None) -> dict:
        return {"Cookie": cookie, "Origin": origin if origin is not None else self.origin}

    def hidden(self, body: str, name: str) -> str:
        match = re.search(rf'name="{re.escape(name)}" value="([^"]*)"', body)
        self.assertIsNotNone(match, f"no hidden {name} in the form")
        return match.group(1)

    def form_body(self, fields: dict) -> str:
        from urllib.parse import urlencode

        return urlencode(fields)

    def confirm(self, task: str, action: str, cookie: str | None = None,
                query: str = "") -> tuple[int, str]:
        cookie = cookie if cookie is not None else self.pair()
        path = f"/task/{task}/action/{action}" + (f"?{query}" if query else "")
        status, _, body = self.page(path, {"Cookie": cookie})
        return status, body

    def submit(self, task: str, action: str, fields: dict, cookie: str,
               origin: str | None = None, content_type: str = FORM,
               query: str = ""):
        path = f"/task/{task}/action/{action}" + (f"?{query}" if query else "")
        headers = {"Cookie": cookie, "Content-Type": content_type,
                   "Origin": origin if origin is not None else self.origin}
        status, response_headers, raw = self.request("POST", path, headers,
                                                     self.form_body(fields))
        return status, raw.decode("utf-8")

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

    def events(self) -> list[dict]:
        from store import SqliteStore

        store = SqliteStore(self.home.root, self.home.db)
        try:
            return [pair[1] for pair in store.read_events()]
        finally:
            store.close()

    def question_seq(self, task: str, to: str) -> int:
        return next(event["seq"] for event in reversed(self.events())
                    if event.get("event") == "ask" and event.get("task") == task
                    and event.get("to") == to)

    # -- pairing --------------------------------------------------------------

    def test_pairing_sets_a_hardened_cookie(self):
        status, headers, _ = self.request("GET", f"/pair?token={self.token_for('hritvik')}")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("Domain", cookie)

    def test_a_wrong_pairing_token_pairs_nobody(self):
        status, headers, body = self.page("/pair?token=not-a-token")
        self.assertEqual(status, 403)
        self.assertIn("Pairing refused", body)
        self.assertNotIn("Set-Cookie", headers)

    def test_the_session_endpoint_names_the_identity_but_never_the_secret(self):
        status, payload = self.json("/api/session")
        self.assertEqual(status, 200)
        self.assertFalse(payload["authenticated"])
        self.assertIsNone(payload["identity"])
        cookie = self.pair()
        status, payload = self.json("/api/session", {"Cookie": cookie})
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["identity"], "hritvik")
        self.assertEqual(payload["roles"], ["implementer", "owner"])
        self.assertNotIn("csrf", payload)

    # -- authorisation --------------------------------------------------------

    def test_an_action_needs_a_paired_session(self):
        status, _, body = self.page("/task/demo/A-1/action/claim")
        self.assertEqual(status, 401)
        self.assertIn("Not signed in", body)
        status, body = self.submit("demo/A-1", "claim", {"revision": "1"}, cookie="loom_session=x")
        self.assertEqual(status, 401)

    def test_csrf_is_required_and_checked(self):
        cookie = self.pair()
        _, body = self.confirm("demo/A-1", "claim", cookie)
        fields = self.form_fields(body, {"ttl": "30m"})
        fields["csrf"] = "wrong"
        status, page = self.submit("demo/A-1", "claim", fields, cookie)
        self.assertEqual(status, 403)
        self.assertIn("CSRF token does not match", page)
        self.assertEqual(self.home.record_of("demo/A-1")["status"], "ready")

    def test_the_origin_must_be_this_ui_exactly(self):
        cookie = self.pair()
        _, body = self.confirm("demo/A-1", "claim", cookie)
        fields = self.form_fields(body, {"ttl": "30m"})
        status, page = self.submit("demo/A-1", "claim", fields, cookie, origin="")
        self.assertEqual(status, 403)
        self.assertIn("origin_rejected", page)
        status, _ = self.submit("demo/A-1", "claim", fields, cookie,
                                origin="http://127.0.0.1:1")
        self.assertEqual(status, 403)
        status, _ = self.submit("demo/A-1", "claim", fields, cookie,
                                origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertEqual(self.home.record_of("demo/A-1")["status"], "ready")

    def test_a_body_that_names_an_actor_is_rejected(self):
        cookie = self.pair()
        for name in ("agent", "actor", "identity", "owner", "user"):
            with self.subTest(field=name):
                _, body = self.confirm("demo/A-1", "claim", cookie)
                fields = self.form_fields(body, {"ttl": "30m", name: "root"})
                status, page = self.submit("demo/A-1", "claim", fields, cookie)
                self.assertEqual(status, 400)
                self.assertIn("may not name the acting identity", page)
        self.assertEqual(self.home.record_of("demo/A-1")["status"], "ready")

    def test_a_role_that_cannot_act_is_refused_before_anything_runs(self):
        cookie = self.pair(ActionHome.REVIEWER)
        status, body = self.confirm("demo/A-1", "claim", cookie)
        self.assertEqual(status, 409)
        self.assertIn("not available", body)
        self.assertIn("role does not include implementer", body)
        self.assertEqual(self.home.record_of("demo/A-1")["status"], "ready")

    def test_a_question_can_only_be_answered_by_its_addressee(self):
        seq = self.question_seq("demo/A-3", ActionHome.REVIEWER)
        reviewer = self.pair(ActionHome.REVIEWER)
        status, body = self.confirm("demo/A-3", "answer", reviewer, query=f"question={seq}")
        self.assertEqual(status, 200)
        self.assertIn("Answer question", body)

        owner = self.pair(ActionHome.HRITVIK)
        status, _ = self.confirm("demo/A-3", "answer", owner, query=f"question={seq}")
        self.assertEqual(status, 404)

    # -- the confirmation page ------------------------------------------------

    def test_the_confirmation_page_shows_revision_epoch_lease_and_actor(self):
        cookie = self.pair()
        status, body = self.confirm("demo/A-2", "renew", cookie)
        self.assertEqual(status, 200)
        self.assertIn("Confirm: Renew lease", body)
        self.assertIn("Acting as", body)
        self.assertIn("hritvik", body)
        self.assertIn("revision presented", body)
        self.assertIn("fencing epoch presented", body)
        self.assertIn(self.home.lease_live, body)
        self.assertEqual(self.hidden(body, "epoch"), "2")
        self.assertEqual(self.hidden(body, "revision"), "1")
        self.assertIn('name="ttl"', body)
        self.assertIn('method="post"', body)

    def test_task_text_is_escaped_on_the_confirmation_page(self):
        self.home.registry["tasks"].append({
            "id": "demo/A-9", "board": "demo.board", "program": "demo",
            "title": "<script>alert(1)</script>", "goal": "x", "status": "ready",
            "revision": 1, "epoch": 1, "write_paths": ["src/a9"],
            "owner_repo": str(self.home.repo),
        })
        self.home.save()
        status, body = self.confirm("demo/A-9", "claim")
        self.assertEqual(status, 200)
        self.assertNotIn("<script", body)
        self.assertIn("&lt;script&gt;", body)

    def test_the_inspector_links_actions_when_a_human_is_configured(self):
        cookie = self.pair()
        status, _, body = self.page("/task/demo/A-1", {"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn("Actions", body)
        self.assertIn("/task/demo/A-1/action/claim", body)
        self.assertIn("signed in as", body)

    # -- execution ------------------------------------------------------------

    def test_a_claim_runs_through_the_front_door_and_links_the_event(self):
        cookie = self.pair()
        _, body = self.confirm("demo/A-1", "claim", cookie)
        fields = self.form_fields(body, {"ttl": "30m"})
        status, page = self.submit("demo/A-1", "claim", fields, cookie)
        self.assertEqual(status, 200)
        self.assertIn("Action recorded", page)
        record = self.home.record_of("demo/A-1")
        self.assertEqual(record["status"], "claimed")
        self.assertEqual(record["claim"]["agent"], "hritvik")
        match = re.search(r"#event-(\d+)", page)
        self.assertIsNotNone(match)
        link_seq = int(match.group(1))
        claim_event = next(event for event in self.events()
                           if event.get("event") == "claim" and event.get("task") == "demo/A-1")
        self.assertEqual(claim_event["seq"], link_seq)
        self.assertEqual(claim_event["agent"], "hritvik")
        self.assertIn("event", page)

    def test_a_stale_page_is_refused_and_not_retried(self):
        cookie = self.pair()
        _, body = self.confirm("demo/A-4", "transition", cookie, query="to=ready")
        fields = self.form_fields(body, {"reason": "decided"})
        # Someone moves the task between render and submit. `ready` is still a legal target from
        # the new state, so the refusal has to come from the revision, not from the action list.
        from loom_ui import actions as engine_actions

        run = engine_actions.execute(self.home.work_home / "loom", self.home.db,
                                     ActionHome.HRITVIK,
                                     ["transition", "demo/A-4", "--revision", "1",
                                      "--to", "planned", "--reason", "re-planned"])
        self.assertTrue(run.ok, run.stderr)
        before = len(self.events())
        status, page = self.submit("demo/A-4", "transition", fields, cookie,
                                   query="to=ready")
        self.assertEqual(status, 409)
        self.assertIn("moved before your action ran", page)
        self.assertIn("Nothing was executed", page)
        self.assertEqual(len(self.events()), before)
        self.assertEqual(self.home.record_of("demo/A-4")["status"], "planned")

    def test_an_engine_conflict_is_reported_once_and_not_retried(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(
                argv, 2, "", "error: revision conflict on demo/A-1: you passed 1, registry has 2.")

        self.stop()
        self.start(runner=runner)
        cookie = self.pair()
        _, body = self.confirm("demo/A-1", "claim", cookie)
        fields = self.form_fields(body, {"ttl": "30m"})
        status, page = self.submit("demo/A-1", "claim", fields, cookie)
        self.assertEqual(status, 409)
        self.assertIn("Nothing was written", page)
        self.assertIn("revision conflict", page)
        self.assertEqual(len(calls), 1, "the UI must not retry a refused action")
        self.assertEqual(self.home.record_of("demo/A-1")["status"], "ready")

    def test_a_gate_denial_is_reported_as_a_refusal(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(
                argv, 2, "", "error: illegal transition ready -> accepted for demo/A-1")

        self.stop()
        self.start(runner=runner)
        cookie = self.pair()
        _, body = self.confirm("demo/A-1", "claim", cookie)
        status, page = self.submit("demo/A-1", "claim",
                                   self.form_fields(body, {"ttl": "30m"}), cookie)
        self.assertEqual(status, 422)
        self.assertIn("Loom refused", page)
        self.assertIn("Nothing was written", page)

    def test_an_unconfirmed_action_says_the_outcome_is_unknown(self):
        def runner(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

        self.stop()
        self.start(runner=runner)
        cookie = self.pair()
        _, body = self.confirm("demo/A-1", "claim", cookie)
        status, page = self.submit("demo/A-1", "claim",
                                   self.form_fields(body, {"ttl": "30m"}), cookie)
        self.assertEqual(status, 504)
        self.assertIn("Outcome unknown", page)
        self.assertIn("may or may not have changed", page)

    def test_a_replayed_form_executes_nothing(self):
        cookie = self.pair()
        _, body = self.confirm("demo/A-1", "ask", cookie)
        fields = self.form_fields(body, {"to": "opencode/worker", "question": "Is this ok?"})
        status, _ = self.submit("demo/A-1", "ask", fields, cookie)
        self.assertEqual(status, 200)
        after_first = len(self.events())
        # `ask` is still offered afterwards, so only the spent form token can refuse this.
        status, page = self.submit("demo/A-1", "ask", fields, cookie)
        self.assertEqual(status, 409)
        self.assertIn("already submitted", page)
        self.assertEqual(len(self.events()), after_first)

    def test_a_field_cannot_smuggle_a_flag(self):
        cookie = self.pair()
        _, body = self.confirm("demo/A-1", "claim", cookie)
        fields = self.form_fields(body, {"ttl": "30m;rm"})
        status, page = self.submit("demo/A-1", "claim", fields, cookie)
        self.assertEqual(status, 400)
        self.assertIn("Lease length is not in the expected form", page)
        self.assertEqual(self.home.record_of("demo/A-1")["status"], "ready")

    def test_an_unknown_field_is_refused(self):
        cookie = self.pair()
        _, body = self.confirm("demo/A-1", "claim", cookie)
        fields = self.form_fields(body, {"ttl": "30m", "worker-command": "rm -rf /"})
        status, page = self.submit("demo/A-1", "claim", fields, cookie)
        self.assertEqual(status, 400)
        self.assertIn("unknown field", page)

    def test_a_non_form_content_type_is_refused(self):
        cookie = self.pair()
        _, body = self.confirm("demo/A-1", "claim", cookie)
        fields = self.form_fields(body, {"ttl": "30m"})
        status, page = self.submit("demo/A-1", "claim", fields, cookie,
                                   content_type="application/json")
        self.assertEqual(status, 415)

    def test_an_oversized_body_is_refused(self):
        cookie = self.pair()
        _, body = self.confirm("demo/A-1", "claim", cookie)
        fields = self.form_fields(body, {"ttl": "30m", "question": "x" * 20000})
        status, page = self.submit("demo/A-1", "claim", fields, cookie)
        self.assertEqual(status, 413)

    # -- what is deliberately absent -----------------------------------------

    def test_there_is_no_operational_action(self):
        cookie = self.pair()
        for action in ("tick", "deploy", "schedule", "workers", "linear", "annotate"):
            with self.subTest(action=action):
                status, _, body = self.page(f"/task/demo/A-1/action/{action}",
                                            {"Cookie": cookie})
                self.assertEqual(status, 404)
                status, page = self.submit("demo/A-1", action, {"revision": "1"}, cookie)
                self.assertEqual(status, 404)
        for verb in ("tick", "workers", "schedule", "deploy", "linear", "annotate"):
            self.assertNotIn(verb, actions.ENGINE_VERBS)

    def test_an_apply_route_does_not_exist(self):
        cookie = self.pair()
        for path in ("/tick", "/api/tick", "/deploy", "/api/actions/tick"):
            with self.subTest(path=path):
                status, _, _ = self.page(path, {"Cookie": cookie})
                self.assertEqual(status, 404)

    # -- the read side --------------------------------------------------------

    def test_the_panel_is_published_as_json(self):
        cookie = self.pair()
        status, payload = self.json("/api/tasks/demo/A-1/actions", {"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(payload["panel"]["identity"], "hritvik")
        self.assertIn("claim", payload["panel"]["available"])
        self.assertIn("ask", payload["panel"]["available"])
        options = {option["id"]: option for option in payload["panel"]["options"]}
        self.assertTrue(options["claim"]["available"])
        self.assertIn("revision 1 → 2", options["claim"]["effect"])

    def test_the_panel_without_a_session_offers_nothing(self):
        status, payload = self.json("/api/tasks/demo/A-1/actions")
        self.assertEqual(status, 200)
        self.assertEqual(payload["panel"]["available"], [])
        self.assertIsNone(payload["panel"]["identity"])

    def test_health_reports_that_actions_are_configured(self):
        status, payload = self.json("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["actions"]["enabled"])
        self.assertEqual([human["identity"] for human in payload["actions"]["humans"]],
                         ["hritvik", "rev"])

    def test_a_task_id_that_ends_in_actions_is_still_readable(self):
        # A legal task id can end in "/actions", which the panel suffix used to claim first.
        self.home.add("demo/actions", "ready", ["src/odd"], revision=1, epoch=1)
        self.home.save()
        status, payload = self.json("/api/tasks/demo/actions")
        self.assertEqual(status, 200)
        self.assertEqual(payload["task"]["id"], "demo/actions")
        self.assertIn("detail", payload)
        # The panel route still resolves for that task, one suffix deeper.
        status, payload = self.json("/api/tasks/demo/actions/actions")
        self.assertEqual(status, 200)
        self.assertEqual(payload["task"], "demo/actions")
        self.assertIn("panel", payload)

    def test_pages_outside_the_inspector_do_not_claim_the_server_is_read_only(self):
        # Regression: shell() defaulted to the read-only sentence, so /graph and /operations
        # asserted that no identity was configured on a server that could act.
        for path in ("/graph?program=demo", "/operations"):
            with self.subTest(path=path):
                status, _, page = self.page(path)
                self.assertEqual(status, 200)
                self.assertNotIn("no configured human identity", page)
                self.assertIn("Task actions run through", page)

    def test_the_version_gate_closes_actions_too(self):
        self.stop()
        self.home.drift_engine("store.py")
        self.start()
        cookie = self.pair()
        status, page = self.submit("demo/A-1", "claim",
                                   {"revision": "1", "csrf": "x", "form": "y"}, cookie)
        self.assertEqual(status, 503)
        self.assertIn("not serving", page)
        self.assertEqual(self.home.record_of("demo/A-1")["status"], "ready")

    def test_head_on_a_confirmation_page_has_no_body(self):
        cookie = self.pair()
        status, headers, raw = self.request("HEAD", "/task/demo/A-1/action/claim",
                                            {"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"")
        self.assertTrue(int(headers["Content-Length"]) > 0)


class UnconfiguredActionTest(ActionServerTest):
    """The same server with no human identity: every action route is closed."""

    configure = False

    # The tests above assume a configured identity; only the fail-closed ones apply here.
    test_an_action_needs_a_paired_session = None
    test_csrf_is_required_and_checked = None
    test_the_origin_must_be_this_ui_exactly = None
    test_a_body_that_names_an_actor_is_rejected = None
    test_a_role_that_cannot_act_is_refused_before_anything_runs = None
    test_a_question_can_only_be_answered_by_its_addressee = None
    test_the_confirmation_page_shows_revision_epoch_lease_and_actor = None
    test_task_text_is_escaped_on_the_confirmation_page = None
    test_the_inspector_links_actions_when_a_human_is_configured = None
    test_a_claim_runs_through_the_front_door_and_links_the_event = None
    test_a_stale_page_is_refused_and_not_retried = None
    test_an_engine_conflict_is_reported_once_and_not_retried = None
    test_a_gate_denial_is_reported_as_a_refusal = None
    test_an_unconfirmed_action_says_the_outcome_is_unknown = None
    test_a_replayed_form_executes_nothing = None
    test_a_field_cannot_smuggle_a_flag = None
    test_an_unknown_field_is_refused = None
    test_a_non_form_content_type_is_refused = None
    test_an_oversized_body_is_refused = None
    test_there_is_no_operational_action = None
    test_an_apply_route_does_not_exist = None
    test_the_panel_is_published_as_json = None
    test_the_panel_without_a_session_offers_nothing = None
    test_the_session_endpoint_names_the_identity_but_never_the_secret = None
    test_pairing_sets_a_hardened_cookie = None
    test_the_version_gate_closes_actions_too = None
    test_health_reports_that_actions_are_configured = None
    test_head_on_a_confirmation_page_has_no_body = None
    test_pages_outside_the_inspector_do_not_claim_the_server_is_read_only = None

    def test_the_read_only_pages_say_the_server_cannot_act(self):
        for path in ("/", "/tasks", "/task/demo/A-1", "/graph?program=demo", "/operations"):
            with self.subTest(path=path):
                status, _, page = self.page(path)
                self.assertEqual(status, 200)
                self.assertIn("no configured human identity", page)

    def test_pairing_is_unavailable_and_says_so(self):
        status, _, body = self.page("/pair?token=anything")
        self.assertEqual(status, 403)
        self.assertIn("Pairing refused", body)

    def test_every_action_route_is_closed(self):
        status, _, body = self.page("/task/demo/A-1/action/claim")
        self.assertEqual(status, 403)
        self.assertIn("Actions are disabled", body)
        status, body = self.submit("demo/A-1", "claim", {"revision": "1", "csrf": "x",
                                                         "form": "y"}, cookie="loom_session=x")
        self.assertEqual(status, 403)
        self.assertIn("no configured human identity", body)
        self.assertEqual(self.home.record_of("demo/A-1")["status"], "ready")

    def test_the_task_page_explains_why_there_are_no_actions(self):
        status, _, body = self.page("/task/demo/A-1")
        self.assertEqual(status, 200)
        self.assertIn("no configured human identity", body)
        self.assertNotIn('method="post"', body)

    def test_health_reports_actions_disabled(self):
        status, payload = self.json("/api/health")
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["actions"]["enabled"])


if __name__ == "__main__":
    unittest.main()
