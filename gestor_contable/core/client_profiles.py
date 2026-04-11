from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
import time
from shutil import copy2
from datetime import datetime
from pathlib import Path
from typing import Any

from gestor_contable.core.settings import get_setting

logger = logging.getLogger(__name__)

_WRITE_LOCK = threading.Lock()


class ClientProfilesError(RuntimeError):
    """Error al leer o escribir client_profiles.json."""


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

def _profiles_path() -> Path:
    return Path(str(get_setting("network_drive", "Z:/DATA"))) / "CONFIG" / "client_profiles.json"


def _hacienda_cache_path() -> Path:
    return Path(str(get_setting("network_drive", "Z:/DATA"))) / "hacienda_cache.db"

def _profiles_tmp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp")


def _profiles_backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.bak")


def _dump_profiles(profiles: dict[str, Any]) -> str:
    return json.dumps(profiles, ensure_ascii=False, indent=2)


def save_profiles(profiles: dict[str, Any]) -> None:
    """Guarda client_profiles.json con escritura atómica y respaldo .bak."""
    if not isinstance(profiles, dict):
        raise ClientProfilesError(
            f"client_profiles.json debe guardarse desde un dict, no desde {type(profiles).__name__}"
        )

    path = _profiles_path()
    tmp = _profiles_tmp_path(path)
    backup = _profiles_backup_path(path)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(_dump_profiles(profiles), encoding="utf-8")

        if path.exists():
            copy2(path, backup)

        tmp.replace(path)
    except Exception as exc:
        logger.exception("No se pudo guardar client_profiles.json en %s", path)
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            logger.debug("No se pudo limpiar temporal de perfiles %s", tmp, exc_info=True)
        raise ClientProfilesError(f"No se pudo guardar client_profiles.json en {path}") from exc


# ---------------------------------------------------------------------------
# Lectura básica de perfiles (API existente — sin cambios)
# ---------------------------------------------------------------------------

def load_profiles() -> dict[str, Any]:
    path = _profiles_path()
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ClientProfilesError(
                    f"client_profiles.json en {path} debe contener un objeto JSON, no {type(raw).__name__}"
                )
            return raw
    except ClientProfilesError:
        logger.exception("client_profiles.json existente pero inválido en %s", path)
        raise
    except Exception as exc:
        if path.exists():
            logger.exception("No se pudo leer client_profiles.json en %s", path)
            raise ClientProfilesError(f"No se pudo leer client_profiles.json en {path}") from exc
    return {}


def get_profile(client_name: str) -> dict[str, Any] | None:
    if not client_name:
        return None
    return load_profiles().get(client_name)


# ---------------------------------------------------------------------------
# Actividades económicas CIIU del cliente (Hacienda)
# ---------------------------------------------------------------------------

def get_saved_activities(client_name: str) -> list[dict[str, str]]:
    """
    Retorna las actividades económicas guardadas en el perfil del cliente.

    Returns lista de dicts {codigo, descripcion, estado}.
    Returns [] si el cliente no tiene actividades guardadas aún.
    """
    profile = get_profile(client_name)
    if not profile:
        return []
    acts = profile.get("actividades_hacienda")
    if not isinstance(acts, list):
        return []
    # Si ninguna actividad tiene el campo 'tipo', fueron guardadas antes de que
    # se introdujera ese campo — re-fetch para obtener P/S correctamente.
    if acts and all("tipo" not in a for a in acts):
        return []
    return acts


def fetch_and_save_activities(client_name: str, cedula: str) -> list[dict[str, str]]:
    """
    Obtiene las actividades económicas CIIU del cliente desde Hacienda
    (cache local primero, API si no están) y las guarda en client_profiles.json.

    Args:
        client_name: Clave del cliente en client_profiles.json.
        cedula:      Cédula jurídica/física del cliente (solo dígitos).

    Returns:
        Lista de actividades {codigo, descripcion, estado}.
        Lista vacía si no se pudo obtener información.
    """
    cedula = "".join(ch for ch in str(cedula or "") if ch.isdigit())
    if not cedula:
        return []

    actividades = _activities_from_cache(cedula)

    if actividades is None:
        # No está en cache — consultar API y cachear
        actividades = _fetch_activities_from_api(cedula)

    if actividades is None:
        logger.warning("No se pudieron obtener actividades para cédula %s", cedula)
        return []

    # Guardar en perfil del cliente
    _save_activities_to_profile(client_name, actividades)
    return actividades


def get_or_fetch_activities(client_name: str, cedula: str) -> list[dict[str, str]]:
    """
    Retorna actividades del cliente. Usa las guardadas en perfil si existen;
    si no, las obtiene desde Hacienda y las guarda.

    Es la función de alto nivel a usar desde el corte engine y la UI.
    """
    saved = get_saved_activities(client_name)
    if saved:
        return saved
    return fetch_and_save_activities(client_name, cedula)


