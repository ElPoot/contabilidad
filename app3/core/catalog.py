from __future__ import annotations

import json
from pathlib import Path

# Archivo .dm del despacho — plantilla para nuevos clientes
_DM_PATH = Path(__file__).parent.parent.parent / "catalogo_de_cuentas.dm"

# Tipos fijos de OGND — no vienen del catálogo
_OGND_TIPOS = ["OGND", "DNR", "ORS", "CNR"]


def _parse_dm(dm_path: Path) -> dict:
    """
    Lee CODIGO|NOMBRE|PADRE y construye:
    {
      "COMPRAS": {},
      "GASTOS": {
        "GASTOS ESPECIFICOS": ["ALQUILER...", ...],
        "GASTOS GENERALES": ["ACUEDUCTOS", ...]
      },
      "OGND": {}
    }
    """
    if not dm_path.exists():
        return _default_catalog()

    rows: dict[str, tuple[str, str]] = {}  # codigo → (nombre, padre)
    for line in dm_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split("|")
        if len(parts) < 2 or parts[0].strip() == "CODIGO":
            continue
        codigo = parts[0].strip()
        nombre = parts[1].strip()
        padre  = parts[2].strip() if len(parts) > 2 else ""
        rows[codigo] = (nombre, padre)

    def children_of(parent_code: str) -> list[str]:
        return [c for c, (_, p) in rows.items() if p == parent_code]

    # Raíces: código con padre vacío
    gastos_code = next(
        (c for c, (n, p) in rows.items() if n == "GASTOS" and not p), None
    )
    gnd_code = next(
        (c for c, (n, _) in rows.items() if "GASTOS NO DEDUCIBLES" in n), None
    )

    gastos_section: dict[str, list[str]] = {}
    if gastos_code:
        for subtipo_code in children_of(gastos_code):
            if gnd_code and subtipo_code == gnd_code:
                continue  # OGND va por su propio flujo
            subtipo_name = rows[subtipo_code][0]
            cuentas = sorted(rows[c][0] for c in children_of(subtipo_code))
            gastos_section[subtipo_name] = cuentas

    return {
        "COMPRAS": {},
        "GASTOS": gastos_section,
        "OGND": {},
    }


def _default_catalog() -> dict:
    """Catálogo embebido de respaldo si no hay .dm disponible."""
    return {
        "COMPRAS": {},
        "GASTOS": {
            "GASTOS ESPECIFICOS": [
                "ALQUILER (INDICAR EL CORRESPONDIENTE)",
                "COMISIONES (INDICAR EL CORRESPONDIENTE)",
                "GASTOS FINANCIEROS (INDICAR EL CORRESPONDIENTE)",
                "HONORARIOS PROFESIONALES (INDICAR EL CORRESPONDIENTE)",
            ],
            "GASTOS GENERALES": [
                "ACCESORIOS PARA EL HOTEL",
                "ACCESORIOS PARA EL NEGOCIO",
                "ACUEDUCTOS",
                "ARTICULOS DE LIMPIEZA",
                "CARGOS BANCARIOS",
                "CCSS - CUOTA PATRONAL",
                "COMBUSTIBLES & LUBRICANTES",
                "COMMUNITY MANAGER",
                "CONSORCIO",
                "CURIER INTERNACIONAL",
                "DERECHOS ADQUIRIDOS - LIQUIDACIONES",
                "ELECTRICIDAD",
                "ENVIOS & ENCOMIENDAS",
                "EQUIPO DE COMPUTO",
                "ESPECIES FISCALES",
                "GASTOS DEL EXTERIOR",
                "GASTOS MEDICOS COLABORADORES",
                "INS - POLIZA RT",
                "INS - SEGUROS",
                "INTERMEDIARIOS INTERNACIONALES",
                "MANTENIMIENTO & REP. EDIFICIO",
                "MANTENIMIENTO & REP. EQUIPO",
                "MANTENIMIENTO & REP. EQUIPO DE COMPUTO",
                "MANTENIMIENTO & REP. EQUIPO DE OFICINA",
                "MANTENIMIENTO & REP. EQUIPO INDUSTRIAL",
                "MANTENIMIENTO & REP. JARDINES",
                "MANTENIMIENTO & REP. LOCAL",
                "MANTENIMIENTO & REP. VEHICULO",
                "MATERIALES & SUMINISTROS",
                "MEMBRESIAS E IMPRESOS",
                "OGPPL",
                "OGPPL - ED",
                "PAPELERIA & UTILES DE OFICINA",
                "PEAJES & PARQUEOS",
                "PERSONERIAS JURIDICAS",
                "PLANILLA / NOMINA GENERAL",
                "RITEVE",
                "SERVICIOS ADMINISTRATIVOS BODEGAJE",
                "SERVICIOS DE RECICLAJE",
                "SERVICIOS DIGITALES TRANSFRONTERIZOS",
                "SERVICIOS ELECTRONICOS",
                "SUSCRIPCIONES",
                "TARJETA VIRTUAL - BAC",
                "TELECOMUNICACIONES",
                "TELEFONIA",
                "TRANSPORTES",
                "UNIFORMES",
                "UTENSILIOS",
                "VIATICOS",
            ],
        },
        "OGND": {},
    }


class CatalogManager:
    def __init__(self, metadata_dir: Path) -> None:
        self.path = metadata_dir / "catalogo_cuentas.json"
        self._data: dict | None = None

    def load(self) -> "CatalogManager":
        """Carga desde JSON del cliente, o bootstraps desde el .dm global."""
        if self.path.exists():
            try:
                raw = self.path.read_text(encoding="utf-8").strip()
                if raw:
                    data = json.loads(raw)
                    # Validar estructura nueva (3 categorías)
                    if isinstance(data, dict) and any(
                        k in data for k in ("COMPRAS", "GASTOS", "OGND")
                    ):
                        self._data = data
                        return self
            except Exception:
                backup = self.path.with_suffix(".invalid.json")
                try:
                    self.path.replace(backup)
                except Exception:
                    pass

        self._data = _parse_dm(_DM_PATH)
        self.save()
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    # ── API pública ────────────────────────────────────────────────────────────

    def categorias(self) -> list[str]:
        return list((self._data or {}).keys()) or ["COMPRAS", "GASTOS", "OGND"]

    def subtipos(self, categoria: str) -> list[str]:
        """Subtipos de una categoría. OGND devuelve lista fija."""
        if categoria == "OGND":
            return list(_OGND_TIPOS)
        return list((self._data or {}).get(categoria, {}).keys())

    def cuentas(self, categoria: str, subtipo: str) -> list[str]:
        """Cuentas hoja para GASTOS. COMPRAS y OGND no tienen cuentas."""
        if categoria in ("OGND", "COMPRAS"):
            return []
        return list((self._data or {}).get(categoria, {}).get(subtipo, []))

    def add_cuenta(self, categoria: str, subtipo: str, nombre: str) -> None:
        """Agrega una cuenta nueva al catálogo del cliente y persiste de inmediato."""
        if self._data is None:
            self.load()
        cat_data = self._data.setdefault(categoria, {})
        sub_list: list = cat_data.setdefault(subtipo, [])
        if nombre not in sub_list:
            sub_list.append(nombre)
            sub_list.sort()
            self.save()
