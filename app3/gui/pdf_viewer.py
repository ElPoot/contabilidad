from __future__ import annotations

import io
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Optional

import customtkinter as ctk

try:
    import fitz  # pymupdf >= 1.24
    PYMUPDF_OK = True
except ImportError:
    PYMUPDF_OK = False

# Paleta — misma que el resto de la app
BG      = "#0d0f14"
SURFACE = "#13161e"
CARD    = "#181c26"
BORDER  = "#252a38"
TEAL    = "#2dd4bf"
TEXT    = "#e8eaf0"
MUTED   = "#6b7280"
CANVAS_BG = "#0a0c10"


class PDFViewer(ctk.CTkFrame):
    """
    Visor de PDF completo integrado con la paleta oscura de App 3.

    Controles:
      - Rueda del mouse          → scroll vertical
      - Ctrl + rueda del mouse   → zoom in/out
      - Botones ◀ ▶              → navegar páginas
      - Botones − +              → zoom manual
      - Botón ↺                  → fit-to-width (ajuste automático al ancho)
      - Clic derecho sobre texto → copia la palabra al portapapeles (modo texto)
    """

    # Zoom libre en pasos de 10% entre 30% y 300%
    ZOOM_MIN   = 0.30
    ZOOM_MAX   = 3.00
    ZOOM_STEP  = 0.10

    def __init__(self, parent, **kwargs):
        kwargs.pop("bg", None)
        kwargs.pop("background", None)
        kwargs.setdefault("fg_color", BG)
        kwargs.setdefault("corner_radius", 0)
        super().__init__(parent, **kwargs)

        self._doc:         Optional[fitz.Document] = None
        self._page_index:  int   = 0
        self._zoom:        float = 1.0      # zoom actual (float libre)
        self._fit_zoom:    float = 1.0      # zoom calculado por fit-to-width
        self._tk_image            = None    # referencia anti-GC
        self._text_blocks: list   = []      # bloques de texto de la página actual

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_toolbar()
        self._build_canvas()

        # Registrar estilos TTK para scrollbars oscuras (idempotente)
        _apply_scrollbar_style()

        self._show_placeholder("Sin documento cargado")

    # ── TOOLBAR ───────────────────────────────────────────────────────────────
    def _build_toolbar(self):
        bar = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10, height=44)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)

        btn = dict(
            fg_color=CARD, hover_color=BORDER,
            text_color=TEXT, corner_radius=7,
            width=30, height=26,
            font=ctk.CTkFont(family="Segoe UI", size=13),
        )
        lbl = dict(
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=MUTED,
        )

        # ── Navegación
        self._btn_prev = ctk.CTkButton(bar, text="◀", state="disabled",
                                        command=self._prev_page, **btn)
        self._btn_prev.pack(side="left", padx=(10, 2), pady=9)

        self._lbl_page = ctk.CTkLabel(bar, text="—", width=78, anchor="center", **lbl)
        self._lbl_page.pack(side="left", padx=2)

        self._btn_next = ctk.CTkButton(bar, text="▶", state="disabled",
                                        command=self._next_page, **btn)
        self._btn_next.pack(side="left", padx=(2, 10))

        # Separador
        ctk.CTkFrame(bar, fg_color=BORDER, width=1).pack(side="left", fill="y", pady=9)

        # ── Zoom
        ctk.CTkButton(bar, text="−", command=self._zoom_out, **btn).pack(
            side="left", padx=(10, 2), pady=9)

        self._lbl_zoom = ctk.CTkLabel(bar, text="—", width=52, anchor="center", **lbl)
        self._lbl_zoom.pack(side="left", padx=2)

        ctk.CTkButton(bar, text="+", command=self._zoom_in, **btn).pack(
            side="left", padx=(2, 4), pady=9)

        # Fit-to-width (↺ = reset al ajuste automático)
        self._btn_fit = ctk.CTkButton(bar, text="↺", command=self._zoom_fit_width,
                                       **btn)
        self._btn_fit.pack(side="left", padx=(0, 10), pady=9)

        # Separador
        ctk.CTkFrame(bar, fg_color=BORDER, width=1).pack(side="left", fill="y", pady=9)

        # Hint Ctrl+scroll
        ctk.CTkLabel(bar, text="Ctrl+scroll = zoom",
                      font=ctk.CTkFont(family="Segoe UI", size=10),
                      text_color="#3a4055").pack(side="left", padx=10)

    # ── CANVAS ────────────────────────────────────────────────────────────────
    def _build_canvas(self):
        container = tk.Frame(self, bg=CANVAS_BG, bd=0, highlightthickness=0)
        container.grid(row=1, column=0, sticky="nsew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(
            container,
            bg=CANVAS_BG,
            highlightthickness=0,
            bd=0,
            cursor="crosshair",
        )
        self._canvas.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(container, orient="vertical",
                             command=self._canvas.yview,
                             style="PDF.Vertical.TScrollbar")
        vsb.grid(row=0, column=1, sticky="ns")

        hsb = ttk.Scrollbar(container, orient="horizontal",
                             command=self._canvas.xview,
                             style="PDF.Horizontal.TScrollbar")
        hsb.grid(row=1, column=0, sticky="ew")

        self._canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # ── Bindings
        # Scroll vertical normal
        # Fit-to-width cuando el canvas se hace visible por primera vez
        self._canvas.bind("<Map>", self._on_canvas_mapped)

        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind("<Button-4>",   self._on_mousewheel)
        self._canvas.bind("<Button-5>",   self._on_mousewheel)

        # Ctrl+scroll = zoom
        self._canvas.bind("<Control-MouseWheel>", self._on_ctrl_scroll)
        self._canvas.bind("<Control-Button-4>",   self._on_ctrl_scroll)
        self._canvas.bind("<Control-Button-5>",   self._on_ctrl_scroll)

        # Clic derecho = copiar texto bajo el cursor
        self._canvas.bind("<Button-3>", self._on_right_click)

        # Resize → recalcular fit-to-width
        self._canvas.bind("<Configure>", self._on_canvas_resize)

    # ── API PÚBLICA ───────────────────────────────────────────────────────────
    def load(self, pdf_path: Path) -> None:
        self._close_doc()
        # Reiniciar viewport antes de abrir un nuevo documento para evitar
        # que Tk conserve desplazamientos previos al cambiar de factura.
        self._canvas.delete("all")
        self._canvas.configure(scrollregion=(0, 0, 1, 1))
        self._canvas.xview_moveto(0)
        self._canvas.yview_moveto(0)
        if not PYMUPDF_OK:
            self._show_placeholder("pymupdf no está instalado.\n\npip install pymupdf")
            return
        if not pdf_path or not pdf_path.exists():
            self._show_placeholder(f"PDF no encontrado:\n{pdf_path}")
            return
        try:
            self._doc = fitz.open(str(pdf_path))
        except Exception as exc:
            self._show_placeholder(f"No se pudo abrir el PDF:\n{exc}")
            return

        self._page_index = 0
        # Calcular fit-to-width después de que el canvas tenga tamaño real
        # Renderizar si el canvas ya tiene tamaño; si no, <Map> lo hará
        self._canvas.update_idletasks()
        if self._canvas.winfo_width() > 50:
            self._recalc_fit_and_render()

    def clear(self) -> None:
        self._close_doc()
        self._show_placeholder("Sin documento cargado")

    def release_file_handles(self, message: str = "") -> None:
        """Cierra el documento actual para liberar locks en Windows."""
        self._close_doc()
        if message:
            self._show_placeholder(message)

    # ── NAVEGACIÓN ────────────────────────────────────────────────────────────
    def _prev_page(self):
        if self._doc and self._page_index > 0:
            self._page_index -= 1
            self._render_page()

    def _next_page(self):
        if self._doc and self._page_index < len(self._doc) - 1:
            self._page_index += 1
            self._render_page()

    # ── ZOOM ──────────────────────────────────────────────────────────────────
    def _zoom_in(self):
        self._set_zoom(self._zoom + self.ZOOM_STEP)

    def _zoom_out(self):
        self._set_zoom(self._zoom - self.ZOOM_STEP)

    def _zoom_fit_width(self):
        """Ajusta zoom para que la página llene exactamente el ancho del canvas."""
        self._recalc_fit_and_render()

    def _set_zoom(self, value: float):
        self._zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, round(value, 2)))
        self._render_page()

    # ── EVENTOS ───────────────────────────────────────────────────────────────
    def _on_mousewheel(self, event: tk.Event):
        # Ctrl presionado → delegar a zoom
        if event.state & 0x0004:
            self._on_ctrl_scroll(event)
            return
        if event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self._canvas.yview_scroll(delta * 3, "units")

    def _on_ctrl_scroll(self, event: tk.Event):
        if event.num == 4:
            delta = 1
        elif event.num == 5:
            delta = -1
        else:
            delta = 1 if event.delta > 0 else -1
        self._set_zoom(self._zoom + delta * self.ZOOM_STEP)

    def _on_right_click(self, event: tk.Event):
        """Copia la línea de texto bajo el cursor al portapapeles."""
        if not self._text_blocks:
            return
        cx = self._canvas.canvasx(event.x)
        cy = self._canvas.canvasy(event.y)
        pad = 20
        zoom = self._zoom

        # Encontrar la palabra bajo el cursor
        hit_block = None
        hit_line  = None
        best_dist = float("inf")

        for word in self._text_blocks:
            x0, y0, x1, y1, text, block_no, line_no, *_ = word
            bx0 = x0 * zoom + pad
            by0 = y0 * zoom + pad
            bx1 = x1 * zoom + pad
            by1 = y1 * zoom + pad
            # Hit exacto dentro del bbox de la palabra
            if bx0 <= cx <= bx1 and by0 <= cy <= by1:
                hit_block = block_no
                hit_line  = line_no
                break
            # Fallback: palabra más cercana
            bcx = (bx0 + bx1) / 2
            bcy = (by0 + by1) / 2
            dist = ((cx - bcx) ** 2 + (cy - bcy) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                hit_block = block_no
                hit_line  = line_no

        if hit_block is None:
            return

        # Juntar todas las palabras de la misma línea en orden
        line_words = [
            w for w in self._text_blocks
            if w[5] == hit_block and w[6] == hit_line
        ]
        line_words.sort(key=lambda w: w[0])  # ordenar por x0
        line_text = " ".join(w[4] for w in line_words).strip()

        if line_text:
            self.clipboard_clear()
            self.clipboard_append(line_text)

    def _on_canvas_resize(self, event: tk.Event):
        """Al redimensionar, recalcular fit si seguimos en modo fit."""
        if not self._doc:
            return
        if abs(self._zoom - self._fit_zoom) < 0.05:
            if hasattr(self, '_resize_id'):
                try: self.after_cancel(self._resize_id)
                except: pass
            self._resize_id = self.after(60, self._recalc_fit_and_render)

    # ── RENDERIZADO ───────────────────────────────────────────────────────────
    def _on_canvas_mapped(self, _event=None):
        """Se dispara cuando el canvas se hace visible.
        El tamaño puede no ser definitivo aún — _on_canvas_resize lo manejará.
        """
        if self._doc:
            # Intentar renderizar; si el tamaño no está listo, Configure lo reintentará
            self.after(30, self._recalc_fit_and_render)

    def _recalc_fit_and_render(self):
        """Calcula zoom fit-to-width y renderiza."""
        if not self._doc:
            return
        self._canvas.update_idletasks()
        w = self._canvas.winfo_width()
        if w < 50:
            self.after(60, self._recalc_fit_and_render)
            return
        page_w = self._doc[self._page_index].rect.width
        pad = 40
        self._fit_zoom = max(self.ZOOM_MIN, (w - pad) / page_w)
        self._zoom = self._fit_zoom
        self._render_page()

    def _render_page(self):
        if not self._doc:
            return

        zoom = self._zoom
        page = self._doc[self._page_index]
        mat  = fitz.Matrix(zoom, zoom)
        pix  = page.get_pixmap(matrix=mat, alpha=False)

        # Guardar bloques de texto para copia con clic derecho
        self._text_blocks = page.get_text("words")  # (x0,y0,x1,y1,word,block,line,word_idx)

        # PPM → PhotoImage
        self._tk_image = tk.PhotoImage(data=pix.tobytes("ppm"))

        hpad = 16   # solo padding horizontal
        vpad = 0    # evitar hueco superior perceptible
        w, h = pix.width, pix.height
        self._canvas.configure(scrollregion=(0, 0, w + hpad * 2, h + vpad * 2))
        self._canvas.delete("all")

        # Sombra sutil
        self._canvas.create_rectangle(
            hpad + 3, vpad + 3, w + hpad + 3, h + vpad + 3,
            fill="#060809", outline="",
        )
        # Página — empieza casi en y=0
        self._canvas.create_image(hpad, vpad, anchor="nw", image=self._tk_image)
        # Forzar scroll al inicio cada vez que se carga una página
        self._canvas.xview_moveto(0)
        self._canvas.yview_moveto(0)
        # Asegurar que no queden offsets de scroll aplicados asincrónicamente.
        self._canvas.after_idle(lambda: self._canvas.yview_moveto(0))

        # Actualizar toolbar
        total = len(self._doc)
        self._lbl_page.configure(text=f"Pág {self._page_index + 1} / {total}")
        self._lbl_zoom.configure(text=f"{int(zoom * 100)}%")
        self._btn_prev.configure(state="normal" if self._page_index > 0 else "disabled")
        self._btn_next.configure(
            state="normal" if self._page_index < total - 1 else "disabled")

    def _show_placeholder(self, message: str):
        self._canvas.delete("all")
        self._canvas.configure(scrollregion=(0, 0, 100, 100))
        self._canvas.update_idletasks()
        cx = max(self._canvas.winfo_width()  // 2, 200)
        cy = max(self._canvas.winfo_height() // 2, 150)
        self._canvas.create_text(
            cx, cy, text=message, fill=MUTED,
            font=("Segoe UI", 12), justify="center", anchor="center",
        )
        self._lbl_page.configure(text="—")
        self._lbl_zoom.configure(text="—")
        self._btn_prev.configure(state="disabled")
        self._btn_next.configure(state="disabled")

    # ── LIMPIEZA ──────────────────────────────────────────────────────────────
    def _close_doc(self):
        if self._doc:
            try:
                self._doc.close()
            except Exception:
                pass
            self._doc = None
        self._tk_image   = None
        self._text_blocks = []


# ── Estilos TTK para scrollbars (se aplica una sola vez) ─────────────────────
_SCROLLBAR_STYLE_APPLIED = False

def _apply_scrollbar_style():
    global _SCROLLBAR_STYLE_APPLIED
    if _SCROLLBAR_STYLE_APPLIED:
        return
    style = ttk.Style()
    for name in ("PDF.Vertical.TScrollbar", "PDF.Horizontal.TScrollbar"):
        style.configure(name,
                         background=SURFACE, troughcolor=BG,
                         borderwidth=0, arrowsize=12,
                         relief="flat")
    _SCROLLBAR_STYLE_APPLIED = True
