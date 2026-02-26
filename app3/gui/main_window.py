from __future__ import annotations

import calendar
import csv
import threading
from datetime import datetime
from decimal import Decimal
from queue import Queue

import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk  # Treeview + diálogos

from app3.config import metadata_dir
from app3.core.catalog import CatalogManager
from app3.core.classifier import ClassificationDB, classify_record
from app3.core.factura_index import FacturaIndexer
from app3.core.models import FacturaRecord
from app3.core.session import ClientSession
from app3.gui.pdf_viewer import PDFViewer
from app3.gui.session_view import SessionView

# ── PALETA (misma que session_view) ──────────────────────────────────────────
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

# ── FUENTES (lazy — se crean solo despues de que existe la ventana raiz) ─────
_fonts: dict = {}

def _f(key: str, size: int, weight: str = "normal") -> ctk.CTkFont:
    if key not in _fonts:
        _fonts[key] = ctk.CTkFont(family="Segoe UI", size=size, weight=weight)
    return _fonts[key]

def F_TITLE()  -> ctk.CTkFont: return _f("mw_title",  11, "bold")
def F_LABEL()  -> ctk.CTkFont: return _f("mw_label",  12)
def F_SMALL()  -> ctk.CTkFont: return _f("mw_small",  11)
def F_BTN()    -> ctk.CTkFont: return _f("mw_btn",    13, "bold")

ESTADO_ICON = {
    "clasificado":   "✓",
    "pendiente":     "·",
    "pendiente_pdf": "!",
    "sin_xml":       "—",
}

# Texto corto para la columna Estado
ESTADO_LABEL = {
    "clasificado":   "clasificado",
    "pendiente":     "pendiente",
    "pendiente_pdf": "sin PDF",
    "sin_xml":       "sin XML",
}

def _fmt_amount(value: str) -> str:
    """Formatea montos como App 2: 137 131,77 (miles con espacio, decimales con coma)."""
    try:
        v = str(value).strip().replace(",", ".")
        f = float(v)
        # Separador de miles = espacio, decimal = coma
        parts = f"{abs(f):,.2f}".split(".")
        integer_part = parts[0].replace(",", " ")
        result = f"{integer_part},{parts[1]}"
        return f"-{result}" if f < 0 else result
    except (ValueError, TypeError):
        return str(value) if value else "—"

def _short_name(name: str, max_len: int = 34) -> str:
    """Abrevia razones sociales como App 2."""
    base = str(name or "").strip().upper()
    for long_form, short_form in [
        ("SOCIEDAD ANONIMA", "S.A."),
        ("SOCIEDAD ANÓNIMA", "S.A."),
        ("SOCIEDAD DE RESPONSABILIDAD LIMITADA", "S.R.L."),
        ("SOCIEDAD RESPONSABILIDAD LIMITADA", "S.R.L."),
        ("COMPANIA LIMITADA", "LTDA."),
        ("COMPAÑIA LIMITADA", "LTDA."),
        ("LIMITADA", "LTDA."),
    ]:
        base = base.replace(long_form, short_form)
    base = " ".join(base.split())
    return base[:max_len]

# Inyectar estilos oscuros en el Treeview de ttk
_TREE_STYLE_DONE = False

def _apply_tree_style():
    global _TREE_STYLE_DONE
    if _TREE_STYLE_DONE:
        return
    style = ttk.Style()
    style.theme_use("default")
    style.configure("Dark.Treeview",
        background=CARD, foreground=TEXT,
        fieldbackground=CARD, borderwidth=0,
        rowheight=19, font=("Segoe UI", 9),
    )
    style.configure("Dark.Treeview.Heading",
        background=SURFACE, foreground=MUTED,
        borderwidth=0, font=("Segoe UI", 8, "bold"),
    )
    style.map("Dark.Treeview",
        background=[("selected", "#1a3a36")],
        foreground=[("selected", TEAL)],
    )
    style.configure("Dark.Vertical.TScrollbar",
        background=SURFACE, troughcolor=BG,
        borderwidth=0, arrowsize=12,
    )
    _TREE_STYLE_DONE = True


