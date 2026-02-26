from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any

from facturacion_system.core.settings import get_setting

logger = logging.getLogger(__name__)


def _profiles_path() -> Path:
    return Path(str(get_setting("network_drive", "Z:/DATA"))) / "CONFIG" / "client_profiles.json"


def load_profiles() -> dict[str, Any]:
    path = _profiles_path()
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
    except Exception:
        logger.warning("No se pudo leer client_profiles.json", exc_info=True)
    return {}


def get_profile(client_name: str) -> dict[str, Any] | None:
    if not client_name:
        return None
    return load_profiles().get(client_name)


def save_profile(client_name: str, profile: dict[str, Any]) -> None:
    if not client_name:
        return
    all_profiles = load_profiles()
    all_profiles[client_name] = profile
    path = _profiles_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(all_profiles, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        logger.warning("No se pudo guardar client_profiles.json", exc_info=True)



def get_email_link(account_email: str) -> dict | None:
    """Retorna el vínculo {'client_folder_name', 'cedula', 'linked_at'} para un email, o None."""
    email = (account_email or "").strip().lower()
    if not email:
        return None
    key = f"__email__:{email}"
    raw = load_profiles().get(key)
    return raw if isinstance(raw, dict) else None


def save_email_link(account_email: str, client_folder_name: str, cedula: str) -> None:
    """Guarda el vínculo email → carpeta en client_profiles.json bajo clave '__email__:{email}'."""
    email = (account_email or "").strip().lower()
    folder = (client_folder_name or "").strip()
    if not email or not folder:
        return
    all_profiles = load_profiles()
    all_profiles[f"__email__:{email}"] = {
        "client_folder_name": folder,
        "cedula": (cedula or "").strip(),
        "linked_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    path = _profiles_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(all_profiles, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        logger.warning("No se pudo guardar vínculo email→carpeta", exc_info=True)


def resolve_client_folder(account_email: str) -> Path | None:
    """
    Dado un email, retorna la Path de la carpeta del cliente para el año fiscal activo.
    - Lee el vínculo guardado con get_email_link()
    - Reconstruye el path: Z:/DATA/PF-{max(open_fiscal_years)}/CLIENTES/{client_folder_name}
    - Si la carpeta no existe en disco, la crea
    - Retorna None si no hay vínculo guardado para ese email
    """
    link = get_email_link(account_email)
    if not link:
        return None

    client_folder_name = str(link.get("client_folder_name") or "").strip()
    if not client_folder_name:
        return None

    open_years = get_setting("open_fiscal_years", [])
    year = max(open_years) if open_years else datetime.date.today().year
    network_drive = Path(str(get_setting("network_drive", "Z:/DATA")))
    folder = network_drive / f"PF-{year}" / "CLIENTES" / client_folder_name
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("No se pudo crear carpeta para vínculo de email: %s", folder, exc_info=True)
        return None

    return folder
