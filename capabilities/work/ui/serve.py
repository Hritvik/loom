#!/usr/bin/env python3
"""Start the read-only local UI.

    python3 ui/serve.py [--port 8788] [--db PATH]

It binds loopback only and exposes no mutation route. See ``loom_ui/server.py`` for the boundary
rules and ``ui/README.md`` for how to run it from the promoted release.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from loom_ui.server import main  # noqa: E402 — after the package directory is importable

if __name__ == "__main__":
    sys.exit(main())
