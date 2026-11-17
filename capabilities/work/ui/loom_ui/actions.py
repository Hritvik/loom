"""Guarded task actions: what may be offered, what is submitted, and how it is executed.

Three separate questions, deliberately answered in three places:

1. **May this session be offered this action?** :func:`catalogue` reads the task through the same
   read model the pages use and returns every action the task's *state* makes relevant, marked
   available or not, with the reason. Role policy from :mod:`loom_ui.identity` is part of that
   answer. This is presentation, not authority: an action shown as available can still be refused.
2. **Is this submission the one the server rendered?** :func:`validate` works from an allow-list of
   fields per action, rejects browser-supplied actor names, and :class:`loom_ui.session.SessionStore`
   consumes a one-shot form nonce. Nothing in the body can add a flag, a command or an identity.
3. **Did Loom accept it?** :func:`execute` runs the supported front door as a subprocess with an
   argument array — never a shell — and reports its exit status and output verbatim. Revision and
   epoch go to the engine, which owns compare-and-swap and fencing, so a stale page fails visibly
   instead of being retried.

There is no entry here for ``tick``, ``tick --apply``, worker dispatch, the scheduler or a
deployment. The allow-list is the boundary: :data:`ENGINE_VERBS` is what can be executed, and a
verb that is not in it has no route to reach.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import engine
import store

from .control import TaskDetail
from .identity import ACTION_ROLES, Policy, TARGET_ROLES

#: The only engine verbs the UI can execute. `tick`, `workers`, `schedule`, `deploy`, `linear` and
#: `annotate` are deliberately absent: they are global, operational or proof-bearing operations.
ENGINE_VERBS = frozenset({"claim", "renew", "release", "takeover", "transition", "ask", "answer"})

#: Field names that would mean "the browser chooses the actor". They are refused, not ignored.
RESERVED_FIELDS = frozenset({"agent", "actor", "acting_as", "acting-as", "identity", "as",
                             "user", "username", "owner", "impersonate"})

#: Server-rendered hidden fields. Their values are re-checked against the option, never trusted.
HIDDEN_FIELDS = frozenset({"csrf", "form", "revision", "epoch", "to", "question"})

#: Transitions the UI does not offer, with the reason recorded where a reader will find it.
EXCLUDED_TARGETS = {
    "reviewed": ("a reviewer's answer is recorded by the coordinator; the UI does not let an "
                 "implementer mark their own work reviewed"),
    "integrated": ("integration is a receipt for a commit that was already merged by the "
                   "coordinator; it is not a browser action"),
}

#: ``reviewed -> accepted`` needs the exact approved commit as proof, which belongs to the
#: coordinator's integration receipt rather than to a form field.
NEEDS_INTEGRATED_SOURCE = frozenset({("reviewed", "accepted")})

OUTPUT_LIMIT = 8000
DEFAULT_TIMEOUT = 90.0

#: An exit status of 2 is the engine refusing the operation. The message decides whether the
#: refusal is a stale write (409) or a gate denial (422); the UI never guesses beyond the text.
CONFLICT_NEEDLES = ("revision conflict", "stale epoch", "is fenced", "expired at",
                    "has an expired lease", "is leased by", "lock-delay", "already answered",
                    "superseded", "no lease to renew", "expired; renewal is no longer safe")


# --------------------------------------------------------------------------
# fields and options
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    hint: str
    required: bool = True
    multiline: bool = False
    max_length: int = 512
    pattern: str | None = None
    default: str = ""
    options: tuple[tuple[str, str], ...] = ()

    def html_type(self) -> str:
        return "textarea" if self.multiline else ("select" if self.options else "text")

    def to_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "hint": self.hint,
                "required": self.required, "multiline": self.multiline,
                "max_length": self.max_length, "default": self.default,
                "options": [{"value": value, "label": label} for value, label in self.options]}


@dataclass(frozen=True)
class Option:
    """One offered action against one task, with its gate and its form fields."""

    id: str
    label: str
    summary: str
    effect: str
    available: bool
    reason: str | None
    roles: tuple[str, ...]
    fields: tuple[Field, ...] = ()
    params: tuple[tuple[str, str], ...] = ()

    def param(self, name: str) -> str | None:
        return dict(self.params).get(name)

    @property
    def target(self) -> str | None:
        return self.param("to")

    def query(self) -> str:
        from urllib.parse import urlencode

        return urlencode(dict(self.params))

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "summary": self.summary,
                "effect": self.effect, "available": self.available, "reason": self.reason,
                "roles": list(self.roles), "params": dict(self.params),
                "fields": [field.to_dict() for field in self.fields]}


@dataclass(frozen=True)
class Panel:
    enabled: bool
    identity: str | None
    roles: tuple[str, ...]
    options: tuple[Option, ...] = ()

    @property
    def available(self) -> tuple[Option, ...]:
        return tuple(option for option in self.options if option.available)

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "identity": self.identity, "roles": list(self.roles),
                "available": [option.id for option in self.available],
                "options": [option.to_dict() for option in self.options]}


TTL_FIELD = Field(
    "ttl", "Lease length",
    "How long the lease lasts, e.g. 30m, 2h, 7d. The engine's default is used when left blank.",
    required=False, max_length=6, pattern=r"^\d{1,4}[smhd]$", default=engine.DEFAULT_TTL)
REASON_FIELD = Field(
    "reason", "Reason",
    "Recorded in the audit event. Someone reading the log later needs to know why.",
    max_length=1000, multiline=True)
EVIDENCE_FIELD = Field(
    "evidence", "Evidence locator",
    "Path inside the owner repository, e.g. evidence/<task>.md.",
    max_length=512, pattern=r"^(?!.*\.\.)[A-Za-z0-9._/-]{1,512}$")
QUESTION_FIELD = Field("question", "Question", "What you need answered.", max_length=2000,
                       multiline=True)
TO_FIELD = Field("to", "Ask", "Engine identity expected to answer; it must not be you.",
                 max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._@/-]{0,63}$")
NEEDED_BY_FIELD = Field(
    "needed_by", "Needed by (optional)",
    "An ISO date or datetime, e.g. 2026-11-20.",
    required=False, max_length=32,
    pattern=r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?Z?)?$")
NOTE_FIELD = Field("note", "Note", "What you checked and what you decided.", max_length=2000,
                   multiline=True)
NEEDS_FIELD = Field(
    "needs", "Who can unblock it", "A person (human) or another agent.",
    required=False, max_length=8, pattern=r"^(human|agent)$", default="agent",
    options=(("agent", "Another agent"), ("human", "A person")))

#: Actions whose engine command carries the fencing epoch the page presented.
EPOCH_ACTIONS = frozenset({"renew", "release"})

REVIEW_DECISIONS = (("approve", "Approve"), ("changes requested", "Request changes"))
ORDINARY_DECISIONS = (("answer", "Answer"),)


def _now(snapshot_as_of: str) -> datetime:
    try:
        return store.parse_time(snapshot_as_of)
    except (TypeError, ValueError):  # pragma: no cover - a snapshot always carries a time
        return datetime.now(timezone.utc)


def _reason(roles: tuple[str, ...]) -> str:
    return "your role does not include " + " or ".join(roles)


def transition_fields(current: str, target: str) -> tuple[Field, ...]:
    """The fields the engine requires for one transition, and no others."""
    fields: list[Field] = []
    if target == "blocked":
        fields.append(NEEDS_FIELD)
    if target in {"rejected", "released"}:
        fields.append(REASON_FIELD)
    if target == "ready" and (current in engine.ACTIVE_STATES or current in {"reviewed", "blocked"}):
        fields.append(REASON_FIELD)
    if target == "accepted":
        fields.append(EVIDENCE_FIELD)
    return tuple(fields)


# --------------------------------------------------------------------------
# the catalogue
# --------------------------------------------------------------------------


def catalogue(detail: TaskDetail, policy: Policy, identity: str | None,
              now: datetime | None = None) -> Panel:
    """Every action relevant to this task's current state, gated for this session."""
    human = policy.get(identity)
    moment = now or _now(detail.as_of)
    task = detail.task
    claim = task.claim
    options: list[Option] = []

    def gated(action: str, target: str | None = None) -> str | None:
        if not policy.configured:
            return "this server has no configured human identity"
        if human is None:
            return "you are not signed in"
        roles = TARGET_ROLES.get(target or "") or ACTION_ROLES[action]
        if not human.allows(action, target):
            return _reason(tuple(sorted(roles)))
        return None

    # -- claim ------------------------------------------------------------
    if task.status in engine.CLAIMABLE_FROM:
        refusal = gated("claim")
        if refusal is None and not task.claimability.claimable:
            refusal = "; ".join(task.claimability.reasons) or "claim would be refused"
        options.append(Option(
            id="claim", label="Claim", summary="Take the lease on this ready task.",
            effect=f"ready → claimed; revision {task.revision} → {task.revision + 1}; "
                   f"task epoch {task.epoch} → {task.epoch + 1}",
            available=refusal is None, reason=refusal, roles=("implementer",),
            fields=(TTL_FIELD,)))

    # -- lease operations --------------------------------------------------
    if claim is not None:
        mine = human is not None and claim.agent == human.identity
        active_state = task.status in engine.ACTIVE_STATES
        refusal = gated("renew")
        if refusal is None and claim.expired:
            refusal = (f"the lease expired at {claim.lease_expires}; renewal is no longer "
                       "allowed and takeover is the recorded way to continue")
        elif refusal is None and not mine:
            refusal = f"held by {claim.agent}, not you"
        elif refusal is None and not active_state:
            refusal = f"a {task.status} task holds no renewable lease"
        options.append(Option(
            id="renew", label="Renew lease", summary="Extend the lease on work you hold.",
            effect=f"lease until {claim.lease_expires} is replaced by now + TTL; revision is "
                   "unchanged",
            available=refusal is None, reason=refusal, roles=("implementer",),
            fields=(TTL_FIELD,)))

        refusal = gated("release")
        if refusal is None and not mine:
            refusal = f"held by {claim.agent}, not you"
        elif refusal is None and task.status not in engine.ACTIVE_STATES | {"in-progress"}:
            refusal = f"a {task.status} task has no claim to release"
        options.append(Option(
            id="release", label="Release claim", summary="Give the task back, keeping the record.",
            effect=f"{task.status} → ready; revision {task.revision} → {task.revision + 1}; "
                   "the claim is cleared and the epoch is not reused",
            available=refusal is None, reason=refusal, roles=("implementer",),
            fields=(REASON_FIELD,)))

        refusal = gated("takeover")
        # The claim view already carries the engine's own expiry decision; parse the same stamp
        # only to place the lock-delay window on the clock.
        expiry = store.parse_time(claim.lease_expires) if claim.lease_expires else None
        delay = engine.parse_duration(engine.DEFAULT_LOCK_DELAY)
        if refusal is None and task.status not in engine.ACTIVE_STATES:
            refusal = f"a {task.status} task holds no claim to take over"
        elif refusal is None and (expiry is None or expiry > moment):
            refusal = f"the lease is live until {claim.lease_expires}; takeover requires expiry"
        elif refusal is None and expiry + delay > moment:
            allowed_at = (expiry + delay).isoformat(timespec="seconds").replace("+00:00", "Z")
            refusal = (f"the lease expired at {claim.lease_expires}, but the "
                       f"{engine.DEFAULT_LOCK_DELAY} lock delay runs until {allowed_at}")
        options.append(Option(
            id="takeover", label="Take over", summary=(
                "Take an expired lease after the lock delay. The previous holder is fenced off "
                "and must stop."),
            effect=f"task epoch {task.epoch} → {task.epoch + 1}; the old worker's epoch stops "
                   "being accepted",
            available=refusal is None, reason=refusal, roles=("implementer",),
            fields=(REASON_FIELD, TTL_FIELD)))

    # -- transitions -------------------------------------------------------
    active = task.status in engine.ACTIVE_STATES | {"in-progress"}
    for target in sorted(engine.TRANSITIONS.get(task.status, set())):
        if target in EXCLUDED_TARGETS:
            continue
        if (task.status, target) in NEEDS_INTEGRATED_SOURCE:
            continue
        roles = tuple(sorted(TARGET_ROLES.get(target) or ACTION_ROLES["transition"]))
        refusal = gated("transition", target)
        if refusal is None and active and claim is None:
            refusal = "the task is active with no claim recorded"
        elif refusal is None and active and claim.agent != (human.identity if human else None):
            refusal = f"claimed by {claim.agent}; only the holder can move it"
        elif refusal is None and target in {"red", "green"} and claim and claim.expired:
            refusal = f"the lease expired at {claim.lease_expires}; no live authority to report"
        elif refusal is None and target == "accepted" and not task.claimability.dependencies_satisfied:
            refusal = ("unmet dependencies: "
                       + ", ".join(task.claimability.unmet_dependencies))
        options.append(Option(
            id="transition", label=f"Move to {target}", summary=(
                f"Record the lifecycle transition {task.status} → {target}."),
            effect=f"{task.status} → {target}; revision {task.revision} → {task.revision + 1}",
            available=refusal is None, reason=refusal, roles=roles,
            fields=transition_fields(task.status, target), params=(("to", target),)))

    # -- questions ---------------------------------------------------------
    refusal = gated("ask")
    if refusal is None and not policy.configured:
        refusal = "this server has no configured human identity"
    options.append(Option(
        id="ask", label="Ask a question", summary="Record a question on the audit log.",
        effect="an ask event; the task revision is unchanged",
        available=refusal is None, reason=refusal, roles=("implementer", "owner", "reviewer"),
        fields=(TO_FIELD, QUESTION_FIELD, NEEDED_BY_FIELD)))

    for question in detail.questions:
        if question.answered or question.asked_to != (human.identity if human else ""):
            continue
        if question.review_source and not question.current:
            continue
        decisions = REVIEW_DECISIONS if question.review_source else ORDINARY_DECISIONS
        decision = Field("decision", "Decision", "Exactly one verdict is recorded.",
                         max_length=24, default=decisions[0][0], options=decisions,
                         pattern="^(" + "|".join(re.escape(value)
                                                 for value, _ in decisions) + ")$")
        options.append(Option(
            id="answer", label=f"Answer question {question.seq}",
            summary=question.question[:200],
            effect=f"an answer event replying to {question.seq}; the revision is unchanged",
            available=True, reason=None, roles=("implementer", "owner", "reviewer"),
            fields=(decision, NOTE_FIELD), params=(("question", str(question.seq)),)))

    return Panel(enabled=policy.configured, identity=identity,
                 roles=tuple(sorted(human.roles)) if human else (), options=tuple(options))


