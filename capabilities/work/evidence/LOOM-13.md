# Evidence — LOOM-13 make bodies carry no state

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-13-a1` (isolated worktree)
Change under review: `b9d387e` (the test and the body). The evidence commit that follows it on this
branch changes only this file.
Status: green, submitted for independent review

This file states the claim at the commit above and nothing else. The review history is in the
registry event log (`./loom events | grep loom/LOOM-13`), which is the audit record; duplicating it
here is what kept the LOOM-2 evidence wrong. Commands below are runnable as written from
`capabilities/work` unless they say otherwise.

## Delivered

`capabilities/work/tests/test_body_claims.py` — the rule as a suite test, in the shape of
`test_boundary.py`:

- **Status claim.** A `Status`/`State` label (`**Status.** Reviewed.`, `Status: integrated`,
  `- State: claimed`) or a line that is nothing but a registry status (`Reviewed.`). The vocabulary
  is read from `schemas/task.schema.json`, so the detector cannot drift from the statuses the
  registry accepts.
- **Dependency claim.** A `Depends on`/`Dependencies`/`Blocked by`/`Blocks` label, or a dependency
  phrase that names a task (`… depends on LOOM-5`). A bare reference (`See LOOM-5 …`) is allowed:
  it asserts no relation.
- **Fixtures.** Every detector is exercised on synthetic sections, so a detector that stopped
  biting fails a test instead of looking like a clean body. The precedence is `test_boundary.py`,
  where review of `eb3deed` found a never-matching pattern that the suite could not see.
- **The document itself.** `docs/work-items.md` is parsed into sections; no section may carry either
  claim, every section keeps a problem statement, and every `**Acceptance.**` keeps criteria under
  it.

`docs/work-items.md`:

- The `**Status.**` line is gone from all twelve sections. Those twelve lines carried twelve status
  claims and five dependency sentences.
- LOOM-1, LOOM-2 and LOOM-3 had only a status line; they gain a problem statement transcribed from
  the registry's own `goal` for each. Nothing was invented: none of the three defines an
  `acceptance` in the registry, so none gains one.
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
- **`docs/work-items.md` is LOOM-2's datapoint, not its fix.** LOOM-2's evidence recorded the same
  drift and recommended a coordinator reconcile. This task supersedes that recommendation for this
  board: the lines are gone rather than corrected, and `loom reconcile` never applied to a
  markdown program board (it refuses a non-packet board, `engine.py:cmd_reconcile`).
