"""Utilidades de IVA - Replicadas de App 2 (xml_manager.py).

Lógica contable estricta para procesamiento de impuestos según
reglamentación de Hacienda CR.
"""

from decimal import Decimal, InvalidOperation
from typing import Any

# Mapeo de códigos de tarifa IVA de Hacienda
IVA_TARIFA_CODE_MAP = {
    "01": "0",      # Tasa 0%
    "02": "1",      # Tasa 1%
    "03": "2",      # Tasa 2%
    "04": "4",      # Tasa 4%
    "05": "8",      # Tasa 8%
    "06": "10",     # Tasa 10%
    "07": "0",      # Tasa 0% (exento)
    "08": "13",     # Tasa 13% (estándar CR)
}


def parse_decimal_value(raw_value: Any) -> Decimal | None:
    """Convierte valor raw a Decimal preservando precisión.

    Maneja formatos:
    - "1000.50" → Decimal("1000.50")
    - "1000,50" → Decimal("1000.50")
    - "1.000,50" → Decimal("1000.50")
    - "1,000.50" → Decimal("1000.50")
    - None, "" → None
    """
    if raw_value is None:
        return None

    text = str(raw_value or "").strip()
    if not text:
        return None

    # Limpiar espacios
    text = text.replace(" ", "")

    # Detectar separadores decimales/miles
    has_comma = "," in text
    has_dot = "." in text

    if has_comma and has_dot:
        # "1.234,56" o "1,234.56"
        if text.rfind(",") > text.rfind("."):
            # 1.234,56 → 1234.56 (coma es decimal)
            text = text.replace(".", "").replace(",", ".")
        else:
            # 1,234.56 → 1234.56 (punto es decimal)
            text = text.replace(",", "")
    elif has_comma:
        # "1000,50" → "1000.50" (coma es decimal)
        text = text.replace(",", ".")
    # Si solo hay punto: ya está en formato estándar

    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def decimal_to_local_text(number: Decimal) -> str:
    """Convierte Decimal a formato local CR: "2450" o "2450,50".

    Elimina decimales .00 y reemplaza . por , para formato local.
    """
    if number is None:
        return ""

    # Convertir a string sin notación científica
    text = format(number, "f")

    # Eliminar ceros decimales finales
    if "." in text:
        text = text.rstrip("0").rstrip(".")

    # Reemplazar punto por coma (formato local CR)
    return text.replace(".", ",")


def sum_decimal_strings(values: list[str]) -> str:
    """Suma lista de strings de montos usando Decimal.

    Retorna resultado como string en formato local.
    Ignora valores None o inválidos.
    """
    total = Decimal("0")
    found_valid = False

    for value in values:
        parsed = parse_decimal_value(value)
        if parsed is None:
            continue
        total += parsed
        found_valid = True

    return decimal_to_local_text(total) if found_valid else "0"


def normalize_tax_rate(raw_rate: Any) -> str:
    """Normaliza tasa de impuesto a string sin decimales innecesarios.

    "13.0" → "13"
    "13.50" → "13,5"
    None → ""
    """
    text = str(raw_rate or "").strip().replace(",", ".")

    if not text:
        return ""

    try:
        numeric = float(text)
    except ValueError:
        return ""

    # Si es entero
    if numeric.is_integer():
        return str(int(numeric))

    # Sino, retorna con máximo 2 decimales, removiendo ceros
    formatted = f"{numeric:.2f}".rstrip("0").rstrip(".")
    return formatted.replace(".", ",")


def ensure_negative_amount(raw_value: Any) -> str:
    """Garantiza que montos de Notas de Crédito sean negativos.

    Si es NotaCreditoElectronica, el monto DEBE ser negativo.
    """
    number = parse_decimal_value(raw_value)
    if number is None:
        return "0"

    # Garantizar negativo
    return decimal_to_local_text(-abs(number))


def validate_iva_sum(iva_dict: dict[str, str]) -> bool:
    """Valida que suma de IVAs por tasa = impuesto_total.

    Retorna True si es consistente (tolerancia: ±0.01).
    """
    iva_cols = ["iva_1", "iva_2", "iva_4", "iva_8", "iva_13"]
    suma_ivas = Decimal("0")

    for col in iva_cols:
        val = parse_decimal_value(iva_dict.get(col, "0"))
        if val:
            suma_ivas += val

    impuesto_total = parse_decimal_value(iva_dict.get("impuesto_total", "0"))
    if impuesto_total is None:
        impuesto_total = Decimal("0")

    # Tolerancia de ±0.01 para redondeos
    diferencia = abs(suma_ivas - impuesto_total)
    return diferencia <= Decimal("0.01")


def validate_total_comprobante(
    subtotal_str: str, impuesto_total_str: str, total_comprobante_str: str
) -> bool:
    """Valida que subtotal + impuesto_total = total_comprobante.

    Retorna True si es consistente (tolerancia: ±0.01).
    """
    subtotal = parse_decimal_value(subtotal_str) or Decimal("0")
    impuesto = parse_decimal_value(impuesto_total_str) or Decimal("0")
    total = parse_decimal_value(total_comprobante_str) or Decimal("0")

    suma = subtotal + impuesto
    diferencia = abs(suma - total)

    return diferencia <= Decimal("0.01")
