"""
Publicación de videos en YouTube vía YouTube Data API v3.

A diferencia de Facebook (token fijo de página), YouTube exige un login
OAuth con la cuenta de Google del usuario. Requiere:
  - youtube_client_secret.json: Client ID OAuth tipo "Desktop app", creado
    en Google Cloud Console (ver instrucciones en el README/plan del proyecto).
    Un mismo Client ID sirve para todos los canales/cuentas de Google.
  - youtube_token[_<key>].json: se genera solo la primera vez que el usuario
    hace login (connect()); guarda el refresh token para las subidas futuras.

Múltiples canales: cada canal es una cuenta de Google distinta, así que
cada uno necesita su propio archivo de token. Se configuran en .env como
YT_CHANNEL_<N>_NAME (nombre para mostrar) + YT_CHANNEL_<N>_TOKEN (nombre
del archivo de token, N=1,2,...). Si no hay ningún YT_CHANNEL_<N>_NAME en
.env, se usa el modo de compatibilidad de un solo canal ("default",
youtube_token.json) para no romper instalaciones previas.

Ninguno de los archivos de token/client_secret debe compartirse ni subirse
a un repositorio — igual que el .env de Facebook.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# "youtube" (lectura + escritura) en vez de solo youtube.upload: sin el, videos.list
# y channels.list devuelven 403, y no se pueden ver ni cambiar los videos
# programados del canal (list_scheduled / reschedule_video). Quien conecto la
# cuenta con el scope viejo tiene que reconectar.
SCOPES = ["https://www.googleapis.com/auth/youtube"]
CLIENT_SECRET_PATH = Path(__file__).parent / "youtube_client_secret.json"
DEFAULT_TOKEN_PATH = Path(__file__).parent / "youtube_token.json"


def list_channels() -> list[dict]:
    """
    Canales configurados en .env (YT_CHANNEL_<N>_NAME / _TOKEN, N=1,2,...
    sin huecos). Si no hay ninguno configurado, cae al modo de un solo
    canal ("default", youtube_token.json) para no romper instalaciones
    previas a esta función. No incluye credenciales: es lo que se manda
    al frontend para el selector de canal.
    """
    channels = []
    n = 1
    while True:
        name = os.environ.get(f"YT_CHANNEL_{n}_NAME")
        if not name:
            break
        token_file = os.environ.get(f"YT_CHANNEL_{n}_TOKEN") or f"youtube_token_{n}.json"
        channels.append({"key": str(n), "name": name, "connected": _is_connected_path(_token_path(token_file))})
        n += 1
    if channels:
        return channels
    return [{"key": "default", "name": "Canal de YouTube", "connected": _is_connected_path(DEFAULT_TOKEN_PATH)}]


def _token_path(token_file: str) -> Path:
    return Path(__file__).parent / token_file


def _channel_token_path(channel_key: Optional[str]) -> Path:
    """Ruta del archivo de token para el canal pedido (o el único/primero
    configurado si no se especifica ninguno)."""
    if channel_key is None or channel_key == "default":
        n = 1
        name = os.environ.get("YT_CHANNEL_1_NAME")
        if not name:
            return DEFAULT_TOKEN_PATH
        return _token_path(os.environ.get("YT_CHANNEL_1_TOKEN") or "youtube_token_1.json")
    token_file = os.environ.get(f"YT_CHANNEL_{channel_key}_TOKEN") or f"youtube_token_{channel_key}.json"
    return _token_path(token_file)


def _is_connected_path(token_path: Path) -> bool:
    """Hay token valido Y con todos los permisos de SCOPES: un token viejo
    (solo youtube.upload) todavia sube videos, pero no puede leer ni editar el
    canal, asi que se pide reconectar."""
    if _load_credentials_from(token_path) is None:
        return False
    try:
        granted = set(json.loads(token_path.read_text(encoding="utf-8")).get("scopes") or [])
    except (OSError, ValueError):
        return False
    return set(SCOPES) <= granted


def is_connected(channel_key: Optional[str] = None) -> bool:
    return _is_connected_path(_channel_token_path(channel_key))


def _load_credentials_from(token_path: Path) -> Optional[Credentials]:
    if not token_path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # El usuario revocó el acceso o el refresh token venció del todo
            # (p.ej. apps OAuth en modo "Testing" en Google Cloud Console,
            # que vencen el refresh token a los 7 días): hay que reconectar
            # de nuevo. Se borra el token viejo para que is_connected()
            # refleje esto y la UI vuelva a ofrecer "Conectar cuenta".
            token_path.unlink(missing_ok=True)
            return None
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _load_credentials(channel_key: Optional[str] = None) -> Optional[Credentials]:
    return _load_credentials_from(_channel_token_path(channel_key))


def connect(channel_key: Optional[str] = None) -> dict:
    """
    Corre el login OAuth: abre el navegador del usuario para que apruebe el
    acceso y guarda las credenciales resultantes en el archivo de token del
    canal pedido. Bloquea hasta que el usuario completa (o cancela) el
    login en el navegador. Para agregar un canal nuevo, loguearse con la
    cuenta de Google DE ESE canal cuando el navegador lo pida (no la de la
    sesión ya conectada).
    """
    if not CLIENT_SECRET_PATH.exists():
        return {
            "ok": False,
            "error": f"Falta el archivo {CLIENT_SECRET_PATH.name} en la carpeta del proyecto "
                     f"(Client ID OAuth de Google Cloud Console, tipo 'Desktop app').",
        }
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
        creds = flow.run_local_server(port=0)
    except Exception as e:
        return {"ok": False, "error": f"No se pudo completar el login de Google: {e}"}

    token_path = _channel_token_path(channel_key)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return {"ok": True}


def publish_video(
    video_path: str,
    title: str,
    description: str,
    privacy_status: str = "unlisted",
    tags: Optional[list] = None,
    is_ai_generated: bool = False,
    on_status: Optional[Callable[[str], None]] = None,
    publish_at: Optional[str] = None,
    channel_key: Optional[str] = None,
) -> dict:
    """
    Sube un video al canal de YouTube conectado.

    Args:
        video_path: ruta local al archivo .mp4 a publicar.
        title: título del video.
        description: descripción del video.
        privacy_status: "public", "unlisted" o "private". Ignorado si se
            pasa publish_at (YouTube exige "private" para programar).
        tags: lista de etiquetas del video.
        is_ai_generated: marca el video como contenido sintético/generado con IA.
        publish_at: fecha/hora RFC3339 en UTC (ej. "2026-09-24T15:00:00Z") en
            la que YouTube debe publicar el video automáticamente. Si se
            pasa, el video sube como "private" y YouTube lo hace público solo
            en ese momento -- no hace falta que este proceso siga vivo.
        channel_key: clave del canal a usar (ver list_channels()); None =
            el único/primero configurado (compatibilidad con instalaciones
            de un solo canal).

    Returns:
        {"ok": True, "video_id": str} si se subió correctamente, o
        {"ok": False, "error": str} con el motivo si algo falló.
    """
    creds = _load_credentials(channel_key)
    if creds is None:
        return {"ok": False, "error": "Se perdió la conexión con YouTube (el token venció o fue revocado). Conectá tu cuenta de nuevo.", "auth_error": True}

    path = Path(video_path)
    if not path.exists():
        return {"ok": False, "error": f"No se encontró el video: {video_path}"}

    notify = on_status or (lambda msg: None)
    try:
        youtube = build("youtube", "v3", credentials=creds)
        status = {"privacyStatus": privacy_status, "containsSyntheticMedia": is_ai_generated}
        if publish_at:
            status["privacyStatus"] = "private"
            status["publishAt"] = publish_at
        body = {
            "snippet": {"title": title or path.stem, "description": description, "tags": tags or []},
            "status": status,
        }
        # Subida resumable en fragmentos (chunksize=-1 = un solo fragmento por
        # llamada a next_chunk, pero reintentable si se corta la conexión) —
        # evita mandar el archivo entero de una para videos grandes.
        media = MediaFileUpload(str(path), chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        notify("Subiendo video a YouTube...")
        response = None
        while response is None:
            # num_retries: reintenta con backoff exponencial ante error de
            # red o HTTP 5xx transitorio, sin reempezar el fragmento desde cero.
            status, response = request.next_chunk(num_retries=3)
            if status:
                notify(f"Subiendo video a YouTube... {int(status.progress() * 100)}%")

        return {"ok": True, "video_id": response["id"]}
    except HttpError as e:
        return {"ok": False, "error": f"Error de YouTube: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Error subiendo a YouTube: {e}"}


def set_thumbnail(video_id: str, thumbnail_path: str, channel_key: Optional[str] = None) -> dict:
    """Sube una miniatura personalizada (.jpg, hasta 2MB) para un video ya publicado."""
    creds = _load_credentials(channel_key)
    if creds is None:
        return {"ok": False, "error": "Se perdió la conexión con YouTube (el token venció o fue revocado). Conectá tu cuenta de nuevo.", "auth_error": True}

    path = Path(thumbnail_path)
    if not path.exists():
        return {"ok": False, "error": f"No se encontró la miniatura: {thumbnail_path}"}

    try:
        youtube = build("youtube", "v3", credentials=creds)
        media = MediaFileUpload(str(path), mimetype="image/jpeg")
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        return {"ok": True}
    except HttpError as e:
        return {"ok": False, "error": f"Error de YouTube: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Error subiendo la miniatura: {e}"}


def get_video_stats(video_ids: list) -> dict:
    """
    Consulta vistas/likes/comentarios de videos ya publicados (YouTube Data API v3).

    Args:
        video_ids: lista de IDs de video (hasta 50 por límite de la API).

    Returns:
        dict {video_id: {"views": int, "likes": int, "comments": int}}. Si no
        hay conexión o falla la consulta, devuelve {} (sin cortar el flujo).
    """
    if not video_ids:
        return {}

    try:
        creds = _load_credentials()
        if creds is None:
            return {}
        youtube = build("youtube", "v3", credentials=creds)
        stats = {}
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            response = youtube.videos().list(part="statistics", id=",".join(batch)).execute()
            for item in response.get("items", []):
                s = item.get("statistics", {})
                stats[item["id"]] = {
                    "views": int(s.get("viewCount", 0)),
                    "likes": int(s.get("likeCount", 0)),
                    "comments": int(s.get("commentCount", 0)),
                }
        return stats
    except Exception:
        return {}


def reschedule_video(video_id: str, publish_at: str, channel_key: Optional[str] = None) -> dict:
    """
    Cambia la fecha de publicacion (publishAt, RFC3339 UTC) de un video que
    ya esta programado en YouTube. videos.update exige el scope
    "youtube" (o youtube.force-ssl), mas amplio que el youtube.upload con
    el que se conecto la cuenta: si falta, devuelve un error claro.

    Returns:
        {"ok": True} o {"ok": False, "error": str}.
    """
    creds = _load_credentials(channel_key)
    if creds is None:
        return {"ok": False, "error": "Se perdio la conexion con YouTube. Conecta tu cuenta de nuevo.", "auth_error": True}
    try:
        youtube = build("youtube", "v3", credentials=creds)
        items = youtube.videos().list(part="status", id=video_id).execute().get("items", [])
        if not items:
            return {"ok": False, "error": f"No se encontro el video {video_id} en YouTube."}
        writable = ("privacyStatus", "embeddable", "license", "publicStatsViewable",
                    "selfDeclaredMadeForKids", "containsSyntheticMedia")
        status = {k: v for k, v in items[0]["status"].items() if k in writable}
        status["privacyStatus"] = "private"  # requisito de YouTube para programar
        status["publishAt"] = publish_at
        youtube.videos().update(part="status", body={"id": video_id, "status": status}).execute()
        return {"ok": True}
    except HttpError as e:
        if getattr(e, "status_code", None) == 403 or "insufficient" in str(e).lower():
            return {"ok": False, "error": "Falta el permiso de YouTube para editar videos: reconecta la cuenta con el scope ampliado. Detalle: " + str(e)}
        return {"ok": False, "error": f"Error de YouTube: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Error reprogramando en YouTube: {e}"}


def list_scheduled(channel_key: Optional[str] = None) -> Optional[list]:
    """
    Fechas (datetime local, naive) de los videos del canal que tienen una
    publicacion programada a futuro. Mira los ultimos 50 subidos.

    Returns:
        Lista (vacia si no hay programados), o None si no se pudo consultar
        (sin conexion, token con el scope viejo, error de red): el llamador
        decide con que otra fuente seguir.
    """
    creds = _load_credentials(channel_key)
    if creds is None:
        return None
    try:
        youtube = build("youtube", "v3", credentials=creds)
        channels = youtube.channels().list(part="contentDetails", mine=True).execute().get("items", [])
        if not channels:
            return None
        uploads = channels[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        items = youtube.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50).execute()
        ids = [i["contentDetails"]["videoId"] for i in items.get("items", [])]
        if not ids:
            return []
        videos = youtube.videos().list(part="status", id=",".join(ids)).execute().get("items", [])
        now = datetime.now(timezone.utc)
        scheduled = []
        for v in videos:
            publish_at = v.get("status", {}).get("publishAt")
            if not publish_at:
                continue
            dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
            if dt > now:
                scheduled.append(dt.astimezone().replace(tzinfo=None))
        return scheduled
    except Exception:
        return None
