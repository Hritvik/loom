# UI-4 — Add read-only operations visibility

Task `loom-ui/UI-4` · revision 10 · attempt `#a5` · epoch 3 · branch `loom/loom-ui-ui-4-a5` · write
path `capabilities/work/ui`
Plan `plans/loom/2026-11-15-ui-plan.md` · items `wayfinder/loom-ui/work-items.md`

The behaviour in this slice was reviewed and approved at revision 8 (review request 2364, answer
2377). This attempt does the rework the coordinator asked for at revision 9: **rebase the slice onto
main after UI-2 integrated, resolve the shared files, and put the resulting integration candidate up
for review**, because UI-3 and UI-4 collide with UI-2 in the same four files and one resolution can
serve both.

| | |
|---|---|
| Base of this candidate | `163bc94` — UI-3's integration candidate, which is `489deb2` (main, UI-2 integrated) plus the UI-3 graph |
| Commits under review | `e9e5e19` (feat: operations visibility), `a7470b4` (fix: start grace + failure counting), plus the integration commit that carries this file |
| Registry base revision | 9 (`loom show loom-ui/UI-4`), claimed at 10 |

The candidate is stacked on UI-3 deliberately: UI-3 is green and awaiting its own review, it already
carries the `templates.shell` + `extra_css` + nav change UI-4 needs, and the coordinator's note said
UI-4 contains UI-3 so one conflict resolution serves both. If UI-3's blob changes before it lands,
this stack is re-parented onto the new UI-3 commit; the resolutions below do not change.

## What this rework changed

1. **Rebased onto the integrated tree.** `git rebase --onto 163bc94 22100c3` replayed both UI-4
   commits. Content conflicts appeared in exactly the four files the coordinator named — `README.md`,
   `loom_ui/__init__.py`, `server.py`, `templates.py` — and in nothing else.
2. **The page renders inside the shared shell instead of its own.** The pre-rebase branch carried a
   private `page()` shell, `_nav`, `_badge` and the UI-1 `CSS`. UI-2 replaced all of that. The
   resolved `templates.py` keeps UI-2's shell, primitives (`esc`, `badge`, `stamp`,
   `signals_table`, `degraded_banner`) and fail-closed pages, keeps UI-3's `extra_css` hook and Graph
   nav item, and grafts the operations view on as one appended section with its own `OPERATIONS_CSS`
   and a `/operations` nav item. Every operations block is escaped, captioned, `scope`-headered and
   inside a focusable scroll region, like the rest of the app.
3. **One gate and one snapshot, not two.** Before integration `GET /operations` went through
   `Application.operations()`, which re-inspected the version and re-read the database — a second
   snapshot path that UI-2's `_load()` exists to replace. Both operations handlers now take
   `(version, snapshot)` from `_load()`, so a closed gate or an unreadable registry produces the same
   distinct `503`s as every other page, and the numbers match the task list because they come from
   the same read. `Application.operations()` was removed rather than left as a second door.
4. **Version.** UI-2 shipped `0.2.0`, UI-3 `0.3.0`; with both present this branch is `0.4.0`.

### Resolutions, file by file

| File | Resolution |
|---|---|
| `ui/README.md` | Kept UI-2's control-room, inspector, graph, empty/stale/partial states and accessibility sections; added the `/operations` and `/api/operations` route rows and the **No controls** boundary row; merged the two "what the suite covers" paragraphs instead of choosing one |
| `ui/loom_ui/__init__.py` | Layout docstring lists `control`, `pages`, `graph`, `graph_page` and `operations`; `__version__ = "0.4.0"` |
| `ui/loom_ui/server.py` | UI-2's imports, `_load`, `_crash`, `_graph_page` and route table kept; `/operations` added to the page routes; `_operations_page`/`_operations` rewritten onto `_load()`; `Application.operations()` deleted |
| `ui/loom_ui/templates.py` | UI-2's shell/CSS/primitives kept verbatim; UI-3's `extra_css` and Graph item kept; operations section appended (`OPERATIONS_CSS`, `_age`, `_capacity`, `_attempts`, `_leases`, `_failures`, `_tick`, `_schedule`, `_release`, `_log`, `operations_page`) and rendered through `shell(...)`. The old private shell, `_nav`, `_badge`, `_counts`, `_claimable`, `_attention` are gone — `pages.py`/`frame` own that now |

### One engine-side change the rebase exposed

`loom schedule install` writes `StartInterval` from `schedule.tick_interval`. On the tree this branch
was cut from (`22100c3`) that key was **absent from `loom.DEFAULTS`**, so with no readable plist the
only value left was the installer's literal 120s and the view called it `default`. Main has since
added it:

