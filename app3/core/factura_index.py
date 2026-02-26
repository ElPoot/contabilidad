from __future__ import annotations

from datetime import datetime
import re
from pathlib import Path

from app3.bootstrap import bootstrap_legacy_paths
from .models import FacturaRecord

bootstrap_legacy_paths()

# App 2 - logica completa: parsing paralelo, dedup SHA256, MensajeHacienda, nombres Hacienda
try:
    from facturacion.xml_manager import CRXMLManager
except ModuleNotFoundError:
    from facturacion_system.core.xml_manager import CRXMLManager  # type: ignore[no-redef]

# App 1 - extraccion de clave desde PDF con scoring y fallback por nombre de archivo
try:
    from facturacion_system.core.pdf_classifier import extract_clave_and_cedula
except ModuleNotFoundError:
    def extract_clave_and_cedula(data: bytes, original_filename: str = "") -> tuple:  # type: ignore[misc]
        return None, None




def _extract_clave_from_filename(filename: str) -> str | None:
    """Extrae clave de 50 dígitos desde el nombre del PDF sin abrir el archivo."""
    match = re.search(r"(\d{50})", str(filename or ""))
    return match.group(1) if match else None

class FacturaIndexer:
    """
    Construye la lista de FacturaRecord para un cliente y periodo usando
    toda la logica existente de App 1 y App 2.

    App 2 (CRXMLManager.load_xml_folder) maneja:
      - Parsing paralelo con ThreadPoolExecutor
      - Deduplicacion por SHA256
      - Asociacion de MensajeHacienda -> estado Hacienda
      - Resolucion de nombres desde cache/API Hacienda
      - Auditoria y logging

    App 1 (extract_clave_and_cedula) maneja:
      - Extrae clave de 50 digitos desde PDFs
      - Prioriza nombre de archivo (instantaneo) antes de leer el contenido
      - Scoring para elegir clave correcta cuando hay varias (ej: Documento Referencia)
      - Fallback de cedula a 10 digitos si Hacienda no responde con 12
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
    ) -> list[FacturaRecord]:
        self.parse_errors = []
        self.audit_report = {}

        from_dt = self._parse_ui_date(from_date)
        to_dt = self._parse_ui_date(to_date)

        xml_root = client_folder / "XML"
        pdf_root = client_folder / "PDF"

        records: dict[str, FacturaRecord] = {}

        # --- PASO 1: cargar XMLs con la logica completa de App 2 ---
        # load_xml_folder hace parsing paralelo + dedup SHA256 +
        # MensajeHacienda + resolucion nombres + auditoria
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

        # --- PASO 2: vincular PDFs usando logica de App 1 (opcional) ---
        if include_pdf_scan:
            self._scan_and_link_pdfs(pdf_root, records)

        self._recompute_states(records)
        return sorted(records.values(), key=lambda r: (r.fecha_emision, r.clave))

    def link_pdfs_for_records(self, client_folder: Path, records: list[FacturaRecord]) -> list[FacturaRecord]:
        """Enriquece una lista ya cargada con vínculos PDF sin reprocesar XML."""
        record_map = {r.clave: r for r in records if r.clave}
        self._scan_and_link_pdfs(client_folder / "PDF", record_map)
        self._recompute_states(record_map)
        return sorted(record_map.values(), key=lambda r: (r.fecha_emision, r.clave))

    def _scan_and_link_pdfs(self, pdf_root: Path, records: dict[str, FacturaRecord]) -> None:
        # extract_clave_and_cedula prioriza el nombre del archivo,
        # solo lee el contenido si no hay clave en el nombre
        if not pdf_root.exists():
            return

        for pdf_file in pdf_root.rglob("*.pdf"):
            clave = _extract_clave_from_filename(pdf_file.name)

            # Fallback costoso solo si el nombre no trae clave.
            if not clave:
                try:
                    clave, _ced = extract_clave_and_cedula(
                        pdf_file.read_bytes(),
                        original_filename=pdf_file.name,
                    )
                except Exception:
                    clave = None

            if not clave:
                continue

            if clave in records:
                records[clave].pdf_path = pdf_file
            else:
                records[clave] = FacturaRecord(
                    clave=clave,
                    pdf_path=pdf_file,
                    estado="sin_xml",
                )

    @staticmethod
    def _recompute_states(records: dict[str, FacturaRecord]) -> None:
        # Estado final de cada registro
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
