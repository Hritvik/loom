# Evidence — LOOM-2 publish the artefact templates

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-2` (isolated worktree)
Status: green, resubmitted after changes requested on review request 1436

All checks below were run on `8168ddc4a3959ac310f098c41a8b2c8f86afb50c`, the branch tip before
this submission. This submission changes this evidence file and nothing else:
`git diff --stat 8168ddc <submitted commit> -- docs templates` is empty, so the templates and the
taxonomy under review are byte-identical to the ones the delta review inspected at `38dbdf3`.

## Delivered

`templates/` holds a copy-and-fill starter for every kind in `docs/artifacts.md`:

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

2. **No portfolio instance in the templates.** The reviewer's own pattern returns nothing:

   ```bash
   grep -riE "hritvik|IAM|UMS|Dutch|ATLAS|alpha" templates/
   ```

   Result: no matches. The only service-shaped text is the `<service>-effort` placeholder.

3. **The suite passes.** From `capabilities/work`:

   ```bash
   python3 -m unittest discover -s tests
   ```

   Result: `Ran 221 tests in 71.141s` — `OK`. That is 221 `def test_` methods across `tests/`,
   matching the number the test runner reports.

4. **The registry validates.** From `capabilities/work`:

   ```bash
   python3 validate_work.py
   ```

   Result: `PASS`, with pre-existing warnings that are not this task's: expired leases and
   dependency drift on other boards, the `docs` write-path overlap between `loom/LOOM-1` and this
   task, and 52 pre-migration integrations proven by evidence only (accepted by
   `decisions/integration-receipts-001.md`).

5. **The reviewed guarantee still holds.** The delta review's concern was that the engine moved
   after the approved commit. For this task's paths it did not move after the reviewed point:

   ```bash
   git diff --stat 38dbdf3..8168ddc -- docs templates
   ```

   Result: empty.

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
- **The templates are prose skeletons, not schemas.** Only `contract-registry.yaml` and
  `capability-map.yaml` have machine-checkable shapes today; the other six are copied and filled
  by hand and nothing validates their contents.
- **`docs/adr/0001-state-leaves-git.md` is history, not a template instance.** It records this
  engine's own split and cites this portfolio's scale. It is the engine's decision record about
  state leaving git; replacing it is not part of publishing templates.

## Rework after the delta review (event 1468, changes requested)

The reviewer found two false claims in the previous evidence. Both are corrected above; neither
was a defect in the templates.

1. **"README.md points at the directory" was false.** It does not — see Limits. The claim is gone
   and the gap is now stated as a limit with a follow-up recommendation.
2. **"132 tests" / "134 tests" were both wrong.** Measured at `8168ddc`:
   `python3 -m unittest discover -s tests` runs **221** tests and reports `OK`. The command is
   recorded above so the number can be reproduced rather than trusted.

Also noted, unchanged here: `docs/adopting.md:10` still has a doubled trailing comment on its
`git config` line. `docs/adopting.md` is LOOM-3's file — outside this task's write paths — so it is
left for that task rather than edited under this claim.
