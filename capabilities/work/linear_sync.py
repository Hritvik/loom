#!/usr/bin/env python3
"""One-way portfolio work registry mirror. Default mode is a local preview."""

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import sys
from urllib.parse import urlsplit
import uuid


WORK_HOME = Path(__file__).resolve().parent
END_MARKER = "End work registry mirror."
MARKER_PATTERN = re.compile(r"^Work registry ID: `([a-z][a-z0-9-]+/[A-Za-z0-9._-]+)`$", re.MULTILINE)
API_KEY_VAR = "LINEAR_API_KEY"
LANES = {
    "claimable": "Claimable",
    "implementing": "Implementing",
    "needs_review": "Needs Review Submission",
    "in_review": "In Review",
    "changes_requested": "Changes Requested",
    "awaiting_integration": "Awaiting Integration",
    "integrated": "Integrated",
    "blocked": "Blocked",
}


class SyncError(Exception):
    """A safe, user-facing sync error."""


def marker(task_id):
    return f"Work registry ID: `{task_id}`"


def issue_id(config, task_id):
    # Linear accepts caller-provided UUID v4 IDs. A stable ID makes retries safe
    # even when the result of an earlier issueCreate was lost.
    namespace = f"portfolio-work-registry:{config['team_id']}:{config['project_id']}:{task_id}"
    return str(uuid.UUID(bytes=hashlib.sha256(namespace.encode()).digest()[:16], version=4))


def ordered_cards(cards):
    by_id = {card["id"]: card for card in cards}
    if len(by_id) != len(cards):
        raise SyncError("duplicate work IDs in the projection")
    visiting = set()
    visited = set()
    ordered = []

    def visit(task_id):
        if task_id in visiting:
            raise SyncError(f"dependency cycle at {task_id}")
        if task_id not in by_id or task_id in visited:
            return
        visiting.add(task_id)
        card = by_id[task_id]
        for dep in card["dependencies"]:
            visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)
        ordered.append(card)

    for task_id in sorted(by_id):
        visit(task_id)
    return ordered


def inline(value):
    return "`" + str(value or "Unassigned").replace("`", "").replace("\n", " ") + "`"


def dependency_link(task_id, issues):
    url = issues.get(task_id, {}).get("url") or ""
    if any(char in url for char in "[]() \r\n"):
        return inline(task_id)
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.netloc == "linear.app":
        return f"[`{task_id}`]({url})"
    return inline(task_id)


def managed_description(card, issues):
    rows = [
        marker(card["id"]),
        "",
        f"- Product: {inline(card['owner'])}",
        f"- Runway: {inline(LANES[card['lane']])}",
        f"- Next actor: {inline(card['next_actor'] or ('Complete' if card['lane'] == 'integrated' else None))}",
        f"- Implementer: {inline(card.get('implementer'))}",
        f"- Reviewer: {inline(card['reviewer'])}",
        f"- Registry revision: {card['revision']}",
    ]
    for label, key in (("Prerequisites", "dependencies"), ("Open blockers", "blockers"),
                       ("Unlocks", "dependents")):
        values = card[key]
        if values:
            rendered = (dependency_link(value, issues) if key != "dependents" else inline(value)
                        for value in values)
            rows.append(f"- {label}: " + ", ".join(rendered))
    return "\n".join([*rows, "", END_MARKER])


def index_issues(rows, desired_ids):
    indexed = {}
    for issue in rows:
        description = issue.get("description") or ""
        markers = list(MARKER_PATTERN.finditer(description))
        if len(markers) > 1 or (markers and (
            description.count(END_MARKER) != 1 or description.find(END_MARKER) < markers[0].end()
        )):
            raise SyncError("a Linear card has a malformed work-registry marker")
        task_id = markers[0][1] if markers else None
        if issue["id"] in desired_ids and task_id != desired_ids[issue["id"]]:
            raise SyncError("a managed Linear card has a mismatched work-registry marker")
        if task_id is None:
            continue
        if task_id in indexed:
            raise SyncError(f"multiple Linear cards match {task_id}")
        indexed[task_id] = issue
    return indexed


def merge_description(existing, managed):
    if not existing:
        return managed
    start = MARKER_PATTERN.search(existing).start()
    end = existing.index(END_MARKER, start) + len(END_MARKER)
    return existing[:start] + managed + existing[end:]


