"""Cargador centralizado de iconos PNG para la UI.

Todos los iconos están en gestor_contable/gui/icons/ (96x96 RGBA, fondo transparente).
Se usan como CTkImage para soportar pantallas HiDPI.

Uso:
    from gestor_contable.gui.icons import get_icon
    btn = ctk.CTkButton(..., image=get_icon("broom", 20), compound="left")
    lbl = ctk.CTkLabel(..., image=get_icon("modal_error", 48), text="")
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import customtkinter as ctk
from PIL import Image

_ICONS_DIR = Path(__file__).parent / "icons"

# Mapeo de nombres semánticos → archivo PNG
_FILES: dict[str, str] = {
    # Modal type icons
    "modal_error":   "modal_error.png",
    "modal_warning": "modal_warning.png",
    "modal_info":    "modal_info.png",
    "modal_success": "modal_success.png",
    "modal_confirm": "modal_confirm.png",
    # UI icons
    "loading":       "loading.png",
    "broom":         "broom.png",
    "trash":         "trash.png",
    "download":      "download.png",
    "folder":        "folder.png",
    "link":          "link.png",
    "duplicate":     "duplicate.png",
    "report":        "report.png",
    "file_pdf":      "file_pdf.png",
    "filter":        "filter.png",
    "calendar":      "calendar.png",
}


@lru_cache(maxsize=128)
def get_icon(name: str, size: int = 24) -> ctk.CTkImage | None:
    """Retorna CTkImage del icono pedido en el tamaño indicado (px).

    Retorna None si el icono no existe (no explota).
    El resultado se cachea por (name, size).
    """
    filename = _FILES.get(name)
    if not filename:
        return None
    path = _ICONS_DIR / filename
    if not path.exists():
        return None
    try:
        img = Image.open(path).convert("RGBA")
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    except Exception:
        return None
