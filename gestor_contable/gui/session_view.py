from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)

import customtkinter as ctk

from gestor_contable.config import client_root, metadata_dir
from gestor_contable.core.client_profiles import load_profiles
from gestor_contable.version import __version__
from gestor_contable.core.session import ClientSession, resolve_client_session
from gestor_contable.core.settings import get_setting

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

# ── FUENTES (lazy -- se crean solo después de que existe la ventana raíz) ──────
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
    return re.sub(r"\D", "", text or "")


def _fmt_cedula(digits: str) -> str:
    """Formatea dígitos de cédula al formato con guiones. Ej: 3101793143 → 3-101-793143"""
    d = _digits(digits)
    if len(d) == 9:   # física: 1-0000-0000
        return f"{d[0]}-{d[1:5]}-{d[5:]}"
    if len(d) == 10:  # jurídica: 3-101-000000
        return f"{d[0]}-{d[1:4]}-{d[4:]}"
    if len(d) == 11:  # DIMEX: 1-00000-00000
        return f"{d[0]}-{d[1:6]}-{d[6:]}"
    return d  # fallback sin formato


def _initials(name: str) -> str:
    words = [w for w in name.split() if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return name[:2].upper() if name else "??"


def _read_client_counts(folder: Path) -> tuple[int, int]:
    """Lee pendientes y clasificadas de un cliente. Seguro para llamar en paralelo."""
    db_path = folder / ".metadata" / "clasificacion.sqlite"
    if not db_path.exists():
        return 0, 0
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM clasificaciones WHERE estado='clasificado'"
            ).fetchone()
            clasificadas = row[0] if row else 0
            row2 = conn.execute(
                "SELECT COUNT(*) FROM clasificaciones WHERE estado != 'clasificado'"
            ).fetchone()
            pendientes = row2[0] if row2 else 0
            return pendientes, clasificadas
    except Exception as exc:
        logger.debug("No se pudo leer BD de %s: %s", folder.name, exc)
        return 0, 0


def _load_saved_clients(year: int) -> list[dict]:
    """
    Lee las carpetas de clientes del disco y sus conteos de clasificacion.
    Retorna lista de dicts con: nombre, cedula, pendientes, clasificadas, year.
    Las consultas SQLite se hacen en paralelo para mejorar rendimiento.
    """
    base = client_root(year)
    if not base.exists():
        return []

    # Cédulas por nombre de carpeta desde perfiles
    profile_ced_by_folder: dict[str, str] = {}
    try:
        profiles = load_profiles()
        for folder_name, profile in profiles.items():
            if folder_name.startswith("__email__:"):
                continue
            if isinstance(profile, dict):
                ced = _digits(str(profile.get("cedula", "")))
                if ced:
                    profile_ced_by_folder[folder_name.strip()] = ced
    except Exception as exc:
        logger.warning("No se pudieron cargar perfiles de clientes: %s", exc)

    folders = [
        f for f in sorted(base.iterdir())
        if f.is_dir() and not f.name.startswith(".")
    ]

    # Leer SQLite en paralelo (I/O bound)
    counts: dict[str, tuple[int, int]] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(folders) or 1)) as pool:
        future_to_folder = {pool.submit(_read_client_counts, f): f for f in folders}
        for future in as_completed(future_to_folder):
            folder = future_to_folder[future]
            counts[folder.name] = future.result()

    clients = []
    for folder in folders:
        pendientes, clasificadas = counts.get(folder.name, (0, 0))
        clients.append({
            "nombre": folder.name,
            "cedula": profile_ced_by_folder.get(folder.name, ""),
            "pendientes": pendientes,
            "clasificadas": clasificadas,
            "year": year,
            "folder": folder,
        })

    return clients


