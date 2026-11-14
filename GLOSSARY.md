# Portfolio work coordination

The shared protocol that lets independent agents work one portfolio without colliding. The registry
is the authority; harnesses and threads are disposable.

## Language

**Task**:
A unit of deliverable work with an identity, a required role, a state and a revision.
_Avoid_: work item, ticket, card, issue

**Role**:
The hat a task requires and an agent wears — implementer, reviewer or integrator.
_Avoid_: persona, agent type, harness, lane

**Agent**:
An identity that can be spawned in any harness and wear any role. Self-asserted and recorded, never
authenticated.
_Avoid_: worker, bot, model, harness

**Harness**:
The environment an agent runs in — Codex, T3, Claude Code. Never a role, never part of the protocol.
_Avoid_: runtime, client, provider

**Thread**:
One running agent session. Ephemeral, not authoritative, and never stored as task state.
_Avoid_: session, run, worker, instance

**Claim**:
A time-bounded exclusive right to work a task, held by one agent in that task's required role.
_Avoid_: lock, assignment, reservation

**Lease**:
The expiry attached to a claim. An expired lease voids the claim; it does not mean the work is done.
_Avoid_: timeout, TTL, deadline

**Epoch**:
A counter incremented on every claim and takeover and copied into the claim. The holder presents it
on each mutation; a stale value is refused. It fences the *work* where the revision CAS fences the
*claim*.
_Avoid_: version, generation, nonce

**Lock-delay**:
The grace window after a lease expires before a takeover may re-grant the task (default two
minutes). It protects resources that cannot validate an epoch, such as a git branch.
_Avoid_: cooldown, backoff, timeout

**Review**:
An independent verdict on green work, recorded as an answer event. The reviewer must not be the
acting agent or an earlier claimant; the coordinator records `reviewed` from an approval.
_Avoid_: approval, sign-off, audit

**Launch**:
The recorded act binding a task, a role, an agent identity and a thread. A launch is an event; the
thread it names may no longer exist.
_Avoid_: spawn, dispatch, assignment

**Projection**:
A derived view of the registry outside it — today the Linear card. A projection never writes back.
_Avoid_: mirror, copy, sync target

**Event**:
An append-only record of an act on a task. The event log is the only history.
_Avoid_: log line, audit row, record

**Coordinator**:
The orchestration function that decides what to launch next. It holds no claim and does no work.
_Avoid_: orchestrator, manager, supervisor, planner

**Owner**:
The human authority that accepts work and makes the decisions agents may not. Not a role an agent
can wear.
_Avoid_: admin, approver, user
