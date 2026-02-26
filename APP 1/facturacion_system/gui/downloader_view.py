import datetime
import re
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

# --- IMPORTS DEL CORE ---
from facturacion_system.core.gmail_downloader import (
    delete_duplicates,
    find_duplicates,
    open_folder,
    plan_tasks,
    run_download,
)
from facturacion_system.core.gmail_utils import (
    authenticate_gmail,
    current_account,
    list_accounts,
)
from facturacion_system.core.imap_backend import ImapDownloader
from facturacion_system.core.pdf_classifier import clasificar_por_hacienda
from facturacion_system.config import CLIENTS_DIR, SIN_CLASIFICAR_DIR
from facturacion_system.core.client_profiles import (
    get_profile,
    resolve_client_folder,
    save_email_link,
    save_profile,
)
from facturacion_system.core.security import (
    get_imap_credential,
    initialize_vault,
    is_vault_unlocked,
    list_imap_emails,
    migrate_from_keyring_if_needed,
    save_imap_credential,
    unlock_vault,
    vault_exists,
    _validate_passphrase_strength,
)
from facturacion_system.core.settings import get_settings, save_settings
from facturacion_system.gui.widgets import InlineCalendar, MultiSelectDropdown

COLOR_CARD = ("#FFFFFF", "#2B2B2B")
FONT_HEADER = ("Segoe UI", 16, "bold")


