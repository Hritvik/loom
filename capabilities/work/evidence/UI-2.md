# UI-2 — Deliver the owner control room and task inspector

Task: `loom-ui/UI-2` · branch `loom/loom-ui-ui-2-a1` · write path `capabilities/work/ui`
Plan: `plans/loom/2026-11-15-ui-plan.md` (private planning repo) · items:
`wayfinder/loom-ui/work-items.md`
Depends on: `loom-ui/UI-1` (satisfied) · blocks `loom-ui/UI-5`, `loom-ui/UI-6`

## What was built

The owner-facing half of the read-only UI: a control room that says what needs a person, an index of
every task, and an inspector that explains one task without conflating its state with its
claimability or its review with its integration.

| File | Role |
|---|---|
| `ui/loom_ui/control.py` | queue assignment (one bucket per task), task detail, review/receipt/question/attempt/event views |
| `ui/loom_ui/pages.py` | control room, task index and task inspector |
| `ui/loom_ui/templates.py` | shared shell, CSS, escaping, freshness, provenance table, fail-closed pages |
| `ui/loom_ui/server.py` | `/tasks`, `/task/<id>`, `/api/control`, detail on `/api/tasks/<id>`, error pages |
| `ui/loom_ui/provenance.py` | one evidence-location rule shared by the portfolio signal and the inspector |
| `ui/tests/test_control.py` | 33 tests over queues, detail and rendering |
| `ui/tests/test_pages.py` | 23 HTTP/accessibility tests |
| `ui/tests/support.py` | `ControlHome`: a fixture with one task in every queue and real review/receipt history |

Suite total: **119 tests, OK** (63 existing, 56 new).

## Decisions and their reasons

1. **Human work is the engine's own marker, not a heuristic.** "Needs you" is
   `blocked` + `blocked_needs: human`, a *current* review request addressed to an owner identity, or
   an unanswered `ask` to one. Owner identities default to `engine.HUMAN_IDENTITIES`
   (`{"human", "owner"}`) and `--owner` adds more. The engine names no portfolio's people, and this
   module does not either; UI-5 owns binding a configured human session.
2. **Every task is in exactly one queue.** Queues are ordered from human decisions to settled:
   needs-you → stalled → review → integration → acceptance → in-progress → claimable → waiting →
   settled. First match owns the row, and `counts["queued"] == counts["total"]` is asserted, so two
   counters cannot disagree about the same task. `settled` is counted but not rendered as a daily
   queue.
3. **Review, integration and acceptance are separate facts.** A green task is not reviewed; a
   reviewed task is not integrated; an integrated task is not accepted. Each row says which stage it
   is at, and an approval that has not become `reviewed` is shown as "approved by reviewer; the
   reviewed transition has not been recorded" rather than "awaiting reviewer".
4. **A lapsed lease blocks a claim, not a review.** An expired lease puts a `ready`/`claimed`/`red`
   task in Stalled. On `green` the request is bound to a commit, not the lease, so the task stays in
   Awaiting review and the row names the expired lease instead of hiding the review.
5. **The inspector reuses the engine's folds.** Review currency is `engine.latest_review_request`,
   the receipt is `engine.integration_receipt`, attempts are `engine.attempt_views` +
   `engine.attempt_payload`, and history is `engine.task_history` over the snapshot's events (a
   one-method adapter, so "which event is a receipt" has one answer). Nothing here re-decides a
   gate.
6. **State and claimability are rendered from separate fields, side by side.** The facts block shows
   both; `claimable` says "yes — claim would succeed" or "no", and "Why it can or cannot be claimed"
   lists the engine's reasons and typed blockers. A `ready` task that is not claimable stays visibly
   `ready`.
7. **Freshness is per sample.** Every queue caption and the page header carry the registry
   `as_of` and its age; a sample older than 60s is labelled `STALE SAMPLE`. Each provenance source
   keeps its own `as_of` and age. A source sampled a moment after the page clock is "just now", not
   "in the future".
8. **Unavailable is not a version problem.** UI-1 reused the version-gate page for an unreadable
   registry; it claimed the gate had closed. There is now a distinct "Loom UI cannot read the
   registry" page, and an unexpected render failure is `500` with no partial view.
9. **No file serving.** Evidence is shown as a locator, its resolved path, existence, size and mtime.
   There is no route that reads an arbitrary path, so the inspector adds no traversal surface.
