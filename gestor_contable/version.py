from __future__ import annotations

# ── Versión de la aplicación ───────────────────────────────────────────────────
# Fuente única de verdad. Editada por build.py antes de cada release.
# Formato: MAJOR.MINOR.PATCH  (semver simplificado)
#
#   MAJOR → cambios incompatibles / rediseño importante
#   MINOR → funcionalidades nuevas (retrocompatible)
#   PATCH → correcciones de bugs / ajustes menores

__version__ = "1.0.1"
__app_name__ = "Gestor Contable"
__author__   = "Clasificador Contable CR"


def get_version() -> str:
    return __version__


def get_version_tuple() -> tuple[int, int, int]:
    parts = __version__.split(".")
    return (int(parts[0]), int(parts[1]), int(parts[2]))