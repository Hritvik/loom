# Evidence — LOOM-2 publish the artefact templates

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-2` (isolated worktree)
Status: green, resubmitted after changes requested on review requests 1547 and 1592

Every check below was re-measured on `82f8bcd32ee68daaa76fc214a8cdb1219f289f75` (the branch tip
before this submission), each beside the command that reproduces it. This submission changes this
evidence file and nothing else, so the templates and the taxonomy under review are byte-identical
to the tree the delta review inspected at `38dbdf3`:

```bash
git diff --stat 38dbdf3..<submitted commit> -- docs templates   # empty
git diff --stat 8168ddc..<submitted commit> -- docs templates   # empty
```

## Delivered

`templates/` holds a copy-and-fill starter for every kind in `docs/artifacts.md`. The table lists
the artefact starters only; `templates/` also carries one configuration example that is not a
starter (see below the table).

| Template | Kind |
|---|---|
| `wayfinder-map.md` | the problem space and its tickets |
| `spec.md` | behaviour, invariants, acceptance, promotion |
| `plan.md` | steps, evidence, risks, out of scope |
| `capability-map.yaml` | provides / consumes / owns / excludes / open questions |
| `contract-registry.yaml` | the registry shape, including pins and gates |
| `adr.md` | context, decision, consequences, alternatives |
| `memory-vault/README.md` | scopes, kinds, and the promote-when-verified rule |
| `agents-block.md` | the coordination block every product repository carries — not an artefact kind |

`templates/linear-portfolio.example.json` is the ninth file under `templates/` and is **not** an
artefact starter: it is the example state and label map for
`~/.config/loom/linear-portfolio.json`, and the only pointer to it is
`capabilities/work/README.md:437-438`. It is listed here so the table is not read as a complete
inventory of the directory.

`docs/artifacts.md` is the index: its kind table has seven rows and its template table has eight,
the eighth explicitly marked "not an artefact kind".

## Verification

Run from the worktree root unless a step says otherwise.

1. **Every linked template exists.** All links under `docs/artifacts.md` resolve:

   ```bash
   for f in $(grep -oE '\.\./templates/[^)]+' docs/artifacts.md | sort -u); do
     test -e "docs/$f" && echo "OK $f" || echo "MISSING $f"; done
   ```

   Result: 8 of 8 `OK`, no `MISSING`.

2. **No portfolio instance in the templates.** The reviewer's widened pattern returns nothing:

   ```bash
   grep -riE "hritvik|jarvis|dutch|ums|iam|atlas|superapp|modelswitch|relay" templates/
   ```

   Result: no matches (exit 1). The only service-shaped text is the `<service>` /
   `<service>-effort` placeholder.

3. **The structured templates parse.**

   ```bash
   python3 -c "import json,yaml; yaml.safe_load(open('templates/capability-map.yaml')); \
     yaml.safe_load(open('templates/contract-registry.yaml')); \
     json.load(open('templates/linear-portfolio.example.json'))"
   ```

   Result: no exception.

4. **The suite passes.** From `capabilities/work`:

   ```bash
   python3 -m unittest discover -s tests
   ```

   Result at `82f8bcd`: `Ran 221 tests in 70.733s` — `OK`. `grep -rn "    def test_" tests --include='*.py'
   | wc -l` also returns `221`, so the runner's number and the source's number agree.
   This number is measured on the branch being submitted, not carried over from an older commit.

5. **The registry validates.** From `capabilities/work`:

   ```bash
   python3 validate_work.py
   ```

   Result at `82f8bcd`: `PASS`, 72 warnings and no errors. Two of the 72 name this task:

   - `WARN  write-path overlap within ~/repositories/loom: docs in loom/LOOM-1 and loom/LOOM-2` —
     it follows from the declared paths and is the coordinator's to sequence, not something this
     diff can clear.
   - `WARN  loom/LOOM-2: registry says …, program says reviewed` — the registry-versus-program
     drift described in Limits. The registry side of the message tracks the task's current status,
     so this is the shape of the warning rather than one snapshot of it: it read `green` at
     `82f8bcd` and reads `claimed` while a worker holds the claim.

   The remaining 70 are conditions of the shared registry and of other boards: stale program-board
   revisions and dependency drift on other boards, expired and legacy (epoch-less) claims on other
   boards, one ordering warning inside the shared event log (`event line 1040: timestamp moved
   backwards`), and 52 pre-migration integrations proven by evidence only (accepted by
   `decisions/integration-receipts-001.md`).

   The drift warning is the one place where "external to this diff" would be false: this task's own
   range edits `docs/work-items.md` and moves LOOM-1, LOOM-2 and LOOM-3 to `**Status.** Reviewed.`,
   while the registry holds them green, claimed and green — so all three warn and the board text
   came from this diff. It is recorded as a known limit in Limits rather than cleared by editing
   three tasks' entries under this claim.

6. **The reviewed guarantee still holds.** The delta review's concern was that the engine moved
   after the approved commit. For this task's paths it did not move after the reviewed point:

   ```bash
   git diff --stat 38dbdf3..82f8bcd -- docs templates   # empty
   git diff --stat 2be45cc..82f8bcd -- docs templates   # the whole delta this task ever had
   ```

   The second command reports 7 files. The two that carry this task's content —
   `docs/artifacts.md` and `templates/agents-block.md` — were named in the delta review at 1468 as
   the only template/taxonomy changes: the `WORK_CONTRACTS` example became `your-contracts`, and
   the coordination block moved from `./task` to `./loom`. The other five are the adopting guide
   and the two ADRs (LOOM-3 and engine history), the work-items index, and the Linear example.

## Limits

- **The repository front page does not reach the templates.** `README.md` at the root mentions
  neither `templates/`, `docs/artifacts.md` nor `docs/adopting.md`:
  `grep -niE "template|artefact|artifact|adopt" README.md` returns nothing, and no commit ever
  added such a pointer (`git log -S "templates/" -- README.md` is empty). An adopter who starts at
  the README cannot get to a template without guessing a path. The reachable pointers today are
  `docs/artifacts.md` (the template table) and `docs/adopting.md:103-104`.
  `README.md` is outside this task's write paths (`docs`, `templates`) and is a shared file in the
  commit guard, so this submission does not change it. **Recommended follow-up:** a task on
  `README.md` that adds one line pointing at `docs/artifacts.md` and `docs/adopting.md`.
- **The program board's status lines drift from the registry.** `docs/work-items.md:7`, `:11` and
  `:15` mark LOOM-1, LOOM-2 and LOOM-3 `**Status.** Reviewed.` — text this task's range introduced
  at `2be45cc` — while the registry holds them green, claimed and green. `validate_work.py:436-443`
  warns on each. The board file is inside this task's write paths (`docs`), but a status line is a
  projection of the registry and belongs to the board sync, not to a worker editing three tasks'
  entries to make its own warning disappear; doing that here would also fight the next claim and
  release. **Recommended follow-up:** the coordinator runs
  `./loom reconcile --board loom.program --apply --agent <coordinator>` so the three lines follow
  the registry, after which all three warnings clear without a template change.
- **The contract registry is validated; the capability map is only cross-referenced — not
  validated.** `validate_work.py:check_contracts` checks the contract-registry shape and its
  copies: `kind` in `http|event`, a known `status`, an `id` ending `.v<version>`, `gate` naming a
  real task, `artifact` + `sha256` present and matching for accepted contracts, companion files
  and fixtures present, consumer pins compared to the artifact digest, and program maps barred
  from inventing a contract id. `validate_work.py:cross_check_capabilities` reads the
  capability-steward `registry.yaml` beside the work home (its `efforts`/`contracts`/`decisions`
  entries with `id`, `path`, `status`, `owner`) and never parses a capability map. So the five
  sections of `templates/capability-map.yaml` — `provides`, `consumes`, `owns`, `excludes`,
  `open_questions` — are a hand-filled shape with no machine check behind them, and the six prose
  starters (wayfinder map, spec, plan, ADR, memory, agents block) are in the same position.
- **`docs/adr/0001-state-leaves-git.md` is history, not a template instance.** It records this
  engine's own split and cites this portfolio's scale. It is the engine's decision record about
  state leaving git; replacing it is not part of publishing templates.
- **`docs/adopting.md:11` carries a doubled trailing comment.** The `coordination.home` line ends
  with two `#` comments in one line, introduced by `d954e99` inside the reviewed range. The file
  is inside this task's write paths (`docs`, per `loom show loom/LOOM-2` and the guard's
  `covers()` in `capabilities/work/hooks/pre-commit:100-105`), but it is LOOM-3's deliverable, and
  `loom show loom/LOOM-3` declares no write paths at all — so no LOOM-3 worker can commit it
  either. This submission leaves the text alone rather than editing another task's deliverable
  under this claim. **Recommended follow-up:** give LOOM-3 the write path and have it fix the
  line, or add it to a documentation-cleanup task.

