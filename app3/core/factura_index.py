from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app3.bootstrap import bootstrap_legacy_paths
from .models import FacturaRecord

bootstrap_legacy_paths()

from facturacion.xml_manager import CRXMLManager  # noqa: E402
from facturacion_system.core.pdf_classifier import extract_clave_and_cedula  # noqa: E402


class FacturaIndexer:
    def __init__(self) -> None:
        self.xml_manager = CRXMLManager()
        self.parse_errors: list[str] = []

    def load_period(self, client_folder: Path, from_date: str = "", to_date: str = "") -> list[FacturaRecord]:
        self.parse_errors = []
        from_dt = self._parse_ui_date(from_date)
        to_dt = self._parse_ui_date(to_date)
        records: dict[str, FacturaRecord] = {}
        xml_root = client_folder / "XML"
        pdf_root = client_folder / "PDF"

        if xml_root.exists():
            for xml_file in xml_root.rglob("*.xml"):
                try:
                    parsed = self.xml_manager.parse_xml_file(xml_file)
                except Exception as exc:  # noqa: BLE001 - tolerar XML corrupto y continuar
                    self.parse_errors.append(f"{xml_file.name}: {exc}")
                    continue
                clave = str(parsed.get("clave_numerica") or "").strip()
                if len(clave) != 50:
                    continue
                fecha = str(parsed.get("fecha_emision") or "")
                if not self._in_range(fecha, from_dt, to_dt):
                    continue
                records[clave] = FacturaRecord(
                    clave=clave,
                    fecha_emision=fecha,
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
