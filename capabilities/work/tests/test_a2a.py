#!/usr/bin/env python3
"""Tests for the A2A v1.0 façade.

Runs the real HTTP server in-process against a disposable work home and drives
it with JSON-RPC requests. Mutations go through the same `task` CLI, so these
tests also prove the two surfaces stay consistent.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORK / "a2a"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_work import Workspace, run  # noqa: E402

_spec = importlib.util.spec_from_file_location("a2a_server", WORK / "a2a" / "server.py")
a2a_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(a2a_server)


class A2AFacadeTest(unittest.TestCase):

    def setUp(self):
        self.ws = Workspace()
        self.server = a2a_server.make_server(self.ws.root, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = self.server.base_url

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.ws.close()

    # -- helpers ---------------------------------------------------------

    def http(self, method, path, payload=None, headers=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
                return response.status, dict(response.headers), json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            body = exc.read()
            exc.close()
            return exc.code, dict(exc.headers), json.loads(body) if body else None

    def rpc(self, method, params=None, version="1.0", agent=None):
        headers = {"A2A-Version": version}
        if agent:
            headers["X-Agent-Id"] = agent
        status, _, body = self.http("POST", "/", {
            "jsonrpc": "2.0", "id": "req-1", "method": method, "params": params or {},
        }, headers)
        self.assertEqual(status, 200, body)
        return body

    def send_action(self, task_id, action, agent, **extra):
        data = {"action": action, **extra}
        return self.rpc("SendMessage", {
            "message": {
                "role": "ROLE_USER",
                "messageId": "msg-1",
                "taskId": task_id,
                "parts": [{"data": data}],
                "metadata": {"agent": agent},
            }
        })

    # -- messages --------------------------------------------------------

    def test_ask_answer_and_inbox_over_a2a(self):
        asked = self.send_action("demo/W-1", "ask", "a2a/asker",
                                 to="a2a/owner", question="Which fixture wins?",
                                 neededBy="2026-11-07")["result"]
        self.assertEqual(asked["id"], "demo/W-1")
        self.assertEqual(asked["status"]["state"], "TASK_STATE_SUBMITTED")

        inbox = self.rpc("messages/list", {"metadata": {"agent": "a2a/owner"}})["result"]
        self.assertEqual(inbox["count"], 1)
        message = inbox["messages"][0]
        self.assertEqual(message["from"], "a2a/asker")
        self.assertEqual(message["question"], "Which fixture wins?")
        self.assertEqual(message["neededBy"], "2026-11-07")
        self.assertFalse(message["answered"])

        self.assertEqual(
            self.rpc("messages/list", {"metadata": {"agent": "a2a/nobody"}})["result"]["count"], 0)

        answered = self.send_action("demo/W-1", "answer", "a2a/owner",
                                    question=message["seq"], decision="src/one")["result"]
        self.assertEqual(answered["id"], "demo/W-1")
        self.assertEqual(
            self.rpc("messages/list", {"metadata": {"agent": "a2a/owner"}})["result"]["count"], 0)
        closed = self.rpc("messages/list",
                          {"metadata": {"agent": "a2a/owner"}, "includeAnswered": True})["result"]
        self.assertTrue(closed["messages"][0]["answered"])

        wrong = self.send_action("demo/W-1", "answer", "a2a/intruder",
                                 question=message["seq"], decision="mine")
        self.assertEqual(wrong["error"]["code"], -32602)
        self.assertIn("addressed to a2a/owner", wrong["error"]["message"])

        self.assertEqual(run("--root", str(self.ws.root), "events", "--verify").returncode, 0)

    def test_independent_review_lifecycle_over_a2a(self):
        git = ["git", "-C", str(self.ws.repo)]
        evidence = self.ws.repo / "evidence" / "W-1.md"
        evidence.parent.mkdir()
        evidence.write_text("Review fixture evidence\n")
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        commit = [*git, "-c", "user.name=Reviewer fixture", "-c",
                  "user.email=review@example.invalid", "-c", "commit.gpgSign=false",
                  "commit", "-qm"]
        subprocess.run([*commit, "Base fixture", "--allow-empty"], check=True)
        base = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
        subprocess.run([*git, "add", "evidence/W-1.md"], check=True)
        subprocess.run([*commit, "Review fixture"], check=True)
        source = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()

        task_id = "demo/W-1"
        owner = "a2a/implementer"
        reviewer = "a2a/reviewer"
        claimed = self.send_action(task_id, "claim", owner, revision=0)["result"]
        epoch = claimed["metadata"]["claim"]["epoch"]
        self.assertEqual(epoch, 1)
        self.send_action(task_id, "transition", owner, revision=1, to="green",
                         epoch=epoch)["result"]
        self.send_action(task_id, "ask", owner, to=reviewer, question="Review source and evidence",
                         reviewSource=source, reviewBase=base,
                         reviewEvidence="evidence/W-1.md")["result"]
        inbox = self.rpc("messages/list", {"metadata": {"agent": reviewer}})["result"]
        question = inbox["messages"][0]
        self.assertEqual(question["reviewSource"], source)
        self.assertEqual(question["reviewBase"], base)
        self.assertEqual(question["reviewEvidence"], "evidence/W-1.md")
        self.assertEqual(question["revision"], 2)
        self.send_action(task_id, "answer", reviewer, question=question["seq"], decision="approve",
                         note="Normal review. Checked behavior and focused tests.")["result"]
        history = self.rpc("GetTask", {"id": task_id, "historyLength": 1})["result"]["history"]
        answer = history[0]["metadata"]
        self.assertEqual(answer["reviewSource"], source)
        self.assertEqual(answer["reviewBase"], base)
        self.assertEqual(answer["reviewEvidence"], "evidence/W-1.md")
        self.assertEqual(answer["decision"], "approve")

        reviewed = self.send_action(task_id, "transition", owner, revision=2, to="reviewed",
                                    epoch=epoch, reviewer=reviewer, reviewAnswer=answer["seq"],
                                    evidence="evidence/W-1.md")["result"]
        self.assertEqual(reviewed["metadata"]["internalStatus"], "reviewed")
        integrated = self.send_action(task_id, "transition", owner, revision=3, to="integrated",
                                      integratedSource=source, evidence="evidence/W-1.md")["result"]
        self.assertEqual(integrated["metadata"]["internalStatus"], "integrated")

    def test_ask_requires_fields_and_identity(self):
        missing = self.send_action("demo/W-1", "ask", "a2a/asker", to="a2a/owner")
        self.assertEqual(missing["error"]["code"], -32602)

        anonymous = self.rpc("SendMessage", {
            "message": {
                "role": "ROLE_USER", "messageId": "m", "taskId": "demo/W-1",
                "parts": [{"data": {"action": "ask", "to": "a2a/owner", "question": "Q?"}}],
            }
        })
        self.assertEqual(anonymous["error"]["code"], -32602)

        no_agent = self.rpc("messages/list", {})
        self.assertEqual(no_agent["error"]["code"], -32602)

    # -- discovery -------------------------------------------------------

    def test_agent_card_is_discoverable_at_well_known_paths(self):
        for path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
            status, headers, card = self.http("GET", path)
            self.assertEqual(status, 200)
            self.assertIn("max-age", headers.get("Cache-Control", ""))
            self.assertTrue(headers.get("ETag"))
            self.assertEqual(card["supportedInterfaces"][0]["protocolBinding"], "JSONRPC")
            self.assertEqual(card["supportedInterfaces"][0]["protocolVersion"], "1.0")
            self.assertFalse(card["capabilities"]["streaming"])
            self.assertFalse(card["capabilities"]["pushNotifications"])
            self.assertEqual(card["skills"][0]["id"], "work-coordination")

        status, headers, _ = self.http(
            "GET", "/.well-known/agent-card.json",
            headers={"If-None-Match": headers["ETag"]})
        self.assertEqual(status, 304)

    # -- reads -----------------------------------------------------------

    def test_list_tasks_maps_states_and_paginates(self):
        first = self.rpc("ListTasks", {"pageSize": 2})["result"]
        self.assertEqual(len(first["tasks"]), 2)
        self.assertTrue(first["nextPageToken"])
        second = self.rpc("ListTasks", {"pageSize": 2, "pageToken": first["nextPageToken"]})["result"]
        self.assertEqual(len(second["tasks"]), 2)
        self.assertEqual(second["nextPageToken"], "")

        ids = {task["id"] for task in first["tasks"] + second["tasks"]}
        self.assertEqual(ids, {"demo/W-1", "demo/W-2", "demo/W-3", "demo/W-4"})
        for task in first["tasks"] + second["tasks"]:
            self.assertIn(task["status"]["state"], {
                "TASK_STATE_SUBMITTED", "TASK_STATE_WORKING",
                "TASK_STATE_INPUT_REQUIRED", "TASK_STATE_COMPLETED",
                "TASK_STATE_CANCELED", "TASK_STATE_REJECTED",
            })
            self.assertNotIn("artifacts", task)
            self.assertNotIn("history", task)

        filtered = self.rpc("ListTasks", {"contextId": "demo"})["result"]
        self.assertEqual(len(filtered["tasks"]), 4)
        by_id = {task["id"]: task for task in filtered["tasks"]}
        self.assertTrue(by_id["demo/W-1"]["metadata"]["claimable"])
        self.assertFalse(by_id["demo/W-2"]["metadata"]["claimable"])
        self.assertEqual(by_id["demo/W-2"]["metadata"]["blockedBy"], ["demo/W-1"])
        self.assertFalse(by_id["demo/W-3"]["metadata"]["claimable"])
        self.assertEqual(self.rpc("ListTasks", {"contextId": "nope"})["result"]["tasks"], [])
        self.assertEqual(self.rpc("ListTasks", {"status": "TASK_STATE_WORKING"})["result"]["tasks"], [])

        invalid = self.rpc("ListTasks", {"pageToken": "not-a-token"})
        self.assertEqual(invalid["error"]["code"], -32602)

    def test_get_task_and_not_found(self):
        result = self.rpc("GetTask", {"id": "demo/W-1", "historyLength": 0})["result"]
        self.assertEqual(result["id"], "demo/W-1")
        self.assertEqual(result["contextId"], "demo")
        self.assertEqual(result["status"]["state"], "TASK_STATE_SUBMITTED")
        self.assertEqual(result["metadata"]["revision"], 0)
        self.assertEqual(result["metadata"]["internalStatus"], "ready")

        missing = self.rpc("GetTask", {"id": "demo/NOPE"})
        self.assertEqual(missing["error"]["code"], -32001)
        self.assertIn("does not exist", missing["error"]["message"])

    # -- mutations -------------------------------------------------------

    def test_send_message_claims_through_the_shared_cli(self):
        result = self.send_action("demo/W-1", "claim", "a2a/agent-1", revision=0, ttl="45m")
        task = result["result"]
        self.assertEqual(task["status"]["state"], "TASK_STATE_WORKING")
        self.assertEqual(task["metadata"]["claim"]["agent"], "a2a/agent-1")
        self.assertEqual(task["metadata"]["revision"], 1)

        conflict = self.send_action("demo/W-1", "claim", "a2a/agent-2", revision=1)
        self.assertEqual(conflict["error"]["code"], -32602)
        self.assertIn("leased by a2a/agent-1", conflict["error"]["message"])

        stale = self.send_action("demo/W-2", "claim", "a2a/agent-2", revision=0)
        self.assertEqual(stale["error"]["code"], -32602)
        self.assertIn("unmet dependencies", stale["error"]["message"])

        history = self.rpc("GetTask", {"id": "demo/W-1", "historyLength": 5})["result"]["history"]
        self.assertTrue(history)
        self.assertIn("claim", history[-1]["parts"][0]["text"])

        verified = run("--root", str(self.ws.root), "events", "--verify")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def test_cancel_task_returns_terminal_canceled_state(self):
        claimed = self.send_action("demo/W-1", "claim", "a2a/agent-1", revision=0)["result"]
        epoch = claimed["metadata"]["claim"]["epoch"]
        canceled = self.rpc("CancelTask", {
            "id": "demo/W-1", "metadata": {"agent": "a2a/agent-1", "epoch": epoch},
        })["result"]
        self.assertEqual(canceled["status"]["state"], "TASK_STATE_CANCELED")
        self.assertEqual(canceled["metadata"]["internalStatus"], "released")

        again = self.rpc("CancelTask", {"id": "demo/W-1", "metadata": {"agent": "a2a/agent-1"}})
        self.assertEqual(again["error"]["code"], -32002)

        terminal = self.send_action("demo/W-1", "claim", "a2a/agent-2", revision=2)
        self.assertEqual(terminal["error"]["code"], -32004)

    def test_send_message_without_task_returns_guidance(self):
        result = self.rpc("SendMessage", {
            "message": {"role": "ROLE_USER", "messageId": "msg-1",
                        "parts": [{"text": "hello"}]},
        })["result"]
        self.assertEqual(result["kind"], "message")
        self.assertEqual(result["role"], "ROLE_AGENT")
        self.assertIn("Work-coordination skill", result["parts"][0]["text"])

    # -- protocol errors -------------------------------------------------

    def test_capabilities_version_and_methods(self):
        self.assertEqual(self.rpc("SubscribeToTask", {"id": "demo/W-1"})["error"]["code"], -32004)
        self.assertEqual(self.rpc("message/stream", {"message": {}})["error"]["code"], -32004)
        self.assertEqual(
            self.rpc("CreateTaskPushNotificationConfig", {"id": "demo/W-1"})["error"]["code"], -32003)
        self.assertEqual(self.rpc("NoSuchMethod")["error"]["code"], -32601)
        self.assertEqual(
            self.rpc("ListTasks", version="0.9")["error"]["code"], -32009)

    def test_unknown_action_is_refused(self):
        result = self.send_action("demo/W-1", "delete-everything", "a2a/agent-1", revision=0)
        self.assertEqual(result["error"]["code"], -32602)
        self.assertIn("unknown action", result["error"]["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
