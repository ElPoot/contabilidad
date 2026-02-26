from __future__ import annotations

from pathlib import Path

from app3.bootstrap import bootstrap_legacy_paths
from .models import FacturaRecord

bootstrap_legacy_paths()

from facturacion.xml_manager import CRXMLManager  # noqa: E402
from facturacion_system.core.pdf_classifier import extract_clave_and_cedula  # noqa: E402


class FacturaIndexer:
    def __init__(self) -> None:
        self.xml_manager = CRXMLManager()

    def load_period(self, client_folder: Path, from_date: str = "", to_date: str = "") -> list[FacturaRecord]:
        records: dict[str, FacturaRecord] = {}
        xml_root = client_folder / "XML"
        pdf_root = client_folder / "PDF"

        if xml_root.exists():
            for xml_file in xml_root.rglob("*.xml"):
                parsed = self.xml_manager.parse_xml_file(xml_file)
                clave = str(parsed.get("clave_numerica") or "").strip()
                if len(clave) != 50:
                    continue
                records[clave] = FacturaRecord(
                    clave=clave,
                    fecha_emision=str(parsed.get("fecha_emision") or ""),
                    emisor_nombre=str(parsed.get("emisor_nombre") or ""),
                    emisor_cedula=str(parsed.get("emisor_cedula") or ""),
                    tipo_documento=str(parsed.get("tipo_documento") or ""),
                    total_comprobante=str(parsed.get("total_comprobante") or ""),
                    xml_path=xml_file,
                    estado="pendiente",
                )

        if pdf_root.exists():
            for pdf_file in pdf_root.rglob("*.pdf"):
                clave, _ced = extract_clave_and_cedula(pdf_file.read_bytes(), original_filename=pdf_file.name)
                if not clave:
                    continue
                if clave in records:
                    records[clave].pdf_path = pdf_file
                else:
                    records[clave] = FacturaRecord(clave=clave, pdf_path=pdf_file, estado="sin_xml")

        for record in records.values():
            if record.pdf_path and record.xml_path:
                record.estado = "pendiente"
            elif record.xml_path and not record.pdf_path:
                record.estado = "pendiente_pdf"
            elif record.pdf_path and not record.xml_path:
                record.estado = "sin_xml"

        return sorted(records.values(), key=lambda r: (r.fecha_emision, r.clave))
