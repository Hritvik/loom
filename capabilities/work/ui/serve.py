#!/usr/bin/env python3
"""Start the local Loom UI.

    python3 ui/serve.py [--port 8788] [--db PATH] [--human identity:roles]...

It binds loopback only. Without a configured human identity it is read-only and says so; with one it
prints a pairing link per identity and enables the guarded task actions in
``loom_ui/actions.py``. See ``loom_ui/server.py`` for the boundary rules and ``ui/README.md`` for
how to run it from the promoted release.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from loom_ui.server import main  # noqa: E402 — after the package directory is importable

if __name__ == "__main__":
    sys.exit(main())
