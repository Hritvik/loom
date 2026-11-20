# Loom UI — v1 operational readiness and promotion record

**Status: proposed — not accepted, not promoted.**

| Field | Value |
|---|---|
| Decision owner | the human owner of this repository |
| Prepared by | `opencode/loom-worker`, implementer of `loom-ui/UI-7` |
| Evidence for the decision | [`VERIFICATION.md`](VERIFICATION.md), [`README.md`](README.md), `capabilities/work/evidence/UI-6.md`, `capabilities/work/evidence/UI-7.md` |
| Accepted by | — |
| Accepted at (commit) | — |
| Manual criterion-8 pass | — (record the pages checked and any failure) |
| Promoted at | — |

This file is a **decision request, not a decision**. Nothing in this slice was accepted, released,
deployed, published or linked into Linear. The task's acceptance is explicit that no promotion or
release occurs without owner acceptance, so the fields above stay blank until the owner answers
[the questions in §8](#8-decision-requested) and the coordinator records the acceptance.

## 1. Recommendation

**Accept the local UI as v1 under the operating envelope in §3**, with two qualifications:

- Acceptance criterion 8 is verified **structurally**, not in a browser. Responsive layout, zoom and
  reflow, measured colour contrast and real screen-reader output are not tested (§2, row 8). The
  recommendation is to accept the structural result and close the gap with a short manual pass by
  the owner — the only user of a single-user local tool — rather than commissioning a browser
  automation suite before v1.
- Promotion is a **status change plus a pointer, not a copy**. The UI's documentation, verification
  record and evidence already live in this repository at stable paths, so copying them into `docs/`
  would create the second home the artefact taxonomy forbids. "Promoted" means: this record moves
  from `proposed` to `promoted`, and the plan's promotion list is ticked (§7).

Do **not** read this as a release decision. `loom deploy promote` — the act that moves the tree the
scheduler runs — is a separate operational decision with its own evidence, and this task neither
performs nor recommends a particular release moment.

## 2. Readiness register

Each row is one acceptance criterion from the reviewed plan, the check that speaks to it, and a
verdict. "Verified" never means "verified by reading the test name"; the named class is the check
that was run.

| # | Acceptance criterion | Evidence | Verdict |
|---|---|---|---|
| 1 | A person can tell decisions needing a human from work merely waiting for an agent, reviewer, dependency or capacity | `ui/VERIFICATION.md` row 9 (`BlockedWork`); the control-room queues in `ui/README.md`, in which every task lands in exactly one queue | **Met** |
| 2 | Every row and detail view shows state, claimability, blocker reason, freshness and revision/lease without conflating them | `BlockedWork`, `TaskInspection`; per-source `as_of` and status | **Met** |
| 3 | A blocked task traces to its prerequisites and the work it gates | `ReadModelParity.test_graph_edges_are_the_front_door_dependencies` (pictured edges equal the engine's own `depends_on`); `GraphAccessibility` (the picture has a complete, labelled text equivalent) | **Met** |
| 4 | Review verdict, evidence location, integration receipt and event history are separate facts | `TaskInspection`; missing evidence is reported missing, not filled in | **Met** |
| 5 | No mutation bypasses the engine; conflicts, stale revisions, expired leases and denials are visible and never silently retried | `StaleWriteBoundary`, `OwnerJourneys`; `ui/README.md` "What is actually enforced" | **Met** |
| 6 | V1 exposes no worker dispatch or global `tick --apply`; any operational control needs separate explicit confirmation; reads never trigger one | `ENGINE_VERBS` is a seven-verb frozenset (`ui/loom_ui/actions.py`); the route table has no operational route (`ui/loom_ui/server.py`); operations is a read-only view | **Met, and stronger than asked**: v1 has no scheduler or deploy control at all, so there is nothing to mis-confirm |
| 7 | Mutations are bound to a server-side human identity and role mapping; browser-supplied actors are rejected | `IdentityBoundary`; `ui/loom_ui/identity.py` role table; `OwnerJourneys.test_an_owner_accepts_integrated_work_from_the_acceptance_queue` walks the owner-only acceptance path end to end | **Met** |
| 8 | Core workflows are keyboard-operable, screen-reader labelled, responsive, and usable without colour perception | `ContentSafety.test_every_page_has_the_accessibility_scaffold`; no script anywhere, so keyboard order is native link/control order | **Partly met — see below** |

### Criterion 8 in detail

*Proven:* every page has a language, one `h1`, `header`/`nav`/`main`/`footer` landmarks, a skip link
as the first link, captioned tables with scoped headers, labels on every control, no positive
`tabindex`, no duplicate ids, and no script — so the page cannot take the keyboard away or rely on a
client runtime. Status is written as text inside every badge (`ui/loom_ui/templates.py`), so it does
not depend on colour alone.

*Not proven:* a media-query layout actually reflows at a narrow viewport or at 200% zoom; contrast
ratios meet WCAG 2.2 AA; a screen reader announces the graph's text alternative usefully; focus
order after a redirect returns somewhere sensible. The verification module says so itself: it has no
JavaScript engine, no layout, no screen-reader output (`ui/VERIFICATION.md`, Limits 1–2).

*Recommendation:* accept on the structural result, and have the owner spend ten minutes in their own
browser on `/`, `/tasks/<id>` with a long title, `/graph` and `/operations`: tab through each, zoom
to 200%, and check status without colour. That closes the practical gap for a single-user local
tool, and the result belongs in this record's promotion table when the owner accepts, so the closure
is a fact rather than an intention. If the owner wants a machine-checked claim instead, that is a
separate task (a real-browser suite), not a change to this record.

## 3. The v1 operating envelope

The claim this acceptance makes, dimension by dimension. The right column is the boundary of the
claim, stated so that a future reader cannot infer more from silence.

| Dimension | v1 claim | Explicitly not claimed |
|---|---|---|
| **Host** | One person, one machine, loopback HTTP only. The listener refuses any non-loopback bind, and every request must address the server by a loopback `Host` (`ui/loom_ui/server.py`) | LAN, reverse proxy, remote or shared service, TLS. Remote use needs authenticated identities, transport security, tenancy and an operator: a new design, not a flag |
| **Identity** | One or more local humans are configured at startup (`--human` or a JSON file). Opening a pairing link binds the browser to that identity; the browser never names an actor. Three roles: implementer, owner, reviewer; `accepted` is owner-only. Sessions are in memory, 8 hours absolute, 1 hour idle, and die on restart | Per-person identity on a shared OS account, SSO, or an identity provider. The pairing link is printed to the terminal and `--human` is visible in the process list; that is acceptable for one person's machine and not for a shared host |
| **Authority** | Loom is the only authority. The UI samples state for display and submits a confirmed action to the `loom` front door; the engine can refuse, and the refusal is shown verbatim with no retry | Any rule re-implemented in the browser. A refusal the engine would give is never pre-empted silently |
| **Mutation** | Seven verbs: `claim`, `renew`, `release`, `takeover`, `transition`, `ask`, `answer` (`ui/loom_ui/actions.py`). `transition` offers only the engine's legal targets for the current state, minus `reviewed` and `integrated` (`EXCLUDED_TARGETS`) and minus `reviewed → accepted` (`NEEDS_INTEGRATED_SOURCE`). An owner — and only an owner — can record `accepted` for integrated work whose dependencies are satisfied, from the acceptance queue (`identity.py` `TARGET_ROLES`; `OwnerJourneys.test_an_owner_accepts_integrated_work_from_the_acceptance_queue` exercises it). Confirmed forms carry revision and fencing epoch, a CSRF token and a one-shot nonce | Marking work `reviewed` or `integrated`: a reviewer's answer and an integration receipt belong to the coordinator, which is also what stops an implementer approving its own work. `reviewed → accepted`, which needs the coordinator's approved commit as proof. Outside v1 entirely, and absent from the route table: every command that writes and is not one of the seven — store-writing `annotate`, `create`, `edge add\|remove`, `board add\|remove\|sync\|import`, `reconcile --apply`, `migrate`, `snapshot`, `rebuild --apply`, `init`, `genesis`; artifact-writing `export --out`, `integration-plan --out`, `graph --format html` (writes `--out` or `task-graph.html`); operational `tick`, `tick --apply`, `workers`, `schedule`, `deploy`, `linear sync --apply`. The read-only commands (`list`, `show`, `search`, `events`, `attempts`, `dependents`, `inbox`, `decisions`, `foldcheck`, `graph` to stdout, `health`, `logs`, `snapshot --list`) have no route either: the UI reads the store in process for display rather than shelling out to them |
| **Read path** | Read-only by construction when no identity is configured. On a version mismatch every data surface answers `503` and leaks no task content | Offline caching or a frozen snapshot. A stale page is refused by the engine's own compare-and-swap |
| **Environment** | Verified on macOS 27.3 (arm64) with CPython 3.14.4 — the machine the verification suite ran on | Any other OS, Python version or architecture, and any minimum Python version. Elsewhere the UI is expected to degrade rather than crash: the scheduler source reads `~/Library/LaunchAgents` and `launchctl` (`ui/loom_ui/provenance.py`, `ui/loom_ui/operations.py`), so off macOS it reports the source degraded with the reason, but that prediction is unverified |
| **Release coupling** | The UI runs from the active front door and reports a release that drifted. A process whose loaded engine sources differ from the active front door closes its data surfaces | Starting from an unrelated checkout and expecting it to agree. After any release promotion the running server must be restarted, or it will serve the mismatch by design and say so |

## 4. Owner decisions

The plan lists six decisions before implementation plus promotion. Each is answered here as a
recommendation with the cost of the alternative; the owner's answer is what the acceptance records.

1. **Deployment model.** *Recommend: local single-user browser UI, loopback only.* The alternative —
   packaged desktop or remote shared service — is not a packaging change: remote use needs transport
   security, per-user authentication, tenancy and an operational owner, none of which exists here.
   Changing later is a new design, and the current code refuses non-loopback binds rather than
   pretending to support them.
2. **Mutation scope.** *Recommend: the seven task verbs, plus the owner's acceptance of integrated
   work from the queue; `tick --apply`, worker dispatch, scheduler and deploy stay CLI-only for v1.*
   The one authority-bearing mutation v1 does include is
   the owner accepting integrated work from the acceptance queue — owner-only in the role table, and
   the reason the queue exists. It is not the same as marking work `reviewed` or `integrated`, which
   stay with the coordinator (§3, Mutation row). The alternative is a scoped, version-bound apply
   operation that enumerates its full effect and refuses if state moved — the plan's Phase 4, and
   still a separate design. Shipping the current global apply through a browser would let one click
   transition unrelated tasks.
3. **Identity and roles.** *Recommend: keep the local identity file/token pairing, three roles, an
   8-hour session that idles out after an hour and dies on restart, for one person.* The alternative
   — several local people with separate roles on one machine — needs per-person OS accounts or a
   real user store, and the pairing link would still be a bearer token in a terminal.
4. **Technology.** *Recommend: keep the stdlib Python server rendering HTML with no JavaScript.* It
   is what makes "untrusted task text cannot become executable" a structural property rather than a
   sanitizer promise. The cost is no client-side interactivity; a richer UI is a new design.
5. **Linear projection.** *Recommend: no embed and no call in v1; the registry stays authoritative.*
   If a pointer is wanted it belongs in prose documentation, not as a link in the UI, where it would
   imply the UI knows something about the projection. The projection remains read-only either way.
6. **Supported environments.** *Recommend: macOS on the measured Python, with other platforms
   explicitly unverified.* A cross-platform claim needs its own run; the scheduler source is
   launchd-shaped and would read as permanently degraded elsewhere, which is honest but is not a
   supported experience.
7. **Promotion.** *Recommend: §7 below* — a status change plus the plan's promotion list, no copy
   into `docs/`.

## 5. Operating runbook

The v1 is a foreground process a person starts, not a service that installs itself.

1. Bring the tree to the commit you intend to run: `loom deploy promote --ref <commit>` from the
   engine. This is the release promotion, separate from accepting this record.
2. Start the UI **from the promoted tree's `capabilities/work`**:
   `python3 ui/serve.py --human <you>:owner,implementer,reviewer`.
   With no `--human` and no identity file, it starts read-only: the action routes are still present,
   and each of them answers `403` with no action offered and nothing submitted. The refusal is in
   `ui/loom_ui/server.py` for any id in `ENGINE_VERBS`;
   `ui/tests/test_action_server.py::test_every_action_route_is_closed` drives one of them
   (`/task/demo/A-1/action/claim`) and asserts the `403` on both `GET` and `POST`.
3. Open the pairing link it prints, once per browser. Restarting the server invalidates it.
4. Work from the control room; every action opens a confirmation page naming the task, the state
   change, the revision, the fencing epoch and the identity before anything is sent.
5. **After promoting a new release, restart the server.** The old process is now serving code from a
   front door that moved; by design it answers `503` and leaks nothing until restarted.
6. Stop it by ending the process; nothing is left running, and the only state outside Loom was the
   in-memory session, which dies with the process.

Not in v1: a login item or launch agent for the UI (only the tick scheduler installs one), a
background service, or a start/stop control anywhere in the product.

## 6. Residual risks the owner is accepting

| Risk | Severity | Who acts |
|---|---|---|
| Criterion 8's browser-only properties are untested: reflow, zoom, measured contrast, screen-reader output. The design is structural and text-labelled, so the expected failure is cosmetic | Medium | Owner: ten-minute manual pass, or commission a browser suite |
| Off macOS the scheduler view would read as permanently degraded (launchd path absent, `launchctl` missing) rather than hidden | Low, documented | Owner: accept macOS-only, or fund a platform-neutral source |
| Single-user assumptions: pairing link and `--human` are visible to anyone who can read the terminal or process list; sessions are in-memory | Low | Owner: keep it on a personal machine; shared use is a new design |
| The UI cannot detect a *correct but old* engine decision beyond the version gate; freshness is per source and the engine still revalidates every action | Low, by design | None for v1 |
| The registry is not globally validator-clean: at the time of writing `validate_work.py` reports pre-existing integration-receipt errors in other programs. The UI displays the registry as it is; none of those errors is in this slice's paths, and this slice neither caused nor fixed them | Low for the UI, real elsewhere | Coordinator, per program |
| No real browser or assistive technology was used anywhere in the evidence, so accessibility claims stay structural | Medium, named | Owner, in the same pass as the first row |

## 7. Promotion package

What "promoted" would mean, exactly. This is not performed here.

| Artifact | Home now | On acceptance |
|---|---|---|
| [`ui/READINESS.md`](READINESS.md) (this record) | product repo | set status to `promoted`, fill accepted-by, accepted-commit and promoted-at |
| [`ui/README.md`](README.md) | product repo | no copy; it is the operating document and already points here |
| [`ui/VERIFICATION.md`](VERIFICATION.md) | product repo | no copy; acceptance record |
| `capabilities/work/evidence/UI-6.md`, `UI-7.md` | product repo | no copy; evidence stays evidence |
| `plans/loom/2026-11-15-ui-plan.md` | private content repo | stays; its Promotion list records owner acceptance, and "copied to `docs/`" is marked not required with the reason below |
| `loom-ui/UI-7` in the registry | work database | marked `accepted` by the owner, through the coordinator or the UI's own acceptance queue once the work is integrated; never by the implementer |

**Why no copy into `docs/`.** The artefact taxonomy says one artefact, one home, and promotion is a
copy plus a pointer *when the artefact lives somewhere else*. The UI's documentation is already a
product-repo document under `capabilities/work/ui/`, at a stable path a reviewer and an operator can
open. Copying it into `docs/` would create two homes whose contents would drift. The pointer this
record adds is the plan entry and the `README.md` reference; if the owner wants a top-level `docs/`
index line as well, that is one sentence in a docs page and belongs to whoever owns `docs/`, not to
this task's write path.

**What promotion does not do.** It does not merge, deploy, restart, publish, announce, or touch the
Linear projection. It does not change any code, and it does not make the UI authoritative for
anything Loom already decides.

## 8. Decision requested

The owner is asked to answer each; the recommendation is first. Answering "accept all as
recommended" is a complete answer.

1. **Deployment** — local single-user loopback UI (recommended) / packaged desktop / remote design
   first.
2. **Mutation scope** — seven task verbs plus the owner's acceptance of integrated work from the
   queue (recommended) / add scheduler and deploy controls now.
3. **Identity** — local pairing, three roles, one person (recommended) / multiple local people with
   separate roles now.
4. **Technology** — stdlib server, no JavaScript (recommended) / a separate frontend.
5. **Linear** — no embed or call in v1 (recommended) / link only.
6. **Environment** — macOS on the measured Python, others unverified (recommended) / take on a
   cross-platform claim with its own run.
7. **Criterion 8** — accept structural verification and close it with an owner manual pass
   (recommended) / commission a real-browser pass before v1.
8. **Promotion form** — mark this record promoted and tick the plan's list, no `docs/` copy
   (recommended) / add a `docs/` pointer as well.

## 9. What would make this record false

This record is evidence about a tree, not a promise about the future. It must be re-derived, not
trusted, when any of these change:

- the action allow-list, the transition target filters (`EXCLUDED_TARGETS`, `NEEDS_INTEGRATED_SOURCE`),
  the route table, the identity/role table, the session rules or the version gate in `ui/loom_ui/` —
  any of which can widen or narrow the envelope in §3;
- the engine's own verbs or state model, which the UI mirrors;
- the operating system or Python version beyond the one measured in §3;
- a release promotion that moves the active front door;
- any owner answer in §8 that differs from the recommendation, which changes the envelope and
  requires this document to be rewritten **before** it is marked promoted.
