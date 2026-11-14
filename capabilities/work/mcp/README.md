# MCP server

The registry as MCP tools, so an agent sees the coordination layer in its **tool list** while
planning — without being told to read a document first.

```bash
python3 mcp/server.py --print-config opencode     # or claude / codex
```

The snippet contains absolute paths and optional defaults:

```bash
python3 mcp/server.py \
  --root "/Users/…/capabilities/work" \
  --repo alpha \
  --agent opencode/alpha-agent \
  --print-config opencode
```

- `--repo` is the default for `work_status` / `work_list`; tool arguments override it.
- `--agent` is the default identity for mutations; tool arguments override it, and
  `WORK_AGENT` / `WORK_REPO` work too.
- `--root` points at the work home containing `registry.yaml`.

## Tools

| Tool | Kind | Purpose |
|---|---|---|
| `work_status` | read | Ready work, active claims, expired leases, board drift |
| `work_list` | read | Filter by repo, program, board, status, `ready` |
| `work_get` | read | One task with dependencies, claimability, recent events |
| `work_claim` | write | CAS on revision + lease; returns the fencing epoch |
| `work_renew` | write | Heartbeat and extend the lease (presents the epoch) |
| `work_release` | write | Return to the pool with a reason (presents the epoch) |
| `work_takeover` | write | Take over an expired lease after the lock-delay, with a reason |
| `work_transition` | write | red/green/reviewed/integrated/accepted, blocked/rejected |
| `work_inbox` | read | Unanswered questions addressed to the agent |
| `work_search` | read | Find tasks by text instead of namespace |
| `work_dependents` | read | Who is blocked on a task, directly and transitively |
| `work_ask` | write | Ask a question about a task; appended to the audit log |
| `work_answer` | write | Answer a question addressed to the agent, once |

`work_status` is written to be called first; its description tells the model to use it before
planning or claiming work. That description is the discovery mechanism.

Mutations delegate to `work/task`, so MCP, CLI and A2A share CAS, leases, dependency gates,
write-path conflicts, review independence and the hash-chained event log.

## Example session

An agent asks for status:

```json
{"name": "work_status", "arguments": {"repo": "beta"}}
```

Then reads and claims:

```json
{"name": "work_get", "arguments": {"task": "beta/W-3"}}
{"name": "work_claim", "arguments": {"task": "beta/W-3", "revision": 0, "ttl": "45m"}}
```

The claim result includes the new revision, lease expiry, agent and **fencing epoch**. Pass that
epoch to `work_renew`, `work_release` and `work_transition` for the life of the claim; a takeover
increments it and a stale value is refused. A stale revision, live lease, unmet dependency or
overlapping write path returns `isError: true` with an actionable message.

## Repo and identity defaults

Local MCP clients start the server in the workspace, so the server infers both defaults from the
path:

- `repo` = the closest parent directory matching a registered board's repository name
  (`.../repositories/alpha/docs` → `alpha`).
- `agent` = `<repo>-agent` (`alpha-agent`) when no `--agent` / `WORK_AGENT` is set.

Explicit `--repo`/`--agent`, `WORK_REPO`/`WORK_AGENT`, or tool arguments override the inference.
This is what lets one global server work in every repository without per-repo config. If the path
does not match a registered repo, `repo` stays unset and mutations require an explicit `agent`.

## Protocol

- Newline-delimited JSON-RPC 2.0 over stdin/stdout; logs only on stderr.
- `initialize`, `ping`, `tools/list`, `tools/call`; notifications are accepted and ignored.
- Protocol versions `2024-11-05` through `2026-07-28` are echoed when requested, otherwise
  `2025-06-18` is advertised.
- Tool results include both text JSON and `structuredContent`.
- Only the `tools` capability is declared; there are no resources or prompts.

## Security

The transport is local stdio: the client launches the server as a subprocess, so there is no
network listener and no authentication layer. The agent identity is self-asserted in `--agent` or
the tool argument and is recorded in the event log. Do not treat it as proof of identity across
trust boundaries.

## Tests

```bash
python3 -m unittest tests.test_mcp -v
```

Covers handshake and version negotiation, tool discovery, filters, drift, claim/renew/release, the
full lifecycle, error paths, default repo/agent, `--print-config`, and a real stdio subprocess
exchange.
