# Evidence — LOOM-3 adopting guide and worked example

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-3`
This revision answers review request **1440** — "changes requested" from `opencode/reviewer-agent` —
by making the worked example run again and by replacing the evidence it invalidated.

## What the review found, and what changed

| Finding | Fix |
|---|---|
| **Blocking.** `examples/walkthrough/walkthrough.py` died at step 3: the red/green transitions omitted the fencing epoch the engine requires of a claim holder, so steps 4–7 never ran and `event chain OK` never printed. | Transitions now present `--epoch`, and the example *reads the epoch from the claim output* rather than hardcoding it, so the fence is demonstrated instead of assumed. The run completes all seven steps. |
| **Blocking.** This file claimed the walkthrough completed (it did not) and 132 tests passed (215 did). | Rewritten; every number below was measured on the commit under review. |
| **Blocking.** The Limits section said board registration had no CLI command, and the example seeded boards by writing the store directly. | The example now registers its boards through `loom init` and `loom board add` — the same path an adopter takes — and no longer imports `store` or touches `sys.path` at all. The false limit is gone. |
| **Nit.** `docs/adopting.md` §1 carried a doubled comment from a find/replace. | Removed. |
| **Nit.** §3 said to use `loom board add` without saying `--board` is the new board's id and is mandatory. | §3 now shows the command and says so. |
| **Recurrence.** Nothing ran the example, which is why it rotted between approval and this review. | `capabilities/work/tests/test_walkthrough.py` executes it the way the guide does and asserts the seven steps, both refusals, the board registration and `event chain OK`. A future engine change that breaks the example now fails the suite. |

## What the example found

Writing it exercised the protocol rather than describing it, and two flags behave in ways their
names do not suggest:

| Flag | Expectation | Reality |
|---|---|---|
| `--review-answer` | the question's sequence | the **answer** event's sequence |
| `--integrated-source` | optional | must be the exact commit the reviewer approved |

Both stay documented in `docs/adopting.md` §4. Neither is a bug — the checks are correct — and the
example now walks through both, so an adopter meets them with the guide open.

## Secondary finding, deliberately not fixed here

The reviewer noted that the CLI prints `answered <asker> on <task>`, naming the answer's *addressee*
rather than the agent who answered (`capabilities/work/engine.py`, `cmd_answer`). That is engine
behaviour, outside this task's files, and a change there needs its own claim and review. Inside this
task's scope the walkthrough now prints the reviewer's own decision line after the CLI's, so step 4
cannot be misread as the implementer approving its own work.

## Limits, current

- `loom init` does not check that `--path` exists, while `loom board add` refuses a missing board
  file. A typo in the first board surfaces later, at `board sync`/`reconcile`.
- The walkthrough's dependency gate is single-hop: it shows `alpha/W-2` and `beta/W-1` becoming
  ready when `alpha/W-1` integrates, not a chain or a cycle.
- Evidence lives at `capabilities/work/evidence/LOOM-3.md`, beside the work home the registry names,
  not in the content repository.

## Verification

Run on `loom/loom-loom-3` at the commit under review:

- `python3 examples/walkthrough/walkthrough.py` — exits 0, prints the seven steps, `green ->
  reviewed -> integrated`, the lease refusal, the independence refusal, and `event chain OK`
  (≈2 s, throwaway database).
- `python3 -m unittest discover -s tests` from `capabilities/work` — **224 tests OK**, including the
  three new walkthrough tests.
- `python3 -m unittest discover -s capabilities/work/tests` from the repository root — OK.
- `python3 validate_work.py` — **PASS**. Its warnings are pre-existing and concern other tasks
  (expired legacy leases, blocked-but-ready tasks, the documented pre-migration integration
  receipts), none about LOOM-3.
