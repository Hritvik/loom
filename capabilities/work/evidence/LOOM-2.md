# Evidence — LOOM-2 publish the artefact templates

Repository: `~/repositories/loom`
Status: green, awaiting independent review

## Delivered

`templates/` now holds a copy-and-fill starter for every kind in `docs/artifacts.md`:

| Template | Kind |
|---|---|
| `wayfinder-map.md` | the problem space and its tickets |
| `spec.md` | behaviour, invariants, acceptance, promotion |
| `plan.md` | steps, evidence, risks, out of scope |
| `capability-map.yaml` | provides / consumes / owns / excludes / open questions |
| `contract-registry.yaml` | the registry shape, including pins and gates |
| `adr.md` | context, decision, consequences, alternatives |
| `memory-vault/README.md` | scopes, kinds, and the promote-when-verified rule |
| `agents-block.md` | the coordination block every product repository carries |

`docs/artifacts.md` links each kind to its template; `README.md` points at the directory.

## Verification

- Every template is a placeholder document with no portfolio instance in it: no program names, no
  service names, no task ids beyond `<program>/<ID>`.
- `python3 -m unittest discover -s tests` — 132 tests pass.
- `python3 validate_work.py` — PASS.

## Limits

- The templates are prose skeletons, not schemas. Only the contract registry and the capability map
  have machine-checkable shapes today.
- `docs/adr/0001` still describes this portfolio's split and should move to the content repository
  once an adopter has used the ADR template; LOOM-3 covers the example that replaces it.

## Rework after review (event 746, changes requested)

- `templates/agents-block.md`: the claim command now includes `--agent <you>`, and the block says
  what `$WORK_COORDINATION_HOME` is and how to set it.
- `templates/memory-vault/README.md`: the two portfolio-flavoured examples are replaced with neutral
  ones.
- `docs/artifacts.md`: the template table is headed "Template | For" so the eighth row is not
  presented as an eighth artefact kind.
- This evidence file corrected: the README does point at `templates/`, and the suite is 134 tests.
