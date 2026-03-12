from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .models import FacturaRecord

logger = logging.getLogger(__name__)

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
      INGRESOS/
      SIN_RECEPTOR/
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
    if cat == "ACTIVO":
        return base / "ACTIVO" / _sanitize_folder(proveedor)
    if cat == "INGRESOS":
        return base / "INGRESOS"
    if cat == "SIN_RECEPTOR":
        return base / "SIN_RECEPTOR"

    # Categoría genérica -- fallback
    parts = [_sanitize_folder(x) for x in [categoria, subtipo, nombre_cuenta, proveedor] if x]
    result = base
    for p in parts:
        result = result / p
    return result


def heal_classified_path(
    stored_path: "Path | str",
    contabilidades_root: Path,
    db: "ClassificationDB | None" = None,
    clave: str | None = None,
) -> "Path | None":
    """Resuelve una ruta clasificada rota porque el contador renombró la carpeta del cliente.

    Estructura: Contabilidades/{mes}/{cliente}/{cat}/.../{archivo}
    El mes NO cambia. Solo la carpeta {cliente} puede ser renombrada.
    Ej: "EMPRESA XYZ" → "EMPRESA XYZ L"

    Busca el archivo en todas las subcarpetas del mes correcto.
    Si db + clave se proporcionan, actualiza la BD con la ruta nueva.
    """
    stored = Path(stored_path)
    if stored.exists():
        return stored

    if not contabilidades_root.exists():
        return None

    # Extraer: mes (intacto) + relativo después del cliente (intacto)
    # Estructura: .../Contabilidades/{mes}/{cliente}/{cat}/.../{archivo}
    parts = stored.parts
    try:
        cont_idx = next(i for i, p in enumerate(parts) if p == "Contabilidades")
        # cont_idx+1 = mes, cont_idx+2 = cliente (renombrado), cont_idx+3: = resto
        if len(parts) <= cont_idx + 3:
            return None
        mes_folder = parts[cont_idx + 1]
        relative_after_client = Path(*parts[cont_idx + 3:])
    except StopIteration:
        return None

    month_dir = contabilidades_root / mes_folder
    if not month_dir.exists():
        return None

    # Buscar en todas las carpetas de cliente del mes correcto
    try:
        for client_dir in month_dir.iterdir():
            if not client_dir.is_dir():
                continue
            candidate = client_dir / relative_after_client
            if candidate.exists():
                if db is not None and clave:
                    db.update_ruta_destino(clave, str(candidate))
                    logger.info(
                        "Ruta reparada: cliente renombrado '%s' -> '%s'",
                        parts[cont_idx + 2], client_dir.name,
                    )
                return candidate
    except OSError:
        pass

    return None


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

    def update_ruta_destino(self, clave: str, nueva_ruta: str) -> None:
        """Actualiza SOLO ruta_destino sin tocar ningún otro campo del registro."""
        with self._lock, sqlite3.connect(self.path) as conn:
            conn.execute(
                "UPDATE clasificaciones SET ruta_destino=? WHERE clave_numerica=?",
                (nueva_ruta, clave),
            )