10. **Accessibility is structure, not decoration.** One `h1`, a skip link, landmarks, `aria-current`,
    a focus ring, a caption and column `scope` on every table, focusable scroll regions, and text for
    every state. Asserted per page in `test_pages.py`, not once globally.
11. **Access logs no longer carry the query string.** The filter value is caller-chosen; the log
    keeps method and path only.
12. **The repo's boundary scanner caught a shortcut.** The first version compared `queue.id` to the
    literal `"settled"`; `tests/test_boundary.py` flagged it as a namespace branch. The queue now
    carries `in_daily_queues`, so no caller compares an id to a literal. The scanner is right to be
    blunt here.

## Verification

All commands run from `capabilities/work` on this branch (`f9f0c27` before the evidence commit).

### UI suite — 119 tests, `OK`

```bash
$ python3 -m unittest discover -s ui/tests -t ui/tests
Ran 119 tests in 28.7s
OK
```

Coverage by file: `test_snapshot.py` (9), `test_model.py` (14), `test_version.py` (8),
`test_provenance.py` (16), `test_server.py` (16), `test_control.py` (33), `test_pages.py` (23).
The 63 pre-existing tests pass unchanged: `support.py` only gains the richer `ControlHome`
subclass and leaves `Home` untouched, and the control room keeps the index-page phrases UI-1's
tests assert.
The new tests assert, among others: one queue per task and counts that sum; owner routing from
`HUMAN_IDENTITIES` and `--owner`; current vs stale vs superseded review requests; rework, rejected,
agent-blocked and never-submitted stalls; bound vs incomplete receipts; approved-but-not-transitioned
green work; an expired lease on green not hiding the review; detail facts matching the engine's own
`integration_receipt` and `attempt_views`; evidence present/missing/unrecorded; the appended event
cap; `404` HTML; `405`/`421`/`403` on every new route; HEAD with no body; the distinct unavailable
page; empty queues; XSS-shaped titles, reasons and questions; and the accessibility scaffold on all
three pages.

### Engine suite — 276 tests, `OK` (2 skipped)

```bash
$ python3 -m unittest discover -s tests
Ran 276 tests in 90.0s
OK (skipped=2)
```

```bash
$ python3 -m unittest tests.test_boundary
Ran 19 tests in 0.5s
OK (skipped=2)
```

Nothing outside `capabilities/work/ui` (and this evidence file) was modified.

### Shared registry validator — 1 error, pre-existing and outside this slice

```bash
$ python3 validate_work.py
...
ERROR midas/W-8: heading W-8 missing from midas.program
FAIL (1 errors, 109 warnings)
```

The error is a `midas` task heading missing from another repository's program document. It does not
touch `capabilities/work/ui`; the same class of shared-registry drift was reported by UI-1. The
warnings include `loom-ui/UI-5..UI-7` "ready but waiting on loom-ui/UI-2", which this branch resolves
for UI-5/UI-6 but not in the registry revision.

### Live read-only smoke test (real registry, 221 tasks)

The UI was served from a **disposable copy** of the promoted release with this branch's `ui/`
installed and the copy's tree digest recorded in its own `engine.release`. That exercises the
`PINNED` path with byte-identical engine sources; **it is not a promotion, and the installed release
was not touched**. `loom deploy status` afterwards still reports `digest: verified`.

```text
health   200  verdict=PINNED
GET /api/control   200 in 0.03s   total=221  queued=221   as_of=2026-11-16T03:24:43Z
GET /          200  111,831 bytes in 0.94s
GET /tasks     200  137,788 bytes in 0.82s
```

One sample of the queues (live data moves under the run — `midas/W-1` moved from
awaiting-review to changes-requested between two samples, which is the point of showing `as_of`):

```text
needs-you      2   portfolio/INFRA-6, portfolio/INFRA-12  (blocked, needs a human)
stalled       16   e.g. iam/W-12 rejected; midas/W-1 changes requested; expired/agent-blocked work
review         1
integration   43
acceptance    61
in-progress    2
claimable      0
waiting       65
settled       31
```

Every real task inspector rendered: **221 of 221 returned 200** (187s for the whole portfolio; each
HTML page re-samples six provenance sources including `git` over up to 20 repositories, so ~0.85s a
page is the cost of not sharing a stale sample).

Real findings the pages report instead of smoothing over:

- `ums/W-18` reviewed, approval bound to 10 paths, **evidence file missing** → partial row and
  "file not found" in the inspector.
