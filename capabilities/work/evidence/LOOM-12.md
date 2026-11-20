# Evidence — LOOM-12 make evidence claims executable

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-12-a9`, rebased onto `main` at `7df7b16`; the registry ask binds the full
commit, and this branch is the integration candidate rather than a pre-merge branch. `main` moved
once more while this revision was being prepared (a docs commit outside `capabilities/work`), and
the branch was rebased onto it, which changes no span — the argument for the narrowing below.

## What changed

An evidence file asserts things a reviewer cannot re-run: how many tests passed, what the validator
said, how many files a sweep found. Those sentences have been wrong in review, round after round,
and the correction was always another sentence. This task makes the assertion executable.

`capabilities/work/engine.py` gains an evidence-claim checker, a derivation registry and a template:

- A **machine-checked span** writes a value between two HTML comments that name a derivation:

  ```
  This module collects <!-- loom:tests.count:tests/test_evidence.py -->55<!-- /loom --> tests.
  ```

  `parse_evidence_claims` reads the span, `check_evidence` re-derives the value from the tree and
  compares it, and `refresh_evidence_text` rewrites a stale value in place. HTML comments render
  blank, so a reader sees the sentence and not the markers.
- A **derivation** is `callable(root, argument, cache) -> str`. The built-ins are `tests.count`
  (what `unittest` collects from a directory or from one module, which is the number `Ran N tests`
  prints), `validator.verdict` (the validator's exit status), `validator.errors` (its `ERROR`
  lines, optionally filtered) and `tracked.count` (files in the git index under a pathspec).
  `register_evidence_derivation` lets a repository add a derivation such as a sweep without
  copying its detector into the engine.
- A claim is only worth marking if the **tree** can answer it. The validator reads the portfolio
  registry, which is shared state a clone does not have and another board's edit can change, so
  `engine.SHARED_STATE_DERIVATIONS` names `validator.*` and a committed evidence file may not use
  one. The derivations stay registered for interactive use, and the registry is passed to the run
  explicitly (`WORK_DB`, else the store in the home) so what a claim is about is the database the
  check named. The template states the rule.
- Nothing fails quietly. An unknown derivation, a marker with no span, a stray closing marker, an
  unclosed code fence and a derivation that cannot answer are all problems, so a typo in a marker
  fails instead of quietly demoting the claim back to prose. `<!--` and `-->` inside a fenced code
  block are a quotation, not a claim, which is how this file may show the syntax at all; the fence
  itself has to close, because an open one would exempt everything below it.
- A derivation cannot invent a number. `validator.errors` refuses to answer when the validator
  fails without printing a `FAIL (n errors, …)` tally, refuses when its tally disagrees with the
  `ERROR` lines it printed, and says plainly when the validator found no registry at all. A
  validator that cannot load a registry is a broken run, and counting it as zero would make every
  claim about it pass vacuously — the exact failure this task exists to remove.
- `tests.count` collects with its own `unittest.TestLoader()`, so a `-k` filter set by whatever
  process runs the check cannot change the answer, and it refuses to count a module that would not
  import: discovery substitutes one placeholder test per broken module, and counting that would
  report a plausible number for a suite that never loaded.
- `EVIDENCE_TEMPLATE` is the starter an author fills in, and `render_evidence_template` fills every
  span for the current tree, so a new evidence file begins current instead of beginning wrong.

`tests/test_evidence.py` adds one test module, in nine groups:

| Group | What it proves |
|---|---|
| `SpanParsingTest` | a span yields its claim, argument and line; prose is not a claim; a fenced example is a quotation; an unclosed span, a stray close, an unknown derivation and an unclosed fence are problems, never silent passes |
| `StaleClaimTest` | a test count wrong by one fails, names the file and line, and refresh repairs it without touching prose |
| `TestsCountTest` | the count ignores a `-k` filter on the shared loader, a missing test directory or module is a problem, a module that will not import is a problem rather than one test, and a module-scoped count stays put when an unrelated test file lands beside it |
| `ValidatorClaimTest` | a claim follows a stub validator's exit status in both directions, so the value is re-derived and not copied; `ERROR` lines are counted and filterable; a missing validator, a missing registry, a missing tally, a tally that disagrees and an exit-0-with-errors run are problems |
| `RealValidatorFixtureTest` | the same claim against this repository's own `validate_work.py` and a registry built under a temporary root, so the verdict is re-derived end to end without this machine's store |
| `TrackedCountTest` | a tracked count comes from the index, not from the working tree; a pathspec cannot escape the repository |
| `BoundarySweepTest` | the derivation this repository registers outside the engine counts a planted home path and a planted mailbox, so the sweep is not a no-op |
| `EvidenceTemplateTest` | the template marks machine-checked lines and prose as different things, names every engine derivation and which of them need the registry, claims nothing from shared state, and renders a current starter |
| `RepositoryEvidenceTest` | the live guard: every claim in `evidence/` is current; no file claims shared state; and the guard is green in a fresh clone |

`WORKER_BRIEF` now points each worker at the template and at the marker rule, so the format arrives
with the task rather than with a review finding.

## Rework after review 2214

The blocking finding was that the guard was not hermetic. `RepositoryEvidenceTest` ran the real
`validate_work.py` against the ambient store, and the rendered template did the same, so the
verdict depended on the machine. With no `~/.local/state/loom/work.db` — a fresh clone, before
`loom init` — the validator prints `FAIL: no registry in …` with no tally and three tests failed;
with a registry, the claim moved when another board's errors changed.

The fix is at the root rather than in a skip: a committed claim is a property of the commit.

- `engine.SHARED_STATE_DERIVATIONS` names the derivations that read state outside the repository.
  `RepositoryEvidenceTest.test_the_guard_does_not_read_shared_state` fails if any file under
  `evidence/` claims one, and `EvidenceTemplateTest.test_the_template_claims_only_what_the_tree_can_answer`
  holds the starter to the same rule.
- `RepositoryEvidenceTest.test_the_guard_passes_in_a_fresh_clone` runs the guard in a subprocess
  with `HOME` and `WORK_DB` pointed at an empty directory, so the documented `unittest` command is
  proved green on a clone that has no registry — the reviewer's repro, as a test.
- This file no longer claims a validator result. The mechanism is still proved end to end: a stub
  validator whose exit status and tally the test controls, and this repository's own
  `validate_work.py` over a registry built under a temporary root, both require the claim to follow
  the run. The engine keeps both derivations for a repository whose evidence is deliberately about
  a known registry.
- `tests.count` now uses a fresh loader and refuses a module that will not import, so the count is
  the tree's rather than the invocation's. The first draft's guard read `1` when run under
  `unittest -k`, because the filter lives on the shared loader.
- The pasted `FAIL (1 errors, … warnings)` tally and the pasted `OK, … tests` line are gone from
  this file. Both are prose, neither is checked, and the tally was already stale one commit later —
  which is the failure mode this task exists to remove. Only commands are pasted now.

## Rework after integration (revision 9)

Approved at revision 8 against a main that had since moved. The coordinator merged the reviewed
commit, the merged tree carried more tests and more test files than the branch did, the guard
failed on the two spans that count them, and the merge was reverted.

The remedy at the registry is the one this file already named in Limits: rebase onto current main
and refresh, which is what the spans in this revision record. Both values changed: the suite grew,
and four test files arrived with LOOM-13 and others. The claim about the sweep did not move, which
is the useful half of the same fact — a span invents nothing and a span about a stable property
stays true across merges.

What the revert also showed is that this is not a one-off. A whole-tree count is a claim about
whatever tree it is measured in, so the next task that adds a test or a test file makes this file
stale again — and that task cannot refresh it, because `evidence/LOOM-12.md` is not its write
path. That hazard is stated in Limits with the two ways out; it is a property of claiming a
tree-wide number in a file other trees merge into, not a defect in the checker, which is why the
checker refused to let it pass.

## Rework after integration (revision 14)

Approved at revision 13 against `489deb2`, and the coordinator could not integrate it: `engine.py`
has changed under LOOM-9 since the review, so the reviewed commit is not an ancestor of `main` and
the receipt cannot integrate as reviewed (the case LOOM-14's validator now names before a merge is
spent). The code is unchanged.

That revision was that rebase and nothing else. The branch became `8cc57c0` (LOOM-14, UI-3 and UI-4)
plus the three commits of this task; the rebase was clean, no test in `tests/` was touched, and the
diff against the reviewed tree was this file. Both count spans moved again — 380 to 393 tests and
16 to 17 files — the third tree the claim had been true of.

## Rework after integration (revision 19)

Approved at revision 18 against `8cc57c0`-era `main`, and refused again for the same reason:
`engine.py` had moved under LOOM-6's integration, so the reviewed commit no longer reached `main`.

This time the rebase onto `9a9dfb8` (LOOM-6, UI-5) came clean **and the counts did not move** — the
tests that landed under LOOM-6 and UI-5 live in `ui/tests/`, which `discover -s tests` does not
reach. That is luck, not a fix: the two spans were one unrelated `tests/test_*.py` away from
staling a third time, and this file has now cost one reverted merge and two refused integrations.

So the tree-wide counts are gone from this file, and the mechanism gains the narrower claim that
replaces them. `tests.count` takes a directory *or a test module inside one*, and a committed claim
now names the module this task added:

- `tests.count:tests/test_evidence.py` counts the tests of the module in this diff. A branch that
  lands `tests/test_something_else.py` leaves it where it is — the claim is no longer about that
  branch's tree. What would move it is a change to this module, which is a rework of this task and
  can therefore refresh this file, because `evidence/LOOM-12.md` is that task's write path.
- `tracked.count:tests` is dropped rather than narrowed. Any tracked-file count moves when any
  file is added, and a one-file claim (`tracked.count:tests/test_evidence.py` answering `1`) would
  be a number that carries nothing. The derivation stays registered, is documented in the
  template, and `TrackedCountTest` still proves it re-derives from the index.
- `sweep.boundary-tree` stays. It is tree-wide, but it is a claim about a property the tree must
  have: it moves only if something genuinely wrong lands, which is exactly when it should go red.
  A suite size is not that kind of number — it moves when the tree improves.

That is a change to what this file claims, not to what the mechanism does: every acceptance test
and every derivation is unaffected, a stale count still fails the suite, and the red run below is
still the same failure. It is written down here because a reviewer should see it and can ask for
the whole-tree count back in one line; the mechanism does not care which claim it is given.

## This file's own claims

Each row below is machine-checked, and the check is the suite:

| Claim | Marker | What it asserts |
|---|---|---|
| Tests in this task's module | `<!-- loom:tests.count:tests/test_evidence.py -->55<!-- /loom -->` | the tests `unittest` collects from `tests/test_evidence.py` |
| Boundary sweep | `<!-- loom:sweep.boundary-tree -->0<!-- /loom -->` | tracked files carrying an absolute home path or a personal mailbox |

Everything else in this file is prose. That is the honest split the template states: a sentence
that no machine reads is a claim on the author, and the two values above are the ones a machine
can settle from the tree. The validator's answer about this task is stated in Limits instead,
because it belongs to the registry and not to this commit. The whole-suite size is a command in
Reproduce rather than a claim, for the reason given above: it is a number about whatever tree it
is measured in, and this file merges into trees other branches build.

## Red run, then green

The claim was wrong by one before it was true. With the suite-size span edited one low, from
`capabilities/work`:

```
python3 -m unittest discover -s tests -p test_evidence.py
AssertionError:
  evidence/LOOM-12.md:104  tests.count  recorded 327  derived 328  STALE
    refresh: the value above is what the tree says now; rewrite the span instead of retyping it
  evidence/LOOM-12.md:105  tracked.count:tests  recorded 12  derived 12  current
  evidence/LOOM-12.md:106  sweep.boundary-tree  recorded 0  derived 0  current
