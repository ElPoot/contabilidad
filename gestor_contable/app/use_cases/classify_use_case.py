"""Use case para clasificacion de facturas.

Centraliza las llamadas a classify_record() que antes vivian
directamente en gui/main_window.py (individual, lote, auto).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from gestor_contable.core.classifier import ClassificationDB, classify_record
from gestor_contable.core.models import FacturaRecord


@dataclass
class ClassifyParams:
    categoria: str
    subtipo: str = ""
    nombre_cuenta: str = ""
    proveedor: str = ""


@dataclass
class ClassifyResult:
    total: int = 0
    exitosos: int = 0
    errores: list[tuple[FacturaRecord, str]] = field(default_factory=list)


def classify_single(
    record: FacturaRecord,
    session_folder: Path,
    db: ClassificationDB,
    params: ClassifyParams,
    client_name_override: str | None = None,
) -> Path | None:
    """Clasifica un solo registro. Lanza RuntimeError si falla."""
    return classify_record(
        record, session_folder, db,
        params.categoria, params.subtipo, params.nombre_cuenta, params.proveedor,
        client_name_override=client_name_override,
    )


def classify_batch(
    records: list[FacturaRecord],
    session_folder: Path,
    db: ClassificationDB,
    params: ClassifyParams,
    get_client_override: Callable[[FacturaRecord], str | None],
    on_progress: Callable[[int, int], None] | None = None,
) -> ClassifyResult:
    """Clasifica un lote de registros.

    Args:
        on_progress: callback(current, total) invocado cada 100 registros.
        get_client_override: recibe un record y retorna el nombre de carpeta
            si el contador la renombro, o None.
    """
    errores: list[tuple[FacturaRecord, str]] = []
    n = len(records)
    for i, record in enumerate(records):
        if on_progress and i % 100 == 0:
            on_progress(i + 1, n)
        try:
            override = get_client_override(record)
            classify_record(
                record, session_folder, db,
                params.categoria, params.subtipo, params.nombre_cuenta, params.proveedor,
                client_name_override=override,
            )
        except Exception as exc:
            errores.append((record, str(exc)))

    return ClassifyResult(
        total=n,
        exitosos=n - len(errores),
        errores=errores,
    )
