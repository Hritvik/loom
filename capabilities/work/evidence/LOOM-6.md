# Evidence — LOOM-6 report health as an artifact

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-6-a3`
Status: green after rework; two review findings fixed (see *Review round 1*)

## The problem

The dispatcher's only output was the registry, and a stopped dispatcher does not write to the
registry. So a dead scheduler, a schedule that was never installed, and a pass that ran and died
holding events it had not consumed all left the same trace: none. The tick already wrote
`tick.decisions.jsonl` per applying pass, but nothing said *when* it last ran, nothing could be
checked with an exit code, and nothing distinguished "nothing to do" from "not running". The
idle-looking-but-dead failure is what started this work.

## Delivered

`health.json` is an artifact of the pass, written outside the registry, and `loom health` is a
question with an exit code.

- **Each tick pass rewrites `health.json`** (`~/.local/state/loom/health.json`, `LOOM_HEALTH`
  overrides it) before any output branch returns, so `--json`, `--quiet` and "nothing happened" all
  write the same record:
  - `ts` — the pass time;
  - `mode` — `apply` or `preview`;
  - `readFrom` — the chain tip the pass found *before* it did any work;
  - `cursor` — the log position the last completed applying pass consumed;
  - `logHead` — the chain tip the pass left behind, `{seq, hash}`;
  - `liveAttempts` — `{task, attempt, role, pid}` for each observed live run;
  - `lastError` — the last error, or `null`;
  - `lastApply` — `{ts, readFrom, cursor, logHead, complete}` for the last *applying* pass.
- **The cursor advances only when an applying pass reaches its end.** A pass that dies leaves the
  cursor where it was, names the failure, and reports the live attempts it left running.
- **`readFrom` is what makes an unfinished pass legible.** `logHead − max(cursor, readFrom)` counts
  the events that arrived *during* the pass and were consumed by nobody. Without `readFrom` the lag
  had to be measured from the previous pass's cursor, which blames a pass for events appended while
  no pass was running — idle, not wedged.
- **A pass that dies still records itself.** `cmd_tick` is a wrapper around the pass body: any
  exception — `WorkError` or otherwise — records `complete: false` with the cursor unmoved, the
  error text and the live attempts re-read from the log, then re-raises the original exception.
- **A preview is recorded but cannot masquerade as a dispatch.** A preview cannot claim, launch or
  consume, so `lastApply` — the dispatcher's clock — is untouched by `loom tick` without `--apply`.
- **`loom health [--interval N] [--now T] [--json]`** exits `0` only when an applying pass finished
  within three intervals and left no event behind, and names every reason it does not:
  - no artifact at all → `no pass has been recorded`;
  - previews but no applying pass → `no applying pass has ever run`;
  - older than `3 × interval` → `the last applying pass was Ns ago; stale after Ns (3 × Ns interval)`;
  - stamped in the future → `the last applying pass is stamped Ns in the future`;
  - `logHead.seq > max(cursor, readFrom)` → `N event(s) were never consumed by the last applying pass: it read from A, its cursor is C, the log ends at H`;
  - `lastApply.complete == false` → `the last applying pass did not finish: <error>`.
  The interval comes from `schedule.tick_interval` in `loom.json` (default 120s), which is the same
  value `loom schedule install` writes into the launchd plist.

The display prints one line per clock, and the age only beside the pass it belongs to:

```
  last pass: <ts> (<mode>)                  # any pass, no age
  dispatch:  <lastApply.ts>, <age>  read from R -> cursor C / log head H  <hash12>
