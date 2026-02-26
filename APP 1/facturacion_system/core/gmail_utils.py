"""
facturacion_system/core/gmail_utils.py
Utilidades compartidas para autenticación, fechas, queries y estado.
"""

from __future__ import annotations
import hashlib
import http.client
import os
import json
import random
import re
import socket
import sqlite3
import threading
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from httplib2 import HttpLib2Error

from facturacion_system.config import (
    CREDENTIALS_FILE,
    CURRENT_ACCOUNT_FILE,
    SCOPES,
    TOKENS_DIR,
)


# ---------------------------------------------------------------------------
# CONFIGURACIÓN Y RUTAS
# ---------------------------------------------------------------------------
def _tokens_dir() -> Path:
    path = Path(TOKENS_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _token_path(email: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_.@+-]", "_", email.strip())
    return _tokens_dir() / f"{safe}.json"


def _load_credentials(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return Credentials.from_authorized_user_info(data, SCOPES)


def _save_credentials(path: Path, creds) -> None:
    path.write_text(creds.to_json(), encoding="utf-8")


# ---------------------------------------------------------------------------
# AUTENTICACIÓN (LO QUE FALTABA)
# ---------------------------------------------------------------------------
def list_accounts() -> list[str]:
    """Devuelve las cuentas conocidas a partir de los tokens guardados."""
    return sorted(p.stem for p in _tokens_dir().glob("*.json"))


def current_account() -> str | None:
    try:
        val = Path(CURRENT_ACCOUNT_FILE).read_text(encoding="utf-8").strip()
        return val or None
    except FileNotFoundError:
        return None


def _set_current_account(email: str | None):
    path = Path(CURRENT_ACCOUNT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if email:
        path.write_text(email, encoding="utf-8")
    elif path.exists():
        path.unlink()


def authenticate_gmail(account_email: str | None = None, *, force_new: bool = False):
    """Autentica Gmail, guarda token y define la cuenta activa."""
    email = account_email or current_account()
    creds = None
    token_path = _token_path(email) if email else None

    if token_path and token_path.exists() and not force_new:
        try:
            creds = _load_credentials(token_path)
        except (json.JSONDecodeError, ValueError, OSError):
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token and not force_new:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(f"No se encontró {CREDENTIALS_FILE}")

            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

            # Obtener email para nombrar el token
            svc = build("gmail", "v1", credentials=creds)
            profile = svc.users().getProfile(userId="me").execute()
            email = profile.get("emailAddress")
            token_path = _token_path(email)
            _save_credentials(token_path, creds)

    svc = build("gmail", "v1", credentials=creds)
    if email:
        _set_current_account(email)
    return svc


def require_saved_session(account_email: str | None = None):
    """Obtiene servicio Gmail sin abrir navegador (para procesos de fondo)."""
    email = account_email or current_account()
    if not email:
        raise ValueError("Inicia sesión desde la interfaz antes de continuar.")

    token_path = _token_path(email)
    if not token_path.exists():
        raise ValueError("Sesión no encontrada. Inicia sesión nuevamente.")

    try:
        creds = _load_credentials(token_path)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        raise ValueError("Sesión inválida. Inicia sesión nuevamente.") from exc

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise ValueError("Sesión expirada. Inicia sesión nuevamente.")

    return build("gmail", "v1", credentials=creds)


def logout_gmail(*, account_email: str | None = None, forget: bool = False):
    email = account_email or current_account()
    if forget and email:
        p = _token_path(email)
        if p.exists():
            p.unlink()
    _set_current_account(None)


# ---------------------------------------------------------------------------
# HELPERS DE QUERY (NUEVOS FILTROS)
# ---------------------------------------------------------------------------
def _norm_date_any(d: str) -> str:
    d = d.strip().replace("-", "/")
    parts = d.split("/")
    if len(parts) != 3:
        raise ValueError(f"Fecha inválida: {d}")
    if len(parts[0]) == 4:
        y, m, dd = map(int, parts)
    else:
        dd, m, y = map(int, parts)
    dt = datetime(y, m, dd)
    return f"{dt.year:04d}/{dt.month:02d}/{dt.day:02d}"


def _before_inclusive(fin_ymd: str) -> str:
    y, m, d = map(int, fin_ymd.split("/"))
    dt = datetime(y, m, d) + timedelta(days=1)
    return f"{dt.year:04d}/{dt.month:02d}/{dt.day:02d}"


def build_query(
    after_ymd: str,
    before_ymd_inclusive: str,
    exts: Sequence[str],
    exclude_inbox: bool,
    exclude_sent: bool,
    exclude_from: Sequence[str],
    include_from: Sequence[str] | None = None,
    include_terms: Sequence[str] | None = None,
    exclude_terms: Sequence[str] | None = None,
) -> str:
    q = ["in:anywhere", "has:attachment"]

    if exclude_inbox:
        q.append("-in:inbox")
    if exclude_sent:
        q.append("-in:sent")

    if exts:
        toks = [f"filename:{e.lower().lstrip('.')}" for e in exts]
        q.append("(" + " OR ".join(toks) + ")")

    q.append(f"after:{after_ymd}")
    q.append(f"before:{_before_inclusive(before_ymd_inclusive)}")

    for addr in exclude_from or []:
        if addr.strip():
            q.append(f"-from:{addr.strip()}")

    inc_from = [f.strip() for f in (include_from or []) if f.strip()]
    if inc_from:
        q.append("from:(" + " OR ".join(inc_from) + ")")

    inc_terms = [t.strip() for t in (include_terms or []) if t.strip()]
    if inc_terms:
        q.append("(" + " OR ".join(f'"{t}"' for t in inc_terms) + ")")

    exc_terms = [t.strip() for t in (exclude_terms or []) if t.strip()]
    if exc_terms:
        for t in exc_terms:
            q.append(f'-"{t}"')

    return " ".join(q)


# ---------------------------------------------------------------------------
# BASE DE DATOS Y UTILIDADES API
# ---------------------------------------------------------------------------
class StateDB:
    def __init__(self, base_dir: Path):
        base = Path(base_dir)
        # Si se recibe una carpeta .metadata, guardar directamente state.sqlite allí
        if base.name == ".metadata":
            self.path = base / "state.sqlite"
        else:
            self.path = base / ".gmail_downloader" / "state.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, timeout=30, isolation_level=None
        )
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS files(digest TEXT PRIMARY KEY, saved_path TEXT, message_id TEXT, attachment_id TEXT, created_at TEXT DEFAULT (datetime('now')))"
            )

    def seen(self, digest: str) -> bool:
        with self._lock:
            if self._conn is None:
                return False
            return (
                self._conn.execute("SELECT 1 FROM files WHERE digest=?", (digest,)).fetchone()
                is not None
            )

    def mark(self, digest: str, path: str, mid: str, aid: str) -> None:
        with self._lock:
            if self._conn is None:
                return
            self._conn.execute(
                "INSERT OR IGNORE INTO files(digest, saved_path, message_id, attachment_id) VALUES (?,?,?,?)",
                (digest, path, mid, aid),
            )

    def close(self):
        with self._lock:
            if self._conn is None:
                return
            self._conn.close()
            self._conn = None


def _list_messages(service, q: str) -> list[str]:
    mids = []
    call = service.users().messages().list(userId="me", q=q, maxResults=100)
    while True:
        resp = _execute_with_retry(call)
        mids.extend([m["id"] for m in resp.get("messages", [])])
        token = resp.get("nextPageToken")
        if not token:
            break
        call = service.users().messages().list(userId="me", q=q, maxResults=100, pageToken=token)
    return mids


def _iter_parts(payload):
    stack = [payload]
    while stack:
        part = stack.pop()
        for sub in part.get("parts", []) or []:
            stack.append(sub)
        yield part


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _execute_with_retry(request, *, max_attempts: int = 8, base_sleep: float = 1.0):
    attempt = 0
    while True:
        try:
            return request.execute()
        except HttpError as e:
            if getattr(e.resp, "status", 0) not in {403, 429}:
                raise
            if attempt + 1 >= max_attempts:
                raise
            time.sleep(base_sleep * (2**attempt) + random.uniform(0, 0.5))
            attempt += 1
        except (socket.error, http.client.HTTPException, HttpLib2Error):
            if attempt + 1 >= max_attempts:
                raise
            time.sleep(base_sleep * (2**attempt))
            attempt += 1
