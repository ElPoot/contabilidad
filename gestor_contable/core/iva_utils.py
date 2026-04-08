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

TAX_BASE_TOLERANCE = Decimal("0.01")
STANDARD_IVA_BASE_RATES = {
    "iva_1": ("1%", Decimal("0.01")),
    "iva_2": ("2%", Decimal("0.02")),
    "iva_4": ("4%", Decimal("0.04")),
    "iva_8": ("8%", Decimal("0.08")),
    "iva_13": ("13%", Decimal("0.13")),
}


def parse_decimal_value(raw_value: Any) -> Decimal | None:
    """Convierte valor raw a Decimal preservando precisión.

    Maneja formatos:
    - "1000.50" -> Decimal("1000.50")
    - "1000,50" -> Decimal("1000.50")
    - "1.000,50" -> Decimal("1000.50")
    - "1,000.50" -> Decimal("1000.50")
    - None, "" -> None
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
            # 1.234,56 -> 1234.56 (coma es decimal)
            text = text.replace(".", "").replace(",", ".")
        else:
            # 1,234.56 -> 1234.56 (punto es decimal)
            text = text.replace(",", "")
    elif has_comma:
        # "1000,50" -> "1000.50" (coma es decimal)
        text = text.replace(",", ".")
    # Si solo hay punto: ya está en formato estándar

    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def is_effectively_zero(amount: Any, tolerance: Decimal = TAX_BASE_TOLERANCE) -> bool:
    """Determina si un monto es contablemente equivalente a cero."""
    parsed = parse_decimal_value(amount)
    if parsed is None:
        return True
    return abs(parsed) <= tolerance


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

    "13.0" -> "13"
    "13.50" -> "13,5"
    None -> ""
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
    subtotal_str: str, impuesto_total_str: str, total_comprobante_str: str,
    otros_cargos_str: str = "0",
) -> bool:
    """Valida que subtotal + impuesto_total + otros_cargos = total_comprobante.

    otros_cargos cubre TotalOtrosCargos del XML (ej: IEBL, IDA, recargas Kölbi).
    Retorna True si es consistente (tolerancia: ±0.01).
    """
    subtotal      = parse_decimal_value(subtotal_str)      or Decimal("0")
    impuesto      = parse_decimal_value(impuesto_total_str) or Decimal("0")
    total         = parse_decimal_value(total_comprobante_str) or Decimal("0")
    otros_cargos  = parse_decimal_value(otros_cargos_str)  or Decimal("0")

    suma = subtotal + impuesto + otros_cargos
    diferencia = abs(suma - total)

    return diferencia <= Decimal("0.01")


def apply_exchange_rate(amount: Decimal, moneda: str, tipo_cambio: Decimal) -> Decimal:
    """Aplica tipo_cambio a un monto si la moneda no es CRC.

    Solo usa tipo_cambio como factor multiplicador. No modifica ni toca tipo_cambio.
    Si moneda == CRC o tipo_cambio inválido, retorna el monto original.

    Args:
        amount: Monto en Decimal
        moneda: Código de moneda (ej: "CRC", "USD", "EUR")
        tipo_cambio: Factor de conversión

    Returns:
        Monto convertido a CRC (Decimal)
    """
    if amount is None or not isinstance(amount, Decimal):
        return Decimal("0")

    moneda_clean = str(moneda or "").strip().upper()
    if not moneda_clean or moneda_clean == "CRC":
        return amount

    if tipo_cambio is None or not isinstance(tipo_cambio, Decimal) or tipo_cambio <= Decimal("0"):
        return amount

    return amount * tipo_cambio


def compute_tax_base_rows(
    totals: dict[str, Any],
    visible_columns: list[str] | tuple[str, ...],
    tolerance: Decimal = TAX_BASE_TOLERANCE,
) -> list[tuple[str, Decimal]]:
    """Deriva bases imponibles visibles y base exenta desde los totales acumulados."""
    subtotal_total = parse_decimal_value(totals.get("subtotal")) or Decimal("0")
    iva_otros_total = parse_decimal_value(totals.get("iva_otros")) or Decimal("0")

    rows: list[tuple[str, Decimal]] = []
    gravadas_total = Decimal("0")

    for col_name in visible_columns:
        rate_meta = STANDARD_IVA_BASE_RATES.get(col_name)
        if rate_meta is None:
            continue

        rate_label, rate_decimal = rate_meta
        iva_total = parse_decimal_value(totals.get(col_name)) or Decimal("0")
        if is_effectively_zero(iva_total, tolerance=tolerance):
            continue

        base_amount = iva_total / rate_decimal
        if is_effectively_zero(base_amount, tolerance=tolerance):
            continue

        rows.append((f"Base imponible {rate_label}", base_amount))
        gravadas_total += base_amount

    if is_effectively_zero(iva_otros_total, tolerance=tolerance):
        exenta_amount = subtotal_total - gravadas_total
        if not is_effectively_zero(exenta_amount, tolerance=tolerance):
            rows.append(("Base exenta", exenta_amount))

    return rows
