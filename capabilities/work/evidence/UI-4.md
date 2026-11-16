# UI-4 — Add read-only operations visibility

Task: `loom-ui/UI-4` · branch `loom/loom-ui-ui-4-a1` · write path `capabilities/work/ui`
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
| `ui/loom_ui/templates.py` | the operations page plus a nav link from the control room; all values escaped, still no script |
| `ui/loom_ui/server.py` | `GET /operations` and `GET /api/operations`; same loopback, read-only and fail-closed gates |
| `ui/tests/test_operations.py` | 20 tests: front-door parity, failure kinds, freshness, schedule/log, no-control HTTP surface |
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
5. **The schedule is the plist *and* launchd.** `loom schedule status` reads the plist, the loaded
   job and the log. This view reads the same three: the plist (label, interval, arguments), the
   `launchctl list` row, and a bounded tail of `~/.local/state/loom/tick.log`. A plist that exists
   but is not loaded is `degraded`.
6. **Release health is labelled as the UI gate verdict.** The `release` source is sampled through
   the same `provenance.release` function as the foundation: `PINNED`/`UNPINNED`/closed verdict,
   release commit, tree digest verification and running root. `loom deploy status` additionally
   compares development `HEAD` with the release; that is a different question and stays a CLI
   operation, so the field is named `UI gate verdict` rather than borrowing `deploy status`'s word.
7. **There is no control to forget to guard.** The page has no form, button, input or script; only
   `GET`/`HEAD` are routed. Every other method on `/operations` is `405` with `Allow: GET, HEAD`.
   No apply, dispatch, stop/start, promote, rollback or reanchor endpoint exists. Tests assert the
   absence of a control surface, not just the absence of a known route.
8. **The log is read, not slurped.** `_tail` seeks to the last 64 KiB and returns at most 20 lines,
   because the scheduler appends to `tick.log` without rotation. Malformed bytes are replaced
   rather than failing the whole page.

## Verification

All commands run from `capabilities/work` on this branch.

### UI suite — 83 tests, `OK`

```bash
python3 -m unittest discover -s ui/tests -t ui/tests
# Ran 83 tests in 13.9s — OK
```

New coverage in `test_operations.py` (20): parity with `loom tick --json` for capacity, limits,
expired leases and live attempts; a claim with no expiry; a failed launch with its reason; a stale
`starting` attempt; a run whose process vanished; a stuck attempt through the engine's own
threshold; a healthy process source despite historical failures; tick record fresh/stale/missing/
malformed; schedule loaded/not loaded; log tail escaping; every source's `as_of` and status; the
release verdict; `/api/operations` shape; no control surface; and a closed version gate returning
`503` on both operations routes.

### Engine suite — 276 tests, `OK (skipped=2)`

```bash
python3 -m unittest discover -s tests
# Ran 276 tests in 87.3s — OK (skipped=2)
```

Only `capabilities/work/ui` was modified; this run confirms the engine, validator, MCP, A2A and
surface tests are unaffected.

### Live smoke test (real registry, `PINNED`)

Started from the reviewed dev tree while the release is installed; the version gate verified that
the loaded `loom`/`engine.py`/`store.py` match the promoted release.

```text
GET /api/operations   200
GET /operations       200
POST /operations      405  Allow: GET, HEAD
GET /                  200
```

The operations payload at `2026-11-16T04:16:45Z` (registry snapshot `04:16:45Z`) reported:

```text
capacity   limits implementer 4, reviewer 3, integrator 1
           active implementer 2, reviewer 0, integrator 0
           live 2 of 2 open attempts; live reviewers 0
leases     1 expired: jarvis/ARCH-09 (opencode/jarvis-agent)
database   ok         04:16:45Z  1 expired lease(s) across 222 task(s)
process    ok         04:16:45Z  2 live worker process(es) of 2 open attempt(s); capacity …
launches   degraded   04:16:45Z  10 failed, stale or lost launch(es); 0 stuck
tick       ok         04:16:15Z  last recorded tick --apply pass 30s before the snapshot
scheduler  ok         04:16:45Z  com.hritvik.loom-tick every 120s; launchd job loaded
release    ok         04:16:45Z  release 640eb8c82801 intact; matches the engine sources loaded
logs       ok         04:16:15Z  20 of the last 20 line(s); last written 04:16:15Z
```

Parity against the supported front door for the same `--now`:

```text
$ ./loom tick --json --now 2026-11-16T04:13:42Z
active       implementer 2, reviewer 0, integrator 0      == UI capacity.active
limits       implementer 4, reviewer 3, integrator 1      == UI capacity.limits
expired      jarvis/ARCH-09 opencode/jarvis-agent 11-10   == UI expired_leases
liveAttempts loom/LOOM-12 68511, loom-ui/UI-4 70452      == UI live attempts

$ ./loom workers
loom/LOOM-12 ... pid 68511 ... loom/LOOM-12#a3
loom-ui/UI-4 ... pid 70452 ... loom-ui/UI-4#a1
```

`./loom schedule status` for the same sample reported label `com.hritvik.loom-tick`, plist present,
interval 120s, the launchd row and a tick-log tail; the operations view reports the same values from
its own sample.

`validate_work.py` still fails on one pre-existing error outside this task —
`midas/W-8: heading W-8 missing from midas.program` — plus the expected `UI-3`/`UI-4` shared
write-path warning. No UI-4 validation error was introduced.

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
- **Process liveness is a point-in-time probe.** A worker that exits between the pid probe and the
  render is shown live until the next request. That is the same probe `loom workers` uses.
- **Historical failures keep `launches` degraded.** The list is bounded at 20 records but is not
  time-windowed; old failed attempts remain visible until the audit record is understood. This is
  deliberate (failures are facts), but an operator may want a recency filter later.
- **Release health is the UI gate verdict, not `deploy status`.** The two answer different
  questions; the field is labelled accordingly and no promotion or rollback control is exposed.
- **No actions.** Claim/renew/release/ask/answer and transitions remain UI-5, and dispatch/`tick
  --apply` remain CLI-only. This slice can only show state.

## Reproduce

```bash
cd capabilities/work
python3 -m unittest discover -s ui/tests -t ui/tests     # 83 tests
python3 ui/serve.py --port 8788                          # browse http://127.0.0.1:8788/operations
python3 -m unittest discover -s tests                    # 276 engine tests
./loom tick --json --now "$(date -u +%Y-%m-%dT%H:%M:%SZ)"  # compare the front door
```

## Review asks

1. Is grouping failed/stale/lost launches into a separate `launches` source — rather than folding
   them into `process` — the right presentation of health, given the engine treats capacity as live
   attempts only?
2. Is three schedule intervals the right staleness threshold for the recorded tick pass, or should a
   missing/old record only ever be informational?
3. Should the tick section expose the preview (`loom tick --json`) as well, or is showing only the
   recorded apply pass the safer read-only contract?
4. Is the UI gate verdict inside the operations view confusing next to `loom deploy status`'s
   different STALE verdict, even with the label, or should release health link out to the CLI?
