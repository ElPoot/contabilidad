"""Overlay de carga integrado (no ventana separada)."""

import customtkinter as ctk

# ── PALETA ────────────────────────────────────────────────────────────────
BG = "#0d0f14"
SURFACE = "#13161e"
CARD = "#181c26"
BORDER = "#252a38"
TEAL = "#2dd4bf"
TEXT = "#e8eaf0"
MUTED = "#6b7280"

class LoadingOverlay(ctk.CTkFrame):
    """Overlay de carga integrado como Frame que se superpone en la ventana principal.

    No es una ventana separada (CTkToplevel), sino un Frame que cubre todo
    y se ubica encima del contenido (usando grid con row/column 0, sticky="nsew").
    """

    def __init__(self, parent):
        super().__init__(parent, fg_color=f"rgba(13, 15, 20, 0.85)")  # Semi-transparente

        # Crear frame central
        center_frame = ctk.CTkFrame(self, fg_color=CARD, corner_radius=12)
        center_frame.place(relx=0.5, rely=0.5, anchor="center", width=500, height=280)

        # Icono + título
        title_lbl = ctk.CTkLabel(
            center_frame,
            text="⏳ Cargando facturas...",
            font=("Segoe UI", 16, "bold"),
            text_color=TEXT,
        )
        title_lbl.pack(pady=(30, 15))

        # Mensaje de estado (actualizable)
        self.status_var = ctk.StringVar(value="Iniciando...")
        self.status_lbl = ctk.CTkLabel(
            center_frame,
            textvariable=self.status_var,
            font=("Segoe UI", 12),
            text_color=MUTED,
        )
        self.status_lbl.pack(pady=(0, 20))

        # Barra de progreso
        self.progress_bar = ctk.CTkProgressBar(
            center_frame,
            fg_color=SURFACE,
            progress_color=TEAL,
            height=8,
        )
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=30, pady=(0, 15))

        # Contador
        self.counter_var = ctk.StringVar(value="0/0 archivos")
        self.counter_lbl = ctk.CTkLabel(
            center_frame,
            textvariable=self.counter_var,
            font=("Segoe UI", 11),
            text_color=MUTED,
        )
        self.counter_lbl.pack(pady=(0, 30))

    def update_status(self, message: str):
        """Actualiza mensaje de estado."""
        self.status_var.set(message)
        self.update_idletasks()

    def update_progress(self, current: int, total: int):
        """Actualiza barra de progreso."""
        pct = current / total if total > 0 else 0
        self.progress_bar.set(pct)
        self.counter_var.set(f"{current}/{total} archivos")
        self.update_idletasks()
