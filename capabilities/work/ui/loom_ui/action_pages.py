"""The confirmation form and the outcome page for one task action.

Two pages, two jobs.

The **confirmation page** exists so a person can see exactly what they are about to do before it
happens: the task, the action, the state it will move from and to, the revision and fencing epoch
being presented, the lease being relied on, who they are acting as, and which fields the engine
requires. It also carries the CSRF token and a one-shot form nonce, so the POST that follows is both
authorised and single-use.

The **outcome page** reports what Loom said, verbatim, and links to the audit event it wrote. A
refusal names the reason and states plainly that nothing was written. Neither page retries, and the
refusal page never pretends the requested change is queued.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from . import templates
from .actions import Field, Option, Panel, Run
from .control import TaskDetail
from .templates import esc, state_badge, task_link
from .version import EngineVersion


@dataclass(frozen=True)
class Outcome:
    """What the server observed: the request, the engine's answer, and the state after it."""

    task_id: str
    option: Option
    actor: str
    run: Run
    event: dict | None
    revision_before: int
    status_before: str
    revision_after: int | None = None
    status_after: str | None = None
    offered_again: bool = False

    @property
    def event_seq(self) -> int | None:
        return int(self.event["seq"]) if self.event and self.event.get("seq") is not None else None


def _task_path(task_id: str) -> str:
    return "/task/" + quote(task_id, safe="/")


def confirm_url(task_id: str, option: Option) -> str:
    base = f"{_task_path(task_id)}/action/{quote(option.id, safe='')}"
    query = option.query()
    return f"{base}?{query}" if query else base


def _facts(rows: list[tuple[str, str]]) -> str:
    body = "".join(f"<dt>{esc(name)}</dt><dd>{value}</dd>" for name, value in rows)
    return f'<dl class="facts">{body}</dl>'


def _field(spec: Field) -> str:
    name = esc(spec.name)
    ident = f"field-{name}"
    required = " required" if spec.required else ""
    marked = (' <span class="required">(required)</span>' if spec.required
              else ' <span class="required">(optional)</span>')
    hint = f'<p class="hint" id="{ident}-hint">{esc(spec.hint)}</p>'
    described = f' aria-describedby="{ident}-hint"'
    if spec.html_type() == "textarea":
        control = (f'<textarea id="{ident}" name="{name}" maxlength="{esc(spec.max_length)}"'
                   f'{required}{described}>{esc(spec.default)}</textarea>')
    elif spec.html_type() == "select":
        options = "".join(
            f'<option value="{esc(value)}"'
            f'{" selected" if value == spec.default else ""}>{esc(label)}</option>'
            for value, label in spec.options)
        control = (f'<select id="{ident}" name="{name}"{described}>'
                   f"{options}</select>")
    else:
        control = (f'<input type="text" id="{ident}" name="{name}" value="{esc(spec.default)}"'
                   f' maxlength="{esc(spec.max_length)}"{required}{described}>')
    return (f'<div class="field"><label for="{ident}">{esc(spec.label)}{marked}</label>'
            f"{hint}{control}</div>")


def _hidden(name: str, value) -> str:
    return f'<input type="hidden" name="{esc(name)}" value="{esc(value)}">'


