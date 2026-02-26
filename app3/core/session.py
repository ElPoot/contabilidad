from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app3.bootstrap import bootstrap_legacy_paths
from app3.config import client_root

bootstrap_legacy_paths()

from facturacion_system.core.client_profiles import load_profiles  # noqa: E402
from facturacion_system.core.settings import get_setting  # noqa: E402
from facturacion.xml_manager import CRXMLManager  # noqa: E402


@dataclass(slots=True)
class ClientSession:
    cedula: str
    nombre: str
    folder: Path
    year: int


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def resolve_client_session(cedula: str, year: int | None = None) -> ClientSession:
    clean = _digits(cedula)
    if len(clean) < 9:
        raise ValueError("La cédula no parece válida.")

    if year is None:
        year = int(get_setting("fiscal_year"))

    manager = CRXMLManager()
    nombre = manager.resolve_party_name(clean, "")
    if not nombre:
        raise ValueError("No se encontró contribuyente en cache/API Hacienda.")

    base = client_root(year)
    expected = base / nombre
    if expected.exists():
        return ClientSession(cedula=clean, nombre=nombre, folder=expected, year=year)

    profiles = load_profiles()
    for key, value in profiles.items():
        if key.startswith("__email__:"):
            continue
        folder_name = key.strip()
        profile_ced = _digits(str((value or {}).get("cedula", ""))) if isinstance(value, dict) else ""
        if profile_ced == clean:
            folder = base / folder_name
            if folder.exists():
                return ClientSession(cedula=clean, nombre=nombre, folder=folder, year=year)

    raise FileNotFoundError(f"No existe carpeta de cliente para '{nombre}' en {base}")