def _save_cedula(cedula: str, folder_name: str) -> None:
    """Persiste únicamente la cédula bajo el nombre de carpeta actual en client_profiles.json."""
    from gestor_contable.config import network_drive

    nd = network_drive()
    profiles_path = nd / "CONFIG" / "client_profiles.json"
    try:
        profiles = load_profiles()
        entry = profiles.get(folder_name)
        if not isinstance(entry, dict):
            entry = {}
        entry["cedula"] = cedula
        profiles[folder_name] = entry
        profiles_path.parent.mkdir(parents=True, exist_ok=True)
        profiles_path.write_text(
            json.dumps(profiles, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Cédula %s guardada para '%s'", cedula, folder_name)
    except Exception as exc:
        logger.warning("No se pudo guardar client_profiles.json: %s", exc)


def _heal_client(
    old_folder_name: str,
    hacienda_name: str,
    cedula: str,
    year: int,
) -> "ClientSession":
    """
    Guarda la cédula en client_profiles.json y, si el nombre de Hacienda difiere
    del nombre de carpeta, realiza automáticamente:
      1. Renombra CLIENTES/{old} → CLIENTES/{hacienda_name}
      2. Renombra Contabilidades/{mes}/{old} → .../{hacienda_name} en todos los meses
      3. Actualiza ruta_origen y ruta_destino en clasificacion.sqlite
      4. Mueve la clave en client_profiles.json al nombre correcto
    Retorna un ClientSession apuntando a la carpeta final.
    """
    from gestor_contable.config import network_drive
    from gestor_contable.core.client_profiles import load_profiles

    nd       = network_drive()
    pf_root  = nd / f"PF-{year}"
    clientes = pf_root / "CLIENTES"

    # ── 1. Renombrar carpeta en CLIENTES si hace falta ────────────────────────
    old_dir = clientes / old_folder_name
    new_dir = clientes / hacienda_name

    if hacienda_name != old_folder_name and old_dir.exists() and not new_dir.exists():
        old_dir.rename(new_dir)
        logger.info("Carpeta CLIENTES renombrada: %s → %s", old_folder_name, hacienda_name)

    final_dir = new_dir if new_dir.exists() else (old_dir if old_dir.exists() else clientes / hacienda_name)

    # ── 2. Renombrar en Contabilidades (todos los meses) ─────────────────────
    if hacienda_name != old_folder_name:
        contab = pf_root / "Contabilidades"
        if contab.exists():
            try:
                for mes_dir in contab.iterdir():
                    if not mes_dir.is_dir():
                        continue
                    old_c = mes_dir / old_folder_name
                    new_c = mes_dir / hacienda_name
                    if old_c.exists() and not new_c.exists():
                        old_c.rename(new_c)
                        logger.info("Renombrado en %s: %s → %s", mes_dir.name, old_folder_name, hacienda_name)
            except OSError as exc:
                logger.warning("Error renombrando en Contabilidades: %s", exc)

    # ── 3. Actualizar rutas en clasificacion.sqlite ───────────────────────────
    if hacienda_name != old_folder_name:
        sqlite_path = final_dir / ".metadata" / "clasificacion.sqlite"
        if sqlite_path.exists():
            try:
                with sqlite3.connect(str(sqlite_path)) as conn:
                    conn.execute(
                        "UPDATE clasificaciones SET ruta_destino = REPLACE(ruta_destino, ?, ?)",
                        (old_folder_name, hacienda_name),
                    )
                    conn.execute(
                        "UPDATE clasificaciones SET ruta_origen = REPLACE(ruta_origen, ?, ?)",
                        (old_folder_name, hacienda_name),
                    )
                logger.info("SQLite actualizado: rutas con nombre nuevo")
            except Exception as exc:
                logger.warning("No se pudo actualizar SQLite: %s", exc)

    # ── 4. Guardar cédula en client_profiles.json ─────────────────────────────
    profiles_path = nd / "CONFIG" / "client_profiles.json"
    try:
        profiles = load_profiles()

        # Mover clave vieja al nombre correcto de Hacienda
        if hacienda_name != old_folder_name and old_folder_name in profiles:
            existing = profiles.pop(old_folder_name)
            if not isinstance(existing, dict):
                existing = {}
            existing["cedula"] = cedula
            profiles[hacienda_name] = existing
        else:
            entry = profiles.get(hacienda_name)
            if not isinstance(entry, dict):
                entry = {}
            entry["cedula"] = cedula
            profiles[hacienda_name] = entry

        profiles_path.parent.mkdir(parents=True, exist_ok=True)
        profiles_path.write_text(
            json.dumps(profiles, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Cédula %s guardada para '%s'", cedula, hacienda_name)
    except Exception as exc:
        logger.warning("No se pudo guardar client_profiles.json: %s", exc)

    return ClientSession(cedula=cedula, nombre=hacienda_name, folder=final_dir, year=year)


# ── TARJETA DE CLIENTE ─────────────────────────────────────────────────────────
class ClientCard(ctk.CTkFrame):
    def __init__(self, parent, client: dict, on_click, **kwargs):
        super().__init__(
            parent,
            fg_color=BORDER,
            corner_radius=14,
            **kwargs,
        )
        self._client = client
        self._on_click = on_click

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
        nombre_truncado = client["nombre"][:42] + "..." if len(client["nombre"]) > 42 else client["nombre"]
        ctk.CTkLabel(self._inner, text=nombre_truncado, font=F_NAME(),
                     text_color=TEXT, anchor="w").grid(
            row=0, column=1, sticky="sw", pady=(12, 1))

        # Pills de estado
        pills_frame = ctk.CTkFrame(self._inner, fg_color="transparent")
        pills_frame.grid(row=1, column=1, sticky="nw", pady=(0, 12))

        if client["pendientes"] > 0:
            pill_color = "#2d2010"
            pill_text_color = WARNING
            pill_text = f"{client['pendientes']} pendientes"
        else:
            pill_color = "#0d2a1e"
            pill_text_color = SUCCESS
            pill_text = "Al dia"

        ctk.CTkLabel(pills_frame, text=pill_text, font=F_SMALL(),
                     fg_color=pill_color, text_color=pill_text_color,
                     corner_radius=20, padx=8, pady=2).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(pills_frame, text=f"PF-{client['year']}", font=F_SMALL(),
                     fg_color=SURFACE, text_color=MUTED,
                     corner_radius=20, padx=8, pady=2).pack(side="left", padx=(0, 6))

        cedula_fmt = _fmt_cedula(client.get("cedula", ""))
        if cedula_fmt:
            ctk.CTkLabel(pills_frame, text=cedula_fmt, font=F_SMALL(),
                         fg_color=SURFACE, text_color=MUTED,
                         corner_radius=20, padx=8, pady=2).pack(side="left")

        # Flecha
        self._arrow = ctk.CTkLabel(self._inner, text="->", font=F_HEADING(),
                                    text_color=MUTED)
        self._arrow.grid(row=0, column=2, rowspan=2, padx=(0, 14))

        # Hover
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


# ── DIÁLOGO: CÉDULA REQUERIDA ──────────────────────────────────────────────────
class _CedulaDialog(ctk.CTkToplevel):
    """
    Modal que aparece cuando se hace clic en un cliente sin cédula registrada.

    Flujo:
      1. Usuario ingresa cédula
      2. Se consulta caché local / API de Hacienda
      3. Si nombre Hacienda == carpeta → guarda cédula y abre sesión
      4. Si nombre Hacienda != carpeta → avisa, renombra carpetas, actualiza SQLite,
         guarda cédula y abre sesión (todo automático detrás del botón)
    """

    def __init__(self, parent, client: dict, on_resolved, **kwargs):
        super().__init__(parent, **kwargs)
        self._client      = client
        self._on_resolved = on_resolved
        self._debounce_id: str | None = None
        self._verified_cedula:       str | None = None
        self._verified_hacienda_name: str | None = None

        self.title("Cédula requerida")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.grab_set()  # bloquear ventana padre

        self._build()
        self.after(100, lambda: self._cedula_entry.focus_set())

        # Centrar sobre la ventana padre
        self.update_idletasks()
        x = parent.winfo_rootx() + parent.winfo_width()  // 2 - self.winfo_width()  // 2
        y = parent.winfo_rooty() + parent.winfo_height() // 2 - self.winfo_height() // 2
        self.geometry(f"+{x}+{y}")

    # ── Construcción ───────────────────────────────────────────────────────────
    def _build(self):
        outer = ctk.CTkFrame(self, fg_color=BORDER, corner_radius=20)
        outer.pack(fill="both", expand=True, padx=2, pady=2)

        frame = ctk.CTkFrame(outer, fg_color=CARD, corner_radius=18)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        frame.grid_columnconfigure(0, weight=1)

        # Título
        ctk.CTkLabel(
            frame, text="Cédula requerida",
            font=F_HEADING(), text_color=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=28, pady=(24, 2))
        ctk.CTkLabel(
            frame, text="Esta carpeta no tiene cédula registrada.",
            font=F_SMALL(), text_color=MUTED,
        ).grid(row=1, column=0, sticky="w", padx=28)

        # Nombre de carpeta actual
        box = ctk.CTkFrame(frame, fg_color=SURFACE, corner_radius=10)
        box.grid(row=2, column=0, sticky="ew", padx=28, pady=(14, 0))
        ctk.CTkLabel(box, text="CARPETA ACTUAL",
                     font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
                     text_color=MUTED).pack(anchor="w", padx=14, pady=(10, 0))
        nombre = self._client["nombre"]
        truncado = nombre[:55] + "..." if len(nombre) > 55 else nombre
        ctk.CTkLabel(box, text=truncado, font=F_LABEL(), text_color=TEXT,
                     anchor="w", wraplength=400, justify="left").pack(
            anchor="w", padx=14, pady=(2, 10))

        # Campo cédula
        ctk.CTkLabel(frame, text="Cédula jurídica o física",
                     font=F_SMALL(), text_color=MUTED).grid(
            row=3, column=0, sticky="w", padx=28, pady=(20, 4))
        self._cedula_entry = ctk.CTkEntry(
            frame,
            placeholder_text="Ej: 3-101-085674",
            fg_color=SURFACE, border_color=BORDER,
            text_color=TEXT, placeholder_text_color="#3a4055",
            font=F_LABEL(), height=44, corner_radius=12,
        )
        self._cedula_entry.grid(row=4, column=0, sticky="ew", padx=28)
        self._cedula_entry.bind("<KeyRelease>", self._on_cedula_change)
        self._cedula_entry.bind("<Return>",     self._on_enter_key)

        # Barra de estado
        self._status_frame = ctk.CTkFrame(frame, fg_color="#1a1e2a", corner_radius=10, height=44)
        self._status_frame.grid(row=5, column=0, sticky="ew", padx=28, pady=(10, 0))
        self._status_frame.grid_columnconfigure(1, weight=1)
        self._status_frame.grid_propagate(False)

        self._status_dot = ctk.CTkLabel(
            self._status_frame, text="●", font=F_SMALL(), text_color=MUTED, width=20)
        self._status_dot.grid(row=0, column=0, padx=(14, 6), pady=10)

        self._status_label = ctk.CTkLabel(
            self._status_frame, text="Ingresa la cédula para verificar",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=MUTED, anchor="w")
        self._status_label.grid(row=0, column=1, sticky="ew", pady=10)

        self._status_badge = ctk.CTkLabel(
            self._status_frame, text="", font=F_SMALL(), text_color=MUTED)
        self._status_badge.grid(row=0, column=2, padx=(0, 14), pady=10)

        # Botón confirmar
        self._btn = ctk.CTkButton(
            frame, text="Verificar y continuar  ->",
            font=F_BTN(), fg_color=TEAL, hover_color=TEAL_DIM,
            text_color="#0d1a18", corner_radius=12, height=46,
            state="disabled", command=self._on_confirm,
        )
        self._btn.grid(row=6, column=0, sticky="ew", padx=28, pady=(14, 24))

    # ── Lógica ─────────────────────────────────────────────────────────────────
    def _on_enter_key(self, _e=None):
        if self._verified_hacienda_name:
            self._on_confirm()

    def _on_cedula_change(self, _e=None):
        if self._debounce_id:
            self.after_cancel(self._debounce_id)
        raw = re.sub(r"\D", "", self._cedula_entry.get() or "")
        if len(raw) < 9:
            self._set_idle()
            self._btn.configure(state="disabled")
            self._verified_cedula        = None
            self._verified_hacienda_name = None
            return
        self._set_searching()
        self._debounce_id = self.after(500, self._do_verify)

    def _do_verify(self):
        cedula = re.sub(r"\D", "", self._cedula_entry.get() or "")

        def worker():
            try:
                from gestor_contable.core.xml_manager import CRXMLManager
                nombre = CRXMLManager().resolve_party_name(cedula, "")
                if not nombre:
                    raise ValueError(
                        f"No se encontró contribuyente con cédula {cedula} "
                        "en caché local ni en API de Hacienda."
                    )
                self.after(0, lambda n=nombre: self._on_verify_ok(cedula, n))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_verify_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_verify_ok(self, cedula: str, hacienda_name: str):
        self._verified_cedula        = cedula
        self._verified_hacienda_name = hacienda_name
        folder_name = self._client["nombre"]

        if hacienda_name == folder_name:
            self._set_match(hacienda_name)
        else:
            self._set_rename(folder_name, hacienda_name)

        self._btn.configure(state="normal")

    def _on_verify_error(self, msg: str):
        self._verified_cedula        = None
        self._verified_hacienda_name = None
        short = msg[:65] + "..." if len(msg) > 65 else msg
        self._set_error(short)
        self._btn.configure(state="disabled", text="Verificar y continuar  ->")

    def _on_confirm(self):
        if not self._verified_hacienda_name:
            return
        cedula       = self._verified_cedula
        hacienda_name = self._verified_hacienda_name
        folder_name  = self._client["nombre"]
        year         = self._client["year"]

        self._btn.configure(state="disabled", text="Procesando...")
        self._cedula_entry.configure(state="disabled")

        def worker():
            try:
                from gestor_contable.config import network_drive
                nd = network_drive()
                clientes = nd / f"PF-{year}" / "CLIENTES"
                folder_path = clientes / folder_name

                _save_cedula(cedula, folder_name)

                session = ClientSession(
                    cedula=cedula,
                    nombre=folder_name,
                    folder=folder_path,
                    year=year,
                )
                self.after(0, lambda s=session: self._finish(s))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_verify_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, session: ClientSession):
        self.destroy()
        self._on_resolved(session)

    # ── Estados visuales ───────────────────────────────────────────────────────
    def _set_idle(self):
        self._status_frame.configure(fg_color="#1a1e2a")
        self._status_dot.configure(text_color=MUTED)
        self._status_label.configure(text="Ingresa la cédula para verificar", text_color=MUTED)  # noqa: RUF001
        self._status_badge.configure(text="")

    def _set_searching(self):
        self._status_frame.configure(fg_color="#1a1e2a")
        self._status_dot.configure(text_color=TEAL)
        self._status_label.configure(text="Consultando Hacienda...", text_color=MUTED)
        self._status_badge.configure(text="")

    def _set_match(self, nombre: str):
        truncado = nombre[:42] + "..." if len(nombre) > 42 else nombre
        self._status_frame.configure(fg_color="#0d2a1e")
        self._status_dot.configure(text_color=SUCCESS)
        self._status_label.configure(text=truncado, text_color=SUCCESS)
        self._status_badge.configure(text="✓ coincide", text_color=SUCCESS)

    def _set_rename(self, old: str, new: str):
        truncado = new[:36] + "..." if len(new) > 36 else new
        self._status_frame.configure(fg_color="#2d2010")
        self._status_dot.configure(text_color=WARNING)
        self._status_label.configure(text=f"Hacienda: {truncado}", text_color=WARNING)
        self._status_badge.configure(text="nombre distinto en Hacienda", text_color=WARNING)

    def _set_error(self, msg: str):
        self._status_frame.configure(fg_color="#2a0d0d")
        self._status_dot.configure(text_color=DANGER)
        self._status_label.configure(text=msg, text_color=DANGER)
        self._status_badge.configure(text="✗", text_color=DANGER)


def _create_client_folder(session: "ClientSession") -> None:
    """Crea carpeta del cliente nuevo y guarda su cédula en client_profiles.json."""
    from gestor_contable.config import network_drive

    folder = session.folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".metadata").mkdir(exist_ok=True)
    (folder / "XML").mkdir(exist_ok=True)
    (folder / "PDF").mkdir(exist_ok=True)

    nd = network_drive()
    profiles_path = nd / "CONFIG" / "client_profiles.json"
    try:
        profiles = load_profiles()
    except Exception:
        profiles = {}
    entry = profiles.get(session.nombre)
    if not isinstance(entry, dict):
        entry = {}
    entry["cedula"] = session.cedula
    profiles[session.nombre] = entry
    profiles_path.parent.mkdir(parents=True, exist_ok=True)
    profiles_path.write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Cliente nuevo creado: %s (%s)", session.nombre, session.cedula)


# ── VISTA DE SESIÓN ────────────────────────────────────────────────────────────
class SessionView(ctk.CTkFrame):
    """
    Panel de inicio de sesión embebido.
    Llama a on_session_resolved(session: ClientSession) cuando el usuario confirma.
    Opcionalmente, llama a on_cancel() si el usuario cancela (para cambiar cliente).
    """

    def __init__(self, parent, on_session_resolved, on_cancel=None, **kwargs):
        super().__init__(parent, fg_color=BG, **kwargs)
        self._on_resolved = on_session_resolved
        self._on_cancel = on_cancel
        self._debounce_id: str | None = None
        self._pending_session: ClientSession | None = None
        self._all_clients: list[dict] = []
        self._pending_new_client: bool = False

        self._build()
        self._load_clients_async()

    # ── CONSTRUCCIÓN ──────────────────────────────────────────────────────────
    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)  # divisor
        self.grid_columnconfigure(2, weight=1)

        self._build_header()
        self._build_left()
        self._build_divider()
        self._build_right()

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=56)
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

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
            header, text=f"v{__version__}",
            font=F_SMALL(), text_color=MUTED,
            fg_color=CARD, corner_radius=20,
        ).grid(row=0, column=2, padx=16, pady=12, ipadx=10, ipady=3)

    def _build_left(self):
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(60, 40), pady=50)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(left, text="Iniciar sesión",
                      font=F_TITLE(), text_color=TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkLabel(left, text="Ingresa la cédula del cliente para cargar\nsu carpeta de documentos",
                      font=F_LABEL(), text_color=MUTED, justify="left").grid(
            row=1, column=0, sticky="w", pady=(6, 28))

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
        self._cedula_entry.bind("<Return>", self._on_enter_key)

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

        self._btn_continuar = ctk.CTkButton(
            card,
            text="Continuar  ->",
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

        # Auto-foco al abrir
        self.after(100, lambda: self._cedula_entry.focus_set())

    def _build_divider(self):
        div = ctk.CTkFrame(self, fg_color=BORDER, width=1, corner_radius=0)
        div.grid(row=1, column=1, sticky="ns", pady=40)

    def _build_right(self):
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=2, sticky="nsew", padx=(40, 60), pady=50)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)

        # Header
        header_row = ctk.CTkFrame(right, fg_color="transparent")
        header_row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header_row.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(header_row, fg_color="transparent")
        title_row.pack(anchor="w")

        ctk.CTkLabel(title_row, text="⚡", font=F_HEADING(),
                      text_color=TEAL).pack(side="left")
        ctk.CTkLabel(title_row, text=" Accesos rapidos",
                      font=F_HEADING(), text_color=TEXT).pack(side="left")

        self._count_badge = ctk.CTkLabel(
            title_row, text="0",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color=TEAL, text_color="#0d1a18",
            corner_radius=20, width=28, height=20,
        )
        self._count_badge.pack(side="left", padx=(8, 0))

        ctk.CTkLabel(header_row,
                      text="Clientes con carpeta activa en este equipo",  # noqa: RUF001
                      font=F_SMALL(), text_color=MUTED).pack(anchor="w", padx=(22, 0))

        # Buscador de clientes
        self._search_entry = ctk.CTkEntry(
            right,
            placeholder_text="Filtrar clientes...",
            fg_color=SURFACE,
            border_color=BORDER,
            text_color=TEXT,
            placeholder_text_color="#3a4055",
            font=F_SMALL(),
            height=36,
            corner_radius=10,
        )
        self._search_entry.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self._search_entry.bind("<KeyRelease>", self._on_search_change)

        # Lista scrollable
        self._client_scroll = ctk.CTkScrollableFrame(
            right, fg_color="transparent",
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=MUTED,
        )
        self._client_scroll.grid(row=2, column=0, sticky="nsew")
        self._client_scroll.grid_columnconfigure(0, weight=1)

        self._loading_label = ctk.CTkLabel(
            self._client_scroll,
            text="Cargando clientes...",
            font=F_LABEL(), text_color=MUTED,
        )
        self._loading_label.grid(row=0, column=0, pady=40)

    # ── CARGA ASÍNCRONA DE CLIENTES ───────────────────────────────────────────
    def _load_clients_async(self):
        def worker():
            try:
                year = int(get_setting("fiscal_year"))
                clients = _load_saved_clients(year)
            except Exception:
                clients = []
            self.after(0, lambda: self._on_clients_loaded(clients))

        threading.Thread(target=worker, daemon=True).start()

    def _on_clients_loaded(self, clients: list[dict]):
        self._all_clients = clients
        self._render_clients(clients)

    def _render_clients(self, clients: list[dict]):
        for w in self._client_scroll.winfo_children():
            w.destroy()

        self._count_badge.configure(text=str(len(self._all_clients)))

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

            if self._all_clients:
                msg = "Sin resultados para la busqueda."
            else:
                msg = "No hay clientes registrados\nen este equipo todavia."

            ctk.CTkLabel(empty, text=msg,
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

    # ── FILTRO DE BÚSQUEDA ────────────────────────────────────────────────────
    def _on_search_change(self, _event=None):
        query = self._search_entry.get().strip().lower()
        if not query:
            self._render_clients(self._all_clients)
        else:
            filtered = [
                c for c in self._all_clients
                if query in c["nombre"].lower() or query in c["cedula"]
            ]
            self._render_clients(filtered)

    # ── LÓGICA DE BÚSQUEDA POR CÉDULA ─────────────────────────────────────────
    def _on_cedula_change(self, _event=None):
        if self._debounce_id:
            self.after_cancel(self._debounce_id)
        raw = _digits(self._cedula_entry.get())
        if len(raw) < 9:
            self._set_preview_idle()
            self._btn_continuar.configure(state="disabled", text="Continuar  ->")
            self._pending_session = None
            self._pending_new_client = False
            return
        self._set_preview_searching()
        self._debounce_id = self.after(500, self._resolve_cedula)

    def _on_enter_key(self, _event=None):
        """Enter en el campo de cédula: confirmar si ya hay sesión resuelta."""
        if self._pending_session:
            self._on_continuar()

    def _resolve_cedula(self):
        cedula = self._cedula_entry.get().strip()

        def worker():
            try:
                session = resolve_client_session(cedula)
                self.after(0, lambda s=session: self._on_resolve_ok(s, new_client=False))
            except FileNotFoundError:
                # Cédula válida pero sin carpeta → ofrecer crear
                try:
                    session = resolve_client_session(cedula, allow_missing=True)
                    self.after(0, lambda s=session: self._on_resolve_ok(s, new_client=True))
                except Exception as exc2:
                    self.after(0, lambda e=exc2: self._on_resolve_error(str(e)))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_resolve_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_resolve_ok(self, session: ClientSession, new_client: bool = False):
        self._pending_session = session
        self._pending_new_client = new_client
        if new_client:
            self._set_preview_new_client(session.nombre)
            self._btn_continuar.configure(state="normal", text="Crear nuevo cliente  ->")
        else:
            self._set_preview_found(session.nombre)
            self._btn_continuar.configure(state="normal", text="Continuar  ->")

    def _on_resolve_error(self, msg: str):
        self._pending_session = None
        self._pending_new_client = False
        self._set_preview_error(msg)
        self._btn_continuar.configure(state="disabled", text="Continuar  ->")

    def _on_client_card_click(self, client: dict):
        """Clic en acceso rápido — resuelve sesión y entra directamente."""
        folder: Path = client["folder"]
        year: int = client["year"]
        nombre: str = client["nombre"]
        cedula: str = _digits(str(client.get("cedula", "")))

        # Sin cédula registrada → pedir al usuario antes de abrir sesión
        if len(cedula) < 9:
            _CedulaDialog(self, client=client, on_resolved=self._on_resolved)
            return

        self._cedula_entry.delete(0, "end")
        self._set_preview_searching()
        self._btn_continuar.configure(state="disabled")

        def worker():
            try:
                try:
                    session = resolve_client_session(cedula, year=year)
                except Exception:
                    session = ClientSession(
                        cedula=cedula, nombre=nombre,
                        folder=folder, year=year,
                    )
                # Login directo: sin paso extra de "Continuar"
                self.after(0, lambda s=session: self._on_resolved(s))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_resolve_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_continuar(self):
        if not self._pending_session:
            return
        if self._pending_new_client:
            self._btn_continuar.configure(state="disabled", text="Creando...")
            session = self._pending_session

            def worker():
                try:
                    _create_client_folder(session)
                    self.after(0, lambda s=session: self._on_resolved(s))
                except Exception as exc:
                    self.after(0, lambda e=exc: self._on_resolve_error(str(e)))

            threading.Thread(target=worker, daemon=True).start()
        else:
            self._on_resolved(self._pending_session)

    # ── ESTADOS DEL PREVIEW ───────────────────────────────────────────────────
    def _set_preview_idle(self):
        self._preview_frame.configure(fg_color="#1a1e2a")
        self._preview_dot.configure(text_color=MUTED)
        self._preview_name.configure(
            text="Ingresa una cedula para buscar", text_color=MUTED)
        self._preview_status.configure(text="")

    def _set_preview_searching(self):
        self._preview_frame.configure(fg_color="#1a1e2a")
        self._preview_dot.configure(text_color=TEAL)
        self._preview_name.configure(text="Buscando...", text_color=MUTED)
        self._preview_status.configure(text="")

    def _set_preview_new_client(self, nombre: str):
        truncado = nombre[:45] + "..." if len(nombre) > 45 else nombre
        self._preview_frame.configure(fg_color="#2a1e0d")
        self._preview_dot.configure(text_color=WARNING)
        self._preview_name.configure(text=truncado, text_color=TEXT)
        self._preview_status.configure(text="Nuevo", text_color=WARNING)

    def _set_preview_found(self, nombre: str):
        truncado = nombre[:45] + "..." if len(nombre) > 45 else nombre
        self._preview_frame.configure(fg_color="#0d2a1e")
        self._preview_dot.configure(text_color=TEAL)
        self._preview_name.configure(text=truncado, text_color=TEAL)
        self._preview_status.configure(text="✓", text_color=SUCCESS)

    def _set_preview_error(self, msg: str):
        short = msg[:60] + "..." if len(msg) > 60 else msg
        self._preview_frame.configure(fg_color="#2a0d0d")
        self._preview_dot.configure(text_color=DANGER)
        self._preview_name.configure(text=short, text_color=DANGER)
        self._preview_status.configure(text="✗", text_color=DANGER)
