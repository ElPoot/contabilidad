from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from gestor_contable.core.settings import get_setting

logger = logging.getLogger(__name__)

# ── Archivo de configuración local (no en red, no en repo) ────────────────────
# Permite sobrescribir la ruta origen del subst sin tocar el código.
# Ejemplo de contenido:
#   { "subst_source": "C:/Users/TuUsuario/OneDrive" }
_LOCAL_SETTINGS = Path.home() / ".gestor_contable" / "local_settings.json"


def _find_onedrive_path() -> Path | None:
    """
    Detecta la carpeta local de OneDrive en este orden de precedencia:
      1. ~/.gestor_contable/local_settings.json  → clave "subst_source"
      2. Variable de entorno %OneDrive% (Windows la setea automáticamente)
      3. Rutas comunes bajo el home del usuario
    """
    # 1. Override manual
    if _LOCAL_SETTINGS.exists():
        try:
            data = json.loads(_LOCAL_SETTINGS.read_text(encoding="utf-8"))
            src = data.get("subst_source", "")
            if src:
                p = Path(src)
                if p.exists():
                    logger.info("subst_source desde local_settings: %s", p)
                    return p
                logger.warning("subst_source configurado pero no existe: %s", p)
        except Exception as exc:
            logger.warning("No se pudo leer local_settings.json: %s", exc)

    # 2. Variable de entorno que Windows setea para OneDrive personal
    for env_key in ("OneDrive", "ONEDRIVE", "OneDriveConsumer", "OneDriveCommercial"):
        val = os.environ.get(env_key, "")
        if val:
            p = Path(val)
            if p.exists():
                logger.info("OneDrive detectado via %%%s%%: %s", env_key, p)
                return p

    # 3. Búsqueda en carpetas comunes del usuario
    home = Path.home()
    candidates: list[Path] = []
    try:
        candidates = [
            f for f in home.iterdir()
            if f.is_dir() and f.name.lower().startswith("onedrive")
        ]
    except Exception as exc:
        logger.debug("No se pudo escanear %s buscando OneDrive: %s", home, exc, exc_info=True)

    if candidates:
        chosen = sorted(candidates)[0]
        logger.info("OneDrive detectado en home: %s", chosen)
        return chosen

    return None


def ensure_drive_mounted() -> bool:
    """
    Verifica que Z: esté disponible. Si no lo está, intenta montarlo con subst
    usando la ruta de OneDrive detectada automáticamente.

    Retorna True si Z: está disponible al terminar.
    """
    drive_letter = get_setting("subst_drive_letter", "Z")
    drive = Path(f"{drive_letter}:/")

    if drive.exists():
        logger.debug("Disco %s: ya está montado.", drive_letter)
        return True

    source = _find_onedrive_path()
    if source is None:
        logger.error(
            "No se encontró ruta de OneDrive para montar %s:. "
            "Crea ~/.gestor_contable/local_settings.json con { \"subst_source\": \"<ruta>\" }",
            drive_letter,
        )
        return False

    try:
        result = subprocess.run(
            ["subst", f"{drive_letter}:", str(source)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            if drive.exists():
                logger.info("Disco %s: montado correctamente desde %s", drive_letter, source)
                return True
            logger.error("subst exitoso pero %s: no aparece en el sistema.", drive_letter)
        else:
            logger.error(
                "subst falló (código %d): %s",
                result.returncode,
                result.stderr.strip() or result.stdout.strip(),
            )
    except FileNotFoundError:
        logger.error("Comando 'subst' no encontrado. ¿Estás en Windows?")
    except subprocess.TimeoutExpired:
        logger.error("subst tardó demasiado y fue cancelado.")
    except Exception as exc:
        logger.error("Error inesperado al montar disco: %s", exc)

    return False


# ── Detección de archivos placeholder de OneDrive ─────────────────────────────
# Cuando "Archivos a petición" está activo, los archivos aparecen en el disco
# pero no están descargados. Intentar leerlos dispara la descarga y puede
# causar timeouts o PermissionError en el indexador y el visor de PDF.
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_FILE_ATTRIBUTE_RECALL_ON_OPEN        = 0x00040000
_PLACEHOLDER_MASK = _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS | _FILE_ATTRIBUTE_RECALL_ON_OPEN


def is_onedrive_placeholder(path: Path) -> bool:
    """
    Retorna True si el archivo es un placeholder de OneDrive (no descargado localmente).
    Solo aplica en Windows; en otros sistemas retorna False.
    """
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == -1:  # INVALID_FILE_ATTRIBUTES — archivo no existe
            return False
        return bool(attrs & _PLACEHOLDER_MASK)
    except Exception as exc:
        logger.debug(
            "is_onedrive_placeholder(%s): fallo consultando atributos Win32, retornando False: %s",
            path,
            exc,
            exc_info=True,
        )
        return False


def network_drive() -> Path:
    return Path(str(get_setting("network_drive", "Z:/DATA")))


def client_root(year: int) -> Path:
    return network_drive() / f"PF-{year}" / "CLIENTES"


def metadata_dir(client_folder: Path) -> Path:
    path = client_folder / ".metadata"
    path.mkdir(parents=True, exist_ok=True)
    return path
