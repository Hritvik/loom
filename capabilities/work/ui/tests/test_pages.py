"""HTTP boundaries and rendering of the control room and task inspector."""
from __future__ import annotations

import http.client
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ControlHome, T0  # noqa: E402

from loom_ui import server  # noqa: E402


class PageTest(unittest.TestCase):
    def setUp(self):
        self.home = ControlHome()
        self.addCleanup(self.home.close)
        self.start()

    def start(self, db_path=None, owners=None):
        self.server = server.make_server("127.0.0.1", 0, db_path or self.home.db,
                                         version_ttl=0, owners=owners)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method="GET", path="/", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def json(self, path, method="GET", headers=None):
        status, response_headers, raw = self.request(method, path, headers)
        return status, response_headers, json.loads(raw)

    def page(self, path):
        status, headers, raw = self.request("GET", path)
        return status, headers, raw.decode("utf-8")

    # -- control room --------------------------------------------------------

    def test_control_room_renders_every_queue(self):
        status, headers, body = self.page("/")
        self.assertEqual(status, 200)
        self.assertIn("Owner control room", body)
        self.assertIn("Local view of the portfolio registry", body)
        self.assertNotIn("<script", body)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        for queue_id in ("needs-you", "stalled", "review", "integration", "acceptance",
                         "in-progress", "claimable", "waiting"):
            self.assertIn(f'id="q-{queue_id}"', body)
        # The owner queue names the blocked task and the question, not just a count.
        self.assertIn("demo/W-15", body)
        self.assertIn("Choose between option A and option B", body)
        self.assertIn("demo/W-11", body)

    def test_control_room_separates_ready_from_claimable(self):
        _, _, body = self.page("/")
        self.assertIn("Not yet claimable", body)
        self.assertIn("unmet dependencies: demo/W-1", body)
        self.assertIn("write-path conflict", body)
        self.assertIn("claim would succeed", body)

    def test_control_room_reports_partial_sources(self):
        _, _, body = self.page("/")
        self.assertIn("Partial data", body)
        self.assertIn("git (degraded)", body)

    def test_control_room_program_filter(self):
        status, _, body = self.page("/?program=other")
        self.assertEqual(status, 200)
        self.assertIn("Filtered to program other", body)
        self.assertIn("demo/W-30", body)
        self.assertNotIn("demo/W-1<", body)

    # -- task index ----------------------------------------------------------

    def test_all_tasks_lists_every_task_and_filters(self):
        status, _, body = self.page("/tasks")
        self.assertEqual(status, 200)
        for task_id in ("demo/W-1", "demo/W-13", "demo/W-30"):
            self.assertIn(f"/task/{task_id}", body)
        status, _, filtered = self.page("/tasks?program=other")
        self.assertEqual(status, 200)
        self.assertIn("/task/demo/W-30", filtered)
        self.assertNotIn("/task/demo/W-1\"", filtered)

    # -- inspector -----------------------------------------------------------

    def test_inspector_shows_the_inspection_facts(self):
        status, _, body = self.page("/task/demo/W-2")
        self.assertEqual(status, 200)
        for heading in ("State and claimability", "Why it can or cannot be claimed",
                        "Claim and lease", "Dependencies and dependents", "Evidence", "Review",
                        "Integration receipt", "Attempts", "Event history",
                        "Source provenance"):
            self.assertIn(heading, body)
        self.assertIn("task epoch", body)
        self.assertIn("fencing epoch (claim)", body)
        self.assertIn("unmet dependencies", body)
        self.assertIn('href="/task/demo/W-1"', body)

    def test_inspector_renders_verdict_and_receipt_for_an_integrated_task(self):
        _, _, body = self.page("/task/demo/W-13")
        self.assertIn("opencode/reviewer-agent", body)
        self.assertIn("dddddddddddd", body)
        self.assertIn("integrated commit", body)
        self.assertIn("integration tip", body)
        self.assertIn("Append-only audit log", body)

    def test_inspector_marks_missing_evidence(self):
        _, _, body = self.page("/task/demo/W-24")
        self.assertIn("file not found", body)
        self.assertIn("missing", body)

    def test_inspector_404s_are_html_and_explained(self):
        status, _, body = self.page("/task/demo/absent")
        self.assertEqual(status, 404)
        self.assertIn("Not found", body)
        self.assertIn("No task demo/absent", body)
        status, _, body = self.page("/task/not-a-task-id")
        self.assertEqual(status, 404)
        self.assertIn("not a task id", body)

    def test_owner_identity_flag_routes_a_question(self):
        status, _, _ = self.page("/")
        self.assertEqual(status, 200)
        _, _, body = self.page("/tasks")
        self.assertIn("/task/demo/W-27", body)
        self.stop()
        self.start(owners=frozenset({"owner", "hritvik"}))
        _, _, body = self.page("/")
        self.assertIn("Can you confirm the acceptance wording?", body)

    # -- JSON ----------------------------------------------------------------

    def test_api_control_serialises_the_same_queues(self):
        status, _, payload = self.json("/api/control")
        self.assertEqual(status, 200)
        room = payload["control"]
        self.assertEqual([queue["id"] for queue in room["queues"]],
                         ["needs-you", "stalled", "review", "integration", "acceptance",
                          "in-progress", "claimable", "waiting", "settled"])
        self.assertEqual(sum(queue["count"] for queue in room["queues"]),
                         room["counts"]["total"])
        self.assertEqual(room["counts"]["total"], 29)
        self.assertIn("owner", room["owner_identities"])
        needs_you = next(queue for queue in room["queues"] if queue["id"] == "needs-you")
        self.assertEqual({item["task"] for item in needs_you["items"]},
                         {"demo/W-11", "demo/W-15", "demo/W-22"})

    def test_api_control_honours_the_program_filter(self):
        _, _, payload = self.json("/api/control?program=other")
        self.assertEqual(payload["control"]["counts"]["total"], 1)
        self.assertEqual([row["task"] for row in payload["control"]["rows"]], ["demo/W-30"])

    def test_api_task_carries_the_detail(self):
        status, _, payload = self.json("/api/tasks/demo/W-13")
        self.assertEqual(status, 200)
        self.assertEqual(payload["task"]["status"], "integrated")
        detail = payload["detail"]
        for key in ("review", "receipt", "questions", "attempts", "events", "evidence",
                    "bucket"):
            self.assertIn(key, detail)
        self.assertEqual(detail["receipt"]["complete"], True)
        self.assertEqual(detail["review"]["decision"], "approve")

    def test_api_task_404_is_unchanged(self):
        status, _, payload = self.json("/api/tasks/demo/absent")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "unknown_task")

    # -- boundaries ----------------------------------------------------------

    def test_a_post_to_a_read_route_is_refused(self):
        for path in ("/", "/tasks", "/task/demo/W-1", "/api/control"):
            with self.subTest(path=path):
                status, headers, payload = self.json(path, method="POST")
                self.assertEqual(status, 405)
                self.assertEqual(headers["Allow"], "GET, HEAD")
                self.assertEqual(payload["error"], "method_not_allowed")

    def test_host_and_origin_checks_cover_the_new_routes(self):
        status, _, payload = self.json("/api/control", headers={"Host": "evil.example"})
        self.assertEqual(status, 421)
        self.assertEqual(payload["error"], "host_not_loopback")
        status, _, payload = self.json("/api/control",
                                       headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "origin_rejected")

    def test_head_requests_carry_no_body(self):
        for path in ("/", "/tasks", "/task/demo/W-1"):
            with self.subTest(path=path):
                status, headers, raw = self.request("HEAD", path)
                self.assertEqual(status, 200)
                self.assertEqual(raw, b"")
                self.assertGreater(int(headers["Content-Length"]), 0)

    # -- fail closed and empty ----------------------------------------------

    def test_an_unreadable_registry_is_not_reported_as_a_version_problem(self):
        self.stop()
        self.start(db_path=self.home.root / "absent.db")
        status, _, body = self.page("/")
        self.assertEqual(status, 503)
        self.assertIn("cannot read the registry", body)
        self.assertIn("does not exist", body)
        self.assertNotIn("version gate closed", body)
        status, _, payload = self.json("/api/control")
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "registry_unavailable")

    def test_an_empty_registry_renders_empty_states(self):
        self.home.registry["tasks"] = []
        self.home.save()
        status, _, body = self.page("/")
        self.assertEqual(status, 200)
        self.assertIn("Nothing in this queue in this sample.", body)
        self.assertIn("0 task(s) in scope", body)
        status, _, body = self.page("/tasks")
        self.assertEqual(status, 200)
        self.assertIn("No rows.", body)

    # -- untrusted content ---------------------------------------------------

    def test_untrusted_task_content_is_escaped_on_both_pages(self):
        self.home.registry["tasks"].append({
            "id": "demo/W-98", "board": "demo.board", "program": "demo",
            "title": '<script>alert("title")</script>',
            "goal": "<img src=x onerror=alert(1)>",
            "status": "ready", "revision": 1, "epoch": 1,
            "owner_repo": str(self.home.repo), "write_paths": ["src/evil"],
        })
        self.home.append(event="ask", task="demo/W-98", agent="opencode/worker", to="owner",
                         question="<script>alert('question')</script>", revision=1,
                         status="ready", now=T0)
        self.home.save()
        for path in ("/", "/tasks", "/task/demo/W-98"):
            with self.subTest(path=path):
                status, _, body = self.page(path)
                self.assertEqual(status, 200)
                self.assertNotIn("<script", body)
                self.assertNotIn("<img", body)
                self.assertIn("&lt;script&gt;", body)


