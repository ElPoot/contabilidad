"""Gestor de la base de datos CABYS local (Hacienda CR).

Estrategia de carga:
  1. Descarga masiva desde la API de Hacienda (queries A-Z, ~9.600 códigos)
  2. Lazy loading individual desde API para códigos nuevos no encontrados localmente
  3. Todo queda cacheado en SQLite local en Z:/DATA/CONFIG/cabys_database.db

Nota: Los códigos CABYS son de 13 dígitos (ej: 4526100000100).
"""
from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

try:
    import requests as _requests
except ModuleNotFoundError:
    _requests = None


def _resolve_cabys_db_path() -> Path:
    """Resuelve ruta de la BD CABYS: network drive o fallback local."""
    fallback_reason = "ruta de red CABYS no disponible"
    try:
        from gestor_contable.core.settings import get_setting
        network_drive = Path(get_setting("network_drive", "Z:/DATA"))
        candidate = network_drive / "CONFIG" / "cabys_database.db"
        if candidate.parent.exists():
            return candidate
        fallback_reason = f"carpeta no existe: {candidate.parent}"
    except Exception as exc:
        fallback_reason = str(exc)
        LOGGER.warning("Fallo resolviendo base de datos CABYS en red; se usara fallback: %s", exc)

    fallback = Path(__file__).resolve().parent.parent / "data" / "cabys_database.db"
    fallback.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Usando BD CABYS de fallback local: %s (motivo: %s)", fallback, fallback_reason)
    return fallback


# ---------------------------------------------------------------------------
# Clasificación de tipo por capítulo CABYS
# ---------------------------------------------------------------------------
# Capítulos que corresponden a SERVICIOS según los datos reales del catálogo CR.
# Bienes: capítulos 01–52. Servicios: capítulos 53–99.
# (Verificado contra los 5,198 códigos descargados: ningún bien tiene capítulo > 52)
_CAPITULOS_SERVICIO: frozenset[int] = frozenset(range(53, 100))


def _tipo_por_capitulo(codigo: str) -> str:
    """Heurística rápida: 'bien' o 'servicio' según los dos primeros dígitos CABYS."""
    if len(codigo) >= 2 and codigo[:2].isdigit():
        return "servicio" if int(codigo[:2]) in _CAPITULOS_SERVICIO else "bien"
    return ""




