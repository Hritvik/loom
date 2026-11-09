# Artefacts

Every artefact in this system is one of seven kinds. The engine owns the kinds; a content
repository owns the instances.

| Kind | Purpose | Lives in | Created by | Promoted by |
|---|---|---|---|---|
| **Wayfinder map** | the problem space and its tickets, before work exists | content repo | a human and an agent, exploring | ticket → task |
| **Spec** | the behaviour a service must have | content repo | an agent, from a claimed task | accepted → copied into the product repo's `docs/` |
| **Plan** | how a spec gets built, step by step | content repo | an agent, from a spec | executed → evidence in the product repo |
| **Capability map** | what one service promises another, and who owes it | content repo | a human, during stewardship | provider acceptance |
| **Contract** | a wire format between services, with fixtures | contract registry | the provider | accepted, pinned by each consumer |
| **Decision (ADR)** | why something is the way it is | either | whoever decided | superseded by a later decision |
| **Memory** | what a later session should not have to rediscover | content repo | any agent | verified → promoted into a spec, ADR or rule |

## Why the split

- **The engine is generic.** It knows what a task, a claim, a lease and an event are. It does not
  know what your services are called.
- **Content is yours.** Maps, specs and plans carry product strategy; they do not belong in a
  repository anyone can read.
- **Contracts are public by nature.** A consumer must be able to read the format it implements, so
  they live in their own registry with pins, not inside either of the other two.

## The three repositories

| Repository | Holds | Visibility |
|---|---|---|
| **loom** | the engine, the protocol, the templates, the examples | public |
| **content** | maps, plans, specs, capability maps, evidence, memory | private |
| **contracts** | the wire formats and their fixtures | public |

Point the engine at the other two:

```bash
export WORK_CONTENT_HOME=~/repositories/portfolio-planning
export WORK_CONTRACTS=~/repositories/portfolio-contracts
export WORK_DB=~/.local/state/loom/work.db
```

## Lifecycle in one line

A wayfinder map produces tickets; a ticket becomes a task; the task produces a spec and a plan; the
plan produces code and evidence in a product repository; the evidence supports integration and
acceptance; anything learned on the way becomes memory, and anything verified becomes a decision.

## Rules that keep the kinds distinct

1. **One artefact, one home.** A spec is not a plan; a plan is not a task. If it needs two homes, it
   is two artefacts.
2. **Promotion is a copy plus a pointer.** When a spec is accepted it is copied into the product
   repository and the central entry is marked promoted. Never edit both.
3. **Memory is not a task tracker.** Claims and status live in the database; memory carries what
   state cannot express.
4. **Contracts never live in the engine or the content repository.** They are consumed by services,
   so they need their own public home and their own release cadence.
