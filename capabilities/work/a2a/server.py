#!/usr/bin/env python3
"""Thin A2A v1.0 façade over the central work registry.

Discovery:  GET  /.well-known/agent-card.json
Transport:  POST /  (JSON-RPC 2.0, protocolBinding JSONRPC, A2A-Version 1.0)

Supported operations:
  ListTasks, GetTask, SendMessage, CancelTask

Everything else is refused with the A2A capability error that matches the Agent
Card (streaming/push/extended card are all false).

Mutations are delegated to the `task` CLI, so CAS, leases, dependency gates,
write-path conflicts and the hash-chained audit log behave identically over A2A
and over the command line. The server binds loopback by default and declares no
authentication scheme: do not expose it beyond a trusted host without adding
one.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store import make_store  # noqa: E402 — sibling package, resolved from capabilities/work

WORK = Path(__file__).resolve().parents[1]
TASK_BIN = WORK / "task"
A2A_VERSION = "1.0"

TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002
PUSH_NOT_SUPPORTED = -32003
UNSUPPORTED_OPERATION = -32004
VERSION_NOT_SUPPORTED = -32009
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

STATE_MAP = {
    "draft": "TASK_STATE_SUBMITTED",
    "planned": "TASK_STATE_SUBMITTED",
    "ready": "TASK_STATE_SUBMITTED",
    "claimed": "TASK_STATE_WORKING",
    "red": "TASK_STATE_WORKING",
    "green": "TASK_STATE_WORKING",
    "reviewed": "TASK_STATE_WORKING",
    "blocked": "TASK_STATE_INPUT_REQUIRED",
    "in-progress": "TASK_STATE_WORKING",
    "integrated": "TASK_STATE_WORKING",
    "accepted": "TASK_STATE_COMPLETED",
    "done": "TASK_STATE_COMPLETED",
    "rejected": "TASK_STATE_REJECTED",
    "released": "TASK_STATE_CANCELED",
}
TERMINAL_STATES = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED",
                   "TASK_STATE_REJECTED"}
DONE_STATUSES = {"accepted", "integrated", "done"}
ACTIVE_STATUSES = {"claimed", "red", "green"}
# Every A2A action maps to exactly one MCP tool, so the two surfaces cannot drift apart.
ACTION_TO_TOOL = {
    "claim": "work_claim",
    "renew": "work_renew",
    "release": "work_release",
    "takeover": "work_takeover",
    "transition": "work_transition",
    "ask": "work_ask",
    "answer": "work_answer",
}
MESSAGE_ACTIONS = {"ask", "answer"}
MUTATING_ACTIONS = set(ACTION_TO_TOOL) - MESSAGE_ACTIONS
# Extension: A2A has no inbox primitive, so messages are read through this method.
MESSAGE_LIST_METHOD = "messages/list"

METHOD_ALIASES = {
    "tasks/list": "ListTasks",
    "tasks/get": "GetTask",
    "message/send": "SendMessage",
    "tasks/send": "SendMessage",
    "tasks/cancel": "CancelTask",
    "tasks/resubscribe": "SubscribeToTask",
}


class RpcError(Exception):
    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def timestamp(value, fallback=None) -> str:
    if not value:
        return fallback or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return str(value)


def parse_timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


class WorkHome:
    def __init__(self, root: Path, task_bin: Path = TASK_BIN, db: str | None = None):
        self.root = Path(root).expanduser()
        self.db = db or os.environ.get("WORK_DB")
        self.task_bin = Path(task_bin)

    # -- reads -----------------------------------------------------------
    # SQLite connections are thread-bound and this server is threaded, so each read opens its own.

    def store(self):
        return make_store(self.root, "sqlite", self.db)

    def registry(self) -> dict:
        store = self.store()
        try:
            return store.load()
        finally:
            store.close()

    def tasks(self) -> list[dict]:
        return self.registry().get("tasks", [])

    def find(self, task_id: str) -> dict:
        for task in self.tasks():
            if task.get("id") == task_id:
                return task
        raise RpcError(TASK_NOT_FOUND, f"task {task_id} does not exist")

    def events(self) -> list[dict]:
        store = self.store()
        try:
            return [record for _, record in store.read_events()]
        finally:
            store.close()

    def events_for(self, task_id: str) -> list[dict]:
        return [event for event in self.events() if event.get("task") == task_id]

    # -- writes ----------------------------------------------------------

    def run_cli(self, *args: str) -> str:
        import subprocess

        result = subprocess.run(
            [sys.executable, str(self.task_bin), "--root", str(self.root), *args],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            message = message.removeprefix("error:").strip()
            if "unknown task" in message:
                raise RpcError(TASK_NOT_FOUND, message)
            raise RpcError(INVALID_PARAMS, message)
        return result.stdout.strip()


def paths_overlap(left: str, right: str) -> bool:
    a, b = left.strip().rstrip("/"), right.strip().rstrip("/")
    return bool(a and b) and (a == b or a.startswith(b + "/") or b.startswith(a + "/"))


def claimability(home: WorkHome, task: dict) -> tuple[bool, list[str]]:
    """Mirror the CLI's claim gate so A2A clients can filter honestly."""
    tasks = home.tasks()
    by_id = {item["id"]: item for item in tasks}
    dependencies = [edge["to"] for edge in home.registry().get("edges", [])
                    if edge.get("kind", "depends-on") == "depends-on" and edge["from"] == task["id"]]
    dependencies += [dep for dep in task.get("depends_on", []) or [] if dep not in dependencies]
    unmet = [dep for dep in dependencies
             if by_id.get(dep, {}).get("status") not in DONE_STATUSES]
    conflicts = []
    for other in tasks:
        if other["id"] == task["id"] or other.get("status") not in ACTIVE_STATUSES:
            continue
        for mine in task.get("write_paths", []) or []:
            for theirs in other.get("write_paths", []) or []:
                if paths_overlap(mine, theirs):
                    conflicts.append(other["id"])
                    break
    usable = task.get("status") == "ready" and not unmet and not conflicts
    return usable, unmet + [f"path:{item}" for item in sorted(set(conflicts))]


