# UI-4 — Add read-only operations visibility

Task: `loom-ui/UI-4` · revision 6 · branch `loom/loom-ui-ui-4-a3` · write path `capabilities/work/ui`
Plan: `plans/loom/2026-11-15-ui-plan.md` (private planning repo) · items:
`wayfinder/loom-ui/work-items.md`

## What was built

A read-only operations view at `/operations` (`/api/operations` for JSON) that explains attempts,
capacity, expired leases, stuck runs, failed/stale/lost launches, the last recorded tick pass, the
schedule, the promoted-release gate and the tick log. Every source is sampled independently and
keeps its own `as_of` and `ok`/`degraded`/`unavailable` status.

| File | Role |
|---|---|
| `ui/loom_ui/operations.py` | typed operations read model; engine folds, front-door config, tick record, launchd, log tail |
| `ui/loom_ui/model.py` | `attempt_start_grace_expired` — the one start-grace predicate the process and launch views share |
| `ui/loom_ui/provenance.py` | the foundation's `process` source uses the same grace predicate |
| `ui/loom_ui/templates.py` | the operations page plus a nav link from the control room; all values escaped, still no script |
| `ui/loom_ui/server.py` | `GET /operations` and `GET /api/operations`; same loopback, read-only and fail-closed gates |
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
8. **The tick interval records whether it was observed or assumed.** `loom schedule install` writes
   `StartInterval` from `schedule.tick_interval` with a literal 120s default, and that key is not in
   the front door's `DEFAULTS`. The view therefore reports `interval_source` as `plist`, `config` or
   `default`; a stale tick whose threshold rests on the default says
   "3 x the 120s schedule (default interval, not the installed plist)" instead of presenting an
   assumption as a measurement.
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

## Rework after review request 2330

The reviewer requested changes on `83c6298` (review request 2330, answer seq 2344). All three
findings are fixed, each with a test that fails on the old code and passes now.

1. **`process` degraded for an in-grace start (was `operations.py:382`).**
   `unreadable` was every open attempt without a pid, with no gate, so a fresh `starting` attempt
   made `process` `degraded` while `launches` was `ok`. Now `model.attempt_start_grace_expired`
   applies the same age check the stale-start branch uses (a start time that cannot be read counts
   as expired, matching `engine.reconcile_launches`), and both sources ask it. Tests:
   `test_a_claim_still_inside_the_start_grace_is_not_unhealthy` (process `ok`, launches `ok`, no
   failure), `test_a_stale_start_degrades_both_sources_about_the_same_attempt`, and in
   `test_provenance.py`: `test_process_does_not_flag_a_claim_inside_the_start_grace`,
   `test_process_flags_a_claim_whose_start_grace_ran_out`; the existing
   `test_process_flags_a_pending_launch_without_a_pid` still passes, so a launch that never recorded
   a pid is still flagged immediately.
2. **The failure headline was capped at 20 before counting.**
   `_process` no longer slices; `collect` counts the full record (`failures_total`) and displays at
   most `MAX_FAILURES`. `_launches` now describes the total and the shown subset. Test:
   `test_the_failure_headline_counts_every_record_not_only_the_listed_ones` seeds 25 failures and
   asserts 20 rows, `failures_total == 25`, `data["failures"] == 25`, `data["not_shown"] == 5`, the
   detail "25 failed … the most recent 20 are listed", and the rendered note
   "25 recorded; the most recent 20 are listed."
3. **The tick staleness fallback read a key the front door does not default.**
   Confirmed: `schedule.tick_interval` is absent from `loom:37 DEFAULTS`; `loom:470` uses a literal
   120. `_schedule` now resolves the interval from the plist, then `schedule.tick_interval`, then the
   installer default, and publishes `interval_source` on the schedule view/signal and the tick
   signal. Tests: `test_the_schedule_interval_records_where_it_was_read_from`,
   `test_an_interval_no_plist_supplies_is_reported_as_assumed`,
   `test_the_tick_interval_prefers_the_plist_then_the_config_then_the_default`,
   `test_a_stale_tick_says_when_its_interval_was_assumed`.
4. **Evidence accuracy.** The previous Verification section said `validate_work.py` failed on one
   error plus a UI-3/UI-4 write-path warning. The current run fails on two errors and the UI-4
   warning is a registry/program drift warning. Corrected below, and the live numbers were
   re-sampled on this branch rather than reused.

## Verification

All commands run from `capabilities/work` on this branch.

### UI suite — 92 tests, `OK`

```bash
python3 -m unittest discover -s ui/tests -t ui/tests
# Ran 92 tests in 14.2s — OK
```

