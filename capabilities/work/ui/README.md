# Loom UI — owner control room and task inspector

A local, loopback-only, read-only view of the work registry. This is the UI-2 slice: the owner
control room, the task index and the task inspector, on top of UI-1's app shell, version gate and
per-source provenance. It has **no** mutation, dispatch or policy route — those are later,
separately reviewed slices.

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
| `GET /tasks` | every task (`?program=` filters) |
| `GET /task/<id>` | the task inspector; `404` HTML when unknown |
| `GET /api/health` | gate state; `503` when closed |
| `GET /api/version` | front door, release commit, digests, verdict |
| `GET /api/control` | the queues and rows as JSON (`?program=` filters) |
| `GET /api/tasks`, `GET /api/tasks/<id>` | task views; the single-task form adds `detail` (review, receipt, questions, attempts, events, evidence, dependencies) |
| `GET /api/boards` | boards with task counts |
| `GET /api/provenance` | source signals only |
| `GET /api/snapshot` | full read model plus every provenance signal |

Unknown routes are `404`; unsupported methods are `405` with `Allow: GET, HEAD`.

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

## Tests

```bash
cd capabilities/work
python3 -m unittest discover -s ui/tests -t ui/tests -v
```

The suite covers read-model parity with `store.SqliteStore.load`, claimability agreement with the
engine's own checks, queue assignment (one bucket per task), owner identity routing, review and
receipt currency, the version-gate verdicts, independent provenance sampling, the HTTP boundaries
(loopback Host, read-only methods, escaping), the empty/stale/partial/unavailable states, and the
accessibility scaffold on every page.
