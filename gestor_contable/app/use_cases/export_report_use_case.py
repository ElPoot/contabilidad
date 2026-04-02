"""Caso de uso: exportar reporte de período a Excel o CSV.

Toda la lógica de extracción de datos, transformación y escritura de archivo
vive aquí. No importa nada de customtkinter ni de gui/.
La vista llama a export_period_report() y recibe éxito (None) o excepción.
"""
from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from gestor_contable.core.classification_utils import classify_transaction
from gestor_contable.core.iva_utils import apply_exchange_rate, parse_decimal_value
from gestor_contable.core.models import FacturaRecord


# ── Helpers internos ─────────────────────────────────────────────────────────

def _parse_date_for_filename(text: str):
    raw = (text or "").strip()
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _month_name_es(dt: datetime) -> str:
    months = {
        1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL", 5: "MAYO", 6: "JUNIO",
        7: "JULIO", 8: "AGOSTO", 9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
    }
    return months.get(dt.month, "MES")


def _format_amount_es(number: Decimal) -> str:
    sign = "-" if number < 0 else ""
    n = abs(number)
    text = f"{n:,.2f}"
    text = text.replace(",", "_").replace(".", ",").replace("_", " ")
    return f"{sign}{text}"


def _safe_excel_sheet_name(raw_name: str, used_names: set[str]) -> str:
    """Sanitiza y hace único un nombre de hoja de Excel (máx. 31 chars)."""
    invalid_chars = {"\\", "/", "*", "?", ":", "[", "]"}
    cleaned = "".join("_" if ch in invalid_chars else ch for ch in str(raw_name or "").strip())
    cleaned = cleaned.strip("'")
    base = (cleaned or "SIN CLASIFICAR")[:31]

    candidate = base
    suffix = 1
    while candidate in used_names:
        suffix_txt = f" ({suffix})"
        allowed = 31 - len(suffix_txt)
        candidate = f"{base[:allowed]}{suffix_txt}"
        suffix += 1

    used_names.add(candidate)
    return candidate


_GASTO_PREFIX = {
    "GASTOS GENERALES": "GG",
    "GASTOS ESPECÍFICOS": "GE",
    "GASTOS ESPECIFICOS": "GE",
}


