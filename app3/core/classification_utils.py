"""Utilidades de clasificación de transacciones para Facturas del Período.

Clasifica cada factura según la perspectiva del cliente actual.
"""

from app3.core.models import FacturaRecord


def classify_transaction(record: FacturaRecord, client_cedula: str) -> str:
    """Clasifica factura según perspectiva del cliente.

    Args:
        record: FacturaRecord con datos XML
        client_cedula: Cédula del cliente actual (sesión)

    Returns:
        "ingreso" - Yo soy emisor (venta)
        "egreso" - Yo soy receptor (compra)
        "ors" - Terceros (ni emisor ni receptor soy yo)
    """
    client_ced = (client_cedula or "").strip()
    if not client_ced:
        return "ors"

    emisor_ced = (record.emisor_cedula or "").strip()
    receptor_ced = (record.receptor_cedula or "").strip()

    # Yo soy emisor → Ingreso (venta)
    if emisor_ced == client_ced:
        return "ingreso"

    # Yo soy receptor → Egreso (compra)
    if receptor_ced == client_ced:
        return "egreso"

    # Terceros
    return "ors"


def get_classification_label(classification: str) -> str:
    """Retorna etiqueta legible de clasificación."""
    labels = {
        "ingreso": "Ingresos",
        "egreso": "Egresos",
        "ors": "ORS",
        "pendiente": "Pendientes",
        "sin_clave": "PDFs sin clave",
        "omitidos": "PDFs omitidos",
        "todas": "Todas las facturas",
    }
    return labels.get(classification, classification)


def filter_records_by_tab(
    records: list[FacturaRecord],
    tab: str,
    client_cedula: str,
    db_records: dict[str, dict],
) -> list[FacturaRecord]:
    """Filtra registros según pestaña activa.

    Args:
        records: Lista de FacturaRecord
        tab: Pestaña activa ("todas", "ingreso", "egreso", "ors", "pendiente", "sin_clave", "omitidos")
        client_cedula: Cédula del cliente actual
        db_records: Mapeo de clave → datos de clasificación (BD)

    Returns:
        Lista filtrada de FacturaRecord
    """
    # Excluir registros omitidos de todas las pestañas excepto "omitidos"
    non_omitted = [r for r in records if not r.razon_omisión]

    if tab == "todas":
        return non_omitted

    if tab == "pendiente":
        # Pendientes: no clasificados (excluir omitidos)
        return [
            r for r in non_omitted
            if not (db_records.get(r.clave, {}).get("estado") == "clasificado")
        ]

    if tab == "sin_clave":
        # PDFs sin clave: no tienen clave válida (50 dígitos) o falta vinculación.
        # Excluir ya clasificados (PDF movido → pdf_path=None en recarga, no es error).
        return [
            r for r in non_omitted
            if not (db_records.get(r.clave, {}).get("estado") == "clasificado")
            and (not r.clave or len(r.clave) != 50 or r.estado in ("pendiente_pdf", "sin_xml") or not r.pdf_path)
        ]

    if tab == "omitidos":
        # PDFs omitidos: detectados como no-facturas o con errores de extracción
        omitted = [
            r for r in records
            if r.razon_omisión in ("non_invoice", "timeout", "extract_failed")
        ]
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"Filter omitidos: {len(omitted)} de {len(records)} registros (razon_omisión != None)")
        for r in omitted[:3]:  # Log first 3
            logger.debug(f"  - {r.clave}: razon={r.razon_omisión}")
        return omitted

    # Clasificar por tipo de transacción (excluir omitidos)
    filtered = []
    for r in non_omitted:
        classification = classify_transaction(r, client_cedula)
        if classification == tab:
            filtered.append(r)

    return filtered


def get_tab_statistics(
    records: list[FacturaRecord],
    client_cedula: str,
    db_records: dict[str, dict],
) -> dict[str, dict]:
    """Calcula estadísticas por pestaña.

    Returns:
        {
            "todas": {"count": int, "clasificados": int, "porcentaje": int},
            "ingreso": {...},
            "egreso": {...},
            "ors": {...},
            "pendiente": {...},
            "sin_clave": {...},
            "omitidos": {...},
        }
    """
    tabs = ["todas", "ingreso", "egreso", "ors", "pendiente", "sin_clave", "omitidos"]
    stats = {}

    for tab in tabs:
        filtered = filter_records_by_tab(records, tab, client_cedula, db_records)
        # Solo contar clasificados para registros sin razon_omisión
        clasificados = sum(
            1
            for r in filtered
            if not r.razon_omisión and db_records.get(r.clave, {}).get("estado") == "clasificado"
        )
        total = len(filtered)
        porcentaje = int((clasificados / total * 100)) if total > 0 else 0

        stats[tab] = {
            "count": total,
            "clasificados": clasificados,
            "porcentaje": porcentaje,
        }

    return stats
