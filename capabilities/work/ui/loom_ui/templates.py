"""The server-rendered shell: layout, escaping and the fail-closed pages.

No JavaScript runs in the shell. That is a deliberate boundary, not a limitation: with no script,
untrusted task text cannot become executable, and there is no client code that could be tempted to
invent a mutation. The CSP still forbids everything but inline style, so a future regression that
injects a script tag fails closed in the browser.

Two accessibility rules are structural rather than decorative. Every page has one ``h1``, a skip
link, landmarks (``header``/``nav``/``main``/``footer``) and a keyboard focus ring that does not
depend on colour alone. Tables carry a caption and column ``scope``, and every scroll container is
focusable so a keyboard user can scroll it without a pointing device.

Every value passes through :func:`esc` before it reaches the page.
"""
from __future__ import annotations

import html
from datetime import datetime
from urllib.parse import quote

import store

from . import __version__
from .provenance import Signal
from .version import EngineVersion

#: A value older than this is labelled stale rather than current.
STALE_AFTER_SECONDS = 60

CSS = """
:root { color-scheme: light dark; --ink:#16202a; --muted:#5b6b7a; --line:#d7dee5;
        --bg:#fbfcfd; --panel:#fff; --accent:#0b5cad; --warn:#8a4b00; --bad:#a11616;
        --good:#0b6b3a; }
@media (prefers-color-scheme: dark) {
  :root { --ink:#e8eef4; --muted:#9fb0c0; --line:#2c3a47; --bg:#101820; --panel:#16212b;
          --accent:#6cb0ff; --warn:#f0b060; --bad:#ff9a9a; --good:#6fdc9b; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font: 15px/1.55 -apple-system, "SF Text", "Segoe UI", Roboto, sans-serif; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 0 20px; }
a { color: var(--accent); text-underline-offset: 2px; }
a:focus-visible, [tabindex]:focus-visible, .skip:focus {
  outline: 3px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
.skip { position:absolute; left:-9999px; top:0; background:var(--panel); color:var(--ink);
        padding:10px 14px; border:1px solid var(--line); z-index:10; }
.skip:focus { left:12px; top:12px; }
header.site { border-bottom:1px solid var(--line); background:var(--panel); }
header.site .wrap { display:flex; flex-wrap:wrap; align-items:center; gap:10px 20px;
                    justify-content:space-between; padding-top:10px; padding-bottom:10px; }
.brand { margin:0; font-weight:700; letter-spacing:-0.01em; }
.brand .muted { font-weight:400; }
nav.views ul, nav.filters ul { display:flex; flex-wrap:wrap; gap:4px; margin:0; padding:0;
                               list-style:none; }
nav.views a, nav.filters a { display:inline-block; padding:5px 10px; border-radius:999px;
                             border:1px solid transparent; color:var(--ink);
                             text-decoration:none; font-size:14px; }
nav.views a:hover, nav.filters a:hover { border-color:var(--line); background:var(--bg); }
nav.views a[aria-current="page"] { border-color:var(--line); background:var(--bg);
                                   font-weight:650; }
nav.filters { margin: 0 0 10px; }
nav.filters a[aria-current="page"] { border-color:var(--accent); font-weight:650; }
main { padding-top: 26px; padding-bottom: 60px; }
main:focus { outline:none; }
h1 { font-size:26px; line-height:1.2; margin:0 0 6px; letter-spacing:-0.02em; }
h2 { font-size:18px; margin:34px 0 6px; letter-spacing:-0.01em; }
h3 { font-size:15px; margin:22px 0 6px; }
h2 .count, h3 .count { color:var(--muted); font-weight:600; font-variant-numeric:tabular-nums; }
p { margin: 8px 0; }
p.lede { color:var(--muted); margin:0 0 16px; max-width: 80ch; }
p.section-note { color:var(--muted); margin:0 0 10px; max-width: 90ch; }
.muted { color: var(--muted); }
.mono, code { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12.5px;
              overflow-wrap: anywhere; }
.badge { display:inline-block; border:1px solid var(--line); border-radius:999px;
         padding:1px 9px; font-size:12px; font-weight:650; white-space:nowrap; }
.badge.good { color: var(--good); border-color: var(--good); }
.badge.degraded { color: var(--warn); border-color: var(--warn); }
.badge.bad { color: var(--bad); border-color: var(--bad); }
.badge.neutral { color: var(--muted); }
.claimable-yes { color: var(--good); font-weight:650; }
.claimable-no { color: var(--bad); font-weight:650; }
.cards { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 8px; padding:0; list-style:none; }
.cards li { border:1px solid var(--line); border-radius:10px; background:var(--panel);
            padding:8px 12px; min-width:96px; }
.cards .n { display:block; font-size:20px; font-weight:700; font-variant-numeric:tabular-nums; }
.cards .k { color:var(--muted); font-size:11.5px; text-transform:uppercase; letter-spacing:.06em; }
.cards a { text-decoration:none; color:inherit; }
.table-scroll { overflow-x:auto; border:1px solid var(--line); border-radius:10px;
                background:var(--panel); }
table { width:100%; border-collapse:collapse; }
caption { text-align:left; padding:9px 12px; color:var(--muted); font-size:12.5px;
          border-bottom:1px solid var(--line); }
th, td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line);
         vertical-align:top; font-size:14px; }
th { font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:var(--muted);
     font-weight:700; white-space:nowrap; }
tbody tr:last-child td { border-bottom:0; }
td.num, th.num { text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap; }
.empty { color:var(--muted); font-style:italic; margin:8px 0; }
.why { display:block; font-weight:600; }
.detail { color:var(--muted); }
.banner { border:1px solid var(--warn); border-left-width:4px; background:var(--panel);
          border-radius:0 8px 8px 0; padding:10px 14px; margin:16px 0; }
.banner h2 { margin:0 0 4px; font-size:15px; }
.note { border-left:3px solid var(--accent); padding:10px 14px; background:var(--panel);
        border-radius:0 8px 8px 0; }
.freshness { font-size:12.5px; }
.freshness.stale { color:var(--warn); font-weight:700; }
.freshness.ok { color:var(--muted); }
dl.facts { display:grid; grid-template-columns: max-content minmax(0, 1fr); gap:6px 20px;
           margin:8px 0 0; }
dl.facts dt { color:var(--muted); font-size:13px; }
dl.facts dd { margin:0; overflow-wrap:anywhere; }
section.queue { margin-top:8px; }
ul.plain { margin:8px 0; padding-left:22px; }
ul.plain li { margin:3px 0; }
footer.site { border-top:1px solid var(--line); color:var(--muted); font-size:13px;
              padding:18px 0 40px; margin-top:44px; }
footer.site p { margin:4px 0; }
@media (max-width: 640px) {
  dl.facts { grid-template-columns: 1fr; gap:0; }
  dl.facts dd { margin-bottom: 8px; }
  h1 { font-size:22px; }
}
"""


