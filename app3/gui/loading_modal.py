"""Modal de carga con progreso real para App3."""

import customtkinter as ctk

# ── PALETA ────────────────────────────────────────────────────────────────
BG = "#0d0f14"
SURFACE = "#13161e"
CARD = "#181c26"
BORDER = "#252a38"
TEAL = "#2dd4bf"
TEXT = "#e8eaf0"
MUTED = "#6b7280"

class LoadingModal(ctk.CTkToplevel):
    """Modal de carga con barra de progreso y mensaje actualizable."""

    def __init__(self, parent, title: str = "Cargando..."):
        super().__init__(parent)
        self.title(title)
        self.geometry("500x250")
        self.resizable(False, False)

        # Centered en parent
        self.transient(parent)
        self.grab_set()

        # Estilo
        self.configure(fg_color=BG)

        # ── CONTENIDO ──
        frame = ctk.CTkFrame(self, fg_color=BG)
        frame.pack(fill="both", expand=True, padx=40, pady=40)

        # Icono + título
        title_lbl = ctk.CTkLabel(
            frame,
            text="⏳ " + title,
            font=("Segoe UI", 16, "bold"),
            text_color=TEXT,
        )
        title_lbl.pack(pady=(0, 20))

        # Mensaje de estado (actualizable)
        self.status_var = ctk.StringVar(value="Iniciando...")
        self.status_lbl = ctk.CTkLabel(
            frame,
            textvariable=self.status_var,
            font=("Segoe UI", 12),
            text_color=MUTED,
        )
        self.status_lbl.pack(pady=(0, 20))

        # Barra de progreso
        self.progress_bar = ctk.CTkProgressBar(
            frame,
            fg_color=SURFACE,
            progress_color=TEAL,
            height=6,
        )
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", pady=(0, 15))

        # Contador
        self.counter_var = ctk.StringVar(value="0/0 archivos")
        self.counter_lbl = ctk.CTkLabel(
            frame,
            textvariable=self.counter_var,
            font=("Segoe UI", 11),
            text_color=MUTED,
        )
        self.counter_lbl.pack()

        self.after(100, self.focus)

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

    def close(self):
        """Cierra modal."""
        try:
            self.destroy()
        except:
            pass
