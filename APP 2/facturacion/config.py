"""Configuración y utilidades de rutas para la aplicación."""
from __future__ import annotations

import logging
import os
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
LOGGER = logging.getLogger(__name__)


def resolve_hacienda_cache_db_path() -> str:
    """Resuelve ruta de cache local para nombres de Hacienda."""
    env_db = os.getenv("HACIENDA_CACHE_DB")
    repo_root = Path(__file__).resolve().parent.parent

    candidates: list[Path] = []
    if env_db:
        candidates.append(Path(env_db).expanduser())

    candidates.append(Path(r"C:\GITHUB\MASS-DOWNLOAD\facturacion_system\data\hacienda_cache.db"))
    candidates.append(repo_root / "data" / "hacienda_cache.db")
    candidates.append(repo_root / "Data" / "hacienda_cache.db")

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    fallback = repo_root / "data" / "hacienda_cache.db"
    fallback.parent.mkdir(parents=True, exist_ok=True)
    return str(fallback)


def resolve_default_data_dir() -> Path:
    """Resuelve la carpeta de datos preferida (DATA_DIR, ./data o ./Data)."""
    env_data_dir = os.getenv("DATA_DIR")
    candidates: list[Path] = []
    if env_data_dir:
        candidates.append(Path(env_data_dir).expanduser())
    repo_root = Path(__file__).resolve().parent.parent
    candidates.extend([repo_root / "data", repo_root / "Data"])
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return candidates[-1]


def resolve_audit_log_dir() -> Path:
    """Resuelve carpeta de logs de auditoría y la crea si no existe."""
    repo_root = Path(__file__).resolve().parent.parent
    audit_dir = repo_root / "data" / "audit_logs"
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir
