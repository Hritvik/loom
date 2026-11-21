# Evidence — loom-ui/UI-7 decide promotion and v1 operational readiness

Repository: `~/repositories/loom`
Branch: `loom/loom-ui-ui-7-a10`
Source: the review request binds the full commit and the base it is reviewed from; this branch is
the submitted revision.

Goal: put the v1 acceptance decision in front of the owner with the evidence, the supported
environment, the deployment and identity decisions, and the promotion package, **without accepting
or promoting anything**. Two kinds of line appear below: a value between `loom:` markers is
re-derived from the tree by `tests/test_evidence.py`, and everything else is prose that a reviewer
reads rather than a machine checks.

## Delivered

| Path | Change |
|---|---|
| `capabilities/work/ui/READINESS.md` | new — the v1 decision record: a verdict per acceptance criterion, the operating envelope, seven owner decisions with a recommendation, the runbook, the residual risks, the promotion package and the questions to answer |
| `capabilities/work/ui/README.md` | one paragraph rewritten: the verification pointer now names `READINESS.md` as the decision record and says it is a request, not an acceptance |
| `capabilities/work/evidence/UI-7.md` | this file |

No product code changed. No test changed. No engine rule, action allow-list, route, role table,
session rule, version gate or persistence format changed. No registry, receipt or plan was edited
from this task; no `docs/` file was written; the Linear projection was not touched.

## Rework after review 3087

The blocking finding was that §3's Mutation row listed `accepted` among the transitions v1 does not
claim, while the code offers it: `identity.py` maps `accepted` to the owner role and
`OwnerJourneys.test_an_owner_accepts_integrated_work_from_the_acceptance_queue` accepts `demo/A-7`
through the browser. That was an understated envelope, not a typo, so the correction is in four
places:

- **§3 Mutation** now states the filter that actually exists — `EXCLUDED_TARGETS` removes `reviewed`
  and `integrated`, `NEEDS_INTEGRATED_SOURCE` removes `reviewed → accepted` — and says plainly that
  an owner, and only an owner, can record `accepted` for integrated work whose dependencies are
  satisfied.
- **§4 decision 2** says the same, so the mutation-scope question the owner answers includes the one
  authority-bearing click the acceptance queue exists for.
- **§2 row 7** cites the journey test, not just the role table.
- **§9** lists the transition filters among the things that would make the record false.

Two cosmetic corrections from the same review: §2 row 3 now cites
`ReadModelParity.test_graph_edges_are_the_front_door_dependencies` for graph-versus-engine parity —
`GraphAccessibility` is the text-equivalent check, a different property — and the README row above no
longer calls a paragraph rewrite "two lines".

No code or test changed in the rework. The verification below is a fresh run on this branch, not a
carried-over result.

## Rework after review 3173

Two required corrections, both in `READINESS.md`; no code, test or other document changed.

1. **Runbook step 2 denied a route that exists.** The sentence said that with no `--human` and no
   identity file "no action route exists". The route exists and refuses: `server.py` answers `403`
   with *Actions are disabled* for any id in `ENGINE_VERBS`, and
   `test_action_server.py::test_every_action_route_is_closed` drives
   `/task/demo/A-1/action/claim` and asserts the `403` on both `GET` and `POST`. The step now says
   the routes are present and answer `403` with nothing submitted, and names the source and the test
   without overstating it: the test drives one route, the code refuses every id. §3's Read path row —
   the claim the wrong sentence contradicted — was left exactly as it was, per the review's
   instruction not to weaken a correct claim to match a wrong one.
2. **The outside-v1 list omitted store-writing commands.** It named twelve entries and left out
   `rebuild --apply`, `genesis`, `init`, `integration-plan --out`, `snapshot`, `export --out` and
   `graph --format html`, any of which a reader could have inferred was offered. `graph` is split
   correctly: `--format html` writes a file (to `--out` or `task-graph.html`), while text, mermaid
   and dot print to stdout and are listed with the read-only commands. It is completed rather than
   marked illustrative, because this row is the part of the record that is supposed to leave nothing
   to silence: it now lists every command that writes and is not one of the seven, grouped as store,
   artifact and operational, and states separately that the read-only commands have no route because
   the UI reads the store in process (`ui/loom_ui/snapshot.py` opens the database directly) instead
   of shelling out to them. That claim was still false as written: `board titles` and
   `events --verify --reanchor` were missing, and the 3219 rework below corrects them.

## Rework after the integration refusal (event 3203)

No document changed in content. The refusal was that the branch edits
`capabilities/work/ui/README.md` while the receipt approved for `56ae63d` covered only
`evidence/UI-7.md` and `ui/READINESS.md`, so integration would have landed an unreviewed README
edit — and the new `LOOM-17` rule refuses exactly that. The cause was mechanical, not editorial: the
final review request in the previous round omitted `--review-base`, so `loom ask` defaulted to the
source's **first parent** (`12ebe4e`), which is the second commit of the rework, and a two-commit
range never sees the README paragraph from the first commit.

Two changes here, both to the submission rather than to the artifact:

1. **Rebased onto the current `main`.** The branch now sits on `c3c2742` (`LOOM-17`) instead of
   `8056368`, so the range has no pre-existing commits in it and the new rule is in force for the
   tree that will integrate. `git merge-base HEAD origin/main` is now `c3c2742`.
2. **The review request names that merge-base explicitly** with `--review-base`, instead of letting
   the default pick a parent inside the branch. The receipt therefore covers all three paths the
   branch changes — `ui/README.md`, `ui/READINESS.md`, `evidence/UI-7.md` — which is the same scope
   the earlier receipts claimed in prose but not in the recorded path pair set.

Nothing in the artifact was reworded to satisfy the refusal: `git diff 56ae63d..2515be0` is empty
for `ui/README.md` and `ui/READINESS.md`. Within this file that range changes the branch line, the
Verification paragraph's suite counts and decision 7 as well as adding this section. The suites were
re-run on the rebased tree, and the engine suite now collects 398 tests (two skipped) because
`LOOM-17` added its own.

## Rework after review 3219

Four corrections, all documentation: `READINESS.md` §3 and §7, and this file. No code, test, registry
or README paragraph changed.

1. **`board titles` was missing from the store-writing group.** `engine.py` offers it as a `board`
   action (`choices=[… "titles"]`, line 5790) and `cmd_board` dispatches it to `backfill_titles`,
   which sets task titles and goals and commits a `titles` event (4841–4873); `--check` previews
   without writing. It is now listed with `board add`, `remove`, `sync` and `import`.
2. **`events` was in the read-only list, but `events --verify --reanchor` writes.** Reanchoring calls
   `store.reanchor`, which rewrites the external `.head` checkpoint (`engine.py` 1496–1502,
   `store.py` 909–920); `--allow-rewind` only decides whether lowering a truncated head is refused.
   `events` left the read-only list, the writing mode joined the operational group, and the read-only
   clause now names each writing command's non-writing mode — `board list`, `edge list`, `reconcile`
   and `rebuild` without `--apply`, `tick`, `export` and `integration-plan` to stdout,
   `snapshot --list`, `workers` listing, `schedule` and `deploy` status, `linear` without `--apply` —
   so the two halves cover the same command set. Plain `events` and `events --verify` still only read.
3. **This file overstated its own delta from `56ae63d`.** It said only the new section and the branch
   line differed; that range also rewrites the Verification paragraph (the 323 and 398 counts) and
   adds decision 7. The sentence now states the diff itself: empty for `ui/README.md` and
   `ui/READINESS.md`, and exactly those changes within this file.
4. **`READINESS.md` §7 claimed a pointer this task cannot write.** It said the record adds "the plan
   entry and the `README.md` reference"; the plan lives in the private content repo and this task's
   write path is `capabilities/work/ui`, so the record adds the README reference only. The same table
   row said the plan *marks* the `docs/` copy not required; the plan's Promotion checklist still
   carries that item unticked, so the row now says this record gives the reason and the owner
   resolves the item.

The completeness sentence is made true rather than hedged, the same choice the 3173 round made: that
row is the part of the record that is supposed to leave nothing to silence.

## Verification

The UI-6 acceptance matrix still passes on the integrated tree, and so do the suites beneath it.
Run from `capabilities/work` (`ui.tests.test_verification` from the same directory):

```bash
python3 -m unittest ui.tests.test_verification
python3 -m unittest discover -s ui/tests -t ui/tests
python3 -m unittest discover -s tests
```

The module this submission's readiness verdict rests on collects
<!-- loom:tests.count:ui/tests/test_verification.py -->44<!-- /loom --> tests, all passing on this
branch. On the rebased tree (`c3c2742`), `discover -s ui/tests -t ui/tests` collects 323 tests and
`discover -s tests` collects 398 (two skipped), all passing. The engine suite carries
`tests/test_evidence.py`, whose repository sweep re-derives every span under `evidence/`; this file
is therefore checked against the tree rather than believed, and a hand-written count would have
failed it.

## What was checked by reading, and where

The envelope in `READINESS.md` §3 names behaviour, so each claim below is a line a reviewer can open
at the submitted commit. None is a claim about live registry state.

| Claim | Where it is enforced |
|---|---|
| Exactly seven engine verbs can be executed | `ui/loom_ui/actions.py` — `ENGINE_VERBS` is a frozenset, and the executor refuses a verb outside it |
| No operational route exists | `ui/loom_ui/server.py` route table — no tick, dispatch, schedule, deploy, promote, rollback or linear route |
| Loopback only | `ui/loom_ui/server.py` — non-loopback binds refused, `LOOPBACK_HOSTS`, `Host` and `Origin` checks |
| Roles and owner-only acceptance | `ui/loom_ui/identity.py` — `ROLES`, `ACTION_ROLES`, `TARGET_ROLES` (`accepted` → owner); `ui/loom_ui/actions.py` — `EXCLUDED_TARGETS` removes `reviewed` and `integrated`, `NEEDS_INTEGRATED_SOURCE` removes `reviewed → accepted`, and `integrated → accepted` stays offered to an owner |
| Sessions are memory-only | `ui/loom_ui/session.py` — absolute and idle TTLs, secret generated at start, one-shot nonces |
| The version gate closes rather than guesses | `ui/loom_ui/version.py` — digest over the engine sources, imported-module check, `MISMATCH`/`DRIFTED`/`ABSENT` closed |
| Off-macOS prediction (degraded scheduler, not a crash) | `ui/loom_ui/provenance.py` (launch-agent path) and `ui/loom_ui/operations.py` (`launchctl` failure returns an error string) |
| Status is text, not colour | `ui/loom_ui/templates.py` — every badge renders its word; body text passes through `esc` |

