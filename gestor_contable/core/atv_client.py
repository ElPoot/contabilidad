"""
ATV Client — consulta de estado de facturas via API de recepcion de ATV.

Credenciales almacenadas en Windows Credential Manager via keyring.
Token cacheado solo en memoria (NUNCA en disco).
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Optional

import keyring
import keyring.errors
import requests

logger = logging.getLogger(__name__)

_SERVICE      = "GestorContable_ATV"
_KEY_USUARIO  = "usuario"
_KEY_CLAVE    = "clave"

_TOKEN_URL    = "https://idp.comprobanteselectronicos.go.cr/auth/realms/rut/protocol/openid-connect/token"
_RECEPCION_URL = "https://api.comprobanteselectronicos.go.cr/recepcion/v1/recepcion/{clave}"

# Cache en memoria — nunca persiste a disco
_cached_token: Optional[str] = None
_token_expires_at: float = 0.0


# ── Gestión de credenciales ───────────────────────────────────────────────────

def save_credentials(usuario: str, clave: str) -> None:
    """Guarda credenciales ATV en Windows Credential Manager."""
    keyring.set_password(_SERVICE, _KEY_USUARIO, usuario.strip())
    keyring.set_password(_SERVICE, _KEY_CLAVE, clave)
    logger.info("Credenciales ATV guardadas en keyring")


def delete_credentials() -> None:
    """Elimina credenciales ATV del Windows Credential Manager."""
    global _cached_token, _token_expires_at
    for key in (_KEY_USUARIO, _KEY_CLAVE):
        try:
            keyring.delete_password(_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass
    _cached_token = None
    _token_expires_at = 0.0
    logger.info("Credenciales ATV eliminadas de keyring")


def has_credentials() -> bool:
    """Retorna True si hay credenciales guardadas."""
    try:
        u = keyring.get_password(_SERVICE, _KEY_USUARIO)
        c = keyring.get_password(_SERVICE, _KEY_CLAVE)
        return bool(u and c)
    except Exception as exc:
        logger.exception("Fallo de keyring al verificar credenciales ATV: %s", exc)
        return False


def get_usuario() -> str:
    """Retorna el usuario guardado (para mostrar en UI), o cadena vacía."""
    try:
        return keyring.get_password(_SERVICE, _KEY_USUARIO) or ""
    except Exception as exc:
        logger.exception("Fallo de keyring al obtener usuario: %s", exc)
        return ""


# ── Token (solo en memoria) ───────────────────────────────────────────────────

def _fetch_token() -> str:
    """Obtiene token de ATV. Usa cache en memoria si aun es valido."""
    global _cached_token, _token_expires_at

    now = time.time()
    if _cached_token and now < _token_expires_at:
        return _cached_token

    usuario = keyring.get_password(_SERVICE, _KEY_USUARIO)
    clave   = keyring.get_password(_SERVICE, _KEY_CLAVE)
    if not usuario or not clave:
        raise RuntimeError("No hay credenciales ATV configuradas")

    resp = requests.post(
        _TOKEN_URL,
        data={
            "client_id":  "api-prod",
            "grant_type": "password",
            "username":   usuario,
            "password":   clave,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    token = data.get("access_token")
    if not token:
        raise RuntimeError("ATV no retorno access_token")

    expires_in = int(data.get("expires_in", 300))
    _cached_token      = token
    _token_expires_at  = now + expires_in - 30  # 30s de margen
    return token


# ── Consulta de facturas ──────────────────────────────────────────────────────

def query_invoice_status(clave: str) -> dict:
    """
    Consulta el estado de una factura por clave de 50 digitos.

    Retorna dict con:
        ind_estado      : str   "aceptado" | "rechazado" | "procesando" | "no_encontrado"
        fecha           : str | None
        respuesta_xml   : str | None   (texto XML del MensajeHacienda)
        respuesta_xml_bytes : bytes | None  (para guardar en disco)
        error           : str | None   (descripcion si fallo la consulta)
    """
    result: dict = {
        "ind_estado":          "desconocido",
        "fecha":               None,
        "respuesta_xml":       None,
        "respuesta_xml_bytes": None,
        "error":               None,
    }

    global _cached_token, _token_expires_at

    try:
        token = _fetch_token()
    except Exception as exc:
        result["error"] = f"Error obteniendo token ATV: {exc}"
        logger.error(result["error"])
        return result

    url = _RECEPCION_URL.format(clave=clave)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        result["error"] = f"Error de red consultando ATV: {exc}"
        logger.error(result["error"])
        return result

    # 400 con "no ha sido recibido" = comprobante nunca fue enviado a Hacienda
    # Es una respuesta valida de negocio, no un error de token.
    if resp.status_code == 400:
        x_error = resp.headers.get("X-Error-Cause", "")
        if "no ha sido recibido" in x_error.lower():
            result["ind_estado"] = "no_recibido"
            logger.info("ATV: clave %s... no fue recibida por Hacienda", clave[:15])
            return result

    # Token expirado o invalido — reintentar una vez con token fresco
    if resp.status_code in (400, 401):
        x_error = resp.headers.get("X-Error-Cause", "")
        logger.info("ATV HTTP %s (X-Error-Cause=%s) — reintentando con token fresco (clave=%s...)",
                     resp.status_code, x_error or "N/A", clave[:15])
        _cached_token = None
        _token_expires_at = 0.0
        try:
            token = _fetch_token()
            headers["Authorization"] = f"Bearer {token}"
            resp = requests.get(url, headers=headers, timeout=15)
        except Exception as exc:
            result["error"] = f"Error reintentando con token fresco: {exc}"
            logger.error(result["error"])
            return result

    if resp.status_code == 404:
        result["ind_estado"] = "no_encontrado"
        return result

    if not resp.ok:
        body = ""
        try:
            body = resp.text[:300]
        except Exception as exc:
            body = f"<body no legible: {type(exc).__name__}: {exc}>"
            logger.warning(
                "No se pudo leer body de error ATV para clave %s...",
                clave[:20],
                exc_info=True,
            )
        x_error = resp.headers.get("X-Error-Cause", "")
        result["error"] = f"ATV respondio HTTP {resp.status_code}"
        logger.error("ATV HTTP %s para clave %s... | X-Error-Cause=%s | body=%s",
                     resp.status_code, clave[:20], x_error or "N/A", body)
        return result

    try:
        data = resp.json()
    except Exception as exc:
        result["error"] = f"Respuesta ATV no es JSON valido: {exc}"
        logger.error(result["error"])
        return result

    result["ind_estado"] = data.get("ind-estado", "desconocido")
    result["fecha"]      = data.get("fecha")

    b64 = data.get("respuesta-xml")
    if b64:
        try:
            xml_bytes = base64.b64decode(b64)
            result["respuesta_xml_bytes"] = xml_bytes
            try:
                result["respuesta_xml"] = xml_bytes.decode("utf-8")
            except UnicodeDecodeError:
                result["respuesta_xml"] = xml_bytes.decode("latin-1", errors="replace")
        except Exception as exc:
            logger.warning("No se pudo decodificar respuesta-xml: %s", exc)

    return result
