"""
Motor de Texto a Voz (TTS) — Chatterbox (Resemble AI)

Chatterbox es un modelo de clonación de voz "zero-shot": no tiene voces
con nombre como los servicios comerciales, genera cualquier voz a partir
de un audio de referencia corto (5-20s). Por eso este proyecto mantiene una
pequeña "biblioteca de voces" (carpeta voices/) con un clip de referencia
por cada acento/género en español. Esos clips se generan UNA SOLA VEZ,
automáticamente, usando Edge TTS internamente como bootstrap (no es un
motor que el usuario elija: es solo la fuente de la voz de referencia).

Chatterbox es autoregresivo: genera el audio palabra a palabra, lo que le
da una prosodia mucho más natural que los motores no-autoregresivos, a
cambio de poder "perder el hilo" ocasionalmente en textos largos (corta o
murmura una palabra). Por eso cada generación se verifica: primero un
chequeo barato de silencios raros, y si pasa, una transcripción real con
faster-whisper comparada contra el texto pedido — con hasta 4 reintentos
si algo no calza. El texto se genera oración por oración: cada reintento
regenera solo la oración con problema, no el texto completo.
"""

import difflib
import os
import re
import sys
import time
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
VOICES_DIR = BASE_DIR / "voices"

OUTPUT_DIR.mkdir(exist_ok=True)
VOICES_DIR.mkdir(exist_ok=True)

# Frase de muestra usada para generar los clips de referencia: neutra, con
# ritmo pausado, similar al tono de narración de un cuento para dormir.
_BOOTSTRAP_SAMPLE_TEXT = {
    "es": (
        "Había una vez, en un lugar muy lejano, una historia que se contaba "
        "en voz baja, cada noche, junto a la luz de las estrellas."
    ),
    "en": (
        "Once upon a time, in a place far away, there was a story that was told "
        "softly, every night, beneath the light of the stars."
    ),
}

# Biblioteca de voces: una por acento/género/idioma. 'bootstrap_voice' es la
# voz de Edge TTS que se usa SOLO para generar el clip de referencia la
# primera vez; la calidad final que escucha el usuario siempre es la del
# motor clonador, no la de Edge — Edge aquí solo aporta el "molde" de acento
# a clonar. 'lang' se usa para elegir automáticamente el idioma de
# generación (y de la frase de bootstrap) sin que el usuario tenga que
# configurarlo.
VOICE_LIBRARY = {
    "es_es_mujer": {
        "label": "Español (España) — Mujer",
        "gentle": True,
        "bootstrap_voice": "es-ES-ElviraNeural",
        "lang": "es",
    },
    "es_es_hombre": {
        "label": "Español (España) — Hombre",
        "gentle": False,
        "bootstrap_voice": "es-ES-AlvaroNeural",
        "lang": "es",
    },
    "es_mx_mujer": {
        "label": "Español (México) — Mujer",
        "gentle": True,
        "bootstrap_voice": "es-MX-DaliaNeural",
        "lang": "es",
    },
    "es_mx_hombre": {
        "label": "Español (México) — Hombre",
        "gentle": False,
        "bootstrap_voice": "es-MX-JorgeNeural",
        "lang": "es",
    },
    "es_ar_mujer": {
        "label": "Español (Argentina) — Mujer",
        "gentle": True,
        "bootstrap_voice": "es-AR-ElenaNeural",
        "lang": "es",
    },
    "es_ar_hombre": {
        "label": "Español (Argentina) — Hombre",
        "gentle": False,
        "bootstrap_voice": "es-AR-TomasNeural",
        "lang": "es",
    },
    "es_co_mujer": {
        "label": "Español (Colombia) — Mujer",
        "gentle": True,
        "bootstrap_voice": "es-CO-SalomeNeural",
        "lang": "es",
    },
    "es_us_mujer": {
        "label": "Español (Latinoamérica neutro) — Mujer",
        "gentle": True,
        "bootstrap_voice": "es-US-PalomaNeural",
        "lang": "es",
    },
    "en_us_mujer": {
        "label": "Inglés (EE. UU.) — Mujer",
        "gentle": True,
        "bootstrap_voice": "en-US-AriaNeural",
        "lang": "en",
    },
    "en_us_hombre": {
        "label": "Inglés (EE. UU.) — Hombre",
        "gentle": False,
        "bootstrap_voice": "en-US-GuyNeural",
        "lang": "en",
    },
    "en_gb_mujer": {
        "label": "Inglés (Reino Unido) — Mujer",
        "gentle": True,
        "bootstrap_voice": "en-GB-SoniaNeural",
        "lang": "en",
    },
    "en_gb_hombre": {
        "label": "Inglés (Reino Unido) — Hombre",
        "gentle": False,
        "bootstrap_voice": "en-GB-RyanNeural",
        "lang": "en",
    },
    "en_au_mujer": {
        "label": "Inglés (Australia) — Mujer",
        "gentle": True,
        "bootstrap_voice": "en-AU-NatashaNeural",
        "lang": "en",
    },
    "en_au_hombre": {
        "label": "Inglés (Australia) — Hombre",
        "gentle": False,
        "bootstrap_voice": "en-AU-WilliamNeural",
        "lang": "en",
    },
}

