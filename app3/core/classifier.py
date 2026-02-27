from __future__ import annotations

import hashlib
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .models import FacturaRecord


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ClassificationDB:
    def __init__(self, metadata_dir: Path) -> None:
        self.path = metadata_dir / "clasificacion.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure()

    def _ensure(self) -> None:
        with self._lock, sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clasificaciones (
                  clave_numerica       TEXT PRIMARY KEY,
                  estado               TEXT,
                  categoria            TEXT,
                  subcategoria         TEXT,
                  proveedor            TEXT,
                  ruta_origen          TEXT,
                  ruta_destino         TEXT,
                  sha256               TEXT,
                  fecha_clasificacion  TEXT,
                  clasificado_por      TEXT
                )
                """
            )

    def get_estado(self, clave: str) -> str | None:
        with self._lock, sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT estado FROM clasificaciones WHERE clave_numerica=?", (clave,)
            ).fetchone()
            return row[0] if row else None

    def get_record(self, clave: str) -> dict | None:
        """Retorna el registro completo de clasificacion o None si no existe."""
        with self._lock, sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM clasificaciones WHERE clave_numerica=?", (clave,)
            ).fetchone()
            if not row:
                return None
            cols = [
                "clave_numerica", "estado", "categoria", "subcategoria", "proveedor",
                "ruta_origen", "ruta_destino", "sha256", "fecha_clasificacion", "clasificado_por",
            ]
            return dict(zip(cols, row))

    def get_records_map(self) -> dict[str, dict]:
        """Retorna todas las clasificaciones en memoria para evitar consultas por fila."""
        with self._lock, sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                """
                SELECT clave_numerica, estado, categoria, subcategoria, proveedor,
                       ruta_origen, ruta_destino, sha256, fecha_clasificacion, clasificado_por
                FROM clasificaciones
                """
            ).fetchall()

        cols = [
            "clave_numerica", "estado", "categoria", "subcategoria", "proveedor",
            "ruta_origen", "ruta_destino", "sha256", "fecha_clasificacion", "clasificado_por",
        ]
        return {str(row[0]): dict(zip(cols, row)) for row in rows}

    def upsert(self, **kwargs: str) -> None:
        keys = [
            "clave_numerica", "estado", "categoria", "subcategoria", "proveedor",
            "ruta_origen", "ruta_destino", "sha256", "fecha_clasificacion", "clasificado_por",
        ]
        payload = {k: kwargs.get(k, "") for k in keys}
        with self._lock, sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO clasificaciones(clave_numerica, estado, categoria, subcategoria, proveedor,
                                            ruta_origen, ruta_destino, sha256, fecha_clasificacion, clasificado_por)
                VALUES(:clave_numerica, :estado, :categoria, :subcategoria, :proveedor,
                       :ruta_origen, :ruta_destino, :sha256, :fecha_clasificacion, :clasificado_por)
                ON CONFLICT(clave_numerica) DO UPDATE SET
                  estado=excluded.estado,
                  categoria=excluded.categoria,
                  subcategoria=excluded.subcategoria,
                  proveedor=excluded.proveedor,
                  ruta_origen=excluded.ruta_origen,
                  ruta_destino=excluded.ruta_destino,
                  sha256=excluded.sha256,
                  fecha_clasificacion=excluded.fecha_clasificacion,
                  clasificado_por=excluded.clasificado_por
                """,
                payload,
            )


def classify_record(
    record: FacturaRecord,
    client_folder: Path,
    db: ClassificationDB,
    categoria: str,
    subcategoria: str,
    proveedor: str,
    user: str = "local",
) -> Path | None:
    """
    Clasifica una factura moviendola a la carpeta contable correspondiente.
    Si no hay PDF, registra como 'pendiente_pdf' sin mover archivos.
    Movimiento atomico: copiar -> verificar SHA256 -> borrar original.
    """
    if record.pdf_path is None:
        db.upsert(
            clave_numerica=record.clave,
            estado="pendiente_pdf",
            categoria=categoria,
            subcategoria=subcategoria,
            proveedor=proveedor,
            ruta_origen=str(record.xml_path or ""),
            ruta_destino="",
            sha256="",
            fecha_clasificacion=datetime.now().isoformat(timespec="seconds"),
            clasificado_por=user,
        )
        return None

    dest_folder = client_folder / categoria / subcategoria / proveedor
    dest_folder.mkdir(parents=True, exist_ok=True)

    original = record.pdf_path
    ruta_origen_str = str(original)  # guardar antes de cualquier operacion

    target = dest_folder / original.name
    if target.exists():
        suffix = sha256_file(original)[:8]
        target = dest_folder / f"{original.stem}__{suffix}{original.suffix}"

    # Movimiento atomico: copiar -> verificar SHA256 -> borrar
    source_hash = sha256_file(original)
    shutil.copy2(original, target)

    copy_hash = sha256_file(target)
    if source_hash != copy_hash:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"Fallo validacion SHA256 al copiar '{original.name}'.\n"
            "El archivo original no fue modificado."
        )

    removed = False
    last_err: Exception | None = None
    for attempt in range(6):
        try:
            original.unlink()
            removed = True
            break
        except PermissionError as err:
            last_err = err
            time.sleep(0.15 * (attempt + 1))
        except OSError as err:
            last_err = err
            break

    if not removed:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            "No se pudo mover el PDF porque está en uso por otra aplicación (ej: visor PDF abierto).\n"
            "Cierra el archivo e intenta nuevamente."
        ) from last_err

    db.upsert(
        clave_numerica=record.clave,
        estado="clasificado",
        categoria=categoria,
        subcategoria=subcategoria,
        proveedor=proveedor,
        ruta_origen=ruta_origen_str,
        ruta_destino=str(target),
        sha256=source_hash,
        fecha_clasificacion=datetime.now().isoformat(timespec="seconds"),
        clasificado_por=user,
    )

    # Actualizar record en memoria
    record.pdf_path = target
    record.estado = "clasificado"

    return target
