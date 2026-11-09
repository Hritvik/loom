# Working in the portfolio — agent guide

You coordinate through one shared registry, not through chat. This file is the whole briefing; read
it once per session, then work from the commands.

Truth lives in the work database at `~/.local/state/loom/work.db` (state, claims, dependencies and
the append-only, hash-chained event log). Everything below is a
surface over those two files. The state model is in [`PROTOCOL.md`](PROTOCOL.md).

## Start every session

```bash
cd ~/repositories/loom/capabilities/work
python3 validate_work.py            # is the registry healthy?
./task list --ready                 # claimable work, ranked by what it unblocks
./task inbox --agent <your-id>      # questions other agents asked you
```

Over MCP the same three calls are `work_status`, `work_list {"ready": true}` and `work_inbox`. The
repo is inferred from your working directory; the agent identity defaults to `<program>-agent`.

## When a release is waiting on multiple products

Use `./task show <release-task>` to find unmet prerequisites. Follow each branch to
the first claimable task, and use `./task dependents <task>` to see what it unlocks.
Keep work with its current agent while the lease is live. When products need each
other, agree a versioned interface and shared fixtures, then track the joint proof
as a separate task that depends on both implementations. Keep owner decisions on
their own gates so local work can continue. Use `./task list --ready` to check
claimability. A task can display Ready while a prerequisite is still open.

## Where things live

| Thing | Where | Rule |
|---|---|---|
| Plans, specs, designs, maps, tickets | your content repository (`$WORK_CONTENT_HOME`) | author there, never in a product repo |
| Interaction contracts | your contract registry (`$WORK_CONTRACTS`) | artifact + fixtures + a `registry.yaml` entry; consumers pin a hash |
| Task state, claims, dependencies | `~/.local/state/loom/work.db` | mutate only through `./task` |
| Code, tests, migrations, evidence | your product repository | the registry points at it, never stores it |
| Durable memory | `.ecc/memory/team/` | `ecc memory save --scope team`; inherited by every harness |

**Promotion.** When a plan or spec is accepted, copy it into the product repo's `docs/` and mark the
central entry `promoted`. The repo copy becomes authoritative; never edit both.

**Memory is unreviewed context, not policy.** Save decisions, lessons, facts and runbooks that a
later session would otherwise have to rediscover:

```bash
ecc memory search "profile state"          # recall before writing
ecc memory save --title "…" --kind lesson --scope team \
  --source-harness <harness> --tag <tag> --stdin < note.md
```

When a memory proves correct, promote it into a governed artifact — a spec, an ADR under
`capabilities/decisions/`, or a rule in `AGENTS.md` — and link back to it.

## Adding or changing a contract

A contract is how one service promises to behave toward another. The artifact lives here, the
provider keeps a build copy, and every consumer pins the exact version it compiles against.

```text
contracts/
  registry.yaml                                   # the index: owner, version, status, pin
  http/<id>/v1/openapi.yaml
  events/<id>/v1/schema.json
  fixtures/<id>/v1/{valid,invalid}/*.json
```

1. **Register before building.** Add the entry to `contracts/registry.yaml` with `status: proposed`
   and the task that delivers it. A contract nobody registered is a rumour.
2. **Put the artifact under `contracts/`**, with fixtures both sides can run. Copy it from the
   provider repository; record `source: {repo, path, commit}`.
3. **Consumers pin a hash.** `pin: {sha256, commit}` is the version that consumer builds against.
   Upgrading is an explicit pin change, never a silent follow of `main`.
4. **Additive changes stay in the same version.** A breaking change is a new `v2` published
   alongside `v1`; the old one stays until its consumers move.
5. **Never edit another service's artifact.** Ask the owner; the registry records who that is.
6. Run `python3 validate_work.py` — it fails on a missing artifact, a hash mismatch, an unknown
   gate task, or a program map that references a contract nobody registered.

## Committing

Every repository carries a pre-commit guard that refuses a commit staging another agent's
in-flight work. It reads the central registry, so it only knows what was actually claimed.

