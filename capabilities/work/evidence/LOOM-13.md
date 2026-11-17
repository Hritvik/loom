# Evidence — LOOM-13 make bodies carry no state

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-13-a5` (isolated worktree)
Change under review: `22100c3..c9ba888` — the body and the first version of the test landed at
`b9d387e`; `d347430` was the rework after the first review, and `c9ba888` is the rework after the
second. The evidence commit that follows it on this branch changes only this file.
Status: green, submitted for independent review

This file states the claim at that range and nothing else. The review history is in the registry
event log (`./loom events | grep loom/LOOM-13`), which is the audit record; duplicating it here is
what kept the LOOM-2 evidence wrong. Commands below are runnable as written from `capabilities/work`
unless they say otherwise.

## Delivered

`capabilities/work/tests/test_body_claims.py` — the rule as a suite test, in the shape of
`test_boundary.py`:

- **Status claim.** A `Status`/`State` label (`**Status.** Reviewed.`, `Status: integrated`,
  `- State: claimed`, a `| Status | Ready |` table row) or a line that is nothing but a registry
  status (`Reviewed.`). The vocabulary is read from `schemas/task.schema.json`, so the detector
  cannot drift from the statuses the registry accepts.
- **Dependency claim.** A `Depends on`/`Dependencies`/`Blocked by`/`Blocks` label, a
  `| Dependencies | LOOM-4 |` table row, or a dependency phrase that names a task
  (`… depends on LOOM-5`). A bare reference (`See LOOM-5 …`) is allowed: it asserts no relation.
- **Fixtures.** Every detector is exercised on synthetic sections, so a detector that stopped
  biting fails a test instead of looking like a clean body. The precedence is `test_boundary.py`,
  where review of `eb3deed` found a never-matching pattern that the suite could not see.
  `| Dependencies | LOOM-4 |` on its own does not satisfy that: the phrase and reference detectors
  catch it too, so `DEPENDENCY_ROW` could have been replaced with a never-matching pattern without
  the suite noticing, which review of `450439c` measured. The row detector's fixture is therefore a
  row with no task reference, `| Dependencies | none |`, whose test also asserts that no reference
  is present — so the fixture keeps proving the detector rather than the words in it.
- **No portfolio identity in the fixtures.** LOOM-1's goal forbids the engine naming this
  portfolio's programs, maps or identifiers, and a synthetic fixture is no exception: the task-id
  examples are `LOOM-5` and the `demo/` namespace the tests already use.
- **No count that expires.** The module docstring said the validator named "every one of the eight"
  disagreeing sections. That count was already stale, and a count that expires on the next merge is
  the LOOM-12 failure mode, so it says "every section that disagreed" instead.
- **The document itself.** `docs/work-items.md` is parsed into sections; no section may carry either
  claim, every section keeps at least one prose line before any `**Acceptance.**`, and every
  `**Acceptance.**` keeps criteria under it. The problem-statement check cuts the section at its
  acceptance instead of skipping only the acceptance line, and is itself exercised on an
  acceptance-only section: a filter that counted acceptance continuation lines as prose passed on a
  section with no problem statement at all, which review of `9f5d540` found.

`docs/work-items.md`:

- The `**Status.**` line is gone from all twelve sections. Those twelve lines carried twelve status
  claims and five dependency sentences.
- LOOM-1, LOOM-2 and LOOM-3 had only a status line; they gain a problem statement transcribed from
  the registry's own `goal` for each. Nothing was invented: none of the three defines an
  `acceptance` in the registry, so none gains one. LOOM-2's goal says `artefact`; its body says
  `artifact`, which is the spelling the rest of this file uses.
- LOOM-13 gains its section. `validate_work.py` reported
  `ERROR loom/LOOM-13: heading LOOM-13 missing from loom.program` at the merge base, and that error
  is this task's to close.

## Verification

```bash
# capabilities/work — the suite, and this file's own tests
python3 -m unittest discover -s tests
python3 -m unittest tests.test_body_claims -v

# the detector bites, on the body exactly as it was at the merge base
git -C ../.. show 22100c3:docs/work-items.md \
  | python3 -c "import sys; sys.path.insert(0,'tests'); import test_body_claims as t; c=t.body_claims(sys.stdin.read()); print(len(c), 'claims:', sorted({x.kind for x in c}))"
#   prints 17 claims: ['dependency', 'status']

