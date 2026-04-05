from __future__ import annotations

from pathlib import Path

_MONTH_NAMES_ES = {
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

_MONTH_ABBR_ES = {
    1: "ENE",
    2: "FEB",
    3: "MAR",
    4: "ABR",
    5: "MAY",
    6: "JUN",
    7: "JUL",
    8: "AGO",
    9: "SET",
    10: "OCT",
    11: "NOV",
    12: "DIC",
}


def month_name_es(month: int) -> str:
    return _MONTH_NAMES_ES.get(int(month), "MES")


def month_abbr_es(month: int) -> str:
    return _MONTH_ABBR_ES.get(int(month), f"{int(month):02d}")


def month_folder_name(month: int) -> str:
    month_num = int(month)
    return f"{month_num:02d}-{month_name_es(month_num)}"


def resolve_incremental_path(directory: Path, filename: str) -> Path:
    file_name = Path(str(filename or "").strip()).name
    if not file_name:
        raise ValueError("Nombre de archivo inválido para exportación.")

    base_path = directory / file_name
    if not base_path.exists():
        return base_path

    stem = Path(file_name).stem
    suffix = Path(file_name).suffix

    counter = 1
    while True:
        candidate = directory / f"{stem} - {counter:02d}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
