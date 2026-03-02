from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import logging
from pathlib import Path
import re
import time
from typing import Any

from .models import FacturaRecord
from .xml_manager import CRXMLManager
from .pdf_cache import PDFCacheManager

try:
    import fitz
except ModuleNotFoundError:  # pragma: no cover - dependencia opcional en runtime
    fitz = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

# ── Pre-compiled regex patterns (avoid recompilation per PDF) ──
_RE_CLAVE_50 = re.compile(r"(\d{50})")
_RE_DIGITS_15_PLUS = re.compile(r"\d{15,}")
_RE_NUMERIC_TOKENS = re.compile(r"\d+")
_RE_DIGITS_50_TEXT = re.compile(r"\d{50}")
_RE_DIGITS_10_20 = re.compile(r"\d{10,20}")
_RE_CLAVE_RAW_BYTES = re.compile(rb"506\d{47}")
_RE_NON_DIGIT = re.compile(r"\D")


def _extract_clave_from_filename(filename: str) -> str | None:
    """Extrae clave de 50 dígitos desde el nombre del PDF sin abrir el archivo."""
    match = _RE_CLAVE_50.search(str(filename or ""))
    return match.group(1) if match else None


def _extract_numeric_tokens(filename: str, min_len: int = 10) -> list[str]:
    """Extrae secuencias numéricas relevantes desde nombre de archivo."""
    return [token for token in _RE_NUMERIC_TOKENS.findall(filename or "") if len(token) >= min_len]


def _is_invoice_candidate(filename: str, pdf_file: Path | None = None) -> bool:
    """Heurística para distinguir comprobantes de PDFs administrativos.

    Un candidato a factura típicamente tiene:
    - Clave de 50 dígitos (506...) en el nombre
    - Palabras clave de comprobantes electrónicos
    - Secuencias numéricas largas SOLO si no viene de carpeta bancaria

    Args:
        filename: Nombre del archivo PDF
        pdf_file: Path completo (opcional) para verificar carpeta padre
    """
    name = (filename or "").lower()

    # Clave completa de 50 dígitos → definitivamente factura
    if _RE_CLAVE_50.search(name):
        return True

    # Palabras clave de comprobantes electrónicos
    invoice_keywords = (
        "factura", "fe", "nc", "nd", "credito", "debito",
        "tiquete", "tq", "remision", "rm", "comprobante",
        "electr", "electro",
    )
    if any(k in name for k in invoice_keywords):
        return True

    # Secuencias numéricas largas: only count if NOT from bancario path.
    # Bancario PDFs like "200010780484080.pdf" have 15+ digits but are
    # bank transaction IDs, not fiscal claves or consecutivos.
    if _RE_DIGITS_15_PLUS.search(name):
        if pdf_file and _is_bancario_path(pdf_file):
            return False
        return True

    return False


def _is_clearly_non_invoice_filename(filename: str) -> bool:
    """Detecta adjuntos administrativos que no vale la pena extraer en profundidad.

    Excluye documentos que claramente NO son comprobantes fiscales.
    Palabras clave expandidas para descartar: marketing, administrativos, órdenes, etc.
    """
    name = (filename or "").lower()

    # Palabras clave que indican NO-comprobante
    non_invoice_keywords = (
        # Marketing/Promocionales
        "brochure", "catalogo", "promocion", "oferta", "descuento",

        # Comunicados/Administrativos
        "comunicado", "aviso", "noticia", "boletin", "circular",

        # Ordenes/Solicitudes (NO facturas)
        "orden de compra", "order", "pedido", "detallepedido",
        "requisicion", "solicitud", "request",

        # Cambios de operador/proveedor
        "cambio de comercializador", "cambio operador", "cambio de proveedor",

        # Documentos administrativos
        "manual", "guia", "instructivo", "politica", "terminosy", "resolucion",
        "reglamento", "contrato",

        # Recibos de otro tipo (NO electrónicos)
        "recibo manual", "ticket manual", "recibo deposito", "constancia",

        # Reportes/Informes (NO comprobantes)
        "reporte", "informe", "resumen", "estado de cuenta", "extracto",

        # Bancarios / Institucionales
        "comprobante de registro de planilla", "soporte sinpe",
        "notificacion", "comprobante transferencia",

        # Otros
        "carta", "oficio", "memo", "memorandum", "junk", "basura", "spam",
    )

    if any(k in name for k in non_invoice_keywords):
        return True

    # Prefijos bancarios conocidos (RR=recibo recurrente, RD=recibo débito)
    stem = name.rsplit(".", 1)[0] if "." in name else name
    bancario_prefixes = ("rr", "rd")
    if any(stem.startswith(p) and len(stem) > 2 and stem[2:].replace(" ", "").replace("_", "").replace("-", "").isdigit() for p in bancario_prefixes):
        return True

    return False


# ── Bancario / institutional path patterns ──
# Folders that contain bank notifications, SINPE receipts, etc.
# These PDFs are never fiscal invoices — skip them entirely.
_BANCARIO_FOLDER_PATTERNS = (
    "bn email comercios",
    "notificacionescajerovirtual",
    "notificaciones cajero virtual",
    "bncr",
    "sinpe",
    "banco nacional",
    "banco de costa rica",
    "bac san jose",
    "bac credomatic",
    "scotiabank",
    "davivienda",
    "promerica",
)


def _is_bancario_path(pdf_file: Path) -> bool:
    """Check if a PDF lives inside a known bancario/institutional folder.

    Looks at parent folder names (up to 3 levels) for known bank patterns.
    """
    parts_lower = [p.lower() for p in pdf_file.parts[-4:-1]]  # up to 3 parent folders
    for part in parts_lower:
        for pattern in _BANCARIO_FOLDER_PATTERNS:
            if pattern in part:
                return True
    return False


