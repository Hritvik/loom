# Team memory

Version-controlled context for every agent working this repository — any harness, any session. A
decision or lesson written here is available to the next agent without a handoff conversation.

## Scopes

| Scope | Where | Committed | Use |
|---|---|---|---|
| `project` | `<repo>/.ecc/memory/project/` | no | machine-local context |
| `team` | `<repo>/.ecc/memory/team/` | yes | context the whole team inherits |
| `user` | `~/.ecc/memory/` | never | operator context across repositories |

## What belongs here

| Kind | Example |
|---|---|
| `decision` | why the registry uses an app subject as the aggregate id |
| `lesson` | a suite that reports green while skipping every integration test |
| `fact` | which repository owns which contract |
| `runbook` | the exact command that reproduces a verification claim |
| `handoff` | context another harness needs to continue work |

## What does not

- **Task state, claims, dependencies** — those live in the coordination database. Memory is not a
  task tracker.
- **Plans, specs, maps** — those are artefacts with their own homes.
- **Secrets or personal data.**

## Use

```bash
ecc memory search "profile state"                 # recall before writing
ecc memory save --title "…" --kind lesson --scope team \
  --source-harness <harness> --tag <tag> --stdin < note.md
ecc memory read <memory-id>
ecc memory doctor --scope team
```

Every entry is **unreviewed context**, not policy. When a memory proves correct, promote it into a
governed artefact — a spec, an ADR, or a rule — and link back to it.
