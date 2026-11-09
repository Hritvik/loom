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

## Rework after review (event 740, changes requested)

The first pass grepped one directory and declared the engine clean. Reviewer-1 grepped the tracked
tree and found fourteen hits. Fixed:

- `task`: `OWNER_DEFAULT` was a person's name; now `WORK_OWNER` or `owner`.
- `hooks/pre-commit`: the engine path was hardcoded; now derived from the hook's own location, and
  the wrapper resolves `WORK_COORDINATION_HOME` or `git config coordination.home`, else no-ops.
- MCP and A2A tool descriptions no longer name this portfolio's services.
- `linear_sync.py`: `--focus` no longer defaults to one portfolio task; it requires `--focus` or
  `--all`.
- `PROTOCOL.md` and `README.md`: the integration-map sections described a file this work deleted.
  Both now describe an optional program map checked by `validate_work.py --programs <dir>`.
- `AGENT-GUIDE.md`, `AGENTS.md`, `docs/artifacts.md`: example identifiers and paths genericised.
- `validate_work.py` gained `--programs`; it previously globbed a directory that no longer exists
  and silently skipped two checks while the docs promised them.
- Tests no longer use a person's name or this portfolio's program ids as fixtures.

Verification: `git grep -n -I -E "hritvik|IAM|UMS|Dutch|portfolio-planning|dutch/S7"` returns
nothing outside `docs/adr/0001` and this evidence file. 134 tests pass; validator PASS.

## Second rework after re-review (event 819, changes requested)

- `capabilities/work/README.md`: the previous edit duplicated 110 lines and left a stale integration
  map section naming the deleted program file. Deduplicated; one optional-map section remains.
- `validate_work.py`: the program-map check hardcoded this portfolio's service names, so any other
  adopter's map failed. It now requires an owner and non-empty producers/consumers, and validates
  task references only.
- The skip is no longer silent: `programs: none (pass --programs <dir> …)` prints when absent.
- A2A agent card tags and examples, and the MCP input-schema descriptions, no longer name this
  portfolio's programs.
- `work-home.yaml` marks `programs` optional; the remaining personal example paths are genericised.

## Third rework after re-review (event 830, changes requested)

The second pass fixed the named lines but left the same class of leak in neighbouring files. This
pass replaced every remaining portfolio identifier in the engine with neutral ones:

- `mcp/server.py` docstring and two input-schema descriptions; `mcp/README.md`, `a2a/README.md`,
  `capabilities/work/README.md`, `PROTOCOL.md` examples
- `task` CLI error-message examples and the A2A contextId sample
- test fixtures across `test_work.py`, `test_mcp.py`, `test_linear_projection.py`,
  `test_linear_sync.py`
- the personal repository link in the top-level README

Verification: `grep -rn -I -E "\biam\b|\bums\b|\bdutch\b|portfolio-contracts"` over the tracked tree
returns nothing outside `docs/adr/0001` and this evidence file. 134 tests pass; validator PASS.
