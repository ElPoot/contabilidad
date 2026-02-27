from __future__ import annotations

import hashlib
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .models import FacturaRecord

_MESES = {
    1: "ENERO",    2: "FEBRERO",  3: "MARZO",     4: "ABRIL",
    5: "MAYO",     6: "JUNIO",    7: "JULIO",      8: "AGOSTO",
    9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
}

# Caracteres no permitidos en nombres de carpetas en Windows
_INVALID_CHARS = frozenset(r'\/:*?"<>|')


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize_folder(name: str) -> str:
    """Elimina caracteres no válidos en nombres de carpeta de Windows."""
    clean = "".join("_" if c in _INVALID_CHARS else c for c in str(name or "").strip())
    return clean[:100] or "SIN_NOMBRE"


def build_dest_folder(
    session_folder: Path,
    fecha_emision: str,
    categoria: str,
    subtipo: str,
    nombre_cuenta: str,
    proveedor: str,
) -> Path:
    """
    Construye la ruta de destino para un PDF clasificado:

    Z:/DATA/PF-{año}/Contabilidades/{mes}/{cliente}/
      COMPRAS/{proveedor}/
      GASTOS/{subtipo}/{nombre_cuenta}/{proveedor}/
      OGND/{subtipo}/
    """
    try:
        dt = datetime.strptime(fecha_emision.strip(), "%d/%m/%Y")
    except (ValueError, AttributeError):
        dt = datetime.now()

    mes_str = f"{dt.month:02d}-{_MESES[dt.month]}"
    # session_folder = Z:/DATA/PF-{year}/CLIENTES/{CLIENT_NAME}
    pf_root     = session_folder.parent.parent  # Z:/DATA/PF-{year}/
    client_name = session_folder.name
    base = pf_root / "Contabilidades" / mes_str / client_name

    cat = categoria.upper()
    if cat == "COMPRAS":
        return base / "COMPRAS" / _sanitize_folder(proveedor)
    if cat == "GASTOS":
        return (
            base
            / "GASTOS"
            / _sanitize_folder(subtipo)
            / _sanitize_folder(nombre_cuenta)
            / _sanitize_folder(proveedor)
        )
    if cat == "OGND":
        return base / "OGND" / _sanitize_folder(subtipo)

    # Categoría genérica — fallback
    parts = [_sanitize_folder(x) for x in [categoria, subtipo, nombre_cuenta, proveedor] if x]
    result = base
    for p in parts:
        result = result / p
    return result


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
            # Migración: agregar columnas nuevas si la tabla venía de versión anterior
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(clasificaciones)").fetchall()
            }
            for col in ("subtipo", "nombre_cuenta"):
                if col not in existing:
                    conn.execute(
                        f"ALTER TABLE clasificaciones ADD COLUMN {col} TEXT"
                    )

    # ── Lectura ────────────────────────────────────────────────────────────────

    _COLS = [
        "clave_numerica", "estado", "categoria", "subtipo", "nombre_cuenta",
        "proveedor", "ruta_origen", "ruta_destino", "sha256",
        "fecha_clasificacion", "clasificado_por",
    ]

    def get_estado(self, clave: str) -> str | None:
        with self._lock, sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT estado FROM clasificaciones WHERE clave_numerica=?", (clave,)
            ).fetchone()
            return row[0] if row else None

    def get_record(self, clave: str) -> dict | None:
        with self._lock, sqlite3.connect(self.path) as conn:
            row = conn.execute(
                f"SELECT {', '.join(self._COLS)} FROM clasificaciones "
                "WHERE clave_numerica=?",
                (clave,),
            ).fetchone()
            return dict(zip(self._COLS, row)) if row else None

    def get_records_map(self) -> dict[str, dict]:
        """Carga todas las clasificaciones en memoria para evitar consultas por fila."""
        with self._lock, sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                f"SELECT {', '.join(self._COLS)} FROM clasificaciones"
            ).fetchall()
        return {str(row[0]): dict(zip(self._COLS, row)) for row in rows}

    # ── Escritura ──────────────────────────────────────────────────────────────

    def upsert(self, **kwargs: str) -> None:
        payload = {k: kwargs.get(k, "") for k in self._COLS}
        cols_sql   = ", ".join(self._COLS)
        params_sql = ", ".join(f":{k}" for k in self._COLS)
        update_sql = ", ".join(
            f"{k}=excluded.{k}" for k in self._COLS if k != "clave_numerica"
        )
        with self._lock, sqlite3.connect(self.path) as conn:
            conn.execute(
                f"""
                INSERT INTO clasificaciones({cols_sql})
                VALUES({params_sql})
                ON CONFLICT(clave_numerica) DO UPDATE SET
                  {update_sql}
                """,
                payload,
            )


def classify_record(
    record: FacturaRecord,
    session_folder: Path,
    db: ClassificationDB,
    categoria: str,
    subtipo: str,
    nombre_cuenta: str,
    proveedor: str,
    user: str = "local",
) -> Path | None:
    """
    Clasifica una factura moviéndola a la carpeta contable correspondiente.

    Si no hay PDF registra como 'pendiente_pdf' sin mover archivos.
    Movimiento atómico: SHA256 → copiar → verificar SHA256 → borrar original.
    """
    if record.pdf_path is None:
        db.upsert(
            clave_numerica=record.clave,
            estado="pendiente_pdf",
            categoria=categoria,
            subtipo=subtipo,
            nombre_cuenta=nombre_cuenta,
            proveedor=proveedor,
            ruta_origen=str(record.xml_path or ""),
            ruta_destino="",
            sha256="",
            fecha_clasificacion=datetime.now().isoformat(timespec="seconds"),
            clasificado_por=user,
        )
        return None

    dest_folder = build_dest_folder(
        session_folder,
        record.fecha_emision,
        categoria,
        subtipo,
        nombre_cuenta,
        proveedor,
    )
    dest_folder.mkdir(parents=True, exist_ok=True)

    original       = record.pdf_path
    ruta_origen_str = str(original)

    target = dest_folder / original.name
    if target.exists():
        suffix = sha256_file(original)[:8]
        target = dest_folder / f"{original.stem}__{suffix}{original.suffix}"

    # Movimiento atómico: copiar → verificar SHA256 → borrar
    source_hash = sha256_file(original)
    shutil.copy2(original, target)

    copy_hash = sha256_file(target)
    if source_hash != copy_hash:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"Fallo validación SHA256 al copiar '{original.name}'.\n"
            "El archivo original no fue modificado."
        )

    removed   = False
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
        proveedor=proveedor,
        ruta_origen=ruta_origen_str,
        ruta_destino=str(target),
        sha256=source_hash,
        fecha_clasificacion=datetime.now().isoformat(timespec="seconds"),
        clasificado_por=user,
    )

    record.pdf_path = target
    record.estado   = "clasificado"

    return target