`test_operations.py` (27) covers: parity with `loom tick --json` for capacity, limits, expired leases
and live attempts; a claim with no expiry; a failed launch with its reason; a stale `starting`
attempt; an in-grace start that must stay healthy; both sources agreeing on a stale start; the
failure headline outliving the 20-row display limit; a run whose process vanished; a stuck attempt
through the engine's own threshold; a healthy process source despite historical failures; tick
record fresh/stale/missing/malformed; the interval's plist/config/default provenance; schedule
loaded/not loaded; log tail escaping; every source's `as_of` and status; the release verdict;
`/api/operations` shape; no control surface; and a closed version gate returning `503` on both
operations routes. `test_provenance.py` gained the two grace tests above.

### Engine suite — 276 tests, `OK (skipped=2)`

```bash
python3 -m unittest discover -s tests
# Ran 276 tests in 87.2s — OK (skipped=2)
```

Only `capabilities/work/ui` was modified; this run confirms the engine, validator, MCP, A2A and
surface tests are unaffected.

### Live smoke test (real registry, `PINNED`)

Served this checkout on `127.0.0.1:8799`; the version gate verified that the loaded
`loom`/`engine.py`/`store.py` match the promoted release.

```text
GET /api/operations   200   read_only true   interval_source plist   failures_total 10
GET /operations       200   no <form>, <button>, <input>, <script>, or method=
HEAD /operations      200
POST/PUT/PATCH/DELETE 405   Allow: GET, HEAD
Content-Security-Policy: default-src 'none'; … form-action 'none'; frame-ancestors 'none'
```

The operations payload at `2026-11-16T04:38:59Z` (registry snapshot `04:38:59Z`) reported:

```text
capacity   limits implementer 4, reviewer 3, integrator 1
           active implementer 2, reviewer 0, integrator 0
           live 2 of 2 open attempts; live reviewers 0
leases     1 expired: jarvis/ARCH-09 (opencode/jarvis-agent)
database   ok         04:38:59Z  1 expired lease(s) across 222 task(s)
process    ok         04:38:59Z  2 live worker process(es) of 2 open attempt(s); capacity …
launches   degraded   04:38:59Z  10 failed, stale or lost launch(es); 0 stuck  (shown 10/10)
tick       ok         04:38:21Z  last recorded tick --apply pass 38s before the snapshot
scheduler  ok         04:38:59Z  com.hritvik.loom-tick every 120s from the plist; job loaded
release    ok         04:38:59Z  release 640eb8c82801 intact; matches the engine sources loaded
logs       ok         04:38:21Z  20 of the last 20 line(s); last written 04:38:21Z
```

`process` is `ok` while `launches` is `degraded` on the same sampling instant: ten historical
failures are a launch-record fact, not a claim that capacity is unhealthy.

Parity against the supported front door for the same `--now`, re-checked by comparing the two
payloads field by field:

```text
$ ./loom tick --json --now 2026-11-16T04:38:59Z
limits       implementer 4, reviewer 3, integrator 1      == UI capacity.limits
active       implementer 2, reviewer 0, integrator 0      == UI capacity.active
expired      jarvis/ARCH-09 opencode/jarvis-agent 11-10   == UI expired_leases
liveAttempts loom/LOOM-13 11718, loom-ui/UI-4 14331       == UI live attempts (pid for pid)

$ ./loom workers
loom/LOOM-13   implementer pid 11718  loom/LOOM-13#a1
loom-ui/UI-4   implementer pid 14331  loom-ui/UI-4#a3
```

`validate_work.py` on this branch reports 2 errors and 109 warnings, both errors outside this task's
write path: `midas/W-8: heading W-8 missing from midas.program` and
`loom/LOOM-13: heading LOOM-13 missing from loom.program`. No UI-4 error is introduced. The one
UI-4 warning is registry/program drift, not a path conflict:
`loom-ui/UI-4: registry says claimed, program says planned` (UI-3 carries the same warning). The
claim is live while this run is in progress, so the registry is the expected side of that drift.

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
- **The tick interval can be assumed.** With no readable plist and no configured
  `schedule.tick_interval`, the interval is the installer's 120s default and the view says so.
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

## Reproduce

```bash
cd capabilities/work
python3 -m unittest discover -s ui/tests -t ui/tests     # 92 tests
python3 ui/serve.py --port 8788                          # browse http://127.0.0.1:8788/operations
python3 -m unittest discover -s tests                    # 276 engine tests
./loom tick --json --now "$(date -u +%Y-%m-%dT%H:%M:%SZ)"  # compare the front door
```

## Review asks

1. Is the start grace now presented consistently? A claim inside the window is `ok` and past it is
   `degraded` in both `process` and `launches`, through one predicate in `model`.
2. Is three schedule intervals the right staleness threshold for the recorded tick pass, or should a
   missing/old record only ever be informational?
3. Should the tick section expose the preview (`loom tick --json`) as well, or is showing only the
   recorded apply pass the safer read-only contract?
4. Is the UI gate verdict inside the operations view confusing next to `loom deploy status`'s
   different STALE verdict, even with the label, or should release health link out to the CLI?
