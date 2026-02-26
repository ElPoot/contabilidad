import base64
import hashlib
import os
import platform
import re
import subprocess
import threading
import time
import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path

# Importamos utilidades
from facturacion_system.core.gmail_utils import (
    StateDB,
    _execute_with_retry,
    _iter_parts,
    _list_messages,
    _norm_date_any,
    _sha256,
    build_query,
    require_saved_session,
)

from .file_manager import get_metadata_folder, get_target_folder
from .settings import get_setting

logger = logging.getLogger(__name__)

_invalid = re.compile(r'[\\/:*?"<>|\r\n]+')


def _trim_component(raw: str, max_len: int, fallback: str) -> str:
    cleaned = _invalid.sub("_", (raw or fallback).strip()).rstrip(". ")
    if len(cleaned) <= max_len:
        return cleaned or fallback
    path_obj = Path(cleaned)
    suffix = path_obj.suffix
    stem = path_obj.stem
    digest = _sha256(cleaned.encode("utf-8"))[:8]
    spare = max_len - len(suffix) - len(digest) - 1
    trimmed_stem = stem[: max(spare, 4)]
    return f"{trimmed_stem}~{digest}{suffix}" if trimmed_stem else f"{digest}{suffix}"


def sanitize_folder(n: str, max_len: int = 80) -> str:
    return re.sub(r"\s+", " ", _trim_component(n, max_len, "Remitente"))


def sanitize_file(n: str, max_len: int = 140) -> str:
    return _trim_component(n, max_len, "adjunto")


@dataclass
class Task:
    message_id: str
    attachment_id: str
    filename: str
    sender_folder: str
    size_est: int = 0


def _ext_ok(filename: str, exts: Sequence[str]) -> bool:
    if not filename:
        return False
    if not exts:
        return True
    fn = filename.lower()
    return any(fn.endswith(e if e.startswith(".") else "." + e) for e in exts)


# --- PLANIFICACIÓN ---
def plan_tasks(
    fecha_inicio: str,
    fecha_fin: str,
    extensiones: Sequence[str],
    excluir_inbox: bool,
    excluir_enviados: bool,
    excluir_remitentes: Sequence[str],
    include_from: Sequence[str],  # NUEVO
    incluir_terminos: Sequence[str],
    exclude_terms: Sequence[str],  # NUEVO
    progress_cb: Callable[[int, int, int], None] | None = None,
    stop_event: threading.Event | None = None,
    pause_event: threading.Event | None = None,
    max_workers: int | None = None,
) -> tuple[list[Task], dict[str, int]]:

    svc = require_saved_session()
    after = _norm_date_any(fecha_inicio)
    before = _norm_date_any(fecha_fin)

    # Construimos la query con TODAS las opciones
    q = build_query(
        after,
        before,
        extensiones,
        excluir_inbox,
        excluir_enviados,
        excluir_remitentes,
        include_from,  # Pasa al query builder
        incluir_terminos,
        exclude_terms,  # Pasa al query builder
    )

    # Listado paginado (optimizado para evitar timeouts en queries gigantes)
    mids = _list_messages(svc, q)
    total_mids = len(mids)
    tasks: list[Task] = []

    if progress_cb:
        progress_cb(0, total_mids, 0)

    thread_local = threading.local()
    lock = threading.Lock()

    def _svc_for_thread():
        svc_local = getattr(thread_local, "svc", None)
        if svc_local is None:
            svc_local = require_saved_session()
            thread_local.svc = svc_local
        return svc_local

    def _extract_for_message(mid: str) -> list[Task]:
        svc_local = _svc_for_thread()
        # Traemos 'size' para estimaciones futuras
        try:
            msg = _execute_with_retry(
                svc_local.users()
                .messages()
                .get(
                    userId="me",
                    id=mid,
                    format="full",
                    fields="id,payload/headers(name,value),payload/parts(filename,body/attachmentId,body/size,parts)",
                )
            )
        except Exception:
            return []

        payload = msg.get("payload", {})
        from_hdr = next(
            (h for h in payload.get("headers", []) if h.get("name", "").lower() == "from"), None
        )
        disp, email = parseaddr(from_hdr.get("value", "") if from_hdr else "")
        sender = sanitize_folder(disp or (email.split("@")[0] if email else "Remitente"))

        msg_tasks: list[Task] = []
        for part in _iter_parts(payload):
            fn = (part.get("filename") or "").strip()
            aid = part.get("body", {}).get("attachmentId")
            size = part.get("body", {}).get("size", 0)
            if aid and _ext_ok(fn, extensiones):
                msg_tasks.append(
                    Task(
                        message_id=mid,
                        attachment_id=aid,
                        filename=sanitize_file(fn),
                        sender_folder=sender,
                        size_est=size,
                    )
                )
        return msg_tasks

    attachments_total = 0
    # Limitamos workers en planificación para no ahogar la API de listado
    workers = max_workers or min(20, (os.cpu_count() or 4) * 2)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_extract_for_message, mid): mid for mid in mids}
        processed_msgs = 0
        for fut in as_completed(futures):
            # Lógica de Stop/Pause
            if stop_event and stop_event.is_set():
                executor.shutdown(cancel_futures=True, wait=False)
                break
            if pause_event:
                while pause_event.is_set() and not (stop_event and stop_event.is_set()):
                    time.sleep(0.5)

            try:
                msg_tasks = fut.result()
                if msg_tasks:
                    with lock:
                        tasks.extend(msg_tasks)
                        attachments_total += len(msg_tasks)
            except Exception:
                pass
            finally:
                with lock:
                    processed_msgs += 1
                    if progress_cb:
                        progress_cb(processed_msgs, total_mids, attachments_total)

    stats = {"messages": processed_msgs, "attachments": attachments_total}
    return tasks, stats


