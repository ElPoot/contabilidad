from __future__ import annotations

import base64
import json
import logging
import secrets
from pathlib import Path

import keyring
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from facturacion_system.config import CONFIG_DIR

# Compat con sistema anterior
IMAP_ACCOUNTS_FILE = CONFIG_DIR / "imap_accounts.json"
SERVICE_ID = "MassDownload_App"

logger = logging.getLogger(__name__)

_master_key: bytes | None = None


# ----------------------
# Vault crypto internals
# ----------------------
def _vault_path() -> Path:
    """Z:/DATA/CONFIG/imap_vault.json"""
    return CONFIG_DIR / "imap_vault.json"


def _salt_path() -> Path:
    """Z:/DATA/CONFIG/vault_salt.bin"""
    return CONFIG_DIR / "vault_salt.bin"


def _get_or_create_salt() -> bytes:
    """
    Lee vault_salt.bin si existe.
    Si no, crea 32 bytes aleatorios y los guarda.
    """
    path = _salt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        data = path.read_bytes()
        if len(data) >= 16:
            return data
    salt = secrets.token_bytes(32)
    path.write_bytes(salt)
    return salt


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256, 600_000 iteraciones, 32 bytes output."""
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000)
    return kdf.derive((passphrase or "").encode("utf-8"))


def _encrypt(key: bytes, plaintext: str) -> dict:
    """
    AES-256-GCM con nonce aleatorio de 12 bytes.
    Retorna dict con ct/nonce/tag en base64.
    """
    nonce = secrets.token_bytes(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce))
    enc = cipher.encryptor()
    ct = enc.update((plaintext or "").encode("utf-8")) + enc.finalize()
    return {
        "ct": base64.b64encode(ct).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "tag": base64.b64encode(enc.tag).decode("ascii"),
    }


def _decrypt(key: bytes, entry: dict) -> str:
    """
    AES-256-GCM decrypt desde dict con ct/nonce/tag.
    Lanza InvalidTag si la clave es incorrecta o los datos fueron alterados.
    """
    ct = base64.b64decode(entry["ct"])
    nonce = base64.b64decode(entry["nonce"])
    tag = base64.b64decode(entry["tag"])
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag))
    dec = cipher.decryptor()
    pt = dec.update(ct) + dec.finalize()
    return pt.decode("utf-8")


def _load_vault() -> dict:
    """Lee imap_vault.json. Retorna {} si no existe."""
    path = _vault_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        logger.warning("No se pudo leer imap_vault.json", exc_info=True)
        return {}


def _write_vault_atomic(data: dict) -> None:
    """
    Escribe a temporal y luego rename atómico.
    Nunca deja vault en estado parcial.
    """
    path = _vault_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _validate_passphrase_strength(passphrase: str) -> tuple[bool, str]:
    if len(passphrase or "") < 8:
        return False, "Mínimo 8 caracteres"
    if not any(c.isalpha() for c in (passphrase or "")):
        return False, "Debe incluir al menos una letra"
    if not any(c.isdigit() for c in (passphrase or "")):
        return False, "Debe incluir al menos un número"
    return True, ""


# ----------------------
# Vault session controls
# ----------------------
def vault_exists() -> bool:
    """Retorna True si imap_vault.json existe y tiene __vault_check__."""
    v = _load_vault()
    return "__vault_check__" in v


def is_vault_unlocked() -> bool:
    """Retorna True si _master_key está en memoria."""
    return _master_key is not None


def lock_vault() -> None:
    """Limpia _master_key de memoria."""
    global _master_key
    _master_key = None


def unlock_vault(passphrase: str) -> bool:
    """
    Deriva clave y verifica contra __vault_check__.
    Si correcta, guarda _master_key y retorna True.
    Si incorrecta, retorna False sin modificar _master_key.
    """
    global _master_key
    vault = _load_vault()
    check = vault.get("__vault_check__")
    if not isinstance(check, dict):
        return False

    candidate = _derive_key(passphrase, _get_or_create_salt())
    try:
        marker = _decrypt(candidate, check)
        if marker != "vault-ok-v1":
            return False
    except (InvalidTag, KeyError, ValueError, TypeError):
        return False

    _master_key = candidate
    return True


def initialize_vault(passphrase: str) -> bool:
    """
    Primer uso: valida fortaleza, crea salt, deriva clave,
    crea __vault_check__, escribe vault inicial y desbloquea.
    """
    global _master_key
    ok, _ = _validate_passphrase_strength(passphrase)
    if not ok:
        return False

    key = _derive_key(passphrase, _get_or_create_salt())
    data = _load_vault()
    data["__vault_check__"] = _encrypt(key, "vault-ok-v1")
    _write_vault_atomic(data)
    _master_key = key
    return True


# ----------------------
# Legacy metadata helpers
# ----------------------
def _load_metadata():
    if not IMAP_ACCOUNTS_FILE.exists():
        return {}
    try:
        raw = json.loads(IMAP_ACCOUNTS_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_metadata(data):
    IMAP_ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    IMAP_ACCOUNTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ----------------------
# Public API (signatures)
# ----------------------
def save_imap_credential(email: str, password: str, host: str) -> None:
    """
    Cifra password con _master_key y guarda en imap_vault.json.
    Lanza RuntimeError si bóveda está bloqueada.
    """
    if _master_key is None:
        raise RuntimeError("Bóveda bloqueada. Desbloquea primero.")

    email = (email or "").strip()
    if not email:
        return

    vault = _load_vault()
    vault[email] = {
        "host": (host or "").strip(),
        **_encrypt(_master_key, password or ""),
    }
    _write_vault_atomic(vault)


def get_imap_credential(email: str) -> tuple[str | None, str | None]:
    """
    Retorna (host, password).
    Si _master_key es None retorna (host, None).
    Si email no está en vault retorna (None, None).
    """
    email = (email or "").strip()
    if not email:
        return None, None

    vault = _load_vault()
    entry = vault.get(email)
    if not isinstance(entry, dict):
        return None, None

    host = entry.get("host")
    if _master_key is None:
        return host, None

    try:
        pwd = _decrypt(_master_key, entry)
        return host, pwd
    except Exception:
        return host, None


def list_imap_emails() -> list[str]:
    """
    Lee emails desde imap_vault.json sin necesitar passphrase.
    Filtra __vault_check__.
    """
    vault = _load_vault()
    return sorted([k for k in vault.keys() if k != "__vault_check__"])


def delete_imap_credential(email: str) -> None:
    """
    Elimina entrada de imap_vault.json usando _write_vault_atomic.
    No requiere passphrase para borrar.
    """
    email = (email or "").strip()
    if not email:
        return
    vault = _load_vault()
    if email in vault:
        del vault[email]
        _write_vault_atomic(vault)


def migrate_from_keyring_if_needed() -> int:
    """
    Ejecutar DESPUÉS de unlock_vault().
    Migra credenciales legacy desde imap_accounts.json + keyring al vault cifrado.
    Idempotente. Retorna cantidad migrada.
    """
    if _master_key is None:
        return 0

    meta = _load_metadata()
    if not meta:
        return 0

    migrated = 0
    for email, info in meta.items():
        if not isinstance(info, dict):
            continue
        host = (info.get("host") or "").strip()
        if not host:
            continue

        v_host, v_pwd = get_imap_credential(email)
        if v_host and v_pwd is not None:
            continue

        try:
            pwd = keyring.get_password(SERVICE_ID, email)
        except Exception:
            pwd = None

        if pwd is None:
            continue

        save_imap_credential(email, pwd, host)
        try:
            keyring.delete_password(SERVICE_ID, email)
        except Exception:
            pass
        migrated += 1

    return migrated
