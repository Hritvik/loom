# Evidence — LOOM-3 adopting guide and worked example

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-3-a1` (attempt `loom/LOOM-3#a1`, epoch 5)
Base: `main` at `22100c3`. Head under review: `d839a20b1781546e0309cbdf85a6006814c9e375`.

This revision is the work already approved at `50c816e`, carried forward onto current `main`. The
three commits above the base are, in order: `d990b6f` (the example presents the fencing epoch and a
test keeps it running), `c315bf3` (the guide creates the board file before registering the board),
`d839a20` (the example reads the epoch from its own token, not the rest of the line). The approval at
`50c816e` predates the rebase, so this asks for review again rather than inheriting it.

## What the task adds

- `docs/adopting.md` — six sections from nothing to a claimed task, plus the review handshake, the MCP
  snippet and the worked example. Every command is the one a stranger would type.
- `examples/walkthrough/walkthrough.py` — two synthetic programs, three tasks, one complete cycle in a
  throwaway database: claim with a lease, a dependency that gates work, an independent reviewer, an
  audit chain.
- `tests/test_adopting_guide.py` — executes the guide's `## 3.` fenced blocks as written.
- `tests/test_walkthrough.py` — executes the example as the guide says to, and requires the refusals
  it claims to show.

The point of both tests is the same: a guide is only verified as an instruction. Reading it as prose
is how §3 previously rotted into something nobody could follow.

## What review 1549 found, and where it stands

| Finding | State |
|---|---|
| **Blocking.** §3 registered `alpha` and `beta` against board files it never told the adopter to create; `loom init` passes (it does not check `--path`), then the next documented command, `loom board add`, exits 2 with `board file missing`. `walkthrough.py` hid the gap by creating its board files first. | Fixed at `c315bf3`. §3 now creates both repositories and their files before registering anything, shows the minimal board-file shape, says who maintains the status line, and names the asymmetry it compensates for. `seed()` mirrors §3 — two repositories, `docs/work-items.md` in each. `tests/test_adopting_guide.py` runs §3 as written. |
| **Blocking.** The evidence claimed `validate_work.py` warned about nothing touching LOOM-3. It warns: the program file says `Reviewed.` while the registry has moved on. | Fixed. The Verification section below quotes the warning it prints and names it as systemic; the count of tasks carrying it has only grown since (LOOM-1/2/4/5/7/8/11 and most of `loom-ui`). |

## What executing the guide found

- The program board file is **hand-maintained**. `loom create` writes the registry only and
  `loom board sync` refuses a program board (`only packet boards are materialised`), while
  `validate_work.py` **errors** on a registered task whose `## <ID> — <title>` heading is missing. An
  adopter who registers a board and creates a task without touching the file meets a red validator.
  §3 says so and shows the heading it needs.
- A board file holding only a title (`# Work items — beta`) is valid; headings become mandatory per
  task, which is why §3's `beta` file starts empty of them.
- Following §3 and §4 to the end leaves
  `WARN alpha/W-1: registry says integrated, program says ready` — the status line the guide says is
  yours to keep. It is a warning, not an error, and `validate_work.py` exits `PASS` on the adopter's
  own fresh state.

## Verification at `d839a20`

Ran in this checkout.

- `python3 examples/walkthrough/walkthrough.py` — exit 0 in ≈2 s. Prints the seven steps,
  `initialized alpha.program`, `added board beta.program`, the lease refusal, the independence
  refusal, `green -> reviewed -> integrated` and `event chain OK`.
- `python3 -m unittest discover -s capabilities/work/tests` — **282 tests OK, 2 skipped**.
- `cd capabilities/work && python3 -m unittest discover -s tests` — **282 tests OK, 2 skipped**
  (same suite, the invocation the guide shows).
- `cd capabilities/work && python3 -m unittest tests.test_adopting_guide tests.test_walkthrough -v` —
  6/6 OK in 1.9 s.
- **Stranger run.** I followed the guide as a stranger in a throwaway `HOME`, with nothing exported:
  §3's fenced blocks extracted and run verbatim (`bash -e`), then §4's commands verbatim with the
  three placeholders supplied — a real 40-character commit, the `ask` sequence, the `answer`
  sequence. Result: `initialized alpha.program`, `added board beta.program`, task in
  `loom list --ready`, `claimed alpha/W-1 ... epoch 1`, `created -> claimed -> red -> green ->
  reviewed -> integrated`, `event chain OK`, and `--print-config opencode` emits a pasteable snippet.
  That is the goal of this task — a stranger runs the protocol end to end — checked the only way it
  can be checked.
- `cd capabilities/work && python3 validate_work.py` — exit **FAIL (1 error)**, on
  `ERROR midas/W-8: heading W-8 missing from midas.program`: a task on a board outside this
  repository, in the shared registry, untouched by this branch and failing before it. Nothing tied to
  LOOM-3 errors. `WARN loom/LOOM-3: registry says claimed, program says reviewed` is the mirror staleness
  described above, and the same line appears for most `loom` and `loom-ui` tasks.
- Negative control on the pre-fix §3 (the block that creates the board files removed, everything else
  run as written): exit 2, `error: board file missing: …/beta/docs/work-items.md` — the reviewer's
  exact failure, so the §3 test is catching the defect rather than merely passing today.

## Limits, current

- `loom init` still does not check that `--path` exists. The guide compensates and names the
  asymmetry; closing it in the engine is a behaviour change to `cmd_init` that needs its own claim
  and its own review, not a drive-by edit under a documentation task.
- §2 tells the adopter to export `WORK_CONTENT_HOME`, but no loom command reads it — it names a
  convention for where content lives, and the variables the engine and validator actually read are
  `WORK_DB` and `WORK_CONTRACTS`. The guide states `WORK_CONTENT_HOME` as the home of maps, plans and
  specs without saying it is inert. Correcting that spans `docs/artifacts.md` and
  `capabilities/work/README.md`, both outside this task's write paths; it is left alone here and
  recorded rather than silently half-fixed.
- The walkthrough's dependency gate is single-hop: it shows `alpha/W-2` and `beta/W-1` becoming ready
  when `alpha/W-1` integrates, not a chain or a cycle.
- §4's three placeholders (`<40-char commit>`, `<ask seq>`, `<answer seq>`) are why §4 has no
  executing test of its own: they are values a stranger supplies, not text a test can run. The flags
  themselves — `--review-answer` taking the answer's sequence, `--integrated-source` pinning the
  approved commit, `--reviewer` never the claimant — are executed by the walkthrough at step 4 and
  asserted in `test_walkthrough.py`. §4's prose is otherwise unchecked, and that is the residual gap.
- Evidence lives at `capabilities/work/evidence/LOOM-3.md`, beside the work home the registry names,
  not in the content repository.