def esc(value) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def task_url(task_id: str) -> str:
    return "/task/" + quote(str(task_id), safe="/")


def task_link(task_id: str, label: str | None = None, *, mono: bool = True) -> str:
    css = ' class="mono"' if mono else ""
    return (f'<a{css} href="{esc(task_url(task_id))}">'
            f'{esc(label if label is not None else task_id)}</a>')


def badge(text, kind: str = "neutral") -> str:
    return f'<span class="badge {esc(kind)}">{esc(text)}</span>'


STATE_KINDS = {
    "accepted": "good",
    "done": "good",
    "integrated": "good",
    "reviewed": "good",
    "green": "good",
    "ready": "neutral",
    "claimed": "degraded",
    "in-progress": "degraded",
    "red": "degraded",
    "blocked": "bad",
    "rejected": "bad",
    "released": "neutral",
    "planned": "neutral",
    "draft": "neutral",
}


def state_badge(status: str) -> str:
    return badge(status, STATE_KINDS.get(status, "neutral"))


def verdict_badge(verdict: str) -> str:
    kind = "good" if verdict in {"PINNED", "ok"} else "degraded"
    return badge(verdict, kind)


def claimability_badge(claimable: bool) -> str:
    if claimable:
        return '<span class="claimable-yes">yes — claim would succeed</span>'
    return '<span class="claimable-no">no</span>'


def duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def age_seconds(value: str | None, now: datetime) -> int | None:
    if not value:
        return None
    try:
        moment = store.parse_time(value)
    except (TypeError, ValueError):
        return None
    return int((now - moment).total_seconds())


def freshness(value: str | None, now: datetime, stale_after: int = STALE_AFTER_SECONDS):
    """A label and a stale flag for one sample time, measured against the render time."""
    seconds = age_seconds(value, now)
    if seconds is None:
        return "time unknown", True
    if seconds < 0:
        # A source sampled a moment after the page's clock is normal, not a time traveller.
        if seconds >= -5:
            return "just now", False
        return "in the future?", True
    if seconds < 2:
        return "just now", False
    return f"{duration(seconds)} ago", seconds > stale_after


def stamp(value: str | None, now: datetime, stale_after: int = STALE_AFTER_SECONDS) -> str:
    label, stale = freshness(value, now, stale_after)
    css = "stale" if stale else "ok"
    if not value:
        return f'<span class="freshness {css}">{esc(label)}</span>'
    return (f'<time datetime="{esc(value)}">{esc(value)}</time> '
            f'<span class="freshness {css}">({esc(label)})</span>')


def _nav(current: str, program: str | None = None) -> str:
    def item(href: str, label: str, key: str) -> str:
        suffix = ""
        if key == "tasks" and program:
            suffix = f"?program={quote(program, safe='')}"
        active = ' aria-current="page"' if current == key else ""
        return f'<li><a href="{esc(href + suffix)}"{active}>{esc(label)}</a></li>'

    return ('<nav class="views" aria-label="Views"><ul>'
            + item("/", "Focus", "focus")
            + item("/tasks", "All tasks", "tasks")
            + item("/graph", "Graph", "graph")
            + "</ul></nav>")


