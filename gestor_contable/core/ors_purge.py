"""Purga de registros ORS (terceros) del cliente.

Mueve XMLs y PDFs de facturas ORS a cuarentena organizada por lote,
con registro auditable en ors_purge.sqlite.

No importa customtkinter ni ningun modulo GUI.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from gestor_contable.core.classification_utils import (
    _is_tiquete_electronico,
    classify_transaction,
)
from gestor_contable.core.classifier import safe_move_file
from gestor_contable.core.models import FacturaRecord


class OrsPurgeDB:
    """Registro auditado de purgas ORS en ors_purge.sqlite."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS batches (
        batch_id   TEXT PRIMARY KEY,
        cedula     TEXT NOT NULL,
        fecha      TEXT NOT NULL,
        total_claves   INTEGER NOT NULL DEFAULT 0,
        total_archivos INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS archivos (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id         TEXT NOT NULL,
        clave            TEXT NOT NULL,
        tipo_archivo     TEXT NOT NULL,
        ruta_original    TEXT NOT NULL,
        ruta_cuarentena  TEXT,
        resultado        TEXT NOT NULL,
        detalle          TEXT,
        FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
    );
    CREATE INDEX IF NOT EXISTS idx_archivos_clave ON archivos (clave);
    CREATE INDEX IF NOT EXISTS idx_archivos_batch  ON archivos (batch_id);
    """

    def __init__(self, metadata_dir: Path, db_filename: str = "ors_purge.sqlite"):
        self._metadata_dir = metadata_dir
        self._db_filename = db_filename
        self._path = metadata_dir / db_filename
        self._lock = threading.Lock()
        self._init_db()

    @property
    def metadata_dir(self) -> Path:
        return self._metadata_dir

    @property
    def client_folder(self) -> Path:
        return self._metadata_dir.parent

    @property
    def quarantine_root(self) -> Path:
        if self._db_filename == "receptor_purge.sqlite":
            return self._metadata_dir / "cuarentena_receptor"
        return self.client_folder / ".ors_quarantine"

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self._path)
            try:
                conn.executescript(self._DDL)
                conn.commit()
            finally:
                conn.close()

    def record_batch(
        self,
        batch_id: str,
        cedula: str,
        total_claves: int,
        total_archivos: int,
    ) -> None:
        with self._lock:
            conn = sqlite3.connect(self._path)
            try:
                conn.execute(
                    "INSERT INTO batches (batch_id, cedula, fecha, total_claves, total_archivos) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        batch_id,
                        cedula,
                        datetime.now().isoformat(timespec="seconds"),
                        total_claves,
                        total_archivos,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def record_archivo(
        self,
        batch_id: str,
        clave: str,
        tipo_archivo: str,
        ruta_original: str,
        ruta_cuarentena: str | None,
        resultado: str,
        detalle: str | None = None,
    ) -> None:
        with self._lock:
            conn = sqlite3.connect(self._path)
            try:
                conn.execute(
                    "INSERT INTO archivos "
                    "(batch_id, clave, tipo_archivo, ruta_original, ruta_cuarentena, resultado, detalle) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (batch_id, clave, tipo_archivo, ruta_original, ruta_cuarentena, resultado, detalle),
                )
                conn.commit()
            finally:
                conn.close()

    def get_batches(self) -> list[dict]:
        with self._lock:
            conn = sqlite3.connect(self._path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM batches ORDER BY fecha DESC"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = sqlite3.connect(self._path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM batches WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def get_archivos_for_batch(self, batch_id: str) -> list[dict]:
        with self._lock:
            conn = sqlite3.connect(self._path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM archivos WHERE batch_id = ? ORDER BY clave, tipo_archivo",
                    (batch_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_archivos_for_clave(self, clave: str) -> list[dict]:
        with self._lock:
            conn = sqlite3.connect(self._path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT a.*, b.fecha, b.cedula "
                    "FROM archivos a JOIN batches b ON a.batch_id = b.batch_id "
                    "WHERE a.clave = ? ORDER BY b.fecha DESC",
                    (clave,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def update_archivo_result(
        self,
        archivo_id: int,
        resultado: str,
        detalle: str | None = None,
    ) -> None:
        with self._lock:
            conn = sqlite3.connect(self._path)
            try:
                if detalle is None:
                    conn.execute(
                        "UPDATE archivos SET resultado = ? WHERE id = ?",
                        (resultado, archivo_id),
                    )
                else:
                    conn.execute(
                        "UPDATE archivos SET resultado = ?, detalle = ? WHERE id = ?",
                        (resultado, detalle, archivo_id),
                    )
                conn.commit()
            finally:
                conn.close()

    def adjust_batch_total_archivos(self, batch_id: str, delta: int) -> None:
        with self._lock:
            conn = sqlite3.connect(self._path)
            try:
                conn.execute(
                    "UPDATE batches SET total_archivos = total_archivos + ? WHERE batch_id = ?",
                    (delta, batch_id),
                )
                conn.commit()
            finally:
                conn.close()


# ── Logica de seleccion ───────────────────────────────────────────────────────

def find_ors_candidates(
    ors_records: list[FacturaRecord],
    cedula_tercero: str,
) -> list[FacturaRecord]:
    """Filtra registros ORS por cedula del tercero a quitar.

    Recibe UNICAMENTE registros que ya estan en la pestana ORS
    (filtrados por la app con la cedula de sesion correcta).
    Selecciona los que explicitamente pertenecen al tercero indicado:
    emisor_cedula o receptor_cedula coincide con cedula_tercero.

    Nunca reclasifica nada. Nunca toca registros fuera del ORS tab.

    Criterios adicionales:
    - No es Tiquete Electronico
    - Clave valida: exactamente 50 digitos numericos
    - Unico por clave
    """
    import re as _re

    cedula_t = _re.sub(r"\D", "", (cedula_tercero or "").strip())
    if not cedula_t:
        return []

    seen: set[str] = set()
    candidates: list[FacturaRecord] = []
    for r in ors_records:
        clave = (r.clave or "").strip()
        if not clave or len(clave) != 50 or not clave.isdigit():
            continue
        if clave in seen:
            continue
        if _is_tiquete_electronico(r):
            continue
        emisor = _re.sub(r"\D", "", (r.emisor_cedula or "").strip())
        receptor = _re.sub(r"\D", "", (r.receptor_cedula or "").strip())
        if emisor != cedula_t and receptor != cedula_t:
            continue
        seen.add(clave)
        candidates.append(r)
    return candidates


def _empty_inventory_bucket() -> dict[str, list[Path]]:
    return {"xml": [], "pdf": [], "response_xml": []}


def build_file_inventory(
    all_records: list[FacturaRecord],
    db_records: dict[str, dict],
    hidden_response_files_by_clave: dict[str, list[dict]] | None = None,
) -> dict[str, dict[str, list[Path]]]:
    """Construye inventario de archivos por clave en una sola pasada.

    Por cada clave incluye:
    - record.xml_path   — XML en carpeta origen
    - record.pdf_path   — PDF en carpeta origen
    - db_records[clave]["ruta_destino"] — PDF ya clasificado en Contabilidades

    - hidden_response_files_by_clave[clave] — MensajeHacienda/MensajeReceptor ocultos

    Returns: {clave: {"xml": [Path, ...], "pdf": [Path, ...], "response_xml": [Path, ...]}}
    """
    inventory: dict[str, dict[str, list[Path]]] = {}

    for r in all_records:
        clave = (r.clave or "").strip()
        if not clave:
            continue
        entry = inventory.setdefault(clave, _empty_inventory_bucket())

        if r.xml_path:
            p = Path(r.xml_path)
            if p.exists() and p not in entry["xml"]:
                entry["xml"].append(p)

        if r.pdf_path:
            p = Path(r.pdf_path)
            if p.exists() and p not in entry["pdf"]:
                entry["pdf"].append(p)

    for clave, rec in db_records.items():
        ruta_destino = rec.get("ruta_destino")
        if ruta_destino:
            p = Path(ruta_destino)
            if p.exists():
                entry = inventory.setdefault(clave, _empty_inventory_bucket())
                if p not in entry["pdf"]:
                    entry["pdf"].append(p)

    for clave, hidden_files in (hidden_response_files_by_clave or {}).items():
        entry = inventory.setdefault(clave, _empty_inventory_bucket())
        for hidden in hidden_files:
            ruta = str(hidden.get("ruta", "") or "").strip()
            if not ruta:
                continue
            p = Path(ruta)
            if p.exists() and p not in entry["response_xml"]:
                entry["response_xml"].append(p)

    return inventory


def _resolve_quarantine_destination(src: Path, quarantine_dir: Path) -> Path:
    dest = quarantine_dir / src.name
    if dest.exists():
        candidate = quarantine_dir / f"{src.stem}_{src.parent.name}{src.suffix}"
        if not candidate.exists():
            return candidate
        return quarantine_dir / f"{src.stem}_{uuid.uuid4().hex[:6]}{src.suffix}"
    return dest


def _hidden_message_tipo_archivo(documento_root: str) -> str:
    root_norm = str(documento_root or "").strip().lower()
    if root_norm == "mensajehacienda":
        return "response_xml_mensajehacienda"
    if root_norm == "mensajereceptor":
        return "response_xml_mensajereceptor"
    return "response_xml"


def resolve_active_batch_for_clave(
    purge_db: OrsPurgeDB,
    clave: str,
) -> dict[str, Any] | None:
    """Retorna el lote ORS activo para una clave si existe uno solo."""
    clave_norm = str(clave or "").strip()
    if len(clave_norm) != 50 or not clave_norm.isdigit():
        return None

    rows = purge_db.get_archivos_for_clave(clave_norm)
    if not rows:
        return None

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        batch_id = str(row.get("batch_id", "") or "").strip()
        if not batch_id:
            continue
        info = grouped.setdefault(
            batch_id,
            {
                "batch_id": batch_id,
                "fecha": str(row.get("fecha", "") or ""),
                "cedula": str(row.get("cedula", "") or ""),
                "active_paths": [],
            },
        )
        if row.get("resultado") != "en_cuarentena":
            continue
        ruta_cuarentena = str(row.get("ruta_cuarentena", "") or "").strip()
        if ruta_cuarentena and Path(ruta_cuarentena).exists():
            info["active_paths"].append(ruta_cuarentena)

    active_batches = [info for info in grouped.values() if info["active_paths"]]
    if not active_batches:
        return None

    active_batches.sort(key=lambda item: item.get("fecha", ""), reverse=True)
    if len(active_batches) > 1:
        return {
            "status": "ambiguous",
            "batch_ids": [info["batch_id"] for info in active_batches],
        }

    active = active_batches[0]
    return {
        "status": "active",
        "batch_id": active["batch_id"],
        "batch_ids": [active["batch_id"]],
        "fecha": active["fecha"],
        "cedula": active["cedula"],
        "batch_dir": str(purge_db.quarantine_root / active["batch_id"]),
    }


def refresh_batch_manifest(purge_db: OrsPurgeDB, batch_id: str) -> None:
    """Regenera manifest.json desde la BD para mantener auditoria coherente."""
    batch = purge_db.get_batch(batch_id)
    if not batch:
        return

    archivos = purge_db.get_archivos_for_batch(batch_id)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for archivo in archivos:
        clave = str(archivo.get("clave", "") or "").strip()
        grouped.setdefault(clave, []).append(archivo)

    claves_info: list[dict[str, Any]] = []
    for clave in sorted(grouped):
        rows = grouped[clave]
        has_active = any(
            row.get("resultado") == "en_cuarentena"
            and str(row.get("ruta_cuarentena", "") or "").strip()
            and Path(str(row.get("ruta_cuarentena", "") or "")).exists()
            for row in rows
        )
        resultados = {str(row.get("resultado", "") or "").strip() for row in rows}
        if has_active and "fallido" in resultados:
            estado = "parcial"
        elif has_active:
            estado = "en_cuarentena"
        elif resultados == {"restaurado"}:
            estado = "restaurado"
        elif resultados == {"fallido"}:
            estado = "fallido"
        elif "restaurado" in resultados and "fallido" in resultados:
            estado = "parcial"
        else:
            estado = "sin_estado"

        claves_info.append(
            {
                "clave": clave,
                "estado": estado,
                "archivos": [
                    {
                        "tipo_archivo": str(row.get("tipo_archivo", "") or ""),
                        "ruta_original": str(row.get("ruta_original", "") or ""),
                        "ruta_cuarentena": str(row.get("ruta_cuarentena", "") or ""),
                        "resultado": str(row.get("resultado", "") or ""),
                        "detalle": str(row.get("detalle", "") or ""),
                    }
                    for row in rows
                ],
            }
        )

    manifest = {
        "batch_id": batch_id,
        "cedula": str(batch.get("cedula", "") or ""),
        "fecha": str(batch.get("fecha", "") or ""),
        "total_claves": int(batch.get("total_claves", 0) or 0),
        "total_archivos": int(batch.get("total_archivos", 0) or 0),
        "claves": claves_info,
    }
    try:
        batch_dir = purge_db.quarantine_root / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        (batch_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def attach_hidden_response_to_batch(
    purge_db: OrsPurgeDB,
    batch_id: str,
    clave: str,
    source_path: Path,
    documento_root: str,
) -> dict[str, str]:
    """Anexa una respuesta huérfana al lote ORS activo correspondiente."""
    tipo_archivo = _hidden_message_tipo_archivo(documento_root)
    quarantine_clave_dir = purge_db.quarantine_root / batch_id / clave
    quarantine_clave_dir.mkdir(parents=True, exist_ok=True)

    if not source_path.exists():
        detalle = "Archivo no encontrado"
        purge_db.record_archivo(
            batch_id=batch_id,
            clave=clave,
            tipo_archivo=tipo_archivo,
            ruta_original=str(source_path),
            ruta_cuarentena=None,
            resultado="fallido",
            detalle=detalle,
        )
        refresh_batch_manifest(purge_db, batch_id)
        raise FileNotFoundError(detalle)

    dest = _resolve_quarantine_destination(source_path, quarantine_clave_dir)
    try:
        safe_move_file(source_path, dest)
        purge_db.record_archivo(
            batch_id=batch_id,
            clave=clave,
            tipo_archivo=tipo_archivo,
            ruta_original=str(source_path),
            ruta_cuarentena=str(dest),
            resultado="en_cuarentena",
        )
        purge_db.adjust_batch_total_archivos(batch_id, 1)
        refresh_batch_manifest(purge_db, batch_id)
        return {
            "tipo_archivo": tipo_archivo,
            "ruta_cuarentena": str(dest),
        }
    except Exception as exc:
        purge_db.record_archivo(
            batch_id=batch_id,
            clave=clave,
            tipo_archivo=tipo_archivo,
            ruta_original=str(source_path),
            ruta_cuarentena=None,
            resultado="fallido",
            detalle=str(exc),
        )
        refresh_batch_manifest(purge_db, batch_id)
        raise


# ── Ejecucion de cuarentena ───────────────────────────────────────────────────

def _quarantine_clave(
    clave: str,
    files: dict[str, list[Path]],
    quarantine_clave_dir: Path,
    batch_id: str,
    purge_db: OrsPurgeDB,
) -> dict:
    """Mueve todos los archivos de una clave a su directorio de cuarentena.

    Returns: {"clave": str, "movidos": [Path, ...], "fallidos": [(Path, str), ...]}
    """
    movidos: list[Path] = []
    fallidos: list[tuple[Path, str]] = []

    quarantine_clave_dir.mkdir(parents=True, exist_ok=True)

    for tipo, paths in files.items():
        for src in paths:
            dest = _resolve_quarantine_destination(src, quarantine_clave_dir)

            try:
                safe_move_file(src, dest)
                movidos.append(src)
                purge_db.record_archivo(
                    batch_id=batch_id,
                    clave=clave,
                    tipo_archivo=tipo,
                    ruta_original=str(src),
                    ruta_cuarentena=str(dest),
                    resultado="en_cuarentena",
                )
            except Exception as exc:
                fallidos.append((src, str(exc)))
                purge_db.record_archivo(
                    batch_id=batch_id,
                    clave=clave,
                    tipo_archivo=tipo,
                    ruta_original=str(src),
                    ruta_cuarentena=None,
                    resultado="fallido",
                    detalle=str(exc),
                )

    return {"clave": clave, "movidos": movidos, "fallidos": fallidos}


def restore_batch(
    batch_id: str,
    purge_db: OrsPurgeDB,
) -> dict:
    """Restaura todos los archivos de un lote de cuarentena a sus rutas originales.

    Lee la tabla `archivos` del lote, mueve cada archivo desde ruta_cuarentena
    de vuelta a ruta_original. Solo procesa archivos con resultado='en_cuarentena'.

    Returns:
        {
            "restaurados": list[str],          # rutas originales restauradas
            "fallidos": list[tuple[str, str]], # (ruta_cuarentena, error)
        }
    """
    archivos = purge_db.get_archivos_for_batch(batch_id)
    restaurados: list[str] = []
    fallidos: list[tuple[str, str]] = []

    for a in archivos:
        if a["resultado"] != "en_cuarentena":
            continue
        src_str = a.get("ruta_cuarentena")
        dest_str = a.get("ruta_original")
        if not src_str or not dest_str:
            fallidos.append((src_str or "", "Ruta de cuarentena o ruta original vacia"))
            continue
        src = Path(src_str)
        dest = Path(dest_str)
        if not src.exists():
            fallidos.append((src_str, "Archivo no encontrado en cuarentena"))
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            safe_move_file(src, dest)
            restaurados.append(dest_str)
            purge_db.update_archivo_result(int(a["id"]), "restaurado")
        except Exception as exc:
            fallidos.append((src_str, str(exc)))

    refresh_batch_manifest(purge_db, batch_id)
    return {"restaurados": restaurados, "fallidos": fallidos}


def execute_purge(
    candidates: list[FacturaRecord],
    file_inventory: dict[str, dict[str, list[Path]]],
    client_folder: Path,
    cedula: str,
    purge_db: OrsPurgeDB,
) -> dict:
    """Ejecuta la cuarentena de todas las claves candidatas.

    Diseñado para correr en worker thread. No toca la UI.

    Returns:
        {
            "batch_id": str,
            "results": list[dict],
            "total_movidos": int,
            "total_fallidos": int,
            "claves_ok": int,
            "claves_parcial": int,
            "claves_fallidas": int,
        }
    """
    batch_id = (
        datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:6]
    )
    batch_dir = client_folder / ".ors_quarantine" / batch_id

    total_archivos = sum(
        len(file_inventory.get(r.clave, {}).get("xml", []))
        + len(file_inventory.get(r.clave, {}).get("pdf", []))
        + len(file_inventory.get(r.clave, {}).get("response_xml", []))
        for r in candidates
    )

    purge_db.record_batch(
        batch_id=batch_id,
        cedula=cedula,
        total_claves=len(candidates),
        total_archivos=total_archivos,
    )

    results: list[dict] = []
    total_movidos = 0
    total_fallidos = 0
    claves_ok = 0
    claves_parcial = 0
    claves_fallidas = 0

    for record in candidates:
        clave = record.clave
        files = file_inventory.get(clave, _empty_inventory_bucket())
        quarantine_clave_dir = batch_dir / clave

        result = _quarantine_clave(
            clave=clave,
            files=files,
            quarantine_clave_dir=quarantine_clave_dir,
            batch_id=batch_id,
            purge_db=purge_db,
        )
        results.append(result)

        total_movidos += len(result["movidos"])
        total_fallidos += len(result["fallidos"])

        if result["fallidos"] and not result["movidos"]:
            claves_fallidas += 1
        elif result["fallidos"]:
            claves_parcial += 1
        else:
            claves_ok += 1

    refresh_batch_manifest(purge_db, batch_id)

    return {
        "batch_id": batch_id,
        "results": results,
        "total_movidos": total_movidos,
        "total_fallidos": total_fallidos,
        "claves_ok": claves_ok,
        "claves_parcial": claves_parcial,
        "claves_fallidas": claves_fallidas,
    }