# ---------------------------------------------------------------------------
# Internos — lectura de actividades
# ---------------------------------------------------------------------------

def _activities_from_cache(cedula: str) -> list[dict[str, str]] | None:
    """
    Lee el raw_json del hacienda_cache.db y extrae el campo 'actividades'.
    Retorna None si no hay entrada en cache o no tiene raw_json.
    """
    cache_path = _hacienda_cache_path()
    if not cache_path.exists():
        return None

    try:
        with contextlib.closing(sqlite3.connect(str(cache_path))) as conn:
            row = conn.execute(
                "SELECT raw_json FROM hacienda_cache WHERE identificacion = ?",
                (cedula,),
            ).fetchone()
    except Exception:
        logger.debug("No se pudo leer hacienda_cache.db para %s", cedula, exc_info=True)
        return None

    if not row or not row[0]:
        return None

    try:
        payload = json.loads(row[0])
    except (ValueError, TypeError):
        return None

    return _parse_actividades(payload)


def _fetch_activities_from_api(cedula: str) -> list[dict[str, str]] | None:
    """
    Consulta la API de Hacienda directamente y retorna las actividades.
    También actualiza el hacienda_cache.db con la respuesta completa.
    """
    try:
        import requests
    except ModuleNotFoundError:
        return None

    url = f"https://api.hacienda.go.cr/fe/ae?identificacion={cedula}"
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=10)
        except requests.RequestException:
            time.sleep(0.6 * (attempt + 1))
            continue

        if resp.status_code == 200:
            try:
                payload = resp.json()
            except ValueError:
                return None

            # Actualizar hacienda_cache.db con el raw_json completo
            _update_hacienda_cache(cedula, payload)
            return _parse_actividades(payload)

        if resp.status_code in (404, 204):
            return []

        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(0.8 * (attempt + 1))
            continue

        return None

    return None


def _parse_actividades(payload: dict) -> list[dict[str, str]]:
    """
    Extrae y normaliza el campo 'actividades' del JSON de Hacienda.

    La API puede devolver el campo como 'actividades' o 'actividadesEconomicas'.
    Cada actividad tiene: codigo, descripcion, estado (A=activa, I=inactiva).
    Solo se devuelven las activas.
    """
    raw_acts = payload.get("actividades") or payload.get("actividadesEconomicas") or []
    if not isinstance(raw_acts, list):
        return []

    result: list[dict[str, str]] = []
    for act in raw_acts:
        if not isinstance(act, dict):
            continue
        estado = str(act.get("estado") or "A").strip().upper()
        codigo = str(act.get("codigo") or act.get("codigoActividad") or "").strip()
        desc = str(act.get("descripcion") or act.get("descripcionActividad") or "").strip()
        tipo = str(act.get("tipo") or "P").strip().upper()  # P=principal, S=secundaria
        if codigo:
            result.append({
                "codigo": codigo,
                "descripcion": desc,
                "estado": estado,
                "tipo": tipo,
            })

    # Priorizar activas (estado "A") pero incluir todas si no hay activas
    activas = [a for a in result if a["estado"] == "A"]
    return activas if activas else result


def _update_hacienda_cache(cedula: str, payload: dict) -> None:
    """Actualiza el raw_json en hacienda_cache.db sin alterar otros campos."""
    cache_path = _hacienda_cache_path()
    if not cache_path.exists():
        return
    try:
        nombre = str(
            payload.get("nombre") or payload.get("razonSocial") or payload.get("razon_social") or ""
        ).strip().upper()
        with contextlib.closing(sqlite3.connect(str(cache_path))) as conn:
            conn.execute(
                """
                INSERT INTO hacienda_cache(identificacion, razon_social, raw_json, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(identificacion) DO UPDATE SET
                    razon_social = excluded.razon_social,
                    raw_json     = excluded.raw_json,
                    updated_at   = excluded.updated_at
                """,
                (cedula, nombre, json.dumps(payload, ensure_ascii=False), int(time.time())),
            )
            conn.commit()
    except Exception:
        logger.debug("No se pudo actualizar hacienda_cache.db para %s", cedula, exc_info=True)


# ---------------------------------------------------------------------------
# Internos — escritura de actividades en perfil
# ---------------------------------------------------------------------------

def _save_activities_to_profile(client_name: str, actividades: list[dict[str, str]]) -> None:
    """
    Guarda las actividades en client_profiles.json bajo la clave del cliente.
    Crea la entrada del cliente si no existe.
    """
    if not client_name:
        return

    with _WRITE_LOCK:
        profiles = load_profiles()
        entry = profiles.get(client_name)
        if not isinstance(entry, dict):
            entry = {}

        entry["actividades_hacienda"] = actividades
        entry["actividades_updated_at"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        profiles[client_name] = entry
        save_profiles(profiles)
