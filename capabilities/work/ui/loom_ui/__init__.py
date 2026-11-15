"""Read-only local UI foundation for the Loom work registry.

The UI owns no state. It reads one consistent snapshot of the registry and derives every
displayed value through the engine's own rules, so a claim that is claimable here is claimable
because ``engine.dependencies_met`` said so, not because a second implementation guessed.

Layout:

``paths``       where the engine home, front door and default database are
``version``     front-door resolution and the fail-closed version gate
``snapshot``    one consistent, read-only SQLite read (registry + event head)
``model``       typed read models; claimability from shared engine logic
``provenance``  independent source signals, each with its own ``as_of`` and degraded state
``server``      loopback-only HTTP shell; GET/HEAD only, no mutation route exists
"""
from __future__ import annotations

import sys
from pathlib import Path

# `capabilities/work` — the engine home. The UI imports `engine` and `store` from here so it
# shares the engine's rules instead of copying them. Nothing outside this directory is written.
ENGINE_HOME = Path(__file__).resolve().parents[2]
if str(ENGINE_HOME) not in sys.path:
    sys.path.insert(0, str(ENGINE_HOME))

__version__ = "0.1.0"
