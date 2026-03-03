"""Sanitización segura de carpetas vacías después de reclasificación."""

import gc
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def find_empty_folders(client_folder: Path) -> list[Path]:
    """
    Encuentra carpetas COMPLETAMENTE vacías en Contabilidades/{mes}/{cliente}/.

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

    client_name = client_folder.name
    pf_root = client_folder.parent.parent
    contabilidades_root = pf_root / "Contabilidades"

    if not contabilidades_root.exists():
        logger.debug(f"No hay carpeta Contabilidades en {pf_root}")
        return []

    empty_folders: list[Path] = []

    try:
        # Recorrer TODOS los meses en Contabilidades (ej: 01-ENERO, 02-FEBRERO, etc)
        for mes_folder in contabilidades_root.iterdir():
            if not mes_folder.is_dir():
                continue

            # Carpeta del cliente dentro del mes (ej: Contabilidades/02-FEBRERO/{client_name}/)
            cliente_in_mes = mes_folder / client_name
            if not cliente_in_mes.exists():
                continue

            # Recorrer recursivamente carpetas de clasificación del cliente en este mes
            for folder in sorted(cliente_in_mes.rglob("*"), key=lambda p: (-len(p.parts), p)):
                if not folder.is_dir():
                    continue

                # Verificar si la carpeta está vacía
                has_content = any(folder.iterdir())
                if not has_content:
                    empty_folders.append(folder)
                    try:
                        rel_path = folder.relative_to(contabilidades_root.parent)
                        logger.debug(f"Carpeta vacía detectada: {rel_path}")
                    except ValueError:
                        logger.debug(f"Carpeta vacía detectada: {folder}")
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

    # Liberar referencias en memoria (importante para Windows)
    gc.collect()

    # Ordenar por profundidad (más profundas primero) para evitar intentar eliminar padre vacío cuando hijo falla
    sorted_folders = sorted(empty_folders, key=lambda p: (-len(p.parts), str(p)))

    for folder in sorted_folders:
        try:
            if not folder.exists():
                continue

            # Verificación: listar contenido (incluso archivos ocultos)
            try:
                items = list(folder.iterdir())
            except PermissionError as pe:
                errors.append(f"Permiso denegado: {folder.name} - Cierra Explorador Windows si está abierto")
                logger.warning(f"Permiso denegado en {folder}: {pe}")
                continue

            if not items:
                # Doble verificación: está seguro que está vacía
                try:
                    folder.rmdir()
                    deleted += 1
                    logger.info(f"Carpeta eliminada: {folder}")
                except PermissionError:
                    errors.append(f"No hay permisos para eliminar: {folder.name}")
                except OSError as ose:
                    if ose.winerror == 5:  # WinError 5: Access Denied
                        errors.append(f"Acceso denegado (quizás abierta): {folder.name}")
                    else:
                        errors.append(f"Error del sistema: {folder.name} ({ose})")
            else:
                # Cambió entre la detección y ahora (contenido agregado)
                file_list = ", ".join(str(i.name) for i in items[:3])
                if len(items) > 3:
                    file_list += f", ... (+{len(items) - 3} más)"
                errors.append(f"Contiene archivos: {folder.name} [{file_list}]")
        except Exception as e:
            errors.append(f"Error inesperado en {folder.name}: {str(e)}")
            logger.exception(f"Error eliminando carpeta {folder}: {e}")

    return deleted, errors
