# Evidence — LOOM-4 prove the public boundary with a test

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-4`
Scope: `capabilities/work`

The boundary is now a failing test rather than a paragraph in an evidence file: the suite refuses
engine source that names a program, tracked files that carry a machine path or a personal mailbox,
and tracked files that carry a name the operator guards.

## Delivered

- `capabilities/work/tests/test_boundary.py` — three checks, each with a self-test that proves the
  detector still bites:
  - **source** — AST scan of every shipped module (`engine.py`, `store.py`, `loom`,
    `validate_work.py`, the MCP and A2A servers, the Linear modules) for a task-id literal, a
    comparison against a namespace literal, a regex branch on `<program>/…`, or a
    `--program`/`--namespace`/`--focus` default that names a program.
  - **tree** — every tracked file is checked for an absolute home path (`/Users/<name>`,
    `/home/<name>`, `/root/<name>`, `C:\Users\<name>` in either separator style) and for a mailbox
    outside the reserved example domains; `TreeDetectorTest` proves both detectors on synthetic
    text.
  - **denylist** — `LOOM_BOUNDARY_DENYLIST` points at the operator's list outside this repository;
    the sweep is literal, case-insensitive and whole-token. A test also fails if the list itself
    resolves inside the public checkout.
- `capabilities/work/engine.py` — the case that shipped: `integration-plan` classified
  `receipt_missing` tasks with `re.match` against a private program's id prefix, and printed a
  "legacy" paragraph naming it. The classification is now the whole `receipt_missing` set, and the
  paragraph counts that set. The `create --id` error example is `<program>/W-1`.
- `capabilities/work/mcp/server.py` — the `work_get` task description uses `<program>/W-1`.
- `capabilities/work/tests/test_linear_client.py`, `tests/test_work.py` — a label fixture and a
  reviewer fixture used a program name and a personal handle; now `alpha` and `owner`.
- `capabilities/work/evidence/LOOM-1.md` — redacted. Its verification commands quoted the same
  private names this task now guards; each is described instead of repeated. A note at the top
  records the redaction and its cause.
- `capabilities/work/README.md` — "Public boundary" under "Audit and verify": what fails, and how to
  supply the denylist.

## Red run (before the fixes)

The new test was written first and run against the tree as claimed:

```
python3 -m unittest tests.test_boundary
FAILED (failures=2, skipped=2)
  capabilities/work/engine.py:389  'alpha/W-1'
  capabilities/work/engine.py:2429 '<private>/W0'   <- a private program namespace
  capabilities/work/engine.py:2445 '<private>/W0'
  capabilities/work/mcp/server.py:568 'alpha/W-1'
  capabilities/work/engine.py:2429 re.match('<private>/W0[0-9]', item['task'])
```

That is the task's premise confirmed: the namespace literal shipped, and a path or email check would
not have caught it.

## Green run

```
python3 -m unittest discover -s tests
Ran 232 tests in 70.7s
OK (skipped=2)

python3 validate_work.py
PASS   (warnings only: LOOM-5/6/8/9/10 readiness, and `capabilities/work` is shared with LOOM-5)
```

The two skips are the denylist tests: the list is not in this environment, and a public checkout has
no copy to read. Their mechanism is covered by the two synthetic tests that do run.

## The tests bite (proved on a scratch copy)

A copy of `capabilities/work` with three violations planted — a task-id literal in `engine.py`, an
absolute home path and a personal mailbox in `README.md`:

```
python3 -m unittest tests.test_boundary
FAILED (failures=3)
  engine.py:30      'acmewidget/W-1'
  README.md:465     '/Users/<name>'
  README.md:466     '<name>@<domain>'
```

Each check reported exactly the planted line. The same copy with one guarded name (`acmewidget`)
planted and a two-line denylist outside the copy:

```
LOOM_BOUNDARY_DENYLIST=<outside the checkout> python3 -m unittest tests.test_boundary.PrivateDenylistTest
FAILED (failures=1)
  README.md:468 'acmewidget'
