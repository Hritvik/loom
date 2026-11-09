# Portfolio Delivery sync to Linear

[Open Portfolio Delivery in Linear](https://linear.app/hritvik-dinkar/project/portfolio-delivery-69c2eb2cf1f7/issues).

The HRI team hosts one project for the portfolio. The work ID identifies each card, and
its product, next actor, implementer, reviewer, and blocker are shown separately. The
sync publishes links to prerequisite cards and names what a task unlocks. Linear card
moves never change the registry. Comments and text outside the managed description
section are preserved from the latest issue read. Use comments for notes written
while the sync runs. Linear descriptions have no compare-and-swap check in this flow.

## Board setup

Use a board view grouped by issue status. Set up the following states in the HRI
team, or map the lane keys in the config to existing states. The sync validates
the destination and required states before writing cards.

| Lane key | Suggested state |
| --- | --- |
| claimable | Claimable |
| implementing | Implementing |
| needs_review | Needs Review Submission |
| in_review | In Review |
| changes_requested | Changes Requested |
| awaiting_integration | Awaiting Integration |
| integrated | Integrated |
| blocked | Blocked |

Save views for product work, review work and coordination. Filter product runway
to Claimable, Implementing and Changes Requested. Filter reviewer runway to In
Review. Filter coordination to Needs Review Submission, Awaiting Integration and
Blocked. Use the work ID prefix to find one product's cards within the shared project.

## Setup and first run

Run from this directory with the same Python environment used by `./task`.
Save the destination config outside Git and keep its location stable for recurring runs.

```json
{
  "team_id": "e7d2385b-dde9-42b2-9044-41647917266a",
  "project_id": "8553a1cf-cd57-422e-959e-05f641b8c8c4"
}
```

An optional `states` object overrides default state names, for example
`{"claimable":"Todo","implementing":"In Progress","integrated":"Done"}`.
Store a team-scoped Linear credential in the runner's secret environment as
`LINEAR_API_KEY`. The API key needs read access to the destination and permission
to create and update issues. The ChatGPT connection and this runner are separate.

```bash
python3 linear_sync.py
python3 linear_sync.py --all
python3 linear_sync.py --config /path/to/linear-portfolio.json
python3 linear_sync.py --config /path/to/linear-portfolio.json --apply
python3 linear_sync.py --config /path/to/linear-portfolio.json
```

The first two commands project the registry locally. A config with no `--apply`
fetches the destination and previews creates and updates. Without `--all`, the
default scope is `dutch/S7` and its transitive prerequisites. Use `--focus` for a
different release. After the Dutch pilot is verified, add `--all` against the same
destination to include other products without duplicating pilot cards.

Check that created cards display the correct states, reviewer and next actor, and
prerequisite links. Rerun with the same scope and expect zero creates and updates.
The sync never deletes or archives cards. If a managed card has been archived or
its marker is damaged, restore or repair it before retrying. Do not drag managed
cards to change the underlying workflow.

## Recurring run

After a successful live preview and pilot, schedule the exact verified apply
command using the local automation runner, with the credential available in its
secret environment. Keep one writer per destination. Start at 30-minute intervals
and report failures. Expand the command to `--all` after the all-portfolio preview
is checked. Concurrent runs sharing one config are serialized. A failed request
stops the run, and the next invocation reconciles what reached Linear before it
writes again. Workflow state changes and saved views are managed in Linear.

The mirror includes work titles and identifiers, recorded agent aliases, task states,
and dependency links. It excludes raw audit notes, private file paths, credentials,
and diffs.

## Verification

The local preview verified 62 cards in the Dutch release dependency path and 183
across the portfolio at setup. The full work-coordination suite passed 121 tests,
including 27 Linear tests for projection, GraphQL transport, preflight, repeated
sync, linked dependencies and preserving notes. The actual workspace still
requires a credentialed preview and a second live run to verify no-op reconciliation.
