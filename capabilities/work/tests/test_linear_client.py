"""Test the Linear client with a stub transport and a disposable redirect server."""
from __future__ import annotations

import io
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from linear_client import LinearClient, LinearError  # noqa: E402


def page(nodes, cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


def issue(issue_id="issue", *, archived_at=None):
    return {
        "id": issue_id,
        "title": "Review relay",
        "description": None,
        "state": {"id": "state"},
        "team": {"id": "team"},
        "project": {"id": "project"},
        "url": "https://linear.app/example/issue/EX-1",
        "archivedAt": archived_at,
    }


class LinearClientTest(unittest.TestCase):
    def setUp(self):
        self.opener = Mock()
        with patch("linear_client.build_opener", return_value=self.opener):
            self.client = LinearClient("test-key", timeout=5)

    def respond(self, *data):
        self.opener.open.side_effect = [
            io.BytesIO(json.dumps(response).encode()) for response in data
        ]

    def requests(self):
        return [json.loads(call.args[0].data) for call in self.opener.open.call_args_list]

    def test_destination_checks_project_team_and_paginates_states(self):
        project = {"id": "project", "name": "Planning", "teams": {"nodes": [{"id": "team"}]}}
        self.respond(
            {"data": {"team": {"id": "team", "name": "Platform", "states": page(
                [{"id": "ready", "name": "Ready"}], "next")},
                "project": project}},
            {"data": {"team": {"id": "team", "name": "Platform", "states": page(
                [{"id": "done", "name": "Done"}])},
                "project": project}},
        )

        self.assertEqual(self.client.destination("team", "project"), {
            "team_name": "Platform", "project_name": "Planning",
            "state_ids": {"Ready": "ready", "Done": "done"},
        })
        first, second = self.requests()
        self.assertEqual(first["variables"]["teamFilter"], {"id": {"eq": "team"}})
        self.assertIsNone(first["variables"]["after"])
        self.assertEqual(second["variables"]["after"], "next")
        request = self.opener.open.call_args_list[0].args[0]
        self.assertEqual(request.full_url, "https://api.linear.app/graphql")
        self.assertEqual(request.headers["Authorization"], "test-key")
        self.assertEqual(self.opener.open.call_args_list[0].kwargs["timeout"], 5)

    def test_destination_rejects_team_outside_project(self):
        self.respond({"data": {
            "team": {"id": "team", "name": "Platform", "states": page([])},
            "project": {"id": "project", "name": "Planning", "teams": {"nodes": []}},
        }})
        with self.assertRaisesRegex(LinearError, "team is not associated"):
            self.client.destination("team", "project")

    def test_lists_all_pages_including_archived_issues(self):
        archived = "2026-10-01T00:00:00Z"
        self.respond(
            {"data": {"issues": page([issue("issue-one")], "next")}},
            {"data": {"issues": page([issue("issue-two", archived_at=archived)])}},
        )
        rows = list(self.client.issues("team", "project"))
        self.assertEqual([row["id"] for row in rows], ["issue-one", "issue-two"])
        self.assertEqual(rows[0]["stateId"], "state")
        self.assertIsNone(rows[0]["description"])
        self.assertEqual(rows[1]["archivedAt"], archived)
        self.assertEqual(rows[1]["url"], "https://linear.app/example/issue/EX-1")
        first, second = self.requests()
        self.assertIn("includeArchived: true", first["query"])
        self.assertEqual(first["variables"]["filter"], {
            "team": {"id": {"eq": "team"}},
            "project": {"id": {"eq": "project"}},
        })
        self.assertEqual(second["variables"]["after"], "next")

    def test_issue_reads_priority_and_labels(self):
        self.respond({"data": {"issues": page([{
            **issue(), "priority": 2,
            "labels": {"nodes": [{"id": "label-1", "name": "iam"}]},
        }])}})
        row = next(self.client.issues("team", "project"))
        self.assertEqual(row["priority"], 2)
        self.assertEqual(row["labelIds"], ["label-1"])

    def test_issue_list_rejects_wrong_scope_and_repeated_cursor(self):
        out_of_scope = issue()
        out_of_scope["project"] = {"id": "different"}
        self.respond({"data": {"issues": page([out_of_scope])}})
        with self.assertRaisesRegex(LinearError, "outside the selected"):
            list(self.client.issues("team", "project"))

        self.respond(
            {"data": {"issues": page([], "repeated")}},
            {"data": {"issues": page([], "repeated")}},
        )
        with self.assertRaisesRegex(LinearError, "pagination"):
            list(self.client.issues("team", "project"))

    def test_mutations_send_input_and_normalize_issue(self):
        self.respond(
            {"data": {"issueCreate": {"success": True, "issue": issue()}}},
            {"data": {"issueUpdate": {"success": True, "issue": issue()}}},
        )
        fields = {"id": "issue", "title": "Review", "teamId": "team", "projectId": "project"}
        self.assertEqual(self.client.create_issue(fields)["id"], "issue")
        self.assertEqual(self.client.update_issue("issue", {"stateId": "state"})["stateId"], "state")
        created, updated = self.requests()
        self.assertEqual(created["variables"], {"input": fields})
        self.assertEqual(updated["variables"], {"id": "issue", "input": {"stateId": "state"}})

    def test_graphql_partial_success_is_refused_without_leaking_response(self):
        self.respond({
            "data": {"issueCreate": {"success": True, "issue": issue()}},
            "errors": [{"message": "Secret and untrusted details"}],
        })
        with self.assertRaises(LinearError) as failure:
            self.client.create_issue({})
        self.assertNotIn("Secret", str(failure.exception))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_missing_mutation_result_and_failed_mutation_are_refused(self):
        self.respond(
            {"data": {"issueUpdate": {"success": False, "issue": None}}},
            {"data": {"issueUpdate": {"success": True, "issue": None}}},
        )
        for _ in range(2):
            with self.assertRaises(LinearError):
                self.client.update_issue("issue", {})

    def test_http_and_network_errors_are_sanitized_without_retries(self):
        self.opener.open.side_effect = [
            HTTPError("https://api.linear.app/graphql", 429, "Secret response", {}, None),
            URLError("Secret network detail"),
        ]
        with self.assertRaisesRegex(LinearError, "429"):
            self.client.update_issue("issue", {})
        with self.assertRaisesRegex(LinearError, "Could not reach"):
            self.client.create_issue({})
        self.assertEqual(self.opener.open.call_count, 2)

    def test_rejects_invalid_credentials_and_accepts_explicit_bearer(self):
        for value in ("", "key\r\nInjected: value", "Bearer ", "bad key"):
            with self.subTest(value=value), self.assertRaises(LinearError):
                LinearClient(value)
        with patch("linear_client.build_opener", return_value=self.opener):
            client = LinearClient("Bearer test-token")
        self.respond({"data": {"issues": page([])}})
        self.assertEqual(list(client.issues("team", "project")), [])
        self.assertEqual(self.opener.open.call_args.args[0].headers["Authorization"], "Bearer test-token")

    def test_redirect_is_refused_before_authorization_can_leave_endpoint(self):
        visited = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                visited.append(self.path)
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(302)
                self.send_header("Location", "/other")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                visited.append(self.path)
                self.send_error(400)

            def log_message(self, *_):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with patch("linear_client.ENDPOINT", f"http://127.0.0.1:{server.server_port}/graphql"):
                    with self.assertRaisesRegex(LinearError, "302"):
                        LinearClient("test-key").create_issue({})
            finally:
                server.shutdown()
                worker.join()
        self.assertEqual(visited, ["/graphql"])


if __name__ == "__main__":
    unittest.main()
