# Evidence — LOOM-14 report a review that cannot integrate

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-14-a1` (isolated worktree, attempt `loom/LOOM-14#a1`)
Change under review: `488d784` — `validate_work.py`, `PROTOCOL.md`, `AGENT-GUIDE.md`. The evidence
commit that follows it on this branch changes only this file.
Status: green, submitted for independent review

Commands below run from `capabilities/work` unless they say otherwise.

## The problem this closes

Integration requires the approved commit to be an ancestor of the board's `integration_ref` at
transition time (`engine.py:require_source_on_ref`). A candidate reviewed against an older main can
therefore never integrate as reviewed, and nothing said so: LOOM-6 merged clean with an `engine.py`
blob 430 lines off, LOOM-12 merged clean and turned main red on stale evidence, and UI-3/UI-4
conflict with UI-2. The integrator learned it only after spending a merge. The validator now names
the reviewed tasks in that position before anyone merges.

## Delivered

`validate_work.py`, beside `cross_check_capabilities`:

- **`git_read(repo, *args)`** — one read-only Git call per question, with `GIT_*` environment
  overrides stripped (the same hygiene as `engine.py:git_review_check`), a 10 s timeout, and `None`
  when Git cannot run at all, so the validator reports rather than crashes.
- **`integration_tip(repo, ref)`** — resolves the board's ref to a commit, and returns the reason it
  could not (`ref` is not `refs/heads/…`, the path is not a checkout, the ref is missing).
- **`check_reviewed_sources(tasks, boards, errors, warnings)`** — for each task in `reviewed` with a
  recorded `review.source`:
  - the source must be a full commit hash; anything else is a warning, because it cannot be checked;
  - the ref is the board's `integration_ref`, defaulting to `refs/heads/main`, read in
    `task.owner_repo` — the same repository and default the engine uses, so the validator and the
    gate cannot disagree;
  - `git merge-base --is-ancestor <source> <tip>` exit 0 reports nothing; exit 1 is an error naming
    the task, the source, the ref and the repository:
    `ERROR <task>: reviewed source <sha> is not in the history of refs/heads/main (<repo>); this
    receipt cannot integrate as reviewed — merge the candidate, review the merge commit, and
    integrate that exact commit`;
  - anything else (unreadable ref, missing object) is a warning once per (repository, ref), never a
    verdict the check cannot prove.
  - Ref tips are resolved once per (repository, ref); one `merge-base` runs per reviewed task.
    A full run over this registry takes 0.73 s.

`PROTOCOL.md` — a **Sequencing** bullet in the review protocol: the integrator merges the candidate,
the reviewer reviews that merge commit, the integrator integrates that exact commit, and no other
candidate merges in between; `validate_work.py` reports every reviewed task whose approved source is
not in the history of its board's integration ref.

`AGENT-GUIDE.md` — the same rule as a named paragraph in the independent review pipeline, next to the
existing paragraph on `integration_ref` and the local-ref limit.

## Verification

```bash
# 1. This registry: the reviewed tasks that cannot integrate are named.
python3 validate_work.py | grep -c "reviewed source"
#   29

python3 validate_work.py | tail -1
#   FAIL (31 errors, 100 warnings)

# Every error that is not this check is the two pre-existing program-heading errors.
python3 validate_work.py | grep '^ERROR' | grep -v 'reviewed source'
#   ERROR midas/W-8: heading W-8 missing from midas.program
#   ERROR loom/LOOM-14: heading LOOM-14 missing from loom.program
```

```bash
# 2. The two acceptance behaviours, on disposable fixtures through the real validator.
#    The fixture work home is the suite's own (tests/test_work.Workspace), which is why this is a
#    harness and not a new test file: tests/ is outside this task's write paths.
python3 - <<'EOF'
import subprocess, sys, tempfile
from pathlib import Path

WORK = Path.cwd()
sys.path.insert(0, str(WORK)); sys.path.insert(0, str(WORK / "tests"))
from test_work import T0, WorkCLITest, validate

case = WorkCLITest("test_create_is_central_and_validates")
case.setUp()
case.mark_green()
case.commit("Line before the candidate")
subprocess.run(["git", "-C", str(case.ws.repo), "checkout", "-q", "-b", "candidate"],
               check=True, capture_output=True)
source = case.commit("Candidate change", path="src/one/value.txt", content="candidate")
subprocess.run(["git", "-C", str(case.ws.repo), "checkout", "-q", "main"],
               check=True, capture_output=True)
approval = case.approve_review(case.request_review(source))
case.invoke("transition", "demo/W-1", "--revision", "2", "--to", "reviewed",
            "--reviewer", "reviewer-r", "--decision", "approve",
            "--review-answer", approval, "--evidence", "evidence/W-1.md",
            "--agent", "agent-b", "--now", T0)

off = validate(case.ws.root)
print("off-branch exit:", off.returncode)
print([line for line in off.stdout.splitlines() if "reviewed source" in line])

subprocess.run(["git", "-C", str(case.ws.repo), "merge", "--ff-only", "candidate"],
               check=True, capture_output=True)
on = validate(case.ws.root)
print("after merge exit:", on.returncode,
      "| mentions the check:", "cannot integrate as reviewed" in on.stdout)
case.tearDown()
EOF
#   off-branch exit: 1
#   ['ERROR demo/W-1: reviewed source … is not in the history of refs/heads/main (…/repo); this
#     receipt cannot integrate as reviewed — merge the candidate, review the merge commit, and
#     integrate that exact commit']
#   after merge exit: 0 | mentions the check: False
```

