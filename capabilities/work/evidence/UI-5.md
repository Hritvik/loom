# UI-5 — Add authenticated, guarded task actions

Task: `loom-ui/UI-5` · branch `loom/loom-ui-ui-5-a3` · write path `capabilities/work/ui`
Goal: supported task actions through the `loom` front door, with server-side human identity
mapping, CSRF/origin defenses and stale-write handling; no global apply or dispatch.

The slice was implemented and approved at `a289114` (attempt `a1`). It could not integrate because
the UI directory moved under it when UI-3 and UI-4 landed, so this attempt (`a3`) rebases that
reviewed commit onto `8cc57c0` and records every conflict resolution in
[Integration candidate](#integration-candidate-attempt-a3). The action code itself is unchanged.

## What was built

A paired person can claim, renew, release, take over, move a task through a legal transition, ask a
question and answer one — from a confirmation page that shows the revision, epoch and lease it is
about to present. Every action is executed by the supported `loom` front door as a subprocess with an
argument array, and every success links to the audit event it wrote.

| File | Role |
|---|---|
| `ui/loom_ui/identity.py` | server-side human identities and roles; empty by default; `--human` / `~/.config/loom/ui.json` |
| `ui/loom_ui/session.py` | pairing tokens, HMAC-signed session cookies, CSRF tokens, one-shot form nonces |
| `ui/loom_ui/actions.py` | the action allow-list, per-task gating, submission validation, argv construction, subprocess execution, conflict classification, audit-event lookup |
| `ui/loom_ui/action_pages.py` | the confirmation form and the outcome page |
| `ui/loom_ui/server.py` | `/pair`, `/api/session`, the confirm route, the POST route, strict Origin for POST, body limits |
| `ui/loom_ui/pages.py` | an Actions section on the inspector; `#event-<seq>` anchors on history rows |
| `ui/loom_ui/templates.py` | form styles, the signed-in identity in the shell, an accurate footer, a generic notice page |
| `ui/loom_ui/control.py` | dropped the `read_only` field from the control payload (the server can act now) |
| `ui/tests/test_identity.py` | 12 tests |
| `ui/tests/test_session.py` | 15 tests |
| `ui/tests/test_actions.py` | 33 tests, including an engine-parity case per refusal and eight real subprocess actions |
| `ui/tests/test_action_server.py` | 34 tests over pairing, CSRF, Origin, staleness, replay, roles, escaping, and the closed routes |
| `ui/tests/support.py` | `ActionHome`: one task per guarded-action state, real leases relative to the wall clock |

Suite totals at `a289114`: **213 tests OK** in `ui/tests` (119 before this slice, 94 new); the engine
suite (`capabilities/work/tests`) was **328 tests OK, 2 skipped**, including the boundary scan that
caught two literals this slice introduced (both fixed). After the rebase onto UI-3 and UI-4 the same
suite is **275 tests OK** (213 + the 62 shipped by those slices) and the engine suite is **341 tests
OK, 2 skipped**; see [Integration candidate](#integration-candidate-attempt-a3).

## Decisions and their reasons

1. **Read-only is the absence of a configuration, not a flag.** With no `--human` and no identity
   file, `Policy` is empty: no pairing token is generated, `SessionStore.pair` can never succeed,
   every action route answers `403`, and the task page says why. `--read-only` exists only to ignore
   a configuration that is present. A future regression cannot enable actions by forgetting a flag.
2. **The browser never names the actor — it is refused, not ignored.** `agent`, `actor`,
   `acting_as`, `identity`, `user`, `username`, `owner` and `impersonate` in a POST body produce
   `400` with "the browser may not name the acting identity". Silently dropping them would leave a
   field that looks like it works, and would hide a caller that believes it is choosing.
3. **Pairing binds identity server-side.** Startup prints one token per configured identity. The
   session id is random and carried as `sid.HMAC(secret, sid)` with `HttpOnly; SameSite=Strict`;
   the HMAC secret is generated at process start, so a restart invalidates every session and nothing
   is written to disk. The token is not consumed on use — it is a local pairing secret printed to
   the terminal, and re-pairing after clearing cookies should not need a restart.
4. **Three overlapping defences on the POST, none of them the only one.** Exact `Origin` equal to
   `http://Host` (missing Origin is refused, not assumed), `application/x-www-form-urlencoded` only,
   a per-session CSRF token compared in constant time, and a one-shot form nonce consumed before
   anything executes. A body over 16 KiB is refused without being parsed.
5. **Two gates, not a second state machine.** `actions.catalogue` decides what may be *offered* to
   this session (role policy + the task's current state + the engine's own claimability view);
   `loom` decides whether the action *succeeds*. The parity tests run the engine command for each
   refusal and assert the engine refuses it too, so the UI gate is neither stricter nor looser by
   accident.
6. **What the UI deliberately does not offer.** `transition → reviewed` (a reviewer's answer is
   recorded by the coordinator), `transition → integrated` (a receipt for a commit already merged,
   not a merge button), and `reviewed → accepted` (needs the exact approved commit as proof). The
   reasons are `EXCLUDED_TARGETS` in `actions.py` and in the README. `tick`, `tick --apply`,
   `workers`, `schedule`, `deploy`, `linear`, `annotate` and every other verb are absent from
   `ENGINE_VERBS`; a test asserts the frozenset, and there is no route for them.
7. **Staleness is shown and refused, never retried.** The confirmation page prints the revision,
   task epoch, claim epoch and lease with their ages and warns when the sample is older than 60s.
   The POST re-reads the registry, recomputes the option, and refuses a page whose revision moved
   before executing. Exactly one subprocess runs; a timeout is reported as an **unknown outcome**
   with an instruction to re-read, because claiming either way would be a guess.
8. **Replay is a conflict, not a duplicate event.** The nonce is consumed before validation, so a
   reloaded or resubmitted form always answers `409` "already submitted". `ask` has no revision
   guard in the engine, which is exactly why the UI needs its own one-shot token.
9. **Attribution comes from the session and the log, not from the page.** The subprocess environment
   strips `WORK_AGENT`, `WORK_DB`, `WORK_OWNER`, `WORK_HOME` and `LOOM_CONFIG`, and `--agent` is the
   session identity. The success page re-reads the snapshot and looks for a new event of an
   action kind, on that task, by that agent; the attempt record a claim also writes is not the audit
   event. If no event can be identified the page says so instead of showing an older one.
10. **Everything the person sees is escaped, and there is still no script.** The confirmation and
    outcome pages go through the same `esc` and the same shell; the CSP keeps `default-src 'none'`
    and now allows `form-action 'self'` so the form can submit. Task titles in a confirm page are
    escaped (tested with a `<script>` title).
11. **The version gate closes actions too.** The POST checks the gate before authentication, so a
    drifted release cannot mutate anything through the UI. A test drifts the engine and asserts
    `503` with the registry unchanged.

## What was verified

```bash
cd capabilities/work
python3 -m unittest discover -s ui/tests -t ui/tests   # 275 tests, OK (post-rebase)
python3 -m unittest discover -s tests                  # 341 tests, OK (2 skipped, post-rebase)
python3 validate_work.py                               # 33 board errors, all pre-existing drift
```

`validate_work.py` reports 33 errors and 107 warnings, none of them about this slice: 30 are the
"reviewed source is not in the history of main" rule that LOOM-14 added, reported against board
receipts in other repositories, and 3 are headings missing from their program files (`loom/LOOM-14`,
`loom/LOOM-15`, `midas/W-8`). None of those inputs is in this branch — it changes no board file — so
the same run against `8cc57c0` reports the same set. The only `loom-ui` output is board drift
(`registry says claimed, program says planned`), which is expected while the task is claimed rather
than accepted.

Beyond the unit level, the full path was exercised by hand against a freshly initialised registry
(`loom init`, `loom create`, the real server, `curl` with a cookie jar), not against the live
portfolio registry:

| Step | Observed |
|---|---|
| pair with the printed token | `303` to `/`, `Set-Cookie` with `HttpOnly; SameSite=Strict` |
| `GET /task/demo/T-1/action/claim` | `200`, revision `0`, no epoch (no claim), `csrf`/`form`/`revision` hidden inputs |
| `POST` the form as the paired identity | `200` "Action recorded", engine output `claimed demo/T-1 at revision 1 … epoch 1`, audit link `#event-3` |
| replay the same body | `409` "This form was already submitted"; the registry did not change again |
| move the task with the CLI, then submit the earlier form | `409` "The task moved before your action ran"; state stayed `red` |

And the read side was spot-checked against the real registry: the inspector lists only the actions
the session may take (a task leased to an agent offers a human only `ask`), and the unavailable list
names the reason ("claimed by opencode/loom-worker", "unmet dependencies: …, write-path conflict …").

## Limits and open decisions

- **One person, one token, no revocation UI.** A token grants a session for the process lifetime
  (8h absolute, 1h idle). Restart to invalidate. Multi-user roles exist in the policy but there is no
  page for issuing, listing or revoking sessions; that is deliberate scope, and a remote or
  multi-user deployment needs a different transport design entirely.
- **Submission for review is not a UI action.** `loom ask … --review-source` needs a git commit to
  verify, and the UI has no way to prove which commit a browser means. Reviewers answer from the
  inspector; implementers still submit with the CLI.
- **The task page's action list is a snapshot.** It is recomputed on the confirm page and again on
  the POST, so a stale list cannot execute; it can only be out of date visually.
- **`ready` with an expired claim cannot be recovered from the UI.** The engine's `claim` says "use
  takeover" and its `takeover` requires an active state, so the UI lists both with their reasons
  rather than inventing a third path. This is an engine contradiction, not a UI workaround.
- **`read_only` was removed from `/api/control`**, and UI-2's assertions were updated to the new
  contract (`/api/health` now reports `actions.enabled` instead). Anyone consuming the old field
  should read `/api/health`. This is the only behaviour change to a previously reviewed surface.
- **The evidence file lives outside the declared write path** (`capabilities/work/evidence/UI-5.md`
  rather than under `capabilities/work/ui`), because the worker brief and the registry's evidence
  locator both point at it and every prior UI task recorded evidence the same way.

## Files touched outside `capabilities/work/ui`

`capabilities/work/evidence/UI-5.md` only. Nothing else in the engine or the planning repository was
edited.

## Integration candidate (attempt a3)

Reviewed source of the original attempt: `a289114` (approved against base `489deb2`). Rebased onto
`8cc57c0` (`integrate(UI-4): add read-only operations visibility`), which already contains UI-3
(`dfb5279`) and LOOM-14 (`cacf124`). The branch is one commit on top of `8cc57c0`; its tree is
`a289114`'s content with the four conflicts below resolved, and nothing removed from UI-3 or UI-4.

### Conflicts, and how each was resolved

All four conflicts are "both slices added lines to the same place". None was resolved by choosing a
side; every one is a union, and the descriptions were rewritten so they still read as one document.

| File | Conflict | Resolution |
|---|---|---|
| `loom_ui/__init__.py` | module roster and `__version__` (UI-3/UI-4 bumped `0.2.0`→`0.4.0`; UI-5 wrote `0.3.0` from the same base) | both roster lines kept (`graph`, `graph_page`, `operations`, `action_pages`); version is `0.5.0` |
| `loom_ui/server.py` | imports; the `_dispatch` route table; the method-refusal helper | imports merged (graph/operations **and** action_pages/actions/identity/session/FRONT_DOOR); route table keeps `/graph` and `/operations` **and** adds `/pair`; both `_graph_page` and the pairing/session handlers retained |
| `loom_ui/templates.py` | `shell()` signature and its footer note | one signature carries `extra_css` (UI-3/UI-4) and `actions`/`actor`/`roles` (UI-5); the identity footer note and its read-only counterpart are both intact |
| `ui/README.md` | title/intro, boundary rows, route table, the 405 sentence, test summary | one document: title names all four slices; boundary table keeps *Shared rules*, *Graph parity* and *No controls*, with *Shared rules* widened to include action legality; route table keeps `/graph`, `/operations`, the action routes and `/api/tasks/<id>/actions`; the 405 sentence now says `GET, HEAD` for reads and `GET, HEAD, POST` for an action route |

### One correction the merge forced

UI-5's method refusal answered every unsupported method with `Allow: GET, HEAD, POST`, which is
wrong for a read route — that was harmless while the only POST route was an action, but it now
contradicts UI-3's and UI-4's reviewed assertion that their pages are `GET, HEAD`. The refusal is now
route-aware (`ACTION_PATH.match(path)` decides), which is the accurate contract and keeps UI-3's and
UI-4's assertions true without editing their meaning:

- `_unsupported` returns `GET, HEAD` for a read route and `GET, HEAD, POST` for an action route.
- `ui/tests/test_server.py` asserts both cases (a read route is no longer advertised as POST-able).
- `ui/tests/test_graph.py` and `ui/tests/test_operations.py` change one expectation each, from the
  retired `read_only` error code to `method_not_allowed` — the same contract change UI-5 already
  applied to `ui/tests/test_pages.py` and `ui/tests/test_control.py` in the reviewed commit. The old
  code said "this server is read-only", which stopped being true when the action routes landed.

No other assertion from UI-3 or UI-4 was touched.

### What was verified after the rebase

```bash
cd capabilities/work
python3 -m unittest discover -s ui/tests -t ui/tests   # 275 OK
python3 -m unittest discover -s tests                  # 341 OK, 2 skipped
python3 validate_work.py                               # 33 pre-existing board errors, none here
git diff --stat 8cc57c0..HEAD                          # 22 files, capabilities/work/ui + evidence only
```

A live integration smoke (a real server over a disposable registry with a matching fake release, a
real `loom` subprocess, `http.client` and a session cookie) crossed all four slices in one session —
**35/35 checks**, including:

| Check | Observed |
|---|---|
| `GET /`, `/graph?program=demo`, `/operations` | `200`; nav carries Focus/All tasks/Graph/Operations; the graph still draws an SVG and a table equivalent; the operations page still has no form, button, input or `method=` |
| pairing | `303`, cookie `HttpOnly; SameSite=Strict` |
| `GET /task/demo/A-1` paired | `200`, header "signed in as hritvik", an Actions section, a claim link, and the graph nav entry still present |
| `GET /task/demo/A-1/action/claim` | `200`, revision/epoch/lease on the page |
| `POST` the form | `200`, engine moved the task to `claimed` with `agent: hritvik`, the page links `#event-<seq>`, and that anchor exists on the task page |
| replay the same body | `409`; no second event, no second claim |
| `PUT/PATCH/DELETE /operations` | `405` with `Allow: GET, HEAD` |
| `PUT /task/demo/A-1/action/claim` | `405` with `Allow: GET, HEAD, POST` |
| `POST /operations` | `405` with `Allow: GET, HEAD` and no body read |
| `/api/session`, `/api/health` | identity `hritvik`, roles, `actions.enabled: true` |
| `/api/graph`, `/api/operations` | both still answer their JSON shapes |
| unconfigured restart | task page renders with no form and says why; `actions.enabled: false`; graph still renders |

### Residual risk from the merge

- **The identity appears in the header only on pages that were given it.** The task, confirm and
  outcome pages show "signed in as …"; `/`, `/tasks`, `/graph` and `/operations` do not, because they
  pass no actor to the shell. That is not a regression (those pages predate actions) but it is an
  inconsistency a person can notice. `/api/session` answers the question, and every mutating page
  names the identity directly above the form. Fixing it means threading the session into the graph
  and operations page builders, which belongs in a follow-up rather than in an integration rebase.
- **Two write paths remain shared.** `capabilities/work/ui` is also the write path of UI-3 and UI-4,
  which the registry already warns about. This candidate is rebased exactly onto `8cc57c0`; if the UI
  directory moves again before integration, it will need the same treatment.
- The limits recorded above (no revocation UI, no review submission from the browser, the
  `ready`-with-expired-claim engine contradiction) are unchanged by the rebase.
