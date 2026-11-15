# UI-1 — Build the local read-only UI foundation

Task: `loom-ui/UI-1` · branch `loom/loom-ui-ui-1` · write path `capabilities/work/ui`
Plan: `plans/loom/2026-11-15-ui-plan.md` (private planning repo) · items:
`wayfinder/loom-ui/work-items.md`

## What was built

A loopback-only, read-only app shell over the work registry, plus the three foundations the later
UI slices depend on: a consistent read model, engine-derived claimability, and per-source
provenance.

| File | Role |
|---|---|
| `capabilities/work/ui/serve.py` | entry point (`python3 ui/serve.py`) |
| `ui/loom_ui/version.py` | active front-door resolution and the fail-closed version gate |
| `ui/loom_ui/snapshot.py` | one `BEGIN DEFERRED`, `mode=ro` read of documents, edges, claims and the event head |
| `ui/loom_ui/model.py` | typed task/board/claim views; claimability from `engine`, not a copy |
| `ui/loom_ui/provenance.py` | six independent source signals, each with its own `as_of` and status |
| `ui/loom_ui/server.py` | GET/HEAD routing, loopback/Host/Origin checks, security headers, fail-closed pages |
| `ui/loom_ui/templates.py` | server-rendered shell; every value escaped, no script |
| `ui/tests/` | 62 tests (unit + HTTP) on disposable work homes |

## Decisions and their reasons

1. **The UI imports `engine` and `store`; it does not restate them.** Claimability is
   `status in engine.CLAIMABLE_FROM` + `engine.dependencies_met` + `engine.write_path_conflicts` +
   the lease gate from `engine.active_claim` / `engine.lease_expiry`. That is the same set of checks
   `loom claim` performs, so "claimable" means `claim` would succeed rather than merely "ready".
   `tests/test_model.py` asserts each fact against the engine directly.
2. **`ready` is shown separately from claimability.** `demo/W-2` is `ready` and not claimable
   (unmet dependency); `demo/W-4` is `ready` and not claimable (write-path conflict with a live
   claim); `demo/W-7` is `ready` with an expired lease, which `claim` refuses and `takeover`
   resolves. The read model says so instead of collapsing both into one colour.
3. **One snapshot per request.** `snapshot.read` opens SQLite `mode=ro` with `PRAGMA query_only`, then
   reads `meta`, `boards`, `tasks`, `edges`, `claims` and the event log inside a single
   `BEGIN DEFERRED`. The UI cannot see a task and a claim from different instants, and it cannot
   write even if a route were added by mistake. `tests/test_snapshot.py` compares the snapshot to
   `store.SqliteStore.load()` field by field, so schema drift fails a test.
4. **The version gate compares what was loaded, not what is on disk.** The active front door comes
   from `loom.running_root()` — the promoted release when one exists, otherwise the development
   checkout. The gate then (a) requires `engine.__file__` and `store.__file__` to live in the UI's
   engine home, (b) compares the three engine source digests, and (c) re-digests the release tree
   against the value recorded at promotion. Verdicts: `PINNED` (serve), `UNPINNED` (serve, reported
   degraded: no release is installed), `MISMATCH`, `DRIFTED`, `ABSENT` (all refuse to serve).
5. **Six sources, six timestamps.** `database`, `process`, `scheduler`, `release`, `git`, `evidence`
   are sampled independently; any failure becomes `degraded`/`unavailable` for that source and the
   rest of the page still renders. No signal inherits another's `as_of`.
6. **No mutation surface exists to forget to guard.** Only `GET`/`HEAD` are routed; every other
   method is `405` with `Allow: GET, HEAD` and a body that says actions belong to `loom`. There is no
   dispatch, transition, tick or deploy route, and no browser-supplied actor anywhere.
7. **Loopback is enforced, not documented.** `_loopback()` refuses a non-loopback bind; every request
   must carry a loopback `Host`; a foreign `Origin` is rejected. Cross-origin, LAN and proxy
   deployments need a separate auth/transport design and are out of scope.

## Verification

All commands run from `capabilities/work` on this branch.

### UI suite — 62 tests, `OK`

```bash
python3 -m unittest discover -s ui/tests -t ui/tests
# Ran 62 tests in 10.1s — OK
```