DEFAULT_VOICE = "es_es_mujer"

# Ajuste para que cualquier voz suene más calmada y contenida; ideal para
# historias para dormir. exaggeration bajo = menos intensidad emocional,
# cfg_weight en el default = sigue de cerca la voz de referencia.
BEDTIME_PRESET = {"exaggeration": 0.3, "cfg_weight": 0.8}


# ─────────────────────────────────────────────
# BIBLIOTECA DE VOCES (bootstrap con Edge TTS, una sola vez)
# ─────────────────────────────────────────────

def voice_reference_path(voice_id: str) -> Path:
    return VOICES_DIR / f"{voice_id}.wav"


def voice_is_ready(voice_id: str) -> bool:
    return voice_reference_path(voice_id).exists()


def bootstrap_voice(voice_id: str, show_progress: bool = True) -> bool:
    """
    Genera (una sola vez) el clip de referencia de una voz de la biblioteca.
    Usa Edge TTS + torchaudio internamente; requiere internet solo esta vez.
    """
    if voice_id not in VOICE_LIBRARY:
        print(f"[ERROR] Voz '{voice_id}' no reconocida. Usa una de: {list(VOICE_LIBRARY)}")
        return False

    if voice_reference_path(voice_id).exists():
        return True

    try:
        import asyncio
        import edge_tts
    except ImportError:
        print(
            f"[ERROR] Para generar la voz de referencia '{voice_id}' la primera vez, "
            "instala: pip install edge-tts (torchaudio ya debería estar instalado)."
        )
        return False

    bootstrap_voice_name = VOICE_LIBRARY[voice_id]["bootstrap_voice"]
    bootstrap_lang = VOICE_LIBRARY[voice_id].get("lang", "es")
    sample_text = _BOOTSTRAP_SAMPLE_TEXT.get(bootstrap_lang, _BOOTSTRAP_SAMPLE_TEXT["es"])
    if show_progress:
        print(f"  🎙️  Preparando voz de referencia '{voice_id}' (solo la primera vez)...")

    tmp_mp3 = VOICES_DIR / f"_tmp_{voice_id}.mp3"
    try:
        async def _gen():
            communicate = edge_tts.Communicate(sample_text, bootstrap_voice_name)
            await communicate.save(str(tmp_mp3))

        asyncio.run(_gen())

        subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp_mp3), "-acodec", "pcm_s16le", str(voice_reference_path(voice_id))],
            check=True, capture_output=True,
        )
        return True
    except Exception as e:
        print(f"[ERROR] No se pudo generar la voz de referencia '{voice_id}': {e}")
        return False
    finally:
        if tmp_mp3.exists():
            tmp_mp3.unlink()


def ensure_all_voices(show_progress: bool = True) -> bool:
    """Genera todos los clips de referencia que aún falten. Pensado para correr una sola vez."""
    ok = True
    for key in VOICE_LIBRARY:
        if not bootstrap_voice(key, show_progress=show_progress):
            ok = False
    return ok