def confirm_page(detail: TaskDetail, option: Option, panel: Panel, version: EngineVersion,
                 now: datetime, csrf: str, form: str) -> str:
    task = detail.task
    claim = task.claim
    label, stale = templates.freshness(detail.as_of, now)
    parts: list[str] = []
    parts.append(f"<h1>Confirm: {esc(option.label)}</h1>")
    parts.append(
        f'<p class="lede">{task_link(task.id)} &middot; {esc(task.title)}</p>')
    parts.append('<p><a href="' + esc(_task_path(task.id)) + '">← Back to the task</a></p>')
    parts.append(
        f'<p class="who">Acting as <strong>{esc(panel.identity)}</strong>'
        f'{" (" + esc(", ".join(panel.roles)) + ")" if panel.roles else ""}. '
        "The browser cannot choose this identity; it comes from the session the server paired."
        "</p>")
    if stale:
        parts.append(
            '<div class="banner" role="status"><h2>Stale sample</h2>'
            f"<p>The registry was read {esc(label)}. Leases and revisions may have moved since. "
            "The engine re-checks the revision you submit, so a stale confirmation is refused "
            "rather than applied to newer state.</p></div>")

    parts.append(_facts([
        ("action", f"<strong>{esc(option.label)}</strong> — {esc(option.summary)}"),
        ("effect", esc(option.effect)),
        ("task state", state_badge(task.status)),
        ("revision presented", f'<span class="mono">{esc(task.revision)}</span>'),
        ("task epoch", f'<span class="mono">{esc(task.epoch)}</span>'),
        ("claim holder", esc(claim.agent) if claim else "no claim"),
        ("fencing epoch presented",
         f'<span class="mono">{esc(claim.epoch) if claim else "not applicable"}</span>'),
        ("lease", (templates.stamp(claim.lease_expires, now) + " ("
                   + ("live" if claim.active else "expired") + ")")
         if claim else "no lease"),
        ("registry sample", templates.stamp(detail.as_of, now)),
    ]))

    hidden = [_hidden("csrf", csrf), _hidden("form", form),
              _hidden("revision", task.revision)]
    if claim and claim.epoch is not None:
        hidden.append(_hidden("epoch", claim.epoch))
    for name, value in option.params:
        hidden.append(_hidden(name, value))

    fields = "".join(_field(spec) for spec in option.fields)
    fields_block = f'<div class="fields">{fields}</div>' if fields else (
        '<p class="section-note">This action takes no additional fields.</p>')
    parts.append(
        f'<form class="action" method="post" action="{esc(confirm_url(task.id, option))}">'
        + "".join(hidden)
        + fields_block
        + f'<button type="submit">Confirm {esc(option.label.lower())}</button>'
        + "</form>")
    parts.append(
        '<p class="section-note">Nothing has been submitted yet. When you confirm, this server '
        "runs the loom front door once, as the identity above, with the revision and epoch shown. "
        "There is no automatic retry: if the registry has moved, the action is refused and you are "
        "shown the new revision. A worker is never dispatched and no policy pass runs.</p>")

    body = "\n".join(parts)
    footer = (f'<p>Task <span class="mono">{esc(task.id)}</span> &middot; '
              f"revision <span class=\"mono\">{esc(task.revision)}</span></p>")
    return templates.shell(title=f"Loom — confirm {option.label}", current="tasks",
                           version=version, body=body, footer=footer, actions=True,
                           actor=panel.identity, roles=panel.roles)


def _event_link(outcome: Outcome) -> str:
    seq = outcome.event_seq
    if seq is None:
        return ('<span class="muted">the engine accepted the action, but no matching audit '
                "event could be identified in the re-read; check the task's event history</span>")
    href = f"{_task_path(outcome.task_id)}#event-{seq}"
    return (f'<a class="mono" href="{esc(href)}">event {esc(seq)}</a> '
            f'<span class="muted">({esc(outcome.event.get("event"))} at '
            f'{esc(outcome.event.get("ts"))})</span>')


