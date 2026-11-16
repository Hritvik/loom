# Loom UI — owner control room, task inspector, dependency graph and operations

A local, loopback-only, read-only view of the work registry. UI-1 built the app shell, version
gate, consistent read model and per-source provenance; UI-2 added the owner control room, the task
index and the task inspector; UI-3 adds the dependency graph on top of both; this branch adds the
read-only operations view. It has
**no** mutation, dispatch or policy route — those are later, separately reviewed slices.

## Run

```bash
python3 ui/serve.py                        # http://127.0.0.1:8788
python3 ui/serve.py --port 0               # pick a free port
python3 ui/serve.py --db /path/to/work.db  # another registry
python3 ui/serve.py --owner hritvik        # who "you" is for the owner queues; repeatable
```

Run it from the **promoted release** (`~/.local/state/loom/engine`), the same tree the scheduler
executes. Started from a development checkout while a release is installed, it refuses to serve:
the engine rules it imported would not be the engine rules that are running.

`--host` accepts only loopback names (`127.0.0.1`, `localhost`, `::1`). A LAN or proxied deployment
needs its own authentication and transport design; it is not a flag here.

## The control room (`/`)

Queues are ordered from "a person must act" to "settled", and **every task is in exactly one
queue**, so the counts always add up to the registry total.

| Queue | What belongs there | Derived from |
|---|---|---|
| **Needs you** | blocked tasks with `blocked_needs: human`; open review requests addressed to an owner identity; open questions addressed to an owner identity | `engine.HUMAN_IDENTITIES` (+ `--owner`), the ask/answer events |
| **Stalled or expired** | expired leases, agent-blocked tasks, rejected tasks, reviews that cannot proceed (rework, never submitted, stale request) | `engine.lease_expiry`, state, the latest review request |
| **Awaiting review** | green work with a current review request to a reviewer | `engine.latest_review_request` |
| **Awaiting integration** | reviewed work, with whether the approval is bound to a content digest | `task.review` |
| **Ready for acceptance** | integrated work, with whether it has an integration receipt | `engine.integration_receipt` |
| **In progress** | a live lease, with agent, epoch and remaining time | `engine.active_claim` |
| **Claimable now** | what `loom claim` would actually accept, ranked by unblocked work | `engine.CLAIMABLE_FROM`, `dependencies_met`, `write_path_conflicts`, the lease gate |
| **Not yet claimable** | ready/planned/draft work that cannot be claimed; the blocker, and any decision it needs | the same engine checks |
| **Settled** | accepted, done or released — counted, listed in All tasks, not in the daily queues | state |

A review request is "current" only when `engine.latest_review_request` says so. A request that names
an older revision is shown as stale and is never presented as actionable.

## Task index (`/tasks`) and inspector (`/task/<id>`)

`/tasks` lists every task with its state, claimability, queue, blocker, lease, revision, last event
and downstream count. `?program=<name>` scopes both pages.

The inspector shows, as **separate** facts:

- state and claimability, each with the claimability reasons and typed blockers;
- revision, the task epoch and the claim's fencing epoch;
- the dependency list and the downstream tasks it gates, each with its own state;
- the evidence locator, where it resolves, and whether the file exists now;
- the review verdict (reviewer, decision, approved commit, base, digest, path count);
- the integration receipt (event, integrated commit, tip, digest, path count, reason);
- every question and review request, including the answer and whether the request is current;
- attempts folded by `engine.attempt_views`, with pid, branch, epoch and liveness;
- the append-only event history (capped at the last 50, with the total named).

## Boundaries

| Boundary | How it is enforced |
|---|---|
| Loopback only | `_loopback()` refuses a non-loopback bind; every request must carry a loopback `Host`; cross-`Origin` requests are rejected |
| Read-only | only `GET`/`HEAD` are routed; every other method is `405` without reading a body |
| One snapshot | registry documents, edges, claims and all events are read inside one `BEGIN DEFERRED` transaction on a `mode=ro` connection |
| Shared rules | claimability, dependencies, path conflicts, review currency, receipts and attempts come from `engine`, not a copy |
| Graph parity | every drawn graph edge comes from `engine.task_edges`; the graph tests compare the view to the engine's own closures |
| No controls | the operations view reads a recorded tick, launchd and the tick log; there is no apply, dispatch, schedule start/stop, promote, rollback or reanchor route |
| Fail closed | a version mismatch or drifted release returns `503` and explains why; an unreadable registry returns a distinct `503`; a render failure returns `500` with no partial page |
| No script | the shell is server-rendered; every value is escaped and the CSP forbids everything but inline style |
| No file serving | evidence paths are shown, never served — there is no path-traversal surface |

## Empty, partial, stale and unavailable states

- **Empty** — each queue and table says "nothing in this sample" instead of rendering a bare header.
- **Partial** — a degraded or unavailable source raises a banner, and the affected row (for example
  a missing evidence file) says so. Nothing is filled in from a stale value.
- **Stale** — every sample time is shown with its age; a registry sample older than 60 seconds is
  labelled `STALE SAMPLE` in the page and in each queue caption.
- **Unavailable** — an unreadable database gets its own page ("Loom UI cannot read the registry"),
  distinct from the version-gate page, and no cached registry is shown.

## Accessibility

One `h1` per page, a skip link, `header`/`nav`/`main`/`footer` landmarks, `aria-current="page"` in
the nav, a visible focus ring, a caption and column `scope` on every table, focusable scroll regions
around wide tables, and text (not colour alone) for every state. The pages are responsive and need
no colour perception: state, claimability and queue are all spelled out.

## Routes