class DatePickerPopup(ctk.CTkToplevel):
    def __init__(self, parent, on_pick, initial_value: str = ""):
        super().__init__(parent)
        self.title("Seleccionar fecha")
        self.configure(fg_color=CARD)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._on_pick = on_pick

        dt = self._parse_date(initial_value) or datetime.today()
        self._year = dt.year
        self._month = dt.month

        self._body = ctk.CTkFrame(self, fg_color=CARD)
        self._body.pack(padx=8, pady=8, fill="both", expand=True)
        self._draw()

    @staticmethod
    def _parse_date(text: str):
        raw = (text or "").strip()
        if not raw:
            return None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return None

    def _draw(self):
        for w in self._body.winfo_children():
            w.destroy()

        top = ctk.CTkFrame(self._body, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(2, 8))
        ctk.CTkButton(top, text="◀", width=32, height=28, fg_color=SURFACE,
                      hover_color=BORDER, command=self._prev_month).pack(side="left")
        ctk.CTkLabel(top, text=f"{calendar.month_name[self._month]} {self._year}",
                      text_color=TEXT, font=F_LABEL()).pack(side="left", expand=True)
        ctk.CTkButton(top, text="▶", width=32, height=28, fg_color=SURFACE,
                      hover_color=BORDER, command=self._next_month).pack(side="right")

        grid = ctk.CTkFrame(self._body, fg_color="transparent")
        grid.pack(padx=4, pady=(0, 8))
        for i, name in enumerate(["Lu", "Ma", "Mi", "Ju", "Vi", "Sá", "Do"]):
            ctk.CTkLabel(grid, text=name, text_color=MUTED, font=F_SMALL()).grid(row=0, column=i, padx=3, pady=2)

        for r, week in enumerate(calendar.monthcalendar(self._year, self._month), start=1):
            for c, day in enumerate(week):
                if day == 0:
                    ctk.CTkLabel(grid, text=" ", width=30).grid(row=r, column=c, padx=2, pady=2)
                    continue
                ctk.CTkButton(
                    grid,
                    text=str(day),
                    width=30,
                    height=28,
                    fg_color=SURFACE,
                    hover_color=TEAL_DIM,
                    text_color=TEXT,
                    command=lambda dd=day: self._pick_day(dd),
                ).grid(row=r, column=c, padx=2, pady=2)

        bottom = ctk.CTkFrame(self._body, fg_color="transparent")
        bottom.pack(fill="x", padx=4)
        ctk.CTkButton(bottom, text="Hoy", width=70, fg_color=SURFACE, hover_color=BORDER,
                      command=self._pick_today).pack(side="left")
        ctk.CTkButton(bottom, text="Limpiar", width=80, fg_color=SURFACE, hover_color=BORDER,
                      command=lambda: self._emit("")).pack(side="right")

    def _prev_month(self):
        self._month -= 1
        if self._month == 0:
            self._month = 12
            self._year -= 1
        self._draw()

    def _next_month(self):
        self._month += 1
        if self._month == 13:
            self._month = 1
            self._year += 1
        self._draw()

    def _pick_day(self, day: int):
        self._emit(f"{day:02d}/{self._month:02d}/{self._year:04d}")

    def _pick_today(self):
        now = datetime.today()
        self._emit(f"{now.day:02d}/{now.month:02d}/{now.year:04d}")

    def _emit(self, value: str):
        self._on_pick(value)
        self.destroy()


def _parse_date_any(text: str):
    raw = (text or "").strip()
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


class DatePickerDropdown(ctk.CTkToplevel):
    """Calendario anclado tipo dropdown (sin barra de título)."""

    def __init__(self, parent, on_pick, initial_value: str = "", x: int = 120, y: int = 120):
        super().__init__(parent)
        self.overrideredirect(True)
        # CTkToplevel no permite transparencia en fg_color.
        self.configure(fg_color=CARD)
        self.attributes("-topmost", True)
        self._on_pick = on_pick
        dt = _parse_date_any(initial_value) or datetime.today()
        self._year = dt.year
        self._month = dt.month
        self.geometry(f"290x270+{x}+{y}")

        self._card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10, border_width=1, border_color=BORDER)
        self._card.pack(fill="both", expand=True)

        self.bind("<Escape>", lambda _e: self._safe_close())
        self.bind("<FocusOut>", lambda _e: self._safe_close())

        self._draw()
        self.after(10, self.focus_force)

    def _safe_close(self):
        try:
            self.destroy()
        except Exception:
            pass

    def _draw(self):
        for w in self._card.winfo_children():
            w.destroy()

        top = ctk.CTkFrame(self._card, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(8, 6))
        ctk.CTkButton(top, text="◀", width=30, height=28, fg_color=SURFACE,
                      hover_color=BORDER, command=self._prev_month).pack(side="left")
        ctk.CTkLabel(top, text=f"{calendar.month_name[self._month]} {self._year}",
                     text_color=TEXT, font=F_LABEL()).pack(side="left", expand=True)
        ctk.CTkButton(top, text="▶", width=30, height=28, fg_color=SURFACE,
                      hover_color=BORDER, command=self._next_month).pack(side="right")

        grid = ctk.CTkFrame(self._card, fg_color="transparent")
        grid.pack(padx=8, pady=(0, 8))
        for i, name in enumerate(["Lu", "Ma", "Mi", "Ju", "Vi", "Sá", "Do"]):
            ctk.CTkLabel(grid, text=name, text_color=MUTED, font=F_SMALL()).grid(row=0, column=i, padx=3, pady=2)

        for r, week in enumerate(calendar.monthcalendar(self._year, self._month), start=1):
            for c, day in enumerate(week):
                if day == 0:
                    ctk.CTkLabel(grid, text=" ", width=30).grid(row=r, column=c, padx=2, pady=2)
                    continue
                ctk.CTkButton(
                    grid,
                    text=str(day),
                    width=30,
                    height=28,
                    fg_color=SURFACE,
                    hover_color=TEAL_DIM,
                    text_color=TEXT,
                    command=lambda dd=day: self._pick_day(dd),
                ).grid(row=r, column=c, padx=2, pady=2)

        bottom = ctk.CTkFrame(self._card, fg_color="transparent")
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkButton(bottom, text="Hoy", width=70, fg_color=SURFACE, hover_color=BORDER,
                      command=self._pick_today).pack(side="left")
        ctk.CTkButton(bottom, text="Limpiar", width=80, fg_color=SURFACE, hover_color=BORDER,
                      command=lambda: self._emit("")).pack(side="right")

    def _prev_month(self):
        self._month -= 1
        if self._month == 0:
            self._month = 12
            self._year -= 1
        self._draw()

    def _next_month(self):
        self._month += 1
        if self._month == 13:
            self._month = 1
            self._year += 1
        self._draw()

    def _pick_day(self, day: int):
        self._emit(f"{day:02d}/{self._month:02d}/{self._year:04d}")

    def _pick_today(self):
        now = datetime.today()
        self._emit(f"{now.day:02d}/{now.month:02d}/{now.year:04d}")

    def _emit(self, value: str):
        self._on_pick(value)
        self._safe_close()