class AccessibilityTest(unittest.TestCase):
    """The scaffold is asserted per page, not once, because each page is its own document."""

    def setUp(self):
        self.home = ControlHome()
        self.addCleanup(self.home.close)
        self.server = server.make_server("127.0.0.1", 0, self.home.db, version_ttl=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def body(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            return response.read().decode("utf-8")
        finally:
            connection.close()

    def test_every_page_has_landmarks_a_skip_link_and_one_h1(self):
        for path in ("/", "/tasks", "/task/demo/W-1", "/operations"):
            with self.subTest(path=path):
                body = self.body(path)
                self.assertEqual(body.count("<h1"), 1)
                self.assertIn('class="skip" href="#main"', body)
                self.assertIn('<main id="main" tabindex="-1"', body)
                self.assertIn('<header class="site">', body)
                self.assertIn('<nav class="views" aria-label="Views">', body)
                self.assertIn('<footer class="site">', body)
                self.assertIn('lang="en"', body)

    def test_tables_are_labelled_and_scroll_containers_are_focusable(self):
        for path in ("/", "/tasks", "/task/demo/W-13", "/operations"):
            with self.subTest(path=path):
                body = self.body(path)
                self.assertIn("<caption>", body)
                self.assertIn('scope="col"', body)
                self.assertIn('role="region"', body)
                self.assertIn('tabindex="0"', body)

    def test_status_is_never_colour_only(self):
        body = self.body("/task/demo/W-23")
        # The state text is present, not only a colour class, and the claimability says yes/no.
        self.assertIn(">claimed<", body)
        self.assertIn(">no<", body)
        self.assertIn("live lease", body)
        # The navigation marks the current page for assistive technology.
        self.assertIn('aria-current="page"', body)


if __name__ == "__main__":
    unittest.main()
