from __future__ import annotations

import json
from pathlib import Path


DEFAULT_CATALOG = {
    "INGRESOS": {"FACTURAS ELECTRONICAS": {}, "TIQUETES ELECTRONICOS": {}},
    "COMPRAS": {"COMPRAS DE CONTADO": {}, "COMPRAS DE CREDITO": {}},
    "GASTOS": {
        "GASTOS ESPECIFICOS": {"ALQUILER": {}, "HONORARIOS PROFESIONALES": {}},
        "GASTOS GENERALES": {"ELECTRICIDAD": {}, "PAPELERIA Y UTILES DE OFICINA": {}},
    },
}


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
                    return data
                raise ValueError("catálogo inválido")
            except Exception:
                # Respaldo y recuperación automática a default.
                backup = self.path.with_suffix(".invalid.json")
                try:
                    if self.path.exists():
                        self.path.replace(backup)
                except Exception:
                    pass
                self.save(DEFAULT_CATALOG)
                return DEFAULT_CATALOG
        self.save(DEFAULT_CATALOG)
        return DEFAULT_CATALOG

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