- **Add `Agent: <your identity>` to the commit message.** With the trailer the guard checks every
  staged path against your live claim. Without it, it falls back to a weaker check that only
  catches a commit spanning two live claims.
- **Stage explicit paths.** `git add -A` is the exact thing the guard exists to stop.
- **Install it once per clone:** `git config core.hooksPath .githooks`. The hook is a thin wrapper;
  the implementation lives in `capabilities/work/hooks/` so every repository behaves the same.
- **The human bypasses** with `git commit --no-verify`.

## The loop

```bash
./task show <id>                                          # read deps, blocks, paths, revision
./task claim <id> --revision N --ttl 45m --agent <you>
./task renew <id> --revision N --ttl 45m --agent <you>    # heartbeat before expiry
./task transition <id> --revision N --to red    --agent <you>
./task transition <id> --revision N --to green --evidence evidence/<id>.md --agent <you>
./task transition <id> --revision N --to integrated --integrated-source <full-commit> \
  --evidence evidence/<id>.md --agent <you>
./task transition <id> --revision N --to accepted --evidence evidence/<id>.md --agent <you>
```

Code, tests, packet bodies and evidence stay in **your** product repository. The registry holds
identity, state, revision, claims and dependencies — nothing else.

## Independent review pipeline

`green` is the review queue. `reviewed` is the integration queue. Review approval binds the submitted commit. The reviewer reads the evidence
identified in the request and records the checks in the verdict. A dependent claim opens only after its prerequisite
is integrated, accepted, or done.

1. **Implementer submits.** Run the scoped checks and record the results and known limits
   in the product repo. Submit a full Git commit hash from the owner repo, its base,
   the evidence path, the checks to reproduce, and any contract pins or migrations. The
   base must cover the full task diff. If you omit the base, the board uses the first parent.
   Mark the task green, then send the request to an independent reviewer using the task inbox.
   Keep the review scope explicit when one change contains multiple tasks.
2. **Reviewer responds.** Read the inbox and inspect the submitted revision. Reviewers do
   not claim or edit the implementation task. Record an `approve` or `changes requested`
   response in the reviewer's own identity. Choose a review method and execution approach
   below. Include the exact revision, findings and checks run. For changes requested, the implementer fixes the findings and submits a new green
   revision and review request with a new commit. Renewing a lease does not clear a
   changes-requested verdict. An approval only applies to the reviewed change.
3. **Coordinator records.** Match the response to the request, reviewer and current green
   revision. Confirm the reviewer did not implement the change or hold its claim. Record
   `reviewed` using the approval event ID. The board rejects stale or superseded approvals,
   approvals from a different reviewer, changes to the submitted evidence locator, and ordinary inbox replies. Changes requested leave the item with the implementer for a new review round.
4. **Product owner integrates.** Record the exact approved commit and evidence. The board
   checks that the commit is on the owner repo's integration branch and that the paths changed
   between the submitted base and commit still match the approved commit. Review the new commit after
   squash, cherry-pick or conflicting changes. Production acceptance remains a separate gate.

```bash
./task list --state green
./task ask <id> --to <reviewer> --review-source <full-commit> --review-base <base-commit> \
  --review-evidence <evidence-path> --question 'Review request and checks'
./task inbox --agent <reviewer>
./task answer <id> --agent <reviewer> --question <request-event> --decision approve \
  --note 'Reviewed revision, verification and findings'
./task transition <id> --revision <green-revision> --to reviewed --reviewer <reviewer> \
  --decision approve --review-answer <answer-event> --evidence <evidence-path>
./task transition <id> --revision <reviewed-revision> --to integrated \
  --integrated-source <full-commit> --evidence <evidence-path>
./task list --state reviewed
```

