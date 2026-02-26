from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import customtkinter as ctk

from app3.bootstrap import bootstrap_legacy_paths
from app3.config import client_root, metadata_dir
from app3.core.session import ClientSession, resolve_client_session

bootstrap_legacy_paths()

from facturacion_system.core.settings import get_setting  # noqa: E402

# ── PALETA ────────────────────────────────────────────────────────────────────
BG       = "#0d0f14"
SURFACE  = "#13161e"
CARD     = "#181c26"
BORDER   = "#252a38"
TEAL     = "#2dd4bf"
TEAL_DIM = "#1a9e8f"
TEXT     = "#e8eaf0"
MUTED    = "#6b7280"
DANGER   = "#f87171"
SUCCESS  = "#34d399"
WARNING  = "#fbbf24"

# ── FUENTES (lazy — se crean solo después de que existe la ventana raíz) ──────
_fonts: dict = {}

def _f(key: str, size: int, weight: str = "normal") -> ctk.CTkFont:
    if key not in _fonts:
        _fonts[key] = ctk.CTkFont(family="Segoe UI", size=size, weight=weight)
    return _fonts[key]

def F_TITLE()   -> ctk.CTkFont: return _f("title",   30, "bold")
def F_HEADING() -> ctk.CTkFont: return _f("heading", 16, "bold")
def F_LABEL()   -> ctk.CTkFont: return _f("label",   12)
def F_SMALL()   -> ctk.CTkFont: return _f("small",   11)
def F_BTN()     -> ctk.CTkFont: return _f("btn",     13, "bold")
def F_AVATAR()  -> ctk.CTkFont: return _f("avatar",  16, "bold")
def F_NAME()    -> ctk.CTkFont: return _f("name",    13, "bold")
def F_META()    -> ctk.CTkFont: return _f("meta",    11)


def _digits(text: str) -> str:
    import re
    return re.sub(r"\D", "", text or "")


def _initials(name: str) -> str:
    words = [w for w in name.split() if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return name[:2].upper() if name else "??"


def _load_saved_clients(year: int) -> list[dict]:
    """
    Lee las carpetas de clientes del disco y sus conteos de clasificacion.
    Retorna lista de dicts con: nombre, cedula, pendientes, clasificadas, year.
    """
    base = client_root(year)
    if not base.exists():
        return []

    clients = []
    for folder in sorted(base.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue

        pendientes = 0
        clasificadas = 0
        db_path = folder / ".metadata" / "clasificacion.sqlite"
        if db_path.exists():
            try:
                with sqlite3.connect(db_path) as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM clasificaciones WHERE estado='clasificado'"
                    ).fetchone()
                    clasificadas = row[0] if row else 0
                    row2 = conn.execute(
                        "SELECT COUNT(*) FROM clasificaciones WHERE estado != 'clasificado'"
                    ).fetchone()
                    pendientes = row2[0] if row2 else 0
            except Exception:
                pass

        clients.append({
            "nombre": folder.name,
            "cedula": "",
            "pendientes": pendientes,
            "clasificadas": clasificadas,
            "year": year,
            "folder": folder,
        })

    return clients


# ── TARJETA DE CLIENTE ─────────────────────────────────────────────────────────
class ClientCard(ctk.CTkFrame):
    def __init__(self, parent, client: dict, on_click, **kwargs):
        # Frame exterior = borde
        super().__init__(
            parent,
            fg_color=BORDER,
            corner_radius=14,
            **kwargs,
        )
        self._client = client
        self._on_click = on_click
        self._hovered = False

        # Frame interior = fondo de la tarjeta
        self._inner = ctk.CTkFrame(self, fg_color=CARD, corner_radius=12)
        self._inner.pack(fill="both", expand=True, padx=1, pady=1)
        self._inner.grid_columnconfigure(1, weight=1)

        # Avatar
        initials = _initials(client["nombre"])
        avatar = ctk.CTkFrame(self._inner, fg_color="#1a3a36", corner_radius=10,
                               width=44, height=44)
        avatar.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=12, sticky="ns")
        avatar.grid_propagate(False)
        ctk.CTkLabel(avatar, text=initials, font=F_AVATAR(),
                     text_color=TEAL).place(relx=.5, rely=.5, anchor="center")

        # Nombre
        nombre_truncado = client["nombre"][:42] + "…" if len(client["nombre"]) > 42 else client["nombre"]
        ctk.CTkLabel(self._inner, text=nombre_truncado, font=F_NAME(),
                     text_color=TEXT, anchor="w").grid(
            row=0, column=1, sticky="sw", pady=(12, 1))

        # Pills de estado
        pills_frame = ctk.CTkFrame(self._inner, fg_color="transparent")
        pills_frame.grid(row=1, column=1, sticky="nw", pady=(0, 12))

        # Pill pendientes
        if client["pendientes"] > 0:
            pill_color = "#2d2010"
            pill_text_color = WARNING
            pill_text = f"{client['pendientes']} pendientes"
        else:
            pill_color = "#0d2a1e"
            pill_text_color = SUCCESS
            pill_text = "✓ Al día"

        ctk.CTkLabel(pills_frame, text=pill_text, font=F_SMALL(),
                     fg_color=pill_color, text_color=pill_text_color,
                     corner_radius=20, padx=8, pady=2).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(pills_frame, text=f"PF-{client['year']}", font=F_SMALL(),
                     fg_color=SURFACE, text_color=MUTED,
                     corner_radius=20, padx=8, pady=2).pack(side="left")

        # Flecha
        self._arrow = ctk.CTkLabel(self._inner, text="→", font=F_HEADING(),
                                    text_color=MUTED)
        self._arrow.grid(row=0, column=2, rowspan=2, padx=(0, 14))

        # Hover — bind en todos los widgets internos
        for w in [self, self._inner, avatar, self._arrow, pills_frame]:
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<Button-1>", self._on_click_evt)

    def _on_enter(self, _e=None):
        self.configure(fg_color=TEAL_DIM)
        self._inner.configure(fg_color="#1a2535")
        self._arrow.configure(text_color=TEAL)

    def _on_leave(self, _e=None):
        self.configure(fg_color=BORDER)
        self._inner.configure(fg_color=CARD)
        self._arrow.configure(text_color=MUTED)

    def _on_click_evt(self, _e=None):
        self._on_click(self._client)


