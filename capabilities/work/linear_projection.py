"""Project coordination state into a board view without reading or changing live state."""
from __future__ import annotations

import datetime as dt
import re
import runpy
from collections import defaultdict
from pathlib import Path

# The CLI file defines the canonical graph and lease rules. Loading its definitions
# under a non-main name avoids opening a registry or executing a command.
_WORK = runpy.run_path(str(Path(__file__).with_name("task")))
_REVIEW_FIELDS = ("review_source", "review_base", "review_evidence")


def _implementer(task: dict, history: list[dict]) -> str | None:
    owner = _WORK["claim_owner"](task)
    if owner:
        return owner
    return next((
        entry["agent"] for entry in reversed(history)
        if entry.get("agent") and (
            entry.get("event") in {"claim", "renew", "takeover"}
            or (entry.get("event") == "transition" and entry.get("status") == "green")
        )
    ), None)


def _review_answer(request: dict, history: list[dict]) -> dict:
    return next((
        entry for entry in reversed(history)
        if entry.get("event") == "answer"
        and entry.get("in_reply_to") == request.get("seq")
        and entry.get("agent") == request.get("to")
        and entry.get("status") == request.get("status") == "green"
        and entry.get("revision") == request.get("revision")
        and entry.get("decision") in {"approve", "changes requested"}
        and all(entry.get(key) == request.get(key) for key in _REVIEW_FIELDS)
        and (entry.get("note") or "").strip()
    ), {})


def _last_rejection(history: list[dict]) -> tuple[dict, dict]:
    requests = {
        entry["seq"]: entry for entry in history
        if entry.get("event") == "ask" and entry.get("status") == "green"
        and (entry.get("review_source") or re.search(r"\breview\b", entry.get("question") or "", re.I))
    }
    for entry in reversed(history):
        if entry.get("event") != "answer" or entry.get("decision") != "changes requested":
            continue
        request = requests.get(entry.get("in_reply_to"))
        if request and _review_answer(request, [entry]):
            return request, entry
    return {}, {}


def _new_review_candidate(request: dict, rejected_request: dict, rejection: dict) -> bool:
    if not request or request["seq"] <= rejection["seq"]:
        return False
    candidate = request["review_source"]
    rejected_source = rejected_request.get("review_source")
    if not rejected_source:
        # Legacy review requests identified the candidate in prose. Only use a
        # recognizable commit prefix to compare candidates, never export the prose.
        match = re.search(r"\b[0-9a-f]{7,64}\b", rejected_request.get("question") or "", re.I)
        rejected_source = match.group().lower() if match else None
    if rejected_source:
        return not candidate.startswith(rejected_source)
    return request["revision"] != rejected_request["revision"]


def _review_state(task: dict, history: list[dict], implementer: str | None) -> tuple[str, str, str | None]:
    request = _WORK["latest_review_request"](task, history)
    answer = _review_answer(request, history) if request else {}
    rejected_request, rejection = _last_rejection(history)
    if rejection and not _new_review_candidate(request, rejected_request, rejection):
        return "changes_requested", implementer or "implementer", rejection.get("agent")
    if not request:
        return "needs_review", "coordinator", None
    reviewer = request["to"]
    return "in_review", "coordinator" if answer.get("decision") == "approve" else reviewer, reviewer


def _lane(
    task: dict, history: list[dict], implementer: str | None, active: bool, blockers: list[str],
) -> tuple[str, str | None, str | None]:
    state = task.get("status")
    review = task.get("review") or {}
    reviewer = review.get("reviewer")
    if state in _WORK["DEP_SATISFIED"]:
        return "integrated", None, reviewer
    if state == "reviewed":
        if review.get("source") and review.get("answer") and review.get("decision") == "approve":
            return "awaiting_integration", implementer or "integrator", reviewer
        blockers.append("Independent review receipt required")
        return "needs_review", "coordinator", reviewer
    if state == "green":
        return _review_state(task, history, implementer)
    if active:
        return "implementing", implementer or "implementer", None
    if state == "ready" and not blockers:
        return "claimable", "implementer", None
    if state in {"claimed", "red"} and task.get("claim"):
        blockers.append("Claim expired")
    if state == "blocked":
        blockers.append("Human decision required" if task.get("blocked_needs") == "human" else "Task marked blocked")
    if not blockers:
        blockers.append(f"Task is {state or 'unavailable'}")
    return "blocked", "human" if state == "blocked" and task.get("blocked_needs") == "human" else "coordinator", None


def _focus_ids(tasks: dict, dependencies: dict, focus: str | None) -> set[str]:
    if focus is None:
        return set(tasks)
    if focus not in tasks:
        raise ValueError(f"unknown focus task {focus}")
    selected = set()
    pending = [focus]
    while pending:
        task_id = pending.pop()
        if task_id in selected or task_id not in tasks:
            continue
        selected.add(task_id)
        pending.extend(dependencies[task_id])
    return selected


def project_cards(
    registry: dict, events: list[dict], now: dt.datetime, focus: str | None = None,
) -> list[dict]:
    """Return sanitized cards for the board or a focus task and all hard prerequisites."""
    tasks = {task["id"]: task for task in registry["tasks"]}
    dependencies = {task_id: _WORK["task_edges"](registry, task_id) for task_id in tasks}
    reverse = _WORK["dependents_index"](registry)
    active = {task_id: task for task_id, task in tasks.items() if _WORK["active_claim"](task, now)}
    history = defaultdict(list)
    for entry in events:
        history[entry.get("task")].append(entry)

    cards = []
    for task_id in sorted(_focus_ids(tasks, dependencies, focus)):
        task = tasks[task_id]
        state = task.get("status")
        blockers = [
            dependency for dependency in dependencies[task_id]
            if tasks.get(dependency, {}).get("status") not in _WORK["DEP_SATISFIED"]
        ] if state not in _WORK["DEP_SATISFIED"] else []
        if state == "ready":
            for other_id, other in active.items():
                if other_id != task_id and any(
                    _WORK["paths_overlap"](left, right)
                    for left in task.get("write_paths") or []
                    for right in other.get("write_paths") or []
                ) and other_id not in blockers:
                    blockers.append(other_id)
        implementer = _implementer(task, history[task_id])
        lane, next_actor, reviewer = _lane(task, history[task_id], implementer, task_id in active, blockers)
        program = task.get("program") or task_id.partition("/")[0]
        cards.append({
            "id": task_id, "title": task.get("title") or task_id,
            "lane": lane, "program": program, "owner": program,
            "next_actor": next_actor, "implementer": implementer, "reviewer": reviewer,
            "dependencies": dependencies[task_id], "blockers": blockers,
            "dependents": _WORK["transitive_dependents"](reverse, task_id),
            "revision": int(task.get("revision", 0)),
        })
    return cards