class CABYSManager:
    """
    Gestiona la base de datos local de códigos CABYS.

    Uso recomendado como singleton:
        mgr = CABYSManager.get_instance()
        info = mgr.get_info("1522190010")
    """

    CABYS_API_URL = "https://api.hacienda.go.cr/fe/cabys?codigo={codigo}"
    _instance: CABYSManager | None = None
    _singleton_lock = threading.Lock()

    def __init__(self) -> None:
        self._db_path = _resolve_cabys_db_path()
        self._db_lock = threading.Lock()
        self._ensure_db()

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------
    @classmethod
    def get_instance(cls) -> CABYSManager:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ------------------------------------------------------------------
    # Esquema SQLite
    # ------------------------------------------------------------------
    def _ensure_db(self) -> None:
        with self._db_lock:
            with contextlib.closing(sqlite3.connect(self._db_path)) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cabys (
                        codigo        TEXT PRIMARY KEY,
                        descripcion   TEXT NOT NULL DEFAULT '',
                        impuesto      REAL,
                        tipo          TEXT NOT NULL DEFAULT '',
                        capitulo      TEXT NOT NULL DEFAULT '',
                        unidad_medida TEXT NOT NULL DEFAULT '',
                        raw_json      TEXT,
                        updated_at    INTEGER NOT NULL DEFAULT 0
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cabys_capitulo ON cabys(capitulo)"
                )
                # Migración: añadir unidad_medida si la tabla ya existía sin ella
                try:
                    conn.execute(
                        "ALTER TABLE cabys ADD COLUMN unidad_medida TEXT NOT NULL DEFAULT ''"
                    )
                except sqlite3.OperationalError:
                    pass  # columna ya existe
                conn.commit()

    # ------------------------------------------------------------------
    # Consulta pública principal
    # ------------------------------------------------------------------
    def get_info(self, codigo: str) -> dict[str, Any] | None:
        """
        Retorna información de un código CABYS.
        Cache-first: si no está en local, consulta la API y guarda el resultado.

        Returns dict con keys: codigo, descripcion, impuesto, tipo, capitulo, unidad_medida
        Returns None si el código está vacío.
        """
        codigo = str(codigo or "").strip()
        if not codigo:
            return None

        cached = self._cache_get(codigo)
        if cached is not None:
            return cached

        fetched = self._fetch_api(codigo)
        if fetched:
            self._cache_put_one(fetched)
            return fetched

        # No existe en Hacienda: guardar registro vacío para evitar re-consultas
        fallback: dict[str, Any] = {
            "codigo": codigo,
            "descripcion": "",
            "impuesto": None,
            "tipo": _tipo_por_capitulo(codigo),
            "capitulo": codigo[:2] if len(codigo) >= 2 else "",
            "unidad_medida": "",
        }
        self._cache_put_one(fallback)
        return fallback

    def get_many(self, codigos: list[str]) -> dict[str, dict[str, Any]]:
        """Obtiene info de múltiples códigos: bulk cache primero, luego API para faltantes."""
        codigos = [str(c).strip() for c in codigos if c]
        if not codigos:
            return {}

        result = self._cache_get_bulk(codigos)
        missing = [c for c in codigos if c not in result]
        for codigo in missing:
            info = self.get_info(codigo)
            if info:
                result[codigo] = info
        return result

    # ------------------------------------------------------------------
    # Cache SQLite — lectura
    # ------------------------------------------------------------------
    def _cache_get(self, codigo: str) -> dict[str, Any] | None:
        with self._db_lock:
            with contextlib.closing(sqlite3.connect(self._db_path)) as conn:
                row = conn.execute(
                    "SELECT descripcion, impuesto, tipo, capitulo, unidad_medida "
                    "FROM cabys WHERE codigo = ?",
                    (codigo,),
                ).fetchone()
        if row is None:
            return None
        return {
            "codigo": codigo,
            "descripcion": row[0] or "",
            "impuesto": row[1],
            "tipo": row[2] or "",
            "capitulo": row[3] or "",
            "unidad_medida": row[4] or "",
        }

    def _cache_get_bulk(self, codigos: list[str]) -> dict[str, dict[str, Any]]:
        if not codigos:
            return {}
        placeholders = ",".join("?" for _ in codigos)
        with self._db_lock:
            with contextlib.closing(sqlite3.connect(self._db_path)) as conn:
                rows = conn.execute(
                    f"SELECT codigo, descripcion, impuesto, tipo, capitulo, unidad_medida "
                    f"FROM cabys WHERE codigo IN ({placeholders})",
                    tuple(codigos),
                ).fetchall()
        return {
            row[0]: {
                "codigo": row[0],
                "descripcion": row[1] or "",
                "impuesto": row[2],
                "tipo": row[3] or "",
                "capitulo": row[4] or "",
                "unidad_medida": row[5] or "",
            }
            for row in rows
        }

    # ------------------------------------------------------------------
    # Cache SQLite — escritura
    # ------------------------------------------------------------------
    _INSERT_SQL = """
        INSERT INTO cabys(codigo, descripcion, impuesto, tipo, capitulo,
                          unidad_medida, raw_json, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(codigo) DO UPDATE SET
            descripcion   = excluded.descripcion,
            impuesto      = excluded.impuesto,
            tipo          = excluded.tipo,
            capitulo      = excluded.capitulo,
            unidad_medida = excluded.unidad_medida,
            raw_json      = excluded.raw_json,
            updated_at    = excluded.updated_at
    """

    def _cache_put_one(self, info: dict[str, Any]) -> None:
        codigo = info.get("codigo", "")
        if not codigo:
            return
        with self._db_lock:
            with contextlib.closing(sqlite3.connect(self._db_path)) as conn:
                conn.execute(
                    self._INSERT_SQL,
                    self._info_to_row(info),
                )
                conn.commit()

    def _cache_put_batch(self, records: list[dict[str, Any]]) -> None:
        """Inserta o actualiza múltiples registros en una sola transacción."""
        rows = [self._info_to_row(r) for r in records if r.get("codigo")]
        if not rows:
            return
        with self._db_lock:
            with contextlib.closing(sqlite3.connect(self._db_path)) as conn:
                conn.executemany(self._INSERT_SQL, rows)
                conn.commit()

    @staticmethod
    def _info_to_row(info: dict[str, Any]) -> tuple:
        codigo = str(info.get("codigo") or "")
        return (
            codigo,
            str(info.get("descripcion") or ""),
            info.get("impuesto"),
            str(info.get("tipo") or ""),
            str(info.get("capitulo") or (codigo[:2] if len(codigo) >= 2 else "")),
            str(info.get("unidad_medida") or ""),
            json.dumps(info, ensure_ascii=False),
            int(time.time()),
        )

    # ------------------------------------------------------------------
    # API de Hacienda (lazy, individual)
    # ------------------------------------------------------------------
    def _fetch_api(self, codigo: str) -> dict[str, Any] | None:
        if _requests is None:
            return None

        url = self.CABYS_API_URL.format(codigo=codigo)
        for attempt in range(3):
            try:
                resp = _requests.get(url, timeout=8)
            except _requests.RequestException:
                time.sleep(0.5 * (attempt + 1))
                continue

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    return None
                if isinstance(data, list):
                    data = data[0] if data else None
                if isinstance(data, dict):
                    return self._normalize_api_response(codigo, data)
                return None

            if resp.status_code in (404, 204):
                return None

            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(0.8 * (attempt + 1))
                continue

            return None

        return None

    def _normalize_api_response(self, codigo: str, data: dict) -> dict[str, Any]:
        descripcion = str(
            data.get("descripcion") or data.get("nombre") or data.get("description") or ""
        ).strip()

        impuesto_raw = data.get("impuesto") or data.get("tarifa") or data.get("iva")
        try:
            impuesto = float(impuesto_raw) if impuesto_raw is not None else None
        except (ValueError, TypeError):
            impuesto = None

        tipo_api = str(data.get("tipo") or data.get("type") or "").lower()
        if "servicio" in tipo_api or "service" in tipo_api:
            tipo = "servicio"
        elif "bien" in tipo_api or "good" in tipo_api or "product" in tipo_api:
            tipo = "bien"
        else:
            tipo = _tipo_por_capitulo(codigo)

        unidad = str(data.get("unidadMedida") or data.get("unidad_medida") or data.get("unidad") or "").strip()

        return {
            "codigo": codigo,
            "descripcion": descripcion,
            "impuesto": impuesto,
            "tipo": tipo,
            "capitulo": codigo[:2] if len(codigo) >= 2 else "",
            "unidad_medida": unidad,
        }

    # ------------------------------------------------------------------
    # Descarga masiva desde la API de Hacienda
    # ------------------------------------------------------------------

    # Términos de búsqueda que cubren el catálogo completo.
    # Estrategia: queries A-Z capturan todos los códigos (sus descripciones
    # contienen al menos una letra). Se agregan términos de alto volumen
    # conocidos para minimizar queries con pocos resultados.
    _BULK_QUERIES: tuple[str, ...] = (
        # Letras del alfabeto — cobertura exhaustiva de descripciones
        "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
        "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
        # Términos de alto volumen
        "n.c.p",         # "no clasificado en otra parte"
        "excepto",
        "incluye",
        "servicio",
        "producto",
        # Términos para capítulos con gaps detectados (55-60, 74-80, 10, 50-52)
        "alojamiento",   # cap 55 — hoteles
        "comida",        # cap 56 — restaurantes
        "restaurante",
        "alimento",      # cap 10 — alimentos procesados
        "transporte",    # cap 50-52
        "flete",
        "publicacion",   # cap 58
        "pelicula",      # cap 59
        "radio",         # cap 60
        "veterinario",   # cap 75
        "alquiler",      # cap 77
        "empleo",        # cap 78
        "viaje",         # cap 79
        "seguridad",     # cap 80
    )

    CABYS_SEARCH_URL = "https://api.hacienda.go.cr/fe/cabys?q={q}&top=99999"

    def download_catalog(
        self,
        progress_callback=None,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """
        Descarga el catálogo CABYS completo desde la API de Hacienda.

        Hace múltiples queries (A-Z + términos clave) con top=99999,
        deduplicando por código. Cubre los ~9,600 códigos del catálogo v4.3.

        Args:
            progress_callback: Función opcional (paso: str, actual: int, total: int) -> None
            stop_event:        threading.Event para cancelar la descarga.

        Returns:
            dict con keys: insertados, queries_ok, queries_error, total_unicos
        """
        if _requests is None:
            raise RuntimeError("El paquete 'requests' es requerido para la descarga.")

        queries = self._BULK_QUERIES
        total_queries = len(queries)
        seen: set[str] = set()
        all_records: list[dict[str, Any]] = []
        queries_ok = 0
        queries_error = 0

        for q_idx, term in enumerate(queries):
            if stop_event and stop_event.is_set():
                LOGGER.info("Descarga CABYS cancelada por el usuario.")
                break

            if progress_callback:
                progress_callback(
                    f"Consultando '{term}'…",
                    q_idx,
                    total_queries,
                )

            items = self._fetch_bulk_query(term)
            if items is None:
                queries_error += 1
                LOGGER.warning("Query CABYS '%s' falló.", term)
                continue

            queries_ok += 1
            for item in items:
                codigo = str(item.get("codigo") or "").strip()
                if not codigo or not codigo.isdigit() or codigo in seen:
                    continue
                seen.add(codigo)
                all_records.append(self._normalize_api_item(codigo, item))

            LOGGER.debug("Query '%s': %d items, acumulado único: %d", term, len(items), len(seen))
            # Pausa cortés para no saturar la API
            time.sleep(0.3)

        # Insertar todo en lotes
        BATCH = 500
        inserted = 0
        total = len(all_records)

        if progress_callback:
            progress_callback("Guardando en base de datos…", 0, total)

        for start in range(0, total, BATCH):
            batch = all_records[start : start + BATCH]
            self._cache_put_batch(batch)
            inserted += len(batch)
            if progress_callback:
                progress_callback("Guardando en base de datos…", inserted, total)

        resultado = {
            "insertados": inserted,
            "total_unicos": len(seen),
            "queries_ok": queries_ok,
            "queries_error": queries_error,
        }
        LOGGER.info(
            "Descarga CABYS completa: %d únicos, %d insertados, %d queries OK, %d errores",
            len(seen), inserted, queries_ok, queries_error,
        )
        return resultado

    def _fetch_bulk_query(self, term: str) -> list[dict] | None:
        """Ejecuta un query de búsqueda a la API CABYS. Retorna lista o None si falla."""
        url = self.CABYS_SEARCH_URL.format(q=term)
        for attempt in range(3):
            try:
                resp = _requests.get(url, timeout=15)
            except _requests.RequestException:
                time.sleep(1.0 * (attempt + 1))
                continue

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    return None
                # La API devuelve {total, cantidad, cabys:[...]}
                if isinstance(data, dict):
                    return data.get("cabys") or []
                if isinstance(data, list):
                    return data
                return []

            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue

            return None

        return None

    def _normalize_api_item(self, codigo: str, item: dict) -> dict[str, Any]:
        """Normaliza un item de la respuesta bulk de la API CABYS."""
        descripcion = str(item.get("descripcion") or "").strip()

        impuesto_raw = item.get("impuesto")
        try:
            impuesto = float(impuesto_raw) if impuesto_raw is not None else None
        except (ValueError, TypeError):
            impuesto = None

        # La API bulk no devuelve campo "tipo" explícito.
        # Derivamos de las categorías jerárquicas si están disponibles,
        # y como fallback usamos la heurística de capítulo.
        categorias = item.get("categorias") or []
        tipo = self._tipo_desde_categorias(categorias) or _tipo_por_capitulo(codigo)

        return {
            "codigo": codigo,
            "descripcion": descripcion,
            "impuesto": impuesto,
            "tipo": tipo,
            "capitulo": codigo[:2] if len(codigo) >= 2 else "",
            "unidad_medida": str(item.get("unidadMedida") or item.get("unidad") or "").strip(),
        }

    @staticmethod
    def _tipo_desde_categorias(categorias: list) -> str:
        """
        Deriva 'bien' o 'servicio' desde el array de categorías jerárquicas CABYS.
        La primera categoría (nivel más alto) suele indicar si es bien o servicio.
        """
        if not categorias:
            return ""
        top = str(categorias[0] if categorias else "").lower()
        if any(kw in top for kw in ["servicio", "service", "actividad"]):
            return "servicio"
        if any(kw in top for kw in ["bien", "producto", "mercancia", "mercancía"]):
            return "bien"
        return ""

    # ------------------------------------------------------------------
    # Importación desde Excel oficial de Hacienda
    # ------------------------------------------------------------------
    def import_from_excel(
        self,
        excel_path: Path | str,
        progress_callback=None,
    ) -> dict[str, Any]:
        """
        Importa el catálogo CABYS completo desde el Excel oficial de Hacienda.

        Formato esperado: "Catalogo-de-bienes-servicios.xlsx" (Hacienda CR).
        El Excel tiene estructura jerárquica de 9 categorías. El código CABYS
        real (13 dígitos) está en la última columna de categoría.

        Args:
            excel_path:        Ruta al archivo .xlsx descargado de Hacienda.
            progress_callback: Función opcional (procesados: int, total: int) -> None.

        Returns:
            dict con keys: insertados, omitidos, total_filas, nuevos (vs API)
        """
        try:
            import pandas as pd
        except ImportError:
            raise RuntimeError("pandas es requerido para importar el Excel CABYS")

        excel_path = Path(excel_path)
        if not excel_path.exists():
            raise FileNotFoundError(f"No se encontró: {excel_path}")

        LOGGER.info("Importando CABYS desde Excel: %s", excel_path)

        # ---- Leer hoja "Catálogo" (row 1 = encabezados, row 0 = vacío) ----
        # Buscamos la hoja que contiene "cat" en el nombre (insensible a mayúsc/acento)
        xl = pd.ExcelFile(excel_path)
        hoja = next(
            (s for s in xl.sheet_names if "cat" in s.lower().replace("á","a").replace("é","e")),
            xl.sheet_names[0],
        )
        # Leer con header en fila 1 (índice 1 porque fila 0 está vacía)
        df = pd.read_excel(excel_path, sheet_name=hoja, header=1, dtype=str)
        total_filas = len(df)
        LOGGER.info("Hoja '%s': %d filas", hoja, total_filas)

        # ---- Identificar columnas clave ------------------------------------
        # El Excel tiene pares (Categoría N, Descripción N) para N=1..9
        # El código CABYS de 13 dígitos está en la última columna "Categoría X"
        # La descripción final está en la columna "Descripción (categoría X)" siguiente
        # El impuesto está en la columna "Impuesto" al final
        cols = list(df.columns)

        col_cabys      = self._find_cabys_col(df, cols)
        col_descripcion = self._find_desc_col(cols, col_cabys)
        col_impuesto   = next((c for c in cols if "impuesto" in str(c).lower()), None)
        col_cat1_desc  = next((c for c in cols if "descripci" in str(c).lower()), None)

        if col_cabys is None:
            raise ValueError(
                f"No se encontró columna con códigos CABYS de 13 dígitos en hoja '{hoja}'.\n"
                f"Columnas detectadas: {cols[:10]}..."
            )

        LOGGER.info("Columna CABYS: '%s', descripción: '%s', impuesto: '%s'",
                    col_cabys, col_descripcion, col_impuesto)

        # ---- Procesar filas -----------------------------------------------
        records: list[dict[str, Any]] = []
        omitidos = 0
        ts = int(time.time())

        for idx, row in df.iterrows():
            codigo = str(row.get(col_cabys) or "").strip()
            # Limpiar ".0" que pandas añade al leer como float
            if codigo.endswith(".0"):
                codigo = codigo[:-2]

            if not codigo or not codigo.isdigit() or len(codigo) < 10:
                omitidos += 1
                continue

            descripcion = str(row.get(col_descripcion) or "").strip() if col_descripcion else ""
            if not descripcion:
                omitidos += 1
                continue

            # IVA
            impuesto_raw = row.get(col_impuesto) if col_impuesto else None
            try:
                impuesto = (
                    float(str(impuesto_raw).replace(",", "."))
                    if impuesto_raw is not None and str(impuesto_raw).strip() not in ("", "nan")
                    else None
                )
            except (ValueError, TypeError):
                impuesto = None

            # Tipo: bien o servicio a partir de la descripción de Categoría 1
            cat1_desc = str(row.get(col_cat1_desc) or "").lower() if col_cat1_desc else ""
            if any(kw in cat1_desc for kw in ["servicio", "actividad", "service"]):
                tipo = "servicio"
            else:
                tipo = _tipo_por_capitulo(codigo)  # heurística por capítulo como fallback

            records.append({
                "codigo":        codigo,
                "descripcion":   descripcion,
                "impuesto":      impuesto,
                "tipo":          tipo,
                "capitulo":      codigo[:2] if len(codigo) >= 2 else "",
                "unidad_medida": "",
                "_ts":           ts,
            })

            if progress_callback and len(records) % 500 == 0:
                progress_callback(len(records), total_filas)

        # ---- Insertar en lotes de 1000 ------------------------------------
        antes = self.get_stats()["total_codigos"]
        BATCH = 1000
        insertados = 0
        for start in range(0, len(records), BATCH):
            self._cache_put_batch(records[start : start + BATCH])
            insertados += min(BATCH, len(records) - start)
            if progress_callback:
                progress_callback(insertados, len(records))

        if progress_callback:
            progress_callback(insertados, insertados)

        despues = self.get_stats()["total_codigos"]
        resultado = {
            "insertados":  insertados,
            "omitidos":    omitidos,
            "total_filas": total_filas,
            "nuevos":      despues - antes,
            "hoja":        hoja,
        }
        LOGGER.info("Excel CABYS importado: %d insertados, %d nuevos, %d omitidos",
                    insertados, despues - antes, omitidos)
        return resultado

    @staticmethod
    def _find_cabys_col(df, cols: list) -> str | None:
        """
        Encuentra la columna que contiene los códigos CABYS de 13 dígitos.
        Muestrea las primeras filas y busca la columna con valores numéricos largos.
        """
        import pandas as pd
        sample = df.head(50)
        for col in reversed(cols):   # el código real está en las últimas columnas
            vals = sample[col].dropna().astype(str)
            vals = vals.str.replace(r"\.0$", "", regex=True).str.strip()
            numeric_long = vals[vals.str.match(r"^\d{10,13}$")]
            if len(numeric_long) >= 3:
                return col
        return None

    @staticmethod
    def _find_desc_col(cols: list, cabys_col: str | None) -> str | None:
        """Retorna la columna de descripción que sigue al código CABYS."""
        if cabys_col is None or cabys_col not in cols:
            return None
        idx = cols.index(cabys_col)
        if idx + 1 < len(cols):
            return cols[idx + 1]
        return None

    # ------------------------------------------------------------------
    # Utilidades públicas
    # ------------------------------------------------------------------
    def get_stats(self) -> dict[str, Any]:
        """Estadísticas de la BD local."""
        with self._db_lock:
            with contextlib.closing(sqlite3.connect(self._db_path)) as conn:
                total = conn.execute("SELECT COUNT(*) FROM cabys").fetchone()[0]
                last_ts = conn.execute("SELECT MAX(updated_at) FROM cabys").fetchone()[0]
                bienes = conn.execute(
                    "SELECT COUNT(*) FROM cabys WHERE tipo = 'bien'"
                ).fetchone()[0]
                servicios = conn.execute(
                    "SELECT COUNT(*) FROM cabys WHERE tipo = 'servicio'"
                ).fetchone()[0]

        from datetime import datetime
        last_str = (
            datetime.fromtimestamp(last_ts).strftime("%d/%m/%Y %H:%M")
            if last_ts else "Nunca"
        )
        return {
            "total_codigos": total,
            "bienes": bienes,
            "servicios": servicios,
            "sin_tipo": total - bienes - servicios,
            "ultima_actualizacion": last_str,
            "db_path": str(self._db_path),
            "ready": total > 0,
        }

    def is_ready(self) -> bool:
        """True si la BD tiene al menos un registro."""
        with self._db_lock:
            with contextlib.closing(sqlite3.connect(self._db_path)) as conn:
                count = conn.execute("SELECT COUNT(*) FROM cabys").fetchone()[0]
        return count > 0
