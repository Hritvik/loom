"""Server-rendered dependency exploration.

Two renderings of the same read model, on purpose:

* a drawn DAG (inline SVG, no script) for the shape, and
* a complete table for the content, which is what a screen reader, a keyboard-only user or a
  browser without SVG uses.

The drawing is bounded and can be omitted; the table is not. Every value passes through
:func:`templates.esc`, so a task title is never markup. The SVG is ``role="img"`` with a ``<title>``
and ``<desc>`` summary, and the scrolling container is focusable so keyboard users can pan it.
"""
from __future__ import annotations

from urllib.parse import quote

from .graph import NO_PROGRAM, GraphView, NodeView
from .templates import esc, shell
from .version import EngineVersion

GRAPH_CSS = """
.graph-scroll { overflow-x: auto; overscroll-behavior-x: contain; padding: 12px;
                border: 1px solid var(--line); border-radius: 10px; background: var(--panel); }
.graph-scroll:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.table-scroll { overflow-x: auto; }
.table-scroll table { width: auto; min-width: 100%; }
a.task { white-space: nowrap; }
figure.graph { margin: 0; position: relative; left: 50%; transform: translateX(-50%);
               width: min(calc(100vw - 40px), 1400px); }
figure.graph figcaption { color: var(--muted); font-size: 13px; margin-top: 8px; }
svg.dag { display: block; }
svg.dag text { font-family: ui-monospace, "SF Mono", Menlo, monospace; fill: var(--ink); }
svg.dag .edge path { fill: none; stroke: var(--muted); stroke-width: 1.4; opacity: .75; }
svg.dag .edge.advisory path { stroke-dasharray: 4 4; }
svg.dag .node rect { fill: var(--panel); stroke: var(--line); stroke-width: 1.6; }
svg.dag .node.focus rect { stroke: var(--accent); stroke-width: 3; }
svg.dag .node.upstream rect { stroke: var(--warn); stroke-width: 2; }
svg.dag .node.downstream rect { stroke: var(--line); stroke-width: 2; }
svg.dag .node text.id { font-size: 11.5px; font-weight: 650; }
svg.dag .node text.state { font-size: 10px; fill: var(--muted); }
svg.dag marker#dag-arrow path { fill: var(--muted); }
.legend { color: var(--muted); font-size: 12.5px; display: flex; flex-wrap: wrap; gap: 14px;
          margin: 8px 0 0; padding: 0; list-style: none; }
.legend .swatch { display: inline-block; width: 12px; height: 12px; border-radius: 3px;
                  border: 2px solid var(--line); vertical-align: -2px; margin-right: 5px; }
.legend .swatch.up { border-color: var(--warn); }
.legend .swatch.focus { border-color: var(--accent); }
.legend .swatch.dash { border-style: dashed; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; padding: 0;
         list-style: none; font-size: 13px; }
a:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; border-radius: 3px; }
caption { text-align: left; color: var(--muted); font-size: 13px; padding: 0 0 6px; }
"""

NODE_W, NODE_H, GAP_X, GAP_Y, PAD = 200, 42, 64, 16, 18


def _url(*, program: str | None = None, task: str | None = None) -> str:
    parts = []
    if program is not None:
        parts.append("program=" + quote(program or NO_PROGRAM, safe=""))
    if task is not None:
        parts.append("task=" + quote(task, safe=""))
    return "/graph" + ("?" + "&".join(parts) if parts else "")


def _task_link(task_id: str, label: str | None = None) -> str:
    return (f'<a href="{esc(_url(task=task_id))}" class="mono task">'
            f'{esc(label or task_id)}</a>')


def _scroll(table: str) -> str:
    """Wide tables scroll instead of crushing nine columns into the page width."""
    return f'<div class="table-scroll">{table}</div>' if table else ""


def _state(node: NodeView) -> str:
    return f'<span class="badge">{esc(node.status)}</span>'


def _claimability(node: NodeView, *, short: bool = False) -> str:
    if node.claimable:
        return '<span class="badge ok">claimable</span>'
    reason = node.claimability_reasons[0] if node.claimability_reasons else "not claimable"
    if short and len(reason) > 64:
        reason = reason[:63].rstrip() + "…"
    return f'<span class="badge degraded">not claimable</span><br><span class="mono">{esc(reason)}</span>'


