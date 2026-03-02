from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_SETTINGS_LOCK = threading.Lock()
_SETTINGS_CACHE: dict[str, Any] | None = None

DEFAULT_SETTINGS: dict[str, Any] = {
    "network_drive": "Z:/DATA",
    "fiscal_year": None,
    "open_fiscal_years": [],
    "max_attachment_mb": 50,
    "default_extensions": ["pdf", "xml", "xlsx", "zip", "jpg", "png"],
    "appearance_mode": "System",
    "download_workers": None,
    "pdf_max_pages": 4,
    "hacienda_timeout": 10.0,
    "hacienda_retries": 2,
    "classification_rules": [],
}


def _sanitize(settings: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(settings)
    if out.get("fiscal_year") in (None, "", 0):
        out["fiscal_year"] = _dt.date.today().year

    open_years = out.get("open_fiscal_years") or []
    years: list[int] = []
    for y in open_years:
        try:
            years.append(int(y))
        except (TypeError, ValueError):
            continue
    if out["fiscal_year"] not in years:
        years.append(int(out["fiscal_year"]))
    out["open_fiscal_years"] = sorted(set(years))

    rules = out.get("classification_rules")
    if not isinstance(rules, list):
        out["classification_rules"] = []

    if out.get("appearance_mode") not in {"Light", "Dark", "System"}:
        out["appearance_mode"] = "System"

    return out


def _config_dir_from(settings: dict[str, Any]) -> Path:
    base = Path(str(settings.get("network_drive") or "Z:/DATA"))
    return base / "CONFIG"


def settings_path() -> Path:
    cfg = get_settings()
    return _config_dir_from(cfg) / "settings.json"


def get_settings() -> dict[str, Any]:
    global _SETTINGS_CACHE
    with _SETTINGS_LOCK:
        if _SETTINGS_CACHE is not None:
            return deepcopy(_SETTINGS_CACHE)

        settings = deepcopy(DEFAULT_SETTINGS)
        path = _config_dir_from(settings) / "settings.json"
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    settings.update(raw)
        except Exception:
            logger.warning("No se pudo leer settings.json, usando defaults", exc_info=True)

        _SETTINGS_CACHE = _sanitize(settings)
        return deepcopy(_SETTINGS_CACHE)


def get_setting(key: str, default: Any = None) -> Any:
    settings = get_settings()
    if default is None and key in DEFAULT_SETTINGS:
        default = DEFAULT_SETTINGS[key]
    return settings.get(key, default)


def save_settings(new_values: dict[str, Any]) -> dict[str, Any]:
    global _SETTINGS_CACHE
    current = get_settings()
    current.update(new_values or {})
    final_settings = _sanitize(current)
    path = _config_dir_from(final_settings) / "settings.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(final_settings, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception:
        logger.warning("No se pudo guardar settings.json", exc_info=True)
    with _SETTINGS_LOCK:
        _SETTINGS_CACHE = deepcopy(final_settings)
    return final_settings


def resolve_fiscal_year_from_clave(clave: str | None, open_years: list[int]) -> int | None:
    if not clave:
        return None
    digits = "".join(ch for ch in str(clave) if ch.isdigit())
    if len(digits) != 50:
        return None
    try:
        yy = int(digits[7:9])
    except ValueError:
        return None
    year = 2000 + yy
    return year if year in set(open_years or []) else None
