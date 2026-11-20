# Loom UI — acceptance verification

This document is the UI-6 verification record for the local Loom UI. It names what was checked,
how to re-run it, what the checks actually prove, and what they do **not** prove. The executable
half lives in `ui/tests/test_verification.py`; the per-module suites under `ui/tests/` remain the
unit record.

Acceptance source: `wayfinder/loom-ui/work-items.md` (UI-6) and the reviewed plan
`plans/loom/2026-11-15-ui-plan.md` (verification plan and UI acceptance criteria 1–8).

## Run

From `capabilities/work`:

```bash
python3 -m unittest ui.tests.test_verification -v     # 44 checks, the matrix below
python3 -m unittest discover -s ui/tests -t ui/tests  # 323 UI checks in total
python3 -m unittest discover -s tests                 # 341 engine checks, 2 skipped
```

The verification module starts a real loopback server over a disposable SQLite registry and, where
the check concerns parity, runs the real `loom` front door as a subprocess. It never reads or writes
the live registry.

## What is verified

Each row is one acceptance dimension. "Check" is the class in
`ui/tests/test_verification.py`; the tests inside that class are the named proof.

| # | Dimension | Check class | What it proves |
|---|---|---|---|
| 1 | Read-model parity | `ReadModelParity` | Every task, claim, dependency and state the UI publishes equals `loom list --json` / `loom show --json` for the same snapshot. The claimable set equals what `loom claim` **accepts**, probed on a throwaway copy of the registry. Graph edges are the engine's dependency edges, not a second graph. |
| 2 | Identity spoofing | `IdentityBoundary` | Ten browser-supplied actor fields are refused with `400` before any subprocess runs; query strings, `X-Agent`/`X-Forwarded-User`/`Authorization` headers, ambient `LOOM_AGENT`/`WORK_AGENT`, a forged cookie and a missing session cannot change the actor. A reviewer session cannot lend itself an implementer role. |
| 3 | Version mismatch | `VersionBoundary` | With the engine tree drifted, every data surface (pages, JSON APIs, health) answers `503` and leaks no task content. The action confirmation and POST close too, with no form and no CSRF token. `/api/version` still reports the mismatch, because that is diagnosis, not data. |
| 4 | Loopback and origin | `NetworkBoundary` | The listener is `127.0.0.1`; non-loopback binds are refused at construction; foreign or missing `Host` is `421`; a wrong port is `421`. GETs from a foreign `Origin` are `403`; POSTs require `Origin` exactly `http://Host` (missing, `null`, `https`, `localhost` and foreign are all refused). No response grants CORS. Security headers are on pages and JSON. |
| 5 | Stale writes | `StaleWriteBoundary` | A moved revision is refused `409` with **zero** engine invocations. A changed fencing epoch reaches the engine once and is refused `409` — no retry. An expired lease removes the action and explains takeover. A timeout is reported as an unknown outcome (`504`) and writes nothing. Takeover is offered only after the lock delay, and the new epoch is visible. |
| 6 | XSS-safe task content | `ContentSafety` | Six payloads are placed in titles, goals, claim agent, blocked reason, question text and review notes. Across eight pages and a confirmation page no `script`/`img`/`iframe`/`object` element and no `on*` attribute is produced; no `javascript:` href is produced; each payload survives as escaped text. JSON returns the original text unchanged, so escaping is presentation, not corruption. |
| 7 | Accessible graph alternative | `GraphAccessibility` | The default view is a program chooser, not a graph. A focus closure has a complete labelled table for every node and edge, with an `img`-role SVG and a scrollable labelled container. Above the 60-node drawing limit no SVG is drawn, the page says so, and the text table still links every task. |
| 8 | Task inspection | `TaskInspection` | The page separates state/claimability, claim and lease, dependencies, evidence, review, integration receipt, attempts and event history. Review verdict, receipt, evidence presence and event anchors match `/api/tasks/<id>`. A missing evidence file is reported as missing, not filled in. |
| 9 | Blocked-task explanation | `BlockedWork` | Dependency, write-path, expired-lease and human/agent blockers each name the gate and what would open it. The owner queue contains every task `loom list --awaiting human` reports, and human- and agent-blocked work land in different queues. |
| 10 | Decision flow | `OwnerJourneys` | A paired reviewer opens the owner queue, follows the task, opens the confirmation, submits an answer over HTTP with a cookie jar and redirect following, gets an outcome page and an event anchor, and the answer is recorded against the right question. A paired owner accepts integrated work from the acceptance queue; the task becomes `accepted` at revision + 1 with the evidence path on the event, and leaves the queue. |
| 11 | Accessible owner flows | `ContentSafety.test_every_page_has_the_accessibility_scaffold` | Every page and the confirmation form has `lang`, a title, exactly one `h1`, a `main` landmark, a skip link as the first link, navigation landmarks, no script, no unlabelled control, no positive `tabindex`, no unscoped `th`, no uncaptioned table, no duplicate `id`. |

### Notes on individual checks

