"""The three owner-facing pages: control room, task index, task inspector.

These render the read models in :mod:`loom_ui.control`. They add no facts of their own: every value
shown is a field of the model, formatted and escaped. The templates keep the same boundaries as the
shell — one ``h1`` per page, a caption on every table, ``scope`` on every header cell, a focusable
scroll region around every wide table, and no script.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from . import templates
from .actions import Panel
from .control import ControlRoom, QueueItem, TaskDetail
from .model import RegistryView
from .provenance import Signal
from .templates import esc, state_badge, task_link
from .version import EngineVersion

DETAIL_CLIP = 260


def clip(value: str, limit: int = DETAIL_CLIP) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _table(caption: str, columns: tuple[tuple[str, bool], ...], rows: list[str],
           label: str) -> str:
    heads = "".join(
        (f'<th scope="col" class="num">{esc(name)}</th>' if numeric
         else f'<th scope="col">{esc(name)}</th>')
        for name, numeric in columns)
    if not rows:
        return '<p class="empty">No rows.</p>'
    return (
        f'<div class="table-scroll" role="region" aria-label="{esc(label)}" tabindex="0">'
        "<table>"
        f"<caption>{esc(caption)}</caption>"
        f"<thead><tr>{heads}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>")


def _facts(rows: list[tuple[str, str]]) -> str:
    body = "".join(f"<dt>{esc(name)}</dt><dd>{value}</dd>" for name, value in rows)
    return f'<dl class="facts">{body}</dl>'


def _program_filter(programs: tuple[str, ...], current: str | None, base: str = "/") -> str:
    if not programs:
        return ""
    items = []
    all_active = ' aria-current="page"' if not current else ""
    items.append(f'<li><a href="{esc(base)}"{all_active}>All programs</a></li>')
    for name in programs:
        href = f"{base}?program={quote(name, safe='')}"
        active = ' aria-current="page"' if current == name else ""
        items.append(f'<li><a href="{esc(href)}"{active}>{esc(name)}</a></li>')
    return ('<nav class="filters" aria-label="Filter by program"><ul>'
            + "".join(items) + "</ul></nav>")


def _claim_cell(task) -> str:
    claim = task.claim
    if claim is None:
        return '<span class="muted">no claim</span>'
    state = "expired" if claim.expired else ("live" if claim.active else "not live")
    lease = claim.lease_expires or "no expiry recorded"
    return (f'{esc(claim.agent)}<br><span class="detail">epoch {esc(claim.epoch)} &middot; '
            f'{esc(state)} &middot; {esc(lease)}</span>')


def _queue_row(item: QueueItem) -> str:
    task = item.task
    reasons = "; ".join(item.bucket.reasons)
    return (
        "<tr>"
        f"<td>{task_link(task.id)}<br><span class=\"detail\">{esc(task.title)}</span></td>"
        f"<td>{state_badge(task.status)}</td>"
        f"<td>{templates.claimability_badge(task.claimability.claimable)}</td>"
        f"<td><span class=\"why\">{esc(reasons)}</span></td>"
        f"<td class=\"detail\">{esc(clip(item.bucket.detail))}</td>"
        f"<td>{_claim_cell(task)}</td>"
        f"<td class=\"num\">r{esc(task.revision)}</td>"
        f"<td class=\"num\">+{esc(len(task.downstream))}</td>"
        "</tr>")


def _queue_section(queue, as_of: str, now: datetime) -> str:
    heading_id = f"q-{queue.id}-h"
    label, stale = templates.freshness(as_of, now)
    caption = (f"All rows are one registry sample from {as_of} ({label}); "
               "they are not independent reads.")
    note = f"{queue.description} Sample: {as_of} ({label})."
    if stale:
        caption = ("STALE SAMPLE: this read is " + label + f" old ({as_of}); reload before "
                   "acting. " + caption)
        note = "STALE SAMPLE. " + note
    if queue.items:
        columns = (("Task", False), ("State", False), ("Claimable", False), ("Why here", False),
                   ("Detail", False), ("Lease / agent", False), ("Rev", True), ("Unblocks", True))
        table = _table(caption, columns,
                       [_queue_row(item) for item in queue.items], queue.title)
    else:
        table = '<p class="empty">Nothing in this queue in this sample.</p>'
    return (
        f'<section class="queue" id="q-{esc(queue.id)}" aria-labelledby="{esc(heading_id)}">'
        f'<h2 id="{esc(heading_id)}">{esc(queue.title)} '
        f'<span class="count">({len(queue.items)})</span></h2>'
        f'<p class="section-note">{esc(note)}</p>'
        f"{table}</section>")


def control_page(control: ControlRoom, view: RegistryView, version: EngineVersion,
                 signals: tuple[Signal, ...], now: datetime,
                 actions: bool = False) -> str:
    label, stale = templates.freshness(control.as_of, now)
    lede = (f"Local view of the portfolio registry. One registry sample from "
            f"{control.as_of} ({label}). "
            f"{control.counts['total']} task(s) in scope. "
            "State and claimability are separate facts; each queue names why a task is there.")
    if control.program:
        lede += f" Filtered to program {control.program}."
    parts = ["<h1>Owner control room</h1>", f'<p class="lede">{esc(lede)}</p>',
             templates.degraded_banner(signals)]
    if stale:
        parts.append(
            '<div class="banner" role="status"><h2>Stale sample</h2>'
            f"<p>This page was rendered from a registry sample taken {esc(label)}. "
            "Claimability and leases may have changed since. Reload before acting on it.</p></div>")
    if control.gaps:
        parts.append('<div class="banner" role="status"><h2>Read-model gaps</h2><ul class="plain">'
                     + "".join(f"<li>{esc(gap)}</li>" for gap in control.gaps)
                     + "</ul></div>")

    cards = "".join(
        f'<li><a href="#q-{esc(queue.id)}"><span class="n">{esc(len(queue.items))}</span>'
        f'<span class="k">{esc(queue.title)}</span></a></li>'
        for queue in control.queues)
    parts.append('<h2 id="summary">Queues at a glance</h2>')
    parts.append(f'<ul class="cards" aria-labelledby="summary">{cards}</ul>')
    parts.append(_program_filter(tuple(sorted({task.program for task in view.tasks})),
                                 control.program))

    for queue in control.queues:
        if not queue.in_daily_queues:
            continue
        parts.append(_queue_section(queue, control.as_of, now))
    settled = control.settled
    if settled is not None and settled.items:
        parts.append(
            f'<p class="section-note">Plus {len(settled.items)} settled task(s) '
            f'({esc(", ".join(sorted({item.task.status for item in settled.items})))}) — '
            f'<a href="/tasks">see All tasks</a>.</p>')

    parts.append('<h2 id="provenance">Source provenance</h2>')
    parts.append('<p class="section-note">Each signal is sampled on its own; a healthy database '
                 "does not make the scheduler or the evidence files current.</p>")
    parts.append(templates.signals_table(signals, now))

    body = "\n".join(part for part in parts if part)
    footer = templates.footer_snapshot(control.as_of, view.digest, view.head_seq, view.db,
                                       now, version)
    return templates.shell(title="Loom — owner control room", current="focus", version=version,
                           body=body, footer=footer, program=control.program, actions=actions)


def tasks_page(control: ControlRoom, view: RegistryView, version: EngineVersion,
               signals: tuple[Signal, ...], now: datetime, actions: bool = False) -> str:
    label, stale = templates.freshness(control.as_of, now)
    rows = []
    for row in control.rows:
        task = row.task
        reasons = "; ".join(row.bucket.reasons)
        last = row.last_event
        last_cell = (f'{esc(last.event)}<br><span class="detail">{esc(last.at)}</span>'
                     if last else '<span class="muted">none</span>')
        rows.append(
            "<tr>"
            f"<td>{task_link(task.id)}<br><span class=\"detail\">{esc(task.title)}</span></td>"
            f"<td>{esc(task.program)}</td>"
            f"<td>{state_badge(task.status)}</td>"
            f"<td>{templates.claimability_badge(task.claimability.claimable)}</td>"
            f"<td>{esc(row.bucket.title)}<br><span class=\"detail\">{esc(clip(reasons, 120))}"
            "</span></td>"
            f"<td class=\"detail\">{esc(clip(row.bucket.detail, 180))}</td>"
            f"<td>{_claim_cell(task)}</td>"
            f"<td class=\"num\">r{esc(task.revision)}</td>"
            f"<td>{last_cell}</td>"
            f"<td class=\"num\">+{esc(len(task.downstream))}</td>"
            "</tr>")

    columns = (("Task", False), ("Program", False), ("State", False), ("Claimable", False),
               ("Queue / why", False), ("Blocker or detail", False), ("Lease / agent", False),
               ("Rev", True), ("Last event", False), ("Unblocks", True))
    scope = f"program {control.program}" if control.program else "every program"
    caption = (f"All {len(rows)} task(s) in {scope}, from one registry sample at "
               f"{control.as_of} ({label}).")
    if stale:
        caption = "STALE SAMPLE. " + caption

    parts = [
        "<h1>All tasks</h1>",
        '<p class="lede">Every task with its lifecycle state, claimability and the queue it '
        "belongs to. The state is not the claimability: a ready task can still be blocked.</p>",
        templates.degraded_banner(signals),
        _program_filter(tuple(sorted({task.program for task in view.tasks})),
                        control.program, base="/tasks"),
        _table(caption, columns, rows, "All tasks"),
    ]
    body = "\n".join(part for part in parts if part)
    footer = templates.footer_snapshot(control.as_of, view.digest, view.head_seq, view.db,
                                       now, version)
    return templates.shell(title="Loom — all tasks", current="tasks", version=version,
                           body=body, footer=footer, program=control.program, actions=actions)


def _link_list(links, caption: str, label: str, empty: str) -> str:
    if not links:
        return f'<p class="empty">{esc(empty)}</p>'
    rows = "".join(
        "<tr>"
        f"<td>{task_link(link.id)}</td>"
        f"<td>{state_badge(link.status)}</td>"
        f"<td>{templates.claimability_badge(link.claimable)}</td>"
        f"<td class=\"num\">r{esc(link.revision)}</td>"
        f"<td>{esc(link.title)}</td>"
        "</tr>" for link in links)
    return _table(caption, (("Task", False), ("State", False), ("Claimable", False),
                            ("Rev", True), ("Title", False)), [rows], label)


def _questions_table(detail: TaskDetail) -> str:
    if not detail.questions:
        return '<p class="empty">No question has been asked about this task.</p>'
    rows = []
    for question in detail.questions:
        about = question.review_source or ""
        if about:
            about = (f"review r{question.revision} source {about[:12]}"
                     f", evidence {question.review_evidence or 'none'}")
        answer = ""
        if question.answered:
            answer = (f"event {question.answer_seq}: {question.answer_decision or 'answered'}"
                      f" by {question.answered_by or 'unknown'}")
            if question.answer_note:
                answer += f" — {clip(question.answer_note, 180)}"
        question_badge = templates.badge(
            question.state, "degraded" if question.state == "open" else "neutral")
        rows.append(
            "<tr>"
            f"<td class=\"num\">{esc(question.seq)}</td>"
            f"<td>{esc(question.asked_by)}</td>"
            f"<td>{esc(question.asked_to)}</td>"
            f"<td>{esc(question.question)}<br><span class=\"detail\">{esc(about)}</span></td>"
            f"<td>{question_badge}"
            f"<br><span class=\"detail\">r{esc(question.revision)} &middot; "
            f"{esc(question.asked_at)}</span></td>"
            f"<td class=\"detail\">{esc(answer) or '<span class=\"muted\">open</span>'}</td>"
            "</tr>")
    return _table("Questions and review requests for this task, oldest first.",
                  (("Event", True), ("From", False), ("To", False), ("Question", False),
                   ("State", False), ("Answer", False)), rows, "Questions")


def _review_section(detail: TaskDetail) -> str:
    review = detail.review
    if review is None:
        verdict = '<p class="empty">No review verdict is recorded on this task.</p>'
    else:
        rows = [
            ("reviewer", esc(review.reviewer)),
            ("decision", esc(review.decision)),
            ("source commit", f'<span class="mono">{esc(review.source or "none")}</span>'),
            ("base commit", f'<span class="mono">{esc(review.base or "root")}</span>'),
            ("evidence", f'<span class="mono">{esc(review.evidence or "none")}</span>'),
            ("approval event", esc(review.answer_seq if review.answer_seq is not None
                                  else "not recorded")),
            ("approved content", esc(review.digest or "no digest (incomplete receipt)")),
            ("approved paths", esc(review.path_count)),
            ("severity", esc(review.severity or "not recorded")),
        ]
        verdict = _facts(rows)
    return (verdict
            + '<h3 id="questions">Inbox and review requests</h3>'
            + _questions_table(detail))


def _receipt_section(detail: TaskDetail) -> str:
    receipt = detail.receipt
    if receipt is None:
        return '<p class="empty">No integration receipt is recorded in the event log.</p>'
    rows = [
        ("recorded by", esc(receipt.kind)),
        ("event", esc(receipt.event_seq)),
        ("recorded at", esc(receipt.recorded_at)),
        ("integrated commit", f'<span class="mono">{esc(receipt.source or "none")}</span>'),
        ("integration tip", f'<span class="mono">{esc(receipt.tip or "none")}</span>'),
        ("approved content", esc(receipt.digest or "no digest (incomplete receipt)")),
        ("approved paths", esc(receipt.path_count if receipt.path_count is not None
                              else "not recorded")),
        ("reason", esc(receipt.reason or "none")),
    ]
    return _facts(rows)


def _attempts_table(detail: TaskDetail) -> str:
    if not detail.attempts:
        return '<p class="empty">No attempt is recorded for this task.</p>'
    rows = []
    for attempt in detail.attempts:
        state = attempt.state or "unknown"
        if attempt.live:
            state += " (process live)"
        place = attempt.worktree or ""
        if attempt.branch:
            place += f" on {attempt.branch}" if place else attempt.branch
        rows.append(
            "<tr>"
            f"<td class=\"mono\">{esc(attempt.attempt)}</td>"
            f"<td>{esc(attempt.role)}<br><span class=\"detail\">{esc(attempt.agent)}</span></td>"
            f"<td>{esc(state)}<br><span class=\"detail\">pid {esc(attempt.pid or '-')}</span></td>"
            f"<td class=\"num\">r{esc(attempt.revision)}<br>"
            f"<span class=\"detail\">epoch {esc(attempt.epoch)}</span></td>"
            f"<td class=\"detail\">{esc(attempt.opened or '')}<br>{esc(attempt.closed or '')}</td>"
            f"<td class=\"detail mono\">{esc(place)}</td>"
            "</tr>")
    return _table("One row per agent run; a live process may outlive a closed attempt.",
                  (("Attempt", False), ("Role / agent", False), ("State", False),
                   ("Revision", True), ("Opened / closed", False), ("Checkout", False)),
                  rows, "Attempts")


def _events_table(detail: TaskDetail) -> str:
    if not detail.events:
        return '<p class="empty">No event names this task.</p>'
    rows = "".join(
        f'<tr id="event-{esc(event.seq)}">'
        f"<td class=\"num\">{esc(event.seq)}</td>"
        f"<td class=\"detail\">{esc(event.at)}</td>"
        f"<td>{esc(event.event)}</td>"
        f"<td class=\"num\">r{esc(event.revision)}</td>"
        f"<td>{esc(event.agent)}</td>"
        f"<td class=\"detail\">{esc(clip(event.summary, 320))}</td>"
        "</tr>" for event in detail.events)
    shown = len(detail.events)
    caption = (f"Append-only audit log for this task, oldest first. Showing {shown} of "
               f"{detail.events_total} event(s); corrections are new events, never edits.")
    return _table(caption, (("Seq", True), ("At", False), ("Event", False), ("Rev", True),
                            ("Agent", False), ("Detail", False)), [rows], "Event history")


def _actions_section(panel: Panel, detail: TaskDetail) -> str:
    if not panel.enabled:
        return ('<p class="empty">This server has no configured human identity, so it cannot act '
                "on a task. Starting it with a configured identity is a deliberate step, not a "
                "default.</p>")
    parts = [f'<p class="section-note">Acting as <strong>{esc(panel.identity or "nobody")}</strong>'
             f'{esc(" (" + ", ".join(panel.roles) + ")" if panel.roles else "")}. '
             "The identity is bound by the server at pairing time and cannot be chosen by this "
             "page. Loom still decides whether each action is legal; the UI only decides what to "
             "offer.</p>"]
    if not panel.identity:
        parts.append(
            '<div class="banner" role="status"><h2>Not signed in</h2>'
            "<p>Open the pairing link this server printed when it started. Without a paired "
            "session no action can be submitted, and no form is rendered.</p></div>")
        return "".join(parts)

    available = [option for option in panel.options if option.available]
    unavailable = [option for option in panel.options if not option.available]

    if available:
        items = "".join(
            f'<li><a href="{esc(_confirm_href(detail.task.id, option))}">{esc(option.label)}</a> '
            f'<span class="detail">— {esc(option.summary)}</span><br>'
            f'<span class="detail">{esc(option.effect)}</span></li>'
            for option in available)
        parts.append(f'<ul class="actions-list">{items}</ul>')
        parts.append('<p class="section-note">Each link opens a confirmation page showing the '
                     "revision, epoch and lease it will present. Nothing is sent until you "
                     "confirm there.</p>")
    else:
        parts.append('<p class="empty">No action is available to you on this task right now. '
                     "The reasons are below.</p>")

    if unavailable:
        shown = "".join(
            f"<li><strong>{esc(option.label)}</strong> — {esc(option.reason or 'not available')}</li>"
            for option in unavailable)
        parts.append(
            f'<details><summary>{len(unavailable)} action(s) not available, and why</summary>'
            f'<ul class="plain">{shown}</ul></details>')
    return "".join(parts)


def _confirm_href(task_id: str, option) -> str:
    from .action_pages import confirm_url

    return confirm_url(task_id, option)


def task_page(detail: TaskDetail, view: RegistryView, version: EngineVersion,
              signals: tuple[Signal, ...], now: datetime,
              panel: Panel | None = None) -> str:
    task = detail.task
    label, stale = templates.freshness(detail.as_of, now)
    claim = task.claim
    parts: list[str] = []
    parts.append(f'<h1 class="mono">{esc(task.id)}</h1>')
    parts.append(f'<p class="lede">{esc(task.title)} &middot; program {esc(task.program)} '
                 f'&middot; board {esc(task.board)}</p>')
    parts.append('<p><a href="/tasks">← All tasks</a> &middot; <a href="/">Focus</a></p>')
    parts.append(templates.degraded_banner(signals))
    if stale:
        parts.append('<div class="banner" role="status"><h2>Stale sample</h2>'
                     f"<p>The registry was read {esc(label)}; claimability and leases may have "
                     "changed. Reload before acting.</p></div>")

    reasons = "".join(f"<li>{esc(reason)}</li>" for reason in detail.bucket.reasons)
    parts.append(
        '<div class="banner" role="status">'
        f'<h2>Queue: {esc(detail.bucket.title)}</h2>'
        f'<ul class="plain">{reasons}</ul>'
        f'<p>{esc(detail.bucket.detail)}</p></div>')

    parts.append('<section aria-labelledby="state-h">')
    parts.append('<h2 id="state-h">State and claimability</h2>')
    parts.append('<p class="section-note">These are different facts. The state is where the task '
                 "is in its lifecycle; claimability says whether `loom claim` would succeed right "
                 "now.</p>")
    facts = [
        ("state", state_badge(task.status)),
        ("claimable", templates.claimability_badge(task.claimability.claimable)),
        ("revision", f'<span class="mono">{esc(task.revision)}</span>'),
        ("task epoch", f'<span class="mono">{esc(task.epoch)}</span>'),
        ("fencing epoch (claim)", '<span class="mono">'
                                  f'{esc(claim.epoch if claim else "no claim")}</span>'),
        ("program / board", f'{esc(task.program)} / {esc(task.board)}'),
        ("goal", esc(task.goal or "not recorded")),
        ("owner repo", f'<span class="mono">{esc(task.owner_repo or "not recorded")}</span>'),
        ("write paths", f'<span class="mono">{esc(", ".join(task.write_paths) or "none")}</span>'),
        ("blocked needs", esc(task.blocked_needs or "not blocked")),
        ("created / updated", f'{esc(task.created_at or "?")} / {esc(task.updated_at or "?")}'),
        ("acceptance", esc("; ".join(task.acceptance) or "not recorded")),
    ]
    parts.append(_facts(facts))
    parts.append("</section>")

    parts.append('<section aria-labelledby="claimability-h">')
    parts.append('<h2 id="claimability-h">Why it can or cannot be claimed</h2>')
    if task.claimability.reasons:
        parts.append('<ul class="plain">' + "".join(
            f"<li>{esc(reason)}</li>" for reason in task.claimability.reasons) + "</ul>")
    else:
        parts.append('<p class="empty">Every claim check passes: state, dependencies, write paths '
                     "and lease.</p>")
    if task.blockers:
        parts.append('<h3 id="blockers">Blockers</h3>')
        parts.append('<ul class="plain">' + "".join(
            f"<li>{templates.badge(blocker.kind, 'degraded')} {esc(blocker.detail)}</li>"
            for blocker in task.blockers) + "</ul>")
    parts.append("</section>")

    if panel is not None:
        parts.append('<section aria-labelledby="actions-h">')
        parts.append('<h2 id="actions-h">Actions</h2>')
        parts.append(_actions_section(panel, detail))
        parts.append("</section>")

    parts.append('<section aria-labelledby="claim-h">')
    parts.append('<h2 id="claim-h">Claim and lease</h2>')
    if claim is None:
        parts.append('<p class="empty">No claim is recorded on the task.</p>')
    else:
        remaining = (f"{claim.remaining_seconds}s" if claim.remaining_seconds is not None
                     else "unknown")
        lease_state = ("active" if claim.active else
                       ("expired" if claim.expired else "not active"))
        parts.append(_facts([
            ("agent", esc(claim.agent)),
            ("claim epoch", esc(claim.epoch)),
            ("base revision", esc(claim.base_revision)),
            ("lease state", f"{esc(lease_state)} ({esc(remaining)} remaining)"),
            ("lease expires", templates.stamp(claim.lease_expires, now)),
            ("heartbeat", templates.stamp(claim.heartbeat, now)),
        ]))
        if claim.expired:
            parts.append('<p class="note">An expired lease cannot be renewed or used to move the '
                         "task. Takeover, after the configured lock delay, is the recorded way to "
                         "continue it — and it increments the fencing epoch.</p>")
    parts.append("</section>")

    parts.append('<section aria-labelledby="deps-h">')
    parts.append('<h2 id="deps-h">Dependencies and dependents</h2>')
    parts.append('<h3 id="upstream">Depends on</h3>')
    parts.append(_link_list(detail.upstream,
                            "Prerequisites of this task; any unmet one is a blocker.",
                            "Upstream dependencies", "This task has no prerequisites."))
    parts.append('<h3 id="downstream">Gates</h3>')
    parts.append(_link_list(
        detail.downstream,
        "Tasks that cannot be satisfied until this one is integrated or accepted.",
        "Downstream dependents", "Nothing depends on this task."))
    parts.append("</section>")

    parts.append('<section aria-labelledby="evidence-h">')
    parts.append('<h2 id="evidence-h">Evidence</h2>')
    evidence = detail.evidence
    state = "present" if evidence.exists else "missing"
    evidence_facts = [
        ("locator", f'<span class="mono">{esc(evidence.locator or "none")}</span>'),
        ("resolved", f'<span class="mono">{esc(evidence.resolved or "unresolved")}</span>'),
        ("file", esc(state)),
    ]
    if evidence.exists:
        evidence_facts.append(("size", f"{esc(evidence.size)} bytes"))
        evidence_facts.append(("modified", esc(evidence.modified)))
    else:
        evidence_facts.append(("problem", esc(evidence.error or "not found")))
    parts.append(_facts(evidence_facts))
    parts.append('<p class="section-note">The file is read from disk when the page is rendered; '
                 "the UI links to the locator rather than serving the file.</p>")
    parts.append("</section>")

    parts.append('<section aria-labelledby="review-h">')
    parts.append('<h2 id="review-h">Review</h2>')
    parts.append(_review_section(detail))
    parts.append("</section>")

    parts.append('<section aria-labelledby="receipt-h">')
    parts.append('<h2 id="receipt-h">Integration receipt</h2>')
    parts.append('<p class="section-note">A receipt proves a reviewed commit was integrated. It is '
                 "not a merge button and never a promise that the content is unchanged.</p>")
    parts.append(_receipt_section(detail))
    parts.append("</section>")

    parts.append('<section aria-labelledby="attempts-h">')
    parts.append('<h2 id="attempts-h">Attempts</h2>')
    parts.append(_attempts_table(detail))
    parts.append("</section>")

    parts.append('<section aria-labelledby="events-h">')
    parts.append('<h2 id="events-h">Event history</h2>')
    parts.append(_events_table(detail))
    parts.append("</section>")

    parts.append('<section aria-labelledby="prov-h">')
    parts.append('<h2 id="prov-h">Source provenance</h2>')
    parts.append(templates.signals_table(signals, now))
    parts.append(_freshness_note(detail.as_of, now))
    parts.append("</section>")

    body = "\n".join(part for part in parts if part)
    footer = templates.footer_snapshot(detail.as_of, view.digest, view.head_seq, view.db,
                                       now, version)
    return templates.shell(title=f"Loom — {task.id}", current="tasks", version=version,
                           body=body, footer=footer,
                           actions=bool(panel and panel.enabled),
                           actor=panel.identity if panel else None,
                           roles=panel.roles if panel else ())


def _freshness_note(as_of: str, now: datetime) -> str:
    label, stale = templates.freshness(as_of, now)
    css = "stale" if stale else "ok"
    return (f'<p class="section-note">The registry above was read at '
            f'<span class="mono">{esc(as_of)}</span> '
            f'<span class="freshness {css}">({esc(label)})</span>. The evidence, git and process '
            "signals were sampled separately at their own times.</p>")


__all__ = ["control_page", "tasks_page", "task_page", "clip"]
