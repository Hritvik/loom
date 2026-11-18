# Evidence — LOOM-9 run the review and receipt phases as event handlers

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-9-a1` (worktree `~/.local/state/loom/worktrees/loom-loom-9-a1`)
Status: green, submitted for independent review

## The problem

`tick_pass` was a fixed sequence of phases: reconcile, receipt, review request, rework, integration,
selection, launch. Each phase re-read the registry, because the phase before it may have moved one,
and three of them could act twice — `sweep_approved_reviews` would transition a task it had already
transited if the status had not yet moved, `review_request_sweep` would re-issue an ask that was
already current, and `changes_requested_tasks` recomputed its set from the log every pass. The order
was load-bearing: run receipt after the review sweep and the sweep sees a green task whose request is
already answered and reports it `resubmit_required`. This is the order that LOOM-10 will have to keep
tracking by hand when it adds an intent record, so it has to stop mattering first.

## Delivered

**Three handlers, one event kind each.** `DERIVE_KINDS = ("receipt", "review", "rework")` in
`engine.py`, each a `DeriveHandler` with the same shape:

| Handler | Owns | Derives | Applies through |
|---|---|---|---|
| `ReceiptHandler` | an `answer` that approves | `reviewed` at this revision | `cmd_transition` → `verified_review` |
| `ReviewHandler` | an `ask` that went stale | a re-issued `ask` at the same revision and source | `cmd_ask` |
| `ReworkHandler` | an `answer` that requests changes | a `rework` flag carrying request and answer | `append_event(event="rework")` |

**Each derivation is a single pass over the log.** `event_index` builds one index of asks, answers,
rework flags and per-task history; every handler derives from that, not by walking the registry and
re-reading task history per task. The tick's phases still do it the old way. That is what makes the
shadow comparison a comparison of two implementations rather than a function compared with itself.

**Shadow first, and the shadow gates the write.** `derive_shadow` derives the handler projection and
the tick projection from the same log and compares them on the fields the handler is allowed to
decide (`INTENT_KEYS`). `tick_pass` runs that comparison at the start of every pass. When it agrees —
`handlers_live` — the pass writes through the handlers. When it disagrees, the pass reports
`derive: the handler projection disagrees with the tick (...)` as a problem, writes through the phases
it has always used, and does not run the handlers at all: a pass that writes through a derivation
nobody compared is the failure the comparison exists to catch. `foldcheck --derive` runs the same
check read-only and exits 1 on divergence, with `DIVERGED <kind> <task>: …` naming the direction.

**`--derive` needs no fold baseline.** It compares two readings of the same log, not the log against
the registry, so it works on a store that has never been snapshotted — the state every store is in
before `loom snapshot` runs.

**A handler never moves authority.** No handler releases a claim, takes one over, takes a lease or
spawns a process. `AUTHORITY_KEYS` names what that would look like (`action`, `release`, `takeover`,
`ttl`, `claim`, `lease_expires`) and `assert_no_authority` raises when any intent carries one, in
`derive_projection` and again in `DeriveHandler.apply`. Clearing a changes-requested task's claim is
still `apply_rework`, which the tick runs as a decision with a lease and a reason.

**`tick --json` is now parseable when a receipt is recorded.** The handlers delegate to `cmd_ask`
and `cmd_transition` so there is one implementation of each write, and those commands print. The
handler applies them under a `_quiet()` redirect, so the pass reports the receipt once instead of
twice and does not print prose in front of the JSON.

## Acceptance

| Criterion | Proof |
|---|---|
| In shadow mode the handler projection equals the tick projection for consecutive passes | `test_the_shadow_projection_equals_the_tick_projection_for_consecutive_passes`: one task per handler (`demo/W-5` approved, `demo/W-6` request addressed to a known agent that is not the configured reviewer, `demo/W-7` changes requested). `foldcheck --derive` prints `derive: the handler projection equals the tick projection (3 intent(s))`; two consecutive JSON passes both report `equal: true`, `divergences: []`, and derive the *same* projection, so a shadow pass cannot drift between passes. The comparison is not vacuous: `test_the_comparison_can_fail_in_either_direction` proves it names both directions, `test_a_divergent_projection_blocks_the_handlers_and_names_itself` replaces `ReceiptHandler.derive` with one that derives nothing and shows the applying pass reporting `receipt demo/W-5` and falling back to the phase it has always used, and `test_a_divergent_projection_is_reported_by_the_check_with_a_failing_exit` shows `derive_check` exiting 1 with `DIVERGED rework demo/W-7: the tick derives it, the handler does not`. |
| The receipt handler calls the verified review path and does nothing when validation fails | `test_the_receipt_handler_writes_through_the_verified_review_path`: the `reviewed` transition carries a `review_digest` and `review_answer`, and the task's `review.digest` equals the transition's. The digest is `verified_review`'s own receipt (it is computed from the path/blob pairs of the approved commit, which `approved_content` reads from Git), so its presence is proof the verified path ran rather than a transition being written directly. `test_the_receipt_handler_writes_nothing_when_the_verified_path_refuses` hand-appends an `answer` that approves but names an agent the request was never addressed to — the writer the verification exists for — and asserts the task stays `green`, has no `review` receipt, that the total event count is unchanged (the refusal wrote nothing at all), and that the refusal is reported with the verified path's own message, `approval must match the reviewer…`. |
| The rework handler sets a flag only; it never releases or takes over a claim | `test_the_rework_handler_flags_and_never_touches_the_claim`: after the handler applies, the task's whole `claim` object is byte-for-byte what it was, the status is still `green`, the revision is unchanged, there is no `release` or `takeover` event, and exactly one `rework` event carries the request, the answer and the observed owner. `test_an_intent_that_carries_authority_is_refused` shows every derived intent naming no authority key and `assert_no_authority` raising on `{"kind": "rework", …, "action": "release"}` before a handler can act on it. `test_the_rework_flag_is_invisible_to_the_event_validator` shows the validator's view of the registry and the log contract is otherwise unchanged by the flag. |

Supporting: `test_a_second_run_of_the_handlers_changes_nothing` (a second full pass appends no event
and leaves `canonical_registry` identical — "survives being run twice"),
`test_the_handlers_reach_the_same_state_in_any_order` (the same fixture applied as
`receipt, review, rework` and as `rework, review, receipt` on a `VACUUM INTO` copy reaches the same
canonical registry and the same multiset of events — the order is no longer load-bearing),
`test_an_applying_tick_runs_the_phases_as_handlers` (an applying tick reports `deriveShadow.equal`,
records the receipt, re-issues the stale request and flags the rework: the handlers are the code that
wrote in a real pass, not a library nobody calls),
`test_an_applying_tick_does_not_flag_a_rework_twice`.

## Verification

```bash
cd capabilities/work
python3 -m unittest discover -s tests          # 341 tests, OK (2 skipped)
python3 -m unittest discover -s tests -p 'test_derive.py'   # 13 tests, OK
./loom foldcheck --help                        # --derive, --reviewer-identity, --human, --known-agent
./loom foldcheck --derive --reviewer-identity opencode/reviewer-agent
```

The live registry, read twice:

```text
derive receipt: 0 nothing to do
derive review: 0 nothing to do
derive rework: 0 nothing to do
derive: the handler projection equals the tick projection (0 intent(s))
```

That run is honest and thin: the live registry currently holds no `green` task (65 integrated, 45
reviewed, 29 accepted, 26 ready, 3 claimed, 1 red, 0 green), so there is nothing for the receipt or
rework handlers to derive and the review handler has no stale request. It does prove the derivation
reads the real log — 2,450 events of production history — without disagreeing or raising. The
evidence that the three handlers agree with the phases is the fixture: it is the only shape that puts
all three intents in one log.

`validate_work.py` is unchanged by this branch and reports what it reported before it: two errors
that predate this task (`midas/W-8` and `loom/LOOM-14`: packet headings missing from a program map),
100 warnings (no fold baseline, pre-migration integrations, incomplete receipts). The fixture's own
validator output is identical before and after a rework flag, except the event count.

## Design decisions

- **A handler owns an event, not a phase.** The phase shape ("sweep every green task and see what is
  true") is what makes order matter. An event shape ("this answer implies this transition") is keyed
  by the event, so a second run finds its own key and does nothing.
- **The rework flag is an event, not a task field.** `schemas/task.schema.json` is
  `additionalProperties: false` and the schema is outside this task's write paths, so a new task field
  would either break the validator or need a contract change I am not entitled to make. An event is
  stronger for the purpose anyway: it names the request and the answer it came from, so "flagged once
  per answer" is a lookup rather than a convention, and `foldcheck` sees no state delta because a flag
  changes no state.
- **The projection excludes the claim action on purpose.** `changes_requested_tasks` also returns
  `owner` and `action` (`release`/`takeover`/`hold`). Those are authority, and acceptance forbids the
  handler from owning them, so `INTENT_KEYS` compares only what a handler may decide. This is
  disclosed rather than hidden: the comparison ignores what both sides agree the handlers must not
  touch, and `assert_no_authority` enforces that the handler never acquires it.
- **Divergence falls back rather than stops.** A disagreement is a bug in one of two derivations and
  the pass cannot know which. Refusing to write would stall every review until someone noticed; using
  the reviewed path and reporting the disagreement keeps the engine working and puts the finding in
  the pass's own record.
- **Comparison at the start of the pass, not after reconcile.** `reconcile_launches` runs first
  because it closes attempts and requeues; the shadow then reads the log as the pass will see it.

## Limits

- **The receipt and review phases now run through the handlers; the rework *claim* decision does
  not.** `apply_rework` still releases or takes over a claim for a changes-requested task, unchanged,
  by design (acceptance 3 constrains the handler). If the intent is that the engine stop yanking
  claims and leave a changes-requested task with its implementer, that is a tick behaviour change
  this task does not make and its acceptance does not ask for. Flagging it rather than deciding it.
- **`foldcheck --derive` is the only new surface, and it is folded into an existing verb.** A `derive`
  subcommand would need a row in `capabilities/work/README.md`, which `tests/test_surface.py`
  requires and which is outside this task's write paths. Rather than edit the README from outside the
  claim (as LOOM-8 had to) or leave a failing suite, the read-only check lives on `foldcheck`, whose
  flags are not part of the documented surface contract. An operator verb that *applies* the pass does
  not exist; the apply path is reached through `tick`, which is the caller that matters.
- **The shadow comparison costs one extra read of the log per pass.** It reads events for each of the
  three dry-run phases plus the handler projection. At 2,450 events that is milliseconds; it is
  linear in the log and would want a snapshot cursor if the log grew by orders of magnitude.
- **`INTENT_KEYS` is the contract between the two derivations.** A field that matters and is not
  listed there is compared by nobody. Adding a decision to a handler means adding its key, or the
  shadow passes while the two disagree about it.
- **The `review` handler's rules are mirrored from `review_request_sweep`, not shared with it.**
  Deliberate — a shared implementation would make the comparison vacuous — but it means the two must
  be kept in step by hand until the sweep is retired. The shadow gate is what makes a drift visible
  instead of silent.
- **`--json` output changed shape.** `tick --json` gained `deriveShadow`, `reworkFlagged`,
  `deriveSkipped` and `deriveRefused`, and the inner command chatter is now suppressed so the payload
  parses. Anything that counted on the prose before the JSON was already fragile; nothing in this repo
  does.

## Review lenses to apply

- **Idempotency.** Does every handler key its write by the event it came from — `answer` seq,
  `request` seq, `ask` seq — and does a second full pass really append nothing?
- **The gate's honesty.** Does the comparison measure the thing acceptance 1 names, and is excluding
  the claim action a defensible reading of "the handler projection" rather than a way to make the test
  pass? `test_the_comparison_can_fail_in_either_direction` exists to make that answerable.
- **The verified path.** Is `cmd_transition` → `verified_review` genuinely the only way the receipt is
  written, and is "wrote nothing" true of a refusal rather than merely "left the status alone"?
- **Authority.** Is there any path by which a handler can reach a claim, a lease or a spawn, including
  through the commands it delegates to?
