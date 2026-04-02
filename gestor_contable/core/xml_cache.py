"""Cache SQLite para resultados parseados de XMLs.

Evita re-parsear XMLs que no cambiaron entre cargas. Clave = ruta relativa
desde xml_root. Invalida por mtime + size (mismo criterio que pdf_cache).
Solo cachea filas con _process_status == "ok" — errores siempre se re-intentan.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class XMLCacheManager:

    def __init__(self, cache_file: Path, xml_root: Path):
        self.cache_file = cache_file
        self.xml_root = xml_root
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(cache_file), check_same_thread=True)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS xml_cache (
                key       TEXT PRIMARY KEY,
                mtime     REAL NOT NULL,
                size      INTEGER NOT NULL,
                data_json TEXT NOT NULL,
                cached_at TEXT
            )
        """)
        self._conn.commit()

    def _make_key(self, xml_file: Path) -> str:
        try:
            return xml_file.relative_to(self.xml_root).as_posix()
        except ValueError:
            return xml_file.name

    def get(self, xml_file: Path) -> dict | None:
        """Retorna el row cacheado si el archivo no cambió, o None."""
        try:
            stat = xml_file.stat()
        except OSError:
            return None

        key = self._make_key(xml_file)
        row = self._conn.execute(
            "SELECT mtime, size, data_json FROM xml_cache WHERE key = ?", (key,)
        ).fetchone()

        if not row:
            return None

        mtime, size, data_json = row
        if abs(stat.st_mtime - mtime) < 0.01 and stat.st_size == size:
            try:
                return json.loads(data_json)
            except Exception:
                return None

        return None  # archivo cambió

    def put_batch(self, entries: list[tuple[Path, dict]]) -> None:
        """Inserta o reemplaza múltiples entradas en una sola transacción."""
        rows = []
        now = datetime.now(timezone.utc).isoformat()
        for xml_file, data in entries:
            try:
                stat = xml_file.stat()
            except OSError:
                continue
            key = self._make_key(xml_file)
            try:
                data_json = json.dumps(data, default=str)
            except Exception:
                continue
            rows.append((key, stat.st_mtime, stat.st_size, data_json, now))

        if rows:
            self._conn.executemany(
                "INSERT OR REPLACE INTO xml_cache (key, mtime, size, data_json, cached_at) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
            logger.debug("XML cache: %d entradas guardadas", len(rows))

    def load_all(self) -> dict[str, tuple[float, int, str]]:
        """Carga todas las entradas en memoria: {key: (mtime, size, data_json)}."""
        rows = self._conn.execute(
            "SELECT key, mtime, size, data_json FROM xml_cache"
        ).fetchall()
        return {row[0]: (row[1], row[2], row[3]) for row in rows}

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
