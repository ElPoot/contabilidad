from __future__ import annotations

import calendar
import os
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from queue import Queue

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, ttk

import logging

from gestor_contable.app.controllers.load_period_controller import (
    load_range_worker,
    load_session_worker,
    months_for_range,
)
from gestor_contable.app.controllers.pdf_swap_controller import execute_pdf_swap
from gestor_contable.app.selection_controller import build_multi_vm, build_single_vm
from gestor_contable.app.selection_vm import SelectionVM
from gestor_contable.app.state.main_window_state import MainWindowState
from gestor_contable.app.use_cases.export_report_use_case import (
    default_export_filename,
    export_period_report,
)
from gestor_contable.config import metadata_dir, network_drive
from gestor_contable.core.catalog import CatalogManager
from gestor_contable.core.classification_utils import (
    classify_transaction,
    filter_records_by_tab,
    get_classification_label,
    get_hacienda_review_status,
    get_tab_statistics,
)
from gestor_contable.core.classifier import (
    ClassificationDB,
    _sanitize_folder,
    build_dest_folder,
    classify_record,
    heal_classified_path,
)
from gestor_contable.core.duplicates_quarantine import (
    DuplicatesQuarantineDB,
    execute_duplicates_quarantine,
    restore_duplicates_batch,
)
from gestor_contable.core.ors_purge import (
    OrsPurgeDB,
    build_file_inventory,
    execute_purge,
    find_ors_candidates,
    restore_batch,
)
from gestor_contable.core.factura_index import FacturaIndexer
from gestor_contable.core.models import FacturaRecord
from gestor_contable.core.report_paths import month_folder_name, resolve_incremental_path
from gestor_contable.core.session import ClientSession
from gestor_contable.core import atv_client
from gestor_contable.gui.icons import get_icon
from gestor_contable.gui.classify_panel import ClassifyPanel, ClassifyPanelCallbacks
from gestor_contable.gui.loading_modal import LoadingOverlay
from gestor_contable.gui.modal_overlay import ModalOverlay
from gestor_contable.gui.pdf_viewer import PDFViewer
from gestor_contable.gui.session_view import SessionView

logger = logging.getLogger(__name__)

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

from gestor_contable.gui.fonts import *

ESTADO_ICON = {
    "clasificado":   "OK",
    "pendiente":     "·",
    "pendiente_pdf": "!",
    "sin_xml":       "--",
}

# Texto corto para la columna Estado
ESTADO_LABEL = {
    "clasificado":   "clasificado",
    "pendiente":     "pendiente",
    "pendiente_pdf": "sin PDF",
    "sin_xml":       "sin XML",
}

def _is_effectively_classified(db_rec: dict, r) -> bool:
    """Determina si un registro cuenta como clasificado para el contador de progreso.

    Un registro está efectivamente clasificado si:
    - Su estado en BD es "clasificado", O
    - Su estado en BD es "pendiente_pdf" con una categoría asignada
      (ingresos/sin_receptor sin PDF: ya tienen categoría pero no se mueve PDF).
    """
    estado = db_rec.get("estado") or getattr(r, "estado", "")
    if estado == "clasificado":
        return True
    if estado == "pendiente_pdf" and str(db_rec.get("categoria") or "").strip():
        return True
    return False


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
        return str(value) if value else "--"

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
        rowheight=TREE_ROW_HEIGHT, font=(TREE_FONT_FAMILY, TREE_FONT_SIZE),
    )
    style.configure("Dark.Treeview.Heading",
        background=SURFACE, foreground=MUTED,
        borderwidth=0, font=(TREE_FONT_FAMILY, TREE_HEADING_SIZE, "bold"),
    )
    style.map("Dark.Treeview",
        background=[("selected", "#1a3a36")],
        foreground=[("selected", TEAL)],
    )
    style.configure("Dark.Vertical.TScrollbar",
        background=CARD, troughcolor=BG,
        borderwidth=0, arrowsize=0,
    )
    _TREE_STYLE_DONE = True


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


class _BaseDatePicker(ctk.CTkToplevel):
    """Clase base para calendarios con lógica compartida de navegación y dibujo."""

    def __init__(self, parent, on_pick, initial_value: str = ""):
        super().__init__(parent)
        self.configure(fg_color=CARD)
        self._on_pick = on_pick
        dt = _parse_date_any(initial_value) or datetime.today()
        self._year = dt.year
        self._month = dt.month

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

    def _safe_close(self):
        try:
            self.destroy()
        except Exception:
            logger.debug("No se pudo cerrar el selector de fecha", exc_info=True)

    def _draw_calendar(self, container):
        for w in container.winfo_children():
            w.destroy()

        top = ctk.CTkFrame(container, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(8, 6))
        ctk.CTkButton(top, text="◀", width=30, height=28, fg_color=SURFACE,
                      hover_color=BORDER, command=self._prev_month).pack(side="left")
        ctk.CTkLabel(top, text=f"{calendar.month_name[self._month]} {self._year}",
                     text_color=TEXT, font=F_LABEL()).pack(side="left", expand=True)
        ctk.CTkButton(top, text="▶", width=30, height=28, fg_color=SURFACE,
                      hover_color=BORDER, command=self._next_month).pack(side="right")

        grid = ctk.CTkFrame(container, fg_color="transparent")
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

        bottom = ctk.CTkFrame(container, fg_color="transparent")
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkButton(bottom, text="Hoy", width=70, fg_color=SURFACE, hover_color=BORDER,
                      command=self._pick_today).pack(side="left")
        ctk.CTkButton(bottom, text="Limpiar", width=80, fg_color=SURFACE, hover_color=BORDER,
                      command=lambda: self._emit("")).pack(side="right")

    def _draw(self):
        raise NotImplementedError("Subclasses must implement _draw()")


class DatePickerPopup(_BaseDatePicker):
    """Versión modal con barra de título (para edición de fecha en formulario)."""

    def __init__(self, parent, on_pick, initial_value: str = ""):
        super().__init__(parent, on_pick, initial_value)
        self.title("Seleccionar fecha")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._body = ctk.CTkFrame(self, fg_color=CARD)
        self._body.pack(padx=8, pady=8, fill="both", expand=True)
        self._draw()

    def _draw(self):
        self._draw_calendar(self._body)


class DatePickerDropdown(_BaseDatePicker):
    """Calendario anclado tipo dropdown (sin barra de título)."""

    def __init__(self, parent, on_pick, initial_value: str = "", x: int = 120, y: int = 120):
        super().__init__(parent, on_pick, initial_value)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.geometry(f"290x270+{x}+{y}")

        self._card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10, border_width=1, border_color=BORDER)
        self._card.pack(fill="both", expand=True)

        self.bind("<Escape>", lambda _e: self._safe_close())
        self.bind("<FocusOut>", lambda _e: self._safe_close())

        self._draw()
        self.after(10, self.focus_force)

    def _draw(self):
        self._draw_calendar(self._card)


