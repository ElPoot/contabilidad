"""Utilidad CLI para auditoria forense de sobrescrituras por consecutivo.

Cruza tres fuentes de evidencia:
1. XMLs reales sobrevivientes en la carpeta del cliente.
2. state.sqlite del otro sistema (correo / adjuntos descargados).
3. Una segunda SQLite opcional consultada mediante SQL configurable.

Uso recomendado:

    python -m gestor_contable.app.services.forensic_overwrite_audit \
        --xml-dir "Z:/DATA/PF-2026/CLIENTES/3-101-812411 SOCIEDAD ANONIMA/XML" \
        --mail-db "Z:/DATA/PF-2026/CLIENTES/3-101-812411 SOCIEDAD ANONIMA/.metadata/state.sqlite" \
        --client-id "3101812411"

Si existe una segunda base de datos con la lista del otro sistema, puede
inyectarse con --other-db + --other-sql siempre que el SELECT devuelva
columnas compatibles como:
    clave, consecutivo, saved_path, created_at, expected_role
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from gestor_contable.core.xml_manager import CRXMLManager


KEY50_RE = re.compile(r"(?<!\d)(\d{50})(?!\d)")
LEGACY_XML_RE = re.compile(r"^(?P<consecutivo>\d{20})_(?P<suffix>firmado|respuesta)\.xml$", re.IGNORECASE)

MESSAGE_ROOTS = {"MensajeHacienda", "MensajeReceptor"}
PRIMARY_ROOTS = {
    "FacturaElectronica",
    "TiqueteElectronico",
    "NotaCreditoElectronica",
    "NotaDebitoElectronica",
}


@dataclass(slots=True)
class XmlInventoryRow:
    ruta: str
    nombre_archivo: str
    existe: bool
    documento_root: str
    tipo_documento: str
    es_documento_primario: bool
    es_mensaje: bool
    es_legacy_consecutivo: bool
    legacy_suffix: str
    clave_xml_real: str
    clave_en_nombre: str
    consecutivo: str
    fecha_emision: str
    emisor_id: str
    emisor_nombre: str
    receptor_id: str
    receptor_nombre: str
    total_comprobante: str
    rol_cliente: str
    observacion: str


@dataclass(slots=True)
class ParseErrorRow:
    ruta: str
    nombre_archivo: str
    error: str


@dataclass(slots=True)
class SourceInventoryRow:
    source_system: str
    row_key: str
    saved_path: str
    nombre_archivo: str
    file_type: str
    clave: str
    consecutivo: str
    created_at: str
    message_id: str
    attachment_id: str
    expected_role: str
    raw_role: str
    observacion: str


@dataclass(slots=True)
class FindingRow:
    source_system: str
    row_key: str
    clave_esperada: str
    consecutivo: str
    expected_role: str
    current_match_clave: str
    current_match_ruta: str
    current_match_role: str
    status: str
    confidence: str
    evidence: str


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _normalize_digits(value: Any) -> str:
    text = str(value or "").strip()
    return "".join(ch for ch in text if ch.isdigit())


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _extract_key_from_text(value: Any) -> str:
    match = KEY50_RE.search(str(value or ""))
    return match.group(1) if match else ""


def _consecutivo_from_clave(clave: str) -> str:
    digits = _normalize_digits(clave)
    return digits[21:41] if len(digits) == 50 else ""


def _role_for_record(emisor_id: str, receptor_id: str, client_id: str) -> str:
    emisor = _normalize_digits(emisor_id)
    receptor = _normalize_digits(receptor_id)
    client = _normalize_digits(client_id)
    if not client:
        return "desconocido"
    if emisor == client and receptor == client:
        return "ambos"
    if emisor == client:
        return "cliente_emisor"
    if receptor == client:
        return "cliente_receptor"
    return "sin_relacion"


def _path_under(path_text: str, base: Path) -> bool:
    try:
        path_norm = str(Path(path_text)).replace("/", "\\").lower()
        base_norm = str(base).replace("/", "\\").lower().rstrip("\\")
        return path_norm == base_norm or path_norm.startswith(base_norm + "\\")
    except Exception:
        return False


def _legacy_info(name: str) -> tuple[bool, str, str]:
    match = LEGACY_XML_RE.match(name)
    if not match:
        return False, "", ""
    return True, match.group("suffix").lower(), match.group("consecutivo")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _copy_sqlite_shadow(db_path: Path, shadow_dir: Path) -> Path:
    _ensure_dir(shadow_dir)
    shadow_path = shadow_dir / db_path.name
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db_path) + suffix)
        if src.exists():
            dst = Path(str(shadow_path) + suffix)
            shutil.copy2(src, dst)
    return shadow_path


def _connect_sqlite_readonly(db_path: Path, shadow_dir: Path | None = None) -> sqlite3.Connection:
    db_path = Path(db_path)
    uri = db_path.resolve(strict=False).as_uri() + "?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        if shadow_dir is None:
            raise
        shadow_path = _copy_sqlite_shadow(db_path, shadow_dir)
        shadow_uri = shadow_path.resolve(strict=False).as_uri() + "?mode=ro"
        return sqlite3.connect(shadow_uri, uri=True)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    _ensure_dir(path.parent)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _scan_xml_inventory(xml_dir: Path, client_id: str, limit_xmls: int | None = None) -> tuple[list[XmlInventoryRow], list[ParseErrorRow]]:
    manager = CRXMLManager()
    rows: list[XmlInventoryRow] = []
    errors: list[ParseErrorRow] = []

    xml_paths = sorted(xml_dir.rglob("*.xml"))
    if limit_xmls:
        xml_paths = xml_paths[:limit_xmls]

    for xml_path in xml_paths:
        is_legacy, legacy_suffix, legacy_consecutivo = _legacy_info(xml_path.name)
        try:
            parsed = manager.parse_xml_file(xml_path)
        except Exception as exc:
            errors.append(
                ParseErrorRow(
                    ruta=str(xml_path),
                    nombre_archivo=xml_path.name,
                    error=str(exc),
                )
            )
            continue

        root = _safe_text(parsed.get("documento_root"))
        clave_real = _normalize_digits(parsed.get("clave_numerica"))
        clave_nombre = _extract_key_from_text(xml_path.name)
        consecutivo = _normalize_digits(parsed.get("consecutivo")) or legacy_consecutivo or _consecutivo_from_clave(clave_real or clave_nombre)
        emisor_id = _normalize_digits(parsed.get("emisor_cedula"))
        receptor_id = _normalize_digits(parsed.get("receptor_cedula"))
        rol = _role_for_record(emisor_id, receptor_id, client_id)

        rows.append(
            XmlInventoryRow(
                ruta=str(xml_path),
                nombre_archivo=xml_path.name,
                existe=True,
                documento_root=root,
                tipo_documento=_safe_text(parsed.get("tipo_documento")),
                es_documento_primario=root in PRIMARY_ROOTS,
                es_mensaje=root in MESSAGE_ROOTS,
                es_legacy_consecutivo=is_legacy,
                legacy_suffix=legacy_suffix,
                clave_xml_real=clave_real,
                clave_en_nombre=clave_nombre,
                consecutivo=consecutivo,
                fecha_emision=_safe_text(parsed.get("fecha_emision")),
                emisor_id=emisor_id,
                emisor_nombre=_safe_text(parsed.get("emisor_nombre")),
                receptor_id=receptor_id,
                receptor_nombre=_safe_text(parsed.get("receptor_nombre")),
                total_comprobante=_safe_text(parsed.get("total_comprobante")),
                rol_cliente=rol,
                observacion="",
            )
        )
    return rows, errors


def _load_state_rows(
    state_db: Path,
    xml_dir: Path,
    shadow_dir: Path | None,
    expected_role: str,
) -> list[SourceInventoryRow]:
    rows: list[SourceInventoryRow] = []
    conn = _connect_sqlite_readonly(state_db, shadow_dir=shadow_dir)
    try:
        conn.row_factory = sqlite3.Row
        query = """
            SELECT digest, saved_path, message_id, attachment_id, created_at
            FROM files
            ORDER BY created_at
        """
        for record in conn.execute(query):
            saved_path = _safe_text(record["saved_path"])
            if not saved_path or not _path_under(saved_path, xml_dir.parent):
                continue
            file_type = Path(saved_path).suffix.lower().lstrip(".")
            clave = _extract_key_from_text(Path(saved_path).name)
            consecutivo = ""
            is_legacy, _suffix, legacy_consecutivo = _legacy_info(Path(saved_path).name)
            if is_legacy:
                consecutivo = legacy_consecutivo
            if not consecutivo and clave:
                consecutivo = _consecutivo_from_clave(clave)

            rows.append(
                SourceInventoryRow(
                    source_system="correo_state",
                    row_key=_safe_text(record["digest"]),
                    saved_path=saved_path,
                    nombre_archivo=Path(saved_path).name,
                    file_type=file_type or "desconocido",
                    clave=clave,
                    consecutivo=consecutivo,
                    created_at=_safe_text(record["created_at"]),
                    message_id=_safe_text(record["message_id"]),
                    attachment_id=_safe_text(record["attachment_id"]),
                    expected_role=expected_role,
                    raw_role="",
                    observacion="",
                )
            )
    finally:
        conn.close()
    return rows


def _load_other_rows(
    other_db: Path | None,
    other_sql: str | None,
    source_label: str,
    expected_role: str,
    shadow_dir: Path | None,
) -> list[SourceInventoryRow]:
    if not other_db or not other_sql:
        return []

    rows: list[SourceInventoryRow] = []
    conn = _connect_sqlite_readonly(other_db, shadow_dir=shadow_dir)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(other_sql)
        columns = {desc[0].lower(): desc[0] for desc in cursor.description or []}

        for index, record in enumerate(cursor, start=1):
            saved_path = _safe_text(record[columns["saved_path"]]) if "saved_path" in columns else ""
            file_type = _safe_text(record[columns["file_type"]]).lower() if "file_type" in columns else ""
            if not file_type and saved_path:
                file_type = Path(saved_path).suffix.lower().lstrip(".")
            file_type = file_type or "xml"

            clave = _normalize_digits(record[columns["clave"]]) if "clave" in columns else ""
            if not clave and saved_path:
                clave = _extract_key_from_text(Path(saved_path).name)

            consecutivo = _normalize_digits(record[columns["consecutivo"]]) if "consecutivo" in columns else ""
            if not consecutivo and saved_path:
                is_legacy, _suffix, legacy_consecutivo = _legacy_info(Path(saved_path).name)
                if is_legacy:
                    consecutivo = legacy_consecutivo
            if not consecutivo and clave:
                consecutivo = _consecutivo_from_clave(clave)

            created_at = _safe_text(record[columns["created_at"]]) if "created_at" in columns else ""
            row_key = _safe_text(record[columns["row_key"]]) if "row_key" in columns else f"{source_label}:{index}"
            raw_role = _safe_text(record[columns["raw_role"]]) if "raw_role" in columns else ""
            row_expected_role = _safe_text(record[columns["expected_role"]]) if "expected_role" in columns else expected_role

            rows.append(
                SourceInventoryRow(
                    source_system=source_label,
                    row_key=row_key,
                    saved_path=saved_path,
                    nombre_archivo=Path(saved_path).name if saved_path else "",
                    file_type=file_type,
                    clave=clave,
                    consecutivo=consecutivo,
                    created_at=created_at,
                    message_id="",
                    attachment_id="",
                    expected_role=row_expected_role,
                    raw_role=raw_role,
                    observacion="",
                )
            )
    finally:
        conn.close()
    return rows


def _build_indexes(rows: list[XmlInventoryRow]) -> tuple[dict[str, list[XmlInventoryRow]], dict[str, list[XmlInventoryRow]]]:
    by_clave: dict[str, list[XmlInventoryRow]] = defaultdict(list)
    by_consecutivo: dict[str, list[XmlInventoryRow]] = defaultdict(list)
    for row in rows:
        if not row.es_documento_primario:
            continue
        if row.clave_xml_real:
            by_clave[row.clave_xml_real].append(row)
        if row.consecutivo:
            by_consecutivo[row.consecutivo].append(row)
    return by_clave, by_consecutivo


def _evaluate_source_row(
    row: SourceInventoryRow,
    current_by_clave: dict[str, list[XmlInventoryRow]],
    current_by_consecutivo: dict[str, list[XmlInventoryRow]],
) -> FindingRow | None:
    if row.file_type != "xml":
        return None

    expected_clave = row.clave
    consecutivo = row.consecutivo or _consecutivo_from_clave(expected_clave)
    current_group = current_by_consecutivo.get(consecutivo, []) if consecutivo else []

    if expected_clave and expected_clave in current_by_clave:
        current = current_by_clave[expected_clave][0]
        status = "sano"
        confidence = "alta"
        evidence = "clave exacta encontrada en filesystem"
        if row.expected_role and row.expected_role != "desconocido" and current.rol_cliente not in {"desconocido", row.expected_role}:
            status = "anomalia_fuente"
            confidence = "media"
            evidence = (
                "clave exacta encontrada pero el rol del cliente en el XML sobreviviente "
                f"es {current.rol_cliente}, no {row.expected_role}"
            )
        return FindingRow(
            source_system=row.source_system,
            row_key=row.row_key,
            clave_esperada=expected_clave,
            consecutivo=consecutivo,
            expected_role=row.expected_role,
            current_match_clave=current.clave_xml_real,
            current_match_ruta=current.ruta,
            current_match_role=current.rol_cliente,
            status=status,
            confidence=confidence,
            evidence=evidence,
        )

    different_keys = sorted({item.clave_xml_real for item in current_group if item.clave_xml_real and item.clave_xml_real != expected_clave})
    if expected_clave and different_keys:
        current = current_group[0]
        return FindingRow(
            source_system=row.source_system,
            row_key=row.row_key,
            clave_esperada=expected_clave,
            consecutivo=consecutivo,
            expected_role=row.expected_role,
            current_match_clave=", ".join(different_keys),
            current_match_ruta=current.ruta,
            current_match_role=current.rol_cliente,
            status="sobrescritura_altamente_probable",
            confidence="alta",
            evidence=(
                "la clave esperada no existe hoy, pero el mismo consecutivo sobrevive con otra clave: "
                + ", ".join(different_keys)
            ),
        )

    if current_group:
        current = current_group[0]
        return FindingRow(
            source_system=row.source_system,
            row_key=row.row_key,
            clave_esperada=expected_clave,
            consecutivo=consecutivo,
            expected_role=row.expected_role,
            current_match_clave=current.clave_xml_real,
            current_match_ruta=current.ruta,
            current_match_role=current.rol_cliente,
            status="colision_probable_por_consecutivo",
            confidence="media",
            evidence="existe un XML sobreviviente para el mismo consecutivo, pero no hay match de clave exacta",
        )

    return FindingRow(
        source_system=row.source_system,
        row_key=row.row_key,
        clave_esperada=expected_clave,
        consecutivo=consecutivo,
        expected_role=row.expected_role,
        current_match_clave="",
        current_match_ruta="",
        current_match_role="",
        status="falta_actual",
        confidence="media" if expected_clave else "baja",
        evidence="la evidencia fuente no tiene match actual por clave ni por consecutivo",
    )


def _build_source_collisions(source_rows: list[SourceInventoryRow]) -> list[dict[str, Any]]:
    grouped: dict[str, list[SourceInventoryRow]] = defaultdict(list)
    for row in source_rows:
        if row.file_type != "xml":
            continue
        consecutivo = row.consecutivo or _consecutivo_from_clave(row.clave)
        if consecutivo:
            grouped[consecutivo].append(row)

    collisions: list[dict[str, Any]] = []
    for consecutivo, rows in grouped.items():
        keys = sorted({row.clave for row in rows if row.clave})
        if len(keys) <= 1:
            continue
        collisions.append(
            {
                "consecutivo": consecutivo,
                "sources": ", ".join(sorted({row.source_system for row in rows})),
                "claves_distintas": ", ".join(keys),
                "total_registros": len(rows),
                "rows": " | ".join(f"{row.source_system}:{row.row_key}" for row in rows[:10]),
            }
        )
    return sorted(collisions, key=lambda item: (item["consecutivo"], item["claves_distintas"]))


def _markdown_report(
    output_path: Path,
    client_id: str,
    xml_dir: Path,
    xml_rows: list[XmlInventoryRow],
    parse_errors: list[ParseErrorRow],
    source_rows: list[SourceInventoryRow],
    findings: list[FindingRow],
    collisions: list[dict[str, Any]],
) -> None:
    primary_rows = [row for row in xml_rows if row.es_documento_primario]
    legacy_primary = [row for row in primary_rows if row.es_legacy_consecutivo]
    status_counts = Counter(f.status for f in findings)
    source_counts = Counter(row.source_system for row in source_rows)

    lines = [
        "# Reporte forense de sobrescrituras por consecutivo",
        "",
        f"- Cliente: `{client_id}`",
        f"- Carpeta XML: `{xml_dir}`",
        f"- Generado: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
        "## Resumen",
        f"- XMLs parseados: **{len(xml_rows)}**",
        f"- Documentos primarios: **{len(primary_rows)}**",
        f"- XMLs legacy expuestos (`20 digitos + _firmado.xml`): **{len(legacy_primary)}**",
        f"- Parse errors: **{len(parse_errors)}**",
        f"- Registros fuente cruzados: **{len(source_rows)}**",
        f"- Colisiones por consecutivo entre fuentes: **{len(collisions)}**",
        "",
        "## Hallazgos por estado",
    ]
    for status in sorted(status_counts):
        lines.append(f"- `{status}`: **{status_counts[status]}**")

    lines.extend(["", "## Registros fuente por sistema"])
    for source in sorted(source_counts):
        lines.append(f"- `{source}`: **{source_counts[source]}**")

    lines.extend(["", "## Ejemplos relevantes"])
    interesting = [
        row for row in findings
        if row.status in {"sobrescritura_altamente_probable", "colision_probable_por_consecutivo", "anomalia_fuente"}
    ]
    for row in interesting[:20]:
        lines.extend(
            [
                f"### {row.status}: {row.consecutivo or row.clave_esperada or row.row_key}",
                f"- Fuente: `{row.source_system}` / `{row.row_key}`",
                f"- Clave esperada: `{row.clave_esperada or 'N/A'}`",
                f"- Consecutivo: `{row.consecutivo or 'N/A'}`",
                f"- Match actual: `{row.current_match_clave or 'N/A'}`",
                f"- Ruta actual: `{row.current_match_ruta or 'N/A'}`",
                f"- Evidencia: {row.evidence}",
                "",
            ]
        )

    if collisions:
        lines.extend(["## Colisiones entre fuentes"])
        for collision in collisions[:20]:
            lines.append(
                f"- `{collision['consecutivo']}` -> {collision['claves_distintas']} "
                f"(fuentes: {collision['sources']})"
            )

    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auditoria forense de posibles sobrescrituras historicas.")
    parser.add_argument("--xml-dir", required=True, help="Carpeta XML del cliente.")
    parser.add_argument("--mail-db", required=True, help="Ruta al state.sqlite del otro sistema.")
    parser.add_argument("--client-id", required=True, help="Cedula del cliente a auditar.")
    parser.add_argument(
        "--mail-expected-role",
        default="cliente_receptor",
        choices=["cliente_emisor", "cliente_receptor", "ambos", "desconocido", "sin_relacion"],
        help="Heuristica de rol esperada para state.sqlite. Usa 'desconocido' si no quieres marcar anomalias por esta via.",
    )
    parser.add_argument("--other-db", help="Segunda SQLite opcional (por ejemplo, Invaco/Invefacon).")
    parser.add_argument(
        "--other-sql",
        help=(
            "SELECT contra la segunda SQLite. Debe devolver columnas compatibles como "
            "clave, consecutivo, saved_path, created_at, expected_role, row_key."
        ),
    )
    parser.add_argument("--other-source-label", default="otro_sistema", help="Etiqueta para la segunda fuente.")
    parser.add_argument(
        "--other-expected-role",
        default="cliente_emisor",
        choices=["cliente_emisor", "cliente_receptor", "ambos", "desconocido", "sin_relacion"],
        help="Rol esperado del cliente en la segunda fuente si el SQL no devuelve expected_role.",
    )
    parser.add_argument(
        "--output-dir",
        help="Carpeta de salida para CSV/Markdown. Default: gestor_contable/data/forensics/<timestamp>/",
    )
    parser.add_argument(
        "--limit-xmls",
        type=int,
        help="Procesa solo N XMLs para una corrida rapida de validacion.",
    )
    parser.add_argument(
        "--no-shadow-copy",
        action="store_true",
        help="No crear copia sombra de SQLite si la apertura readonly falla.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    repo_root = Path(__file__).resolve().parents[3]
    output_dir = Path(args.output_dir) if args.output_dir else repo_root / "gestor_contable" / "data" / "forensics" / _now_stamp()
    _ensure_dir(output_dir)
    shadow_dir = None if args.no_shadow_copy else output_dir / "_shadow_sqlite"

    xml_dir = Path(args.xml_dir)
    mail_db = Path(args.mail_db)
    other_db = Path(args.other_db) if args.other_db else None

    print(f"[1/4] Escaneando XMLs reales en {xml_dir} ...")
    xml_rows, parse_errors = _scan_xml_inventory(xml_dir, args.client_id, limit_xmls=args.limit_xmls)

    print(f"[2/4] Leyendo state.sqlite ({mail_db}) ...")
    mail_rows = _load_state_rows(
        state_db=mail_db,
        xml_dir=xml_dir,
        shadow_dir=shadow_dir,
        expected_role=args.mail_expected_role,
    )

    other_rows: list[SourceInventoryRow] = []
    if other_db and args.other_sql:
        print(f"[3/4] Leyendo segunda fuente ({other_db}) ...")
        other_rows = _load_other_rows(
            other_db=other_db,
            other_sql=args.other_sql,
            source_label=args.other_source_label,
            expected_role=args.other_expected_role,
            shadow_dir=shadow_dir,
        )

    print("[4/4] Cruzando evidencias ...")
    current_by_clave, current_by_consecutivo = _build_indexes(xml_rows)
    source_rows = mail_rows + other_rows
    findings = [
        finding
        for row in source_rows
        for finding in [_evaluate_source_row(row, current_by_clave, current_by_consecutivo)]
        if finding is not None
    ]
    collisions = _build_source_collisions(source_rows)

    filesystem_csv = output_dir / "filesystem_inventory.csv"
    parse_errors_csv = output_dir / "parse_errors.csv"
    mail_csv = output_dir / "mail_state_inventory.csv"
    findings_csv = output_dir / "findings.csv"
    collisions_csv = output_dir / "source_collisions.csv"
    report_md = output_dir / "reporte_forense.md"

    _write_csv(filesystem_csv, [asdict(row) for row in xml_rows])
    _write_csv(parse_errors_csv, [asdict(row) for row in parse_errors])
    _write_csv(mail_csv, [asdict(row) for row in mail_rows])
    if other_rows:
        _write_csv(output_dir / f"{args.other_source_label}_inventory.csv", [asdict(row) for row in other_rows])
    _write_csv(findings_csv, [asdict(row) for row in findings])
    _write_csv(collisions_csv, collisions)
    _markdown_report(
        output_path=report_md,
        client_id=args.client_id,
        xml_dir=xml_dir,
        xml_rows=xml_rows,
        parse_errors=parse_errors,
        source_rows=source_rows,
        findings=findings,
        collisions=collisions,
    )

    print(f"Listo. Salida en: {output_dir}")
    print(f"- XMLs parseados: {len(xml_rows)}")
    print(f"- Parse errors: {len(parse_errors)}")
    print(f"- Registros fuente: {len(source_rows)}")
    print(f"- Colisiones entre fuentes: {len(collisions)}")
    print(f"- Hallazgos: {len(findings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