def find_option(panel: Panel, action: str, target: str | None = None,
                question: str | None = None) -> Option | None:
    for option in panel.options:
        if option.id != action:
            continue
        if action == "transition" and option.target != target:
            continue
        if action == "answer" and option.param("question") != str(question):
            continue
        return option
    return None


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Submission:
    values: dict[str, str]
    revision: int
    epoch: int | None


def validate(option: Option, submitted: dict[str, str],
             claim_epoch: int | None = None) -> tuple[Submission | None, tuple[str, ...]]:
    """Check the body's shape and values. Unknown or actor-naming fields are errors."""
    problems: list[str] = []
    known = {field.name for field in option.fields}
    for name in submitted:
        if name in RESERVED_FIELDS:
            problems.append(f"the browser may not name the acting identity ({name!r} is refused)")
        elif name in HIDDEN_FIELDS:
            continue
        elif name not in known:
            problems.append(f"unknown field {name!r}")
    if problems:
        return None, tuple(problems)

    values: dict[str, str] = {}
    for spec in option.fields:
        raw = str(submitted.get(spec.name) or "").strip()
        if not raw:
            if spec.required:
                problems.append(f"{spec.label} is required")
                continue
            values[spec.name] = spec.default
            continue
        if len(raw) > spec.max_length:
            problems.append(f"{spec.label} is longer than {spec.max_length} characters")
            continue
        if spec.options and raw not in {value for value, _ in spec.options}:
            problems.append(f"{spec.label} must be one of the offered values")
            continue
        if spec.pattern and not re.fullmatch(spec.pattern, raw):
            problems.append(f"{spec.label} is not in the expected form")
            continue
        values[spec.name] = raw

    try:
        revision = int(str(submitted.get("revision") or ""))
        if revision < 0:
            raise ValueError
    except ValueError:
        problems.append("the revision being presented is missing or malformed")
        revision = 0
    epoch_raw = str(submitted.get("epoch") or "").strip()
    epoch = None
    if epoch_raw:
        try:
            epoch = int(epoch_raw)
            if epoch < 0:
                raise ValueError
        except ValueError:
            problems.append("the epoch being presented is malformed")
    # A claim created before fencing existed carries no epoch, and the engine treats it as
    # grandfathered; demanding one would block a renewal that Loom would allow.
    if epoch is None and claim_epoch is not None and option.id in EPOCH_ACTIONS:
        problems.append("the fencing epoch is required for this action; reload the task")
    if problems:
        return None, tuple(problems)
    return Submission(values=values, revision=revision, epoch=epoch), ()