def mirror(client, config, destination, cards, *, apply):
    ordered = ordered_cards(cards)
    states = {}
    for lane in {card["lane"] for card in ordered}:
        name = config["states"].get(lane)
        if not name or name not in destination["state_ids"]:
            raise SyncError(f"Linear workflow state missing for {lane}: {name or 'unmapped'}")
        states[lane] = destination["state_ids"][name]

    desired_ids = {issue_id(config, card["id"]): card["id"] for card in ordered}
    issues = index_issues(client.issues(config["team_id"], config["project_id"]), desired_ids)
    for card in ordered:
        if issues.get(card["id"], {}).get("archivedAt"):
            raise SyncError(f"{card['id']} is archived in Linear; restore it before syncing")

    result = {"created": 0, "updated": 0, "unchanged": 0}
    for card in ordered:
        task_id = card["id"]
        issue = issues.get(task_id)
        fields = {
            "title": f"[{task_id}] {card['title']}"[:250],
            "description": merge_description(
                issue.get("description") if issue else None, managed_description(card, issues),
            ),
            "stateId": states[card["lane"]],
        }
        if not issue:
            if apply:
                issues[task_id] = client.create_issue({
                    **fields, "id": issue_id(config, task_id),
                    "teamId": config["team_id"], "projectId": config["project_id"],
                })
            result["created"] += 1
            continue
        changed = {key: value for key, value in fields.items() if issue.get(key) != value}
        if changed and apply:
            fresh = client.get_issue(issue["id"], config["team_id"], config["project_id"])
            current = index_issues([fresh], {fresh["id"]: task_id})[task_id]
            if current.get("archivedAt"):
                raise SyncError(f"{task_id} was archived during the sync")
            fields["description"] = merge_description(current["description"], managed_description(card, issues))
            changed = {key: value for key, value in fields.items() if current.get(key) != value}
        if changed:
            if apply:
                issues[task_id] = client.update_issue(issue["id"], changed)
            result["updated"] += 1
        else:
            result["unchanged"] += 1
    return result


def load_config(path):
    config = json.loads(path.read_text())
    if not isinstance(config, dict):
        raise SyncError("Linear config must be an object")
    for field in ("team_id", "project_id"):
        try:
            config[field] = str(uuid.UUID(config[field]))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise SyncError(f"Linear config needs a valid {field} UUID") from exc
    states = config.get("states", {})
    if not isinstance(states, dict) or any(
        key not in LANES or not isinstance(value, str) or not value.strip() for key, value in states.items()
    ):
        raise SyncError("Linear states must map lane keys to workflow state names")
    config["states"] = LANES | states
    return config


def snapshot(focus):
    from linear_projection import project_cards

    cli = runpy.run_path(str(WORK_HOME / "task"))
    store = cli["make_store"](WORK_HOME, "sqlite", os.environ.get("WORK_DB"))
    with store.lock():
        registry = store.load()
        records = store.read_events()
        if cli["verify_event_chain"](records) or cli["verify_registry_events"](registry, records):
            raise SyncError("work registry audit validation failed; mirror left unchanged")
    return project_cards(registry, [event for _, event in records], dt.datetime.now(dt.timezone.utc), focus)


def sync_target(config_path, focus, apply):
    from linear_client import LinearClient, LinearError

    config = load_config(config_path)
    credential = os.environ.get(API_KEY_VAR, "").strip()
    if not credential:
        raise SyncError(f"set {API_KEY_VAR} in the runner's secret environment")
    # Serialize runs using this config, independently of registry writes.
    lock_path = config_path.with_suffix(config_path.suffix + ".lock")
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SyncError("a Linear sync using this config is already running") from exc
        cards = snapshot(focus)
        try:
            client = LinearClient(credential)
            destination = client.destination(config["team_id"], config["project_id"])
            counts = mirror(client, config, destination, cards, apply=apply)
        except LinearError as exc:
            raise SyncError(str(exc)) from exc
    return {"mode": "apply" if apply else "preview", "destination": destination["project_name"], **counts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--focus", help="work ID and transitive prerequisites (default dutch/S7)")
    scope.add_argument("--all", action="store_true", help="include every centrally registered task")
    parser.add_argument("--config", type=Path, help="local Linear destination config")
    parser.add_argument("--apply", action="store_true", help="apply create/update operations to Linear")
    args = parser.parse_args(argv)
    if args.apply and not args.config:
        parser.error("--apply requires --config")
    try:
        focus = None if args.all else args.focus or "dutch/S7"
        if args.config:
            result = sync_target(args.config.expanduser().resolve(), focus, args.apply)
        else:
            result = {"mode": "local preview", "cards": ordered_cards(snapshot(focus))}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (SyncError, OSError, ValueError) as exc:
        print(f"Linear sync: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