```

`test_the_denylist_lives_outside_the_repository` passed, so the list was accepted from outside the
checkout and would have been refused from inside it.

## Review round two — the Windows branch repaired

Review of `2086122` requested changes: the Windows half of the tree scan did not match a real
home path. `WINDOWS_HOME` required *two literal backslashes* after the drive letter and two after
the directory, so only a JSON-escaped string hit; `C:\Users\<name>`, `C:/Users/<name>` and
`D:\home\<name>` all passed the scan clean. Acceptance criterion 2 was met for POSIX only, and the
docstring's "a refactor cannot quietly turn them into no-ops" was untrue for the tree detectors,
which had no synthetic test.

Fixed in `d27dfd2`:

- `WINDOWS_HOME` now accepts one or two separators of either kind,
  `[A-Za-z]:[\\/]{1,2}(?:Users|home|root)[\\/]{1,2}[A-Za-z0-9._-]+`.
- `TreeDetectorTest` exercises `home_paths` and `personal_emails` on synthetic text: POSIX
  homes, Windows homes with `\`, `/` and escaped `\\`, relative/placeholder paths that must not
  match, and reserved versus reachable mailboxes. The fixtures are assembled from parts because
  this file is itself tracked and scanned.

The detector before and after, on the reviewer's three examples:

```
'C:\\Users\\<name>' old: False new: True
'C:/Users/<name>'   old: False new: True
'D:\\home\\<name>'  old: False new: True
```

The repaired scan re-ran over every tracked file and found no Windows home path, so the change
exposes no new leak.

```
python3 -m unittest tests.test_boundary -v
Ran 13 tests ... OK (skipped=2)

python3 -m unittest discover -s tests
Ran 235 tests in 73.5s
OK (skipped=2)

python3 validate_work.py
PASS
```

## Acceptance

| Criterion | Where it is proved |
|---|---|
| A structural test fails on a literal program-namespace id or a branch on one | `EngineSourceTest.test_engine_source_carries_no_task_id_literal`, `…_does_not_branch_on_a_namespace`; red run above |
| A test fails on an absolute home path or a personal email in tracked files | `TrackedTreeTest`; `TreeDetectorTest` for both separator styles and for reserved versus reachable mailboxes; scratch run above |
| The private denylist runs without the list entering the public repo | `PrivateDenylistTest`: env-var path, outside-repo assertion, skip-with-pointer, synthetic mechanism tests |

## Private denylist, against this checkout

Supplied with the operator's list (six entries: this portfolio's program names, a personal handle
and two private repository slugs) outside the checkout:

```
entries=6 hits=3
  README.md line 24
  docs/adopting.md line 8
  docs/adr/0001-state-leaves-git.md line 53
```

Zero hits inside `capabilities/work` — the engine, its tests, its docs and its evidence are clean.
The three remaining hits are outside this task's write path, so they are recorded below rather than
fixed here. With the list absent (the default in a public checkout) the sweep skips; supplied, it
reports them and fails.

## Limits

- **Three guarded names survive outside the write path.** `README.md:24` and `docs/adopting.md:8`
  are the clone URL, which carries a personal GitHub handle; `docs/adr/0001-state-leaves-git.md:53`
  names a private repository slug. Fixing them needs a `README.md`/`docs/` write path, or a
  follow-up task that owns those files. The sweep will stay red until then.
- **The source scan covers `capabilities/work` Python only.** Shell hooks (`.githooks/`,
  `capabilities/work/hooks/`) and templates outside it are covered by the tracked-tree checks, which
  catch paths, mailboxes and denylisted names but cannot see a namespace branch.
- **Option defaults** are checked for `--program`, `--namespace` and `--focus`. An option invented
  under another name could still default to a named program; the literal and branch scans would only
  catch it if the value looked like a task id.
- **`evidence/LOOM-1.md` was edited** by this task. The redaction is described at the top of that
  file; no claim in it changed, only the quoted names.

## Reproduce

```bash
cd capabilities/work
python3 -m unittest tests.test_boundary -v
LOOM_BOUNDARY_DENYLIST=~/.config/loom/boundary-denylist.txt \
  python3 -m unittest tests.test_boundary.PrivateDenylistTest -v
python3 -m unittest discover -s tests
python3 validate_work.py
```
