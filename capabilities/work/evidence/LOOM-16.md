# Evidence — LOOM-16 retire a task or a board with a tombstone

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-16-a5` (isolated worktree, rebased onto `main` at `c3c2742`)
Change under review: `c3c2742` to the tip of `loom/loom-loom-16-a5` — the feature commit `43221d4`,
the evidence commit `c1cd85a`, the rework commit `a29884d`, and the commit that carries this file,
which changes only `capabilities/work/evidence/LOOM-16.md`.
Status: approved at review 3131 on a base that has since moved; rebased onto current `main` at the
portfolio coordinator's request (event 3200) and resubmitted for review of the integration
candidate.

This file states what the range delivers and how to re-check it. Review history belongs in the
registry event log (`./loom events --task loom/LOOM-16`), not here.

## Why deletion had to be an event

The registry is a projection of a hash-chained log: `loom foldcheck` replays the `state_delta` each
event carries and names every field where the registry disagrees with the fold. A `delete` that
removed rows would pass for an ordinary read and then break `foldcheck` forever, which is why
"remove work" had no implementation at all.

The store already understood deletion inside a delta — `apply_state_delta` has handled
`{"id": …, "deleted": true}` since the fold landed — but nothing emitted it. `retire_documents()`
drops the documents from the registry in memory and lets the store's existing end-of-transaction
delta capture the removal. There is no new store code, no hand-written delta, and no log rewrite:
the new event is appended like any other and the chain still verifies.

`delete` is also careful not to assert a status. `validate_work.py` and
`engine.verify_registry_events` treat an event that carries `status` as the last word on a task's
state, so a status-bearing tombstone would be reported as "task X no longer in registry" the moment
it worked. The event records `prior_status` instead: what was deleted, without claiming the task
still has a state.

## Delivered

`capabilities/work/engine.py`:

- **`cmd_delete`** — `loom delete <task> --reason <text>` retires one task;
  `loom delete --board <id> --reason <text>` retires every task on the board in one event.
  Exactly one target is required, and the reason cannot be blank.
- **`delete_guard`** — refuses with the offending ids named: a task whose lease is live
  (`active_claim`), and a task with a dependent (edges and `depends_on`, via `dependents_index`)
  that is not retired by the same command. For a board, "the same command" is the whole board, so
  intra-board dependencies are retired together rather than blocking each other.
- **`retire_documents`** — removes the task documents, every edge with the task at either end, and
  every legacy shared-artifact claim that named the task. The last two are not cosmetic: a dangling
  edge or a claim pointing at a missing task is an `ERROR` in the validator, and there is no CLI
  command that edits claims, so leaving them would make deletion impossible rather than safe.
- **`tombstone_paths`** — the evidence file and the body document (program markdown or packet
  board JSON), printed and never touched. Acceptance says the command prints the paths a human
  should remove; this is that list.
- **`cmd_events`** — `--task` now also matches the `tasks` list a tombstone carries, so the one
  event that retired a board is addressable under each task it retired. The match requires a list,
  because the genesis event's `tasks` is a count.
- `board remove` now says `retire them first with loom delete --board <id> --reason ...` instead of
  the dead-end "move or delete them first".
- `delete` is in `ENGINE_COMMANDS` and `build_parser()`, so `loom` routes it and `loom --help`
  lists it; the MCP and A2A surfaces are unchanged (no new tool, no new action).

The event shape, for a single task:

```json
{"event": "delete", "agent": "opencode/loom-worker", "reason": "…",
 "tasks": ["demo/W-3"], "task": "demo/W-3", "board": "demo.board",
 "prior_status": {"demo/W-3": "draft"}, "count": 1,
 "kept_evidence": ["/repo/evidence/W-3.md"], "kept_bodies": ["/repo/docs/board.json"],
 "state_delta": {"tasks": [{"id": "demo/W-3", "deleted": true}], "edges": {…}}}
```

For a board: `deleted_board` replaces `task`/`board`, `tasks` lists every id retired, and one
`state_delta` carries every deletion. `tasks` is present in both shapes so the tombstone reader has
one rule.

`capabilities/work/validate_work.py`:

- **`tombstones()`** reads the `delete` events, in order, into retired task ids, retired board ids
  and `(board, suffix)` packet keys. **Rework:** a later `board add` for a retired id clears that
  board and its packet keys, and a later `create` for a retired task id clears that task. The log
  is ordered, and the last word on whether work is live is the registration, not the tombstone.
  Without this the retirement stayed in force for the rest of the log: a board added back had its
  packets reported as missing although present, its headings reported as missing, and a body that
  had disappeared after the re-add passed unnoticed.
- A retired board is not read from disk. That is the MyFinance case: the checkout is gone on
  purpose, so `missing <path>` is no longer reported for it, and its tombstoned tasks are not
  reported as missing headings.
- A deleted packet is not reported as "not registered centrally" (the packet document survives in
  the board JSON by design, because the engine never edits content).
- A retired task is not reported as "task X no longer in registry" if any status-bearing event
  referred to it.

`capabilities/work/tests/test_delete.py` — the module collects
<!-- loom:tests.count:tests/test_delete.py -->14<!-- /loom --> tests on a disposable work home:

- a deleted task leaves `list`, `graph`, `export`, `search` and `show`, while `events --verify`,
  `foldcheck` and `validate_work.py` all pass;
- the tombstone records agent, reason, `prior_status` and `count`, and carries no `status`;
- deleting a packet task stops the validator reporting its leftover packet;
- edges and task-scoped claims are dropped, and the validator is clean afterwards;
- a live claim refuses the delete and names `demo/W-1 (agent-a)`;
- a dependent refuses the delete and names `demo/W-2`;
- exactly one target and a nonblank reason, an unknown task, and the `--reason` requirement;
- `delete --board` retires four tasks in one event, drops every edge, then `board remove` succeeds
  and the suite stays green (`foldcheck` + validator); the tombstone is also visible from
  `events --task demo/W-2`, one of the tasks it retired;
- a board whose checkout was never created produces `missing …` and `heading … missing` errors,
  `delete --board` clears them, and `board remove` finishes the job;
- a re-added packet board is validated again: its present packet is not reported missing;
- a re-added program board is validated again: its present heading is not reported missing;
- a re-added board whose body then disappears is reported `missing` (it passed before the rework);
- a board with no tasks left points at `loom board remove`;
- an evidence file and the board file both survive the delete and are printed.

`capabilities/work/README.md` — one CLI table row, because
`tests/test_surface.py:test_work_readme_documents_every_cli_command` requires every parser
subcommand to be documented. See Limits.

## Rebase onto current `main`

Review 3131 approved a range cut against `7df7b16`, and `engine.py` has moved since: `main` now
carries LOOM-12's evidence-claim machinery (511 lines appended after `verify_registry_events`) and
LOOM-17's reviewed-source and fork-point changes. Integrating the old blob would have landed code no
review had seen, so the four commits were replayed onto `c3c2742`.

The replay was textually clean, which is not the same as reviewed. What the rebase changes, and what
a reviewer should read:

- `cmd_delete`, `delete_guard`, `retire_documents`, `tombstone_paths` and the `events --task` match
  are unchanged, and they still sit before the region LOOM-12 appended to; no moved code touches
  them.
- `delete` is still in `ENGINE_COMMANDS` and in `build_parser()` after `board`, so the routing and
  the README row that `tests/test_surface.py` requires are unchanged.
- `validate_work.py` and `tests/test_delete.py` do not overlap the rebased commits at all.
- LOOM-12's derivations read this file. Its one machine-checked span is
  `tests.count:tests/test_delete.py`; it re-derives to the recorded value, so the rebase did not
  leave a stale receipt behind. From `capabilities/work`:

  ```bash
  python3 -c "import engine; from pathlib import Path; \
      print(engine.check_evidence_file(Path('evidence/LOOM-16.md'), root=Path('.'))['ok'])"
  #   True
  ```

The whole suite on the rebased tip is green (`OK (skipped=2)`), and `loom foldcheck` and the
validator are still exercised through `test_delete.py` as before.

The replay is a straight four-commit sequence on top of `c3c2742`, so integrating the branch is a
fast-forward to its tip. The tip the reviewer must read is the commit this file arrives in; that is
also the `--review-source` in the review request. The review base is `c3c2742`, the fork point from
`main` — not this file's parent, because a docs-only tip as the base would leave the receipt
approving only this file while the merge lands the other four paths.

## Verification

```bash
# capabilities/work — the whole suite on this tip
python3 -m unittest discover -s tests
#   OK (skipped=2); the two skips are test_boundary.PrivateDenylistTest, which need
#   LOOM_BOUNDARY_DENYLIST
python3 -m unittest discover -s tests -p test_delete.py
#   Ran 14 tests — OK
```

The rework is only worth believing if the tests fail without it. Against the first feature commit's
`validate_work.py` (`43221d4`) the same three new tests fail (packet missing, heading missing, and a
missing body passing), so they pin the defect and not the shape of the fix.

The MyFinance shape, end to end on a disposable home (a program board whose checkout does not
exist, with one rejected task). Reproduce from `capabilities/work`:

```bash
python3 - <<'EOF'
import sys
sys.path.insert(0, "tests")
from test_work import Workspace, run, validate, T0

ws = Workspace()
gone = ws.root / "gone"          # never created: the checkout was deleted
ws.registry["boards"].append({"id": "cal.program", "kind": "program", "role": "provider",
                              "repo": str(gone), "path": "work-items.md"})
ws.registry["tasks"].append({"id": "cal/1", "board": "cal.program", "program": "cal",
                             "owner_repo": str(gone), "title": "Rejected thing",
                             "status": "rejected", "revision": 4})
