# A2A v1.0 façade

A thin, standards-based front door to the work registry, so agents that speak A2A can discover and
claim work without shelling into this directory.

```bash
python3 a2a/server.py --port 8787          # binds 127.0.0.1 by default
```

- Discovery: `GET http://127.0.0.1:8787/.well-known/agent-card.json`
- JSON-RPC: `POST http://127.0.0.1:8787/` with `A2A-Version: 1.0`
- Health: `GET /health`

The Agent Card declares `JSONRPC` / protocol `1.0`, `streaming: false`,
`pushNotifications: false`, `extendedAgentCard: false`, one skill: `work-coordination`.

## Operations

| A2A method | Behaviour |
|---|---|
| `ListTasks` | List central tasks; `contextId` is the program (`alpha`, `beta`, `gamma-ref`); filters `status`, `statusTimestampAfter`; `pageSize` 1–100 with opaque `pageToken`; `includeArtifacts`; `historyLength`. Each `Task.metadata` carries `claimable` and `blockedBy` |
| `GetTask` | One task by registry id, with `metadata.revision`, claim, dependencies, write paths, evidence and review |
| `SendMessage` | Mutating actions against an existing task, delegated to the `task` CLI |
| `CancelTask` | Moves a ready/claimed/red/green task to `released` (A2A `TASK_STATE_CANCELED`) with a reason |
| anything else | `-32601`; streaming/extended card → `-32004`; push config → `-32003` |
| `messages/list` | Extension (not in the A2A spec): unanswered questions for `metadata.agent`, optional `taskId` and `includeAnswered` |

Task creation stays central: this agent does not accept `SendMessage` without a `taskId`. Author new
work with `./task create`, then materialise the packet.

## SendMessage actions

The message carries one `data` part. Identity is `message.metadata.agent`, falling back to
`params.metadata.agent` or the `X-Agent-Id` header.

```json
{
  "jsonrpc": "2.0", "id": 2, "method": "SendMessage",
  "params": {
    "message": {
      "role": "ROLE_USER",
      "messageId": "msg-1",
      "taskId": "alpha/W-1",
      "parts": [{"data": {"action": "claim", "revision": 0, "ttl": "45m"}}],
      "metadata": {"agent": "opencode/alpha-agent-3"}
    }
  }
}
```

| `action` | Required fields | CLI equivalent |
|---|---|---|
| `claim` | `revision` | `task claim … --revision … --ttl …` |
| `renew` | `revision`, `epoch` | `task renew … --revision … --epoch … --ttl …` |
| `release` | `revision`, `epoch`, `reason` | `task release … --epoch …` |
| `takeover` | `revision`, `reason`; optional `lockDelay` | `task takeover … --lock-delay …` |
| `transition` | `revision`, `epoch`, `to`; plus `evidence`, `reviewer`, `decision`, `reason` as required | `task transition … --epoch …` |
| `ask` | `to`, `question`; optional `neededBy` | `task ask …` |
| `answer` | `question` (seq), `decision`; optional `note` | `task answer …` |

Because the server delegates to the CLI, every guarantee is identical: CAS on `revision`, the
fencing epoch, lease expiry, the takeover lock-delay, dependency gates, write-path conflicts, review
independence, evidence requirements and the hash-chained event log.

## Example

```bash
curl -s http://127.0.0.1:8787/.well-known/agent-card.json | jq .skills

curl -s http://127.0.0.1:8787/ \
  -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' \
  -d '{"jsonrpc":"2.0","id":1,"method":"ListTasks",
       "params":{"contextId":"alpha","status":"TASK_STATE_SUBMITTED","pageSize":10}}'
```

`SendMessage` returns the updated A2A `Task`; `ListTasks` returns
`{"tasks":[…],"nextPageToken":""}`; `GetTask` returns the `Task` object.

## Reading messages — extension

A2A has no inbox primitive, so unanswered questions are read through a documented extension. It is
not part of the A2A spec; clients that do not know the method are unaffected.

```bash
curl -s http://127.0.0.1:8787/ \
  -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' \
  -d '{"jsonrpc":"2.0","id":3,"method":"messages/list",
       "params":{"metadata":{"agent":"opencode/reviewer"}}}'
```

Returns `{"agent": …, "count": n, "messages": [{seq, ts, task, from, question, neededBy, answered}]}`.
Optional params: `taskId` to filter one task, `includeAnswered` to include closed questions.
`ask` and `answer` mutate nothing: they append `ask`/`answer` events to the hash-chained audit log
and leave the task's revision and status untouched, which is why they need no `revision` field.

## State mapping

| Registry | A2A |
|---|---|
| `draft`, `planned`, `ready` | `TASK_STATE_SUBMITTED` |
| `claimed`, `red`, `green`, `reviewed`, `in-progress` | `TASK_STATE_WORKING` |
| `blocked` | `TASK_STATE_INPUT_REQUIRED` |
| `integrated` | `TASK_STATE_WORKING` (gate-passed, awaiting acceptance) |
| `accepted`, `done` | `TASK_STATE_COMPLETED` |
| `rejected` | `TASK_STATE_REJECTED` |
| `released` | `TASK_STATE_CANCELED` |

The richer internal state, revision, claim and evidence stay in `Task.metadata`.
`metadata.claimable` mirrors the CLI claim gate (state `ready`, dependencies satisfied, no active
write-path conflict); `metadata.blockedBy` lists the blocking task ids. A2A has no native
"claimable" filter, so clients filter on these fields after `ListTasks`.

## Security

There is no authentication. The server binds loopback by default and warns when bound more widely.
`metadata.agent` and `X-Agent-Id` are self-asserted, so they are useful for attribution in the event
log but are not proof of identity. Add authentication and TLS before exposing this endpoint beyond a
trusted local host.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

`tests/test_a2a.py` starts the real server on an ephemeral port against a disposable work home and
covers discovery, ETag/304, list/get, pagination, claim/conflict/CAS, cancellation, terminal-task
refusal, unsupported capabilities and version negotiation.
