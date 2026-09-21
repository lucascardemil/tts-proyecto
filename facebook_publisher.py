"""
Publicación de videos en una Página de Facebook vía Graph API.

Requiere un Page Access Token (de una app de Facebook Developers, con
permisos pages_manage_posts + pages_read_engagement) guardado en las
variables de entorno FB_PAGE_<N>_ID y FB_PAGE_<N>_TOKEN (ver .env, meta_auth.py).
No hace falta ningún flujo de login/OAuth: el token ya autoriza a la app
a publicar en esa Página.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import requests

import meta_auth

GRAPH_API_VERSION = "v19.0"
# Facebook usa un host aparte para subir video (no el graph.facebook.com normal).
VIDEO_UPLOAD_URL = f"https://graph-video.facebook.com/{GRAPH_API_VERSION}/{{page_id}}/videos"
PHOTO_UPLOAD_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{{page_id}}/photos"

MIN_SCHEDULE_SECONDS = 10 * 60  # Facebook exige al menos 10 minutos en el futuro
MAX_SCHEDULE_SECONDS = 75 * 24 * 60 * 60  # y como máximo 75 días

POST_SPACING_SECONDS = 60 * 60  # separación mínima que mantenemos entre publicaciones
_STATE_PATH = Path(__file__).parent / "facebook_publish_state.json"

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # segundos: 2, 4, 8...


def _post_with_retry(url: str, max_retries: int = MAX_RETRIES, **kwargs):
    """POST con reintentos ante error de red o HTTP 5xx (transitorios)."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            response = requests.post(url, **kwargs)
        except requests.RequestException as e:
            last_exc = e
        else:
            if response.status_code < 500:
                return response
            last_exc = requests.RequestException(f"HTTP {response.status_code}")
        if attempt < max_retries - 1:
            time.sleep(RETRY_BACKOFF_BASE ** attempt)
    raise last_exc


def _load_next_slot() -> Optional[float]:
    """Momento (timestamp) en que puede salir la próxima publicación, o None si nunca se publicó nada."""
    try:
        return float(json.loads(_STATE_PATH.read_text(encoding="utf-8"))["next_slot"])
    except Exception:
        return None


def _save_next_slot(ts: float) -> None:
    try:
        _STATE_PATH.write_text(json.dumps({"next_slot": ts}), encoding="utf-8")
    except Exception:
        pass  # es solo para espaciar publicaciones, no debe interrumpir la publicación en sí


def _decide_publish_time() -> tuple[Optional[datetime], float]:
    """
    Decide automáticamente cuándo debe salir esta publicación, sin que el
    usuario tenga que elegir horario: ahora mismo si pasó más de una hora
    desde la última (o nunca se publicó), o al completar esa hora si no.

    Devuelve (scheduled_time, effective_ts):
      - scheduled_time: None si hay que publicar ya, o el datetime programado.
      - effective_ts: el momento real (ahora o programado) a partir del cual
        se cuenta la próxima hora de espera.
    """
    now = time.time()
    next_slot = _load_next_slot()

    if next_slot is None or next_slot <= now or next_slot - now > MAX_SCHEDULE_SECONDS:
        return None, now

    if next_slot - now < MIN_SCHEDULE_SECONDS:
        # Falta tan poco que programarlo violaría el mínimo de 10 minutos de
        # Facebook para publicaciones programadas — se publica ya en su lugar.
        return None, now

    return datetime.fromtimestamp(next_slot), next_slot


def get_video_stats(video_ids: list, id_to_page: Optional[dict] = None) -> dict:
    """
    Consulta vistas/likes/comentarios de videos ya publicados (Graph API).

    Args:
        video_ids: lista de IDs de video de Facebook.
        id_to_page: mapeo {video_id: page_id} para usar el token de la
            página que publicó cada video (varias páginas, cada una con su
            propio token). Los videos sin entrada (registros previos a esta
            función) usan la primera página configurada.

    Returns:
        dict {video_id: {"views": int, "likes": int, "comments": int}}. Si no
        hay token configurado o falla la consulta, devuelve {} (sin cortar el flujo).
    """
    if not video_ids:
        return {}
    id_to_page = id_to_page or {}

    stats = {}
    for video_id in video_ids:
        _, access_token, err = meta_auth.get_credentials(id_to_page.get(video_id))
        if err:
            continue
        try:
            response = requests.get(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{video_id}",
                params={
                    "fields": "views,likes.summary(true),comments.summary(true)",
                    "access_token": access_token,
                },
                timeout=30,
            )
            payload = response.json()
            if not response.ok or "error" in payload:
                continue
            stats[video_id] = {
                "views": int(payload.get("views", 0) or 0),
                "likes": int(payload.get("likes", {}).get("summary", {}).get("total_count", 0)),
                "comments": int(payload.get("comments", {}).get("summary", {}).get("total_count", 0)),
            }
        except Exception:
            continue
    return stats


