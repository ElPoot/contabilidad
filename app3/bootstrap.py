from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_legacy_paths() -> None:
    """Add legacy app folders to sys.path so App 3 can reuse App1/App2 modules."""
    repo_root = Path(__file__).resolve().parent.parent
    legacy_paths = [repo_root / "APP 1", repo_root / "APP 2"]
    for path in legacy_paths:
        path_str = str(path)
        if path.exists() and path_str not in sys.path:
            sys.path.insert(0, path_str)