Coverage by file: `test_snapshot.py` (8), `test_model.py` (13), `test_version.py` (9),
`test_provenance.py` (15), `test_server.py` (17). They exercise, among others: snapshot parity with
`SqliteStore.load`, claimability agreement with the engine checks, the five version verdicts, a
source that raises while the others still report, a live `sleep` process detected through a `launch`
event, a real `git` repository, missing evidence, every non-GET method, a non-loopback `Host`, a
foreign `Origin`, a refused non-loopback bind, XSS-shaped task text, and a closed gate rendering
`503` instead of data.

### Engine suite — 237 tests, `OK`

```bash
python3 -m unittest discover -s tests
# Ran 237 tests in 75.1s — OK
```

Nothing outside `capabilities/work/ui` was modified; this run confirms the engine, validator, MCP,
A2A and surface tests are unaffected.

### Live smoke test (real registry, `PINNED`)

```text
$ python3 ui/serve.py --port 8791
loom ui listening on http://127.0.0.1:8791 (read-only, loopback only)

GET /api/health                     200   ok=true  verdict=PINNED
GET /                               200
POST /api/tasks/demo/W-1            405   Allow: GET, HEAD
GET /api/health  (Host: evil.example)      421
GET /api/health  (Origin: https://evil.example)  403
```

`GET /api/snapshot` at `2026-11-15T13:40:33Z` returned 220 tasks across 25 boards, head event 1801,
and six independent signals — note the `evidence` sample is a second later than the rest, which is
the point:

```text
database   ok        2026-11-15T13:40:33Z  one consistent read of …/work.db: 220 tasks, 25 boards, head 1801
process    ok        2026-11-15T13:40:33Z  2 live worker process(es) of 2 pending launch(es)
scheduler  ok        2026-11-15T13:40:33Z  com.<owner>.loom-tick runs every 120s
release    ok        2026-11-15T13:40:33Z  release 070ab244f328 … intact and matches the engine sources this UI loaded
git        degraded  2026-11-15T13:40:33Z  19 of 20 owner repositories sampled; 1 unreadable
evidence   degraded  2026-11-15T13:40:34Z  40 evidence file(s) present; 80 missing
```

`git` and `evidence` are degraded because the portfolio genuinely has an unreadable owner path and
evidence files that were never written. The UI reports that instead of presenting it as health.

### Refusals

```text
$ python3 ui/serve.py --host 0.0.0.0 --port 8791
error: refusing to bind '0.0.0.0': this UI is loopback-only by design, …
exit=2
```

## Limits and known gaps

- **No actions.** Claim/renew/release/ask/answer and transitions are UI-5. This slice cannot mutate
  state, so it cannot be used to drive work — only to see it.
- **`git` sampling is bounded at 20 repositories.** The 21st and beyond are listed as truncated, not
  silently dropped. Each page load re-samples, which is fine locally but would need caching before
  it is called from a hot path.
- **The scheduler signal reads the launch-agent plist; it does not ask `launchctl`.** Loaded vs
  written state can differ, and the signal says only what the plist declares. UI-4 owns the
  operations view and can add the live probe.
- **`process` liveness is a point-in-time probe.** A worker that exits between the probe and render
  is shown as live; the next request corrects it.
- **The event head and registry come from the same transaction, but the six provenance sources do
  not share one.** That is deliberate and visible on the page.
- **The shared registry is not this task's.** `validate_work.py` was reporting 22 errors when this
  evidence was written, all in other programs' tasks; they came from process-audit records written
  by unreleased code and were fixed separately. None touched `capabilities/work/ui`.
- **Promotion is not done.** `loom deploy promote` and the owner acceptance gate are UI-7.

## Reproduce

```bash
cd capabilities/work
python3 -m unittest discover -s ui/tests -t ui/tests     # 62 tests
python3 ui/serve.py --port 8788                          # browse http://127.0.0.1:8788
python3 -m unittest discover -s tests                    # 237 engine tests
```

## Review asks

1. Is deriving claimability from the engine's own primitives — rather than from
   `loom list --ready` — the right reading of "shared Loom logic"? It is stricter: `--ready` ranks
   candidates, while `claim` also refuses an expired lease.
2. Is `UNPINNED` correctly open-with-degradation, or should a missing promoted release close the gate
   as well?
3. Are the six sources the right set for this slice, and is any of them sampled in a way that
   overstates freshness?
4. Does the fail-closed content negotiation hold for malformed or hostile requests not covered by the
   tests above?
