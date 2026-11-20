## Planning location

<!-- coordination:start — identical across repos; update centrally, do not edit per repo -->
Active maps, tickets, specs, designs and implementation plans live in the content repository at
`~/repositories/portfolio-planning`. The coordination engine — registry, claims, evidence, decisions,
MCP and A2A — is the work home `$WORK_HOME`, your loom checkout at
`~/repositories/loom/capabilities/work`.

- Read `$WORK_HOME/AGENT-GUIDE.md` first: the coordination loop, the enforced rules, and how to
  escalate. `$WORK_HOME/PROTOCOL.md` holds the state model and the claim/lease protocol.
- Find work with `cd $WORK_HOME && ./loom list --ready`, check `./loom inbox --agent <you>`, and
  claim with `./loom claim <id> --revision N --ttl 45m --agent <you>`.
- Export `WORK_HOME` and `WORK_DB` (the registry, `~/.local/state/loom/work.db`). Evidence and
  decisions resolve under `$WORK_HOME`; never write them inside a promoted release tree, which
  drifts the release digest and closes the version gate.
- A candidate integrates only when its approved commit is on the integration branch: merge the
  candidate, review the merge commit, integrate that exact commit. A review taken before a rebase onto
  that branch cannot integrate — the gate refuses it and names the task, so do not spend a merge on it.
- Task state lives in `$WORK_DB`; inherited memory lives in the content repository at
  `~/repositories/portfolio-planning/.ecc/memory/team`.
- Promote only accepted specs/ADRs here, under `docs/`; active planning stays central.
<!-- coordination:end -->
