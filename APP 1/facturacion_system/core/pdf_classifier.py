# -*- coding: utf-8 -*-
"""
facturacion_system.core.pdf_classifier

Clasifica PDFs de comprobantes costarricenses usando la Clave numérica (50 dígitos)
para obtener la identificación del EMISOR y consultar la razón social en Hacienda.

Puntos clave (por los casos que te fallaban):
- NO se “pegan” todos los números del PDF: eso generaba claves falsas cuando hay teléfonos
  con prefijo (506) y otros números cerca.
- Se prioriza SIEMPRE la Clave del comprobante (o la del nombre del archivo) para sacar
  la cédula del EMISOR, evitando agarrar la del CLIENTE.
- Si hay 2 claves (ej. “Documento Referencia”), se elige la que está cerca de “CLAVE”.
- Cache persistente en SQLite dentro del repo: facturacion_system/data/hacienda_cache.db

Requisitos:
  pip install pdfplumber requests
"""

from __future__ import annotations

import io
import hashlib
import os
import re
import time
import json
import shutil
import sqlite3
import threading
import unicodedata
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import pdfplumber
import requests


# =============================================================================
# Normalización / nombres seguros (Windows)
# =============================================================================

_INVALID_WIN_CHARS = re.compile(r'[\\/:*?"<>|]')

