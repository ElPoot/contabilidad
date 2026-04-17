"""Generador de PDFs desde datos de facturas XML.

Este módulo proporciona funcionalidad para generar PDFs simples a partir de
datos de FacturaRecord, permitiendo clasificar facturas que solo tienen XML
sin PDF correspondiente.

Nota: El PDF generado incluye la clave de 50 dígitos como texto legible,
lo que permite que el indexer detecte la clave en futuras recargas.
"""

from __future__ import annotations

from pathlib import Path
from decimal import Decimal

try:
    import fitz  # pymupdf >= 1.24
except ImportError:
    fitz = None

from gestor_contable.core.models import FacturaRecord


def extract_items_from_xml(xml_path: Path | str) -> list[dict] | None:
    """Extrae items/líneas de un archivo XML de Hacienda CR.

    Args:
        xml_path: Ruta al archivo XML

    Returns:
        Lista de dicts con items, o None si no se puede parsear.
        Cada dict tiene: {"desc": "...", "qty": "...", "unit": "...", "monto": "..."}
    """
    try:
        import io
        import xml.etree.ElementTree as ET

        xml_path = Path(xml_path)
        if not xml_path.exists():
            return None

        try:
            tree = ET.parse(xml_path)
        except ET.ParseError:
            fixed = xml_path.read_bytes().decode("iso-8859-1").encode("utf-8")
            tree = ET.parse(io.BytesIO(fixed))
        root = tree.getroot()

        # Buscar todas las LineaDetalle en el XML (pueden estar en diferentes rutas)
        items = []

        # Namespace de Hacienda CR
        namespaces = {
            '': 'https://www.hacienda.go.cr/fe/esquemas/2024/v4.2',
            'ds': 'http://www.w3.org/2000/09/xmldsig#',
        }

        # Buscar todos los elementos que contengan Detalle
        for linea in root.iter():
            # Buscar elementos que terminen con "LineaDetalle"
            if linea.tag.endswith('LineaDetalle'):
                desc = ""
                qty = ""
                unit = ""
                monto = ""

                # Iterar sobre children para extraer datos
                for child in linea:
                    tag_name = child.tag.split('}')[-1]  # Remover namespace

                    if tag_name == "Detalle":
                        desc = child.text or ""
                    elif tag_name == "Cantidad":
                        qty = child.text or ""
                    elif tag_name == "UnidadMedida":
                        unit = child.text or ""
                    elif tag_name == "MontoTotal":
                        monto = child.text or ""
                    elif tag_name == "PrecioUnitario" and not monto:
                        # Fallback si no hay MontoTotal
                        monto = child.text or ""

                if desc.strip():
                    items.append({
                        "desc": desc.strip()[:45],
                        "qty": qty.strip(),
                        "unit": unit.strip()[:8],
                        "monto": monto.strip(),
                    })

        return items if items else None

    except Exception as e:
        # Si falla el parsing, retornar None
        import logging
        logging.debug(f"No se pudieron extraer items del XML: {e}")
        return None


def _parse_decimal(value: str | None) -> Decimal:
    """Parsea string de decimal de forma segura."""
    if not value:
        return Decimal("0")
    try:
        # Reemplazar comas por puntos (formato europeo/costarricense)
        normalized = str(value).strip().replace(",", ".")
        return Decimal(normalized)
    except Exception:
        return Decimal("0")


def _format_amount(value: str | None) -> str:
    """Formatea decimal como 'XXX XXX,XX' (miles con espacio, decimales con coma)."""
    d = _parse_decimal(value)
    abs_d = abs(d)

    # Convertir a formato con 2 decimales
    formatted = f"{abs_d:,.2f}"
    parts = formatted.split(".")

    # Reemplazar comas (miles) con espacios
    integer_part = parts[0].replace(",", " ")
    decimal_part = parts[1]

    result = f"{integer_part},{decimal_part}"
    return f"-{result}" if d < 0 else result


