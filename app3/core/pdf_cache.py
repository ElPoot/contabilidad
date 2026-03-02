"""Caché de rutas de PDFs para optimizar carga.

Guarda un índice de PDFs escaneados con sus rutas, timestamps y checksums.
La próxima carga solo re-escanea PDFs nuevos o modificados.

Cache keys use POSIX-normalized paths relative to pdf_root to avoid
collisions between PDFs with the same filename in different subdirectories.
"""

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class PDFCacheManager:
    """Gestiona caché de rutas de PDFs para evitar re-escaneos."""

    def __init__(self, cache_file: Path, pdf_root: Path | None = None):
        """
        Args:
            cache_file: Ruta al archivo JSON del caché (ej: .metadata/pdf_cache.json)
            pdf_root: Raíz de la carpeta PDF del cliente.  When set, cache
                      keys are relative POSIX paths (e.g. "Sender/file.pdf")
                      instead of bare filenames, avoiding collisions.
        """
        self.cache_file = cache_file
        self.pdf_root = pdf_root
        self.cache: dict = self._load_cache()

    # ── Key helpers ──

    def _make_key(self, pdf_file: Path) -> str:
        """Cache key = POSIX relative path from pdf_root.

        Falls back to bare filename when pdf_root is not set or the file
        is not under pdf_root (should not happen in practice).
        """
        if self.pdf_root:
            try:
                return pdf_file.relative_to(self.pdf_root).as_posix()
            except ValueError:
                pass
        return pdf_file.name

    def _get_entry(self, pdf_file: Path) -> dict | None:
        """Retrieve cache entry, with automatic legacy-key migration.

        Old caches used bare filenames as keys.  When we find a legacy key
        whose stored ``path`` matches *this* pdf_file, we migrate it to the
        new relative-path key in-place (dict mutation, persisted on next
        save_cache).  If the stored path does NOT match, the legacy entry
        belongs to a *different* file with the same name — we leave it alone
        and return None so this file gets re-scanned (correct behavior).
        """
        pdfs = self.cache.get("pdfs", {})
        key = self._make_key(pdf_file)
        entry = pdfs.get(key)
        if entry is not None:
            return entry

        # Legacy fallback: old caches used filename-only keys
        legacy_key = pdf_file.name
        if legacy_key != key and legacy_key in pdfs:
            legacy_entry = pdfs[legacy_key]
            # Only migrate if stored path matches this exact file
            stored_path = legacy_entry.get("path", "")
            if stored_path and Path(stored_path) == pdf_file:
                pdfs[key] = legacy_entry
                del pdfs[legacy_key]
                logger.debug("Cache migrado: %s → %s", legacy_key, key)
                return legacy_entry
        return None

    # ── Public API ──

    def _load_cache(self) -> dict:
        """Cargar caché desde archivo JSON."""
        if not self.cache_file.exists():
            return {"version": "2", "pdfs": {}}

        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                cache = json.load(f)
            logger.info(f"Caché de PDFs cargado: {len(cache.get('pdfs', {}))} entradas")
            return cache
        except Exception as exc:
            logger.warning(f"No se pudo cargar caché: {exc}. Empezando nuevo.")
            return {"version": "2", "pdfs": {}}

    def save_cache(self) -> None:
        """Guardar caché a archivo JSON."""
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, default=str)
            logger.debug(f"Caché de PDFs guardado: {len(self.cache.get('pdfs', {}))} entradas")
        except Exception as exc:
            logger.warning(f"No se pudo guardar caché: {exc}")

    def get_cached_path(self, pdf_file: Path) -> Path | None:
        """
        Obtener ruta cacheada si el archivo aún existe y no cambió.

        Uses fast validation (file size + mtime) instead of full MD5 to avoid
        reading every cached PDF over the network on each load.  Full MD5 is
        only computed when the fast check is inconclusive.

        Args:
            pdf_file: Archivo PDF a verificar

        Returns:
            Ruta cacheada si es válida, None si necesita re-escaneo
        """
        entry = self._get_entry(pdf_file)

        if not entry:
            return None

        # Verificar que el archivo aún existe
        cached_path = Path(entry.get("path", ""))
        if not cached_path.exists():
            return None

        # ── Fast validation: size + mtime ──
        try:
            stat = pdf_file.stat()
        except OSError:
            return None

        stored_size = entry.get("size")
        stored_mtime = entry.get("mtime")

        if stored_size is not None and stored_mtime is not None:
            # Fast path: if size AND mtime both match, trust the cache
            if stat.st_size == stored_size and abs(stat.st_mtime - stored_mtime) < 0.01:
                return cached_path

            # Size changed → content definitely changed
            if stat.st_size != stored_size:
                return None

            # Size same but mtime differs (network drive quirk, copy, touch) →
            # lazy MD5 recheck before invalidating
            stored_checksum = entry.get("checksum")
            if stored_checksum:
                current_checksum = self._compute_checksum(pdf_file)
                if current_checksum == stored_checksum:
                    # Content unchanged — update mtime in cache to avoid
                    # future MD5 rechecks for this entry
                    entry["mtime"] = stat.st_mtime
                    return cached_path
                return None  # Content actually changed

            # No checksum stored and mtime differs → assume changed
            return None

        # Legacy entry without size/mtime: fall back to MD5 (one-time migration)
        stored_checksum = entry.get("checksum")
        if stored_checksum:
            current_checksum = self._compute_checksum(pdf_file)
            if current_checksum != stored_checksum:
                return None  # Archivo cambió, re-escanear

        return cached_path

    def get_cached_clave(self, pdf_file: Path) -> str | None:
        """
        Obtener clave de factura asociada a un PDF cacheado.

        Args:
            pdf_file: Path del archivo PDF

        Returns:
            Clave de factura (50 dígitos) si existe, None si no se guardó
        """
        entry = self._get_entry(pdf_file)
        if entry:
            return entry.get("clave")
        return None

    def get_cached_status(self, pdf_file: Path) -> str | None:
        """
        Obtener veredicto negativo cacheado de un PDF.

        Args:
            pdf_file: Path del archivo PDF

        Returns:
            Status string (e.g. "non_invoice") if a negative verdict was
            cached, None otherwise.
        """
        entry = self._get_entry(pdf_file)
        if entry:
            return entry.get("status")
        return None

    def add_to_cache(
        self,
        pdf_file: Path,
        clave: str = "",
        checksum: str = "",
        status: str = "",
    ) -> None:
        """
        Agregar o actualizar entrada de caché.

        Stores size+mtime for fast validation on subsequent loads, plus MD5
        as a secondary integrity check when size/mtime are ambiguous.

        Args:
            pdf_file: Ruta completa del PDF
            clave: Clave de factura asociada (50 dígitos), opcional
            checksum: Pre-computed MD5 hex digest.  When the caller already
                       has the file bytes in memory, it can hash them and pass
                       the result here to avoid a second full read over the
                       network.  If empty, the checksum is computed from disk.
            status: Negative verdict to cache permanently (only "non_invoice"
                    should be used here).  Transient failures (empty, timeout)
                    should NOT be cached — they get re-scanned next load.
        """
        key = self._make_key(pdf_file)

        try:
            stat = pdf_file.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            size = 0
            mtime = 0.0

        if not checksum:
            checksum = self._compute_checksum(pdf_file)

        entry = {
            "path": str(pdf_file),
            "size": size,
            "mtime": mtime,
            "checksum": checksum,
            "timestamp": datetime.now().isoformat(),
        }
        if clave:
            entry["clave"] = clave
        if status:
            entry["status"] = status

        self.cache.setdefault("pdfs", {})[key] = entry

    def remove_from_cache(self, pdf_file: Path) -> None:
        """Remover entrada del caché."""
        key = self._make_key(pdf_file)
        if "pdfs" in self.cache and key in self.cache["pdfs"]:
            del self.cache["pdfs"][key]
            logger.debug(f"Removido del caché: {key}")

    def clear_cache(self) -> None:
        """Limpiar todo el caché."""
        self.cache = {"version": "2", "pdfs": {}}
        logger.info("Caché de PDFs limpiado")

    @staticmethod
    def _compute_checksum(pdf_file: Path, chunk_size: int = 8192) -> str:
        """Computar checksum MD5 del archivo."""
        if not pdf_file.exists():
            return ""

        hash_md5 = hashlib.md5()
        try:
            with open(pdf_file, "rb") as f:
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as exc:
            logger.debug(f"No se pudo computar checksum de {pdf_file.name}: {exc}")
            return ""
