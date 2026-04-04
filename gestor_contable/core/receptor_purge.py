"""Purga de respuestas receptor (MensajeHacienda de confirmacion de MensajeReceptor).

Mueve XMLs identificados como respuesta_receptor a cuarentena auditada en
.metadata/cuarentena_receptor/{batch_id}/.

Reutiliza OrsPurgeDB para el registro SQLite (mismo esquema, distinto archivo).
No importa customtkinter ni ningun modulo GUI.
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from gestor_contable.core.ors_purge import OrsPurgeDB


def execute_receptor_purge(
    receptor_files: list[dict],
    client_folder: Path,
    purge_db: OrsPurgeDB,
) -> dict:
    """Mueve los XML respuesta_receptor a cuarentena auditada.

    Args:
        receptor_files: lista de dicts con claves "archivo", "ruta", "clave_numerica".
                        Proviene de FacturaIndexer.receptor_response_files.
        client_folder:  carpeta raiz del cliente (Z:/DATA/PF-XXXX/CLIENTES/...).
        purge_db:       instancia de OrsPurgeDB apuntando a receptor_purge.sqlite.

    Returns:
        {
            "batch_id": str,
            "total_movidos": int,
            "total_fallidos": int,
            "fallidos": [(ruta_original, error), ...]
        }
    """
    batch_id = (
        datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:6]
    )
    batch_dir = client_folder / ".metadata" / "cuarentena_receptor" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    purge_db.record_batch(
        batch_id=batch_id,
        cedula="receptor_responses",
        total_claves=len(receptor_files),
        total_archivos=len(receptor_files),
    )

    movidos: list[str] = []
    fallidos: list[tuple[str, str]] = []

    for entry in receptor_files:
        ruta_str = str(entry.get("ruta", "") or "").strip()
        clave = str(entry.get("clave_numerica", "") or entry.get("archivo", "")).strip()
        if not ruta_str:
            fallidos.append((ruta_str, "Ruta vacia"))
            continue

        src = Path(ruta_str)
        if not src.exists():
            fallidos.append((ruta_str, "Archivo no encontrado"))
            purge_db.record_archivo(
                batch_id=batch_id,
                clave=clave,
                tipo_archivo="respuesta_receptor",
                ruta_original=ruta_str,
                ruta_cuarentena=None,
                resultado="fallido",
                detalle="Archivo no encontrado",
            )
            continue

        dest = batch_dir / src.name
        if dest.exists():
            dest = batch_dir / f"{src.stem}_{uuid.uuid4().hex[:6]}{src.suffix}"

        try:
            shutil.move(str(src), str(dest))
            movidos.append(ruta_str)
            purge_db.record_archivo(
                batch_id=batch_id,
                clave=clave,
                tipo_archivo="respuesta_receptor",
                ruta_original=ruta_str,
                ruta_cuarentena=str(dest),
                resultado="en_cuarentena",
            )
        except Exception as exc:
            fallidos.append((ruta_str, str(exc)))
            purge_db.record_archivo(
                batch_id=batch_id,
                clave=clave,
                tipo_archivo="respuesta_receptor",
                ruta_original=ruta_str,
                ruta_cuarentena=None,
                resultado="fallido",
                detalle=str(exc),
            )

    _write_manifest(batch_dir, batch_id, movidos, fallidos)

    return {
        "batch_id": batch_id,
        "total_movidos": len(movidos),
        "total_fallidos": len(fallidos),
        "fallidos": fallidos,
    }


def _write_manifest(
    batch_dir: Path,
    batch_id: str,
    movidos: list[str],
    fallidos: list[tuple[str, str]],
) -> None:
    manifest = {
        "batch_id": batch_id,
        "tipo": "respuesta_receptor",
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "total_movidos": len(movidos),
        "total_fallidos": len(fallidos),
        "movidos": movidos,
        "fallidos": [{"ruta": r, "error": e} for r, e in fallidos],
    }
    try:
        (batch_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
