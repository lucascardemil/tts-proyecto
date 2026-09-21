"""
Generador automático de video para historias de dormir sobre animales.

Toma imágenes + el audio ya generado (o cualquier audio) y arma un video
vertical (1080x1920, formato Reels/Shorts/TikTok) con Remotion:
  - Efecto Ken Burns (zoom/paneo lento) en cada imagen
  - Transiciones tipo crossfade entre imágenes
  - Subtítulos sincronizados palabra por palabra (vía transcripción automática)
  - Viñeta cinematográfica + luciérnagas animadas (ambiente nocturno/emocional)
  - Tarjeta de título animada al inicio

Requiere Node.js + `npm install` corrido dentro de la carpeta video/, y las
dependencias Python opcionales: faster-whisper, mutagen (ver requirements.txt).
"""

import difflib
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent
VIDEO_DIR = BASE_DIR / "video"
VIDEO_PUBLIC_DIR = VIDEO_DIR / "public"
VIDEO_OUT_DIR = VIDEO_DIR / "out"

VIDEO_PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_OUT_DIR.mkdir(parents=True, exist_ok=True)

FPS = 30
MIN_IMAGE_SECONDS = 3.5  # cuánto se muestra cada imagen como mínimo
DEFAULT_CLIP_SECONDS = 5.0  # duración asumida de un clip de video si no se puede leer

# Debe coincidir con TRANSITION_FRAMES en video/src/StoryVideo.tsx — TransitionSeries
# solapa este número de frames entre cada par de escenas consecutivas, así que el
# contenido visible termina siendo (n-1) * TRANSITION_FRAMES frames más corto que la
# suma de duraciones de escena. Si no se compensa, sobra ese tiempo en negro al final
# (la composición y el audio duran más que el contenido visual real).
TRANSITION_FRAMES = 20
KEN_BURNS_PATTERN = ["zoomIn", "panRight", "zoomOut", "panLeft"]

# Specs de entrega del audio final (ver normalize_audio()).
AUDIO_TARGET_SAMPLE_RATE = 48000
AUDIO_TARGET_LUFS = -15  # punto medio del rango -14/-16 LUFS pedido
AUDIO_TARGET_TP = -1.5  # dBTP

# Debe coincidir con subtitleStyleSchema.default(...) en video/src/schema.ts —
# `npx remotion render` NO rellena los campos faltantes de un objeto anidado
# contra el schema de Zod (solo hace merge superficial con defaultProps), así
# que un subtitleStyle parcial (p.ej. solo {"position": "bottom"}) llega al
# componente con fontSize/textColor/etc. en `undefined` y el subtítulo se ve
# sin estilo (texto negro chico, sin fondo). Por eso acá se completa siempre
# el dict entero antes de escribirlo en props.json.
SUBTITLE_STYLE_DEFAULTS = {
    "fontFamily": "cinzel",
    "fontSize": 44,
    "position": "bottom",
    "textColor": "#ffffff",
    "highlightColor": "#ffd98a",
    "background": True,
    "strokeColor": None,
    "strokeWidth": 2,
    "uppercase": False,
    "italic": False,
    "letterSpacing": 0,
    "animationType": "highlight",
}