class SmartDownloaderView(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")

        self.client_folder: Path | None = None
        self.tasks = []
        self.imap_ids = []
        self.imap_folder = "INBOX"  # Variable nueva para recordar la carpeta
        self._planning = False
        self._imap_loaded_password = None
        self._plan_stop = threading.Event()
        self._plan_pause = threading.Event()
        self._organize_stop = threading.Event()

        self.grid_columnconfigure(0, weight=3, uniform="cols")
        self.grid_columnconfigure(1, weight=2, uniform="cols")
        self.grid_rowconfigure(0, weight=1)

        self.left_col = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        # --- CARDS DE LA INTERFAZ ---
        self._card_account(self.left_col)
        self._card_scope(self.left_col)
        self._card_filters(self.left_col)
        self._card_settings(self.left_col)

        # --- INTEGRACIÓN: SECCIÓN DE PRUEBA ---
        self._card_test_tools(self.left_col)

        self._action_buttons(self.left_col)

        self.right_col = ctk.CTkFrame(self, fg_color="transparent")
        self.right_col.grid(row=0, column=1, sticky="nsew")
        self._setup_status_panel(self.right_col)

        self._cal_target = None

        self._refresh_accounts_ui()
        self._refresh_imap_ui()
        self._load_settings_ui()

    def _card_test_tools(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        card.pack(fill="x", pady=(0, 15))

        ctk.CTkLabel(
            card,
            text="📂 ORGANIZAR PDFs POR MES",
            font=FONT_HEADER,
        ).pack(anchor="w", padx=15, pady=(15, 5))

        ctk.CTkLabel(
            card,
            text="Clasifica los PDFs del cliente por mes y razón social de Hacienda\n"
            "→ PF-2026\\[MES]\\[CLIENTE]\\COMPRAS\\[RAZON SOCIAL]\\",
            font=("Segoe UI", 11),
            text_color="gray",
            justify="left",
        ).pack(anchor="w", padx=15)

        self.btn_prueba_pdf = ctk.CTkButton(
            card,
            text="⚡ Organizar PDFs en carpetas de mes",
            command=self.ejecutar_prueba_pdf,
            fg_color="#E65100",
            hover_color="#BF360C",
        )
        self.btn_prueba_pdf.pack(fill="x", padx=15, pady=(10, 5))

        self.progress_prueba = ctk.CTkProgressBar(card, height=10)
        self.progress_prueba.pack(fill="x", padx=15, pady=(0, 5))
        self.progress_prueba.set(0)

        self.lbl_resultado_prueba = ctk.CTkLabel(
            card,
            text="",
            font=("Consolas", 11),
            wraplength=400,
            justify="left",
        )
        self.lbl_resultado_prueba.pack(anchor="w", padx=15, pady=(0, 15))

    def ejecutar_prueba_pdf(self):
        if not self.client_folder or not self.client_folder.exists():
            messagebox.showwarning("Cliente requerido", "Selecciona primero la carpeta de un cliente.")
            return

        carpeta_pdf_origen = self.client_folder / "PDF"
        if not carpeta_pdf_origen.exists() or not any(carpeta_pdf_origen.rglob("*.pdf")):
            messagebox.showinfo(
                "Sin PDFs",
                f"No se encontraron PDFs en:\n{carpeta_pdf_origen}\n\nDescarga primero los adjuntos.",
            )
            return

        total_pdfs = sum(1 for _ in carpeta_pdf_origen.rglob("*.pdf"))
        if not messagebox.askyesno(
            "Confirmar organización",
            f"Se encontraron {total_pdfs} PDF(s) en {self.client_folder.name}\\PDF\\.\n\n¿Organizarlos por mes y razón social?",
        ):
            return

        self._organize_stop.clear()
        self._organize_last_logged = 0
        self.btn_prueba_pdf.configure(state="disabled", text="⏳ Consultando Hacienda...")
        self.progress_prueba.set(0)
        self.lbl_resultado_prueba.configure(text="Consultando API de Hacienda...")
        threading.Thread(target=self._organizar_pdfs, daemon=True).start()

    def _procesar_pdf_uno(self, done: int, total: int, filename: str):
        self.progress_prueba.set(done / max(1, total))
        self.lbl_resultado_prueba.configure(text=f"Procesando {done}/{total}: {filename}")
        if done == total or done - getattr(self, "_organize_last_logged", 0) >= 200:
            self.log(f"📄 Organización en progreso: {done}/{total}")
            self._organize_last_logged = done

    def _organizar_pdfs(self):
        from facturacion_system.config import PF_DIR

        try:
            if not self.client_folder:
                return
            carpeta_pdf_origen = self.client_folder / "PDF"
            self.log(f"🚀 Iniciando organización de PDFs en: {carpeta_pdf_origen}")
            stats = clasificar_por_hacienda(
                str(carpeta_pdf_origen),
                str(PF_DIR),
                progress_cb=self._procesar_pdf_uno,
                stop_event=self._organize_stop,
                move_files=True,
                return_details=True,
            )
            movidos = stats.get("classified", 0)
            sin_clasificar = stats.get("unclassified", 0)
            errores = stats.get("errors", 0)

            resultado = (
                f"✅ Clasificados  : {movidos}\n"
                f"⚠️  Sin clave     : {sin_clasificar}\n"
                f"❌ Errores       : {errores}"
            )
            self.lbl_resultado_prueba.configure(text=resultado)
            self.progress_prueba.set(1.0)
            self.log(f"🏁 Organización completa — {movidos} movidos, {sin_clasificar} sin clave, {errores} errores")
            for item in stats.get("error_samples", []):
                self.log(f"❌ {item}")
            for item in stats.get("unclassified_samples", []):
                self.log(f"⚠️ Sin clave: {item}")

            messagebox.showinfo(
                "✅ Organización Completa",
                f"PDFs organizados en {self.client_folder.name}:\n\n"
                f"  ✅ Clasificados  : {movidos}\n"
                f"  ⚠️  Sin clave     : {sin_clasificar}\n"
                f"  ❌ Errores       : {errores}",
            )
            if sin_clasificar > 0:
                self.after(0, lambda: self._show_unclassified_warning(sin_clasificar, self.client_folder.name, stage="organize"))
        except Exception as e:
            self.log(f"❌ Error general: {e}")
            messagebox.showerror("Error", str(e))
        finally:
            self.btn_prueba_pdf.configure(state="normal", text="⚡ Organizar PDFs en carpetas de mes")


    # --- UI COMPONENTS ---
    def _card_account(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        card.pack(fill="x", pady=(0, 15))
        ctk.CTkLabel(card, text="🔌 CONEXIÓN Y DESTINO", font=FONT_HEADER).pack(
            anchor="w", padx=15, pady=(15, 5)
        )

        self.mode_var = ctk.StringVar(value="GMAIL_API")
        row_mode = ctk.CTkFrame(card, fg_color="transparent")
        row_mode.pack(fill="x", padx=15, pady=5)
        ctk.CTkRadioButton(
            row_mode,
            text="Gmail Oficial",
            variable=self.mode_var,
            value="GMAIL_API",
            command=self._toggle_auth_mode,
        ).pack(side="left", padx=(0, 20))
        ctk.CTkRadioButton(
            row_mode,
            text="Outlook / App Pass",
            variable=self.mode_var,
            value="IMAP",
            command=self._toggle_auth_mode,
        ).pack(side="left")

        self.frame_gmail = ctk.CTkFrame(card, fg_color="transparent")
        self.frame_gmail.pack(fill="x", padx=15, pady=5)
        self.account_var = ctk.StringVar(value="Nueva cuenta…")
        self.account_menu = ctk.CTkOptionMenu(
            self.frame_gmail,
            variable=self.account_var,
            values=["Nueva cuenta…"],
            width=180,
            command=self._on_gmail_account_change,
        )
        self.account_menu.pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            self.frame_gmail,
            text="Conectar",
            width=80,
            command=self.authenticate,
            fg_color="#1a73e8",
        ).pack(side="left", padx=5)

        self.frame_imap = ctk.CTkFrame(card, fg_color="transparent")
        grid_imap = ctk.CTkFrame(self.frame_imap, fg_color="transparent")
        grid_imap.pack(fill="x", pady=5)
        self.entry_host = ctk.CTkComboBox(
            grid_imap,
            values=["outlook.office365.com", "imap.gmail.com", "imap.mail.yahoo.com"],
            width=180,
        )
        self.entry_host.set("outlook.office365.com")
        self.entry_host.grid(row=0, column=0, padx=5, pady=5)
        self.entry_user = ctk.CTkComboBox(
            grid_imap, width=180, values=[], command=self._on_imap_user_select
        )
        self.entry_user.set("")
        self.entry_user.grid(row=0, column=1, padx=5, pady=5)
        self.entry_pass = ctk.CTkEntry(
            grid_imap, width=150, show="*", placeholder_text="App Password"
        )
        self.entry_pass.grid(row=0, column=2, padx=5, pady=5)
        ctk.CTkButton(grid_imap, text="Probar", width=60, command=self.test_imap_conn).grid(
            row=0, column=3, padx=5
        )
        self.chk_save_creds = ctk.CTkCheckBox(self.frame_imap, text="Guardar en Bóveda Segura")
        self.chk_save_creds.pack(anchor="w", padx=5, pady=(0, 5))

        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=15, pady=(10, 15))
        ctk.CTkButton(
            row2, text="📂 Seleccionar Cliente", command=self.pick_folder, fg_color="#e37400"
        ).pack(side="left", padx=(0, 10))
        self.lbl_dest = ctk.CTkLabel(row2, text="(Cliente no seleccionado)", text_color="gray")
        self.lbl_dest.pack(side="left", padx=5)
        ctk.CTkButton(row2, text="↗", width=30, command=self.open_dest, fg_color="#444").pack(
            side="right"
        )

    def _toggle_auth_mode(self):
        if self.mode_var.get() == "GMAIL_API":
            self.frame_imap.pack_forget()
            self.frame_gmail.pack(fill="x", padx=15, pady=5)
        else:
            self.frame_gmail.pack_forget()
            self.frame_imap.pack(fill="x", padx=15, pady=5)
            self._refresh_imap_ui()

    def _card_scope(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        card.pack(fill="x", pady=(0, 15))
        ctk.CTkLabel(card, text="📅 RANGO Y TIPOS", font=FONT_HEADER).pack(
            anchor="w", padx=15, pady=(15, 5)
        )
        self.row_dates = ctk.CTkFrame(card, fg_color="transparent")
        self.row_dates.pack(fill="x", padx=15, pady=5)
        self.e_from = ctk.CTkEntry(self.row_dates, width=100, placeholder_text="DD/MM/AAAA")
        self.e_from.pack(side="left")
        ctk.CTkButton(
            self.row_dates,
            text="📅",
            width=30,
            fg_color="#555",
            command=lambda: self._calendar(self.e_from),
        ).pack(side="left", padx=2)
        ctk.CTkLabel(self.row_dates, text=" a ").pack(side="left")
        self.e_to = ctk.CTkEntry(self.row_dates, width=100, placeholder_text="DD/MM/AAAA")
        self.e_to.pack(side="left")
        ctk.CTkButton(
            self.row_dates,
            text="📅",
            width=30,
            fg_color="#555",
            command=lambda: self._calendar(self.e_to),
        ).pack(side="left", padx=2)
        self.seg_dates = ctk.CTkSegmentedButton(
            self.row_dates, values=["30 días", "Este año", "Todo"], command=self._apply_quick_range
        )
        self.seg_dates.set("30 días")
        self._apply_quick_range("30 días")
        self.seg_dates.pack(side="right", padx=5)

        self.cal_frame = ctk.CTkFrame(
            card,
            corner_radius=10,
            border_width=1,
            border_color=("#BFBFBF", "#555555"),
        )
        self.calendar = InlineCalendar(self.cal_frame, self._on_cal_pick)
        self.calendar.pack(padx=5, pady=5, anchor="w")

        self.row_ext = ctk.CTkFrame(card, fg_color="transparent")
        self.row_ext.pack(fill="x", padx=15, pady=(6, 12))
        self.exts = MultiSelectDropdown(self.row_ext)
        self.exts.pack(fill="x")

    def _card_filters(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        card.pack(fill="x", pady=(0, 15))
        ctk.CTkLabel(card, text="🔍 FILTROS AVANZADOS", font=FONT_HEADER).pack(
            anchor="w", padx=15, pady=(15, 5)
        )
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(grid, text="Que contenga:").grid(row=0, column=0, sticky="w", padx=5)
        self.e_inc_terms = ctk.CTkEntry(grid, placeholder_text="Ej: factura")
        self.e_inc_terms.grid(row=0, column=1, sticky="ew", padx=5, pady=2)
        ctk.CTkLabel(grid, text="Que NO contenga:").grid(row=1, column=0, sticky="w", padx=5)
        self.e_exc_terms = ctk.CTkEntry(grid, placeholder_text="Ej: publicidad")
        self.e_exc_terms.grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        ctk.CTkLabel(grid, text="Solo de emails:").grid(row=2, column=0, sticky="w", padx=5)
        self.e_inc_from = ctk.CTkEntry(grid, placeholder_text="Ej: @empresa.com")
        self.e_inc_from.grid(row=2, column=1, sticky="ew", padx=5, pady=2)
        ctk.CTkLabel(grid, text="Excluir emails:").grid(row=3, column=0, sticky="w", padx=5)
        self.e_exc_from = ctk.CTkEntry(grid, placeholder_text="Ej: no-reply")
        self.e_exc_from.grid(row=3, column=1, sticky="ew", padx=5, pady=2)
        grid.columnconfigure(1, weight=1)
        row_chk = ctk.CTkFrame(card, fg_color="transparent")
        row_chk.pack(fill="x", padx=15, pady=(10, 15))
        self.var_exc_inbox = ctk.BooleanVar(value=False)
        self.var_exc_sent = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(row_chk, text="Excluir Inbox", variable=self.var_exc_inbox).pack(
            side="left", padx=5
        )
        ctk.CTkCheckBox(row_chk, text="Excluir Enviados", variable=self.var_exc_sent).pack(
            side="left", padx=5
        )

    def _card_settings(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        card.pack(fill="x", pady=(0, 15))
        ctk.CTkLabel(card, text="⚙️ AJUSTES", font=FONT_HEADER).pack(anchor="w", padx=15, pady=(15, 5))

        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(grid, text="Modo apariencia:").grid(row=0, column=0, sticky="w", padx=5)
        self.set_appearance = ctk.CTkOptionMenu(grid, values=["System", "Light", "Dark"], width=140)
        self.set_appearance.grid(row=0, column=1, sticky="w", padx=5)
        ctk.CTkLabel(grid, text="Max adjunto MB:").grid(row=1, column=0, sticky="w", padx=5)
        self.set_max_attach = ctk.CTkEntry(grid, width=140)
        self.set_max_attach.grid(row=1, column=1, sticky="w", padx=5)
        ctk.CTkLabel(grid, text="Workers descarga:").grid(row=2, column=0, sticky="w", padx=5)
        self.set_workers = ctk.CTkEntry(grid, width=140)
        self.set_workers.grid(row=2, column=1, sticky="w", padx=5)

        self.adv_open = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(card, text="Mostrar avanzados", variable=self.adv_open, command=self._toggle_advanced_settings).pack(anchor="w", padx=15, pady=5)
        self.adv_frame = ctk.CTkFrame(card, fg_color="transparent")

        ctk.CTkLabel(self.adv_frame, text="Año fiscal:").grid(row=0, column=0, sticky="w", padx=5)
        self.set_fiscal_year = ctk.CTkEntry(self.adv_frame, width=120)
        self.set_fiscal_year.grid(row=0, column=1, sticky="w", padx=5)
        ctk.CTkLabel(self.adv_frame, text="Drive red:").grid(row=1, column=0, sticky="w", padx=5)
        self.set_network = ctk.CTkEntry(self.adv_frame, width=220)
        self.set_network.grid(row=1, column=1, sticky="w", padx=5)
        ctk.CTkLabel(self.adv_frame, text="PDF max páginas:").grid(row=2, column=0, sticky="w", padx=5)
        self.set_pdf_pages = ctk.CTkEntry(self.adv_frame, width=120)
        self.set_pdf_pages.grid(row=2, column=1, sticky="w", padx=5)
        ctk.CTkLabel(self.adv_frame, text="Hacienda timeout:").grid(row=3, column=0, sticky="w", padx=5)
        self.set_timeout = ctk.CTkEntry(self.adv_frame, width=120)
        self.set_timeout.grid(row=3, column=1, sticky="w", padx=5)
        ctk.CTkLabel(self.adv_frame, text="Hacienda retries:").grid(row=4, column=0, sticky="w", padx=5)
        self.set_retries = ctk.CTkEntry(self.adv_frame, width=120)
        self.set_retries.grid(row=4, column=1, sticky="w", padx=5)
        ctk.CTkLabel(self.adv_frame, text="Años abiertos (csv):").grid(row=5, column=0, sticky="w", padx=5)
        self.set_open_years = ctk.CTkEntry(self.adv_frame, width=220)
        self.set_open_years.grid(row=5, column=1, sticky="w", padx=5)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(5, 12))
        ctk.CTkButton(row, text="Guardar Ajustes", command=self._save_settings_ui).pack(side="left", padx=5)
        ctk.CTkButton(row, text="Guardar Perfil Cliente", command=self._save_client_profile).pack(side="left", padx=5)

    def _toggle_advanced_settings(self):
        if self.adv_open.get():
            self.adv_frame.pack(fill="x", padx=10, pady=5)
        else:
            self.adv_frame.pack_forget()

    def _load_settings_ui(self):
        cfg = get_settings()
        self.set_appearance.set(str(cfg.get("appearance_mode", "System")))
        self.set_max_attach.delete(0, "end"); self.set_max_attach.insert(0, str(cfg.get("max_attachment_mb", 50)))
        self.set_workers.delete(0, "end"); self.set_workers.insert(0, str(cfg.get("download_workers", "") or ""))
        self.set_fiscal_year.delete(0, "end"); self.set_fiscal_year.insert(0, str(cfg.get("fiscal_year", "")))
        self.set_network.delete(0, "end"); self.set_network.insert(0, str(cfg.get("network_drive", "Z:/DATA")))
        self.set_pdf_pages.delete(0, "end"); self.set_pdf_pages.insert(0, str(cfg.get("pdf_max_pages", 4)))
        self.set_timeout.delete(0, "end"); self.set_timeout.insert(0, str(cfg.get("hacienda_timeout", 10.0)))
        self.set_retries.delete(0, "end"); self.set_retries.insert(0, str(cfg.get("hacienda_retries", 2)))
        self.set_open_years.delete(0, "end"); self.set_open_years.insert(0, ",".join(str(x) for x in cfg.get("open_fiscal_years", [])))
        self.exts.set_options(cfg.get("default_extensions", ["pdf", "xml", "xlsx", "zip", "jpg", "png"]))

    def _save_settings_ui(self):
        raw_open = [x.strip() for x in self.set_open_years.get().split(",") if x.strip()]
        try:
            max_attachment_mb = int(self.set_max_attach.get() or 50)
            download_workers = int(self.set_workers.get()) if self.set_workers.get().strip() else None
            fiscal_year = int(self.set_fiscal_year.get() or datetime.date.today().year)
            pdf_max_pages = int(self.set_pdf_pages.get() or 4)
            hacienda_timeout = float(self.set_timeout.get() or 10.0)
            hacienda_retries = int(self.set_retries.get() or 2)
        except ValueError:
            messagebox.showerror(
                "Ajustes inválidos",
                "Revisa los campos numéricos (MB, workers, año fiscal, páginas PDF, timeout y retries).",
            )
            return

        save_settings(
            {
                "appearance_mode": self.set_appearance.get(),
                "max_attachment_mb": max_attachment_mb,
                "download_workers": download_workers,
                "fiscal_year": fiscal_year,
                "network_drive": self.set_network.get().strip() or "Z:/DATA",
                "pdf_max_pages": pdf_max_pages,
                "hacienda_timeout": hacienda_timeout,
                "hacienda_retries": hacienda_retries,
                "open_fiscal_years": [int(x) for x in raw_open if x.isdigit()],
                "default_extensions": self.exts.selected(),
            }
        )
        messagebox.showinfo("Ajustes", "Ajustes guardados. Si cambiaste año fiscal o unidad de red, reinicia la app.")

    def _save_client_profile(self):
        if not self.client_folder:
            return messagebox.showwarning("Perfil", "Selecciona primero una carpeta de cliente")
        profile = {
            "gmail_account": self.account_var.get(),
            "default_extensions": self.exts.selected(),
            "exclude_from": [x.strip() for x in self.e_exc_from.get().split(",") if x.strip()],
            "include_from": [x.strip() for x in self.e_inc_from.get().split(",") if x.strip()],
            "exclude_terms": [x.strip() for x in self.e_exc_terms.get().split(",") if x.strip()],
            "include_terms": [x.strip() for x in self.e_inc_terms.get().split(",") if x.strip()],
            "date_range_days": self._infer_date_range_days(),
        }
        save_profile(self.client_folder.name, profile)
        messagebox.showinfo("Perfil", f"Perfil guardado para {self.client_folder.name}")

    def _infer_date_range_days(self) -> int:
        try:
            d1 = datetime.datetime.strptime(self.e_from.get(), "%d/%m/%Y").date()
            d2 = datetime.datetime.strptime(self.e_to.get(), "%d/%m/%Y").date()
            return max(1, (d2 - d1).days + 1)
        except ValueError:
            return 30

    def _action_buttons(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", pady=5)
        self.btn_plan = ctk.CTkButton(
            frame,
            text="1. ANALIZAR",
            height=40,
            font=("Segoe UI", 12, "bold"),
            fg_color="#188038",
            command=self.plan,
        )
        self.btn_plan.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.btn_dl = ctk.CTkButton(
            frame,
            text="2. DESCARGAR",
            height=40,
            font=("Segoe UI", 12, "bold"),
            fg_color="#1a73e8",
            state="disabled",
            command=self.download,
        )
        self.btn_dl.pack(side="left", fill="x", expand=True, padx=(5, 0))
        row_ctrl = ctk.CTkFrame(parent, fg_color="transparent")
        row_ctrl.pack(fill="x", pady=5)
        self.btn_pause = ctk.CTkButton(
            row_ctrl,
            text="Pausar",
            width=80,
            state="disabled",
            command=self._toggle_pause,
            fg_color="gray",
        )
        self.btn_pause.pack(side="left")
        self.btn_cancel = ctk.CTkButton(
            row_ctrl,
            text="Cancelar",
            width=80,
            state="disabled",
            command=self._cancel,
            fg_color="#d93025",
        )
        self.btn_cancel.pack(side="left", padx=5)

    def _setup_status_panel(self, parent):
        self.progress = ctk.CTkProgressBar(parent, height=15)
        self.progress.pack(fill="x", padx=15, pady=15)
        self.progress.set(0)
        self.lbl_status = ctk.CTkLabel(parent, text="Esperando...", font=("Consolas", 14))
        self.lbl_status.pack(pady=5)
        ctk.CTkLabel(parent, text="Bitácora de operaciones", font=("Segoe UI", 12, "bold")).pack(
            anchor="w", padx=15
        )
        self.log_txt = ctk.CTkTextbox(parent)
        self.log_txt.pack(fill="both", expand=True, padx=15, pady=(0, 10))
        foot = ctk.CTkFrame(parent, fg_color="transparent")
        foot.pack(fill="x", padx=15, pady=10)
        ctk.CTkButton(
            foot,
            text="Limpiar Duplicados",
            command=self.cleanup_dupes,
            fg_color="transparent",
            border_width=1,
            text_color="gray",
        ).pack(fill="x")

    # --- HELPERS ---
    def _calendar(self, entry):
        if self._cal_target == entry and self.cal_frame.winfo_manager():
            self.cal_frame.pack_forget()
            self._cal_target = None
            return

        self._cal_target = entry
        value = entry.get().strip()
        try:
            if value:
                dt = datetime.datetime.strptime(value, "%d/%m/%Y")
                self.calendar.show_month(dt.year, dt.month)
        except ValueError:
            pass

        self.cal_frame.pack(fill="x", padx=15, pady=(2, 8), before=self.row_ext)

    def _on_cal_pick(self, val):
        if self._cal_target:
            self._cal_target.delete(0, "end")
            self._cal_target.insert(0, val)
            self.cal_frame.pack_forget()
            self._cal_target = None

    def _apply_quick_range(self, val):
        today = datetime.date.today()
        if val == "30 días":
            start = today - datetime.timedelta(days=29)
        elif val == "Este año":
            start = datetime.date(today.year, 1, 1)
        else:
            start = datetime.date(2000, 1, 1)
        self.e_from.delete(0, "end")
        self.e_from.insert(0, start.strftime("%d/%m/%Y"))
        self.e_to.delete(0, "end")
        self.e_to.insert(0, today.strftime("%d/%m/%Y"))

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.btn_plan.configure(state=state)
        self.btn_pause.configure(state="normal" if busy else "disabled")
        self.btn_cancel.configure(state="normal" if busy else "disabled")

    def _toggle_pause(self):
        if self._plan_pause.is_set():
            self._plan_pause.clear()
            self.btn_pause.configure(text="Pausar", fg_color="gray")
            self.log("Reanudando...")
        else:
            self._plan_pause.set()
            self.btn_pause.configure(text="Reanudar", fg_color="#e37400")
            self.log("Pausado.")

    def _cancel(self):
        self._plan_stop.set()
        self.log("Cancelando operación...")

    def log(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_txt.insert("end", f"[{ts}] {msg}\n")
        self.log_txt.see("end")

    def authenticate(self):
        sel = self.account_var.get()
        is_new = sel == "Nueva cuenta…"
        try:
            authenticate_gmail(None if is_new else sel, force_new=is_new)
            self._refresh_accounts_ui()
            new_account = current_account()
            if new_account:
                self._resolve_and_apply_folder(new_account)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _refresh_accounts_ui(self):
        accts = list_accounts()
        self.account_menu.configure(values=accts + ["Nueva cuenta…"])
        curr = current_account()
        if curr:
            self.account_var.set(curr)

    # --- IMAP LOGIC ---
    def _refresh_imap_ui(self):
        self.entry_user.configure(values=list_imap_emails())

    def _on_imap_user_select(self, email):
        host, password = get_imap_credential(email)
        if host:
            self.entry_host.set(host)
        self._imap_loaded_password = password
        self.entry_pass.delete(0, "end")
        self.entry_pass.configure(placeholder_text="Usando contraseña guardada" if password else "App Password")
        self.log(f"Credenciales cargadas para {email}" if password else f"Sin contraseña guardada para {email}")
        if host and not password and not is_vault_unlocked():
            self.entry_pass.configure(placeholder_text="🔒 Bóveda bloqueada — presiona Probar para desbloquear")
        if email and email != "":
            self._resolve_and_apply_folder(email)

    def _on_gmail_account_change(self, selected_account: str):
        if selected_account == "Nueva cuenta…":
            return
        self._resolve_and_apply_folder(selected_account)

    def _resolve_and_apply_folder(self, account_email: str):
        """
        Intenta resolver la carpeta vinculada a account_email.
        Si hay vínculo → aplica la carpeta automáticamente.
        Si no hay vínculo → abre diálogo de vinculación inicial.
        """
        folder = resolve_client_folder(account_email)
        if folder:
            self.client_folder = folder
            self.lbl_dest.configure(text=folder.name, text_color=("green", "lightgreen"))
            self.log(f"📂 Carpeta resuelta automáticamente: {folder.name}")
            self._apply_profile_for_client(folder.name)
        else:
            self._open_link_account_dialog(account_email)

    def _open_link_account_dialog(self, account_email: str):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Vincular cuenta a cliente")
        dialog.geometry("560x360")
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text="Vincular cuenta a cliente",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=("#1a73e8", "#6fa8ff"),
        ).pack(pady=(16, 8))

        ctk.CTkLabel(
            dialog,
            text=(
                f"La cuenta `{account_email}` no tiene carpeta asignada.\n"
                "Ingresa la cédula del cliente para vincularla:"
            ),
            justify="center",
        ).pack(pady=(0, 10))

        body = ctk.CTkFrame(dialog, fg_color=COLOR_CARD, corner_radius=10)
        body.pack(fill="both", expand=True, padx=16, pady=8)

        entry_cedula = ctk.CTkEntry(body, placeholder_text="Ej: 3-101-085674", width=240)
        entry_cedula.pack(pady=(16, 8))

        lbl_resultado = ctk.CTkLabel(body, text="", wraplength=460)
        lbl_resultado.pack(pady=6)

        entry_nombre_manual = ctk.CTkEntry(body, placeholder_text="Nombre manual (si no aparece en Hacienda)", width=420)

        row_btn = ctk.CTkFrame(body, fg_color="transparent")
        row_btn.pack(pady=(10, 12))

        btn_vincular = ctk.CTkButton(row_btn, text="Vincular", state="disabled")
        btn_vincular.pack(side="left", padx=5)

        def _mostrar_resultado(razon: str | None, cedula: str):
            btn_buscar.configure(state="normal", text="Buscar en Hacienda")
            if razon:
                nombre_final = razon.upper().strip()
                lbl_resultado.configure(text=f"✅ {nombre_final}", text_color=("green", "lightgreen"))
                btn_vincular.configure(state="normal")
                dialog._nombre_confirmado = nombre_final
                dialog._cedula_confirmada = cedula
                if entry_nombre_manual.winfo_ismapped():
                    entry_nombre_manual.pack_forget()
            else:
                lbl_resultado.configure(text="❌ No encontrado. Escribe el nombre manualmente:", text_color="orange")
                if not entry_nombre_manual.winfo_ismapped():
                    entry_nombre_manual.pack(pady=6)
                btn_vincular.configure(state="normal")

        def _buscar_cedula():
            cedula_raw = entry_cedula.get().strip()
            cedula = re.sub(r"\D", "", cedula_raw)
            if not cedula:
                return

            from facturacion_system.core.pdf_classifier import _db_connect, consultar_razon_social_hacienda, default_db_path

            btn_buscar.configure(state="disabled", text="Consultando...")

            def _query():
                try:
                    conn = _db_connect(default_db_path())
                    razon = consultar_razon_social_hacienda(conn, cedula)
                    conn.close()
                except Exception:
                    razon = None
                dialog.after(0, lambda: _mostrar_resultado(razon, cedula))

            threading.Thread(target=_query, daemon=True).start()

        def _vincular():
            from facturacion_system.core.file_manager import sanitize_folder_name
            from facturacion_system.core.settings import get_setting

            nombre = getattr(dialog, "_nombre_confirmado", None) or entry_nombre_manual.get().strip().upper()
            cedula = getattr(dialog, "_cedula_confirmada", "") or re.sub(r"\D", "", entry_cedula.get())
            if not nombre:
                return

            nombre_limpio = sanitize_folder_name(nombre.upper())
            open_years = get_setting("open_fiscal_years", [])
            year = max(open_years) if open_years else datetime.date.today().year
            pf_dir = Path(str(get_setting("network_drive", "Z:/DATA"))) / f"PF-{year}"
            carpeta_destino = pf_dir / "CLIENTES" / nombre_limpio

            if carpeta_destino.exists():
                msg = f"Se encontró carpeta existente:\n{nombre_limpio}\n\n¿Vincular esta carpeta a {account_email}?"
                if not messagebox.askyesno("Carpeta encontrada", msg):
                    return
            else:
                carpeta_destino.mkdir(parents=True, exist_ok=True)
                self.log(f"📁 Carpeta creada: {nombre_limpio}")

            save_email_link(account_email, nombre_limpio, cedula)
            perfil = get_profile(nombre_limpio) or {}
            perfil["gmail_account"] = account_email
            save_profile(nombre_limpio, perfil)

            self.client_folder = carpeta_destino
            self.lbl_dest.configure(text=nombre_limpio, text_color=("green", "lightgreen"))
            self.log(f"🔗 Cuenta {account_email} vinculada a {nombre_limpio}")
            dialog.destroy()

        btn_buscar = ctk.CTkButton(row_btn, text="Buscar en Hacienda", command=_buscar_cedula)
        btn_buscar.pack(side="left", padx=5)
        btn_vincular.configure(command=_vincular)

        ctk.CTkButton(row_btn, text="Cancelar", fg_color="#777", command=dialog.destroy).pack(side="left", padx=5)

    def _ensure_vault_unlocked(self) -> bool:
        if is_vault_unlocked():
            return True
        if not vault_exists():
            return self._open_vault_setup_dialog()
        return self._open_vault_unlock_dialog()

    def _open_vault_setup_dialog(self) -> bool:
        result = [False]
        dialog = ctk.CTkToplevel(self)
        dialog.title("🔐 Configurar Bóveda de Credenciales")
        dialog.geometry("620x420")
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text="🔐 Configurar Bóveda de Credenciales",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(pady=(16, 8))
        ctk.CTkLabel(
            dialog,
            text=(
                "Primera vez. Crea una passphrase para proteger tus credenciales IMAP.\n"
                "Mínimo 8 caracteres con letras y números.\n"
                "Esta passphrase no se puede recuperar si se olvida."
            ),
            justify="center",
        ).pack(pady=(0, 10))

        card = ctk.CTkFrame(dialog, fg_color=COLOR_CARD, corner_radius=10)
        card.pack(fill="both", expand=True, padx=16, pady=8)

        pass_var = ctk.StringVar(value="")
        conf_var = ctk.StringVar(value="")

        entry_pass = ctk.CTkEntry(card, show="*", textvariable=pass_var, width=360, placeholder_text="Passphrase")
        entry_pass.pack(pady=(16, 6))
        entry_conf = ctk.CTkEntry(card, show="*", textvariable=conf_var, width=360, placeholder_text="Confirmar passphrase")
        entry_conf.pack(pady=6)

        strength = ctk.CTkProgressBar(card, width=360)
        strength.pack(pady=8)
        strength.set(0)

        lbl_req = ctk.CTkLabel(card, text="", justify="left")
        lbl_req.pack(pady=4)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(pady=(12, 12))

        btn_create = ctk.CTkButton(row, text="Crear Bóveda", state="disabled")
        btn_create.pack(side="left", padx=6)

        def _refresh_strength(*_):
            pwd = pass_var.get()
            ok, msg = _validate_passphrase_strength(pwd)
            score = 0.0
            if len(pwd) >= 8:
                score += 0.4
            if any(c.isalpha() for c in pwd):
                score += 0.3
            if any(c.isdigit() for c in pwd):
                score += 0.3
            strength.set(min(score, 1.0))
            if ok:
                lbl_req.configure(text="✅ Passphrase válida", text_color=("green", "lightgreen"))
            else:
                lbl_req.configure(text=f"❌ {msg}", text_color="orange")
            btn_create.configure(state="normal" if ok and pwd == conf_var.get() else "disabled")

        pass_var.trace_add("write", _refresh_strength)
        conf_var.trace_add("write", _refresh_strength)

        def _create():
            pwd = pass_var.get()
            ok = initialize_vault(pwd)
            if not ok:
                messagebox.showerror("Passphrase inválida", "Debe tener mínimo 8 caracteres, letras y números.")
                return
            n = migrate_from_keyring_if_needed()
            if n > 0:
                self.log(f"🔄 {n} credenciales migradas desde el sistema anterior.")
            self.log("🔐 Bóveda creada y desbloqueada.")
            result[0] = True
            dialog.destroy()

        btn_create.configure(command=_create)
        ctk.CTkButton(row, text="Cancelar", fg_color="#777", command=dialog.destroy).pack(side="left", padx=6)

        self.wait_window(dialog)
        return result[0]

    def _open_vault_unlock_dialog(self) -> bool:
        result = [False]
        dialog = ctk.CTkToplevel(self)
        dialog.title("🔐 Desbloquear Bóveda")
        dialog.geometry("480x280")
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="🔐 Desbloquear Bóveda", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(16, 8))
        ctk.CTkLabel(dialog, text="Ingresa tu passphrase maestra").pack(pady=4)

        pass_var = ctk.StringVar(value="")
        entry = ctk.CTkEntry(dialog, show="*", textvariable=pass_var, width=300)
        entry.pack(pady=8)
        entry.focus_set()

        lbl_error = ctk.CTkLabel(dialog, text="", text_color="red")
        lbl_error.pack(pady=4)

        row = ctk.CTkFrame(dialog, fg_color="transparent")
        row.pack(pady=12)

        state = {"tries": 0}
        btn_unlock = ctk.CTkButton(row, text="Desbloquear")
        btn_unlock.pack(side="left", padx=6)

        def _unlock():
            ok = unlock_vault(pass_var.get())
            if ok:
                n = migrate_from_keyring_if_needed()
                if n > 0:
                    self.log(f"🔄 {n} credenciales migradas.")
                self.log("🔓 Bóveda desbloqueada.")
                result[0] = True
                dialog.destroy()
                return

            state["tries"] += 1
            lbl_error.configure(text=f"Passphrase incorrecta. ({state['tries']}/5)")
            if state["tries"] >= 5:
                btn_unlock.configure(state="disabled")
                dialog.after(30000, lambda: btn_unlock.configure(state="normal"))
                state["tries"] = 0

        btn_unlock.configure(command=_unlock)
        ctk.CTkButton(row, text="Cancelar", fg_color="#777", command=dialog.destroy).pack(side="left", padx=6)

        self.wait_window(dialog)
        return result[0]

    def _imap_password(self) -> str:
        typed = self.entry_pass.get()
        return typed or (self._imap_loaded_password or "")

    def test_imap_conn(self):
        h, u, p = self.entry_host.get(), self.entry_user.get(), self._imap_password()
        if self.chk_save_creds.get():
            if not self._ensure_vault_unlocked():
                return
        if not all([h, u, p]):
            return messagebox.showwarning("!", "Faltan datos")
        try:
            dl = ImapDownloader(h, u, p)
            if dl.connect():
                dl.disconnect()
                self.log(f"Conexión exitosa con {u}")
                if self.chk_save_creds.get():
                    save_imap_credential(u, p, h)
                    self._refresh_imap_ui()
                messagebox.showinfo("Éxito", "Conexión IMAP OK")
        except Exception as e:
            messagebox.showerror("Error", f"{e}")

    def pick_folder(self):
        d = filedialog.askdirectory(
            title="Selecciona carpeta del cliente",
            initialdir=str(CLIENTS_DIR),
        )
        if not d:
            return

        selected = Path(d)
        try:
            selected.relative_to(CLIENTS_DIR)
        except ValueError:
            messagebox.showerror(
                "Error",
                f"La carpeta debe estar dentro de:\n{CLIENTS_DIR}",
            )
            return

        self.client_folder = selected
        self.lbl_dest.configure(text=selected.name, text_color=("green", "lightgreen"))
        self._apply_profile_for_client(selected.name)

    def _apply_profile_for_client(self, client_name: str):
        profile = get_profile(client_name) or {}
        if not profile:
            return
        if profile.get("gmail_account"):
            self.account_var.set(profile.get("gmail_account"))
        if profile.get("default_extensions"):
            self.exts.set_selected(profile.get("default_extensions"))
        self.e_exc_from.delete(0, "end"); self.e_exc_from.insert(0, ", ".join(profile.get("exclude_from", [])))
        self.e_inc_from.delete(0, "end"); self.e_inc_from.insert(0, ", ".join(profile.get("include_from", [])))
        self.e_exc_terms.delete(0, "end"); self.e_exc_terms.insert(0, ", ".join(profile.get("exclude_terms", [])))
        self.e_inc_terms.delete(0, "end"); self.e_inc_terms.insert(0, ", ".join(profile.get("include_terms", [])))
        days = int(profile.get("date_range_days") or 0)
        if days > 0:
            end = datetime.date.today()
            start = end - datetime.timedelta(days=max(0, days - 1))
            self.e_from.delete(0, "end"); self.e_from.insert(0, start.strftime("%d/%m/%Y"))
            self.e_to.delete(0, "end"); self.e_to.insert(0, end.strftime("%d/%m/%Y"))

    def open_dest(self):
        if self.client_folder and self.client_folder.exists():
            try:
                open_folder(str(self.client_folder))
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo abrir la carpeta: {e}")

    def cleanup_dupes(self):
        if not self.client_folder:
            return
        groups = find_duplicates(self.client_folder, self.exts.selected())
        count = sum(len(v) for v in groups.values())
        if count > 0 and messagebox.askyesno("Limpiar", f"¿Borrar {count} duplicados?"):
            delete_duplicates(groups)
            self.log("Limpieza completada.")

    def _read_inputs(self):
        return {
            "fecha_inicio": self.e_from.get(),
            "fecha_fin": self.e_to.get(),
            "extensiones": self.exts.selected(),
            "excluir_inbox": self.var_exc_inbox.get(),
            "excluir_enviados": self.var_exc_sent.get(),
            "excluir_remitentes": [
                x.strip() for x in self.e_exc_from.get().split(",") if x.strip()
            ],
            "include_from": [x.strip() for x in self.e_inc_from.get().split(",") if x.strip()],
            "incluir_terminos": [x.strip() for x in self.e_inc_terms.get().split(",") if x.strip()],
            "exclude_terms": [x.strip() for x in self.e_exc_terms.get().split(",") if x.strip()],
        }

    # --- EXECUTION ---
    def plan(self):
        mode = self.mode_var.get()
        inputs = self._read_inputs()
        if not inputs["fecha_inicio"]:
            return messagebox.showwarning("!", "Faltan fechas.")
        if mode == "GMAIL_API" and not current_account():
            return messagebox.showwarning("!", "Conecta Google.")
        if mode == "IMAP" and not self._imap_password():
            return messagebox.showwarning("!", "Falta password.")

        self._planning = True
        self._set_busy(True)
        self.log(f"Iniciando análisis ({mode})...")
        self._plan_stop.clear()
        self._plan_pause.clear()

        def _run():
            try:
                if mode == "GMAIL_API":

                    def cb(done, total, atts):
                        self.progress.set(done / max(1, total))
                        self.lbl_status.configure(text=f"Analizando: {done} msgs")

                    tasks, stats = plan_tasks(**inputs, progress_cb=cb, stop_event=self._plan_stop)
                    self.tasks = tasks
                    self.log(f"Fin Análisis. {stats['attachments']} adjuntos.")
                    if tasks:
                        self.btn_dl.configure(state="normal")
                else:
                    h, u, p = self.entry_host.get(), self.entry_user.get(), self._imap_password()
                    dl = ImapDownloader(h, u, p)
                    dl.connect()

                    self.lbl_status.configure(text="Buscando correos...")
                    folder_used = dl.select_best_folder()
                    self.imap_folder = folder_used  # RECORDAMOS LA CARPETA
                    self.log(f"Carpeta detectada: {folder_used}")

                    mids = dl.search_emails(inputs["fecha_inicio"], inputs["fecha_fin"])
                    self.imap_ids = mids
                    self.log(f"Encontrados: {len(mids)} correos.")
                    if mids:
                        self.btn_dl.configure(state="normal")
                    dl.disconnect()
            except Exception as e:
                self.log(f"Error: {e}")
            finally:
                self._set_busy(False)

        threading.Thread(target=_run, daemon=True).start()

    def _show_unclassified_warning(self, unclassified_count: int, client_folder_name: str, *, stage: str = "download"):
        """Muestra diálogo de aviso cuando hay PDFs sin clasificar."""
        if unclassified_count == 0:
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("⚠️ PDFs Sin Clasificar")
        dialog.geometry("560x320")
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text="⚠️ ATENCIÓN",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="orange",
        ).pack(pady=(20, 5))

        if stage == "download":
            detalle = (
                f"Se detectaron {unclassified_count} PDF(s) sin clave de Hacienda válida durante la descarga.\n"
                "Esto es un pre-chequeo: AÚN NO han sido organizados."
            )
            ruta = (self.client_folder / "PDF") if self.client_folder else SIN_CLASIFICAR_DIR
            pie = "Actualmente están en la carpeta PDF del cliente:"
            texto_boton = "Abrir carpeta PDF del cliente"
        else:
            detalle = (
                f"Se detectaron {unclassified_count} PDF(s) sin clave de Hacienda válida durante la organización."
            )
            ruta = SIN_CLASIFICAR_DIR / client_folder_name
            pie = "Se movieron a la carpeta de revisión manual:"
            texto_boton = "Abrir carpeta SIN_CLASIFICAR"

        ctk.CTkLabel(
            dialog,
            text=detalle,
            font=ctk.CTkFont(size=13),
            justify="center",
        ).pack(pady=5)

        ctk.CTkLabel(
            dialog,
            text=pie,
            font=ctk.CTkFont(size=12),
        ).pack(pady=5)

        ctk.CTkLabel(
            dialog,
            text=str(ruta),
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=("gray85", "gray25"),
            corner_radius=6,
            padx=10,
            pady=6,
        ).pack(pady=10, padx=20, fill="x")

        ctk.CTkButton(
            dialog,
            text=texto_boton,
            command=lambda: open_folder(str(ruta)),
        ).pack(pady=5)

        ctk.CTkButton(dialog, text="Entendido", command=dialog.destroy).pack(pady=5)


    def download(self):
        if not self.client_folder:
            return messagebox.showwarning("!", "Selecciona carpeta de cliente.")
        mode = self.mode_var.get()
        self._set_busy(True)
        self.log("Descargando...")

        def _run():
            try:
                if mode == "GMAIL_API":

                    def cb(done, c, msg=""):
                        self.progress.set(done / max(1, c["total"]))
                        self.lbl_status.configure(text=f"⬇️ {done}/{c['total']}")

                    res = run_download(
                        self.tasks,
                        self.client_folder,
                        progress_cb=cb,
                        stop_event=self._plan_stop,
                    )
                    self.log(f"Fin. Descargados: {res['downloaded']}")
                    # Sin pre-chequeo durante descarga para priorizar rendimiento.
                else:
                    h, u, p = self.entry_host.get(), self.entry_user.get(), self._imap_password()
                    dl = ImapDownloader(h, u, p)
                    dl.connect()

                    def cb_imap(done, st):
                        self.progress.set(done / max(1, st["total"]))
                        self.lbl_status.configure(text=f"⬇️ {done}/{st['total']}")

                    stats = dl.download_attachments(
                        self.imap_ids,
                        str(self.client_folder),
                        allowed_exts=self.exts.selected(),
                        progress_cb=cb_imap,
                        source_folder=self.imap_folder,
                    )
                    dl.disconnect()
                    self.log(f"Fin IMAP. Descargados: {stats['downloaded']}")
                messagebox.showinfo("Fin", "Proceso terminado.")
            except Exception as e:
                self.log(f"Error: {e}")
            finally:
                self._set_busy(False)

        threading.Thread(target=_run, daemon=True).start()
