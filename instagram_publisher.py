"""
Publicación de videos (Reels) en la cuenta de Instagram vinculada a la
Página de Facebook, vía Graph API. Reutiliza las mismas credenciales que
facebook_publisher.py (FB_PAGE_<N>_ID / FB_PAGE_<N>_TOKEN, vía meta_auth.py) — Instagram ya
está vinculado a esa Página, no hace falta login OAuth aparte.

El token necesita además los permisos instagram_basic +
instagram_content_publish (si fue creado solo con pages_manage_posts /
pages_read_engagement, Meta va a devolver un error de permisos claro en la
primera llamada — en ese caso hay que regenerarlo en Meta for Developers).

Usa "resumable upload" (subida directa del archivo local a
rupload.facebook.com) en vez de la vía normal de Instagram (video_url
público) — evita tener que hostear el video en algún lado.

Todo lo publicado se marca con is_ai_generated=true (contenido generado con
IA/TTS). Facebook (Página) no tiene ese parámetro documentado en su propia
API, así que el marcado como IA solo aplica del lado de Instagram.
"""

import time
from pathlib import Path
from typing import Callable, Optional

import requests

import meta_auth

GRAPH_API_VERSION = "v21.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
RUPLOAD_URL = f"https://rupload.facebook.com/ig-api-upload/{GRAPH_API_VERSION}"

STATUS_POLL_SECONDS = 5
STATUS_MAX_WAIT_SECONDS = 600

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # segundos: 2, 4, 8...