```

## Review round 1 — changes requested, both fixed

The reviewer reproduced two defects in the human-readable verdict (`answer` to `ask` 2382 on
revision 2, `d2684ea`). Neither changed the exit-code contract.

**F1 (blocking) — the age was computed from one clock and printed beside another.** An applying pass
at 10:00:00Z followed by a hand-run preview at 10:29:55Z printed
`last pass: 10:29:55Z (preview), 1800s ago` and `the last pass was 1800s ago`, while the pass it
named had run 5s earlier. The sentence contradicted the timestamp beside it, and a future stamp was
printed as a negative "ago".

*Fixed.* The `last pass` line is a mode line and carries no age; the `dispatch:` line carries the
applying pass's own age, computed from `lastApply.ts`. The staleness problem now says "the last
applying pass was Ns ago". A future stamp is a problem in its own right instead of a negative age.
Proof: `test_a_preview_does_not_lend_its_clock_to_the_dispatch_line` (asserts the mode line is
unstamped, the dispatch line carries `1800s ago`, and `ageSeconds == 1800`) and
`test_a_future_stamp_is_not_reported_as_healthy`.

**F2 (blocking) — "cursor N is behind log head M" fired only as a side effect of a crash, and
blamed the wrong pass.** `record_pass` sets `cursor := head` at the end of a completed applying
pass, so for a completed pass the condition cannot fire; for a crashed one it compared the
*previous* pass's cursor against the current head, which counts events appended while no pass was
running and attributes them to the pass that died.

*Fixed.* Each pass records `readFrom`, the chain tip observed before it does any work; the verdict
counts `logHead − max(cursor, readFrom)`, i.e. events that arrived during the pass. The comment at
the top of the Health section, `record_pass`'s docstring, `cmd_health`'s docstring and the dead-state
table below no longer claim an independent "wedged cursor" state; the third state is a pass that
died holding events it never consumed. An artifact written before `readFrom` existed is not accused
of lagging by the height of the whole log — it falls back to the cursor and shows `read from ?`
(`test_an_artifact_written_before_readFrom_existed_is_not_accused`).

Proof: `test_a_crash_does_not_blame_the_pass_for_events_appended_before_it_ran` reproduces the
reviewer's case — a completed pass at head 1, an idle `loom create` to head 2, then a pass that
reads from 2 and dies: no "never consumed" is printed. `test_events_appended_during_a_crashing_pass_are_reported_as_unconsumed`
covers the honest direction: a pass that claims and launches (three events of its own) and then dies
reports exactly those three, and `liveAttempts` lists the worker it left behind.

## Acceptance

| Criterion | Proof |
|---|---|
| Each pass rewrites `health.json` with the pass time, cursor, log head, live attempts and the last error | `test_an_applying_pass_records_its_time_cursor_and_head` asserts `ts`, `readFrom`, `cursor`, `logHead.seq`, a 64-hex `logHead.hash`, `liveAttempts`, `lastError is None` and `lastApply`; `test_a_preview_records_itself_but_never_stands_in_for_a_dispatch` asserts a preview writes `mode: preview` with `lastApply` still `null` |
| `loom health` exits non-zero when the last pass is older than three intervals | `test_health_fails_only_when_the_last_pass_is_older_than_three_intervals`: at +180s exit 0, at +181s exit 1 with `stale after 180s` for `--interval 60`; `test_the_interval_defaults_to_the_configured_schedule`: a `loom.json` with `tick_interval: 300` gives `staleAfterSeconds: 900`, exit 0 at +840s, exit 1 at +901s |

Additional tests: `test_a_pass_that_dies_records_the_error_and_leaves_the_cursor_behind`,
`test_a_later_pass_advances_the_cursor_and_clears_the_error`, and the five new tests named above.

The dead states, each distinguishable from an idle engine:

| State | What the artifact says | `loom health` |
|---|---|---|
| Stopped dispatcher | `lastApply.ts` ages, however fresh `ts` is | exit 1, `the last applying pass was Ns ago` |
| Stopped schedule (never installed, or only previews run) | `lastApply: null` while `ts` moves | exit 1, `no applying pass has ever run` |
| Crashed pass | `lastApply.complete: false`, `lastError` set, `liveAttempts` shows what it left running | exit 1, `did not finish: <error>` |
| Crashed pass that appended events | `logHead.seq > readFrom` on the same record | exit 1, `N event(s) were never consumed by the last applying pass` |
| Clock wrong | `lastApply.ts` in the future | exit 1, `stamped Ns in the future` |
| Idle engine | fresh `lastApply.ts`, `cursor == logHead`, nothing pending, no error | exit 0 |

## Verification

```bash
cd capabilities/work
python3 -m unittest discover -s tests
# Ran 287 tests in 88s — OK (skipped=2); 11 of them are the health tests in tests/test_work.py
python3 validate_work.py
# FAIL (2 errors, 110 warnings) — see Limits: both errors are other tasks' board headings
```

A scratch work home, so the demonstration does not touch the live registry or the live artifact
(`loom init`, one ready task, one applying pass at 2031-01-01T00:00:00Z, interval 120s):

```
$ loom health --interval 120 --now 2031-01-01T00:02:00Z
health: OK
  artifact: /tmp/loom6demo.wnuNu5/health.json
  last pass: 2031-01-01T00:00:00Z (apply)
  dispatch:  2031-01-01T00:00:00Z, 120s ago  read from 2 -> cursor 2 / log head 2  f5e772eb1a3d
  attempts:  0 live
  error:     none
