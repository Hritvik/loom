# Evidence — LOOM-12 make evidence claims executable

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-12-a1`
Commit under review: the tip of this branch; the registry ask binds the full commit

## What changed

An evidence file asserts things a reviewer cannot re-run: how many tests passed, what the validator
said, how many files a sweep found. Those sentences have been wrong in review, round after round,
and the correction was always another sentence. This task makes the assertion executable.

`capabilities/work/engine.py` gains an evidence-claim checker, a derivation registry and a template:

- A **machine-checked span** writes a value between two HTML comments that name a derivation:

  ```
  The suite collects <!-- loom:tests.count -->276<!-- /loom --> tests.
  ```

  `parse_evidence_claims` reads the span, `check_evidence` re-derives the value from the tree and
  compares it, and `refresh_evidence_text` rewrites a stale value in place. HTML comments render
  blank, so a reader sees the sentence and not the markers.
- A **derivation** is `callable(root, argument, cache) -> str`. The built-ins are `tests.count`
  (what `unittest` collects, which is the number `Ran N tests` prints), `validator.verdict` (the
  validator's exit status), `validator.errors` (its `ERROR` lines, optionally filtered) and
  `tracked.count` (files in the git index under a pathspec). `register_evidence_derivation` lets a
  repository add a derivation such as a sweep without copying its detector into the engine.
- Nothing fails quietly. An unknown derivation, a marker with no span, a stray closing marker, an
  unclosed code fence and a derivation that cannot answer are all problems, so a typo in a marker
  fails instead of quietly demoting the claim back to prose. `<!--` and `-->` inside a fenced code
  block are a quotation, not a claim, which is how this file may show the syntax at all; the fence
  itself has to close, because an open one would exempt everything below it.
- A derivation cannot invent a zero. `validator.errors` refuses to answer when the validator fails
  without printing a `FAIL (n errors, …)` tally, and refuses when its tally disagrees with the
  `ERROR` lines it printed. A validator that cannot load a registry prints `FAIL` with no error
  lines, and counting that as zero would make every claim about it pass vacuously — the exact
  failure this task exists to remove.
- `EVIDENCE_TEMPLATE` is the starter an author fills in, and `render_evidence_template` fills every
  span for the current tree, so a new evidence file begins current instead of beginning wrong.

`tests/test_evidence.py` adds one test module, in seven groups:

| Group | What it proves |
|---|---|
| `SpanParsingTest` | a span yields its claim, argument and line; prose is not a claim; a fenced example is a quotation; an unclosed span, a stray close, an unknown derivation and an unclosed fence are problems, never silent passes |
| `StaleClaimTest` | a test count wrong by one fails, names the file and line, and refresh repairs it without touching prose |
| `ValidatorClaimTest` | a claim follows a stub validator's exit status in both directions, so the value is re-derived and not copied; `ERROR` lines are counted and filterable; a missing validator, a missing tally, a tally that disagrees and an exit-0-with-errors run are problems |
| `TrackedCountTest` | a tracked count comes from the index, not from the working tree; a pathspec cannot escape the repository |
| `BoundarySweepTest` | the derivation this repository registers outside the engine counts a planted home path and a planted mailbox, so the sweep is not a no-op |
| `EvidenceTemplateTest` | the template marks machine-checked lines and prose as different things, every example in it is a real derivation, and the rendered starter is current |
| `RepositoryEvidenceTest` | the live guard: every claim in `evidence/` is current now, and the guard fails if no evidence file carries one |

`WORKER_BRIEF` now points each worker at the template and at the marker rule, so the format arrives
with the task rather than with a review finding.

## This file's own claims

Each row below is machine-checked, and the check is the suite:

| Claim | Marker | What it asserts |
|---|---|---|
| Suite size | `<!-- loom:tests.count -->316<!-- /loom -->` | the tests `unittest` collects under `tests/` |
| Test files | `<!-- loom:tracked.count:tests -->12<!-- /loom -->` | files in the git index under `tests/` |
| Boundary sweep | `<!-- loom:sweep.boundary-tree -->0<!-- /loom -->` | tracked files carrying an absolute home path or a personal mailbox |
| Validator | `<!-- loom:validator.errors:loom/LOOM-12 -->0<!-- /loom -->` | `ERROR` lines the validator prints naming this task |

Everything else in this file is prose. That is the honest split the template states: a sentence
that no machine reads is a claim on the author, and the four values above are the ones a machine can
settle.

## Red run, then green

The claim was wrong by one before it was true. With the suite-size span edited from 316 to 315, from
`capabilities/work`:

```
python3 -m unittest discover -s tests -p test_evidence.py
AssertionError:
  evidence/LOOM-12.md:63  tests.count  recorded 315  derived 316  STALE
    refresh: the value above is what the tree says now; rewrite the span instead of retyping it
  evidence/LOOM-12.md:64  tracked.count:tests  recorded 12  derived 12  current
  evidence/LOOM-12.md:65  sweep.boundary-tree  recorded 0  derived 0  current
  evidence/LOOM-12.md:66  validator.errors:loom/LOOM-12  recorded 0  derived 0  current
