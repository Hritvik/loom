#!/usr/bin/env python3
"""MCP stdio server for the central work registry.

Exposes the coordination layer as tools so an agent discovers it from its tool list, before it
starts planning or editing a repository:

  work_status      ready work, active claims, expired leases, board drift
  work_list        filter tasks by repo/program/board/status
  work_get         one task with dependencies, claimability and recent events
  work_claim       compare-and-swap + lease
  work_renew       heartbeat
  work_release     return to the pool
  work_transition  red/green/reviewed/integrated/accepted and terminal states

Mutations delegate to `work/task`, so the CLI, A2A and MCP surfaces share one implementation of
CAS, leases, dependency gates, write-path conflicts and the hash-chained audit log.

Transport: newline-delimited JSON-RPC 2.0 over stdin/stdout. Logs go to stderr only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store import make_store  # noqa: E402 — sibling package, resolved from capabilities/work

WORK = Path(__file__).resolve().parents[1]
TASK_BIN = WORK / "task"
SERVER_NAME = "work-coordination"
SERVER_VERSION = "1.0.0"

# Newest first. Echo a requested version we know; otherwise advertise the widely supported one.
SUPPORTED_VERSIONS = ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
FALLBACK_VERSION = "2025-06-18"

DONE_STATUSES = {"accepted", "integrated", "done"}
ACTIVE_STATUSES = {"claimed", "red", "green"}
PATH_LOCK_STATUSES = {"claimed", "red"}  # green is finished work and holds no directory
STATUSES = [
    "draft", "planned", "ready", "claimed", "red", "green", "blocked", "reviewed",
    "integrated", "accepted", "rejected", "released", "in-progress", "done",
]


class ToolError(Exception):
    """A tool execution error the model can act on."""


# --------------------------------------------------------------------------
# Registry reads
# --------------------------------------------------------------------------


class Registry:
    def __init__(self, root: Path, backend: str = "sqlite", db: str | None = None):
        self.root = Path(root).expanduser()
        self.store = make_store(self.root, backend, db or os.environ.get("WORK_DB"))

    def has_registry(self) -> bool:
        return self.store.has_registry()

    def document(self) -> dict:
        return self.store.load()

    def tasks(self) -> list[dict]:
        return self.document().get("tasks", [])

    def boards(self) -> dict[str, dict]:
        return {entry["id"]: entry for entry in self.document().get("boards", [])}

    def find(self, task_id: str) -> dict:
        for task in self.tasks():
            if task.get("id") == task_id:
                return task
        raise ToolError(f"unknown task {task_id}; call work_list to discover ids")

    def edges(self) -> list[dict]:
        return self.document().get("edges", [])

    def events(self) -> list[dict]:
        return [record for _, record in self.store.read_events()]

    def events_for(self, task_id: str, limit: int = 5) -> list[dict]:
        return [event for event in self.events() if event.get("task") == task_id][-limit:]


def normalized_path(value: str) -> str:
    return str(value).strip().rstrip("/")


def paths_overlap(left: str, right: str) -> bool:
    a, b = normalized_path(left), normalized_path(right)
    return bool(a and b) and (a == b or a.startswith(b + "/") or b.startswith(a + "/"))


def dependencies(registry: Registry, task: dict) -> list[str]:
    deps = [edge["to"] for edge in registry.edges()
            if edge.get("kind", "depends-on") == "depends-on" and edge["from"] == task["id"]]
    for dep in task.get("depends_on", []) or []:
        if dep not in deps:
            deps.append(dep)
    return deps


def dependents_map(tasks: list[dict], edges: list[dict]) -> dict[str, list[str]]:
    """Reverse of the depends-on graph: task id -> ids that depend on it."""
    index: dict[str, list[str]] = {}
    for task in tasks:
        deps = [edge["to"] for edge in edges
                if edge.get("kind", "depends-on") == "depends-on" and edge["from"] == task["id"]]
        for dep in task.get("depends_on", []) or []:
            if dep not in deps:
                deps.append(dep)
        for dep in deps:
            index.setdefault(dep, []).append(task["id"])
    return index


def downstream(index: dict[str, list[str]], task_id: str) -> list[str]:
    seen: set[str] = set()
    stack = list(index.get(task_id, []))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(index.get(current, []))
    return sorted(seen)


def claimability(registry: Registry, task: dict, tasks: list[dict]) -> tuple[bool, list[str]]:
    by_id = {item["id"]: item for item in tasks}
    unmet = [dep for dep in dependencies(registry, task)
             if by_id.get(dep, {}).get("status") not in DONE_STATUSES]
    conflicts = []
    for other in tasks:
        if other["id"] == task["id"] or other.get("status") not in PATH_LOCK_STATUSES:
            continue
        for mine in task.get("write_paths", []) or []:
            for theirs in other.get("write_paths", []) or []:
                if paths_overlap(mine, theirs):
                    conflicts.append(other["id"])
                    break
    usable = task.get("status") == "ready" and not unmet and not conflicts
    return usable, unmet + [f"path:{item}" for item in sorted(set(conflicts))]


def summarize(registry: Registry, task: dict, tasks: list[dict],
              index: dict[str, list[str]] | None = None) -> dict:
    usable, blocked = claimability(registry, task, tasks)
    index = index if index is not None else dependents_map(tasks, registry.edges())
    direct = sorted(set(index.get(task["id"], [])))
    return {
        "id": task["id"],
        "title": task.get("title"),
        "status": task.get("status"),
        "revision": task.get("revision", 0),
        "board": task.get("board"),
        "program": task.get("program"),
        "ownerRepo": task.get("owner_repo"),
        "dependsOn": dependencies(registry, task),
        "dependents": direct,
        "unblocks": len(downstream(index, task["id"])),
        "writePaths": task.get("write_paths", []),
        "claim": task.get("claim"),
        "evidence": task.get("evidence"),
        "review": task.get("review"),
        "claimable": usable,
        "blockedBy": blocked,
    }


def known_repo_names(registry: Registry) -> set[str]:
    names = set()
    for task in registry.tasks():
        owner = task.get("owner_repo")
        if owner:
            names.add(Path(str(owner)).expanduser().name)
    for entry in registry.boards().values():
        repo = entry.get("repo")
        if repo:
            names.add(Path(str(repo)).expanduser().name)
    return names


def infer_repo(registry: Registry, start: Path | str | None = None) -> str | None:
    """Infer the repo default from the workspace path.

    Local MCP servers run with cwd set to the workspace, so an agent working in
    `.../repositories/iam/docs` gets `iam` without any per-repo configuration.
    """
    names = known_repo_names(registry)
    if not names:
        return None
    current = Path(start or Path.cwd()).expanduser()
    try:
        current = current.resolve()
    except OSError:
        return None
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if candidate.name in names:
            return candidate.name
    return None


def repo_matches(task: dict, repo: str | None) -> bool:
    if not repo:
        return True
    owner = str(task.get("owner_repo") or "")
    target = str(Path(repo).expanduser())
    return (owner == target
            or Path(owner).name.casefold() == Path(target).name.casefold())


def infer_agent(registry: Registry, repo: str | None) -> str | None:
    """Prefer the program namespace over the directory name.

    `~/repositories/gym-tracker` is the MyFitnessGuide repo, so the default
    identity is `myfitnessguide-agent`, not `gym-tracker-agent`.
    """
    if not repo:
        return None
    programs = Counter(
        task["program"] for task in registry.tasks()
        if task.get("program") and repo_matches(task, repo))
    if programs:
        return f"{programs.most_common(1)[0][0]}-agent"
    return f"{Path(repo).expanduser().name.casefold()}-agent"


def filter_tasks(tasks: list[dict], repo=None, program=None, board=None) -> list[dict]:
    selected = tasks
    if repo:
        selected = [task for task in selected if repo_matches(task, repo)]
    if program:
        selected = [task for task in selected if task.get("program") == program]
    if board:
        selected = [task for task in selected if task.get("board") == board]
    return selected


def board_drift(registry: Registry, tasks: list[dict], boards: dict[str, dict]) -> list[dict]:
    drift = []
    cache: dict[str, dict] = {}
    for task in tasks:
        entry = boards.get(task.get("board"))
        if not entry or entry.get("kind") != "board":
            continue
        board_id = entry["id"]
        if board_id not in cache:
            path = Path(entry["repo"]).expanduser() / entry["path"]
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                cache[board_id] = {packet["id"]: packet for packet in data.get("packets", [])}
            except (OSError, json.JSONDecodeError):
                cache[board_id] = {}
        packet = cache[board_id].get(task["id"].split("/", 1)[-1])
        if packet is None:
            drift.append({"task": task["id"], "kind": "missing-packet", "board": board_id})
        elif packet.get("status") != task.get("status"):
            kind = "imported" if int(task.get("revision", 0)) == 0 else "stale"
            drift.append({
                "task": task["id"],
                "kind": kind,
                "board": board_id,
                "registryStatus": task.get("status"),
                "boardStatus": packet.get("status"),
            })
    return drift


# --------------------------------------------------------------------------
# Mutations
# --------------------------------------------------------------------------


def run_cli(root: Path, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(TASK_BIN), "--root", str(root), *args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip().removeprefix("error:").strip()
        raise ToolError(message)
    return result.stdout.strip()


def mutation_agent(arguments: dict, default_agent: str | None) -> str:
    agent = arguments.get("agent") or default_agent
    if not agent:
        raise ToolError(
            "agent identity is required for mutations; pass `agent` or start the server with "
            "--agent / WORK_AGENT")
    return str(agent)


# --------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------


def tool_status(registry: Registry, arguments: dict, default_repo: str | None) -> dict:
    repo = arguments.get("repo") or default_repo
    program = arguments.get("program")
    board = arguments.get("board")
    tasks = registry.tasks()
    scoped = filter_tasks(tasks, repo, program, board)
    boards = registry.boards()
    index = dependents_map(tasks, registry.edges())
    now = dt.datetime.now(dt.timezone.utc)
    ready = []
    claimed = []
    expired = []
    awaiting_human = []
    for task in scoped:
        usable, blocked = claimability(registry, task, tasks)
        if usable:
            ready.append({"id": task["id"], "title": task.get("title"),
                          "revision": task.get("revision", 0),
                          "unblocks": len(downstream(index, task["id"]))})
        if task.get("status") == "blocked" and (task.get("blocked_needs") or "agent") == "human":
            awaiting_human.append({"id": task["id"], "title": task.get("title"),
                                   "revision": task.get("revision", 0)})
        claim = task.get("claim") or {}
        if task.get("status") in ACTIVE_STATUSES and claim:
            try:
                expiry = dt.datetime.fromisoformat(
                    str(claim.get("lease_expires")).replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=dt.timezone.utc)
            except (TypeError, ValueError):
                expiry = None
            entry = {
                "id": task["id"],
                "agent": claim.get("agent"),
                "leaseExpires": claim.get("lease_expires"),
                "revision": task.get("revision", 0),
                "expired": bool(expiry and expiry <= now),
            }
            claimed.append(entry)
            if entry["expired"]:
                expired.append(entry)
    counts = Counter(task.get("status") for task in scoped)
    return {
        "repo": repo,
        "program": program,
        "board": board,
        "counts": dict(sorted(counts.items())),
        "ready": ready,
        "claimed": claimed,
        "expiredLeases": expired,
        "awaitingHuman": awaiting_human,
        "drift": board_drift(registry, scoped, boards),
        "events": len(registry.events()),
        "claimableCount": len(ready),
    }


def tool_list(registry: Registry, arguments: dict, default_repo: str | None) -> dict:
    repo = arguments.get("repo") or default_repo
    program = arguments.get("program")
    board = arguments.get("board")
    wanted = arguments.get("status")
    if isinstance(wanted, str):
        wanted = [wanted]
    if wanted:
        for value in wanted:
            if value not in STATUSES:
                raise ToolError(f"unknown status {value!r}; choose from {', '.join(STATUSES)}")
    tasks = registry.tasks()
    scoped = filter_tasks(tasks, repo, program, board)
    if wanted:
        scoped = [task for task in scoped if task.get("status") in wanted]
    index = dependents_map(tasks, registry.edges())
    summaries = [summarize(registry, task, tasks, index) for task in scoped]
    if arguments.get("ready"):
        summaries = [item for item in summaries if item["claimable"]]
        summaries.sort(key=lambda item: (-item["unblocks"], item["id"]))
    limit = arguments.get("limit", 50)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ToolError("limit must be a positive integer")
    limit = min(limit, 200)
    return {"count": len(summaries), "tasks": summaries[:limit]}


def tool_get(registry: Registry, arguments: dict, default_repo: str | None) -> dict:
    task_id = arguments.get("task")
    if not task_id:
        raise ToolError("task is required")
    task = registry.find(str(task_id))
    document = summarize(registry, task, registry.tasks())
    document["goal"] = task.get("goal")
    document["acceptance"] = task.get("acceptance", [])
    document["statusSource"] = task.get("status_source", "registry")
    document["recentEvents"] = []
    for event in registry.events_for(task["id"]):
        summary = {key: event.get(key) for key in
                   ("seq", "ts", "event", "status", "revision", "agent", "reason", "lease_expires")}
        if event.get("review_source"):
            summary.update(reviewSource=event["review_source"],
                           reviewBase=event.get("review_base"),
                           reviewEvidence=event.get("review_evidence"))
            if event["event"] == "answer":
                summary.update(decision=event["decision"], note=event["note"],
                               inReplyTo=event["in_reply_to"])
        document["recentEvents"].append(summary)
    return document


def tool_claim(registry: Registry, arguments: dict, default_agent: str | None) -> dict:
    task_id = arguments.get("task")
    revision = arguments.get("revision")
    if not task_id or not isinstance(revision, int) or isinstance(revision, bool):
        raise ToolError("task and integer revision are required")
    agent = mutation_agent(arguments, default_agent)
    args = ["claim", str(task_id), "--revision", str(revision), "--agent", agent]
    if arguments.get("ttl"):
        args += ["--ttl", str(arguments["ttl"])]
    run_cli(registry.root, *args)
    task = registry.find(str(task_id))
    return summarize(registry, task, registry.tasks())


def tool_renew(registry: Registry, arguments: dict, default_agent: str | None) -> dict:
    task_id = arguments.get("task")
    revision = arguments.get("revision")
    if not task_id or not isinstance(revision, int) or isinstance(revision, bool):
        raise ToolError("task and integer revision are required")
    agent = mutation_agent(arguments, default_agent)
    args = ["renew", str(task_id), "--revision", str(revision), "--agent", agent]
    if arguments.get("ttl"):
        args += ["--ttl", str(arguments["ttl"])]
    run_cli(registry.root, *args)
    task = registry.find(str(task_id))
    return summarize(registry, task, registry.tasks())


def tool_release(registry: Registry, arguments: dict, default_agent: str | None) -> dict:
    task_id = arguments.get("task")
    revision = arguments.get("revision")
    reason = arguments.get("reason")
    if not task_id or not isinstance(revision, int) or isinstance(revision, bool):
        raise ToolError("task and integer revision are required")
    if not reason:
        raise ToolError("reason is required")
    agent = mutation_agent(arguments, default_agent)
    run_cli(registry.root, "release", str(task_id), "--revision", str(revision),
            "--reason", str(reason), "--agent", agent)
    task = registry.find(str(task_id))
    return summarize(registry, task, registry.tasks())


def tool_transition(registry: Registry, arguments: dict, default_agent: str | None) -> dict:
    task_id = arguments.get("task")
    revision = arguments.get("revision")
    target = arguments.get("to")
    if not task_id or not isinstance(revision, int) or isinstance(revision, bool) or not target:
        raise ToolError("task, integer revision and to are required")
    if target not in STATUSES:
        raise ToolError(f"unknown target {target!r}")
    agent = mutation_agent(arguments, default_agent)
    args = ["transition", str(task_id), "--revision", str(revision), "--to", str(target),
            "--agent", agent]
    for key, flag in (("evidence", "--evidence"), ("reviewer", "--reviewer"),
                      ("decision", "--decision"), ("severity", "--severity"),
                      ("reason", "--reason"), ("reviewAnswer", "--review-answer"),
                      ("integratedSource", "--integrated-source")):
        if arguments.get(key):
            args += [flag, str(arguments[key])]
    run_cli(registry.root, *args)
    task = registry.find(str(task_id))
    return summarize(registry, task, registry.tasks())


# --------------------------------------------------------------------------
# MCP surface
# --------------------------------------------------------------------------

TOOL_DEFS = [
    {
        "name": "work_status",
        "title": "Work status",
        "description": (
            "Call this before planning or claiming work in a repository. Returns ready/claimable "
            "tasks, active claims with lease expiry, expired leases, and registry-to-board drift "
            "for the repo, program or board. This is the entry point to the central coordination "
            "registry shared by IAM, UMS and Dutch."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "repo": {"type": "string", "description": "Repository name or path, e.g. iam, ums, dutch"},
                "program": {"type": "string", "description": "Program namespace, e.g. iam, ums, dutch-ref"},
                "board": {"type": "string", "description": "Board id, e.g. iam.w09"},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "work_list",
        "title": "List central work",
        "description": (
            "List centrally authored tasks filtered by repo/program/board/status. Set ready=true "
            "to see only tasks that can be claimed now (dependencies met, no path conflict). Use "
            "this instead of inventing a task list when planning."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "repo": {"type": "string"},
                "program": {"type": "string"},
                "board": {"type": "string"},
                "status": {
                    "oneOf": [
                        {"type": "string", "enum": STATUSES},
                        {"type": "array", "items": {"type": "string", "enum": STATUSES}},
                    ]
                },
                "ready": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "work_get",
        "title": "Get a central task",
        "description": (
            "Read one task: status, revision, dependencies, write paths, claim, evidence, review, "
            "claimability and recent audit events. Read the revision before claiming."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task"],
            "properties": {"task": {"type": "string", "description": "Namespaced id, e.g. iam/W-12"}},
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "work_claim",
        "title": "Claim work with a lease",
        "description": (
            "Claim a ready task with compare-and-swap on revision and a time-boxed lease. Fails if "
            "the revision is stale, another agent holds a live lease, dependencies are unmet, or "
            "write paths overlap another active claim. Renew before the lease expires."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task", "revision"],
            "properties": {
                "task": {"type": "string"},
                "revision": {"type": "integer", "minimum": 0},
                "ttl": {"type": "string", "description": "Lease duration, e.g. 30m, 2h"},
                "agent": {"type": "string", "description": "Agent identity; defaults to server --agent"},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "work_renew",
        "title": "Renew a lease",
        "description": (
            "Heartbeat and extend the lease on a task you own. Fails after expiry; use the CLI "
            "takeover flow only after a recorded expiry."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task", "revision"],
            "properties": {
                "task": {"type": "string"},
                "revision": {"type": "integer", "minimum": 0},
                "ttl": {"type": "string"},
                "agent": {"type": "string"},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "work_release",
        "title": "Release claimed work",
        "description": (
            "Return a task you own to the ready pool with a recorded reason so another agent can "
            "pick it up. Uses compare-and-swap on revision."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task", "revision", "reason"],
            "properties": {
                "task": {"type": "string"},
                "revision": {"type": "integer", "minimum": 0},
                "reason": {"type": "string", "minLength": 1},
                "agent": {"type": "string"},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "work_transition",
        "title": "Move work through its lifecycle",
        "description": (
            "Transition a task: red/green for the implementation loop, reviewed with an independent "
            "reviewer and their approval event, integrated/accepted with the approved source and "
            "evidence, blocked/rejected with a reason. Illegal "
            "transitions and missing evidence/review are refused."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task", "revision", "to"],
            "properties": {
                "task": {"type": "string"},
                "revision": {"type": "integer", "minimum": 0},
                "to": {"type": "string", "enum": STATUSES},
                "evidence": {"type": "string"},
                "reviewer": {"type": "string"},
                "reviewAnswer": {"type": "integer", "minimum": 1,
                                 "description": "Approval answer event sequence"},
                "integratedSource": {"type": "string", "description": "Full approved Git commit"},
                "decision": {"type": "string", "enum": ["approve", "reject"]},
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
                "reason": {"type": "string"},
                "agent": {"type": "string"},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "work_takeover",
        "title": "Take over an expired lease",
        "description": (
            "Take over a task whose lease has expired, with a recorded reason. Refused while the "
            "lease is live; the previous agent is recorded in the audit log."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task", "revision", "reason"],
            "properties": {
                "task": {"type": "string"},
                "revision": {"type": "integer", "minimum": 0},
                "reason": {"type": "string"},
                "ttl": {"type": "string"},
                "agent": {"type": "string"},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "work_search",
        "title": "Find work by question",
        "description": (
            "Search central tasks by text across id, title, goal, program, board and write paths, "
            "so you do not need to know the namespace first. Use this when the question is "
            "'where is the avatar work?' rather than 'what is in ums?'."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["terms"],
            "properties": {
                "terms": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    ]
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "work_dependents",
        "title": "Who is waiting on this task",
        "description": (
            "List the tasks blocked by a task, directly and transitively, with their status. Use "
            "this before choosing what to do: a task that unblocks four others outranks one that "
            "unblocks none."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task"],
            "properties": {"task": {"type": "string"}},
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "work_inbox",
        "title": "Read your open questions",
        "description": (
            "List questions other agents asked you on the audit log that are still unanswered, "
            "with the task, asker and needed-by date. Call this when starting work or when a task "
            "is blocked on a decision."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "task": {"type": "string"},
                "all": {"type": "boolean"},
                "agent": {"type": "string"},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "work_ask",
        "title": "Ask an agent a question about a task",
        "description": (
            "Ask the owner of a task (or any agent) a question that blocks progress. The question "
            "is appended to the hash-chained audit log and appears in their inbox; it does not "
            "change task state or revision. Include reviewSource to submit a revision for independent review."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task", "to", "question"],
            "properties": {
                "task": {"type": "string"},
                "to": {"type": "string", "description": "Agent identity expected to answer"},
                "question": {"type": "string"},
                "neededBy": {"type": "string", "description": "ISO date the answer is needed by"},
                "reviewSource": {"type": "string", "description": "Full Git commit for review"},
                "reviewBase": {"type": "string", "description": "Full base Git commit for review"},
                "reviewEvidence": {"type": "string", "description": "Evidence locator recorded with the review submission"},
                "agent": {"type": "string"},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "work_answer",
        "title": "Answer a question on a task",
        "description": (
            "Answer a question addressed to you, recording the decision on the audit log. Only the "
            "addressee may answer and only once; the answer is linked to the question event."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task", "question", "decision"],
            "properties": {
                "task": {"type": "string"},
                "question": {"type": "integer", "description": "seq of the ask event"},
                "decision": {"type": "string"},
                "note": {"type": "string"},
                "agent": {"type": "string"},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
]

TOOL_HANDLERS = {
    "work_status": lambda registry, args, agent, repo: tool_status(registry, args, repo),
    "work_list": lambda registry, args, agent, repo: tool_list(registry, args, repo),
    "work_get": lambda registry, args, agent, repo: tool_get(registry, args, repo),
    "work_claim": lambda registry, args, agent, repo: tool_claim(registry, args, agent),
    "work_renew": lambda registry, args, agent, repo: tool_renew(registry, args, agent),
    "work_release": lambda registry, args, agent, repo: tool_release(registry, args, agent),
    "work_transition": lambda registry, args, agent, repo: tool_transition(registry, args, agent),
    "work_takeover": lambda registry, args, agent, repo: tool_takeover(registry, args, agent),
    "work_inbox": lambda registry, args, agent, repo: tool_inbox(registry, args, agent),
    "work_search": lambda registry, args, agent, repo: tool_search(registry, args, agent),
    "work_dependents": lambda registry, args, agent, repo: tool_dependents(registry, args, agent),
    "work_ask": lambda registry, args, agent, repo: tool_ask(registry, args, agent),
    "work_answer": lambda registry, args, agent, repo: tool_answer(registry, args, agent),
}


def tool_takeover(registry: Registry, arguments: dict, default_agent: str | None) -> dict:
    task_id = arguments.get("task")
    revision = arguments.get("revision")
    reason = arguments.get("reason")
    if not task_id or not isinstance(revision, int) or isinstance(revision, bool):
        raise ToolError("task and integer revision are required")
    if not reason:
        raise ToolError("reason is required")
    agent = mutation_agent(arguments, default_agent)
    args = ["takeover", str(task_id), "--revision", str(revision), "--reason", str(reason),
            "--agent", agent]
    if arguments.get("ttl"):
        args += ["--ttl", str(arguments["ttl"])]
    run_cli(registry.root, *args)
    task = registry.find(str(task_id))
    return summarize(registry, task, registry.tasks())


def tool_inbox(registry: Registry, arguments: dict, default_agent: str | None) -> dict:
    agent = mutation_agent(arguments, default_agent)
    args = ["inbox", "--agent", agent, "--json"]
    if arguments.get("task"):
        args += ["--task", str(arguments["task"])]
    if arguments.get("all"):
        args.append("--all")
    output = run_cli(registry.root, *args)
    questions = json.loads(output) if output.startswith("[") else []
    return {"agent": agent, "count": len(questions), "questions": questions}


def tool_ask(registry: Registry, arguments: dict, default_agent: str | None) -> dict:
    task_id = arguments.get("task")
    target = arguments.get("to")
    question = arguments.get("question")
    if not task_id or not target or not question:
        raise ToolError("task, to and question are required")
    agent = mutation_agent(arguments, default_agent)
    args = ["ask", str(task_id), "--to", str(target), "--question", str(question),
            "--agent", agent]
    if arguments.get("neededBy"):
        args += ["--needed-by", str(arguments["neededBy"])]
    for key, flag in (("reviewSource", "--review-source"), ("reviewBase", "--review-base"),
                      ("reviewEvidence", "--review-evidence")):
        if arguments.get(key):
            args += [flag, str(arguments[key])]
    output = run_cli(registry.root, *args)
    return {"task": str(task_id), "to": str(target), "result": output}


def tool_answer(registry: Registry, arguments: dict, default_agent: str | None) -> dict:
    task_id = arguments.get("task")
    question = arguments.get("question")
    decision = arguments.get("decision")
    if not task_id or not isinstance(question, int) or isinstance(question, bool) or not decision:
        raise ToolError("task, integer question seq and decision are required")
    agent = mutation_agent(arguments, default_agent)
    args = ["answer", str(task_id), "--question", str(question), "--decision", str(decision),
            "--agent", agent]
    if arguments.get("note"):
        args += ["--note", str(arguments["note"])]
    output = run_cli(registry.root, *args)
    return {"task": str(task_id), "question": question, "result": output}


def tool_search(registry: Registry, arguments: dict, default_agent: str | None) -> dict:
    terms = arguments.get("terms") or arguments.get("query")
    if isinstance(terms, str):
        terms = [terms]
    if not terms:
        raise ToolError("terms are required, e.g. terms: ['avatar']")
    args = ["search", *[str(term) for term in terms], "--json"]
    if arguments.get("limit"):
        args += ["--limit", str(arguments["limit"])]
    output = run_cli(registry.root, *args)
    tasks = json.loads(output) if output.startswith("[") else []
    all_tasks = registry.tasks()
    index = dependents_map(all_tasks, registry.edges())
    return {"count": len(tasks),
            "tasks": [summarize(registry, task, all_tasks, index) for task in tasks]}


def tool_dependents(registry: Registry, arguments: dict, default_agent: str | None) -> dict:
    task_id = arguments.get("task")
    if not task_id:
        raise ToolError("task is required")
    task = registry.find(str(task_id))
    tasks = registry.tasks()
    index = dependents_map(tasks, registry.edges())
    by_id = {item["id"]: item for item in tasks}
    direct = sorted(set(index.get(task["id"], [])))

    def brief(dep_id: str) -> dict:
        other = by_id.get(dep_id, {})
        return {"id": dep_id, "status": other.get("status"), "title": other.get("title")}

    transitive = downstream(index, task["id"])
    return {"task": task["id"], "count": len(transitive),
            "direct": [brief(dep) for dep in direct],
            "transitive": [brief(dep) for dep in transitive]}


def tool_result(structured: dict, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(structured, indent=2)}],
        "structuredContent": structured,
        "isError": is_error,
    }


def rpc_error(request_id, code: int, message: str, data=None) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def negotiate_version(requested: str | None) -> str:
    if requested in SUPPORTED_VERSIONS:
        return requested
    return FALLBACK_VERSION


def handle_message(message: dict, registry: Registry, default_agent: str | None,
                   default_repo: str | None):
    """Return a response dict, or None for notifications."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return rpc_error(None, -32600, "not a JSON-RPC 2.0 message")
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        # Notification: initialized, cancelled, progress. Nothing to answer.
        return None
    params = message.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": negotiate_version(params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Central work coordination for IAM, UMS and Dutch. Before planning or editing "
                    "a repository, call work_status to see ready work, active claims and board "
                    "drift. Read work_get for the revision, then work_claim. Never invent task ids "
                    "or edit another agent's claim. Task bodies stay in the owning repository."),
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOL_DEFS}}
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOL_HANDLERS:
            return rpc_error(request_id, -32602, f"Unknown tool: {name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return rpc_error(request_id, -32602, "arguments must be an object")
        try:
            result = TOOL_HANDLERS[name](registry, arguments, default_agent, default_repo)
            return {"jsonrpc": "2.0", "id": request_id, "result": tool_result(result)}
        except ToolError as exc:
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": tool_result({"error": str(exc)}, is_error=True)}
        except Exception as exc:  # pragma: no cover - defensive
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": tool_result({"error": f"internal error: {exc}"}, is_error=True)}
    return rpc_error(request_id, -32601, f"Method not found: {method}")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def print_config(client: str, root: Path, repo: str | None, agent: str | None) -> None:
    command = [sys.executable, str(Path(__file__).resolve())]
    args = ["--root", str(root)]
    if repo:
        args += ["--repo", repo]
    if agent:
        args += ["--agent", agent]
    if client == "opencode":
        document = {"mcp": {"work-coordination": {
            "type": "local",
            "command": command + args,
            "enabled": True,
        }}}
        print(json.dumps(document, indent=2))
    elif client == "claude":
        document = {"mcpServers": {"work-coordination": {
            "command": command[0],
            "args": command[1:] + args,
        }}}
        print(json.dumps(document, indent=2))
    elif client == "codex":
        quoted = ", ".join(json.dumps(item) for item in command[1:] + args)
        print("[mcp_servers.work-coordination]")
        print("command = " + json.dumps(command[0]))
        print(f"args = [{quoted}]")
    else:
        raise SystemExit(f"unknown client {client!r}; choose opencode, claude or codex")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCP stdio server for the work registry.")
    parser.add_argument("--root", default=str(WORK), help="work home directory")
    parser.add_argument("--repo", default=os.environ.get("WORK_REPO"),
                        help="default repo filter for work_status/work_list")
    parser.add_argument("--agent", default=os.environ.get("WORK_AGENT"),
                        help="default agent identity for mutations")
    parser.add_argument("--print-config", choices=["opencode", "claude", "codex"],
                        help="print a client config snippet and exit")
    parser.add_argument("--store", choices=["sqlite"], default=os.environ.get("WORK_STORE", "sqlite"))
    parser.add_argument("--db", default=os.environ.get("WORK_DB"),
                        help="sqlite database path (default ~/.local/state/loom/work.db)")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser()
    if args.print_config:
        print_config(args.print_config, root, args.repo, args.agent)
        return 0

    registry = Registry(root, args.store, args.db)
    if not registry.has_registry():
        print(f"error: no registry in {registry.store.describe()}", file=sys.stderr)
        return 2
    default_repo = args.repo or infer_repo(registry)
    default_agent = args.agent or infer_agent(registry, default_repo)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = rpc_error(None, -32700, f"parse error: {exc}")
        else:
            response = handle_message(message, registry, default_agent, default_repo)
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