def _format_amount_es(number: Decimal) -> str:
    sign = "-" if number < 0 else ""
    n = abs(number)
    text = f"{n:,.2f}"
    text = text.replace(",", "_").replace(".", ",").replace("_", " ")
    return f"{sign}{text}"


class App3Window(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("App 3 — Clasificador Contable")
        self.geometry("1440x860")
        self.minsize(1100, 680)
        self.configure(fg_color=BG)
        self.grid_rowconfigure(0, weight=0, minsize=64)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.session: ClientSession | None = None
        self.db: ClassificationDB | None = None
        self.catalog: dict = {}
        self.records: list[FacturaRecord] = []
        self.all_records: list[FacturaRecord] = []
        self._db_records: dict[str, dict] = {}
        self.selected: FacturaRecord | None = None
        self._load_queue: Queue = Queue()
        self._active_calendar: DatePickerDropdown | None = None

        _apply_tree_style()
        self._build()

        # Abrir pantalla de sesión al inicio
        self.withdraw()
        self.after(100, self._open_session_view)

    # ── SESIÓN ────────────────────────────────────────────────────────────────
    def _open_session_view(self):
        SessionView(self, on_session_resolved=self._on_session_resolved)

    def _on_session_resolved(self, session: ClientSession):
        self.deiconify()
        self.focus_force()
        self._load_session(session)

    def _load_session(self, session: ClientSession):
        self._set_status("Cargando facturas...")
        self._load_queue = Queue()

        def worker():
            try:
                mdir = metadata_dir(session.folder)
                catalog = CatalogManager(mdir).load()
                db = ClassificationDB(mdir)
                indexer = FacturaIndexer()
                records = indexer.load_period(
                    session.folder,
                    from_date="",
                    to_date="",
                )
                self._load_queue.put(("ok", (session, catalog, db, records, indexer.parse_errors)))
            except Exception as exc:
                self._load_queue.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(150, self._poll_load)

    def _poll_load(self):
        if self._load_queue.empty():
            self.after(150, self._poll_load)
            return

        status, payload = self._load_queue.get()

        if status == "error":
            self._set_status("Error al cargar")
            self._show_error("Error al cargar cliente", payload)
            return

        session, catalog, db, records, parse_errors = payload
        self.session = session
        self.catalog = catalog
        self.db = db
        self.all_records = records
        self._db_records = db.get_records_map()
        self.records = self._apply_date_filter(records)
        self.selected = None

        # Actualizar header
        self._lbl_cliente.configure(text=session.folder.name)
        self._lbl_year.configure(text=f"PF-{session.year}")

        # Actualizar catálogo
        cats = sorted(catalog.keys())
        self._cat_cb.configure(values=cats)
        if cats:
            self._cat_var.set(cats[0])
            self._on_categoria_change()

        self.pdf_viewer.clear()
        self._refresh_tree()
        self._update_progress()
        self._set_status("Listo")

        if parse_errors:
            self._show_warning(
                "Advertencias al cargar",
                f"Facturas cargadas: {len(records)}\n"
                f"XML con error (omitidos): {len(parse_errors)}\n\n"
                + "\n".join(parse_errors[:5])
            )

    # ── CONSTRUCCIÓN UI ───────────────────────────────────────────────────────
    def _build(self):
        self._build_header()
        self._build_body()

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=64)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)
        hdr.grid_columnconfigure(2, weight=1)

        # Logo
        ctk.CTkLabel(hdr, text="📊", fg_color="#1a3a36", corner_radius=8,
                      width=32, height=32,
                      font=ctk.CTkFont(size=16)).grid(row=0, column=0, padx=(16,8), pady=14)
        ctk.CTkLabel(hdr, text="Clasificador  Contable",
                      font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
                      text_color=TEXT).grid(row=0, column=1, sticky="w", padx=(0, 12))

        # Info cliente activo (bloque izquierdo)
        client_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        client_frame.grid(row=0, column=2, sticky="w")

        ctk.CTkLabel(client_frame, text="Cliente:",
                      font=F_SMALL(), text_color=MUTED).pack(side="left", padx=(0,6))
        self._lbl_cliente = ctk.CTkLabel(client_frame, text="Sin sesión",
                                          font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                                          text_color=TEXT)
        self._lbl_cliente.pack(side="left")
        self._lbl_year = ctk.CTkLabel(client_frame, text="",
                                       font=F_SMALL(), text_color=MUTED,
                                       fg_color=CARD, corner_radius=20)
        self._lbl_year.pack(side="left", padx=(10,0), ipadx=8, ipady=2)

        # Filtros de fecha (bloque central)
        date_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        date_frame.grid(row=0, column=3, sticky="e", padx=8)

        ctk.CTkLabel(date_frame, text="Desde:", font=F_SMALL(),
                      text_color=MUTED).pack(side="left", padx=(0,4))
        self.from_var = ctk.StringVar()
        self._from_entry = ctk.CTkEntry(
            date_frame,
            textvariable=self.from_var,
            width=100,
            placeholder_text="DD/MM/AAAA",
            fg_color=CARD,
            border_color=BORDER,
            text_color=TEXT,
            font=F_SMALL(),
            height=32,
            corner_radius=8,
        )
        self._from_entry.pack(side="left")
        ctk.CTkButton(date_frame, text="📅", width=30, height=32,
                      fg_color=SURFACE, hover_color=BORDER, command=lambda: self._open_date_picker("from")).pack(side="left", padx=(4, 8))

        ctk.CTkLabel(date_frame, text="Hasta:", font=F_SMALL(),
                      text_color=MUTED).pack(side="left", padx=(10,4))
        self.to_var = ctk.StringVar()
        self._to_entry = ctk.CTkEntry(
            date_frame,
            textvariable=self.to_var,
            width=100,
            placeholder_text="DD/MM/AAAA",
            fg_color=CARD,
            border_color=BORDER,
            text_color=TEXT,
            font=F_SMALL(),
            height=32,
            corner_radius=8,
        )
        self._to_entry.pack(side="left")
        ctk.CTkButton(date_frame, text="📅", width=30, height=32,
                      fg_color=SURFACE, hover_color=BORDER, command=lambda: self._open_date_picker("to")).pack(side="left", padx=(4, 8))

        ctk.CTkButton(date_frame, text="Filtrar", width=70, height=32,
                       fg_color=TEAL, hover_color=TEAL_DIM, text_color="#0d1a18",
                       font=F_SMALL(), corner_radius=8,
                       command=self._on_filter).pack(side="left", padx=(8,0))

        # Botón cambiar cliente
        ctk.CTkButton(hdr, text="⇄  Cambiar cliente", width=150, height=32,
                       fg_color=CARD, hover_color=SURFACE, text_color=MUTED,
                       border_color=BORDER, border_width=1,
                       font=F_SMALL(), corner_radius=8,
                       command=self._open_session_view).grid(
            row=0, column=4, padx=(10, 8), pady=14)

        # Status
        self._status_var = ctk.StringVar(value="")
        ctk.CTkLabel(hdr, textvariable=self._status_var,
                      font=F_SMALL(), text_color=MUTED).grid(
            row=0, column=5, padx=(0, 16))

        ctk.CTkFrame(self, fg_color=BORDER, height=1, corner_radius=0).grid(
            row=0, column=0, sticky="sew", pady=(63, 0)
        )

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=8, pady=(8, 8))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=33, minsize=360)  # lista
        body.grid_columnconfigure(1, weight=52, minsize=520)  # visor PDF
        body.grid_columnconfigure(2, weight=15, minsize=260)  # clasificación

        self._build_list_panel(body)
        self._build_pdf_panel(body)
        self._build_classify_panel(body)

    # ── PANEL IZQUIERDO — LISTA ───────────────────────────────────────────────
    def _build_list_panel(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=10, border_width=1, border_color=BORDER)
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        # Header del panel
        top = ctk.CTkFrame(frame, fg_color=CARD, corner_radius=10, height=44)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_propagate(False)
        top.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(top, text="Facturas del período",
                      font=F_TITLE(), text_color=TEXT).grid(
            row=0, column=0, sticky="w", padx=14, pady=12)

        ctk.CTkButton(top, text="Exportar reporte", width=120, height=30,
                      fg_color=SURFACE, hover_color=BORDER, text_color=TEXT,
                      font=F_SMALL(), corner_radius=8,
                      command=self._export_report).grid(row=0, column=2, sticky="e", padx=(0, 10))

        self._progress_var = ctk.StringVar(value="")
        ctk.CTkLabel(top, textvariable=self._progress_var,
                      font=F_SMALL(), text_color=TEAL).grid(
            row=0, column=1, sticky="e", padx=14)

        # Treeview con estilo oscuro
        tree_frame = ctk.CTkFrame(frame, fg_color=CARD, corner_radius=10)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        cols = ("estado", "fecha", "tipo", "emisor", "moneda", "total")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                  selectmode="browse", style="Dark.Treeview")
        self.tree.heading("estado",  text="Estado")
        self.tree.heading("fecha",   text="Fecha")
        self.tree.heading("tipo",    text="Tipo")
        self.tree.heading("emisor",  text="Emisor")
        self.tree.heading("moneda",  text="Mon.")
        self.tree.heading("total",   text="Total")
        self.tree.column("estado",  width=72,  stretch=False)
        self.tree.column("fecha",   width=78,  stretch=False)
        self.tree.column("tipo",    width=46,  stretch=False)
        self.tree.column("emisor",  width=160)
        self.tree.column("moneda",  width=36,  stretch=False, anchor="center")
        self.tree.column("total",   width=96,  anchor="e", stretch=False)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                             command=self.tree.yview, style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self.tree.tag_configure("clasificado",   foreground=SUCCESS)
        self.tree.tag_configure("pendiente_pdf", foreground=WARNING)
        self.tree.tag_configure("sin_xml",       foreground=MUTED)
        self.tree.tag_configure("pendiente",     foreground=TEXT)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    # ── PANEL CENTRAL — VISOR PDF ─────────────────────────────────────────────
    def _build_pdf_panel(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=BG, corner_radius=10, border_width=1, border_color=BORDER)
        frame.grid(row=0, column=1, sticky="nsew", padx=6)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        # NO usar grid_propagate(False) — colapsa el frame a tamaño 0
        # El layout estable viene de minsize en las columnas del body (en _build_body)

        # PDFViewer ocupa TODO el panel — incluye su propia toolbar internamente
        self.pdf_viewer = PDFViewer(frame)
        self.pdf_viewer.grid(row=0, column=0, sticky="nsew")

    # ── PANEL DERECHO — CLASIFICACIÓN ────────────────────────────────────────
    def _build_classify_panel(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=10, border_width=1, border_color=BORDER)
        frame.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # Header fijo
        top = ctk.CTkFrame(frame, fg_color=CARD, corner_radius=10, height=44)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_propagate(False)
        ctk.CTkLabel(top, text="Clasificación",
                      font=F_TITLE(), text_color=TEXT).pack(side="left", padx=12, pady=10)

        # Contenido scrollable
        scroll = ctk.CTkScrollableFrame(
            frame, fg_color="transparent",
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=MUTED,
        )
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # ── Pill Hacienda ─────────────────────────────────────────────────────
        self._hacienda_lbl = ctk.CTkLabel(
            scroll, text="",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=SUCCESS, fg_color="#0d2a1e", corner_radius=8,
            anchor="center",
        )
        self._hacienda_lbl.grid(row=0, column=0, sticky="ew",
                                 padx=12, pady=(12, 0), ipadx=6, ipady=4)

        # ── Panel clasificación contable ──────────────────────────────────────
        clf_border = ctk.CTkFrame(scroll, fg_color=BORDER, corner_radius=12)
        clf_border.grid(row=1, column=0, sticky="ew", padx=12, pady=10)
        clf = ctk.CTkFrame(clf_border, fg_color=CARD, corner_radius=10)
        clf.pack(fill="both", expand=True, padx=1, pady=1)
        clf.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(clf, text="CLASIFICACIÓN CONTABLE",
                      font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
                      text_color=TEAL).grid(row=0, column=0, sticky="w",
                                             padx=12, pady=(12, 6))

        ctk.CTkLabel(clf, text="Categoría", font=F_SMALL(),
                      text_color=MUTED).grid(row=1, column=0, sticky="w", padx=12)
        self._cat_var = ctk.StringVar()
        self._cat_cb = ctk.CTkComboBox(clf, variable=self._cat_var, values=[],
                                        state="readonly",
                                        fg_color=SURFACE, border_color=BORDER,
                                        button_color=BORDER, button_hover_color=TEAL,
                                        text_color=TEXT, font=F_LABEL(),
                                        dropdown_fg_color=CARD, dropdown_text_color=TEXT,
                                        command=self._on_categoria_change)
        self._cat_cb.grid(row=2, column=0, sticky="ew", padx=12, pady=(2, 8))

        ctk.CTkLabel(clf, text="Subcategoría", font=F_SMALL(),
                      text_color=MUTED).grid(row=3, column=0, sticky="w", padx=12)
        self._subcat_var = ctk.StringVar()
        self._subcat_cb = ctk.CTkComboBox(clf, variable=self._subcat_var, values=[],
                                           state="readonly",
                                           fg_color=SURFACE, border_color=BORDER,
                                           button_color=BORDER, button_hover_color=TEAL,
                                           text_color=TEXT, font=F_LABEL(),
                                           dropdown_fg_color=CARD, dropdown_text_color=TEXT)
        self._subcat_cb.grid(row=4, column=0, sticky="ew", padx=12, pady=(2, 8))

        ctk.CTkLabel(clf, text="Proveedor", font=F_SMALL(),
                      text_color=MUTED).grid(row=5, column=0, sticky="w", padx=12)
        self._prov_var = ctk.StringVar()
        ctk.CTkEntry(clf, textvariable=self._prov_var,
                      fg_color=SURFACE, border_color=BORDER, text_color=TEXT,
                      font=F_LABEL(), height=34, corner_radius=8).grid(
            row=6, column=0, sticky="ew", padx=12, pady=(2, 12))

        self._btn_classify = ctk.CTkButton(
            clf, text="✔  Clasificar",
            font=F_BTN(), fg_color=TEAL, hover_color=TEAL_DIM,
            text_color="#0d1a18", corner_radius=10, height=40,
            state="disabled",
            command=self._classify_selected,
        )
        self._btn_classify.grid(row=7, column=0, sticky="ew", padx=12, pady=(0, 12))

        # ── Clasificación anterior ────────────────────────────────────────────
        self._prev_frame = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=10)
        self._prev_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        self._prev_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self._prev_frame, text="ANTERIOR",
                      font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
                      text_color=MUTED).grid(row=0, column=0, sticky="w",
                                              padx=10, pady=(8, 2))
        self._prev_var = ctk.StringVar(value="—")
        ctk.CTkLabel(self._prev_frame, textvariable=self._prev_var,
                      font=F_SMALL(), text_color="#555e6e",
                      justify="left", wraplength=200, anchor="w").grid(
            row=1, column=0, sticky="ew", padx=10, pady=(0, 8))


    # ── TABLA ─────────────────────────────────────────────────────────────────
    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for idx, r in enumerate(self.records):
            estado = (self._db_records.get(r.clave, {}).get("estado") if self.db else None) or r.estado
            icon  = ESTADO_ICON.get(estado, "·")
            label = ESTADO_LABEL.get(estado, estado)
            tag   = estado if estado in ("clasificado", "pendiente_pdf", "sin_xml") else ""
            # Abreviar tipo de documento
            tipo_raw = str(r.tipo_documento or "")
            tipo_short = (tipo_raw
                .replace("Factura Electrónica", "FE")
                .replace("Factura electronica", "FE")
                .replace("Nota de Crédito", "NC")
                .replace("Nota de Debito", "ND")
                .replace("Tiquete", "TQ")
                [:4])
            moneda = str(r.moneda or "")[:3]
            self.tree.insert("", "end", iid=str(idx),
                              values=(
                                  icon + " " + label,
                                  r.fecha_emision,
                                  tipo_short,
                                  _short_name(r.emisor_nombre),
                                  moneda,
                                  _fmt_amount(r.total_comprobante),
                              ),
                              tags=(tag,))

    def _update_progress(self):
        if not self.records:
            self._progress_var.set("")
            return
        total = len(self.records)
        clf = sum(
            1
            for r in self.records
            if ((self._db_records.get(r.clave, {}).get("estado") if self.db else None) or r.estado)
            == "clasificado"
        )
        pct = int(clf / total * 100) if total else 0
        self._progress_var.set(f"{clf}/{total}  ({pct}%)")

    # ── SELECCIÓN ─────────────────────────────────────────────────────────────
    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self.selected = self.records[idx]
        r = self.selected

        # Estado Hacienda — pill superior
        if r.estado_hacienda:
            esh = r.estado_hacienda.strip()
            color = SUCCESS if "aceptado" in esh.lower() else WARNING
            bg    = "#0d2a1e" if color == SUCCESS else "#2d2010"
            icon  = "✓" if color == SUCCESS else "⚠"
            self._hacienda_lbl.configure(
                text=f"{icon}  Hacienda: {esh}",
                text_color=color, fg_color=bg,
            )
        else:
            self._hacienda_lbl.configure(text="", fg_color="transparent")

        # PDF
        if r.pdf_path and r.pdf_path.exists():
            self.pdf_viewer.load(r.pdf_path)
        else:
            self.pdf_viewer.clear()

        # Prellenar proveedor
        if r.emisor_nombre:
            self._prov_var.set(r.emisor_nombre)

        # Habilitar botón clasificar
        self._btn_classify.configure(state="normal")

        # Clasificación previa
        if self.db:
            prev = self._db_records.get(r.clave)
            if prev and prev.get("estado") == "clasificado":
                self._prev_var.set(
                    f"{prev['categoria']} › {prev['subcategoria']}\n"
                    f"{prev['proveedor']}\n"
                    f"{prev['fecha_clasificacion']}"
                )
            else:
                self._prev_var.set("—")

    # ── CATÁLOGO ──────────────────────────────────────────────────────────────
    def _on_categoria_change(self, _value=None):
        cat = self._cat_var.get()
        subcats = sorted(self.catalog.get(cat, {}).keys())
        self._subcat_cb.configure(values=subcats,
                                   state="readonly" if subcats else "normal")
        self._subcat_var.set(subcats[0] if subcats else "")

    def _on_filter(self):
        if not self.all_records:
            return
        self.records = self._apply_date_filter(self.all_records)
        self.selected = None
        self.pdf_viewer.clear()
        self._refresh_tree()
        self._update_progress()
        self._set_status("Filtro aplicado")

    def _open_date_picker(self, target: str):
        self._close_date_picker()
        initial = self.from_var.get() if target == "from" else self.to_var.get()
        entry = self._from_entry if target == "from" else self._to_entry

        def on_pick(value: str):
            if target == "from":
                self.from_var.set(value)
            else:
                self.to_var.set(value)
            self._close_date_picker()

        self.update_idletasks()
        x = entry.winfo_rootx()
        y = entry.winfo_rooty() + entry.winfo_height() + 6
        self._active_calendar = DatePickerDropdown(
            self,
            on_pick=on_pick,
            initial_value=initial,
            x=x,
            y=y,
        )

    def _close_date_picker(self):
        if self._active_calendar is not None:
            self._active_calendar._safe_close()
            self._active_calendar = None

    def _export_report(self):
        if not self.records:
            messagebox.showinfo("Exportar", "No hay registros para exportar.")
            return

        default_name = f"App3_reporte_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        target = filedialog.asksaveasfilename(
            title="Exportar reporte",
            defaultextension=".xlsx",
            initialfile=default_name,
            filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")],
            confirmoverwrite=True,
        )
        if not target:
            return

        rows: list[dict[str, str]] = []
        for r in self.records:
            estado = (self._db_records.get(r.clave, {}).get("estado") if self.db else None) or r.estado
            rows.append(
                {
                    "clave_numerica": r.clave,
                    "estado": estado,
                    "fecha_emision": r.fecha_emision,
                    "tipo_documento": r.tipo_documento,
                    "emisor_nombre": r.emisor_nombre,
                    "emisor_cedula": r.emisor_cedula,
                    "receptor_nombre": r.receptor_nombre,
                    "receptor_cedula": r.receptor_cedula,
                    "moneda": r.moneda,
                    "subtotal": r.subtotal,
                    "impuesto_total": r.impuesto_total,
                    "total_comprobante": r.total_comprobante,
                    "estado_hacienda": r.estado_hacienda,
                    "consecutivo": r.consecutivo,
                    "xml_path": str(r.xml_path or ""),
                    "pdf_path": str(r.pdf_path or ""),
                }
            )

        try:
            if target.lower().endswith(".csv"):
                with open(target, "w", newline="", encoding="utf-8-sig") as fh:
                    writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
            else:
                import pandas as pd
                from openpyxl.styles import Alignment, Font, PatternFill

                df = pd.DataFrame(rows)
                pretty_headers = {
                    "clave_numerica": "Clave",
                    "tipo_documento": "Tipo documento",
                    "fecha_emision": "Fecha emisión",
                    "consecutivo": "Consecutivo",
                    "emisor_nombre": "Emisor",
                    "emisor_cedula": "Cédula emisor",
                    "receptor_nombre": "Receptor",
                    "receptor_cedula": "Cédula receptor",
                    "moneda": "Moneda",
                    "subtotal": "Subtotal",
                    "impuesto_total": "Impuesto total",
                    "total_comprobante": "Total comprobante",
                    "estado_hacienda": "Estado Hacienda",
                    "estado": "Estado App 3",
                    "xml_path": "Ruta XML",
                    "pdf_path": "Ruta PDF",
                }

                numeric_columns = {"subtotal", "impuesto_total", "total_comprobante"}
                text_columns = {
                    "clave_numerica", "consecutivo", "emisor_cedula", "receptor_cedula", "xml_path", "pdf_path"
                }
                date_column = "fecha_emision"

                for col in text_columns:
                    if col in df.columns:
                        df[col] = df[col].fillna("").astype(str).str.strip()

                for col in numeric_columns:
                    if col in df.columns:
                        df[col] = pd.to_numeric(
                            df[col].astype(str).str.replace(" ", "", regex=False).str.replace(",", ".", regex=False),
                            errors="coerce",
                        )

                if date_column in df.columns:
                    df[date_column] = pd.to_datetime(df[date_column], format="%d/%m/%Y", errors="coerce")

                owner_name = self._lbl_cliente.cget("text") or "REPORTE DE COMPROBANTES"
                date_from_label = self.from_var.get().strip() or "01/01/1900"
                date_to_label = self.to_var.get().strip() or datetime.now().strftime("%d/%m/%Y")

                title_fill = PatternFill(fill_type="solid", fgColor="0B2B66")
                subtitle_fill = PatternFill(fill_type="solid", fgColor="7F7F7F")
                summary_fill = PatternFill(fill_type="solid", fgColor="EDEDED")
                header_fill = PatternFill(fill_type="solid", fgColor="D9E1F2")
                credit_fill = PatternFill(fill_type="solid", fgColor="FDE2E2")
                title_font = Font(name="Calibri", size=16, bold=True, color="FFFFFF")
                subtitle_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
                summary_font = Font(name="Calibri", size=10, bold=True, color="1F2937")
                header_font = Font(name="Calibri", size=10, bold=True, color="1F2937")

                with pd.ExcelWriter(target, engine="openpyxl") as writer:
                    display_df = df.rename(columns={col: pretty_headers.get(col, col.replace("_", " ").title()) for col in df.columns})
                    display_df.to_excel(writer, index=False, sheet_name="Reporte", startrow=4)
                    ws = writer.sheets["Reporte"]

                    max_col = ws.max_column if ws.max_column > 0 else 1
                    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)
                    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max_col)
                    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=max_col)
                    title_cell = ws.cell(row=1, column=1)
                    title_cell.value = str(owner_name).upper()
                    title_cell.font = title_font
                    title_cell.alignment = Alignment(horizontal="center", vertical="center")
                    title_cell.fill = title_fill

                    subtitle_cell = ws.cell(row=2, column=1)
                    subtitle_cell.value = f"REPORTE DE CLASIFICACIÓN - Período: {date_from_label} al {date_to_label}"
                    subtitle_cell.font = subtitle_font
                    subtitle_cell.alignment = Alignment(horizontal="center", vertical="center")
                    subtitle_cell.fill = subtitle_fill

                    monto_total = Decimal("0")
                    valid_amounts: list[Decimal] = []
                    if "total_comprobante" in df.columns:
                        for value in df["total_comprobante"].dropna().tolist():
                            try:
                                valid_amounts.append(Decimal(str(value)))
                            except Exception:
                                continue
                    if valid_amounts:
                        monto_total = sum(valid_amounts, Decimal("0"))
                    monedas = sorted({str(m).strip() for m in df["moneda"].dropna().tolist() if str(m).strip()}) if "moneda" in df.columns else []
                    if not monedas:
                        moneda_value = "N/A"
                    elif len(monedas) == 1:
                        moneda_value = monedas[0]
                    else:
                        moneda_value = "MIXTA: " + ", ".join(monedas)
                    generated = datetime.now().strftime("%d/%m/%Y %H:%M")

                    summary_cell = ws.cell(row=3, column=1)
                    summary_cell.value = (
                        f"Total filas: {len(df)}   |   Monto Total: {_format_amount_es(monto_total)}   |   "
                        f"Moneda: {moneda_value}   |   Generado: {generated}"
                    )
                    summary_cell.font = summary_font
                    summary_cell.alignment = Alignment(horizontal="center", vertical="center")
                    summary_cell.fill = summary_fill

                    header_row = 5
                    for col_idx in range(1, ws.max_column + 1):
                        cell = ws.cell(row=header_row, column=col_idx)
                        cell.font = header_font
                        cell.fill = header_fill
                        cell.alignment = Alignment(horizontal="center", vertical="center")

                    tipo_idx = list(df.columns).index("tipo_documento") + 1 if "tipo_documento" in df.columns else None

                    for col_idx, col_name in enumerate(df.columns, start=1):
                        for row_idx in range(header_row + 1, len(df) + header_row + 1):
                            cell = ws.cell(row=row_idx, column=col_idx)
                            if col_name in text_columns:
                                cell.number_format = "@"
                                cell.value = "" if cell.value is None else str(cell.value)
                            elif col_name == date_column and cell.value is not None:
                                cell.number_format = "dd/mm/yyyy"
                            elif col_name in numeric_columns and cell.value is not None:
                                cell.number_format = "#,##0.00"

                    if tipo_idx is not None:
                        for row_idx in range(header_row + 1, len(df) + header_row + 1):
                            if ws.cell(row=row_idx, column=tipo_idx).value == "Nota de Crédito":
                                for col in range(1, ws.max_column + 1):
                                    ws.cell(row=row_idx, column=col).fill = credit_fill

                    for col_idx in range(1, ws.max_column + 1):
                        max_len = 0
                        for row_idx in range(header_row, ws.max_row + 1):
                            value = ws.cell(row=row_idx, column=col_idx).value
                            if value is None:
                                continue
                            max_len = max(max_len, len(str(value)))
                        ws.column_dimensions[ws.cell(row=header_row, column=col_idx).column_letter].width = min(max(max_len + 3, 12), 65)

                    ws.freeze_panes = ws["A6"]
            messagebox.showinfo("Exportar", f"Reporte guardado en:\n{target}")
            self._set_status("Reporte exportado")
        except Exception as exc:
            self._show_error("Error al exportar", str(exc))

    # ── CLASIFICACIÓN ─────────────────────────────────────────────────────────
    def _classify_selected(self):
        if not self.session or not self.selected or not self.db:
            return

        cat    = self._cat_var.get().strip().upper()
        subcat = self._subcat_var.get().strip().upper()
        prov   = self._prov_var.get().strip().upper()

        if not cat or not prov:
            self._show_warning("Atención", "Completa categoría y proveedor.")
            return

        if self._db_records.get(self.selected.clave, {}).get("estado") == "clasificado":
            if not self._ask("Reclasificar",
                              "Esta factura ya fue clasificada.\n¿Deseas reclasificarla?"):
                return

        self._btn_classify.configure(state="disabled", text="Clasificando...")

        # Liberar lock del PDF mostrado antes de mover/eliminar en Windows.
        self.pdf_viewer.release_file_handles("Procesando clasificación...")

        record  = self.selected
        session = self.session
        db      = self.db

        def worker():
            try:
                classify_record(record, session.folder, db, cat, subcat, prov)
                self.after(0, self._on_classify_ok)
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_classify_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_classify_ok(self):
        if self.db and self.selected:
            updated = self.db.get_record(self.selected.clave)
            if updated:
                self._db_records[self.selected.clave] = updated
        self._btn_classify.configure(state="normal", text="✔  Clasificar")
        self._refresh_tree()
        self._update_progress()
        self._on_select()

    @staticmethod
    def _parse_ui_date(value: str):
        text = (value or "").strip()
        if not text:
            return None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    def _apply_date_filter(self, records: list[FacturaRecord]) -> list[FacturaRecord]:
        from_dt = self._parse_ui_date(self.from_var.get())
        to_dt = self._parse_ui_date(self.to_var.get())
        if not from_dt and not to_dt:
            return list(records)

        filtered: list[FacturaRecord] = []
        for record in records:
            fecha_txt = (record.fecha_emision or "").strip()
            try:
                fecha = datetime.strptime(fecha_txt, "%d/%m/%Y").date()
            except ValueError:
                # Mantener visibles los PDF sin XML para revisión manual.
                if record.estado == "sin_xml":
                    filtered.append(record)
                continue
            if from_dt and fecha < from_dt:
                continue
            if to_dt and fecha > to_dt:
                continue
            filtered.append(record)
        return filtered

    def _on_classify_error(self, msg: str):
        self._btn_classify.configure(state="normal", text="✔  Clasificar")
        self._show_error("Error al clasificar", msg)

    # ── HELPERS ───────────────────────────────────────────────────────────────
    def _set_status(self, text: str):
        self._status_var.set(text)

    def _show_error(self, title: str, msg: str):
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("420x200")
        win.configure(fg_color=CARD)
        win.grab_set()
        ctk.CTkLabel(win, text=f"✗  {title}", font=F_BTN(),
                      text_color=DANGER).pack(pady=(24, 8))
        ctk.CTkLabel(win, text=msg, font=F_SMALL(), text_color=TEXT,
                      wraplength=380, justify="center").pack(pady=(0, 16))
        ctk.CTkButton(win, text="Cerrar", fg_color=SURFACE,
                       hover_color=BORDER, text_color=TEXT,
                       command=win.destroy).pack()

    def _show_warning(self, title: str, msg: str):
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("440x220")
        win.configure(fg_color=CARD)
        win.grab_set()
        ctk.CTkLabel(win, text=f"⚠  {title}", font=F_BTN(),
                      text_color=WARNING).pack(pady=(24, 8))
        ctk.CTkLabel(win, text=msg, font=F_SMALL(), text_color=TEXT,
                      wraplength=400, justify="center").pack(pady=(0, 16))
        ctk.CTkButton(win, text="Entendido", fg_color=SURFACE,
                       hover_color=BORDER, text_color=TEXT,
                       command=win.destroy).pack()

    def _ask(self, title: str, msg: str) -> bool:
        result = [False]
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("380x180")
        win.configure(fg_color=CARD)
        win.grab_set()
        ctk.CTkLabel(win, text=msg, font=F_LABEL(), text_color=TEXT,
                      wraplength=340, justify="center").pack(pady=(28, 16))
        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack()

        def yes():
            result[0] = True
            win.destroy()

        ctk.CTkButton(btns, text="Sí, reclasificar", fg_color=TEAL,
                       hover_color=TEAL_DIM, text_color="#0d1a18",
                       command=yes).pack(side="left", padx=8)
        ctk.CTkButton(btns, text="Cancelar", fg_color=SURFACE,
                       hover_color=BORDER, text_color=TEXT,
                       command=win.destroy).pack(side="left", padx=8)
        win.wait_window()
        return result[0]
