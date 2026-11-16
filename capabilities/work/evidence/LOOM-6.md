# Evidence — LOOM-6 report health as an artifact

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-6-a1`
Status: green, submitted for independent review

## The problem

The dispatcher's only output was the registry, and a stopped dispatcher does not write to the
registry. So a dead scheduler, a schedule that was never installed, and a pass whose cursor stopped
reaching the log all left the same trace: none. The tick already wrote `tick.decisions.jsonl` per
applying pass, but nothing said *when* it last ran, nothing could be checked with an exit code, and
nothing distinguished "nothing to do" from "not running". The idle-looking-but-dead failure is what
started this work.

## Delivered

`health.json` is an artifact of the pass, written outside the registry, and `loom health` is a
question with an exit code.

- **Each tick pass rewrites `health.json`** (`~/.local/state/loom/health.json`, `LOOM_HEALTH`
  overrides it) before any output branch returns, so `--json`, `--quiet` and "nothing happened" all
  write the same record:
  - `ts` — the pass time;
  - `cursor` — the log position the last completed applying pass consumed;
  - `logHead` — the chain tip the pass observed, `{seq, hash}`;
  - `liveAttempts` — `{task, attempt, role, pid}` for each observed live run;
  - `lastError` — the last error, or `null`.
  Additive fields: `mode` (`apply`/`preview`) and `lastApply` (`{ts, cursor, logHead, complete}`).
- **The cursor advances only when an applying pass reaches its end.** A pass that dies leaves the
  cursor where it was and names the failure, so "running but wedged" is a different record from
  "not running".
- **A pass that dies still records itself.** `cmd_tick` is a wrapper around the pass body: any
  exception — `WorkError` or otherwise — records `complete: false` with the cursor unmoved and the
  error text, then re-raises. A crash therefore cannot leave the previous healthy artifact in place.
- **A preview is recorded but cannot masquerade as a dispatch.** A preview cannot claim, launch or
  consume, so `lastApply` — the dispatcher's clock — is untouched by `loom tick` without `--apply`.
  Running previews by hand cannot make a stopped schedule look alive.
- **`loom health [--interval N] [--now T] [--json]`** exits `0` only when an applying pass finished
  within three intervals and consumed the log, and names every reason it does not:
  - no artifact at all → `no pass has been recorded`;
  - previews but no applying pass → `no applying pass has ever run`;
  - older than `3 × interval` → `the last pass was Ns ago; stale after Ns (3 × Ns interval)`;
  - `cursor < logHead.seq` → `cursor N is behind log head M`;
  - `lastApply.complete == false` → `the last applying pass did not finish: <error>`.
  The interval comes from `schedule.tick_interval` in `loom.json` (default 120s), which is the same
  value `loom schedule install` writes into the launchd plist.

## Acceptance

| Criterion | Proof |
|---|---|
| Each pass rewrites `health.json` with the pass time, cursor, log head, live attempts and the last error | `test_an_applying_pass_records_its_time_cursor_and_head` asserts `ts`, `cursor`, `logHead.seq`, a 64-hex `logHead.hash`, `liveAttempts`, `lastError is None` and `lastApply`; `test_a_preview_records_itself_but_never_stands_in_for_a_dispatch` asserts a preview writes `mode: preview` with `lastApply` still `null` |
| `loom health` exits non-zero when the last pass is older than three intervals | `test_health_fails_only_when_the_last_pass_is_older_than_three_intervals`: at +180s exit 0, at +181s exit 1 with `stale after 180s` for `--interval 60`; `test_the_interval_defaults_to_the_configured_schedule`: a `loom.json` with `tick_interval: 300` gives `staleAfterSeconds: 900`, exit 0 at +840s, exit 1 at +901s |

Additional tests: `test_a_pass_that_dies_records_the_error_and_leaves_the_cursor_behind` (the pass
dies on the decisions write; the artifact records `IsADirectoryError`, `complete: false`, `cursor 0`
behind `log head 1`, and `loom health` reports both the lag and the failure) and
`test_a_later_pass_advances_the_cursor_and_clears_the_error` (a completed pass clears `lastError`,
advances the cursor to the head, and health returns to OK).

The goal's three dead states, each now distinguishable:

| State | What the artifact says | `loom health` |
|---|---|---|
| Stopped dispatcher | `lastApply.ts` ages | exit 1, `stale after Ns` |
| Stopped schedule (never installed, or only previews run) | `lastApply: null` while `ts` moves | exit 1, `no applying pass has ever run` |
| Wedged cursor | `lastApply.cursor < lastApply.logHead.seq` | exit 1, `cursor N is behind log head M` |
| Crashed pass | `lastApply.complete: false`, `lastError` set | exit 1, `did not finish: <error>` |
| Idle engine | fresh `ts`, cursor == head, no error | exit 0 |

## Verification

```bash
cd capabilities/work
python3 -m unittest discover -s tests
# Ran 282 tests in 86s — OK (skipped=2); 6 of them are new in tests/test_work.py
python3 validate_work.py
# FAIL (2 errors, 110 warnings) — see Limits: both errors are other tasks' board headings
```

A scratch work home, so the demonstration does not touch the live registry or the live artifact
(`init`, one ready task, one applying pass at `2031-01-01T00:00:00Z`, interval 120s):

```
$ cat health.json
{
  "cursor": 2,
  "lastApply": {
    "complete": true,
    "cursor": 2,
    "logHead": {
      "hash": "3c13e77bf50ff484f3eede31ef6bfc4ab5a1a47f0cc62605b1e707b2af7d604b",
      "seq": 2
    },
    "ts": "2031-01-01T00:00:00Z"
  },
  "lastError": null,
  "liveAttempts": [],
  "logHead": {
    "hash": "3c13e77bf50ff484f3eede31ef6bfc4ab5a1a47f0cc62605b1e707b2af7d604b",
    "seq": 2
  },
  "mode": "apply",
  "ts": "2031-01-01T00:00:00Z"
}
$ loom health --interval 120 --now 2031-01-01T00:02:00Z
health: OK
  artifact: <tmp>/health.json
  last pass: 2031-01-01T00:00:00Z (apply), 120s ago
  dispatch:  2031-01-01T00:00:00Z  cursor 2 / log head 2  3c13e77bf50f
  attempts:  0 live
  error:     none