# ─────────────────────────────────────────────
# CHATTERBOX (Resemble AI) — único motor TTS del proyecto
# ─────────────────────────────────────────────
# MIT license, apto para uso comercial. Autoregresivo: mucho más natural
# que las alternativas no-autoregresivas probadas (OpenVoice/MeloTTS), a
# cambio de poder perder palabras ocasionalmente en textos largos — de ahí
# la verificación con reintentos más abajo.

_chatterbox_model = None  # se cachea en memoria tras la primera carga
_verify_whisper_model = None  # modelo chico de faster-whisper, cacheado


def _tts_device() -> str:
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _patch_broken_perth_watermarker(model) -> None:
    """
    resemble-perth puede tirar excepción al aplicar la marca de agua en
    clips muy cortos, y ChatterboxTTS.generate() no tiene forma de
    desactivarla. Se envuelve para que un fallo del watermarker no tire
    abajo toda la generación — se entrega el audio sin marca de agua en
    ese caso.
    """
    watermarker = getattr(model, "watermarker", None)
    if watermarker is None or getattr(watermarker, "_watermark_patched", False):
        return

    original_apply = watermarker.apply_watermark

    def _safe_apply_watermark(wav, *args, **kwargs):
        try:
            return original_apply(wav, *args, **kwargs)
        except Exception as e:
            print(f"  ⚠️  Watermark falló ({e}), se entrega el audio sin marca de agua.")
            return wav

    watermarker.apply_watermark = _safe_apply_watermark
    watermarker._watermark_patched = True


def _get_chatterbox_model():
    """
    Carga y cachea el modelo Chatterbox (una sola instancia por proceso).

    Usa el checkpoint Multilingual V3 (23 idiomas, entrenado también en
    español) en vez del checkpoint base (mayormente inglés, "fingía" hablar
    español vía clonación zero-shot). El base sustituía/alucinaba palabras
    en español de forma reproducible ("huía un perofel" en vez de "y un
    perro fiel"); el V3 resolvió ese mismo texto limpio, sin reintentos —
    confirmado al oído por el usuario.
    """
    global _chatterbox_model
    if _chatterbox_model is not None:
        return _chatterbox_model

    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    print(f"  (cargando Chatterbox Multilingual V3 en '{_tts_device()}', puede tardar la primera vez...)")
    model = ChatterboxMultilingualTTS.from_pretrained(_tts_device(), t3_model="v3")
    _patch_broken_perth_watermarker(model)
    _chatterbox_model = model
    return model


def _get_verify_whisper_model():
    """Carga y cachea un modelo chico de faster-whisper, usado solo para
    verificar que el audio generado por Chatterbox dice lo que se le pidió."""
    global _verify_whisper_model
    if _verify_whisper_model is not None:
        return _verify_whisper_model

    from faster_whisper import WhisperModel

    compute_type = "float16" if _tts_device() == "cuda" else "int8"
    # "medium" en vez de "small": con "small" el propio ruido de transcripción
    # en español (confusiones fonéticas tipo "lejano"→"leyano") ya generaba
    # varios mismatches por chunk incluso con audio correcto, disparando
    # reintentos innecesarios.
    _verify_whisper_model = WhisperModel("medium", device=_tts_device(), compute_type=compute_type)
    return _verify_whisper_model


