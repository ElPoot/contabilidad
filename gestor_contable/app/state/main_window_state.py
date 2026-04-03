"""Estado de pantalla de la ventana principal.

Dataclass pura — sin imports de customtkinter, sin logica de negocio.
Centraliza los atributos mutables de App3Window que representan estado
de UI: registros visibles, seleccion, pestana activa, rango cargado, etc.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gestor_contable.core.models import FacturaRecord


@dataclass
class MainWindowState:
    """Estado mutable de la pantalla principal.

    Todos los campos tienen valores por defecto para permitir
    instanciacion sin argumentos desde App3Window.__init__().
    """

    # Registros visibles (filtrados por pestana y rango activos)
    records: list[FacturaRecord] = field(default_factory=list)
    # Todos los registros cargados en la sesion (sin filtrar)
    all_records: list[FacturaRecord] = field(default_factory=list)
    # Registro unico seleccionado (seleccion simple)
    selected: FacturaRecord | None = None
    # Registros seleccionados en lote (multi-seleccion)
    selected_records: list[FacturaRecord] = field(default_factory=list)
    # Pestana activa: "todas" | "pendiente" | "clasificado" | etc.
    active_tab: str = "todas"
    # Meses ya cargados en la sesion actual: {(year, month), ...}
    loaded_months: set[tuple[int, int]] = field(default_factory=set)
    # Cache acumulativo clave->record de todos los meses cargados
    records_map: dict[str, FacturaRecord] = field(default_factory=dict)
    # True si el usuario cambio las fechas manualmente (no resetear al recargar)
    user_set_dates: bool = False
    # Ruta destino de la clasificacion mostrada en el panel "Anterior"
    prev_dest_path: Path | None = None
    # Renombrados de carpetas detectados en el ultimo load: [{mes, old_name, new_name}]
    detected_renames: list[dict] = field(default_factory=list)
    # PDFs rechazados por duplicado en el ultimo load: {path_rechazado: path_ganador}
    pdf_duplicates_rejected: dict = field(default_factory=dict)