```bash
# 3. The check reads the board's integration_ref, and a source on the ref reports nothing.
python3 - <<'EOF'
import subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from validate_work import check_reviewed_sources

tmp = tempfile.TemporaryDirectory()
repo = Path(tmp.name) / "refrepo"; repo.mkdir()
def git(*args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()
identity = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgSign=false", "-c", "core.hooksPath=/dev/null")
git("init", "-q", "-b", "main")
git(*identity, "commit", "--allow-empty", "-qm", "base"); base = git("rev-parse", "HEAD")
git(*identity, "commit", "--allow-empty", "-qm", "candidate"); candidate = git("rev-parse", "HEAD")
git("branch", "integration", base)

task = {"id": "demo/W-1", "board": "demo.board", "status": "reviewed",
        "owner_repo": str(repo), "review": {"source": candidate}}
for ref in ("refs/heads/integration", "refs/heads/main"):
    errors, warnings = [], []
    check_reviewed_sources([task], {"demo.board": {"integration_ref": ref}}, errors, warnings)
    print(ref, "errors:", len(errors), "warnings:", len(warnings))
    for line in errors:
        print("  ", line.split(" (" + str(repo))[0])
tmp.cleanup()
EOF
#   refs/heads/integration errors: 1 warnings: 0
#      demo/W-1: reviewed source … is not in the history of refs/heads/integration
#   refs/heads/main errors: 0 warnings: 0
```

```bash
# 4. The suite still passes, including the boundary sweep over validate_work.py.
python3 -m unittest discover -s tests
#   Ran 328 tests in 98.531s
#   OK (skipped=2)
python3 -m unittest tests.test_boundary
#   Ran 19 tests in 0.554s
#   OK (skipped=2)
```

The two skips are `test_boundary.PrivateDenylistTest`, which need `LOOM_BOUNDARY_DENYLIST`; they skip
in this worktree exactly as in a public checkout. No count is asserted here that a command above does
not print.

## Limits

- **No suite test landed with the change.** `tests/` is outside this task's declared write paths
  (`AGENT-GUIDE.md`, `PROTOCOL.md`, `validate_work.py`), so the two acceptance behaviours are proven
  by harness 2 and the board-ref behaviour by harness 3 rather than by a committed test. Harness 2 is
  already shaped as a `test_work.py` case: a follow-up task should lift it in, so a future edit to the
  check cannot quietly stop biting. This is the one thing about this change that review should not
  accept silently.
- **Only `reviewed` is checked.** That is the queue that integrates next, and the state the
  acceptance names. An already-`integrated` receipt proves its own ancestry, and re-checking history
  for `accepted` tasks would re-litigate merges that already happened.
- **A warning, not an error, when the check cannot run.** A missing owner checkout, a
  non-`refs/heads` ref, a missing integration branch, an unreadable object or a malformed receipt
  hash each produce one warning per repository and ref. Those are receipt or checkout problems, not
  proof that the source is off the branch, and a validator that reported them as errors would be
  inventing a verdict.
- **The local ref only.** The check reads `owner_repo`'s local branch, matching the engine; it does
  not consult a remote, which is the engine's documented limit, not a new one.
- **Two registry errors pre-date this change.** `midas/W-8` and `loom/LOOM-14` are missing headings in
  their program documents. LOOM-14 is this task: its registry row exists centrally but
  `~/repositories/loom/docs/work-items.md` has no `## LOOM-14 — …` section, and that file is outside
  this task's write paths. The validator named it before this change and still does.
- **The board's `integration_ref` is unset everywhere in this registry**, so every reported task is
  being judged against the default `refs/heads/main`. When a board configures a different ref, the
  message names that ref instead (harness 3).