| Route | Returns |
|---|---|
| `GET /` | the owner control room (`?program=` filters) |
| `GET /graph?program=<program>`, `GET /graph?task=<id>` | program-scoped dependency graph, or a focus on one task's upstream/downstream paths, with a complete table equivalent |
| `GET /operations` | the rendered operations view |
| `GET /tasks` | every task (`?program=` filters) |
| `GET /task/<id>` | the task inspector; `404` HTML when unknown |
| `GET /api/health` | gate state; `503` when closed |
| `GET /api/version` | front door, release commit, digests, verdict |
| `GET /api/control` | the queues and rows as JSON (`?program=` filters) |
| `GET /api/graph?program=<program>`, `GET /api/graph?task=<id>` | the same graph view as JSON |
| `GET /api/operations` | attempts, capacity, leases, tick, schedule, release and log health |
| `GET /api/tasks`, `GET /api/tasks/<id>` | task views; the single-task form adds `detail` (review, receipt, questions, attempts, events, evidence, dependencies) |
| `GET /api/boards` | boards with task counts |
| `GET /api/provenance` | source signals only |
| `GET /api/snapshot` | full read model plus every provenance signal |

Unknown routes are `404`; unsupported methods are `405` with `Allow: GET, HEAD`. An unknown program
or focus task is a `404` that names what was missing, not an empty graph.

## Dependency graph

The graph is a projection of the engine's dependency data, not a second graph:

| Question | Answered by |
|---|---|
| which tasks does this one depend on | `engine.task_edges` (the same call `loom claim` uses to refuse) |
| what does this one transitively gate | `engine.dependents_index` + `engine.transitive_dependents` |
| what must finish before this one | the same edges walked in reverse, breadth-first |
| what is claimable | `model.claimability`, which is the engine's own predicate |

Rules that keep the picture honest:

- **No portfolio-wide default.** `/graph` lists programs and their task counts; it never draws every
  task. A program over 60 nodes is listed as a table with no diagram, and the page asks for a focus.
- **Focus wins, and is always complete in text.** `/graph?task=<id>` shows every transitive
  prerequisite and dependent. Only the drawing is bounded at 60 nodes; the table lists all of them
  and says how many were not drawn.
- **Advisory edges are labelled, not hidden.** `provider-parity` edges come from the registry and are
  drawn dashed, marked "advisory, does not block". Only `depends-on` edges gate a claim.
- **Scoping cannot hide a blocker.** A prerequisite outside the program becomes a listed boundary
  edge; a dependency naming no task becomes a "data problems" entry; a cycle is named rather than
  walked forever.
- **Accessible without the drawing.** The SVG is `role="img"` with `<title>`/`<desc>` and its scroll
  container is keyboard focusable; the table below carries every node, every edge, and each path in
  prerequisite → dependent order, with `<caption>` and `scope` headers. There is still no script, so
  task text cannot become executable.

## Provenance

Six sources are sampled independently, each with its own `as_of` and status (`ok`, `degraded`,
`unavailable`):

| Source | Sampled from |
|---|---|
| `database` | the snapshot's read time, head event and content digest |
| `process` | attempts folded with `engine.attempt_views`, probed for a live pid |
| `scheduler` | the launch agent plist for the configured tick label |
| `release` | `engine.release`, the running root, and a recomputed tree digest |
| `git` | `git -C <owner repo> rev-parse` for each distinct owner repository |
| `evidence` | existence of each task's evidence file |

They are never merged into one timestamp. Two sources sampled a second apart are still two samples.

## Operations

`/operations` explains what is running and what is stale without running anything:

| Fact | Derived from | Front-door parity |
|---|---|---|
| Capacity | `engine.attempt_views` + `engine.live_attempts`, limits from `loom.load_config` | `loom tick --json` `active` / `limits` |
| Worker attempts | the same attempt fold; a live pid is probed, not inferred from a launch state | `loom workers` |
| Expired leases | the predicate in `engine.cmd_tick`, from the same snapshot | `loom tick --json` `expired` |
| Stuck attempts | live attempts older than the configured `worker.stuck_after` | `loom tick` "Stuck attempts" |
| Failed / stale / lost launches | terminal `failed` attempts; `starting` attempts past `ATTEMPT_START_GRACE`; open attempts whose process is gone | the attempt record |
| Last tick pass | the last line of `~/.local/state/loom/tick.decisions.jsonl` (or `$LOOM_TICK_DECISIONS`) | the recorded `tick --apply` pass |
| Schedule | the launch agent plist plus `launchctl list` | `loom schedule status` |
| Release | `engine.release`, the running root and a recomputed tree digest | the UI gate verdict; `loom deploy status` additionally compares development HEAD, a different question |
| Tick log | a bounded tail of `~/.local/state/loom/tick.log` | `loom schedule status` log tail |

Every fact above belongs to one of seven independently sampled sources: `database`, `process`,
`launches`, `tick`, `scheduler`, `release` and `logs`. Each has its own `as_of` and status. A tick
record older than three schedule intervals is shown degraded, never as current health; a missing log
or decision record is `unavailable`, not an empty success. The UI never executes `tick`, starts or
stops a job, promotes or rolls back a release, or dispatches a worker. A page refresh runs only
reads.

## Tests

```bash
cd capabilities/work
python3 -m unittest discover -s ui/tests -t ui/tests -v
```

The suite covers read-model parity with `store.SqliteStore.load`, claimability agreement with the
engine's own checks, queue assignment (one bucket per task), owner identity routing, review and
receipt currency, the version-gate verdicts, independent provenance sampling, operations parity with
`loom tick --json` for the same `--now`, failed/stale/lost launches, tick-record freshness, the
schedule and log sources, the HTTP boundaries (loopback Host, read-only methods, escaping, no
control surface on `/operations`), the empty/stale/partial/unavailable states, and the accessibility
scaffold on every page.
