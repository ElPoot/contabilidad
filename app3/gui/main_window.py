from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from app3.config import metadata_dir
from app3.core.catalog import CatalogManager
from app3.core.classifier import ClassificationDB, classify_record
from app3.core.factura_index import FacturaIndexer
from app3.core.models import FacturaRecord
from app3.core.session import resolve_client_session


class App3Window(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("App 3 - Clasificador Contable (v1)")
        self.geometry("1200x720")

        self.session = None
        self.records: list[FacturaRecord] = []
        self.selected: FacturaRecord | None = None

        self._build()

    def _build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Label(top, text="Cédula:").pack(side="left")
        self.cedula_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.cedula_var, width=20).pack(side="left", padx=6)
        ttk.Button(top, text="Cargar cliente", command=self.load_client).pack(side="left", padx=6)
        ttk.Label(top, text="Desde (DD/MM/AAAA):").pack(side="left", padx=(14, 4))
        self.from_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.from_var, width=12).pack(side="left")
        ttk.Label(top, text="Hasta:").pack(side="left", padx=(10, 4))
        self.to_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.to_var, width=12).pack(side="left")

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=1)

        cols = ("estado", "fecha", "emisor", "total", "clave")
        self.tree = ttk.Treeview(left, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=c.upper())
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        self.info_var = tk.StringVar(value="Selecciona una factura")
        ttk.Label(right, textvariable=self.info_var, wraplength=500, justify="left").pack(anchor="w", pady=(0, 10))

        ttk.Label(right, text="Categoría").pack(anchor="w")
        self.categoria_var = tk.StringVar(value="COMPRAS")
        self.categoria_cb = ttk.Combobox(right, textvariable=self.categoria_var, values=["COMPRAS", "GASTOS", "INGRESOS"], state="readonly")
        self.categoria_cb.pack(fill="x", pady=3)

        ttk.Label(right, text="Subcategoría").pack(anchor="w")
        self.subcategoria_var = tk.StringVar(value="COMPRAS DE CONTADO")
        ttk.Entry(right, textvariable=self.subcategoria_var).pack(fill="x", pady=3)

        ttk.Label(right, text="Proveedor").pack(anchor="w")
        self.proveedor_var = tk.StringVar()
        ttk.Entry(right, textvariable=self.proveedor_var).pack(fill="x", pady=3)

        ttk.Button(right, text="Clasificar", command=self.classify_selected).pack(anchor="w", pady=8)

    def load_client(self) -> None:
        try:
            self.session = resolve_client_session(self.cedula_var.get())
            mdir = metadata_dir(self.session.folder)
            catalog = CatalogManager(mdir).load()
            self.categoria_cb.configure(values=sorted(catalog.keys()))

            self.db = ClassificationDB(mdir)
            self.records = FacturaIndexer().load_period(
                self.session.folder,
                from_date=self.from_var.get(),
                to_date=self.to_var.get(),
            )
            self.refresh_tree()
            messagebox.showinfo("Sesión", f"Cliente cargado: {self.session.folder.name}\nFacturas: {len(self.records)}")
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for idx, r in enumerate(self.records):
            estado = self.db.get_estado(r.clave) or r.estado
            self.tree.insert("", "end", iid=str(idx), values=(estado, r.fecha_emision, r.emisor_nombre, r.total_comprobante, r.clave))

    def on_select(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        idx = int(selected[0])
        self.selected = self.records[idx]
        proveedor = self.selected.emisor_nombre or "PROVEEDOR"
        self.proveedor_var.set(proveedor)
        self.info_var.set(
            f"Clave: {self.selected.clave}\n"
            f"XML: {self.selected.xml_path or 'N/A'}\n"
            f"PDF: {self.selected.pdf_path or 'N/A'}\n"
            f"Tipo: {self.selected.tipo_documento}\n"
        )

    def classify_selected(self) -> None:
        if not self.session or not self.selected:
            messagebox.showwarning("Atención", "Carga cliente y selecciona una factura")
            return
        try:
            classify_record(
                self.selected,
                self.session.folder,
                self.db,
                self.categoria_var.get().strip().upper(),
                self.subcategoria_var.get().strip().upper(),
                self.proveedor_var.get().strip().upper(),
            )
            self.refresh_tree()
            messagebox.showinfo("Listo", "Factura clasificada")
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
