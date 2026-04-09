"""Constructor de SelectionVM — logica de decision de seleccion, sin Tk.

Recibe FacturaRecord, tab activa y datos ya resueltos (pdf_path, clasificacion
anterior) y devuelve un SelectionVM listo para renderizar en main_window.py.

Sin imports de customtkinter, sin acceso a widgets, sin threads.
"""
from __future__ import annotations

from pathlib import Path

from gestor_contable.app.selection_vm import SelectionVM
from gestor_contable.core.classification_utils import get_hacienda_review_status
from gestor_contable.core.iva_utils import parse_decimal_value
from gestor_contable.core.models import FacturaRecord

# Colores de la paleta de diseno (mismos que main_window.py)
_SUCCESS = "#34d399"
_WARNING = "#fbbf24"

_DOC_TYPE_LABELS = {
    "Factura Electrónica": "Factura",
    "Factura electronica": "Factura",
    "Nota de Crédito": "Nota de Crédito",
    "Nota de Débito": "Nota de Débito",
    "Tiquete": "Tiquete",
}


def _format_doc_amount(value: str, moneda: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "--"

    amount = parse_decimal_value(raw)
    if amount is None:
        return raw

    formatted_abs = f"{abs(amount):,.2f}"
    integer_part, decimal_part = formatted_abs.split(".")
    formatted = f"{integer_part.replace(',', ' ')},{decimal_part}"
    if amount < 0:
        formatted = f"-{formatted}"

    currency = (moneda or "").strip().upper()
    if currency in {"CRC", "COLON", "COLONES", "CR"}:
        prefix = "CRC "
    elif currency == "USD":
        prefix = "USD "
    elif currency:
        prefix = f"{currency} "
    else:
        prefix = ""
    return f"{prefix}{formatted}"


def build_single_vm(
    r: FacturaRecord,
    active_tab: str,
    pdf_path: Path | None,
    prev_text: str,
    prev_dest_path: Path | None,
    pdf_duplicates_rejected: dict[Path, Path] | None = None,
) -> SelectionVM:
    """Construye el SelectionVM para seleccion simple de una factura."""
    vm = SelectionVM(
        mode="single",
        doc_total=_format_doc_amount(r.total_comprobante, r.moneda),
        doc_fecha=r.fecha_emision or "--",
        doc_tipo=_DOC_TYPE_LABELS.get(r.tipo_documento or "", r.tipo_documento or "--"),
    )

    if pdf_duplicates_rejected and pdf_path:
        for rejected, winner in pdf_duplicates_rejected.items():
            if winner == pdf_path:
                vm.btn_swap_pdf_visible = True
                vm.swap_pdf_target = rejected
                break

    # Pill Hacienda
    hacienda_review_status = get_hacienda_review_status(r)
    if r.estado_hacienda:
        esh = r.estado_hacienda.strip()
        color = _SUCCESS if "aceptado" in esh.lower() else _WARNING
        vm.hacienda_text = f"  Hacienda: {esh}"
        vm.hacienda_color = color
        vm.hacienda_bg = "#0d2a1e" if color == _SUCCESS else "#2d2010"
    elif hacienda_review_status == "sin_respuesta":
        vm.hacienda_text = "  Hacienda: Sin respuesta"
        vm.hacienda_color = _WARNING
        vm.hacienda_bg = "#2d2010"

    # Visor PDF
    vm.viewer_pdf_path = pdf_path
    if pdf_path is None:
        if r.estado == "pendiente_pdf":
            vm.viewer_message = (
                "XML sin PDF\n\nPresiona \u00abCrear PDF\u00bb para generar\n"
                "una factura a partir de los datos del XML."
            )
        elif r.razon_omision:
            razon_text = {
                "non_invoice": "Detectado como no-factura (borrador, cat\u00e1logo, comunicado, etc.)",
                "timeout": "Timeout durante extracci\u00f3n de clave",
                "extract_failed": "Error al extraer informaci\u00f3n del PDF",
            }.get(r.razon_omision, "PDF omitido")
            vm.viewer_release_message = f"\u2298 PDF Omitido\n\n{razon_text}"

    # Proveedor
    vm.proveedor = r.emisor_nombre or ""

    # Botones segun estado
    if hacienda_review_status == "rechazada":
        vm.btn_classify_enabled = False
        vm.btn_classify_text = "\u2298 No clasificable"
        vm.block_reason = "Hacienda rechazó esta factura y no puede clasificarse."
        if r.estado == "pendiente_pdf":
            vm.btn_create_pdf_visible = True
    elif hacienda_review_status == "sin_respuesta":
        vm.btn_classify_enabled = False
        vm.btn_classify_text = "\u2298 No clasificable"
        vm.block_reason = "Sin respuesta de Hacienda; no clasificar por ahora."
        vm.btn_recheck_hacienda_visible = True
        if r.estado == "pendiente_pdf":
            vm.btn_create_pdf_visible = True
    elif r.estado == "huerfano":
        vm.btn_recover_visible = True
        vm.btn_classify_enabled = False
        vm.btn_classify_text = "\u2298 No clasificable"
        vm.block_reason = "Recupera el PDF antes de clasificarlo."
    elif r.razon_omision:
        vm.btn_link_visible = True
        vm.btn_delete_visible = True
        vm.btn_classify_enabled = False
        vm.btn_classify_text = "\u2298 No clasificable"
        vm.block_reason = {
            "non_invoice": "El PDF fue marcado como no factura y requiere revisión manual.",
            "timeout": "La extracción del PDF excedió el tiempo permitido.",
            "extract_failed": "No se pudo extraer información útil del PDF.",
        }.get(r.razon_omision, "Este PDF fue omitido y no puede clasificarse todavía.")
    elif r.estado == "pendiente_pdf":
        vm.btn_create_pdf_visible = True
        vm.btn_classify_text = "Clasificar sin PDF"
    elif active_tab in ("ingreso", "sin_receptor") and pdf_path is not None:
        vm.btn_auto_classify_visible = True

    # Clasificacion anterior
    vm.prev_text = prev_text
    vm.prev_dest_path = prev_dest_path

    return vm


def build_multi_vm(records: list[FacturaRecord]) -> SelectionVM:
    """Construye el SelectionVM para seleccion multiple (lote o emisores mixtos)."""
    cedulas = {r.emisor_cedula for r in records}

    if len(cedulas) > 1:
        vm = SelectionVM(
            mode="multi_mixed",
            batch_count=len(records),
            batch_emisor="Emisores mixtos",
        )
        vm.viewer_message = (
            f"Advertencia: {len(records)} facturas de {len(cedulas)} emisores distintos.\n"
            "Solo se puede clasificar en lote facturas del MISMO EMISOR."
        )
        vm.btn_classify_enabled = False
        vm.btn_classify_text = f"Advertencia: Emisores distintos ({len(cedulas)})"
        vm.block_reason = "Selecciona solo facturas del mismo emisor para usar el modo lote."
        vm.prev_frame_visible = False
        return vm

    emisor_nombre = records[0].emisor_nombre or "Emisor desconocido"
    vm = SelectionVM(
        mode="multi_same",
        batch_count=len(records),
        batch_emisor=emisor_nombre,
    )
    vm.viewer_message = (
        f"Lote de clasificaci\u00f3n\n"
        f"{len(records)} facturas seleccionadas\n\n"
        f"Emisor: {emisor_nombre}\n\n"
        "Complete la clasificacion y haga click\n"
        "en 'Clasificar N facturas'."
    )
    vm.proveedor = emisor_nombre
    vm.btn_classify_text = f"Clasificar {len(records)} facturas"
    vm.prev_frame_visible = False
    hacienda_statuses = {
        status
        for status in (get_hacienda_review_status(record) for record in records)
        if status is not None
    }
    if hacienda_statuses:
        vm.btn_classify_enabled = False
        vm.btn_classify_text = "\u2298 Lote bloqueado"
        if hacienda_statuses == {"rechazada"}:
            vm.block_reason = "El lote incluye facturas rechazadas por Hacienda."
        elif hacienda_statuses == {"sin_respuesta"}:
            vm.block_reason = "El lote incluye facturas sin respuesta de Hacienda."
            vm.btn_recheck_hacienda_visible = True
        else:
            vm.block_reason = "El lote incluye facturas rechazadas o sin respuesta de Hacienda."
            if "sin_respuesta" in hacienda_statuses:
                vm.btn_recheck_hacienda_visible = True
    return vm