## Rework after review request 1592 (event 1608, changes requested)

The reviewer re-verified the deliverable end to end — the eight starters covering the seven kinds
plus `agents-block.md`, the seven-row kind table against the eight-row template table, all eight
template links, the empty portfolio-instance grep, the YAML and JSON parses,
`contract-registry.yaml`'s field names against `check_contracts`, the `agents-block` commands
against `./loom claim --help`, the `efc1b0d..82f8bcd` range touching only this file, and a
re-measured 221 tests — and rejected one sentence. No template, taxonomy or test-count change was
needed.

1. **A second warning names this task, and the "not of this diff" clause was too broad.** The
   previous version said the `docs` write-path overlap with `loom/LOOM-1` was the only warning
   naming this task. `validate_work.py` emits two: that overlap, and
   `loom/LOOM-2: registry says …, program says reviewed`, which is raised against
   `docs/work-items.md` — a file this task's own range edits. Verification step 5 now names both
   and attributes the drift correctly, and the drift is carried in Limits as a known limit with the
   board-sync follow-up, instead of being described as external.

## Rework after review request 1547 (event 1579, changes requested)

The reviewer confirmed the deliverable — the template set, the seven-versus-eight table, the
absence of a portfolio instance, the `agents-block` commands against `./loom claim --help`, the
`contract-registry.yaml` field names against `check_contracts`, and the re-measured 221 tests — and
rejected the record on three points. All three are corrected above; no template or taxonomy
content changed.

