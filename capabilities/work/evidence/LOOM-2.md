# Evidence — LOOM-2 publish the artefact templates

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-2` (isolated worktree)
Status: green, submitted for independent review

This file states the claim at the commit that carries it and nothing else. The review history —
eleven asks and ten answers — is in the registry event log (`loom events | grep loom/LOOM-2`), which is
the audit record; duplicating it here is what kept this file wrong. Commands are runnable as written.

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
`capabilities/work/README.md:476-477`. It is listed here so the table is not read as a complete
inventory of the directory.

`docs/artifacts.md` is the index: its kind table has seven rows and its template table has eight,
the eighth explicitly marked "not an artefact kind".

## Verification

Run from `capabilities/work` at the commit that carries this file. It changes only this file, so the
deliverable is the one the delta reviews inspected.

```bash
git diff --stat 38dbdf3..HEAD -- docs/artifacts.md templates   # empty
git diff --stat 8168ddc..HEAD -- docs/artifacts.md templates   # empty
python3 -m unittest discover -s tests                          # 276 tests, OK (skipped=2)
python3 validate_work.py                                       # PASS, 78 warnings, 0 errors
```

- **Suite.** 276 tests, `OK (skipped=2)`; the two skips are `test_boundary.PrivateDenylistTest`,
  which need `LOOM_BOUNDARY_DENYLIST`. `grep -rn "    def test_" tests --include='*.py' | wc -l`
  also gives 276.
- **Validator.** `PASS`, 78 warnings, 0 errors. Exactly one names this task:
  `loom/LOOM-2: registry says …, program says reviewed`. One more is LOOM-1's drift, which this
  task's board edit also caused; Limits records both. The remaining 76 are registry state:
  11 board-central revisions, 38 status drifts on other boards (40 drift lines in all, of which the
  two above are the loom ones), 12 ready-but-waiting, 6 expired leases, 6 epoch-less claims, 1
  event-line ordering warning, 2 aggregates (52 evidence-only integrations; 21 digest-less reviewed
  receipts). 11 + 38 + 12 + 6 + 6 + 1 + 2 = 76. The aggregates are single warning lines, not
  per-task counts.
- **Templates.** Nine files under `templates/`: the eight starters above plus
  `linear-portfolio.example.json`. All eight `../templates/` links in `docs/artifacts.md` resolve;
  `grep -riE "hritvik|jarvis|dutch|ums|iam|atlas|superapp|modelswitch|relay" templates/` is empty;
  the two YAML files and the JSON parse.
- **Taxonomy.** `docs/artifacts.md` has a seven-row kind table and an eight-row template table whose
  last row is marked "not an artefact kind".

## Limits

- **`README.md` does not point at the templates.** `grep -niE "template|artefact|artifact|adopt"
  README.md` is empty and `git log -S "templates/" -- README.md` is empty, so an adopter starting at
  the front page cannot reach them; the reachable pointers are `docs/artifacts.md` and
  `docs/adopting.md:103-104`. The file is outside this task's declared write path and is shared, so
  this submission leaves it alone. **Recommended follow-up:** a task that adds one line to it.
- **Two program-board status lines drift from the registry.** `docs/work-items.md:7`, `:11` and `:15`
  read `**Status.** Reviewed.`, introduced by `fec72e2e` inside the `2be45cc..82f8bcd` range. Two
  disagree with the registry today (LOOM-1 `integrated`, LOOM-2 `green`) and `validate_work.py:450-454`
  warns on those two; LOOM-3 matches and does not warn, and would warn again only while the two
  differ. The board file is outside this task's declared write path, and a status line is a
  projection of the registry: **the coordinator runs**
  `./loom reconcile --board loom.program --apply --agent <coordinator>`.
- **The capability map is cross-referenced, not validated.** `check_contracts` validates the
  contract registry; `cross_check_capabilities` reads the steward `registry.yaml` and never parses a
  capability map. So the six sections of `templates/capability-map.yaml` — `provides`, `consumes`,
  `owns`, `excludes`, `open_questions`, `evidence` — are a hand-filled shape with no machine check
  behind them, as are the six prose starters.
- **`docs/adr/0001-state-leaves-git.md` is history, not a template instance.** It records this
  engine's split and cites this portfolio's scale. Replacing it is not part of publishing templates.
- **`docs/adopting.md:11` carries a doubled trailing comment**, introduced by `d954e99`. The file is
  LOOM-3's deliverable; `loom show loom/LOOM-3` declares five paths including `docs/adopting.md`, so
  LOOM-3 can commit it and should.