def generate_factura_pdf(
    record: FacturaRecord,
    output_path: Path,
    items: list[dict] | None = None
) -> None:
    """Genera un PDF con datos de la factura desde XML.

    Args:
        record: FacturaRecord con datos del XML
        output_path: Path donde guardar el PDF
        items: Lista opcional de dicts con items: [{"desc": "...", "qty": "...", "unit": "...", "monto": "..."}, ...]

    Raises:
        ImportError: Si pymupdf no está instalado
        Exception: Si hay error en generación o escritura del PDF
    """
    if fitz is None:
        raise ImportError("pymupdf (fitz) es requerido para generar PDFs")

    # Crear documento A4
    doc = fitz.open()
    try:
        page = doc.new_page(pno=-1, width=595, height=842)  # A4 en puntos

        # Fondo oscuro (como la app)
        bg_color = (0.05, 0.06, 0.08)  # #0d0f14
        page.draw_rect(page.rect, color=None, fill=bg_color)

        # Colores y fuentes
        text_color = (0.93, 0.93, 0.94)  # #e8eaf0 en RGB (0-1)
        muted_color = (0.42, 0.45, 0.50)  # #6b7280 en RGB
        teal_color = (0.18, 0.83, 0.75)  # #2dd4bf en RGB
        border_color = (0.45, 0.52, 0.72)  # #7286b8 en RGB (más visible en fondo oscuro)

        # Márgenes
        margin_left = 30
        margin_right = 30
        margin_top = 30
        y_pos = margin_top

        # Ancho útil
        page_width = 595 - margin_left - margin_right

        # === TÍTULO ===
        doc_type = record.tipo_documento or "Documento"
        page.insert_text((margin_left, y_pos), doc_type.upper(),
                         fontsize=20, color=text_color, fontname="helv")
        y_pos += 35

        # === INFORMACIÓN BÁSICA ===
        # Consecutivo, Fecha, Clave
        info_text = f"Nº {record.consecutivo or '--'}  |  {record.fecha_emision or '--'}"
        page.insert_text((margin_left, y_pos), info_text,
                         fontsize=10, color=muted_color, fontname="helv")
        y_pos += 20

        # Clave de 50 dígitos (MUY IMPORTANTE para re-indexación)
        clave_text = f"Clave: {record.clave}"
        page.insert_text((margin_left, y_pos), clave_text,
                         fontsize=9, color=teal_color, fontname="helv")
        y_pos += 20

        # Estado Hacienda
        if record.estado_hacienda:
            estado_text = f"Estado Hacienda: {record.estado_hacienda}"
            page.insert_text((margin_left, y_pos), estado_text,
                             fontsize=9, color=text_color, fontname="helv")
            y_pos += 20

        y_pos += 10

        # === SECCION EMISOR ===
        page.insert_text((margin_left, y_pos), "EMISOR",
                         fontsize=10, color=muted_color, fontname="helv")
        y_pos += 15

        if record.emisor_nombre:
            page.insert_text((margin_left + 10, y_pos), record.emisor_nombre,
                             fontsize=10, color=text_color, fontname="helv")
            y_pos += 15

        if record.emisor_cedula:
            page.insert_text((margin_left + 10, y_pos), f"Cedula: {record.emisor_cedula}",
                             fontsize=9, color=muted_color, fontname="helv")
            y_pos += 15

        y_pos += 10

        # === SECCION RECEPTOR ===
        page.insert_text((margin_left, y_pos), "RECEPTOR",
                         fontsize=10, color=muted_color, fontname="helv")
        y_pos += 15

        if record.receptor_nombre:
            page.insert_text((margin_left + 10, y_pos), record.receptor_nombre,
                             fontsize=10, color=text_color, fontname="helv")
            y_pos += 15

        if record.receptor_cedula:
            page.insert_text((margin_left + 10, y_pos), f"Cedula: {record.receptor_cedula}",
                             fontsize=9, color=muted_color, fontname="helv")
            y_pos += 15

        y_pos += 15

        # === LINEA SEPARADORA ===
        line_y = y_pos
        page.draw_line((margin_left, line_y), (595 - margin_right, line_y),
                       color=border_color, width=1)
        y_pos += 15

        # === DETALLES / ITEMS ===
        if items:
            page.insert_text((margin_left, y_pos), "DETALLES DE COMPRA",
                             fontsize=10, color=muted_color, fontname="helv")
            y_pos += 15

            # Encabezados de tabla
            col_desc = margin_left
            col_qty = margin_left + 300
            col_unit = col_qty + 60
            col_monto = col_unit + 60

            page.insert_text((col_desc, y_pos), "Descripcion",
                             fontsize=8, color=teal_color, fontname="helv")
            page.insert_text((col_qty, y_pos), "Cantidad",
                             fontsize=8, color=teal_color, fontname="helv")
            page.insert_text((col_unit, y_pos), "P.Unit",
                             fontsize=8, color=teal_color, fontname="helv")
            page.insert_text((col_monto, y_pos), "Monto",
                             fontsize=8, color=teal_color, fontname="helv")
            y_pos += 12

            # Línea separadora de encabezados
            page.draw_line((col_desc, y_pos), (595 - margin_right, y_pos),
                           color=border_color, width=0.5)
            y_pos += 8

            # Items
            for item in items:
                desc = str(item.get("desc", "")).strip()[:40]
                qty = str(item.get("qty", "")).strip()
                unit = str(item.get("unit", "")).strip()[:8]
                monto = _format_amount(item.get("monto", ""))

                if desc:  # Solo mostrar si hay descripción
                    page.insert_text((col_desc, y_pos), desc,
                                     fontsize=8, color=text_color, fontname="helv")
                    page.insert_text((col_qty, y_pos), qty,
                                     fontsize=8, color=text_color, fontname="helv")
                    page.insert_text((col_unit, y_pos), unit,
                                     fontsize=8, color=text_color, fontname="helv")
                    page.insert_text((col_monto, y_pos), monto,
                                     fontsize=8, color=text_color, fontname="helv")
                    y_pos += 11

            y_pos += 10

            # Línea separadora antes de montos
            page.draw_line((margin_left, y_pos), (595 - margin_right, y_pos),
                           color=border_color, width=1)
            y_pos += 15

        # === MONTOS ===
        def _draw_amount_row(label: str, value: str, is_bold: bool = False):
            nonlocal y_pos
            fontname = "helv"
            fontsize = 10 if is_bold else 9

            # Etiqueta a la izquierda
            page.insert_text((margin_left, y_pos), label,
                             fontsize=fontsize, color=text_color, fontname=fontname)

            # Valor a la derecha (posición fija aproximada)
            # Calculamos aproximadamente basado en número de caracteres
            formatted = _format_amount(value)
            # Para Helvetica, aproximadamente 6px por carácter en fontsize 9-10
            est_width = len(formatted) * (fontsize * 0.55)
            value_x = max(margin_left + 250, 595 - margin_right - est_width)
            page.insert_text((value_x, y_pos), formatted,
                             fontsize=fontsize, color=text_color, fontname=fontname)

            y_pos += 18

        # Subtotal
        if record.subtotal:
            _draw_amount_row("Subtotal:", record.subtotal)

        # IVA desglosado por tasa (solo si > 0)
        iva_items = [
            ("IVA 1%", record.iva_1),
            ("IVA 2%", record.iva_2),
            ("IVA 4%", record.iva_4),
            ("IVA 8%", record.iva_8),
            ("IVA 13%", record.iva_13),
            ("IVA Otros", record.iva_otros),
        ]

        for label, value in iva_items:
            if value and _parse_decimal(value) > 0:
                _draw_amount_row(label, value)

        # Total de impuestos
        if record.impuesto_total:
            _draw_amount_row("Impuesto Total:", record.impuesto_total)

        # Total comprobante (destacado)
        if record.total_comprobante:
            _draw_amount_row("TOTAL:", record.total_comprobante, is_bold=True)

        # Moneda y tipo de cambio
        if record.moneda and record.moneda != "CRC":
            y_pos += 5
            moneda_text = f"Moneda: {record.moneda}"
            if record.tipo_cambio:
                moneda_text += f" | Tipo de cambio: {_format_amount(record.tipo_cambio)}"
            page.insert_text((margin_left, y_pos), moneda_text,
                             fontsize=9, color=muted_color, fontname="helv")
            y_pos += 18

        y_pos += 20

        # === FOOTER ===
        footer_text = "* PDF generado automaticamente por App 3 — No es el documento original *"
        footer_y = 800
        footer_color = (0.65, 0.68, 0.72)  # Color mas claro para footer
        page.insert_text((margin_left, footer_y), footer_text,
                         fontsize=8, color=footer_color, fontname="helv")

        # Crear carpeta si no existe
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Guardar PDF
        doc.save(output_path)
    finally:
        doc.close()