1. **False write-path claim.** The previous version said `docs/adopting.md` was "outside this
   task's write paths". It is not: `loom show loom/LOOM-2` lists `docs`, and the guard's `covers()`
   matches any path under a declared root. The claim is replaced by the accurate statement in
   Limits, together with the fact that LOOM-3 declares no write paths.

2. **Wrong line number.** The doubled comment is at `docs/adopting.md:11`, not `:10` — line 10 is
   the `core.hooksPath` line. Corrected everywhere it appears.

3. **Overstated machine-checkability.** "Only `contract-registry.yaml` and `capability-map.yaml`
   have machine-checkable shapes today" was wrong about the capability map: `check_contracts`
   validates the contract registry; `cross_check_capabilities` reads the capability-steward
   registry and never parses a capability map. Reworded in Limits.

Also taken from that review: the Delivered table now says it lists artefact starters only, and
names `templates/linear-portfolio.example.json` so the table is not read as the whole directory.

## Rework after the delta review (event 1468, changes requested)

Kept for the record. Both findings were false claims in the evidence, not defects in the
templates; both were corrected in `efc1b0d` and confirmed fixed by the reviewer at 1547.

1. **"README.md points at the directory" was false.** It does not — see Limits. The claim is gone
   and the gap is stated as a limit with a follow-up recommendation.
2. **"132 tests" / "134 tests" were both wrong.** Measured here: 221, with the command recorded
   above so the number can be reproduced rather than trusted.
