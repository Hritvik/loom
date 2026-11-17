"""HTTP boundaries: loopback only, read-only, escaped, and fail-closed."""
from __future__ import annotations

import http.client
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Home  # noqa: E402

from loom_ui import server  # noqa: E402


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.home = Home()
        self.addCleanup(self.home.close)
        self.start()

    def start(self, ttl: float = 0.0):
        self.server = server.make_server("127.0.0.1", 0, self.home.db, version_ttl=ttl)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method="GET", path="/", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def json(self, path, method="GET", headers=None):
        status, response_headers, raw = self.request(method, path, headers)
        return status, response_headers, json.loads(raw)

    # -- happy path ----------------------------------------------------------

    def test_health_reports_the_gate_state(self):
        status, _, payload = self.json("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["actions"]["enabled"])
        self.assertTrue(payload["version"]["ok"])

    def test_snapshot_returns_one_read_model_with_provenance(self):
        status, _, payload = self.json("/api/snapshot")
        self.assertEqual(status, 200)
        self.assertFalse(payload["actions"]["enabled"])
        self.assertEqual(len(payload["provenance"]), 6)
        self.assertEqual(payload["version"]["front_door"], payload["version"]["front_door"])
        tasks = payload["snapshot"]["tasks"]
        self.assertEqual(len(tasks), 7)
        for task in tasks:
            self.assertIn("status", task)
            self.assertIn("claimability", task)
        self.assertEqual([signal["source"] for signal in payload["provenance"]],
                         ["database", "process", "scheduler", "release", "git", "evidence"])

    def test_task_endpoints(self):
        status, _, payload = self.json("/api/tasks")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["tasks"]), 7)
        status, _, payload = self.json("/api/tasks/demo/W-4")
        self.assertEqual(status, 200)
        self.assertFalse(payload["task"]["claimability"]["claimable"])
        self.assertTrue(payload["task"]["claimability"]["path_conflicts"])
        status, _, payload = self.json("/api/tasks/demo/absent")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "unknown_task")

    def test_boards_endpoint(self):
        status, _, payload = self.json("/api/boards")
        self.assertEqual(status, 200)
        self.assertEqual(payload["boards"][0]["id"], "demo.board")

    def test_provenance_endpoint(self):
        status, _, payload = self.json("/api/provenance")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["provenance"]), 6)

    def test_index_is_server_rendered_without_script(self):
        status, headers, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        body = raw.decode("utf-8")
        self.assertIn("Local view of the portfolio registry", body)
        self.assertIn("Claimable now", body)
        self.assertNotIn("<script", body)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])

    def test_security_headers_are_present(self):
        _, headers, _ = self.request("GET", "/api/health")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_unknown_route_is_404(self):
        status, _, payload = self.json("/api/nope")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "not_found")

    # -- boundaries ----------------------------------------------------------

    def test_unhandled_methods_are_refused_with_the_real_method_set(self):
        for method in ("PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                status, headers, payload = self.json("/api/tasks/demo/W-1", method=method)
                self.assertEqual(status, 405)
                self.assertEqual(headers["Allow"], "GET, HEAD")
                self.assertEqual(payload["error"], "method_not_allowed")
        status, headers, payload = self.json("/api/tasks/demo/W-1", method="POST")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD")
        # A refusal names the methods of the route addressed, and only an action route takes a
        # POST — the read routes above must not advertise one.
        status, headers, payload = self.json("/task/demo/W-1/action/claim", method="PUT")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD, POST")

    def test_non_loopback_host_is_rejected(self):
        status, _, payload = self.json("/api/health", headers={"Host": "evil.example"})
        self.assertEqual(status, 421)
        self.assertEqual(payload["error"], "host_not_loopback")
        status, _, _ = self.json("/api/health",
                                 headers={"Host": f"192.168.1.10:{self.port}"})
        self.assertEqual(status, 421)

    def test_cross_origin_request_is_rejected(self):
        status, _, payload = self.json("/api/health",
                                       headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "origin_rejected")

    def test_same_origin_request_is_accepted(self):
        status, _, _ = self.json("/api/health",
                                 headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)

    def test_binding_a_non_loopback_address_is_refused(self):
        for address in ("0.0.0.0", "192.168.1.10", "example.com", "::"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    server.make_server(address, 0, self.home.db)

    def test_task_text_is_escaped(self):
        self.home.registry["tasks"].append({
            "id": "demo/W-9",
            "board": "demo.board",
            "program": "demo",
            "title": '<script>alert(1)</script><img src=x onerror=alert(2)>',
            "goal": "x",
            "status": "ready",
            "revision": 1,
            "write_paths": ["src/nine"],
        })
        self.home.save()
        status, _, raw = self.request("GET", "/api/tasks/demo/W-9")
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertIn("<script>", payload["task"]["title"])  # JSON carries the raw value
        status, _, raw = self.request("GET", "/")
        body = raw.decode("utf-8")
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn("<script", body)
        self.assertNotIn("<img", body)

    # -- fail closed ---------------------------------------------------------

    def test_a_version_mismatch_closes_the_page(self):
        self.stop()
        self.home.drift_engine("store.py")
        self.start()
        status, _, raw = self.request("GET", "/")
        self.assertEqual(status, 503)
        body = raw.decode("utf-8")
        self.assertIn("not serving", body)
        self.assertIn("MISMATCH", body)
        status, _, payload = self.json("/api/health")
        self.assertEqual(status, 503)
        self.assertFalse(payload["ok"])
        status, _, payload = self.json("/api/snapshot")
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "version_gate_closed")

    def test_an_unreadable_registry_is_reported_not_crashed(self):
        self.stop()
        self.server = server.make_server("127.0.0.1", 0, self.home.root / "absent.db",
                                         version_ttl=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]
        status, _, raw = self.request("GET", "/")
        self.assertEqual(status, 503)
        self.assertIn("does not exist", raw.decode("utf-8"))
        status, _, payload = self.json("/api/snapshot")
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "registry_unavailable")


if __name__ == "__main__":
    unittest.main()
