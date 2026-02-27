from __future__ import annotations

import hashlib
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from app3.bootstrap import bootstrap_legacy_paths
from app3.config import network_drive
from .models import FacturaRecord

bootstrap_legacy_paths()

from facturacion_system.core.file_manager import sanitize_folder_name  # noqa: E402

MONTH_NAMES = {
    1: "01-ENERO",
    2: "02-FEBRERO",
    3: "03-MARZO",
    4: "04-ABRIL",
    5: "05-MAYO",
    6: "06-JUNIO",
    7: "07-JULIO",
    8: "08-AGOSTO",
    9: "09-SEPTIEMBRE",
    10: "10-OCTUBRE",
    11: "11-NOVIEMBRE",
    12: "12-DICIEMBRE",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _month_bucket(fecha_emision: str) -> str:
    raw = (fecha_emision or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return MONTH_NAMES.get(dt.month, f"{dt.month:02d}-MES")
        except ValueError:
            continue
    now = datetime.now()
    return MONTH_NAMES[now.month]


def build_dest_folder(
    *,
    record: FacturaRecord,
    year: int,
    client_name: str,
    categoria: str,
    subtipo: str,
    nombre_cuenta: str,
    proveedor: str,
) -> Path:
    cat = categoria.strip().upper()
    stp = subtipo.strip().upper()
    cuenta = nombre_cuenta.strip().upper()
    prov = proveedor.strip().upper()

    month_folder = _month_bucket(record.fecha_emision)
    client_clean = sanitize_folder_name(client_name)
    base = network_drive() / f"PF-{year}" / "Contabilidades" / month_folder / client_clean

    if cat == "COMPRAS":
        provider = sanitize_folder_name(prov or record.emisor_nombre or "SIN PROVEEDOR")
        return base / "COMPRAS" / provider

    if cat == "GASTOS":
        tipo = sanitize_folder_name(stp or "GASTOS GENERALES")
        account = sanitize_folder_name(cuenta or "SIN CUENTA")
        provider = sanitize_folder_name(prov or record.emisor_nombre or "SIN PROVEEDOR")
        return base / "GASTOS" / tipo / account / provider

    if cat == "OGND":
        tipo_ognd = sanitize_folder_name(stp or "OGND")
        return base / "OGND" / tipo_ognd

    raise ValueError(f"Categoría no soportada: {categoria}")


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
                  subtipo              TEXT,
                  nombre_cuenta        TEXT,
                  proveedor            TEXT,
                  ruta_origen          TEXT,
                  ruta_destino         TEXT,
                  sha256               TEXT,
                  fecha_clasificacion  TEXT,
                  clasificado_por      TEXT
                )
                """
            )

            existing_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(clasificaciones)").fetchall()
            }
            if "subtipo" not in existing_cols:
                conn.execute("ALTER TABLE clasificaciones ADD COLUMN subtipo TEXT")
            if "nombre_cuenta" not in existing_cols:
                conn.execute("ALTER TABLE clasificaciones ADD COLUMN nombre_cuenta TEXT")

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
                """
                SELECT clave_numerica, estado, categoria,
                       COALESCE(subtipo, subcategoria, '') AS subtipo,
                       COALESCE(nombre_cuenta, subcategoria, '') AS nombre_cuenta,
                       proveedor, ruta_origen, ruta_destino, sha256,
                       fecha_clasificacion, clasificado_por,
                       COALESCE(subcategoria, '') AS subcategoria
                FROM clasificaciones
                WHERE clave_numerica=?
                """,
                (clave,),
            ).fetchone()
            if not row:
                return None
            cols = [
                "clave_numerica", "estado", "categoria", "subtipo", "nombre_cuenta",
                "proveedor", "ruta_origen", "ruta_destino", "sha256", "fecha_clasificacion",
                "clasificado_por", "subcategoria",
            ]
            return dict(zip(cols, row))

    def get_records_map(self) -> dict[str, dict]:
        """Retorna todas las clasificaciones en memoria para evitar consultas por fila."""
        with self._lock, sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                """
                SELECT clave_numerica, estado, categoria,
                       COALESCE(subtipo, subcategoria, '') AS subtipo,
                       COALESCE(nombre_cuenta, subcategoria, '') AS nombre_cuenta,
                       proveedor, ruta_origen, ruta_destino, sha256,
                       fecha_clasificacion, clasificado_por,
                       COALESCE(subcategoria, '') AS subcategoria
                FROM clasificaciones
                """
            ).fetchall()

        cols = [
            "clave_numerica", "estado", "categoria", "subtipo", "nombre_cuenta", "proveedor",
            "ruta_origen", "ruta_destino", "sha256", "fecha_clasificacion", "clasificado_por",
            "subcategoria",
        ]
        return {str(row[0]): dict(zip(cols, row)) for row in rows}

    def upsert(self, **kwargs: str) -> None:
        keys = [
            "clave_numerica", "estado", "categoria", "subcategoria", "subtipo", "nombre_cuenta",
            "proveedor", "ruta_origen", "ruta_destino", "sha256", "fecha_clasificacion", "clasificado_por",
        ]
        payload = {k: kwargs.get(k, "") for k in keys}
        if not payload.get("subcategoria"):
            payload["subcategoria"] = payload.get("nombre_cuenta") or payload.get("subtipo") or ""
        with self._lock, sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO clasificaciones(
                    clave_numerica, estado, categoria, subcategoria, subtipo, nombre_cuenta,
                    proveedor, ruta_origen, ruta_destino, sha256, fecha_clasificacion, clasificado_por
                )
                VALUES(
                    :clave_numerica, :estado, :categoria, :subcategoria, :subtipo, :nombre_cuenta,
                    :proveedor, :ruta_origen, :ruta_destino, :sha256, :fecha_clasificacion, :clasificado_por
                )
                ON CONFLICT(clave_numerica) DO UPDATE SET
                  estado=excluded.estado,
                  categoria=excluded.categoria,
                  subcategoria=excluded.subcategoria,
                  subtipo=excluded.subtipo,
                  nombre_cuenta=excluded.nombre_cuenta,
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
    subtipo: str,
    nombre_cuenta: str,
    proveedor: str,
    *,
    year: int | None = None,
    user: str = "local",
) -> Path | None:
    """
    Clasifica una factura moviéndola a estructura mensual de contabilidades.
    Si no hay PDF, registra como 'pendiente_pdf' sin mover archivos.
    Movimiento atómico: copiar -> verificar SHA256 -> borrar original.
    """
    if year is None:
        year = datetime.now().year

    categoria = categoria.strip().upper()
    subtipo = subtipo.strip().upper()
    nombre_cuenta = nombre_cuenta.strip().upper()
    proveedor = proveedor.strip().upper()

    if record.pdf_path is None:
        db.upsert(
            clave_numerica=record.clave,
            estado="pendiente_pdf",
            categoria=categoria,
            subtipo=subtipo,
            nombre_cuenta=nombre_cuenta,
            subcategoria=nombre_cuenta or subtipo,
            proveedor=proveedor,
            ruta_origen=str(record.xml_path or ""),
            ruta_destino="",
            sha256="",
            fecha_clasificacion=datetime.now().isoformat(timespec="seconds"),
            clasificado_por=user,
        )
        return None

    dest_folder = build_dest_folder(
        record=record,
        year=year,
        client_name=client_folder.name,
        categoria=categoria,
        subtipo=subtipo,
        nombre_cuenta=nombre_cuenta,
        proveedor=proveedor,
    )
    dest_folder.mkdir(parents=True, exist_ok=True)

    original = record.pdf_path
    ruta_origen_str = str(original)  # guardar antes de cualquier operación

    target = dest_folder / original.name
    if target.exists():
        suffix = sha256_file(original)[:8]
        target = dest_folder / f"{original.stem}__{suffix}{original.suffix}"

    # Movimiento atómico: copiar -> verificar SHA256 -> borrar
    source_hash = sha256_file(original)
    shutil.copy2(original, target)

    copy_hash = sha256_file(target)
    if source_hash != copy_hash:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"Fallo validación SHA256 al copiar '{original.name}'.\n"
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
        subtipo=subtipo,
        nombre_cuenta=nombre_cuenta,
        subcategoria=nombre_cuenta or subtipo,
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