```console
$ git show main:capabilities/work/loom | sed -n '54,55p'
    "schedule": {"tick_label": "local.loom-tick", "linear_label": "local.loom-linear",
                 "tick_interval": 120, "linear_interval": 300},
```

So on the integration base the honest source without a plist is `config`, and `default` now means
only "the front door could not be read at all". The two tests that asserted `default` were updated to
say `config` (`test_an_interval_no_plist_supplies_is_reported_with_its_source`,
`test_a_stale_tick_says_where_its_interval_came_from`), and `_tick_interval`'s docstring now
describes the three cases in that order. This is a correction to decision 8 below, not a behaviour
change: the view still publishes `interval_source` and still refuses to present an assumption as a
measurement.

## What was built (unchanged by the rework)

A read-only operations view at `/operations` (`/api/operations` for JSON) that explains attempts,
capacity, expired leases, stuck runs, failed/stale/lost launches, the last recorded tick pass, the
schedule, the promoted-release gate and the tick log. Every source is sampled independently and
keeps its own `as_of` and `ok`/`degraded`/`unavailable` status.

| File | Role |
|---|---|
| `ui/loom_ui/operations.py` | typed operations read model; engine folds, front-door config, tick record, launchd, log tail |
| `ui/loom_ui/model.py` | `attempt_start_grace_expired` — the one start-grace predicate the process and launch views share |
| `ui/loom_ui/provenance.py` | the foundation's `process` source uses the same grace predicate |
| `ui/loom_ui/templates.py` | the operations page inside the shared shell, plus the nav item; all values escaped, still no script |
| `ui/loom_ui/server.py` | `GET /operations` and `GET /api/operations` through the shared gate and snapshot |
| `ui/tests/test_operations.py` | 27 tests: front-door parity, start grace, failure counting, freshness, schedule/log, no-control HTTP surface |
| `ui/README.md` | routes, operations sources, parity and the no-control boundary |

## Decisions and their reasons

1. **Values come from the engine's own folds, not a second implementation.**
   `engine.attempt_views` + `engine.live_attempts` produce live workers and capacity; limits come
   from the front door's `load_config`; expired leases use the predicate in `engine.cmd_tick` (a
   claim with no parseable expiry is expired); stuck attempts use the configured
   `worker.stuck_after` and the tick's age calculation. `test_capacity_leases_and_live_attempts_match_tick_json`
   runs the real `loom tick --json --now <snapshot time>` against the same disposable registry and
   compares `active`, `limits`, `expired` and `liveAttempts` field by field.
2. **One snapshot time, several sample times.** Capacity, leases, attempt age, the stuck threshold
   and tick freshness are computed at the snapshot's read time, so the operations page cannot
   disagree with the task list. The pid probe, `launchctl`, the plist, the release digest and the
   log each carry their own `as_of`. The page shows both the operations sample and the registry
   snapshot time.
3. **The tick is a recorded pass, never a live one.** The view reads the last line of
   `~/.local/state/loom/tick.decisions.jsonl` (or `$LOOM_TICK_DECISIONS`), the durable record
   `tick --apply` writes. A record older than three schedule intervals is `degraded`; a missing or
   malformed record is `unavailable`. The UI never invokes `tick`, so a page refresh cannot claim,
   launch, review or integrate anything.
4. **Failures are separated from capacity health.** `process` reports live workers, open attempts
   and limits. `launches` reports terminal `failed` attempts, `starting` attempts past the engine's
   `ATTEMPT_START_GRACE`, and open attempts whose recorded process is gone. A historical failure
   therefore does not make capacity look unhealthy; it does keep the launch record degraded, which
   is the fact an operator needs to inspect.
5. **One start-grace predicate, not two.** `model.attempt_start_grace_expired` answers "has this run
   had the engine's `ATTEMPT_START_GRACE` to attach a process?" and both the `process` capacity line
   and the `launches` stale-start record use it, so the two sources cannot call the same attempt
   healthy and stale at the same instant. A claim inside the window is a spawn in flight (`ok`); a
   pid-less attempt past it is `degraded` in both. The foundation's `provenance.process` uses the
   same predicate, and still flags a `launch` record that claims to have spawned a run but recorded
   no pid immediately, because that hand-off is incomplete at any age.
6. **A headline never reports a capped list as the whole record.** Every failure is counted; only
   the display is limited to `MAX_FAILURES = 20`, and the view exposes `failures_total`, the
   `launches` signal exposes `shown`/`not_shown`, its detail says "the most recent 20 are listed",
   and the page carries the same note.
7. **The schedule is the plist *and* launchd.** `loom schedule status` reads the plist, the loaded
   job and the log. This view reads the same three: the plist (label, interval, arguments), the
   `launchctl list` row, and a bounded tail of `~/.local/state/loom/tick.log`. A plist that exists
   but is not loaded is `degraded`.
