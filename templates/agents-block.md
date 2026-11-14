## Planning location

<!-- coordination:start — identical across repos; update centrally, do not edit per repo -->
Active maps, tickets, specs, designs and implementation plans live in the content repository at
`$WORK_CONTENT_HOME`. The coordination engine — registry, claims, contracts, MCP and A2A — is
`$WORK_COORDINATION_HOME`.

- Read `$WORK_COORDINATION_HOME/capabilities/work/AGENT-GUIDE.md` first: the coordination loop, the
  enforced rules, and how to escalate.
- Find work with `cd $WORK_COORDINATION_HOME/capabilities/work && ./loom list --ready`,
  check `./loom inbox --agent <you>`, and claim with
  `./loom claim <id> --revision N --ttl 45m --agent <you>`.
- `$WORK_COORDINATION_HOME` is your loom checkout; export it, or replace it with the path.
- Task state lives in `$WORK_DB`; inherited memory lives in the content repository.
- Promote only accepted specs/ADRs here, under `docs/`; active planning stays central.
<!-- coordination:end -->
