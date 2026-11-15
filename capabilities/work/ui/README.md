# Loom UI — read-only foundation

A local, loopback-only view of the work registry. This is the UI-1 slice: the app shell, version
gate, consistent read model and per-source provenance. It has **no** mutation, dispatch or policy
route — those are later, separately reviewed slices.

## Run

```bash
python3 ui/serve.py                 # http://127.0.0.1:8788
python3 ui/serve.py --port 0        # pick a free port
python3 ui/serve.py --db /path/to/work.db
```

Run it from the **promoted release** (`~/.local/state/loom/engine`), the same tree the scheduler
executes. Started from a development checkout while a release is installed, it refuses to serve:
the engine rules it imported would not be the engine rules that are running.

`--host` accepts only loopback names (`127.0.0.1`, `localhost`, `::1`). A LAN or proxied deployment
needs its own authentication and transport design; it is not a flag here.

## Boundaries

| Boundary | How it is enforced |
|---|---|
| Loopback only | `_loopback()` refuses a non-loopback bind; every request must carry a loopback `Host`; cross-`Origin` requests are rejected |
| Read-only | only `GET`/`HEAD` are routed; every other method is `405` without reading a body |
| One snapshot | registry documents, edges, claims and the event head are read inside one `BEGIN DEFERRED` transaction on a `mode=ro` connection |
| Shared rules | claimability, dependency satisfaction, path conflicts and downstream counts come from `engine`, not a copy |
| Fail closed | a version mismatch, drifted release or unreadable registry returns `503` with the reason, never a partial page |
| No script | the shell is server-rendered; task text is escaped and the CSP forbids everything but inline style |

## Routes

| Route | Returns |
|---|---|
| `GET /` | the rendered shell |
| `GET /api/health` | gate state; `503` when closed |
| `GET /api/version` | front door, release commit, digests, verdict |
| `GET /api/snapshot` | full read model plus every provenance signal |
| `GET /api/tasks`, `GET /api/tasks/<id>` | task views (claimability separate from state) |
| `GET /api/boards` | boards with task counts |
| `GET /api/provenance` | source signals only |

Unknown routes are `404`; unsupported methods are `405` with `Allow: GET, HEAD`.

## Provenance

Six sources are sampled independently, each with its own `as_of` and status (`ok`, `degraded`,
`unavailable`):

| Source | Sampled from |
|---|---|
| `database` | the snapshot's read time, head event and content digest |
| `process` | pending `launch` events in the snapshot, probed with `engine.launch_is_live` |
| `scheduler` | the launch agent plist for the configured tick label |
| `release` | `engine.release`, the running root, and a recomputed tree digest |
| `git` | `git -C <owner repo> rev-parse` for each distinct owner repository |
| `evidence` | existence of each task's evidence file |

They are never merged into one timestamp. Two sources sampled a second apart are still two samples.

## Tests

```bash
cd capabilities/work
python3 -m unittest discover -s ui/tests -t ui/tests -v
```

The suite covers read-model parity with `store.SqliteStore.load`, claimability agreement with
`loom list --ready`, version-gate verdicts, independent provenance sampling, and the HTTP
boundaries (loopback Host, read-only methods, escaping).
