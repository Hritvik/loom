# UI-6 — Verify UI security, compatibility and core journeys

Task: `loom-ui/UI-6` · branch `loom/loom-ui-ui-6-a1` · write path `capabilities/work/ui`
Plan: `plans/loom/2026-11-15-ui-plan.md` (private planning repo) · items:
`wayfinder/loom-ui/work-items.md`

Goal: demonstrate that the UI preserves Loom's state and authority boundaries and supports the
agreed owner workflows, and record what was verified, what it does not prove, and which decisions
remain with the owner.

## What was built

An acceptance-level verification suite over a live loopback server, a real SQLite registry and the
real `loom` front door, plus the written record.

| File | Role |
|---|---|
| `ui/tests/test_verification.py` | 44 checks in ten classes, one per UI-6 dimension: read-model parity, identity, version, network, stale writes, content safety and accessibility, graph alternative, inspection, blocked explanation, owner journeys |
| `ui/VERIFICATION.md` | the acceptance record: matrix, commands, results, limits, remaining owner decisions |
| `ui/README.md` | points at the verification record |

Product code under `ui/loom_ui/` was **not changed**. No engine rule, transition, action allow-list
or persistence format changed. No requirement, contract or acceptance text was edited.

## What is verified

| Dimension | Check class | What it proves |
|---|---|---|
| Read-model parity | `ReadModelParity` | Every task, claim, dependency and state equals `loom list --json` / `loom show --json` for the same snapshot; the claimable set equals what `loom claim` accepts; graph edges are the engine's edges |
| Identity spoofing | `IdentityBoundary` | Ten actor fields refused before execution; query, header, ambient-env, forged-cookie and role-lending attempts cannot choose the actor |
| Version mismatch | `VersionBoundary` | Every data surface answers `503` on a drifted engine with no task content; action GET and POST close too; `/api/version` still reports the mismatch |
| Loopback / origin | `NetworkBoundary` | Loopback-only listener, foreign/missing `Host` refused, exact `Origin` on POST, no CORS, security headers on pages and JSON |
| Stale writes | `StaleWriteBoundary` | Moved revision refused with zero engine calls; changed epoch refused once with no retry; expired lease explains takeover; timeout is an unknown outcome; takeover only after the lock delay |
| XSS-safe content | `ContentSafety` | Six payloads across eight pages and a confirmation: no live element or `on*` attribute, no `javascript:` href, escaped text preserved, JSON lossless; structural accessibility scaffold on every page |
| Accessible graph | `GraphAccessibility` | Chooser by default; complete labelled table per node and edge; above 60 nodes nothing is drawn and the table still links every task |
| Task inspection | `TaskInspection` | State/claimability, lease, dependencies, evidence, review, receipt, attempts and history are separate facts traceable to `/api/tasks/<id>`; missing evidence is reported as missing |
| Blocked explanation | `BlockedWork` | Dependency, write-path, expired-lease and human/agent blockers name the gate; the owner queue contains every task `loom list --awaiting human` reports |
| Decision flow | `OwnerJourneys` | A paired reviewer answers a question and a paired owner accepts integrated work over HTTP with a cookie jar and redirects; the outcome links the event that was actually written |

## Decisions and their reasons

1. **Parity is measured against `loom claim`, not `loom list --ready`.** `--ready` ignores a lapsed
   lease and lists a task `claim` refuses, so it is not the authority the UI models. Each parity
   probe copies the registry and runs the real `claim` on the copy, leaving the fixture untouched.
2. **The checks are end-to-end, not helper calls.** A check that calls the same helper the page
   calls cannot show the surface a person uses preserves the boundary. The verification module
   starts a server and, where the claim is about parity, runs the front door as a subprocess.
3. **A browser-shaped client, not a browser.** The journeys use a cookie jar and follow redirects,
   which is what a server can observe. No JavaScript engine, layout or screen reader is pretended;
   that limit is written down rather than implied.
