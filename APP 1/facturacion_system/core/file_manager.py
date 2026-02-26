from pathlib import Path
import re

from facturacion_system.core.settings import get_setting, resolve_fiscal_year_from_clave

EXTENSION_MAP = {
    "xml": "XML",
    "pdf": "PDF",
    "xlsx": "XLSX",
    "xls": "XLSX",
    "docx": "DOCS",
    "doc": "DOCS",
    "txt": "DOCS",
    "odt": "DOCS",
    "zip": "OTROS",
    "rar": "OTROS",
    "7z": "OTROS",
    "jpg": "OTROS",
    "jpeg": "OTROS",
    "png": "OTROS",
}

MESES = {
    1: "ENERO",
    2: "FEBRERO",
    3: "MARZO",
    4: "ABRIL",
    5: "MAYO",
    6: "JUNIO",
    7: "JULIO",
    8: "AGOSTO",
    9: "SEPTIEMBRE",
    10: "OCTUBRE",
    11: "NOVIEMBRE",
    12: "DICIEMBRE",
}


def _extension_map() -> dict[str, str]:
    mapping = dict(EXTENSION_MAP)
    extra = get_setting("extension_map", {})
    if isinstance(extra, dict):
        for k, v in extra.items():
            if k and v:
                mapping[str(k).lower().lstrip(".")] = str(v)
    return mapping


def _rule_target(file_extension: str, sender_name: str | None = None) -> str | None:
    ext = file_extension.lower().lstrip(".")
    sender = (sender_name or "").lower()
    for rule in get_setting("classification_rules", []):
        if not isinstance(rule, dict):
            continue
        ext_ok = not rule.get("extension") or str(rule.get("extension")).lower().lstrip(".") == ext
        dom = str(rule.get("from_domain") or "").lower().strip()
        dom_ok = not dom or dom in sender
        target = rule.get("target_folder")
        if ext_ok and dom_ok and target:
            return str(target)
    return None


def sanitize_folder_name(name: str) -> str:
    invalid = r'[\/:*?"<>|]'
    cleaned = re.sub(invalid, "_", (name or "Remitente").strip())
    return (cleaned or "Remitente").rstrip(". ")


def extract_month_from_clave(clave: str) -> str | None:
    try:
        if not clave:
            return None
        digits = re.sub(r"\D", "", clave)
        if len(digits) != 50:
            return None
        dd = int(digits[3:5])
        mm = int(digits[5:7])
        if not (1 <= dd <= 31 and 1 <= mm <= 12):
            return None
        return MESES[mm]
    except (ValueError, IndexError):
        return None


def get_target_folder(client_base: Path, file_extension: str, sender_name: str | None = None) -> Path:
    ext_lower = file_extension.lower().lstrip(".")
    folder_type = _rule_target(ext_lower, sender_name) or _extension_map().get(ext_lower, "OTROS")

    type_folder = client_base / folder_type
    type_folder.mkdir(parents=True, exist_ok=True)

    if folder_type == "PDF" and sender_name:
        sender_clean = sanitize_folder_name(sender_name)
        sender_folder = type_folder / sender_clean
        sender_folder.mkdir(parents=True, exist_ok=True)
        return sender_folder
    return type_folder


def _resolve_pf_base(pf_base: Path, clave: str | None) -> Path:
    open_years = get_setting("open_fiscal_years", [])
    clave_year = resolve_fiscal_year_from_clave(clave, open_years)
    if not clave_year:
        return pf_base
    return pf_base.parent / f"PF-{clave_year}"


def get_pdf_target_folder(
    pf_base: Path,
    client_folder_name: str | None,
    clave: str | None,
    razon_social: str | None,
    remitente: str | None = None,
) -> Path:
    pf_base = _resolve_pf_base(pf_base, clave)
    mes = extract_month_from_clave(clave) if clave else None

    client_segment = sanitize_folder_name((client_folder_name or "").strip()) if client_folder_name else ""

    if mes and razon_social:
        nombre_clean = sanitize_folder_name(razon_social.upper().strip())
        target = pf_base / mes
        if client_segment:
            target = target / client_segment
        target = target / "COMPRAS" / nombre_clean
    else:
        target = pf_base / "SIN_CLASIFICAR"
        if client_segment:
            target = target / client_segment
        if remitente:
            remitente_clean = sanitize_folder_name(remitente.strip())
            target = target / remitente_clean

    target.mkdir(parents=True, exist_ok=True)
    return target


def get_metadata_folder(client_base: Path) -> Path:
    metadata = client_base / ".metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    return metadata
