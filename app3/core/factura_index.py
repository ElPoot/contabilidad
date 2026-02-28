from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import logging
from pathlib import Path
import re
import time
from typing import Any

from app3.bootstrap import bootstrap_legacy_paths
from .models import FacturaRecord
from .xml_manager import CRXMLManager

try:
    import fitz
except ModuleNotFoundError:  # pragma: no cover - dependencia opcional en runtime
    fitz = None  # type: ignore[assignment]

bootstrap_legacy_paths()

# App 1 - extracción de clave desde PDF con scoring y fallback por nombre de archivo
try:
    from facturacion_system.core.pdf_classifier import extract_clave_and_cedula
except ModuleNotFoundError:

    def extract_clave_and_cedula(data: bytes, original_filename: str = "") -> tuple:  # type: ignore[misc]
        return None, None


logger = logging.getLogger(__name__)


def _extract_clave_from_filename(filename: str) -> str | None:
    """Extrae clave de 50 dígitos desde el nombre del PDF sin abrir el archivo."""
    match = re.search(r"(\d{50})", str(filename or ""))
    return match.group(1) if match else None


def _extract_numeric_tokens(filename: str, min_len: int = 10) -> list[str]:
    """Extrae secuencias numéricas relevantes desde nombre de archivo."""
    return [token for token in re.findall(r"\d+", filename or "") if len(token) >= min_len]


def _is_invoice_candidate(filename: str) -> bool:
    """Heurística para distinguir comprobantes de PDFs administrativos."""
    name = (filename or "").lower()
    if re.search(r"\d{10,}", name):
        return True
    keywords = ("factura", "fe", "nc", "nd", "credito", "debito", "electr")
    return any(k in name for k in keywords)