def _facts(rows: list[tuple[str, str]], caption: str | None = None) -> str:
    body = "".join(f'<tr><th scope="row">{esc(name)}</th><td>{value}</td></tr>'
                   for name, value in rows)
    heading = f"<caption>{esc(caption)}</caption>" if caption else ""
    return _scroll(f"<table>{heading}<tbody>{body}</tbody></table>")


def _relation_label(node: NodeView) -> str:
    if node.relation == "program":
        return "in program"
    if node.relation == "both":
        return "both (cycle)"
    return node.relation


def _path_cell(node: NodeView) -> str:
    if not node.path:
        return '<span class="empty">—</span>'
    return " &rarr; ".join(_task_link(step, step) if step != node.id else
                           f'<span class="mono">{esc(step)}</span>'
                           for step in node.path)


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _node_table(nodes: tuple[NodeView, ...], caption: str, *,
                relationships: bool = True, compact: bool = False) -> str:
    """One table builder for both the section lists and the complete text equivalent.

    ``compact`` keeps the reading order short enough for the page width; ``relationships=False``
    is the program list, where distance and path have no meaning.
    """
    if not nodes:
        return '<p class="empty">No task in this view.</p>'

    def row(node: NodeView) -> str:
        cells = ["<tr>"]
        if not compact:
            cells.append(f'<th scope="row">{esc(_relation_label(node))}</th>')
        if relationships:
            cells.append(f'<td class="num">{esc(_distance(node))}</td>')
        cells.append(f'<td>{_task_link(node.id)}')
        if node.outside_program:
            cells.append(f'<br><span class="mono">in {esc(node.program or NO_PROGRAM)}</span>')
        cells.append("</td>")
        cells.append(f"<td>{_state(node)}</td>")
        cells.append(f"<td>{_claimability(node, short=compact)}</td>")
        if not compact:
            cells.append(f'<td>{esc(node.program or NO_PROGRAM)}</td>')
            cells.append(f'<td class="num">+{esc(node.downstream_count)}</td>')
        if relationships:
            cells.append(f"<td>{_path_cell(node)}</td>")
        cells.append(f"<td>{esc(node.title)}</td>")
        cells.append("</tr>")
        return "".join(cells)

    headers = []
    if not compact:
        headers.append('<th scope="col">Relation</th>')
    if relationships:
        headers.append('<th scope="col" class="num">Distance</th>')
    headers.append('<th scope="col">Task</th>')
    headers.append('<th scope="col">State</th>')
    headers.append('<th scope="col">Claimability</th>')
    if not compact:
        headers.append('<th scope="col">Program</th>')
        headers.append('<th scope="col" class="num">Downstream</th>')
    if relationships:
        headers.append('<th scope="col">Path (prerequisite &rarr; dependent)</th>')
    headers.append('<th scope="col">Title</th>')
    return _scroll(
        f"<table><caption>{esc(caption)}</caption>"
        f"<thead><tr>{''.join(headers)}</tr></thead>"
        f"<tbody>{''.join(row(node) for node in nodes)}</tbody></table>")


def _distance(node: NodeView) -> str:
    if node.relation == "focus":
        return "0"
    if node.relation == "program":
        return "—"
    values = [value for value in (node.upstream_distance, node.downstream_distance)
              if value is not None]
    return str(min(values)) if values else "—"


def _edges_table(view: GraphView) -> str:
    if not view.edges:
        return '<p class="empty">No dependency edges exist among these tasks.</p>'
    relation = {node.id: node for node in view.nodes}

    def cell(task_id: str) -> str:
        node = relation.get(task_id)
        if node is None:
            return f'<span class="mono">{esc(task_id)}</span>'
        return _task_link(task_id)

    rows = "".join(
        "<tr>"
        f"<td>{cell(edge.dependent)}</td>"
        f"<td>{cell(edge.dependency)}</td>"
        f"<td class=\"mono\">{esc(edge.kind)}</td>"
        f"<td>{'blocks a claim' if edge.gating else 'advisory, does not block'}</td>"
        "</tr>"
        for edge in view.edges)
    return _scroll(
        "<table><caption>Every edge in scope. \"A depends on B\" is read left to right."
        "</caption><thead><tr>"
        "<th scope=\"col\">Dependent</th><th scope=\"col\">Depends on</th>"
        "<th scope=\"col\">Kind</th><th scope=\"col\">Effect</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>")


