from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class FacturaRecord:
    clave: str
    fecha_emision: str = ""
    emisor_nombre: str = ""
    emisor_cedula: str = ""
    tipo_documento: str = ""
    total_comprobante: str = ""
    xml_path: Path | None = None
    pdf_path: Path | None = None
    estado: str = "pendiente"
