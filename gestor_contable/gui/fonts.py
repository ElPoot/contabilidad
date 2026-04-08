"""
Gestor Contable - Sistema Tipográfico Unificado
Centraliza todas las fuentes de la aplicación para evitar inconsistencias de diseño.

Escala calibrada para resoluciones 1080p (1920x1080) en ventana ~1440x860.

── FILOSOFÍA DE USO ──────────────────────────────────────────────────────────
• Las funciones F_*() genéricas (F_BODY, F_LABEL, etc.) se usan en componentes
  de propósito general (session_view, classify_panel, setup_window, etc.).
• Los roles semánticos (F_MODAL_*, F_BTN_*, F_CARD_*, F_APP_TITLE, etc.) se
  asignan a secciones concretas de la UI principal. Cambia uno sin afectar otro.
• TREE_FONT_* controla exclusivamente el Treeview de facturas (ttk).
"""

import customtkinter as ctk

_fonts = {}

def get_font(size: int, weight: str = "normal", family: str = "Segoe UI") -> ctk.CTkFont:
    """Implementa flyweight/cache para reutilizar instancias de fuentes nativas."""
    key = f"{family}-{size}-{weight}"
    if key not in _fonts:
        _fonts[key] = ctk.CTkFont(family=family, size=size, weight=weight)
    return _fonts[key]

# ── TITULARES ─────────────────────────────────────────────────────────────
def F_TITLE() -> ctk.CTkFont:       return get_font(16, "bold")    # Títulos principales (Facturas del período)
def F_HEADING() -> ctk.CTkFont:     return get_font(14, "bold")    # Títulos de paneles / nombre de cliente
def F_SUBHEADING() -> ctk.CTkFont:  return get_font(12, "bold")    # Subtítulos, tarjetas de clientes

# ── CUERPO (BASE) ─────────────────────────────────────────────────────────
def F_BODY() -> ctk.CTkFont:        return get_font(11, "normal")  # Cajas de texto, listas, inputs
def F_BODY_BOLD() -> ctk.CTkFont:   return get_font(11, "bold")    # Énfasis en texto base

# ── ETIQUETAS (LABELS) ────────────────────────────────────────────────────
def F_LABEL() -> ctk.CTkFont:       return get_font(10, "normal")  # Etiquetas sobre inputs (Ej: "Desde:")
def F_LABEL_BOLD() -> ctk.CTkFont:  return get_font(10, "bold")    # Etiquetas con énfasis

# ── DATOS PEQUEÑOS ────────────────────────────────────────────────────────
def F_SMALL() -> ctk.CTkFont:       return get_font(9, "normal")   # Textos secundarios, metadatos
def F_SMALL_BOLD() -> ctk.CTkFont:  return get_font(9, "bold")     # Badges, status pequeños
def F_MICRO() -> ctk.CTkFont:       return get_font(8, "normal")   # Paths de archivo, detalles técnicos
def F_MICRO_BOLD() -> ctk.CTkFont:  return get_font(8, "bold")     # Títulos microscópicos en tarjetas

# ── CONTROLES Y U.I ───────────────────────────────────────────────────────
def F_BUTTON() -> ctk.CTkFont:      return get_font(11, "bold")    # Botones genéricos de UI
def F_BUTTON_LG() -> ctk.CTkFont:   return get_font(13, "bold")    # Botón principal grande ("Clasificar")
def F_AVATAR() -> ctk.CTkFont:      return get_font(14, "bold")    # Icono de avatar / logo

# ═══════════════════════════════════════════════════════════════════════════════
# ROLES SEMÁNTICOS POR COMPONENTE
# Ajusta aquí para cambiar solo la sección que necesites sin afectar el resto.
# ═══════════════════════════════════════════════════════════════════════════════

# ── BARRA SUPERIOR (TOOLBAR / HEADER) ─────────────────────────────────────
# El título principal de la app y los títulos de sección en paneles.
def F_APP_TITLE() -> ctk.CTkFont:       return get_font(15, "bold")    # "Clasificador Contable" en el header
def F_SECTION_TITLE() -> ctk.CTkFont:   return get_font(14, "bold")    # Encabezados de paneles (ej. "Clasificación")
def F_SECTION_LABEL() -> ctk.CTkFont:   return get_font(9,  "bold")    # Labels de sección en minúsculos (ej. "ACCIONES ORS")

# ── MODALES ───────────────────────────────────────────────────────────────
# Ventanas modales: confirmaciones, historial, ORS, cuarentena, etc.
def F_MODAL_TITLE() -> ctk.CTkFont:     return get_font(14, "bold")    # Título de modal (ej. "Historial de cuarentenas")
def F_MODAL_SUBTITLE() -> ctk.CTkFont:  return get_font(12, "bold")    # Sub-título o nombre de cuenta en modal
def F_MODAL_BODY() -> ctk.CTkFont:      return get_font(12, "normal")  # Cuerpo de texto informativo en modal
def F_MODAL_SUBTEXT() -> ctk.CTkFont:   return get_font(11, "normal")  # Texto secundario en modal
def F_MODAL_HINT() -> ctk.CTkFont:      return get_font(10, "normal")  # Notas al pie en modales
def F_MODAL_MICRO() -> ctk.CTkFont:     return get_font(9,  "normal")  # Rutas, IDs técnicos en modal

# ── BOTONES DE ACCIÓN (en modales y paneles de acción) ────────────────────
def F_BTN_PRIMARY() -> ctk.CTkFont:     return get_font(13, "bold")    # Botón primario (ej. "Confirmar purga", "Restaurar lote")
def F_BTN_SECONDARY() -> ctk.CTkFont:   return get_font(13, "normal")  # Botón secundario (ej. "Cancelar", "Cerrar")
def F_BTN_LIST() -> ctk.CTkFont:        return get_font(11, "normal")  # Botones en listas (historial de lotes)

# ── TARJETAS / RESÚMENES ──────────────────────────────────────────────────
# Widgets tipo "card" con métricas o datos clave (ej. resumen de purga ORS).
def F_CARD_TITLE() -> ctk.CTkFont:      return get_font(12, "bold")    # Título de tarjeta
def F_CARD_VALUE() -> ctk.CTkFont:      return get_font(12, "bold")    # Valor numérico en tarjeta
def F_CARD_LABEL() -> ctk.CTkFont:      return get_font(12, "normal")  # Etiqueta de fila en tarjeta

# ── TABLA DE FACTURAS (ttk.Treeview) ─────────────────────────────────────
# Controla la tabla principal de facturas (Dark.Treeview).
# Cambia aquí para afectar solo la tabla sin tocar el resto de la UI.
TREE_FONT_FAMILY: str  = "Segoe UI"
TREE_FONT_SIZE: int    = 9              # Tamaño para celdas de datos
TREE_HEADING_SIZE: int = 9             # Tamaño para encabezados de columna
TREE_ROW_HEIGHT: int   = 20            # Alto de fila en píxeles