def _extract_consecutivo_from_clave(clave: str) -> str | None:
    """Extrae consecutivo (20 dígitos) desde clave Hacienda de 50 dígitos.

    Formato clave CR (50):
    - 0:3   país (506)
    - 3:9   fecha (ddmmyy)
    - 9:21  cédula emisor (12)
    - 21:41 consecutivo (20)
    - 41:42 situación (1)
    - 42:50 seguridad (8)
    """
    raw = (clave or "").strip()
    if len(raw) != 50 or not raw.isdigit():
        return None
    return raw[21:41]


def _extract_emisor_from_clave(clave: str) -> str | None:
    """Extrae cédula emisor (12 dígitos) desde clave Hacienda de 50 dígitos."""
    raw = (clave or "").strip()
    if len(raw) != 50 or not raw.isdigit():
        return None
    return raw[9:21]


def _normalize_digits(text: str) -> str:
    """Retorna únicamente dígitos de un texto."""
    return _RE_NON_DIGIT.sub("", text or "")


def _resolve_record_key_from_extracted_clave(clave: str, consecutivo_index: dict[str, str]) -> str | None:
    """Si una clave extraída no existe en records, intenta mapearla por consecutivo oficial."""
    consecutivo = _extract_consecutivo_from_clave(clave)
    if not consecutivo:
        return None
    if consecutivo in consecutivo_index:
        return consecutivo_index[consecutivo]
    return None