def _boundary_table(view: GraphView) -> str:
    if not view.boundary:
        return ""
    rows = "".join(
        "<tr>"
        f"<td>{_task_link(edge.dependent)}</td>"
        f"<td>{_task_link(edge.dependency)}</td>"
        "<td>outside this program</td>"
        "</tr>"
        for edge in view.boundary)
    return (
        "<h2>Dependencies leaving the program</h2>"
        "<p>The graph is program-scoped, but a blocker outside it is still a blocker. These edges "
        "are listed rather than drawn, so scoping cannot hide one.</p>"
        + _scroll("<table><caption>Cross-program prerequisites of the tasks above.</caption>"
                  "<thead><tr><th scope=\"col\">Dependent</th><th scope=\"col\">Depends on</th>"
                  "<th scope=\"col\">Where</th></tr></thead>"
                  f"<tbody>{rows}</tbody></table>"))


def _program_chooser(view: GraphView) -> str:
    if not view.programs:
        return '<p class="empty">The registry has no tasks, so there is nothing to graph.</p>'
    rows = []
    for summary in view.programs:
        states = ", ".join(f"{status} {count}" for status, count in summary.statuses)
        rows.append(
            "<tr>"
            f"<td><a href=\"{esc(_url(program=summary.program))}\">{esc(summary.label)}</a></td>"
            f"<td class=\"num\">{esc(summary.tasks)}</td>"
            f"<td class=\"num\">{esc(summary.claimable)}</td>"
            f"<td>{esc(states)}</td>"
            "</tr>")
    return _scroll(
        "<table><caption>Programs in this snapshot. Open one to see its dependency shape, or "
        "focus a task by id from the address bar: <span class=\"mono\">/graph?task=&lt;id&gt;</span>."
        "</caption><thead><tr>"
        "<th scope=\"col\">Program</th><th scope=\"col\" class=\"num\">Tasks</th>"
        "<th scope=\"col\" class=\"num\">Claimable</th><th scope=\"col\">States</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>")


def _problem_section(view: GraphView) -> str:
    if not view.unknown_dependencies and not view.cycles:
        return ""
    items = []
    if view.cycles:
        items.append("<li><strong>Cycle:</strong> these tasks appear both upstream and downstream "
                     "of the focus, so their dependencies cannot all be satisfied: "
                     + ", ".join(f'<span class="mono">{esc(task_id)}</span>'
                                 for task_id in view.cycles) + "</li>")
    for finding in view.unknown_dependencies:
        items.append(f'<li><strong>Unknown dependency:</strong> {esc(finding)}</li>')
    return ("<h2>Data problems</h2>"
            '<p class="note">Loom records these edges but they cannot be drawn or satisfied as '
            "normal dependencies. They are reported instead of being dropped.</p>"
            f"<ul>{''.join(items)}</ul>")


def _focus_facts(view: GraphView) -> str:
    node = view.node(view.focus or "")
    if node is None:
        return ""
    rows = [
        ("Task", _task_link(node.id)),
        ("Title", esc(node.title)),
        ("State", _state(node)),
        ("Claimability", _claimability(node)),
        ("Program", esc(node.program or NO_PROGRAM)),
        ("Board", f'<span class="mono">{esc(node.board)}</span>'),
        ("Upstream", esc(len(view.upstream()))),
        ("Downstream", esc(len(view.downstream()))),
    ]
    return _facts(rows, "Focus task facts")