The board enforces claim-history independence and checks the structured inbox approval.
Review assignments leave task ownership with the implementer. The evidence locator is
bound to the verdict. Its contents remain part of the reviewer’s checks. Older completed work remains
recorded. If a reviewed task has no receipt, return it to ready with a reason, then claim and
resubmit it. Record approval before marking it integrated. Packet import and reconciliation
cannot promote tasks into a reviewed or completed state.

Git verification uses the owner repository's local `refs/heads/main` by default, or the
board's configured `integration_ref`. Checking out a review branch does not change the gate.
The board does not establish that a commit landed on a remote branch.
Agent IDs are local audit labels and depend on trusted callers. Remote or authenticated review
requires an identity and branch-protection policy outside this gate.

### Choose a review method

The independent reviewer chooses the method and may combine methods across different
parts of the same revision. Record the choice and the reason in the verdict.

| Method | When to choose it | What to check |
|---|---|---|
| Normal | Bounded change with familiar behavior and a clear test surface | Compare the diff to the work item and repo standards. Trace affected callers and regressions. Check that the tests exercise the requested outcome. |
| Adversarial | Trust boundaries, financial effects, privacy, migrations, delivery/replay, or changes whose failures are hard to reverse | Start from the guarantees the change claims. Try to falsify them with concrete misuse, fault, concurrency, recovery, or rollback scenarios. Use isolated environments and synthetic inputs for fault injection. Record which claims were tested and which remain unverified. |

### Select review lenses

Every review checks scope and behavior, affected callers, and evidence. Add lenses based
on what the change touches and what could fail. The reviewer records the selected lenses
and any unverified guarantee that affects the verdict. A lens is a question to answer
about the change. Normal and adversarial are methods for answering it.

| Lens | Select when the change touches | Ask and verify |
|---|---|---|
| Product behavior and regression | Any user-visible or workflow change | Does the normal entry point satisfy the work item? What happens in empty, stale, denied, partial and repeated cases? Which existing behavior do callers rely on? |
| Architecture and maintainability | Shared logic, service boundaries, refactors or new abstractions | Does the right component own the decision and data? Are dependencies explicit, changes localized, and failure handling readable? |
| Contracts and compatibility | APIs, events, schemas, SDKs, public interfaces or cross-repo changes | Are versions and pins reproducible? Can old and new consumers coexist? Do actual provider and consumer code agree on errors, ordering and valid and invalid fixtures? |
| Authentication, authorization and isolation | Identity, tokens, secrets, tenancy, network trust or privileged actions | Can a caller cross an app or tenant boundary, spoof authority, replay a credential, or bypass a denial? Check expiry, rotation, privilege and safe errors. |
| Privacy and data lifecycle | Personal data, telemetry, exports, retention, deletion or sharing | What is collected, disclosed, retained and logged? Do access controls and erasure survive retries, downstream copies and restore? |
| Domain and financial integrity | Ledgers, balances, allocation, money, permissions or state invariants | Can rounding, reversal, stale state or duplicate requests corrupt outcomes? Prove accounting invariants, authorization and reconciliation on representative inputs. |
| Persistence and migrations | Schema, stored data, data backfills or indexing | Check old data, mixed app versions, upgrade ordering, interruptions, safe roll-forward or rollback, and whether the migration can be resumed. |
| Concurrency, delivery and recovery | Queues, retries, jobs, outboxes, offline writes or multi-step effects | Test loss, duplicate and reordered delivery, ambiguous acknowledgement, restart and competing workers. Check leases, atomicity, idempotency and durable outcomes. |
| Performance, capacity and cost | Hot paths, fan-out, large data, provider quotas or paid infrastructure | Measure the relevant latency, throughput or resource use. Bound inputs, retries and queues. Check what happens at capacity and how spend is limited. |
| Operations and release | Deploys, configuration, alerting, dependencies, feature flags or rollout | Can an operator reproduce and observe the change? Check artifact provenance, config and secret scope, readiness, rollback or forward repair, alert ownership and restore evidence. |
| Supply chain and dependency trust | Dependencies, build tooling, images, third-party services or permissions | Check version and artifact integrity, lock or digest pins, dependency changes, build provenance and permissions. Verify any required licensing or provider terms with the owner. |
| User experience and accessibility | UI, mobile flows, forms, navigation or copy | Check focus and assistive technology, keyboard and touch, loading and errors, offline behavior, deep links and whether visible state reflects the actual outcome. |
| External effects and AI safety | Agent tools, model calls, approvals, outbound actions or untrusted content | Check authority at execution, approval scope and expiry, untrusted content handling, isolation, evidence of side effects, quotas and what happens when the result is unknown. |
| Verification and handoff | Every submission, with extra scrutiny for shared or irreversible changes | Can someone reproduce the checks from the pinned revision? Do the tests fail for the right reasons? Label fixture, integration and deployed evidence. Update the relevant contracts, docs and runbooks. |

