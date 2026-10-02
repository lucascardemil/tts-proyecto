"""
Verificacion visual de highlights de clips gaming (Fortnite, CS2, Valorant, etc.)
con IA. Complementa `detect_beats` (picos de audio): un pico de RMS solo dice
"aca hubo un sonido fuerte", no si es un kill real, un ruido random, o si el
clip se corto antes de otra pelea que el audio no capto.

Tres proveedores, con fallback automatico en cadena: Gemini primero
(gemini-3.5-flash-lite, mejor calidad/sin limite de frames por request), si
se queda sin cuota (QuotaExceededError) cae a Groq (qwen/qwen3.8-27b, gratis,
30 req/min, pero max 3 imagenes por request -- se resamplea la ventana antes
de mandarla), y si Groq TAMBIEN esta sin cuota cae a OpenRouter (prueba una
lista de modelos gratis con vision en orden, porque los modelos individuales
:free de OpenRouter saturan/rotan disponibilidad seguido -- confirmado en
vivo: google/gemma-4-31b-it:free devolvio 429 "temporarily rate-limited
upstream" dos veces seguidas en pruebas separadas, y el router automatico
"openrouter/free" puede devolver un modelo no apto para la tarea, ej.
nvidia/nemotron-3.5-content-safety:free, que solo clasifica seguridad y
nunca describe la imagen -- por eso NO se usa el router, se prueba una lista
fija en orden). Esto paso en produccion la noche del 25-26/09: Gemini free
tier (15 req/min) se agoto en un lote con varios clips seguidos y bloqueo un
video con QuotaExceededError.

Flujo:
1. `detect_beats` (gaming_clip.py) da candidatos por audio -> rapido y gratis.
2. Por cada candidato, se extraen frames (ventana +-1.2s a 3 fps) con ffmpeg.
3. Se le manda al modelo de vision el set de frames (con timestamps) pidiendo
   JSON: es_kill (bool), tipo, confianza, texto_kill_feed, label_sugerido.
4. Un pase adicional escanea frames espaciados por TODO el clip (1 cada ~1.5s)
   para encontrar eventos que el audio no genero un pico suficiente (ej. un
   segundo intercambio que quedo cortado al final del clip de Medal).
5. Devuelve una lista de eventos verificados, ordenada, lista para armar
   `kills` + `labels` en los props de ClipEdit (o descartar el clip si no hay
   ningun evento real).

Requiere GEMINI_API_KEY y/o GROQ_API_KEY y/o OPENROUTER_API_KEY en el entorno
(.env). Sin ninguna de las tres, `verify_clip` lanza VisionError de entrada
-- no hay fallback silencioso a "todo es un kill".
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-3.5-flash-lite"  # rapido y barato, alcanza para clasificar frames
GROQ_MODEL = "qwen/qwen3.8-27b"         # fallback gratis cuando Gemini se queda sin cuota
GROQ_MAX_IMAGES = 3                     # limite duro de la API de Groq para este modelo
# Lista de modelos gratis de OpenRouter con vision, en orden de preferencia --
# NO el router "openrouter/free" (puede devolver un modelo inadecuado, ver
# arriba). Confirmado en vivo que dots-studio responde bien y lee texto en
# pantalla con precision; los gemma quedan de respaldo si dots-studio tambien
# esta saturado (la disponibilidad de modelos :free rota).
OPENROUTER_MODELS = [
    "dots-studio/dots-3-note-preview:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
]
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
WINDOW_BEFORE = 1.2   # segundos antes del beat de audio a inspeccionar
WINDOW_AFTER = 1.5    # segundos despues
FPS_SAMPLE = 3        # frames por segundo dentro de la ventana de un candidato
SCAN_STEP = 1.5       # separacion entre frames del pase de barrido completo


class VisionError(RuntimeError):
    pass


class QuotaExceededError(VisionError):
    """Cuota de la API de vision agotada (429 RESOURCE_EXHAUSTED persistente).
    Distinto de un simple error de red: no tiene sentido reintentar en
    segundos, hay que esperar a que resetee la cuota (diaria en el free tier)
    o subir de plan. Dispara un fallback automatico Gemini -> Groq ->
    OpenRouter (ver _ask_vision_model) -- solo se propaga hacia arriba si
    LOS TRES proveedores estan sin cuota o sin configurar."""
    pass


NOT_CONFIGURED_MSG = (
    "Falta una key de visión en el .env (GEMINI_API_KEY, GROQ_API_KEY u OPENROUTER_API_KEY): "
    "gaming_clip verifica cada highlight mirando el kill-feed y no tiene fallback silencioso "
    "a solo-audio. Gemini da una key gratis en aistudio.google.com/apikey."
)


def is_configured() -> bool:
    """True si hay al menos un proveedor de vision disponible (GEMINI_API_KEY,
    GROQ_API_KEY, u OPENROUTER_API_KEY). Usar antes de crear un proyecto
    gaming_clip para fallar rápido con un mensaje claro, en vez de que el
    primer video del lote recién descubra que falta la key."""
    return bool(
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GROQ_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
    )


@dataclass
class VisualEvent:
    time: float               # segundo del clip original
    kind: str                 # "kill" | "down" | "victory" | "big_moment" | "none"
    confidence: float         # 0..1, autoreportado por el modelo
    label: str                # texto para mostrar en pantalla (p.ej. "ELIMINATED")
    detail: str = ""          # texto/kill-feed leido, para debug/log
    source: str = "beat"      # "beat" (vino de audio) | "scan" (barrido visual)


def _get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    from google import genai  # import diferido: falla rapido y claro si falta el SDK
    return genai.Client(api_key=api_key)


def _get_groq_client():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None
    from groq import Groq  # import diferido
    return Groq(api_key=api_key)


def _gemini_generate(client, text_prompt: str, frames: list[Path], max_retries: int = 3, base_delay: float = 6.0):
    """Llama a Gemini con reintentos ante 503 (modelo saturado) -- observado en
    producción, no es hipotético. Cuota agotada (429 RESOURCE_EXHAUSTED que
    persiste) se propaga como QuotaExceededError de inmediato (sin reintentar
    -- la cuota gratis resetea por día, insistir cada pocos segundos solo
    quema el rate limit más rápido). Otros errores (mal prompt, key invalida,
    etc.) se propagan de inmediato como VisionError."""
    import time as _time
    from google.genai import errors as genai_errors

    parts = [{"text": text_prompt}]
    for f in frames:
        data = base64.b64encode(f.read_bytes()).decode("ascii")
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": data}})

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(model=GEMINI_MODEL, contents=[{"role": "user", "parts": parts}])
            return resp.text
        except (genai_errors.ServerError, genai_errors.ClientError) as e:
            msg = str(e)
            status = getattr(e, "status_code", None) or getattr(e, "code", None)
            if status == 429 or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                raise QuotaExceededError(f"Cuota de Gemini agotada: {msg}") from e
            if status != 503 and "UNAVAILABLE" not in msg:
                raise VisionError(f"Gemini error no recuperable: {e}") from e
            last_exc = e
            _time.sleep(base_delay * (attempt + 1))
    raise VisionError(f"Gemini siguió saturado tras {max_retries} intentos: {last_exc}")


def _groq_generate(client, text_prompt: str, frames: list[Path], max_retries: int = 3, base_delay: float = 5.0):
    """Fallback gratis cuando Gemini se queda sin cuota. qwen/qwen3.8-27b
    acepta MAX 3 imagenes por request (limite duro de la API, confirmado en
    vivo) -- si vienen mas frames, se resamplean parejo (primero, mitad,
    ultimo) en vez de cortar solo el final, para no perder el momento pico
    si cae fuera de las primeras 3."""
    import time as _time
    import groq as groq_errors

    if len(frames) > GROQ_MAX_IMAGES:
        idxs = np.linspace(0, len(frames) - 1, GROQ_MAX_IMAGES).round().astype(int)
        frames = [frames[i] for i in sorted(set(idxs))]

    content = [{"type": "text", "text": text_prompt}]
    for f in frames:
        data = base64.b64encode(f.read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}})

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL, messages=[{"role": "user", "content": content}], max_tokens=300,
            )
            return resp.choices[0].message.content
        except groq_errors.RateLimitError as e:
            msg = str(e)
            if "tokens per day" in msg.lower() or "requests per day" in msg.lower():
                raise QuotaExceededError(f"Cuota de Groq agotada: {msg}") from e
            last_exc = e
            _time.sleep(base_delay * (attempt + 1))
        except groq_errors.APIError as e:
            raise VisionError(f"Groq error no recuperable: {e}") from e
    raise VisionError(f"Groq siguió limitado tras {max_retries} intentos: {last_exc}")


def _openrouter_generate(api_key: str, text_prompt: str, frames: list[Path], rounds: int = 2, retry_delay: float = 4.0) -> str:
    """Segundo fallback (Gemini y Groq ambos sin cuota). Prueba
    OPENROUTER_MODELS en orden, repitiendo la ronda completa hasta `rounds`
    veces -- la disponibilidad de modelos :free rota y es intermitente
    dentro de un mismo minuto (confirmado en vivo: dots-studio dio 200 en
    una llamada, 429 en la siguiente, 200 de nuevo pocos segundos despues),
    asi que un solo intento por modelo no es suficiente para considerar
    "todos saturados". max_tokens alto (1200): dots-studio razona
    internamente antes de responder y con un limite bajo el contenido final
    sale None (el modelo gasta todo el presupuesto en el razonamiento,
    visto en vivo con max_tokens=200)."""
    import time as _time
    import requests

    last_exc: Optional[Exception] = None
    for round_num in range(rounds):
        for model in OPENROUTER_MODELS:
            content = [{"type": "text", "text": text_prompt}]
            for f in frames:
                data = base64.b64encode(f.read_bytes()).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}})
            try:
                resp = requests.post(
                    OPENROUTER_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": 1200},
                    timeout=30,
                )
            except requests.RequestException as e:
                last_exc = e
                continue
            if resp.status_code == 429:
                last_exc = VisionError(f"{model}: rate-limited (429)")
                continue
            if resp.status_code != 200:
                last_exc = VisionError(f"{model}: HTTP {resp.status_code} - {resp.text[:200]}")
                continue
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content")
            if text:
                return text
            last_exc = VisionError(f"{model}: respuesta vacía (finish_reason={data.get('choices',[{}])[0].get('finish_reason')})")
        if round_num < rounds - 1:
            _time.sleep(retry_delay)

    raise VisionError(f"Todos los modelos de OpenRouter fallaron/saturados tras {rounds} rondas: {last_exc}")


def _ask_vision_model(text_prompt: str, frames: list[Path]) -> str:
    """Punto de entrada unico para pedirle a un modelo de vision que analice
    `frames` con `text_prompt`. Cadena de fallback: Gemini -> Groq ->
    OpenRouter. Cada salto solo ocurre ante QuotaExceededError del anterior
    (sin cuota o sin configurar) -- otros VisionError (mal prompt, JSON
    invalido, saturado tras reintentos) se propagan de inmediato sin probar
    el siguiente proveedor, porque el problema es del pedido, no de la
    disponibilidad. Si LOS TRES fallan/no estan configurados, recien ahi se
    propaga el error hacia arriba."""
    gemini_client = _get_gemini_client()
    if gemini_client is not None:
        try:
            return _gemini_generate(gemini_client, text_prompt, frames)
        except QuotaExceededError as e:
            logger.warning("Gemini sin cuota, probando fallback a Groq: %s", e)

    groq_client = _get_groq_client()
    if groq_client is not None:
        try:
            return _groq_generate(groq_client, text_prompt, frames)
        except QuotaExceededError as e:
            logger.warning("Groq sin cuota, probando fallback a OpenRouter: %s", e)

    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key:
        return _openrouter_generate(openrouter_key, text_prompt, frames)

    configured = [n for n, v in (
        ("Gemini", gemini_client), ("Groq", groq_client), ("OpenRouter", openrouter_key),
    ) if v is not None]
    if not configured:
        raise VisionError(
            "Ninguna de GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY está configurada "
            "-- no se puede verificar visualmente el clip. No hay fallback silencioso."
        )
    raise QuotaExceededError(f"Los proveedores configurados ({', '.join(configured)}) están sin cuota.")


def _extract_frames(video_path: Path, start: float, end: float, fps: float, out_dir: Path) -> list[Path]:
    """Extrae frames de [start, end] (clampeado a >=0) a `fps` cuadros/seg."""
    start = max(start, 0.0)
    if end <= start:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "f_%04d.jpg"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(video_path),
        "-vf", f"fps={fps}", "-q:v", "2", str(pattern),
    ]
    subprocess.run(cmd, capture_output=True, check=False)
    return sorted(out_dir.glob("f_*.jpg"))


_CANDIDATE_PROMPT = """Sos un analista de clips de videojuegos (foco: Fortnite, CS2, Valorant).
Te doy una serie de capturas de pantalla consecutivas de un clip, tomadas en
una ventana de tiempo alrededor de un pico de audio (posible disparo, kill,
explosion). Los timestamps (segundos desde el inicio del CLIP ORIGINAL) estan
en el mismo orden que las imagenes: {timestamps}

