"""Constructor de SelectionVM — logica de decision de seleccion, sin Tk.

Recibe FacturaRecord, tab activa y datos ya resueltos (pdf_path, clasificacion
anterior) y devuelve un SelectionVM listo para renderizar en main_window.py.

Sin imports de customtkinter, sin acceso a widgets, sin threads.
"""
from __future__ import annotations

from pathlib import Path

from gestor_contable.app.selection_vm import SelectionVM
from gestor_contable.core.models import FacturaRecord

# Colores de la paleta de diseno (mismos que main_window.py)
_SUCCESS = "#34d399"
_WARNING = "#fbbf24"


def build_single_vm(
    r: FacturaRecord,
    active_tab: str,
    pdf_path: Path | None,
    prev_text: str,
    prev_dest_path: Path | None,
) -> SelectionVM:
    """Construye el SelectionVM para seleccion simple de una factura."""
    vm = SelectionVM(mode="single")

    # Pill Hacienda
    if r.estado_hacienda:
        esh = r.estado_hacienda.strip()
        color = _SUCCESS if "aceptado" in esh.lower() else _WARNING
        vm.hacienda_text = f"  Hacienda: {esh}"
        vm.hacienda_color = color
        vm.hacienda_bg = "#0d2a1e" if color == _SUCCESS else "#2d2010"

    # Visor PDF
    vm.viewer_pdf_path = pdf_path
    if pdf_path is None:
        if r.estado == "pendiente_pdf":
            vm.viewer_message = (
                "XML sin PDF\n\nPresiona \u00abCrear PDF\u00bb para generar\n"
                "una factura a partir de los datos del XML."
            )
        elif r.razon_omisión:
            razon_text = {
                "non_invoice": "Detectado como no-factura (borrador, cat\u00e1logo, comunicado, etc.)",
                "timeout": "Timeout durante extracci\u00f3n de clave",
                "extract_failed": "Error al extraer informaci\u00f3n del PDF",
            }.get(r.razon_omisión, "PDF omitido")
            vm.viewer_release_message = f"\u2298 PDF Omitido\n\n{razon_text}"

    # Proveedor
    vm.proveedor = r.emisor_nombre or ""

    # Botones segun estado
    if r.estado == "huerfano":
        vm.btn_recover_visible = True
        vm.btn_classify_enabled = False
        vm.btn_classify_text = "\u2298 No clasificable"
    elif r.razon_omisión:
        vm.btn_link_visible = True
        vm.btn_delete_visible = True
        vm.btn_classify_enabled = False
        vm.btn_classify_text = "\u2298 No clasificable"
    elif r.estado == "pendiente_pdf":
        vm.btn_create_pdf_visible = True
        vm.btn_classify_text = "Clasificar sin PDF"
    elif active_tab in ("ingreso", "sin_receptor"):
        vm.btn_auto_classify_visible = True

    # Clasificacion anterior
    vm.prev_text = prev_text
    vm.prev_dest_path = prev_dest_path

    return vm


def build_multi_vm(records: list[FacturaRecord]) -> SelectionVM:
    """Construye el SelectionVM para seleccion multiple (lote o emisores mixtos)."""
    cedulas = {r.emisor_cedula for r in records}

    if len(cedulas) > 1:
        vm = SelectionVM(mode="multi_mixed")
        vm.viewer_message = (
            f"Advertencia: {len(records)} facturas de {len(cedulas)} emisores distintos.\n"
            "Solo se puede clasificar en lote facturas del MISMO EMISOR."
        )
        vm.btn_classify_enabled = False
        vm.btn_classify_text = f"Advertencia: Emisores distintos ({len(cedulas)})"
        vm.prev_frame_visible = False
        return vm

    emisor_nombre = records[0].emisor_nombre or "Emisor desconocido"
    vm = SelectionVM(mode="multi_same")
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
    return vm