# the body as it is now
python3 -c "import sys; sys.path.insert(0,'tests'); import test_body_claims as t; print(len(t.body_claims(open('../../docs/work-items.md').read())), 'claims')"
#   prints 0 claims

# every section has prose before its acceptance; the least of them
python3 - <<'EOF'
import sys; sys.path.insert(0, "tests")
import test_body_claims as t
text = t.BODY.read_text(encoding="utf-8")
print("least prose lines:", min(len(t.problem_statement(lines)) for _, lines in t.sections(text)))
EOF
#   prints least prose lines: 1

# the problem-statement check bites: delete LOOM-7's prose from a copy and run that test alone
python3 - <<'EOF'
import pathlib, sys, tempfile, unittest
sys.path.insert(0, "tests")
import test_body_claims as t
text = t.BODY.read_text(encoding="utf-8")
breaking = text.replace(
    "A receipt records the paths and blobs it covered, so integration verifies exact content instead of\n"
    "re-diffing a history that may have moved.\n", "")
assert breaking != text, "the LOOM-7 prose was not found; this check no longer tests anything"
with tempfile.TemporaryDirectory() as tmp:
    copy = pathlib.Path(tmp, "work-items.md"); copy.write_text(breaking, encoding="utf-8")
    t.BODY = copy
    result = unittest.TextTestRunner(verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromName(
            "test_body_claims.WorkItemsBodyTest.test_every_section_keeps_its_problem_statement"))
print("failures:", len(result.failures), "errors:", len(result.errors))
EOF
#   prints failures: 1 errors: 0

# the table shape a status claim is also caught (the fixtures cover it; this is the detector)
python3 -c "import sys; sys.path.insert(0,'tests'); import test_body_claims as t; print(t.body_claims('## X\n\n| Status | Ready |\n\n| Dependencies | LOOM-4 |\n'))"
#   prints two claims: status and dependency

# every detector is load-bearing: replace one with a never-matching pattern and the suite fails.
# The last line is the fix this rework makes — at 450439c it printed failures=0.
python3 - <<'EOF'
import re, sys, unittest
sys.path.insert(0, "tests")
import test_body_claims as t
for name in ("STATUS_LABEL", "STATUS_ROW", "DEPENDENCY_LABEL", "DEPENDENCY_ROW",
             "DEPENDENCY_PHRASE", "TASK_REFERENCE"):
    original = getattr(t, name); setattr(t, name, re.compile(r"(?!x)x"))
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(t.ClaimDetectorTest)
    result = unittest.TextTestRunner(verbosity=0, stream=open("/dev/null", "w")).run(suite)
    setattr(t, name, original)
    print(f"{name:20s} failures={len(result.failures)} errors={len(result.errors)}")
EOF
#   prints, in order: STATUS_LABEL 5, STATUS_ROW 1, DEPENDENCY_LABEL 1, DEPENDENCY_ROW 1,
#   DEPENDENCY_PHRASE 4, TASK_REFERENCE 6 — no detector has failure count 0

# this range adds no portfolio program name and no personal handle to the tracked tree. The sweep is
# LOOM-1's own, and its guarded names are read from that file and from the operator denylist rather
# than copied here: a file that copies them trips the sweep it is running. Both lines must list the
# same five paths.
python3 - <<'EOF'
import re, subprocess
source = open("evidence/LOOM-1.md", encoding="utf-8").read()
pattern = re.search(r'git grep -n -I -E "([^"]+)"', source).group(1)
for ref in ("22100c3", "HEAD"):
    out = subprocess.run(["git", "-C", "../..", "grep", "-l", "-I", "-E", pattern, ref],
                         capture_output=True, text=True).stdout.split()
    print(ref, sorted(path.split(":", 1)[1] for path in out))
EOF
#   both refs print README.md, capabilities/work/decisions/published-history-001.md,
#   capabilities/work/evidence/LOOM-1.md, docs/adopting.md and docs/adr/0001-state-leaves-git.md.
#   At 450439c this range had a sixth: the test file. It is gone.

# what validate_work.py will read from this file, using its own parsers
python3 - <<'EOF'
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("v", "validate_work.py")
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
text = Path("../../docs/work-items.md").read_text(encoding="utf-8")
print("program_statuses:", v.program_statuses(text))      # {} — no drift warning can name this board
print("LOOM-13 heading:", "LOOM-13" in v.program_task_ids(text))
EOF