def _request_with_retry(method: str, url: str, max_retries: int = MAX_RETRIES, **kwargs):
    """GET/POST con reintentos ante error de red o HTTP 5xx (transitorios)."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            response = requests.request(method, url, **kwargs)
        except requests.RequestException as e:
            last_exc = e
        else:
            if response.status_code < 500:
                return response
            last_exc = requests.RequestException(f"HTTP {response.status_code}")
        if attempt < max_retries - 1:
            time.sleep(RETRY_BACKOFF_BASE ** attempt)
    raise last_exc


def _parse_response(response) -> tuple[Optional[dict], Optional[dict]]:
    try:
        payload = response.json()
    except ValueError:
        return None, {"ok": False, "error": f"Respuesta inesperada de Instagram (HTTP {response.status_code})"}

    if not response.ok or "error" in payload:
        return None, meta_auth.classify_error(payload, "Instagram")

    return payload, None


def get_media_stats(media_ids: list, id_to_page: Optional[dict] = None) -> dict:
    """
    Consulta reproducciones/likes/comentarios de reels ya publicados (Graph API).

    Args:
        media_ids: lista de IDs de media de Instagram.
        id_to_page: mapeo {media_id: page_id} para usar el token de la
            página que publicó cada reel (varias páginas, cada una con su
            propio token). Los reels sin entrada (registros previos a esta
            función) usan la primera página configurada.

    Returns:
        dict {media_id: {"views": int, "likes": int, "comments": int}}. Si no
        hay token configurado o falla la consulta, devuelve {} (sin cortar el flujo).
    """
    if not media_ids:
        return {}
    id_to_page = id_to_page or {}

    stats = {}
    for media_id in media_ids:
        _, access_token, err = meta_auth.get_credentials(id_to_page.get(media_id))
        if err:
            continue
        try:
            response = requests.get(
                f"{GRAPH_URL}/{media_id}",
                params={
                    "fields": "plays,like_count,comments_count",
                    "access_token": access_token,
                },
                timeout=30,
            )
            payload = response.json()
            if not response.ok or "error" in payload:
                continue
            stats[media_id] = {
                "views": int(payload.get("plays", 0) or 0),
                "likes": int(payload.get("like_count", 0) or 0),
                "comments": int(payload.get("comments_count", 0) or 0),
            }
        except Exception:
            continue
    return stats


def _get_ig_user_id(page_id: str, access_token: str) -> tuple[Optional[str], Optional[dict]]:
    try:
        response = _request_with_retry(
            "GET",
            f"{GRAPH_URL}/{page_id}",
            params={"fields": "instagram_business_account", "access_token": access_token},
            timeout=30,
        )
    except requests.RequestException as e:
        return None, {"ok": False, "error": f"No se pudo conectar con Facebook: {e}"}

    payload, err = _parse_response(response)
    if err:
        return None, err

    ig_account = payload.get("instagram_business_account")
    if not ig_account:
        return None, {
            "ok": False,
            "error": "Esta Página de Facebook no tiene una cuenta de Instagram vinculada "
            "(o al token le falta el permiso instagram_basic).",
        }
    return ig_account["id"], None


def publish_video(
    video_path: str,
    title: str,
    description: str,
    page_id: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Sube un video como Reel a la cuenta de Instagram vinculada a la Página
    de Facebook indicada (o la primera configurada si no se pasa page_id),
    marcado como contenido generado con IA.

    Returns:
        {"ok": True, "media_id": str} si se publicó, o
        {"ok": False, "error": str} si algo falló.
    """
    page_id, access_token, err = meta_auth.get_credentials(page_id)
    if err:
        return err

    path = Path(video_path)
    if not path.exists():
        return {"ok": False, "error": f"No se encontró el video: {video_path}"}

    # Ver el comentario equivalente en facebook_publisher.publish_video.
    token_check = meta_auth.validate(page_id)
    if not token_check["ok"]:
        return {"ok": False, "error": token_check["error"], "auth_error": token_check.get("auth_error", False)}

    notify = on_status or (lambda msg: None)

    ig_user_id, err = _get_ig_user_id(page_id, access_token)
    if err:
        return err

    # Fase 1: crear el container en modo resumable (sin video_url — el
    # archivo se sube en la fase 2, directo desde acá).
    notify("Creando publicación en Instagram...")
    try:
        container_response = _request_with_retry(
            "POST",
            f"{GRAPH_URL}/{ig_user_id}/media",
            params={"access_token": access_token},
            json={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": description,
                "is_ai_generated": True,
                "share_to_feed": True,
            },
            timeout=30,
        )
    except requests.RequestException as e:
        return {"ok": False, "error": f"No se pudo conectar con Instagram: {e}"}

    container_payload, err = _parse_response(container_response)
    if err:
        return err
    container_id = container_payload["id"]

    # Fase 2: subir el archivo entero a rupload.facebook.com (streaming, sin
    # cargarlo entero en memoria). Reintenta desde el principio del archivo
    # ante error de red (el upload no es incremental como en Facebook).
    notify("Subiendo video a Instagram...")
    file_size = path.stat().st_size
    last_exc = None
    upload_response = None
    for attempt in range(MAX_RETRIES):
        try:
            with open(path, "rb") as f:
                upload_response = requests.post(
                    f"{RUPLOAD_URL}/{container_id}",
                    headers={
                        "Authorization": f"OAuth {access_token}",
                        "Content-Type": "video/mp4",
                        "offset": "0",
                        "file_size": str(file_size),
                    },
                    data=f,
                    timeout=300,
                )
            break
        except requests.RequestException as e:
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                notify(f"Reintentando subida a Instagram ({attempt + 2}/{MAX_RETRIES})...")
                time.sleep(RETRY_BACKOFF_BASE ** attempt)
    else:
        return {"ok": False, "error": f"Error subiendo el video a Instagram: {last_exc}"}

    # Nota: Instagram suele responder 400 "ProcessingFailedError" acá aunque
    # el archivo se haya recibido bien y termine procesando OK — la única
    # fuente de verdad confiable es el status_code que se consulta abajo, no
    # el código HTTP de esta subida. Por eso no se corta acá aunque falle.

    # Fase 3: esperar a que Instagram termine de procesar el video.
    notify("Esperando que Instagram procese el video (puede tardar varios minutos)...")
    deadline = time.time() + STATUS_MAX_WAIT_SECONDS
    was_rate_limited = False
    while time.time() < deadline:
        try:
            status_response = requests.get(
                f"{GRAPH_URL}/{container_id}",
                params={"fields": "status_code", "access_token": access_token},
                timeout=30,
            )
            status_payload, err = _parse_response(status_response)
        except requests.RequestException as e:
            return {"ok": False, "error": f"Error consultando el estado en Instagram: {e}"}
        if err:
            # Rate limit de la app (transitorio) mientras se sigue procesando
            # el video: esperar y reintentar en vez de abortar la publicación.
            try:
                is_transient = status_response.json().get("error", {}).get("is_transient")
            except ValueError:
                is_transient = False
            if is_transient:
                was_rate_limited = True
                time.sleep(STATUS_POLL_SECONDS)
                continue
            return err

        status_code = status_payload.get("status_code")
        if status_code == "FINISHED":
            break
        if status_code in ("ERROR", "EXPIRED"):
            return {"ok": False, "error": f"Instagram no pudo procesar el video (estado: {status_code})"}
        time.sleep(STATUS_POLL_SECONDS)
    else:
        if was_rate_limited:
            return {"ok": False, "error": "Instagram: se alcanzó el límite de solicitudes de la app. Esperá unos minutos y volvé a intentar."}
        return {"ok": False, "error": "Instagram tardó demasiado en procesar el video."}

    # Fase 4: publicar el container ya procesado.
    notify("Publicando en Instagram...")
    try:
        publish_response = _request_with_retry(
            "POST",
            f"{GRAPH_URL}/{ig_user_id}/media_publish",
            params={"access_token": access_token},
            json={"creation_id": container_id},
            timeout=30,
        )
    except requests.RequestException as e:
        return {"ok": False, "error": f"No se pudo publicar en Instagram: {e}"}

    publish_payload, err = _parse_response(publish_response)
    if err:
        return err

    return {"ok": True, "media_id": publish_payload["id"]}


