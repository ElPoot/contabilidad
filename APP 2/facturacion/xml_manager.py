"""Capa de negocio para parsing XML, cache y normalización de datos."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from .config import LOGGER, resolve_audit_log_dir, resolve_hacienda_cache_db_path

try:
    import requests
except ModuleNotFoundError:
    requests = None

class CRXMLManager:
    """Gestiona carga, identificación y aplanamiento de XML de Hacienda CR."""
    HACIENDA_API_URL = "https://api.hacienda.go.cr/fe/ae?identificacion={ident}"

    def __init__(self) -> None:
        self.last_duplicate_count = 0
        self.cache_db_path = resolve_hacienda_cache_db_path()
        self._ensure_hacienda_cache_db()

    DOCUMENT_TYPES = {
        "FacturaElectronica": "Factura Electrónica",
        "TiqueteElectronico": "Tiquete Electrónico",
        "NotaCreditoElectronica": "Nota de Crédito",
        "NotaDebitoElectronica": "Nota de Débito",
        "MensajeReceptor": "Mensaje de Receptor",
        "MensajeHacienda": "Mensaje de Hacienda",
    }
    DOCUMENT_ROOT_INVOICE_TYPES = {
        "FacturaElectronica",
        "TiqueteElectronico",
        "NotaCreditoElectronica",
        "NotaDebitoElectronica",
    }
    EMPTY_TEXT_MARKERS = {"nan", "none", "null", "n/a", "na", "s/n", "-"}
    IVA_TARIFA_CODE_MAP = {
        "01": "0",
        "02": "1",
        "03": "2",
        "04": "4",
        "05": "8",
        "06": "10",
        "07": "0",
        "08": "13",
    }
    def parse_xml_file(self, xml_path: str | Path) -> dict[str, Any]:
        """Parsea un XML en modo streaming para reducir uso de RAM."""
        xml_path = Path(xml_path)
        flat_xml, root_name = self.flatten_xml_stream(xml_path)
        flat_data: dict[str, Any] = {
            "archivo": xml_path.name,
            "ruta": str(xml_path),
            "carpeta": str(xml_path.parent),
            "xml_hash": self.compute_file_hash(xml_path),
            "documento_root": root_name,
            "tipo_documento": self.DOCUMENT_TYPES.get(root_name, f"Desconocido ({root_name})"),
        }
        flat_data.update(flat_xml)
        flat_data["clave_numerica"] = self.extract_first_non_empty(
            flat_data,
            [
                "FacturaElectronica_Clave",
                "TiqueteElectronico_Clave",
                "NotaCreditoElectronica_Clave",
                "NotaDebitoElectronica_Clave",
                "MensajeReceptor_Clave",
                "MensajeHacienda_Clave",
            ],
        )
        flat_data["estado_hacienda_xml"] = self.extract_first_non_empty(
            flat_data,
            ["MensajeHacienda_IndEstado", "MensajeHacienda_EstadoMensaje", "MensajeReceptor_Mensaje"],
        )
        flat_data["detalle_estado_hacienda_xml"] = self.extract_first_non_empty(
            flat_data,
            ["MensajeHacienda_DetalleMensaje", "MensajeReceptor_DetalleMensaje", "MensajeHacienda_Mensaje"],
        )
        # Campos operativos solicitados.
        raw_fecha = self.pick_doc_value(flat_data, root_name, "FechaEmision")
        flat_data["fecha_emision"] = self.format_date_ddmmyyyy(raw_fecha)
        flat_data["emisor_nombre"] = self.pick_doc_value(flat_data, root_name, "Emisor_Nombre")
        flat_data["emisor_cedula"] = self.pick_doc_value(flat_data, root_name, "Emisor_Identificacion_Numero")
        flat_data["receptor_nombre"] = self.pick_doc_value(flat_data, root_name, "Receptor_Nombre")
        flat_data["receptor_cedula"] = self.pick_doc_value(flat_data, root_name, "Receptor_Identificacion_Numero")
        flat_data["moneda"] = self.pick_doc_value(
            flat_data,
            root_name,
            "ResumenFactura_CodigoTipoMoneda_CodigoMoneda",
        )
        flat_data["tipo_cambio"] = self.pick_doc_value(
            flat_data,
            root_name,
            "ResumenFactura_CodigoTipoMoneda_TipoCambio",
        )
        flat_data["consecutivo"] = self.pick_doc_value(flat_data, root_name, "NumeroConsecutivo")
        flat_data["subtotal"] = self.pick_doc_value(flat_data, root_name, "ResumenFactura_TotalVentaNeta")
        flat_data["total_comprobante"] = self.pick_doc_value(flat_data, root_name, "ResumenFactura_TotalComprobante")
        impuestos = self.extract_iva_breakdown(flat_data, root_name, xml_path=xml_path)
        flat_data.update(impuestos)
        for amount_col in ["subtotal", "tipo_cambio", "iva_1", "iva_2", "iva_4", "iva_8", "iva_13", "iva_otros", "impuesto_total", "total_comprobante"]:
            default_zero = amount_col.startswith("iva_")
            flat_data[amount_col] = self.normalize_amount_text(flat_data.get(amount_col), default_zero=default_zero)

        if root_name == "NotaCreditoElectronica":
            for amount_col in ["subtotal", "iva_1", "iva_2", "iva_4", "iva_8", "iva_13", "iva_otros", "impuesto_total", "total_comprobante"]:
                flat_data[amount_col] = self.ensure_negative_amount(flat_data.get(amount_col))

        # Compatibilidad con filtros existentes de UI.
        flat_data["cliente_nombre"] = self.extract_first_non_empty(
            flat_data,
            ["receptor_nombre", "emisor_nombre", "DetalleServicio_LineaDetalle_Detalle"],
        )
        flat_data["cliente_cedula"] = self.extract_first_non_empty(
            flat_data,
            ["receptor_cedula", "emisor_cedula", "Receptor_IdentificacionExtranjero"],
        )
        return flat_data
    def pick_doc_value(self, data: dict[str, Any], root_name: str, suffix: str) -> str:
        keys: list[str] = []
        if root_name:
            keys.append(f"{root_name}_{suffix}")
        keys.extend(
            f"{doc}_{suffix}" for doc in self.DOCUMENT_ROOT_INVOICE_TYPES if f"{doc}_{suffix}" not in keys
        )
        keys.append(suffix)
        return self.extract_first_non_empty(data, keys)
    def extract_iva_breakdown(self, data: dict[str, Any], root_name: str, xml_path: Path | None = None) -> dict[str, str]:
        """Calcula IVA por tarifa priorizando desglose de resumen cuando exista."""
        summary_breakdown = self.extract_iva_from_summary_breakdown_xml(xml_path) if xml_path else None
        if summary_breakdown is not None:
            summary_breakdown["impuesto_total"] = self.pick_doc_value(data, root_name, "ResumenFactura_TotalImpuesto")
            return summary_breakdown

        tarifa_detalle = self.pick_doc_value(data, root_name, "DetalleServicio_LineaDetalle_Impuesto_Tarifa")
        impuesto_monto_detalle = self.pick_doc_value(data, root_name, "DetalleServicio_LineaDetalle_Impuesto_Monto")
        impuesto_neto_detalle = self.pick_doc_value(data, root_name, "DetalleServicio_LineaDetalle_ImpuestoNeto")

        tarifa_values = self.parse_pipe_values(tarifa_detalle)
        monto_values = self.parse_pipe_values(impuesto_monto_detalle)
        neto_values = self.parse_pipe_values(impuesto_neto_detalle)

        amount_values = neto_values if len(neto_values) == len(tarifa_values) and neto_values else monto_values

        iva_map: dict[str, list[str]] = {}
        for idx, tarifa in enumerate(tarifa_values):
            monto = amount_values[idx] if idx < len(amount_values) else ""
            normalized_rate = self.normalize_tax_rate(tarifa)
            if not normalized_rate or not monto:
                continue
            iva_map.setdefault(normalized_rate, []).append(monto)

        result: dict[str, str] = {
            "iva_1": self.sum_decimal_strings(iva_map.get("1", [])),
            "iva_2": self.sum_decimal_strings(iva_map.get("2", [])),
            "iva_4": self.sum_decimal_strings(iva_map.get("4", [])),
            "iva_8": self.sum_decimal_strings(iva_map.get("8", [])),
            "iva_13": self.sum_decimal_strings(iva_map.get("13", [])),
            "iva_otros": self.sum_decimal_strings(
                [m for t, montos in iva_map.items() if t not in {"1", "2", "4", "8", "13"} for m in montos]
            ),
        }
        result["impuesto_total"] = self.pick_doc_value(data, root_name, "ResumenFactura_TotalImpuesto")
        return result

    def extract_iva_from_summary_breakdown_xml(self, xml_path: Path) -> dict[str, str] | None:
        """Extrae IVA desde nodos ResumenFactura/TotalDesgloseImpuesto."""
        buckets: dict[str, list[str]] = {
            "1": [],
            "2": [],
            "4": [],
            "8": [],
            "13": [],
            "otros": [],
        }
        found_breakdown = False

        for _event, elem in ET.iterparse(xml_path, events=("end",)):
            if self.local_name(elem.tag) != "TotalDesgloseImpuesto":
                continue

            found_breakdown = True
            node_data = {self.local_name(child.tag): (child.text or "").strip() for child in elem}
            codigo = node_data.get("Codigo", "")
            tarifa_code = node_data.get("CodigoTarifaIVA", "")
            monto = node_data.get("TotalMontoImpuesto", "")
            if not monto:
                elem.clear()
                continue

            mapped_rate = self.IVA_TARIFA_CODE_MAP.get(tarifa_code, "")
            if mapped_rate in {"1", "2", "4", "8", "13"}:
                buckets[mapped_rate].append(monto)
            elif codigo == "01":
                buckets["otros"].append(monto)
            else:
                buckets["otros"].append(monto)
            elem.clear()

        if not found_breakdown:
            return None

        return {
            "iva_1": self.sum_decimal_strings(buckets["1"]),
            "iva_2": self.sum_decimal_strings(buckets["2"]),
            "iva_4": self.sum_decimal_strings(buckets["4"]),
            "iva_8": self.sum_decimal_strings(buckets["8"]),
            "iva_13": self.sum_decimal_strings(buckets["13"]),
            "iva_otros": self.sum_decimal_strings(buckets["otros"]),
        }

    @staticmethod
    def normalize_tax_rate(raw_rate: Any) -> str:
        text = str(raw_rate or "").strip().replace(",", ".")
        if not text:
            return ""
        try:
            numeric = float(text)
        except ValueError:
            return ""
        if numeric.is_integer():
            return str(int(numeric))
        return f"{numeric:.2f}".rstrip("0").rstrip(".")
    @staticmethod
    def parse_pipe_values(raw_value: Any) -> list[str]:
        if raw_value is None:
            return []
        return [part.strip() for part in str(raw_value).split("|") if part.strip()]
    def sum_decimal_strings(self, values: list[str]) -> str:
        """Suma valores decimales sin perder precisión por uso de float."""
        total = Decimal("0")
        found_valid = False
        for value in values:
            parsed = self.parse_decimal_value(value)
            if parsed is None:
                continue
            total += parsed
            found_valid = True
        return self.decimal_to_local_text(total) if found_valid else ""
    def _ensure_hacienda_cache_db(self) -> None:
        with sqlite3.connect(self.cache_db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hacienda_cache (
                    identificacion TEXT PRIMARY KEY,
                    razon_social TEXT,
                    raw_json TEXT,
                    updated_at INTEGER
                )
                """
            )
            conn.commit()

    @staticmethod
    def normalize_identification(raw_ident: Any) -> str:
        return "".join(ch for ch in str(raw_ident or "") if ch.isdigit())

    def _cache_get_name(self, ident: str) -> str:
        with sqlite3.connect(self.cache_db_path) as conn:
            row = conn.execute("SELECT razon_social FROM hacienda_cache WHERE identificacion = ?", (ident,)).fetchone()
            return str(row[0] or "") if row else ""

    def _cache_get_names_bulk(self, identifiers: list[str]) -> dict[str, str]:
        """Obtiene nombres en lote desde sqlite para reducir overhead de conexiones."""
        if not identifiers:
            return {}
        placeholders = ",".join("?" for _ in identifiers)
        query = f"SELECT identificacion, razon_social FROM hacienda_cache WHERE identificacion IN ({placeholders})"
        with sqlite3.connect(self.cache_db_path) as conn:
            rows = conn.execute(query, tuple(identifiers)).fetchall()
        return {str(row[0]): str(row[1] or "") for row in rows}

    def _cache_put_name(self, ident: str, razon_social: str, raw_json: dict[str, Any] | None = None) -> None:
        with sqlite3.connect(self.cache_db_path) as conn:
            conn.execute(
                """
                INSERT INTO hacienda_cache(identificacion, razon_social, raw_json, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(identificacion) DO UPDATE SET
                    razon_social=excluded.razon_social,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (ident, razon_social, json.dumps(raw_json, ensure_ascii=False) if raw_json else None, int(time.time())),
            )
            conn.commit()

    def _fetch_hacienda_name(self, ident: str) -> str:
        if requests is None:
            return ""

        url = self.HACIENDA_API_URL.format(ident=ident)
        for attempt in range(3):
            try:
                response = requests.get(url, timeout=8)
            except requests.RequestException:
                LOGGER.warning("Error de red consultando Hacienda para %s (intento %s)", ident, attempt + 1)
                time.sleep(0.6 * (attempt + 1))
                continue

            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    LOGGER.warning("Respuesta JSON inválida para identificación %s", ident)
                    return ""
                name = str(payload.get("nombre") or payload.get("razonSocial") or payload.get("razon_social") or "").strip()
                if name:
                    name_upper = name.upper()
                    self._cache_put_name(ident, name_upper, payload)
                    return name_upper
                self._cache_put_name(ident, "", payload)
                return ""

            if response.status_code in (404, 204):
                self._cache_put_name(ident, "", None)
                return ""

            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(0.8 * (attempt + 1))
                continue
            return ""
        return ""

    def resolve_party_name(self, ident: Any, fallback_name: Any) -> str:
        """Resuelve nombre desde cache/API usando identificación y fallback."""
        clean_ident = self.normalize_identification(ident)
        fallback = str(fallback_name or "").strip().upper()
        if not clean_ident:
            return fallback

        cached = self._cache_get_name(clean_ident)
        if cached:
            return cached.upper()

        fetched = self._fetch_hacienda_name(clean_ident)
        if fetched:
            return fetched.upper()

        if fallback:
            self._cache_put_name(clean_ident, fallback, None)
        return fallback

    def resolve_party_names_in_dataframe(self, df: pd.DataFrame, max_workers: int = 8) -> pd.DataFrame:
        """Completa nombres de emisor/receptor en paralelo sin bloquear parsing XML."""
        if df.empty:
            return df

        working_df = df.copy()
        id_columns = [
            ("emisor_cedula", "emisor_nombre"),
            ("receptor_cedula", "receptor_nombre"),
        ]

        ids_to_lookup: set[str] = set()
        for id_col, _ in id_columns:
            if id_col not in working_df.columns:
                continue
            normalized = working_df[id_col].map(self.normalize_identification)
            ids_to_lookup.update({value for value in normalized.tolist() if value})

        if not ids_to_lookup:
            return working_df

        resolved_map: dict[str, str] = {}
        sorted_ids = sorted(ids_to_lookup)
        cached_map = self._cache_get_names_bulk(sorted_ids)
        ids_to_fetch: list[str] = []
        for ident in sorted_ids:
            cached = str(cached_map.get(ident, "")).strip()
            if cached:
                resolved_map[ident] = cached.upper()
            else:
                ids_to_fetch.append(ident)

        if ids_to_fetch and requests is not None:
            LOGGER.info("Consultando Hacienda para %s identificaciones en paralelo.", len(ids_to_fetch))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {executor.submit(self._fetch_hacienda_name, ident): ident for ident in ids_to_fetch}
                for future in as_completed(future_map):
                    ident = future_map[future]
                    try:
                        fetched_name = future.result()
                    except Exception:  # noqa: BLE001
                        LOGGER.exception("Fallo resolviendo identificación %s", ident)
                        continue
                    if fetched_name:
                        resolved_map[ident] = fetched_name.upper()

        for id_col, name_col in id_columns:
            if id_col not in working_df.columns:
                continue
            if name_col not in working_df.columns:
                working_df[name_col] = ""

            normalized_ids = working_df[id_col].map(self.normalize_identification)
            fallback_names = working_df[name_col].fillna("").astype(str).str.strip().str.upper()
            looked_up = normalized_ids.map(resolved_map).fillna("")
            working_df[name_col] = looked_up.where(looked_up.ne(""), fallback_names)

        return working_df

    @staticmethod
    def compute_file_hash(xml_path: Path) -> str:
        hasher = hashlib.sha256()
        with xml_path.open("rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()
    @staticmethod
    def format_date_ddmmyyyy(raw_date: Any) -> str:
        value = str(raw_date or "").strip()
        if not value:
            return ""

        clean = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(clean).strftime("%d/%m/%Y")
        except ValueError:
            pass

        for pattern, token_len in [
            ("%Y-%m-%dT%H:%M:%S", 19),
            ("%Y-%m-%d %H:%M:%S", 19),
            ("%Y-%m-%d", 10),
            ("%d/%m/%Y", 10),
        ]:
            try:
                return datetime.strptime(clean[:token_len], pattern).strftime("%d/%m/%Y")
            except ValueError:
                continue

        return value
    @staticmethod
    def parse_decimal_value(raw_value: Any) -> Decimal | None:
        value = str(raw_value or "").strip()
        if not value:
            return None

        cleaned = value.replace(" ", "")
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
            return None

    @staticmethod
    def decimal_to_local_text(number: Decimal) -> str:
        """Convierte Decimal a texto local sin notación científica ni redondeo extra."""
        plain = format(number, "f")
        if "." in plain:
            plain = plain.rstrip("0").rstrip(".")
        return plain.replace(".", ",")

    @classmethod
    def normalize_amount_text(cls, raw_value: Any, default_zero: bool = False) -> str:
        """Normaliza montos preservando precisión original y separador decimal local."""
        number = cls.parse_decimal_value(raw_value)
        if number is None:
            return "0" if default_zero else ""
        return cls.decimal_to_local_text(number)

    @classmethod
    def ensure_negative_amount(cls, raw_value: Any) -> str:
        """Garantiza signo negativo en notas de crédito preservando precisión."""
        number = cls.parse_decimal_value(raw_value)
        if number is None:
            return "0"
        return cls.decimal_to_local_text(-abs(number))
    def flatten_xml_stream(self, xml_path: Path) -> tuple[dict[str, str], str]:
        """Aplana XML vía iterparse (CPU/RAM más eficiente para lotes grandes)."""
        output: dict[str, str] = {}
        stack: list[str] = []
        root_name = ""
        context = ET.iterparse(xml_path, events=("start", "end"))
        for event, elem in context:
            tag_name = self.local_name(elem.tag)
            if event == "start":
                stack.append(tag_name)
                if not root_name:
                    root_name = tag_name
                continue
            text = self.normalize_text(elem.text)
            if text and len(elem) == 0:
                key = "_".join(stack)
                if key in output:
                    output[key] = f"{output[key]} | {text}"
                else:
                    output[key] = text
            stack.pop()
            elem.clear()
        return output, root_name
    def load_xml_folder(self, folder_path: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Carga XML de forma segura y retorna DataFrame junto a reporte de auditoría."""
        started_at = time.perf_counter()
        folder = Path(folder_path)
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(f"La carpeta no existe o no es válida: {folder}")

        xml_files = sorted(folder.rglob("*.xml"))
        rows: list[dict[str, Any]] = []
        if not xml_files:
            report = self._build_audit_report(
                rows=[],
                total_files_found=0,
                duplicates=[],
                processing_time_seconds=0.0,
            )
            self._persist_audit_report(report)
            return pd.DataFrame(), report

        max_workers = max(2, min(8, (os.cpu_count() or 2)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self._safe_parse_xml_file, xml_file) for xml_file in xml_files]
            for future in as_completed(futures):
                rows.append(future.result())

        df = pd.DataFrame(rows)
        associated = self.associate_hacienda_messages(df)
        comprobantes = self.filter_comprobante_rows(associated)
        deduped, duplicate_files = self.remove_duplicate_hashes_with_audit(comprobantes)
        enriched = self.resolve_party_names_in_dataframe(deduped)
        optimized = self.optimize_dataframe_memory(enriched)

        processing_time_seconds = round(time.perf_counter() - started_at, 3)
        report = self._build_audit_report(
            rows=rows,
            total_files_found=len(xml_files),
            duplicates=duplicate_files,
            processing_time_seconds=processing_time_seconds,
        )
        self._persist_audit_report(report)
        return optimized, report

    def _safe_parse_xml_file(self, xml_file: Path) -> dict[str, Any]:
        """Parsea un XML individual encapsulando errores para procesamiento paralelo."""
        try:
            row = self.parse_xml_file(xml_file)
            row["_process_status"] = "ok"
            return row
        except ET.ParseError as exc:
            LOGGER.warning("XML inválido en %s: %s", xml_file, exc)
            return {
                "archivo": xml_file.name,
                "ruta": str(xml_file),
                "carpeta": str(xml_file.parent),
                "tipo_documento": "XML inválido",
                "error": f"ParseError: {exc}",
                "_process_status": "invalid_xml",
            }
        except (OSError, ValueError, PermissionError) as exc:
            LOGGER.exception("Error procesando %s", xml_file)
            return {
                "archivo": xml_file.name,
                "ruta": str(xml_file),
                "carpeta": str(xml_file.parent),
                "tipo_documento": "Error",
                "error": str(exc),
                "_process_status": "failed",
            }

    def remove_duplicate_hashes_with_audit(self, df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, str]]]:
        """Elimina duplicados exactos por hash y retorna detalle de descartados."""
        self.last_duplicate_count = 0
        if df.empty or "xml_hash" not in df.columns:
            return df, []

        duplicate_files: list[dict[str, str]] = []
        first_seen: dict[str, dict[str, str]] = {}
        keep_rows: list[int] = []
        for idx, row in df.iterrows():
            file_hash = str(row.get("xml_hash", ""))
            if not file_hash:
                keep_rows.append(idx)
                continue
            if file_hash not in first_seen:
                first_seen[file_hash] = {
                    "archivo": str(row.get("archivo", "")),
                    "ruta": str(row.get("ruta", "")),
                }
                keep_rows.append(idx)
                continue
            duplicate_files.append(
                {
                    "archivo": str(row.get("archivo", "")),
                    "ruta": str(row.get("ruta", "")),
                    "hash": file_hash,
                    "original": first_seen[file_hash]["archivo"],
                    "original_ruta": first_seen[file_hash]["ruta"],
                }
            )

        dedup = df.loc[keep_rows].copy()
        self.last_duplicate_count = len(df) - len(dedup)
        return dedup, duplicate_files

    def _build_audit_report(
        self,
        rows: list[dict[str, Any]],
        total_files_found: int,
        duplicates: list[dict[str, str]],
        processing_time_seconds: float,
    ) -> dict[str, Any]:
        """Construye reporte de auditoría del lote de XML procesado."""
        failed_files = [
            {
                "archivo": str(row.get("archivo", "")),
                "ruta": str(row.get("ruta", "")),
                "error": str(row.get("error", "Error desconocido")),
            }
            for row in rows
            if str(row.get("_process_status", "")).lower() in {"failed", "invalid_xml"}
        ]
        invalid_xml_files = sum(1 for row in rows if str(row.get("_process_status", "")).lower() == "invalid_xml")
        successfully_processed = sum(1 for row in rows if str(row.get("_process_status", "")).lower() == "ok")

        files_by_type: dict[str, int] = {}
        for row in rows:
            doc_type = str(row.get("tipo_documento", "Desconocido") or "Desconocido")
            files_by_type[doc_type] = files_by_type.get(doc_type, 0) + 1

        report: dict[str, Any] = {
            "total_files_found": total_files_found,
            "successfully_processed": successfully_processed,
            "failed_files": failed_files,
            "duplicate_files": duplicates,
            "invalid_xml_files": invalid_xml_files,
            "processing_time_seconds": processing_time_seconds,
            "files_by_type": dict(sorted(files_by_type.items(), key=lambda item: item[0])),
            "files_by_status": {
                "ok": successfully_processed,
                "failed": len(failed_files),
                "duplicates": len(duplicates),
                "invalid_xml": invalid_xml_files,
            },
        }
        return report

    def _persist_audit_report(self, report: dict[str, Any]) -> None:
        """Guarda cada reporte en data/audit_logs para auditoría histórica."""
        try:
            audit_dir = resolve_audit_log_dir()
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = audit_dir / f"audit_{stamp}.json"
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            LOGGER.exception("No se pudo guardar el reporte de auditoría")

    def associate_hacienda_messages(self, df: pd.DataFrame) -> pd.DataFrame:
        """Asocia comprobantes con MensajeHacienda por clave numérica."""
        if df.empty:
            return df
        df = df.copy()
        for col in [
            "estado_hacienda",
            "detalle_estado_hacienda",
            "clave_numerica",
            "estado_hacienda_xml",
            "detalle_estado_hacienda_xml",
            "documento_root",
        ]:
            if col not in df.columns:
                df[col] = ""
        message_rows = df[
            (df["documento_root"].astype(str) == "MensajeHacienda")
            & (df["clave_numerica"].astype(str).str.len() > 0)
        ].copy()
        if not message_rows.empty:
            message_rows["estado_hacienda"] = message_rows["estado_hacienda_xml"].apply(
                self.normalize_hacienda_status
            )
            status_map = (
                message_rows.sort_values(by="archivo")
                .drop_duplicates(subset=["clave_numerica"], keep="last")
                [["clave_numerica", "estado_hacienda", "detalle_estado_hacienda_xml"]]
                .set_index("clave_numerica")
            )
            is_invoice = df["documento_root"].astype(str).isin(self.DOCUMENT_ROOT_INVOICE_TYPES)
            matched = df["clave_numerica"].astype(str).map(status_map["estado_hacienda"])
            matched_detail = df["clave_numerica"].astype(str).map(status_map["detalle_estado_hacienda_xml"])
            df.loc[is_invoice, "estado_hacienda"] = matched[is_invoice].fillna("")
            df.loc[is_invoice, "detalle_estado_hacienda"] = matched_detail[is_invoice].fillna("")
            is_hacienda_message = df["documento_root"].astype(str) == "MensajeHacienda"
            df.loc[is_hacienda_message, "estado_hacienda"] = df.loc[
                is_hacienda_message, "estado_hacienda_xml"
            ].apply(self.normalize_hacienda_status)
            df.loc[is_hacienda_message, "detalle_estado_hacienda"] = df.loc[
                is_hacienda_message, "detalle_estado_hacienda_xml"
            ]
        return df
    def filter_comprobante_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        """Oculta filas de mensajes de Hacienda/Receptor, conservando su estado asociado."""
        if df.empty or "documento_root" not in df.columns:
            return df
        ignored_roots = {"MensajeHacienda", "MensajeReceptor"}
        mask = ~df["documento_root"].astype(str).isin(ignored_roots)
        return df.loc[mask].copy()
    def remove_duplicate_hashes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Elimina duplicados exactos por hash de contenido XML."""
        self.last_duplicate_count = 0
        if df.empty or "xml_hash" not in df.columns:
            return df
        before = len(df)
        dedup = df.drop_duplicates(subset=["xml_hash"], keep="first").copy()
        self.last_duplicate_count = before - len(dedup)
        return dedup
    @staticmethod
    def local_name(tag: str) -> str:
        return tag.split("}", maxsplit=1)[-1] if "}" in tag else tag
    @staticmethod
    def extract_first_non_empty(data: dict[str, Any], keys: list[str]) -> str:
        for key in keys:
            value = data.get(key)
            if value:
                return str(value)
        return ""
    @staticmethod
    def normalize_hacienda_status(raw_status: Any) -> str:
        value = str(raw_status or "").strip().lower()
        mapping = {
            "aceptado": "Aceptada",
            "1": "Aceptada",
            "rechazado": "Rechazada",
            "2": "Rechazada",
            "procesando": "Procesando",
            "3": "Procesando",
            "recibido": "Recibida",
            "error": "Error",
        }
        return mapping.get(value, str(raw_status)) if value else ""
    @classmethod
    def normalize_text(cls, raw_text: Any) -> str:
        text = str(raw_text or "").strip()
        return "" if text.lower() in cls.EMPTY_TEXT_MARKERS else text

    def optimize_dataframe_memory(self, df: pd.DataFrame) -> pd.DataFrame:
        """Reduce huella RAM usando dtypes compactos y categóricos."""
        if df.empty:
            return df

        optimized = df.copy()
        category_candidates = {
            "tipo_documento",
            "documento_root",
            "estado_hacienda",
            "moneda",
        }
        for column in optimized.columns:
            if column in category_candidates:
                optimized[column] = optimized[column].fillna("").astype("category")

        return optimized