def command_for(option: Option, task_id: str, submission: Submission) -> list[str]:
    """The engine arguments for one validated submission. An argument array, never a string."""
    values = submission.values
    verb = option.id
    if verb not in ENGINE_VERBS:
        raise ValueError(f"{verb!r} is not an executable action")
    args = [verb, task_id]
    if verb == "claim":
        return args + ["--revision", str(submission.revision), "--ttl", values["ttl"]]
    if verb == "renew":
        args += ["--revision", str(submission.revision)]
        if submission.epoch is not None:
            args += ["--epoch", str(submission.epoch)]
        return args + ["--ttl", values["ttl"]]
    if verb == "release":
        args += ["--revision", str(submission.revision)]
        if submission.epoch is not None:
            args += ["--epoch", str(submission.epoch)]
        return args + ["--reason", values["reason"]]
    if verb == "takeover":
        return args + ["--revision", str(submission.revision),
                       "--reason", values["reason"], "--ttl", values["ttl"]]
    if verb == "transition":
        target = option.target or ""
        args += ["--revision", str(submission.revision), "--to", target]
        if submission.epoch is not None:
            args += ["--epoch", str(submission.epoch)]
        for name in ("reason", "evidence", "needs"):
            if values.get(name):
                args += [f"--{name}", values[name]]
        return args
    if verb == "answer":
        return args + ["--question", str(option.param("question")), "--decision",
                       values["decision"], "--note", values["note"]]
    if verb == "ask":
        args += ["--to", values["to"], "--question", values["question"]]
        if values.get("needed_by"):
            args += ["--needed-by", values["needed_by"]]
        return args
    raise ValueError(f"{verb!r} has no argument mapping")  # pragma: no cover - covered above


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    kind: str                      # success | conflict | denied | failed | timeout
    exit_code: int | None
    stdout: str
    stderr: str
    argv: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.kind == "success"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "exit_code": self.exit_code, "stdout": self.stdout,
                "stderr": self.stderr,
                "argv": [part for part in self.argv]}


