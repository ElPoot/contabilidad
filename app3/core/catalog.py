from __future__ import annotations

import json
from pathlib import Path

CATALOG_DM_PATH = Path(__file__).resolve().parents[2] / "catalogo_de_cuentas.dm"

DEFAULT_CATALOG = {
    "COMPRAS": {
        "COMPRAS": {
            "COMPRAS DE CONTADO": {},
            "COMPRAS DE CREDITO": {},
        }
    },
    "GASTOS": {
        "GASTOS ESPECIFICOS": {
            "ALQUILER": {},
            "COMISIONES": {},
            "GASTOS FINANCIEROS": {},
            "HONORARIOS PROFESIONALES": {},
        },
        "GASTOS GENERALES": {
            "ELECTRICIDAD": {},
            "TELECOMUNICACIONES": {},
            "ACUEDUCTOS": {},
            "PAPELERIA & UTILES DE OFICINA": {},
        },
    },
    "OGND": {
        "TIPOS": {
            "OGND": {},
            "DNR": {},
            "ORS": {},
            "CNR": {},
        }
    },
}


def _normalize_name(value: str) -> str:
    text = str(value or "").strip().upper()
    if "(" in text:
        text = text.split("(", 1)[0].strip()
    return " ".join(text.split())


class CatalogManager:
    def __init__(self, metadata_dir: Path) -> None:
        self.path = metadata_dir / "catalogo_cuentas.json"

    def load(self) -> dict:
        if self.path.exists():
            try:
                raw_text = self.path.read_text(encoding="utf-8").strip()
                if not raw_text:
                    raise ValueError("archivo vacío")
                data = json.loads(raw_text)
                if isinstance(data, dict):
                    normalized = self._normalize_catalog(data)
                    self.save(normalized)
                    return normalized
                raise ValueError("catálogo inválido")
            except Exception:
                backup = self.path.with_suffix(".invalid.json")
                try:
                    if self.path.exists():
                        self.path.replace(backup)
                except Exception:
                    pass

        catalog = self.load_from_dm(CATALOG_DM_PATH)
        self.save(catalog)
        return catalog

    def load_from_dm(self, dm_path: Path) -> dict:
        if not dm_path.exists():
            return self._normalize_catalog(DEFAULT_CATALOG)

        nodes: dict[str, dict[str, str]] = {}
        lines = dm_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for raw_line in lines[1:]:
            line = raw_line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|", 2)]
            if len(parts) < 3:
                continue
            code, name, parent = parts[0], _normalize_name(parts[1]), parts[2].strip()
            if not code or not name:
                continue
            nodes[code] = {"name": name, "parent": parent}

        catalog = {
            "COMPRAS": {"COMPRAS": {}},
            "GASTOS": {"GASTOS ESPECIFICOS": {}, "GASTOS GENERALES": {}},
            "OGND": {"TIPOS": {}},
        }

        for code, node in nodes.items():
            name = node["name"]
            parent = node["parent"]

            if parent == "5000":  # COMPRAS direct children
                catalog["COMPRAS"]["COMPRAS"][name] = {}
                continue

            if parent == "6100":  # GASTOS ESPECIFICOS children
                catalog["GASTOS"]["GASTOS ESPECIFICOS"][name] = {}
                continue

            if parent == "6200":  # GASTOS GENERALES children
                catalog["GASTOS"]["GASTOS GENERALES"][name] = {}
                continue

            if parent == "7000" and name in {"OGND", "DNR", "ORS", "CNR"}:
                catalog["OGND"]["TIPOS"][name] = {}

        normalized = self._normalize_catalog(catalog)
        return normalized

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._normalize_catalog(data), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def categorias(self) -> list[str]:
        data = self.load()
        return sorted(data.keys())

    def subtipos(self, categoria: str) -> list[str]:
        data = self.load()
        cat = str(categoria or "").strip().upper()
        return sorted(data.get(cat, {}).keys())

    def cuentas(self, categoria: str, subtipo: str) -> list[str]:
        data = self.load()
        cat = str(categoria or "").strip().upper()
        stp = str(subtipo or "").strip().upper()
        cuentas = data.get(cat, {}).get(stp, {})
        return sorted(cuentas.keys()) if isinstance(cuentas, dict) else []

    def add_cuenta(self, categoria: str, subtipo: str, cuenta: str) -> None:
        cat = str(categoria or "").strip().upper()
        stp = str(subtipo or "").strip().upper()
        cta = _normalize_name(cuenta)
        if not cat or not stp or not cta:
            return

        data = self.load()
        data.setdefault(cat, {})
        data[cat].setdefault(stp, {})
        if not isinstance(data[cat][stp], dict):
            data[cat][stp] = {}
        data[cat][stp][cta] = {}
        self.save(data)

    def _normalize_catalog(self, raw: dict) -> dict:
        out: dict[str, dict[str, dict[str, dict]]] = {}
        if not isinstance(raw, dict):
            return json.loads(json.dumps(DEFAULT_CATALOG))

        for cat_name, subtypes in raw.items():
            cat = _normalize_name(cat_name)
            if not cat:
                continue
            out.setdefault(cat, {})

            if not isinstance(subtypes, dict):
                continue
            for subtype_name, cuentas in subtypes.items():
                stp = _normalize_name(subtype_name)
                if not stp:
                    continue
                out[cat].setdefault(stp, {})

                if isinstance(cuentas, dict):
                    for cuenta_name in cuentas.keys():
                        cta = _normalize_name(cuenta_name)
                        if cta:
                            out[cat][stp][cta] = {}

        # Garantizar presencia mínima
        for cat, min_data in DEFAULT_CATALOG.items():
            out.setdefault(cat, {})
            for stp, cuentas in min_data.items():
                out[cat].setdefault(stp, {})
                for cta in cuentas.keys():
                    out[cat][stp].setdefault(cta, {})

        return out