Mira el kill-feed / HUD / indicadores de eliminacion, no solo el centro de la
pantalla. Decidi si en esta ventana ocurre un evento real y notable.

Devolveme SOLO un JSON (sin markdown, sin texto extra) con esta forma exacta:
{{
  "es_evento_real": true/false,
  "tipo": "kill" | "down" | "victory" | "otro" | "ninguno",
  "confianza": 0.0-1.0,
  "timestamp_exacto": <numero, el segundo del clip original donde ocurre el pico del evento, de los timestamps dados>,
  "texto_leido": "<texto exacto del kill-feed/HUD si es legible, o vacio>",
  "label_sugerido": "<ELIMINATED / DOWNED / VICTORY / texto corto en mayusculas para mostrar en pantalla, o vacio si no es evento>"
}}

Si no ves ningun kill-feed, eliminacion, ni indicador de combate real (solo
movimiento, construccion, menus, transiciones), es_evento_real debe ser false
y confianza baja. No inventes texto que no puedas leer con claridad."""

_SCAN_PROMPT = """Sos un analista de clips de videojuegos (foco: Fortnite, CS2, Valorant).
Estas viendo un frame SUELTO de un clip, en el segundo {t:.2f} del clip
original. Mira el kill-feed / HUD arriba y a los costados.