- `iam/W07-A` reviewed with **no review verdict recorded** on the task.
- `dutch-ref/W10-A`, `W10-B` integrated with receipts that **record no content digest** → shown as
  incomplete, exactly as `engine.integration_receipt` and `require_reviewed_source` treat them.

Boundaries against the live surface:

```text
POST /api/control                405  Allow: GET, HEAD  error=read_only
POST /task/loom-ui/UI-2          405  Allow: GET, HEAD  error=read_only
Host: evil.example               421  error=host_not_loopback
Origin: https://evil.example     403  error=origin_rejected
GET /task/nope/NOPE              404  HTML "Not found"
GET /tasks?program=loom-ui       200  access log line: "GET /tasks 200" (no query string)
```

## Limits and known gaps

- **The control room has no search box or free-text filter.** Programs are filtered by links and a
  task is opened by id or from All tasks. Phase 1's "program/task search" is partially met; a search
  input would need its own query-string design and `form-action` review. Flagged for review.
- **Owner identity needs `--owner` for a personal identity.** In the live registry 15 review
  requests are addressed to a person's name, which is not in `engine.HUMAN_IDENTITIES`, so they are
  not in "Needs you" by default. Hard-coding a person would break the engine's boundary rule; the
  flag is the interim answer and UI-5 owns the configured session identity.
- **Acceptance queue is large** (61 integrated tasks today). It is a true backlog, not a bug, but it
  is the least discriminating queue. Whether it belongs in "Needs you" is a review question.
- **Integration blockers are not evaluated.** The inspector shows the receipt and whether it is
  complete; it does not run the tick's `merge_needed` / `paths_diverged` / `receipt_missing` git
  checks. Those are policy work owned by tick/UI-4 and would make a page load run git history walks.
- **No auto-refresh.** With no script, a page is a static render; staleness is labelled rather than
  fixed. Reload is the refresh.
- **Event history is capped at the last 50 events per task**; the total is named. One real task has
  31 events, so this is not yet hit in practice.
- **Attempt liveness is a point-in-time probe**, inherited from UI-1.
- **Per-page provenance is ~0.85s.** Fine for one owner, expensive for a test that walks 221 pages.
  A bounded cache would need its own freshness semantics; not added here.
- **The evidence file is written outside the code write path** at the harness's instruction
  (`evidence/UI-2.md`), as UI-1 did.
- **`validate_work.py` is red for one unrelated task** (`midas/W-8` heading), not for this slice.

## Review asks

1. **Queue precedence.** One bucket per task, ordered needs-you → stalled → review → integration →
   acceptance → in-progress → claimable → waiting → settled. Is that the right order for an owner's
   day, or should "Ready for acceptance" (61 today) sit inside "Needs you" despite its size?
2. **Expired lease on green.** I let a lapsed lease leave a green task in Awaiting review (the
   request is bound to a commit) and named the lease in the row. Is that the correct reading of
   `review_request_sweep`, or should it be Stalled until the lease is released or taken over?
3. **Owner identity.** Is a repeatable `--owner` flag an acceptable interim default, given that the
   engine's human set does not include the portfolio's actual person, and UI-5 will bind the
   session? Or should this slice refuse to guess and require the flag?
4. **Approved-but-not-reviewed.** Green work whose current request is approved but not yet
   transitioned is shown as "approved by reviewer; the reviewed transition has not been recorded".
   Is that the right owner-facing statement, or should it be its own queue?
5. **Integration readiness.** I deliberately do not evaluate merge-needed / paths-diverged /
   receipt-missing, only the receipt's existence and digest. Is that the right split with UI-4/tick,
   or does an owner need those verdicts on the inspector now?
6. **Accessibility.** The pages are server-rendered with a skip link, landmarks, captions, `scope`
   and focusable scroll regions; no screen-reader or keyboard walkthrough was performed. Is the
   markup sufficient for UI-6's accessibility verification, or should a legend for state colours and
   a visible caption option be added now?

## Reproduce

```bash
cd capabilities/work
python3 -m unittest discover -s ui/tests -t ui/tests        # 119 tests
python3 ui/serve.py --port 8788                             # browse http://127.0.0.1:8788
python3 ui/serve.py --port 8788 --owner <your-identity>     # route your own questions/reviews
python3 -m unittest discover -s tests                       # 276 engine tests
```

The UI must run from the promoted release to reach `PINNED`; from an unpromoted checkout it reports
`MISMATCH` and refuses to serve, as UI-1 designed.