class FacturaIndexer:
    """
    Construye la lista de FacturaRecord para un cliente y periodo usando
    toda la lógica existente de App 1 y App 2.
    """

    def __init__(self) -> None:
        self.xml_manager = CRXMLManager()
        self.parse_errors: list[str] = []
        self.audit_report: dict = {}
        self.pdf_cache: PDFCacheManager | None = None  # Se inicializa en load_period()

    def load_period(
        self,
        client_folder: Path,
        from_date: str = "",
        to_date: str = "",
        include_pdf_scan: bool = True,
        allow_pdf_content_fallback: bool = True,
    ) -> list[FacturaRecord]:
        self.parse_errors = []
        self.audit_report = {}

        start_total = time.perf_counter()

        from_dt = self._parse_ui_date(from_date)
        to_dt = self._parse_ui_date(to_date)

        xml_root = client_folder / "XML"
        pdf_root = client_folder / "PDF"
        metadata_dir = client_folder / ".metadata"

        records: dict[str, FacturaRecord] = {}

        # ── CACHÉ DE PDFs ──
        cache_file = metadata_dir / "pdf_cache.json"
        self.pdf_cache = PDFCacheManager(cache_file, pdf_root=pdf_root)

        # ── PASO 1: XML ──
        start_xml = time.perf_counter()
        if xml_root.exists():
            try:
                df, audit = self.xml_manager.load_xml_folder(xml_root)
                self.audit_report = audit

                for failed in audit.get("failed_files", []):
                    self.parse_errors.append(
                        f"{failed.get('archivo', '?')}: {failed.get('error', 'error desconocido')}"
                    )

                if not df.empty:
                    for _, row in df.iterrows():
                        clave = str(row.get("clave_numerica") or "").strip()
                        if len(clave) != 50:
                            continue

                        fecha = str(row.get("fecha_emision") or "")
                        if not self._in_range(fecha, from_dt, to_dt):
                            continue

                        ruta_raw = row.get("ruta")
                        xml_path = Path(str(ruta_raw)) if ruta_raw else None

                        records[clave] = FacturaRecord(
                            clave=clave,
                            fecha_emision=fecha,
                            emisor_nombre=str(row.get("emisor_nombre") or ""),
                            emisor_cedula=str(row.get("emisor_cedula") or ""),
                            receptor_nombre=str(row.get("receptor_nombre") or ""),
                            receptor_cedula=str(row.get("receptor_cedula") or ""),
                            tipo_documento=str(row.get("tipo_documento") or ""),
                            consecutivo=str(row.get("consecutivo") or ""),
                            subtotal=str(row.get("subtotal") or ""),
                            iva_1=str(row.get("iva_1") or ""),
                            iva_2=str(row.get("iva_2") or ""),
                            iva_4=str(row.get("iva_4") or ""),
                            iva_8=str(row.get("iva_8") or ""),
                            iva_13=str(row.get("iva_13") or ""),
                            impuesto_total=str(row.get("impuesto_total") or ""),
                            total_comprobante=str(row.get("total_comprobante") or ""),
                            moneda=str(row.get("moneda") or ""),
                            tipo_cambio=str(row.get("tipo_cambio") or ""),
                            estado_hacienda=str(row.get("estado_hacienda") or ""),
                            xml_path=xml_path,
                            estado="pendiente",
                        )
            except Exception as exc:
                self.parse_errors.append(f"Error cargando carpeta XML: {exc}")
        xml_time = time.perf_counter() - start_xml
        logger.info(f"XML parsing: {xml_time:.2f}s → {len(records)} registros")

        # ── PASO 2: PDFs ──
        if include_pdf_scan:
            start_pdf = time.perf_counter()
            pdf_scan_report = self._scan_and_link_pdfs_optimized(
                pdf_root,
                records,
                allow_pdf_content_fallback=allow_pdf_content_fallback,
            )
            pdf_time = time.perf_counter() - start_pdf
            self.audit_report["pdf_scan"] = pdf_scan_report.get("audit", {})
            logger.info(f"PDF scanning: {pdf_time:.2f}s")

            # ── Crear registros dummy para PDFs omitidos (sin clave) ──
            omitidos = pdf_scan_report.get("omitidos", {})
            logger.info(f"Creando {len(omitidos)} registros dummy para PDFs omitidos")
            for pdf_filename, omit_info in omitidos.items():
                razon = omit_info.get("razon", "desconocido")
                # Buscar el archivo PDF en la carpeta recursivamente
                pdf_path = None
                if pdf_root.exists():
                    # Buscar por nombre exacto en subdirectorios
                    for pdf_file in pdf_root.rglob("*.pdf"):
                        if pdf_file.name == pdf_filename:
                            pdf_path = pdf_file
                            break

                # Crear un registro dummy para el PDF omitido
                dummy_clave = f"OMITIDO_{pdf_filename.replace('.pdf', '').replace(' ', '_')}"
                razon_final = razon if razon in ("non_invoice", "timeout", "extract_failed") else "non_invoice"
                dummy_record = FacturaRecord(
                    clave=dummy_clave,
                    fecha_emision="",
                    emisor_nombre="[PDF omitido]",
                    receptor_nombre="[PDF omitido]",
                    pdf_path=pdf_path,
                    estado="sin_xml",
                    razon_omisión=razon_final,
                )
                records[dummy_clave] = dummy_record
                logger.debug(f"Registro dummy creado: {dummy_clave} | razon={razon_final} | tiene_pdf={pdf_path is not None}")

        # ── RECOMPUTE ──
        start_recompute = time.perf_counter()
        self._recompute_states(records)
        recompute_time = time.perf_counter() - start_recompute
        logger.info(f"State recomputation: {recompute_time:.2f}s")

        total_time = time.perf_counter() - start_total
        logger.info(f"load_period() TOTAL: {total_time:.2f}s")

        return sorted(records.values(), key=lambda r: (r.fecha_emision, r.clave))

    def link_pdfs_for_records(
        self,
        client_folder: Path,
        records: list[FacturaRecord],
        allow_pdf_content_fallback: bool = True,
    ) -> list[FacturaRecord]:
        """Enriquece una lista ya cargada con vínculos PDF sin reprocesar XML."""
        record_map = {r.clave: r for r in records if r.clave}
        self._scan_and_link_pdfs_optimized(
            client_folder / "PDF",
            record_map,
            allow_pdf_content_fallback=allow_pdf_content_fallback,
        )
        self._recompute_states(record_map)
        return sorted(record_map.values(), key=lambda r: (r.fecha_emision, r.clave))

    def _scan_and_link_pdfs(
        self,
        pdf_root: Path,
        records: dict[str, FacturaRecord],
        allow_pdf_content_fallback: bool = True,
    ) -> None:
        self._scan_and_link_pdfs_optimized(
            pdf_root,
            records,
            allow_pdf_content_fallback=allow_pdf_content_fallback,
        )

    def _scan_and_link_pdfs_optimized(
        self,
        pdf_root: Path,
        records: dict[str, FacturaRecord],
        allow_pdf_content_fallback: bool = True,
        timeout_seconds: int = 4,  # 4s: captura todas las facturas sin perder velocidad (~40-42s)
        max_workers: int = 24,      # 24 workers: network-I/O bound from Z:/, benefits from more concurrency
    ) -> dict[str, Any]:
        """
        Vincula PDFs a registros de factura con paralelismo y auditoría.

        Example:
            >>> indexer = FacturaIndexer()
            >>> report = indexer._scan_and_link_pdfs_optimized(Path("/tmp/PDF"), {})
            >>> sorted(report.keys())
            ['audit', 'linked', 'omitidos']
        """
        base_audit = {
            "total_procesados": 0,
            "exitosos": 0,
            "omitidos": 0,
            "tiempo_total_segundos": 0.0,
            "velocidad_promedio_ms_por_pdf": 0.0,
            "porcentaje_omitidos": 0.0,
            "picos": {
                "pdf_mas_lento": ("", 0),
                "pdf_mas_grande_mb": ("", 0.0),
            },
        }
        if not pdf_root.exists():
            return {"linked": {}, "omitidos": {}, "audit": base_audit}

        all_pdf_files = [p for p in pdf_root.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"]
        total_files = len(all_pdf_files)
        if not all_pdf_files:
            return {"linked": {}, "omitidos": {}, "audit": base_audit}

        # ── FILTRAR POR CACHÉ ──
        # PDFs que están en caché y no cambiaron: no necesitan escaneo
        # Keys use pdf_file (Path) to avoid filename collisions across subfolders.
        cached_pdfs: dict[Path, Path] = {}  # {pdf_file → cached_path}
        cached_pdf_claves: dict[Path, str] = {}  # {pdf_file → clave}
        pdfs_to_scan: list[Path] = []

        cached_negative_verdicts: dict[Path, str] = {}  # {pdf_file → status}

        if self.pdf_cache:
            for pdf_file in all_pdf_files:
                cached_path = self.pdf_cache.get_cached_path(pdf_file)
                if cached_path:
                    # PDF está en caché y no cambió - verificar si tiene clave asociada
                    cached_clave = self.pdf_cache.get_cached_clave(pdf_file)
                    if cached_clave:
                        # Tiene clave → fue vinculado exitosamente, usar del caché
                        cached_pdfs[pdf_file] = cached_path
                        cached_pdf_claves[pdf_file] = cached_clave
                        continue

                    # Check for cached negative verdict — ONLY non_invoice is permanent.
                    # empty/timeout/extract_failed are transient and get re-scanned.
                    cached_status = self.pdf_cache.get_cached_status(pdf_file)
                    if cached_status == "non_invoice":
                        cached_negative_verdicts[pdf_file] = cached_status
                        continue

                    # No clave, no permanent negative verdict → re-process
                    pdfs_to_scan.append(pdf_file)
                else:
                    # PDF es nuevo o cambió
                    pdfs_to_scan.append(pdf_file)
        else:
            pdfs_to_scan = all_pdf_files

        # Sort by size ascending: small invoices finish fast, large non-invoices
        # don't block the thread pool.  Wrapped in try/except because stat()
        # can fail on network drives even if the file exists.
        def _safe_size(p: Path) -> int:
            try:
                return p.stat().st_size
            except OSError:
                return 0
        pdfs_to_scan.sort(key=_safe_size)

        cached_count = len(cached_pdfs)
        cached_neg_count = len(cached_negative_verdicts)
        scan_count = len(pdfs_to_scan)
        if cached_count > 0 or cached_neg_count > 0:
            logger.info(
                f"Caché de PDFs: {cached_count} reutilizados, "
                f"{cached_neg_count} negativos (skip), {scan_count} por escanear"
            )

        started = time.perf_counter()
        linked: dict[str, Path] = {}
        omitidos: dict[str, dict[str, Any]] = {}
        diagnostics_sin_clave: list[dict[str, Any]] = []
        pdf_checksums: dict[Path, str] = {}  # in-memory checksums from workers
        max_slow_name = ""
        max_slow_ms = 0
        max_size_name = ""
        max_size_mb = 0.0

        consecutivo_index = self._build_consecutivo_index(records)
        if pdfs_to_scan:
            logger.info("Escaneando %s PDFs en %s (+ %s del caché)", scan_count, pdf_root, cached_count)
            logger.info("ThreadPoolExecutor lanzado: %s workers", max(1, min(max_workers, scan_count)))

        # Register cached negative verdicts as omitidos.
        # "Last chance" pre-link: if filename tokens NOW resolve against
        # consecutivo_index (e.g. new XMLs loaded since last cache), rescue
        # the PDF instead of skipping it.
        for pdf_file, neg_status in cached_negative_verdicts.items():
            rescued_clave = self._resolve_clave_from_filename_tokens(
                pdf_file.name, consecutivo_index,
            )
            if rescued_clave and rescued_clave in records:
                # Rescued! Link it and invalidate the cached verdict
                linked[rescued_clave] = pdf_file
                records[rescued_clave].pdf_path = pdf_file
                if self.pdf_cache:
                    # Re-cache with clave, remove negative status
                    self.pdf_cache.add_to_cache(pdf_file, clave=rescued_clave)
                logger.info("PDF rescatado del caché negativo: %s → %s", pdf_file.name, rescued_clave)
            else:
                omitidos[pdf_file.name] = {
                    "razon": neg_status,
                    "error": "Veredicto cacheado",
                    "intento": 0,
                }
                # Log cached negatives for visibility in diagnostics
                filename_tokens = _extract_numeric_tokens(pdf_file.name)
                diagnostics_sin_clave.append(
                    {
                        "archivo": str(pdf_file),
                        "razon": neg_status,
                        "error": "Veredicto cacheado (no reattempt)",
                        "intento": 0,
                        "tokens_nombre": filename_tokens[:10],
                        "tokens_texto": [],
                        "claves_50_detectadas": [],
                        "tiempo_ms": 0,
                    }
                )

        # Procesar PDFs del caché: vincular con sus claves asociadas
        for pdf_file, pdf_path in cached_pdfs.items():
            clave = cached_pdf_claves.get(pdf_file)
            if clave and clave in records:
                # Vincular a registro existente usando clave guardada
                linked[clave] = pdf_path
                records[clave].pdf_path = pdf_path
                logger.debug(f"PDF cacheado: {pdf_file.name} → {clave} (VINCULADO)")
            else:
                # Sin clave guardada - usar para reconciliación posterior
                pass  # Se procesará en _reconcile_missing_with_filename_consecutivo

        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, scan_count))) as executor:
            future_map = {
                executor.submit(
                    self._process_single_pdf,
                    pdf_file,
                    allow_pdf_content_fallback,
                    timeout_seconds,
                    consecutivo_index,
                ): pdf_file
                for pdf_file in pdfs_to_scan
            }
            processed_count = 0
            for future in as_completed(future_map):
                pdf_file = future_map[future]
                processed_count += 1

                # Log de progreso cada 50 PDFs
                if processed_count % 50 == 0:
                    logger.debug(f"PDF scan progreso: {processed_count}/{scan_count}")

                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover
                    logger.exception("Error no controlado procesando PDF %s", pdf_file)
                    omitidos[pdf_file.name] = {"razon": "extract_failed", "error": str(exc), "intento": 1}
                    continue

                elapsed_ms = int(result.get("tiempo_ms", 0))
                size_mb = float(result.get("size_mb", 0.0))
                # Capture in-memory checksum to avoid re-reading from Z:/
                result_checksum = result.get("checksum", "")
                if result_checksum:
                    pdf_checksums[pdf_file] = result_checksum
                if elapsed_ms > max_slow_ms:
                    max_slow_ms = elapsed_ms
                    max_slow_name = pdf_file.name
                if size_mb > max_size_mb:
                    max_size_mb = size_mb
                    max_size_name = pdf_file.name

                clave = result.get("clave")
                metodo = str(result.get("metodo") or "")

                # ── Validate raw_bytes claves against known records ──
                # Raw bytes can match noise in compressed PDF streams.
                # If the clave doesn't exist in records and can't be resolved
                # via consecutivo_index, discard it and let downstream
                # fallbacks (filename_consecutivo, text_tokens) try instead.
                if clave and metodo == "raw_bytes":
                    if clave not in records:
                        resolved = _resolve_record_key_from_extracted_clave(clave, consecutivo_index)
                        if not resolved:
                            logger.debug(
                                "PDF: %s → raw_bytes clave %s not in records, discarding",
                                pdf_file.name, clave,
                            )
                            clave = None
                            metodo = ""

                if clave and clave not in records:
                    clave_por_consecutivo = _resolve_record_key_from_extracted_clave(clave, consecutivo_index)
                    if clave_por_consecutivo:
                        clave = clave_por_consecutivo
                        metodo = "clave_extraida_mapeada_por_consecutivo"

                if clave and clave not in records:
                    for candidate in result.get("claves_detectadas", []):
                        if candidate in records:
                            clave = candidate
                            metodo = "contenido_clave_en_records"
                            break

                if not clave:
                    for candidate in result.get("claves_detectadas", []):
                        if candidate in records:
                            clave = candidate
                            metodo = "contenido_clave_en_records"
                            break

                if not clave:
                    # fallback fuerte por consecutivo presente en nombre, contra XMLs ya cargados
                    clave = self._resolve_clave_from_filename_tokens(pdf_file.name, consecutivo_index)
                    if clave:
                        metodo = "filename_consecutivo"

                if not clave:
                    text_tokens = result.get("text_tokens") or []
                    clave = self._resolve_clave_from_tokens(text_tokens, consecutivo_index)
                    if clave:
                        metodo = "contenido_consecutivo"

                if clave:
                    linked[clave] = pdf_file
                    if clave in records:
                        records[clave].pdf_path = pdf_file
                    else:
                        records[clave] = FacturaRecord(clave=clave, pdf_path=pdf_file, estado="sin_xml")
                    logger.debug("PDF: %s → FOUND EN %s (%sms)", pdf_file.name, metodo.upper(), elapsed_ms)
                    continue

                reason = str(result.get("razon", "extract_failed"))
                message = str(result.get("error") or "")

                filename_tokens = _extract_numeric_tokens(pdf_file.stem, min_len=10)

                # ── Reclassify unlinked PDFs as non_invoice ──
                # If a PDF fails (timeout/extract_failed) AND has no tokens
                # that map to any known XML record, it's almost certainly not
                # an invoice.  Counting it as "omitidos factura" inflates the
                # error rate with bancarios/comunicados/manuales.
                if reason in ("extract_failed", "timeout"):
                    can_map_by_name = bool(
                        self._resolve_clave_from_filename_tokens(pdf_file.name, consecutivo_index)
                    )
                    is_candidate = _is_invoice_candidate(pdf_file.name, pdf_file)
                    if not can_map_by_name and not is_candidate:
                        reason = "non_invoice"
                    # Bancario path → always reclassify if can't map by name
                    elif not can_map_by_name and _is_bancario_path(pdf_file):
                        reason = "non_invoice"

                if reason == "timeout":
                    logger.warning("PDF: %s → timeout (%sms) - omitido", pdf_file.name, elapsed_ms)
                elif reason == "corrupted":
                    logger.error("PDF: %s → corrupted (%s)", pdf_file.name, message)
                elif reason == "non_invoice":
                    logger.debug("PDF: %s → ignorado (no comprobante)", pdf_file.name)
                else:
                    logger.debug("PDF: %s → omitido (%s)", pdf_file.name, reason)
                text_tokens = result.get("text_tokens") or []
                claves_detectadas = result.get("claves_detectadas") or []
                diagnostics_sin_clave.append(
                    {
                        "archivo": str(pdf_file),
                        "razon": reason,
                        "error": message,
                        "intento": int(result.get("intento", 1)),
                        "tokens_nombre": filename_tokens[:10],
                        "tokens_texto": text_tokens[:10],
                        "claves_50_detectadas": claves_detectadas[:10],
                        "tiempo_ms": elapsed_ms,
                    }
                )

                omitidos[pdf_file.name] = {
                    "razon": reason,
                    "error": message,
                    "intento": int(result.get("intento", 1)),
                }

        self._reconcile_missing_with_filename_consecutivo(records, all_pdf_files, linked)

        total_time = time.perf_counter() - started
        successful = len(linked)
        ignored = sum(1 for detail in omitidos.values() if detail.get("razon") == "non_invoice")
        skipped = len(omitidos) - ignored
        candidate_total = max(total_files - ignored, 1)
        avg_ms = (total_time * 1000.0 / total_files) if total_files else 0.0
        omit_pct = (skipped / candidate_total * 100.0) if candidate_total else 0.0

        logger.info("Vinculadas %s/%s (%.1f%%) en %.2fs", successful, total_files, successful * 100.0 / total_files, total_time)
        logger.info("Omitidos factura: %s/%s (%.2f%%). Ignorados no factura: %s", skipped, candidate_total, omit_pct, ignored)

        claves_faltantes_pdf = sorted(
            clave for clave, record in records.items() if record.xml_path and not record.pdf_path
        )
        if claves_faltantes_pdf:
            logger.warning(
                "Claves con XML sin PDF vinculado: %s. Se listan para revisión manual.",
                len(claves_faltantes_pdf),
            )
            for clave in claves_faltantes_pdf:
                logger.warning("CLAVE SIN PDF: %s", clave)

        if diagnostics_sin_clave:
            logger.warning("ANALISIS DETALLADO PDFs SIN CLAVE (%s):", len(diagnostics_sin_clave))
            for item in diagnostics_sin_clave:
                logger.warning(
                    "SIN_CLAVE | archivo=%s | razon=%s | intento=%s | tiempo_ms=%s | tokens_nombre=%s | tokens_texto=%s | claves_50=%s | error=%s",
                    item.get("archivo", ""),
                    item.get("razon", ""),
                    item.get("intento", 0),
                    item.get("tiempo_ms", 0),
                    item.get("tokens_nombre", []),
                    item.get("tokens_texto", []),
                    item.get("claves_50_detectadas", []),
                    item.get("error", ""),
                )

        if omit_pct > 1.0:
            logger.warning("Margen de error alto: %.2f%% omitidos (> 1%%).", omit_pct)

        # ── ACTUALIZAR CACHÉ ──
        if self.pdf_cache:
            # Build reverse index path→clave for O(1) lookup instead of O(n) scan
            path_to_clave: dict[Path, str] = {
                pdf_path: clave_candidate
                for clave_candidate, pdf_path in linked.items()
                if len(clave_candidate) == 50 and clave_candidate.isdigit()
            }

            # Build omitidos index: filename → reason for PERMANENT negative verdict caching.
            # Only "non_invoice" is permanent.  Transient failures (empty, timeout,
            # extract_failed) are NOT cached — they get re-scanned next load.
            omitidos_by_name: dict[str, str] = {
                fname: detail.get("razon", "")
                for fname, detail in omitidos.items()
                if detail.get("razon") == "non_invoice"
                and detail.get("intento", 1) != 0  # skip already-cached verdicts
            }

            for pdf_file in pdfs_to_scan:
                clave = path_to_clave.get(pdf_file, "")
                checksum = pdf_checksums.get(pdf_file, "")
                status = omitidos_by_name.get(pdf_file.name, "")
                self.pdf_cache.add_to_cache(pdf_file, clave, checksum=checksum, status=status)

            self.pdf_cache.save_cache()
            logger.info(f"Caché actualizado: {len(self.pdf_cache.cache.get('pdfs', {}))} PDFs cacheados")

        return {
            "linked": linked,
            "omitidos": omitidos,
            "audit": {
                "total_procesados": total_files,
                "exitosos": successful,
                "omitidos": skipped,
                "ignorados_no_factura": ignored,
                "total_candidatos_factura": candidate_total,
                "claves_faltantes_pdf": claves_faltantes_pdf,
                "diagnostico_sin_clave": diagnostics_sin_clave,
                "tiempo_total_segundos": round(total_time, 4),
                "velocidad_promedio_ms_por_pdf": round(avg_ms, 2),
                "porcentaje_omitidos": round(omit_pct, 2),
                "picos": {
                    "pdf_mas_lento": (max_slow_name, max_slow_ms),
                    "pdf_mas_grande_mb": (max_size_name, round(max_size_mb, 2)),
                },
            },
        }

    def _process_single_pdf(
        self,
        pdf_file: Path,
        allow_pdf_content_fallback: bool,
        timeout_seconds: int,
        consecutivo_index: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        clave = _extract_clave_from_filename(pdf_file.name)

        try:
            size_bytes = pdf_file.stat().st_size
        except PermissionError as exc:
            return {
                "clave": None,
                "razon": "permission_denied",
                "error": str(exc),
                "intento": 1,
                "tiempo_ms": int((time.perf_counter() - started) * 1000),
                "size_mb": 0.0,
            }

        size_mb = size_bytes / (1024 * 1024)
        if clave:
            # ✅ OPTIMIZACIÓN: Si ya encontramos la clave en el nombre,
            # NO extraemos contenido del PDF (ahorra ~30-40ms por PDF)
            return {
                "clave": clave,
                "metodo": "filename",
                "intento": 1,
                "tiempo_ms": int((time.perf_counter() - started) * 1000),
                "size_mb": size_mb,
            }

        if not allow_pdf_content_fallback:
            return {
                "clave": None,
                "razon": "extract_failed",
                "error": "Fallback de contenido deshabilitado.",
                "intento": 1,
                "tiempo_ms": int((time.perf_counter() - started) * 1000),
                "size_mb": size_mb,
            }

        # Para adjuntos claramente administrativos evitamos extracción costosa.
        if _is_clearly_non_invoice_filename(pdf_file.name):
            return {
                "clave": None,
                "razon": "non_invoice",
                "error": "Descartado por heurística de no comprobante.",
                "intento": 1,
                "tiempo_ms": int((time.perf_counter() - started) * 1000),
                "size_mb": size_mb,
            }

        # ── EARLY DISCARD: bancario/institutional folders ──
        # PDFs inside known bank folders (BN Email Comercios, etc.) are normally not
        # fiscal invoices. But before discarding, try raw bytes scan: if the filename
        # suggests a valid invoice (e.g. "3101172696_..."), it might be real and we
        # should link it, not discard permanently to cache.
        if _is_bancario_path(pdf_file):
            # Last chance 1: if filename tokens match a known consecutivo, link it
            if consecutivo_index:
                pre_clave = self._resolve_clave_from_filename_tokens(
                    pdf_file.name, consecutivo_index,
                )
                if pre_clave:
                    return {
                        "clave": pre_clave,
                        "metodo": "filename_consecutivo_pre",
                        "intento": 1,
                        "tiempo_ms": int((time.perf_counter() - started) * 1000),
                        "size_mb": size_mb,
                    }
            # Last chance 2: raw bytes clave scan (fast ~1-2ms, vs ~200ms fitz)
            # Only read if file size is reasonable and filename looks invoice-like
            try:
                pdf_data = self._read_pdf_bytes_streaming(pdf_file)
                raw_clave = self._try_raw_bytes_clave(pdf_data)
                if raw_clave:
                    return {
                        "clave": raw_clave,
                        "metodo": "raw_bytes_bancario",
                        "intento": 1,
                        "tiempo_ms": int((time.perf_counter() - started) * 1000),
                        "size_mb": size_mb,
                        "checksum": hashlib.md5(pdf_data).hexdigest()[:8],
                    }
            except Exception:
                pass  # Si falla la lectura de raw bytes, descartar normalmente
            # Confirmed: no clave found in raw bytes. Discard as bancario/non-invoice.
            return {
                "clave": None,
                "razon": "non_invoice",
                "error": "Descartado por ruta bancaria/institucional (raw_bytes sin clave).",
                "intento": 1,
                "tiempo_ms": int((time.perf_counter() - started) * 1000),
                "size_mb": size_mb,
            }

        # ── PRE-LINK by filename tokens vs consecutivo_index ──
        # If filename tokens uniquely match a known XML consecutivo,
        # we can link without reading the PDF at all (~0ms vs ~200-500ms).
        if consecutivo_index:
            pre_clave = self._resolve_clave_from_filename_tokens(
                pdf_file.name, consecutivo_index,
            )
            if pre_clave:
                return {
                    "clave": pre_clave,
                    "metodo": "filename_consecutivo_pre",
                    "intento": 1,
                    "tiempo_ms": int((time.perf_counter() - started) * 1000),
                    "size_mb": size_mb,
                }

        try:
            pdf_data = self._read_pdf_bytes_streaming(pdf_file)
        except PermissionError as exc:
            return {
                "clave": None,
                "razon": "permission_denied",
                "error": str(exc),
                "intento": 1,
                "tiempo_ms": int((time.perf_counter() - started) * 1000),
                "size_mb": size_mb,
            }
        except Exception as exc:
            return {
                "clave": None,
                "razon": "extract_failed",
                "error": str(exc),
                "intento": 1,
                "tiempo_ms": int((time.perf_counter() - started) * 1000),
                "size_mb": size_mb,
            }

        if not pdf_data:
            return {
                "clave": None,
                "razon": "empty",
                "error": "PDF vacío.",
                "intento": 1,
                "tiempo_ms": int((time.perf_counter() - started) * 1000),
                "size_mb": size_mb,
            }

        # Compute MD5 from in-memory bytes (cost: ~0ms, avoids re-read from Z:/)
        pdf_checksum = hashlib.md5(pdf_data).hexdigest()

        # ── RAW BYTES PRE-SCAN: search for 506+47 digits in raw PDF stream ──
        # Claves often appear as literal ASCII text in PDF content streams.
        # This avoids the cost of pdfplumber/fitz parsing entirely.
        # We validate structure (prefix 506 + valid date segment) to avoid
        # false positives from compressed streams or binary noise.
        # NOTE: The result is returned as a *candidate* — the caller in
        # _scan_and_link_pdfs_optimized verifies it against records/index
        # before final acceptance.  This eliminates false positives without
        # introducing false negatives.
        raw_clave = self._try_raw_bytes_clave(pdf_data)
        if raw_clave:
            return {
                "clave": raw_clave,
                "metodo": "raw_bytes",
                "intento": 1,
                "tiempo_ms": int((time.perf_counter() - started) * 1000),
                "size_mb": size_mb,
                "checksum": pdf_checksum,
            }

        attempts = 2
        clave_retry, retry_error, text_tokens, claves_detectadas = self._extract_clave_from_pdf_text(pdf_data)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if elapsed_ms > timeout_seconds * 1000:
            return {
                "clave": None,
                "razon": "timeout",
                "error": f"Superó timeout de {timeout_seconds}s.",
                "intento": attempts,
                "tiempo_ms": elapsed_ms,
                "size_mb": size_mb,
                "checksum": pdf_checksum,
            }

        if clave_retry:
            return {
                "clave": clave_retry,
                "metodo": "reintento_texto_pdf",
                "intento": attempts,
                "tiempo_ms": elapsed_ms,
                "size_mb": size_mb,
                "claves_detectadas": claves_detectadas,
                "checksum": pdf_checksum,
            }

        return {
            "clave": None,
            "razon": retry_error,
            "error": "Extractor de texto (fitz) no encontró clave de 50 digitos" if retry_error == "extract_failed" else "",
            "intento": attempts,
            "tiempo_ms": elapsed_ms,
            "size_mb": size_mb,
            "text_tokens": text_tokens,
            "claves_detectadas": claves_detectadas,
            "checksum": pdf_checksum,
        }

    @staticmethod
    def _read_pdf_bytes_streaming(
        pdf_file: Path,
        chunk_size: int = 1024 * 1024,
        _LARGE_THRESHOLD: int = 50 * 1024 * 1024,  # 50 MB
    ) -> bytes:
        """Lee un PDF completo.

        For typical invoice PDFs (< 50 MB), a single read_bytes() is faster
        than chunked streaming.  For very large files (> 50 MB, e.g. scanned
        catalogs), uses chunked reading to avoid a single massive allocation.
        """
        try:
            size = pdf_file.stat().st_size
        except OSError:
            size = 0

        if size <= _LARGE_THRESHOLD:
            return pdf_file.read_bytes()

        # Chunked streaming for very large files
        chunks = bytearray()
        with pdf_file.open("rb") as stream:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                chunks.extend(chunk)
        return bytes(chunks)

    @staticmethod
    def _try_raw_bytes_clave(pdf_data: bytes) -> str | None:
        """Search raw PDF bytes for a valid Costa Rican clave (506 + 47 digits).

        Validates structural segments of the clave to reject false positives
        from compressed streams or binary noise:
          - Positions 0:3   → "506" (country code, already matched by regex)
          - Positions 3:5   → day (01-31)
          - Positions 5:7   → month (01-12)
          - Positions 41:42 → situación comprobante (1-4)
        """
        for raw_match in _RE_CLAVE_RAW_BYTES.finditer(pdf_data):
            candidate = raw_match.group(0).decode("ascii")
            if len(candidate) != 50:
                continue

            # Validate date segment (ddmmyy at positions 3:9)
            day = int(candidate[3:5])
            month = int(candidate[5:7])
            if not (1 <= day <= 31 and 1 <= month <= 12):
                continue

            # Validate situación (position 41): must be 1-4
            situacion = int(candidate[41])
            if situacion < 1 or situacion > 4:
                continue

            return candidate
        return None

    @staticmethod
    def _extract_clave_from_pdf_text(
        pdf_data: bytes,
    ) -> tuple[str | None, str, list[str], list[str]]:
        """Reintento con PyMuPDF para detectar clave 50 dígitos y tokens numéricos útiles.

        Uses escalated page scanning to balance speed and completeness:
          1. Read first 3 pages (covers 99%+ of invoices).
          2. If not found and PDF has <= 10 pages, read pages 4-6.
          3. If still not found and PDF has <= 10 pages, read remaining pages
             (small PDFs are likely real invoices worth a full scan).
          4. PDFs with > 10 pages that fail after 6 pages are almost certainly
             not invoices — don't waste time reading 50+ pages of a catalog.
        """
        if fitz is None:
            return None, "extract_failed", [], []
        try:
            document = fitz.open(stream=pdf_data, filetype="pdf")
        except Exception as exc:
            return None, ("corrupted" if "cannot open" in str(exc).lower() else "extract_failed"), [], []

        try:
            total_pages = len(document)
            text_content = ""

            # ── Stage 1: first 3 pages ──
            stage1_limit = min(3, total_pages)
            for i in range(stage1_limit):
                page_text = document[i].get_text("text")
                text_content += page_text + "\n"
                if _RE_DIGITS_50_TEXT.search(page_text):
                    break
            else:
                # No early exit — check if we found anything so far
                matches = _RE_DIGITS_50_TEXT.findall(text_content)
                if not matches and total_pages > 3:
                    # ── Stage 2: pages 4-6 ──
                    stage2_limit = min(6, total_pages)
                    for i in range(3, stage2_limit):
                        page_text = document[i].get_text("text")
                        text_content += page_text + "\n"
                        if _RE_DIGITS_50_TEXT.search(page_text):
                            break
                    else:
                        matches = _RE_DIGITS_50_TEXT.findall(text_content)
                        if not matches and total_pages <= 10 and total_pages > 6:
                            # ── Stage 3: remaining pages (small PDFs only) ──
                            for i in range(6, total_pages):
                                page_text = document[i].get_text("text")
                                text_content += page_text + "\n"
                                if _RE_DIGITS_50_TEXT.search(page_text):
                                    break
        finally:
            document.close()

        if not text_content.strip():
            return None, "empty", [], []

        matches_50 = list(dict.fromkeys(_RE_DIGITS_50_TEXT.findall(text_content)))
        if matches_50:
            return matches_50[0], "ok", [], matches_50[:20]

        tokens = _RE_DIGITS_10_20.findall(text_content)[:20]
        return None, "extract_failed", tokens, []

    @staticmethod
    def _build_consecutivo_index(records: dict[str, FacturaRecord]) -> dict[str, str]:
        """
        Construye índice robusto token -> clave basado en estructura Hacienda.

        Prioriza:
        - Consecutivo oficial (posiciones 21:41 de la clave de 50).
        - Consecutivo del XML (`record.consecutivo`) normalizado a dígitos.
        - Combinación emisor+consecutivo para desambiguación.

        Solo conserva tokens con mapeo único para evitar falsos positivos.
        """
        candidates: dict[str, set[str]] = {}
        for clave, record in records.items():
            consecutivo_oficial = _extract_consecutivo_from_clave(clave)
            emisor_oficial = _extract_emisor_from_clave(clave)
            if consecutivo_oficial:
                candidates.setdefault(consecutivo_oficial, set()).add(clave)
                # sufijos útiles para nombres truncados (mínimo 10)
                for size in (19, 18, 17, 16, 15, 14, 13, 12, 11, 10):
                    candidates.setdefault(consecutivo_oficial[-size:], set()).add(clave)

            cons_xml = _RE_NON_DIGIT.sub("", record.consecutivo or "")
            if len(cons_xml) >= 10:
                candidates.setdefault(cons_xml, set()).add(clave)

            if emisor_oficial and consecutivo_oficial:
                candidates.setdefault(f"{emisor_oficial}{consecutivo_oficial}", set()).add(clave)

        return {token: next(iter(claves)) for token, claves in candidates.items() if len(claves) == 1}

    @staticmethod
    def _resolve_clave_from_filename_tokens(filename: str, consecutivo_index: dict[str, str]) -> str | None:
        """Intenta resolver una clave de 50 dígitos usando tokens numéricos del nombre."""
        tokens = sorted(_extract_numeric_tokens(filename, min_len=10), key=len, reverse=True)
        return FacturaIndexer._resolve_clave_from_tokens(tokens, consecutivo_index)

    @staticmethod
    def _resolve_clave_from_tokens(tokens: list[str], consecutivo_index: dict[str, str]) -> str | None:
        """Resuelve clave por tokens numéricos priorizando coincidencia exacta y luego sufijo."""
        for token in sorted((t for t in tokens if len(t) >= 10), key=len, reverse=True):
            exact = consecutivo_index.get(token)
            if exact:
                return exact
            matches = [clave for known_token, clave in consecutivo_index.items() if known_token.endswith(token)]
            if len(set(matches)) == 1:
                return matches[0]
        return None

    @staticmethod
    def _reconcile_missing_with_filename_consecutivo(
        records: dict[str, FacturaRecord],
        pdf_files: list[Path],
        linked: dict[str, Path],
    ) -> None:
        """Segundo pase: vincula por consecutivo en nombre si la coincidencia es única."""
        assigned_paths = set(linked.values())
        for clave, record in records.items():
            if not record.xml_path or record.pdf_path:
                continue
            consecutivo = _extract_consecutivo_from_clave(clave)
            if not consecutivo:
                continue

            short_cons = consecutivo[-10:]
            candidates: list[Path] = []
            for pdf_file in pdf_files:
                if pdf_file in assigned_paths:
                    continue
                digits_name = _normalize_digits(pdf_file.stem)
                if consecutivo in digits_name or digits_name.endswith(short_cons):
                    candidates.append(pdf_file)

            if len(candidates) == 1:
                record.pdf_path = candidates[0]
                linked[clave] = candidates[0]
                assigned_paths.add(candidates[0])
                logger.debug("PDF: %s → FOUND EN RECONCILIACION_CONSECUTIVO", candidates[0].name)

    @staticmethod
    def _recompute_states(records: dict[str, FacturaRecord]) -> None:
        for record in records.values():
            if record.pdf_path and record.xml_path:
                record.estado = "pendiente"
            elif record.xml_path and not record.pdf_path:
                record.estado = "pendiente_pdf"
            elif record.pdf_path and not record.xml_path:
                record.estado = "sin_xml"

    @staticmethod
    def _parse_ui_date(value: str):
        text = (value or "").strip()
        if not text:
            return None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _in_range(fecha_emision: str, from_dt, to_dt) -> bool:
        if not from_dt and not to_dt:
            return True
        try:
            fecha = datetime.strptime((fecha_emision or "").strip(), "%d/%m/%Y").date()
        except ValueError:
            return False
        if from_dt and fecha < from_dt:
            return False
        if to_dt and fecha > to_dt:
            return False
        return True
