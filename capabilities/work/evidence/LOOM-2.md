# Evidence — LOOM-2 publish the artefact templates

Repository: `~/repositories/loom`
Branch: `loom/loom-loom-2` (isolated worktree)
Status: green, submitted for independent review

This file states the claim at the commit that carries it and nothing else. The review history is in the registry event log (`loom events | grep loom/LOOM-2`), which is
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
python3 -m unittest discover -s tests                          # passes, skips without the denylist
python3 validate_work.py                                       # PASS; warnings are shared state
```

No counts are recorded here on purpose. Every number in this file's earlier drafts was true when
written and false within a merge, and a count cannot be checked without re-running the command
anyway. Each line above prints its own current value; read it there.

- **Suite.** Passes. The two skips are `test_boundary.PrivateDenylistTest`, which need
  `LOOM_BOUNDARY_DENYLIST`.
- **Validator.** `PASS`. Warnings belong to the shared registry, not to this diff; the current list
  is `python3 validate_work.py | grep '^WARN'`. The only warning that can name this task is the
  program-board drift line, and it names it only while the board line and the registry status differ.
- **Templates.** Nine files under `templates/`: the eight starters above plus
  `linear-portfolio.example.json`. Every `../templates/` link in `docs/artifacts.md` resolves; no
  program name from this portfolio appears under `templates/`; the two YAML files and the JSON parse.
- **Taxonomy.** `docs/artifacts.md` has a seven-row kind table and an eight-row template table whose
  last row is marked "not an artefact kind".

## Limits

- **`README.md` does not point at the templates.** `grep -niE "template|artefact|artifact|adopt"
  README.md` is empty and `git log -S "templates/" -- README.md` is empty, so an adopter starting at
  the front page cannot reach them; the reachable pointers are `docs/artifacts.md` and
  `docs/adopting.md:103-104`. The file is outside this task's declared write path and is shared, so
  this submission leaves it alone. **Recommended follow-up:** a task that adds one line to it.
- **Program-board status lines drift from the registry.** `docs/work-items.md` marks LOOM-1, LOOM-2
  and LOOM-3 `**Status.** Reviewed.` — introduced by `fec72e2e` inside the `2be45cc..82f8bcd` range —
  and other lines in the same file read `Ready` for tasks whose registry status has since moved. A
  line warns only while the two differ, so the number of warnings follows the registry rather than
  this diff; `validate_work.py` reports them per line. The board file is outside this task's declared
  write path, and a status line is a projection of the registry: **the coordinator runs**
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