8. **The tick interval records whether it was observed, configured or assumed.** The order is the
   plist's `StartInterval` (`plist`), then the front door's `schedule.tick_interval` (`config`),
   then the installer's literal 120s (`default`, reachable only when the front door cannot be read).
   A stale tick whose threshold rests on anything but the plist says so —
   "3 x the 120s schedule (config interval, not the installed plist)" — instead of presenting an
   assumption as a measurement. *(Corrected in this rework: `schedule.tick_interval` is now in the
   front door's `DEFAULTS`, so `config` is the normal no-plist answer, not `default`.)*
9. **Release health is labelled as the UI gate verdict.** The `release` source is sampled through
   the same `provenance.release` function as the foundation: `PINNED`/`UNPINNED`/closed verdict,
   release commit, tree digest verification and running root. `loom deploy status` additionally
   compares development `HEAD` with the release; that is a different question and stays a CLI
   operation, so the field is named `UI gate verdict` rather than borrowing `deploy status`'s word.
10. **There is no control to forget to guard.** The page has no form, button, input or script; only
    `GET`/`HEAD` are routed. Every other method on `/operations` is `405` with `Allow: GET, HEAD`.
    No apply, dispatch, stop/start, promote, rollback or reanchor endpoint exists. Tests assert the
    absence of a control surface, not just the absence of a known route.
11. **The log is read, not slurped.** `_tail` seeks to the last 64 KiB and returns at most 20 lines,
    because the scheduler appends to `tick.log` without rotation. Malformed bytes are replaced
    rather than failing the whole page.

## Verification

All commands below were run on this branch unless a line says otherwise.

### UI suite — 180 tests, `OK`

```console
$ cd capabilities/work && python3 -m unittest discover -s ui/tests -t ui/tests
Ran 180 tests in 39.9s
OK
```

That is UI-1/UI-2/UI-3's 151 plus UI-4's 27 in `test_operations.py` and the two grace tests in
`test_provenance.py`. `test_operations.py` covers parity with `loom tick --json` for capacity,
limits, expired leases and live attempts; a claim with no expiry; a failed launch with its reason; a
stale `starting` attempt; an in-grace start that must stay healthy; both sources agreeing on a stale
start; the failure headline outliving the 20-row display limit; a run whose process vanished; a
stuck attempt through the engine's own threshold; a healthy process source despite historical
failures; tick record fresh/stale/missing/malformed; the interval's plist/config/default provenance;
schedule loaded/not loaded; log tail escaping; every source's `as_of` and status; the release
verdict; `/api/operations` shape; no control surface; and a closed version gate returning `503` on
both operations routes.

### Engine suite — 328 tests, `OK (skipped=2)`

```console
$ cd capabilities/work && python3 -m unittest discover -s tests
Ran 328 tests in 97.8s
OK (skipped=2)
```

Only `capabilities/work/ui` is touched by this branch apart from this evidence file; this run is the
check that the engine, store, validator, MCP and A2A surfaces still agree.

### Validator — 2 errors, both outside this task's write path

```console
$ cd capabilities/work && python3 validate_work.py
ERROR midas/W-8: heading W-8 missing from midas.program
ERROR loom/LOOM-14: heading LOOM-14 missing from loom.program
FAIL (2 errors, 106 warnings)
```

No UI-4 error. The only UI-4 line is registry/program drift, expected while the claim is live:

```console
WARN  loom-ui/UI-4: registry says claimed, program says planned
```

### Front-door parity, sampled on the live registry

Read-only preview against the real registry at `2026-11-17T05:56Z`:

```console
$ ./loom tick --json --now "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
limits       implementer 4, reviewer 3, integrator 1
active       implementer 2, reviewer 0, integrator 0
expired      jarvis/ARCH-09  opencode/jarvis-agent
liveAttempts loom-ui/UI-4 pid 96689, teach/W-8 pid 6856

$ ./loom workers
loom-ui/UI-4  implementer pid 96689  loom-ui/UI-4#a5
teach/W-8     implementer pid 6856   teach/W-8#a3
```

The field-by-field comparison of those four values against the operations view is asserted in the
suite (`test_capacity_leases_and_live_attempts_match_tick_json`), which shells out to the real
`loom` for the same `--now`.

### Live page smoke — the gate closed, and that is the finding

Serving this checkout on `127.0.0.1:8799` returns `503` on every route, including `/operations`:

```console
$ python3 capabilities/work/ui/serve.py --port 8799
GET /             -> 503    GET /operations        -> 503
GET /graph        -> 503    GET /api/operations    -> 503
<h1>Loom UI is not serving</h1>
Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; …; form-action 'none'
```

`GET /api/version` explains why, and it is not this branch:

```console
verdict          DRIFTED
detail           release tree ~/.local/state/loom/engine no longer matches the digest recorded at
                 promotion (80da114da8f3 recorded, 3161f735144f now); promote again from a reviewed
                 commit
release commit   640eb8c82801, promoted 2026-11-16T02:28:42Z
```

The promoted tree gained one file after promotion — `capabilities/work/evidence/W-7.md` is the only
non-`__pycache__` file under `~/.local/state/loom/engine` with an mtime later than the promotion:

```console
$ cd ~/.local/state/loom/engine && find . -newermt "2026-11-16 02:29" -type f \
    -not -path "*/__pycache__/*" -not -path "./.git/*" -not -name "*.db*" -not -name "*.log"
./capabilities/work/evidence/W-7.md
```

So the fail-closed path is what this run verified end to end, and a page-level smoke (real page, real
`launches`/`tick`/`scheduler`/`logs` rows) needs a promotion first. Promoting is the coordinator's
call, not this task's, and writing into the promoted tree to make the gate open would be exactly the
drift the gate exists to catch. The reviewer who wants the rendered page can serve the same commit
from a freshly promoted tree; nothing in this branch depends on the drift.

## Limits and known gaps

- **The tick section can be older than the page.** It is the last recorded `tick --apply` pass, with
  its age shown; the UI does not run a preview or an apply. A tick that never wrote a decision record
  is `unavailable`, not "no work".
- **`tick.log` is not a heartbeat.** The scheduled pass runs `--quiet`, so a healthy quiet tick can
  leave the log untouched. The log's `as_of` is its mtime, and the view says "last written" rather
  than implying the tick last ran then. The decision record is the heartbeat.
- **`launchctl` is macOS-only.** On another platform the job is `unknown` and the source is
  `degraded`; the rest of the page still renders. Cross-platform scheduling is an owner decision
  (UI-7), not a hidden fallback.
- **The tick interval can still be assumed.** With no readable plist and an unreadable front door,
  the interval is the installer's 120s literal and the view says `default`.
- **Process liveness is a point-in-time probe.** A worker that exits between the pid probe and the
  render is shown live until the next request. That is the same probe `loom workers` uses.
- **Historical failures keep `launches` degraded.** The list is bounded at 20 rows for display but
  counted in full (`failures_total`) and not time-windowed; old failed attempts remain visible until
  the audit record is understood. This is deliberate (failures are facts), but an operator may want
  a recency filter later.
- **Release health is the UI gate verdict, not `deploy status`.** The two answer different
  questions; the field is labelled accordingly and no promotion or rollback control is exposed.
- **No actions.** Claim/renew/release/ask/answer and transitions remain UI-5, and dispatch/`tick
  --apply` remain CLI-only. This slice can only show state.
- **The candidate is stacked on UI-3.** It contains `163bc94` rather than main, on purpose (one
  resolution serves both slices). If UI-3 changes, this branch is re-parented and nothing in the four
  resolved files changes.

## Reproduce

```bash
cd capabilities/work
python3 -m unittest discover -s ui/tests -t ui/tests     # 180 tests
python3 -m unittest discover -s tests                    # 328 engine tests
python3 validate_work.py                                 # 2 errors, both outside ui/
python3 ui/serve.py --port 8788                          # browse /, /graph, /operations
./loom tick --json --now "$(date -u +%Y-%m-%dT%H:%M:%SZ)"  # compare the front door
```

## Review asks

1. **The integration, not just the slice.** Are the four resolutions right — in particular, does
   `/operations` rendering inside `templates.shell` (rather than its own shell) keep everything UI-2
   promised, and is routing both operations handlers through `_load()` the correct single-snapshot
   and single-gate shape?
2. **The stack.** Is reviewing UI-4 on top of UI-3's candidate the right way to land one resolution
   for both, given UI-3 is green and awaiting review? If UI-3's blob moves, is re-parenting enough?
3. **The corrected interval source.** On this base `schedule.tick_interval` *is* in the front door's
   `DEFAULTS`, so an interval with no plist reports `config`, not `default`. Is that the right
   reading for an operator, or should the view treat the front door's DEFAULTS value as "default"
   anyway because nobody configured it?
4. **Start grace and failure counting** (unchanged from the approved revision 8): a claim inside the
   window is `ok` and past it is `degraded` in both `process` and `launches` through one predicate;
   three schedule intervals is the recorded-tick staleness threshold; the 20-row display bound is
   never presented as the total.
5. **The drifted release.** The gate closing on `~/.local/state/loom/engine` is a real finding about
   the environment, not this branch. Does the coordinator want it promoted so the page-level smoke
   can be re-run, or is the in-suite front-door parity enough for this review?
