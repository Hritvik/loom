# Adopting loom

Fifteen minutes from nothing to a claimed task.

## 1. Install

```bash
git clone https://github.com/Hritvik/loom.git ~/repositories/loom
cd ~/repositories/loom
git config core.hooksPath .githooks        # the coordination pre-commit guard
python3 -m unittest discover -s capabilities/work/tests
```

Only Python 3 and PyYAML are required. The database is SQLite, created on first use.

## 2. Point it at your content and contracts

```bash
export WORK_CONTENT_HOME=~/repositories/<your-content-repo>
export WORK_CONTRACTS=~/repositories/<your-contracts-repo>   # optional
export WORK_DB=~/.local/state/loom/work.db
```

`WORK_CONTENT_HOME` is where maps, plans and specs live. `WORK_CONTRACTS` is only needed if you
validate a contract registry; without it that check is skipped.

## 3. Register a board, then a task

A board is configuration, not an event: it names a repository and the file that holds the packet
detail. Seed one with the store, then use the CLI for everything else.

```python
import sys; sys.path.insert(0, "<loom>/capabilities/work")
from store import make_store
store = make_store("<loom>/capabilities/work", "sqlite", None)
store.registry = {"kind": "work-coordination-registry", "version": 1, "updated": "2026-01-01",
                  "boards": [{"id": "alpha.program", "kind": "program", "role": "provider",
                              "repo": "~/repositories/alpha", "path": "docs/work-items.md"}],
                  "tasks": [], "edges": [], "claims": []}
store.save(); store.close()
```

```bash
cd ~/repositories/loom/capabilities/work
./task create --agent you/alpha-agent --id alpha/W-1 --board alpha.program \
  --title "First task" --status ready --write-path src/first
./task list --ready
```

## 4. Work the loop

```bash
./task claim alpha/W-1 --revision 0 --ttl 45m --agent you/alpha-agent
# -> claimed alpha/W-1 at revision 1 ... epoch 1
./task transition alpha/W-1 --revision 1 --epoch 1 --to red   --agent you/alpha-agent
./task transition alpha/W-1 --revision 2 --epoch 1 --to green --agent you/alpha-agent
```

The claim prints its fencing epoch. Present it on `renew`, `release` and `transition` while you hold
the claim; a takeover increments it and fences the old attempt.

Review is a two-step handshake, and the reviewer must never be a claimant:

```bash
./task ask alpha/W-1 --to you/reviewer --agent you/alpha-agent \
  --question "Review the submitted revision" --review-source <40-char commit> \
  --review-evidence evidence/W-1.md
./task answer alpha/W-1 --question <ask seq> --decision approve \
  --note "Reproduced the checks" --agent you/reviewer
./task transition alpha/W-1 --revision 3 --to reviewed --reviewer you/reviewer \
  --decision approve --review-answer <answer seq> --agent you/integrator
./task transition alpha/W-1 --revision 4 --to integrated \
  --evidence evidence/W-1.md --integrated-source <the approved commit> --agent you/alpha-agent
```

Two flags catch people out: `--review-answer` takes the **answer** event's sequence, not the
question's, and `--integrated-source` must be the exact commit the reviewer approved.

This handshake can run unattended. `loom tick --apply` launches an independent reviewer for each
open request addressed to `opencode/reviewer-agent` and records `reviewed` from an approval. The
reviewer decides; the coordinator records, so the acting agent is never the reviewer of record.

## 5. Connect an agent

```bash
python3 capabilities/work/mcp/server.py --print-config opencode
```

Paste the snippet into your client's MCP configuration. The server infers the repository from the
working directory and the agent identity from the program, so one global entry serves every
repository.

## 6. See it work

```bash
python3 examples/walkthrough/walkthrough.py
```

Two synthetic programs, three tasks, one complete cycle in a throwaway database.

## Where things live afterwards

`docs/artifacts.md` lists the seven artefact kinds, their homes and their promotion paths.
`templates/` holds a starter for each.