4. **Stale pages never reach the engine.** The conceptual split is: a page is a sample, the engine
   is the authority. The moved-revision check asserts zero subprocess invocations; the changed-epoch
   check asserts exactly one attempt and no retry.
5. **XSS checks are structural.** The assertion is the absence of a live element or `on*` attribute,
   not a substring hunt: `onerror=` appears safely inside escaped text, and an escaped-looking
   payload is exactly what another payload's correct escaping produces.
6. **The version gate is tested with a paired session.** The identity gate answers first, so pairing
   is necessary for the version check to prove the version gate rather than the session gate.
7. **A large portfolio is a real fixture.** `GraphAccessibility.prepare` adds 35 tasks so the demo
   program exceeds the 60-node drawing limit; the check proves the table remains complete when the
   picture is deliberately withheld.

## Verification

All commands run from `capabilities/work` on this branch.

```bash
python3 -m unittest ui.tests.test_verification        # Ran 44 tests — OK
python3 -m unittest discover -s ui/tests -t ui/tests  # Ran 323 tests — OK
python3 -m unittest discover -s tests                 # Ran 341 tests — OK (skipped=2)
```

Per class in `ui/tests/test_verification.py`: `ReadModelParity` (4), `IdentityBoundary` (5),
`VersionBoundary` (3), `NetworkBoundary` (7), `StaleWriteBoundary` (5), `ContentSafety` (5),
`GraphAccessibility` (4), `TaskInspection` (4), `BlockedWork` (5), `OwnerJourneys` (2).

`python3 validate_work.py` reports pre-existing errors on integration receipts in other programs
(`uds/W-1`, `uds-client/W-1`, `modelswitch/W-1`, `loom/LOOM-10`, `midas/W-8`, several `teach`
tasks, `loom/LOOM-15`). None is in this slice's paths and no registry or receipt was changed here.
The validator failure is recorded rather than hidden; it is the coordinator's to resolve.

## Limits

Written in full in `ui/VERIFICATION.md`. The material ones:

- No real browser: no JavaScript, CSS layout, zoom, responsive or screen-reader output; the
  accessibility checks are structural and do not claim WCAG 2.2 AA conformance.
- Keyboard order is inferred from markup (links, native controls, skip link), not driven.
- Disposable fixtures, not the live registry; IPv4 loopback in the HTTP journeys.
- Lease windows use wall-clock margins of hours/days, not the exact expiry instant.
- A fixed XSS/origin/actor corpus, not fuzzing; one browser and two identities, not concurrency.
- Loopback HTTP only; TLS, proxy and LAN deployment are refused by construction, not verified.
- Operations/scheduler value parity stays with the UI-1/UI-4 unit suites; this module checks the
  operations page has no control surface and leaks no content.

## Remaining owner decisions

1. **Deployment model** — local single-user browser UI (current), packaged desktop, or remote
   shared service. Remote needs authentication, transport security, tenancy and an operator.
2. **Mutation scope** — keep global `tick --apply`, dispatch and scheduler/deploy controls CLI-only
   for v1, or commission a separately scoped design.
3. **Identity and roles** — is local token pairing with restart-invalidated sessions acceptable;
   does more than one local person need separate roles?
4. **Technology** — stdlib Python server rendering HTML remains the choice.
5. **Linear projection** — link to it or not; the registry stays authoritative.
6. **Supported environments** — macOS was the written and run environment; a cross-platform claim
   needs its own run.
7. **Promotion** — whether to copy the slice and its evidence into the product repository is UI-7's
   decision, not this task's.

## Review focus

The reviewer should check that the matrix in `ui/VERIFICATION.md` maps each UI-6 acceptance
dimension to a check that actually exercises it, that the limits are honest rather than defensive,
and that no check proves a helper while appearing to prove the surface. The parity probes and the
two browser-shaped journeys are the places a weaker suite would have taken a shortcut.