def _parse_response(response) -> tuple[Optional[dict], Optional[dict]]:
    """
    Interpreta una respuesta de la Graph API. Devuelve (payload, None) si
    vino bien, o (None, {"ok": False, "error": ...}) si algo falló — para
    poder hacer `payload, err = _parse_response(...); if err: return err`
    en cada fase de la subida sin repetir el parseo tres veces.
    """
    try:
        payload = response.json()
    except ValueError:
        return None, {"ok": False, "error": f"Respuesta inesperada de Facebook (HTTP {response.status_code})"}

    if not response.ok or "error" in payload:
        # El mensaje de error de Facebook suele explicar exactamente qué
        # falló (token vencido, permisos faltantes, etc) — se lo pasamos
        # tal cual al usuario en vez de un genérico "falló la subida".
        return None, meta_auth.classify_error(payload, "Facebook")

    return payload, None


def _resolve_schedule(requested_ts: Optional[float]) -> tuple[Optional[datetime], float]:
    """
    Si vino un horario pedido explícitamente (elegido a mano o sugerido por
    el "mejor horario"), lo usa tal cual — salvo que esté fuera de la
    ventana que admite Facebook (menos de 10 min o más de 75 días), en cuyo
    caso se publica ya. Si no vino ninguno, cae al auto-decide interno
    (_decide_publish_time) que solo mantiene 1 hora de separación.
    """
    if requested_ts is None:
        return _decide_publish_time()

    now = time.time()
    delta = requested_ts - now
    if delta < MIN_SCHEDULE_SECONDS or delta > MAX_SCHEDULE_SECONDS:
        return None, now
    return datetime.fromtimestamp(requested_ts), requested_ts