def task_to_a2a(home: WorkHome, task: dict, history_length: int = 0,
                include_artifacts: bool = False) -> dict:
    usable, blocked_by = claimability(home, task)
    status = {
        "state": STATE_MAP.get(task.get("status"), "TASK_STATE_UNSPECIFIED"),
        "timestamp": timestamp(task.get("updated_at") or task.get("created_at")),
    }
    metadata = {
        "internalStatus": task.get("status"),
        "revision": task.get("revision", 0),
        "board": task.get("board"),
        "program": task.get("program"),
        "ownerRepo": task.get("owner_repo"),
        "dependsOn": task.get("depends_on", []),
        "writePaths": task.get("write_paths", []),
        "claim": task.get("claim"),
        "evidence": task.get("evidence"),
        "review": task.get("review"),
        "statusSource": task.get("status_source", "registry"),
        "claimable": usable,
        "blockedBy": blocked_by,
    }
    document = {
        "id": task["id"],
        "contextId": task.get("program") or task.get("board"),
        "status": status,
        "metadata": metadata,
    }
    if include_artifacts and task.get("evidence"):
        document["artifacts"] = [{
            "artifactId": f"{task['id']}-evidence",
            "name": "Evidence",
            "parts": [{"text": task["evidence"]}],
        }]
    if history_length > 0:
        events = home.events_for(task["id"])[-history_length:]
        document["history"] = [event_to_message(task["id"], event) for event in events]
    return document


def event_to_message(task_id: str, event: dict) -> dict:
    summary = event.get("event", "event")
    detail = event.get("status") or event.get("previous_status") or ""
    text = f"{summary} {detail} at revision {event.get('revision', '?')} by {event.get('agent', 'unknown')}"
    if event.get("reason"):
        text += f": {event['reason']}"
    metadata = {"event": summary, "seq": event.get("seq")}
    if event.get("review_source"):
        metadata.update(reviewSource=event["review_source"], revision=event["revision"],
                        reviewBase=event.get("review_base"),
                        reviewEvidence=event.get("review_evidence"))
        if summary == "answer":
            metadata.update(decision=event["decision"], note=event["note"],
                            inReplyTo=event["in_reply_to"], agent=event["agent"])
    return {
        "kind": "message",
        "messageId": f"{task_id}:event:{event.get('seq')}",
        "role": "ROLE_AGENT",
        "parts": [{"text": text}],
        "metadata": metadata,
    }


