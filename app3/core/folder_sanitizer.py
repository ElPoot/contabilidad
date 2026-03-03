"""Sanitización segura de carpetas vacías después de reclasificación."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def find_empty_folders(client_folder: Path) -> list[Path]:
    """
    Encuentra carpetas COMPLETAMENTE vacías en Contabilidades/{cliente}/.

    Una carpeta se considera vacía solo si:
    - No contiene archivos (ni .pdf, ni .txt, ni nada)
    - No contiene subcarpetas con archivos

    Args:
        client_folder: Ruta a Z:/DATA/PF-{year}/CLIENTES/{client}/

    Returns:
        Lista de rutas Path de carpetas vacías (en orden para eliminar hijos antes que padres)
    """
    if not client_folder.exists():
        logger.warning(f"Carpeta de cliente no existe: {client_folder}")
        return []

    contabilidades_root = client_folder.parent.parent / "Contabilidades" / client_folder.name
    if not contabilidades_root.exists():
        logger.debug(f"No hay Contabilidades para {client_folder.name}")
        return []

    empty_folders: list[Path] = []

    try:
        # Recorrer recursivamente todo en Contabilidades/{cliente}/
        for folder in sorted(contabilidades_root.rglob("*"), key=lambda p: (-len(p.parts), p)):
            if not folder.is_dir():
                continue

            # Verificar si la carpeta está vacía
            has_content = any(folder.iterdir())
            if not has_content:
                empty_folders.append(folder)
                logger.debug(f"Carpeta vacía detectada: {folder.relative_to(contabilidades_root.parent)}")
    except Exception as e:
        logger.exception(f"Error escaneando carpetas en {contabilidades_root}: {e}")
        return []

    return empty_folders


def delete_empty_folders(empty_folders: list[Path]) -> tuple[int, list[str]]:
    """
    Elimina carpetas vacías de forma segura.

    Args:
        empty_folders: Lista de rutas Path de carpetas vacías

    Returns:
        (cantidad_eliminadas, lista_de_errores)
    """
    deleted = 0
    errors: list[str] = []

    # Ordenar por profundidad (más profundas primero) para evitar intentar eliminar padre vacío cuando hijo falla
    sorted_folders = sorted(empty_folders, key=lambda p: (-len(p.parts), str(p)))

    for folder in sorted_folders:
        try:
            if folder.exists() and not any(folder.iterdir()):
                # Doble verificación: está seguro que está vacía
                folder.rmdir()
                deleted += 1
                logger.info(f"Carpeta eliminada: {folder}")
            elif folder.exists():
                # Cambió entre la detección y ahora (contenido agregado)
                errors.append(f"Contiene archivos ahora: {folder.name}")
        except Exception as e:
            errors.append(f"Error eliminando {folder.name}: {str(e)}")
            logger.exception(f"Error eliminando carpeta {folder}: {e}")

    return deleted, errors
