# Evidence — LOOM-1 remove portfolio content from the public engine

Repository: `~/repositories/loom`
Status: green, awaiting independent review

## Delivered

- The portfolio integration map moved out of the engine to the content repository
  (`portfolio-planning/capabilities/work/programs/identity-platform-integration.yaml`).
- The Linear projection runbook moved with it; it names a specific workspace.
- `README.md`: the ten-row program table and the Dutch ADR note are replaced by a three-repository
  model (engine / content / contracts) and the environment variables that point at them.
- `AGENT-GUIDE.md`: the portfolio review-scope examples and the plan link are gone; "where things
  live" now points at `$WORK_CONTENT_HOME` and `$WORK_CONTRACTS`.
- `PROTOCOL.md`: the service-ownership list is genericised.
- `docs/artifacts.md` added: the seven artefact kinds, their homes, creation and promotion path.

## Verification

- `grep -ri "IAM\|UMS\|Dutch\|hritvik"` over the engine's markdown returns only the example value
  of `WORK_CONTENT_HOME`.
- `python3 -m unittest discover -s tests` — 132 tests, all pass.
- `python3 validate_work.py` — PASS; contracts are skipped unless `--contracts` is given.

## Limits

- `docs/adr/0001-state-leaves-git.md` still describes this portfolio's split decision. It is an
  example of an ADR more than an engine decision; it should move to the content repository once a
  generic ADR template exists.