def _write_gasto_grouped(
    ws, sheet_df, display_cols,
    numeric_columns, text_columns, date_column,
    pretty_headers, owner_name, sheet_name, date_from_label, date_to_label,
    title_fill, subtitle_fill, summary_fill, header_fill, credit_fill,
    title_font, subtitle_font, summary_font, header_font,
):
    """Hoja Gasto -- agrupación por (subtipo, nombre_cuenta).

    Layout por grupo:
        [filas de datos -- sin color]
        [fila subtotal: sumas numéricas + label "GG/GE / NOMBRE" en última col]  <- fill azul
        [fila vacía]

    display_cols: columnas a mostrar.  subtipo/nombre_cuenta se leen del DataFrame
    para agrupar aunque no aparezcan en display_cols.
    """
    import pandas as pd
    from openpyxl.styles import Alignment, Font, PatternFill

    n_cols = len(display_cols)

    # Filas 1-3: título / subtítulo / resumen
    for row in (1, 2, 3):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=n_cols)

    ws.cell(row=1, column=1).value = str(owner_name).upper()
    ws.cell(row=1, column=1).font = title_font
    ws.cell(row=1, column=1).alignment = Alignment(horizontal="center", vertical="center")
    ws.cell(row=1, column=1).fill = title_fill

    ws.cell(row=2, column=1).value = (
        f"REPORTE DE {sheet_name.upper()} - Período: {date_from_label} al {date_to_label}"
    )
    ws.cell(row=2, column=1).font = subtitle_font
    ws.cell(row=2, column=1).alignment = Alignment(horizontal="center", vertical="center")
    ws.cell(row=2, column=1).fill = subtitle_fill

    monto_total = Decimal("0")
    if "total_comprobante" in sheet_df.columns:
        for v in sheet_df["total_comprobante"].dropna():
            try:
                monto_total += Decimal(str(v))
            except Exception:
                pass

    monedas = (
        sorted({str(m).strip() for m in sheet_df["moneda"].dropna() if str(m).strip()})
        if "moneda" in sheet_df.columns else []
    )
    moneda_value = (
        "N/A" if not monedas
        else monedas[0] if len(monedas) == 1
        else "MIXTA: " + ", ".join(monedas)
    )

    ws.cell(row=3, column=1).value = (
        f"Total filas: {len(sheet_df)}   |   Monto Total: {_format_amount_es(monto_total)}   |   "
        f"Moneda: {moneda_value}   |   Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    ws.cell(row=3, column=1).font = summary_font
    ws.cell(row=3, column=1).alignment = Alignment(horizontal="center", vertical="center")
    ws.cell(row=3, column=1).fill = summary_fill

    # Fila 5: encabezados de columna
    for col_idx, col_name in enumerate(display_cols, start=1):
        cell = ws.cell(row=5, column=col_idx)
        cell.value = pretty_headers.get(col_name, col_name.replace("_", " ").title())
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    tipo_col_idx = (display_cols.index("tipo_documento") + 1) if "tipo_documento" in display_cols else None
    numeric_display = [c for c in display_cols if c in numeric_columns and c in display_cols]

    subtotal_fill = PatternFill(fill_type="solid", fgColor="BDD7EE")
    subtotal_font = Font(bold=True)

    group_cols = [c for c in ("subtipo", "nombre_cuenta") if c in sheet_df.columns]
    sort_cols  = group_cols + (["fecha_emision"] if "fecha_emision" in sheet_df.columns else [])
    sorted_df  = sheet_df.sort_values(sort_cols) if sort_cols else sheet_df

    def _safe(v):
        try:
            return None if pd.isna(v) else v
        except (TypeError, ValueError):
            return v

    current_row = 6

    if group_cols:
        for group_keys, group_df in sorted_df.groupby(group_cols, sort=False):
            if isinstance(group_keys, tuple) and len(group_keys) == 2:
                subtipo_val = str(group_keys[0]).strip().upper()
                cuenta_val  = str(group_keys[1]).strip()
            else:
                subtipo_val = ""
                cuenta_val  = str(group_keys).strip()

            group_sums: dict[str, Decimal] = {c: Decimal("0") for c in numeric_display}

            for _, row_data in group_df.iterrows():
                for col_idx, col_name in enumerate(display_cols, start=1):
                    val = _safe(row_data.get(col_name)) if col_name in row_data.index else None
                    cell = ws.cell(row=current_row, column=col_idx)
                    cell.value = val
                    if col_name in text_columns:
                        cell.number_format = "@"
                        cell.value = "" if cell.value is None else str(cell.value)
                    elif col_name == date_column and cell.value is not None:
                        cell.number_format = "dd/mm/yyyy"
                    elif col_name in numeric_columns and cell.value is not None:
                        cell.number_format = "#,##0.00"
                        if isinstance(cell.value, Decimal):
                            cell.value = float(cell.value)

                if tipo_col_idx and ws.cell(row=current_row, column=tipo_col_idx).value == "Nota de Crédito":
                    for c in range(1, n_cols + 1):
                        ws.cell(row=current_row, column=c).fill = credit_fill

                for col_name in numeric_display:
                    tv = _safe(row_data.get(col_name)) if col_name in row_data.index else None
                    try:
                        if tv is not None:
                            group_sums[col_name] += Decimal(str(tv))
                    except Exception:
                        pass

                current_row += 1

            for col_idx in range(1, n_cols + 1):
                ws.cell(row=current_row, column=col_idx).fill = subtotal_fill

            for col_name in numeric_display:
                ci = display_cols.index(col_name) + 1
                tc = ws.cell(row=current_row, column=ci)
                tc.value         = float(group_sums[col_name])
                tc.number_format = "#,##0.00"
                tc.font          = subtotal_font

            prefix      = _GASTO_PREFIX.get(subtipo_val, "")
            cuenta_label = cuenta_val.upper() if cuenta_val else subtipo_val
            label        = f"{prefix} / {cuenta_label}" if prefix else cuenta_label
            lbl = ws.cell(row=current_row, column=n_cols)
            lbl.value     = label
            lbl.font      = subtotal_font
            lbl.alignment = Alignment(horizontal="right", vertical="center")

            current_row += 2  # subtotal + fila vacía

    else:
        for _, row_data in sorted_df.iterrows():
            for col_idx, col_name in enumerate(display_cols, start=1):
                val  = _safe(row_data[col_name])
                cell = ws.cell(row=current_row, column=col_idx)
                cell.value = val
                if col_name in text_columns:
                    cell.number_format = "@"
                    cell.value = "" if cell.value is None else str(cell.value)
                elif col_name == date_column and cell.value is not None:
                    cell.number_format = "dd/mm/yyyy"
                elif col_name in numeric_columns and cell.value is not None:
                    cell.number_format = "#,##0.00"
            current_row += 1

    for col_idx in range(1, n_cols + 1):
        max_len = 0
        for row_idx in range(5, current_row):
            v = ws.cell(row=row_idx, column=col_idx).value
            if v is not None:
                max_len = max(max_len, len(str(v)))
        ws.column_dimensions[
            ws.cell(row=5, column=col_idx).column_letter
        ].width = min(max(max_len + 3, 12), 65)

    ws.freeze_panes = ws["A6"]


# ── API pública ──────────────────────────────────────────────────────────────

def default_export_filename(client_name: str, from_date: str, to_date: str) -> str:
    """Nombre de archivo sugerido para el reporte de exportación."""
    base_dt = _parse_date_for_filename(from_date) or _parse_date_for_filename(to_date) or datetime.now()
    year = base_dt.strftime("%Y")
    month_txt = _month_name_es(base_dt)
    client_clean = (str(client_name or "REPORTE")
                    .replace("/", " ")
                    .replace("\\", " ")
                    .strip())
    if len(client_clean) > 42:
        client_clean = client_clean[:42].strip()
    return f"PF-{year} - {client_clean} - REPORTE - {month_txt}.xlsx"


def export_period_report(
    records: list[FacturaRecord],
    db_records: dict,
    client_cedula: str,
    target_path: Path,
    owner_name: str,
    date_from_label: str,
    date_to_label: str,
) -> None:
    """Exporta los registros del período a Excel (.xlsx) o CSV.

    Args:
        records: lista de FacturaRecord ya filtrados por período y sin omitidos.
        db_records: {clave: {estado, categoria, subtipo, nombre_cuenta}} de la BD.
        client_cedula: cédula del receptor (cliente activo) para classify_transaction.
        target_path: ruta destino (extensión determina formato: .xlsx o .csv).
        owner_name: nombre del cliente para encabezados del Excel.
        date_from_label: fecha de inicio en formato "dd/mm/yyyy" (solo para encabezados).
        date_to_label: fecha de fin en formato "dd/mm/yyyy" (solo para encabezados).

    Raises:
        ValueError: si no hay registros o la extensión no es soportada.
        IOError/OSError: si no se puede escribir el archivo.
    """
    rows: list[dict] = []
    for r in records:
        meta = db_records.get(r.clave, {}) if db_records else {}
        estado = meta.get("estado") or r.estado
        rows.append(
            {
                "clave_numerica": r.clave,
                "tipo_documento": r.tipo_documento,
                "fecha_emision": r.fecha_emision,
                "consecutivo": r.consecutivo,
                "emisor_nombre": r.emisor_nombre,
                "emisor_cedula": r.emisor_cedula,
                "receptor_nombre": r.receptor_nombre,
                "receptor_cedula": r.receptor_cedula,
                "moneda": r.moneda,
                "tipo_cambio": r.tipo_cambio,
                "subtotal": r.subtotal,
                "iva_1": r.iva_1,
                "iva_2": r.iva_2,
                "iva_4": r.iva_4,
                "iva_8": r.iva_8,
                "iva_13": r.iva_13,
                "iva_otros": r.iva_otros,
                "impuesto_total": r.impuesto_total,
                "total_comprobante": r.total_comprobante,
                "estado_hacienda": r.estado_hacienda,
                "detalle_estado_hacienda": r.detalle_estado_hacienda,
                "categoria": str(meta.get("categoria") or ""),
                "subtipo": str(meta.get("subtipo") or ""),
                "nombre_cuenta": str(meta.get("nombre_cuenta") or ""),
                "estado": estado,
                "clasificacion_tx": classify_transaction(r, client_cedula),
            }
        )

    export_columns = [
        "tipo_documento",
        "fecha_emision",
        "consecutivo",
        "emisor_nombre",
        "emisor_cedula",
        "receptor_nombre",
        "receptor_cedula",
        "moneda",
        "tipo_cambio",
        "subtotal",
        "iva_1",
        "iva_2",
        "iva_4",
        "iva_8",
        "iva_13",
        "iva_otros",
        "impuesto_total",
        "total_comprobante",
        "estado_hacienda",
        "detalle_estado_hacienda",
        "categoria",
        "subtipo",
        "nombre_cuenta",
        "estado",
    ]

    _HIDDEN = {"subtipo", "nombre_cuenta", "estado", "categoria", "detalle_estado_hacienda", "clasificacion_tx"}
    display_columns = [c for c in export_columns if c not in _HIDDEN]

    numeric_columns = {
        "subtotal", "tipo_cambio",
        "iva_1", "iva_2", "iva_4", "iva_8", "iva_13", "iva_otros",
        "impuesto_total", "total_comprobante",
    }
    text_columns = {"clave_numerica", "consecutivo", "emisor_cedula", "receptor_cedula"}
    date_column = "fecha_emision"

    pretty_headers = {
        "clave_numerica": "Clave",
        "tipo_documento": "Tipo documento",
        "fecha_emision": "Fecha emisión",
        "consecutivo": "Consecutivo",
        "emisor_nombre": "Emisor",
        "emisor_cedula": "Cédula emisor",
        "receptor_nombre": "Receptor",
        "receptor_cedula": "Cédula receptor",
        "moneda": "Moneda",
        "tipo_cambio": "Tipo cambio",
        "subtotal": "Subtotal",
        "iva_1": "IVA 1%",
        "iva_2": "IVA 2%",
        "iva_4": "IVA 4%",
        "iva_8": "IVA 8%",
        "iva_13": "IVA 13%",
        "impuesto_total": "Impuesto total",
        "total_comprobante": "Total comprobante",
        "estado_hacienda": "Estado Hacienda",
        "detalle_estado_hacienda": "Detalle Estado Hacienda",
        "categoria": "Categoría",
        "subtipo": "Subtipo",
        "nombre_cuenta": "Cuenta",
        "estado": "Estado App 3",
    }

    target = str(target_path)

    if target.lower().endswith(".csv"):
        with open(target, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=export_columns)
            writer.writeheader()
            writer.writerows([{col: row.get(col, "") for col in export_columns} for row in rows])
        return

    # Excel
    import pandas as pd
    from openpyxl.styles import Alignment, Font, PatternFill

    df_all = pd.DataFrame(rows)
    df = df_all[[col for col in export_columns if col in df_all.columns]].copy()

    for col in text_columns:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(" ", "", regex=False).str.replace(",", ".", regex=False),
                errors="coerce",
            )

    amounts_to_convert = {
        "subtotal", "iva_1", "iva_2", "iva_4", "iva_8", "iva_13", "iva_otros",
        "impuesto_total", "total_comprobante"
    }

    if "moneda" in df_all.columns and "tipo_cambio" in df_all.columns:
        for idx in df.index:
            moneda_str = str(df_all.loc[idx, "moneda"] or "").strip().upper()
            if moneda_str and moneda_str != "CRC":
                tc = parse_decimal_value(df_all.loc[idx, "tipo_cambio"])
                if tc and tc > Decimal("0"):
                    for col in amounts_to_convert:
                        if col in df.columns and pd.notna(df.loc[idx, col]):
                            amount_val = df.loc[idx, col]
                            if amount_val and isinstance(amount_val, (int, float)):
                                amount = Decimal(str(amount_val))
                                converted = apply_exchange_rate(amount, moneda_str, tc)
                                df.loc[idx, col] = float(converted)

    if date_column in df.columns:
        df[date_column] = pd.to_datetime(df[date_column], format="%d/%m/%Y", errors="coerce")

    clasificacion_tx = df_all["clasificacion_tx"].fillna("").astype(str).str.strip().str.lower()

    mask_ventas = clasificacion_tx.eq("ingreso")
    mask_egreso = clasificacion_tx.eq("egreso")
    mask_sin_receptor = clasificacion_tx.eq("sin_receptor")

    categoria_upper = df_all["categoria"].fillna("").astype(str).str.strip().str.upper()
    estado_export = df_all["estado"].fillna("").astype(str).str.strip().str.lower()

    mask_gasto = mask_egreso & categoria_upper.eq("GASTOS")
    mask_ognd = categoria_upper.eq("OGND")
    mask_compras = mask_egreso & categoria_upper.eq("COMPRAS")
    mask_activos = mask_egreso & categoria_upper.eq("ACTIVO")
    mask_pendiente = mask_egreso & estado_export.eq("pendiente")

    estado_hacienda_col = df_all["estado_hacienda"].fillna("").astype(str).str.strip()
    mask_rechazados = estado_hacienda_col.eq("Rechazada")

    used_names: set[str] = set()
    sheet_map: dict[str, pd.DataFrame] = {}
    for label, mask in [
        ("Ingresos", mask_ventas),
        ("Compras", mask_compras),
        ("Gastos", mask_gasto),
        ("OGND", mask_ognd),
        ("Activos", mask_activos),
        ("Pendientes", mask_pendiente),
        ("Sin Receptor", mask_sin_receptor),
        ("Rechazados", mask_rechazados),
    ]:
        chunk = df.loc[mask]
        if not chunk.empty:
            sheet_map[_safe_excel_sheet_name(label, used_names)] = chunk.copy()

    if not sheet_map:
        sheet_map[_safe_excel_sheet_name("Reporte", used_names)] = df.copy()

    title_fill = PatternFill(fill_type="solid", fgColor="0B2B66")
    subtitle_fill = PatternFill(fill_type="solid", fgColor="7F7F7F")
    summary_fill = PatternFill(fill_type="solid", fgColor="EDEDED")
    header_fill = PatternFill(fill_type="solid", fgColor="D9E1F2")
    credit_fill = PatternFill(fill_type="solid", fgColor="DAF2D0")
    title_font = Font(bold=True, color="FFFFFF", size=22)
    subtitle_font = Font(bold=True, color="FFFFFF", size=14)
    summary_font = Font(bold=False, color="111111", size=12)
    header_font = Font(bold=True)

    def _filter_iva_cols(cols, sdf):
        """Elimina columnas IVA cuyo valor es todo-cero en el DataFrame dado."""
        IVA_COLS = {"iva_1", "iva_2", "iva_4", "iva_8", "iva_13", "iva_otros"}
        ZERO_VALUES = {"", "0", "0.0", "0,00", "0.00", "nan", "none", "null"}
        result = []
        for col in cols:
            if col not in IVA_COLS:
                result.append(col)
            else:
                col_values = sdf[col].astype(str).str.strip().str.lower()
                if col_values.loc[~col_values.isin(ZERO_VALUES)].any():
                    result.append(col)
        return result

    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        if "Sheet" in writer.book.sheetnames:
            del writer.book["Sheet"]

        for sheet_name, sheet_df in sheet_map.items():
            # Hoja Gastos: layout agrupado especial
            if sheet_name == "Gastos":
                ws = writer.book.create_sheet(title=sheet_name)
                writer.sheets[sheet_name] = ws
                gasto_base = [c for c in display_columns if c not in {"receptor_nombre", "receptor_cedula"} and c in sheet_df.columns]
                gasto_cols = _filter_iva_cols(gasto_base, sheet_df)
                _write_gasto_grouped(
                    ws, sheet_df, gasto_cols,
                    numeric_columns, text_columns, date_column,
                    pretty_headers, owner_name, sheet_name,
                    date_from_label, date_to_label,
                    title_fill, subtitle_fill, summary_fill,
                    header_fill, credit_fill,
                    title_font, subtitle_font, summary_font, header_font,
                )
                continue

            # Hoja OGND: layout agrupado especial
            if sheet_name == "OGND":
                ws = writer.book.create_sheet(title=sheet_name)
                writer.sheets[sheet_name] = ws
                ognd_cols = _filter_iva_cols([c for c in display_columns if c in sheet_df.columns], sheet_df)
                _write_gasto_grouped(
                    ws, sheet_df, ognd_cols,
                    numeric_columns, text_columns, date_column,
                    pretty_headers, owner_name, sheet_name,
                    date_from_label, date_to_label,
                    title_fill, subtitle_fill, summary_fill,
                    header_fill, credit_fill,
                    title_font, subtitle_font, summary_font, header_font,
                )
                continue

            # Hoja Rechazados: incluye detalle_estado_hacienda
            if sheet_name == "Rechazados":
                rechazados_hidden = {"subtipo", "nombre_cuenta", "estado", "categoria", "receptor_nombre", "receptor_cedula"}
                rechazados_cols = [c for c in export_columns
                                   if c not in rechazados_hidden and c in sheet_df.columns]
                IVA_COLS = {"iva_1", "iva_2", "iva_4", "iva_8", "iva_13", "iva_otros"}
                ZERO_VALUES = {"", "0", "0.0", "0,00", "0.00", "nan", "none", "null"}
                visible_rechazados = []
                for col in rechazados_cols:
                    if col not in IVA_COLS:
                        visible_rechazados.append(col)
                    else:
                        col_values = sheet_df[col].astype(str).str.strip().str.lower()
                        has_nonzero = col_values.loc[~col_values.isin(ZERO_VALUES)].any()
                        if has_nonzero:
                            visible_rechazados.append(col)
                display_df = sheet_df[visible_rechazados].rename(
                    columns={col: pretty_headers.get(col, col.replace("_", " ").title()) for col in visible_rechazados}
                )
                display_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=4)
                ws = writer.sheets[sheet_name]

                max_col = ws.max_column if ws.max_column > 0 else 1
                ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)
                ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max_col)
                ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=max_col)

                title_cell = ws.cell(row=1, column=1)
                title_cell.value = str(owner_name).upper()
                title_cell.font = title_font
                title_cell.alignment = Alignment(horizontal="center", vertical="center")
                title_cell.fill = title_fill

                subtitle_cell = ws.cell(row=2, column=1)
                subtitle_cell.value = f"REPORTE DE {sheet_name.upper()} - Período: {date_from_label} al {date_to_label}"
                subtitle_cell.font = subtitle_font
                subtitle_cell.alignment = Alignment(horizontal="center", vertical="center")
                subtitle_cell.fill = subtitle_fill

                monto_total = Decimal("0")
                if "total_comprobante" in sheet_df.columns:
                    valid_amounts = []
                    for value in sheet_df["total_comprobante"].dropna().tolist():
                        try:
                            valid_amounts.append(Decimal(str(value)))
                        except Exception:
                            continue
                    if valid_amounts:
                        monto_total = sum(valid_amounts, Decimal("0"))

                monedas = (
                    sorted({str(m).strip() for m in sheet_df["moneda"].dropna().tolist() if str(m).strip()})
                    if "moneda" in sheet_df.columns else []
                )
                moneda_value = (
                    "N/A" if not monedas
                    else monedas[0] if len(monedas) == 1
                    else "MIXTA: " + ", ".join(monedas)
                )
                generated = datetime.now().strftime("%d/%m/%Y %H:%M")

                summary_cell = ws.cell(row=3, column=1)
                summary_cell.value = (
                    f"Total filas: {len(sheet_df)}   |   Monto Total: {_format_amount_es(monto_total)}   |   "
                    f"Moneda: {moneda_value}   |   Generado: {generated}"
                )
                summary_cell.font = summary_font
                summary_cell.alignment = Alignment(horizontal="center", vertical="center")
                summary_cell.fill = summary_fill

                header_row = 5
                for col_idx in range(1, ws.max_column + 1):
                    header_cell = ws.cell(row=header_row, column=col_idx)
                    header_cell.font = header_font
                    header_cell.alignment = Alignment(horizontal="center", vertical="center")
                    header_cell.fill = header_fill

                for col_idx, col_name in enumerate(visible_rechazados, start=1):
                    for row_idx in range(header_row + 1, len(sheet_df) + header_row + 1):
                        cell = ws.cell(row=row_idx, column=col_idx)
                        if col_name in text_columns:
                            cell.number_format = "@"
                            cell.value = "" if cell.value is None else str(cell.value)
                        elif col_name == date_column and cell.value is not None:
                            cell.number_format = "dd/mm/yyyy"
                        elif col_name in numeric_columns and cell.value is not None:
                            cell.number_format = "#,##0.00"
                            if isinstance(cell.value, Decimal):
                                cell.value = float(cell.value)

                tipo_idx = (
                    visible_rechazados.index("tipo_documento") + 1
                    if "tipo_documento" in visible_rechazados else None
                )
                if tipo_idx is not None:
                    for row_idx in range(header_row + 1, len(sheet_df) + header_row + 1):
                        if ws.cell(row=row_idx, column=tipo_idx).value == "Nota de Crédito":
                            for col in range(1, ws.max_column + 1):
                                ws.cell(row=row_idx, column=col).fill = credit_fill

                for col_idx in range(1, ws.max_column + 1):
                    max_len = 0
                    for row_idx in range(header_row, ws.max_row + 1):
                        value = ws.cell(row=row_idx, column=col_idx).value
                        if value is None:
                            continue
                        max_len = max(max_len, len(str(value)))
                    ws.column_dimensions[ws.cell(row=header_row, column=col_idx).column_letter].width = min(max(max_len + 3, 12), 65)

                ws.freeze_panes = ws["A6"]
                continue

            # Hojas normales
            exclude_receptor = sheet_name != "Sin Receptor"
            visible_cols_base = [
                c for c in display_columns
                if c in sheet_df.columns and not (exclude_receptor and c in {"receptor_nombre", "receptor_cedula"})
            ]

            visible_cols_filtered = _filter_iva_cols(visible_cols_base, sheet_df)

            display_df = sheet_df[visible_cols_filtered].rename(
                columns={col: pretty_headers.get(col, col.replace("_", " ").title()) for col in visible_cols_filtered}
            )
            display_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=4)
            ws = writer.sheets[sheet_name]

            max_col = ws.max_column if ws.max_column > 0 else 1
            ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)
            ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max_col)
            ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=max_col)

            title_cell = ws.cell(row=1, column=1)
            title_cell.value = str(owner_name).upper()
            title_cell.font = title_font
            title_cell.alignment = Alignment(horizontal="center", vertical="center")
            title_cell.fill = title_fill

            subtitle_cell = ws.cell(row=2, column=1)
            subtitle_cell.value = f"REPORTE DE {sheet_name.upper()} - Período: {date_from_label} al {date_to_label}"
            subtitle_cell.font = subtitle_font
            subtitle_cell.alignment = Alignment(horizontal="center", vertical="center")
            subtitle_cell.fill = subtitle_fill

            monto_total = Decimal("0")
            if "total_comprobante" in sheet_df.columns:
                valid_amounts = []
                for value in sheet_df["total_comprobante"].dropna().tolist():
                    try:
                        valid_amounts.append(Decimal(str(value)))
                    except Exception:
                        continue
                if valid_amounts:
                    monto_total = sum(valid_amounts, Decimal("0"))

            monedas = (
                sorted({str(m).strip() for m in sheet_df["moneda"].dropna().tolist() if str(m).strip()})
                if "moneda" in sheet_df.columns
                else []
            )
            moneda_value = (
                "N/A" if not monedas
                else monedas[0] if len(monedas) == 1
                else "MIXTA: " + ", ".join(monedas)
            )
            generated = datetime.now().strftime("%d/%m/%Y %H:%M")

            summary_cell = ws.cell(row=3, column=1)
            summary_cell.value = (
                f"Total filas: {len(sheet_df)}   |   Monto Total: {_format_amount_es(monto_total)}   |   "
                f"Moneda: {moneda_value}   |   Generado: {generated}"
            )
            summary_cell.font = summary_font
            summary_cell.alignment = Alignment(horizontal="center", vertical="center")
            summary_cell.fill = summary_fill

            header_row = 5
            for col_idx in range(1, ws.max_column + 1):
                cell = ws.cell(row=header_row, column=col_idx)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center", vertical="center")

            tipo_idx = (
                visible_cols_filtered.index("tipo_documento") + 1
                if "tipo_documento" in visible_cols_filtered else None
            )

            for col_idx, col_name in enumerate(visible_cols_filtered, start=1):
                for row_idx in range(header_row + 1, len(sheet_df) + header_row + 1):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    if col_name in text_columns:
                        cell.number_format = "@"
                        cell.value = "" if cell.value is None else str(cell.value)
                    elif col_name == date_column and cell.value is not None:
                        cell.number_format = "dd/mm/yyyy"
                    elif col_name in numeric_columns and cell.value is not None:
                        cell.number_format = "#,##0.00"
                        if isinstance(cell.value, Decimal):
                            cell.value = float(cell.value)

            if tipo_idx is not None:
                for row_idx in range(header_row + 1, len(sheet_df) + header_row + 1):
                    if ws.cell(row=row_idx, column=tipo_idx).value == "Nota de Crédito":
                        for col in range(1, ws.max_column + 1):
                            ws.cell(row=row_idx, column=col).fill = credit_fill

            for col_idx in range(1, ws.max_column + 1):
                max_len = 0
                for row_idx in range(header_row, ws.max_row + 1):
                    value = ws.cell(row=row_idx, column=col_idx).value
                    if value is None:
                        continue
                    max_len = max(max_len, len(str(value)))
                ws.column_dimensions[ws.cell(row=header_row, column=col_idx).column_letter].width = min(max(max_len + 3, 12), 65)

            ws.freeze_panes = ws["A6"]