def publish_photo(
    image_url: str,
    caption: str,
    page_id: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Publica una imagen a partir de una URL PUBLICA -- a diferencia del
    video, la Graph API de Instagram no admite subida de archivo local para
    imagenes (el container IMAGE exige "image_url" servida desde afuera). El
    container IMAGE publica sincronicamente: no hace falta sondear
    status_code como en REELS.

    Returns:
        {"ok": True, "media_id": str} si se publicó, o
        {"ok": False, "error": str} si algo falló.
    """
    page_id, access_token, err = meta_auth.get_credentials(page_id)
    if err:
        return err

    token_check = meta_auth.validate(page_id)
    if not token_check["ok"]:
        return {"ok": False, "error": token_check["error"], "auth_error": token_check.get("auth_error", False)}

    notify = on_status or (lambda msg: None)
    ig_user_id, err = _get_ig_user_id(page_id, access_token)
    if err:
        return err

    notify("Creando publicación de imagen en Instagram...")
    try:
        container_response = _request_with_retry(
            "POST",
            f"{GRAPH_URL}/{ig_user_id}/media",
            params={"access_token": access_token},
            json={"media_type": "IMAGE", "image_url": image_url, "caption": caption},
            timeout=60,
        )
    except requests.RequestException as e:
        return {"ok": False, "error": f"No se pudo conectar con Instagram: {e}"}
    container_payload, err = _parse_response(container_response)
    if err:
        return err
    container_id = container_payload["id"]

    notify("Publicando en Instagram...")
    try:
        publish_response = _request_with_retry(
            "POST",
            f"{GRAPH_URL}/{ig_user_id}/media_publish",
            params={"access_token": access_token},
            json={"creation_id": container_id},
            timeout=30,
        )
    except requests.RequestException as e:
        return {"ok": False, "error": f"No se pudo publicar en Instagram: {e}"}
    publish_payload, err = _parse_response(publish_response)
    if err:
        return err
    return {"ok": True, "media_id": publish_payload["id"]}
