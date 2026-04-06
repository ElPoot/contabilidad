"""Controlador para la lógica de negocio de intercambio de PDF duplicado."""
from pathlib import Path
from gestor_contable.core.models import FacturaRecord

def execute_pdf_swap(
    record: FacturaRecord, 
    rejected_path: Path, 
    pdf_duplicates_rejected: dict[Path, Path]
) -> tuple[bool, str]:
    """
    Intercambia el PDF actual del registro por el descartado y actualiza el historial.
    Retorna (True, "") si fue exitoso, o (False, "mensaje de error").
    """
    if not rejected_path.exists():
        return False, f"El archivo descartado ya no existe:\n{rejected_path.name}"

    current_path = record.pdf_path
    record.pdf_path = rejected_path
    
    # Intercambiar en el historial de duplicados para permitir revertir
    if current_path:
        pdf_duplicates_rejected[current_path] = rejected_path
    if rejected_path in pdf_duplicates_rejected:
        del pdf_duplicates_rejected[rejected_path]

    return True, ""
