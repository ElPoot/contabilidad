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


def _try_make_writable(folder: Path) -> bool:
    """
    Intenta cambiar permisos de la carpeta a escribible.

    Args:
        folder: Ruta de la carpeta

    Returns:
        True si se logró cambiar permisos, False si no
    """
    try:
        import stat
        # Cambiar permisos a 777 (lectura, escritura, ejecución)
        os.chmod(folder, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        logger.debug(f"Permisos cambiados para: {folder}")
        return True
    except Exception as e:
        logger.debug(f"No se pudieron cambiar permisos de {folder}: {e}")
        return False


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
                errors.append(f"Permiso denegado para leer: {folder.name}")
                logger.warning(f"Permiso denegado en {folder}: {pe}")
                continue

            if not items:
                # Doble verificación: está seguro que está vacía
                try:
                    folder.rmdir()
                    deleted += 1
                    logger.info(f"Carpeta eliminada: {folder}")
                except PermissionError:
                    # Intento 1: cambiar permisos
                    logger.debug(f"Intentando cambiar permisos para: {folder}")
                    if _try_make_writable(folder):
                        try:
                            folder.rmdir()
                            deleted += 1
                            logger.info(f"Carpeta eliminada (después de cambiar permisos): {folder}")
                        except Exception as e2:
                            errors.append(f"Permisos insuficientes: {folder.name} (necesitas derechos administrativos)")
                            logger.warning(f"Fallo incluso después de cambiar permisos: {e2}")
                    else:
                        errors.append(f"Permisos insuficientes: {folder.name} (necesitas derechos administrativos)")
                except OSError as ose:
                    if ose.winerror == 5:  # WinError 5: Access Denied
                        errors.append(f"Acceso denegado: {folder.name} (proceso puede estar usando esta carpeta)")
                    else:
                        errors.append(f"Error del sistema: {folder.name} ({ose.strerror})")
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


def find_residual_contabilidades_folders(
    contabilidades_root: Path,
    session_client_name: str,
    db_records: dict,
) -> list[dict]:
    """
    Detecta carpetas residuales del nombre anterior del cliente en Contabilidades.

    Una carpeta es residual si:
    - Existe Contabilidades/{mes}/{session_client_name}/
    - TAMBIEN existe una carpeta hermana que empieza con session_client_name (el nuevo nombre)
    - La BD no tiene registros apuntando al nombre original en ese mes

    Returns:
        Lista de dicts:
        {
            "path":      Path,   # raiz de la carpeta residual
            "mes":       str,    # ej: "02-FEBRERO"
            "has_files": bool,   # True = tiene archivos reales (solo reporte); False = solo dirs vacios
        }
    """
    if not contabilidades_root.exists():
        return []

    results = []

    try:
        for mes_dir in contabilidades_root.iterdir():
            if not mes_dir.is_dir():
                continue

            original_dir = mes_dir / session_client_name
            if not original_dir.exists():
                continue

            # Buscar carpeta hermana que empiece con el mismo nombre (la renombrada)
            try:
                renamed_sibling = next(
                    (d for d in mes_dir.iterdir()
                     if d.is_dir()
                     and d.name != session_client_name
                     and d.name.startswith(session_client_name)),
                    None,
                )
            except OSError:
                continue

            if renamed_sibling is None:
                continue  # no hay carpeta renombrada — no es un caso residual

            # Verificar que BD ya no apunta a la carpeta original en este mes
            old_prefix = f"Contabilidades/{mes_dir.name}/{session_client_name}/"
            old_count = sum(
                1 for rec in db_records.values()
                if old_prefix in rec.get("ruta_destino", "").replace("\\", "/")
            )
            if old_count > 0:
                continue  # BD todavia tiene registros aqui — no es residual

            # Distinguir entre "tiene archivos reales" y "solo subdirectorios vacios"
            has_real_files = any(p for p in original_dir.rglob("*") if p.is_file())

            results.append({
                "path": original_dir,
                "mes": mes_dir.name,
                "has_files": has_real_files,
            })
            logger.info(
                f"Carpeta residual detectada: {mes_dir.name}/{session_client_name} "
                f"(has_files={has_real_files}, renombrada a: {renamed_sibling.name})"
            )
    except Exception as e:
        logger.exception(f"Error escaneando carpetas residuales en {contabilidades_root}: {e}")

    return results