def recover_orphaned_pdf(
    orphaned_info: dict,
    db: ClassificationDB,
) -> bool:
    """
    Recupera un PDF huérfano moviéndolo a su ubicación correcta.

    Args:
        orphaned_info: Diccionario con keys: clave, archivo, ruta_esperada, motivo
        db: ClassificationDB para actualizar registros

    Returns:
        True si se recuperó exitosamente, False si falló
    """
    try:
        clave = orphaned_info.get("clave")
        archivo_actual = Path(orphaned_info.get("archivo"))
        ruta_esperada = orphaned_info.get("ruta_esperada")
        motivo = orphaned_info.get("motivo")

        if not archivo_actual.exists():
            raise FileNotFoundError(f"Archivo no existe: {archivo_actual}")

        if motivo == "not_in_db":
            # PDF sin registro en BD -- simplemente eliminar
            archivo_actual.unlink()
            logging.info(f"Eliminado PDF huérfano sin registro: {archivo_actual}")
            return True

        if not ruta_esperada:
            raise ValueError("No hay ruta esperada para este PDF")

        ruta_esperada = Path(ruta_esperada)

        # Crear carpeta destino si no existe
        ruta_esperada.parent.mkdir(parents=True, exist_ok=True)

        # Si ya existe en destino, no hacer nada (ya está correcto)
        if archivo_actual == ruta_esperada and ruta_esperada.exists():
            logging.info(f"PDF ya está en ubicación correcta: {ruta_esperada}")
            return True

        # Mover archivo con retry loop (como en classify_record)
        for attempt in range(12):
            try:
                shutil.move(str(archivo_actual), str(ruta_esperada))
                logging.info(
                    f"Recuperado PDF: {archivo_actual.name}\n"
                    f"  De: {archivo_actual}\n"
                    f"  A:  {ruta_esperada}"
                )

                # Actualizar BD
                db.upsert(
                    clave_numerica=clave,
                    ruta_destino=str(ruta_esperada),
                )
                return True
            except PermissionError:
                time.sleep(0.2 * (attempt + 1))
            except OSError:
                break

        raise RuntimeError(f"No se pudo mover PDF después de 12 intentos")

    except Exception as e:
        logging.error(f"Error recuperando PDF {orphaned_info.get('clave')}: {e}")
        return False


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
    Movimiento ATÓMICO con verificación SHA256:
      1. Calcular SHA256 del original
      2. Copiar (preservando metadata)
      3. Calcular SHA256 de la copia
      4. Si no coinciden → borrar copia, error
      5. Si coinciden → borrar original (con retry)
      6. Registrar en BD
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

    # (1) Calcular hash del original
    source_hash = sha256_file(original)

    target = dest_folder / original.name
    if target.exists():
        suffix = source_hash[:8]
        target = dest_folder / f"{original.stem}__{suffix}{original.suffix}"

    # (2) Copiar con metadata preservada
    try:
        shutil.copy2(str(original), str(target))
        logger.info(f"PDF copiado: {original.name} → {target.name}")
    except Exception as err:
        raise RuntimeError(
            f"No se pudo copiar el PDF a la carpeta de destino.\n"
            f"Verifica que Z:/ esté accesible y haya espacio disponible.\n\n"
            f"Error: {err}"
        ) from err

    # (3) Calcular hash de la copia
    try:
        dest_hash = sha256_file(target)
    except Exception as err:
        # Si no se puede leer la copia, eliminarla y abortar
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"No se pudo verificar la copia del PDF.\n"
            f"La copia ha sido eliminada. Intenta de nuevo.\n\n"
            f"Error: {err}"
        ) from err

    # (4) Verificar integridad SHA256
    if dest_hash != source_hash:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"SHA256 mismatch después de copiar el PDF.\n"
            f"Original: {source_hash}\n"
            f"Copia:    {dest_hash}\n"
            f"La copia corrupta ha sido eliminada. El original está intacto.\n"
            f"Intenta de nuevo."
        )

    logger.info(f"SHA256 verificado: {source_hash}")

    # (5) Borrar original con retry loop
    deleted = False
    last_err: Exception | None = None

    for attempt in range(12):
        try:
            original.unlink()
            deleted = True
            logger.info(f"Original eliminado después del intento {attempt + 1}")
            break
        except PermissionError as err:
            last_err = err
            # Espera progresiva: 0.2s, 0.4s, 0.6s... hasta 2.4s
            time.sleep(0.2 * (attempt + 1))
        except OSError as err:
            last_err = err
            # Otros errores del SO (no reintentar)
            break

    if not deleted:
        # Si no se puede borrar el original, al menos elimina la copia para evitar duplicado
        target.unlink(missing_ok=True)
        raise RuntimeError(
            "El PDF fue copiado correctamente, pero no se pudo eliminar el original\n"
            "(está en uso por otra aplicación, ej: visor PDF abierto).\n"
            "La copia ha sido eliminada para evitar duplicados.\n\n"
            "Cierra el visor de PDFs e intenta de nuevo. [Intentos: {attempt + 1}/12]"
        ) from last_err

    # (6) Registrar en BD
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