Devolveme SOLO un JSON (sin markdown) con esta forma exacta:
{{
  "hay_evento": true/false,
  "tipo": "kill" | "down" | "victory" | "otro" | "ninguno",
  "confianza": 0.0-1.0,
  "texto_leido": "<texto del kill-feed si es legible, o vacio>"
}}

Marca hay_evento=true SOLO si hay un indicador claro de eliminacion, victoria,
o combate activo intenso (no solo un arma en mano o un HUD normal de fondo)."""


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except (ValueError, json.JSONDecodeError) as e:
        raise VisionError(f"El modelo de visión no devolvió JSON válido: {e} | texto: {text[:300]}")


def verify_beat(video_path: Path, beat_time: float, work_dir: Path) -> Optional[VisualEvent]:
    """Confirma (o descarta) un candidato de detect_beats mirando los frames
    alrededor. Devuelve None si el modelo de vision concluye que no hay
    evento real."""
    start = beat_time - WINDOW_BEFORE
    end = beat_time + WINDOW_AFTER
    frame_dir = work_dir / f"beat_{beat_time:.2f}"
    frames = _extract_frames(video_path, start, end, FPS_SAMPLE, frame_dir)
    if not frames:
        return None
    timestamps = [round(max(start, 0.0) + i / FPS_SAMPLE, 2) for i in range(len(frames))]

    prompt = _CANDIDATE_PROMPT.format(timestamps=timestamps)
    text = _ask_vision_model(prompt, frames)
    data = _parse_json_response(text)

    if not data.get("es_evento_real"):
        logger.info("Beat %.2fs descartado (no es evento real): %s", beat_time, data)
        return None

    return VisualEvent(
        time=float(data.get("timestamp_exacto", beat_time)),
        kind=data.get("tipo", "otro"),
        confidence=float(data.get("confianza", 0.5)),
        label=data.get("label_sugerido") or "KILL",
        detail=data.get("texto_leido", ""),
        source="beat",
    )


def scan_for_missed_events(
    video_path: Path, seconds: float, exclude_near: list[float], work_dir: Path,
    step: float = SCAN_STEP, exclude_radius: float = 2.5,
) -> list[VisualEvent]:
    """Barrido de todo el clip (1 frame cada `step` segundos) buscando eventos
    que ningun pico de audio haya generado un candidato cerca (p.ej. una pelea
    que sigue justo hasta que el clip de Medal se corta)."""
    frame_dir = work_dir / "scan"
    frame_dir.mkdir(parents=True, exist_ok=True)
    # Puntos de muestreo: paso fijo desde 0, pero siempre incluyendo un frame
    # cerca del final -- un paso que no divide exacto a `seconds` puede dejar
    # sin muestrear el ultimo tramo del clip (donde a veces el clip de Medal
    # corta a mitad de una segunda pelea).
    sample_times = list(np.arange(0.0, seconds, step))
    tail = seconds - 0.3
    if not sample_times or (tail - sample_times[-1]) > step * 0.5:
        sample_times.append(round(tail, 2))

    found: list[VisualEvent] = []
    idx = 0
    for t in sample_times:
        if any(abs(t - b) <= exclude_radius for b in exclude_near):
            idx += 1
            continue
        out_path = frame_dir / f"scan_{idx:04d}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video_path),
             "-frames:v", "1", "-q:v", "2", str(out_path)],
            capture_output=True, check=False,
        )
        idx += 1
        if not out_path.exists():
            continue
        prompt = _SCAN_PROMPT.format(t=t)
        try:
            text = _ask_vision_model(prompt, [out_path])
            data = _parse_json_response(text)
        except QuotaExceededError:
            raise
        except VisionError:
            continue
        if data.get("hay_evento") and float(data.get("confianza", 0)) >= 0.55:
            found.append(VisualEvent(
                time=round(t, 2), kind=data.get("tipo", "otro"),
                confidence=float(data["confianza"]), label=data.get("tipo", "").upper() or "MOMENTO",
                detail=data.get("texto_leido", ""), source="scan",
            ))
    return found


def verify_clip(
    video_path: Path, seconds: float, audio_beats: list[float], work_dir: Path,
    do_scan: bool = True,
) -> list[VisualEvent]:
    """Punto de entrada: toma los candidatos de audio (detect_beats), los
    verifica visualmente, y opcionalmente barre el resto del clip buscando
    eventos que el audio no capto. Devuelve eventos verificados y ordenados
    por tiempo. Lista vacia = el clip no tiene ningun momento real (se debería
    descartar, no editarlo igual).

    QuotaExceededError acá significa que TODOS los proveedores configurados
    (Gemini, Groq, OpenRouter) están sin cuota -- el fallback entre ellos ya
    ocurrió dentro de _ask_vision_model."""
    events: list[VisualEvent] = []

    for beat in audio_beats:
        try:
            ev = verify_beat(video_path, beat, work_dir)
        except QuotaExceededError:
            raise  # ambos proveedores sin cuota: no tiene sentido seguir gastando llamadas
        except VisionError as e:
            logger.warning("Fallo verificando beat %.2fs: %s", beat, e)
            continue
        if ev:
            events.append(ev)

    if do_scan:
        confirmed_times = [e.time for e in events]
        try:
            missed = scan_for_missed_events(video_path, seconds, confirmed_times, work_dir)
            events.extend(missed)
        except QuotaExceededError:
            raise
        except VisionError as e:
            logger.warning("Fallo el barrido completo: %s", e)

    events.sort(key=lambda e: e.time)
    return events
