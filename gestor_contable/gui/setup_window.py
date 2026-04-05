from __future__ import annotations

import json
import logging
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from gestor_contable.gui.fonts import *

logger = logging.getLogger(__name__)

# ── PALETA ────────────────────────────────────────────────────────────────────
BG      = "#0d0f14"
SURFACE = "#13161e"
CARD    = "#181c26"
BORDER  = "#252a38"
TEAL    = "#2dd4bf"
TEAL_DIM= "#1a9e8f"
TEXT    = "#e8eaf0"
MUTED   = "#6b7280"
DANGER  = "#f87171"
SUCCESS = "#34d399"
WARNING = "#fbbf24"

_LOCAL_SETTINGS = Path.home() / ".gestor_contable" / "local_settings.json"


def _detect_onedrive() -> str:
    """Intenta detectar la ruta de OneDrive automáticamente."""
    import os
    for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        val = os.environ.get(key, "")
        if val and Path(val).exists():
            return val

    home = Path.home()
    try:
        candidates = sorted(
            f for f in home.iterdir()
            if f.is_dir() and f.name.lower().startswith("onedrive")
        )
        if candidates:
            return str(candidates[0])
    except Exception:
        pass
    return ""


class SetupWindow(ctk.CTk):
    """
    Ventana de configuración inicial. Se muestra solo la primera vez
    (o cuando no se puede encontrar la unidad Z:).
    Al completar, guarda ~/.gestor_contable/local_settings.json y se cierra.
    """

    def __init__(self, reason: str = "") -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("Gestor Contable — Configuración inicial")
        self.geometry("560x480")
        self.minsize(520, 440)
        self.configure(fg_color=BG)
        self.resizable(False, False)

        # Centrar en pantalla
        self.update_idletasks()
        x = (self.winfo_screenwidth()  - 560) // 2
        y = (self.winfo_screenheight() - 480) // 2
        self.geometry(f"560x480+{x}+{y}")

        self._reason = reason
        self._completed = False
        self._build()

        detected = _detect_onedrive()
        if detected:
            self._path_entry.insert(0, detected)
            self._validate_path(detected)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # ── Header ────────────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=56)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header, text="📊",
            fg_color="#1a3a36", corner_radius=8,
            width=32, height=32, font=F_AVATAR(),
        ).grid(row=0, column=0, padx=(16, 8), pady=12)

        ctk.CTkLabel(
            header,
            text="Clasificador Contable",
            font=F_HEADING(),
            text_color=TEXT,
        ).grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(
            header, text="Configuración inicial",
            font=F_LABEL(),
            fg_color=CARD, text_color=MUTED, corner_radius=20,
        ).grid(row=0, column=2, padx=16, ipadx=10, ipady=3)

        # ── Aviso (si hay razón específica) ───────────────────────────────────
        if self._reason:
            warn = ctk.CTkFrame(self, fg_color="#2a1a0d", corner_radius=0, height=36)
            warn.grid(row=1, column=0, sticky="ew")
            warn.grid_propagate(False)
            ctk.CTkLabel(
                warn, text=f"⚠  {self._reason}",
                font=F_LABEL(),
                text_color=WARNING,
            ).pack(side="left", padx=16, pady=8)
        else:
            # fila vacía para mantener layout
            ctk.CTkFrame(self, fg_color=BG, height=8).grid(row=1, column=0)

        # ── Título ────────────────────────────────────────────────────────────
        intro = ctk.CTkFrame(self, fg_color="transparent")
        intro.grid(row=2, column=0, sticky="ew", padx=40, pady=(28, 0))

        ctk.CTkLabel(
            intro, text="Primera configuración",
            font=F_TITLE(),
            text_color=TEXT, anchor="w",
        ).pack(anchor="w")

        ctk.CTkLabel(
            intro,
            text="Indica dónde está tu carpeta de OneDrive.\n"
                 "El sistema la montará automáticamente como disco Z: cada vez que abras la app.",
            font=F_BODY(),
            text_color=MUTED, justify="left", anchor="w",
        ).pack(anchor="w", pady=(4, 0))

        # ── Tarjeta de configuración ──────────────────────────────────────────
        card = ctk.CTkFrame(self, fg_color=CARD, border_width=1, border_color=BORDER, corner_radius=14)
        card.grid(row=3, column=0, sticky="nsew", padx=40, pady=24)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text="CARPETA DE ONEDRIVE",
            font=F_SMALL_BOLD(),
            text_color=TEAL, anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=24, pady=(20, 0))

        ctk.CTkLabel(
            card, text="Ruta de la carpeta local de OneDrive en este equipo",
            font=F_LABEL(),
            text_color=MUTED, anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=24, pady=(8, 4))

        # Entry + Botón examinar
        path_row = ctk.CTkFrame(card, fg_color="transparent")
        path_row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=24)
        path_row.grid_columnconfigure(0, weight=1)

        self._path_entry = ctk.CTkEntry(
            path_row,
            placeholder_text="Ej: C:/Users/TuNombre/OneDrive",
            fg_color=SURFACE, border_color=BORDER,
            text_color=TEXT, placeholder_text_color="#3a4055",
            font=F_BODY(),
            height=40, corner_radius=10,
        )
        self._path_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._path_entry.bind("<KeyRelease>", self._on_path_key)

        ctk.CTkButton(
            path_row,
            text="Examinar",
            font=F_BODY(),
            fg_color=SURFACE, hover_color=BORDER,
            text_color=TEXT, border_color=BORDER, border_width=1,
            height=40, corner_radius=10, width=90,
            command=self._browse,
        ).grid(row=0, column=1)

        # Feedback de validación
        self._feedback = ctk.CTkLabel(
            card, text="",
            font=F_LABEL(),
            text_color=MUTED, anchor="w",
        )
        self._feedback.grid(row=3, column=0, columnspan=2, sticky="w", padx=24, pady=(6, 0))

        # Estructura esperada dentro de OneDrive
        hint = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=8)
        hint.grid(row=4, column=0, columnspan=2, sticky="ew", padx=24, pady=(12, 0))
        ctk.CTkLabel(
            hint,
            text="  La app usará:  <OneDrive>/DATA/PF-{año}/CLIENTES/",
            font=F_LABEL(),
            text_color=MUTED, anchor="w",
        ).pack(anchor="w", padx=8, pady=8)

        # Botón guardar
        self._btn_save = ctk.CTkButton(
            card,
            text="Guardar y continuar  ->",
            font=F_BUTTON(),
            fg_color=TEAL, hover_color=TEAL_DIM,
            text_color="#0d1a18",
            height=44, corner_radius=12,
            state="disabled",
            command=self._save,
        )
        self._btn_save.grid(row=5, column=0, columnspan=2, sticky="ew",
                            padx=24, pady=(16, 24))

    # ── LÓGICA ────────────────────────────────────────────────────────────────
    def _browse(self) -> None:
        current = self._path_entry.get().strip()
        initial = current if current and Path(current).exists() else str(Path.home())
        chosen = filedialog.askdirectory(
            title="Selecciona tu carpeta de OneDrive",
            initialdir=initial,
        )
        if chosen:
            self._path_entry.delete(0, "end")
            self._path_entry.insert(0, chosen)
            self._validate_path(chosen)

    def _on_path_key(self, _event=None) -> None:
        self._validate_path(self._path_entry.get().strip())

    def _validate_path(self, raw: str) -> None:
        if not raw:
            self._set_feedback("", MUTED)
            self._btn_save.configure(state="disabled")
            return

        p = Path(raw)
        if not p.exists():
            self._set_feedback("✗  La carpeta no existe", DANGER)
            self._btn_save.configure(state="disabled")
            return
        if not p.is_dir():
            self._set_feedback("✗  La ruta no es una carpeta", DANGER)
            self._btn_save.configure(state="disabled")
            return

        # Verificar si ya tiene estructura DATA/
        data_path = p / "DATA"
        if data_path.exists():
            self._set_feedback(f"✓  Carpeta válida  ·  DATA/ encontrada", SUCCESS)
        else:
            self._set_feedback("✓  Carpeta válida  ·  (DATA/ se creará al usar la app)", TEAL)

        self._btn_save.configure(state="normal")

    def _set_feedback(self, text: str, color: str) -> None:
        self._feedback.configure(text=text, text_color=color)

    def _save(self) -> None:
        raw = self._path_entry.get().strip()
        p = Path(raw)
        if not p.exists():
            self._set_feedback("✗  Ruta inválida, no se puede guardar", DANGER)
            return

        try:
            _LOCAL_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            existing: dict = {}
            if _LOCAL_SETTINGS.exists():
                try:
                    existing = json.loads(_LOCAL_SETTINGS.read_text(encoding="utf-8"))
                except Exception:
                    pass
            existing["subst_source"] = str(p)
            _LOCAL_SETTINGS.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("Configuración guardada: subst_source=%s", p)
        except Exception as exc:
            self._set_feedback(f"✗  No se pudo guardar: {exc}", DANGER)
            logger.error("Error guardando local_settings.json: %s", exc)
            return

        self._completed = True
        self.destroy()