ws.save()
print(validate(ws.root).stdout.splitlines()[-4:])
for args in (["delete", "--board", "cal.program", "--reason", "checkout deleted"],
             ["board", "remove", "--board", "cal.program"]):
    r = run("--root", str(ws.root), *args, "--agent", "opencode/loom-worker", "--now", T0)
    print(r.returncode, r.stdout)
print(validate(ws.root).stdout.splitlines()[-2:])
ws.close()
EOF
#   prints, before (the WARN is the disposable home having no fold baseline):
#     WARN  no fold baseline; run `loom snapshot` so foldcheck can detect a field written without an event
#     ERROR cal.program: missing /tmp/…/gone/work-items.md
#     ERROR cal/1: heading 1 missing from cal.program
#     FAIL (2 errors, 2 warnings)
#   then:
#     deleted board cal.program (1 task)
#       bodies kept:   /tmp/…/gone/work-items.md
#       files are never removed for you; delete them by hand
#       next: loom board remove --board cal.program --agent opencode/loom-worker
#     0 removed board cal.program
#   and after:
#     WARN  no fold baseline; …
#     PASS
```

The rework, on a disposable home (`delete --board`, `board remove`, `board add` the same id,
`create` a task, then validate). Reproduce from `capabilities/work`:

```bash
python3 -m unittest discover -s tests -p test_delete.py
#   test_a_packet_board_added_back_after_its_tombstone_is_validated_again ... ok
#   test_a_program_board_added_back_after_its_tombstone_is_validated_again ... ok
#   test_a_board_added_back_whose_body_disappears_is_reported_missing ... ok
```

A refusal, with the ids named (`exit 2`):

```text
error: refusing to delete demo/W-1: live claim held by demo/W-1 (agent-a); dependents not also retired: demo/W-2
```

The live registry was re-read with `main`'s engine and this one, from the same store: both print the
same `FAIL (n errors, n warnings)` tally and the same `ERROR` lines, so this range adds no validator
finding to the shared home. Those failures are pre-existing receipts whose commits are not on their
integration branches, plus headings missing from shared board bodies that this task's write paths do
not include — including `loom/LOOM-16`'s own, because its body belongs to the planning repository
and `delete` neither creates nor requires one. The shared home holds no `delete` event, so the
ordering change cannot alter its output:

```bash
# both the main checkout and this worktree, from capabilities/work
python3 validate_work.py 2>&1 | tail -1
#   FAIL (the same tally and the same ERROR lines from both)
```

## Limits

- **README is outside the claimed write paths.** `tests/test_surface.py` makes "delete is in the
  parser" and "delete is in the README table" the same condition, so the suite cannot pass without
  the row. The pre-commit guard classes `capabilities/work/` as shared and allowed the commit, but
  the claim's write paths did not list `README.md`. One table row was added at `43221d4` and the
  coordinator has been asked to ratify or reject it (event 3041, asked
  `opencode/loom-coordinator`). Reverting it means re-scoping the surface contract, which is not
  this task's to accept.
- **The evidence locator resolves against the work home.** In this branch the file is at
  `capabilities/work/evidence/LOOM-16.md`; the running work home is a deployed copy, so the registry
  artifact resolves only once the branch is integrated or the copy is placed there. The reviewer
  asked for the copy (review 3054, non-blocking b) and it is a content file, not a repository write.
- **A retired board is two steps.** `delete --board` retires the work, as acceptance asks, and
  leaves the board document registered so `board remove` can drop it. Between the two the board
  still lists in `loom board list`; after `delete --board` the validator no longer reads its file.
- **Re-registration is authoritative.** `board add` for a retired id is a supported way to bring a
  board back, so the validator validates it again and reports a missing body. A task id can also be
  created again after a delete (the registry no longer holds it), and the tombstone reader clears
  that id's retirement when it sees the `create`. This is a policy the rework chose over refusing a
  tombstoned id: refusing would add a second command's worth of state to every add, and the ordered
  log already says what the operator meant.
- **Files are never removed.** Evidence and body documents survive every delete and are printed.
  That includes the program heading: a deleted task's `## W-8 — …` section stays in the markdown
  until a human edits it, and the validator does not report an unregistered heading.
- **A retired board's packets stay in the board JSON.** `loom board import` would register a
  tombstoned packet again: import reads the board, not the tombstones. Not changed here — import
  is adoption of a board's own claim, and a reviewer may reasonably want the tombstone to win.
- **"Live claim" means an unexpired lease on an active status.** A claim whose lease has expired is
  not authority (AGENT-GUIDE, "Rules that are enforced"), so a task that still carries one can be
  deleted. A worker that is somehow still running is then fenced by "unknown task" on its next
  write; the delete does not scan for a live process.
- **Task-scoped shared-artifact claims are dropped, not refused.** Refusing would leave no way to
  delete a task that ever carried one, because no CLI command edits `registry["claims"]`.
- **The validator result is prose on purpose.** The shared registry is not in this checkout; the
  counts above depend on it and a committed evidence file must not machine-check them.
