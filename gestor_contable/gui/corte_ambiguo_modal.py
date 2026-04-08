"""Overlay de clasificación para facturas AMBIGUO del corte mensual.

Cubre toda la ventana principal (mismo patrón que LoadingOverlay) con un fondo
oscuro y muestra el PDF + panel de decisión embebido — sin ventana flotante.

Flujo:
  COMPRAS  /  GASTOS  /  Omitir →
El contador decide en segundos. La decisión puede guardarse para el
proveedor completo (todos los meses futuros) o solo para esta factura.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from gestor_contable.gui.fonts import *

from gestor_contable.core.corte_engine import (
    CorteEngine,
    CorteItem,
    CATEGORIA_COMPRAS,
    CATEGORIA_GASTOS,
    METODO_VENDOR_CATALOG,
)
from gestor_contable.gui.pdf_viewer import PDFViewer

logger = logging.getLogger(__name__)

# ── Paleta (idéntica al resto de la app) ──────────────────────────────────────
BG      = "#0d0f14"
SURFACE = "#13161e"
CARD    = "#181c26"
BORDER  = "#252a38"
TEAL    = "#2dd4bf"
TEAL_DIM= "#1a9e8f"
TEXT    = "#e8eaf0"
MUTED   = "#6b7280"
WARNING = "#fbbf24"
SUCCESS = "#34d399"
DANGER  = "#f87171"


def _fmt_monto(raw: str) -> str:
    if not raw or raw in ("", "0"):
        return "—"
    try:
        val = float(str(raw).replace(",", ".").replace(" ", ""))
        return f"₡ {val:,.2f}"
    except (ValueError, TypeError):
        return raw


class CorteClasificacionOverlay(ctk.CTkFrame):
    """
    Overlay embebido (no Toplevel) para la cola de facturas AMBIGUO.

    Se coloca sobre la ventana principal con grid rowspan=2, igual que
    LoadingOverlay. El fondo oscuro crea el efecto de "dim" sin blur.

    Args:
        parent:       Ventana padre (App3Window).
        items:        Lista de CorteItem con categoria == AMBIGUO.
        engine:       CorteEngine activo.
        on_complete:  Callback con la lista completa actualizada al cerrar.
    """

    def __init__(
        self,
        parent,
        items: list[CorteItem],
        engine: CorteEngine,
        on_complete: Callable[[list[CorteItem]], None] | None = None,
    ):
        # fg_color oscuro → efecto de overlay/dim sobre el contenido de abajo
        super().__init__(parent, fg_color=BG)

        self._items       = [i for i in items if i is not None]
        self._engine      = engine
        self._on_complete = on_complete
        self._idx         = 0
        self._decididos   = 0

        # Colocarse encima de todo el contenido de la ventana padre
        self.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.lift()         # asegurar que queda por encima
        self.focus_set()

        self._build_ui()
        self._show_item(0)

    # ──────────────────────────────────────────────────────────────────────────
    # Layout
    # ──────────────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=3)   # PDF — ancho
        self.grid_columnconfigure(1, weight=0)   # panel derecho — fijo
        self.grid_rowconfigure(0, weight=0)      # barra superior
        self.grid_rowconfigure(1, weight=1)      # contenido

        # ── Barra superior ────────────────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        top.grid_columnconfigure(1, weight=1)
        top.grid_propagate(False)

        ctk.CTkLabel(
            top, text="Cola de clasificación — Corte mensual",
            font=F_APP_TITLE(),
            text_color=TEXT,
        ).grid(row=0, column=0, padx=16, pady=12, sticky="w")

        self._prog_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            top, textvariable=self._prog_var,
            font=F_MODAL_BODY(),
            text_color=MUTED,
        ).grid(row=0, column=1, padx=16, pady=12, sticky="e")

        # Botón de cierre anticipado (esquina derecha)
        ctk.CTkButton(
            top, text="Terminar",
            font=F_BTN_LIST(),
            fg_color="transparent",
            hover_color=CARD,
            text_color=MUTED,
            border_width=1,
            border_color=BORDER,
            height=28,
            width=90,
            corner_radius=6,
            command=self._terminar,
        ).grid(row=0, column=2, padx=12, pady=10, sticky="e")

        # ── Visor PDF ─────────────────────────────────────────────────────────
        self._pdf_viewer = PDFViewer(self)
        self._pdf_viewer.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=8)

        # ── Panel de decisión ─────────────────────────────────────────────────
        panel = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=10, width=280)
        panel.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=8)
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)

        # Nombre del proveedor
        self._prov_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            panel, textvariable=self._prov_var,
            font=F_MODAL_SUBTITLE(),
            text_color=TEXT,
            wraplength=240,
            justify="center",
        ).grid(row=0, column=0, padx=16, pady=(20, 4), sticky="ew")

        # Cédula
        self._ced_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            panel, textvariable=self._ced_var,
            font=F_MODAL_SUBTEXT(),
            text_color=MUTED,
        ).grid(row=1, column=0, padx=16, pady=(0, 4), sticky="ew")

        # Monto
        self._monto_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            panel, textvariable=self._monto_var,
            font=get_font(20, "bold"),
            text_color=TEAL,
        ).grid(row=2, column=0, padx=16, pady=(4, 2), sticky="ew")

        # Fecha
        self._fecha_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            panel, textvariable=self._fecha_var,
            font=F_MODAL_SUBTEXT(),
            text_color=MUTED,
        ).grid(row=3, column=0, padx=16, pady=(0, 20), sticky="ew")

        ctk.CTkFrame(panel, fg_color=BORDER, height=1).grid(
            row=4, column=0, sticky="ew", padx=16, pady=4
        )

        ctk.CTkLabel(
            panel, text="¿A dónde va esta factura?",
            font=F_MODAL_BODY(),
            text_color=MUTED,
        ).grid(row=5, column=0, padx=16, pady=(16, 10), sticky="ew")

        ctk.CTkButton(
            panel,
            text="COMPRAS",
            font=F_MODAL_TITLE(),
            fg_color=TEAL,
            hover_color=TEAL_DIM,
            text_color=BG,
            height=52,
            corner_radius=8,
            command=lambda: self._decidir(CATEGORIA_COMPRAS),
        ).grid(row=6, column=0, padx=16, pady=(0, 10), sticky="ew")

        ctk.CTkButton(
            panel,
            text="GASTOS",
            font=F_MODAL_TITLE(),
            fg_color=WARNING,
            hover_color="#d97706",
            text_color=BG,
            height=52,
            corner_radius=8,
            command=lambda: self._decidir(CATEGORIA_GASTOS),
        ).grid(row=7, column=0, padx=16, pady=(0, 16), sticky="ew")

        ctk.CTkFrame(panel, fg_color=BORDER, height=1).grid(
            row=8, column=0, sticky="ew", padx=16, pady=4
        )

        self._recordar_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            panel,
            text="Recordar para este proveedor",
            variable=self._recordar_var,
            font=F_MODAL_BODY(),
            text_color=TEXT,
            fg_color=TEAL,
            hover_color=TEAL_DIM,
            checkmark_color=BG,
        ).grid(row=9, column=0, padx=16, pady=(12, 4), sticky="w")

        ctk.CTkLabel(
            panel,
            text="Aplica a todas las facturas\nfuturas de este emisor",
            font=F_MODAL_HINT(),
            text_color=MUTED,
            justify="left",
        ).grid(row=10, column=0, padx=32, pady=(0, 16), sticky="w")

        ctk.CTkButton(
            panel,
            text="Omitir →",
            font=F_BTN_LIST(),
            fg_color="transparent",
            hover_color=CARD,
            text_color=MUTED,
            border_width=1,
            border_color=BORDER,
            height=32,
            corner_radius=6,
            command=self._omitir,
        ).grid(row=11, column=0, padx=16, pady=(0, 8), sticky="ew")

        ctk.CTkLabel(
            panel,
            text="Las omitidas quedan como\nAMBIGUO en el reporte",
            font=F_MODAL_HINT(),
            text_color=MUTED,
            justify="center",
        ).grid(row=12, column=0, padx=16, pady=(0, 20), sticky="ew")

    # ──────────────────────────────────────────────────────────────────────────
    # Navegación
    # ──────────────────────────────────────────────────────────────────────────
    def _show_item(self, idx: int) -> None:
        if idx >= len(self._items):
            self._terminar()
            return

        self._idx = idx
        item  = self._items[idx]
        rec   = item.record
        total = len(self._items)

        self._prog_var.set(f"Factura {idx + 1} de {total} · {total - idx - 1} restantes")
        self._prov_var.set(rec.emisor_nombre or "Emisor desconocido")
        self._ced_var.set(rec.emisor_cedula or "")
        self._monto_var.set(_fmt_monto(rec.total_comprobante))
        self._fecha_var.set(rec.fecha_emision or "")

        if rec.pdf_path and Path(rec.pdf_path).exists():
            self._pdf_viewer.load_pdf(str(rec.pdf_path))
        else:
            self._pdf_viewer.show_message(
                "PDF no disponible",
                "Este comprobante no tiene PDF asociado.\nRevisá el XML para decidir."
            )

    def _decidir(self, categoria: str) -> None:
        item = self._items[self._idx]
        rec  = item.record

        item.categoria = categoria
        item.metodo    = METODO_VENDOR_CATALOG
        item.confianza = 1.0
        item.nota      = f"Decisión manual del contador: {categoria}"

        if self._recordar_var.get() and rec.emisor_cedula:
            try:
                self._engine.guardar_decision_proveedor(
                    emisor_cedula = rec.emisor_cedula,
                    emisor_nombre = rec.emisor_nombre or "",
                    categoria     = categoria,
                )
            except Exception:
                logger.warning(
                    "No se pudo guardar decisión para %s", rec.emisor_cedula, exc_info=True
                )

        self._decididos += 1
        self._show_item(self._idx + 1)

    def _omitir(self) -> None:
        self._show_item(self._idx + 1)

    # ──────────────────────────────────────────────────────────────────────────
    # Cierre
    # ──────────────────────────────────────────────────────────────────────────
    def _terminar(self) -> None:
        """Oculta el overlay y notifica con la lista actualizada."""
        callback = self._on_complete
        items    = self._items

        self.grid_remove()   # Ocultar overlay (sin destruir, por si acaso)
        self.destroy()

        if callback:
            try:
                callback(items)
            except Exception:
                logger.warning("Error en callback on_complete del overlay", exc_info=True)


# ──────────────────────────────────────────────────────────────────────────────
# Función de conveniencia
# ──────────────────────────────────────────────────────────────────────────────
def resolver_ambiguos(
    parent,
    resultados: list[CorteItem],
    engine: CorteEngine,
    on_complete: Callable[[list[CorteItem]], None] | None = None,
) -> None:
    """
    Muestra el overlay de clasificación si hay items AMBIGUO.
    Si no hay ninguno, llama on_complete directamente.

    Args:
        parent:      Ventana padre (App3Window).
        resultados:  Lista completa de CorteItem.
        engine:      CorteEngine activo con el metadata_dir del cliente.
        on_complete: Callback con la lista completa al terminar.
    """
    from gestor_contable.core.corte_engine import CATEGORIA_AMBIGUO

    ambiguos = [i for i in resultados if i.categoria == CATEGORIA_AMBIGUO]

    if not ambiguos:
        if on_complete:
            on_complete(resultados)
        return

    def _on_overlay_complete(items_actualizados: list[CorteItem]) -> None:
        # Los CorteItem ya fueron modificados in-place — notificar con lista completa
        if on_complete:
            on_complete(resultados)

    CorteClasificacionOverlay(
        parent      = parent,
        items       = ambiguos,
        engine      = engine,
        on_complete = _on_overlay_complete,
    )
