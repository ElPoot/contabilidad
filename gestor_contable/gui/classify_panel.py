from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import customtkinter as ctk
import tkinter as tk

from gestor_contable.app.selection_vm import SelectionVM
from gestor_contable.core.catalog import CatalogManager
from gestor_contable.gui.icons import get_icon

BG = "#0d0f14"
SURFACE = "#13161e"
CARD = "#181c26"
BORDER = "#252a38"
TEAL = "#2dd4bf"
TEAL_DIM = "#1a9e8f"
TEXT = "#e8eaf0"
MUTED = "#6b7280"
DANGER = "#f87171"
SUCCESS = "#34d399"
WARNING = "#fbbf24"

_CATEGORY_ORDER = ["COMPRAS", "GASTOS", "OGND", "ACTIVO"]
_OGND_FALLBACK = ["OGND", "DNR", "ORS", "CNR"]


def _font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


@dataclass(slots=True)
class ClassifyPanelCallbacks:
    on_classify: Callable[[], None]
    on_auto_classify: Callable[[], None]
    on_recover: Callable[[], None]
    on_link: Callable[[], None]
    on_delete_omitido: Callable[[], None]
    on_create_pdf: Callable[[], None]
    on_open_new_cuenta: Callable[[], None]
    on_open_dest_folder: Callable[[Path | None], None]
    on_form_change: Callable[[], None]