## Decisions and their reasons

1. **The task produces a decision package, not an acceptance.** The work item says no promotion or
   release occurs without owner acceptance. An implementer who "accepted" its own readiness would be
   the failure this gate exists to prevent, so `READINESS.md` carries a blank accepted-by field and
   the owner's questions in its final section.
2. **Criterion 8 is recorded as partly met.** The verification record proves a structural
   accessibility scaffold and text-encoded status; it explicitly does not prove reflow, zoom,
   contrast or screen-reader output. Writing "met" there would convert a named limit into silence,
   so the register names the four unproven properties and recommends a ten-minute owner pass as the
   proportionate closure for a single-user local tool.
3. **No product code changed.** A portability fix (a platform-neutral scheduler source) or a
   contrast test would each invalidate part of the UI-6 record and require re-verification. Neither
   is required to make the v1 decision, so both are recorded as risks rather than slipped into a
   decision task.
4. **Promotion is a status change plus a pointer, not a copy.** The artefact taxonomy says one
   artefact, one home, and the UI's docs already live in this repository under `capabilities/work/ui`.
   A `docs/` duplicate would be a second home that drifts, so the package records why no copy is
   proposed and leaves a `docs/` index line as the owner's optional addition.
5. **`loom deploy promote` is deliberately not conflated with artefact promotion.** The UI runs from
   a promoted release and a release promotion moves the front door under it, which is why the runbook
   says to restart the server and why the record refuses to recommend a release moment.
6. **The review's blocking finding changed the envelope, not the summary.** The Mutation row said
   `accepted` was not claimed while the UI offers it to an owner from the acceptance queue. Deleting
   the word would have left the claim vague, so the row now names the filter that exists and the
   acceptance path it leaves open. The record was corrected to match the code; no code was changed to
   make the old sentence true.
7. **The receipt's base is named, not defaulted.** An earlier round asked for review without
   `--review-base`, so `loom ask` used the source's first parent and the recorded path set silently
   shrank to the last two commits of the branch. The range is part of the claim — an approval is
   over a set of paths — so the request now names the merge-base with `main` and the base is written
   into this evidence like any other claim. The alternative, asking again over the same shortened
   range, would have produced an approving receipt for a change it did not cover.
8. **The list is completed, not softened.** The reviewer found two writing commands absent from a
   sentence that claimed to name every one. Deleting the completeness claim would have left the row
   vague, so `board titles` and `events --verify --reanchor` were added and `events` left the
   read-only clause. The sentence is true only while it matches `build_parser` and the front door,
   which is why it now names the writing mode of `events` rather than the whole command.

## Limits

- **This is a request, not a decision.** The reviewer can approve the artifact; only the owner can
  accept v1 or authorise promotion, and this submission records neither.
- **No browser was used**, as in UI-6. The manual pass in §2 of the readiness record is proposed,
  not performed here.
- **The environment claim is a measured run, not a support matrix**: macOS 27.3 (arm64), CPython
  3.14.4, on the machine that ran the suites. The off-macOS degradation is read from code, not run.
- **Only this file is machine-checked.** `READINESS.md` is prose; the counts and lifetimes it states
  (seven verbs, three roles, 8 hours, 1 hour) are cited from the code paths in the table above, not
  re-derived by a span.
- **Live registry health is not claimed.** `validate_work.py`'s pre-existing errors in other programs
  are recorded in `evidence/UI-6.md` and are neither this slice's nor re-asserted here.

## Review focus

The reviewer should attack the honesty of the register, not the prose quality:

- Does the Mutation row now understate nothing? It is the row two reviews found false, most recently
  on completeness, so check both halves: the envelope against `actions.py` and `identity.py`, and the
  outside-v1 list against `build_parser` and the front door — including that `events` appears only
  through its writing mode and `board titles` sits with the other store-writing `board` actions.
- Is the corrected §7 pointer now limited to what this task can actually write, without leaving the
  owner's plan item looking done?
- Is "partly met" on criterion 8 the right verdict, and is an owner manual pass a proportionate
  closure rather than a way to avoid testing?
- Does any recommendation quietly widen the envelope — remote use, a scheduler control, multi-user
  identity — while the code still refuses it?
- Is the taxonomy argument against a `docs/` copy sound, or does promotion actually require a product
  copy here?
- Does anything in `READINESS.md` read as an acceptance, a release, or a promotion that did not
  happen?
