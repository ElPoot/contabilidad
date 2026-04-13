"""Controller para operaciones con PDFs huerfanos.

Encapsula la logica de dominio que antes vivia directamente en
gui/orphaned_pdfs_modal.py: escaneo, adopcion y recuperacion.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from gestor_contable.core.classifier import (
    ClassificationDB,
    adopt_orphaned_pdf,
    recover_orphaned_pdf,
)
from gestor_contable.core.classification_utils import find_orphaned_pdfs

logger = logging.getLogger(__name__)


@dataclass
class RecoveryResult:
    recovered: int = 0
    failed: int = 0
    total: int = 0
    recovered_ids: list[int] = field(default_factory=list)


def scan_orphaned_pdfs(
    session_folder: Path,
    db_records: dict,
) -> list[dict]:
    """Escanea PDFs huerfanos en Contabilidades/ para el cliente activo."""
    pf_root = session_folder.parent.parent
    contabilidades_root = pf_root / "Contabilidades"
    return find_orphaned_pdfs(
        contabilidades_root, db_records,
        client_name=session_folder.name,
    )


def recover_selected(
    orphaned_list: list[dict],
    selected_indices: list[int],
    db: ClassificationDB,
) -> RecoveryResult:
    """Recupera o adopta los PDFs huerfanos en los indices seleccionados."""
    result = RecoveryResult(total=len(selected_indices))

    for idx in selected_indices:
        if idx >= len(orphaned_list):
            result.failed += 1
            continue

        orphaned_info = orphaned_list[idx]
        motivo = orphaned_info.get("motivo", "")

        if motivo in {"not_in_db", "huerfano_sin_destino", "adoptar_en_sitio"}:
            ok = adopt_orphaned_pdf(orphaned_info, db)
        else:
            ok = recover_orphaned_pdf(orphaned_info, db)

        if ok:
            result.recovered += 1
            result.recovered_ids.append(idx)
        else:
            result.failed += 1

    return result