# nothing inside a section was lost: every prose line at the base is still there,
# and the acceptance paragraphs are identical apart from LOOM-13's new one
git -C ../.. show 22100c3:docs/work-items.md | awk '/^## /{f=1} f' \
  | grep -v '^#' | grep -v '^\*\*Status\.\*\*' | grep -v '^$' \
  | while read -r line; do grep -qxF -- "$line" ../../docs/work-items.md || echo "MISSING: $line"; done
#   prints nothing
diff <(git -C ../.. show 22100c3:docs/work-items.md | sed -n '/^\*\*Acceptance\.\*\*/,/^$/p') \
     <(sed -n '/^\*\*Acceptance\.\*\*/,/^$/p' ../../docs/work-items.md)
#   prints only the added LOOM-13 acceptance block, and exits 1 because the diff is not empty
```

No count is asserted here that the command above does not print. The two skips in the suite are
`test_boundary.PrivateDenylistTest`, which need `LOOM_BOUNDARY_DENYLIST`; they are skipped in this
worktree exactly as in a public checkout.

## The five removed edges already exist in the registry

The dependency sentences deleted from the bodies were redundant, not the only copy:

| Body said | Registry records |
|---|---|
| LOOM-6 depends on LOOM-5 | `loom/LOOM-6` deps: `loom/LOOM-5` |
| LOOM-8 depends on LOOM-5 | `loom/LOOM-8` deps: `loom/LOOM-5` |
| LOOM-9 depends on LOOM-8 | `loom/LOOM-9` deps: `loom/LOOM-8` |
| LOOM-10 depends on LOOM-5 and LOOM-9 | `loom/LOOM-10` deps: `loom/LOOM-5`, `loom/LOOM-9` |
| LOOM-12 depends on LOOM-4 | `loom/LOOM-12` deps: `loom/LOOM-4` |

Re-derive with `./loom show <task> | grep deps` from the work home. Readiness is computed from
these edges; it was never computed from the sentences.

## Limits

- **The validator ran elsewhere.** `validate_work.py` reads each board at the path in the registry,
  which is `~/repositories/loom`, so it cannot be pointed at this worktree. What is verified here is
  the two parsers it uses, applied to this tree's file (`program_statuses` returns `{}` and
  `program_task_ids` finds the `LOOM-13` heading). After merge, the `registry says X, program says Y`
  warnings for this board and the `LOOM-13` heading error should disappear; the warnings that remain
  belong to the shared registry, not to this diff, and their list is
  `python3 validate_work.py | grep '^WARN'` after the merge.
- **The convention survives in the engine, deliberately.** `validate_work.py:program_statuses`,
  `examples/walkthrough/walkthrough.py` and two `test_work.py` fixtures still write `**Status.**`
  lines: a packet-style program board may carry them, and its drift warning is unchanged. This task
  removes the duplicate from *this* program's board, not the ability to have one. Changing the
  validator is outside this task's write paths and outside its argument.
- **The structural test cannot require what the registry does not define.** It requires a problem
  statement of every section and criteria under every acceptance that is present. LOOM-1, LOOM-2
  and LOOM-3 have no `acceptance` in the registry, so their sections state a problem and no
  acceptance; a test that demanded one would be inventing a requirement.
- **The detector is line-based.** A claim split across a line break in the middle of a sentence is
  caught only when the label or the phrase survives the break. Prose that wraps onto the word
  `dependency` is not a claim (tested), which was a real false positive before the `after`
  punctuation requirement — a sentence of this very acceptance tripped it.
- **The boundary sweep matches files this task did not write.** LOOM-1's evidence, its published
  history, the public clone URL docs and the ADR that records the limit all name what LOOM-1
  removed; they are outside this task's write paths and unchanged by it. What this range controls is
  that it adds no match of its own: the sweep returns the same five tracked paths at the merge base
  and at this tip, where `450439c` had a sixth — the test file.
- **`docs/work-items.md` is LOOM-2's datapoint, not its fix.** LOOM-2's evidence recorded the same
  drift and recommended a coordinator reconcile. This task supersedes that recommendation for this
  board: the lines are gone rather than corrected, and `loom reconcile` never applied to a
  markdown program board (it refuses a non-packet board, `engine.py:cmd_reconcile`).