# ── VISTA DE SESIÓN ────────────────────────────────────────────────────────────
class SessionView(ctk.CTkToplevel):
    """
    Pantalla completa de inicio de sesión.
    Llama a on_session_resolved(session: ClientSession) cuando el usuario confirma.
    """

    def __init__(self, parent, on_session_resolved, **kwargs):
        super().__init__(parent, **kwargs)
        self._on_resolved = on_session_resolved
        self._debounce_id = None
        self._resolve_thread = None

        self.title("Clasificador Contable — Iniciar sesión")
        self.geometry("1100x640")
        self.minsize(900, 560)
        self.configure(fg_color=BG)
        self.resizable(True, True)

        # Centrar en pantalla
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - 1100) // 2
        y = (sh - 640) // 2
        self.geometry(f"1100x640+{x}+{y}")

        self._build()
        self._load_clients_async()

        # Bloquear ventana padre mientras esta está abierta
        self.grab_set()
        self.focus_force()

    # ── CONSTRUCCIÓN ──────────────────────────────────────────────────────────
    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_header()
        self._build_left()
        self._build_right()
        self._build_divider()

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=56)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        # Logo
        logo_icon = ctk.CTkLabel(header, text="📊",
                                  fg_color="#1a3a36", corner_radius=8,
                                  width=32, height=32, font=ctk.CTkFont(size=16))
        logo_icon.grid(row=0, column=0, padx=(16, 8), pady=12)

        ctk.CTkLabel(
            header,
            text="Clasificador  Contable",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=TEXT,
        ).grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(
            header, text="APP 3 · v1.0",
            font=F_SMALL(), text_color=MUTED,
            fg_color=CARD, corner_radius=20,
        ).grid(row=0, column=2, padx=16, pady=12, ipadx=10, ipady=3)

    def _build_left(self):
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(60, 40), pady=50)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)

        # Título
        ctk.CTkLabel(left, text="Iniciar sesión",
                      font=F_TITLE(), text_color=TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkLabel(left, text="Ingresa la cédula del cliente para cargar\nsu carpeta de documentos",
                      font=F_LABEL(), text_color=MUTED, justify="left").grid(
            row=1, column=0, sticky="w", pady=(6, 28))

        # Tarjeta
        card_border = ctk.CTkFrame(left, fg_color=BORDER, corner_radius=20)
        card_border.grid(row=2, column=0, sticky="new")

        card = ctk.CTkFrame(card_border, fg_color=CARD, corner_radius=18)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="NUEVA SESIÓN",
                      font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
                      text_color=TEAL).grid(row=0, column=0, sticky="w",
                                            padx=24, pady=(22, 0))

        ctk.CTkLabel(card, text="Cédula jurídica o física",
                      font=F_SMALL(), text_color=MUTED).grid(
            row=1, column=0, sticky="w", padx=24, pady=(14, 4))

        self._cedula_entry = ctk.CTkEntry(
            card,
            placeholder_text="Ej: 3-101-085674",
            fg_color=SURFACE,
            border_color=BORDER,
            text_color=TEXT,
            placeholder_text_color="#3a4055",
            font=F_LABEL(),
            height=44,
            corner_radius=12,
        )
        self._cedula_entry.grid(row=2, column=0, sticky="ew", padx=24)
        self._cedula_entry.bind("<KeyRelease>", self._on_cedula_change)

        # Preview del nombre
        self._preview_frame = ctk.CTkFrame(
            card, fg_color="#0d2a1e", corner_radius=10, height=42)
        self._preview_frame.grid(row=3, column=0, sticky="ew",
                                  padx=24, pady=(10, 0))
        self._preview_frame.grid_columnconfigure(1, weight=1)
        self._preview_frame.grid_propagate(False)

        self._preview_dot = ctk.CTkLabel(
            self._preview_frame, text="●", font=F_SMALL(),
            text_color=MUTED, width=20)
        self._preview_dot.grid(row=0, column=0, padx=(14, 6), pady=10)

        self._preview_name = ctk.CTkLabel(
            self._preview_frame, text="Ingresa una cédula para buscar",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=MUTED, anchor="w")
        self._preview_name.grid(row=0, column=1, sticky="ew", pady=10)

        self._preview_status = ctk.CTkLabel(
            self._preview_frame, text="",
            font=F_SMALL(), text_color=MUTED)
        self._preview_status.grid(row=0, column=2, padx=(0, 14), pady=10)

        # Botón continuar
        self._btn_continuar = ctk.CTkButton(
            card,
            text="Continuar  →",
            font=F_BTN(),
            fg_color=TEAL,
            hover_color=TEAL_DIM,
            text_color="#0d1a18",
            corner_radius=12,
            height=46,
            state="disabled",
            command=self._on_continuar,
        )
        self._btn_continuar.grid(row=4, column=0, sticky="ew",
                                  padx=24, pady=(14, 24))

    def _build_right(self):
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew", padx=(40, 60), pady=50)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        # Header
        header_row = ctk.CTkFrame(right, fg_color="transparent")
        header_row.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        header_row.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(header_row, fg_color="transparent")
        title_row.pack(anchor="w")

        ctk.CTkLabel(title_row, text="⚡", font=F_HEADING(),
                      text_color=TEAL).pack(side="left")
        ctk.CTkLabel(title_row, text=" Accesos rápidos",
                      font=F_HEADING(), text_color=TEXT).pack(side="left")

        self._count_badge = ctk.CTkLabel(
            title_row, text="0",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color=TEAL, text_color="#0d1a18",
            corner_radius=20, width=28, height=20,
        )
        self._count_badge.pack(side="left", padx=(8, 0))

        ctk.CTkLabel(header_row,
                      text="Clientes con carpeta activa en este equipo",
                      font=F_SMALL(), text_color=MUTED).pack(anchor="w", padx=(22, 0))

        # Lista scrollable
        self._client_scroll = ctk.CTkScrollableFrame(
            right, fg_color="transparent",
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=MUTED,
        )
        self._client_scroll.grid(row=1, column=0, sticky="nsew")
        self._client_scroll.grid_columnconfigure(0, weight=1)

        # Estado inicial mientras carga
        self._loading_label = ctk.CTkLabel(
            self._client_scroll,
            text="Cargando clientes...",
            font=F_LABEL(), text_color=MUTED,
        )
        self._loading_label.grid(row=0, column=0, pady=40)

    def _build_divider(self):
        div = ctk.CTkFrame(self, fg_color=BORDER, width=1, corner_radius=0)
        div.grid(row=1, column=0, columnspan=2, sticky="ns",
                  padx=(self.winfo_reqwidth() // 2, 0), pady=40)

    # ── CARGA ASÍNCRONA DE CLIENTES ───────────────────────────────────────────
    def _load_clients_async(self):
        def worker():
            try:
                year = int(get_setting("fiscal_year"))
                clients = _load_saved_clients(year)
            except Exception:
                clients = []
            self.after(0, lambda: self._render_clients(clients))

        threading.Thread(target=worker, daemon=True).start()

    def _render_clients(self, clients: list[dict]):
        # Limpiar loading
        for w in self._client_scroll.winfo_children():
            w.destroy()

        self._count_badge.configure(text=str(len(clients)))

        if not clients:
            empty = ctk.CTkFrame(
                self._client_scroll,
                fg_color="transparent",
                border_color=BORDER, border_width=2,
                corner_radius=16,
            )
            empty.grid(row=0, column=0, sticky="ew", pady=20, padx=4)
            ctk.CTkLabel(empty, text="📂", font=ctk.CTkFont(size=32),
                          text_color=MUTED).pack(pady=(28, 8))
            ctk.CTkLabel(empty,
                          text="No hay clientes registrados\nen este equipo todavía.",
                          font=F_LABEL(), text_color=MUTED,
                          justify="center").pack(pady=(0, 28))
            return

        for i, client in enumerate(clients):
            card = ClientCard(
                self._client_scroll,
                client=client,
                on_click=self._on_client_card_click,
            )
            card.grid(row=i, column=0, sticky="ew", pady=(0, 8), padx=4)

    # ── LÓGICA DE BÚSQUEDA ────────────────────────────────────────────────────
    def _on_cedula_change(self, _event=None):
        if self._debounce_id:
            self.after_cancel(self._debounce_id)
        raw = _digits(self._cedula_entry.get())
        if len(raw) < 9:
            self._set_preview_idle()
            self._btn_continuar.configure(state="disabled")
            return
        self._set_preview_searching()
        self._debounce_id = self.after(500, self._resolve_cedula)

    def _resolve_cedula(self):
        cedula = self._cedula_entry.get().strip()

        def worker():
            try:
                session = resolve_client_session(cedula)
                self.after(0, lambda s=session: self._on_resolve_ok(s))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_resolve_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_resolve_ok(self, session: ClientSession):
        self._pending_session = session
        self._set_preview_found(session.nombre)
        self._btn_continuar.configure(state="normal")

    def _on_resolve_error(self, msg: str):
        self._pending_session = None
        self._set_preview_error(msg)
        self._btn_continuar.configure(state="disabled")

    def _on_client_card_click(self, client: dict):
        """Clic en acceso rápido — resuelve sesión directo desde la carpeta."""
        folder: Path = client["folder"]
        year: int = client["year"]
        nombre: str = client["nombre"]

        # Llenar input visualmente
        self._cedula_entry.delete(0, "end")
        self._cedula_entry.insert(0, nombre)

        self._set_preview_searching()
        self._btn_continuar.configure(state="disabled")

        def worker():
            try:
                # Construir sesión directamente desde la carpeta conocida
                session = ClientSession(
                    cedula="",
                    nombre=nombre,
                    folder=folder,
                    year=year,
                )
                self.after(0, lambda s=session: self._on_resolve_ok(s))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_resolve_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_continuar(self):
        if hasattr(self, "_pending_session") and self._pending_session:
            session = self._pending_session
            self.grab_release()
            self.destroy()
            self._on_resolved(session)

    # ── ESTADOS DEL PREVIEW ───────────────────────────────────────────────────
    def _set_preview_idle(self):
        self._preview_frame.configure(fg_color="#1a1e2a")
        self._preview_dot.configure(text_color=MUTED)
        self._preview_name.configure(
            text="Ingresa una cédula para buscar", text_color=MUTED)
        self._preview_status.configure(text="")

    def _set_preview_searching(self):
        self._preview_frame.configure(fg_color="#1a1e2a")
        self._preview_dot.configure(text_color=TEAL)
        self._preview_name.configure(text="Buscando...", text_color=MUTED)
        self._preview_status.configure(text="")

    def _set_preview_found(self, nombre: str):
        truncado = nombre[:45] + "…" if len(nombre) > 45 else nombre
        self._preview_frame.configure(fg_color="#0d2a1e")
        self._preview_dot.configure(text_color=TEAL)
        self._preview_name.configure(text=truncado, text_color=TEAL)
        self._preview_status.configure(text="✓", text_color=SUCCESS)

    def _set_preview_error(self, msg: str):
        short = msg[:60] + "…" if len(msg) > 60 else msg
        self._preview_frame.configure(fg_color="#2a0d0d")
        self._preview_dot.configure(text_color=DANGER)
        self._preview_name.configure(text=short, text_color=DANGER)
        self._preview_status.configure(text="✗", text_color=DANGER)
