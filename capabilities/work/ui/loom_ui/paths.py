"""Where the engine lives, and how the UI finds the supported front door.

The front door is the ``loom`` script, loaded as a module. Loading it (rather than re-deriving its
paths) means the UI asks the engine what it is running, exactly as ``loom deploy status`` does.
"""
from __future__ import annotations

import importlib.util
import os
from functools import lru_cache
from importlib.machinery import SourceFileLoader
from pathlib import Path

from . import ENGINE_HOME

REPO_ROOT = ENGINE_HOME.parents[1]
FRONT_DOOR = ENGINE_HOME / "loom"
DEFAULT_DB = Path.home() / ".local/state/loom/work.db"


def default_db() -> Path:
    """The database the CLI would use, honouring the same environment variables."""
    return Path(os.environ.get("WORK_DB") or DEFAULT_DB).expanduser()


def engine_home() -> Path:
    """The work home this process actually imported ``engine`` and ``store`` from."""
    return ENGINE_HOME


@lru_cache(maxsize=1)
def front_door():
    """The ``loom`` front door as a module.

    ``loom`` is a script without a ``.py`` suffix, so it is loaded by path. It is loaded once; the
    module carries ``engine_dir``, ``running_root``, ``read_release`` and ``tree_digest`` — the
    engine's own answers to "which release am I?" and "is its tree intact?".
    """
    path = FRONT_DOOR
    if not path.is_file():
        raise FileNotFoundError(f"no loom front door at {path}")
    loader = SourceFileLoader("loom_front_door", str(path))
    spec = importlib.util.spec_from_loader("loom_front_door", loader)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load the loom front door from {path}")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module