def usage_message() -> dict:
    return {
        "kind": "message",
        "messageId": f"msg-{uuid.uuid4()}",
        "role": "ROLE_AGENT",
        "parts": [{
            "text": (
                "Work-coordination skill. Send a data part with "
                '{"action":"claim|renew|release|takeover|transition", ...} and an existing '
                "taskId, plus metadata.agent. Use ListTasks to discover task ids. "
                "This agent does not create tasks; author new work with the central CLI."
            )
        }],
    }


def event_message(text: str, agent: str = "work-coordinator") -> dict:
    return {
        "kind": "message",
        "messageId": f"msg-{uuid.uuid4()}",
        "role": "ROLE_AGENT",
        "parts": [{"text": text}],
        "metadata": {"agent": agent},
    }


def encode_page(offset: int) -> str:
    payload = json.dumps({"offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_page(token: str) -> int:
    if not token:
        return 0
    padded = token + "=" * (-len(token) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
        offset = int(payload["offset"])
        if offset < 0:
            raise ValueError
        return offset
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise RpcError(INVALID_PARAMS, "invalid pageToken")


def caller_agent(params: dict, message: dict, headers) -> str | None:
    for container in (message.get("metadata"), params.get("metadata")):
        if isinstance(container, dict):
            for key in ("agent", "agentId", "caller"):
                if container.get(key):
                    return str(container[key])
    return headers.get("X-Agent-Id") if headers else None


def extract_action(message: dict) -> dict:
    for part in message.get("parts", []) or []:
        if isinstance(part, dict) and isinstance(part.get("data"), dict):
            if "action" in part["data"]:
                return part["data"]
        text = part.get("text") if isinstance(part, dict) else None
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "action" in parsed:
                return parsed
    return {}


class Dispatcher:
    def __init__(self, home: WorkHome):
        self.home = home

    def dispatch(self, method: str, params: dict, agent: str | None) -> dict:
        method = METHOD_ALIASES.get(method, method)
        if method == "ListTasks":
            return self.list_tasks(params)
        if method == "GetTask":
            return self.get_task(params)
        if method == "SendMessage":
            return self.send_message(params, agent)
        if method == "CancelTask":
            return self.cancel_task(params, agent)
        if method in {MESSAGE_LIST_METHOD, "messages/list"}:
            return self.list_messages(params, agent)
        if method in {"SubscribeToTask", "SendStreamingMessage", "message/stream",
                      "GetExtendedAgentCard"}:
            raise RpcError(UNSUPPORTED_OPERATION, f"{method} is not supported by this agent")
        if "PushNotification" in method:
            raise RpcError(PUSH_NOT_SUPPORTED, "push notifications are not supported")
        raise RpcError(METHOD_NOT_FOUND, f"method {method} not found")

    # -- reads -----------------------------------------------------------

    def list_tasks(self, params: dict) -> dict:
        context_id = params.get("contextId")
        wanted = params.get("status")
        if isinstance(wanted, str):
            wanted = [wanted]
        statuses = None
        if wanted:
            statuses = set()
            for value in wanted:
                mapped = STATE_MAP.get(value, value)
                if mapped not in STATE_MAP.values():
                    raise RpcError(INVALID_PARAMS, f"unknown status {value!r}")
                statuses.add(mapped)
        after = params.get("statusTimestampAfter")
        after_moment = None
        if after:
            try:
                after_moment = parse_timestamp(after)
            except (TypeError, ValueError):
                raise RpcError(INVALID_PARAMS, "invalid statusTimestampAfter")

        tasks = self.home.tasks()
        if context_id:
            tasks = [task for task in tasks if task.get("program") == context_id]
        if statuses:
            tasks = [task for task in tasks
                     if STATE_MAP.get(task.get("status")) in statuses]
        if after_moment:
            def newer(task):
                stamp = task.get("updated_at") or task.get("created_at")
                try:
                    return parse_timestamp(stamp) > after_moment
                except (TypeError, ValueError):
                    return False
            tasks = [task for task in tasks if newer(task)]

        def sort_key(task):
            try:
                return parse_timestamp(task.get("updated_at") or task.get("created_at"))
            except (TypeError, ValueError):
                return dt.datetime.min.replace(tzinfo=dt.timezone.utc)

        tasks.sort(key=sort_key, reverse=True)
        page_size = params.get("pageSize", 50)
        if not isinstance(page_size, int) or isinstance(page_size, bool):
            raise RpcError(INVALID_PARAMS, "pageSize must be an integer")
        page_size = max(1, min(100, page_size))
        offset = decode_page(params.get("pageToken") or "")
        page = tasks[offset:offset + page_size]
        history_length = params.get("historyLength", 0)
        if not isinstance(history_length, int) or isinstance(history_length, bool) or history_length < 0:
            raise RpcError(INVALID_PARAMS, "historyLength must be a non-negative integer")
        include_artifacts = bool(params.get("includeArtifacts", False))
        next_token = encode_page(offset + page_size) if offset + page_size < len(tasks) else ""
        return {
            "tasks": [task_to_a2a(self.home, task, history_length, include_artifacts)
                      for task in page],
            "nextPageToken": next_token,
        }

    def get_task(self, params: dict) -> dict:
        task_id = params.get("id") or params.get("taskId")
        if not task_id:
            raise RpcError(INVALID_PARAMS, "GetTask requires id")
        history_length = params.get("historyLength", 0)
        if not isinstance(history_length, int) or isinstance(history_length, bool) or history_length < 0:
            raise RpcError(INVALID_PARAMS, "historyLength must be a non-negative integer")
        task = self.home.find(str(task_id))
        return task_to_a2a(self.home, task, history_length, include_artifacts=True)

    # -- writes ----------------------------------------------------------

    def send_message(self, params: dict, agent: str | None) -> dict:
        message = params.get("message")
        if not isinstance(message, dict):
            raise RpcError(INVALID_PARAMS, "SendMessage requires a message object")
        message_agent = caller_agent(params, message, None)
        agent = message_agent or agent
        task_id = message.get("taskId")
        if not task_id:
            return usage_message()
        task = self.home.find(str(task_id))
        action = extract_action(message)
        operation = str(action.get("action", "")).lower() if action else ""
        if operation not in MESSAGE_ACTIONS and STATE_MAP.get(task.get("status")) in TERMINAL_STATES:
            raise RpcError(UNSUPPORTED_OPERATION,
                           f"{task['id']} is terminal ({task.get('status')}); start a new task")
        if not action:
            return event_message(
                f"{task['id']} is at revision {task.get('revision', 0)} with status "
                f"{task.get('status')}. Send an action data part to mutate it.")
        if not agent:
            raise RpcError(INVALID_PARAMS, "metadata.agent is required for mutating actions")
        if operation in MESSAGE_ACTIONS:
            return self.message_action(operation, action, task, agent, params)
        if operation not in MUTATING_ACTIONS:
            raise RpcError(INVALID_PARAMS, f"unknown action {operation!r}")
        revision = action.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise RpcError(INVALID_PARAMS, "action requires an integer revision (compare-and-swap)")

        args = [operation, task["id"], "--revision", str(revision), "--agent", agent]
        if operation in {"claim", "renew", "takeover"}:
            if action.get("ttl"):
                args += ["--ttl", str(action["ttl"])]
        if operation == "release":
            if not action.get("reason"):
                raise RpcError(INVALID_PARAMS, "release requires a reason")
            args += ["--reason", str(action["reason"])]
        if operation == "takeover":
            if not action.get("reason"):
                raise RpcError(INVALID_PARAMS, "takeover requires a reason")
            args += ["--reason", str(action["reason"])]
        if operation == "transition":
            target = action.get("to")
            if not target:
                raise RpcError(INVALID_PARAMS, "transition requires a target state")
            args += ["--to", str(target)]
            for key, flag in (("evidence", "--evidence"), ("severity", "--severity"),
                              ("reason", "--reason"), ("reviewAnswer", "--review-answer"),
                              ("integratedSource", "--integrated-source")):
                if action.get(key):
                    args += [flag, str(action[key])]
            if target == "reviewed":
                if action.get("reviewer"):
                    args += ["--reviewer", str(action["reviewer"])]
                args += ["--decision", str(action.get("decision") or "approve")]
            else:
                if action.get("reviewer"):
                    args += ["--reviewer", str(action["reviewer"])]
                if action.get("decision"):
                    args += ["--decision", str(action["decision"])]

        self.home.run_cli(*args)
        return self._updated_task(params, task["id"])

    def message_action(self, operation: str, action: dict, task: dict, agent: str,
                       params: dict) -> dict:
        if operation == "ask":
            target = action.get("to")
            question = action.get("question")
            if not target or not question:
                raise RpcError(INVALID_PARAMS, "ask requires to and question")
            args = ["ask", task["id"], "--to", str(target), "--question", str(question),
                    "--agent", agent]
            if action.get("neededBy"):
                args += ["--needed-by", str(action["neededBy"])]
            for key, flag in (("reviewSource", "--review-source"), ("reviewBase", "--review-base"),
                              ("reviewEvidence", "--review-evidence")):
                if action.get(key):
                    args += [flag, str(action[key])]
        else:
            seq = action.get("question")
            decision = action.get("decision")
            if not isinstance(seq, int) or isinstance(seq, bool) or not decision:
                raise RpcError(INVALID_PARAMS,
                               "answer requires an integer question seq and a decision")
            args = ["answer", task["id"], "--question", str(seq), "--decision", str(decision),
                    "--agent", agent]
            if action.get("note"):
                args += ["--note", str(action["note"])]
        self.home.run_cli(*args)
        return self._updated_task(params, task["id"])

    def list_messages(self, params: dict, agent: str | None) -> dict:
        metadata = params.get("metadata") or {}
        agent = metadata.get("agent") or params.get("agent") or agent
        if not agent:
            raise RpcError(INVALID_PARAMS, "messages/list requires metadata.agent")
        args = ["inbox", "--agent", str(agent), "--json"]
        if params.get("taskId"):
            args += ["--task", str(params["taskId"])]
        if params.get("includeAnswered"):
            args.append("--all")
        output = self.home.run_cli(*args)
        messages = json.loads(output) if output.startswith("[") else []
        return {"agent": str(agent), "messages": messages, "count": len(messages)}

    def _updated_task(self, params: dict, task_id: str) -> dict:
        updated = self.home.find(task_id)
        configuration = params.get("configuration") or {}
        history_length = configuration.get("historyLength", 0)
        if not isinstance(history_length, int) or isinstance(history_length, bool):
            history_length = 0
        return task_to_a2a(self.home, updated, history_length, include_artifacts=True)

    def cancel_task(self, params: dict, agent: str | None) -> dict:
        task_id = params.get("id") or params.get("taskId")
        if not task_id:
            raise RpcError(INVALID_PARAMS, "CancelTask requires id")
        task = self.home.find(str(task_id))
        if task.get("status") not in {"ready", "claimed", "red", "green"}:
            raise RpcError(TASK_NOT_CANCELABLE,
                           f"{task['id']} is {task.get('status')} and cannot be canceled")
        metadata = params.get("metadata") or {}
        agent = metadata.get("agent") or metadata.get("agentId") or agent
        if not agent:
            raise RpcError(INVALID_PARAMS, "metadata.agent is required to cancel a task")
        reason = params.get("reason") or "canceled via A2A"
        self.home.run_cli("transition", task["id"], "--revision", str(task.get("revision", 0)),
                          "--to", "released", "--reason", reason, "--agent", agent)
        updated = self.home.find(task["id"])
        return task_to_a2a(self.home, updated, 0, include_artifacts=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WorkCoordinationA2A/1.0"

    # -- HTTP verbs ------------------------------------------------------

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in {"/.well-known/agent-card.json", "/.well-known/agent.json"}:
            card = self.server.agent_card()
            body = json.dumps(card, indent=2).encode()
            etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "max-age=300")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.respond(body, cache="max-age=300", etag=etag)
            return
        if path == "/health":
            self.respond(json.dumps({"status": "ok"}).encode())
            return
        self.respond(json.dumps({"error": "not found"}).encode(), status=404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in {"/", "/rpc", "/a2a/v1"}:
            self.respond(json.dumps({"error": "not found"}).encode(), status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 1_000_000:
            self.respond(json.dumps(rpc_error(None, INVALID_REQUEST, "invalid body length")).encode(),
                         status=400)
            return
        raw = self.rfile.read(length)
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            self.respond(json.dumps(rpc_error(None, -32700, "parse error")).encode(), status=400)
            return
        if not isinstance(request, dict):
            self.respond(json.dumps(rpc_error(None, INVALID_REQUEST, "batch requests are not supported")).encode())
            return

        request_id = request.get("id")
        version = self.headers.get("A2A-Version") or request.get("version") or ""
        if version not in {"", A2A_VERSION}:
            self.respond(json.dumps(rpc_error(
                request_id, VERSION_NOT_SUPPORTED,
                f"only A2A {A2A_VERSION} is supported, received {version}")).encode())
            return
        if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            self.respond(json.dumps(rpc_error(request_id, INVALID_REQUEST, "not a JSON-RPC 2.0 request")).encode())
            return
        params = request.get("params") or {}
        if not isinstance(params, dict):
            self.respond(json.dumps(rpc_error(request_id, INVALID_PARAMS, "params must be an object")).encode())
            return
        try:
            result = self.server.dispatcher.dispatch(
                request["method"], params, self.headers.get("X-Agent-Id"))
            payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except RpcError as exc:
            payload = rpc_error(request_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            payload = rpc_error(request_id, INTERNAL_ERROR, f"internal error: {exc}")
        self.respond(json.dumps(payload).encode())

    # -- helpers ---------------------------------------------------------

    def respond(self, body: bytes, status: int = 200, cache: str | None = None,
                etag: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", cache)
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, fmt, *args):  # keep the server quiet during tests
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)


def rpc_error(request_id, code: int, message: str, data=None) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


class WorkCoordinationServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, root: Path, task_bin: Path, verbose: bool = False):
        self.home = WorkHome(root, task_bin)
        self.dispatcher = Dispatcher(self.home)
        self.verbose = verbose
        super().__init__(address, Handler)
        host, port = self.server_address[:2]
        advertised = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        self.base_url = f"http://{advertised}:{port}"

    def agent_card(self) -> dict:
        return {
            "protocolVersion": A2A_VERSION,
            "name": "Distracted Work Coordination",
            "description": (
                "Central coordination for a portfolio of repositories. Discover claimable tasks, "
                "claim them with a lease, renew or release, take over expired leases, and move "
                "work through review to acceptance."
            ),
            "url": f"{self.base_url}/",
            "supportedInterfaces": [{
                "url": f"{self.base_url}/",
                "protocolBinding": "JSONRPC",
                "protocolVersion": A2A_VERSION,
            }],
            "version": "1.0.0",
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "extendedAgentCard": False,
            },
            "defaultInputModes": ["application/json"],
            "defaultOutputModes": ["application/json"],
            "skills": [{
                "id": "work-coordination",
                "name": "Portfolio work coordination",
                "description": (
                    "List central tasks and read one; act on a task with "
                    + ", ".join(ACTION_TO_TOOL)
                    + ". Every action uses compare-and-swap, leases and the hash-chained audit "
                    "log. Task bodies stay in the owning repository."
                ),
                "tags": ["work", "claims", "leases", "coordination", "iam", "ums", "dutch"],
                "examples": [
                    "ListTasks with contextId iam and status TASK_STATE_SUBMITTED",
                    "SendMessage to iam/W-12 with data {action: claim, revision: 0, ttl: 45m}",
                    "SendMessage to iam/W-12 with data {action: ask, to: iam-agent, question: ...}",
                ],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            }],
        }


def make_server(root: Path, host: str = "127.0.0.1", port: int = 8787,
                task_bin: Path = TASK_BIN, verbose: bool = False) -> WorkCoordinationServer:
    return WorkCoordinationServer((host, port), Path(root), Path(task_bin), verbose)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A2A v1.0 façade over the work registry.")
    parser.add_argument("--root", default=str(WORK))
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (loopback only by default)")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--task", default=str(TASK_BIN), help="path to the task CLI")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    server = make_server(Path(args.root), args.host, args.port, Path(args.task), args.verbose)
    print(f"A2A agent card: {server.base_url}/.well-known/agent-card.json")
    print(f"JSON-RPC endpoint: {server.base_url}/")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("WARNING: no authentication is implemented; do not expose this server publicly.",
              file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