def classify(exit_code: int | None, stderr: str) -> str:
    if exit_code == 0:
        return "success"
    if exit_code == 2:
        text = (stderr or "").lower()
        if any(needle in text for needle in CONFLICT_NEEDLES):
            return "conflict"
        return "denied"
    return "failed"


def _environment() -> dict[str, str]:
    """The ambient environment minus anything that could name a different actor or registry."""
    env = dict(os.environ)
    for name in ("WORK_AGENT", "WORK_DB", "WORK_OWNER", "WORK_HOME", "LOOM_CONFIG"):
        env.pop(name, None)
    return env


def execute(front_door: Path, db_path: Path, identity: str, command: list[str],
            timeout: float = DEFAULT_TIMEOUT, runner=None) -> Run:
    """Run the supported front door once, and report what it said."""
    argv = [sys.executable, str(front_door), "--db", str(db_path), "--agent", identity, *command]
    if not Path(front_door).is_file():
        return Run(kind="failed", exit_code=None, stdout="",
                   stderr=f"the front door {front_door} does not exist; nothing was executed",
                   argv=tuple(argv))
    runner = runner or subprocess.run
    try:
        completed = runner(argv, capture_output=True, text=True, timeout=timeout,
                           shell=False, env=_environment())
    except subprocess.TimeoutExpired:
        return Run(kind="timeout", exit_code=None, stdout="",
                   stderr=(f"the front door did not finish within {int(timeout)}s. The registry "
                           "may or may not have changed: re-read the task before retrying."),
                   argv=tuple(argv))
    except OSError as exc:
        return Run(kind="failed", exit_code=None, stdout="",
                   stderr=f"the front door could not be started: {exc}", argv=tuple(argv))
    stdout = (completed.stdout or "")[:OUTPUT_LIMIT]
    stderr = (completed.stderr or "")[:OUTPUT_LIMIT]
    return Run(kind=classify(completed.returncode, stderr), exit_code=completed.returncode,
               stdout=stdout, stderr=stderr, argv=tuple(argv))


AUDIT_EVENTS = frozenset({"claim", "renew", "release", "takeover", "transition", "ask", "answer"})


def audit_event(events, task_id: str, after_seq: int, identity: str) -> dict | None:
    """The one audit event this action wrote, identified without guessing.

    The engine appends at least one event for every accepted mutation. A claim also opens an
    attempt; that is a run record, not the action's audit event, so the event kind is restricted
    to the verbs and the highest matching sequence wins. If nothing matches, the caller is told
    the event could not be identified rather than shown an older one.
    """
    found = None
    for event in events:
        try:
            seq = int(event.get("seq") or 0)
        except (TypeError, ValueError):
            continue
        if (seq > after_seq and event.get("task") == task_id
                and event.get("agent") == identity
                and event.get("event") in AUDIT_EVENTS):
            if found is None or seq > int(found.get("seq") or 0):
                found = event
    return found


__all__ = ["ENGINE_VERBS", "RESERVED_FIELDS", "EXCLUDED_TARGETS", "Field", "Option", "Panel",
           "Submission", "Run", "catalogue", "find_option", "validate", "command_for", "execute",
           "classify", "audit_event", "transition_fields"]
