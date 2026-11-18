"""Program-scoped dependency exploration, folded from the engine's own edges.

The graph is *derived*, never declared. Every gating edge comes from :func:`engine.task_edges`,
which is what refuses a claim; downstream reachability comes from :func:`engine.dependents_index`
and :func:`engine.transitive_dependents`. Upstream reachability walks the same edges in reverse.
Nothing here re-implements a rule: if the engine's dependency data changes, these views change with
it, and ``tests/test_graph.py`` compares the two directly rather than trusting the page.

Three deliberate limits:

* **No portfolio-wide default.** A request without ``program`` or ``task`` returns a program
  chooser, not 250 nodes. The plan's "avoid drawing the entire portfolio at once by default" is a
  property of the read model, not just the template.
* **A program too large to draw asks for a focus.** Above :data:`DEFAULT_NODE_LIMIT` the program
  view is listed as a table with no diagram. Focus views always keep their complete table and only
  bound the drawing, so the accessible equivalent is never the thing that gets truncated.
* **Nothing is silently dropped.** A dependency that leaves the program becomes a recorded
  boundary edge; a dependency that names no task becomes a reported problem; a cycle is named
  instead of being walked forever.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, dataclass

import engine
import store

from . import model
from .snapshot import Snapshot

DEFAULT_NODE_LIMIT = 60
NO_PROGRAM = "(none)"
GATING_KIND = "depends-on"


class GraphScopeError(LookupError):
    """The requested program or focus task is not in this snapshot."""


@dataclass(frozen=True)
class NodeView:
    id: str
    title: str
    status: str
    program: str
    board: str
    claimable: bool
    claimability_reasons: tuple[str, ...]
    relation: str  # focus | upstream | downstream | both | program
    upstream_distance: int | None
    downstream_distance: int | None
    downstream_count: int
    path: tuple[str, ...]  # prerequisite -> dependent order, focus included
    outside_program: bool


@dataclass(frozen=True)
class EdgeView:
    dependent: str
    dependency: str
    kind: str
    gating: bool


@dataclass(frozen=True)
class ProgramSummary:
    program: str
    label: str
    tasks: int
    claimable: int
    statuses: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class GraphView:
    as_of: str
    digest: str
    head_seq: int
    mode: str  # chooser | program | focus | missing
    program: str | None
    focus: str | None
    programs: tuple[ProgramSummary, ...]
    nodes: tuple[NodeView, ...]
    edges: tuple[EdgeView, ...]
    boundary: tuple[EdgeView, ...]
    layers: tuple[tuple[str, ...], ...]
    focus_column: int | None
    rendered: tuple[str, ...]
    omitted: int
    drawn: bool
    cycles: tuple[str, ...]
    unknown_dependencies: tuple[str, ...]
    message: str | None
    note: str | None

    def to_dict(self) -> dict:
        payload = asdict(self)
        for key in ("nodes", "edges", "boundary", "programs", "layers", "rendered",
                    "cycles", "unknown_dependencies"):
            payload[key] = [_plain(item) for item in payload[key]]
        return payload

    def node(self, task_id: str) -> NodeView | None:
        return next((node for node in self.nodes if node.id == task_id), None)

    def upstream(self) -> list[NodeView]:
        return [node for node in self.nodes if node.relation in {"upstream", "both"}]

    def downstream(self) -> list[NodeView]:
        return [node for node in self.nodes if node.relation in {"downstream", "both"}]


def _plain(value):
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _program_of(task: dict) -> str:
    return str(task.get("program") or "")


def _label(program: str) -> str:
    return program or NO_PROGRAM


def _walk(registry: dict, step, origin: str) -> dict[str, tuple[str, ...]]:
    """Shortest paths from ``origin`` along ``step``; ``origin`` itself is excluded.

    Breadth-first, so the first path found is a shortest one, and the ``seen`` set is what stops a
    cycle from looping. The path is returned focus-first; callers reverse it when they want
    prerequisite order. A dependency naming no task is not walked into — it is reported by
    :func:`_unknown` instead of raising inside the engine.
    """
    known = {task["id"] for task in registry["tasks"]}
    paths: dict[str, tuple[str, ...]] = {origin: (origin,)}
    queue = deque([origin])
    while queue:
        current = queue.popleft()
        for nxt in step(current):
            if nxt in paths or nxt not in known:
                continue
            paths[nxt] = paths[current] + (nxt,)
            queue.append(nxt)
    paths.pop(origin, None)
    return paths


def _upstream_step(registry: dict):
    def step(task_id: str) -> tuple[str, ...]:
        return tuple(sorted(set(engine.task_edges(registry, task_id))))
    return step


def _downstream_step(registry: dict):
    index = engine.dependents_index(registry)

    def step(task_id: str) -> tuple[str, ...]:
        return tuple(sorted(set(index.get(task_id, ()))))
    return step


def _edges_within(registry: dict, ids: tuple[str, ...]) -> tuple[EdgeView, ...]:
    """Every dependency edge whose both ends are in scope.

    Gating edges are the engine's ``depends-on`` resolution. Non-gating registry edges (for example
    ``provider-parity``) are advisory: they are shown and labelled, but they do not block a claim.
    """
    scope = set(ids)
    found: set[tuple[str, str, str]] = set()
    for task_id in ids:
        for dependency in engine.task_edges(registry, task_id):
            if dependency in scope and dependency != task_id:
                found.add((task_id, dependency, GATING_KIND))
    for edge in registry.get("edges", []):
        kind = str(edge.get("kind") or GATING_KIND)
        if kind == GATING_KIND:
            continue
        dependent, dependency = edge.get("from"), edge.get("to")
        if dependent in scope and dependency in scope and dependent != dependency:
            found.add((str(dependent), str(dependency), kind))
    return tuple(EdgeView(dependent, dependency, kind, kind == GATING_KIND)
                 for dependent, dependency, kind in sorted(found))


def _boundary(registry: dict, ids: tuple[str, ...]) -> tuple[EdgeView, ...]:
    """Gating dependencies that leave the program scope, kept rather than hidden."""
    scope = set(ids)
    found: set[tuple[str, str, str]] = set()
    for task_id in ids:
        for dependency in engine.task_edges(registry, task_id):
            if dependency not in scope:
                found.add((task_id, dependency, GATING_KIND))
    return tuple(EdgeView(dependent, dependency, kind, True)
                 for dependent, dependency, kind in sorted(found))


def _unknown(registry: dict, ids: tuple[str, ...]) -> tuple[str, ...]:
    known = {task["id"] for task in registry["tasks"]}
    findings = set()
    for task_id in ids:
        for dependency in engine.task_edges(registry, task_id):
            if dependency not in known:
                findings.add(f"{task_id} depends on {dependency}, which is not a task")
    return tuple(sorted(findings))


def _layers(registry: dict, ids: tuple[str, ...]) -> tuple[tuple[tuple[str, ...], ...], tuple[str, ...]]:
    """Topological columns for a program: a task sits one column right of its deepest prerequisite."""
    scope = set(ids)
    dependencies = {
        task_id: [dep for dep in engine.task_edges(registry, task_id) if dep in scope]
        for task_id in ids
    }
    column: dict[str, int] = {}
    remaining = set(ids)
    while remaining:
        ready = [task_id for task_id in remaining
                 if all(dep in column for dep in dependencies[task_id])]
        if not ready:
            break
        for task_id in ready:
            column[task_id] = 1 + max((column[dep] for dep in dependencies[task_id]), default=-1)
        remaining -= set(ready)
    cycles = tuple(sorted(remaining))
    for task_id in remaining:  # unresolved cycle members get their own trailing column
        column[task_id] = 1 + max(column.values(), default=-1)

    grouped: dict[int, list[str]] = {}
    for task_id, depth in column.items():
        grouped.setdefault(depth, []).append(task_id)
    return tuple(tuple(sorted(grouped[depth])) for depth in sorted(grouped)), cycles


def _focus_layers(upstream: dict[str, int], downstream: dict[str, int],
                  focus: str) -> tuple[tuple[tuple[str, ...], ...], int]:
    """Columns by distance: prerequisites left of the focus, dependents right of it."""
    grouped: dict[int, list[str]] = {0: [focus]}
    for task_id, distance in upstream.items():
        grouped.setdefault(-distance, []).append(task_id)
    for task_id, distance in downstream.items():
        if task_id in upstream:
            continue  # a cycle member: place it once, on the prerequisite side
        grouped.setdefault(distance, []).append(task_id)
    columns = sorted(grouped)
    focus_column = columns.index(0)
    return tuple(tuple(sorted(grouped[column])) for column in columns), focus_column


def programs(registry: dict, now) -> tuple[ProgramSummary, ...]:
    grouped: dict[str, list[dict]] = {}
    for task in registry["tasks"]:
        grouped.setdefault(_program_of(task), []).append(task)
    summaries = []
    for program, tasks in grouped.items():
        statuses: dict[str, int] = {}
        claimable = 0
        for task in tasks:
            statuses[task.get("status", "")] = statuses.get(task.get("status", ""), 0) + 1
            if model.claimability(registry, task, now).claimable:
                claimable += 1
        summaries.append(ProgramSummary(
            program=program,
            label=_label(program),
            tasks=len(tasks),
            claimable=claimable,
            statuses=tuple(sorted(statuses.items())),
        ))
    return tuple(sorted(summaries, key=lambda summary: (-summary.tasks, summary.label)))


def _node(registry: dict, task: dict, now, *, relation: str, program: str,
          downstream_count: int, upstream: int | None = None, downstream: int | None = None,
          path: tuple[str, ...] = ()) -> NodeView:
    eligibility = model.claimability(registry, task, now)
    return NodeView(
        id=task["id"],
        title=str(task.get("title") or ""),
        status=str(task.get("status") or ""),
        program=_program_of(task),
        board=str(task.get("board") or ""),
        claimable=eligibility.claimable,
        claimability_reasons=eligibility.reasons,
        relation=relation,
        upstream_distance=upstream,
        downstream_distance=downstream,
        downstream_count=downstream_count,
        path=path,
        outside_program=bool(program) and _program_of(task) != program,
    )


def _sorted_nodes(nodes: list[NodeView]) -> tuple[NodeView, ...]:
    order = {"focus": 0, "upstream": 1, "both": 2, "downstream": 3, "program": 4}
    return tuple(sorted(nodes, key=lambda node: (
        order.get(node.relation, 9),
        node.upstream_distance if node.upstream_distance is not None else 10_000,
        node.downstream_distance if node.downstream_distance is not None else 10_000,
        node.id,
    )))


def _program_view(snapshot: Snapshot, program: str, limit: int) -> GraphView:
    registry = snapshot.registry
    now = store.parse_time(snapshot.as_of)
    ids = tuple(task["id"] for task in registry["tasks"] if _program_of(task) == program)
    if not ids:
        raise GraphScopeError(f"no task in program {_label(program)!r}")
    by_id = {task["id"]: task for task in registry["tasks"]}
    index = engine.dependents_index(registry)
    counts = {task_id: len(engine.transitive_dependents(index, task_id)) for task_id in ids}
    nodes = [_node(registry, by_id[task_id], now, relation="program", program=program,
                   downstream_count=counts[task_id])
             for task_id in ids]
    edges = _edges_within(registry, ids)
    layered, cycles = _layers(registry, ids)
    drawn = len(ids) <= limit
    if drawn:
        rendered = tuple(task_id for layer in layered for task_id in layer)
        layers = layered
        omitted = 0
        note = None
    else:
        rendered = ()
        layers = ()
        omitted = len(ids)
        note = (f"This program has {len(ids)} tasks, more than the {limit}-node drawing limit, "
                "so no diagram is drawn. Choose a focus task to see its upstream and downstream "
                "paths; the complete program is listed in the table below.")
    return GraphView(
        as_of=snapshot.as_of,
        digest=snapshot.digest,
        head_seq=snapshot.head_seq,
        mode="program",
        program=program,
        focus=None,
        programs=programs(registry, now),
        nodes=_sorted_nodes(nodes),
        edges=edges,
        boundary=_boundary(registry, ids),
        layers=layers,
        focus_column=None,
        rendered=rendered,
        omitted=omitted,
        drawn=drawn,
        cycles=cycles,
        unknown_dependencies=_unknown(registry, ids),
        message=None,
        note=note,
    )


def _focus_view(snapshot: Snapshot, focus: str, program: str | None, limit: int) -> GraphView:
    registry = snapshot.registry
    now = store.parse_time(snapshot.as_of)
    by_id = {task["id"]: task for task in registry["tasks"]}
    task = by_id.get(focus)
    if task is None:
        raise GraphScopeError(f"no task {focus!r} in this registry")
    focus_program = _program_of(task)
    if program is not None and focus_program != program:
        raise GraphScopeError(
            f"task {focus} belongs to program {_label(focus_program)!r}, not {_label(program)!r}")

    up_paths = _walk(registry, _upstream_step(registry), focus)
    down_paths = _walk(registry, _downstream_step(registry), focus)
    up_distance = {task_id: len(path) - 1 for task_id, path in up_paths.items()}
    down_distance = {task_id: len(path) - 1 for task_id, path in down_paths.items()}

    ids = tuple(task["id"] for task in by_id.values() if task["id"] == focus
                or task["id"] in up_paths or task["id"] in down_paths)
    index = engine.dependents_index(registry)
    counts = {task_id: len(engine.transitive_dependents(index, task_id)) for task_id in ids}

    nodes: list[NodeView] = []
    for task_id, record in by_id.items():
        if task_id == focus:
            continue
        if task_id in up_paths and task_id in down_paths:
            relation = "both"
        elif task_id in up_paths:
            relation = "upstream"
        elif task_id in down_paths:
            relation = "downstream"
        else:
            continue
        walk = up_paths[task_id] if task_id in up_paths else down_paths[task_id]
        path = tuple(reversed(walk)) if relation == "upstream" else walk
        nodes.append(_node(
            registry, record, now,
            relation=relation,
            program=focus_program,
            downstream_count=counts[task_id],
            upstream=up_distance.get(task_id),
            downstream=down_distance.get(task_id),
            path=path,
        ))
    nodes.append(_node(registry, task, now, relation="focus", program=focus_program,
                       downstream_count=counts[focus],
                       upstream=0, downstream=0, path=(focus,)))

    edges = _edges_within(registry, ids)
    layers, focus_column = _focus_layers(up_distance, down_distance, focus)

    # Bound the drawing only. The table above lists every node and edge in the focus closure.
    ordered = sorted(
        (node for node in nodes if node.relation != "focus"),
        key=lambda node: (min(
            node.upstream_distance if node.upstream_distance is not None else 10_000,
            node.downstream_distance if node.downstream_distance is not None else 10_000), node.id))
    keep = {focus} | {node.id for node in ordered[:max(limit - 1, 0)]}
    shown_layers = tuple(tuple(task_id for task_id in layer if task_id in keep)
                         for layer in layers)
    shown_layers = tuple(layer for layer in shown_layers if layer)
    focus_column = next((index for index, layer in enumerate(shown_layers) if focus in layer),
                        None)
    rendered = tuple(task_id for layer in shown_layers for task_id in layer)
    omitted = len(nodes) - len(rendered)
    note = None
    if omitted:
        note = (f"{omitted} related task(s) are beyond the {limit}-node drawing limit; they are "
                "listed in the text equivalent below but not drawn.")

    cycles = tuple(sorted(node.id for node in nodes if node.relation == "both"))
    return GraphView(
        as_of=snapshot.as_of,
        digest=snapshot.digest,
        head_seq=snapshot.head_seq,
        mode="focus",
        program=focus_program,
        focus=focus,
        programs=programs(registry, now),
        nodes=_sorted_nodes(nodes),
        edges=edges,
        boundary=(),
        layers=shown_layers,
        focus_column=focus_column,
        rendered=rendered,
        omitted=omitted,
        drawn=True,
        cycles=cycles,
        unknown_dependencies=_unknown(registry, ids),
        message=None,
        note=note,
    )


def _chooser(snapshot: Snapshot) -> GraphView:
    registry = snapshot.registry
    now = store.parse_time(snapshot.as_of)
    return GraphView(
        as_of=snapshot.as_of,
        digest=snapshot.digest,
        head_seq=snapshot.head_seq,
        mode="chooser",
        program=None,
        focus=None,
        programs=programs(registry, now),
        nodes=(),
        edges=(),
        boundary=(),
        layers=(),
        focus_column=None,
        rendered=(),
        omitted=0,
        drawn=False,
        cycles=(),
        unknown_dependencies=(),
        message=None,
        note=("The graph is scoped to one program or one task. The whole portfolio is never drawn "
              "at once: choose a program to see its shape, or a task to trace its blockers and its "
              "impact."),
    )


def missing(snapshot: Snapshot, message: str) -> GraphView:
    """A chooser-shaped view that says the requested scope is not in this snapshot."""
    view = _chooser(snapshot)
    return GraphView(**{**view.__dict__, "mode": "missing", "message": message})


def build(snapshot: Snapshot, program: str | None = None, focus: str | None = None,
          limit: int = DEFAULT_NODE_LIMIT) -> GraphView:
    """Build one graph view from one snapshot.

    ``focus`` wins over ``program`` for scoping, but a ``program`` given alongside a ``focus`` is
    still checked, so a wrong link fails loudly instead of silently showing another program.
    A request with neither returns the program chooser rather than a portfolio-wide diagram.
    """
    program = (program or "").strip() or None
    focus = (focus or "").strip() or None
    if program == NO_PROGRAM:  # the chooser's label for a task with no program
        program = ""
    if limit < 2:
        raise ValueError("the node limit must leave room for at least a focus and one neighbour")
    if focus:
        return _focus_view(snapshot, focus, program, limit)
    if program is not None:
        return _program_view(snapshot, program, limit)
    return _chooser(snapshot)


def find(view: GraphView, task_id: str) -> NodeView | None:
    return view.node(task_id)


if __name__ == "__main__":  # pragma: no cover - manual poking only
    from .snapshot import read

    print(json.dumps(build(read()).to_dict(), indent=2))
