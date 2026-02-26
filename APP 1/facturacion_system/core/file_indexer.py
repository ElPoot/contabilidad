import os
from collections.abc import Callable, Sequence
from pathlib import Path


def index_files(
    destino: str, extensiones: Sequence[str], state_cls: type, hash_func: Callable[[bytes], str]
) -> int:
    """Index existing files under *destino* using *state_cls* and *hash_func*.

    Only files with extensions listed in *extensiones* are considered (case-insensitive).
    Returns the number of new files indexed.
    """
    base = Path(destino)
    state = state_cls(base)
    count = 0
    for root, _, files in os.walk(base):
        for f in files:
            if extensiones and not any(
                f.lower().endswith(x if x.startswith(".") else "." + x) for x in extensiones
            ):
                continue
            p = Path(root) / f
            try:
                digest = hash_func(p.read_bytes())
                if not state.seen(digest):
                    state.mark(digest, str(p), "", "")
                    count += 1
            except Exception:
                pass
    return count
