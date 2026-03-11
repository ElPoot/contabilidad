"""Utilidades de clasificación de transacciones para Facturas del Período.

Clasifica cada factura según la perspectiva del cliente actual.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from app3.core.models import FacturaRecord

logger = logging.getLogger(__name__)


def classify_transaction(record: FacturaRecord, client_cedula: str) -> str:
    """Clasifica factura según perspectiva del cliente.

    Args:
        record: FacturaRecord con datos XML
        client_cedula: Cédula del cliente actual (sesión)

    Returns:
        "ingreso" - Yo soy emisor (venta)
        "egreso" - Yo soy receptor (compra)
        "sin_receptor" - Egreso sin receptor identificado
        "ors" - Terceros (ni emisor ni receptor soy yo)
    """
    client_ced = (client_cedula or "").strip()
    if not client_ced:
        return "ors"

    emisor_ced = (record.emisor_cedula or "").strip()
    receptor_ced = (record.receptor_cedula or "").strip()

    # Yo soy emisor -> Ingreso (venta)
    if emisor_ced == client_ced:
        return "ingreso"

    # Yo soy receptor -> Egreso (compra)
    if receptor_ced == client_ced:
        return "egreso"

    # Egreso sin receptor identificado (otros gastos, sin receptor cedula)
    receptor_ced_str = str(getattr(record, "receptor_cedula", "") or "").strip().lower()
    receptor_empty = receptor_ced_str in {"", "null", "none", "nan"}
    if not emisor_ced == client_ced and receptor_empty:
        return "sin_receptor"

    # Terceros
    return "ors"


def get_classification_label(classification: str) -> str:
    """Retorna etiqueta legible de clasificación."""
    labels = {
        "ingreso": "Ingresos",
        "egreso": "Egresos",
        "sin_receptor": "Sin Receptor",
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
        tab: Pestaña activa ("todas", "ingreso", "egreso", "sin_receptor", "ors", "pendiente", "sin_clave", "omitidos")
        client_cedula: Cédula del cliente actual
        db_records: Mapeo de clave -> datos de clasificación (BD)

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
        # Excluir ya clasificados (PDF movido -> pdf_path=None en recarga, no es error).
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
        logger.debug(f"Filter omitidos: {len(omitted)} de {len(records)} registros (razon_omisión != None)")
        for r in omitted[:3]:  # Log first 3
            logger.debug(f"  - {r.clave}: razon={r.razon_omisión}")
        return omitted

    if tab == "huerfanos":
        # PDFs huérfanos: inconsistentes que pueden ser recuperados
        huerfanos = [
            r for r in records
            if r.razon_omisión and r.razon_omisión.startswith("orphaned_")
        ]
        logger.debug(f"Filter huérfanos: {len(huerfanos)} de {len(records)} registros huérfanos")
        return huerfanos

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
            "sin_receptor": {...},
            "ors": {...},
            "pendiente": {...},
            "sin_clave": {...},
            "omitidos": {...},
        }
    """
    tabs = ["todas", "ingreso", "egreso", "sin_receptor", "ors", "pendiente", "sin_clave", "omitidos"]
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


def create_orphaned_record(orphaned_info: dict) -> FacturaRecord:
    """Convierte un diccionario de PDF huérfano en FacturaRecord dummy.

    Los registros dummy se muestran en la pestaña "huérfanos" para recuperación.
    """
    clave = orphaned_info.get("clave", "DESCONOCIDA")
    ruta_actual = orphaned_info.get("ruta_actual", "")
    motivo = orphaned_info.get("motivo", "desconocido")

    motivo_labels = {
        "not_in_db": "Sin registro en BD",
        "wrong_location": "Ubicación incorrecta",
        "duplicado": "Duplicado",
        "huerfano_sin_destino": "Reclasificación falló",
    }

    # Crear record con clave requerida
    record = FacturaRecord(clave=clave)
    record.estado = "huerfano"  # Estado especial
    record.razon_omisión = f"orphaned_{motivo}"  # Marcar como huérfano
    record.pdf_path = Path(ruta_actual) if ruta_actual else None
    record.emisor_nombre = "PDF HUÉRFANO ⚠️"
    record.receptor_nombre = motivo_labels.get(motivo, motivo)
    record.fecha_emision = motivo_labels.get(motivo, motivo)  # Mostrar motivo en fecha
    record._orphaned_info = orphaned_info  # Guardar metadata para recuperación

    return record