def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def _normalize_spaces(s: str) -> str:
    s = (s or "").replace("\x00", " ")
    s = re.sub(r"[\r\n\t]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s

def _sanitize_folder_name(name: str, max_len: int = 140) -> Optional[str]:
    if not name:
        return None
    name = _normalize_spaces(name)
    name = _INVALID_WIN_CHARS.sub("", name).strip().rstrip(". ")
    if not name:
        return None
    return name[:max_len].rstrip(". ")

def _upper_safe(name: str) -> Optional[str]:
    name = _strip_accents(name or "").upper()
    return _sanitize_folder_name(name)


# =============================================================================
# Lectura PDF (texto)
# =============================================================================

def _extract_text(pdf_path: str, max_pages: int = 3) -> str:
    """
    Extrae texto de las primeras `max_pages` páginas.
    (Se puede subir a 4–6 si tienes PDFs donde la clave sale tarde.)
    """
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        n = min(max_pages, len(pdf.pages))
        for i in range(n):
            text += (pdf.pages[i].extract_text() or "") + "\n"
    return text


# =============================================================================
# Clave numérica (50 dígitos) y cédula del EMISOR desde clave
# =============================================================================

# 1) En nombre de archivo: 506 + 47 dígitos (contiguos)
_RE_KEY_IN_FILENAME = re.compile(r"(506\d{47})")

# 2) En texto: permite espacios/guiones ENTRE dígitos, pero NO letras.
#    Esto evita falsos positivos cuando hay teléfonos (506) y otros números separados por texto.
_RE_KEY_IN_TEXT = re.compile(r"(506(?:[\s\-]*\d){47})")

# 3) Captura por etiqueta (clave / cl. numerica) y luego busca la clave dentro de ese bloque.
_RE_KEY_LABEL_BLOCK = re.compile(
    r"(?is)\b(?:CLAVE(?:\s+COMPROBANTE)?|CL\.?\s*NUM[ÉE]RICA|CL\s*NUMERICA|CLNUMERICA)\b[^0-9]*([0-9\s\-]{45,220})"
)

def _clean_key(candidate: str) -> Optional[str]:
    k = re.sub(r"\D", "", candidate or "")
    if len(k) == 50 and k.startswith("506"):
        return k
    return None

def _extract_key_candidates(text: str, filename: str) -> List[Dict]:
    """
    Devuelve candidatos de clave con un score base y (si aplica) posición en texto.
    """
    out: List[Dict] = []

    # filename
    for m in _RE_KEY_IN_FILENAME.finditer(filename or ""):
        k = _clean_key(m.group(1))
        if k:
            out.append({"key": k, "src": "filename", "pos": -1})

    # label blocks
    for m in _RE_KEY_LABEL_BLOCK.finditer(text or ""):
        block = m.group(1)
        # dentro del bloque, buscamos patrón flexible por si viene cortada en dos líneas
        km = _RE_KEY_IN_TEXT.search(block)
        if km:
            k = _clean_key(km.group(1))
            if k:
                out.append({"key": k, "src": "label", "pos": m.start()})

    # raw text matches
    for m in _RE_KEY_IN_TEXT.finditer(text or ""):
        k = _clean_key(m.group(1))
        if k:
            out.append({"key": k, "src": "text", "pos": m.start()})

    # dedupe (mantener el mejor src/pos por key)
    best_by_key: Dict[str, Dict] = {}
    src_rank = {"filename": 3, "label": 2, "text": 1}
    for item in out:
        k = item["key"]
        cur = best_by_key.get(k)
        if (cur is None) or (src_rank[item["src"]] > src_rank[cur["src"]]):
            best_by_key[k] = item
    return list(best_by_key.values())

def _score_key_candidate(text: str, item: Dict) -> int:
    """
    Puntuación para elegir la clave principal cuando hay varias.
    """
    src = item.get("src")
    pos = item.get("pos", -1)
    key = item["key"]

    score = 0
    if src == "filename":
        score += 1000
    elif src == "label":
        score += 900
    else:
        score += 800

    # context (si tenemos posición real en texto)
    if pos is not None and pos >= 0 and text:
        left = text[max(0, pos - 120): pos].upper()
        right = text[pos: pos + 120].upper()
        ctx = left + " " + right

        if "DOCUMENTO REFERENCIA" in ctx or "REFERENCIA" in ctx:
            score -= 300
        if "CLAVE" in ctx or "CL." in ctx or "NUMERICA" in ctx:
            score += 120

    # si el key aparece muchas veces, le subimos un poco
    if text:
        approx = len(re.findall(re.escape(key[:10]), re.sub(r"\s+", "", text)))
        score += min(approx, 5) * 5

    return score

def _pick_best_key(text: str, filename: str) -> Optional[str]:
    candidates = _extract_key_candidates(text, filename)
    if not candidates:
        return None
    candidates.sort(key=lambda it: _score_key_candidate(text, it), reverse=True)
    return candidates[0]["key"]

def _emisor_id_from_key(key50: str) -> Optional[str]:
    """
    La clave Hacienda (50 dígitos) lleva la identificación del emisor en posiciones 10–21 (12 dígitos, con ceros).
    0-based: key[9:21]
    """
    if not key50 or len(key50) != 50:
        return None
    raw = key50[9:21]
    ident = raw.lstrip("0") or raw  
    if not (ident.isdigit() and 9 <= len(ident) <= 12):
        return None
    return ident


# =============================================================================
# Hacienda AE API + cache persistente (SQLite)
# =============================================================================

_HACIENDA_URL = "https://api.hacienda.go.cr/fe/ae?identificacion={ident}"

def default_db_path() -> str:
    """La BD de caché va en Z:/DATA/DATABASE/hacienda_cache.db."""
    from facturacion_system.config import DATABASE_DIR

    db_dir = Path(DATABASE_DIR)
    db_dir.mkdir(parents=True, exist_ok=True)
    return str(db_dir / "hacienda_cache.db")

def _db_connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
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
    return conn

def _db_get(conn: sqlite3.Connection, ident: str) -> Optional[str]:
    cur = conn.execute("SELECT razon_social FROM hacienda_cache WHERE identificacion = ?", (ident,))
    row = cur.fetchone()
    return row[0] if row and row[0] else None

def _db_put(conn: sqlite3.Connection, ident: str, razon: Optional[str], raw_json: Optional[dict]) -> None:
    conn.execute(
        """
        INSERT INTO hacienda_cache(identificacion, razon_social, raw_json, updated_at)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(identificacion) DO UPDATE SET
            razon_social=excluded.razon_social,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at
        """,
        (ident, razon, json.dumps(raw_json, ensure_ascii=False) if raw_json else None, int(time.time())),
    )
    conn.commit()

def _fetch_hacienda(ident: str, timeout: float | None = None, retries: int | None = None) -> Tuple[Optional[str], Optional[dict]]:
    """
    Consulta Hacienda AE. Devuelve (razon_social, json) o (None, json/None) si no existe.
    """
    from facturacion_system.core.settings import get_setting

    timeout = float(timeout if timeout is not None else get_setting("hacienda_timeout", 10.0))
    retries = int(retries if retries is not None else get_setting("hacienda_retries", 2))
    url = _HACIENDA_URL.format(ident=ident)
    for i in range(retries + 1):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                razon = data.get("nombre") or data.get("razonSocial") or data.get("razon_social")
                if razon:
                    razon = _normalize_spaces(str(razon))
                return razon, data
            if r.status_code in (404, 204):
                return None, None
            # rate limit / transient errors
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(0.8 * (i + 1))
                continue
            # otros: no reintentar mucho
            return None, None
        except Exception:
            time.sleep(0.6 * (i + 1))
            continue
    return None, None

def consultar_razon_social_hacienda(conn: sqlite3.Connection, ident: str) -> Optional[str]:
    """
    Busca en cache primero. Si no, consulta Hacienda.
    Si ident viene “raro” (ej 12 dígitos que NO existe), intenta variante de 10 dígitos.
    """
    ident = re.sub(r"\D", "", ident or "")
    if not ident:
        return None

    cached = _db_get(conn, ident)
    if cached:
        return cached

    razon, data = _fetch_hacienda(ident)
    if razon:
        _db_put(conn, ident, razon, data)
        return razon

    # Variante común: algunas facturas muestran 12 dígitos pegados,
    # pero Hacienda responde con el 10 dígitos (ej: 3-101-085674 => 3101085674)
    if len(ident) == 12:
        ident10 = ident[:10]
        cached10 = _db_get(conn, ident10)
        if cached10:
            _db_put(conn, ident, cached10, None)
            return cached10

        razon10, data10 = _fetch_hacienda(ident10)
        if razon10:
            _db_put(conn, ident10, razon10, data10)
            _db_put(conn, ident, razon10, None)
            return razon10

    _db_put(conn, ident, None, data)  # cache negativo (evita repetir)
    return None


# =============================================================================
# Clasificación principal
# =============================================================================

def _iter_pdfs(root: str) -> Iterable[Tuple[str, str]]:
    """
    Yields (pdf_path, remitente_original_folder_name)
    """
    for dirpath, _, filenames in os.walk(root):
        remitente = os.path.basename(dirpath)
        for fn in filenames:
            if fn.lower().endswith(".pdf"):
                yield os.path.join(dirpath, fn), remitente

def _make_other_folder_name(prefix: str, remitente: str) -> str:
    rem = _upper_safe(remitente) or "SIN_NOMBRE"
    # En tu salida estabas usando: OTROS_NO_IDENTIFICADOS_<REM>
    return f"{prefix}_{rem}"

def clasificar_por_hacienda(
    carpeta_origen: str,
    carpeta_destino: str,
    *,
    db_path: Optional[str] = None,
    otros_prefix: str = "OTROS_NO_IDENTIFICADOS",
    max_pages: int | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
    stop_event: threading.Event | None = None,
    move_files: bool = False,
    return_details: bool = False,
):
    """Clasifica PDFs por Hacienda con soporte de progreso y cancelación."""
    from facturacion_system.core.file_manager import get_pdf_target_folder
    from facturacion_system.core.settings import get_setting

    carpeta_origen_abs = os.path.abspath(carpeta_origen)
    carpeta_destino_abs = os.path.abspath(carpeta_destino)
    os.makedirs(carpeta_destino_abs, exist_ok=True)

    if db_path is None:
        db_path = default_db_path()
    else:
        db_path = os.path.abspath(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    if max_pages is None:
        max_pages = int(get_setting("pdf_max_pages", 4))

    conn = _db_connect(db_path)
    pdfs = list(_iter_pdfs(carpeta_origen_abs))
    total = len(pdfs)
    stats = {
        "processed": 0,
        "classified": 0,
        "unclassified": 0,
        "errors": 0,
        "cancelled": False,
        "error_samples": [],
        "unclassified_samples": [],
    }

    # Solo evitamos recursión si el destino está dentro del origen.
    # Si destino=PF-YYYY y origen=PF-YYYY/CLIENTES/.../PDF, NO es recursión.
    dest_inside_source = (
        os.path.commonpath([carpeta_destino_abs, carpeta_origen_abs]) == carpeta_origen_abs
    )

    try:
        for pdf_path, remitente in pdfs:
            if stop_event and stop_event.is_set():
                stats["cancelled"] = True
                break

            if dest_inside_source and (
                os.path.commonpath([os.path.abspath(pdf_path), carpeta_destino_abs]) == carpeta_destino_abs
            ):
                continue

            filename = os.path.basename(pdf_path)
            razon = None
            ident_emisor = None
            key50 = None

            key50 = None
            m = _RE_KEY_IN_FILENAME.search(filename)
            if m:
                key50 = _clean_key(m.group(1))

            text = ""
            if not key50:
                try:
                    text = _extract_text(pdf_path, max_pages=max_pages)
                except Exception:
                    text = ""
                key50 = _pick_best_key(text, filename)
            ident_emisor = _emisor_id_from_key(key50) if key50 else None
            if ident_emisor:
                razon = consultar_razon_social_hacienda(conn, ident_emisor)

            nombre_carpeta = _upper_safe(razon) if razon else (_upper_safe(ident_emisor) if ident_emisor else None)
            try:
                if Path(carpeta_destino_abs).name.startswith("PF-"):
                    target_folder = get_pdf_target_folder(
                        pf_base=Path(carpeta_destino_abs),
                        client_folder_name=Path(carpeta_origen_abs).parent.name,
                        clave=key50,
                        razon_social=nombre_carpeta,
                        remitente=remitente,
                    )
                else:
                    folder = nombre_carpeta or _make_other_folder_name(otros_prefix, remitente)
                    target_folder = Path(carpeta_destino_abs) / folder
                    target_folder.mkdir(parents=True, exist_ok=True)

                destino = target_folder / filename
                if destino.exists():
                    h = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()[:8]
                    destino = target_folder / f"{Path(filename).stem}__{h}{Path(filename).suffix}"

                if move_files:
                    shutil.move(pdf_path, str(destino))
                else:
                    shutil.copy2(pdf_path, str(destino))

                if nombre_carpeta:
                    stats["classified"] += 1
                else:
                    stats["unclassified"] += 1
                    if len(stats["unclassified_samples"]) < 10:
                        stats["unclassified_samples"].append(filename)
            except Exception as err:
                stats["errors"] += 1
                if len(stats["error_samples"]) < 10:
                    stats["error_samples"].append(f"{filename}: {err}")

            stats["processed"] += 1
            if progress_cb:
                progress_cb(stats["processed"], total, filename)
    finally:
        conn.close()

    if move_files:
        origen = Path(carpeta_origen_abs)
        for carpeta in sorted(origen.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if carpeta.is_dir() and not any(carpeta.iterdir()):
                try:
                    carpeta.rmdir()
                except Exception:
                    pass

    if return_details:
        return stats
    return int(stats["classified"] + stats["unclassified"])




def _format_cedula_emisor(ident: str | None) -> str | None:
    """Formatea identificación numérica a formato de cédula legible."""
    if not ident:
        return None
    digits = re.sub(r"\D", "", ident)
    if len(digits) == 10:
        return f"{digits[0]}-{digits[1:4]}-{digits[4:10]}"
    if len(digits) == 9:
        return f"{digits[0]}-{digits[1:5]}-{digits[5:9]}"
    return digits


def extract_clave_and_cedula(
    pdf_bytes: bytes,
    original_filename: str = "",
) -> tuple[str | None, str | None]:
    """
    Extrae clave de Hacienda y cédula del emisor desde bytes PDF.

    OPTIMIZACIÓN: Busca primero en el nombre del archivo.
    Solo lee el contenido del PDF si no encuentra la clave en el nombre.
    """
    if not pdf_bytes:
        return None, None

    # PASO 1: Buscar clave en el nombre del archivo (instantáneo)
    if original_filename:
        m = _RE_KEY_IN_FILENAME.search(original_filename)
        if m:
            key50 = _clean_key(m.group(1))
            if key50:
                ident_emisor = _emisor_id_from_key(key50)
                cedula = _format_cedula_emisor(ident_emisor)
                return key50, cedula

    # PASO 2: Solo si no hay clave en el nombre, leer el PDF
    try:
        text = ""
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            n = min(4, len(pdf.pages))
            for i in range(n):
                page_text = pdf.pages[i].extract_text() or ""
                text += page_text + "\n"
                # Optimización adicional: si ya encontramos la clave, parar
                if _RE_KEY_IN_TEXT.search(re.sub(r"\D", "", text)) or _RE_KEY_LABEL_BLOCK.search(text):
                    break

        filename_to_search = original_filename.strip() if original_filename else ""
        key50 = _pick_best_key(text, filename_to_search)
        ident_emisor = _emisor_id_from_key(key50) if key50 else None
        cedula = _format_cedula_emisor(ident_emisor)
        return key50, cedula

    except Exception:
        return None, None

# =============================================================================
# Alias / compatibilidad (por si tu GUI importa otros nombres)
# =============================================================================