exit 0
```

F1, the reviewer's reproduction: the applying pass is 1800s old and a preview ran 5s ago. The mode
line names the preview with no age; the age sits on the line it belongs to.

```
$ loom tick --now 2031-01-01T00:29:55Z            # preview, appends nothing
$ loom health --interval 120 --now 2031-01-01T00:30:00Z
health: UNHEALTHY
  artifact: /tmp/loom6demo.wnuNu5/health.json
  last pass: 2031-01-01T00:29:55Z (preview)
  dispatch:  2031-01-01T00:00:00Z, 1800s ago  read from 2 -> cursor 2 / log head 2  f5e772eb1a3d
  attempts:  0 live
  error:     none
  PROBLEM    the last applying pass was 1800s ago; stale after 360s (3 x 120s interval)
exit 1
```

F2, the reviewer's reproduction: an idle `loom create` moved the head to 3 before the pass started,
and the pass died without appending anything. It is reported as a crash, not as a pass that failed
to consume an event it never saw.

```
$ loom create ... demo/W-2 ...                    # head 2 -> 3, no pass running
$ LOOM_TICK_DECISIONS=<tmp>/broken loom tick --apply --max-implementer 0 --max-reviewer 0 ...
IsADirectoryError: [Errno 21] Is a directory: '<tmp>/broken'
$ loom health --interval 120 --now 2031-01-01T00:30:10Z
health: UNHEALTHY
  last pass: 2031-01-01T00:30:10Z (apply)
  dispatch:  2031-01-01T00:30:10Z, 0s ago  read from 3 -> cursor 2 / log head 3  58cf97a58bac
  attempts:  0 live
  error:     IsADirectoryError: [Errno 21] Is a directory: '<tmp>/broken'
  PROBLEM    the last applying pass did not finish: IsADirectoryError: [Errno 21] ...
exit 1
```

The same pass shape, but this one claims and launches first: the three events it appended are the
ones it left unconsumed, and the worker it started is still listed.

```
$ LOOM_TICK_DECISIONS=<tmp>/broken loom tick --apply --max-implementer 1 \
    --worker-command "/bin/sh -c 'sleep 5'" ...
