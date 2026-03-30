"""Modal overlay embebido — reemplaza messagebox y CTkToplevel de diálogos simples.

Uso básico (mensajes):
    ModalOverlay.show_error(self, "Título", "Mensaje")
    ModalOverlay.show_info(self, "Título", "Mensaje")
    ModalOverlay.show_warning(self, "Título", "Mensaje")
    ModalOverlay.show_success(self, "Título", "Mensaje")

Uso confirmación asíncrona (callback):
    ModalOverlay.show_confirm(self, "Título", "Mensaje",
                               on_yes=lambda: ..., on_no=lambda: ...)

Uso confirmación síncrona (reemplaza messagebox.askyesno y _ask):
    if ModalOverlay.ask_sync(self, "Título", "¿Continuar?"):
        ...

Uso avanzado (contenido personalizado — reemplaza CTkToplevel grandes):
    overlay, card, close_fn = ModalOverlay.build(self)
    # construir contenido en 'card' con pack/grid normalmente
    # llamar close_fn() para cerrar
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from gestor_contable.gui.icons import get_icon

# ── Paleta (idéntica al resto del proyecto) ───────────────────────────────────
BG      = "#0d0f14"
SURFACE = "#13161e"
CARD    = "#181c26"
BORDER  = "#252a38"
TEAL    = "#2dd4bf"
TEXT    = "#e8eaf0"
MUTED   = "#6b7280"
DANGER  = "#f87171"
SUCCESS = "#34d399"
WARNING = "#fbbf24"

_KIND: dict[str, str] = {
    "error":   "modal_error",
    "warning": "modal_warning",
    "info":    "modal_info",
    "success": "modal_success",
    "confirm": "modal_confirm",
}


def _f(size: int = 13, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI", size=size,
                       weight="bold" if bold else "normal")


class ModalOverlay(ctk.CTkFrame):
    """Overlay embebido: frame oscuro que cubre el padre + tarjeta centrada."""

    def __init__(
        self,
        parent,
        kind: str,
        title: str,
        message: str = "",
        on_close: Callable | None = None,
        on_yes: Callable | None = None,
        on_no: Callable | None = None,
        confirm_text: str = "Sí",
        cancel_text: str = "Cancelar",
    ):
        super().__init__(parent, fg_color=BG)
        self._on_close = on_close
        self._on_yes   = on_yes
        self._on_no    = on_no

        # Cubrir todo el padre y subir al frente
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.lift()

        # Tarjeta centrada con bordes redondeados
        card = ctk.CTkFrame(
            self,
            fg_color=CARD,
            corner_radius=16,
            border_width=1,
            border_color=BORDER,
        )
        card.place(relx=0.5, rely=0.5, anchor="center")
        card.columnconfigure(0, weight=1)

        icon_key = _KIND.get(kind, _KIND["info"])
        icon_img = get_icon(icon_key, 48)

        # Icono
        icon_lbl = ctk.CTkLabel(card, text="", image=icon_img) if icon_img else ctk.CTkLabel(card, text="")
        icon_lbl.grid(row=0, column=0, pady=(28, 0), padx=40)

        # Título
        ctk.CTkLabel(
            card, text=title,
            font=_f(15, bold=True), text_color=TEXT, wraplength=360,
        ).grid(row=1, column=0, pady=(8, 0), padx=24)

        # Mensaje (opcional)
        if message:
            ctk.CTkLabel(
                card, text=message,
                font=_f(12), text_color=MUTED, wraplength=360, justify="center",
            ).grid(row=2, column=0, pady=(8, 0), padx=24)

        # Botones
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=3, column=0, pady=(20, 24), padx=24)

        if kind == "confirm":
            ctk.CTkButton(
                btn_row, text=confirm_text, width=130,
                fg_color=TEAL, text_color=BG, font=_f(13, bold=True),
                command=self._yes,
            ).pack(side="left", padx=(0, 8))
            ctk.CTkButton(
                btn_row, text=cancel_text, width=130,
                fg_color=SURFACE, border_width=1, border_color=BORDER,
                text_color=TEXT, font=_f(13),
                command=self._no,
            ).pack(side="left")
        else:
            ctk.CTkButton(
                btn_row, text="Cerrar", width=140,
                fg_color=TEAL, text_color=BG, font=_f(13, bold=True),
                command=self._close,
            ).pack()

        # Click en el fondo oscuro → cerrar
        self.bind("<Button-1>", self._on_bg_click)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _close(self):
        cb = self._on_close
        self.destroy()
        if cb:
            cb()

    def _yes(self):
        cb = self._on_yes
        self.destroy()
        if cb:
            cb()

    def _no(self):
        cb = self._on_no
        self.destroy()
        if cb:
            cb()

    def _on_bg_click(self, event):
        if event.widget is self:
            self._close()

    # ── API estática — mensajes simples ───────────────────────────────────────

    @staticmethod
    def show_error(parent, title: str, message: str = "",
                   on_close: Callable | None = None) -> "ModalOverlay":
        return ModalOverlay(parent, "error", title, message, on_close=on_close)

    @staticmethod
    def show_warning(parent, title: str, message: str = "",
                     on_close: Callable | None = None) -> "ModalOverlay":
        return ModalOverlay(parent, "warning", title, message, on_close=on_close)

    @staticmethod
    def show_info(parent, title: str, message: str = "",
                  on_close: Callable | None = None) -> "ModalOverlay":
        return ModalOverlay(parent, "info", title, message, on_close=on_close)

    @staticmethod
    def show_success(parent, title: str, message: str = "",
                     on_close: Callable | None = None) -> "ModalOverlay":
        return ModalOverlay(parent, "success", title, message, on_close=on_close)

    # ── API estática — confirmaciones ──────────────────────────────────────────

    @staticmethod
    def show_confirm(
        parent,
        title: str,
        message: str = "",
        on_yes: Callable | None = None,
        on_no: Callable | None = None,
        confirm_text: str = "Sí",
        cancel_text: str = "Cancelar",
    ) -> "ModalOverlay":
        return ModalOverlay(
            parent, "confirm", title, message,
            on_yes=on_yes, on_no=on_no,
            confirm_text=confirm_text, cancel_text=cancel_text,
        )

    @staticmethod
    def ask_sync(
        parent,
        title: str,
        message: str = "",
        confirm_text: str = "Sí",
        cancel_text: str = "Cancelar",
    ) -> bool:
        """Confirmación síncrona — bloquea el event loop hasta que el usuario responde.

        Reemplaza messagebox.askyesno() y el patrón _ask() + wait_variable.
        Retorna True si el usuario confirmó, False si canceló.
        """
        result = [False]
        done = tk.BooleanVar(value=False)

        def on_yes():
            result[0] = True
            done.set(True)

        def on_no():
            done.set(True)

        ModalOverlay.show_confirm(
            parent, title, message,
            on_yes=on_yes, on_no=on_no,
            confirm_text=confirm_text, cancel_text=cancel_text,
        )
        parent.wait_variable(done)
        return result[0]

    # ── API avanzada — contenido personalizado ────────────────────────────────

    @staticmethod
    def build(parent) -> tuple[ctk.CTkFrame, ctk.CTkFrame, Callable]:
        """Crea overlay con tarjeta grande (88% × 88% del padre) vacía.

        Retorna (overlay, card, close_fn):
          overlay  — el frame oscuro de fondo (no necesita usarse directamente)
          card     — CTkFrame donde el llamador construye todo el contenido
          close_fn — llama overlay.destroy() para cerrar el overlay
        """
        overlay = ctk.CTkFrame(parent, fg_color=BG)
        overlay.place(x=0, y=0, relwidth=1, relheight=1)
        overlay.lift()

        card = ctk.CTkFrame(
            overlay,
            fg_color=CARD,
            corner_radius=16,
            border_width=1,
            border_color=BORDER,
        )
        card.place(relx=0.5, rely=0.5, relwidth=0.88, relheight=0.88, anchor="center")

        return overlay, card, overlay.destroy