def outcome_page(outcome: Outcome, version: EngineVersion, now: datetime,
                 panel: Panel) -> str:
    option = outcome.option
    run = outcome.run
    parts: list[str] = []
    if run.ok:
        parts.append("<h1>Action recorded</h1>")
        parts.append(
            f'<p class="lede">{task_link(outcome.task_id)} &middot; {esc(option.label)} '
            f"was accepted by the front door.</p>")
    elif run.kind == "conflict":
        parts.append("<h1>Nothing was written: the task moved</h1>")
        parts.append(
            f'<p class="lede">{task_link(outcome.task_id)} &middot; {esc(option.label)} '
            "was refused because the revision or lease you presented is no longer current. "
            "The engine did not retry, and neither did this server.</p>")
    elif run.kind == "denied":
        parts.append("<h1>Nothing was written: Loom refused</h1>")
        parts.append(
            f'<p class="lede">{task_link(outcome.task_id)} &middot; {esc(option.label)} '
            "did not pass a gate. The registry is unchanged.</p>")
    else:
        parts.append("<h1>The action could not be confirmed</h1>")
        parts.append(
            f'<p class="lede">{task_link(outcome.task_id)} &middot; {esc(option.label)} '
            "did not complete cleanly.</p>")

    rows = [
        ("task", task_link(outcome.task_id)),
        ("action", esc(option.label)),
        ("acting as", esc(outcome.actor)),
        ("revision presented", f'<span class="mono">{esc(outcome.revision_before)}</span>'),
        ("state before", state_badge(outcome.status_before)),
        ("exit status", f'<span class="mono">{esc(outcome.run.exit_code if outcome.run.exit_code is not None else "no exit")}</span>'),
    ]
    if outcome.revision_after is not None:
        rows.append(("revision now", f'<span class="mono">{esc(outcome.revision_after)}</span>'))
    if outcome.status_after is not None:
        rows.append(("state now", state_badge(outcome.status_after)))
    if run.ok:
        rows.append(("audit event", _event_link(outcome)))
    parts.append(_facts(rows))

    if run.ok:
        parts.append('<h2 id="next">What changed</h2>')
        parts.append(
            '<p class="section-note">The engine\u2019s own words are quoted below; nothing here '
            "is a summary of them. The task page and the audit event are the record.</p>")
    elif run.kind == "timeout":
        parts.append(
            '<div class="banner" role="status"><h2>Outcome unknown</h2>'
            "<p>The front door did not return, so this server cannot say whether the change "
            "landed. Re-read the task before doing anything else: retrying blind could repeat "
            "an action that already succeeded.</p></div>")
    else:
        parts.append(
            '<div class="banner" role="status"><h2>No state change</h2>'
            "<p>The command exited without changing the registry. Reload the task to act on its "
            "current revision; the same form is not resubmitted for you.</p></div>")

    output = esc(run.stdout or "(no output on stdout)")
    error = esc(run.stderr or "(no output on stderr)")
    parts.append(f'<pre class="output" aria-label="Standard output">{output}</pre>')
    if run.stderr:
        parts.append(f'<pre class="output" aria-label="Standard error">{error}</pre>')

    if run.kind == "conflict":
        parts.append(
            '<p class="section-note">A conflict means someone else acted first — a claim, a '
            "renewal, a takeover or a transition. Read the current state before choosing again; "
            "a takeover, not a refreshed claim, is what recovers a lapsed lease.</p>")

    links = [(_task_path(outcome.task_id), "Re-read the task")]
    if outcome.revision_after is not None:
        fresh = Option(id=option.id, label=option.label, summary=option.summary,
                       effect=option.effect, available=outcome.offered_again, reason=None,
                       roles=option.roles, fields=option.fields, params=option.params)
        if outcome.offered_again:
            links.append((confirm_url(outcome.task_id, fresh), "Try again on the new revision"))
    links += [("/tasks", "All tasks"), ("/", "Focus")]
    parts.append('<p>' + " &middot; ".join(
        f'<a href="{esc(href)}">{esc(label)}</a>' for href, label in links) + "</p>")

    parts.append(
        '<p class="section-note">Reloading this page is safe: the form that produced it is spent. '
        "Submitting a spent form again is refused and executes nothing.</p>")

    body = "\n".join(parts)
    footer = (f'<p>Task <span class="mono">{esc(outcome.task_id)}</span> &middot; '
              f"action <span class=\"mono\">{esc(option.id)}</span> &middot; "
              f'observed {templates.stamp(now.isoformat(timespec="seconds").replace("+00:00", "Z"), now)}</p>')
    return templates.shell(title=f"Loom — {option.label}", current="tasks", version=version,
                           body=body, footer=footer, actions=True,
                           actor=panel.identity, roles=panel.roles)


def not_available_page(detail: TaskDetail, option: Option, panel: Panel,
                       version: EngineVersion) -> str:
    """The action is real but this session may not take it from this state."""
    reason = option.reason or "the engine would refuse it from this state"
    return templates.notice(
        title=f"{option.label} is not available", status=409,
        message=reason,
        details=(f"task {detail.task.id} is {detail.task.status} at revision "
                 f"{detail.task.revision}",),
        links=((_task_path(detail.task.id), "Back to the task"), ("/", "Focus")))


def notice(title: str, status: int, message: str, details: tuple[str, ...] = (),
           links: tuple[tuple[str, str], ...] = ()) -> str:
    return templates.notice(title, status, message, details, links)


__all__ = ["Outcome", "confirm_url", "confirm_page", "outcome_page", "not_available_page",
           "notice"]