evidence/LOOM-12.md: 4 machine-checked claim(s), 1 stale, 0 problem(s) — STALE
Refresh instead of retyping, from capabilities/work:
  LOOM_EVIDENCE_REFRESH=1 python3 -m unittest discover -s tests -p test_evidence.py

FAILED (failures=1)
Ran 40 tests in 1.352s
```

Refresh rewrites the span and the same command passes:

```
LOOM_EVIDENCE_REFRESH=1 python3 -m unittest discover -s tests -p test_evidence.py
refreshed under LOOM_EVIDENCE_REFRESH=1: LOOM-12.md: tests.count: 315 -> 316
Ran 40 tests in 1.517s
OK
```

The fenced example at the top of this file still reads 276: refresh left it alone, because a marker
inside a fenced block is a quotation and not a claim.

The full tree, from `capabilities/work`:

```
python3 -m unittest discover -s tests      # OK, 316 tests, 2 skipped
python3 validate_work.py                   # FAIL (1 errors, 109 warnings) — see Limits
```

## Acceptance

| Criterion | Where it is proved |
|---|---|
| A stale test count in an evidence file fails the suite | `RepositoryEvidenceTest.test_every_claim_in_the_evidence_is_current` runs the checker over every file in `evidence/`; `StaleClaimTest.test_a_stale_test_count_fails_the_check` drives the same path with a count wrong by one, and the red run above is that failure on this file |
| A claimed validator result is re-derived rather than copied | `ValidatorClaimTest.test_the_verdict_follows_the_validator_both_ways` and `…_a_flipped_validator_makes_a_current_claim_stale` flip a stub validator and require the claim to follow it; `test_errors_are_counted_and_filtered_by_name` re-derives counts. This file's own validator claim is the task-scoped one, for the reason under Limits |
| The evidence template marks machine-checked lines and prose | `EVIDENCE_TEMPLATE`'s legend names both kinds and labels the prose sections; `EvidenceTemplateTest` asserts the legend, that every example in the derivation table resolves, that the rendered starter is current, and that rendering is idempotent |

## Limits

- **Prose is unchecked by construction.** The mechanism covers what an author marks. A number
  written as prose is still believed, and this file still contains prose claims. Marking is a
  choice the author makes, and the template states the choice; it cannot make the choice for them.
- **A suite-size claim belongs to one tree.** Adding a test makes it stale in every evidence file
  that records one, on every branch. That is the intended alarm and it is a cost: a worker whose
  branch adds tests will see this repository's suite red through a shared file it may not be
  allowed to edit. Refresh is one command, but the count is still a claim about a tree, and the
  remedy is to refresh in the commit that changes the tree — or not to claim a count at all, which
  is what LOOM-2's evidence does.
- **The validator's global verdict is deliberately not claimed here.** `validate_work.py` validates
  shared registry state; on this tree it exits 1 because of a missing heading on a board outside
  this task's write path (`midas/W-8`), and that can change under another board's edit rather than
  under this diff. A `validator.verdict` claim in a committed evidence file would be a landmine, so
  this file claims only what the validator says about this task. Both derivations exist and are
  proved; the choice of what to claim is the author's.
- **`sweep.boundary-tree` is registered by the suite, not the engine.** It reuses the detectors in
  `tests/test_boundary.py` rather than copying them, so it resolves only while the tests run. An
  engine-only check reports it as an unknown derivation — a failure, not a pass, but a reviewer
  running `engine.check_evidence_file` by hand will see it.
- **`tests.count` counts loaded tests, it does not run them.** That is what keeps the guard
  non-recursive: a test cannot run the suite it is running inside. A stale count is caught; a
  failing suite is not, beyond the fact that a failing suite is already failing.
- **No CLI verb was added.** `tests/test_surface.py` requires every engine command to be documented
  in `capabilities/work/README.md`, which is outside this task's write path. The checker is a
  library entry point (`engine.check_evidence_file`, `engine.refresh_evidence_file`) plus the suite,
  and the refresh path is the `LOOM_EVIDENCE_REFRESH` environment variable on the evidence test.
- **Only this file carries marked claims.** Earlier evidence files still record prose numbers and
  commands. Converting them is a separate task: each file belongs to the task that wrote it, and
  their write paths are not this one.
- **The guard runs when the suite runs, and nowhere else.** `validate_work.py` is outside this
  task's write path, so no second caller checks evidence claims. A tree whose tests are run has the
  guard; a tree whose tests are not run has none, and this task does not claim otherwise.

## Reproduce

From `capabilities/work`:

```bash
python3 -m unittest discover -s tests -p test_evidence.py            # the mechanism
python3 -m unittest discover -s tests                                # the whole suite
python3 validate_work.py                                             # registry, shared state
LOOM_EVIDENCE_REFRESH=1 python3 -m unittest discover -s tests -p test_evidence.py   # refresh spans
```

`engine.check_evidence_file` resolves the three engine derivations on its own, but
`sweep.boundary-tree` is registered by the test module, so a hand-run check reports that one claim
as an unknown derivation. Run the evidence test when you want all four resolved.