IsADirectoryError: [Errno 21] Is a directory: '<tmp>/broken'
$ loom health --interval 120 --now 2031-01-01T00:31:00Z
health: UNHEALTHY
  last pass: 2031-01-01T00:31:00Z (apply)
  dispatch:  2031-01-01T00:31:00Z, 0s ago  read from 3 -> cursor 2 / log head 6  588f02cdb355
  attempts:  1 live (demo/W-1#a1)
  error:     IsADirectoryError: [Errno 21] Is a directory: '<tmp>/broken'
  PROBLEM    3 event(s) were never consumed by the last applying pass: it read from 3, its cursor is 2, the log ends at 6
  PROBLEM    the last applying pass did not finish: IsADirectoryError: [Errno 21] ...
exit 1
```

`health.json` after the first applying pass, showing every field the acceptance criterion names:

```json
{
  "cursor": 2,
  "lastApply": {
    "complete": true,
    "cursor": 2,
    "logHead": {"hash": "f5e772eb1a3d5c2cdc0422f31eb5d8b176a6453b610171354ea51bc21ec88fba", "seq": 2},
    "readFrom": 2,
    "ts": "2031-01-01T00:00:00Z"
  },
  "lastError": null,
  "liveAttempts": [],
  "logHead": {"hash": "f5e772eb1a3d5c2cdc0422f31eb5d8b176a6453b610171354ea51bc21ec88fba", "seq": 2},
  "mode": "apply",
  "readFrom": 2,
  "ts": "2031-01-01T00:00:00Z"
}
```

## Files

- `capabilities/work/engine.py` — `health_path`, `log_head`, `read_health`, `health_attempts`,
  `record_pass` (with `readFrom`), `_record_pass_safely`, `_observed_attempts`; `cmd_tick` split
  into the recording wrapper and `tick_pass(args, store, read_from=...)`, with the record written
  before the output branches.
- `capabilities/work/loom` — `cmd_health` (one line per clock, `readFrom` based verdict), the
  `health` front-door command, `tick_interval` in `DEFAULTS["schedule"]`, usage text, and
  `global_value` so `--now` (a global flag that `split_globals` strips) still reaches the verdict.
- `capabilities/work/tests/test_work.py` — eleven health tests and `LOOM_HEALTH` isolation in the
  fixture (without it every existing tick test would write the developer's real artifact).

## Review lenses to apply

- **Crash and recovery.** The wrapper records `complete: false` for any exception, re-reads live
  attempts from the log, and re-raises. `_record_pass_safely` swallows its own write failure with a
  warning so the artifact can never mask the pass's error. Is the `Exception` catch the right
  boundary, and is `lastError` cleared by exactly the right event?
- **Two clocks.** `ts` (any pass) versus `lastApply.ts` (applying passes). The verdict uses only
  `lastApply`; the display shows both and ages only the applying one. Check that a preview can
  never make the dispatcher look alive, and that a fresh preview after a stale dispatch reads as
  stale rather than as healthy (F1).
- **Cursor semantics.** `cursor` is "what the last completed applying pass consumed", not a durable
  read position the engine gates work on (that is LOOM-10). `readFrom` is "where this pass found the
  log". Check the pair holds for a failed pass, a preview, and a pass that both reads and appends
  (F2).
- **Artifact placement.** `health.json` is outside the repository and outside the database, next to
  `tick.decisions.jsonl` and `engine.release`. It is written under the store lock, atomically via a
  temp file and `os.replace`.
- **Upgrade path.** The artifact outlives the engine that wrote it. A record with no `readFrom`
  must not be judged by the new rule.

## Limits

- **The validator's two errors are not this change's.** `python3 validate_work.py` against the live
  registry reports `midas/W-8: heading W-8 missing from midas.program` and `loom/LOOM-13: heading
  LOOM-13 missing from loom.program` (LOOM-13 is in review in another worktree). The diff touches no
  board, packet or heading; it is `engine.py`, `loom`, `tests/` and this file. All 110 warnings are
  the same pre-existing board drift.
- **The interval is read from `loom.json`, not from launchd.** `loom schedule install` derives the
  plist from the same config, so they agree when installed that way; a hand-edited plist would make
  `health`'s three-interval threshold disagree with the real schedule.
- **The cursor is artifact state, not store state.** It is reported, not enforced. When LOOM-10 puts
  an intent "with the cursor", the durable position belongs in the store and this artifact should
  read it rather than own it. `readFrom` has the same status: it is the position this pass *found*,
  taken outside the store lock, so a writer racing the pass's start could be counted on either side
  of it. The error is one pass wide and self-correcting on the next.
- **A failing pass resets its own clock.** `lastApply.ts` is updated by a failed pass as well, so a
  dispatcher that fails every pass reports `did not finish` rather than going stale. That is the
  intended reading (it is running and failing), but it means staleness alone no longer proves a
  dispatcher is dead once a pass has failed — `complete` must be read too.
- **`liveAttempts` on a crash is observed after the fact.** The wrapper re-reads the log rather than
  asking the pass what it had counted, so an attempt whose process exited between the launch and the
  crash is correctly absent. That is the same liveness rule the rest of the engine uses.
- **`README.md` and `AGENT-GUIDE.md` do not list `loom health`.** Both are outside this task's write
  paths. The command is in `loom --help` usage text.
- **Nothing was promoted.** `loom deploy promote` was not run; the release engine keeps writing no
  artifact until this is integrated and promoted, and `loom health` will say `no pass has been
  recorded` until the first applying pass under the new engine.
