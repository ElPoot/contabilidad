"""Motor de clasificación para Cortes Mensuales.

Clasifica cada factura del mes en INGRESOS / COMPRAS / GASTOS / AMBIGUO
sin mover archivos ni asignar cuentas contables detalladas.

Jerarquía de decisión por factura:
  1. ¿El cliente es emisor?                        → INGRESOS  (automático)
  2. ¿El proveedor tiene decisión previa guardada? → aplica esa decisión
  3. ¿El XML tiene líneas con código CABYS?        → afinidad CIIU × CABYS  + predominancia por monto
  4. ¿El CABYS es conocido pero CIIU no aplica?    → tipo bien/servicio como señal
  5. Ninguna señal suficiente                      → AMBIGUO (cola manual)
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from gestor_contable.core.classification_utils import classify_transaction
from gestor_contable.core.models import FacturaRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
CATEGORIA_INGRESOS = "INGRESOS"
CATEGORIA_COMPRAS  = "COMPRAS"
CATEGORIA_GASTOS   = "GASTOS"
CATEGORIA_AMBIGUO  = "AMBIGUO"

METODO_AUTO_INGRESO   = "auto_ingreso"    # cliente es emisor
METODO_VENDOR_CATALOG = "vendor_catalog"  # decisión previa guardada
METODO_AUTO_CIIU      = "auto_ciiu"       # clasificado por afinidad CIIU×CABYS
METODO_AUTO_TIPO      = "auto_tipo"       # clasificado solo por tipo bien/servicio
METODO_AMBIGUO        = "ambiguo"         # no hay suficiente información

# ---------------------------------------------------------------------------
# Capítulos CABYS que son SIEMPRE GASTOS sin importar el sector
# ---------------------------------------------------------------------------
# Combustibles (cap 33): son un gasto operativo en todos los sectores.
# Aunque sean indispensables para el negocio (transporte, construcción,
# agricultura), no forman parte de la mercancía ni de la materia prima
# que se vende — son costos de funcionamiento.
_CAPITULOS_SIEMPRE_GASTOS: frozenset[str] = frozenset({
    "33",   # Gasolina, diesel, keroseno, lubricantes
})


# ---------------------------------------------------------------------------
# Resultado por factura
# ---------------------------------------------------------------------------
@dataclass
class CorteItem:
    record: FacturaRecord
    categoria: str          # INGRESOS | COMPRAS | GASTOS | AMBIGUO
    metodo: str             # cómo se llegó a la decisión
    confianza: float        # 0.0–1.0 (1.0 = certeza, 0.5 = heurística)
    nota: str               # explicación legible para la UI / reporte


# ---------------------------------------------------------------------------
# Carga de tabla de afinidad CIIU
# ---------------------------------------------------------------------------
_AFFINITY_PATH = Path(__file__).parent / "ciiu_affinity.json"
_affinity_cache: dict | None = None
_affinity_lock  = threading.Lock()


def _load_affinity() -> dict:
    global _affinity_cache
    with _affinity_lock:
        if _affinity_cache is None:
            try:
                raw = json.loads(_AFFINITY_PATH.read_text(encoding="utf-8"))
                _affinity_cache = raw
            except Exception:
                logger.warning("No se pudo cargar ciiu_affinity.json, usando default vacío.")
                _affinity_cache = {"reglas": {}, "default": {"bien_es_compra": False}}
        return _affinity_cache


def _bien_es_compra_para_ciiu(ciiu_codigo: str) -> bool | None:
    """
    Dado un código CIIU (ej: "56101"), retorna True si los bienes que compra
    ese sector se consideran COMPRAS, False si son GASTOS.
    Retorna None si el código no está en la tabla (sector desconocido).
    """
    affinity = _load_affinity()
    reglas   = affinity.get("reglas", {})

    # Buscar de más específico a más general (4→3→2 dígitos)
    for length in (4, 3, 2):
        prefix = ciiu_codigo[:length]
        if prefix in reglas:
            return reglas[prefix]["bien_es_compra"]

    # Fallback: default de la tabla
    default = affinity.get("default", {})
    if "bien_es_compra" in default:
        return default["bien_es_compra"]

    return None


def _compras_capitulos_para_ciiu(ciiu_codigo: str) -> frozenset[str] | None:
    """
    Retorna el conjunto de capítulos CABYS (2 dígitos) que son COMPRAS para
    el sector dado, o None si el sector no tiene whitelist definida.

    Si el sector tiene 'compras_capitulos', solo esos capítulos son COMPRAS;
    todo otro capítulo de bien es GASTOS, independientemente de bien_es_compra.
    """
    affinity = _load_affinity()
    reglas   = affinity.get("reglas", {})

    for length in (4, 3, 2):
        prefix = ciiu_codigo[:length]
        if prefix in reglas:
            caps = reglas[prefix].get("compras_capitulos")
            if caps is not None:
                return frozenset(str(c) for c in caps)
            return None  # sector encontrado pero sin whitelist → usar bien_es_compra genérico

    return None


# ---------------------------------------------------------------------------
# Motor principal
# ---------------------------------------------------------------------------
class CorteEngine:
    """
    Clasifica un conjunto de FacturaRecord para generar el corte mensual.

    Uso:
        engine = CorteEngine(
            client_cedula = session.cedula,
            client_name   = session.nombre,
            actividades   = get_or_fetch_activities(session.nombre, session.cedula),
            metadata_dir  = session.folder / ".metadata",
            xml_manager   = CRXMLManager(),
        )
        resultados = engine.clasificar(records)
    """

    def __init__(
        self,
        client_cedula: str,
        client_name: str,
        actividades: list[dict[str, str]],
        metadata_dir: Path,
        xml_manager: Any,
    ) -> None:
        self.client_cedula = client_cedula
        self.client_name   = client_name
        self.metadata_dir  = Path(metadata_dir)
        self.xml_manager   = xml_manager

        # Pre-calcular si los bienes son COMPRAS para alguna actividad del cliente
        self._bien_es_compra: bool | None = self._resolver_bien_es_compra(actividades)
        # Lista blanca de capítulos CABYS que son COMPRAS (None = usar bien_es_compra genérico)
        # Se fusiona con capítulos extra definidos por cliente en corte_capitulos_extra.json
        base_caps = self._resolver_compras_capitulos(actividades)
        extra_caps = self._cargar_capitulos_extra()
        if extra_caps:
            merged = (base_caps or frozenset()) | extra_caps
            self._compras_capitulos: frozenset[str] | None = merged
        else:
            self._compras_capitulos = base_caps
        self._actividades_desc = ", ".join(
            a.get("descripcion", a.get("codigo", "?")) for a in actividades
        ) if actividades else "sin actividades"

        # Catálogo de proveedores con decisión previa (lazy load)
        self._vendor_catalog: dict[str, str] | None = None
        self._catalog_lock = threading.Lock()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def clasificar(
        self,
        records: list[FacturaRecord],
        progress_callback=None,
    ) -> list[CorteItem]:
        """
        Clasifica todos los registros del mes.

        Args:
            records:           Lista de FacturaRecord del período.
            progress_callback: Función opcional (actual: int, total: int) -> None

        Returns:
            Lista de CorteItem en el mismo orden que records.
        """
        resultados: list[CorteItem] = []
        total = len(records)

        for idx, record in enumerate(records):
            try:
                item = self._clasificar_uno(record)
            except Exception:
                logger.exception("Error clasificando factura %s", record.clave)
                item = CorteItem(
                    record     = record,
                    categoria  = CATEGORIA_AMBIGUO,
                    metodo     = METODO_AMBIGUO,
                    confianza  = 0.0,
                    nota       = "Error interno durante clasificación",
                )
            resultados.append(item)

            if progress_callback and (idx % 10 == 0 or idx == total - 1):
                progress_callback(idx + 1, total)

        return resultados

    def guardar_decision_proveedor(
        self,
        emisor_cedula: str,
        emisor_nombre: str,
        categoria: str,
    ) -> None:
        """
        Guarda la decisión manual del contador para un proveedor específico.
        Se aplica a todas las futuras facturas del mismo proveedor.

        Args:
            emisor_cedula: Cédula del proveedor.
            emisor_nombre: Nombre (para referencia visual).
            categoria:     "COMPRAS" o "GASTOS".
        """
        if categoria not in (CATEGORIA_COMPRAS, CATEGORIA_GASTOS):
            raise ValueError(f"Categoría inválida: {categoria}")

        catalog = self._get_vendor_catalog()
        catalog[emisor_cedula] = {"categoria": categoria, "nombre": emisor_nombre}
        self._save_vendor_catalog(catalog)
        logger.info("Decisión guardada: %s (%s) → %s", emisor_nombre, emisor_cedula, categoria)

    # ------------------------------------------------------------------
    # Clasificación de un registro
    # ------------------------------------------------------------------
    def _clasificar_uno(self, record: FacturaRecord) -> CorteItem:
        # ---- Paso 1: dirección de la transacción -------------------------
        tx = classify_transaction(record, self.client_cedula)

        if tx == "ingreso":
            return CorteItem(
                record    = record,
                categoria = CATEGORIA_INGRESOS,
                metodo    = METODO_AUTO_INGRESO,
                confianza = 1.0,
                nota      = "Cliente es emisor (venta / ingreso)",
            )

        # ---- Paso 2: catálogo de proveedores (decisión previa) -----------
        cat_previa = self._check_vendor_catalog(record.emisor_cedula)
        if cat_previa:
            return CorteItem(
                record    = record,
                categoria = cat_previa,
                metodo    = METODO_VENDOR_CATALOG,
                confianza = 1.0,
                nota      = f"Proveedor {record.emisor_nombre} clasificado previamente como {cat_previa}",
            )

        # ---- Paso 3: análisis CABYS de las líneas del XML ----------------
        if record.xml_path and Path(record.xml_path).exists():
            lineas = self._extraer_lineas(record.xml_path)
            if lineas:
                return self._clasificar_por_cabys(record, lineas)

        # ---- Paso 4: si no hay líneas CABYS, usar tipo del CABYS global --
        # (algunos XML de Hacienda antigua no tienen líneas con CABYS)
        if self._bien_es_compra is not None:
            # Sin info de líneas: asumir bien genérico y aplicar regla CIIU
            if self._bien_es_compra:
                return CorteItem(
                    record    = record,
                    categoria = CATEGORIA_COMPRAS,
                    metodo    = METODO_AUTO_TIPO,
                    confianza = 0.5,
                    nota      = f"Sin CABYS en XML — sector {self._actividades_desc} compra bienes como insumo",
                )
            else:
                return CorteItem(
                    record    = record,
                    categoria = CATEGORIA_GASTOS,
                    metodo    = METODO_AUTO_TIPO,
                    confianza = 0.5,
                    nota      = f"Sin CABYS en XML — sector {self._actividades_desc} trata bienes como gasto",
                )

        # ---- Paso 5: AMBIGUO --------------------------------------------
        return CorteItem(
            record    = record,
            categoria = CATEGORIA_AMBIGUO,
            metodo    = METODO_AMBIGUO,
            confianza = 0.0,
            nota      = "Sin CABYS ni actividad CIIU — requiere decisión manual",
        )

    # ------------------------------------------------------------------
    # Clasificación por análisis CABYS de líneas
    # ------------------------------------------------------------------
    def _clasificar_por_cabys(
        self,
        record: FacturaRecord,
        lineas: list[dict],
    ) -> CorteItem:
        """
        Clasifica la factura analizando los CABYS de sus líneas de detalle.
        Aplica la regla de predominancia: la categoría con mayor monto total gana.
        La factura va ENTERA a esa categoría (no se parte).
        """
        from gestor_contable.core.cabys_manager import CABYSManager
        cabys_mgr = CABYSManager.get_instance()

        # Recopilar todos los CABYS únicos de las líneas para bulk lookup
        codigos_cabys = list({l["cabys"] for l in lineas if l.get("cabys")})
        cabys_info    = cabys_mgr.get_many(codigos_cabys) if codigos_cabys else {}

        monto_compras = Decimal("0")
        monto_gastos  = Decimal("0")
        lineas_sin_cabys = 0

        for linea in lineas:
            monto = self._parse_monto(linea.get("monto_total", ""))
            cabys = linea.get("cabys", "")

            if not cabys:
                lineas_sin_cabys += 1
                continue

            info     = cabys_info.get(cabys)
            tipo     = info.get("tipo", "")     if info else ""
            capitulo = info.get("capitulo", "") if info else ""

            categoria_linea = self._categoria_para_tipo(tipo, capitulo)

            if categoria_linea == CATEGORIA_COMPRAS:
                monto_compras += monto
            else:
                monto_gastos += monto

        # Si todas las líneas carecen de CABYS, caer al paso 4
        if lineas_sin_cabys == len(lineas):
            return self._clasificar_uno_sin_cabys(record)

        # Regla de predominancia por monto
        if monto_compras == Decimal("0") and monto_gastos == Decimal("0"):
            # Líneas sin montos (facturas de servicio sin MontoTotal por línea)
            categoria = CATEGORIA_GASTOS
            confianza = 0.6
            nota_monto = "sin montos por línea, asumiendo GASTOS"
        elif monto_compras >= monto_gastos:
            categoria = CATEGORIA_COMPRAS
            confianza = float(monto_compras / (monto_compras + monto_gastos)) if (monto_compras + monto_gastos) > 0 else 0.5
            nota_monto = f"₡{monto_compras:,.0f} en insumos vs ₡{monto_gastos:,.0f} en gastos"
        else:
            categoria = CATEGORIA_GASTOS
            confianza = float(monto_gastos / (monto_compras + monto_gastos))
            nota_monto = f"₡{monto_gastos:,.0f} en gastos vs ₡{monto_compras:,.0f} en insumos"

        return CorteItem(
            record    = record,
            categoria = categoria,
            metodo    = METODO_AUTO_CIIU,
            confianza = round(confianza, 2),
            nota      = f"CABYS × CIIU ({self._actividades_desc}): {nota_monto}",
        )

    def _clasificar_uno_sin_cabys(self, record: FacturaRecord) -> CorteItem:
        """Fallback para cuando el XML tiene líneas pero todas sin código CABYS."""
        if self._bien_es_compra is True:
            return CorteItem(
                record    = record,
                categoria = CATEGORIA_COMPRAS,
                metodo    = METODO_AUTO_TIPO,
                confianza = 0.4,
                nota      = "XML con líneas sin código CABYS — asumiendo COMPRAS por sector",
            )
        if self._bien_es_compra is False:
            return CorteItem(
                record    = record,
                categoria = CATEGORIA_GASTOS,
                metodo    = METODO_AUTO_TIPO,
                confianza = 0.4,
                nota      = "XML con líneas sin código CABYS — asumiendo GASTOS por sector",
            )
        return CorteItem(
            record    = record,
            categoria = CATEGORIA_AMBIGUO,
            metodo    = METODO_AMBIGUO,
            confianza = 0.0,
            nota      = "Líneas sin CABYS y sector sin regla CIIU",
        )

    # ------------------------------------------------------------------
    # Helpers de clasificación
    # ------------------------------------------------------------------
    def _categoria_para_tipo(self, tipo_cabys: str, capitulo: str = "") -> str:
        """
        Dado el tipo CABYS ('bien' o 'servicio') y su capítulo, retorna la categoría.

        Jerarquía de reglas para bienes:
          1. Capítulos siempre GASTOS (ej: combustibles cap 33): sin excepción de sector
          2. Whitelist compras_capitulos: si el sector define lista explícita,
             solo los capítulos en la lista son COMPRAS; el resto = GASTOS
          3. bien_es_compra genérico: si no hay whitelist, usa la regla binaria
          4. Conservador: sin info → GASTOS
        """
        if tipo_cabys == "servicio":
            return CATEGORIA_GASTOS

        # Regla 1: whitelist explícita del sector (máxima prioridad para bienes)
        # Permite que sectores específicos (ej: gasolineras) declaren cap 33 como COMPRAS
        if self._compras_capitulos is not None:
            return CATEGORIA_COMPRAS if capitulo in self._compras_capitulos else CATEGORIA_GASTOS

        # Regla 2: capítulos globalmente GASTOS cuando no hay whitelist
        # (combustibles — gasto operativo en cualquier sector que no los venda)
        if capitulo in _CAPITULOS_SIEMPRE_GASTOS:
            return CATEGORIA_GASTOS

        # Regla 3: bien_es_compra genérico (sector sin whitelist ni cap especial)
        if tipo_cabys == "bien":
            if self._bien_es_compra is True:
                return CATEGORIA_COMPRAS
            if self._bien_es_compra is False:
                return CATEGORIA_GASTOS

        # Tipo desconocido o sin regla CIIU → conservador
        if self._bien_es_compra is True:
            return CATEGORIA_COMPRAS
        return CATEGORIA_GASTOS

    @staticmethod
    def _resolver_bien_es_compra(actividades: list[dict[str, str]]) -> bool | None:
        """
        Dada la lista de actividades CIIU del cliente, determina si los bienes
        que compra son COMPRAS (True), GASTOS (False), o indeterminado (None).

        Prioridad: actividades principales (tipo "P") gobiernan bien_es_compra.
        Si alguna principal da False (ej: transporte), prevalece aunque haya una
        secundaria con True (ej: ganadería como actividad secundaria).
        Las secundarias solo aplican como fallback si ninguna principal tiene regla.

        Para múltiples actividades del mismo tipo, se usa OR: si cualquiera → True.
        """
        if not actividades:
            return None

        def _evaluar(actos: list[dict]) -> bool | None:
            any_true  = False
            any_known = False
            for act in actos:
                ciiu = str(act.get("codigo") or "").strip()
                resultado = _bien_es_compra_para_ciiu(ciiu)
                if resultado is None:
                    continue
                any_known = True
                if resultado:
                    any_true = True
            if not any_known:
                return None
            return any_true

        principales = [a for a in actividades if str(a.get("tipo") or "P").upper() == "P"]
        secundarias = [a for a in actividades if str(a.get("tipo") or "P").upper() != "P"]

        # Evaluar principales primero; si dan resultado conocido, usar ese
        resultado_p = _evaluar(principales)
        if resultado_p is not None:
            return resultado_p

        # Sin principales conocidas: evaluar secundarias como fallback
        return _evaluar(secundarias)

    @staticmethod
    def _resolver_compras_capitulos(actividades: list[dict[str, str]]) -> frozenset[str] | None:
        """
        Retorna la unión de los capítulos CABYS que son COMPRAS para TODAS las
        actividades del cliente, o None si ninguna actividad tiene whitelist definida.

        Lógica OR: si CUALQUIER actividad tiene whitelist, se usa la unión de todas.
        Esto permite clientes con actividades mixtas (ej: restaurante + catering)
        que pueden ampliar el rango de capítulos permitidos.
        """
        if not actividades:
            return None

        caps_total: set[str] = set()
        alguna_whitelist = False

        for act in actividades:
            ciiu = str(act.get("codigo") or "").strip()
            caps = _compras_capitulos_para_ciiu(ciiu)
            if caps is not None:
                alguna_whitelist = True
                caps_total.update(caps)

        return frozenset(caps_total) if alguna_whitelist else None

    @staticmethod
    def _parse_monto(raw: str) -> Decimal:
        """Parsea monto en texto (puede tener coma decimal o punto) a Decimal."""
        if not raw:
            return Decimal("0")
        cleaned = str(raw).strip().replace(" ", "")
        if "," in cleaned and "." in cleaned:
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            cleaned = cleaned.replace(",", ".")
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return Decimal("0")

    def _extraer_lineas(self, xml_path) -> list[dict]:
        """Extrae líneas CABYS del XML via xml_manager."""
        try:
            return self.xml_manager.extract_lineas_cabys(xml_path) or []
        except Exception:
            logger.debug("No se pudieron extraer líneas CABYS de %s", xml_path, exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Catálogo de proveedores (aprendizaje)
    # ------------------------------------------------------------------
    def _get_vendor_catalog(self) -> dict[str, dict]:
        with self._catalog_lock:
            if self._vendor_catalog is None:
                self._vendor_catalog = self._load_vendor_catalog()
            return self._vendor_catalog

    def _check_vendor_catalog(self, emisor_cedula: str) -> str | None:
        """Retorna la categoría guardada para un proveedor, o None si no hay."""
        if not emisor_cedula:
            return None
        catalog = self._get_vendor_catalog()
        entry   = catalog.get(str(emisor_cedula).strip())
        if isinstance(entry, dict):
            return entry.get("categoria")
        return None

    def _cargar_capitulos_extra(self) -> frozenset[str] | None:
        """
        Carga capítulos CABYS adicionales definidos por cliente en
        .metadata/corte_capitulos_extra.json.

        Permite que clientes específicos (ej: supermercados con sección de
        ferretería) clasifiquen capítulos extra como COMPRAS sin alterar las
        reglas globales del sector en ciiu_affinity.json.

        Formato del archivo:
            { "compras_capitulos_extra": ["73", "82"], "nota": "..." }
        """
        path = self.metadata_dir / "corte_capitulos_extra.json"
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                caps = raw.get("compras_capitulos_extra", [])
                if caps:
                    return frozenset(str(c) for c in caps)
        except Exception:
            logger.warning("No se pudo leer corte_capitulos_extra.json", exc_info=True)
        return None

    def _load_vendor_catalog(self) -> dict[str, dict]:
        path = self.metadata_dir / "corte_proveedores.json"
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
        except Exception:
            logger.warning("No se pudo leer corte_proveedores.json", exc_info=True)
        return {}

    def _save_vendor_catalog(self, catalog: dict) -> None:
        with self._catalog_lock:
            self._vendor_catalog = catalog
        path = self.metadata_dir / "corte_proveedores.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("No se pudo guardar corte_proveedores.json", exc_info=True)


# ---------------------------------------------------------------------------
# Función de conveniencia
# ---------------------------------------------------------------------------
def run_corte(
    records: list[FacturaRecord],
    client_cedula: str,
    client_name: str,
    metadata_dir: Path,
    xml_manager: Any,
    progress_callback=None,
) -> list[CorteItem]:
    """
    Punto de entrada simplificado para clasificar un período completo.

    Obtiene las actividades CIIU del cliente automáticamente,
    instancia el engine y clasifica todos los registros.

    Returns lista de CorteItem listos para generar el reporte Excel.
    """
    from gestor_contable.core.client_profiles import get_or_fetch_activities

    actividades = get_or_fetch_activities(client_name, client_cedula)

    if not actividades:
        logger.warning(
            "Cliente '%s' sin actividades CIIU — clasificación usará solo tipo CABYS.",
            client_name,
        )

    engine = CorteEngine(
        client_cedula = client_cedula,
        client_name   = client_name,
        actividades   = actividades,
        metadata_dir  = metadata_dir,
        xml_manager   = xml_manager,
    )

    return engine.clasificar(records, progress_callback=progress_callback)