def publish_video(
    video_path: str,
    title: str,
    description: str,
    page_id: Optional[str] = None,
    scheduled_time: Optional[float] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Sube un video a la Página de Facebook indicada (o la primera configurada
    si no se pasa page_id). Si se pasa scheduled_time (timestamp) lo usa como
    horario real de Facebook (scheduled_publish_time de la Graph API — la
    publicación queda programada del lado de Meta, visible en Meta Business
    Suite, y sobrevive a que este server se reinicie). Si no se pasa nada,
    decide sola cuándo debe salir para mantener al menos 1 hora de
    separación con la anterior (ver POST_SPACING_SECONDS/_decide_publish_time).

    Usa el protocolo de subida reanudable (Resumable Upload) de la Graph
    API en vez de mandar el archivo entero en un solo POST: para videos de
    decenas/cientos de MB, el método simple ("source" en un solo request)
    suele ser rechazado por Facebook con HTTP 413 antes de procesar nada.
    La subida reanudable manda el archivo en fragmentos cuyo tamaño decide
    el propio Facebook (start_offset/end_offset de cada respuesta).

    Args:
        video_path: ruta local al archivo .mp4 a publicar.
        title: título del video.
        description: descripción/caption del post.
        scheduled_time: timestamp (epoch) al que debe salir, o None para
            dejar que la función decida sola.

    Returns:
        {"ok": True, "video_id": str, "scheduled_time": str|None} si se
        subió correctamente (scheduled_time en ISO si se programó, None si
        se publicó de inmediato), o {"ok": False, "error": str} si algo falló.
    """
    page_id, access_token, err = meta_auth.get_credentials(page_id)
    if err:
        return err

    path = Path(video_path)
    if not path.exists():
        return {"ok": False, "error": f"No se encontró el video: {video_path}"}

    # Chequeo del token antes de empezar a transferir: si está invalidado,
    # Facebook rechazaría la subida igual, pero recién después de abrir la
    # sesión — y el mensaje que devuelve no dice qué hacer al respecto.
    token_check = meta_auth.validate(page_id)
    if not token_check["ok"]:
        return {"ok": False, "error": token_check["error"], "auth_error": token_check.get("auth_error", False)}

    scheduled_time, effective_ts = _resolve_schedule(scheduled_time)
    finish_extra = {}
    if scheduled_time is not None:
        finish_extra["published"] = "false"
        finish_extra["scheduled_publish_time"] = str(int(scheduled_time.timestamp()))

    url = VIDEO_UPLOAD_URL.format(page_id=page_id)
    file_size = path.stat().st_size
    notify = on_status or (lambda msg: None)

    # Fase 1: "start" — Facebook abre la sesión y decide el tamaño del
    # primer fragmento (end_offset - start_offset).
    notify("Iniciando subida a Facebook...")
    try:
        start_response = _post_with_retry(
            url,
            data={"access_token": access_token, "upload_phase": "start", "file_size": file_size},
            timeout=60,
        )
    except requests.RequestException as e:
        return {"ok": False, "error": f"No se pudo conectar con Facebook: {e}"}

    start_payload, err = _parse_response(start_response)
    if err:
        return err

    upload_session_id = start_payload["upload_session_id"]
    video_id = start_payload["video_id"]
    start_offset = int(start_payload["start_offset"])
    end_offset = int(start_payload["end_offset"])

    # Fase 2: "transfer" — se manda cada fragmento indicado por Facebook,
    # hasta que start_offset == end_offset (no queda nada más por subir).
    try:
        with open(path, "rb") as f:
            while start_offset < end_offset:
                f.seek(start_offset)
                chunk = f.read(end_offset - start_offset)
                pct = int(start_offset / file_size * 100) if file_size else 0
                notify(f"Subiendo video a Facebook... {pct}%")
                transfer_response = _post_with_retry(
                    url,
                    data={
                        "access_token": access_token,
                        "upload_phase": "transfer",
                        "start_offset": start_offset,
                        "upload_session_id": upload_session_id,
                    },
                    files={"video_file_chunk": chunk},
                    timeout=120,
                )
                transfer_payload, err = _parse_response(transfer_response)
                if err:
                    return err
                start_offset = int(transfer_payload["start_offset"])
                end_offset = int(transfer_payload["end_offset"])
    except requests.RequestException as e:
        return {"ok": False, "error": f"Error subiendo el video a Facebook: {e}"}

    # Fase 3: "finish" — cierra la sesión y publica (o programa) el video.
    notify("Finalizando publicación en Facebook...")
    try:
        finish_response = _post_with_retry(
            url,
            data={
                "access_token": access_token,
                "upload_phase": "finish",
                "upload_session_id": upload_session_id,
                "title": title,
                "description": description,
                **finish_extra,
            },
            timeout=60,
        )
    except requests.RequestException as e:
        return {"ok": False, "error": f"No se pudo finalizar la publicación en Facebook: {e}"}

    _, err = _parse_response(finish_response)
    if err:
        return err

    _save_next_slot(effective_ts + POST_SPACING_SECONDS)
    return {
        "ok": True,
        "video_id": video_id,
        "scheduled_time": scheduled_time.isoformat() if scheduled_time else None,
    }


def publish_photo(
    image_path: str,
    caption: str,
    page_id: Optional[str] = None,
    scheduled_time: Optional[float] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Sube una imagen a la Página indicada -- POST directo con multipart
    "source" (a diferencia del video no hace falta el protocolo reanudable,
    una imagen entera en un solo request no choca con el límite que sí
    afecta a los videos). Reusa meta_auth/_parse_response/_resolve_schedule
    tal cual publish_video.

    Returns:
        {"ok": True, "post_id": str} si se publicó, o
        {"ok": False, "error": str} si algo falló.
    """
    page_id, access_token, err = meta_auth.get_credentials(page_id)
    if err:
        return err

    path = Path(image_path)
    if not path.exists():
        return {"ok": False, "error": f"No se encontró la imagen: {image_path}"}

    token_check = meta_auth.validate(page_id)
    if not token_check["ok"]:
        return {"ok": False, "error": token_check["error"], "auth_error": token_check.get("auth_error", False)}

    scheduled_time, effective_ts = _resolve_schedule(scheduled_time)
    data = {"access_token": access_token, "caption": caption}
    if scheduled_time is not None:
        data["published"] = "false"
        data["scheduled_publish_time"] = str(int(scheduled_time.timestamp()))

    notify = on_status or (lambda msg: None)
    notify("Subiendo imagen a Facebook...")
    try:
        with open(path, "rb") as f:
            response = _post_with_retry(
                PHOTO_UPLOAD_URL.format(page_id=page_id),
                data=data,
                files={"source": f},
                timeout=120,
            )
    except requests.RequestException as e:
        return {"ok": False, "error": f"No se pudo publicar la imagen en Facebook: {e}"}

    payload, err = _parse_response(response)
    if err:
        return err

    _save_next_slot(effective_ts + POST_SPACING_SECONDS)
    return {"ok": True, "post_id": payload.get("post_id") or payload.get("id")}
