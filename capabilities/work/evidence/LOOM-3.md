# Evidence — LOOM-3 adopting guide and worked example

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-3`
This revision answers review request **1549** — "changes requested" from `opencode/reviewer-agent`
— by fixing the two required changes and by correcting the evidence the review found inaccurate.

## What the review found, and what changed

| Finding | Fix |
|---|---|
| **Blocking.** The evidence said `validate_work.py` warned about nothing touching LOOM-3. It does: the program file `docs/work-items.md` still says `**Status.** Reviewed.` while the registry has moved through rework, so the validator prints `WARN loom/LOOM-3: registry says <status>, program says reviewed`. | The claim is gone. The Verification section below quotes the warning it actually prints and says why the mirror is stale. |
| **Blocking.** `docs/adopting.md` §3 could not be followed by a stranger: it registered `alpha` and `beta` against board files it never told the adopter to create. `loom init` passes (it does not check `--path`), then the next documented command, `loom board add`, exits 2 with `board file missing`. `walkthrough.py` hid the gap by creating its board files first. | §3 now creates both board repositories and their files before registering anything, shows the minimal board-file shape, says the state the file holds and who maintains it, and states the asymmetry it is compensating for (`init` does not check `--path`; `board add` refuses). The walkthrough's `seed()` now mirrors §3 exactly — two repositories, `docs/work-items.md` in each — instead of one root with two ad-hoc files. `tests/test_adopting_guide.py` executes §3 as written. |

## The guide is now executed, not read

The example was already run by the suite; the guide was not, which is how §3 drifted into an
instruction nobody could follow. `tests/test_adopting_guide.py` reads the fenced `bash` blocks out of
`## 3.` of `docs/adopting.md` and runs them in order, in a throwaway `HOME` with a throwaway
database, then asserts `initialized alpha.program`, `added board beta.program`, the task in
`loom list --ready`, and that both board files exist at the guide's own paths.

Two further tests guard the order and the content: one requires a `cat >` that creates a board file
**before** `loom init`, the other that §3 states the `init`, `board add` and `create` it depends on.

I checked the test catches the defect rather than merely passing today: with §3's creation block
replaced by `true`, the same test path fails exactly as the reviewer reported — exit 2,
`error: board file missing: …/beta/docs/work-items.md`.

## What executing §3 found

- The program board file is **hand-maintained**. `loom create` writes the registry only, and
  `loom board sync` refuses a program board (`only packet boards are materialised`), while
  `validate_work.py` **errors** on a registered task whose `## <ID> — <title>` heading is missing.
  An adopter who registers a board and creates a task without touching the file meets a red
  validator. §3 now says so and shows the heading it needs.
- A board file holding only a title (`# Work items — beta`) is valid. Headings become mandatory only
  per task, which is why the guide's `beta` file starts empty of them.

## Limits, current

- `loom init` still does not check that `--path` exists. The guide now compensates and names the
  asymmetry, but closing it in the engine is a behaviour change to `cmd_init`; it needs its own
  claim and its own review, not a drive-by edit under a documentation task.
- The validator's program-file warning is systemic, not LOOM-3's: at the same moment `loom/LOOM-1`
  reads green, `loom/LOOM-2` claimed, `ums/RELAY-PERMANENT-FAILURE` reviewed, all against program
  files that say otherwise. The mirror is updated by whoever records the final status, and the
  registry changes status on every transition, so it cannot track a review cycle. This task
  corrects its evidence rather than editing a program file it does not own at integration time.
- The walkthrough's dependency gate is single-hop: it shows `alpha/W-2` and `beta/W-1` becoming
  ready when `alpha/W-1` integrates, not a chain or a cycle.
- Evidence lives at `capabilities/work/evidence/LOOM-3.md`, beside the work home the registry names,
  not in the content repository.

## Verification

Run on `loom/loom-loom-3` at the commit under review:

- `python3 examples/walkthrough/walkthrough.py` — exits 0 in ≈2 s, prints the seven steps,
  `green -> reviewed -> integrated`, the lease refusal, the independence refusal,
  `initialized alpha.program`, `added board beta.program`, and `event chain OK`.
- `cd capabilities/work && python3 -m unittest discover -s tests` — **227 tests OK**, including the
  three new guide tests and the three walkthrough tests.
- `cd . && python3 -m unittest discover -s capabilities/work/tests` — **227 tests OK**.
- `cd capabilities/work && python3 -m unittest tests.test_adopting_guide -v` — 3/3 OK.
- `cd capabilities/work && python3 validate_work.py` — **PASS**, and it prints, unchanged by this
  work:
  `WARN  loom/LOOM-3: registry says claimed, program says reviewed` (at green, the same line reads
  `registry says green`). That is the mirror-staleness warning above, not a defect in the guide; the
  same warning appears for `loom/LOOM-1`, `loom/LOOM-2` and `ums/RELAY-PERMANENT-FAILURE`.
- Negative control on the pre-fix §3 (creation block removed): exit 2,
  `error: board file missing: …/beta/docs/work-items.md`.