def _is_clearly_non_invoice_filename(filename: str) -> bool:
    """Detecta adjuntos administrativos que no vale la pena extraer en profundidad."""
    name = (filename or "").lower()
    non_invoice_keywords = (
        "brochure",
        "comunicado",
        "cambio de comercializador",
        "detallepedido",
        "pedido",
    )
    return any(k in name for k in non_invoice_keywords)


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
    return re.sub(r"\D", "", text or "")


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

        from_dt = self._parse_ui_date(from_date)
        to_dt = self._parse_ui_date(to_date)

        xml_root = client_folder / "XML"
        pdf_root = client_folder / "PDF"

        records: dict[str, FacturaRecord] = {}

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

        if include_pdf_scan:
            pdf_scan_report = self._scan_and_link_pdfs_optimized(
                pdf_root,
                records,
                allow_pdf_content_fallback=allow_pdf_content_fallback,
            )
            self.audit_report["pdf_scan"] = pdf_scan_report.get("audit", {})

        self._recompute_states(records)
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
        timeout_seconds: int = 5,
        max_workers: int = 8,
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

        pdf_files = [p for p in pdf_root.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"]
        total_files = len(pdf_files)
        if not pdf_files:
            return {"linked": {}, "omitidos": {}, "audit": base_audit}

        started = time.perf_counter()
        linked: dict[str, Path] = {}
        omitidos: dict[str, dict[str, Any]] = {}
        diagnostics_sin_clave: list[dict[str, Any]] = []
        max_slow_name = ""
        max_slow_ms = 0
        max_size_name = ""
        max_size_mb = 0.0

        consecutivo_index = self._build_consecutivo_index(records)
        logger.info("Escaneando %s PDFs en %s", total_files, pdf_root)
        logger.info("ThreadPoolExecutor lanzado: %s workers", max(1, min(max_workers, total_files)))

        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, total_files))) as executor:
            future_map = {
                executor.submit(
                    self._process_single_pdf,
                    pdf_file,
                    allow_pdf_content_fallback,
                    timeout_seconds,
                ): pdf_file
                for pdf_file in pdf_files
            }
            for future in as_completed(future_map):
                pdf_file = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover
                    logger.exception("Error no controlado procesando PDF %s", pdf_file)
                    omitidos[pdf_file.name] = {"razon": "extract_failed", "error": str(exc), "intento": 1}
                    continue

                elapsed_ms = int(result.get("tiempo_ms", 0))
                size_mb = float(result.get("size_mb", 0.0))
                if elapsed_ms > max_slow_ms:
                    max_slow_ms = elapsed_ms
                    max_slow_name = pdf_file.name
                if size_mb > max_size_mb:
                    max_size_mb = size_mb
                    max_size_name = pdf_file.name

                clave = result.get("clave")
                metodo = str(result.get("metodo") or "")

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
                if reason == "extract_failed" and not _is_invoice_candidate(pdf_file.name):
                    reason = "non_invoice"

                if reason == "timeout":
                    logger.warning("PDF: %s → timeout (%sms) - omitido", pdf_file.name, elapsed_ms)
                elif reason == "corrupted":
                    logger.error("PDF: %s → corrupted (%s)", pdf_file.name, message)
                elif reason == "non_invoice":
                    logger.debug("PDF: %s → ignorado (no comprobante)", pdf_file.name)
                else:
                    logger.debug("PDF: %s → omitido (%s)", pdf_file.name, reason)

                filename_tokens = _extract_numeric_tokens(pdf_file.stem, min_len=10)
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

        self._reconcile_missing_with_filename_consecutivo(records, pdf_files, linked)

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

        attempts = 1
        try:
            clave, _ced = extract_clave_and_cedula(pdf_data, original_filename=pdf_file.name)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms > timeout_seconds * 1000:
                return {
                    "clave": None,
                    "razon": "timeout",
                    "error": f"Superó timeout de {timeout_seconds}s.",
                    "intento": attempts,
                    "tiempo_ms": elapsed_ms,
                    "size_mb": size_mb,
                }
            if clave:
                return {
                    "clave": clave,
                    "metodo": "contenido",
                    "intento": attempts,
                    "tiempo_ms": elapsed_ms,
                    "size_mb": size_mb,
                }
        except Exception as exc:
            logger.debug("PDF: %s → extract_clave_and_cedula falló: %s", pdf_file.name, exc)

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
            }

        if clave_retry:
            return {
                "clave": clave_retry,
                "metodo": "reintento_texto_pdf",
                "intento": attempts,
                "tiempo_ms": elapsed_ms,
                "size_mb": size_mb,
                "claves_detectadas": claves_detectadas,
            }

        return {
            "clave": None,
            "razon": retry_error,
            "error": "extract_clave_and_cedula retornó None" if retry_error == "extract_failed" else "",
            "intento": attempts,
            "tiempo_ms": elapsed_ms,
            "size_mb": size_mb,
            "text_tokens": text_tokens,
            "claves_detectadas": claves_detectadas,
        }

    @staticmethod
    def _read_pdf_bytes_streaming(pdf_file: Path, chunk_size: int = 1024 * 1024) -> bytes:
        """Lee un PDF por streaming (sin read_bytes())."""
        chunks = bytearray()
        with pdf_file.open("rb") as stream:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                chunks.extend(chunk)
        return bytes(chunks)

    @staticmethod
    def _extract_clave_from_pdf_text(pdf_data: bytes) -> tuple[str | None, str, list[str], list[str]]:
        """Reintento con PyMuPDF para detectar clave 50 dígitos y tokens numéricos útiles."""
        if fitz is None:
            return None, "extract_failed", [], []
        try:
            document = fitz.open(stream=pdf_data, filetype="pdf")
        except Exception as exc:
            return None, ("corrupted" if "cannot open" in str(exc).lower() else "extract_failed"), [], []

        try:
            text_content = "\n".join(page.get_text("text") for page in document)
        finally:
            document.close()

        if not text_content.strip():
            return None, "empty", [], []

        matches_50 = list(dict.fromkeys(re.findall(r"\d{50}", text_content)))
        if matches_50:
            return matches_50[0], "ok", [], matches_50[:20]

        tokens = [token for token in re.findall(r"\d{10,20}", text_content)][:20]
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

            cons_xml = re.sub(r"\D", "", record.consecutivo or "")
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
