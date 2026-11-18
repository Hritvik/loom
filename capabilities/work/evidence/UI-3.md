# UI-3 — Add program-scoped dependency exploration

Task: `loom-ui/UI-3` · branch `loom/loom-ui-ui-3-a3` · write path `capabilities/work/ui`
Plan: `plans/loom/2026-11-15-ui-plan.md` (private planning repo) · items:
`wayfinder/loom-ui/work-items.md` (`UI-3`)

This is the **integration candidate**: the UI-3 graph rebased onto `main`
(`489deb2 integrate(LOOM-6): report health as an artifact`), which now carries UI-2's control room.
The previously reviewed read model and page are unchanged; what moved is how the slice attaches to
the shell UI-2 introduced.

## Rework — what this attempt had to solve

The coordinator sent UI-3 back at revision 5 with one concrete defect: UI-2 integrated after this
branch was cut, and the two touched the same four files. The instruction was to rebase onto current
`main`, resolve the four files, and ask for review of the integration candidate, "so the reviewed
blob is the one that lands".

| File | What conflicted | Resolution |
|---|---|---|
| `ui/README.md` | title, opening paragraph, boundary table, route table | UI-2's title/paragraph and boundaries kept, the graph row and the graph route rows merged into them; the "Dependency graph" section appends unchanged |
| `ui/loom_ui/__init__.py` | the layout list and `__version__` | both layout lines kept (`control`/`pages` from UI-2, `graph`/`graph_page` from UI-3); version is `0.3.0`, above UI-2's `0.2.0` |
| `ui/loom_ui/server.py` | imports, `_dispatch`, JSON handlers, helpers | UI-2's `_load`/`_crash`/`_html`/control handlers kept verbatim; the graph routes re-expressed against them (see below) |
| `ui/loom_ui/templates.py` | the whole page body | UI-2's `shell`/`header_shell`/`_nav` kept; the graph adds one nav item and an `extra_css` hook instead of the old bare link |

Three things changed in the merge that are worth reviewing:

1. **The graph page is now inside the shared shell.** It previously rendered its own document
   (own `<main>`, own header, no nav). It now calls `templates.shell(..., current="graph",
   extra_css=GRAPH_CSS)`, so `/graph` has the same skip link, landmarks, nav with
   `aria-current="page"` and focus ring as the control room and the inspector, and there is one
   `h1` per page. `shell` gained one optional parameter, `extra_css`, appended after the shared
   stylesheet; no existing caller changes.
2. **The graph uses UI-2's failure paths instead of its own.** The old `_graph_model` read the
   snapshot itself and had a private `_closed` helper that could only render the version-gate page.
   It now takes the snapshot from `self._load()`, so the graph inherits the same gates and the same
   distinct responses UI-2 established: version gate → `503` closed page, unreadable registry →
   `503` unavailable page, render failure → `500` with no partial page (and JSON equivalents).
   A graph that cannot be built is a `500`, not a silently empty diagram.
3. **A missing scope stays data, not an exception.** `graph.GraphScopeError` becomes
   `graph.missing(...)`: `404` with the reason on HTML, `{"error": "unknown_scope"}` on JSON.

Everything the earlier review approved still holds: edges come from `engine.task_edges`, upstream is
the same edges walked in reverse, downstream is `engine.dependents_index` +
`engine.transitive_dependents`, focus closure is complete in text while only the drawing is capped,
boundary edges/unknown dependencies/cycles are reported rather than dropped, advisory
`provider-parity` edges are drawn dashed and marked non-gating, and the view is read-only.

## What was built

A dependency view over the registry that is a **projection of the engine's edges**, plus the
accessible table that carries the same facts without the drawing.

| File | Role |
|---|---|
| `ui/loom_ui/graph.py` | read model: program/focus scope, node and edge views, transitive upstream, transitive downstream, shortest paths, layers, boundary edges, cycles and unknown dependencies |
| `ui/loom_ui/graph_page.py` | the rendered view: inline SVG DAG and the complete table equivalent, no script, inside the shared shell |
| `ui/loom_ui/server.py` | `GET /graph`, `GET /api/graph`; query parsing; a missing scope is a named `404` |
| `ui/loom_ui/templates.py` | a `Graph` nav item and an `extra_css` hook for pages that need their own rules |
| `ui/tests/test_graph.py` | 32 tests: engine parity, scoping, paths, bounds, escaping, HTTP boundaries, fail-closed |

