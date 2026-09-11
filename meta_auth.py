"""
Credenciales de Meta (Graph API) compartidas por facebook_publisher.py e
instagram_publisher.py: ambos usan el mismo par FB_PAGE_ID /
FB_PAGE_ACCESS_TOKEN, así que la lectura del entorno, la clasificación de
los errores de la Graph API y la validación del token viven acá una sola vez.

El caso que este módulo resuelve: los Page Access Token de Meta se
invalidan solos (expiran a los ~60 días, o Meta los mata si el usuario
cambia su contraseña). Cuando eso pasa, reintentar la publicación no
arregla nada — hay que pegar un token nuevo en .env. Por eso:

  - validate() permite avisar ANTES de empezar a subir 100 MB de video.
  - classify_error() marca los errores de auth como tales (auth_error), para
    que la UI ofrezca "recargar token" en vez de un reintento condenado.
  - reload_env() vuelve a leer .env sin reiniciar el server, así el token
    nuevo se aplica en caliente.

Para dejar de renovar tokens a mano conviene usar un token de Sistema
(System User) desde Meta Business Suite: no expira. Ver los comentarios de
FB_PAGE_ACCESS_TOKEN en .env.
"""

import os
import time
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

GRAPH_API_VERSION = "v21.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

MISSING_CREDENTIALS_ERROR = "Falta configurar FB_PAGE_ID y FB_PAGE_ACCESS_TOKEN en .env"

RENEW_HINT = (
    "El token de Meta dejó de ser válido (expiró o Meta lo invalidó). "
    "Generá uno nuevo, pegalo en .env como FB_PAGE_ACCESS_TOKEN y tocá "
    "«Recargar token» — no hace falta reiniciar el servidor."
)

# Códigos de la Graph API que significan "el token no sirve" (no reintentar).
# 190 = access token inválido/expirado; 102 = sesión caducada;
# 463/467 = token expirado o invalidado. 10 y 200-299 son de permisos
# faltantes: tampoco se arreglan reintentando, pero sí regenerando el token
# con los scopes correctos, así que entran en la misma categoría.
_AUTH_ERROR_CODES = {102, 190, 463, 467}
_PERMISSION_ERROR_CODES = {10}

# validate() se llama desde la UI (badge de estado) y antes de cada
# publicación; se cachea el resultado para no pegarle a Graph cada vez.
_CACHE_TTL_SECONDS = 300
_cache: Optional[dict] = None
_cache_ts: float = 0.0


def get_credentials() -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """
    Devuelve (page_id, access_token, err). err es None si ambas están
    configuradas, o el dict de error listo para devolver al usuario si falta
    alguna — para poder hacer:

        page_id, access_token, err = meta_auth.get_credentials()
        if err:
            return err
    """
    page_id = os.environ.get("FB_PAGE_ID")
    access_token = os.environ.get("FB_PAGE_ACCESS_TOKEN")
    if not page_id or not access_token:
        return None, None, {"ok": False, "error": MISSING_CREDENTIALS_ERROR}
    return page_id, access_token, None


def is_auth_error(payload: dict) -> bool:
    """True si el error de la Graph API es por token inválido/permisos."""
    error = (payload or {}).get("error") or {}
    code = error.get("code")
    if code in _AUTH_ERROR_CODES or code in _PERMISSION_ERROR_CODES:
        return True
    if isinstance(code, int) and 200 <= code <= 299:
        return True  # familia de errores de permisos de la Graph API
    return error.get("type") == "OAuthException"


def classify_error(payload: dict, platform: str) -> dict:
    """
    Convierte el payload de error de la Graph API en el dict de error que
    consumen los publishers y la UI.

    El mensaje de Meta se pasa tal cual porque suele explicar exactamente
    qué falló; a los errores de token se le agrega qué tiene que hacer el
    usuario, porque el mensaje de Meta no lo dice.
    """
    error = (payload or {}).get("error") or {}
    message = error.get("message") or f"Error desconocido de {platform}"
    auth_error = is_auth_error(payload)
    if auth_error:
        message = f"{message} — {RENEW_HINT}"
    return {"ok": False, "error": message, "auth_error": auth_error}


def _debug_token(access_token: str) -> dict:
    """
    Datos de expiración y permisos del token, vía /debug_token. Necesita un
    app token (FB_APP_ID|FB_APP_SECRET), que es opcional en este proyecto:
    si no están configurados, devuelve {} y validate() se queda solo con el
    ping (válido / no válido, sin fecha).
    """
    app_id = os.environ.get("FB_APP_ID")
    app_secret = os.environ.get("FB_APP_SECRET")
    if not app_id or not app_secret:
        return {}
    try:
        response = requests.get(
            f"{GRAPH_URL}/debug_token",
            params={"input_token": access_token, "access_token": f"{app_id}|{app_secret}"},
            timeout=20,
        )
        data = (response.json() or {}).get("data") or {}
    except (requests.RequestException, ValueError):
        return {}

    result = {"scopes": data.get("scopes")}
    expires_at = data.get("expires_at") or data.get("data_access_expires_at")
    # expires_at == 0 significa que el token no expira (System User token).
    if expires_at:
        result["expires_at"] = datetime.fromtimestamp(int(expires_at)).isoformat()
    return result


def validate(force: bool = False) -> dict:
    """
    Chequea que el token siga sirviendo, con un GET liviano a la Página.

    Returns:
        {"ok": True, "page_name": str, "expires_at": iso|None, "scopes": list|None}
        o {"ok": False, "error": str, "auth_error": bool}.
    """
    global _cache, _cache_ts

    if not force and _cache is not None and time.time() - _cache_ts < _CACHE_TTL_SECONDS:
        return _cache

    page_id, access_token, err = get_credentials()
    if err:
        return {**err, "auth_error": True}  # sin credenciales no hay nada que reintentar

    try:
        response = requests.get(
            f"{GRAPH_URL}/{page_id}",
            params={"fields": "id,name", "access_token": access_token},
            timeout=20,
        )
    except requests.RequestException as e:
        # Falla de red: no se puede afirmar que el token esté mal, así que no
        # se cachea ni se marca como auth_error (el reintento sí tiene sentido).
        return {"ok": False, "error": f"No se pudo verificar el token con Meta: {e}", "auth_error": False}

    try:
        payload = response.json()
    except ValueError:
        return {
            "ok": False,
            "error": f"Respuesta inesperada de Meta al verificar el token (HTTP {response.status_code})",
            "auth_error": False,
        }

    if not response.ok or "error" in payload:
        result = classify_error(payload, "Meta")
    else:
        result = {"ok": True, "page_name": payload.get("name"), **_debug_token(access_token)}

    _cache, _cache_ts = result, time.time()
    return result


def reload_env() -> dict:
    """
    Vuelve a leer .env pisando lo que ya esté en el entorno (load_dotenv()
    normal no sobreescribe) y revalida. Es lo que permite aplicar un token
    nuevo sin reiniciar el servidor.
    """
    load_dotenv(override=True)
    return validate(force=True)