class NewCuentaDialog(ctk.CTkToplevel):
    """Modal dialog for adding a new account."""

    def __init__(self, parent, categoria: str, subtipo: str, existing_cuentas: list,
                 catalog_mgr, on_success):
        super().__init__(parent)
        self.overrideredirect(True)
        self.configure(fg_color=CARD)
        self.attributes("-topmost", True)
        self._on_success = on_success
        self._categoria = categoria
        self._subtipo = subtipo
        self._existing_cuentas = existing_cuentas
        self._catalog_mgr = catalog_mgr
        self.geometry("350x180+400+300")

        self._card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10,
                                   border_width=1, border_color=BORDER)
        self._card.pack(fill="both", expand=True, padx=10, pady=10)

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._save())

        # Title
        ctk.CTkLabel(
            self._card, text="Agregar Nueva Cuenta",
            font=F_MODAL_SUBTITLE(),
            text_color=TEXT,
        ).pack(fill="x", padx=12, pady=(12, 8))

        # Input field
        self._input_var = ctk.StringVar()
        ctk.CTkLabel(
            self._card, text="Nombre de la cuenta",
            font=F_SMALL(), text_color=MUTED,
        ).pack(fill="x", padx=12, pady=(0, 4))

        self._entry = ctk.CTkEntry(
            self._card, textvariable=self._input_var,
            fg_color=SURFACE, border_color=BORDER, text_color=TEXT,
            font=F_LABEL(), height=32, corner_radius=6,
        )
        self._entry.pack(fill="x", padx=12, pady=(0, 12))
        self._entry.focus()

        # Error message label
        self._error_label = ctk.CTkLabel(
            self._card, text="", text_color=DANGER,
            font=F_SMALL(), wraplength=300,
        )
        self._error_label.pack(fill="x", padx=12, pady=(0, 8))

        # Button row
        btn_frame = ctk.CTkFrame(self._card, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkButton(
            btn_frame, text="Cancelar", width=100,
            fg_color=SURFACE, hover_color=BORDER,
            font=F_SMALL(), text_color=TEXT,
            command=self._cancel,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_frame, text="Guardar", width=100,
            fg_color=TEAL, hover_color=TEAL_DIM,
            font=F_SMALL(), text_color="#0d1a18",
            command=self._save,
        ).pack(side="right")

        self.after(10, self.focus_force)

    def _show_error(self, msg: str):
        self._error_label.configure(text=msg)

    def _validate_and_save(self) -> str | None:
        """Validate input and return new account name or None if invalid."""
        name = self._input_var.get().strip()

        if not name:
            self._show_error("El nombre no puede estar vacío.")
            return None

        if len(name) > 100:
            self._show_error("El nombre no puede exceder 100 caracteres.")
            return None

        if name.upper() in [c.upper() for c in self._existing_cuentas]:
            self._show_error(f"La cuenta '{name}' ya existe.")
            return None

        return name

    def _save(self):
        name = self._validate_and_save()
        if name:
            try:
                self._catalog_mgr.add_cuenta(self._categoria, self._subtipo, name)
                self._on_success(name)
                self._safe_close()
            except Exception as e:
                self._show_error(f"Error al guardar: {str(e)}")

    def _cancel(self):
        self._safe_close()

    def _safe_close(self):
        try:
            self.destroy()
        except Exception:
            pass


class App3Window(ctk.CTk):
    # ── Propiedades delegadas a MainWindowState ───────────────────────────────
    # Permiten que el resto del codigo siga usando self.records, self.selected,
    # self._active_tab, etc. sin ningun cambio. La fuente de verdad es self._window_state.

    @property
    def records(self) -> list[FacturaRecord]:
        return self._window_state.records

    @records.setter
    def records(self, value: list[FacturaRecord]) -> None:
        self._window_state.records = value

    @property
    def all_records(self) -> list[FacturaRecord]:
        return self._window_state.all_records

    @all_records.setter
    def all_records(self, value: list[FacturaRecord]) -> None:
        self._window_state.all_records = value

    @property
    def selected(self) -> FacturaRecord | None:
        return self._window_state.selected

    @selected.setter
    def selected(self, value: FacturaRecord | None) -> None:
        self._window_state.selected = value

    @property
    def selected_records(self) -> list[FacturaRecord]:
        return self._window_state.selected_records

    @selected_records.setter
    def selected_records(self, value: list[FacturaRecord]) -> None:
        self._window_state.selected_records = value

    @property
    def _active_tab(self) -> str:
        return self._window_state.active_tab

    @_active_tab.setter
    def _active_tab(self, value: str) -> None:
        self._window_state.active_tab = value

    @property
    def _loaded_months(self) -> set[tuple[int, int]]:
        return self._window_state.loaded_months

    @_loaded_months.setter
    def _loaded_months(self, value: set[tuple[int, int]]) -> None:
        self._window_state.loaded_months = value

    @property
    def _records_map(self) -> dict[str, FacturaRecord]:
        return self._window_state.records_map

    @_records_map.setter
    def _records_map(self, value: dict[str, FacturaRecord]) -> None:
        self._window_state.records_map = value

    @property
    def _user_set_dates(self) -> bool:
        return self._window_state.user_set_dates

    @_user_set_dates.setter
    def _user_set_dates(self, value: bool) -> None:
        self._window_state.user_set_dates = value

    @property
    def _prev_dest_path(self) -> Path | None:
        return self._window_state.prev_dest_path

    @_prev_dest_path.setter
    def _prev_dest_path(self, value: Path | None) -> None:
        self._window_state.prev_dest_path = value

    @property
    def _detected_renames(self) -> list[dict]:
        return self._window_state.detected_renames

    @_detected_renames.setter
    def _detected_renames(self, value: list[dict]) -> None:
        self._window_state.detected_renames = value

    @property
    def _pdf_duplicates_rejected(self) -> dict:
        return self._window_state.pdf_duplicates_rejected

    @_pdf_duplicates_rejected.setter
    def _pdf_duplicates_rejected(self, value: dict) -> None:
        self._window_state.pdf_duplicates_rejected = value

    # ─────────────────────────────────────────────────────────────────────────

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("App 3 -- Clasificador Contable")
        self.geometry("1920x1080")
        self.after(0, lambda: self.state("zoomed"))
        self.minsize(1100, 680)
        self.configure(fg_color=BG)
        self.grid_rowconfigure(0, weight=0)  # Header (can expand with tabs)
        self.grid_rowconfigure(1, weight=1)  # Body
        self.grid_columnconfigure(0, weight=1)

        self.session: ClientSession | None = None
        self.db: ClassificationDB | None = None
        self.catalog_mgr: CatalogManager | None = None
        self._window_state: MainWindowState = MainWindowState()
        self._db_records: dict[str, dict] = {}
        self._load_queue: Queue = Queue()
        self._active_calendar: DatePickerDropdown | None = None
        self._load_generation: int = 0
        self._all_cuentas: list[str] = []  # Unfiltered account list
        self._loading_overlay: LoadingOverlay | None = None  # Overlay de carga
        self._tree_clave_map: dict[str, FacturaRecord] = {}  # Mapeo: clave -> record (para mantener orden)
        self._range_load_generation: int = 0                # Generacion para cargas de rango adicionales
        self._range_load_queue: Queue = Queue()             # Cola para resultados de carga de rango
        self._tab_buttons: dict[str, ctk.CTkButton] = {}  # Botones de pestanas
        self._catalog_categories: list[str] = []          # Categorias manuales del catalogo (egresos)
        self._receptor_response_files: list = []
        self._hidden_response_files_by_clave: dict[str, list[dict]] = {}
        self._ors_autopurge_summary: dict = {"moved_files": [], "batch_ids": []}
        self._selection_vm: SelectionVM | None = None

        _apply_tree_style()

        # Crear body_container (para header y body) -- inicialmente oculto
        self._body_container = ctk.CTkFrame(self, fg_color=BG)
        self._body_container.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self._body_container.grid_rowconfigure(0, weight=0)  # Header
        self._body_container.grid_rowconfigure(1, weight=1)  # Body
        self._body_container.grid_columnconfigure(0, weight=1)
        self._build(self._body_container)
        self._body_container.grid_remove()  # Ocultar inicialmente

        # Crear SessionView (visible al inicio) -- llena toda la ventana
        self._session_frame = SessionView(self, on_session_resolved=self._on_session_resolved)
        self._session_frame.grid(row=0, column=0, rowspan=2, sticky="nsew")

    # ── SESIÓN ────────────────────────────────────────────────────────────────
    def _open_session_view(self):
        # Mostrar SessionView y ocultar body_container
        self._body_container.grid_remove()
        self._session_frame.grid()
        self._session_frame.focus_force()

    def _on_session_resolved(self, session: ClientSession):
        # Ocultar SessionView y mostrar body_container
        self._session_frame.grid_remove()
        self._body_container.grid()
        self.focus_force()
        self._load_session(session)

    def _clear_cache_and_reload(self):
        """Limpia el cache de PDFs y recarga todos los datos.

        Útil cuando los XMLs quedan desvinculados de sus PDFs tras clasificación.
        """
        if not self.session:
            return

        if not self._ask("Limpiar cache",
                        "Se eliminará el cache de PDFs y se recalcularán todas las vinculaciones.\n\n"
                        "Esto toma ~40-50 segundos. ¿Deseas continuar?"):
            return

        try:
            # Limpiar cache de PDFs
            mdir = metadata_dir(self.session.folder)
            cache_file = mdir / "pdf_cache.json"
            if cache_file.exists():
                cache_file.unlink()
                logger.info(f"Cache limpiado: {cache_file}")

            # Recalcular registros (fuerza rescan de PDFs, preservar rango de fechas activo)
            self._load_session(self.session, reset_dates=False)
        except Exception as e:
            self._show_error("Error al limpiar cache", str(e))

    def _load_session(self, session: ClientSession, reset_dates: bool = True):
        self._load_generation += 1
        generation = self._load_generation
        self._load_queue = Queue()

        # Resetear cache acumulativo de la sesion anterior
        self._loaded_months = set()
        self._records_map = {}
        self._detected_renames = []

        # Rango de fechas: mes anterior por defecto al abrir cliente (solo si el usuario no ha cambiado el rango)
        if reset_dates and not self._user_set_dates:
            _today = date.today()
            _first_current = _today.replace(day=1)
            _last_prev = _first_current - timedelta(days=1)
            _first_prev = _last_prev.replace(day=1)
            self.from_var.set(_first_prev.strftime("%d/%m/%Y"))
            self.to_var.set(_last_prev.strftime("%d/%m/%Y"))

        # Capturar el rango que se va a cargar para registrarlo en _loaded_months al terminar
        load_from = self.from_var.get()
        load_to = self.to_var.get()
        load_months = self._months_for_range(load_from, load_to)

        # Mostrar overlay de carga integrado
        if not hasattr(self, '_loading_overlay') or not self._loading_overlay:
            self._loading_overlay = LoadingOverlay(self)
            self._loading_overlay.grid(row=0, column=0, rowspan=2, sticky="nsew")
        else:
            self._loading_overlay.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self._loading_overlay.update_status("Iniciando...")

        # CRITICO: Forzar actualización visual del overlay
        # Sin esto, el overlay no se ve en pantalla durante el worker thread
        self.update_idletasks()
        self.update()

        def worker():
            try:
                def _progress(msg: str, current: int, total: int):
                    self.after(0, lambda m=msg, c=current, t=total: (
                        self._loading_overlay.update_status(m),
                        self._loading_overlay.update_progress(c, t),
                    ))

                result = load_session_worker(session, load_from, load_to, load_months, _progress)
                self._load_queue.put(("ok", (
                    generation, session,
                    result.catalog, result.db, result.records,
                    result.parse_errors, result.failed_xml_files,
                    result.renames, result.pdf_duplicates_rejected, result.load_months,
                    result.receptor_response_files,
                    result.hidden_response_files_by_clave,
                    result.ors_autopurge_summary,
                )))
            except Exception as exc:
                logger.exception("Error en worker")
                self._load_queue.put(("error", (generation, str(exc))))

        threading.Thread(target=worker, daemon=True).start()
        self.after(150, self._poll_load)

    def _poll_load(self):
        import time
        if self._load_queue.empty():
            self.after(150, self._poll_load)
            return

        status, payload = self._load_queue.get()

        # Ocultar overlay de carga
        if hasattr(self, '_loading_overlay') and self._loading_overlay:
            self._loading_overlay.grid_remove()

        if status == "error":
            generation, message = payload
            if generation != self._load_generation:
                return
            self._set_status("Error al cargar")
            self._show_error("Error al cargar cliente", message)
            return

        generation, session, catalog_mgr, db, records, parse_errors, failed_xml_files, renames, pdf_duplicates_rejected, load_months, receptor_response_files, hidden_response_files_by_clave, ors_autopurge_summary = payload
        if generation != self._load_generation:
            return
        self.session = session
        self.catalog_mgr = catalog_mgr
        self.db = db
        self.all_records = records
        self._records_map = {r.clave: r for r in records}
        self._loaded_months = load_months
        self._db_records = db.get_records_map()
        self._pdf_duplicates_rejected = pdf_duplicates_rejected  # {path_rechazado: path_ganador}
        self._detected_renames = renames
        self._hidden_response_files_by_clave = self._merge_hidden_response_files_by_clave(
            None,
            hidden_response_files_by_clave,
        )
        self._ors_autopurge_summary = self._normalize_ors_autopurge_summary(ors_autopurge_summary)
        self._active_tab = "egreso"
        self._update_tab_appearance(self._active_tab)
        self._ors_frame.grid_remove()
        self._btn_purge_ors.configure(state="disabled")
        self.records = self._apply_filters()
        self.selected = None
        self.selected_records = []

        # Actualizar header
        self._lbl_cliente.configure(text=session.folder.name)
        self._lbl_year.configure(text=f"PF-{session.year}")

        # Actualizar catálogo
        cats = catalog_mgr.categorias()
        self._catalog_categories = list(cats)
        self._classify_panel.set_catalog(catalog_mgr, list(cats))
        self._sync_category_for_record(None)

        self.pdf_viewer.clear()

        # Actualizar overlay: renderizando tabla
        if hasattr(self, '_loading_overlay') and self._loading_overlay and self._loading_overlay.winfo_exists():
            self._loading_overlay.update_status("Renderizando lista de facturas...")
            self._loading_overlay.update_progress(0, len(self.records))

        # Timing del refresh
        start_refresh = time.perf_counter()
        self._refresh_tree()
        refresh_time = time.perf_counter() - start_refresh
        logger.info(f"_refresh_tree() tardó {refresh_time:.2f}s para {len(self.records)} registros")

        self._update_progress()
        self._set_status(self._format_ors_autopurge_status("Listo"))

        self._receptor_response_files = receptor_response_files or []
        ors_autopurge_notice = self._build_ors_autopurge_notice()

        if parse_errors:
            if ors_autopurge_notice:
                parse_errors = [ors_autopurge_notice, *parse_errors]
            self._show_parse_errors_modal(
                "Advertencias al cargar",
                parse_errors,
                records_count=len(records),
                failed_xml_files=failed_xml_files,
                receptor_response_files=self._receptor_response_files,
            )
        elif ors_autopurge_notice:
            ModalOverlay.show_info(self, "Auto-saneo ORS", ors_autopurge_notice)

        if renames:
            self._btn_consolidar.grid()
            self.after(100, lambda r=renames: self._show_rename_warning(r))
        else:
            self._btn_consolidar.grid_remove()

        # Recuperar respuestas de Hacienda faltantes via ATV (si hay credenciales)
        self.after(600, self._start_atv_recovery)

    def _start_atv_recovery(self) -> None:
        """Consulta ATV automaticamente para facturas sin respuesta de Hacienda."""
        if not atv_client.has_credentials():
            return

        targets = [
            r for r in self.all_records
            if (r.estado_hacienda == ""
                and r.estado not in ("sin_xml", "huerfano")
                and r.xml_path is not None
                and len(r.clave) == 50)
        ]
        if not targets:
            return

        generation = self._load_generation
        total = len(targets)
        self._set_status(f"ATV: verificando {total} factura(s) sin respuesta de Hacienda...")

        q: Queue = Queue()

        def _worker():
            saved = 0
            for i, record in enumerate(targets):
                result = atv_client.query_invoice_status(record.clave)
                q.put(("progress", i + 1, total, result.get("ind_estado", "...")))

                xml_bytes = result.get("respuesta_xml_bytes")
                ind = result.get("ind_estado", "")
                if xml_bytes and ind not in ("no_encontrado", "desconocido", ""):
                    out = record.xml_path.parent / f"{record.clave}_MH.xml"
                    if not out.exists():
                        try:
                            out.write_bytes(xml_bytes)
                            saved += 1
                        except Exception as exc:
                            logger.warning("ATV: no se pudo guardar XML para %s: %s", record.clave, exc)

            q.put(("done", saved))

        threading.Thread(target=_worker, daemon=True).start()
        self.after(200, lambda: self._poll_atv_recovery(q, generation))

    def _poll_atv_recovery(self, q: Queue, generation: int) -> None:
        """Actualiza el status bar con el progreso de la recuperacion ATV."""
        if generation != self._load_generation:
            return  # la sesion cambio, descartar

        while not q.empty():
            msg = q.get()
            kind = msg[0]

            if kind == "progress":
                _, current, total, ind_estado = msg
                self._set_status(f"ATV: {current}/{total} -- {ind_estado}")

            elif kind == "done":
                saved = msg[1]
                if saved > 0:
                    self._set_status(f"ATV: {saved} respuesta(s) recuperada(s) -- recargando...")
                    self.after(800, lambda: self._load_session(self.session, reset_dates=False))
                else:
                    self._set_status("Listo")
                return

        self.after(200, lambda: self._poll_atv_recovery(q, generation))

    # -- VERIFICACION MANUAL DE HACIENDA (ATV) --

    def _recheck_hacienda_selected(self) -> None:
        """Consulta ATV para la(s) factura(s) seleccionada(s) sin respuesta."""
        targets: list[FacturaRecord] = []
        # Diagnostico: que records tenemos?
        sr = self.selected_records
        ss = self.selected
        logger.info("_recheck_hacienda_selected: selected_records=%d, selected=%s",
                     len(sr) if sr else 0,
                     ss.clave[:15] if ss else "None")
        candidates = sr if sr else ([ss] if ss else [])
        for r in candidates:
            ok = (r.estado_hacienda == ""
                  and r.estado not in ("sin_xml", "huerfano")
                  and r.xml_path is not None
                  and len(r.clave) == 50)
            if ok:
                targets.append(r)
            else:
                logger.info("  EXCLUIDO: clave=%s... estado_hacienda=%r estado=%s xml_path=%s len_clave=%d",
                            r.clave[:15], r.estado_hacienda, r.estado,
                            "Si" if r.xml_path else "None", len(r.clave))
        if not targets:
            self._set_status("No hay facturas sin respuesta seleccionadas.")
            return
        if not atv_client.has_credentials():
            self._set_status("Sin credenciales ATV configuradas.")
            return
        self._run_atv_recheck(targets, source="seleccion")

    def _recheck_hacienda_batch(self) -> None:
        """Consulta ATV para TODAS las facturas sin respuesta del periodo."""
        targets = [
            r for r in self.all_records
            if r.estado_hacienda == ""
            and r.estado not in ("sin_xml", "huerfano")
            and r.xml_path is not None
            and len(r.clave) == 50
        ]
        if not targets:
            self._set_status("No hay facturas sin respuesta de Hacienda.")
            if hasattr(self, "_atv_batch_status_lbl"):
                self._atv_batch_status_lbl.configure(
                    text="Todas las facturas tienen respuesta.",
                    text_color=SUCCESS,
                )
            return
        if not atv_client.has_credentials():
            self._set_status("Sin credenciales ATV configuradas.")
            if hasattr(self, "_atv_batch_status_lbl"):
                self._atv_batch_status_lbl.configure(
                    text="Sin credenciales ATV.",
                    text_color=DANGER,
                )
            return
        if hasattr(self, "_btn_recheck_atv_batch"):
            self._btn_recheck_atv_batch.configure(state="disabled")
        self._run_atv_recheck(targets, source="lote")

    def _run_atv_recheck(self, targets: list[FacturaRecord], source: str) -> None:
        """Ejecuta la consulta ATV en un hilo y actualiza la UI."""
        generation = self._load_generation
        total = len(targets)
        self._set_status(f"ATV ({source}): verificando {total} factura(s)...")
        if hasattr(self, "_atv_batch_status_lbl"):
            self._atv_batch_status_lbl.configure(
                text=f"Verificando {total} factura(s)...",
                text_color=WARNING,
            )
        q: Queue = Queue()
        def _worker():
            saved = 0
            for i, record in enumerate(targets):
                try:
                    result = atv_client.query_invoice_status(record.clave)
                except Exception as exc:
                    logger.warning("ATV recheck error %s: %s", record.clave[:12], exc)
                    q.put(("progress", i + 1, total, "error"))
                    continue

                ind = result.get("ind_estado", "")
                xml_bytes = result.get("respuesta_xml_bytes")
                error_msg = result.get("error", "")
                
                logger.info("ATV response for %s: ind=%s, has_xml=%s, error=%s", 
                            record.clave[:15], ind, bool(xml_bytes), error_msg)

                q.put(("progress", i + 1, total, ind or "..."))

                if xml_bytes and ind not in ("no_encontrado", "desconocido", ""):
                    out = record.xml_path.parent / f"{record.clave}_MH.xml"
                    existed = out.exists()
                    try:
                        out.write_bytes(xml_bytes)
                        saved += 1
                        if existed:
                            logger.info("  -> XML sobreescrito en disco (forzando re-lectura): %s", out.name)
                        else:
                            logger.info("  -> Guardado nuevo XML: %s", out.name)
                    except Exception as exc:
                        logger.warning("ATV: error guardando XML %s: %s", record.clave, exc)
                elif ind == "no_recibido":
                    # Actualizar estado virtual si el sistema lo necesita en el record
                    pass

            q.put(("done", saved))
        threading.Thread(target=_worker, daemon=True).start()
        self.after(200, lambda: self._poll_atv_recheck(q, generation, source, total))

    def _poll_atv_recheck(self, q: Queue, generation: int, source: str, total: int) -> None:
        """Actualiza el status bar con el progreso de la verificacion ATV manual."""
        if generation != self._load_generation:
            return
        while not q.empty():
            msg = q.get()
            kind = msg[0]
            if kind == "progress":
                _, current, total_count, ind_estado = msg
                self._set_status(f"ATV ({source}): {current}/{total_count} -- {ind_estado}")
                if hasattr(self, "_atv_batch_status_lbl"):
                    self._atv_batch_status_lbl.configure(text=f"Verificando {current}/{total_count}...")
            elif kind == "done":
                saved = msg[1]
                if hasattr(self, "_btn_recheck_atv_batch"):
                    self._btn_recheck_atv_batch.configure(state="normal")
                if saved > 0:
                    self._set_status(f"ATV: {saved} respuesta(s) recuperada(s) -- recargando...")
                    if hasattr(self, "_atv_batch_status_lbl"):
                        self._atv_batch_status_lbl.configure(
                            text=f"{saved}/{total} respuesta(s) recuperada(s).",
                            text_color=SUCCESS,
                        )
                    self.after(800, lambda: self._load_session(self.session, reset_dates=False))
                else:
                    self._set_status(f"ATV ({source}): sin respuestas nuevas.")
                    if hasattr(self, "_atv_batch_status_lbl"):
                        self._atv_batch_status_lbl.configure(
                            text=f"Sin respuestas nuevas ({total} consultada(s)).",
                            text_color=MUTED,
                        )
                return
        self.after(200, lambda: self._poll_atv_recheck(q, generation, source, total))

    def _apply_pdf_enrichment(self, generation: int, enriched_records: list[FacturaRecord], parse_errors: list[str]):
        """Ya no se usa (PDFs cargados en _load_session). Mantenido por compatibilidad."""
        if generation != self._load_generation:
            return

        self.all_records = enriched_records
        self.records = self._apply_date_filter(self.all_records)
        self._refresh_tree()
        self._update_progress()
        self._set_status("Listo")

        if parse_errors:
            self._show_warning(
                "Advertencias en PDFs",
                f"Se detectaron incidencias en {len(parse_errors)} PDF(s).\n\n" + "\n".join(parse_errors[:5]),
            )

    # ── CONSTRUCCIÓN UI ───────────────────────────────────────────────────────
    def _build(self, parent=None):
        if parent is None:
            parent = self
        self._build_header(parent)
        self._build_body(parent)

    def _build_header(self, parent=None):
        if parent is None:
            parent = self
        hdr = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(1, weight=1)
        hdr.grid_columnconfigure(2, weight=1)

        # Logo
        ctk.CTkLabel(hdr, text="", image=get_icon("report", 28),
                      fg_color="#1a3a36", corner_radius=8,
                      width=32, height=32).grid(row=0, column=0, padx=(16,8), pady=14)
        ctk.CTkLabel(hdr, text="Clasificador  Contable",
                      font=F_APP_TITLE(),
                      text_color=TEXT).grid(row=0, column=1, sticky="w", padx=(0, 12))

        # Info cliente activo (bloque izquierdo)
        client_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        client_frame.grid(row=0, column=2, sticky="w")

        ctk.CTkLabel(client_frame, text="Cliente:",
                      font=F_LABEL(), text_color=MUTED).pack(side="left", padx=(0,6))
        self._lbl_cliente = ctk.CTkLabel(client_frame, text="Sin sesión",
                                          font=F_HEADING(),
                                          text_color=TEXT)
        self._lbl_cliente.pack(side="left")
        self._lbl_year = ctk.CTkLabel(client_frame, text="",
                                       font=F_SMALL(), text_color=MUTED,
                                       fg_color=CARD, corner_radius=20)
        self._lbl_year.pack(side="left", padx=(10,0), ipadx=8, ipady=2)

        # Filtros de fecha (bloque central)
        date_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        date_frame.grid(row=0, column=3, sticky="e", padx=8)

        ctk.CTkLabel(date_frame, text="Desde:", font=F_LABEL(),
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
            font=F_BODY(),
            height=32,
            corner_radius=8,
        )
        self._from_entry.pack(side="left")
        ctk.CTkButton(date_frame, text=" ", width=36, height=32,
                      image=get_icon("calendar", 16), compound="left",
                      fg_color=SURFACE, hover_color=BORDER, font=F_SMALL(), corner_radius=8,
                      command=lambda: self._open_date_picker("from")).pack(side="left", padx=(4, 8))

        ctk.CTkLabel(date_frame, text="Hasta:", font=F_LABEL(),
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
            font=F_BODY(),
            height=32,
            corner_radius=8,
        )
        self._to_entry.pack(side="left")
        ctk.CTkButton(date_frame, text=" ", width=36, height=32,
                      image=get_icon("calendar", 16), compound="left",
                      fg_color=SURFACE, hover_color=BORDER, font=F_SMALL(), corner_radius=8,
                      command=lambda: self._open_date_picker("to")).pack(side="left", padx=(4, 8))

        ctk.CTkButton(date_frame, text="Filtrar", width=70, height=32,
                       fg_color=TEAL, hover_color=TEAL_DIM, text_color="#0d1a18",
                       font=F_BODY_BOLD(), corner_radius=8,
                       command=self._on_filter).pack(side="left", padx=(8,0))

        # Botón limpiar cache y recargar
        ctk.CTkButton(date_frame, text="Limpiar cache", width=130, height=32,
                       fg_color=SURFACE, hover_color=BORDER, text_color=TEXT,
                       border_color=WARNING, border_width=1,
                       font=F_BODY_BOLD(), corner_radius=8,
                       command=self._clear_cache_and_reload).pack(side="left", padx=(8,0))

        # Botón cambiar cliente
        ctk.CTkButton(hdr, text="Cambiar cliente", width=150, height=32,
                       fg_color=CARD, hover_color=SURFACE, text_color=MUTED,
                       border_color=BORDER, border_width=1,
                       font=F_BODY_BOLD(), corner_radius=8,
                       command=self._open_session_view).grid(
            row=0, column=4, padx=(10, 8), pady=14)

        # Status
        self._status_var = ctk.StringVar(value="")
        ctk.CTkLabel(hdr, textvariable=self._status_var,
                      font=F_SMALL(), text_color=MUTED).grid(
            row=0, column=5, padx=(0, 16))

        # Pestañas de clasificación (fila 1)
        tabs_container = ctk.CTkFrame(hdr, fg_color="transparent")
        tabs_container.grid(row=1, column=0, columnspan=6, sticky="ew", padx=16, pady=(8, 8))
        tabs_container.grid_columnconfigure(0, weight=1)

        tab_configs = [
            ("todas", "Todas"),
            ("ingreso", "Ingresos"),
            ("egreso", "Egresos"),
            ("sin_receptor", "Sin Receptor"),
            ("ors", "ORS"),
            ("pendiente", "Pendientes"),
            ("sin_clave", "Sin clave"),
            ("omitidos", "Omitidos"),
            ("huerfanos", "Huerfanos"),
            ("rechazados", "Rechazados"),
            ("sin_respuesta", "Sin Respuesta"),
        ]

        for tab_id, tab_label in tab_configs:
            btn = ctk.CTkButton(
                tabs_container,
                text=tab_label,
                width=90,
                height=28,
                fg_color=CARD,
                hover_color=BORDER,
                text_color=TEXT,
                font=F_BODY_BOLD(),
                corner_radius=6,
                command=lambda t=tab_id: self._on_tab_clicked(t),
            )
            btn.pack(side="left", padx=4)
            self._tab_buttons[tab_id] = btn

        # Marcar pestaña inicial como activa
        self._update_tab_appearance(self._active_tab)

        # Separador
        ctk.CTkFrame(self, fg_color=BORDER, height=1, corner_radius=0).grid(
            row=0, column=0, columnspan=6, sticky="ew", pady=(0, 0)
        )

    def _build_body(self, parent=None):
        if parent is None:
            parent = self
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=8, pady=(8, 8))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=33, minsize=360)  # lista
        body.grid_columnconfigure(1, weight=52, minsize=520)  # visor PDF
        body.grid_columnconfigure(2, weight=18, minsize=280)  # clasificación

        self._build_list_panel(body)
        self._build_pdf_panel(body)
        self._build_classify_panel(body)

    # ── PANEL IZQUIERDO -- LISTA ───────────────────────────────────────────────
    def _build_list_panel(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=10, border_width=1, border_color=BORDER)
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)  # Treeview

        # Header del panel
        top = ctk.CTkFrame(frame, fg_color=CARD, corner_radius=10, height=44)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_propagate(False)
        top.grid_columnconfigure(0, weight=1)

        # Título del panel — jerarquía clara
        ctk.CTkLabel(top, text="Facturas del período",
                      font=F_HEADING(), text_color=TEXT).grid(
            row=0, column=0, sticky="w", padx=14, pady=10)

        # Badge de progreso — teal, compacto
        self._progress_var = ctk.StringVar(value="")
        ctk.CTkLabel(top, textvariable=self._progress_var,
                      font=F_BODY_BOLD(), text_color=TEAL).grid(
            row=0, column=1, sticky="w", padx=(0, 8))

        # ── Acciones secundarias (mismo peso visual) ───────────────────────
        _BTN_SEC = dict(
            height=32, fg_color=SURFACE, hover_color=BORDER,
            text_color=TEXT, font=F_BODY_BOLD(), corner_radius=8,
        )
        ctk.CTkButton(top, text="Exportar", width=100,
                      image=get_icon("download", 16), compound="left",
                      **_BTN_SEC,
                      command=self._export_report).grid(
            row=0, column=2, sticky="e", padx=(0, 4))

        ctk.CTkButton(top, text="Sanitizar", width=100,
                      image=get_icon("broom", 16), compound="left",
                      **_BTN_SEC,
                      command=self._sanitize_folders).grid(
            row=0, column=3, sticky="e", padx=(0, 4))

        ctk.CTkButton(top, text="Duplicados", width=110,
                      image=get_icon("duplicate", 16), compound="left",
                      **_BTN_SEC,
                      command=self._find_duplicate_pdfs).grid(
            row=0, column=4, sticky="e", padx=(0, 4))

        # ── CTA principal (Corte) — destaca por icono ligeramente más grande ──────
        ctk.CTkButton(top, text="Corte", width=100,
                      image=get_icon("report", 18), compound="left",
                      **_BTN_SEC,
                      command=self._generar_corte).grid(
            row=0, column=5, sticky="e", padx=(0, 10))

        # Consolidar — oculto hasta detectar renombrados
        self._btn_consolidar = ctk.CTkButton(
            top, text="Consolidar", width=90, height=28,
            fg_color=SURFACE, hover_color=BORDER, text_color=WARNING,
            font=F_LABEL(), corner_radius=6,
            command=self._consolidate_folders,
        )
        self._btn_consolidar.grid(row=0, column=6, sticky="e", padx=(0, 10))
        self._btn_consolidar.grid_remove()  # Oculto hasta detectar renombrados

        # Treeview con estilo oscuro
        tree_frame = ctk.CTkFrame(frame, fg_color=CARD, corner_radius=10)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # Columnas: Tipo, Fecha, Consecutivo, Emisor, Impuesto, Total
        cols = ("tipo", "fecha", "consecutivo", "emisor", "impuesto", "total")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                  selectmode="extended", style="Dark.Treeview")
        self.tree.heading("tipo",         text="TP", anchor="center")
        self.tree.heading("fecha",        text="Fecha", anchor="center")
        self.tree.heading("consecutivo",  text="Consecutivo", anchor="center")
        self.tree.heading("emisor",       text="Emisor", anchor="center")
        self.tree.heading("impuesto",     text="Impuesto", anchor="center")
        self.tree.heading("total",        text="Total", anchor="center")

        self.tree.column("tipo",         width=35,  stretch=False, anchor="center")
        self.tree.column("fecha",        width=75,  stretch=False, anchor="center")
        self.tree.column("consecutivo",  width=140, stretch=False, anchor="center")
        self.tree.column("emisor",       width=200, anchor="w")
        self.tree.column("impuesto",     width=90,  stretch=False, anchor="e")
        self.tree.column("total",        width=96,  stretch=False, anchor="e")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                             command=self.tree.yview, style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        # Etiquetas para colores de fondo según estado
        self.tree.tag_configure("clasificado",   background="#1a4d3d", foreground=TEXT)      # Verde oscuro
        self.tree.tag_configure("pendiente",     background="",        foreground=TEXT)      # Sin color (normal)
        self.tree.tag_configure("pendiente_pdf", background="#1a3d4d", foreground=TEXT)     # Azul oscuro
        self.tree.tag_configure("sin_xml",       background="#2d2d2d", foreground=MUTED)    # Gris oscuro
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Return>", lambda _e: self._on_tree_enter())
        self.tree.bind("<Left>", lambda _e: self._step_classify_category(-1))
        self.tree.bind("<Right>", lambda _e: self._step_classify_category(1))
        self.tree.bind("<Tab>", lambda _e: self._on_tree_tab())
        self.tree.bind("<Shift-Tab>", lambda _e: self._on_tree_shift_tab())
        self.tree.bind("<Control-a>", lambda _e: self._on_tree_select_all())

    # ── PANEL CENTRAL -- VISOR PDF ─────────────────────────────────────────────
    def _build_pdf_panel(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=10, border_width=1, border_color=BORDER)
        frame.grid(row=0, column=1, sticky="nsew", padx=6)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        # NO usar grid_propagate(False) -- colapsa el frame a tamaño 0
        # El layout estable viene de minsize en las columnas del body (en _build_body)

        # PDFViewer ocupa TODO el panel -- incluye su propia toolbar internamente
        self.pdf_viewer = PDFViewer(frame)
        self.pdf_viewer.grid(row=0, column=0, sticky="nsew")

    # ── PANEL DERECHO -- CLASIFICACIÓN ────────────────────────────────────────
    def _build_classify_panel(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=10, border_width=1, border_color=BORDER)
        frame.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # Header fijo
        top = ctk.CTkFrame(frame, fg_color=CARD, corner_radius=10, height=38)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_propagate(False)
        ctk.CTkLabel(top, text="Clasificación",
                      font=F_SECTION_TITLE(),
                      text_color=TEXT).pack(side="left", padx=12, pady=7)

        # Contenido scrollable
        scroll = ctk.CTkScrollableFrame(
            frame, fg_color="transparent",
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=MUTED,
        )
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)
        callbacks = ClassifyPanelCallbacks(
            on_classify=self._classify_selected,
            on_classify_safe=self._classify_selected_safe,
            on_auto_classify=self._auto_classify_current_tab,
            on_recover=self._recover_selected,
            on_link=self._link_omitted_to_xml,
            on_delete_omitido=self._delete_omitido,
            on_create_pdf=self._create_pdf_for_selected,
            on_open_new_cuenta=self._open_new_cuenta_dialog,
            on_open_dest_folder=self._open_dest_folder,
            on_form_change=self._update_path_preview,
            on_tab_out=self._focus_tree,
            on_shift_tab_out=self._focus_tree,
            on_recheck_hacienda=self._recheck_hacienda_selected,
            on_swap_pdf=self._swap_rejected_pdf,
        )
        self._classify_panel = ClassifyPanel(scroll, callbacks=callbacks)
        self._classify_panel.grid(row=0, column=0, sticky="ew")

        self._btn_classify = self._classify_panel.btn_classify
        self._btn_auto_classify = self._classify_panel.btn_auto_classify
        self._btn_create_pdf = self._classify_panel.btn_create_pdf
        self._btn_recover = self._classify_panel.btn_recover
        self._btn_link = self._classify_panel.btn_link
        self._btn_delete = self._classify_panel.btn_delete

        # ── Accion de pestaña ORS: Purgar ORS ─────────────────────────────────
        # Visible solo cuando la pestaña activa es ORS; independiente de seleccion.
        ors_inner = ctk.CTkFrame(scroll, fg_color=CARD, border_width=1, border_color=BORDER, corner_radius=12)
        ors_inner.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        ors_inner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            ors_inner,
            text="ACCIONES ORS",
            font=F_SECTION_LABEL(),
            text_color=DANGER,
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self._btn_purge_ors = ctk.CTkButton(
            ors_inner,
            text="Purgar ORS",
            font=F_BUTTON(),
            fg_color=DANGER,
            hover_color="#dc2626",
            text_color="#0d1a18",
            corner_radius=10,
            height=40,
            state="disabled",
            command=self._purge_ors_clicked,
        )
        self._btn_purge_ors.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))

        self._btn_ors_classify_manual = ctk.CTkButton(
            ors_inner,
            text="Clasificar como Gasto",
            font=F_BUTTON(),
            fg_color=WARNING,
            hover_color="#d97706",
            text_color="#0d1a18",
            corner_radius=10,
            height=36,
            state="disabled",
            command=self._on_ors_classify_manual,
        )
        self._btn_ors_classify_manual.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))

        self._ors_history_lbl = tk.Label(
            ors_inner,
            text="Ver historial de purgas",
            font=("Segoe UI", 10),
            fg=MUTED,
            bg=CARD,
            cursor="hand2",
            anchor="w",
        )
        self._ors_history_lbl.grid(row=3, column=0, sticky="w", padx=12, pady=(0, 10))
        self._ors_history_lbl.bind("<Button-1>", lambda _e: self._show_ors_purge_history())

        self._ors_frame = ors_inner
        self._ors_frame.grid_remove()  # Ocultar hasta que la pestaña sea ORS

        self._build_atv_batch_panel(scroll)

    def _build_atv_batch_panel(self, scroll):
        """Construye el panel de verificación ATV masiva para pestaña Sin Respuesta."""
        atv_inner = ctk.CTkFrame(scroll, fg_color=CARD, border_width=1, border_color=BORDER, corner_radius=12)
        atv_inner.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        atv_inner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            atv_inner,
            text="VERIFICACIÓN ATV",
            font=F_SECTION_LABEL(),
            text_color=WARNING,
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self._btn_recheck_atv_batch = ctk.CTkButton(
            atv_inner,
            text="Verificar todas via ATV",
            font=F_BUTTON(),
            fg_color=WARNING,
            hover_color="#e8a61c",
            text_color=BG,
            corner_radius=10,
            height=38,
            command=self._recheck_hacienda_batch,
        )
        self._btn_recheck_atv_batch.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))

        self._atv_batch_status_lbl = ctk.CTkLabel(
            atv_inner,
            text="Consulta el API de ATV para obtener respuestas faltantes.",
            font=F_SMALL(),
            text_color=MUTED,
            justify="left",
            anchor="w",
            wraplength=240,
        )
        self._atv_batch_status_lbl.grid(row=2, column=0, sticky="w", padx=12, pady=(0, 10))

        self._atv_batch_frame = atv_inner
        self._atv_batch_frame.grid_remove()

    def _step_classify_category(self, step: int):
        if not getattr(self, "_classify_panel", None):
            return None
        if not self.selected and not self.selected_records:
            return None
        if self._classify_panel.step_category(step):
            return "break"
        return None

    # ── TECLADO: ENTER / TAB / CTRL+A ────────────────────────────────────────────

    def _on_tree_enter(self):
        """Enter en el Treeview — solo clasifica si el botón está habilitado."""
        if not self.selected and not self.selected_records:
            return "break"
        btn_state = str(self._btn_classify.cget("state"))
        if btn_state == "disabled":
            return "break"
        self._classify_selected()
        return "break"

    def _classify_selected_safe(self):
        """Clasificar con validación de estado del botón (para Enter en campos del panel)."""
        btn_state = str(self._btn_classify.cget("state"))
        if btn_state == "disabled":
            return
        self._classify_selected()

    def _on_tree_tab(self):
        """Tab en Treeview → foco al primer widget interactivo del panel derecho."""
        if not self.selected and not self.selected_records:
            return "break"
        if getattr(self, "_classify_panel", None):
            self._classify_panel.focus_first_interactive()
        return "break"

    def _on_tree_shift_tab(self):
        """Shift-Tab en Treeview → foco al último widget del panel derecho."""
        if not self.selected and not self.selected_records:
            return "break"
        if getattr(self, "_classify_panel", None):
            self._classify_panel.focus_last_interactive()
        return "break"

    def _on_tree_select_all(self):
        """Ctrl+A en Treeview → seleccionar todas las facturas visibles."""
        all_items = self.tree.get_children()
        if all_items:
            self.tree.selection_set(all_items)
            self.tree.focus(all_items[0])
        return "break"

    def _focus_tree(self):
        """Devuelve foco al Treeview y restaura la selección visual."""
        self.tree.focus_set()
        sel = self.tree.selection()
        if sel:
            self.tree.focus(sel[0])

    # ── PESTAÑAS DE FACTURAS DEL PERÍODO ────────────────────────────────────────
    def _on_tab_clicked(self, tab: str):
        """Maneja click en pestaña. Filtra registros y actualiza UI."""
        self._active_tab = tab
        self._update_tab_appearance(tab)

        # Filtrar registros según pestaña activa
        if self.session:
            self.records = self._apply_filters()
            self._refresh_tree()
            self._update_progress()

        # Limpiar estado de botones contextuales al cambiar de pestaña
        self.selected = None
        self.selected_records = []
        self._sync_category_for_record(None)
        self._classify_panel.clear_selection_state()

        # Panel ORS: mostrar solo en pestaña ORS (accion de pestaña, no de seleccion)
        if tab == "ors" and self.session:
            self._ors_frame.grid()
            self._btn_purge_ors.configure(state="normal")
        else:
            self._ors_frame.grid_remove()
            self._btn_purge_ors.configure(state="disabled")

        # Panel ATV batch: mostrar solo en pestaña Sin Respuesta
        if tab == "sin_respuesta" and self.session:
            self._atv_batch_frame.grid()
        else:
            self._atv_batch_frame.grid_remove()

    def _update_tab_appearance(self, active_tab: str):
        """Actualiza colores de botones de pestañas."""
        for tab_id, btn in self._tab_buttons.items():
            if tab_id == active_tab:
                btn.configure(fg_color=TEAL, text_color=BG)
            else:
                btn.configure(fg_color=CARD, text_color=TEXT)

    def _get_client_cedula(self) -> str:
        """Obtiene cédula confiable del cliente desde client_profiles.json.

        Estructura: client_name -> gmail_account -> __email__:{gmail} -> cedula
        Con fallback inteligente a cedula más frecuente en XMLs si no hay match.
        """
        if not self.session:
            return ""

        try:
            from gestor_contable.core.client_profiles import load_profiles
            profiles = load_profiles()

            # Paso 1: Buscar perfil del cliente por nombre
            client_name = self.session.nombre
            if client_name in profiles:
                profile = profiles[client_name]
                if isinstance(profile, dict):
                    # Paso 2: Obtener gmail_account del perfil
                    gmail = str(profile.get("gmail_account", "")).strip().lower()
                    if gmail:
                        # Paso 3: Buscar entrada __email__:{gmail}
                        email_key = f"__email__:{gmail}"
                        if email_key in profiles:
                            email_profile = profiles[email_key]
                            if isinstance(email_profile, dict):
                                cedula = str(email_profile.get("cedula", "")).strip()
                                if cedula:
                                    # Limpiar cédula (solo dígitos)
                                    import re
                                    cedula_clean = re.sub(r"\D", "", cedula)
                                    if cedula_clean:
                                        logger.debug(f"Cédula obtenida de perfiles: {cedula_clean}")
                                        return cedula_clean
        except Exception as e:
            logger.warning(f"Error obteniendo cédula desde perfiles: {e}")

        # Fallback inteligente: si la cédula no match ningún registro,
        # usar la cédula más frecuente en todos los registros
        if self.all_records:
            from collections import Counter
            import re
            cedula_candidates = Counter()

            for r in self.all_records:
                if r.emisor_cedula:
                    cedula_clean = re.sub(r"\D", "", str(r.emisor_cedula))
                    if cedula_clean:
                        cedula_candidates[cedula_clean] += 1
                if r.receptor_cedula:
                    cedula_clean = re.sub(r"\D", "", str(r.receptor_cedula))
                    if cedula_clean:
                        cedula_candidates[cedula_clean] += 1

            if cedula_candidates:
                most_common_cedula, count = cedula_candidates.most_common(1)[0]
                logger.info(f"Cedula más frecuente en registros: {most_common_cedula} ({count} apariciones)")
                return most_common_cedula

        # Último fallback: cedula de sesión
        cedula_fallback = (self.session.cedula or "").strip()
        logger.info(f"Usando cedula fallback de sesión: {cedula_fallback}")
        return cedula_fallback

    # ── TABLA ─────────────────────────────────────────────────────────────────
    def _refresh_tree(self):
        """Refresca Treeview ordenado: Emisor -> Tipo -> Fecha (con colores por estado)."""
        self.tree.delete(*self.tree.get_children())

        if not self.records:
            return

        # Mapeo de tipo_documento -> abreviatura
        tipo_map = {
            "Factura Electrónica": "FE",
            "Factura electronica": "FE",
            "Nota de Crédito": "NC",
            "Nota de Débito": "ND",
            "Tiquete": "TQ",
        }

        # Reordenar por: Emisor -> Tipo de documento -> Fecha
        def sort_key(r):
            emisor = (r.emisor_nombre or "").lower()
            tipo = (r.tipo_documento or "").lower()
            fecha = r.fecha_emision or ""
            return (emisor, tipo, fecha)

        sorted_records = sorted(self.records, key=sort_key)

        # Preparar items + mapeo de clave -> record
        items_to_insert = []
        self._tree_clave_map = {}  # Mapeo visual: iid -> record (para _on_select)

        for idx, r in enumerate(sorted_records):
            # Estado para etiqueta de color
            db_rec = self._db_records.get(r.clave, {}) if self.db else {}
            estado = db_rec.get("estado") or r.estado
            # pendiente_pdf con categoria guardada → tratar como clasificado visualmente
            if estado == "pendiente_pdf" and db_rec.get("categoria"):
                estado = "clasificado"
            tag = estado if estado in ("clasificado", "pendiente", "pendiente_pdf", "sin_xml", "huerfano") else "pendiente"

            # Formatear campos
            tipo_raw = str(r.tipo_documento or "")
            tipo_short = tipo_map.get(tipo_raw, tipo_raw[:4])
            emisor_short = _short_name(r.emisor_nombre)

            # Valores numéricos con formato
            iva_13_fmt = _fmt_amount(r.iva_13)
            impuesto_fmt = _fmt_amount(r.impuesto_total)
            total_fmt = _fmt_amount(r.total_comprobante)

            # Orden de columnas: tipo, fecha, consecutivo, emisor, impuesto, total
            row_values = (
                tipo_short,
                r.fecha_emision,
                r.consecutivo,
                emisor_short,
                impuesto_fmt,
                total_fmt,
            )

            # Generar IID único: usar índice para evitar duplicados (ej: "506040..._{0}")
            iid = f"{r.clave}_{idx}" if r.clave else f"UNKNOWN_{idx}"
            items_to_insert.append((iid, row_values, tag))
            self._tree_clave_map[iid] = r  # Mapeo IID -> record

        # Insertar en batches para que UI responda
        batch_size = 200
        for batch_start in range(0, len(items_to_insert), batch_size):
            batch_end = min(batch_start + batch_size, len(items_to_insert))
            for iid, values, tag in items_to_insert[batch_start:batch_end]:
                self.tree.insert("", "end", iid=iid, values=values, tags=(tag,))

            self.update_idletasks()

            if hasattr(self, '_loading_overlay') and self._loading_overlay and self._loading_overlay.winfo_exists():
                pct = batch_end / len(items_to_insert)
                self._loading_overlay.update_progress(batch_end, len(items_to_insert))

    def _update_progress(self):
        if not self.records:
            self._progress_var.set("")
            return
        total = len(self.records)
        clf = sum(
            1
            for r in self.records
            if _is_effectively_classified(
                self._db_records.get(r.clave, {}) if self.db else {},
                r,
            )
        )
        pct = int(clf / total * 100) if total else 0
        self._progress_var.set(f"{clf}/{total}  ({pct}%)")

    # ── SELECCIÓN ─────────────────────────────────────────────────────────────
    def _on_select(self, _event=None):
        """Maneja la selección con debounce para evitar congelar la UI al usar Shift+Flechas."""
        if hasattr(self, '_select_timer') and self._select_timer:
            self.after_cancel(self._select_timer)
        self._select_timer = self.after(150, self._do_on_select)

    def _do_on_select(self):
        self._select_timer = None
        sel = self.tree.selection()

        # Si no hay selección, limpiar estado
        if not sel:
            self.selected = None
            self.selected_records = []
            self._sync_category_for_record(None)
            self._classify_panel.clear_selection_state()
            return

        # Resolver todos los records seleccionados
        records = []
        for iid in sel:
            if hasattr(self, '_tree_clave_map') and iid in self._tree_clave_map:
                records.append(self._tree_clave_map[iid])
            else:
                # Fallback: búsqueda por clave en records
                matches = [r for r in self.records if r.clave == iid]
                if matches:
                    records.append(matches[0])

        self.selected_records = records

        if len(records) == 1:
            # FLUJO EXISTENTE: una sola factura, sin cambios al comportamiento
            self._on_select_single(records[0])
        elif len(records) > 1:
            # MODO LOTE: múltiples facturas del mismo emisor
            self._on_multi_select(records)

    def _on_select_single(self, r: FacturaRecord):
        """Maneja selección de una sola factura."""
        self.selected = r
        self._sync_category_for_record(r)
        pdf_path = self._resolve_pdf_path_for_record(r)
        prev_text, prev_dest = self._resolve_previous_classification(r)
        vm = build_single_vm(
            r, self._active_tab, pdf_path, prev_text, prev_dest,
            pdf_duplicates_rejected=self._pdf_duplicates_rejected
        )
        self._render_selection_vm(vm)

    def _on_multi_select(self, records: list[FacturaRecord]):
        """Maneja selección de múltiples facturas (modo lote)."""
        vm = build_multi_vm(records)
        self.selected = records[0] if vm.mode != "multi_mixed" else None
        self._render_selection_vm(vm)
        self._sync_category_for_record(records[0] if vm.mode != "multi_mixed" else None)

    # ── HELPERS DE SELECCION ──────────────────────────────────────────────────

    def _resolve_pdf_path_for_record(self, r: FacturaRecord) -> Path | None:
        """Resuelve la ruta del PDF a cargar: original o ruta clasificada si ya fue movido."""
        if r.pdf_path and r.pdf_path.exists():
            return r.pdf_path
        if self.db and r.clave:
            db_record = self._db_records.get(r.clave)
            if db_record and db_record.get("ruta_destino"):
                ruta_destino = Path(db_record["ruta_destino"])
                if not ruta_destino.exists() and self.session:
                    cont_root = self.session.folder.parent.parent / "Contabilidades"
                    ruta_destino = (
                        heal_classified_path(ruta_destino, cont_root, self.db, r.clave)
                        or ruta_destino
                    )
                if ruta_destino.exists():
                    logger.debug(f"PDF cargado desde ruta clasificada: {ruta_destino}")
                    r.pdf_path = ruta_destino
                    return ruta_destino
                # exists() falló pero el registro está clasificado en BD.
                # Puede ser delay de OneDrive/Windows tras mover el archivo.
                # Devolver la ruta igual — el visor mostrará el error específico
                # ("PDF no encontrado" o "no descargado") en vez de quedar en blanco.
                if db_record.get("estado") == "clasificado":
                    logger.debug(f"PDF clasificado: exists() falló, pasando ruta al visor: {ruta_destino}")
                    r.pdf_path = ruta_destino
                    return ruta_destino
        return None

    def _resolve_previous_classification(self, r: FacturaRecord) -> tuple[str, Path | None]:
        """Devuelve (texto_clasificacion_previa, ruta_destino) para el panel RUTA."""
        if not self.db:
            return "--", None
        prev = self._db_records.get(r.clave) or {}
        estado = prev.get("estado")
        has_classification = (
            estado == "clasificado"
            or (estado == "pendiente_pdf" and prev.get("categoria"))
        )
        if not has_classification:
            return "--", None
        crumbs = " \u203a ".join(
            p for p in [
                prev.get("categoria"),
                prev.get("subtipo"),
                prev.get("nombre_cuenta"),
                prev.get("proveedor"),
            ] if p
        )
        # Construir linea secundaria con mes + fecha de clasificacion
        mes_str = self._get_mes_str(r.fecha_emision) if r.fecha_emision else ""
        fecha_clas = prev.get("fecha_clasificacion", "")
        secondary_parts = [p for p in (mes_str, fecha_clas) if p]
        secondary = " \u00b7 ".join(secondary_parts)
        prev_text = f"{crumbs}\n{secondary}"
        ruta_dest = prev.get("ruta_destino", "")
        if not ruta_dest:
            return prev_text, None
        ruta_dest_path = Path(ruta_dest)
        if not ruta_dest_path.is_absolute() and self.session:
            ruta_dest_path = self.session.folder.parent.parent / ruta_dest_path
        if not ruta_dest_path.exists() and self.session:
            cont_root = self.session.folder.parent.parent / "Contabilidades"
            sanada = heal_classified_path(ruta_dest_path, cont_root, self.db, prev.get("clave_numerica"))
            if sanada:
                ruta_dest_path = sanada
        return prev_text, ruta_dest_path

    def _render_selection_vm(self, vm: SelectionVM):
        """Aplica un SelectionVM a los widgets del panel derecho y visor."""
        self._selection_vm = vm
        # Visor PDF
        if vm.viewer_pdf_path is not None:
            self.pdf_viewer.load(vm.viewer_pdf_path)
        elif vm.viewer_message is not None:
            self.pdf_viewer.show_message(vm.viewer_message)
        elif vm.viewer_release_message is not None:
            self.pdf_viewer.release_file_handles(vm.viewer_release_message)
        else:
            self.pdf_viewer.clear()

        self._classify_panel.render(vm)
        self._update_path_preview()

    def _forced_form_values_for_record(self, record: FacturaRecord | None) -> dict[str, str] | None:
        """Retorna valores forzados del formulario según el tipo real de transacción."""
        if not record or not self.session:
            return None
        tx_kind = classify_transaction(record, self._get_client_cedula())
        if tx_kind == "ingreso":
            return {"categoria": "INGRESOS", "subtipo": ""}
        if tx_kind == "sin_receptor":
            return {"categoria": "SIN_RECEPTOR", "subtipo": ""}
        if tx_kind == "ors":
            db_rec = self._db_records.get(record.clave or "", {})
            # Override manual activo: el contador decidió clasificarlo como gasto propio
            if db_rec.get("ors_manual_override") == "manual":
                return None
            # Ya clasificado en BD con categoría distinta a OGND: respetar esa decisión
            if db_rec.get("estado") == "clasificado" and db_rec.get("categoria", "OGND") != "OGND":
                return None
            return {"categoria": "OGND", "subtipo": "ORS"}
        return None

    def _sync_category_for_record(self, record: FacturaRecord | None):
        """Sincroniza el selector de categoría con el contexto del documento."""
        forced = self._forced_form_values_for_record(record)
        manual_cats = list(self._catalog_categories)
        if forced:
            self._classify_panel.sync_category(
                manual_cats,
                forced_cat=forced["categoria"],
                forced_subtipo=forced.get("subtipo", ""),
            )
        else:
            self._classify_panel.sync_category(manual_cats)
        self._update_ors_classify_manual_btn(record)

    def _update_ors_classify_manual_btn(self, record: FacturaRecord | None):
        """Habilita el botón 'Clasificar como Gasto' solo cuando corresponde.

        Condiciones: pestaña ORS activa + registro seleccionado + el formulario
        sigue forzado (el override todavía no está activo para este registro).
        """
        if self._active_tab != "ors" or not record:
            self._btn_ors_classify_manual.configure(state="disabled")
            return
        db_rec = self._db_records.get(record.clave or "", {})
        already_unlocked = (
            db_rec.get("ors_manual_override") == "manual"
            or (db_rec.get("estado") == "clasificado" and db_rec.get("categoria", "OGND") != "OGND")
        )
        state = "disabled" if already_unlocked else "normal"
        self._btn_ors_classify_manual.configure(state=state)

    def _open_new_cuenta_dialog(self):
        """Open modal dialog to add a new account."""
        if not self.catalog_mgr:
            self._show_warning("Atención", "Catálogo no disponible.")
            return

        form_values = self._classify_panel.get_form_values()
        cat = form_values["cat"]
        subtipo = form_values["subtipo"]
        if cat != "GASTOS" or not subtipo:
            self._show_warning("Atención", "Selecciona una categoría y tipo primero.")
            return

        dialog = NewCuentaDialog(
            self,
            categoria=cat,
            subtipo=subtipo,
            existing_cuentas=self.catalog_mgr.cuentas(cat, subtipo),
            catalog_mgr=self.catalog_mgr,
            on_success=self._on_nueva_cuenta_added,
        )

    def _on_nueva_cuenta_added(self, new_cuenta: str):
        """Called when a new account is successfully added."""
        self._classify_panel.refresh_current_options(new_cuenta)
        self._update_path_preview()
        self._set_status(f"Cuenta '{new_cuenta}' agregada.")

    def _update_path_preview(self):
        """Muestra la ruta de destino estimada debajo del formulario."""
        if not self.session:
            self._classify_panel.set_path_preview("")
            return
        if not self.selected and not self.selected_records:
            self._classify_panel.set_path_preview("")
            return

        form_values = self._classify_panel.get_form_values()
        cat = form_values["cat"]
        subtipo = form_values["subtipo"] if cat in ("GASTOS", "OGND") else ""
        cuenta = form_values["cuenta"] if cat == "GASTOS" else ""
        prov = form_values["prov"].strip() if cat in ("COMPRAS", "GASTOS", "ACTIVO") else ""
        fecha   = (self.selected.fecha_emision if self.selected else "") or ""

        # Validar mínimos necesarios para construir la ruta
        if not cat:
            self._classify_panel.set_path_preview("")
            return
        if cat in ("COMPRAS", "ACTIVO") and not prov:
            self._classify_panel.set_path_preview("")
            return
        if cat == "GASTOS" and not (subtipo and cuenta and prov):
            self._classify_panel.set_path_preview("")
            return
        if cat == "OGND" and not subtipo:
            self._classify_panel.set_path_preview("")
            return
        # INGRESOS y SIN_RECEPTOR no necesitan validación (sin subtipo/cuenta/prov)

        try:
            dest  = build_dest_folder(self.session.folder, fecha, cat, subtipo, cuenta, prov)
            parts = dest.parts
            snippet = (".../" + "/".join(parts[-4:]) + "/") if len(parts) > 4 else str(dest) + "/"
            self._classify_panel.set_path_preview(snippet)
        except Exception:
            self._classify_panel.set_path_preview("")

    def _on_filter(self):
        if not self.session:
            return
        self._load_range_if_needed()

    # ── CARGA POR RANGO ACUMULATIVO ───────────────────────────────────────────

    @staticmethod
    def _months_for_range(from_str: str, to_str: str) -> set[tuple[int, int]]:
        return months_for_range(from_str, to_str)

    def _load_range_if_needed(self):
        """Carga los meses faltantes del rango activo o solo re-filtra si ya estan en cache."""
        from_str = self.from_var.get()
        to_str = self.to_var.get()

        needed = self._months_for_range(from_str, to_str)
        if not needed:
            # Rango invalido o vacio: solo re-filtrar lo que ya hay
            self.records = self._apply_filters()
            self.selected = None
            self.selected_records = []
            self.pdf_viewer.clear()
            self._refresh_tree()
            self._update_progress()
            self._set_status("Filtro aplicado")
            return

        missing = needed - self._loaded_months
        if not missing:
            # Todo ya en cache — respuesta instantanea
            self.records = self._apply_filters()
            self.selected = None
            self.selected_records = []
            self.pdf_viewer.clear()
            self._refresh_tree()
            self._update_progress()
            self._set_status("Filtro aplicado")
            return

        # Hay meses que no se han cargado aun — lanzar carga en worker
        self._start_range_load(missing)

    def _start_range_load(self, missing_months: set[tuple[int, int]]):
        """Carga en background los meses faltantes y los fusiona en el cache."""
        self._range_load_generation += 1
        generation = self._range_load_generation
        self._range_load_queue = Queue()

        if not hasattr(self, '_loading_overlay') or not self._loading_overlay:
            self._loading_overlay = LoadingOverlay(self)
            self._loading_overlay.grid(row=0, column=0, rowspan=2, sticky="nsew")
        else:
            self._loading_overlay.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self._loading_overlay.update_status("Cargando periodo adicional...")
        self.update_idletasks()
        self.update()

        session = self.session

        def worker():
            try:
                def _progress(msg: str, current: int, total: int):
                    self.after(0, lambda m=msg, c=current, t=total: (
                        self._loading_overlay.update_status(m),
                        self._loading_overlay.update_progress(c, t),
                    ))

                result = load_range_worker(session, missing_months, _progress)
                self._range_load_queue.put((
                    "ok",
                    (
                        generation,
                        result.new_records,
                        result.loaded_months,
                        result.hidden_response_files_by_clave,
                        result.ors_autopurge_summary,
                    ),
                ))
            except Exception as exc:
                logger.exception("Error en range worker")
                self._range_load_queue.put(("error", (generation, str(exc))))

        threading.Thread(target=worker, daemon=True).start()
        self.after(150, self._poll_range_load)

    def _poll_range_load(self):
        """Polling de la cola de carga de rango adicional."""
        if self._range_load_queue.empty():
            self.after(150, self._poll_range_load)
            return

        status, payload = self._range_load_queue.get()

        if hasattr(self, '_loading_overlay') and self._loading_overlay:
            self._loading_overlay.grid_remove()

        if status == "error":
            generation, message = payload
            if generation != self._range_load_generation:
                return
            self._show_error("Error al cargar periodo", message)
            return

        generation, new_records, loaded_months, hidden_response_files_by_clave, ors_autopurge_summary = payload
        if generation != self._range_load_generation:
            return

        for r in new_records:
            self._records_map[r.clave] = r
        self._loaded_months |= loaded_months
        self.all_records = list(self._records_map.values())
        self._hidden_response_files_by_clave = self._merge_hidden_response_files_by_clave(
            self._hidden_response_files_by_clave,
            hidden_response_files_by_clave,
        )
        self._ors_autopurge_summary = self._normalize_ors_autopurge_summary(ors_autopurge_summary)

        self.records = self._apply_filters()
        self.selected = None
        self.selected_records = []
        self.pdf_viewer.clear()
        self._refresh_tree()
        self._update_progress()
        self._set_status(self._format_ors_autopurge_status("Filtro aplicado"))
        ors_autopurge_notice = self._build_ors_autopurge_notice()
        if ors_autopurge_notice:
            ModalOverlay.show_info(self, "Auto-saneo ORS", ors_autopurge_notice)

    def _open_date_picker(self, target: str):
        self._close_date_picker()
        initial = self.from_var.get() if target == "from" else self.to_var.get()
        entry = self._from_entry if target == "from" else self._to_entry

        def on_pick(value: str):
            if target == "from":
                self.from_var.set(value)
            else:
                self.to_var.set(value)
            self._user_set_dates = True
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

    @staticmethod
    def _detect_period_month_year(records: list[FacturaRecord], *fallback_dates: str) -> tuple[int, int]:
        for record in records:
            try:
                dt = datetime.strptime((record.fecha_emision or "").strip(), "%d/%m/%Y")
                return dt.month, dt.year
            except ValueError:
                continue

        for raw_date in fallback_dates:
            try:
                dt = datetime.strptime((raw_date or "").strip(), "%d/%m/%Y")
                return dt.month, dt.year
            except ValueError:
                continue

        now = datetime.now()
        return now.month, now.year

    def _current_client_folder_name(self) -> str:
        if self.session:
            return self.session.folder.name
        return _sanitize_folder(self._lbl_cliente.cget("text") or "CLIENTE")

    def _export_report(self):
        if not self.records:
            ModalOverlay.show_info(self, "Exportar", "No hay registros para exportar.")
            return

        owner_name = self._lbl_cliente.cget("text") or "REPORTE DE COMPROBANTES"
        date_from_label = self.from_var.get().strip() or "01/01/1900"
        date_to_label = self.to_var.get().strip() or datetime.now().strftime("%d/%m/%Y")

        client_cedula = self._get_client_cedula()
        period_records = [r for r in self._apply_date_filter(self.all_records) if not r.razon_omisión]
        if not period_records:
            ModalOverlay.show_info(self, "Exportar", "No hay registros válidos en el período seleccionado.")
            return

        mes_actual, anio_actual = self._detect_period_month_year(
            period_records,
            self.from_var.get(),
            self.to_var.get(),
        )
        reportes_dir = (
            network_drive()
            / f"PF-{anio_actual}"
            / "REPORTES"
            / month_folder_name(mes_actual)
            / self._current_client_folder_name()
        )

        try:
            reportes_dir.mkdir(parents=True, exist_ok=True)
            target_path = resolve_incremental_path(
                reportes_dir,
                default_export_filename(
                    owner_name,
                    self.from_var.get(),
                    self.to_var.get(),
                    mes=mes_actual,
                    anio=anio_actual,
                ),
            )
            coverage_info = export_period_report(
                period_records,
                self._db_records if self.db else {},
                client_cedula,
                target_path,
                owner_name,
                date_from_label,
                date_to_label,
            )
            msg = f"Reporte guardado en:\n{target_path}"
            unassigned_count = int(coverage_info.get("unassigned_count", 0) or 0)
            if unassigned_count:
                unassigned_keys = [str(k) for k in coverage_info.get("unassigned_keys", [])[:5]]
                suffix = f"\n... y {unassigned_count - 5} más" if unassigned_count > 5 else ""
                claves_txt = "\n".join(unassigned_keys) if unassigned_keys else "(sin detalle)"
                msg += (
                    f"\n\nAdvertencia: {unassigned_count} registro(s) quedaron en la hoja 'Fuera Reporte'.\n"
                    f"Primeras claves:\n{claves_txt}{suffix}"
                )
                self._set_status("Reporte exportado con advertencias")
            else:
                self._set_status("Reporte exportado")
            ModalOverlay.show_info(self, "Exportar", msg)
        except Exception as exc:
            self._show_error("Error al exportar", str(exc))

    # ── CORTE MENSUAL ──────────────────────────────────────────────────────────
    def _generar_corte(self) -> None:
        """Orquesta el flujo completo del corte mensual:
        run_corte → resolver_ambiguos → generar_corte_excel.
        """
        if not self.session or not self.all_records:
            ModalOverlay.show_info(self, "Corte", "No hay datos cargados para generar el corte.")
            return

        from gestor_contable.core.corte_engine import run_corte
        from gestor_contable.core.corte_excel import generar_corte_excel, default_filename
        from gestor_contable.gui.corte_ambiguo_modal import resolver_ambiguos
        from gestor_contable.core.classification_utils import classify_transaction

        client_name = self.session.folder.name
        client_hacienda_name = self.session.nombre or client_name
        client_cedula = self._get_client_cedula()
        mdir = metadata_dir(self.session.folder)

        # Registros del período: excluir omitidos y ORS (terceros — ni emisor ni receptor es el cliente)
        # sin_receptor SÍ entra: son facturas sin cédula receptor pero válidas para el cliente
        # Excepción: ORS con override manual activo SÍ entran (gasto propio en terreno de tercero)
        period_records = [
            r for r in self._apply_date_filter(self.all_records)
            if not r.razon_omisión
            and (
                classify_transaction(r, client_cedula) != "ors"
                or self._db_records.get(r.clave or "", {}).get("ors_manual_override") == "manual"
            )
            and get_hacienda_review_status(r) != "rechazada"
        ]
        if not period_records:
            ModalOverlay.show_info(self, "Corte", "No hay facturas válidas en el período seleccionado.")
            return

        mes_actual, anio_actual = self._detect_period_month_year(
            period_records,
            self.from_var.get(),
            self.to_var.get(),
        )
        cortes_dir = (
            network_drive()
            / f"PF-{anio_actual}"
            / "CORTES"
            / month_folder_name(mes_actual)
            / client_name
        )
        try:
            cortes_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self._show_error("Error al guardar", f"No se pudo crear la carpeta de cortes:\n{cortes_dir}\n\n{exc}")
            return

        filename = default_filename(client_hacienda_name, mes_actual, anio_actual)

        self._set_status("Generando corte...")
        self._progress_var.set("Clasificando facturas...")

        def worker():
            try:
                from gestor_contable.core.xml_manager import CRXMLManager

                xml_manager = CRXMLManager()

                def on_progress(current: int, total: int):
                    pct = f"{current}/{total}" if total else ""
                    self.after(0, lambda p=pct: self._progress_var.set(f"Clasificando {p}"))

                resultados = run_corte(
                    records           = period_records,
                    client_cedula     = client_cedula,
                    client_name       = client_name,
                    metadata_dir      = mdir,
                    xml_manager       = xml_manager,
                    progress_callback = on_progress,
                )

                self.after(0, lambda: self._on_corte_done(
                    resultados, cortes_dir, filename, client_name, mes_actual, anio_actual
                ))
            except Exception as exc:
                logger.exception("Error al generar corte")
                self.after(0, lambda e=str(exc): (
                    self._progress_var.set(""),
                    self._set_status("Error en corte"),
                    self._show_error("Error al generar corte", e),
                ))

        threading.Thread(target=worker, daemon=True).start()

    def _on_corte_done(
        self,
        resultados,
        output_dir: Path,
        output_filename: str,
        client_name: str,
        mes: int,
        anio: int,
    ) -> None:
        """Llamado desde el worker cuando run_corte termina. Hilo principal."""
        from gestor_contable.core.corte_engine import CATEGORIA_AMBIGUO
        from gestor_contable.core.corte_engine import CorteEngine
        from gestor_contable.core.corte_excel import generar_corte_excel
        from gestor_contable.gui.corte_ambiguo_modal import resolver_ambiguos

        ambiguos = [i for i in resultados if i.categoria == CATEGORIA_AMBIGUO]
        self._progress_var.set(
            f"Clasificadas | {len(resultados) - len(ambiguos)} auto · {len(ambiguos)} AMBIGUO"
        )

        mdir = metadata_dir(self.session.folder)
        client_cedula = self._get_client_cedula()

        # Necesitamos un engine para el modal (guardar decisiones de proveedor)
        from gestor_contable.core.client_profiles import get_or_fetch_activities
        actividades = get_or_fetch_activities(client_name=client_name, cedula=client_cedula)
        engine = CorteEngine(
            client_cedula = client_cedula,
            client_name   = client_name,
            actividades   = actividades,
            metadata_dir  = mdir,
            xml_manager   = None,
        )

        def _escribir_excel(items_finales):
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                target_path = resolve_incremental_path(output_dir, output_filename)
                generar_corte_excel(
                    resultados  = items_finales,
                    client_name = client_name,
                    output_path = target_path,
                    mes         = mes,
                    anio        = anio,
                )
                self._progress_var.set("")
                self._set_status("Corte generado")
                ModalOverlay.show_success(
                    self, "Corte Mensual",
                    f"Corte generado exitosamente.\n\n{target_path}"
                )
            except Exception as exc:
                logger.exception("Error al escribir Excel del corte")
                self._progress_var.set("")
                self._set_status("Error en corte")
                self._show_error("Error al guardar el corte", str(exc))

        resolver_ambiguos(
            parent      = self,
            resultados  = resultados,
            engine      = engine,
            on_complete = _escribir_excel,
        )

    # ── DETECCIÓN DE CARPETAS RENOMBRADAS ─────────────────────────────────────
    def _show_rename_warning(self, renames: list[dict]):
        """Avisa al usuario que el contador renombró carpetas del cliente en Contabilidades."""
        import tkinter as tk

        detail_lines = []
        total_affected = 0
        for r in renames:
            detail_lines.append(f"  {r['mes']}: \"{r['old_name']}\" → \"{r['new_name']}\"  ({r['affected']} registros)")
            total_affected += r["affected"]

        detail = "\n".join(detail_lines)
        msg = (
            f"Se detectaron {len(renames)} carpeta(s) del cliente renombradas en Contabilidades.\n"
            f"Esto ocurre cuando el contador renombra la carpeta (ej: agrega una 'L').\n\n"
            f"{detail}\n\n"
            f"Total de registros con ruta desactualizada: {total_affected}\n\n"
            f"¿Actualizar los registros en la base de datos con las nuevas rutas?"
        )

        _overlay, modal, close_fn = ModalOverlay.build(self)

        ctk.CTkLabel(
            modal, text="Carpetas del cliente renombradas",
            font=F_TITLE(), text_color=WARNING,
        ).pack(pady=(20, 8), padx=24, anchor="w")

        txt = tk.Text(
            modal, wrap="word", height=8,
            bg=SURFACE, fg=TEXT, relief="flat",
            font=("Consolas", 11), bd=0, padx=10, pady=8,
        )
        txt.insert("1.0", msg)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        btn_frame = ctk.CTkFrame(modal, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(0, 16))

        def on_ignorar():
            close_fn()

        def on_actualizar():
            close_fn()
            self._apply_rename_fixes(renames)

        ctk.CTkButton(
            btn_frame, text="Ignorar", width=120,
            fg_color=SURFACE, hover_color=BORDER, text_color=MUTED,
            command=on_ignorar,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_frame, text="Actualizar registros", width=200,
            fg_color=TEAL, hover_color="#14b8a6", text_color="#0d0f14",
            command=on_actualizar,
        ).pack(side="left")

    def _effective_client_name(self, mes_str: str) -> str | None:
        """Retorna el nombre renombrado de la carpeta del cliente para el mes dado.

        Si el contador renombró la carpeta (ej: agregó ' (L)'), devuelve el nombre nuevo
        para que classify_record use la carpeta correcta en Contabilidades.
        """
        for r in self._detected_renames:
            if r.get("mes") == mes_str:
                return r.get("new_name")
        return None

    @staticmethod
    def _get_mes_str(fecha: str) -> str:
        """Convierte 'DD/MM/YYYY' al string de mes usado en Contabilidades (ej: '03-MARZO')."""
        from gestor_contable.core.classifier import _MESES
        try:
            from datetime import datetime as _dt
            d = _dt.strptime(fecha.strip(), "%d/%m/%Y")
            return f"{d.month:02d}-{_MESES[d.month]}"
        except Exception:
            return ""

    def _apply_rename_fixes(self, renames: list[dict]):
        """Actualiza en BD todas las rutas rotas usando heal_classified_path."""
        if not self.session or not self.db:
            return

        contabilidades_root = self.session.folder.parent.parent / "Contabilidades"

        def worker():
            updated = 0
            db_records = self.db.get_records_map()
            for clave, rec in db_records.items():
                ruta = rec.get("ruta_destino", "")
                if not ruta:
                    continue
                from pathlib import Path as _Path
                if _Path(ruta).exists():
                    continue
                new_path = heal_classified_path(ruta, contabilidades_root, self.db, clave)
                if new_path:
                    updated += 1
            self.after(0, lambda: self._on_rename_fixes_done(updated))

        threading.Thread(target=worker, daemon=True).start()

    def _on_rename_fixes_done(self, updated: int):
        """Callback cuando terminó la actualización de rutas."""
        if not self.session or not self.db:
            return   # sesión cambió mientras corría el worker
        self._db_records = self.db.get_records_map()
        self._refresh_tree()
        self._update_progress()
        self._show_info(
            "Rutas actualizadas",
            f"Se actualizaron {updated} registro(s) con las nuevas rutas de carpetas.\n"
            f"Los cambios quedaron guardados en la base de datos."
        )

    # ── CONSOLIDACIÓN DE CARPETAS DUPLICADAS ──────────────────────────────────
    def _consolidate_folders(self):
        """Mueve PDFs de carpetas con nombre incorrecto a las carpetas renombradas por el contador."""
        if not self.session or not self.db:
            return
        if not self._detected_renames:
            self._show_info("Consolidar", "No hay carpetas renombradas detectadas.")
            return

        contabilidades_root = self.session.folder.parent.parent / "Contabilidades"

        # Construir resumen de qué se va a hacer
        lines = []
        for r in self._detected_renames:
            lines.append(f"  {r['mes']}: '{r['old_name']}' -> '{r['new_name']}' ({r['affected']} archivo(s))")
        detalle = "\n".join(lines)

        if not self._ask(
            "Consolidar carpetas duplicadas",
            f"Se moverán los PDFs de las carpetas con nombre anterior a las carpetas renombradas "
            f"y se actualizará la base de datos.\n\n{detalle}\n\n¿Deseas continuar?"
        ):
            return

        renames_snapshot = list(self._detected_renames)
        db = self.db

        def worker():
            from gestor_contable.core.classification_utils import consolidate_duplicate_client_folders
            total_moved = 0
            all_errors: list[str] = []
            for r in renames_snapshot:
                moved, errors = consolidate_duplicate_client_folders(
                    contabilidades_root,
                    original_name=r["old_name"],
                    renamed_name=r["new_name"],
                    db=db,
                    month=r["mes"],
                )
                total_moved += moved
                all_errors.extend(errors)

            def on_done():
                if all_errors:
                    self._show_warning(
                        "Consolidación completada con errores",
                        f"Se movieron {total_moved} archivo(s).\n\nErrores:\n" + "\n".join(all_errors[:10]),
                    )
                else:
                    self._show_info(
                        "Consolidación completada",
                        f"Se movieron {total_moved} archivo(s) a las carpetas correctas.\n"
                        f"La base de datos fue actualizada.",
                    )
                self._load_session(self.session, reset_dates=False)

            self.after(0, on_done)

        threading.Thread(target=worker, daemon=True).start()

    # ── SANITIZACIÓN DE CARPETAS VACÍAS ────────────────────────────────────────
    def _sanitize_folders(self):
        """Detecta y elimina carpetas vacías (manual, con confirmación)."""
        if not self.session:
            ModalOverlay.show_info(self, "Sanitizar", "No hay sesión activa")
            return

        def worker():
            from gestor_contable.core.folder_sanitizer import (
                find_empty_folders,
                find_residual_contabilidades_folders,
            )
            try:
                empty_folders = find_empty_folders(self.session.folder)

                pf_root = self.session.folder.parent.parent
                contabilidades_root = pf_root / "Contabilidades"
                db_records = self.db.get_records_map() if self.db else {}
                residuals = find_residual_contabilidades_folders(
                    contabilidades_root,
                    self.session.folder.name,
                    db_records,
                )

                # Separar residuales con archivos (solo reporte) de las limpias (eliminar raiz)
                residuals_with_content = [r for r in residuals if r["has_files"]]
                residual_roots_with_files = {r["path"] for r in residuals_with_content}
                residual_roots_clean = [r["path"] for r in residuals if not r["has_files"]]

                # Excluir de empty_folders cualquier path dentro de una residual con archivos
                filtered_empty = [
                    p for p in empty_folders
                    if not any(p == root or root in p.parents
                               for root in residual_roots_with_files)
                ]

                # Agregar solo las raices de residuales limpias
                # (sus subdirs ya estan en filtered_empty via find_empty_folders)
                all_empty = list(dict.fromkeys(filtered_empty + residual_roots_clean))

                self.after(0, lambda: self._show_sanitization_modal(
                    all_empty, residuals_with_content
                ))
            except Exception as e:
                self.after(0, lambda error=e: self._show_error("Error al escanear", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def _show_sanitization_modal(
        self,
        empty_folders: list[Path],
        residuals_with_content: list[dict] | None = None,
    ):
        """Muestra modal de confirmación con carpetas a eliminar."""
        residuals_with_content = residuals_with_content or []
        if not empty_folders and not residuals_with_content:
            ModalOverlay.show_info(self, "Sanitizar", "No hay carpetas vacias\n\nTodas las carpetas de clasificación tienen contenido.")
            return

        overlay, card, close_fn = ModalOverlay.build(self)

        # Header
        header = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=0)
        header.pack(fill="x")
        if empty_folders:
            header_text = f"Se encontraron {len(empty_folders)} carpeta(s) vacias"
        else:
            header_text = "Carpetas residuales detectadas (solo reporte)"
        ctk.CTkLabel(
            header,
            text=header_text,
            font=F_MODAL_SUBTITLE(),
            text_color=TEXT,
        ).pack(side="left", padx=16, pady=12)

        # Body con listado
        body = ctk.CTkFrame(card, fg_color=BG)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        # Scrollable frame para lista
        from customtkinter import CTkScrollableFrame
        list_frame = CTkScrollableFrame(body, fg_color=CARD, corner_radius=8)
        list_frame.grid(row=0, column=0, sticky="nsew")

        # Mostrar carpetas vacias a eliminar (ruta relativa para claridad)
        contabilidades_root = self.session.folder.parent.parent / "Contabilidades"
        for folder in sorted(empty_folders):
            try:
                rel_path = folder.relative_to(contabilidades_root)
            except ValueError:
                rel_path = folder
            label_text = f"  {rel_path}"
            ctk.CTkLabel(
                list_frame,
                text=label_text,
                font=F_MODAL_HINT(),
                text_color=MUTED,
                justify="left",
            ).pack(anchor="w", padx=12, pady=4)

        # Sección informativa: residuales con contenido (no se eliminan)
        if residuals_with_content:
            ctk.CTkLabel(
                list_frame,
                text="Carpetas residuales con contenido (no se eliminaran):",
                font=F_MODAL_HINT(),
                text_color=WARNING,
            ).pack(anchor="w", padx=12, pady=(12, 2))
            for r in residuals_with_content:
                ctk.CTkLabel(
                    list_frame,
                    text=f"  {r['mes']}/{r['path'].name}  [contiene archivos]",
                    font=F_MODAL_HINT(),
                    text_color=MUTED,
                ).pack(anchor="w", padx=12, pady=2)

        # Footer con botones
        footer = ctk.CTkFrame(body, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        footer.grid_columnconfigure(0, weight=1)

        # Info y advertencia (solo si hay carpetas a eliminar)
        if empty_folders:
            ctk.CTkLabel(
                footer,
                text="Solo se eliminaran carpetas COMPLETAMENTE vacias",
                font=F_MODAL_HINT(),
                text_color=WARNING,
            ).pack(anchor="w")

            ctk.CTkLabel(
                footer,
                text="Si hay error de acceso: Cierra Explorador Windows si esta abierto en estas carpetas",
                font=F_MODAL_MICRO(),
                text_color=MUTED,
            ).pack(anchor="w", pady=(4, 0))
        else:
            ctk.CTkLabel(
                footer,
                text="Las carpetas residuales tienen archivos y no se eliminaran automaticamente.",
                font=F_MODAL_HINT(),
                text_color=WARNING,
            ).pack(anchor="w")

        # Botones
        button_frame = ctk.CTkFrame(footer, fg_color="transparent")
        button_frame.pack(anchor="e", pady=(8, 0))

        ctk.CTkButton(
            button_frame,
            text="Cancelar" if empty_folders else "Cerrar",
            width=100,
            height=32,
            fg_color=SURFACE,
            hover_color=BORDER,
            text_color=TEXT,
            corner_radius=8,
            command=close_fn,
        ).pack(side="left", padx=(0, 8))

        if empty_folders:
            ctk.CTkButton(
                button_frame,
                text="Eliminar",
                width=100,
                height=32,
                fg_color=DANGER,
                hover_color="#f56565",
                text_color="white",
                corner_radius=8,
                command=lambda: self._execute_sanitization(empty_folders, close_fn),
            ).pack(side="left")

    def _execute_sanitization(self, empty_folders: list[Path], close_fn):
        """Ejecuta la eliminación de carpetas vacías en thread."""
        close_fn()

        def worker():
            from gestor_contable.core.folder_sanitizer import delete_empty_folders
            try:
                deleted, errors = delete_empty_folders(empty_folders)
                self.after(0, lambda: self._show_sanitization_result(deleted, errors))
            except Exception as e:
                self.after(0, lambda error=e: self._show_error("Error al eliminar", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def _show_sanitization_result(self, deleted: int, errors: list[str]):
        """Muestra resultado de la sanitización."""
        message = f"{deleted} carpeta(s) eliminada(s)"
        if errors:
            message += f"\n\n{len(errors)} error(es):\n" + "\n".join(f"  • {e}" for e in errors[:5])
            # Detectar si hay errores de permisos
            has_perm_errors = any("Permisos" in e or "administrativo" in e.lower() for e in errors)
            if has_perm_errors:
                message += "\n\nSOLUCION:\n  1. Cierra la app\n  2. Ejecuta como ADMINISTRADOR\n  3. Intenta de nuevo"
            ModalOverlay.show_warning(self, "Sanitización completada", message)
        else:
            ModalOverlay.show_success(self, "Sanitización completada", message)
        self._set_status(f"Sanitización: {deleted} carpeta(s) eliminada(s)")

    # ── RECUPERACIÓN DE PDFs HUÉRFANOS ────────────────────────────────────────
    def _recover_selected(self):
        """Recupera el PDF huérfano seleccionado."""
        if not self.selected or not self.db:
            return

        # Obtener metadata del PDF huérfano
        orphaned_info = getattr(self.selected, "_orphaned_info", None)
        if not orphaned_info:
            self._show_warning("Error", "Este PDF no tiene información de recuperación")
            return

        ruta_actual = orphaned_info.get("ruta_actual")
        ruta_esperada = orphaned_info.get("ruta_esperada")
        clave = orphaned_info.get("clave")

        if not ruta_esperada:
            self._show_warning("Error", "No se pudo determinar la ruta de destino")
            return

        # Confirmar
        motivo = orphaned_info.get("motivo", "desconocido")
        if not self._ask(
            "Recuperar PDF",
            f"¿Recuperar este PDF?\n\n"
            f"Clave: {clave}\n"
            f"Motivo: {motivo}\n"
            f"De: {Path(ruta_actual).name}\n"
            f"A: {Path(ruta_esperada).name}"
        ):
            return

        # Ejecutar recuperación
        def worker():
            try:
                from gestor_contable.core.classifier import recover_orphaned_pdf
                if recover_orphaned_pdf(orphaned_info, self.db):
                    self.after(0, lambda: self._show_info("Recuperado", "PDF movido exitosamente"))
                    self.after(0, self._load_session, self.session)
                else:
                    self.after(0, lambda: self._show_error("Error", "No se pudo recuperar el PDF"))
            except Exception as e:
                self.after(0, lambda error=e: self._show_error("Error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def _link_omitted_to_xml(self):
        """Vincula un PDF omitido a un XML sin PDF disponible."""
        if not self.selected or not self.db:
            return

        # Obtener XMLs sin PDF que puedan ser vinculados a este PDF
        xmls_without_pdf = [
            r for r in self.all_records
            if r.xml_path and not r.pdf_path and r.estado == "pendiente_pdf"
        ]

        if not xmls_without_pdf:
            self._show_warning(
                "Sin XMLs disponibles",
                "No hay XMLs sin PDF disponibles para vincular.\n\n"
                "Asegúrate de que haya archivos XML en la carpeta que aún no tengan PDF."
            )
            return

        # Diálogo de selección embebido
        _ov, selection_window, _close_sel = ModalOverlay.build(self)

        # Header
        header = ctk.CTkFrame(selection_window, fg_color=SURFACE, corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text="Seleccionar XML para vincular",
            font=F_MODAL_SUBTITLE(),
            text_color=TEXT,
        ).pack(side="left", padx=16, pady=12)

        # Body con Treeview
        body = ctk.CTkFrame(selection_window, fg_color=BG)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        # Treeview
        from tkinter import ttk
        tree_frame = ctk.CTkFrame(body, fg_color=CARD, corner_radius=8)
        tree_frame.pack(fill="both", expand=True, pady=(0, 12))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.grid(row=0, column=1, sticky="ns")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=CARD, foreground=TEXT, fieldbackground=CARD)
        style.configure("Treeview.Heading", background=SURFACE, foreground=TEXT)

        tree = ttk.Treeview(
            tree_frame,
            columns=("emisor", "fecha", "tipo", "clave"),
            height=12,
            yscrollcommand=scrollbar.set,
            selectmode="browse",
        )
        scrollbar.configure(command=tree.yview)

        tree.column("#0", width=0, stretch=False)
        tree.column("emisor", anchor="w", width=200)
        tree.column("fecha", anchor="w", width=80)
        tree.column("tipo", anchor="w", width=60)
        tree.column("clave", anchor="w", width=150)

        tree.heading("#0", text="")
        tree.heading("emisor", text="Emisor")
        tree.heading("fecha", text="Fecha")
        tree.heading("tipo", text="Tipo")
        tree.heading("clave", text="Clave")

        # Llenar árbol con XMLs disponibles
        for idx, r in enumerate(xmls_without_pdf):
            tree.insert(
                "", "end",
                iid=str(idx),
                values=(
                    r.emisor_nombre[:30],
                    r.fecha_emision,
                    (r.tipo_documento or "")[:10],
                    r.clave,
                )
            )

        tree.grid(row=0, column=0, sticky="nsew")

        # Botones
        button_frame = ctk.CTkFrame(body, fg_color="transparent")
        button_frame.pack(fill="x")

        def on_cancel():
            _close_sel()

        def on_select():
            selection = tree.selection()
            if not selection:
                self._show_warning("Selección", "Selecciona un XML para vincular")
                return

            idx = int(selection[0])
            selected_xml = xmls_without_pdf[idx]

            _close_sel()

            # Confirmar vinculación
            if not self._ask(
                "Confirmar vinculación",
                f"¿Vincular el PDF a este XML?\n\n"
                f"Emisor: {selected_xml.emisor_nombre}\n"
                f"Fecha: {selected_xml.fecha_emision}\n"
                f"Tipo: {selected_xml.tipo_documento}\n"
                f"Clave: {selected_xml.clave}"
            ):
                return

            # Ejecutar vinculación
            omitido_record = self.selected

            def worker():
                try:
                    import shutil
                    src_pdf = omitido_record.pdf_path
                    if not src_pdf or not src_pdf.exists():
                        raise FileNotFoundError(f"PDF omitido no encontrado: {src_pdf}")

                    # Destino: carpeta PDF del cliente, con nombre = clave del XML
                    # xml_path está en CLIENT/XML/archivo.xml → PDF en CLIENT/PDF/
                    dest_dir = selected_xml.xml_path.parent.parent / "PDF"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest_pdf = dest_dir / f"{selected_xml.clave}.pdf"

                    # Copiar de forma segura (SHA256)
                    import hashlib

                    def sha256(path):
                        h = hashlib.sha256()
                        with open(path, "rb") as f:
                            for chunk in iter(lambda: f.read(65536), b""):
                                h.update(chunk)
                        return h.hexdigest()

                    shutil.copy2(src_pdf, dest_pdf)
                    if sha256(src_pdf) != sha256(dest_pdf):
                        dest_pdf.unlink(missing_ok=True)
                        raise IOError("Verificación SHA256 fallida — PDF no movido")

                    # Eliminar original omitido
                    src_pdf.unlink()

                    self.after(0, lambda: self._show_info(
                        "Vinculacion completada",
                        f"PDF vinculado al XML de {selected_xml.emisor_nombre}"
                    ))
                    self.after(0, self._load_session, self.session)
                except Exception as e:
                    self.after(0, lambda error=e: self._show_error("Error en vinculación", str(error)))

            # Liberar el lock del visor antes de iniciar el worker
            self.pdf_viewer.release_file_handles("Vinculando PDF...")
            threading.Thread(target=worker, daemon=True).start()

        ctk.CTkButton(
            button_frame, text="Cancelar", fg_color=SURFACE, hover_color=BORDER,
            text_color=TEXT, command=on_cancel, width=100, corner_radius=8,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            button_frame, text="Vincular", fg_color="#8b5cf6", hover_color="#7c3aed",
            text_color="#0d1a18", command=on_select, width=100, corner_radius=8,
        ).pack(side="right")

    def _swap_rejected_pdf(self) -> None:
        """Intercambia el PDF actual por el descartado (delegando a la capa de aplicacion)."""
        vm = self._selection_vm
        if not self.selected or not vm or vm.mode != "single":
            return
        
        if not vm.swap_pdf_target:
            return
        
        r = self.selected[0] if isinstance(self.selected, list) else self.selected
        rejected_path = vm.swap_pdf_target
        
        success, error_msg = execute_pdf_swap(r, rejected_path, self._pdf_duplicates_rejected)
        
        if not success:
            self._show_warning("Intercambiar PDF", error_msg)
            return
            
        self._on_select_single(r)

    def _create_pdf_for_selected(self):
        """Crea un PDF desde los datos del XML para facturas sin PDF."""
        r = self.selected
        if not r or r.estado != "pendiente_pdf" or not r.xml_path:
            return

        # Ruta destino: .../CLIENT/PDF/{clave}.pdf
        client_folder = r.xml_path.parent.parent
        pdf_root = client_folder / "PDF"
        output_path = pdf_root / f"{r.clave}.pdf"

        self._btn_create_pdf.configure(state="disabled", text="Generando...")

        def _do_generate():
            from gestor_contable.core.pdf_generator import generate_factura_pdf, extract_items_from_xml
            try:
                # Intentar extraer items del XML
                items = extract_items_from_xml(r.xml_path) if r.xml_path and r.xml_path.exists() else None
                generate_factura_pdf(r, output_path, items=items)
                self.after(0, lambda: _on_done())
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda e=err: _on_error(e))

        def _on_done():
            r.pdf_path = output_path
            r.estado = "pendiente"
            self._refresh_tree()
            self._update_progress()
            self.pdf_viewer.load(output_path)
            self._on_select_single(r)  # Re-trigger para actualizar botones

        def _on_error(err):
            self._btn_create_pdf.configure(state="normal", text="Crear PDF")
            ModalOverlay.show_error(self, "Error al generar PDF", f"No se pudo generar el PDF:\n{err}")

        threading.Thread(target=_do_generate, daemon=True).start()

    def _delete_omitido(self):
        """Borra uno o más PDFs omitidos del disco."""
        # Determinar qué registros borrar
        records_to_delete = []
        if self.selected_records:
            # Multi-selección: verificar que todos sean omitidos
            records_to_delete = [r for r in self.selected_records if r.razon_omisión]
        elif self.selected and self.selected.razon_omisión:
            # Selección simple
            records_to_delete = [self.selected]

        if not records_to_delete:
            self._show_warning("Advertencia", "Selecciona PDFs omitidos para borrar")
            return

        # Preparar mensaje de confirmación
        if len(records_to_delete) == 1:
            msg = (
                f"¿Borrar este PDF omitido?\n\n"
                f"Archivo: {records_to_delete[0].pdf_path.name if records_to_delete[0].pdf_path else 'desconocido'}\n"
                f"Razón: {records_to_delete[0].razon_omisión}\n\n"
                "Esta acción no se puede deshacer."
            )
        else:
            msg = (
                f"¿Borrar {len(records_to_delete)} PDFs omitidos?\n\n"
                f"Se eliminarán {len(records_to_delete)} archivos de forma permanente.\n"
                "Esta acción no se puede deshacer."
            )

        if not self._ask("Borrar PDFs", msg, confirm_text="Sí, borrar"):
            return

        # Cerrar visor en UI thread para liberar el archivo antes de lanzar el worker
        if self.selected and any(r.clave == self.selected.clave for r in records_to_delete):
            self.pdf_viewer._close_doc()

        # Ejecutar borrado en worker — solo I/O, sin tocar estado UI
        def worker():
            deleted_claves = []
            errors = []

            for record in records_to_delete:
                try:
                    if record.pdf_path:
                        Path(record.pdf_path).unlink(missing_ok=True)
                        deleted_claves.append(record.clave)
                except Exception as e:
                    errors.append(f"{record.pdf_path.name if record.pdf_path else 'desconocido'}: {str(e)}")

            self.after(0, lambda: self._on_delete_omitido_done(deleted_claves, errors))

        threading.Thread(target=worker, daemon=True).start()

    def _on_delete_omitido_done(self, deleted_claves: list, errors: list):
        """Callback en hilo UI: aplica resultados del borrado de omitidos."""
        deleted = len(deleted_claves)
        if deleted > 0:
            deleted_set = set(deleted_claves)
            self.all_records = [r for r in self.all_records if r.clave not in deleted_set]
            self.selected = None
            self.selected_records = []
            self.records = self._apply_filters()
            self._refresh_tree()
            self._update_progress()
            self.pdf_viewer._close_doc()
            if deleted == 1:
                self._show_info("PDF borrado", "Archivo eliminado exitosamente")
            else:
                self._show_info("PDFs borrados", f"{deleted} archivos eliminados exitosamente")

        if errors:
            msg_error = "Errores en el borrado:\n\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                msg_error += f"\n... y {len(errors) - 5} errores más"
            self._show_error("Error", msg_error)

    def _show_info(self, title: str, message: str):
        ModalOverlay.show_info(self, title, message)

    # ── CLASIFICACIÓN ─────────────────────────────────────────────────────────
    def _classify_selected(self):
        if not self.session or not self.selected or not self.db:
            return

        # Bloquear clasificación de facturas con bloqueo de Hacienda
        all_selected = self.selected_records if self.selected_records else ([self.selected] if self.selected else [])
        rechazados = [
            r for r in all_selected
            if get_hacienda_review_status(r) == "rechazada"
        ]
        if rechazados:
            nombres = "\n".join(r.emisor_nombre[:50] for r in rechazados[:5])
            suffix = f"\n... y {len(rechazados) - 5} más" if len(rechazados) > 5 else ""
            self._show_warning(
                "Factura rechazada",
                f"Las siguientes facturas fueron rechazadas por Hacienda y no pueden clasificarse:\n{nombres}{suffix}"
            )
            return
        sin_respuesta = [
            r for r in all_selected
            if get_hacienda_review_status(r) == "sin_respuesta"
        ]
        if sin_respuesta:
            nombres = "\n".join(r.emisor_nombre[:50] for r in sin_respuesta[:5])
            suffix = f"\n... y {len(sin_respuesta) - 5} más" if len(sin_respuesta) > 5 else ""
            self._show_warning(
                "Sin respuesta de Hacienda",
                f"Las siguientes facturas siguen sin respuesta de Hacienda y no pueden clasificarse todavía:\n{nombres}{suffix}"
            )
            return

        form_values = self._classify_panel.get_form_values()
        cat = form_values["cat"].strip().upper()
        subtipo = form_values["subtipo"].strip().upper() if cat in ("GASTOS", "OGND") else ""
        cuenta = form_values["cuenta"].strip().upper() if cat == "GASTOS" else ""
        prov = form_values["prov"].strip().upper() if cat in ("COMPRAS", "GASTOS", "ACTIVO") else ""

        if not cat:
            self._show_warning("Atención", "Selecciona una categoría.")
            return
        if cat == "GASTOS" and not subtipo:
            self._show_warning("Atención", "Selecciona el tipo de gasto.")
            return
        if cat == "GASTOS" and not cuenta:
            self._show_warning("Atención", "Selecciona la cuenta contable.")
            return
        if cat in ("COMPRAS", "GASTOS", "ACTIVO") and not prov:
            self._show_warning("Atención", "Ingresa el proveedor.")
            return
        if cat == "OGND" and not subtipo:
            self._show_warning("Atención", "Selecciona el tipo OGND.")
            return
        # INGRESOS y SIN_RECEPTOR no necesitan validación adicional

        if len(self.selected_records) <= 1:
            # FLUJO EXISTENTE: clasificar una sola factura
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
            _client_override = self._effective_client_name(self._get_mes_str(record.fecha_emision))

            def worker():
                try:
                    classify_record(record, session.folder, db, cat, subtipo, cuenta, prov,
                                    client_name_override=_client_override)
                    self.after(0, self._on_classify_ok)
                except Exception as exc:
                    self.after(0, lambda e=exc: self._on_classify_error(str(e)))

            threading.Thread(target=worker, daemon=True).start()
        else:
            # MODO LOTE: clasificar múltiples facturas
            records_to_classify = list(self.selected_records)
            n = len(records_to_classify)

            # Pedir confirmación si alguno ya estaba clasificado
            ya_clasificados = [
                r for r in records_to_classify
                if self._db_records.get(r.clave, {}).get("estado") == "clasificado"
            ]
            if ya_clasificados:
                if not self._ask("Reclasificar en lote",
                                  f"{len(ya_clasificados)} de {n} facturas ya estan clasificadas.\n"
                                  f"¿Deseas reclasificarlas?"):
                    return

            self._btn_classify.configure(state="disabled", text=f"Clasificando 0/{n}...")
            self.pdf_viewer.release_file_handles("Clasificando en lote...")

            record_list = records_to_classify
            session = self.session
            db = self.db

            def worker():
                errores = []
                for i, record in enumerate(record_list):
                    if i % 100 == 0:
                        self.after(0, lambda i=i: self._btn_classify.configure(
                            text=f"Clasificando {i+1}/{n}..."
                        ))
                    try:
                        _override = self._effective_client_name(
                            self._get_mes_str(record.fecha_emision)
                        )
                        classify_record(record, session.folder, db, cat, subtipo, cuenta, prov,
                                        client_name_override=_override)
                    except Exception as exc:
                        errores.append((record, str(exc)))

                self.after(0, lambda: self._on_batch_classify_done(n, errores))

            threading.Thread(target=worker, daemon=True).start()

    def _on_classify_ok(self):
        if self.db and self.selected:
            updated = self.db.get_record(self.selected.clave)
            if updated:
                self._db_records[self.selected.clave] = updated
        saved_clave = self.selected.clave if self.selected else None
        self._btn_classify.configure(state="normal", text="Clasificar")
        self._refresh_tree()
        self._update_progress()
        # Auto-avance: seleccionar el siguiente registro si existe
        if saved_clave:
            # Buscar IID correcto (formato: clave_idx) usando _tree_clave_map
            saved_iid = next(
                (iid for iid, r in self._tree_clave_map.items() if r.clave == saved_clave),
                None
            )
            if saved_iid and self.tree.exists(saved_iid):
                # Intentar obtener el siguiente registro
                next_iid = self.tree.next(saved_iid)
                # Si hay siguiente, seleccionarlo; si no, quedarse en el actual
                target_iid = next_iid if next_iid else saved_iid
                self.tree.selection_set(target_iid)
                self.tree.focus(target_iid)
                self.tree.see(target_iid)
        self._on_select()

    def _on_batch_classify_done(self, total: int, errores: list[tuple]):
        """Maneja finalización de clasificación en lote."""
        exitosos = total - len(errores)

        # Actualizar BD local en memoria con los nuevos estados (una sola query)
        if self.db:
            self._db_records.update(self.db.get_records_map())

        # Refrescar árbol para actualizar estado visual de las filas
        self._refresh_tree()
        self._update_progress()

        # Restaurar botón y mostrar resultado
        if errores:
            self._btn_classify.configure(
                state="normal",
                text=f"Clasificacion: {exitosos}/{total} OK"
            )
            # Mostrar primeros errores (máximo 5)
            msgs = "\n".join(
                f"• {r.emisor_nombre}: {e}"
                for r, e in errores[:5]
            )
            if len(errores) > 5:
                msgs += f"\n... y {len(errores) - 5} errores mas"
            self._show_error("Errores en clasificacion en lote", msgs)
        else:
            self._btn_classify.configure(
                state="normal",
                text=f"Clasificacion completada: {total} OK"
            )
            self._show_info("Exito", f"{total} facturas clasificadas correctamente.")

        # Limpiar selección multi
        self.selected_records = []
        self.tree.selection_remove(self.tree.selection())

    def _auto_classify_current_tab(self):
        """Auto-clasifica todos los registros en la pestaña actual (Ingresos o Sin Receptor)."""
        if not self.session or not self.db or self._active_tab not in ("ingreso", "sin_receptor"):
            return

        # Determinar categoría según tab activo
        if self._active_tab == "ingreso":
            categoria = "INGRESOS"
        elif self._active_tab == "sin_receptor":
            categoria = "SIN_RECEPTOR"
        else:
            return

        # Obtener registros de la pestaña actual que no estén clasificados
        registros_a_clasificar = [
            r for r in self.records
            if not r.razon_omisión
            and self._db_records.get(r.clave, {}).get("estado") != "clasificado"
        ]

        if not registros_a_clasificar:
            self._show_info("Sin registros", f"No hay registros pendientes en {self._active_tab}")
            return

        # Pedir confirmación
        if not self._ask(
            f"Auto-clasificar {categoria}",
            f"¿Clasificar {len(registros_a_clasificar)} registros como {categoria}?\n\n"
            f"Se moverán automáticamente a:\n"
            f"Contabilidades/[mes]/[cliente]/{categoria}/"
        ):
            return

        # Ejecutar clasificación en worker thread
        self._btn_auto_classify.configure(state="disabled", text=f"Clasificando 0/{len(registros_a_clasificar)}...")
        self.pdf_viewer.release_file_handles("Clasificando en lote...")

        records_list = registros_a_clasificar
        session = self.session
        db = self.db
        n = len(records_list)

        def worker():
            errores = []
            for i, record in enumerate(records_list):
                if i % 100 == 0:
                    self.after(0, lambda i=i: self._btn_auto_classify.configure(
                        text=f"Clasificando {i+1}/{n}..."
                    ))
                try:
                    # Para INGRESOS y SIN_RECEPTOR, no se necesitan subtipo, cuenta, proveedor
                    _override = self._effective_client_name(
                        self._get_mes_str(record.fecha_emision)
                    )
                    classify_record(record, session.folder, db, categoria, "", "", "",
                                    client_name_override=_override)
                except Exception as exc:
                    errores.append((record, str(exc)))

            self.after(0, lambda: self._on_batch_classify_done(n, errores))

        threading.Thread(target=worker, daemon=True).start()

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

    def _apply_filters(self) -> list[FacturaRecord]:
        """Aplica filtro de pestaña activa + filtro de fecha sobre all_records."""
        tab_filtered = filter_records_by_tab(
            self.all_records,
            self._active_tab,
            self._get_client_cedula(),
            self._db_records,
        )
        return self._apply_date_filter(tab_filtered)

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
                # Mantener visibles registros sin fecha parseable:
                # sin_xml (PDF sin XML) y huerfano (PDF en Contabilidades sin BD)
                if record.estado in ("sin_xml", "huerfano"):
                    filtered.append(record)
                continue
            if from_dt and fecha < from_dt:
                continue
            if to_dt and fecha > to_dt:
                continue
            filtered.append(record)
        return filtered

    def _on_classify_error(self, msg: str):
        self._btn_classify.configure(state="normal", text="Clasificar")
        self._show_error("Error al clasificar", msg)

    # ── HELPERS ───────────────────────────────────────────────────────────────
    def _set_status(self, text: str):
        self._status_var.set(text)

    @staticmethod
    def _normalize_ors_autopurge_summary(summary: dict | None) -> dict:
        base = summary or {}
        return {
            "moved_files": list(base.get("moved_files", []) or []),
            "batch_ids": list(base.get("batch_ids", []) or []),
        }

    @staticmethod
    def _merge_hidden_response_files_by_clave(
        current: dict[str, list[dict]] | None,
        incoming: dict[str, list[dict]] | None,
    ) -> dict[str, list[dict]]:
        """Fusiona respuestas ocultas por clave sin perder meses ya cargados."""
        merged: dict[str, list[dict]] = {}
        seen_by_clave: dict[str, set[str]] = {}

        for source in (current, incoming):
            for clave, entries in (source or {}).items():
                clave_norm = str(clave or "").strip()
                if not clave_norm:
                    continue

                bucket = merged.setdefault(clave_norm, [])
                seen = seen_by_clave.setdefault(clave_norm, set())

                for entry in entries or []:
                    item = dict(entry or {})
                    ruta = str(item.get("ruta", "") or "").strip()
                    archivo = str(item.get("archivo", "") or "").strip()
                    documento_root = str(item.get("documento_root", "") or "").strip()
                    dedupe_key = ruta or f"{archivo}|{documento_root}|{clave_norm}"
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    bucket.append(item)

        return merged

    def _format_ors_autopurge_status(self, base_text: str) -> str:
        moved_files = self._ors_autopurge_summary.get("moved_files", [])
        batch_ids = self._ors_autopurge_summary.get("batch_ids", [])
        if not moved_files:
            return base_text
        return (
            f"{base_text} · ORS auto-saneó {len(moved_files)} respuesta(s) "
            f"en {len(batch_ids)} lote(s)"
        )

    def _build_ors_autopurge_notice(self) -> str:
        moved_files = self._ors_autopurge_summary.get("moved_files", [])
        batch_ids = self._ors_autopurge_summary.get("batch_ids", [])
        if not moved_files:
            return ""
        lotes = ", ".join(batch_ids[:3])
        suffix = ""
        if len(batch_ids) > 3:
            suffix = f" y {len(batch_ids) - 3} lote(s) más"
        return (
            f"[auto_saneo_ors] Se movieron {len(moved_files)} respuesta(s) huérfana(s) "
            f"a {len(batch_ids)} lote(s) ORS activo(s).\n"
            f"Lotes: {lotes}{suffix}\n"
            "Revise Historial de cuarentenas para el detalle."
        )

    def _show_error(self, title: str, msg: str):
        ModalOverlay.show_error(self, title, msg)

    def _show_warning(self, title: str, msg: str):
        ModalOverlay.show_warning(self, title, msg)

    def _show_parse_errors_modal(self, title: str, parse_errors: list[str], records_count: int = 0, failed_xml_files: list[str] | None = None, receptor_response_files: list | None = None):
        """Modal in-app con lista scrollable de advertencias y botón de exportar."""
        import tkinter as tk

        overlay, card, close_fn = ModalOverlay.build(self)

        # ── Header ───────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text=f"{title}", font=F_BUTTON(),
                      text_color=WARNING).pack(side="left", padx=16, pady=12)
        ctk.CTkLabel(hdr,
                      text=f"{len(parse_errors)} advertencias  ·  {records_count} facturas cargadas",
                      font=F_SMALL(), text_color=MUTED).pack(side="right", padx=16, pady=12)

        # ── Lista scrollable ─────────────────────────────────────────────────
        txt_frame = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=6)
        txt_frame.pack(fill="both", expand=True, padx=16, pady=(12, 8))

        sb = tk.Scrollbar(txt_frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(txt_frame, wrap="word", bg=SURFACE, fg=TEXT, relief="flat",
                       font=("Consolas", 10), bd=0, padx=10, pady=8,
                       yscrollcommand=sb.set)
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)

        for i, err in enumerate(parse_errors, 1):
            txt.insert("end", f"[{i:03d}] {err}\n")
        txt.config(state="disabled")

        # ── Botones ──────────────────────────────────────────────────────────
        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.pack(pady=(0, 16))

        close = close_fn

        def ignore_failed_xml():
            """Guarda los XML fallidos en ignored_xml_errors.json y cierra el modal."""
            import json as _json
            if not self.session:
                return
            meta = self.session.folder / ".metadata"
            meta.mkdir(parents=True, exist_ok=True)
            ignored_path = meta / "ignored_xml_errors.json"
            existing: list[str] = []
            if ignored_path.exists():
                try:
                    existing = _json.loads(ignored_path.read_text(encoding="utf-8")).get("ignored", [])
                except Exception:
                    logger.warning("No se pudo leer ignored_xmls.json", exc_info=True)
            new_list = list(dict.fromkeys(existing + (failed_xml_files or [])))
            ignored_path.write_text(
                _json.dumps({"ignored": new_list}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            close()

        ctk.CTkButton(
            btns, text="Exportar errores", image=get_icon("download", 16), compound="left", width=160,
            fg_color=SURFACE, hover_color=BORDER,
            text_color=TEAL, border_width=1, border_color=TEAL,
            command=lambda: self._export_parse_errors_txt(parse_errors, records_count),
        ).pack(side="left", padx=8)
        if failed_xml_files:
            ctk.CTkButton(
                btns, text=f"Ignorar {len(failed_xml_files)} XML fallido{'s' if len(failed_xml_files) != 1 else ''}", width=190,
                fg_color=SURFACE, hover_color=BORDER,
                text_color=WARNING, border_width=1, border_color=WARNING,
                command=ignore_failed_xml,
            ).pack(side="left", padx=8)
        if receptor_response_files:
            ctk.CTkButton(
                btns,
                text=f"Cuarentena respuestas receptor ({len(receptor_response_files)})",
                width=240,
                fg_color=SURFACE, hover_color=BORDER,
                text_color=DANGER, border_width=1, border_color=DANGER,
                command=lambda: self._purge_receptor_responses(receptor_response_files, close),
            ).pack(side="left", padx=8)
        ctk.CTkButton(
            btns, text="Entendido", width=120,
            fg_color=SURFACE, hover_color=BORDER, text_color=TEXT,
            command=close,
        ).pack(side="left", padx=8)

    def _export_parse_errors_txt(self, parse_errors: list[str], records_count: int):
        """Genera TXT estructurado optimizado para análisis por IA."""
        from tkinter import filedialog
        from datetime import datetime
        from pathlib import Path as _Path

        ts = datetime.now()
        client = self.session.folder.name if self.session else "sin_cliente"
        default_name = f"errores_{client}_{ts.strftime('%Y%m%d_%H%M%S')}.txt"

        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Texto", "*.txt")],
            initialfile=default_name,
            title="Exportar reporte de errores",
        )
        if not path:
            return

        # ── Categorizar ──────────────────────────────────────────────────────
        cats: dict[str, list[str]] = {
            "total_mismatch":       [],
            "iva_mismatch":         [],
            "xml_duplicate":        [],
            "xml_failed":           [],
            "respuesta_receptor":   [],
            "respuesta_failed":     [],
            "pdf_duplicate":        [],
            "other":                [],
        }
        for err in parse_errors:
            if "total no cuadra" in err:
                cats["total_mismatch"].append(err)
            elif "IVAs no cuadra" in err or "suma de IVA" in err:
                cats["iva_mismatch"].append(err)
            elif "duplicados descartados" in err or "XML(s) duplicados" in err:
                cats["xml_duplicate"].append(err)
            elif "[respuesta_receptor]" in err:
                cats["respuesta_receptor"].append(err)
            elif "[respuesta_irrecuperable]" in err or "[respuesta_no_asociada]" in err:
                cats["respuesta_failed"].append(err)
            elif "Error cargando carpeta XML" in err or (
                ": " in err and "factura" not in err.lower() and "pdf" not in err.lower()
            ):
                cats["xml_failed"].append(err)
            elif "PDF" in err or "pdf" in err.lower():
                cats["pdf_duplicate"].append(err)
            else:
                cats["other"].append(err)

        label_map = {
            "total_mismatch":     "total_comprobante no cuadra  (subtotal + IVA ≠ total)",
            "iva_mismatch":       "suma de IVAs no cuadra con impuesto_total",
            "xml_duplicate":      "XML duplicados (descartados automáticamente)",
            "xml_failed":         "XML fallidos / error de parseo",
            "respuesta_receptor": "Respuestas Hacienda a confirmaciones receptor (normales, sin accion requerida)",
            "respuesta_failed":   "Mensajes de respuesta con incidencia (irrecuperables o no asociados)",
            "pdf_duplicate":      "PDF duplicado resuelto automáticamente",
            "other":              "otros / sin categoría",
        }

        SEP = "=" * 80

        lines: list[str] = [
            SEP,
            "GESTOR CONTABLE — REPORTE DE ERRORES DE CARGA",
            "Optimizado para análisis por IA (Claude)",
            SEP,
            f"TIMESTAMP         : {ts.strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        if self.session:
            lines += [
                f"CLIENTE           : {self.session.folder.name}",
                f"AÑO_FISCAL        : PF-{self.session.year}",
                f"CARPETA_CLIENTE   : {self.session.folder}",
                f"CARPETA_XML       : {self.session.folder / 'XML'}",
                f"CARPETA_PDF       : {self.session.folder / 'PDF'}",
            ]

        lines += [
            f"FACTURAS_CARGADAS : {records_count}",
            f"TOTAL_ADVERTENCIAS: {len(parse_errors)}",
            "",
            SEP,
            "RESUMEN POR TIPO",
            SEP,
        ]
        for cat, errs in cats.items():
            if errs:
                lines.append(f"  [{len(errs):>4}]  {label_map[cat]}")

        lines += [
            "",
            SEP,
            "CONTEXTO PARA IA — significado y acción recomendada por tipo",
            SEP,
            "  total_mismatch : ADVERTENCIA — factura SÍ cargada y válida.",
            "                   XML declara subtotal + impuesto_total ≠ total_comprobante.",
            "                   Causas: redondeo del emisor, descuentos no separados.",
            "                   Acción: clasificar normalmente. Si auditoría lo exige, revisar XML.",
            "",
            "  iva_mismatch   : ADVERTENCIA — factura SÍ cargada.",
            "                   Suma iva_1+iva_2+iva_4+iva_8+iva_13 ≠ impuesto_total del XML.",
            "                   Puede ignorarse salvo auditoría estricta.",
            "",
            "  xml_duplicate  : DOS archivos XML con la misma clave de 50 dígitos.",
            "                   Se conservó el primero encontrado; el duplicado se descartó.",
            "                   Verificar si ambos son idénticos o si hay discrepancia de montos.",
            "",
            "  xml_failed     : XML no parseado — factura NO cargada.",
            "                   El archivo está corrupto o tiene formato inválido.",
            "                   Acción: revisión manual del archivo XML indicado.",
            "",
            "  respuesta_receptor: MensajeHacienda que confirma recepcion de un MensajeReceptor",
            "                   enviado por el propio cliente (confirmacion de aceptacion/rechazo",
            "                   de factura recibida). NO son errores. Solo se reportan para",
            "                   visibilidad. Detectados leyendo el contenido XML: cedula en",
            "                   posicion 9-20 de la clave == NumeroCedulaReceptor del mensaje.",
            "                   Accion: ninguna requerida. Opcional: mover a cuarentena.",
            "",
            "  respuesta_failed: MensajeHacienda/MensajeReceptor con incidencia.",
            "                   Puede ser un _respuesta.xml irrecuperable o una respuesta",
            "                   que NO corresponde a ningún comprobante cargado por clave.",
            "                   Acción: revisar manualmente el archivo indicado y",
            "                   verificar que la clave pertenezca al XML principal correcto.",
            "",
            "  pdf_duplicate  : Dos PDFs apuntaban a la misma clave.",
            "                   Sistema conservó el más pesado (más bytes = más contenido).",
            "",
            SEP,
            "DETALLE COMPLETO",
            SEP,
        ]

        for cat, errs in cats.items():
            if not errs:
                continue
            lines.append("")
            lines.append(f"  ── {label_map[cat].upper()} ({len(errs)}) ──")
            if cat == "respuesta_receptor":
                lines.append(f"  Total: {len(errs)} archivos. Son normales — Hacienda confirma recepcion")
                lines.append("  de MensajeReceptor enviado por el cliente. No requieren accion.")
                lines.append("  Puede moverlos a cuarentena desde el modal de advertencias al cargar.")
            else:
                for i, err in enumerate(errs, 1):
                    lines.append(f"  [{i:03d}] {err}")

        lines += ["", SEP, "FIN DEL REPORTE", SEP]

        try:
            _Path(path).write_text("\n".join(lines), encoding="utf-8")
            self._show_info(
                "Reporte exportado",
                f"Guardado en:\n{path}\n\nPuede enviarlo a Claude para análisis.",
            )
        except Exception as exc:
            self._show_warning("Error al exportar", f"No se pudo guardar el archivo:\n{exc}")

    @staticmethod
    def _round_corners(win):
        """Aplica esquinas redondeadas (Windows 11) a un CTkToplevel via DWM."""
        try:
            import ctypes
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUNDSMALL = 3
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(ctypes.c_int(DWMWCP_ROUNDSMALL)),
                ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            logger.debug("No se pudo aplicar esquinas redondeadas al modal", exc_info=True)

    def _ask(self, title: str, msg: str, confirm_text: str = "Sí, reclasificar") -> bool:
        return ModalOverlay.ask_sync(self, title, msg, confirm_text=confirm_text)


    # ── DETECCIÓN DE DUPLICADOS POR SHA256 ─────────────────────────────────────────
    def _find_duplicate_pdfs(self):
        """Detecta duplicados: PDFs origen vs clasificados + XMLs duplicados en origen."""
        if not self.session or not self.db:
            ModalOverlay.show_info(self, "Limpiar duplicados", "No hay sesión activa")
            return

        # Mostrar overlay de carga
        if not hasattr(self, '_loading_overlay') or not self._loading_overlay:
            self._loading_overlay = LoadingOverlay(self)
            self._loading_overlay.grid(row=0, column=0, rowspan=2, sticky="nsew")
        else:
            self._loading_overlay.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self._loading_overlay.update_status("Escaneando duplicados...")
        self._loading_overlay.set_counter_text("Escaneo profundo sin contador en tiempo real")
        self._loading_overlay.progress_bar.set(0)
        self.update_idletasks()

        def _set_status(msg: str):
            self.after(0, lambda m=msg: (
                self._loading_overlay.update_status(m),
                self._loading_overlay.set_counter_text("Escaneo profundo sin contador en tiempo real"),
                self._loading_overlay.progress_bar.set(0),
                self._loading_overlay.update_idletasks(),
            ) if hasattr(self, '_loading_overlay') and self._loading_overlay else None
            )

        # Detectar duplicados en thread
        def worker():
            from gestor_contable.core.classification_utils import (
                find_duplicates_pdf_origin_vs_classified,
                find_duplicate_xmls_in_origin,
                find_duplicate_pdfs_within_origin,
            )
            try:
                # Tipo 1: PDFs descargados que ya fueron clasificados
                _set_status("Comparando PDFs origen vs clasificados...")
                pdf_redundantes = find_duplicates_pdf_origin_vs_classified(
                    self.session.folder,
                    self._db_records,
                )

                # Tipo 2: XMLs duplicados en la carpeta XML/
                _set_status("Calculando hashes de XMLs en origen...")
                xml_duplicados = find_duplicate_xmls_in_origin(self.session.folder)

                # Tipo 3: PDFs con contenido idéntico en múltiples rutas dentro de PDF/
                _set_status("Calculando hashes de PDFs en origen...")
                pdf_duplicados_origen = find_duplicate_pdfs_within_origin(self.session.folder)

                # Tipo 4: PDFs rechazados por el indexador (misma clave, peor candidato)
                pdf_dup_rejected = dict(self._pdf_duplicates_rejected)

                def _done():
                    if hasattr(self, '_loading_overlay') and self._loading_overlay:
                        self._loading_overlay.grid_remove()
                    self._show_cleanup_modal(pdf_redundantes, xml_duplicados, pdf_duplicados_origen, pdf_dup_rejected)

                self.after(0, _done)
            except Exception as e:
                def _err(error=e):
                    if hasattr(self, '_loading_overlay') and self._loading_overlay:
                        self._loading_overlay.grid_remove()
                    self._show_error("Error al escanear", str(error))
                self.after(0, _err)

        threading.Thread(target=worker, daemon=True).start()

    def _show_cleanup_modal(self, pdf_redundantes: list[dict], xml_duplicados: list[dict], pdf_duplicados_origen: list[dict] | None = None, pdf_dup_rejected: dict | None = None):
        """Muestra modal con duplicados a mover a cuarentena: PDFs redundantes + XMLs + duplicados en origen + rechazados por indexador."""
        pdf_duplicados_origen = pdf_duplicados_origen or []
        pdf_dup_rejected = {k: v for k, v in (pdf_dup_rejected or {}).items() if k.exists()}
        total = len(pdf_redundantes) + len(xml_duplicados) + sum(len(g["a_eliminar"]) for g in pdf_duplicados_origen) + len(pdf_dup_rejected)
        if total == 0:
            self._set_status("Duplicados: sin hallazgos")
            open_history = ModalOverlay.ask_sync(
                self,
                "Limpiar duplicados",
                "No hay duplicados detectados en este escaneo.\n\n"
                "Si necesita recuperar archivos enviados previamente a cuarentena, "
                "puede abrir el historial de lotes restaurables.",
                confirm_text="Ver historial",
                cancel_text="Cerrar",
            )
            if open_history:
                self._show_duplicates_restore_history()
            return

        overlay, modal, close_fn = ModalOverlay.build(self)

        # Header
        header = ctk.CTkFrame(modal, fg_color=SURFACE, corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text=f"Se encontraron {total} archivo(s) redundante(s)",
            font=F_MODAL_SUBTITLE(),
            text_color=TEXT,
        ).pack(side="left", padx=16, pady=12)

        # Body
        body = ctk.CTkFrame(modal, fg_color=BG)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        # Treeview
        tree_frame = ctk.CTkFrame(body, fg_color=CARD, corner_radius=8)
        tree_frame.pack(fill="both", expand=True, pady=(0, 12))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.grid(row=0, column=1, sticky="ns")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=CARD, foreground=TEXT, fieldbackground=CARD)
        style.configure("Treeview.Heading", background=SURFACE, foreground=TEXT)

        tree = ttk.Treeview(
            tree_frame,
            columns=("tipo", "archivo", "accion"),
            height=18,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=tree.yview)
        tree.column("#0", width=0)
        tree.column("tipo", width=200, anchor="w")
        tree.column("archivo", width=500, anchor="w")
        tree.column("accion", width=150, anchor="center")
        tree.heading("tipo", text="Tipo")
        tree.heading("archivo", text="Archivo")
        tree.heading("accion", text="Accion")
        tree.grid(row=0, column=0, sticky="nsew")

        # Lista unificada para cuarentena: [(tipo_str, Path)]
        files_to_quarantine: list[tuple[str, Path]] = []

        # ─ SECCIÓN 1: PDFs redundantes (descargados que ya fueron clasificados)
        if pdf_redundantes:
            parent_pdf = tree.insert("", "end", text="", values=("PDFs en origen (redundantes)", "", ""))
            for pdf_item in pdf_redundantes:
                en_pdf = pdf_item["en_pdf"]
                en_clasificado = pdf_item["en_clasificado"]
                sha256 = pdf_item["sha256"][:8] + "..."
                files_to_quarantine.append(("pdf_redundante", en_pdf))
                tree.insert(parent_pdf, "end", text="", values=(f"SHA256: {sha256}", en_pdf.name[:60], "CUARENTENA"))
                tree.insert(parent_pdf, "end", text="", values=("", f"  Ya clasificado en: {en_clasificado.parent.name}", "Mantener"))

        # ─ SECCIÓN 2: XMLs duplicados (copias innecesarias)
        if xml_duplicados:
            parent_xml = tree.insert("", "end", text="", values=("XMLs duplicados en origen", "", ""))
            for xml_group in xml_duplicados:
                mantener = xml_group["mantener"]
                a_eliminar = xml_group["a_eliminar"]
                sha256 = xml_group["sha256"][:8] + "..."
                tree.insert(parent_xml, "end", text="", values=(f"SHA256: {sha256}", mantener.name[:60], "Mantener"))
                for xml_dup in a_eliminar:
                    files_to_quarantine.append(("xml_duplicado", xml_dup))
                    tree.insert(parent_xml, "end", text="", values=("", xml_dup.name[:60], "CUARENTENA"))

        # ─ SECCIÓN 3: PDFs duplicados físicamente dentro de PDF/
        if pdf_duplicados_origen:
            parent_dup = tree.insert("", "end", text="", values=("PDFs duplicados en PDF/", "", ""))
            for grupo in pdf_duplicados_origen:
                mantener = grupo["mantener"]
                sha256 = grupo["sha256"][:8] + "..."
                tree.insert(parent_dup, "end", text="", values=(f"SHA256: {sha256}", f"{mantener.name[:50]}  ({mantener.parent.name}/)", "Mantener"))
                for dup_path in grupo["a_eliminar"]:
                    files_to_quarantine.append(("pdf_duplicado_origen", dup_path))
                    tree.insert(parent_dup, "end", text="", values=("", f"  {dup_path.name[:50]}  ({dup_path.parent.name}/)", "CUARENTENA"))

        # ─ SECCIÓN 4: PDFs rechazados por indexador (misma clave, peor candidato)
        if pdf_dup_rejected:
            parent_rej = tree.insert("", "end", text="", values=("PDFs descartados por clave duplicada", "", ""))
            for rej_path, winner_path in pdf_dup_rejected.items():
                files_to_quarantine.append(("pdf_rechazado", rej_path))
                rej_size = rej_path.stat().st_size if rej_path.exists() else 0
                tree.insert(parent_rej, "end", text="", values=(f"({rej_size} B)", f"{rej_path.name[:50]}  ({rej_path.parent.name}/)", "CUARENTENA"))
                tree.insert(parent_rej, "end", text="", values=("", f"  Ganador: {winner_path.name[:50]}  ({winner_path.parent.name}/)", "Mantener"))

        # Nota informativa
        ctk.CTkLabel(
            body,
            text="Los archivos se moveran a .metadata/duplicates_quarantine/ y pueden restaurarse por lote.",
            font=F_MODAL_HINT(),
            text_color=MUTED,
        ).pack(anchor="w", pady=(0, 4))

        # Footer con botones
        footer = ctk.CTkFrame(modal, fg_color=BG)
        footer.pack(fill="x", padx=12, pady=12)

        def proceed():
            close_fn()
            if files_to_quarantine:
                self._execute_duplicates_quarantine(files_to_quarantine)
            else:
                ModalOverlay.show_info(self, "Cuarentena", "No hay archivos para mover")

        ctk.CTkButton(
            footer,
            text="Mover a cuarentena",
            fg_color=WARNING,
            hover_color="#d97706",
            text_color=BG,
            font=F_BTN_PRIMARY(),
            command=proceed,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            footer,
            text="Restaurar lote...",
            fg_color=SURFACE,
            hover_color=BORDER,
            text_color=TEXT,
            font=F_MODAL_BODY(),
            command=lambda: (close_fn(), self._show_duplicates_restore_history()),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            footer,
            text="Cancelar",
            fg_color=SURFACE,
            hover_color=BORDER,
            text_color=TEXT,
            command=close_fn,
        ).pack(side="left")

    def _execute_duplicates_quarantine(self, files_to_quarantine: list[tuple[str, Path]]):
        """Mueve archivos redundantes a cuarentena auditada en worker thread."""
        from gestor_contable.config import metadata_dir as _metadata_dir

        mdir = _metadata_dir(self.session.folder)
        quarantine_db = DuplicatesQuarantineDB(mdir)

        def worker():
            try:
                summary = execute_duplicates_quarantine(files_to_quarantine, mdir, quarantine_db)
                self.after(0, lambda: self._on_duplicates_quarantine_done(summary))
            except Exception as exc:
                self.after(0, lambda e=exc: ModalOverlay.show_error(self, "Error en cuarentena", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_duplicates_quarantine_done(self, summary: dict):
        """Callback UI tras completar cuarentena de duplicados."""
        batch_id = summary["batch_id"]
        movidos = summary["movidos"]
        fallidos = summary["fallidos"]

        msg_lines = [
            f"Lote: {batch_id}",
            f"Archivos en cuarentena: {movidos}",
        ]
        if fallidos:
            msg_lines.append(f"Errores: {fallidos}")
        msg_lines.append("")
        msg_lines.append("Puede restaurar este lote desde:")
        msg_lines.append("Duplicados > Restaurar lote...")

        msg = "\n".join(msg_lines)
        if fallidos == 0:
            ModalOverlay.show_success(self, "Cuarentena completada", msg)
        else:
            ModalOverlay.show_warning(self, "Cuarentena con errores", msg)

        self._set_status(f"Cuarentena duplicados: {movidos} archivo(s) movido(s)")

    def _show_duplicates_restore_history(self):
        """Modal de historial de cuarentenas de duplicados — permite restaurar por lote."""
        import tkinter as tk
        from gestor_contable.config import metadata_dir as _metadata_dir

        if not self.session:
            ModalOverlay.show_info(self, "Restaurar", "No hay sesion activa")
            return

        mdir = _metadata_dir(self.session.folder)
        quarantine_db = DuplicatesQuarantineDB(mdir)
        batches = quarantine_db.list_batches()

        overlay, card, close_fn = ModalOverlay.build(self)

        # Header
        hdr = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(
            hdr,
            text="Historial de cuarentena de duplicados",
            font=F_MODAL_TITLE(),
            text_color=TEXT,
        ).pack(side="left", padx=16, pady=12)

        if not batches:
            ctk.CTkLabel(
                card,
                text="No hay lotes en cuarentena para este cliente.",
                font=F_MODAL_BODY(),
                text_color=MUTED,
            ).pack(padx=24, pady=32)
            ctk.CTkButton(card, text="Cerrar", fg_color=SURFACE, border_width=1,
                          border_color=BORDER, text_color=TEXT, command=close_fn).pack(pady=(0, 24))
            return

        # Lista de lotes
        list_frame = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(8, 8))

        sb = tk.Scrollbar(list_frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(
            list_frame,
            wrap="none",
            bg=SURFACE,
            fg=TEXT,
            relief="flat",
            font=("Consolas", 10),
            bd=0,
            padx=10,
            pady=8,
            yscrollcommand=sb.set,
            state="normal",
        )
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)

        for b in batches:
            fecha = b["fecha"][:19].replace("T", " ")
            txt.insert("end", f"Lote: {b['batch_id']}  |  {fecha}  |  {b['total_archivos']} archivo(s)\n")
        txt.configure(state="disabled")

        # Selector de lote
        ctk.CTkLabel(card, text="ID del lote a restaurar:", font=F_MODAL_BODY(),
                     text_color=MUTED).pack(anchor="w", padx=16, pady=(8, 2))

        batch_options = [b["batch_id"] for b in batches]
        batch_var = ctk.StringVar(value=batch_options[0] if batch_options else "")
        ctk.CTkOptionMenu(
            card,
            values=batch_options,
            variable=batch_var,
            fg_color=SURFACE,
            button_color=BORDER,
            button_hover_color=TEAL,
            text_color=TEXT,
            font=F_MODAL_BODY(),
        ).pack(fill="x", padx=16, pady=(0, 8))

        error_lbl = ctk.CTkLabel(card, text="", font=F_MODAL_SUBTEXT(), text_color=DANGER)
        error_lbl.pack(padx=16, pady=(0, 4))

        def _on_restore():
            selected = batch_var.get().strip()
            if not selected:
                error_lbl.configure(text="Seleccione un lote.")
                return
            confirmed = ModalOverlay.ask_sync(
                self,
                "Restaurar lote",
                f"Se restauraran los archivos del lote {selected}\na sus ubicaciones originales.\n\n¿Continuar?",
            )
            if not confirmed:
                return
            close_fn()

            def _worker():
                try:
                    result = restore_duplicates_batch(selected, quarantine_db)
                    self.after(0, lambda r=result: self._on_duplicates_restore_done(r))
                except Exception as exc:
                    self.after(0, lambda e=exc: ModalOverlay.show_error(self, "Error al restaurar", str(e)))

            threading.Thread(target=_worker, daemon=True).start()

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(pady=(0, 24))
        ctk.CTkButton(
            btn_row,
            text="Restaurar lote",
            width=160,
            fg_color=TEAL,
            hover_color=TEAL_DIM,
            text_color=BG,
            font=F_BTN_PRIMARY(),
            command=_on_restore,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row,
            text="Cerrar",
            width=120,
            fg_color=SURFACE,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=F_BTN_SECONDARY(),
            command=close_fn,
        ).pack(side="left")

    def _on_duplicates_restore_done(self, result: dict):
        """Callback UI tras restaurar un lote de cuarentena de duplicados."""
        restaurados = result["restaurados"]
        fallidos = result["fallidos"]
        msg = f"Archivos restaurados: {restaurados}"
        if fallidos:
            msg += f"\nErrores: {fallidos}"
        if fallidos == 0:
            ModalOverlay.show_success(self, "Restauracion completada", msg)
        else:
            ModalOverlay.show_warning(self, "Restauracion con errores", msg)
        self._set_status(f"Restauracion duplicados: {restaurados} archivo(s)")

    # ── PURGA RESPUESTAS RECEPTOR ─────────────────────────────────────────────

    def _purge_receptor_responses(self, receptor_files: list, close_modal_fn):
        """Mueve los MensajeHacienda de respuesta receptor a cuarentena auditada."""
        if not self.session or not receptor_files:
            return

        n = len(receptor_files)
        confirmed = ModalOverlay.ask_sync(
            self,
            "Cuarentena respuestas receptor",
            f"Se moveran {n} archivo{'s' if n != 1 else ''} a cuarentena.\n\n"
            "Estos son confirmaciones de Hacienda a MensajeReceptor enviados\n"
            "por el cliente. No son facturas. Pueden recuperarse desde:\n"
            ".metadata/cuarentena_receptor/\n\n"
            "¿Continuar?",
            confirm_text="Mover a cuarentena",
        )
        if not confirmed:
            return

        close_modal_fn()

        from gestor_contable.core.ors_purge import OrsPurgeDB
        from gestor_contable.core.receptor_purge import execute_receptor_purge

        metadata = self.session.folder / ".metadata"
        metadata.mkdir(parents=True, exist_ok=True)
        purge_db = OrsPurgeDB(metadata, db_filename="receptor_purge.sqlite")
        client_folder = self.session.folder

        def worker():
            result = execute_receptor_purge(receptor_files, client_folder, purge_db)
            self.after(0, lambda r=result: _on_done(r))

        def _on_done(result: dict):
            movidos = result["total_movidos"]
            fallidos = result["total_fallidos"]
            self._receptor_response_files = []
            if fallidos:
                self._show_warning(
                    "Cuarentena completada con errores",
                    f"Movidos: {movidos}  |  Fallidos: {fallidos}\n"
                    f"Lote: {result['batch_id']}\n"
                    "Revise .metadata/cuarentena_receptor/ para detalles.",
                )
            else:
                self._show_info(
                    "Cuarentena completada",
                    f"{movidos} archivo{'s' if movidos != 1 else ''} movido{'s' if movidos != 1 else ''} a cuarentena.\n"
                    f"Lote: {result['batch_id']}\n"
                    "Para restaurar: Ver historial de cuarentenas (panel ORS).",
                )

        threading.Thread(target=worker, daemon=True).start()

    # ── CLASIFICACION MANUAL ORS COMO GASTO ───────────────────────────────────

    def _on_ors_classify_manual(self):
        """Desbloquea el formulario para clasificar el registro ORS seleccionado como GASTOS.

        Solo disponible en la pestaña ORS. El desbloqueo se persiste en BD para
        sobrevivir reinicios. El archivo se moverá al clasificar normalmente.
        """
        if not self.selected or not self.db:
            return
        clave = self.selected.clave or ""
        if not clave:
            self._show_warning("Atención", "Este registro no tiene clave Hacienda y no puede desbloquearse.")
            return
        self.db.set_ors_manual_override(clave, "manual")
        self._db_records.setdefault(clave, {})["ors_manual_override"] = "manual"
        self._btn_ors_classify_manual.configure(state="disabled")
        self._sync_category_for_record(self.selected)
        self._update_path_preview()

    # ── PURGA ORS ─────────────────────────────────────────────────────────────

    def _purge_ors_clicked(self):
        """Abre el dialogo de cedula para iniciar el flujo de purga ORS."""
        if not self.session:
            return
        self._show_ors_cedula_modal()

    def _show_ors_cedula_modal(self):
        """Modal con campo de texto para que el usuario ingrese la cedula de validacion."""
        import tkinter as tk

        overlay, card, close_fn = ModalOverlay.build(self)
        card.configure(corner_radius=16)

        ctk.CTkLabel(
            card,
            text="Purgar ORS — Verificacion de cedula",
            font=F_MODAL_TITLE(),
            text_color=DANGER,
        ).pack(padx=24, pady=(24, 4))

        ctk.CTkLabel(
            card,
            text=(
                "Ingrese la cedula de la empresa AJENA cuyos registros\n"
                "desea quitar de la pestana ORS.\n\n"
                "Solo se moveran a cuarentena los registros ORS donde\n"
                "el emisor O el receptor sea exactamente esa cedula.\n"
                "Ningun registro fuera del ORS es afectado."
            ),
            font=F_MODAL_BODY(),
            text_color=MUTED,
            justify="center",
        ).pack(padx=24, pady=(0, 16))

        cedula_var = ctk.StringVar()
        entry = ctk.CTkEntry(
            card,
            textvariable=cedula_var,
            placeholder_text="Ej: 3101234567",
            fg_color=SURFACE,
            border_color=BORDER,
            text_color=TEXT,
            font=F_BTN_SECONDARY(),
            height=40,
            justify="center",
        )
        entry.pack(padx=40, pady=(0, 8), fill="x")
        entry.focus()

        error_lbl = ctk.CTkLabel(
            card,
            text="",
            font=F_MODAL_SUBTEXT(),
            text_color=DANGER,
        )
        error_lbl.pack(padx=24, pady=(0, 8))

        def _on_confirm():
            import re
            raw = cedula_var.get().strip()
            cedula_clean = re.sub(r"\D", "", raw)
            if len(cedula_clean) < 9:
                error_lbl.configure(text="La cedula debe tener al menos 9 digitos.")
                return
            close_fn()
            self._prepare_ors_purge(cedula_clean)

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(pady=(0, 24))
        ctk.CTkButton(
            btn_row,
            text="Continuar",
            width=130,
            fg_color=DANGER,
            hover_color="#dc2626",
            text_color="#0d1a18",
            font=F_BTN_PRIMARY(),
            command=_on_confirm,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row,
            text="Cancelar",
            width=130,
            fg_color=SURFACE,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=F_BTN_SECONDARY(),
            command=close_fn,
        ).pack(side="left")

        entry.bind("<Return>", lambda _e: _on_confirm())

    def _prepare_ors_purge(self, cedula: str):
        """Calcula candidatos e inventario, luego muestra el preview de purga.

        Opera exclusivamente sobre self.records (registros ya filtrados como ORS
        por la pestana activa). Nunca toca registros de otras categorias.
        """
        candidates = find_ors_candidates(self.records, cedula)

        if not candidates:
            ModalOverlay.show_info(
                self,
                "Sin candidatos",
                f"No hay registros ORS donde el emisor o receptor sea {cedula}.\n"
                "Verifique la cedula ingresada.",
            )
            return

        db_records = self._db_records if self.db else {}
        file_inventory = build_file_inventory(
            self.all_records,
            db_records,
            self._hidden_response_files_by_clave,
        )

        total_xml = sum(len(file_inventory.get(r.clave, {}).get("xml", [])) for r in candidates)
        total_pdf = sum(len(file_inventory.get(r.clave, {}).get("pdf", [])) for r in candidates)
        total_response_xml = sum(
            len(file_inventory.get(r.clave, {}).get("response_xml", []))
            for r in candidates
        )

        self._show_ors_purge_preview(
            cedula,
            candidates,
            file_inventory,
            total_xml,
            total_pdf,
            total_response_xml,
        )

    def _show_ors_purge_preview(
        self,
        cedula: str,
        candidates: list,
        file_inventory: dict,
        total_xml: int,
        total_pdf: int,
        total_response_xml: int,
    ):
        """Modal de previsualizacion antes de confirmar la purga."""
        import tkinter as tk

        overlay, card, close_fn = ModalOverlay.build(self)

        # Header
        hdr = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(
            hdr,
            text="Previsualizacion de purga ORS",
            font=F_MODAL_TITLE(),
            text_color=DANGER,
        ).pack(side="left", padx=16, pady=12)
        ctk.CTkLabel(
            hdr,
            text=f"Cedula: {cedula}",
            font=F_MODAL_BODY(),
            text_color=MUTED,
        ).pack(side="right", padx=16, pady=12)

        # Resumen
        summary = ctk.CTkFrame(card, fg_color=CARD, corner_radius=8)
        summary.pack(fill="x", padx=16, pady=(12, 8))
        for label, value in [
            ("Claves a purgar:", str(len(candidates))),
            ("XMLs afectados:", str(total_xml)),
            ("PDFs afectados:", str(total_pdf)),
            ("Respuestas XML afectadas:", str(total_response_xml)),
        ]:
            row_f = ctk.CTkFrame(summary, fg_color="transparent")
            row_f.pack(fill="x", padx=12, pady=3)
            ctk.CTkLabel(row_f, text=label, font=F_CARD_LABEL(),
                         text_color=MUTED, anchor="w").pack(side="left")
            ctk.CTkLabel(row_f, text=value, font=F_CARD_VALUE(),
                         text_color=TEXT, anchor="e").pack(side="right")

        # Lista de claves
        ctk.CTkLabel(
            card,
            text="Documentos que seran movidos a cuarentena:",
            font=F_MODAL_SUBTEXT(),
            text_color=MUTED,
        ).pack(anchor="w", padx=16, pady=(4, 2))

        list_frame = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        sb = tk.Scrollbar(list_frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(
            list_frame,
            wrap="none",
            bg=SURFACE,
            fg=TEXT,
            relief="flat",
            font=("Consolas", 10),
            bd=0,
            padx=10,
            pady=8,
            yscrollcommand=sb.set,
            state="normal",
        )
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)

        max_rows = 200
        for i, r in enumerate(candidates[:max_rows], 1):
            files = file_inventory.get(r.clave, {})
            n_xml = len(files.get("xml", []))
            n_pdf = len(files.get("pdf", []))
            n_rsp = len(files.get("response_xml", []))
            emisor = (r.emisor_nombre or "desconocido")[:40]
            txt.insert(
                "end",
                f"[{i:03d}] {r.clave}  |  {emisor}  |  {n_xml} XML  {n_pdf} PDF  {n_rsp} RESP\n",
            )
        if len(candidates) > max_rows:
            txt.insert("end", f"\n... y {len(candidates) - max_rows} claves mas\n")
        txt.configure(state="disabled")

        ctk.CTkLabel(
            card,
            text="Los archivos se moveran a .ors_quarantine/ dentro de la carpeta del cliente.",
            font=F_MODAL_HINT(),
            text_color=MUTED,
        ).pack(padx=16, pady=(0, 4))

        # Botones
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(pady=(4, 16))

        def _on_confirm():
            close_fn()
            self._btn_purge_ors.configure(state="disabled", text="Purgando...")
            threading.Thread(
                target=self._execute_ors_purge_worker,
                args=(cedula, candidates, file_inventory),
                daemon=True,
            ).start()

        ctk.CTkButton(
            btn_row,
            text="Confirmar purga",
            width=160,
            fg_color=DANGER,
            hover_color="#dc2626",
            text_color="#0d1a18",
            font=F_BTN_PRIMARY(),
            command=_on_confirm,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row,
            text="Cancelar",
            width=130,
            fg_color=SURFACE,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=F_BTN_SECONDARY(),
            command=close_fn,
        ).pack(side="left")

    def _execute_ors_purge_worker(self, cedula: str, candidates: list, file_inventory: dict):
        """Worker thread: ejecuta la cuarentena y notifica al hilo UI."""
        try:
            from gestor_contable.config import metadata_dir as _metadata_dir
            mdir = _metadata_dir(self.session.folder)
            purge_db = OrsPurgeDB(mdir)
            summary = execute_purge(
                candidates=candidates,
                file_inventory=file_inventory,
                client_folder=self.session.folder,
                cedula=cedula,
                purge_db=purge_db,
            )
            self.after(0, lambda: self._on_ors_purge_done(summary, cedula))
        except Exception as exc:
            self.after(
                0,
                lambda: (
                    self._btn_purge_ors.configure(state="normal", text="Purgar ORS"),
                    ModalOverlay.show_error(self, "Error en purga ORS", str(exc)),
                ),
            )

    def _on_ors_purge_done(self, summary: dict, cedula: str):
        """Callback UI: aplica resultados de la purga al estado de la ventana."""
        self._btn_purge_ors.configure(state="normal", text="Purgar ORS")

        batch_id = summary["batch_id"]
        claves_ok = summary["claves_ok"]
        claves_parcial = summary["claves_parcial"]
        claves_fallidas = summary["claves_fallidas"]
        total_movidos = summary["total_movidos"]
        total_fallidos = summary["total_fallidos"]

        # Remover de all_records las claves purgadas completamente
        claves_purgadas = {
            r["clave"]
            for r in summary["results"]
            if not r["fallidos"]
        }
        if claves_purgadas:
            self.all_records = [r for r in self.all_records if r.clave not in claves_purgadas]
            for clave in claves_purgadas:
                self._hidden_response_files_by_clave.pop(clave, None)
            self.records = self._apply_filters()
            self._refresh_tree()
            self._update_progress()

        msg_lines = [
            f"Lote: {batch_id}",
            f"Claves en cuarentena: {claves_ok}",
        ]
        if claves_parcial:
            msg_lines.append(f"Claves parciales: {claves_parcial}")
        if claves_fallidas:
            msg_lines.append(f"Claves fallidas: {claves_fallidas}")
        msg_lines.append(f"Archivos movidos: {total_movidos}")
        if total_fallidos:
            msg_lines.append(f"Archivos con error: {total_fallidos}")

        msg = "\n".join(msg_lines)

        if claves_fallidas == 0 and total_fallidos == 0:
            ModalOverlay.show_success(self, "Purga ORS completada", msg)
        else:
            ModalOverlay.show_warning(self, "Purga ORS con errores", msg)
        self._set_status(f"Purga ORS: {total_movidos} archivo(s) movido(s)")

    def _show_ors_purge_history(self):
        """Modal de historial de purgas (ORS + respuestas receptor) consultable por lote."""
        if not self.session:
            return

        import tkinter as tk
        from gestor_contable.config import metadata_dir as _metadata_dir

        mdir = _metadata_dir(self.session.folder)

        # Cargar lotes de ambos DBs y mezclarlos con su origen
        all_batches: list[dict] = []
        db_ors = db_receptor = None
        try:
            db_ors = OrsPurgeDB(mdir)
            for b in db_ors.get_batches():
                b["_tipo"] = "ORS"
                b["_db"] = db_ors
                all_batches.append(b)
        except Exception:
            pass
        try:
            db_receptor = OrsPurgeDB(mdir, db_filename="receptor_purge.sqlite")
            for b in db_receptor.get_batches():
                b["_tipo"] = "Receptor"
                b["_db"] = db_receptor
                all_batches.append(b)
        except Exception:
            pass

        # Ordenar por fecha descendente
        all_batches.sort(key=lambda b: b.get("fecha", ""), reverse=True)

        if not all_batches and db_ors is None and db_receptor is None:
            ModalOverlay.show_error(self, "Error", "No se pudo leer el historial.")
            return

        overlay, card, close_fn = ModalOverlay.build(self)

        hdr = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(
            hdr,
            text="Historial de cuarentenas",
            font=F_MODAL_TITLE(),
            text_color=TEXT,
        ).pack(side="left", padx=16, pady=12)
        ctk.CTkButton(
            hdr,
            text="Cerrar",
            width=80,
            fg_color=SURFACE,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=F_MODAL_BODY(),
            command=close_fn,
        ).pack(side="right", padx=16, pady=10)

        if not all_batches:
            ctk.CTkLabel(
                card,
                text="No hay cuarentenas registradas para este cliente.",
                font=F_MODAL_BODY(),
                text_color=MUTED,
            ).pack(expand=True)
            return

        # Panel izquierdo: lista de lotes
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=12)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        batch_list_frame = ctk.CTkScrollableFrame(body, fg_color=SURFACE, corner_radius=8)
        batch_list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        batch_list_frame.grid_columnconfigure(0, weight=1)

        # Panel derecho: detalle del lote seleccionado
        detail_frame = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=8)
        detail_frame.grid(row=0, column=1, sticky="nsew")
        detail_frame.grid_columnconfigure(0, weight=1)
        detail_frame.grid_rowconfigure(2, weight=1)

        detail_header = ctk.CTkLabel(
            detail_frame,
            text="Seleccione un lote para ver el detalle",
            font=F_MODAL_BODY(),
            text_color=MUTED,
        )
        detail_header.grid(row=0, column=0, columnspan=2, padx=12, pady=8, sticky="w")

        # Closure mutable: batch_id y su purge_db activos
        _selected: list = ["", None]  # [batch_id, purge_db]

        def _do_restore():
            bid, active_db = _selected
            if not bid or active_db is None:
                return
            if not ModalOverlay.ask_sync(
                self,
                "Restaurar lote",
                f"Los archivos del lote {bid[:23]} se moveran de vuelta\n"
                "a sus rutas originales. Esta accion sobreescribira\n"
                "cualquier archivo con el mismo nombre en el destino.\n\n"
                "¿Continuar?",
                confirm_text="Restaurar",
            ):
                return
            btn_restore.configure(state="disabled", text="Restaurando...")
            threading.Thread(
                target=self._restore_ors_batch_worker,
                args=(bid, active_db, close_fn),
                daemon=True,
            ).start()

        btn_restore = ctk.CTkButton(
            detail_frame,
            text="Restaurar lote",
            width=150,
            fg_color=WARNING,
            hover_color="#e8a61c",
            text_color="#0d1a18",
            font=F_MODAL_SUBTITLE(),
            state="disabled",
            command=_do_restore,
        )
        btn_restore.grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 6), sticky="w")

        detail_sb = tk.Scrollbar(detail_frame)
        detail_sb.grid(row=2, column=1, sticky="ns")
        detail_txt = tk.Text(
            detail_frame,
            wrap="none",
            bg=SURFACE,
            fg=TEXT,
            relief="flat",
            font=("Consolas", 9),
            bd=0,
            padx=8,
            pady=6,
            yscrollcommand=detail_sb.set,
            state="disabled",
        )
        detail_txt.grid(row=2, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))
        detail_sb.config(command=detail_txt.yview)

        def _load_batch_detail(batch_id: str, batch: dict):
            active_db = batch["_db"]
            _selected[0] = batch_id
            _selected[1] = active_db
            tipo = batch["_tipo"]
            detail_header.configure(
                text=f"[{tipo}]  Lote {batch_id[:23]}  |  {batch['fecha']}",
                text_color=TEXT,
            )
            try:
                archivos = active_db.get_archivos_for_batch(batch_id)
            except Exception as e:
                archivos = []
                logger.warning(f"Error cargando detalle de lote: {e}")

            detail_txt.configure(state="normal")
            detail_txt.delete("1.0", "end")
            can_restore = any(a.get("resultado") == "en_cuarentena" for a in archivos)
            btn_restore.configure(state="normal" if can_restore else "disabled")
            if not archivos:
                detail_txt.insert("end", "Sin registros de archivos para este lote.\n")
            else:
                for a in archivos:
                    resultado = a["resultado"]
                    tipo_arch = a["tipo_archivo"].upper()
                    nombre_orig = Path(a["ruta_original"]).name
                    ruta_q = Path(a["ruta_cuarentena"]).name if a["ruta_cuarentena"] else "—"
                    detalle = f"  [{a['detalle']}]" if a.get("detalle") else ""
                    detail_txt.insert(
                        "end",
                        f"[{resultado:14s}] [{tipo_arch:20s}] {nombre_orig}  ->  {ruta_q}{detalle}\n",
                    )
            detail_txt.configure(state="disabled")

        for i, batch in enumerate(all_batches):
            bid = batch["batch_id"]
            tipo = batch["_tipo"]
            tipo_color = DANGER if tipo == "ORS" else WARNING
            btn_text = f"[{tipo}]  {batch['fecha'][:19]}  ({batch['total_archivos']} archivos)"
            ctk.CTkButton(
                batch_list_frame,
                text=btn_text,
                anchor="w",
                font=F_BTN_LIST(),
                fg_color=CARD if i % 2 == 0 else SURFACE,
                hover_color=BORDER,
                text_color=tipo_color,
                corner_radius=4,
                command=lambda b=bid, bdata=batch: _load_batch_detail(b, bdata),
            ).grid(row=i, column=0, sticky="ew", padx=4, pady=2)

    def _restore_ors_batch_worker(self, batch_id: str, purge_db: OrsPurgeDB, close_fn):
        """Worker thread: restaura archivos de cuarentena a sus rutas originales."""
        try:
            result = restore_batch(batch_id, purge_db)
            self.after(0, lambda: self._on_ors_batch_restored(result, close_fn))
        except Exception as exc:
            self.after(
                0,
                lambda: ModalOverlay.show_error(
                    self, "Error al restaurar", str(exc)
                ),
            )

    def _on_ors_batch_restored(self, result: dict, close_fn):
        """Callback UI: muestra resultado de restauracion y recarga registros."""
        restaurados = len(result["restaurados"])
        fallidos = result["fallidos"]

        close_fn()  # Cerrar historial antes de mostrar resultado

        if fallidos:
            msg = (
                f"{restaurados} archivo(s) restaurados.\n"
                f"{len(fallidos)} error(es):\n"
                + "\n".join(f"  • {e}" for _, e in fallidos[:5])
            )
            ModalOverlay.show_warning(self, "Restauracion con errores", msg)
        else:
            ModalOverlay.show_success(
                self,
                "Restauracion completada",
                f"{restaurados} archivo(s) restaurados a sus rutas originales.\n\n"
                "Recargue el periodo para ver los registros recuperados.",
            )
        self._set_status(f"Restauracion ORS: {restaurados} archivo(s)")

    # ── FIN PURGA ORS ─────────────────────────────────────────────────────────

    def _open_dest_folder(self, event=None):
        """Abre en el Explorador de Windows la carpeta donde está el PDF clasificado."""
        ruta = event if isinstance(event, Path) else None
        if ruta is None and self.selected and self.db:
            db_rec = self._db_records.get(self.selected.clave)
            if db_rec and db_rec.get("ruta_destino"):
                ruta = Path(db_rec["ruta_destino"])
        try:
            if ruta is None:
                return
            ruta = Path(ruta)
            if not ruta.is_absolute() and self.session:
                ruta = self.session.folder.parent.parent / ruta
            if not ruta.exists() and self.session:
                cont_root = self.session.folder.parent.parent / "Contabilidades"
                ruta = heal_classified_path(ruta, cont_root, self.db, self.selected.clave if self.selected else None) or ruta

            folder = ruta if ruta.is_dir() else ruta.parent
            if folder.exists():
                os.startfile(str(folder))
            else:
                self._show_warning("Ruta no disponible", f"No se encontró la carpeta destino:\n{ruta}")
        except Exception as e:
            logger.warning(f"No se pudo abrir carpeta destino: {e}")
            self._show_warning("No se pudo abrir la ruta", f"No se pudo abrir la carpeta destino.\n\n{e}")
