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
    comparison against a namespace literal, a prefix branch on `<program>/` (a `.startswith`, a
    `re.match`, or `id.split("/")[0]` compared), a regex branch on `<program>/…`, or a
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

## Review round three — the source detectors proved, prefix branches caught

Review of `eb3deed` accepted criteria 2 and 3 and found two gaps in criterion 1:

1. The file claimed the detectors were exercised on synthetic source, but only the tree detectors
   had such a test. Replacing `TASK_ID_LITERAL` with a never-matching pattern and planting a real
   `acmewidget/W-1` in `engine.py` left the suite green: the source detectors could become no-ops
   under a real leak, the same defect `d27dfd2` fixed for the tree detectors.
2. A branch on the namespace *prefix* was missed. `task["id"].startswith("acmewidget/")`,
   `re.match(r"^acmewidget/", task["id"])` and `task["id"].split("/")[0] == "acmewidget"` each name
   a program without spelling a whole task id, and all three passed.

Fixed in `6ee4396`:

- `SourceDetectorTest` exercises all three source detectors on synthetic source: a task id is found;
  placeholders, agent identities and mime types are not; every prefix-branch shape is found; generic
  task-id code (`id.split("/", 1)[0] + "/"`, `re.fullmatch(r"[a-z][a-z0-9.-]{2,63}", program)`,
  `id.startswith("W-")` — a task-name prefix, not a namespace) is not; an option default names a
  program while a required option does not.
- `namespace_branches` now treats a task-id expression as a namespace source. It flags
  `.startswith`/`.endswith`/`.split`/`.partition`/`.removeprefix` on an id (or on the namespace taken
  from an id) when the literal names a namespace, a comparison between such an expression and a
  namespace literal, `"…/" in task["id"]`, and `re.match(r"^…/", id)`.

The no-op control, on a clean copy with `TASK_ID_LITERAL` replaced by `r"(?!)"` and nothing planted:

```
python3 -m unittest tests.test_boundary
FAILED (failures=1, skipped=2)
  test_namespaced_literals_finds_a_task_id: [] != ['acmewidget/W-1']
```

The reviewer's three examples plus a full task-id literal, planted in `engine.py` of a scratch copy:

```
python3 -m unittest tests.test_boundary.EngineSourceTest
FAILED (failures=2)
  engine.py:3869  'acmewidget/W-1'
  engine.py:3871  task['id'].startswith('acmewidget/')
  engine.py:3873  re.match('^acmewidget/', task['id'])
  engine.py:3875  task['id'].split('/')[0] == 'acmewidget'
```

`names_a_namespace`, the literal test behind the prefix branch, on namespace and generic literals:

```
True   'acmewidget/'   True  '^acmewidget/'   True   'acmewidget'
False  '/'             False '[a-z][a-z0-9.-]{2,63}'
False  '^[a-z][a-z0-9-]{1,31}/[A-Za-z0-9._-]+$'
```

`'capabilities/work'` reads as a namespace too, which is why a prefix literal is only judged when it
is compared against an expression that holds a task id; the generic patterns above are not.

```
python3 -m unittest tests.test_boundary -v
Ran 19 tests ... OK (skipped=2)

python3 -m unittest discover -s tests
Ran 270 tests in 85.7s (skipped=2) at 47c2715
OK (skipped=2)

python3 validate_work.py
PASS
```

## Acceptance

| Criterion | Where it is proved |
|---|---|
| A structural test fails on a literal program-namespace id or a branch on one | `EngineSourceTest.test_engine_source_carries_no_task_id_literal`, `…_does_not_branch_on_a_namespace`; `SourceDetectorTest` proves both detectors on synthetic source, including prefix-only branches; red run and no-op control above |
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
- **The branch scan follows an id one step.** It sees a literal compared against a task id, against
  the namespace taken from one (`id.split("/")[0]`), or used as a prefix/regex/membership test on
  one. A namespace copied into a variable and compared later, a pattern built by concatenation
  (`re.match(r"^" + name + "/", id)`), and a module-level compiled pattern applied as
  `pattern.match(id)` are generic code that the scan cannot tell from correct code.
- **The literal scan does not catch a bare namespace.** `namespaced_literals` matches a whole
  task-id literal (`<namespace>/<NAME>`), not a namespace on its own, because a bare short name is
  indistinguishable from an ordinary word. So the most plausible way this binding returns is
  invisible to it: a module-level collection of program names — `PROGRAMS = ("alpha", "beta")` — plus
  `if task["id"].split("/")[0] in PROGRAMS:` produces no hit from any of the three detectors, and
  neither does `re.match(r"^" + name + "/", task_id)` built from a local. Catching that needs the
  denylist-supplied sweep (`LOOM_BOUNDARY_DENYLIST`), which is a tracked-tree check and therefore
  cannot see a branch either, or a scan that is told the names. Treat the branch scan as a
  tripwire for the direct shape, not as proof of absence.
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

The tree under review is `47c2715`. Earlier rounds quote pre-rebase hashes (`d27dfd2`, `eb3deed`,
`6ee4396`, `2086122`): they still resolve in the object database but are no longer on the branch, so
map a round by its description rather than its hash.
