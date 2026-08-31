"""
Publicación de videos en YouTube vía YouTube Data API v3.

A diferencia de Facebook (token fijo de página), YouTube exige un login
OAuth con la cuenta de Google del usuario. Requiere:
  - youtube_client_secret.json: Client ID OAuth tipo "Desktop app", creado
    en Google Cloud Console (ver instrucciones en el README/plan del proyecto).
  - youtube_token.json: se genera solo la primera vez que el usuario hace
    login (connect()); guarda el refresh token para las subidas futuras.

Ninguno de los dos archivos debe compartirse ni subirse a un repositorio —
igual que el .env de Facebook.
"""

from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET_PATH = Path(__file__).parent / "youtube_client_secret.json"
TOKEN_PATH = Path(__file__).parent / "youtube_token.json"


def is_connected() -> bool:
    return TOKEN_PATH.exists()


def _load_credentials() -> Optional[Credentials]:
    if not TOKEN_PATH.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def connect() -> dict:
    """
    Corre el login OAuth: abre el navegador del usuario para que apruebe el
    acceso y guarda las credenciales resultantes en TOKEN_PATH. Bloquea
    hasta que el usuario completa (o cancela) el login en el navegador.
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

    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return {"ok": True}


def publish_video(
    video_path: str,
    title: str,
    description: str,
    privacy_status: str = "public",
    tags: Optional[list] = None,
    is_ai_generated: bool = False,
) -> dict:
    """
    Sube un video al canal de YouTube conectado.

    Args:
        video_path: ruta local al archivo .mp4 a publicar.
        title: título del video.
        description: descripción del video.
        privacy_status: "public", "unlisted" o "private".
        tags: lista de etiquetas del video.
        is_ai_generated: marca el video como contenido sintético/generado con IA.

    Returns:
        {"ok": True, "video_id": str} si se subió correctamente, o
        {"ok": False, "error": str} con el motivo si algo falló.
    """
    creds = _load_credentials()
    if creds is None:
        return {"ok": False, "error": "Todavía no conectaste tu cuenta de YouTube."}

    path = Path(video_path)
    if not path.exists():
        return {"ok": False, "error": f"No se encontró el video: {video_path}"}

    try:
        youtube = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {"title": title or path.stem, "description": description, "tags": tags or []},
            "status": {"privacyStatus": privacy_status, "containsSyntheticMedia": is_ai_generated},
        }
        # Subida resumable en fragmentos (chunksize=-1 = un solo fragmento por
        # llamada a next_chunk, pero reintentable si se corta la conexión) —
        # evita mandar el archivo entero de una para videos grandes.
        media = MediaFileUpload(str(path), chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _, response = request.next_chunk()

        return {"ok": True, "video_id": response["id"]}
    except HttpError as e:
        return {"ok": False, "error": f"Error de YouTube: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Error subiendo a YouTube: {e}"}
