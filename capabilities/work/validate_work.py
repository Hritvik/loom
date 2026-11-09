#!/usr/bin/env python3
"""Validate the portfolio work-coordination registry.

Boards remain authoritative for local packet state. This script checks task schema conformance,
cross-repo identity, status drift, dependency edges and readiness, leases, shared-artifact claims,
write-path overlaps and the hash-chained event log.

Usage: python3 validate_work.py [--root DIR]
Standard library + PyYAML; no jsonschema dependency.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import make_store  # noqa: E402 — sibling module

STATUS_ACTIVE = {"claimed", "red", "green"}
STATUS_DONE = {"accepted", "integrated", "done"}
STATUSES = [
    "draft", "planned", "ready", "claimed", "red", "green", "blocked", "reviewed",
    "integrated", "accepted", "rejected", "released", "in-progress", "done",
]


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


# --------------------------------------------------------------------------
# Minimal JSON Schema subset validator (draft 2020-12 features we use)
# --------------------------------------------------------------------------


def type_matches(value, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    return True


def validate_schema(value, schema: dict, path: str = "$") -> list[str]:
    import re

    errors: list[str] = []
    declared = schema.get("type")
    if declared is not None:
        names = declared if isinstance(declared, list) else [declared]
        if not any(type_matches(value, name) for name in names):
            return [f"{path}: expected {' or '.join(names)}, got {type(value).__name__}"]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
        if schema.get("format") == "date-time":
            try:
                parse_time(value)
            except (TypeError, ValueError):
                errors.append(f"{path}: invalid date-time {value!r}")

    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")

    if isinstance(value, list):
        if schema.get("uniqueItems"):
            rendered = [json.dumps(item, sort_keys=True) for item in value]
            if len(rendered) != len(set(rendered)):
                errors.append(f"{path}: duplicate items")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(validate_schema(item, schema["items"], f"{path}[{index}]"))

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}: unexpected property {key!r}")
        for key, subschema in properties.items():
            if key in value:
                errors.extend(validate_schema(value[key], subschema, f"{path}.{key}"))

    return errors


# --------------------------------------------------------------------------
# Repository text parsing
# --------------------------------------------------------------------------


def heading_id(line: str) -> str | None:
    if not line.startswith("## ") or " — " not in line:
        return None
    return line[3:].split(" — ", 1)[0].strip()


def program_task_ids(text: str) -> set[str]:
    return {heading_id(line) for line in text.splitlines() if heading_id(line)}


def program_statuses(text: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        heading = heading_id(line)
        if heading:
            current = heading
        elif current and line.startswith("**Status.**"):
            body = line.lower()
            if "complete and independently accepted" in body or "accepted" in body:
                statuses[current] = "accepted"
            elif "active" in body:
                statuses[current] = "in-progress"
            elif "blocked" in body:
                statuses[current] = "blocked"
            elif "rejected" in body:
                statuses[current] = "rejected"
            elif "ready" in body:
                statuses[current] = "ready"
            else:
                statuses[current] = "planned"
    return statuses


def paths_overlap(left: str, right: str) -> bool:
    a, b = left.strip().rstrip("/"), right.strip().rstrip("/")
    return bool(a and b) and (a == b or a.startswith(b + "/") or b.startswith(a + "/"))


# --------------------------------------------------------------------------
# Event log
# --------------------------------------------------------------------------


def verify_event_chain(records: list[tuple[bytes, dict]]) -> list[str]:
    import hashlib

    errors = []
    previous_raw = None
    last_ts = None
    for index, (raw, record) in enumerate(records, start=1):
        if record.get("seq") != index:
            errors.append(f"event line {index}: seq is {record.get('seq')}")
        expected = hashlib.sha256(previous_raw).hexdigest() if previous_raw else "0" * 64
        if record.get("prev") != expected:
            errors.append(f"event line {index}: prev hash mismatch (log edited or truncated)")
        try:
            moment = parse_time(record.get("ts"))
            if last_ts and moment < last_ts:
                errors.append(f"event line {index}: timestamp moved backwards")
            last_ts = moment
        except (TypeError, ValueError):
            errors.append(f"event line {index}: invalid timestamp {record.get('ts')!r}")
        previous_raw = raw
    return errors


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def cross_check_capabilities(root: Path, explicit: str | None, errors: list[str],
                             warnings: list[str]) -> dict:
    """Cross-check the capability-steward registry that sits beside the work home.

    The two registries describe different things (capabilities vs work items), but a steward entry
    pointing at a missing artifact, an owner that is not a registered effort, or an owner that is
    dormant is drift that no single registry can see.
    """
    home = Path(explicit).expanduser() if explicit else root.parent
    registry_path = home / "registry.yaml"
    if not registry_path.is_file():
        if explicit:
            errors.append(f"capability registry: {registry_path} not found")
        return {}
    if not explicit and not (home / "capability-home.yaml").is_file():
        return {}

    try:
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        errors.append(f"capability registry: invalid YAML ({exc})")
        return {}

    efforts = registry.get("efforts") or []
    effort_ids = {entry.get("id") for entry in efforts if entry.get("id")}
    dormant = {entry.get("id") for entry in efforts if entry.get("status") == "dormant"}
    unowned = 0
    for section in ("efforts", "contracts", "decisions"):
        for entry in registry.get(section) or []:
            label = f"capability registry: {section}/{entry.get('id')}"
            artifact = entry.get("path")
            if artifact and not (home / artifact).is_file():
                errors.append(f"{label}: artifact missing at capabilities/{artifact}")
            owner = entry.get("owner")
            if owner and owner != "unassigned" and owner not in effort_ids:
                warnings.append(f"{label}: owner {owner!r} is not a registered effort")
            if owner in dormant:
                warnings.append(f"{label}: owned by dormant effort {owner!r}")
            if entry.get("status") in {"exploring", "identified"} and owner in {None, "unassigned"}:
                unowned += 1
    return {"unowned": unowned, "entries": sum(len(registry.get(section) or [])
                                                for section in ("efforts", "contracts", "decisions"))}


def check_contracts(root: Path, explicit: str | None, by_id: dict[str, dict], errors: list[str],
                    warnings: list[str]) -> dict:
    """Validate the central wire-contract registry and the copies it points at.

    The artifact is a copy of the provider's authoritative file; the consumer pins the hash it
    builds against, so a changed artifact without a new pin is visible rather than silent.
    """
    target = explicit or os.environ.get("WORK_CONTRACTS")
    if not target:
        return {}
    contracts_dir = Path(target).expanduser()
    registry_path = contracts_dir / "registry.yaml"
    if not registry_path.is_file():
        errors.append(f"contracts: no registry at {registry_path}")
        return {}
    data = load_yaml(registry_path)
    if not isinstance(data, dict) or data.get("kind") != "wire-contract-registry":
        errors.append("contracts: registry kind must be wire-contract-registry")
        return {}
    entries = data.get("contracts") or []
    seen: set[str] = set()
    for entry in entries:
        cid = entry.get("id")
        label = f"contracts/{cid}"
        if cid in seen:
            errors.append(f"{label}: duplicate id")
        seen.add(cid)
        if entry.get("kind") not in {"http", "event"}:
            errors.append(f"{label}: unknown kind {entry.get('kind')!r}")
        if entry.get("status") not in {"proposed", "accepted", "deprecated"}:
            errors.append(f"{label}: unknown status {entry.get('status')!r}")
        if not str(cid or "").endswith(f".v{entry.get('version')}"):
            errors.append(f"{label}: id does not end with its version .v{entry.get('version')}")
        gate = entry.get("gate")
        if gate and gate not in by_id:
            errors.append(f"{label}: gate references unknown task {gate}")
        artifact = entry.get("artifact")
        digest = entry.get("sha256")
        if entry.get("status") == "accepted" and not (artifact and digest):
            errors.append(f"{label}: accepted contracts need artifact and sha256")
        if artifact:
            path = contracts_dir / artifact
            if not path.is_file():
                errors.append(f"{label}: artifact missing at contracts/{artifact}")
            elif digest and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                errors.append(f"{label}: artifact hash does not match sha256")
        for rel in entry.get("files") or []:
            if not (contracts_dir / rel).is_file():
                errors.append(f"{label}: companion missing at contracts/{rel}")
        for fixture_kind in ("valid", "invalid"):
            for rel in (entry.get("fixtures") or {}).get(fixture_kind, []) or []:
                if not (contracts_dir / rel).is_file():
                    errors.append(f"{label}: {fixture_kind} fixture missing at contracts/{rel}")
        for consumer in entry.get("consumers") or []:
            pin = consumer.get("pin") or {}
            if pin.get("sha256") and digest and pin["sha256"] != digest:
                warnings.append(
                    f"{label}: {consumer.get('service')} is pinned to a different version")

    # A program map may reference a contract, never invent one.
    for path in sorted((root / "programs").glob("*.yaml")):
        plan = load_yaml(path)
        if not isinstance(plan, dict):
            continue
        for contract in plan.get("contracts") or []:
            if contract.get("id") not in seen:
                errors.append(
                    f"{path.name}: contract {contract.get('id')} is not in contracts/registry.yaml")
    return {"contracts": len(entries),
            "accepted": sum(1 for entry in entries if entry.get("status") == "accepted")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the work-coordination registry.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--today", default=None, help="override today's date (YYYY-MM-DD)")
    parser.add_argument(
        "--capabilities", default=None,
        help="capability-steward home to cross-check (default: the parent holding capability-home.yaml)")
    parser.add_argument("--store", choices=["sqlite"], default="sqlite")
    parser.add_argument("--db", default=os.environ.get("WORK_DB")
                        or str(Path.home() / ".local/state/loom/work.db"))
    parser.add_argument(
        "--contracts", default=None,
        help="contract registry to validate (default: WORK_CONTRACTS, else skipped)")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser()
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()

    errors: list[str] = []
    warnings: list[str] = []
    store = make_store(root, args.store, args.db)
    if not store.has_registry():
        print(f"FAIL: no registry in {store.describe()}", file=sys.stderr)
        return 1
    registry = store.load()

    schema = json.loads((root / "schemas/task.schema.json").read_text(encoding="utf-8"))

    boards = {}
    for entry in registry.get("boards", []):
        if entry["id"] in boards:
            errors.append(f"duplicate board id {entry['id']}")
        boards[entry["id"]] = entry

    loaded = {}
    for board_id, entry in boards.items():
        path = Path(entry["repo"]).expanduser() / entry["path"]
        if not path.is_file():
            errors.append(f"{board_id}: missing {path}")
            continue
        loaded[board_id] = entry
        if entry["kind"] == "board":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"{board_id}: invalid JSON ({exc})")
                continue
            entry["_packets"] = {packet["id"]: packet for packet in data.get("packets", [])}
            entry["_data"] = data
        else:
            text = path.read_text(encoding="utf-8")
            entry["_program"] = program_statuses(text)
            entry["_ids"] = program_task_ids(text)

    tasks = registry.get("tasks", [])
    seen: set[str] = set()
    by_id: dict[str, dict] = {}
    for task in tasks:
        task_id = task.get("id", "<missing>")
        for problem in validate_schema(task, schema, task_id):
            errors.append(f"schema: {problem}")
        if task_id in seen:
            errors.append(f"{task_id}: duplicate task id")
        seen.add(task_id)
        by_id[task_id] = task

        board_id = task.get("board")
        if board_id not in boards:
            errors.append(f"{task_id}: unknown board {board_id}")
            continue
        entry = boards[board_id]
        suffix = task_id.split("/", 1)[-1]
        if entry["kind"] == "board":
            packet = entry.get("_packets", {}).get(suffix)
            if packet is None:
                errors.append(f"{task_id}: packet {suffix} missing from {board_id}")
            else:
                if packet.get("status") != task.get("status"):
                    if int(task.get("revision", 0)) == 0:
                        errors.append(
                            f"{task_id}: imported registry status {task.get('status')} differs from "
                            f"board {packet.get('status')}; run reconcile")
                    else:
                        warnings.append(
                            f"{task_id}: registry says {task.get('status')}, board says "
                            f"{packet.get('status')}; run board sync")
                central = packet.get("central")
                if isinstance(central, dict):
                    if central.get("revision") != task.get("revision"):
                        warnings.append(f"{task_id}: board central revision is stale")
                    if central.get("status") != task.get("status"):
                        warnings.append(f"{task_id}: board central status is stale; run board sync")
        else:
            if suffix not in entry.get("_ids", set()):
                errors.append(f"{task_id}: heading {suffix} missing from {board_id}")
            elif (task.get("status_source") != "manual"
                  and suffix in entry.get("_program", {})
                  and entry["_program"][suffix] != task.get("status")):
                warnings.append(
                    f"{task_id}: registry says {task.get('status')}, program says {entry['_program'][suffix]}")

    for board_id, entry in boards.items():
        if entry["kind"] != "board" or "_packets" not in entry:
            continue
        registered = {task_id.split("/", 1)[-1] for task_id, task in by_id.items()
                      if task.get("board") == board_id}
        for suffix in sorted(set(entry["_packets"]) - registered):
            warnings.append(
                f"{board_id}: packet {suffix} is not registered centrally; run `task board import`")

    # ---- dependency edges -------------------------------------------------
    edges = registry.get("edges", [])
    for edge in edges:
        for endpoint in (edge["from"], edge["to"]):
            if endpoint not in by_id:
                errors.append(f"edge {edge['from']} -> {edge['to']}: unknown {endpoint}")

    graph: dict[str, list[str]] = {task_id: [] for task_id in by_id}
    for edge in edges:
        if edge.get("kind", "depends-on") != "depends-on":
            continue
        if edge["from"] in graph and edge["to"] in graph:
            graph[edge["from"]].append(edge["to"])
    for task in tasks:
        for dep in task.get("depends_on", []) or []:
            if dep not in by_id:
                errors.append(f"{task['id']}: unknown dependency {dep}")
            elif dep in graph:
                graph[task["id"]].append(dep)

    visiting, visited = set(), set()

    def visit(node: str, trail: list[str]) -> None:
        if node in visiting:
            errors.append("dependency cycle: " + " -> ".join(trail + [node]))
            return
        if node in visited:
            return
        visiting.add(node)
        for neighbour in graph.get(node, []):
            visit(neighbour, trail + [node])
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, [])

    now = dt.datetime.now(dt.timezone.utc)
    for task_id, task in by_id.items():
        unmet = [dep for dep in graph.get(task_id, []) if by_id.get(dep, {}).get("status") not in STATUS_DONE]
        if unmet and task.get("status") == "ready":
            warnings.append(f"{task_id}: ready but waiting on {', '.join(unmet)}")
        if task.get("status") in STATUS_ACTIVE:
            claim = task.get("claim")
            if not claim:
                if int(task.get("revision", 0)) == 0:
                    warnings.append(
                        f"{task_id}: legacy {task.get('status')} state imported without a lease")
                else:
                    errors.append(f"{task_id}: status {task.get('status')} requires a claim")
                continue
            try:
                expiry = parse_time(claim["lease_expires"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"{task_id}: claim has invalid lease_expires")
                continue
            if expiry <= now:
                warnings.append(f"{task_id}: lease expired at {claim['lease_expires']} (takeover available)")
            if int(claim.get("base_revision", -1)) >= int(task.get("revision", 0)):
                errors.append(f"{task_id}: claim base_revision must be below revision")
            heartbeat = claim.get("heartbeat")
            if heartbeat:
                try:
                    if parse_time(heartbeat) > expiry:
                        errors.append(f"{task_id}: heartbeat is after lease expiry")
                except (TypeError, ValueError):
                    errors.append(f"{task_id}: invalid heartbeat")
        elif task.get("claim"):
            errors.append(f"{task_id}: status {task.get('status')} must not carry a claim")

    # ---- shared-artifact ownership claims --------------------------------
    claimed: dict[str, str] = {}
    for claim in registry.get("claims", []):
        artifact = Path(claim["repo"]).expanduser() / claim["artifact"]
        if not artifact.is_file():
            errors.append(f"claim {claim['artifact']}: file missing")
        expiry = claim.get("expires")
        if isinstance(expiry, str):
            expiry = dt.date.fromisoformat(expiry)
        if isinstance(expiry, dt.date) and expiry < today:
            warnings.append(f"claim {claim['artifact']}: expired {expiry}")
        if claim.get("task") and claim["task"] not in by_id:
            errors.append(f"claim {claim['artifact']}: unknown task {claim['task']}")
        if claim["artifact"] in claimed and claimed[claim["artifact"]] != claim["owner"]:
            errors.append(
                f"claim conflict: {claim['artifact']} claimed by {claimed[claim['artifact']]} and {claim['owner']}")
        claimed[claim["artifact"]] = claim["owner"]

    # ---- write-path overlap ---------------------------------------------
    owners: dict[str, tuple[str, str]] = {}
    for task in tasks:
        if task.get("status") not in STATUS_ACTIVE:
            continue
        for write_path in task.get("write_paths", []) or []:
            previous = owners.get(write_path)
            if previous and previous[0] != task.get("owner_repo"):
                errors.append(f"write-path overlap: {write_path} in {previous[1]} and {task['id']}")
            elif previous and previous[1] != task["id"]:
                warnings.append(
                    f"write-path overlap within {task.get('owner_repo')}: {write_path} in {previous[1]} and {task['id']}")
            owners[write_path] = (task.get("owner_repo", ""), task["id"])

    registered_pairs = {(task.get("board"), task_id.split("/", 1)[-1])
                        for task_id, task in by_id.items()}
    for board_id, entry in boards.items():
        if entry["kind"] != "board" or "_data" not in entry:
            continue
        repo = entry["repo"]
        for packet in entry["_data"].get("packets", []):
            if packet.get("status") not in STATUS_ACTIVE:
                continue
            if (board_id, packet["id"]) in registered_pairs:
                continue
            owner = f"{board_id}/{packet['id']}"
            for write_path in packet.get("write_paths", []):
                previous = owners.get(write_path)
                if previous and previous[0] != repo:
                    errors.append(f"write-path overlap: {write_path} in {previous[1]} and {owner}")
                elif previous and previous[1] != owner:
                    warnings.append(
                        f"write-path overlap within {repo}: {write_path} in {previous[1]} and {owner}")
                owners[write_path] = (repo, owner)

    # ---- integration plans ----------------------------------------------
    plan_count = 0
    plans_dir = root / "programs"
    services = {"iam", "ums", "dutch", "dutch-ref", "portfolio"}
    slice_states = {"startable", "coordinated", "blocked", "owner-confirmation"}
    if plans_dir.is_dir():
        for path in sorted(plans_dir.glob("*.yaml")):
            plan = load_yaml(path)
            if not isinstance(plan, dict) or plan.get("kind") != "integration-plan":
                errors.append(f"{path.name}: kind must be integration-plan")
                continue
            plan_count += 1
            contract_ids = set()
            for contract in plan.get("contracts", []):
                contract_id = contract.get("id")
                if contract_id in contract_ids:
                    errors.append(f"{path.name}: duplicate contract {contract_id}")
                contract_ids.add(contract_id)
                if contract.get("owner") and contract["owner"] not in services:
                    errors.append(f"{path.name}: contract {contract_id} has unknown owner")
                for role in ("producers", "consumers"):
                    for service in contract.get(role, []) or []:
                        if service not in services:
                            errors.append(
                                f"{path.name}: contract {contract_id} references unknown service {service}")
                if contract.get("task") and contract["task"] not in by_id:
                    errors.append(f"{path.name}: contract {contract_id} references unknown task {contract['task']}")
            for journey in plan.get("journeys", []):
                for step in journey.get("steps", []):
                    if step.get("task") and step["task"] not in by_id:
                        errors.append(f"{path.name}: journey {journey.get('id')} references unknown task {step['task']}")
                    if step.get("contract") and step["contract"] not in contract_ids:
                        errors.append(
                            f"{path.name}: journey {journey.get('id')} references unknown contract {step['contract']}")
                for gate in journey.get("gates", []) or []:
                    if gate not in by_id:
                        errors.append(f"{path.name}: journey {journey.get('id')} references unknown gate {gate}")
            for item in plan.get("slices", []):
                if item.get("state") not in slice_states:
                    errors.append(f"{path.name}: slice {item.get('id')} has unknown state {item.get('state')}")
                if item.get("state") == "blocked" and not item.get("blocked_by"):
                    errors.append(f"{path.name}: blocked slice {item.get('id')} needs blocked_by")
                for task_id in item.get("tasks", []) or []:
                    if task_id not in by_id:
                        errors.append(f"{path.name}: slice {item.get('id')} references unknown task {task_id}")
                for blocker in item.get("blocked_by", []) or []:
                    if blocker.startswith("decision:"):
                        decision = blocker.split(":", 1)[1]
                        if not (root / "decisions" / f"{decision}.yaml").is_file():
                            errors.append(f"{path.name}: slice {item.get('id')} references unknown decision {decision}")
                    elif blocker not in by_id:
                        errors.append(f"{path.name}: slice {item.get('id')} references unknown blocker {blocker}")
            for name, entry in plan.get("agent_start", {}).items():
                for task_id in (entry.get("tasks", []) or []) + (entry.get("hold", []) or []):
                    if task_id not in by_id:
                        errors.append(f"{path.name}: agent_start {name} references unknown task {task_id}")

    # ---- event log -------------------------------------------------------
    records: list[tuple[bytes, dict]] = []
    stored_events = store.read_events()
    if stored_events:
        for raw, _parsed in stored_events:
            if raw.strip():
                try:
                    records.append((raw, json.loads(raw)))
                except json.JSONDecodeError as exc:
                    errors.append(f"event log: invalid JSON ({exc})")
        errors.extend(verify_event_chain(records))
        last_by_task: dict[str, dict] = {}
        for _, record in records:
            if record.get("task"):
                last_by_task[record["task"]] = record
        for task_id, record in last_by_task.items():
            if task_id not in by_id:
                warnings.append(f"event log: task {task_id} no longer in registry")
                continue
            task = by_id[task_id]
            if record.get("revision") != task.get("revision"):
                errors.append(
                    f"{task_id}: event log revision {record.get('revision')} != registry {task.get('revision')}"
                    " (hand edit or interrupted write)")
            if record.get("status") != task.get("status"):
                errors.append(
                    f"{task_id}: event log status {record.get('status')!r} != registry {task.get('status')!r}")
            claim = task.get("claim") or {}
            if claim.get("agent") and record.get("event") in {"claim", "renew", "takeover"}:
                if record.get("agent") != claim.get("agent"):
                    errors.append(f"{task_id}: event log claim agent does not match registry claim")
        claimants: dict[str, set[str]] = {}
        for _, record in records:
            if record.get("event") in {"claim", "renew", "takeover"} and record.get("task"):
                if record.get("agent"):
                    claimants.setdefault(record["task"], set()).add(record["agent"])
        for task_id, task in by_id.items():
            reviewer = (task.get("review") or {}).get("reviewer")
            if reviewer and reviewer in claimants.get(task_id, set()):
                warnings.append(
                    f"{task_id}: reviewer {reviewer!r} also claimed the task; review is not independent")
    else:
        warnings.append("events.ndjson missing; run `./task genesis --agent <id>`")

    programs = sum(1 for entry in boards.values() if entry["kind"] == "program")
    steward_counts = cross_check_capabilities(root, args.capabilities, errors, warnings)
    contract_counts = check_contracts(root, args.contracts, by_id, errors, warnings)
    readme = root / "README.md"
    if readme.is_file():
        match = re.search(r"\*\*(\d+) boards, (\d+) tasks, (\d+) edges\*\*",
                          readme.read_text(encoding="utf-8"))
        if match:
            found = tuple(int(value) for value in match.groups())
            actual = (len(boards), len(tasks), len(edges))
            if found != actual:
                warnings.append(
                    f"README.md claims {found[0]} boards, {found[1]} tasks, {found[2]} edges; "
                    f"registry has {actual[0]} boards, {actual[1]} tasks, {actual[2]} edges")
    print("Work coordination validation")
    print(f"  root: {root}")
    print(f"  boards: {len(boards)} ({programs} program, {len(boards) - programs} packet boards)")
    print(f"  tasks: {len(tasks)}  edges: {len(edges)}  claims: {len(registry.get('claims', []))}"
          f"  events: {len(records)}  plans: {plan_count}")
    if steward_counts:
        print(f"  steward: {steward_counts['entries']} entries, "
              f"{steward_counts['unowned']} exploring/identified with no owner")
    if contract_counts:
        print(f"  contracts: {contract_counts['contracts']} "
              f"({contract_counts['accepted']} accepted)")
    for warning in warnings:
        print(f"WARN  {warning}")
    for error in errors:
        print(f"ERROR {error}")
    print("PASS" if not errors else f"FAIL ({len(errors)} errors, {len(warnings)} warnings)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