- **Claimability parity is measured against `loom claim`, not `loom list --ready`.** The CLI's
  `--ready` filter checks state, dependencies and path conflicts but not a lapsed lease, so it
  lists `demo/W-7` while `claim` refuses it. The UI models the claim gate; the probe copies the
  database and runs the real `claim` on the copy, so the fixture is not mutated.
- **Version checks need a paired session.** The action confirmation answers `401`/`403` for a
  missing session or an unconfigured policy before it reads the registry, so the check pairs first;
  otherwise it would prove the identity gate and not the version gate.
- **The XSS corpus distinguishes markup from text.** Plain text such as `javascript:alert(1)` is
  correctly rendered as text; the assertion is that it never becomes an `href` or an element.
  An already-escaped-looking payload is skipped by the "raw string absent" check because another
  payload's correct escaping produces the same characters.

## Results on this branch

| Suite | Command | Result |
|---|---|---|
| Verification | `python3 -m unittest ui.tests.test_verification` | **44 tests OK** |
| UI | `python3 -m unittest discover -s ui/tests -t ui/tests` | **323 tests OK** |
| Engine | `python3 -m unittest discover -s tests` | **341 tests OK, 2 skipped** |

`python3 validate_work.py` reports pre-existing errors on integration receipts in other programs
(e.g. `uds/W-1`, `loom/LOOM-10`, `teach/W-7`). They are unrelated to this slice: no registry or
receipt in this branch was changed. The registry was not edited for this task.

## Limits

These are the things this verification does not establish. They are named so UI-7 does not read
silence as coverage.

1. **No real browser is used.** The journeys use Python's cookie jar and redirect handling, which
   is the part of a browser a server can observe. There is no JavaScript engine, no CSS layout, no
   zoom/reflow check, no responsive breakpoint check, no screenshot diff and no real screen-reader
   output. The accessibility checks are structural (landmarks, labels, captions, table scope,
   focus order in markup), not a WCAG 2.2 AA conformance audit.
2. **Keyboard use is inferred from markup, not driven.** The pages contain no script and use
   links, native controls and a skip link; no automated tab-order walk is performed.
3. **Disposable fixtures, not the live registry.** Every check builds its own work home. Values
   from the live registry can differ in size and content (and the live registry currently has
   unrelated validator errors).
4. **IPv4 loopback only in the HTTP checks.** The bind rule accepts `::1`, and the unit suite
   covers the bind refusal, but the journey server is addressed over `127.0.0.1`.
5. **Timing margins.** Lease-expiry checks use fixtures relative to the wall clock with hours or
   days of margin; they do not test the exact instant a lease expires.
6. **Action execution is one real subprocess path.** Claim, renew, takeover, transition, ask and
   answer are exercised through the real front door; timeouts use a stub runner because a real
   timeout would take 90 seconds. Failed and denied exit codes are covered by the existing action
   suites and reused here through the real conflict path.
7. **Fixed corpus.** XSS, origin and actor-field checks use a fixed, documented corpus, not
   fuzzing or property-based generation.
8. **Single browser, single user.** Two identities are configured in the fixture, but concurrency
   between two paired browsers, session fixation beyond the cookie HMAC, and multi-user
   deployment are not exercised.
9. **No transport security.** The UI is loopback HTTP by design. TLS, a reverse proxy and a LAN
   deployment are out of scope and refused by construction, not verified here.
10. **Operations and scheduler values** are covered by the UI-1/UI-4 unit suites. This module
    asserts the operations page renders without a control surface and without leaking content,
    not that its values match a live scheduler.

## Remaining owner decisions

These come from the plan's "Owner decisions before implementation" and are still open for UI-7.
None of them is answered by this verification; the code is written so each one can be answered
without weakening the boundary.

1. **Deployment model.** Local single-user browser UI (current implementation) vs a packaged
   desktop app vs a remote shared service. Remote use needs authenticated identities, transport
   security, tenancy and an operational owner; none of that exists in this slice.
2. **Mutation scope.** The implemented scope is read-only by default, then guarded task actions.
   Confirm that global `tick --apply`, worker dispatch and scheduler/deploy controls stay CLI-only
   for v1, or commission a separate scoped design.
3. **Identity and roles.** Confirm the local identity file/token pairing is acceptable, whether
   more than one local person needs separate roles, and whether session lifetime and the
   restart-invalidates-sessions rule match how the owner works.
4. **Technology.** The implementation is a stdlib Python server rendering HTML. No separate
   frontend was added; confirm that remains the choice.
5. **Linear projection.** The UI does not embed or call the Linear projection. Decide whether it
   should link to it; the registry remains authoritative either way.
6. **Supported environments.** The checks were written and run on macOS. A cross-platform claim
   needs its own run (launchd-specific operations sources are known to be macOS-shaped).
7. **Promotion.** Whether to copy this slice and its evidence into the product repository's
   `docs/` under a central promotion record is UI-7's decision, not this task's.

## What was deliberately not changed

No requirement, contract or acceptance text was edited. No engine rule, action allow-list,
transition rule or persistence format changed. The slice adds verification code and this document;
the product code under `ui/loom_ui/` is unchanged.