def _normalize_words(text: str) -> list:
    """Minúsculas, sin acentos, sin puntuación — para comparar texto pedido vs. transcripto sin falsos positivos."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text)
    return text.split()


def _count_mismatched_words(expected_words: list, actual_words: list) -> tuple:
    """Cuenta palabras que no calzan entre lo pedido y lo transcripto (alineación por bloques, no por posición exacta).
    Devuelve (mismatches_totales, inserciones) — las inserciones (palabras de más en
    lo transcripto que no están en lo pedido) son casi siempre repeticiones/alucinaciones
    del modelo, a diferencia de sustituciones que suelen ser ruido de transcripción."""
    matcher = difflib.SequenceMatcher(a=expected_words, b=actual_words, autojunk=False)
    mismatched = 0
    insertions = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            mismatched += max(i2 - i1, j2 - j1)
        if tag == "insert":
            insertions += j2 - j1
    return mismatched, insertions


def _transcript_mismatch(expected_text: str, audio_path: str, max_mismatches: int) -> bool:
    """
    True si la transcripción de audio_path difiere de expected_text en más
    palabras de las toleradas. El umbral escala con el largo del texto (8%
    de las palabras esperadas) en vez de ser un número fijo — con un número
    fijo, un fragmento largo (hasta CHUNK_SIZE=550 caracteres, ~90 palabras)
    dispararía reintentos por cualquier variación menor de transcripción;
    max_mismatches queda como piso mínimo para textos cortos. 8% (no 15%):
    con 15% dos palabras alucinadas en una frase de 20 pasaban sin disparar
    reintento pese a notarse al oído — confirmado con un caso real.

    Las inserciones (palabra de más, típico "se repite una palabra al unir")
    disparan glitch SIEMPRE, sin pasar por el piso de tolerancia — con textos
    cortos el piso mínimo (1) dejaba pasar exactamente ese caso.
    """
    try:
        whisper_model = _get_verify_whisper_model()
        segments, _ = whisper_model.transcribe(audio_path, language="es" if _looks_spanish(expected_text) else None)
        transcribed = " ".join(seg.text for seg in segments)
    except Exception as e:
        print(f"  ⚠️  No se pudo verificar la transcripción ({e}), se entrega el audio igual.")
        return False

    expected_words = _normalize_words(expected_text)
    mismatches, insertions = _count_mismatched_words(expected_words, _normalize_words(transcribed))
    if insertions >= 1:
        return True
    threshold = max(max_mismatches, round(0.08 * len(expected_words)))
    return mismatches > threshold


def _looks_spanish(text: str) -> bool:
    return bool(re.search(r"[áéíóúñ¿¡]", text.lower()))


class _AlignmentGlitchDetector:
    """
    Agrupa el chequeo barato (silencios raros) y el caro (transcripción con
    Whisper) en un solo lugar: si el chequeo barato ya detecta un glitch, no
    hace falta pagar el costo de transcribir para confirmarlo.
    """

    def __init__(self, max_word_mismatches: int):
        self.max_word_mismatches = max_word_mismatches

    def has_glitch(self, audio_path: str, expected_text: str) -> bool:
        if _detect_dead_air(audio_path):
            return True
        return _transcript_mismatch(expected_text, audio_path, self.max_word_mismatches)


def _detect_dead_air(audio_path: str, min_gap_seconds: float = 1.4) -> bool:
    """
    Detecta silencios anormalmente largos EN MEDIO del audio (no al
    principio/final, donde un poco de silencio es normal) — típico de
    cuando el modelo "se cae" un instante durante la generación. No
    depende de whisper ni de ninguna librería extra, así que funciona
    siempre, incluso sin faster-whisper instalado.
    """
    import wave
    import array

    try:
        with wave.open(audio_path, "rb") as w:
            sr = w.getframerate()
            n = w.getnframes()
            if n == 0:
                return False
            raw = w.readframes(n)
            samples = array.array("h", raw)  # PCM de 16 bits
    except Exception:
        return False

    if not samples:
        return False

    win = max(int(sr * 0.1), 1)  # ventanas de 100ms
    silence_threshold = 400  # umbral de amplitud RMS (escala de 16 bits, ±32768)
    n_windows = len(samples) // win
    if n_windows < 3:
        return False

    # Márgenes que se ignoran (silencio al principio/final es normal)
    edge_windows = max(int(0.3 / 0.1), 1)  # ~0.3s de cada lado

    silent_run = 0
    max_silent_run = 0
    for i in range(edge_windows, n_windows - edge_windows):
        chunk = samples[i * win:(i + 1) * win]
        rms = (sum(s * s for s in chunk) / len(chunk)) ** 0.5
        if rms < silence_threshold:
            silent_run += 1
            max_silent_run = max(max_silent_run, silent_run)
        else:
            silent_run = 0

    gap_seconds = max_silent_run * (win / sr)
    return gap_seconds > min_gap_seconds


def tts_chatterbox(
    text: str,
    output_path: Optional[str] = None,
    voice: str = DEFAULT_VOICE,
    language: Optional[str] = None,
    exaggeration: float = 0.5,
    cfg_weight: float = 0.8,
    temperature: float = 0.65,
    repetition_penalty: float = 1.3,
    audio_prompt_path: Optional[str] = None,
    verify_audio: bool = True,
    max_word_mismatches: int = 1,
    max_attempts: int = 5,
) -> Optional[str]:
    """
    Genera audio con Chatterbox (clonación de voz zero-shot, autoregresivo).

    Args:
        text: Texto a convertir.
        output_path: Ruta de salida (.wav). Si es None, se genera automáticamente.
        voice: Clave de la biblioteca de voces (ver VOICE_LIBRARY). Se ignora
            si se pasa audio_prompt_path explícitamente.
        language: Código de idioma, ej. 'es' (español), 'en' (inglés). Se usa
            solo para elegir el modelo de verificación por Whisper; Chatterbox
            no necesita que se le indique el idioma.
        exaggeration: Intensidad emocional/expresividad (0.0-1.0, default 0.5).
        cfg_weight: Qué tan de cerca sigue el timbre/ritmo de la voz de
            referencia (0.0-1.0, default 0.8).
        temperature: Aleatoriedad del muestreo autoregresivo (default de la
            librería 0.8, acá 0.65). Más bajo reduce la tasa de palabras
            sustituidas/alucinadas — el motor se apega más al texto pedido,
            a cambio de un poco menos de variación natural en la entonación.
        repetition_penalty: Penaliza repetir/sustituir tokens recientes
            (default de la librería 1.2, acá 1.3 — un poco más agresivo).
        audio_prompt_path: Ruta a un WAV de referencia propio (5-20s) para clonar
            una voz personalizada, en vez de usar la biblioteca (voice se ignora).
        verify_audio: Si es True (default), verifica el audio generado (silencios
            raros + transcripción con Whisper) y reintenta si algo no calza
            con el texto pedido. Es lo que compensa que Chatterbox, al ser
            autoregresivo, puede perder palabras ocasionalmente.
        max_word_mismatches: Palabras de tolerancia entre el texto pedido y la
            transcripción antes de considerar que hubo un glitch.
        max_attempts: Intentos totales (1 inicial + reintentos) antes de
            entregar el resultado igual aunque siga marcando glitch. Cada
            reintento baja `temperature` un poco (piso 0.4) para reducir el
            riesgo de alucinar.

    Returns:
        Ruta al archivo WAV generado, o None si hubo error.
    """
    try:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # noqa: F401
    except ImportError:
        print("[ERROR] Chatterbox no instalado. Ejecuta: pip install -r requirements.txt")
        return None

    # Resolver qué clip de referencia usar (voz de la biblioteca, o una personalizada)
    resolved_language = language
    if audio_prompt_path:
        resolved_prompt = audio_prompt_path
        resolved_language = resolved_language or "es"
    else:
        resolved_voice = voice if voice in VOICE_LIBRARY else DEFAULT_VOICE
        if voice and voice not in VOICE_LIBRARY:
            print(f"[AVISO] Voz '{voice}' no reconocida; se usará la voz por defecto '{DEFAULT_VOICE}'.")
        if not voice_is_ready(resolved_voice):
            bootstrap_voice(resolved_voice)
        if not voice_is_ready(resolved_voice):
            print(f"[ERROR] No se pudo preparar la voz '{resolved_voice}'.")
            return None
        resolved_prompt = str(voice_reference_path(resolved_voice))
        resolved_language = resolved_language or VOICE_LIBRARY[resolved_voice].get("lang", "es")
        voice = resolved_voice

    if output_path is None:
        timestamp = int(time.time())
        output_path = str(OUTPUT_DIR / f"audio_{timestamp}.wav")

    try:
        model = _get_chatterbox_model()
    except ImportError:
        print("[ERROR] Chatterbox no instalado. Ejecuta: pip install -r requirements.txt")
        return None
    except Exception as e:
        print(f"[ERROR] No se pudo cargar el modelo Chatterbox: {e}")
        return None

    detector = _AlignmentGlitchDetector(max_word_mismatches)
    tmp_path = f"{output_path}.tmp.wav"

    try:
        import numpy as np
        import soundfile as sf

        for attempt in range(max_attempts):
            # En reintentos (no en el primer intento) bajamos temperature
            # progresivamente: menos aleatoriedad de muestreo reduce el
            # riesgo de alucinar/sustituir palabras, a costa de algo de
            # naturalidad — un costo que solo se paga si ya hubo un fallo.
            attempt_temperature = max(temperature - 0.1 * attempt, 0.4)
            wav = model.generate(
                text,
                language_id=resolved_language,
                audio_prompt_path=resolved_prompt,
                exaggeration=exaggeration,
                cfg_weight=cfg_weight,
                temperature=attempt_temperature,
                repetition_penalty=repetition_penalty,
            )
            # PCM de 16 bits explícito: el tensor que devuelve
            # ChatterboxMultilingualTTS.generate() es float, que el módulo
            # wave de la stdlib (usado en _detect_dead_air/_concat_wav) no
            # sabe leer ("unknown format: 3"). Convertir a int16 lo deja legible.
            wav_np = wav.squeeze().cpu().numpy()
            wav_int16 = np.clip(wav_np * 32767, -32768, 32767).astype(np.int16)
            sf.write(tmp_path, wav_int16, model.sr, subtype="PCM_16")

            if verify_audio and detector.has_glitch(tmp_path, text):
                if attempt < max_attempts - 1:
                    print(f"  ⚠️  Se detectó un posible corte/glitch, reintentando ({attempt + 2}/{max_attempts})...")
                    continue
                print("  ⚠️  Glitch persistente tras los reintentos; se entrega el resultado igual.")
            break

        shutil.move(tmp_path, output_path)
        print(f"✓ Audio guardado en: {output_path}")
        return output_path
    except Exception as e:
        print(f"[ERROR] Chatterbox falló: {e}")
        return None
    finally:
        if Path(tmp_path).exists():
            Path(tmp_path).unlink()


# ─────────────────────────────────────────────
# TEXTOS LARGOS: DIVISIÓN EN FRAGMENTOS Y UNIÓN DE AUDIO
# ─────────────────────────────────────────────

# text_to_speech_long empaqueta oraciones cortas hasta PACK_CHUNK_SIZE (ver
# split_text_chunks con pack=True): generar oración por oración (pack=False)
# fragmentaba demasiado un texto con muchas oraciones cortas — decenas de
# llamadas al motor, una costura cada una. 220 caracteres agrupa ~2-4
# oraciones típicas de un cuento sin acercarse al rango donde el motor
# autoregresivo empieza a derivar/alucinar más.
PACK_CHUNK_SIZE = 220
# CHUNK_SIZE queda como techo de seguridad para partir por palabras una
# "oración" anormalmente larga (texto sin puntuación).
CHUNK_SIZE = 550


def split_text_chunks(text: str, max_len: int, pack: bool = True) -> list:
    """
    Divide un texto en fragmentos, cortando en límites de oración (. ! ? y
    saltos de línea) para no partir frases a la mitad.

    Si pack=True (default), agrupa varias oraciones consecutivas en un
    mismo fragmento hasta llenar max_len. Si pack=False, cada oración es su
    propio fragmento — max_len actúa solo como techo de seguridad para
    partir por palabras una oración anormalmente larga (ej. sin puntuación).
    Sin agrupar, cada llamada al motor genera una secuencia más corta (menos
    deriva/alucinación en modelos autoregresivos) y un reintento por glitch
    regenera solo la oración que falló, no el bloque entero.
    """
    text = text.strip()
    if not text:
        return []
    if pack and len(text) <= max_len:
        return [text]

    sentences = re.split(r"(?<=[.!?\n])\s+", text)

    chunks = []
    current = ""
    for sentence in sentences:
        # Si una sola "oración" ya es más larga que max_len, la partimos por palabras
        while len(sentence) > max_len:
            corte = sentence[:max_len].rfind(" ")
            corte = corte if corte > 0 else max_len
            chunks.append(sentence[:corte].strip())
            sentence = sentence[corte:].strip()

        if not pack:
            if sentence.strip():
                chunks.append(sentence.strip())
            continue

        if len(current) + len(sentence) + 1 <= max_len:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                chunks.append(current)
            current = sentence

    if current:
        chunks.append(current)

    return [c for c in chunks if c.strip()]


def _trim_silence(samples, threshold: int = 300, edge_padding: int = 0) -> "array.array":
    """Recorta silencio del principio y el final de un array de muestras PCM 16-bit."""
    n = len(samples)
    if n == 0:
        return samples

    start = 0
    while start < n and abs(samples[start]) < threshold:
        start += 1
    end = n
    while end > start and abs(samples[end - 1]) < threshold:
        end -= 1

    start = max(start - edge_padding, 0)
    end = min(end + edge_padding, n)
    return samples[start:end]


def _concat_wav(parts: list, output_path: str, pause_seconds: float = 0.35) -> None:
    """
    Une varios WAV en uno solo (deben compartir formato/sample rate).
    Recorta el silencio de los bordes de cada fragmento y agrega una pausa
    fija y controlada entre ellos — sin esto, algunos fragmentos quedan con
    más o menos "aire" al principio/final y la unión suena entrecortada o
    con silencios irregulares entre oraciones.
    """
    import wave
    import array

    with wave.open(parts[0], "rb") as first:
        params = first.getparams()
        sr = first.getframerate()

    pause_samples = array.array("h", [0] * int(sr * pause_seconds))

    trimmed_frames = []
    for i, p in enumerate(parts):
        with wave.open(p, "rb") as w:
            raw = w.readframes(w.getnframes())
        samples = array.array("h", raw)
        # Padding para no cortar el ataque/cola de consonantes suaves justo
        # en el borde del silencio recortado (sonaban "mordidas" al unir).
        samples = _trim_silence(samples, edge_padding=int(sr * 0.03))
        trimmed_frames.append(samples)
        if i < len(parts) - 1:
            trimmed_frames.append(pause_samples)

    with wave.open(output_path, "wb") as out:
        out.setparams(params)
        for samples in trimmed_frames:
            out.writeframes(samples.tobytes())


# ─────────────────────────────────────────────
# FUNCIONES PRINCIPALES
# ─────────────────────────────────────────────

def text_to_speech(text: str, output_path: Optional[str] = None, **kwargs) -> Optional[str]:
    """Genera audio con Chatterbox. Ver tts_chatterbox() para los argumentos disponibles."""
    print(f"\n🎙️  Chatterbox")
    print(f"📝 Texto: {text[:80]}{'...' if len(text) > 80 else ''}")
    print("-" * 50)
    return tts_chatterbox(text, output_path, **kwargs)


def text_to_speech_long(
    text: str,
    output_path: Optional[str] = None,
    chunk_size: Optional[int] = None,
    on_progress=None,
    **kwargs,
) -> Optional[str]:
    """
    Igual que text_to_speech, pero para textos largos: los divide en
    fragmentos, genera el audio de cada uno y los une en un solo archivo.
    Úsala siempre en vez de text_to_speech cuando el texto pueda ser largo;
    si el texto es corto se comporta exactamente igual (no hay costo extra).

    Args:
        on_progress: callback opcional on_progress(percent: int, message: str)
            para reportar avance real (por ejemplo, para una barra de progreso
            en una interfaz web). Es aparte de los print() normales.
    """
    def progress(pct: int, msg: str):
        if on_progress:
            on_progress(min(max(pct, 0), 100), msg)

    max_len = chunk_size or PACK_CHUNK_SIZE
    chunks = split_text_chunks(text, max_len, pack=True)

    if not chunks:
        print("[ERROR] No se proporcionó texto.")
        progress(100, "Error: no se proporcionó texto.")
        return None

    if len(chunks) == 1:
        progress(8, "Generando audio...")
        result = text_to_speech(text, output_path=output_path, **kwargs)
        progress(100, "Listo" if result else "Error al generar el audio.")
        return result

    if output_path is None:
        timestamp = int(time.time())
        output_path = str(OUTPUT_DIR / f"audio_{timestamp}.wav")

    n = len(chunks)
    print(f"\n📚 Texto largo detectado ({len(text)} caracteres): dividiendo en {n} fragmentos...")
    progress(5, f"Dividido en {n} fragmentos...")

    with tempfile.TemporaryDirectory(dir=OUTPUT_DIR) as tmp_dir:
        part_paths = []
        for i, chunk in enumerate(chunks, start=1):
            print(f"  → Fragmento {i}/{n} ({len(chunk)} caracteres)...")
            # Reservamos el último 8% para la unión final de los fragmentos
            pct = 5 + round((i - 1) / n * 87)
            progress(pct, f"Generando fragmento {i} de {n}...")

            part_path = str(Path(tmp_dir) / f"part_{i:03d}.wav")
            result = text_to_speech(chunk, output_path=part_path, **kwargs)
            if not result:
                print(f"[ERROR] Falló el fragmento {i}, se aborta la unión.")
                progress(100, f"Error al generar el fragmento {i}.")
                return None
            part_paths.append(result)

        progress(93, "Uniendo fragmentos...")
        print("🔗 Uniendo fragmentos...")
        try:
            _concat_wav(part_paths, output_path)
        except Exception as e:
            print(f"[ERROR] No se pudieron unir los fragmentos: {e}")
            progress(100, "Error al unir los fragmentos.")
            return None

    progress(100, "Listo")
    print(f"✓ Audio completo guardado en: {output_path}")
    return output_path


# ─────────────────────────────────────────────
# REPRODUCCIÓN DE AUDIO
# ─────────────────────────────────────────────

def play_audio(filepath: str) -> bool:
    """Reproduce un archivo de audio (WAV o MP3) en Windows/Linux/macOS."""
    path = Path(filepath)
    if not path.exists():
        print(f"[ERROR] Archivo no encontrado: {filepath}")
        return False

    try:
        if sys.platform == "win32":
            if filepath.lower().endswith(".wav"):
                import winsound
                winsound.PlaySound(filepath, winsound.SND_FILENAME)
            else:
                os.startfile(filepath)

        elif sys.platform == "darwin":
            subprocess.run(["afplay", filepath], check=True)

        else:  # Linux
            if filepath.endswith(".wav"):
                subprocess.run(["aplay", filepath], check=True, capture_output=True)
            else:
                for player in ["mpg123", "ffplay", "cvlc"]:
                    try:
                        cmd = ["ffplay", "-nodisp", "-autoexit", filepath] if player == "ffplay" else [player, filepath]
                        subprocess.run(cmd, check=True, capture_output=True)
                        break
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        continue

        return True
    except Exception as e:
        print(f"[AVISO] No se pudo reproducir automáticamente: {e}")
        print(f"  → Abre el archivo manualmente: {filepath}")
        return False


# ─────────────────────────────────────────────
# DEMO / USO DIRECTO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("   🎙️  TTS CON CHATTERBOX (voz clonada por acento)")
    print("=" * 55)
    print("\nVoces disponibles:")
    for key, info in VOICE_LIBRARY.items():
        estado = "✓" if voice_is_ready(key) else "↓ se genera al usar"
        print(f"  {estado}  {key}: {info['label']}")

    texto = (
        "Había una vez un viejo faro junto al mar, y un perro que esperaba "
        "cada noche a que su dueño volviera a casa."
    )
    print(f"\n--- Generando audio de demo con la voz '{DEFAULT_VOICE}' ---\n")

    resultado = text_to_speech_long(texto, voice=DEFAULT_VOICE)

    if resultado:
        print(f"\n✅ ¡Éxito! Archivo generado: {resultado}")
        play_audio(resultado)
    else:
        print("\n❌ No se pudo generar el audio.")
