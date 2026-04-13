"""Modal para recuperar PDFs huérfanos u inconsistentes."""

import threading
from pathlib import Path
from tkinter import ttk

import customtkinter as ctk
from gestor_contable.gui.fonts import *

from gestor_contable.core.classifier import (
    ClassificationDB,
    adopt_orphaned_pdf,
    recover_orphaned_pdf,
)
from gestor_contable.core.classification_utils import find_orphaned_pdfs
from gestor_contable.gui.icons import get_icon
from gestor_contable.gui.modal_overlay import ModalOverlay

# ── PALETA ─────────────────────────────────────────────────────────────────
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


class OrphanedPDFsModal(ctk.CTkToplevel):
    """Modal para detectar y recuperar PDFs huérfanos."""

    def __init__(
        self,
        parent,
        session_folder: Path,
        db: ClassificationDB,
        db_records: dict,
    ):
        super().__init__(parent)
        self.title("Recuperar PDFs Huérfanos")
        self.geometry("900x600")
        self.resizable(True, True)
        self.configure(fg_color=BG)

        self.session_folder = session_folder
        self.db = db
        self.db_records = db_records
        self.orphaned = []
        self.selected_indices = set()

        self._build_ui()
        self._start_scan()

    def _build_ui(self):
        """Construye la interfaz."""
        # Header
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0)
        header.pack(fill="x")

        ctk.CTkLabel(
            header,
            text="Escanear PDFs Inconsistentes",
            image=get_icon("filter", 18),
            compound="left",
            font=F_BUTTON_LG(),
            text_color=TEXT,
        ).pack(side="left", padx=16, pady=12)

        ctk.CTkLabel(
            header,
            text="(Se buscan archivos que no están en la ubicación correcta)",
            font=F_LABEL(),
            text_color=MUTED,
        ).pack(side="left", padx=0, pady=12)

        # Body
        body = ctk.CTkFrame(self, fg_color=BG)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        # Status label
        self._status_label = ctk.CTkLabel(
            body, text="Escaneando PDFs...", font=F_BODY(), text_color=TEAL
        )
        self._status_label.grid(row=0, column=0, sticky="ew", pady=(0, 12))

        # Treeview con scroll
        tree_frame = ctk.CTkFrame(body, fg_color=CARD, corner_radius=8)
        tree_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 12))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Treeview
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=CARD, foreground=TEXT, fieldbackground=CARD)
        style.configure("Treeview.Heading", background=SURFACE, foreground=TEXT)

        self._tree = ttk.Treeview(
            tree_frame,
            columns=("clave", "motivo", "ruta_actual"),
            height=15,
            yscrollcommand=scrollbar.set,
            selectmode="extended",
        )
        scrollbar.configure(command=self._tree.yview)

        self._tree.column("#0", width=0, stretch=False)
        self._tree.column("clave", anchor="w", width=180)
        self._tree.column("motivo", anchor="w", width=180)
        self._tree.column("ruta_actual", anchor="w", width=500)

        self._tree.heading("#0", text="")
        self._tree.heading("clave", text="Clave")
        self._tree.heading("motivo", text="Motivo")
        self._tree.heading("ruta_actual", text="Ubicación Actual")

        self._tree.grid(row=0, column=0, sticky="nsew")

        # Footer con botones
        footer = ctk.CTkFrame(body, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)

        # Info
        self._info_label = ctk.CTkLabel(
            footer,
            text="",
            font=F_LABEL(),
            text_color=MUTED,
        )
        self._info_label.grid(row=0, column=0, sticky="w")

        # Botones
        button_frame = ctk.CTkFrame(footer, fg_color="transparent")
        button_frame.grid(row=0, column=1, sticky="e")

        ctk.CTkButton(
            button_frame,
            text="Seleccionar Todo",
            width=120,
            height=32,
            fg_color=SURFACE,
            hover_color=BORDER,
            text_color=TEXT,
            font=F_LABEL(),
            corner_radius=8,
            command=self._select_all,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            button_frame,
            text="Deseleccionar Todo",
            width=120,
            height=32,
            fg_color=SURFACE,
            hover_color=BORDER,
            text_color=TEXT,
            font=F_LABEL(),
            corner_radius=8,
            command=self._deselect_all,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            button_frame,
            text="Recuperar",
            width=120,
            height=32,
            fg_color=SUCCESS,
            hover_color="#2ecc71",
            text_color="#0d1a18",
            font=F_LABEL_BOLD(),
            corner_radius=8,
            command=self._recover_selected,
        ).pack(side="left")

    def _start_scan(self):
        """Inicia el scan de PDFs huérfanos en un thread."""

        def worker():
            try:
                # Ruta a Contabilidades
                pf_root = self.session_folder.parent.parent
                contabilidades_root = pf_root / "Contabilidades"

                self.orphaned = find_orphaned_pdfs(
                    contabilidades_root, self.db_records,
                    client_name=self.session_folder.name,
                )

                # Actualizar UI
                self.after(0, self._display_results)
            except Exception as e:
                self.after(0, lambda error=e: self._show_error("Error en escaneo", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def _display_results(self):
        """Muestra los resultados en el Treeview."""
        # Limpiar árbol
        for item in self._tree.get_children():
            self._tree.delete(item)

        # Agregar resultados
        motivo_labels = {
            "not_in_db": "Sin registro en BD",
            "wrong_location": "Ubicación incorrecta",
            "duplicado": "Duplicado (en ambas ubicaciones)",
            "huerfano_sin_destino": "Huérfano (reclasificación falló)",
        }

        for i, orphaned_info in enumerate(self.orphaned):
            clave = orphaned_info.get("clave", "?")
            motivo = orphaned_info.get("motivo", "desconocido")
            ruta = orphaned_info.get("ruta_actual", "?")

            motivo_label = motivo_labels.get(motivo, motivo)

            self._tree.insert(
                "",
                "end",
                iid=i,
                values=(clave, motivo_label, ruta),
            )

        # Actualizar status
        count = len(self.orphaned)
        if count == 0:
            self._status_label.configure(
                text="No se encontraron PDFs inconsistentes", text_color=SUCCESS
            )
            self._info_label.configure(text="")
        else:
            self._status_label.configure(
                text=f"Encontrados {count} PDF(s) inconsistente(s)", text_color=WARNING
            )
            self._info_label.configure(text=f"Selecciona los PDFs a recuperar y haz clic en 'Recuperar'")

    def _select_all(self):
        """Selecciona todos los items."""
        for item in self._tree.get_children():
            self._tree.selection_add(item)

    def _deselect_all(self):
        """Deselecciona todos los items."""
        self._tree.selection_remove(self._tree.selection())

    def _recover_selected(self):
        """Recupera los PDFs seleccionados."""
        selected = self._tree.selection()
        if not selected:
            ModalOverlay.show_warning(self, "Advertencia", "Selecciona al menos un PDF para recuperar")
            return

        count = len(selected)

        def _do_recovery():
            def worker():
                recovered = 0
                failed = 0
                recovered_ids = []

                for item_id in selected:
                    idx = int(item_id)
                    if idx < len(self.orphaned):
                        orphaned_info = self.orphaned[idx]
                        motivo = orphaned_info.get("motivo", "")
                        if motivo == "not_in_db":
                            ok = adopt_orphaned_pdf(orphaned_info, self.db)
                        else:
                            ok = recover_orphaned_pdf(orphaned_info, self.db)
                        if ok:
                            recovered += 1
                            recovered_ids.append(item_id)
                        else:
                            failed += 1

                # Actualizar UI
                self.after(
                    0,
                    lambda: self._show_recovery_result(recovered, failed, count, recovered_ids),
                )

            threading.Thread(target=worker, daemon=True).start()

        ModalOverlay.show_confirm(
            self,
            "Confirmar recuperación",
            (
                f"¿Procesar {count} PDF(s)?\n\n"
                "Los PDFs con destino conocido se moverán a su ubicación correcta.\n"
                "Los PDFs sin registro en BD se adoptarán en su ubicación actual."
            ),
            on_yes=_do_recovery,
            confirm_text="Procesar",
        )

    def _show_recovery_result(self, recovered: int, failed: int, total: int, recovered_ids: list):
        """Muestra el resultado de la recuperación."""
        message = f"Recuperados: {recovered}\nFallidos: {failed}\nTotal: {total}"

        if failed == 0:
            ModalOverlay.show_success(self, "Recuperación completada", message)
            self._status_label.configure(
                text=f"{recovered} PDF(s) recuperados exitosamente", text_color=SUCCESS
            )
        else:
            ModalOverlay.show_warning(self, "Recuperación parcial", message)
            self._status_label.configure(
                text=f"{recovered} recuperados, {failed} fallaron", text_color=WARNING
            )

        # Borrar solo los ítems recuperados con éxito; los fallidos permanecen visibles
        for item_id in recovered_ids:
            if self._tree.exists(item_id):
                self._tree.delete(item_id)

    def _show_error(self, title: str, message: str):
        ModalOverlay.show_error(self, title, message)
        self._status_label.configure(text=f"Error: {message}", text_color=DANGER)