## Decisions and their reasons

1. **Every edge comes from `engine.task_edges`.** That is the call `loom claim` uses to refuse a
   claim, so the drawn graph and the refusal cannot disagree. Downstream reachability is
   `engine.dependents_index` + `engine.transitive_dependents`; upstream is a breadth-first walk of the
   same edges in reverse. The upstream traversal is written here because the engine has no reverse
   closure helper, but it consumes the engine's edge resolution, not a copy of it.
   `tests/test_graph.py` compares all three to the engine directly.
2. **No portfolio-wide default.** `/graph` with no parameters lists the 19 programs with task,
   claimable and state counts. Only a program (1–43 tasks in the live sample) or a task focus is
   drawn. This is a property of the read model (`mode="chooser"`), not just the template, so a future
   client cannot accidentally ask for "everything".
3. **Focus is unbounded in text, bounded in drawing.** A focus closure always lists every transitive
   prerequisite and dependent in the table; only the SVG is capped at 60 nodes, and the page says how
   many were not drawn. A program above the cap gets no diagram at all and asks for a focus, because a
   program-shaped picture at that size would be unreadable rather than informative.
4. **Three ways to lose a dependency, all closed.** A prerequisite outside the program becomes a
   listed boundary edge (including its program); a dependency naming no task becomes a "data
   problems" entry; a cycle is detected in the upstream/downstream walk and named instead of being
   walked forever. The tests cover each.
5. **Advisory edges are labelled, not hidden.** `provider-parity` edges are recorded by Loom but do
   not gate readiness, so they are drawn dashed and marked "advisory, does not block"; only
   `depends-on` edges are marked "blocks a claim". Hiding them would misrepresent the registry;
   drawing them like blockers would overstate the gate.
6. **Columns mean something.** A program is layered topologically from its prerequisites; a focus is
   layered by distance, prerequisites left of it and dependents right of it. The focus column is
   identified in the read model (`focus_column`) so the page does not recompute it.
7. **Accessibility is not an afterthought.** The SVG is `role="img"` with `<title>` and `<desc>`
   (task count, edge count, focus, upstream/downstream counts, and what the arrows mean); its scroll
   container is keyboard focusable with a label pointing at the table. The table below carries every
   node, every edge, each shortest path in prerequisite → dependent order, with `<caption>`,
   `<th scope="col">` and `<th scope="row">`. State and claimability stay separate columns, and a
   blocker reason is shown rather than collapsed into a colour. There is still no script, so a task
   title cannot become executable markup.
8. **Read-only is unchanged.** `/graph` adds only `GET`/`HEAD` routes; `POST /graph` is `405` with
   `Allow: GET, HEAD`, a foreign `Origin` is `403`, a non-loopback `Host` is `421`, and a version
   mismatch closes both the page and the JSON with `503`. The graph cannot claim, transition or
   dispatch anything.

## Verification

All commands run from `capabilities/work` on this branch (rebase already applied; tree clean at the
reviewed commit).

### UI suite — 151 tests, `OK`

```bash
python3 -m unittest discover -s ui/tests -t ui/tests
# Ran 151 tests in 33.2s — OK
```

151 = UI-1's foundation + UI-2's control room/inspector (56) + this slice's 32 graph tests. The
graph tests are unchanged by the rework: they passed before the rebase and after it, which is the
point — the merge moved where the page is attached, not what it says.

### Engine suite — 328 tests, `OK` (2 skipped)

```bash
python3 -m unittest discover -s tests
# Ran 328 tests in 105.7s — OK (skipped=2)
```

Nothing outside `capabilities/work/ui` (and this evidence file) was modified; this run confirms the
engine, MCP, A2A, validator and surface tests are unaffected by the rebase.

### Live smoke test on the integration candidate (real registry, `UNPINNED`)

Started from this checkout with an isolated `HOME` (so the installed release is not the front door)
against the real `~/.local/state/loom/work.db`:

sample `2026-11-17T05:29:32Z` · 19 programs · head event 2509 · digest `00c1880802d6d0e2`