# Catálogo de estilos de subtítulo: "clasico" es el look original del
# proyecto, y los otros 6 clonan los estilos virales documentados en
# https://github.com/nicolaigaina/ai-video-captions
# (backend/caption-styles.config.json) — Hormozi, MrBeast, Karaoke, Minimal,
# Bounce y Classic. Cada valor es un subtitle_style completo — se usa tal
# cual, sin mezclar con SUBTITLE_STYLE_DEFAULTS.
SUBTITLE_PRESETS = {
    "clasico": {
        "label": "Clásico (caja)",
        "fontFamily": "cinzel",
        "fontSize": 44,
        "position": "bottom",
        "textColor": "#ffffff",
        "highlightColor": "#ffd98a",
        "background": True,
        "strokeColor": None,
        "strokeWidth": 2,
        "uppercase": False,
        "italic": False,
        "letterSpacing": 0,
        "animationType": "highlight",
    },
    "hormozi": {
        "label": "Hormozi",
        "fontFamily": "montserrat",
        "fontSize": 46,
        "position": "bottom",
        "textColor": "#ffffff",
        "highlightColor": "#00ffff",
        "background": False,
        "strokeColor": "#000000",
        "strokeWidth": 2,
        "uppercase": True,
        "italic": False,
        "letterSpacing": 0,
        "animationType": "highlight",
    },
    "mrbeast": {
        "label": "MrBeast",
        "fontFamily": "bebas",
        "fontSize": 50,
        "position": "bottom",
        "textColor": "#ffff00",
        "highlightColor": "#ff6600",
        "background": False,
        "strokeColor": "#000000",
        "strokeWidth": 3,
        "uppercase": True,
        "italic": False,
        "letterSpacing": 0,
        "animationType": "highlight",
    },
    "karaoke": {
        "label": "Karaoke",
        "fontFamily": "montserrat",
        "fontSize": 46,
        "position": "bottom",
        "textColor": "#ffffff",
        "highlightColor": "#0080ff",
        "background": False,
        "strokeColor": "#000000",
        "strokeWidth": 2,
        "uppercase": True,
        "italic": False,
        "letterSpacing": 0,
        "animationType": "highlight",
    },
    "minimal": {
        "label": "Minimal",
        "fontFamily": "bebas",
        "fontSize": 48,
        "position": "bottom",
        "textColor": "#ffffff",
        "highlightColor": "#f5f5f5",
        "background": False,
        "strokeColor": "#000000",
        "strokeWidth": 1.5,
        "uppercase": True,
        "italic": True,
        "letterSpacing": 2,
        "animationType": "scale",
    },
    "bounce": {
        "label": "Bounce",
        "fontFamily": "bangers",
        "fontSize": 48,
        "position": "bottom",
        "textColor": "#00ff88",
        "highlightColor": "#ff00ff",
        "background": False,
        "strokeColor": "#000000",
        "strokeWidth": 2,
        "uppercase": True,
        "italic": False,
        "letterSpacing": 0,
        "animationType": "bounce",
    },
    "classic": {
        "label": "Classic",
        "fontFamily": "anton",
        "fontSize": 48,
        "position": "bottom",
        "textColor": "#ffffff",
        "highlightColor": "#ffff00",
        "background": False,
        "strokeColor": "#000000",
        "strokeWidth": 3,
        "uppercase": True,
        "italic": False,
        "letterSpacing": 0,
        "animationType": "highlight",
    },
}
DEFAULT_SUBTITLE_PRESET = "clasico"


def get_subtitle_preset_style(preset_id: str) -> dict:
    """Devuelve el subtitle_style de un preset (sin la 'label', que es solo
    para la UI), o el del preset por default si el id no existe."""
    preset = SUBTITLE_PRESETS.get(preset_id, SUBTITLE_PRESETS[DEFAULT_SUBTITLE_PRESET])
    return {k: v for k, v in preset.items() if k != "label"}

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv"}

_whisper_model = None  # se cachea en memoria tras la primera carga


# ─────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────

def scene_type(path: str) -> str:
    """'image' o 'video' según la extensión del archivo."""
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return "image"  # cualquier extensión desconocida se trata como imagen


def get_video_duration(path: str, assumed: float = DEFAULT_CLIP_SECONDS) -> float:
    """
    Duración real de un clip de video en segundos. Si no se puede leer
    (falta opencv-python, formato raro, etc), devuelve la duración asumida
    en vez de fallar — los clips de este proyecto son de ~5s de todos modos.
    """
    try:
        import cv2

        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()
        if fps and frame_count and fps > 0:
            duration = frame_count / fps
            if duration > 0.1:
                return duration
    except Exception:
        pass
    return assumed


def get_audio_duration(audio_path: str) -> float:
    """Duración del audio en segundos, sin depender de ffmpeg."""
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        print("[ERROR] Falta 'mutagen'. Ejecuta: pip install mutagen")
        raise

    audio = MutagenFile(audio_path)
    if audio is None or audio.info is None:
        raise RuntimeError(f"No se pudo leer la duración de: {audio_path}")
    return float(audio.info.length)


