#!/usr/bin/env python3
"""Tests for the MCP stdio server.

Covers protocol handshake, tool discovery, read filters, claim/renew/release, the full lifecycle,
error paths and real newline-delimited stdio transport.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import select
import subprocess
import sys
import unittest
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_work import Workspace  # noqa: E402

_spec = importlib.util.spec_from_file_location("mcp_server", WORK / "mcp" / "server.py")
mcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp)

SERVER = WORK / "mcp" / "server.py"


class MCPTest(unittest.TestCase):

    def setUp(self):
        self.ws = Workspace()
        self.registry = mcp.Registry(self.ws.root)
        self.repo = str(self.ws.repo)

    def tearDown(self):
        self.ws.close()

    def call(self, method, params=None, agent="mcp/agent", repo=None):
        response = mcp.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
            self.registry, agent, repo)
        return response

    def tool(self, name, arguments=None, agent="mcp/agent", repo=None):
        response = self.call("tools/call", {"name": name, "arguments": arguments or {}},
                             agent=agent, repo=repo)
        self.assertIn("result", response, response)
        return response["result"]

    def structured(self, name, arguments=None, agent="mcp/agent", repo=None):
        result = self.tool(name, arguments, agent, repo)
        self.assertFalse(result.get("isError"), result)
        return result["structuredContent"]

    # -- handshake and discovery -----------------------------------------

    def test_initialize_negotiates_and_advertises_tools(self):
        response = self.call("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        })
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "work-coordination")
        self.assertIn("work_status", result["instructions"])

        unknown = self.call("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(unknown["result"]["protocolVersion"], mcp.FALLBACK_VERSION)

    def test_tools_list_is_self_describing(self):
        response = self.call("tools/list")
        tools = {tool["name"]: tool for tool in response["result"]["tools"]}
        self.assertEqual(set(tools), {
            "work_status", "work_list", "work_get", "work_claim",
            "work_renew", "work_release", "work_takeover", "work_transition",
            "work_inbox", "work_ask", "work_answer", "work_search", "work_dependents",
        })
        for tool in tools.values():
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])
        self.assertIn("before planning", tools["work_status"]["description"])

    def test_notifications_are_ignored(self):
        response = mcp.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            self.registry, "mcp/agent", None)
        self.assertIsNone(response)

    def test_unknown_tool_is_a_protocol_error(self):
        response = self.call("tools/call", {"name": "work_delete_everything", "arguments": {}})
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("Unknown tool", response["error"]["message"])

    # -- reads ------------------------------------------------------------

    def test_status_reports_ready_claims_and_drift(self):
        status = self.structured("work_status", {"repo": self.repo})
        self.assertEqual(status["claimableCount"], 1)
        self.assertEqual(status["ready"][0]["id"], "demo/W-1")
        self.assertEqual(status["claimed"], [])
        self.assertEqual(status["drift"], [])
        self.assertEqual(status["counts"]["ready"], 2)
        self.assertEqual(status["counts"]["draft"], 2)

        board_path = self.ws.repo / "docs" / "board.json"
        board = json.loads(board_path.read_text())
        next(packet for packet in board["packets"] if packet["id"] == "W-1")["status"] = "blocked"
        board_path.write_text(json.dumps(board, indent=2) + "\n")
        drift = self.structured("work_status", {"repo": self.repo})["drift"]
        self.assertEqual(drift[0]["task"], "demo/W-1")
        self.assertEqual(drift[0]["kind"], "imported")

    def test_list_filters_and_ready(self):
        all_tasks = self.structured("work_list", {"repo": self.repo})
        self.assertEqual(all_tasks["count"], 4)

        ready = self.structured("work_list", {"repo": self.repo, "ready": True})
        self.assertEqual([task["id"] for task in ready["tasks"]], ["demo/W-1"])
        self.assertFalse(next(task for task in all_tasks["tasks"]
                              if task["id"] == "demo/W-2")["claimable"])

        drafts = self.structured("work_list", {"repo": self.repo, "status": ["draft"]})
        self.assertEqual({task["id"] for task in drafts["tasks"]}, {"demo/W-3", "demo/W-4"})

        invalid = self.tool("work_list", {"status": "bogus"})
        self.assertTrue(invalid["isError"])
        self.assertIn("unknown status", invalid["content"][0]["text"])

    def test_get_includes_dependencies_and_events(self):
        task = self.structured("work_get", {"task": "demo/W-2"})
        self.assertEqual(task["status"], "ready")
        self.assertEqual(task["dependsOn"], ["demo/W-1"])
        self.assertFalse(task["claimable"])
        self.assertEqual(task["blockedBy"], ["demo/W-1"])
        self.assertEqual(task["revision"], 0)

        missing = self.tool("work_get", {"task": "demo/NOPE"})
        self.assertTrue(missing["isError"])
        self.assertIn("unknown task", missing["content"][0]["text"])

    # -- mutations --------------------------------------------------------

    def test_ask_inbox_answer_over_mcp(self):
        asked = self.structured("work_ask", {
            "task": "demo/W-1", "to": "mcp/other",
            "question": "Which fixture is authoritative?", "neededBy": "2026-11-07",
        })
        self.assertEqual(asked["to"], "mcp/other")

        inbox = self.structured("work_inbox", {}, agent="mcp/other")
        self.assertEqual(inbox["count"], 1)
        question = inbox["questions"][0]
        self.assertEqual(question["from"], "mcp/agent")
        self.assertEqual(question["neededBy"], "2026-11-07")

        answered = self.structured("work_answer", {
            "task": "demo/W-1", "question": question["seq"], "decision": "Use src/one",
        }, agent="mcp/other")
        self.assertIn("answered", answered["result"])

        empty = self.structured("work_inbox", {}, agent="mcp/other")
        self.assertEqual(empty["count"], 0)

        wrong = self.tool("work_answer", {
            "task": "demo/W-1", "question": question["seq"], "decision": "again",
        }, agent="mcp/third")
        self.assertTrue(wrong["isError"])
        self.assertIn("addressed to mcp/other", wrong["content"][0]["text"])

        self.structured("work_get", {"task": "demo/W-1"})

    def test_search_and_dependents(self):
        found = self.structured("work_search", {"terms": "demo/W-3"})
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["tasks"][0]["id"], "demo/W-3")

        missing = self.structured("work_search", {"terms": ["no-such-term"]})
        self.assertEqual(missing["count"], 0)

        blocks = self.structured("work_dependents", {"task": "demo/W-1"})
        self.assertEqual([item["id"] for item in blocks["direct"]], ["demo/W-2"])
        self.assertEqual(blocks["count"], 1)
        self.assertEqual(blocks["transitive"][0]["status"], "ready")

        detail = self.structured("work_get", {"task": "demo/W-1"})
        self.assertEqual(detail["dependents"], ["demo/W-2"])
        self.assertEqual(detail["unblocks"], 1)

    def test_status_reports_downstream_weight_and_human_queue(self):
        status = self.structured("work_status", {"repo": self.repo})
        ready = {item["id"]: item for item in status["ready"]}
        self.assertEqual(ready["demo/W-1"]["unblocks"], 1)
        self.assertEqual(status["awaitingHuman"], [])

        record = self.ws.registry["tasks"][0]
        record["status"] = "blocked"
        record["blocked_needs"] = "human"
        self.ws.save()
        waiting = self.structured("work_status", {"repo": self.repo})
        self.assertEqual([item["id"] for item in waiting["awaitingHuman"]], ["demo/W-1"])

    def test_claim_renew_release(self):
        claimed = self.structured("work_claim",
                                  {"task": "demo/W-1", "revision": 0, "ttl": "45m"})
        self.assertEqual(claimed["status"], "claimed")
        self.assertEqual(claimed["revision"], 1)
        self.assertEqual(claimed["claim"]["agent"], "mcp/agent")
        epoch = claimed["claim"]["epoch"]
        self.assertEqual(epoch, 1)

        conflict = self.tool("work_claim",
                             {"task": "demo/W-1", "revision": 1, "agent": "mcp/other"})
        self.assertTrue(conflict["isError"])
        self.assertIn("leased by mcp/agent", conflict["content"][0]["text"])

        renewed = self.structured("work_renew",
                                  {"task": "demo/W-1", "revision": 1, "ttl": "1h",
                                   "epoch": epoch})
        self.assertEqual(renewed["revision"], 2)
        self.assertIsNotNone(renewed["claim"]["lease_expires"])

        released = self.structured("work_release",
                                   {"task": "demo/W-1", "revision": 2, "reason": "handing back",
                                    "epoch": epoch})
        self.assertEqual(released["status"], "ready")
        self.assertEqual(released["revision"], 3)
        self.assertIsNone(released["claim"])

        events = self.structured("work_get", {"task": "demo/W-1"})["recentEvents"]
        self.assertEqual([event["event"] for event in events], ["claim", "renew", "release"])

    def review_source(self):
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
        return base, source

    def test_full_lifecycle_through_tools(self):
        claimed = self.structured("work_claim", {"task": "demo/W-1", "revision": 0})
        epoch = claimed["claim"]["epoch"]
        self.structured("work_transition",
                        {"task": "demo/W-1", "revision": 1, "to": "red", "epoch": epoch})
        self.structured("work_transition",
                        {"task": "demo/W-1", "revision": 2, "to": "green", "epoch": epoch})
        base, source = self.review_source()
        self.structured("work_ask", {
            "task": "demo/W-1", "to": "mcp/reviewer", "question": "Review source and evidence",
            "reviewSource": source, "reviewBase": base, "reviewEvidence": "evidence/W-1.md",
        })
        question = self.structured("work_inbox", {}, agent="mcp/reviewer")["questions"][0]
        self.assertEqual(question["reviewSource"], source)
        self.assertEqual(question["reviewBase"], base)
        self.assertEqual(question["reviewEvidence"], "evidence/W-1.md")
        self.assertEqual(question["revision"], 3)
        self.structured("work_answer", {
            "task": "demo/W-1", "question": question["seq"], "decision": "approve",
            "note": "Normal review. Checked behavior and focused tests.",
        }, agent="mcp/reviewer")
        answer = self.structured("work_get", {"task": "demo/W-1"})["recentEvents"][-1]
        self.assertEqual(answer["reviewSource"], source)
        self.assertEqual(answer["reviewBase"], base)
        self.assertEqual(answer["reviewEvidence"], "evidence/W-1.md")
        self.assertEqual(answer["decision"], "approve")
        reviewed = self.structured("work_transition", {
            "task": "demo/W-1", "revision": 3, "to": "reviewed", "epoch": epoch,
            "reviewer": "mcp/reviewer", "decision": "approve",
            "reviewAnswer": answer["seq"], "evidence": "evidence/W-1.md",
        })
        self.assertEqual(reviewed["status"], "reviewed")
        self.assertEqual(reviewed["review"]["reviewer"], "mcp/reviewer")

        integrated = self.structured("work_transition", {
            "task": "demo/W-1", "revision": 4, "to": "integrated",
            "evidence": "evidence/W-1.md", "integratedSource": source,
        })
        self.assertEqual(integrated["status"], "integrated")
        accepted = self.structured("work_transition", {
            "task": "demo/W-1", "revision": 5, "to": "accepted",
            "evidence": "evidence/W-1.md",
        })
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["revision"], 6)

    def test_mutation_requires_an_agent(self):
        result = self.tool("work_claim", {"task": "demo/W-1", "revision": 0}, agent=None)
        self.assertTrue(result["isError"])
        self.assertIn("agent identity is required", result["content"][0]["text"])

    def test_repo_inference_from_workspace_path(self):
        self.assertEqual(mcp.infer_repo(self.registry, self.ws.repo), "repo")
        self.assertEqual(mcp.infer_repo(self.registry, self.ws.repo / "docs" / "nested"), "repo")
        self.assertIsNone(mcp.infer_repo(self.registry, "/tmp"))

        env = {key: value for key, value in os.environ.items()
               if key not in {"WORK_REPO", "WORK_AGENT"}}
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "work_status", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "work_claim", "arguments": {"task": "demo/W-1", "revision": 0}}},
        ]
        process = subprocess.run(
            [sys.executable, str(SERVER), "--root", str(self.ws.root)],
            input="\n".join(json.dumps(message) for message in messages) + "\n",
            capture_output=True, text=True, cwd=self.ws.repo, env=env, timeout=20)
        lines = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
        status = lines[1]["result"]["structuredContent"]
        self.assertEqual(status["repo"], "repo")
        self.assertEqual([task["id"] for task in status["ready"]], ["demo/W-1"])
        claimed = lines[2]["result"]["structuredContent"]
        self.assertEqual(claimed["claim"]["agent"], "demo-agent")

    def test_default_repo_from_server_configuration(self):
        # handle_message's default repo is used when the tool argument is omitted.
        response = mcp.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "work_list", "arguments": {}}},
            self.registry, "mcp/agent", self.repo)
        result = response["result"]["structuredContent"]
        self.assertEqual(result["count"], 4)

    # -- transport --------------------------------------------------------

    def test_stdio_transport_is_newline_delimited(self):
        process = subprocess.Popen(
            [sys.executable, str(SERVER), "--root", str(self.ws.root),
             "--repo", self.repo, "--agent", "mcp/stdio-agent"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "work_status", "arguments": {}}},
            ]
            for message in messages:
                process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

            responses = []
            for _ in range(3):
                ready, _, _ = select.select([process.stdout], [], [], 5)
                self.assertTrue(ready, "timed out waiting for MCP response")
                line = process.stdout.readline()
                self.assertTrue(line.endswith("\n"))
                responses.append(json.loads(line))

            self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
            self.assertEqual(len(responses[1]["result"]["tools"]), 13)
            self.assertEqual(responses[2]["result"]["structuredContent"]["counts"]["ready"], 2)
        finally:
            process.terminate()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()

    def test_print_config_contains_absolute_paths(self):
        result = subprocess.run(
            [sys.executable, str(SERVER), "--root", str(self.ws.root),
             "--repo", "alpha", "--agent", "opencode/alpha-agent", "--print-config", "opencode"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        entry = document["mcp"]["work-coordination"]
        self.assertEqual(entry["type"], "local")
        self.assertIn(str(self.ws.root), entry["command"])
        self.assertIn("--agent", entry["command"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
