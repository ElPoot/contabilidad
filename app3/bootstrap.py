from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_legacy_paths() -> None:
    """Add App 1 folder to sys.path so App 3 can reuse its modules."""
    repo_root = Path(__file__).resolve().parent.parent
    legacy_paths = [repo_root / "APP 1"]  # APP 2 ya no es necesario
    for path in legacy_paths:
        path_str = str(path)
        if path.exists() and path_str not in sys.path:
            sys.path.insert(0, path_str)