def find_orphaned_pdfs(
    contabilidades_root: Path,
    db_records: dict[str, dict],
    client_name: str = "",
) -> list[dict]:
    """Escanea PDFs en Contabilidades que no están en BD o están en estado inconsistente.

    Si client_name se proporciona, filtra solo PDFs de ese cliente.
    La ruta esperada es: Contabilidades/{mes}/{client_name}/...

    Returns:
        Lista de diccionarios con:
        {
            "clave": str,
            "archivo": Path,
            "ruta_actual": str,
            "ruta_esperada": str | None,  # Si hay en BD
            "motivo": str,  # "not_in_db" | "wrong_location" | "estado_inconsistente"
        }
    """
    orphaned = []

    if not contabilidades_root.exists():
        return orphaned

    # Crear mapa inverso: archivo -> clave (desde BD)
    from pathlib import Path
    db_by_destino = {
        str(Path(v["ruta_destino"])): k
        for k, v in db_records.items()
        if v.get("ruta_destino")
    }

    # Escanear todos los PDFs en Contabilidades
    for pdf_path in contabilidades_root.rglob("*.pdf"):
        # Filtrar por cliente si se proporciona
        if client_name and client_name not in pdf_path.parts:
            continue

        nombre = pdf_path.name
        # Extraer clave del nombre (si es un archivo nombrado por clave)
        clave_from_name = nombre.replace(".pdf", "").strip()

        # Buscar en BD por ruta_destino
        clave = db_by_destino.get(str(pdf_path))

        # Si no encontramos por ruta, intentar por nombre de archivo (50 dígitos)
        if not clave and len(clave_from_name) == 50 and clave_from_name.isdigit():
            clave = clave_from_name

        if not clave:
            # PDF sin registro en BD
            orphaned.append({
                "clave": clave_from_name if len(clave_from_name) == 50 else "DESCONOCIDA",
                "archivo": pdf_path,
                "ruta_actual": str(pdf_path),
                "ruta_esperada": None,
                "motivo": "not_in_db",
            })
            continue

        # Verificar consistencia
        db_record = db_records.get(clave, {})
        ruta_esperada = db_record.get("ruta_destino")
        ruta_origen = db_record.get("ruta_origen")

        # Caso 1: PDF en ubicación antigua, copia también en ubicación nueva (duplicado)
        if ruta_esperada and str(pdf_path) != ruta_esperada and Path(ruta_esperada).exists():
            orphaned.append({
                "clave": clave,
                "archivo": pdf_path,
                "ruta_actual": str(pdf_path),
                "ruta_esperada": ruta_esperada,
                "motivo": "duplicado",
            })
        # Caso 2: PDF en ubicación incorrecta (tiene ruta esperada pero no está ahí)
        elif ruta_esperada and str(pdf_path) != ruta_esperada:
            orphaned.append({
                "clave": clave,
                "archivo": pdf_path,
                "ruta_actual": str(pdf_path),
                "ruta_esperada": ruta_esperada,
                "motivo": "wrong_location",
            })
        # Caso 3: PDF huérfano sin ruta esperada (reclasificación falló a mitad)
        elif not ruta_esperada and ruta_origen:
            orphaned.append({
                "clave": clave,
                "archivo": pdf_path,
                "ruta_actual": str(pdf_path),
                "ruta_esperada": ruta_origen,  # Usar ruta_origen como fallback
                "motivo": "huerfano_sin_destino",
            })

    logger.info(f"Encontrados {len(orphaned)} PDFs huérfanos u inconsistentes")
    return orphaned


