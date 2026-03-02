from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app3.core.settings import get_setting

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
