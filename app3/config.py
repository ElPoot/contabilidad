from __future__ import annotations

from pathlib import Path

from .bootstrap import bootstrap_legacy_paths

bootstrap_legacy_paths()

from facturacion_system.core.settings import get_setting  # noqa: E402


def network_drive() -> Path:
    return Path(str(get_setting("network_drive", "Z:/DATA")))


def client_root(year: int) -> Path:
    return network_drive() / f"PF-{year}" / "CLIENTES"


def metadata_dir(client_folder: Path) -> Path:
    path = client_folder / ".metadata"
    path.mkdir(parents=True, exist_ok=True)
    return path
