import imaplib
import email
import logging
import os
import re
from email.header import decode_header
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Reutilizamos la base de datos de duplicados
from facturacion_system.core.gmail_utils import StateDB, _sha256
from .file_manager import get_target_folder
from .settings import get_setting


def decode_mime_words(s):
    if not s:
        return ""
    return "".join(
        (b.decode(enc or "utf-8", errors="replace") if isinstance(b, bytes) else b)
        for b, enc in decode_header(s)
    )


def _format_date_imap(date_str):
    """Convierte 'dd/mm/yyyy' a formato IMAP 'dd-Mon-yyyy' (Siempre en Inglés)"""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    try:
        d = datetime.strptime(date_str, "%d/%m/%Y")
        return f"{d.day}-{months[d.month-1]}-{d.year}"
    except ValueError:
        return None


class ImapDownloader:
    def __init__(self, server, email_user, email_pass):
        self.server = server
        self.user = email_user
        self.password = email_pass
        self.client = None

    def connect(self):
        try:
            self.client = imaplib.IMAP4_SSL(self.server)
            self.client.login(self.user, self.password)
            return True
        except Exception as e:
            raise Exception(f"Error conexión IMAP: {e}")

    def disconnect(self):
        if self.client:
            try:
                self.client.logout()
            except Exception:
                logger.debug("Error cerrando sesión IMAP", exc_info=True)

    def select_best_folder(self):
        """Intenta seleccionar [Gmail]/All Mail, o cae a INBOX"""
        candidates = []
        if "gmail" in self.server:
            candidates.append("[Gmail]/All Mail")
            candidates.append("[Gmail]/Todos")  # Por si acaso está localizado
        candidates.append("INBOX")

        for folder in candidates:
            try:
                # Intentamos seleccionar
                status, _ = self.client.select(f'"{folder}"', readonly=True)
                if status == "OK":
                    logger.debug("Carpeta seleccionada %s", folder)
                    return folder
            except Exception:
                logger.debug("No se pudo seleccionar carpeta %s", folder, exc_info=True)
                continue

        # Fallback final
        self.client.select("INBOX", readonly=True)
        return "INBOX"

    def select_specific_folder(self, folder):
        """Fuerza la selección de una carpeta específica"""
        try:
            self.client.select(f'"{folder}"', readonly=True)
        except Exception:
            logger.warning("Error seleccionando %s, usando INBOX", folder, exc_info=True)
            self.client.select("INBOX", readonly=True)

    def search_emails(self, date_from=None, date_to=None):
        # NOTA: Se asume que la carpeta ya fue seleccionada con select_best_folder
        criteria = []
        if date_from:
            d_str = _format_date_imap(date_from)
            if d_str:
                criteria.append(f'SINCE "{d_str}"')

        if date_to:
            d_str = _format_date_imap(date_to)
            if d_str:
                criteria.append(f'BEFORE "{d_str}"')

        if not criteria:
            criteria.append("ALL")

        query = " ".join(criteria)
        logger.debug("IMAP Query: %s", query)

        status, data = self.client.search(None, query)
        if status != "OK":
            return []
        return data[0].split()

    def download_attachments(
        self, msg_ids, dest_folder, allowed_exts=None, progress_cb=None, source_folder="INBOX"
    ):
        # PASO CRÍTICO: Seleccionar la carpeta antes de hacer FETCH
        self.select_specific_folder(source_folder)

        base = Path(dest_folder)
        base.mkdir(parents=True, exist_ok=True)
        state = StateDB(base)

        stats = {"downloaded": 0, "skipped": 0, "errors": 0, "total": len(msg_ids)}
        processed = 0

        for num in msg_ids:
            try:
                # FETCH ahora funcionará porque seleccionamos carpeta arriba
                res, data = self.client.fetch(num, "(RFC822)")
                if res != "OK":
                    continue

                msg = email.message_from_bytes(data[0][1])
                sender = decode_mime_words(msg.get("From", "Desconocido"))

                safe_sender = re.sub(r'[\\/*?:"<>|]', "", sender.split("<")[0].strip())

                for part in msg.walk():
                    if part.get_content_maintype() == "multipart":
                        continue
                    if part.get("Content-Disposition") is None:
                        continue

                    fname = part.get_filename()
                    if not fname:
                        continue
                    fname = decode_mime_words(fname)

                    if allowed_exts:
                        ext = os.path.splitext(fname)[1].lower().replace(".", "")
                        if ext not in allowed_exts:
                            continue

                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue

                    max_attachment_mb = int(get_setting("max_attachment_mb", 50))
                    max_bytes = max_attachment_mb * 1024 * 1024
                    if len(payload) > max_bytes:
                        logger.warning(
                            "Adjunto omitido por tamaño > %sMB: %s",
                            max_attachment_mb,
                            fname,
                        )
                        continue

                    digest = _sha256(payload)
                    if state.seen(digest):
                        stats["skipped"] += 1
                        continue

                    ext = Path(fname).suffix.lstrip(".")
                    target_folder = get_target_folder(
                        base,
                        ext,
                        sender_name=safe_sender,
                    )
                    safe_fname = re.sub(r'[\\/*?:"<>|]', "_", fname)
                    fpath = target_folder / safe_fname

                    if fpath.exists():
                        fpath = target_folder / f"{digest[:8]}_{safe_fname}"

                    fpath.write_bytes(payload)
                    state.mark(digest, str(fpath), str(num), "")
                    stats["downloaded"] += 1

            except Exception as e:
                stats["errors"] += 1
                logger.warning("Error msg %s: %s", num, e)
            finally:
                processed += 1
                if progress_cb:
                    progress_cb(processed, stats)

        state.close()
        return stats