```text
GET /graph                              200  19 programs listed, no diagram
GET /graph?program=loom-ui              200  7 tasks, 10 edges, 5 layers, 0 boundary edges
GET /graph?task=loom-ui%2FUI-6          200  focus page: 5 upstream, 1 downstream
GET /graph?program=nope                 404  names the missing scope
GET /api/graph?program=loom-ui          200  {"read_only": true, "graph": {"mode": "program"}}
GET /api/graph?task=loom-ui%2FUI-6      200  5 upstream, 1 downstream, cycles [], unknown []
GET /api/graph?program=nope             404  {"error": "unknown_scope"}
POST /graph?program=loom-ui             405  Allow: GET, HEAD
GET /graph          Origin: evil.example 403  {"error": "origin_rejected"}
GET /api/graph      Host: evil.example   421  {"error": "host_not_loopback"}
```

Shell integration checked on the rendered page:

```text
<nav class="views" aria-label="Views"><ul>
  <li><a href="/">Focus</a></li>
  <li><a href="/tasks?program=loom-ui">All tasks</a></li>
  <li><a href="/graph" aria-current="page">Graph</a></li>
</ul></nav>
one <h1>; zero <script>; Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; ...
```

The graph agrees with the front door on the same snapshot:

```text
$ ./loom show loom-ui/UI-6
  deps:  loom-ui/UI-2, loom-ui/UI-3, loom-ui/UI-4, loom-ui/UI-5
  blocks: loom-ui/UI-7

GET /api/graph?task=loom-ui/UI-6
  upstream:  UI-1, UI-2, UI-3, UI-4, UI-5
  downstream: UI-7
  cycles: [], unknown dependencies: []
```

Fail-closed, with the real `HOME` (an installed release is the front door, so this checkout's engine
sources differ from it):

```text
GET /graph?program=loom-ui        503  "Loom UI is not serving" (verdict MISMATCH)
GET /api/graph?program=loom-ui    503  {"error": "version_gate_closed"}
```

### Validator

```bash
python3 validate_work.py
# FAIL (2 errors, 104 warnings)
```

Both errors are pre-existing and unrelated to this slice: `midas/W-8: heading W-8 missing from
midas.program` and `loom/LOOM-14: heading LOOM-14 missing from loom.program`. None of the findings
are in `capabilities/work/ui`.

## Limits and known gaps

- **The layout is simple, not a graph library.** It is layered and deterministic; long edges can pass
  behind unrelated boxes. It is readable at the sizes this registry has (largest program: 43 tasks)
  and bounded above 60, but it is not a general layout engine.
- **No zoom, pan buttons or click-to-focus in the drawing.** With no script, the table links are the
  navigation. The SVG is a summary; the table is the interface.
- **Paths are the shortest path, not every path.** A task reachable by two routes shows one.
- **A node in a dependency cycle appears in both the upstream and the downstream list** and is
  labelled `both (cycle)`. That is deliberate: the cycle is the finding.
- **Program mode lists boundary edges but does not draw them.** Focus mode does include a
  cross-program prerequisite as a node, marked `outside_program`.
- **State is one snapshot.** There is no diff of the graph between revisions or events; that belongs
  to a history view, not this slice.
- **The graph pays for a read model it does not use.** `/graph` goes through `_load()`, which builds
  the task read model as well as reading the snapshot. That is the price of using UI-2's single
  gate-and-read path instead of a second one; the read is one snapshot either way.
- **`__version__` is `0.3.0`.** It sits above UI-2's `0.2.0`; whoever integrates next should keep the
  number moving forward rather than reconciling by hand as this rework had to.
- **No promotion.** `loom deploy promote`, owner acceptance and the supported-environment decision
  remain UI-7.

## Reproduce

```bash
cd capabilities/work
python3 -m unittest discover -s ui/tests -t ui/tests     # 151 tests
python3 ui/serve.py --port 8788                          # browse http://127.0.0.1:8788/graph
python3 -m unittest discover -s tests                    # 328 engine tests
```

## Review asks

1. Is the merge resolution right — in particular, is taking UI-2's `_load()` path for `/graph` (and
   therefore building the task read model it does not need) better than a second gate-and-read just
   for the graph?
2. `templates.shell` gained `extra_css` so `/graph` could keep its drawing rules inside the shared
   shell. Is an optional CSS hook the right seam, or should page-specific CSS live in its own linked
   block?
3. Is "program or focus, never the portfolio" still the right default boundary now that the control
   room has a program filter that could also scope the graph?
4. Upstream is a reverse walk of `engine.task_edges` because the engine exposes no reverse closure.
   Should that helper move into `engine` so later surfaces cannot drift — accepting a change outside
   `capabilities/work/ui` in a later task?
5. Is 60 the right drawing cap, and is "program too large → table only, ask for a focus" better than
   truncating the program graph?