# --- DESCARGA MASIVA OPTIMIZADA ---
def run_download(
    tasks: list[Task],
    client_folder: Path,
    max_workers: int | None = None,
    progress_cb: Callable[[int, dict[str, int], str], None] | None = None,
    stop_event: threading.Event | None = None,
    pause_event: threading.Event | None = None,
) -> dict[str, int]:

    if not client_folder.exists():
        raise ValueError(f"Carpeta cliente no existe: {client_folder}")

    # StateDB ahora vive en .metadata
    metadata_dir = get_metadata_folder(client_folder)
    state = StateDB(metadata_dir)
    ram_lock = threading.Lock()

    seen_cache: set[str] = set()

    counters = {
        "downloaded": 0,
        "skipped_duplicate": 0,
        "errors": 0,
        "total": len(tasks),
        "bytes": 0,
        "unclassified_pdfs": 0,
        "error_samples": [],
    }
    done = 0


    thread_local = threading.local()

    def _svc_for_thread():
        svc_local = getattr(thread_local, "svc", None)
        if svc_local is None:
            svc_local = require_saved_session()
            thread_local.svc = svc_local
        return svc_local

    def _download_one(t: Task):
        nonlocal done
        if stop_event and stop_event.is_set():
            return
        if pause_event:
            while pause_event.is_set() and not (stop_event and stop_event.is_set()):
                time.sleep(0.5)

        status_msg = ""
        try:
            svc_local = _svc_for_thread()
            att = _execute_with_retry(
                svc_local.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=t.message_id, id=t.attachment_id)
            )
            data = base64.urlsafe_b64decode(att["data"].encode("utf-8"))
            dg = _sha256(data)

            with ram_lock:
                already_seen = dg in seen_cache
                if not already_seen:
                    seen_cache.add(dg)

            if already_seen or state.seen(dg):
                with ram_lock:
                    counters["skipped_duplicate"] += 1
                status_msg = f"♻️ Duplicado: {t.filename}"
            else:
                file_ext = Path(t.filename).suffix.lstrip(".")
                try:
                    # XML, PDF, XLSX, DOCS, OTROS siguen el flujo por tipo
                    target_folder = get_target_folder(
                        client_folder,
                        file_ext,
                        sender_name=t.sender_folder,
                    )
                except OSError as create_err:
                    logger.exception("No se pudo crear carpeta destino para %s", t.filename)
                    raise OSError(
                        f"No se pudo crear carpeta destino para '{t.filename}': {create_err}"
                    ) from create_err

                path = target_folder / t.filename
                if path.exists():
                    path = target_folder / f"{path.stem}__{dg[:8]}{path.suffix}"

                path.write_bytes(data)
                state.mark(dg, str(path), t.message_id, t.attachment_id)

                with ram_lock:
                    counters["downloaded"] += 1
                    counters["bytes"] += len(data)
                status_msg = f"⬇️ Descargado: {t.filename}"

        except OSError as e:
            with ram_lock:
                counters["errors"] += 1
                if len(counters["error_samples"]) < 10:
                    counters["error_samples"].append(f"{t.filename}: {e}")
            status_msg = f"❌ Error carpeta/archivo: {t.filename}"
        except Exception as e:
            with ram_lock:
                counters["errors"] += 1
                if len(counters["error_samples"]) < 10:
                    counters["error_samples"].append(f"{t.filename}: {e}")
            status_msg = f"❌ Error: {t.filename}"
        finally:
            with ram_lock:
                done += 1
                if progress_cb:
                    progress_cb(done, counters.copy(), status_msg)

    cfg_workers = get_setting("download_workers", None)
    workers = max_workers or cfg_workers or min(64, (os.cpu_count() or 4) * 8)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = []
        for t in tasks:
            if stop_event and stop_event.is_set():
                break
            futures.append(executor.submit(_download_one, t))

        for _ in as_completed(futures):
            if stop_event and stop_event.is_set():
                executor.shutdown(cancel_futures=True, wait=False)
                break

    state.close()
    return counters



def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


# Funciones de utilidad existentes (sin cambios)
def find_duplicates(client_folder: Path, extensiones: Sequence[str] = ()) -> dict[str, list[Path]]:
    """Busca duplicados en carpeta de cliente."""
    groups: dict[str, list[Path]] = {}
    for root, _, files in os.walk(client_folder):
        if ".metadata" in root:
            continue
        for f in files:
            if extensiones and not _ext_ok(f, extensiones):
                continue
            p = Path(root) / f
            try:
                dg = _sha256_file(p)
                groups.setdefault(dg, []).append(p)
            except Exception:
                pass
    return {k: v for k, v in groups.items() if len(v) > 1}


def delete_duplicates(groups: dict[str, list[Path]], keep: str = "first") -> int:
    deleted = 0
    for _, paths in groups.items():
        if keep == "newest":
            paths = sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)
        for p in paths[1:]:
            try:
                p.unlink()
                deleted += 1
            except Exception:
                pass
    return deleted


def open_folder(path: str):
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        raise OSError(f"No se pudo preparar la carpeta: {path}: {err}") from err

    system = platform.system()
    if system == "Windows":
        os.startfile(str(target))
    elif system == "Darwin":
        subprocess.run(["open", str(target)], check=True)
    else:
        subprocess.run(["xdg-open", str(target)], check=True)