evidence/LOOM-12.md: 3 machine-checked claim(s), 1 stale, 0 problem(s) — STALE
Refresh instead of retyping, from capabilities/work:
  LOOM_EVIDENCE_REFRESH=1 python3 -m unittest discover -s tests -p test_evidence.py

Ran 52 tests in 1.249s

FAILED (failures=2)
```

Two tests fail: the guard, and the fresh-clone guard that runs it in a subprocess. Both name the
line, the recorded value and the derived one. (The line numbers are the ones that run printed.)

That transcript is a quotation of the run that produced this mechanism, kept because it is the
red-then-green proof: 327 and 328 are that tree's counts and 52 is that tree's evidence-test count,
not this file's. The live numbers are the spans in the table above, and the suite settles them.

Refresh rewrites the span and the same command passes:

```
LOOM_EVIDENCE_REFRESH=1 python3 -m unittest discover -s tests -p test_evidence.py
....................................................
Ran 52 tests in 1.325s

OK

refreshed under LOOM_EVIDENCE_REFRESH=1: LOOM-12.md: tests.count: 327 -> 328
```

The fenced example at the top of this file still reads 276: refresh left it alone, because a marker
inside a fenced block is a quotation and not a claim.

The whole tree, from `capabilities/work`:

```bash
python3 -m unittest discover -s tests
python3 validate_work.py
```

Run at this commit, from `capabilities/work`: the suite was green (`OK`, with the skips it
reports), the documented `HOME=$(mktemp -d)` run of the same suite was green with the same result,
and the validator failed with 38 errors, none of them naming `loom/LOOM-12`.

Those three sentences are prose on purpose. The suite's size is a property of whatever tree it is
measured in, and this file claims only its own module; the validator's verdict is registry state,
which a clone does not have and another board's edit can change. In both cases the reviewer's run,
not this paragraph, is the check.

## Acceptance

| Criterion | Where it is proved |
|---|---|
| A stale test count in an evidence file fails the suite | `RepositoryEvidenceTest.test_every_claim_in_the_evidence_is_current` runs the checker over every file in `evidence/`; `StaleClaimTest.test_a_stale_test_count_fails_the_check` drives the same path with a count wrong by one, and the red run above is that failure on this file |
| A claimed validator result is re-derived rather than copied | `ValidatorClaimTest.test_the_verdict_follows_the_validator_both_ways` and `…_a_flipped_validator_makes_a_current_claim_stale` flip a stub validator and require the claim to follow it; `RealValidatorFixtureTest` does the same with this repository's own validator over a registry built under a temporary root, for both the verdict and the error count. No committed file claims a validator result — the rule and its reason are in Limits |
| The evidence template marks machine-checked lines and prose | `EVIDENCE_TEMPLATE`'s legend names both kinds and labels the prose sections; `EvidenceTemplateTest` asserts the legend, that every engine derivation is named with whether it answers from the tree, that the starter claims nothing from shared state, and that the rendered starter is current and idempotent |

No criterion depends on *which* tree a count is measured in. `tests.count` answers the same way for
a directory and for a module, and `TestsCountTest.test_a_module_argument_counts_that_module_and_not_its_siblings`
with `…_a_module_scoped_count_does_not_move_when_a_sibling_lands` pin the narrower form that the
rework above uses: the second one plants a new test file beside the claimed module and requires the
claim not to move, which is the exact event that reverted a merge.

## Limits

- **Prose is unchecked by construction.** The mechanism covers what an author marks. A number
  written as prose is still believed, and this file still contains prose claims. Marking is a
  choice the author makes, and the template states the choice; it cannot make the choice for them.
- **A whole-tree count is not a claim a committed file can keep.** This has cost one reverted
  merge and two refused integrations. The first integration attempt was reverted because the two
  tree-wide counts here were stale in the merged tree, not because anything in the diff was wrong;
  the second and third were refused before a merge because the reviewed commit no longer reached
  `main` (LOOM-9, then LOOM-6, landed under it). Refresh-on-integration and dropping the counts
  were the two obvious ways out; this revision takes a third, inside what a worker may decide:
  claim the narrowest tree that answers the sentence. `tests.count` accepts a module, so the claim
  here names `tests/test_evidence.py` and no longer moves when an unrelated test lands. What the
  limit keeps is the general rule, which is now stated in the template: a span should name the
  narrowest thing that answers it, and a count over a tree other branches write to will go stale
  for changes its own diff never made. Two consequences stay open. A change to
  `tests/test_evidence.py` still moves this span — that change is a rework of this task, so it can
  refresh it; and `main` moving between the review and the merge is still a coordinating
  responsibility, which is what LOOM-14's ancestry check names before a merge is spent rather than
  after.
- **The mechanism cannot make a claim current by itself.** A stale span fails and says what to run;
  refreshing is a command, not something the guard does on its own. `LOOM_EVIDENCE_REFRESH=1` is
  that command, and it rewrites only the spans, never the prose around them.
- **The validator is deliberately not claimed here.** `validate_work.py` validates shared registry
  state, so its verdict is a property of the machine: on this tree it fails for boards outside this
  task's write path, and that set changes under other boards' edits rather than under this diff. A
  committed `validator.*` claim would fail in a fresh clone and go red for changes this diff never
  made, so `SHARED_STATE_DERIVATIONS` names both derivations and the guard refuses them in
  `evidence/`. They remain available for interactive use, and a repository whose evidence is
  deliberately about a known registry can still claim them.
- **A validator result is proved, not claimed.** `ValidatorClaimTest` runs a stub validator whose
  exit status and tally it controls, and `RealValidatorFixtureTest` runs the repository's own
  `validate_work.py` over a registry built under a temporary root. What is not proved is that the
  real `validate_work.py` against this machine's registry today reports what a reviewer wants to
  read — that is what the reviewer's own run is for, and it is not a claim about this commit.
- **`sweep.boundary-tree` is registered by the suite, not the engine.** It reuses the detectors in
  `tests/test_boundary.py` rather than copying them, so it resolves only while the tests run. An
  engine-only check reports it as an unknown derivation — a failure, not a pass, but a reviewer
  running `engine.check_evidence_file` by hand will see it.
- **`tests.count` counts loaded tests, it does not run them.** That is what keeps the guard
  non-recursive: a test cannot run the suite it is running inside. A stale count is caught; a
  failing suite is not, beyond the fact that a failing suite is already failing.
- **The fresh-clone guard is a subprocess test.** It proves the documented command is green with an
  empty `HOME` and no registry, which is the environment the review found broken. It is one
  process spawn, so it asserts on the environment it constructs rather than on every environment a
  reviewer might have; a registry path outside `HOME` and `WORK_DB` would not be covered.
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
HOME=$(mktemp -d) python3 -m unittest discover -s tests              # a clone with no registry
python3 -m unittest discover -s tests                                # the whole suite
python3 validate_work.py                                             # registry, shared state
LOOM_EVIDENCE_REFRESH=1 python3 -m unittest discover -s tests -p test_evidence.py   # refresh spans
```

`engine.check_evidence_file` resolves the three engine derivations on its own, but
`sweep.boundary-tree` is registered by the test module, so a hand-run check reports that one claim
as an unknown derivation. Run the evidence test when you want all three resolved.
