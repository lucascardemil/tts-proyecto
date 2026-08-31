"""
Publicación de videos en una Página de Facebook vía Graph API.

Requiere un Page Access Token (de una app de Facebook Developers, con
permisos pages_manage_posts + pages_read_engagement) guardado en las
variables de entorno FB_PAGE_ID y FB_PAGE_ACCESS_TOKEN (ver .env.example).
No hace falta ningún flujo de login/OAuth: el token ya autoriza a la app
a publicar en esa Página.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

GRAPH_API_VERSION = "v19.0"
# Facebook usa un host aparte para subir video (no el graph.facebook.com normal).
VIDEO_UPLOAD_URL = f"https://graph-video.facebook.com/{GRAPH_API_VERSION}/{{page_id}}/videos"

MIN_SCHEDULE_SECONDS = 10 * 60  # Facebook exige al menos 10 minutos en el futuro
MAX_SCHEDULE_SECONDS = 75 * 24 * 60 * 60  # y como máximo 75 días

POST_SPACING_SECONDS = 60 * 60  # separación mínima que mantenemos entre publicaciones
_STATE_PATH = Path(__file__).parent / "facebook_publish_state.json"


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
        fb_error = payload.get("error", {})
        message = fb_error.get("message", "Error desconocido de Facebook")
        return None, {"ok": False, "error": message}

    return payload, None


def publish_video(video_path: str, title: str, description: str) -> dict:
    """
    Sube un video a la Página de Facebook configurada. Decide sola cuándo
    debe salir la publicación para mantener al menos 1 hora de separación
    con la anterior (ver POST_SPACING_SECONDS/_decide_publish_time): la
    publica de inmediato si ya pasó esa hora, o la programa automáticamente
    para completarla — el usuario nunca elige horario.

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

    Returns:
        {"ok": True, "video_id": str, "scheduled_time": str|None} si se
        subió correctamente (scheduled_time en ISO si se programó, None si
        se publicó de inmediato), o {"ok": False, "error": str} si algo falló.
    """
    page_id = os.environ.get("FB_PAGE_ID")
    access_token = os.environ.get("FB_PAGE_ACCESS_TOKEN")
    if not page_id or not access_token:
        return {
            "ok": False,
            "error": "Falta configurar FB_PAGE_ID y FB_PAGE_ACCESS_TOKEN en .env",
        }

    path = Path(video_path)
    if not path.exists():
        return {"ok": False, "error": f"No se encontró el video: {video_path}"}

    scheduled_time, effective_ts = _decide_publish_time()
    finish_extra = {}
    if scheduled_time is not None:
        finish_extra["published"] = "false"
        finish_extra["scheduled_publish_time"] = str(int(scheduled_time.timestamp()))

    url = VIDEO_UPLOAD_URL.format(page_id=page_id)
    file_size = path.stat().st_size

    # Fase 1: "start" — Facebook abre la sesión y decide el tamaño del
    # primer fragmento (end_offset - start_offset).
    try:
        start_response = requests.post(
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
                transfer_response = requests.post(
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
    try:
        finish_response = requests.post(
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