class ClassifyPanel(ctk.CTkFrame):
    """Panel visual de clasificación contable."""

    def __init__(self, parent, callbacks: ClassifyPanelCallbacks):
        super().__init__(parent, fg_color="transparent")
        self.callbacks = callbacks
        self.catalog_mgr: CatalogManager | None = None
        self._manual_categories: list[str] = []
        self._forced_category = ""
        self._forced_subtipo = ""
        self._last_manual_category = ""
        self._manual_subtipos: dict[str, str] = {"GASTOS": "", "OGND": ""}
        self._all_cuentas: list[str] = []
        self._filtered_cuentas: list[str] = []
        self._prev_dest_path: Path | None = None
        self._callbacks_suspended = False

        self._cat_var = ctk.StringVar(value="")
        self._tipo_var = ctk.StringVar(value="")
        self._cuenta_var = ctk.StringVar(value="")
        self._cuenta_search_var = ctk.StringVar(value="")
        self._prov_var = ctk.StringVar(value="")

        self._cuenta_search_var.trace_add("write", self._on_search_changed)
        self._prov_var.trace_add("write", lambda *_: self._notify_form_change())

        self.grid_columnconfigure(0, weight=1)

        self._build()
        self.clear_selection_state()

    @property
    def btn_classify(self):
        return self._btn_classify

    @property
    def btn_auto_classify(self):
        return self._btn_auto_classify

    @property
    def btn_create_pdf(self):
        return self._btn_create_pdf

    @property
    def btn_recover(self):
        return self._btn_recover

    @property
    def btn_link(self):
        return self._btn_link

    @property
    def btn_delete(self):
        return self._btn_delete

    @contextmanager
    def _suspend_callbacks(self):
        previous = self._callbacks_suspended
        self._callbacks_suspended = True
        try:
            yield
        finally:
            self._callbacks_suspended = previous

    def _notify_form_change(self):
        if self._callbacks_suspended:
            return
        self.callbacks.on_form_change()

    def _build(self):
        self._batch_banner = ctk.CTkFrame(
            self,
            fg_color="#113831",
            corner_radius=12,
            border_width=1,
            border_color="#1d5e53",
        )
        self._batch_banner.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        self._batch_banner.grid_columnconfigure(0, weight=1)
        self._batch_title = ctk.CTkLabel(
            self._batch_banner,
            text="",
            font=_font(11, "bold"),
            text_color=TEXT,
            anchor="w",
        )
        self._batch_title.grid(row=0, column=0, sticky="ew", padx=10, pady=(7, 1))
        self._batch_subtitle = ctk.CTkLabel(
            self._batch_banner,
            text="",
            font=_font(10),
            text_color=MUTED,
            anchor="w",
        )
        self._batch_subtitle.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 7))

        self._hacienda_lbl = ctk.CTkLabel(
            self,
            text="",
            font=_font(10, "bold"),
            text_color=SUCCESS,
            fg_color="#0d2a1e",
            corner_radius=8,
            anchor="center",
        )
        self._hacienda_lbl.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6), ipadx=4, ipady=3)

        self._doc_strip = ctk.CTkFrame(
            self,
            fg_color=SURFACE,
            corner_radius=10,
            border_width=1,
            border_color=BORDER,
        )
        self._doc_strip.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        for column in range(3):
            self._doc_strip.grid_columnconfigure(column, weight=1)
        self._doc_total_value = self._build_doc_metric(self._doc_strip, 0, "TOTAL")
        self._doc_fecha_value = self._build_doc_metric(self._doc_strip, 1, "FECHA")
        self._doc_tipo_value = self._build_doc_metric(self._doc_strip, 2, "TIPO")

        card_border = ctk.CTkFrame(self, fg_color=BORDER, corner_radius=14)
        card_border.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))
        self._form_card = ctk.CTkFrame(card_border, fg_color=CARD, corner_radius=12)
        self._form_card.pack(fill="both", expand=True, padx=1, pady=1)
        self._form_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self._form_card,
            text="CLASIFICACIÓN CONTABLE",
            font=_font(9, "bold"),
            text_color=TEAL,
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 6))

        self._category_label = ctk.CTkLabel(
            self._form_card,
            text="Categoría",
            font=_font(11),
            text_color=MUTED,
        )
        self._category_label.grid(row=1, column=0, sticky="w", padx=12)

        self._category_slot = ctk.CTkFrame(self._form_card, fg_color="transparent")
        self._category_slot.grid(row=2, column=0, sticky="ew", padx=12, pady=(4, 8))
        self._category_slot.grid_columnconfigure(0, weight=1)

        self._category_selector = ctk.CTkSegmentedButton(
            self._category_slot,
            values=_CATEGORY_ORDER,
            fg_color=SURFACE,
            selected_color=TEAL,
            selected_hover_color=TEAL_DIM,
            unselected_color=SURFACE,
            unselected_hover_color=BORDER,
            text_color=TEXT,
            command=self._on_category_changed,
            height=30,
            font=_font(10, "bold"),
        )
        self._category_selector.grid(row=0, column=0, sticky="ew")

        self._forced_category_badge = ctk.CTkLabel(
            self._category_slot,
            text="",
            font=_font(11, "bold"),
            fg_color="#153b36",
            text_color=TEAL,
            corner_radius=10,
            anchor="w",
        )
        self._forced_category_badge.grid(row=0, column=0, sticky="ew")

        self._tipo_frame = ctk.CTkFrame(self._form_card, fg_color="transparent")
        self._tipo_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self._tipo_frame, text="Tipo", font=_font(10), text_color=MUTED).grid(row=0, column=0, sticky="w")
        self._tipo_cb = ctk.CTkComboBox(
            self._tipo_frame,
            variable=self._tipo_var,
            values=[],
            state="readonly",
            fg_color=SURFACE,
            border_color=BORDER,
            button_color=BORDER,
            button_hover_color=TEAL,
            text_color=TEXT,
            font=_font(12),
            dropdown_fg_color=CARD,
            dropdown_text_color=TEXT,
            command=self._on_subtipo_changed,
            height=32,
        )
        self._tipo_cb.grid(row=1, column=0, sticky="ew", pady=(3, 0))

        self._cuenta_frame = ctk.CTkFrame(self._form_card, fg_color="transparent")
        self._cuenta_frame.grid_columnconfigure(0, weight=1)
        cuenta_header = ctk.CTkFrame(self._cuenta_frame, fg_color="transparent")
        cuenta_header.grid(row=0, column=0, sticky="ew")
        cuenta_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(cuenta_header, text="Cuenta", font=_font(10), text_color=MUTED).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            cuenta_header,
            text="+",
            width=28,
            height=26,
            fg_color=TEAL,
            hover_color=TEAL_DIM,
            text_color=BG,
            font=_font(11, "bold"),
            command=self.callbacks.on_open_new_cuenta,
        ).grid(row=0, column=1, sticky="e")
        self._cuenta_search_entry = ctk.CTkEntry(
            self._cuenta_frame,
            textvariable=self._cuenta_search_var,
            placeholder_text="Buscar cuenta...",
            fg_color=SURFACE,
            border_color=BORDER,
            text_color=TEXT,
            font=_font(12),
            height=32,
        )
        self._cuenta_search_entry.grid(row=1, column=0, sticky="ew", pady=(4, 4))
        self._cuenta_search_entry.bind("<Down>", lambda _e: self._move_account_selection(1))
        self._cuenta_search_entry.bind("<Up>", lambda _e: self._move_account_selection(-1))
        self._cuenta_search_entry.bind("<Return>", lambda _e: self._confirm_account_selection())
        self._cuenta_search_entry.bind("<Escape>", lambda _e: self._clear_account_search())

        self._cuenta_results_lbl = ctk.CTkLabel(
            self._cuenta_frame,
            text="",
            font=_font(10),
            text_color=MUTED,
            anchor="w",
        )
        self._cuenta_results_lbl.grid(row=2, column=0, sticky="ew", pady=(0, 4))

        self._cuenta_list = ctk.CTkScrollableFrame(
            self._cuenta_frame,
            fg_color=SURFACE,
            corner_radius=10,
            border_width=1,
            border_color=BORDER,
            height=148,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=MUTED,
        )
        self._cuenta_list.grid(row=3, column=0, sticky="ew")
        self._cuenta_list.grid_columnconfigure(0, weight=1)

        self._prov_frame = ctk.CTkFrame(self._form_card, fg_color="transparent")
        self._prov_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self._prov_frame, text="Proveedor", font=_font(10), text_color=MUTED).grid(row=0, column=0, sticky="w")
        self._prov_entry = ctk.CTkEntry(
            self._prov_frame,
            textvariable=self._prov_var,
            fg_color=SURFACE,
            border_color=BORDER,
            text_color=TEXT,
            font=_font(12),
            height=34,
        )
        self._prov_entry.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self._prov_entry.bind("<Return>", lambda _e: self.callbacks.on_classify())

        self._preview_frame = ctk.CTkFrame(
            self._form_card,
            fg_color=SURFACE,
            corner_radius=10,
            border_width=1,
            border_color=BORDER,
        )
        self._preview_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self._preview_frame,
            text="Destino estimado",
            font=_font(9, "bold"),
            text_color=MUTED,
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        self._preview_lbl = ctk.CTkLabel(
            self._preview_frame,
            text="",
            font=_font(9),
            text_color=TEXT,
            justify="left",
            anchor="w",
            wraplength=212,
        )
        self._preview_lbl.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

        self._actions_frame = ctk.CTkFrame(self._form_card, fg_color="transparent")
        self._actions_frame.grid_columnconfigure(0, weight=1)

        self._btn_recover = ctk.CTkButton(
            self._actions_frame,
            text="Recuperar PDF",
            font=_font(13, "bold"),
            fg_color=WARNING,
            hover_color="#e8a61c",
            text_color=BG,
            corner_radius=10,
            height=38,
            command=self.callbacks.on_recover,
        )
        self._btn_link = ctk.CTkButton(
            self._actions_frame,
            text="Vincular a XML",
            image=get_icon("link", 18),
            compound="left",
            font=_font(13, "bold"),
            fg_color="#0f766e",
            hover_color="#115e59",
            text_color=TEXT,
            corner_radius=10,
            height=38,
            command=self.callbacks.on_link,
        )
        self._btn_delete = ctk.CTkButton(
            self._actions_frame,
            text="Borrar PDF",
            image=get_icon("trash", 18),
            compound="left",
            font=_font(13, "bold"),
            fg_color=DANGER,
            hover_color="#dc2626",
            text_color=BG,
            corner_radius=10,
            height=38,
            command=self.callbacks.on_delete_omitido,
        )
        self._btn_create_pdf = ctk.CTkButton(
            self._actions_frame,
            text="Crear PDF",
            image=get_icon("file_pdf", 18),
            compound="left",
            font=_font(13, "bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            text_color=TEXT,
            corner_radius=10,
            height=38,
            command=self.callbacks.on_create_pdf,
        )
        self._btn_auto_classify = ctk.CTkButton(
            self._actions_frame,
            text="Clasificar todos",
            font=_font(13, "bold"),
            fg_color="#0891b2",
            hover_color="#0e7490",
            text_color=TEXT,
            corner_radius=10,
            height=38,
            command=self.callbacks.on_auto_classify,
        )

        self._btn_classify = ctk.CTkButton(
            self._form_card,
            text="Clasificar",
            image=get_icon("modal_success", 18),
            compound="left",
            font=_font(14, "bold"),
            fg_color=TEAL,
            hover_color=TEAL_DIM,
            text_color=BG,
            corner_radius=12,
            height=46,
            command=self.callbacks.on_classify,
        )

        self._block_reason_lbl = ctk.CTkLabel(
            self._form_card,
            text="",
            font=_font(10),
            text_color=WARNING,
            justify="left",
            anchor="w",
            wraplength=220,
        )

        self._prev_frame = ctk.CTkFrame(
            self,
            fg_color=CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
        )
        self._prev_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self._prev_frame,
            text="ANTERIOR",
            font=_font(9, "bold"),
            text_color=MUTED,
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        self._prev_primary_lbl = ctk.CTkLabel(
            self._prev_frame,
            text="--",
            font=_font(11, "bold"),
            text_color=TEXT,
            anchor="w",
            justify="left",
            wraplength=220,
        )
        self._prev_primary_lbl.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 2))
        self._prev_secondary_lbl = ctk.CTkLabel(
            self._prev_frame,
            text="",
            font=_font(10),
            text_color=MUTED,
            anchor="w",
            justify="left",
            wraplength=220,
        )
        self._prev_secondary_lbl.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 5))
        self._prev_path_lbl = tk.Label(
            self._prev_frame,
            text="",
            font=("Segoe UI", 10),
            fg=TEAL,
            bg=CARD,
            cursor="hand2",
            anchor="w",
            justify="left",
            wraplength=215,
        )
        self._prev_path_lbl.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))
        self._prev_path_lbl.bind(
            "<Button-1>",
            lambda _e: self.callbacks.on_open_dest_folder(self._prev_dest_path),
        )

        self._bind_category_shortcuts(
            self,
            self._batch_banner,
            self._hacienda_lbl,
            self._doc_strip,
            self._form_card,
            self._category_selector,
            self._forced_category_badge,
            self._tipo_cb,
            self._cuenta_list,
            self._btn_classify,
            self._btn_recover,
            self._btn_link,
            self._btn_delete,
            self._btn_create_pdf,
            self._btn_auto_classify,
            self._prev_frame,
        )

        self._layout_form_rows()

    def _build_doc_metric(self, parent, column: int, label: str) -> ctk.CTkLabel:
        ctk.CTkLabel(parent, text=label, font=_font(8, "bold"), text_color=MUTED).grid(
            row=0, column=column, sticky="w", padx=10, pady=(6, 0)
        )
        value_widget = ctk.CTkLabel(
            parent,
            text="--",
            font=_font(12, "bold"),
            text_color=TEXT,
            anchor="w",
        )
        value_widget.grid(row=1, column=column, sticky="ew", padx=10, pady=(0, 6))
        return value_widget

    def _layout_form_rows(self):
        row = 3
        self._tipo_frame.grid(row=row, column=0, sticky="ew", padx=12, pady=(0, 8))
        row += 1
        self._cuenta_frame.grid(row=row, column=0, sticky="ew", padx=12, pady=(0, 8))
        row += 1
        self._prov_frame.grid(row=row, column=0, sticky="ew", padx=12, pady=(0, 8))
        row += 1
        self._preview_frame.grid(row=row, column=0, sticky="ew", padx=12, pady=(0, 8))
        row += 1
        self._actions_frame.grid(row=row, column=0, sticky="ew", padx=12, pady=(0, 8))
        row += 1
        self._btn_classify.grid(row=row, column=0, sticky="ew", padx=12, pady=(0, 4))
        row += 1
        self._block_reason_lbl.grid(row=row, column=0, sticky="ew", padx=12, pady=(0, 10))

    def set_catalog(self, mgr: CatalogManager | None, categories: list[str]):
        self.catalog_mgr = mgr
        self._manual_categories = self._ordered_categories(categories)
        self._category_selector.configure(values=self._manual_categories or _CATEGORY_ORDER)
        if not self._forced_category:
            self.sync_category(self._manual_categories)

    def sync_category(
        self,
        categories: list[str],
        forced_cat: str | None = None,
        forced_subtipo: str | None = None,
    ):
        previous_forced_category = self._forced_category
        self._manual_categories = self._ordered_categories(categories)
        self._forced_category = (forced_cat or "").strip().upper()
        self._forced_subtipo = (forced_subtipo or "").strip().upper()

        with self._suspend_callbacks():
            if self._forced_category:
                if not previous_forced_category:
                    self._remember_manual_selection()
                self._category_label.configure(text="Modo")
                self._category_selector.grid_remove()
                self._forced_category_badge.configure(text=f"Automático: {self._forced_category}")
                self._forced_category_badge.grid()
                self._cat_var.set(self._forced_category)
            else:
                self._category_label.configure(text="Categoría")
                self._forced_category_badge.grid_remove()
                self._category_selector.grid()
                values = self._manual_categories or _CATEGORY_ORDER
                self._category_selector.configure(values=values)
                if previous_forced_category:
                    current = self._last_manual_category
                    selected = current if current in values else (values[0] if values else "")
                    remembered_subtipo = self._manual_subtipos.get(selected, "") if selected in ("GASTOS", "OGND") else ""
                    self._tipo_var.set(remembered_subtipo)
                else:
                    current = self._cat_var.get().strip().upper()
                    if current in values:
                        selected = current
                    elif self._last_manual_category in values:
                        selected = self._last_manual_category
                    else:
                        selected = values[0] if values else ""
                self._cat_var.set(selected)
                if selected:
                    self._category_selector.set(selected)
                    self._last_manual_category = selected

            self._apply_category_change(notify=False)

        self._notify_form_change()

    def refresh_current_options(self, selected_cuenta: str | None = None):
        with self._suspend_callbacks():
            self._apply_category_change(notify=False)
            if selected_cuenta:
                self._select_account(selected_cuenta, notify=False)
        self._notify_form_change()

    def step_category(self, step: int) -> bool:
        if self._forced_category:
            return False

        values = self._manual_categories or _CATEGORY_ORDER
        if len(values) < 2:
            return False

        current = self._cat_var.get().strip().upper()
        if current in values:
            index = values.index(current)
        else:
            index = 0

        next_value = values[(index + step) % len(values)]
        if next_value == current:
            return False

        self._category_selector.set(next_value)
        self._on_category_changed(next_value)
        return True

    def get_form_values(self) -> dict[str, str]:
        cat = self._forced_category or self._cat_var.get().strip().upper()
        if cat in ("GASTOS", "OGND"):
            subtipo = self._forced_subtipo or self._tipo_var.get().strip().upper()
        else:
            subtipo = ""
        cuenta = self._cuenta_var.get().strip().upper() if cat == "GASTOS" else ""
        prov = self._prov_var.get().strip()
        return {
            "cat": cat,
            "subtipo": subtipo,
            "cuenta": cuenta,
            "prov": prov,
        }

    def set_path_preview(self, text: str):
        preview = (text or "").strip()
        if preview:
            self._preview_lbl.configure(text=preview)
            self._preview_frame.grid()
        else:
            self._preview_lbl.configure(text="")
            self._preview_frame.grid_remove()

    def clear_selection_state(self):
        self._hide_context_buttons()
        self._batch_banner.grid_remove()
        self._hacienda_lbl.grid_remove()
        self._doc_strip.grid_remove()
        self._prev_frame.grid_remove()
        self._set_prev_dest_path(None)
        self.set_path_preview("")
        self._btn_classify.configure(state="disabled", text="Clasificar")
        self._set_block_reason("Selecciona una factura para empezar.")

    def render(self, vm: SelectionVM):
        with self._suspend_callbacks():
            self._prov_var.set(vm.proveedor or "")

        self._render_batch(vm)
        self._render_hacienda(vm)
        self._render_doc_strip(vm)
        self._render_context_buttons(vm)
        self._btn_classify.configure(
            state="normal" if vm.btn_classify_enabled else "disabled",
            text=vm.btn_classify_text,
        )
        self._set_block_reason(vm.block_reason if not vm.btn_classify_enabled else "")
        self._render_previous(vm)

    def _render_batch(self, vm: SelectionVM):
        if vm.batch_count > 0:
            is_warning = vm.mode == "multi_mixed"
            self._batch_banner.configure(
                fg_color="#3d2a12" if is_warning else "#113831",
                border_color="#7c5b1a" if is_warning else "#1d5e53",
            )
            self._batch_title.configure(text=f"Lote: {vm.batch_count} factura(s)")
            self._batch_subtitle.configure(text=vm.batch_emisor or "Sin emisor")
            self._batch_banner.grid()
        else:
            self._batch_banner.grid_remove()

    def _render_hacienda(self, vm: SelectionVM):
        if vm.hacienda_text:
            self._hacienda_lbl.configure(
                text=vm.hacienda_text,
                text_color=vm.hacienda_color,
                fg_color=vm.hacienda_bg,
            )
            self._hacienda_lbl.grid()
        else:
            self._hacienda_lbl.grid_remove()

    def _render_doc_strip(self, vm: SelectionVM):
        if vm.mode == "single" and any((vm.doc_total, vm.doc_fecha, vm.doc_tipo)):
            self._doc_total_value.configure(text=vm.doc_total or "--")
            self._doc_fecha_value.configure(text=vm.doc_fecha or "--")
            self._doc_tipo_value.configure(text=vm.doc_tipo or "--")
            self._doc_strip.grid()
        else:
            self._doc_strip.grid_remove()

    def _render_context_buttons(self, vm: SelectionVM):
        self._hide_context_buttons()
        row = 0
        for widget, visible in (
            (self._btn_recover, vm.btn_recover_visible),
            (self._btn_link, vm.btn_link_visible),
            (self._btn_delete, vm.btn_delete_visible),
            (self._btn_create_pdf, vm.btn_create_pdf_visible),
            (self._btn_auto_classify, vm.btn_auto_classify_visible),
        ):
            if not visible:
                continue
            widget.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            widget.configure(state="normal")
            row += 1
        if row:
            self._actions_frame.grid()
        else:
            self._actions_frame.grid_remove()

    def _render_previous(self, vm: SelectionVM):
        if not vm.prev_frame_visible:
            self._prev_frame.grid_remove()
            self._set_prev_dest_path(None)
            return

        primary, secondary = self._split_previous_text(vm.prev_text)
        self._prev_primary_lbl.configure(text=primary or "--")
        self._prev_secondary_lbl.configure(text=secondary)
        self._set_prev_dest_path(vm.prev_dest_path)
        self._prev_frame.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 12))

    def _hide_context_buttons(self):
        for widget in (
            self._btn_recover,
            self._btn_link,
            self._btn_delete,
            self._btn_create_pdf,
            self._btn_auto_classify,
        ):
            widget.grid_remove()

    def _set_block_reason(self, reason: str):
        text = (reason or "").strip()
        self._block_reason_lbl.configure(text=text)
        if text:
            self._block_reason_lbl.grid()
        else:
            self._block_reason_lbl.grid_remove()

    def _set_prev_dest_path(self, path: Path | None):
        self._prev_dest_path = path
        if path is None:
            self._prev_path_lbl.config(text="", cursor="arrow")
            return
        self._prev_path_lbl.config(text=self._format_prev_dest_path(path), cursor="hand2")

    @staticmethod
    def _split_previous_text(text: str) -> tuple[str, str]:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if not lines:
            return "--", ""
        if len(lines) == 1:
            return lines[0], ""
        return lines[0], "\n".join(lines[1:])

    @staticmethod
    def _format_prev_dest_path(path: Path) -> str:
        try:
            parts = list(path.parts)
            cont_idx = next(i for i, part in enumerate(parts) if part == "Contabilidades")
            rel_parts = parts[cont_idx + 1:]
        except StopIteration:
            rel_parts = list(path.parts[-5:])

        if not rel_parts:
            return str(path)

        folders = rel_parts[:-1]
        file_name = rel_parts[-1]
        if len(folders) > 3:
            folders = [" / ".join(folders[:2]), " / ".join(folders[2:])]
        else:
            folders = [" / ".join(folders)] if folders else []

        lines = [line for line in folders if line]
        lines.append(file_name)
        return "\n".join(lines)

    def _ordered_categories(self, categories: list[str]) -> list[str]:
        normalized = {str(value).strip().upper() for value in categories if str(value).strip()}
        ordered = [category for category in _CATEGORY_ORDER if category in normalized]
        ordered.extend(sorted(value for value in normalized if value not in ordered))
        return ordered

    def _bind_category_shortcuts(self, *widgets):
        for widget in widgets:
            try:
                widget.bind("<Left>", lambda _e: self._step_category_from_key(-1), add="+")
                widget.bind("<Right>", lambda _e: self._step_category_from_key(1), add="+")
            except NotImplementedError:
                continue

    def _step_category_from_key(self, step: int):
        return "break" if self.step_category(step) else None

    def _on_category_changed(self, value=None):
        selected = (value or "").strip().upper()
        if selected:
            with self._suspend_callbacks():
                self._cat_var.set(selected)
                self._last_manual_category = selected
        self._apply_category_change(notify=True)

    def _apply_category_change(self, notify: bool):
        cat = self._forced_category or self._cat_var.get().strip().upper()
        mgr = self.catalog_mgr

        if cat == "GASTOS":
            tipos = mgr.subtipos("GASTOS") if mgr else []
            self._configure_tipo_values(tipos, self._forced_subtipo, disabled=bool(self._forced_subtipo))
            self._tipo_frame.grid()
            self._cuenta_frame.grid()
            self._prov_frame.grid()
            self._refresh_account_options(notify=False)
        elif cat == "OGND":
            tipos = mgr.subtipos("OGND") if mgr else _OGND_FALLBACK
            self._configure_tipo_values(tipos, self._forced_subtipo, disabled=bool(self._forced_subtipo))
            self._tipo_frame.grid()
            self._cuenta_frame.grid_remove()
            self._clear_accounts()
            self._prov_frame.grid_remove()
            with self._suspend_callbacks():
                self._prov_var.set("")
        elif cat in ("COMPRAS", "ACTIVO"):
            self._tipo_frame.grid_remove()
            with self._suspend_callbacks():
                self._tipo_var.set("")
            self._cuenta_frame.grid_remove()
            self._clear_accounts()
            self._prov_frame.grid()
        elif cat in ("INGRESOS", "SIN_RECEPTOR"):
            self._tipo_frame.grid_remove()
            with self._suspend_callbacks():
                self._tipo_var.set("")
                self._prov_var.set("")
            self._cuenta_frame.grid_remove()
            self._clear_accounts()
            self._prov_frame.grid_remove()
        else:
            self._tipo_frame.grid_remove()
            with self._suspend_callbacks():
                self._tipo_var.set("")
                self._prov_var.set("")
            self._cuenta_frame.grid_remove()
            self._clear_accounts()
            self._prov_frame.grid_remove()

        if notify:
            self._notify_form_change()

    def _on_subtipo_changed(self, _value=None):
        cat = self._forced_category or self._cat_var.get().strip().upper()
        if not self._forced_subtipo and cat in self._manual_subtipos:
            self._manual_subtipos[cat] = self._tipo_var.get().strip().upper()
        self._refresh_account_options(notify=True)

    def _configure_tipo_values(self, values: list[str], forced_value: str, disabled: bool):
        clean_values = [str(value).strip().upper() for value in values if str(value).strip()]
        selected = forced_value or self._tipo_var.get().strip().upper()
        if selected not in clean_values:
            cat = self._forced_category or self._cat_var.get().strip().upper()
            remembered = self._manual_subtipos.get(cat, "") if not forced_value else ""
            if remembered in clean_values:
                selected = remembered
            else:
                selected = clean_values[0] if clean_values else ""
        self._tipo_cb.configure(values=clean_values, state="disabled" if disabled else "readonly")
        with self._suspend_callbacks():
            self._tipo_var.set(selected)
        cat = self._forced_category or self._cat_var.get().strip().upper()
        if not forced_value and cat in self._manual_subtipos:
            self._manual_subtipos[cat] = selected

    def _remember_manual_selection(self):
        current_cat = self._cat_var.get().strip().upper()
        if current_cat in self._manual_categories or current_cat in _CATEGORY_ORDER:
            self._last_manual_category = current_cat

        current_tipo = self._tipo_var.get().strip().upper()
        if current_cat in self._manual_subtipos and current_tipo:
            self._manual_subtipos[current_cat] = current_tipo

    def _refresh_account_options(self, notify: bool):
        cat = self._forced_category or self._cat_var.get().strip().upper()
        subtipo = self._forced_subtipo or self._tipo_var.get().strip().upper()
        if cat == "GASTOS" and self.catalog_mgr:
            self._all_cuentas = self.catalog_mgr.cuentas("GASTOS", subtipo)
            self._apply_account_filter(reset_selection=True, notify=notify)
            self._cuenta_frame.grid()
            return

        self._clear_accounts()
        self._cuenta_frame.grid_remove()
        if notify:
            self._notify_form_change()

    def _clear_accounts(self):
        self._all_cuentas = []
        self._filtered_cuentas = []
        with self._suspend_callbacks():
            self._cuenta_search_var.set("")
            self._cuenta_var.set("")
        self._rebuild_account_rows()

    def _on_search_changed(self, *_args):
        self._apply_account_filter(reset_selection=False, notify=True)

    def _apply_account_filter(self, reset_selection: bool, notify: bool):
        search_term = self._cuenta_search_var.get().strip().lower()
        if search_term:
            filtered = [cuenta for cuenta in self._all_cuentas if search_term in cuenta.lower()]
        else:
            filtered = list(self._all_cuentas)

        current = self._cuenta_var.get().strip()
        if reset_selection or current not in filtered:
            selected = filtered[0] if filtered else ""
        else:
            selected = current

        changed = selected != current
        with self._suspend_callbacks():
            self._filtered_cuentas = filtered
            self._cuenta_var.set(selected)
        self._rebuild_account_rows()

        if notify and (changed or reset_selection):
            self._notify_form_change()

    def _rebuild_account_rows(self):
        for widget in self._cuenta_list.winfo_children():
            widget.destroy()

        count = len(self._filtered_cuentas)
        self._cuenta_results_lbl.configure(
            text=f"{count} cuenta(s)" if count else "Sin cuentas que coincidan con la búsqueda"
        )

        if not self._filtered_cuentas:
            empty = ctk.CTkLabel(
                self._cuenta_list,
                text="No hay cuentas para mostrar.",
                font=_font(11),
                text_color=MUTED,
                anchor="w",
            )
            empty.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
            self._bind_category_shortcuts(empty)
            return

        for index, cuenta in enumerate(self._filtered_cuentas):
            selected = cuenta == self._cuenta_var.get().strip()
            row = ctk.CTkButton(
                self._cuenta_list,
                text=cuenta,
                anchor="w",
                fg_color="#153b36" if selected else "transparent",
                hover_color="#214842" if selected else SURFACE,
                text_color=TEAL if selected else TEXT,
                corner_radius=8,
                height=30,
                border_width=1 if selected else 0,
                border_color=TEAL if selected else SURFACE,
                command=lambda value=cuenta: self._select_account(value, notify=True),
            )
            row.grid(row=index, column=0, sticky="ew", padx=6, pady=2)
            self._bind_category_shortcuts(row)

    def _select_account(self, cuenta: str, notify: bool):
        selected = (cuenta or "").strip()
        if not selected:
            return
        changed = selected != self._cuenta_var.get().strip()
        with self._suspend_callbacks():
            self._cuenta_var.set(selected)
        self._rebuild_account_rows()
        if notify and changed:
            self._notify_form_change()

    def _move_account_selection(self, step: int):
        if not self._filtered_cuentas:
            return "break"
        current = self._cuenta_var.get().strip()
        if current in self._filtered_cuentas:
            index = self._filtered_cuentas.index(current)
        else:
            index = 0
        index = max(0, min(len(self._filtered_cuentas) - 1, index + step))
        self._select_account(self._filtered_cuentas[index], notify=True)
        return "break"

    def _confirm_account_selection(self):
        if self._filtered_cuentas:
            current = self._cuenta_var.get().strip() or self._filtered_cuentas[0]
            self._select_account(current, notify=True)
        return "break"

    def _clear_account_search(self):
        with self._suspend_callbacks():
            self._cuenta_search_var.set("")
        self._apply_account_filter(reset_selection=False, notify=False)
        return "break"
