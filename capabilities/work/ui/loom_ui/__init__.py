"""Local UI for the Loom work registry: a read model, and guarded task actions.

The UI owns no state. It reads one consistent snapshot of the registry and derives every displayed
value through the engine's own rules, so a claim that is claimable here is claimable because
``engine.dependencies_met`` said so, not because a second implementation guessed.

Actions do not change that. A browser can only ask this server to run one of a fixed set of
``loom`` verbs as an identity the server paired with it, presenting the revision and epoch the page
showed. Loom still decides whether the action is legal; the UI decides only what to offer and how
to report the answer.

Layout:

``paths``       where the engine home, front door and default database are
``version``     front-door resolution and the fail-closed version gate
``snapshot``    one consistent, read-only SQLite read (registry + event head)
``model``       typed read models; claimability from shared engine logic
``control``     owner queues and per-task inspection, folded from one snapshot's events
``identity``    server-side human identities and roles (empty by default: no actor, no actions)
``session``     pairing tokens, signed session cookies, CSRF and one-shot form nonces
``actions``     the action allow-list, gating, validation and the guarded subprocess runner
``pages``       the control room, task index and task inspector
``graph``       program/focus-scoped dependency views derived from the engine's edges
``graph_page``  the drawn DAG and its complete table equivalent
``operations``  attempts, capacity, leases, tick, schedule, release and log health
``action_pages`` the confirmation form and the outcome page for one action
``provenance``  independent source signals, each with its own ``as_of`` and degraded state
``server``      loopback-only HTTP shell: reads, pairing, and the guarded action route
"""
from __future__ import annotations

import sys
from pathlib import Path

# `capabilities/work` — the engine home. The UI imports `engine` and `store` from here so it
# shares the engine's rules instead of copying them. Nothing outside this directory is written.
ENGINE_HOME = Path(__file__).resolve().parents[2]
if str(ENGINE_HOME) not in sys.path:
    sys.path.insert(0, str(ENGINE_HOME))

__version__ = "0.5.0"
