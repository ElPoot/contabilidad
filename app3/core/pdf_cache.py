"""Caché de rutas de PDFs para optimizar carga.

Guarda un índice de PDFs escaneados con sus rutas, timestamps y checksums.
La próxima carga solo re-escanea PDFs nuevos o modificados.
"""

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class PDFCacheManager:
    """Gestiona caché de rutas de PDFs para evitar re-escaneos."""

    def __init__(self, cache_file: Path):
        """
        Args:
            cache_file: Ruta al archivo JSON del caché (ej: .metadata/pdf_cache.json)
        """
        self.cache_file = cache_file
        self.cache: dict = self._load_cache()

    def _load_cache(self) -> dict:
        """Cargar caché desde archivo JSON."""
        if not self.cache_file.exists():
            return {"version": "1", "pdfs": {}}

        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                cache = json.load(f)
            logger.info(f"Caché de PDFs cargado: {len(cache.get('pdfs', {}))} entradas")
            return cache
        except Exception as exc:
            logger.warning(f"No se pudo cargar caché: {exc}. Empezando nuevo.")
            return {"version": "1", "pdfs": {}}

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

        Args:
            pdf_file: Archivo PDF a verificar

        Returns:
            Ruta cacheada si es válida, None si necesita re-escaneo
        """
        filename = pdf_file.name
        entry = self.cache.get("pdfs", {}).get(filename)

        if not entry:
            return None

        # Verificar que el archivo aún existe
        cached_path = Path(entry.get("path", ""))
        if not cached_path.exists():
            return None

        # Verificar checksum (detectar cambios)
        stored_checksum = entry.get("checksum")
        if stored_checksum:
            current_checksum = self._compute_checksum(pdf_file)
            if current_checksum != stored_checksum:
                return None  # Archivo cambió, re-escanear

        return cached_path

    def get_cached_clave(self, pdf_filename: str) -> str | None:
        """
        Obtener clave de factura asociada a un PDF cacheado.

        Args:
            pdf_filename: Nombre del archivo PDF

        Returns:
            Clave de factura (50 dígitos) si existe, None si no se guardó
        """
        entry = self.cache.get("pdfs", {}).get(pdf_filename)
        if entry:
            return entry.get("clave")
        return None

    def add_to_cache(self, pdf_file: Path, clave: str = "") -> None:
        """
        Agregar o actualizar entrada de caché.

        Args:
            pdf_file: Ruta completa del PDF
            clave: Clave de factura asociada (50 dígitos), opcional
        """
        filename = pdf_file.name
        checksum = self._compute_checksum(pdf_file)

        entry = {
            "path": str(pdf_file),
            "checksum": checksum,
            "timestamp": datetime.now().isoformat(),
        }
        if clave:
            entry["clave"] = clave

        self.cache.setdefault("pdfs", {})[filename] = entry

    def remove_from_cache(self, pdf_filename: str) -> None:
        """Remover entrada del caché."""
        if "pdfs" in self.cache and pdf_filename in self.cache["pdfs"]:
            del self.cache["pdfs"][pdf_filename]
            logger.debug(f"Removido del caché: {pdf_filename}")

    def clear_cache(self) -> None:
        """Limpiar todo el caché."""
        self.cache = {"version": "1", "pdfs": {}}
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
