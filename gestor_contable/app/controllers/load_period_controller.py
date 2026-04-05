"""Controller de carga de periodo.

Extrae la orquestación de carga de XMLs, PDFs y huérfanos fuera de la vista.
No importa customtkinter ni ningún componente visual.

Funciones exportadas:
    months_for_range()           -- calcula (year, month) que cubre un rango
    filter_sinxml_by_clave_date() -- filtra registros sin_xml fuera de rango
    load_session_worker()         -- carga completa al abrir/cambiar cliente
    load_range_worker()           -- carga incremental de meses faltantes
"""
from __future__ import annotations

import calendar
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from gestor_contable.config import metadata_dir
from gestor_contable.core.catalog import CatalogManager
from gestor_contable.core.classifier import ClassificationDB
from gestor_contable.core.factura_index import FacturaIndexer
from gestor_contable.core.models import FacturaRecord
from gestor_contable.core.session import ClientSession

logger = logging.getLogger(__name__)

# Firma del callback de progreso: (mensaje, progreso_actual, total)
ProgressCallback = Callable[[str, int, int], None]


def _merge_hidden_response_maps(*maps: dict[str, list[dict]] | None) -> dict[str, list[dict]]:
    """Fusiona mapas clave->respuestas ocultas preservando orden y sin duplicados."""
    merged: dict[str, list[dict]] = {}
    seen_by_clave: dict[str, set[str]] = {}

    for source in maps:
        for clave, entries in (source or {}).items():
            clave_norm = str(clave or "").strip()
            if not clave_norm:
                continue

            bucket = merged.setdefault(clave_norm, [])
            seen = seen_by_clave.setdefault(clave_norm, set())

            for entry in entries or []:
                item = dict(entry or {})
                ruta = str(item.get("ruta", "") or "").strip()
                archivo = str(item.get("archivo", "") or "").strip()
                documento_root = str(item.get("documento_root", "") or "").strip()
                dedupe_key = ruta or f"{archivo}|{documento_root}|{clave_norm}"
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                bucket.append(item)

    return merged


# ── Resultados tipados ────────────────────────────────────────────────────────

@dataclass
class SessionLoadResult:
    """Resultado de load_session_worker — todo lo que _poll_load necesita."""
    catalog: CatalogManager
    db: ClassificationDB
    records: list[FacturaRecord]
    parse_errors: list[str]
    failed_xml_files: list
    renames: list[dict]
    pdf_duplicates_rejected: dict
    load_months: set[tuple[int, int]]
    receptor_response_files: list  # [{archivo, ruta, clave_numerica}, ...]
    hidden_response_files_by_clave: dict[str, list[dict]]
    ors_autopurge_summary: dict


@dataclass
class RangeLoadResult:
    """Resultado de load_range_worker — registros nuevos y meses cargados."""
    new_records: list[FacturaRecord]
    loaded_months: set[tuple[int, int]]
    hidden_response_files_by_clave: dict[str, list[dict]]
    ors_autopurge_summary: dict


# ── Helpers de rango ─────────────────────────────────────────────────────────

def months_for_range(from_str: str, to_str: str) -> set[tuple[int, int]]:
    """Retorna el conjunto de (year, month) que cubre el rango de fechas dado."""
    def _parse(s: str):
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(s.strip(), fmt).date()
            except (ValueError, AttributeError):
                continue
        return None

    from_dt = _parse(from_str)
    to_dt = _parse(to_str)
    if not from_dt or not to_dt:
        return set()
    months: set[tuple[int, int]] = set()
    cursor = from_dt.replace(day=1)
    while cursor <= to_dt:
        months.add((cursor.year, cursor.month))
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)
    return months


def filter_sinxml_by_clave_date(
    records: list,
    from_str: str,
    to_str: str,
) -> list:
    """Excluye registros sin_xml cuya fecha de clave está fuera del rango cargado.

    Cuando se carga con rango de fechas, los PDFs de otros meses no encuentran
    su XML y se marcan sin_xml aunque su XML exista (solo no fue cargado).
    La clave tiene la fecha en las posiciones 3:9 (DDMMYY), lo que permite
    determinar a qué mes pertenece. Si la fecha cae dentro del rango, el
    sin_xml es genuino y se conserva.
    """
    def _parse(s: str):
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime((s or "").strip(), fmt).date()
            except (ValueError, AttributeError):
                continue
        return None

    from_dt = _parse(from_str)
    to_dt = _parse(to_str)
    if not from_dt and not to_dt:
        return records

    result = []
    for r in records:
        if r.estado != "sin_xml":
            result.append(r)
            continue
        clave = r.clave or ""
        if len(clave) < 9:
            result.append(r)  # Clave inválida: conservar para revisión manual
            continue
        try:
            clave_day   = int(clave[3:5])
            clave_month = int(clave[5:7])
            clave_year  = 2000 + int(clave[7:9])
            clave_date  = date(clave_year, clave_month, clave_day)
        except (ValueError, IndexError):
            result.append(r)  # No se pudo parsear: conservar
            continue
        if from_dt and clave_date < from_dt:
            continue
        if to_dt and clave_date > to_dt:
            continue
        result.append(r)
    return result


# ── Workers de carga (UI-free) ────────────────────────────────────────────────

