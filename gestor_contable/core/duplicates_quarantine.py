"""Cuarentena auditada para archivos duplicados/redundantes.

Mueve archivos a .metadata/duplicates_quarantine/<batch_id>/
con registro auditable en duplicates_quarantine.sqlite.
Permite restaurar por lote.

No importa customtkinter ni ningun modulo GUI.
"""
from __future__ import annotations

import contextlib
import logging
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from gestor_contable.core.classifier import safe_move_file

logger = logging.getLogger(__name__)


class DuplicatesQuarantineDB:
    """Registro auditado de cuarentenas de duplicados en duplicates_quarantine.sqlite."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS batches (
        batch_id       TEXT PRIMARY KEY,
        fecha          TEXT NOT NULL,
        total_archivos INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS archivos (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id        TEXT NOT NULL,
        tipo_archivo    TEXT NOT NULL,
        ruta_original   TEXT NOT NULL,
        ruta_cuarentena TEXT,
        resultado       TEXT NOT NULL,
        detalle         TEXT,
        FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
    );
    CREATE INDEX IF NOT EXISTS idx_archivos_batch ON archivos (batch_id);
    """

    def __init__(self, metadata_dir: Path):
        self._path = metadata_dir / "duplicates_quarantine.sqlite"
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self._path)
            try:
                conn.executescript(self._DDL)
                conn.commit()
            finally:
                conn.close()

    def create_batch(self, batch_id: str, total: int) -> None:
        with self._lock, contextlib.closing(sqlite3.connect(self._path)) as conn:
            conn.execute(
                "INSERT INTO batches (batch_id, fecha, total_archivos) VALUES (?, ?, ?)",
                (batch_id, datetime.now().isoformat(), total),
            )
            conn.commit()

    def record_file(
        self,
        batch_id: str,
        tipo: str,
        ruta_original: str,
        ruta_cuarentena: str | None,
        resultado: str,
        detalle: str | None = None,
    ) -> None:
        with self._lock, contextlib.closing(sqlite3.connect(self._path)) as conn:
            conn.execute(
                """INSERT INTO archivos
                   (batch_id, tipo_archivo, ruta_original, ruta_cuarentena, resultado, detalle)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (batch_id, tipo, ruta_original, ruta_cuarentena, resultado, detalle),
            )
            conn.commit()

    def list_batches(self) -> list[dict]:
        with self._lock, contextlib.closing(sqlite3.connect(self._path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM batches ORDER BY fecha DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_batch_files(self, batch_id: str) -> list[dict]:
        with self._lock, contextlib.closing(sqlite3.connect(self._path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM archivos WHERE batch_id = ?",
                (batch_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_batch_records(self, batch_id: str) -> None:
        with self._lock, contextlib.closing(sqlite3.connect(self._path)) as conn:
            conn.execute("DELETE FROM archivos WHERE batch_id = ?", (batch_id,))
            conn.execute("DELETE FROM batches WHERE batch_id = ?", (batch_id,))
            conn.commit()


def execute_duplicates_quarantine(
    files_to_quarantine: list[tuple[str, Path]],
    metadata_dir: Path,
    quarantine_db: DuplicatesQuarantineDB,
) -> dict:
    """Mueve archivos duplicados a cuarentena auditada.

    Args:
        files_to_quarantine: lista de (tipo, ruta_original)
        metadata_dir: carpeta .metadata/ del cliente
        quarantine_db: instancia de DuplicatesQuarantineDB

    Returns:
        dict con batch_id, movidos, fallidos, results
    """
    batch_id = uuid.uuid4().hex[:12].upper()
    quarantine_dir = metadata_dir / "duplicates_quarantine" / batch_id
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    quarantine_db.create_batch(batch_id, len(files_to_quarantine))

    movidos = 0
    fallidos = 0
    results = []

    for tipo, src_path in files_to_quarantine:
        dst_path = quarantine_dir / src_path.name
        if dst_path.exists():
            dst_path = quarantine_dir / f"{src_path.stem}_{movidos}{src_path.suffix}"

        try:
            if not src_path.exists():
                quarantine_db.record_file(batch_id, tipo, str(src_path), None, "skip", "no existe en disco")
                results.append({"tipo": tipo, "ruta": src_path, "ok": False, "detalle": "no existe"})
                continue
            safe_move_file(src_path, dst_path)
            
            try:
                quarantine_db.record_file(batch_id, tipo, str(src_path), str(dst_path), "ok")
            except Exception as db_exc:
                try:
                    safe_move_file(dst_path, src_path)
                except Exception as rollback_exc:
                    logger.error(
                        "Doble fallo crítico: BD falló y rollback físico falló para el archivo %s. DB Err: %s, Rollback Err: %s",
                        src_path, db_exc, rollback_exc
                    )
                raise db_exc

            movidos += 1
            results.append({"tipo": tipo, "ruta": src_path, "ok": True})
        except Exception as e:
            try:
                quarantine_db.record_file(batch_id, tipo, str(src_path), None, "error", str(e))
            except Exception:
                pass
            fallidos += 1
            results.append({"tipo": tipo, "ruta": src_path, "ok": False, "detalle": str(e)})

    return {
        "batch_id": batch_id,
        "movidos": movidos,
        "fallidos": fallidos,
        "results": results,
    }


def restore_duplicates_batch(
    batch_id: str,
    quarantine_db: DuplicatesQuarantineDB,
) -> dict:
    """Restaura todos los archivos de un lote de cuarentena a su ubicacion original."""
    files = quarantine_db.get_batch_files(batch_id)

    restaurados = 0
    fallidos = 0
    results = []

    for f in files:
        if f["resultado"] != "ok":
            continue
        src = Path(f["ruta_cuarentena"])
        dst = Path(f["ruta_original"])

        try:
            if not src.exists():
                results.append({"ruta": dst, "ok": False, "detalle": "ya no esta en cuarentena"})
                fallidos += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            safe_move_file(src, dst)
            restaurados += 1
            results.append({"ruta": dst, "ok": True})
        except Exception as e:
            fallidos += 1
            results.append({"ruta": dst, "ok": False, "detalle": str(e)})

    if restaurados > 0 and fallidos == 0:
        quarantine_db.delete_batch_records(batch_id)

    return {
        "batch_id": batch_id,
        "restaurados": restaurados,
        "fallidos": fallidos,
        "results": results,
    }