def find_duplicate_pdfs_by_hash(
    contabilidades_root: Path,
    db_records: dict[str, dict],
    client_name: str = "",
) -> list[dict]:
    """
    Escanea PDFs en Contabilidades y detecta duplicados por SHA256.

    Agrupa archivos con el mismo hash y retorna solo grupos con 2+ archivos.

    Args:
        contabilidades_root: Raíz de la carpeta Contabilidades
        db_records: Mapeo de clave -> datos de clasificación (BD)
        client_name: Limita el escaneo a este cliente (si se proporciona)

    Returns:
        Lista de diccionarios con:
        {
            "sha256": str,
            "archivos": [Path, ...],
            "en_bd": Path | None,  # cuál está registrado como ruta_destino
            "status": str,         # "automático" | "ambiguo" | "sin_registro"
        }
    """
    if not contabilidades_root.exists():
        return []

    # Crear mapa inverso: archivo -> clave (desde BD)
    db_by_destino = {
        str(Path(v["ruta_destino"])): k
        for k, v in db_records.items()
        if v.get("ruta_destino")
    }

    # Mapeo: SHA256 -> lista de archivos
    hash_groups: dict[str, list[Path]] = {}

    # Función worker para calcular SHA256 de un PDF
    def compute_sha256(pdf_path: Path) -> tuple[Path, str | None]:
        try:
            from app3.core.classifier import sha256_file
            hash_val = sha256_file(pdf_path)
            return (pdf_path, hash_val)
        except Exception as e:
            logger.warning(f"No se pudo calcular SHA256 de {pdf_path}: {e}")
            return (pdf_path, None)

    # Recolectar todos los PDFs a escanear
    pdfs_to_scan = []
    for pdf_path in contabilidades_root.rglob("*.pdf"):
        # Filtrar por cliente si se proporciona
        if client_name and client_name not in pdf_path.parts:
            continue
        pdfs_to_scan.append(pdf_path)

    logger.info(f"Escaneando {len(pdfs_to_scan)} PDFs en Contabilidades para detectar duplicados...")

    # Calcular SHA256 en paralelo (ThreadPoolExecutor)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(compute_sha256, pdfs_to_scan))

    # Agrupar por SHA256
    for pdf_path, hash_val in results:
        if hash_val is None:
            continue
        if hash_val not in hash_groups:
            hash_groups[hash_val] = []
        hash_groups[hash_val].append(pdf_path)

    # Filtrar solo grupos con 2+ archivos
    duplicates = []
    for sha256, archivo_list in hash_groups.items():
        if len(archivo_list) >= 2:
            # Determinar cuál está en BD y cuál no
            en_bd = None
            for archivo in archivo_list:
                if str(archivo) in db_by_destino:
                    en_bd = archivo
                    break

            # Determinar status automático
            if en_bd is None:
                # Ninguno en BD → no se puede eliminar automáticamente
                status = "sin_registro"
            elif len([a for a in archivo_list if str(a) in db_by_destino]) > 1:
                # Múltiples en BD → ambiguo, no se puede elegir
                status = "ambiguo"
            else:
                # Uno en BD, otros no → automático, eliminar los que no están en BD
                status = "automático"

            duplicates.append({
                "sha256": sha256,
                "archivos": sorted(archivo_list),  # Ordenar para consistencia
                "en_bd": en_bd,
                "status": status,
            })
            logger.info(
                f"Duplicados encontrados (SHA256: {sha256[:16]}...): "
                f"{len(archivo_list)} archivos, status={status}"
            )

    logger.info(f"Total: {len(duplicates)} grupo(s) de duplicados detectados")
    return duplicates