def header_shell(current: str, version: EngineVersion, program: str | None = None) -> str:
    return f"""<header class="site">
  <div class="wrap">
    <p class="brand">Loom <span class="muted">read-only</span></p>
    {_nav(current, program)}
    <p class="muted">ui {esc(__version__)} &middot; engine {verdict_badge(version.verdict)}</p>
  </div>
</header>"""


def shell(*, title: str, current: str, version: EngineVersion, body: str,
          footer: str, program: str | None = None, extra_css: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(title)}</title>
<style>{CSS}{extra_css}</style>
</head>
<body>
<a class="skip" href="#main">Skip to main content</a>
{header_shell(current, version, program)}
<main id="main" tabindex="-1" class="wrap">
{body}
</main>
<footer class="site">
  <div class="wrap">
    {footer}
    <p class="note">This UI is read-only. It exposes no endpoint that changes task state,
    dispatches a worker, or runs a policy pass. Actions belong to <span class="mono">loom</span>
    and arrive in a later, separately reviewed slice.</p>
  </div>
</footer>
</body>
</html>
"""


def signals_table(signals: tuple[Signal, ...], now: datetime) -> str:
    rows = []
    for signal in signals:
        kind = "good" if signal.status == "ok" else (
            "degraded" if signal.status == "degraded" else "bad")
        rows.append(
            "<tr>"
            f'<th scope="row">{esc(signal.source)}</th>'
            f'<td>{badge(signal.status, kind)}</td>'
            f'<td>{stamp(signal.as_of, now)}</td>'
            f'<td>{esc(signal.detail)}</td>'
            "</tr>")
    return (
        '<div class="table-scroll" role="region" aria-label="Source provenance" tabindex="0">'
        "<table>"
        "<caption>Each source is sampled independently; their times are not one "
        "transaction.</caption>"
        "<thead><tr><th scope=\"col\">Source</th><th scope=\"col\">Status</th>"
        "<th scope=\"col\">Sampled</th><th scope=\"col\">Detail</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>")


def degraded_banner(signals: tuple[Signal, ...]) -> str:
    hurt = [signal for signal in signals if signal.status != "ok"]
    if not hurt:
        return ""
    listed = ", ".join(f"{signal.source} ({signal.status})" for signal in hurt)
    return ('<div class="banner" role="status">'
            "<h2>Partial data</h2>"
            f"<p>These sources could not be sampled cleanly: {esc(listed)}. "
            "Their rows and the task fields they feed say so instead of being filled in.</p>"
            "</div>")


def footer_snapshot(as_of: str, digest: str, head_seq: int, db: str, now: datetime,
                    version: EngineVersion) -> str:
    return (f'<p>Registry sample {stamp(as_of, now)} &middot; '
            f'event head <span class="mono">{esc(head_seq)}</span> &middot; '
            f'digest <span class="mono">{esc(digest[:16])}</span></p>'
            f'<p>Database <span class="mono">{esc(db)}</span><br>'
            f'Front door <span class="mono">{esc(version.front_door)}</span></p>')


def unavailable(reason: str, status: int = 503,
                title: str = "Loom UI cannot read the registry") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#main">Skip to main content</a>
<main id="main" tabindex="-1" class="wrap">
  <h1>{esc(title)}</h1>
  <p class="lede">HTTP {esc(status)}. No registry values are shown, because none could be read.</p>
  <div class="banner" role="status">
    <h2>Why</h2>
    <p>{esc(reason)}</p>
  </div>
  <p>Check that the database exists and is readable, then reload. The UI never falls back to a
  cached or partially read registry.</p>
</main>
</body>
</html>
"""


def missing(message: str, hint: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Loom — not found</title>
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#main">Skip to main content</a>
<main id="main" tabindex="-1" class="wrap">
  <h1>Not found</h1>
  <p class="lede">HTTP 404. {esc(message)}</p>
  <p>{esc(hint)}</p>
  <p><a href="/">Focus</a> &middot; <a href="/tasks">All tasks</a></p>
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
<a class="skip" href="#main">Skip to main content</a>
<main id="main" tabindex="-1" class="wrap">
  <h1>Loom UI is not serving</h1>
  <p class="lede">The version gate closed, so no registry data is shown.
  {verdict_badge(verdict)} &middot; HTTP {esc(status)}</p>
  <h2>Why</h2>
  <p>{esc(reason)}</p>
  <p>{esc(detail)}</p>
  {version_table(version)}
  <p class="note">The UI fails closed rather than show values that could disagree with the engine
  that is actually running. Start it from the promoted release, or promote the reviewed commit
  with <span class="mono">loom deploy promote</span>.</p>
</main>
</body>
</html>
"""


def version_table(version: EngineVersion | None) -> str:
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
    return (f'<div class="table-scroll" role="region" aria-label="Version gate" tabindex="0">'
            f'<table><caption>What the gate compared</caption><tbody>{body}</tbody></table></div>')
