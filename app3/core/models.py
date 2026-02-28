from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class FacturaRecord:
    clave: str

    # Campos basicos (App 2 - CRXMLManager)
    fecha_emision: str = ""
    emisor_nombre: str = ""
    emisor_cedula: str = ""
    receptor_nombre: str = ""
    receptor_cedula: str = ""
    tipo_documento: str = ""
    consecutivo: str = ""

    # Montos (App 2)
    subtotal: str = ""
    impuesto_total: str = ""
    total_comprobante: str = ""
    moneda: str = ""
    tipo_cambio: str = ""

    # IVA desglosado por tasa (App 2 - CRXMLManager)
    iva_1:  str = ""
    iva_2:  str = ""
    iva_4:  str = ""
    iva_8:  str = ""
    iva_13: str = ""

    # Estado Hacienda (App 2 - asociacion MensajeHacienda)
    estado_hacienda: str = ""

    # Rutas
    xml_path: Path | None = None
    pdf_path: Path | None = None

    # Estado de clasificacion en App 3
    estado: str = "pendiente"
    # pendiente      -> tiene XML y PDF, sin clasificar
    # pendiente_pdf  -> tiene XML pero no PDF
    # sin_xml        -> tiene PDF pero no XML
    # clasificado    -> ya fue clasificado (segun BD)

    # Razon de omisión (si el PDF fue omitido)
    razon_omisión: str | None = None
    # None         -> no fue omitido
    # "non_invoice" -> detectado como no-factura (borrador, catálogo, etc.)
    # "timeout"    -> timeout durante extracción
    # "extract_failed" -> error en extracción
