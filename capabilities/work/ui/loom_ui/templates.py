"""Server-rendered HTML shell.

No JavaScript runs in the shell. That is a deliberate boundary, not a limitation: with no script,
untrusted task text cannot become executable, and there is no client code that could be tempted to
invent a mutation. The CSP still forbids everything but inline style, so a future regression that
injects a script tag fails closed in the browser.

Every value passes through :func:`esc` before it reaches the page.
"""
from __future__ import annotations

import html

from . import __version__
from .model import RegistryView, claimable, expired_leases
from .provenance import Signal
from .version import EngineVersion

CSS = """
:root { color-scheme: light dark; --ink:#16202a; --muted:#5b6b7a; --line:#d7dee5;
        --bg:#fbfcfd; --panel:#fff; --accent:#0b6bcb; --warn:#8a4b00; --bad:#a11616; }
@media (prefers-color-scheme: dark) {
  :root { --ink:#e8eef4; --muted:#9fb0c0; --line:#2c3a47; --bg:#101820; --panel:#16212b;
          --accent:#6cb0ff; --warn:#f0b060; --bad:#ff8a8a; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font: 15px/1.5 -apple-system, "SF Text", "Segoe UI", Roboto, sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted);
     margin: 36px 0 10px; }
p.lede { margin: 0 0 18px; color: var(--muted); }
.badge { display:inline-block; border:1px solid var(--line); border-radius:999px;
         padding:2px 10px; font-size:12px; font-weight:600; }
.badge.ok { color: var(--accent); }
.badge.degraded { color: var(--warn); }
.cards { display:flex; flex-wrap:wrap; gap:10px; margin:0; padding:0; list-style:none; }
.cards li { border:1px solid var(--line); border-radius:10px; background:var(--panel);
            padding:10px 14px; min-width:128px; }
.cards .n { display:block; font-size:22px; font-weight:650; }
.cards .k { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }
table { width:100%; border-collapse:collapse; background:var(--panel);
        border:1px solid var(--line); border-radius:10px; overflow:hidden; }
th, td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line);
         vertical-align:top; font-size:14px; }
th { font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:var(--muted);
     font-weight:650; }
tr:last-child td { border-bottom:0; }
td.num, th.num { text-align:right; font-variant-numeric: tabular-nums; }
.empty { color:var(--muted); font-style:italic; }
code, .mono { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size:12.5px; }
.note { border-left:3px solid var(--accent); padding:10px 14px; background:var(--panel);
        border-radius:0 8px 8px 0; }
.status-ok { color: var(--accent); }
.status-degraded { color: var(--warn); }
.status-unavailable { color: var(--bad); }
footer { margin-top:40px; color:var(--muted); font-size:13px; }
"""


def esc(value) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _badge(status: str) -> str:
    css = "ok" if status in {"PINNED", "ok"} else "degraded"
    return f'<span class="badge {css}">{esc(status)}</span>'


def _counts(view: RegistryView) -> str:
    ordered = [("claimable", view.counts.get("claimable", 0)),
               ("total", view.counts.get("total", 0))]
    for status, count in sorted(view.counts.items()):
        if status in {"claimable", "total"}:
            continue
        ordered.append((status, count))
    cells = "".join(
        f'<li><span class="n">{esc(count)}</span><span class="k">{esc(name)}</span></li>'
        for name, count in ordered)
    return f'<ul class="cards">{cells}</ul>'