exit 0
$ loom health --interval 120 --now 2031-01-01T00:06:01Z
health: UNHEALTHY
  artifact: <tmp>/health.json
  last pass: 2031-01-01T00:00:00Z (apply), 361s ago
  dispatch:  2031-01-01T00:00:00Z  cursor 2 / log head 2  3c13e77bf50f
  attempts:  0 live
  error:     none
  PROBLEM    the last pass was 361s ago; stale after 360s (3 x 120s interval)
exit 1
```

Same work home, with the decisions write pointed at a directory so the pass dies there:

```
$ LOOM_TICK_DECISIONS=<tmp>/broken loom tick --apply --quiet ...
IsADirectoryError: [Errno 21] Is a directory: '<tmp>/broken'
$ loom health --interval 120 --now 2031-01-01T00:04:30Z
health: UNHEALTHY
  last pass: 2031-01-01T00:04:00Z (apply), 30s ago
  dispatch:  2031-01-01T00:04:00Z  cursor 2 / log head 2  3c13e77bf50f
  error:     IsADirectoryError: [Errno 21] Is a directory: '<tmp>/broken'
  PROBLEM    the last applying pass did not finish: IsADirectoryError: [Errno 21] ...
exit 1
```

Note the last case: the log had not grown, so `cursor == logHead.seq` and staleness was 30s. Without
`complete` the artifact would have called a crashing dispatcher healthy — that is why the verdict
also reads `lastApply.complete`. It was a gap found while building the demonstration, not by a test.

## Files

- `capabilities/work/engine.py` — `health_path`, `log_head`, `read_health`, `record_pass`,
  `_record_pass_safely`; `cmd_tick` split into the recording wrapper and `tick_pass`, with the
  record written before the output branches.
- `capabilities/work/loom` — `cmd_health`, the `health` front-door command, `tick_interval` in
  `DEFAULTS["schedule"]`, usage text, and `global_value` so `--now` (a global flag that
  `split_globals` strips) still reaches the verdict.
- `capabilities/work/tests/test_work.py` — six tests and `LOOM_HEALTH` isolation in the fixture
  (without it every existing tick test would write the developer's real artifact).

## Review lenses to apply

- **Crash and recovery.** The wrapper records `complete: false` for any exception, including one
  raised before the pass mutated anything. `_record_pass_safely` swallows its own write failure with
  a warning so the artifact can never mask the pass's error. Is the `Exception` catch the right
  boundary, and is `lastError` cleared by exactly the right event?
- **Two clocks.** `ts` (any pass) versus `lastApply.ts` (applying passes). The verdict uses
  `lastApply`; the display shows both. Check that a preview can never make the dispatcher look alive
  and that a *stale* preview after a fresh dispatch does not mislead.
- **Cursor semantics.** The cursor is "what the last completed applying pass consumed", not a
  durable read position the engine gates work on (that is LOOM-10). Check the definition holds for
  a failed pass, a preview, and a pass that both reads and appends.
- **Artifact placement.** `health.json` is outside the repository and outside the database, next to
  `tick.decisions.jsonl` and `engine.release`. It is written under the store lock, atomically via a
  temp file and `os.replace`.

## Limits

- **The validator's two errors are not this change's.** `python3 validate_work.py` against the live
  registry reports `midas/W-8: heading W-8 missing from midas.program` and `loom/LOOM-13: heading
  LOOM-13 missing from loom.program` (LOOM-13 is in review in another worktree as this was
  written). The diff touches no board, packet or heading; it is `engine.py`, `loom` and `tests/`.
  All 110 warnings are the same pre-existing board drift.
- **The interval is read from `loom.json`, not from launchd.** `loom schedule install` derives the
  plist from the same config, so they agree when installed that way; a hand-edited plist would make
  `health`'s three-interval threshold disagree with the real schedule.
- **The cursor is artifact state, not store state.** It is reported, not enforced. When LOOM-10 puts
  an intent "with the cursor", the durable position belongs in the store and this artifact should
  read it rather than own it.
- **A failing pass resets its own clock.** `lastApply.ts` is updated by a failed pass as well, so a
  dispatcher that fails every pass reports `did not finish` rather than going stale. That is the
  intended reading (it is running and failing), but it means staleness alone no longer proves a
  dispatcher is dead once a pass has failed — `complete` must be read too.
- **`README.md` and `AGENT-GUIDE.md` do not list `loom health`.** Both are outside this task's write
  paths. The command is in `loom --help` usage text.
- **Nothing was promoted.** `loom deploy promote` was not run; the release engine keeps writing no
  artifact until this is integrated and promoted, and `loom health` will say `no pass has been
  recorded` until the first applying pass under the new engine.
