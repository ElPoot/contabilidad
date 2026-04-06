"""ViewModel de seleccion para el panel derecho + visor PDF + botones de accion.

Dataclass pura — sin imports de customtkinter ni logica de negocio.
Captura todas las decisiones de render que antes vivian dispersas en
_on_select_single() y _on_multi_select().
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SelectionVM:
    """Vista-modelo para el panel derecho, visor PDF y botones de accion.

    Generado por _on_select_single / _on_multi_select en main_window.py
    y consumido por _render_selection_vm(), que es el unico lugar que
    toca widgets de seleccion.
    """

    # ── Modo de seleccion ─────────────────────────────────────────────────────
    # "single"      — una sola factura seleccionada
    # "multi_same"  — lote: multiples facturas del mismo emisor
    # "multi_mixed" — multiples facturas de emisores distintos (bloqueado)
    mode: str = "single"

    # ── Pill Hacienda ─────────────────────────────────────────────────────────
    hacienda_text: str = ""          # vacio → ocultar pill
    hacienda_color: str = "#e8eaf0"
    hacienda_bg: str = "transparent"

    # ── Visor PDF ─────────────────────────────────────────────────────────────
    # Prioridad de render: pdf_path > message > release_message > clear
    viewer_pdf_path: Path | None = None
    viewer_message: str | None = None          # pdf_viewer.show_message()
    viewer_release_message: str | None = None  # pdf_viewer.release_file_handles()

    # ── Proveedor prefill ─────────────────────────────────────────────────────
    proveedor: str = ""

    # ── Contexto visual del documento / lote ─────────────────────────────────
    batch_count: int = 0
    batch_emisor: str = ""
    doc_total: str = ""
    doc_fecha: str = ""
    doc_tipo: str = ""

    # ── Botones opcionales (visible = grid; no visible = grid_remove) ─────────
    btn_recover_visible: bool = False
    btn_link_visible: bool = False
    btn_delete_visible: bool = False
    btn_create_pdf_visible: bool = False
    btn_auto_classify_visible: bool = False
    btn_recheck_hacienda_visible: bool = False
    btn_swap_pdf_visible: bool = False
    swap_pdf_target: Path | None = None

    # ── Boton Clasificar (siempre visible; solo cambia estado y texto) ────────
    btn_classify_enabled: bool = True
    btn_classify_text: str = "Clasificar"
    block_reason: str = ""

    # ── Panel clasificacion anterior ──────────────────────────────────────────
    prev_frame_visible: bool = True
    prev_text: str = "--"
    prev_dest_path: Path | None = None