def normalize_audio(src_path: str, dest_path: Path, report=print) -> bool:
    """
    Normaliza el audio de narración a las specs de entrega: 48kHz, loudness
    -15 LUFS (dentro del rango -14/-16 pedido) con true peak <= -1.5 dBTP,
    vía el filtro `loudnorm` (EBU R128) de ffmpeg. Escribe WAV PCM sin
    comprimir en dest_path — Remotion lo recodea a AAC al renderizar.

    Returns:
        True si se normalizó con ffmpeg; False si no está disponible y se
        debe usar una copia simple del audio original como respaldo.
    """
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-af", f"loudnorm=I={AUDIO_TARGET_LUFS}:TP={AUDIO_TARGET_TP}:LRA=11",
        "-ar", str(AUDIO_TARGET_SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        str(dest_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=(sys.platform == "win32"))
    except FileNotFoundError:
        report("[AVISO] No se encontró 'ffmpeg' — se usa el audio original sin normalizar loudness/sample rate.")
        return False
    if result.returncode != 0:
        report("[AVISO] ffmpeg falló normalizando el audio, se usa el original sin normalizar:")
        report(result.stderr[-1000:])
        return False
    return True


def _get_whisper_model():
    """Carga faster-whisper una sola vez y lo cachea en memoria."""
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model

    from faster_whisper import WhisperModel

    print("  (cargando modelo de transcripción, puede tardar la primera vez...)")
    # 'small' es un buen equilibrio velocidad/precisión para voz sintética clara.
    # device="auto" de ctranslate2 puede detectar una GPU por el driver y luego
    # fallar al cargar cublas si el entorno no trae los runtimes CUDA (torch
    # CPU-only) — mismo chequeo que tts_engine._tts_device() para no reventar.
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    _whisper_model = WhisperModel("small", device=device, compute_type=compute_type)
    return _whisper_model


def transcribe_words(audio_path: str, language: str = "es") -> list:
    """
    Transcribe el audio y devuelve una lista de {word, start, end} (segundos).
    Como el audio suele ser TTS (voz sintética muy clara), la precisión es alta.
    """
    try:
        model = _get_whisper_model()
    except ImportError:
        print("[ERROR] Falta 'faster-whisper'. Ejecuta: pip install faster-whisper")
        return []

    print("  🗣️  Transcribiendo audio para sincronizar los subtítulos...")
    segments, _ = model.transcribe(audio_path, language=language, word_timestamps=True)

    words = []
    for segment in segments:
        for w in segment.words:
            word_text = w.word.strip()
            if word_text:
                words.append({"word": word_text, "start": round(w.start, 3), "end": round(w.end, 3)})
    return words


def _subtitle_cache_path(audio_path: Path) -> Path:
    """Ruta del archivo de caché de transcripción para un audio dado."""
    return audio_path.with_name(audio_path.name + ".subs.json")


def _load_cached_words(audio_path: Path) -> Optional[list]:
    """
    Devuelve las palabras ya transcritas para este audio si existe una caché
    válida (mismo archivo, sin modificar desde que se transcribió), o None si
    hay que transcribir de nuevo. Evita repetir una transcripción costosa
    cuando se regenera el mismo video (nuevas imágenes, reintentos, etc.)
    con el mismo audio de narración.
    """
    cache_path = _subtitle_cache_path(audio_path)
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("mtime") == audio_path.stat().st_mtime and isinstance(data.get("words"), list):
            return data["words"]
    except Exception:
        pass
    return None


def _save_cached_words(audio_path: Path, words: list) -> None:
    cache_path = _subtitle_cache_path(audio_path)
    try:
        cache_path.write_text(
            json.dumps({"mtime": audio_path.stat().st_mtime, "words": words}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass  # la caché es solo una optimización, no debe interrumpir el pipeline


def _build_kenburns_sequence(n_images: int) -> list:
    """Alterna direcciones de Ken Burns para que no se repita seguido."""
    return [KEN_BURNS_PATTERN[i % len(KEN_BURNS_PATTERN)] for i in range(n_images)]


PAUSE_GAP_THRESHOLD = 0.15  # segundos de silencio entre palabras para considerarlo una pausa
PAUSE_SNAP_WINDOW = 0.6  # cuánto se puede mover un corte para alinearlo a una pausa cercana


def _find_pause_midpoints(words: list) -> list:
    """
    Puntos medios (segundos) de los silencios entre palabras consecutivas,
    ordenados por tamaño de pausa descendente (las pausas más grandes suelen
    marcar fin de oración/idea, así que se priorizan al ajustar cortes).
    """
    pauses = []
    for a, b in zip(words, words[1:]):
        gap = b["start"] - a["end"]
        if gap >= PAUSE_GAP_THRESHOLD:
            pauses.append((gap, (a["end"] + b["start"]) / 2))
    pauses.sort(key=lambda p: -p[0])
    return [t for _, t in pauses]


def _snap_cuts_to_pauses(cut_times: list, words: list) -> list:
    """
    Ajusta cada tiempo de corte (excepto el último, que es el final del audio)
    al punto medio de la pausa de habla más cercana dentro de PAUSE_SNAP_WINDOW,
    para que el cambio de imagen no caiga a mitad de una palabra/frase. Cada
    pausa se usa como mucho una vez. Sin pausa cercana disponible, se deja el
    corte tal como estaba (reparto equitativo, comportamiento actual).
    """
    if not words or len(cut_times) <= 1:
        return cut_times

    available = _find_pause_midpoints(words)
    used = set()
    snapped = list(cut_times)
    for i in range(len(cut_times) - 1):  # el último corte es el final del audio, no se mueve
        best_idx, best_dist = None, PAUSE_SNAP_WINDOW
        for j, pause_t in enumerate(available):
            if j in used:
                continue
            dist = abs(pause_t - cut_times[i])
            if dist <= best_dist:
                best_idx, best_dist = j, dist
        if best_idx is not None:
            snapped[i] = available[best_idx]
            used.add(best_idx)
    return snapped


def _normalize_tokens(text: str) -> list:
    return re.findall(r"[a-záéíóúñü0-9]+", text.lower())


def _align_boundaries_to_script(frases: list, words: list, duration: float) -> Optional[list]:
    """
    Devuelve n+1 puntos de corte (segundos, dominio 0..duration) alineados a
    donde cada frase empieza a decirse en el audio real (según la
    transcripción palabra por palabra), o None si la confianza es demasiado
    baja (el caller debe usar el reparto viejo — partes iguales/reescalado —
    como fallback).
    """
    if not frases or not words:
        return None
    word_tokens = [_normalize_tokens(w["word"])[:1] for w in words]  # 0 o 1 token por word
    word_tokens = [t[0] if t else "" for t in word_tokens]

    starts = [None] * len(frases)
    cursor = 0
    resolved = 0
    for i, frase in enumerate(frases):
        tokens = _normalize_tokens(frase)
        if not tokens:
            continue
        window = word_tokens[cursor:cursor + max(len(tokens) * 4, 25)]
        sm = difflib.SequenceMatcher(None, window, tokens, autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
        if not blocks or sm.ratio() < 0.25:
            continue
        first, last = blocks[0], blocks[-1]
        starts[i] = words[cursor + first.a]["start"]
        cursor = cursor + last.a + last.size
        resolved += 1

    if resolved < max(1, len(frases) * 0.4):
        return None  # muy poco confiable, mejor no arriesgar

    # ancla obligatoria en 0 y al final (duración del audio); el resto se
    # interpola proporcional a longitud de frase entre los puntos SÍ resueltos
    anchors = [(0, 0.0)] + [(i, t) for i, t in enumerate(starts) if t is not None] + [(len(frases), duration)]
    filled = [None] * (len(frases) + 1)
    for i, t in anchors:
        filled[i] = t
    for a, b in zip(anchors, anchors[1:]):
        i0, t0 = a
        i1, t1 = b
        if i1 - i0 <= 1:
            continue
        span_tokens = [len(_normalize_tokens(frases[k])) or 1 for k in range(i0, i1)]
        total = sum(span_tokens)
        acc = 0
        for k in range(i0 + 1, i1):
            acc += span_tokens[k - i0 - 1]
            filled[k] = t0 + (t1 - t0) * (acc / total)

    # no-decreciente por seguridad
    for i in range(1, len(filled)):
        filled[i] = max(filled[i], filled[i - 1])
    return filled


def _build_timeline(
    scenes: list,
    audio_name: str,
    duration: float,
    words: list,
    title: str,
    subtitle_style: Optional[dict] = None,
    frases: Optional[list] = None,
    animate_images: bool = True,
    ai_label: Optional[str] = None,
) -> dict:
    """
    Arma el diccionario de props que consume la composición de Remotion.

    Args:
        scenes: lista de dicts {'name', 'type': 'image'|'video', 'native_duration': float|None}
        audio_name, duration, words, title: igual que antes.
        subtitle_style: dict opcional (fontFamily/fontSize/position/textColor/
            highlightColor/background). Si es None, se omite la clave del
            todo y el default de zod en schema.ts rellena los valores
            actuales — mismo video que antes de esta opción.
        frases: lista opcional de "frase del guion" por escena (mismo orden
            y largo que scenes). Si viene y se puede alinear con confianza
            contra la transcripción real, cada escena dura lo que tarda su
            frase en narrarse en vez de repartirse en partes iguales.
    """
    n = len(scenes)

    # TransitionSeries solapa TRANSITION_FRAMES entre cada par de escenas
    # consecutivas (ver constante), así que el contenido visible termina
    # siendo (n-1) * TRANSITION_FRAMES frames más corto que la suma de
    # duraciones de escena. 'extended_duration' es el total que hay que
    # repartir entre las escenas para que, tras el solape, el contenido
    # visible dure exactamente 'duration' (el audio) — sin esto, sobran
    # esos frames en negro al final. La duración del audio en sí
    # (totalDurationSeconds, más abajo) no cambia.
    transition_overlap = max(n - 1, 0) * TRANSITION_FRAMES / FPS
    extended_duration = duration + transition_overlap

    # Duración objetivo de cada escena: los clips de video usan su duración
    # real (capada a la duración extendida); las imágenes se reparten
    # el tiempo que sobra, con un mínimo de MIN_IMAGE_SECONDS cada una.
    aligned = _align_boundaries_to_script(frases, words, duration) if frases and len(frases) == n else None

    if aligned:
        # Cortes alineados a donde cada frase empieza a narrarse en el audio
        # real, en vez de reparto artificial parejo/reescalado.
        scale = extended_duration / duration if duration > 0 else 1.0
        boundaries = [b * scale for b in aligned]
        target = [boundaries[i + 1] - boundaries[i] for i in range(n)]
        min_gap = (TRANSITION_FRAMES + 1) / FPS
        for i in range(1, len(boundaries) - 1):
            boundaries[i] = max(boundaries[i - 1] + min_gap, min(boundaries[i], boundaries[i + 1] - min_gap))
    else:
        target = [None] * n
        for i, sc in enumerate(scenes):
            if sc["type"] == "video":
                target[i] = min(sc["native_duration"] or DEFAULT_CLIP_SECONDS, extended_duration)

        video_total = sum(t for t in target if t is not None)
        n_images = target.count(None)
        remaining = max(extended_duration - video_total, 0.0)
        per_image = max(MIN_IMAGE_SECONDS, remaining / n_images) if n_images else 0.0
        target = [per_image if t is None else t for t in target]

        # Sin imágenes que absorban el desfase entre el total de los clips y la
        # duración del audio, hay que repartirlo proporcionalmente entre todos los
        # clips (en vez de dejar que el ajuste final de 'boundaries[-1]' lo vuelque
        # entero sobre el último clip, que terminaría con un playbackRate muy bajo
        # = cámara lenta / "glitch" perceptible solo en la última escena).
        if n_images == 0 and video_total > 0 and abs(extended_duration - video_total) > 0.01:
            scale = extended_duration / video_total
            target = [t * scale for t in target]

        # Puntos de corte entre dos imágenes consecutivas: se ajustan a la pausa
        # de habla más cercana para que el cambio de imagen no caiga a mitad de
        # una palabra. Los cortes que tocan un clip de video no se mueven, porque
        # su duración es fija (la del archivo). Ver PAUSE_GAP_THRESHOLD/PAUSE_SNAP_WINDOW.
        boundaries = [0.0]
        for dur in target:
            boundaries.append(boundaries[-1] + dur)
        boundaries[-1] = extended_duration

        adjustable_idx = [i for i in range(1, n) if scenes[i - 1]["type"] == "image" and scenes[i]["type"] == "image"]
        if adjustable_idx and words:
            cut_times = [boundaries[i] for i in adjustable_idx]
            snapped = _snap_cuts_to_pauses(cut_times, words)
            for idx, new_t in zip(adjustable_idx, snapped):
                boundaries[idx] = new_t
            # Un corte ajustado no puede cruzarse con sus vecinos (mantener orden temporal)
            # ni dejar una escena más corta que TRANSITION_FRAMES: TransitionSeries exige que
            # cada Sequence dure al menos lo que la Transition que le sigue, o Remotion falla.
            min_gap = (TRANSITION_FRAMES + 1) / FPS
            for i in range(1, len(boundaries) - 1):
                boundaries[i] = max(boundaries[i - 1] + min_gap, min(boundaries[i], boundaries[i + 1] - min_gap))

    directions = _build_kenburns_sequence(n)

    clips = []
    t = 0.0
    for i, (sc, dur) in enumerate(zip(scenes, target)):
        end_t = extended_duration if i == n - 1 else min(boundaries[i + 1], extended_duration)
        start_frame = round(t * FPS)
        end_frame = max(round(end_t * FPS), start_frame + 1)

        clip = {"src": sc["name"], "type": sc["type"], "startFrame": start_frame, "endFrame": end_frame}
        if sc["type"] == "video":
            native = sc["native_duration"] or DEFAULT_CLIP_SECONDS
            clip["nativeDurationInFrames"] = max(round(native * FPS), 1)
            slot_duration = end_t - t
            if slot_duration > native:
                # El slot asignado (p.ej. la última escena, que se estira para
                # llenar el timeline) es más largo que el clip: en vez de
                # dejar que Remotion haga loop (reinicia el clip = glitch),
                # bajamos la velocidad para que una sola pasada llene el slot.
                clip["playbackRate"] = native / slot_duration
        else:
            clip["kenBurns"] = directions[i] if animate_images else "none"

        clips.append(clip)
        t = end_t
        if t >= extended_duration:
            break

    timeline = {
        "title": title,
        "audioSrc": audio_name,
        "totalDurationSeconds": duration,
        "scenes": clips,
        "subtitles": words,
    }
    timeline["subtitleStyle"] = {**SUBTITLE_STYLE_DEFAULTS, **(subtitle_style or {})}
    if ai_label:
        timeline["aiLabel"] = ai_label
    return timeline


# ─────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# ORDENAMIENTO AUTOMÁTICO DE IMÁGENES
# ─────────────────────────────────────────────

def _natural_sort_key(path: str) -> list:
    """
    Clave de orden 'natural': separa el nombre en trozos de texto/números
    para que 'escena2' quede antes que 'escena10' (a diferencia del orden
    alfabético puro, donde '10' queda antes que '2').
    """
    name = Path(path).stem
    parts = re.split(r"(\d+)", name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def _has_digits(path: str) -> bool:
    return bool(re.search(r"\d", Path(path).stem))


def _get_exif_datetime(path: str):
    """Fecha/hora de captura de la foto (EXIF), o None si no tiene o falla la lectura."""
    try:
        from PIL import Image, ExifTags

        img = Image.open(path)
        exif_data = img.getexif()
        if not exif_data:
            return None

        # DateTimeOriginal vive en el sub-IFD de Exif, no en el nivel raíz
        exif_ifd = exif_data.get_ifd(0x8769) if hasattr(exif_data, "get_ifd") else {}
        tag_map = {v: k for k, v in ExifTags.TAGS.items()}

        for tag_name in ("DateTimeOriginal", "DateTimeDigitized"):
            tag_id = tag_map.get(tag_name)
            if tag_id and tag_id in exif_ifd:
                try:
                    return datetime.strptime(exif_ifd[tag_id], "%Y:%m:%d %H:%M:%S")
                except (ValueError, TypeError):
                    continue

        tag_id = tag_map.get("DateTime")
        if tag_id and tag_id in exif_data:
            try:
                return datetime.strptime(exif_data[tag_id], "%Y:%m:%d %H:%M:%S")
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    return None


def sort_images(image_paths: list, report=print) -> list:
    """
    Reordena las imágenes automáticamente:
      1. Si los nombres de archivo tienen números (ej. 'escena_1.jpg',
         'foto2.png'), se ordenan por esos números.
      2. Si ningún nombre tiene números, se intenta ordenar por la fecha
         de captura guardada en el EXIF de cada foto.
      3. Si no hay números ni EXIF completo en todas las imágenes, se
         mantiene el orden en que se subieron (no se puede inferir mejor orden).
    """
    if len(image_paths) <= 1:
        return image_paths

    if any(_has_digits(p) for p in image_paths):
        report("  🔢 Ordenando imágenes por los números en el nombre del archivo...")
        return sorted(image_paths, key=_natural_sort_key)

    dated = [(p, _get_exif_datetime(p)) for p in image_paths]
    if all(dt is not None for _, dt in dated):
        report("  📅 Ordenando imágenes por fecha de captura (EXIF)...")
        return [p for p, _ in sorted(dated, key=lambda x: x[1])]

    report("  ↕️  No se detectaron números en los nombres ni fecha EXIF en todas las "
           "imágenes; se mantiene el orden en que se subieron.")
    return image_paths


def build_props(
    image_paths: list,
    audio_path: str,
    title: str,
    language: str = "es",
    subtitles_enabled: bool = True,
    subtitle_style: Optional[dict] = None,
    frases: Optional[list] = None,
    animate_images: bool = True,
    ai_label: Optional[str] = None,
    on_progress=None,
) -> Optional[dict]:
    """
    Arma la edición: copia los assets al proyecto de Remotion, transcribe el
    audio para los subtítulos y escribe el timeline en video/props.json —
    sin renderizar. El resultado queda listo para abrirse en Remotion Studio
    (o para renderizarse con render_props()).

    Args:
        image_paths: rutas locales de las imágenes y/o clips de video (se
            detecta el tipo por extensión), en el orden deseado.
        audio_path: ruta local del audio de narración (mp3 o wav).
        title: título de la historia (se muestra en la tarjeta inicial).
        language: idioma para la transcripción de subtítulos.
        subtitles_enabled: si es False, el video se genera sin subtítulos y
            se salta por completo el paso de transcripción (más rápido).
        subtitle_style: dict opcional con fontFamily/fontSize/position/
            textColor/highlightColor/background (ver subtitleStyleSchema en
            video/src/schema.ts). Si es None, se usan los valores por
            defecto (ajustables después a mano en Remotion Studio).
        frases: lista opcional de "frase del guion" por escena, en el mismo
            orden que image_paths — ver _align_boundaries_to_script.
        animate_images: si es False, las imágenes quedan estáticas (sin
            Ken Burns); no afecta a los clips de video.
        ai_label: rótulo fijo durante todo el video (p. ej. "Historia
            recreada con IA"); None = sin rótulo.
        on_progress: función opcional callback(str) para reportar avance.

    Returns:
        El timeline (dict) escrito en props.json, o None si hubo error.
    """
    def report(msg: str):
        print(msg)
        if on_progress:
            on_progress(msg)

    if not image_paths:
        report("[ERROR] Necesitas al menos una imagen o clip de video.")
        return None
    if not Path(audio_path).exists():
        report(f"[ERROR] No se encontró el audio: {audio_path}")
        return None

    # 1. Ordenar las escenas automáticamente (por nombre/EXIF) y preparar la
    #    carpeta pública de Remotion (assets estáticos del render)
    image_paths = sort_images(image_paths, report=report)

    report("📁 Preparando imágenes, clips de video y audio...")
    for f in VIDEO_PUBLIC_DIR.glob("*"):
        if f.is_file():
            f.unlink()

    scenes = []
    n_video_clips = 0
    for i, src in enumerate(image_paths):
        ext = Path(src).suffix.lower() or ".jpg"
        dest_name = f"scene_{i:03d}{ext}"
        shutil.copy(src, VIDEO_PUBLIC_DIR / dest_name)

        stype = scene_type(src)
        native_duration = None
        if stype == "video":
            native_duration = get_video_duration(str(VIDEO_PUBLIC_DIR / dest_name))
            n_video_clips += 1

        scenes.append({"name": dest_name, "type": stype, "native_duration": native_duration})

    if n_video_clips:
        report(f"  🎞️  {n_video_clips} clip(s) de video detectado(s) entre las escenas.")

    # Normalizado a WAV siempre (48kHz + loudness) — así el sample rate y el
    # loudness de entrega no dependen del formato con que llegó el audio.
    # Si ffmpeg no está disponible, se cae a copiar el original tal cual
    # (conservando su extensión real, ya que el WAV de respaldo no aplica).
    audio_name = "narracion.wav"
    report("🎚️  Normalizando audio (48kHz, loudness)...")
    if not normalize_audio(audio_path, VIDEO_PUBLIC_DIR / audio_name, report=report):
        audio_name = f"narracion{Path(audio_path).suffix.lower() or '.mp3'}"
        shutil.copy(audio_path, VIDEO_PUBLIC_DIR / audio_name)

    # 2. Duración real del audio
    duration = get_audio_duration(str(VIDEO_PUBLIC_DIR / audio_name))
    report(f"⏱️  Duración del audio: {duration:.1f}s")

    # 3. Transcripción para subtítulos sincronizados (opcional)
    if subtitles_enabled:
        cache_source = Path(audio_path)
        words = _load_cached_words(cache_source)
        if words is not None:
            report(f"✓ Subtítulos reutilizados de una transcripción anterior ({len(words)} palabras)")
        else:
            report("🗣️  Transcribiendo para generar subtítulos...")
            words = transcribe_words(str(VIDEO_PUBLIC_DIR / audio_name), language=language)
            report(f"✓ {len(words)} palabras transcritas")
            _save_cached_words(cache_source, words)
    else:
        words = []
        report("🔇 Subtítulos desactivados — se omite la transcripción.")

    # 4. Timeline (qué escena se ve cuándo, con qué efecto o si es un clip de video)
    timeline = _build_timeline(
        scenes, audio_name, duration, words, title, subtitle_style,
        frases=frases, animate_images=animate_images, ai_label=ai_label,
    )
    props_path = VIDEO_DIR / "props.json"
    props_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")

    report("✅ Edición lista.")
    return timeline


def render_props(
    props_path,
    output_name: Optional[str] = None,
    orientation: str = "vertical",
    on_progress=None,
) -> Optional[str]:
    """
    Renderiza con Remotion un props.json ya armado (por build_props()).

    Args:
        orientation: "vertical" (1080x1920, Reels/Shorts/TikTok) u
            "horizontal" (1920x1080, YouTube estándar) — selecciona la
            composición de Remotion a usar (ver video/src/Root.tsx).

    Returns:
        Ruta al .mp4 generado, o None si hubo error.
    """
    def report(msg: str, percent: Optional[int] = None):
        print(msg)
        if on_progress:
            on_progress(f"{msg} ({percent}%)" if percent is not None else msg)

    if output_name is None:
        output_name = f"video_{int(time.time())}.mp4"
    output_path = VIDEO_OUT_DIR / output_name

    composition_id = "StoryVideoHorizontal" if orientation == "horizontal" else "StoryVideo"
    report("🎬 Renderizando video con Remotion (puede tardar varios minutos)...")
    cmd = [
        "npx", "remotion", "render", "src/index.ts", composition_id,
        str(output_path), f"--props={props_path}",
        # Specs de entrega (H.264/yuv420p/BT.709 + AAC 192k) — el sample
        # rate y el loudness del audio ya vienen fijados por normalize_audio()
        # sobre narracion.wav antes de llegar acá.
        "--codec=h264",
        "--crf=20",
        "--pixel-format=yuv420p",
        "--color-space=bt709",
        "--audio-codec=aac",
        "--audio-bitrate=192k",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(VIDEO_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            shell=(sys.platform == "win32"),
        )
    except FileNotFoundError:
        report(
            "[ERROR] No se encontró 'npx'. Instala Node.js (https://nodejs.org) y "
            "corre 'npm install' dentro de la carpeta video/."
        )
        return None

    # Remotion actualiza su barra de progreso pisando la misma línea con \r,
    # no con \n, así que hay que leer de a un carácter para verla en vivo.
    frame_re = re.compile(r"(\d+)/(\d+)")
    ansi_re = re.compile(r"\x1b\[[0-9;]*m")
    output_tail = ""
    line_buf = ""
    last_percent = None
    while True:
        ch = proc.stdout.read(1)
        if not ch:
            break
        output_tail = (output_tail + ch)[-2500:]
        if ch in ("\r", "\n"):
            line = ansi_re.sub("", line_buf).strip()
            line_buf = ""
            m = frame_re.search(line)
            if m:
                frames, total = int(m.group(1)), int(m.group(2))
                if total > 0:
                    percent = min(99, int(frames / total * 100))
                    if percent != last_percent:
                        last_percent = percent
                        report("🎬 Renderizando video con Remotion...", percent)
        else:
            line_buf += ch
    proc.wait()

    if proc.returncode != 0:
        report("[ERROR] Remotion falló al renderizar:")
        report(output_tail)
        return None

    report(f"✅ Video guardado en: {output_path}")
    return str(output_path)


def generate_video(
    image_paths: list,
    audio_path: str,
    title: str,
    output_name: Optional[str] = None,
    language: str = "es",
    subtitles_enabled: bool = True,
    subtitle_style: Optional[dict] = None,
    orientation: str = "vertical",
    on_progress=None,
) -> Optional[str]:
    """
    Pipeline completo (uso por CLI): arma la edición y la renderiza en un
    solo paso. Ver build_props()/render_props() para el flujo dividido que
    usa la app web (edición → Remotion Studio → render manual).
    """
    timeline = build_props(
        image_paths=image_paths,
        audio_path=audio_path,
        title=title,
        language=language,
        subtitles_enabled=subtitles_enabled,
        subtitle_style=subtitle_style,
        on_progress=on_progress,
    )
    if timeline is None:
        return None
    return render_props(
        VIDEO_DIR / "props.json", output_name=output_name, orientation=orientation, on_progress=on_progress
    )


if __name__ == "__main__":
    # Uso directo desde terminal:
    #   python video_maker.py "Título" img1.jpg img2.jpg ... --audio narracion.mp3
    import argparse

    parser = argparse.ArgumentParser(description="Genera un video de historia para dormir")
    parser.add_argument("title", nargs="?", default=None, help="Título de la historia (opcional)")
    parser.add_argument("images", nargs="+", help="Rutas de las imágenes y/o clips de video, en orden")
    parser.add_argument("--audio", required=True, help="Ruta del audio de narración")
    parser.add_argument("--output", help="Nombre del archivo .mp4 de salida")
    parser.add_argument("--lang", default="es", help="Idioma para los subtítulos")
    parser.add_argument("--no-subtitles", action="store_true", help="Genera el video sin subtítulos")
    parser.add_argument("--subtitle-font", choices=["cinzel", "playfair", "poppins", "montserrat"],
                         help="Fuente de los subtítulos (default: cinzel)")
    parser.add_argument("--subtitle-size", type=int, help="Tamaño de letra de los subtítulos (default: 44)")
    parser.add_argument("--subtitle-position", choices=["top", "center", "bottom"],
                         help="Posición de los subtítulos (default: bottom; ignorada si se usan --subtitle-x/--subtitle-y)")
    parser.add_argument("--subtitle-x", type=float,
                         help="Posición horizontal libre en %% (0-100). Requiere también --subtitle-y")
    parser.add_argument("--subtitle-y", type=float,
                         help="Posición vertical libre en %% (0-100). Requiere también --subtitle-x")
    parser.add_argument("--subtitle-color", help="Color del texto, ej. #ffffff (default: #ffffff)")
    parser.add_argument("--subtitle-highlight", help="Color de la palabra activa, ej. #ffd98a (default: #ffd98a)")
    parser.add_argument("--no-subtitle-background", action="store_true",
                         help="Quita el fondo semitransparente detrás de los subtítulos")
    args = parser.parse_args()

    args.title = args.title or ""

    for flag_name, value in (("--subtitle-x", args.subtitle_x), ("--subtitle-y", args.subtitle_y)):
        if value is not None and not (0 <= value <= 100):
            parser.error(f"{flag_name} debe estar entre 0 y 100 (recibido: {value})")
    if (args.subtitle_x is None) != (args.subtitle_y is None):
        parser.error("--subtitle-x y --subtitle-y deben usarse juntos")

    subtitle_style = None
    style_overrides = {
        "fontFamily": args.subtitle_font,
        "fontSize": args.subtitle_size,
        "position": args.subtitle_position,
        "positionX": args.subtitle_x,
        "positionY": args.subtitle_y,
        "textColor": args.subtitle_color,
        "highlightColor": args.subtitle_highlight,
        "background": False if args.no_subtitle_background else None,
    }
    if any(v is not None for v in style_overrides.values()):
        subtitle_style = {k: v for k, v in style_overrides.items() if v is not None}

    result = generate_video(
        image_paths=args.images,
        audio_path=args.audio,
        title=args.title,
        output_name=args.output,
        language=args.lang,
        subtitles_enabled=not args.no_subtitles,
        subtitle_style=subtitle_style,
    )
    sys.exit(0 if result else 1)