def find_duplicates_pdf_origin_vs_classified(
    client_folder: Path,
    db_records: dict[str, dict],
) -> list[dict]:
    """
    Detecta PDFs descargados (en PDF/) que ya fueron clasificados (en Contabilidades/).

    Si un PDF existe en ambos lugares con igual SHA256, el de PDF/ es redundante.

    Returns:
        Lista de diccionarios:
        {
            "sha256": str,
            "en_pdf": Path,          # ubicación en CLIENTES/{cliente}/PDF/
            "en_clasificado": Path,  # ubicación en Contabilidades/
            "a_eliminar": Path,      # siempre en_pdf (la copia redundante)
        }
    """
    from app3.core.classifier import sha256_file

    redundantes = []

    # Carpetas a escanear
    pdf_folder = client_folder / "PDF"

    if not pdf_folder.exists():
        return redundantes

    # Mapeo: SHA256 -> ruta en Contabilidades (desde BD)
    sha256_to_classified = {}
    for record in db_records.values():
        ruta_destino = record.get("ruta_destino")
        if ruta_destino:
            try:
                classified_path = Path(ruta_destino)
                if classified_path.exists():
                    hash_val = sha256_file(classified_path)
                    if hash_val not in sha256_to_classified:
                        sha256_to_classified[hash_val] = classified_path
            except Exception as e:
                logger.warning(f"No se pudo calcular SHA256 de {ruta_destino}: {e}")

    # Escanear PDFs en PDF/ y buscar duplicados
    for pdf_path in pdf_folder.rglob("*.pdf"):
        try:
            pdf_hash = sha256_file(pdf_path)

            if pdf_hash in sha256_to_classified:
                classified_path = sha256_to_classified[pdf_hash]
                redundantes.append({
                    "sha256": pdf_hash,
                    "en_pdf": pdf_path,
                    "en_clasificado": classified_path,
                    "a_eliminar": pdf_path,  # Siempre eliminar la copia en PDF/
                })
                logger.info(
                    f"Duplicado encontrado (SHA256: {pdf_hash[:16]}...): "
                    f"{pdf_path.name} ya está clasificado en {classified_path.parent.name}"
                )
        except Exception as e:
            logger.warning(f"No se pudo procesar {pdf_path}: {e}")

    logger.info(f"Total: {len(redundantes)} PDF(s) redundante(s) en origen")
    return redundantes


def find_duplicate_xmls_in_origin(
    client_folder: Path,
) -> list[dict]:
    """
    Detecta XMLs duplicados en CLIENTES/{cliente}/XML/.

    Si hay N copias del mismo SHA256, mantiene 1 y marca N-1 para eliminar.

    Returns:
        Lista de diccionarios:
        {
            "sha256": str,
            "copias": [Path, Path, ...],  # todas las copias del archivo
            "mantener": Path,              # cuál mantener (la primera)
            "a_eliminar": [Path, ...],     # cuáles eliminar (el resto)
        }
    """
    from app3.core.classifier import sha256_file

    duplicados = []

    xml_folder = client_folder / "XML"
    if not xml_folder.exists():
        return duplicados

    # Mapeo: SHA256 -> lista de rutas
    sha256_groups = {}

    for xml_path in xml_folder.rglob("*.xml"):
        try:
            xml_hash = sha256_file(xml_path)
            if xml_hash not in sha256_groups:
                sha256_groups[xml_hash] = []
            sha256_groups[xml_hash].append(xml_path)
        except Exception as e:
            logger.warning(f"No se pudo calcular SHA256 de {xml_path}: {e}")

    # Filtrar solo grupos con 2+ copias
    for sha256, copias in sha256_groups.items():
        if len(copias) >= 2:
            copias_ordenadas = sorted(copias)  # Mantener la primera (por orden alfabético)
            mantener = copias_ordenadas[0]
            a_eliminar = copias_ordenadas[1:]

            duplicados.append({
                "sha256": sha256,
                "copias": copias_ordenadas,
                "mantener": mantener,
                "a_eliminar": a_eliminar,
            })
            logger.info(
                f"Duplicados en XML encontrados (SHA256: {sha256[:16]}...): "
                f"{len(copias)} copias, mantener: {mantener.name}, eliminar: {len(a_eliminar)}"
            )

    logger.info(f"Total: {len(duplicados)} grupo(s) de XMLs duplicados en origen")
    return duplicados