def _svg(view: GraphView) -> str:
    if not view.drawn or not view.layers:
        return ""
    by_id = {node.id: node for node in view.nodes}
    columns = len(view.layers)
    rows = max(len(layer) for layer in view.layers)
    width = PAD * 2 + columns * NODE_W + max(columns - 1, 0) * GAP_X
    height = PAD * 2 + rows * NODE_H + max(rows - 1, 0) * GAP_Y
    box: dict[str, tuple[float, float]] = {}
    for column, layer in enumerate(view.layers):
        x = PAD + column * (NODE_W + GAP_X)
        span = len(layer) * NODE_H + max(len(layer) - 1, 0) * GAP_Y
        top = (height - span) / 2
        for row, task_id in enumerate(layer):
            box[task_id] = (x, top + row * (NODE_H + GAP_Y))

    edges = []
    for edge in view.edges:
        if edge.dependent not in box or edge.dependency not in box:
            continue
        x1, y1 = box[edge.dependent]
        x2, y2 = box[edge.dependency]
        mid1, mid2 = y1 + NODE_H / 2, y2 + NODE_H / 2
        css = "edge" if edge.gating else "edge advisory"
        if abs(x1 - x2) < 1:  # same column: loop under the boxes
            d = (f"M {x1 + NODE_W / 2:.1f} {y1 + NODE_H:.1f} "
                 f"C {x1 + NODE_W / 2:.1f} {y1 + NODE_H + 34:.1f} "
                 f"{x2 + NODE_W / 2:.1f} {y2 + NODE_H + 34:.1f} "
                 f"{x2 + NODE_W / 2:.1f} {y2 + NODE_H + 1:.1f}")
        elif x2 < x1:
            d = (f"M {x1:.1f} {mid1:.1f} C {(x1 + x2 + NODE_W) / 2:.1f} {mid1:.1f} "
                 f"{(x1 + x2 + NODE_W) / 2:.1f} {mid2:.1f} {x2 + NODE_W + 1:.1f} {mid2:.1f}")
        else:
            d = (f"M {x1 + NODE_W:.1f} {mid1:.1f} C {(x1 + NODE_W + x2) / 2:.1f} {mid1:.1f} "
                 f"{(x1 + NODE_W + x2) / 2:.1f} {mid2:.1f} {x2 - 1:.1f} {mid2:.1f}")
        edges.append(
            f'<g class="{css}"><title>{esc(edge.dependent)} depends on {esc(edge.dependency)}'
            f'</title><path d="{d}" marker-end="url(#dag-arrow)"/></g>')

    nodes = []
    for task_id, (x, y) in box.items():
        node = by_id[task_id]
        label = task_id if len(task_id) <= 26 else task_id[:25] + "…"
        nodes.append(
            f'<g class="node {esc(node.relation if node.relation != "both" else "upstream")}">'
            f'<title>{esc(task_id)} — {esc(node.status)} — {esc(node.title)}</title>'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{NODE_W}" height="{NODE_H}" rx="8"/>'
            f'<text class="id" x="{x + 10:.1f}" y="{y + 17:.1f}">{esc(label)}</text>'
            f'<text class="state" x="{x + 10:.1f}" y="{y + 32:.1f}">{esc(node.status)}</text>'
            '</g>')

    summary = (f"{len(view.rendered)} of {len(view.nodes)} task(s) and "
               f"{len(view.edges)} dependenc{'y' if len(view.edges) == 1 else 'ies'} in scope.")
    if view.focus:
        summary += (f" Focus {view.focus}; {len(view.upstream())} upstream, "
                    f"{len(view.downstream())} downstream.")
    return (
        '<figure class="graph">'
        '<div class="graph-scroll" tabindex="0" role="group" '
        'aria-label="Dependency graph, scrollable. The text-equivalent table follows.">'
        f'<svg class="dag" role="img" aria-labelledby="dag-title dag-desc" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" height="{height:.0f}">'
        '<title id="dag-title">Dependency graph</title>'
        f'<desc id="dag-desc">{esc(summary + " Arrows point from a task to a prerequisite it "
                                         "depends on.")}</desc>'
        '<defs><marker id="dag-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z"/></marker></defs>'
        f'<g>{"".join(edges)}</g>'
        f'<g>{"".join(nodes)}</g>'
        '</svg></div>'
        '<figcaption>Columns place a task right of the prerequisites it depends on; the focus is '
        'the accented box.</figcaption>'
        '<ul class="legend">'
        + (
            '<li><span class="swatch focus"></span>focus</li>'
            '<li><span class="swatch up"></span>upstream (must finish first)</li>'
            '<li><span class="swatch"></span>downstream (impacted)</li>'
            if view.focus else
            '<li><span class="swatch"></span>task, placed by dependency depth</li>'
        )
        + (
            '<li><span class="swatch dash"></span>advisory edge (does not block)</li>'
            if any(not edge.gating for edge in view.edges) else ""
        )
        + '<li>&#8594; points at the prerequisite</li>'
        '</ul>'
        '</figure>')