def load_session_worker(
    session: ClientSession,
    from_date: str,
    to_date: str,
    load_months: set[tuple[int, int]],
    progress_callback: ProgressCallback | None = None,
) -> SessionLoadResult:
    """Orquestación de carga de sesión completa: XMLs, PDFs, huérfanos, catálogo.

    Se ejecuta en un worker thread. No toca UI directamente.
    Usa progress_callback(mensaje, actual, total) para reportar avance.
    """
    def _cb(msg: str, current: int = 0, total: int = 100):
        if progress_callback:
            progress_callback(msg, current, total)

    start_total = time.perf_counter()

    mdir = metadata_dir(session.folder)
    _cb("Preparando cliente...", 10, 100)
    catalog = CatalogManager(mdir).load()
    db = ClassificationDB(mdir)
    indexer = FacturaIndexer()

    _cb("Leyendo XMLs...", 20, 100)
    _cb("Escaneando PDFs (esto toma ~40s)...", 30, 100)

    start_load = time.perf_counter()
    records = indexer.load_period(
        session.folder,
        from_date=from_date,
        to_date=to_date,
        include_pdf_scan=True,
        allow_pdf_content_fallback=True,
    )
    load_time = time.perf_counter() - start_load
    logger.info("load_period() tardó %.2fs para %d registros", load_time, len(records))
    _cb(f"XMLs y PDFs ({load_time:.1f}s). Buscando huerfanos...", 70, 100)

    if from_date or to_date:
        records = filter_sinxml_by_clave_date(records, from_date, to_date)

    from gestor_contable.core.classification_utils import (
        create_orphaned_record,
        find_orphaned_pdfs,
        find_renamed_client_folders,
    )
    pf_root = session.folder.parent.parent
    contabilidades_root = pf_root / "Contabilidades"
    local_db_records = db.get_records_map()
    client_name = session.folder.name

    start_orphan = time.perf_counter()
    renames = find_renamed_client_folders(contabilidades_root, client_name, local_db_records)
    orphaned_list = find_orphaned_pdfs(contabilidades_root, local_db_records, client_name)
    orphan_time = time.perf_counter() - start_orphan
    logger.info("Huerfanos tardó %.2fs, encontrados: %d", orphan_time, len(orphaned_list))
    _cb(f"Huerfanos ({orphan_time:.1f}s). Finalizando...", 90, 100)

    for orphaned_info in orphaned_list:
        records.append(create_orphaned_record(orphaned_info))

    if orphaned_list:
        logger.info("Agregados %d registros huerfanos", len(orphaned_list))

    total_time = time.perf_counter() - start_total
    logger.info("load_session_worker total: %.2fs", total_time)

    return SessionLoadResult(
        catalog=catalog,
        db=db,
        records=records,
        parse_errors=indexer.parse_errors,
        failed_xml_files=indexer.failed_xml_files,
        renames=renames,
        pdf_duplicates_rejected=indexer.pdf_duplicates_rejected,
        load_months=load_months,
        receptor_response_files=indexer.receptor_response_files,
        hidden_response_files_by_clave=indexer.hidden_message_files_by_clave,
        ors_autopurge_summary=indexer.ors_autopurge_summary,
    )


def load_range_worker(
    session: ClientSession,
    missing_months: set[tuple[int, int]],
    progress_callback: ProgressCallback | None = None,
) -> RangeLoadResult:
    """Orquestación de carga incremental: meses faltantes del rango activo.

    Se ejecuta en un worker thread. No toca UI directamente.
    """
    def _cb(msg: str, current: int = 0, total: int = 0):
        if progress_callback:
            progress_callback(msg, current, total)

    sorted_months = sorted(missing_months)
    n = len(sorted_months)
    all_new: list[FacturaRecord] = []
    aggregated_hidden_response_files_by_clave: dict[str, list[dict]] = {}
    aggregated_ors_autopurge_summary: dict[str, list] = {
        "moved_files": [],
        "batch_ids": [],
    }

    for i, (year, month) in enumerate(sorted_months):
        first_day = date(year, month, 1)
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        from_s = first_day.strftime("%d/%m/%Y")
        to_s = last_day.strftime("%d/%m/%Y")
        _cb(f"Cargando mes {i + 1}/{n}...", i, n)
        indexer = FacturaIndexer()
        batch = indexer.load_period(
            session.folder,
            from_date=from_s,
            to_date=to_s,
            include_pdf_scan=True,
            allow_pdf_content_fallback=True,
        )
        all_new.extend(filter_sinxml_by_clave_date(batch, from_s, to_s))
        aggregated_hidden_response_files_by_clave = _merge_hidden_response_maps(
            aggregated_hidden_response_files_by_clave,
            indexer.hidden_message_files_by_clave,
        )
        aggregated_ors_autopurge_summary["moved_files"].extend(
            indexer.ors_autopurge_summary.get("moved_files", [])
        )
        for batch_id in indexer.ors_autopurge_summary.get("batch_ids", []):
            if batch_id not in aggregated_ors_autopurge_summary["batch_ids"]:
                aggregated_ors_autopurge_summary["batch_ids"].append(batch_id)

    return RangeLoadResult(
        new_records=all_new,
        loaded_months=set(sorted_months),
        hidden_response_files_by_clave=aggregated_hidden_response_files_by_clave,
        ors_autopurge_summary=aggregated_ors_autopurge_summary,
    )