def _signals(signals: tuple[Signal, ...]) -> str:
    rows = []
    for signal in signals:
        rows.append(
            "<tr>"
            f"<th scope=\"row\">{esc(signal.source)}</th>"
            f"<td class=\"status-{esc(signal.status)}\">{esc(signal.status)}</td>"
            f"<td class=\"mono\">{esc(signal.as_of)}</td>"
            f"<td>{esc(signal.detail)}</td>"
            "</tr>")
    return ("<table><caption class=\"empty\">Each source is sampled independently; "
            "their times are not one transaction.</caption>"
            "<thead><tr><th>Source</th><th>Status</th><th>As of</th><th>Detail</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


def _claimable(view: RegistryView) -> str:
    tasks = claimable(view)
    if not tasks:
        return '<p class="empty">No task is claimable right now.</p>'
    rows = "".join(
        "<tr>"
        f"<td class=\"mono\">{esc(task.id)}</td>"
        f"<td>{esc(task.status)}</td>"
        f"<td class=\"num\">r{esc(task.revision)}</td>"
        f"<td>{esc(task.title)}</td>"
        f"<td class=\"num\">+{esc(len(task.downstream))}</td>"
        "</tr>"
        for task in tasks)
    return ("<table><thead><tr><th>Task</th><th>State</th><th class=\"num\">Rev</th>"
            "<th>Title</th><th class=\"num\">Downstream</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")


def _attention(view: RegistryView) -> str:
    expired = expired_leases(view)
    blocked = [task for task in view.tasks if task.status == "blocked"]
    if not expired and not blocked:
        return '<p class="empty">No expired leases or blocked tasks.</p>'
    rows = []
    for task in expired:
        claim = task.claim
        rows.append(
            "<tr>"
            f"<td class=\"mono\">{esc(task.id)}</td>"
            "<td>expired lease</td>"
            f"<td>{esc(claim.agent if claim else '')}</td>"
            f"<td class=\"mono\">{esc(claim.lease_expires if claim else '')}</td>"
            "</tr>")
    for task in blocked:
        rows.append(
            "<tr>"
            f"<td class=\"mono\">{esc(task.id)}</td>"
            "<td>blocked</td>"
            f"<td>{esc(task.blocked_needs or 'agent')}</td>"
            f"<td>{esc('; '.join(task.claimability.reasons) or 'no reason recorded')}</td>"
            "</tr>")
    return ("<table><thead><tr><th>Task</th><th>Why</th><th>Owner / needs</th>"
            "<th>Detail</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


def page(view: RegistryView, version: EngineVersion, signals: tuple[Signal, ...]) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Loom — read-only</title>
<style>{CSS}</style>
</head>
<body>
<main>
<header>
  <h1>Loom</h1>
  <p class="lede">Read-only local view of the portfolio registry &middot; ui {esc(__version__)}
  &middot; engine {_badge(version.verdict)}</p>
</header>

<h2>Registry snapshot</h2>
{_counts(view)}

<h2>Claimable now</h2>
{_claimable(view)}

<h2>Needs attention</h2>
{_attention(view)}

<h2>Source provenance</h2>
{_signals(signals)}

<footer>
  <p>Snapshot <span class="mono">{esc(view.as_of)}</span> &middot;
  digest <span class="mono">{esc(view.digest[:16])}</span> &middot;
  event head <span class="mono">{esc(view.head_seq)}</span></p>
  <p>Database <span class="mono">{esc(view.db)}</span><br>
  Front door <span class="mono">{esc(version.front_door)}</span></p>
  <p class="note">This shell is read-only. It exposes no endpoint that changes task state,
  dispatches a worker, or runs a policy pass. Actions belong to <span class="mono">loom</span>
  and arrive in a later, separately reviewed slice.</p>
</footer>
</main>
</body>
</html>
"""


def closed(version: EngineVersion | None, reason: str, status: int) -> str:
    verdict = version.verdict if version else "UNAVAILABLE"
    detail = version.detail if version else reason
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Loom UI — unavailable</title>
<style>{CSS}</style>
</head>
<body>
<main>
<header>
  <h1>Loom UI is not serving</h1>
  <p class="lede">The version gate closed, so no registry data is shown.
  {_badge(verdict)} &middot; HTTP {esc(status)}</p>
</header>
<h2>Why</h2>
<p>{esc(reason)}</p>
<p>{esc(detail)}</p>
{_version_table(version)}
<footer>
  <p class="note">The UI fails closed rather than show values that could disagree with the engine
  that is actually running. Start it from the promoted release, or promote the reviewed commit
  with <span class="mono">loom deploy promote</span>.</p>
</footer>
</main>
</body>
</html>
"""


def _version_table(version: EngineVersion | None) -> str:
    if version is None:
        return ""
    rows = [
        ("verdict", version.verdict),
        ("front door", version.front_door),
        ("running root", version.running_root),
        ("UI engine root", version.ui_root),
        ("release commit", version.release_commit or "none"),
        ("release promoted", version.release_promoted_at or "never"),
        ("release digest", version.release_digest or "none"),
        ("front-door engine digest", version.expected_digest or "unknown"),
        ("UI engine digest", version.actual_digest or "unknown"),
        ("sampled", version.as_of),
    ]
    body = "".join(
        f'<tr><th scope="row">{esc(name)}</th><td class="mono">{esc(value)}</td></tr>'
        for name, value in rows)
    return f"<table><tbody>{body}</tbody></table>"