### Choose how to run the review

- **Direct review.** Inspect the submitted revision and leave actionable findings inline at
  the file and line. Each finding identifies the expected behavior, a concrete failure
  case and its impact. Put cross-file findings in the review summary.
- **Subagent-assisted review.** Give independent reviewers distinct scopes on the same
  pinned revision, such as authorization, retry and recovery, or tests and contracts.
  Use subagents who did not implement the submitted work. Each reports concrete findings
  with source references and verification. The assigned reviewer checks contested and
  blocking findings, deduplicates feedback, and records one verdict in their own identity.
  Subagent reports are evidence for that verdict.

Return the revision, method, execution approach, selected lenses, checks run, blocking findings with source
references, and any remaining verification limits. The inbox answer carries the verdict
and a link to detailed findings when needed. Review comments can stay local or inline in
the review surface. Use the team's authorization rules before posting them externally.

## Rules that are enforced, not advisory

| Rule | What happens if you ignore it |
|---|---|
| Claim before you work | the transition is refused; you do not own the task |
| Re-read the revision before every mutation | a stale revision is refused, not merged |
| The reviewer cannot be you, or anyone who ever claimed the task | the transition is refused |
| `integrated`/`accepted` need an evidence path | refused |
| `accepted` needs every dependency integrated | refused |
| Release rather than sit on a lease | the lease expires and anyone may take over, with a reason recorded |

## Finding work you cannot name

```bash
./task search avatar                # id, title, goal, program, board, write paths
./task dependents <program>/<ID>    # who is blocked on it, directly and transitively
./task list --ready                 # "+4 downstream" = finishing it releases four tasks
```

Pick the top of `--ready` unless you have a reason not to: it is ordered by downstream work, which
is the only priority signal this system has.

## When you are blocked

```bash
./task ask <task> --to <agent> --question "..." --needed-by 2026-11-07 --agent <you>
./task inbox --agent <you>
./task answer <task> --question <seq> --decision "..." --agent <you>
./task transition <task> --revision N --to blocked --needs human --reason "..." --agent <you>
```

- Ask the agent who owns the answer, not the owner of the portfolio. Questions are events: they
  survive your session and they are attributed.
- `--needs human` means only the owner can resolve it. Use it for contracts, prices, product and
  identity decisions. Never make one yourself, and never accept a contract on the owner's behalf.
- `--needs agent` (the default) means another agent can unblock you.
- `./task list --awaiting human` is the owner's queue. Do not add to it casually.

## If a tool is missing

The MCP server exposes 13 tools. If your tool list shows fewer — no `work_search`, no
`work_dependents` — your client is running an old server process. Restart it; the code is current.

## Do not

- Edit another agent's claimed task, or its packet body.
- Run `git add -A`, `git add .` or `git commit -a` in this repository. The working tree is shared
  with other agents; stage the paths you changed. A swept commit mixes two agents' work under one
  message and no one can review it afterwards.
- Review your own work, including after releasing the lease.
- Treat `--agent` as authentication. It is an assertion, recorded for attribution, not proof.
- Put personal, health, employer or credential material in a public repository.
- Create work outside the registry. New tasks start with `./task create`, on a registered board.