def _breadcrumbs(view: GraphView) -> str:
    items = ['<li><a href="/">Control room</a></li>', '<li><a href="/graph">All programs</a></li>']
    if view.program:
        items.append(f'<li><a href="{esc(_url(program=view.program))}">'
                     f'{esc(view.program or NO_PROGRAM)}</a></li>')
    if view.focus:
        items.append(f'<li><span class="mono">{esc(view.focus)}</span></li>')
    return '<ul class="chips">' + "".join(items) + "</ul>"


def page(view: GraphView, version: EngineVersion) -> str:
    if view.mode == "chooser":
        heading = "Dependency graph"
        lede = ("Choose a program, or focus a task, to see what must happen first and what a "
                "change would unblock.")
        body = f"<h2>Programs</h2>{_program_chooser(view)}"
    elif view.mode == "missing":
        heading = "Dependency graph"
        lede = "That scope is not in this snapshot."
        body = f'<p class="note">{esc(view.message or "no such scope")}</p>{_program_chooser(view)}'
    elif view.mode == "focus":
        heading = f"Focus {view.focus}"
        upstream = view.upstream()
        downstream = view.downstream()
        lede = (f"{_plural(len(upstream), 'upstream prerequisite')} and "
                f"{_plural(len(downstream), 'downstream task')} for {view.focus} in "
                f"{view.program or NO_PROGRAM}.")
        note = f'<p class="note">{esc(view.note)}</p>' if view.note else ""
        body = (
            f"<h2>Focus</h2>{_focus_facts(view)}"
            f"<h2>Upstream — must finish first</h2>"
            f"{_node_table(tuple(upstream), 'Everything the focus transitively depends on.', compact=True)}"
            f"<h2>Downstream — impacted by this task</h2>"
            f"{_node_table(tuple(downstream), 'Everything that transitively depends on the focus.', compact=True)}"
            f"<h2>Graph</h2>{_svg(view) or '<p class=\"empty\">No related task to draw.</p>'}"
            f"{note}"
            f"<h2>Every edge in this closure</h2>{_edges_table(view)}"
            f"{_problem_section(view)}"
            f"<h2>Text equivalent</h2>"
            f"{_node_table(view.nodes, 'Every task in the focus closure, including the focus.')}"
        )
    else:
        heading = f"Program {view.program or NO_PROGRAM}"
        lede = (f"{_plural(len(view.nodes), 'task')} and "
                f"{_plural(len(view.edges), 'dependency edge')} in this program.")
        note = f'<p class="note">{esc(view.note)}</p>' if view.note else ""
        body = (
            "<h2>Graph</h2>"
            f"{note}"
            f"{_svg(view)}"
            f"{_boundary_table(view)}"
            f"<h2>Every edge in this program</h2>{_edges_table(view)}"
            f"{_problem_section(view)}"
            f"<h2>Text equivalent</h2>"
            f"{_node_table(view.nodes, 'Every task in the program, with its relationships.', relationships=False)}"
        )

    heading_html = (
        f"<h1>{esc(heading)}</h1>"
        f'<p class="lede">Read-only. {esc(lede)}</p>'
        f"{_breadcrumbs(view)}"
        f"{body}")
    footer = (
        f'<p>Snapshot <span class="mono">{esc(view.as_of)}</span> &middot; '
        f'digest <span class="mono">{esc(view.digest[:16])}</span> &middot; '
        f'event head <span class="mono">{esc(view.head_seq)}</span></p>'
        "<p>Edges come from the engine's dependency data, not from a copy of it.</p>")
    return shell(title="Loom — dependency graph", current="graph", version=version,
                 body=heading_html, footer=footer, program=view.program,
                 extra_css=GRAPH_CSS)
