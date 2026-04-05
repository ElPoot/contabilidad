from __future__ import annotations

import io
import logging
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Optional

import customtkinter as ctk
from gestor_contable.gui.fonts import *

from gestor_contable.config import is_onedrive_placeholder

try:
    import fitz  # pymupdf >= 1.24
    PYMUPDF_OK = True
except ImportError:
    PYMUPDF_OK = False

# Paleta -- misma que el resto de la app
BG      = "#0d0f14"
SURFACE = "#13161e"
CARD    = "#181c26"
BORDER  = "#252a38"
TEAL    = "#2dd4bf"
TEXT    = "#e8eaf0"
MUTED   = "#6b7280"
CANVAS_BG = "#0a0c10"

logger = logging.getLogger(__name__)


class PDFViewer(ctk.CTkFrame):
    """
    Visor de PDF completo integrado con la paleta oscura de App 3.

    Controles:
      - Rueda del mouse          -> scroll vertical
      - Ctrl + rueda del mouse   -> zoom in/out
      - Botones ◀ ▶              -> navegar páginas
      - Botones − +              -> zoom manual
      - Botón ↺                  -> fit-to-width (ajuste automático al ancho)
      - Click izquierdo + arrastrar -> seleccionar texto (highlight azul)
      - Soltar selección         -> auto-copia al portapapeles
      - Ctrl+C                   -> copia la selección actual
      - Clic derecho sobre texto -> copia la línea completa al portapapeles
    """

    # Zoom libre en pasos de 10% entre 30% y 300%
    ZOOM_MIN   = 0.30
    ZOOM_MAX   = 3.00
    ZOOM_STEP  = 0.10

    # Padding del canvas (debe coincidir con valores en _render_page)
    _HPAD = 16   # padding horizontal
    _VPAD = 0    # padding vertical

    # Tamaño de celda para índice espacial (PDF points)
    _CELL_SIZE = 50

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
        self._spatial_grid: dict  = {}      # índice espacial: (col, row) → list[int]

        # Variables para selección de texto con drag
        self._sel_start:  Optional[tuple] = None  # (cx, cy) canvas — inicio del drag
        self._sel_end:    Optional[tuple] = None  # (cx, cy) canvas — posición actual drag
        self._sel_ids:    list = []               # IDs de canvas.create_rectangle() del highlight
        self._sel_text:   str  = ""              # texto seleccionado actual (para Ctrl+C)
        self._drag_pending: bool = False          # throttle para drag (~60fps)

        self._build_toolbar()
        self._build_canvas()

        # Registrar estilos TTK para scrollbars oscuras (idempotente)
        _apply_scrollbar_style()

        self._show_placeholder("Sin documento cargado")

    # ── TOOLBAR ───────────────────────────────────────────────────────────────
    def _build_toolbar(self):
        # Barra que ocupa todo el ancho, height fijo
        bar = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=26)
        bar.pack(side="top", fill="x", padx=0, pady=0)
        bar.pack_propagate(False)

        # Frame interno para empacar los elementos sin que se expandan
        inner = ctk.CTkFrame(bar, fg_color=SURFACE, corner_radius=0)
        inner.pack(side="left", fill="y", padx=0, pady=0)

        btn = dict(
            fg_color=CARD, hover_color=BORDER,
            text_color=TEXT, corner_radius=7,
            width=30, height=16,
            font=F_BODY(),
        )
        lbl = dict(
            font=F_LABEL(),
            text_color=MUTED,
        )

        # ── Navegación
        self._btn_prev = ctk.CTkButton(inner, text="◀", state="disabled",
                                        command=self._prev_page, **btn)
        self._btn_prev.pack(side="left", padx=(6, 2), pady=0)

        self._lbl_page = ctk.CTkLabel(inner, text="--", width=78, anchor="center", **lbl)
        self._lbl_page.pack(side="left", padx=2)

        self._btn_next = ctk.CTkButton(inner, text="▶", state="disabled",
                                        command=self._next_page, **btn)
        self._btn_next.pack(side="left", padx=(2, 6), pady=0)

        # Separador
        ctk.CTkFrame(inner, fg_color=BORDER, width=1).pack(side="left", fill="y", pady=0)

        # ── Zoom
        ctk.CTkButton(inner, text="−", command=self._zoom_out, **btn).pack(
            side="left", padx=(6, 2), pady=0)

        self._lbl_zoom = ctk.CTkLabel(inner, text="--", width=52, anchor="center", **lbl)
        self._lbl_zoom.pack(side="left", padx=2)

        ctk.CTkButton(inner, text="+", command=self._zoom_in, **btn).pack(
            side="left", padx=(2, 2), pady=0)

        # Fit-to-width (↺ = reset al ajuste automático)
        self._btn_fit = ctk.CTkButton(inner, text="↺", command=self._zoom_fit_width,
                                       **btn)
        self._btn_fit.pack(side="left", padx=(2, 6), pady=0)

        # Separador
        ctk.CTkFrame(inner, fg_color=BORDER, width=1).pack(side="left", fill="y", pady=0, padx=2)

        # Hint Ctrl+scroll
        ctk.CTkLabel(inner, text="Ctrl+scroll = zoom",
                      font=F_MICRO(),
                      text_color="#3a4055").pack(side="left", padx=4, pady=0)

    # ── CANVAS ────────────────────────────────────────────────────────────────
    def _build_canvas(self):
        # Contenedor que se expande para llenar espacio disponible
        container = tk.Frame(self, bg=CANVAS_BG, bd=0, highlightthickness=0)
        container.pack(side="top", fill="both", expand=True, padx=0, pady=0)

        self._canvas = tk.Canvas(
            container,
            bg=CANVAS_BG,
            highlightthickness=0,
            bd=0,
            cursor="xterm",
        )
        self._canvas.pack(side="left", fill="both", expand=True)

        vsb = ttk.Scrollbar(container, orient="vertical",
                             command=self._canvas.yview,
                             style="PDF.Vertical.TScrollbar")
        vsb.pack(side="right", fill="y")

        hsb = ttk.Scrollbar(container, orient="horizontal",
                             command=self._canvas.xview,
                             style="PDF.Horizontal.TScrollbar")
        hsb.pack(side="bottom", fill="x")

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

        # Selección de texto con drag
        self._canvas.bind("<Button-1>",         self._on_sel_start)
        self._canvas.bind("<B1-Motion>",        self._on_sel_drag)
        self._canvas.bind("<ButtonRelease-1>",  self._on_sel_end)
        self._canvas.bind("<Control-c>",        self._on_copy)
        self._canvas.bind("<Control-C>",        self._on_copy)

        # Resize -> recalcular fit-to-width
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
        if is_onedrive_placeholder(pdf_path):
            self._show_placeholder(
                f"El archivo no está descargado localmente.\n\n"
                f"Abre OneDrive y descarga:\n{pdf_path.name}"
            )
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

    def show_message(self, message: str) -> None:
        """Muestra un mensaje de texto centrado en el canvas (sin PDF)."""
        self._show_placeholder(message)

    def release_file_handles(self, message: str = "") -> None:
        """Cierra el documento actual para liberar locks en Windows.

        IMPORTANTE: En Windows, los archivos mapeados en memoria pueden tomar
        tiempo para liberarse después de close(). Se agrega delay para asegurar.
        """
        import time
        self._close_doc()
        # Dar tiempo al SO para liberar el archivo en Windows
        time.sleep(0.1)
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
        # Ctrl presionado -> delegar a zoom
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
        zoom = self._zoom

        # Encontrar la palabra bajo el cursor
        hit_block = None
        hit_line  = None
        best_dist = float("inf")

        for word in self._text_blocks:
            x0, y0, x1, y1, text, block_no, line_no, *_ = word
            bx0 = x0 * zoom + self._HPAD
            by0 = y0 * zoom + self._VPAD
            bx1 = x1 * zoom + self._HPAD
            by1 = y1 * zoom + self._VPAD
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

    # ── SELECCIÓN DE TEXTO CON DRAG ────────────────────────────────────────────
    def _on_sel_start(self, event: tk.Event):
        """Inicia selección al hacer click izquierdo."""
        if not self._text_blocks:
            return
        cx = self._canvas.canvasx(event.x)
        cy = self._canvas.canvasy(event.y)
        self._sel_start = (cx, cy)
        self._sel_end   = (cx, cy)
        self._clear_sel_rects()   # limpiar selección previa visual
        self._sel_text = ""

    def _on_sel_drag(self, event: tk.Event):
        """Extiende selección al arrastrar (throttled ~60fps)."""
        if self._sel_start is None or not self._text_blocks:
            return
        cx = self._canvas.canvasx(event.x)
        cy = self._canvas.canvasy(event.y)
        self._sel_end = (cx, cy)
        if not self._drag_pending:
            self._drag_pending = True
            self._canvas.after(16, self._flush_drag)   # máx ~60 fps

    def _flush_drag(self):
        """Procesa el drag y redibuja los highlights (llamado desde after)."""
        self._drag_pending = False
        if self._sel_start is not None:
            self._draw_selection()

    def _on_sel_end(self, event: tk.Event):
        """Finaliza selección y captura texto."""
        if self._sel_start is None:
            return
        cx = self._canvas.canvasx(event.x)
        cy = self._canvas.canvasy(event.y)
        self._sel_end = (cx, cy)
        words = self._get_words_in_selection()
        self._sel_text = self._words_to_text(words)
        # Auto-copy al portapapeles al soltar (como selección en terminal)
        if self._sel_text:
            self.clipboard_clear()
            self.clipboard_append(self._sel_text)

    def _on_copy(self, event=None):
        """Ctrl+C — copia la selección actual al portapapeles."""
        if self._sel_text:
            self.clipboard_clear()
            self.clipboard_append(self._sel_text)

    def _build_spatial_grid(self, words: list) -> dict:
        """Construye índice espacial: divide el PDF en celdas de _CELL_SIZE puntos.

        Retorna: dict((col, row) → list[int] indices en words)
        """
        grid: dict = {}
        cs = self._CELL_SIZE
        for i, w in enumerate(words):
            x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
            c0 = int(x0 / cs)
            c1 = int(x1 / cs)
            r0 = int(y0 / cs)
            r1 = int(y1 / cs)
            for c in range(c0, c1 + 1):
                for r in range(r0, r1 + 1):
                    key = (c, r)
                    if key not in grid:
                        grid[key] = []
                    grid[key].append(i)
        return grid

    def _draw_selection(self):
        """Dibuja rectángulos highlight sobre palabras en el rango seleccionado."""
        self._clear_sel_rects()
        if self._sel_start is None or self._sel_end is None:
            return
        words = self._get_words_in_selection()
        zoom = self._zoom
        for word in words:
            x0, y0, x1, y1 = word[0], word[1], word[2], word[3]
            bx0 = x0 * zoom + self._HPAD
            by0 = y0 * zoom + self._VPAD
            bx1 = x1 * zoom + self._HPAD
            by1 = y1 * zoom + self._VPAD
            rid = self._canvas.create_rectangle(
                bx0, by0, bx1, by1,
                fill="#2563eb", outline="",
                stipple="gray25",    # simula semitransparencia en Tkinter
            )
            self._sel_ids.append(rid)

    def _clear_sel_rects(self):
        """Elimina del canvas los rectángulos de selección."""
        for rid in self._sel_ids:
            self._canvas.delete(rid)
        self._sel_ids = []

    def _get_words_in_selection(self) -> list:
        """Retorna palabras cuyo bbox intersecta con el rect de selección actual.

        Usa índice espacial para evitar iteración lineal de todas las palabras.
        """
        if self._sel_start is None or self._sel_end is None or not self._spatial_grid:
            return []
        zoom = self._zoom
        cx0, cy0 = self._sel_start
        cx1, cy1 = self._sel_end
        sel_x0, sel_x1 = min(cx0, cx1), max(cx0, cx1)
        sel_y0, sel_y1 = min(cy0, cy1), max(cy0, cy1)

        # Convertir canvas coords → PDF coords (invertir la transformación)
        pdf_x0 = (sel_x0 - self._HPAD) / zoom
        pdf_x1 = (sel_x1 - self._HPAD) / zoom
        pdf_y0 = (sel_y0 - self._VPAD) / zoom
        pdf_y1 = (sel_y1 - self._VPAD) / zoom

        # Determinar qué celdas del grid intersectan la selección
        cs = self._CELL_SIZE
        c0 = max(0, int(pdf_x0 / cs))
        c1 = int(pdf_x1 / cs)
        r0 = max(0, int(pdf_y0 / cs))
        r1 = int(pdf_y1 / cs)

        # Candidatos (deduplicados por índice)
        candidates: set = set()
        for c in range(c0, c1 + 1):
            for r in range(r0, r1 + 1):
                indices = self._spatial_grid.get((c, r))
                if indices:
                    candidates.update(indices)

        # Filtro exacto con AABB en PDF coords + overlap threshold 30%
        selected = []
        for i in candidates:
            w = self._text_blocks[i]
            x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
            # Overlap exacto en PDF coords
            ox = max(0.0, min(x1, pdf_x1) - max(x0, pdf_x0))
            oy = max(0.0, min(y1, pdf_y1) - max(y0, pdf_y0))
            word_area = (x1 - x0) * (y1 - y0)
            if word_area > 0 and (ox * oy) / word_area >= 0.30:
                selected.append(w)

        return selected

    def _words_to_text(self, words):
        """Reconstruye texto ordenado: bloque → línea → x, con saltos entre líneas."""
        if not words:
            return ""
        words_sorted = sorted(words, key=lambda w: (w[5], w[6], w[0]))
        lines = []
        cur_block, cur_line, buf = None, None, []
        for w in words_sorted:
            bn, ln = w[5], w[6]
            if cur_block is None:
                cur_block, cur_line = bn, ln
            if bn != cur_block or ln != cur_line:
                lines.append(" ".join(x[4] for x in buf))
                buf = []
                cur_block, cur_line = bn, ln
            buf.append(w)
        if buf:
            lines.append(" ".join(x[4] for x in buf))
        return "\n".join(lines)

    def _on_canvas_resize(self, event: tk.Event):
        """Al redimensionar, recalcular fit si seguimos en modo fit."""
        if not self._doc:
            return
        if abs(self._zoom - self._fit_zoom) < 0.05:
            if hasattr(self, '_resize_id'):
                try:
                    self.after_cancel(self._resize_id)
                except Exception:
                    logger.debug("No se pudo cancelar el resize pendiente del visor PDF", exc_info=True)
            self._resize_id = self.after(60, self._recalc_fit_and_render)

    # ── RENDERIZADO ───────────────────────────────────────────────────────────
    def _on_canvas_mapped(self, _event=None):
        """Se dispara cuando el canvas se hace visible.
        El tamaño puede no ser definitivo aún -- _on_canvas_resize lo manejará.
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

        # Limpiar selección previa (cambio de página/zoom)
        self._clear_sel_rects()
        self._sel_start = None
        self._sel_end   = None
        self._sel_text  = ""
        self._drag_pending = False

        zoom = self._zoom
        page = self._doc[self._page_index]
        mat  = fitz.Matrix(zoom, zoom)
        pix  = page.get_pixmap(matrix=mat, alpha=False)

        # Guardar bloques de texto para copia con clic derecho
        self._text_blocks = page.get_text("words")  # (x0,y0,x1,y1,word,block,line,word_idx)

        # Construir índice espacial para selección eficiente
        self._spatial_grid = self._build_spatial_grid(self._text_blocks)

        # PPM -> PhotoImage
        self._tk_image = tk.PhotoImage(data=pix.tobytes("ppm"))

        w, h = pix.width, pix.height
        self._canvas.configure(scrollregion=(0, 0, w + self._HPAD * 2, h + self._VPAD * 2))
        self._canvas.delete("all")

        # Sombra sutil
        self._canvas.create_rectangle(
            self._HPAD + 3, self._VPAD + 3, w + self._HPAD + 3, h + self._VPAD + 3,
            fill="#060809", outline="",
        )
        # Página -- empieza casi en y=0
        self._canvas.create_image(self._HPAD, self._VPAD, anchor="nw", image=self._tk_image)
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
        self._lbl_page.configure(text="--")
        self._lbl_zoom.configure(text="--")
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
        self._spatial_grid = {}
        self._drag_pending = False
        # Limpiar selección
        self._clear_sel_rects()
        self._sel_start = None
        self._sel_end   = None
        self._sel_text  = ""
        self._sel_ids   = []


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
