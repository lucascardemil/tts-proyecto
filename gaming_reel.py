"""
Reels de noticias de videojuegos con motion graphics (pagina JugadasEpicasVideojuegos).

Una noticia real de las ultimas 2 semanas (RSS, ver gaming_news.next_reel_news) -> guion en ingles con escenas
tipadas (el modelo solo redacta con esos datos) -> narracion TTS -> palabras con Whisper -> props de la composicion
Remotion `GamingReel` (solo graficos animados, sin imagenes de juego) -> mp4 vertical en video/out.

El guion se valida contra la noticia: ninguna cifra ni cita puede salir de la nada.
"""

import colorsys
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

import gaming_news
import video_maker

logger = logging.getLogger(__name__)

FPS = 30
TAIL_SECONDS = 0.6  # cola tras la ultima palabra
SCENE_TYPES = ("hook", "stat", "versus", "timeline", "quote", "outro")
MIN_SCENES, MAX_SCENES = 6, 8
MIN_WORDS, MAX_WORDS = 90, 160  # palabras de narracion (el prompt pide 110-140, ~45 s)
MAX_SCENE_WORDS = 45
SCRIPT_ATTEMPTS = 3
DEFAULT_VOICE = "en_us_hombre"
KIND = "gaming_reel"  # prompts/gaming_reel_system.md
TRIGGER = "Write the Reel script for this news item."


class ReelError(RuntimeError):
    """El guion del reel no cumple el formato o inventa datos (reintentable: otro intento suele cumplir)."""


def reel_voice() -> str:
    """Voz inglesa del reel: GAMING_REEL_VOICE (biblioteca o 'custom:<slug>') o la de biblioteca por defecto."""
    return os.environ.get("GAMING_REEL_VOICE", "").strip() or DEFAULT_VOICE


def trending_names() -> tuple:
    """Nombres de los juegos en tendencia (Medal, con cache); vacio si no se pudo leer."""
    try:
        import gaming_clip
        return tuple(g["label"] for g in gaming_clip.list_games() if g.get("trending"))
    except Exception:
        logger.warning("gaming_reel: no se pudo leer la lista de juegos en tendencia", exc_info=True)
        return ()


# ---- validacion del guion --------------------------------------------------------------------------------------------

_SECTIONS_RE = re.compile(
    r"HOOK:\s*(.+?)\s*\n\s*TITLE:\s*(.+?)\s*\n\s*CAPTION:\s*(.+?)\s*\n\s*(SCENE\s*1\b.*)\Z", re.DOTALL | re.IGNORECASE)
_SCENE_RE = re.compile(r"^\s*SCENE\s*(\d+)\s*\|(.*)$", re.MULTILINE | re.IGNORECASE)
_VALUE_RE = re.compile(r"^([$€£]?)\s*(\d[\d,]*(?:\.\d+)?)\s*([A-Za-z%+ ]{0,12}?)$")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _alnum(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def _digits_key(number: str) -> str:
    key = number.replace(",", "")
    return key[:-2] if key.endswith(".0") else key


def _source_numbers(source: str) -> set:
    return {_digits_key(n) for n in _NUMBER_RE.findall(source or "")}


def parse_value(value: str) -> Optional[dict]:
    """'$70' / '100M' / '2.5 million' / '40%' -> {prefix, number, key, decimals, suffix}; None si no es un numero."""
    m = _VALUE_RE.match((value or "").strip())
    if not m:
        return None
    prefix, raw, suffix = m.group(1), m.group(2), m.group(3).strip()
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    return {"prefix": prefix, "number": float(raw.replace(",", "")), "key": _digits_key(raw),
            "decimals": decimals, "suffix": suffix}


def _need_known_number(parsed: Optional[dict], value: str, known: set, where: str) -> dict:
    if not parsed:
        raise ReelError(
            f"{where}: el valor '{value}' no es un numero: usa UN solo numero con digitos (p. ej. '1800', '$70', '40%'); "
            "un rango ('1200 to 1800') no vale: elige una cifra o usa una escena versus '<A> vs <B>'")
    if parsed["key"] not in known:
        raise ReelError(f"{where}: la cifra '{value}' no aparece en la noticia")
    return parsed


def _build_scene(n: int, kind: str, on_screen: str, value: str, narration: str, source: str, known: set) -> dict:
    where = f"escena {n} ({kind})"
    scene = {"type": kind, "narration": narration}
    if kind in ("hook", "outro"):
        scene["text"] = on_screen
    elif kind == "stat":
        v = _need_known_number(parse_value(value), value, known, where)
        scene.update(text=on_screen, prefix=v["prefix"], number=v["number"], decimals=v["decimals"], suffix=v["suffix"])
    elif kind == "versus":
        labels = [s.strip() for s in re.split(r"\s+vs\.?\s+", on_screen, flags=re.IGNORECASE)]
        values = [s.strip() for s in re.split(r"\s+vs\.?\s+", value, flags=re.IGNORECASE)]
        if len(labels) != 2 or len(values) != 2 or not all(labels):
            raise ReelError(f"{where}: se espera '<A> vs <B>' en el texto y en el valor")
        a = _need_known_number(parse_value(values[0]), values[0], known, where)
        b = _need_known_number(parse_value(values[1]), values[1], known, where)
        scene.update(labelA=labels[0], labelB=labels[1], valueA=a["number"], valueB=b["number"],
                     prefix=a["prefix"], suffix=a["suffix"] or b["suffix"])
    elif kind == "timeline":
        items = [s.strip() for s in on_screen.split("/") if s.strip()]
        if not 2 <= len(items) <= 4:
            raise ReelError(f"{where}: la linea de tiempo lleva de 2 a 4 pasos separados por ' / '")
        for item in items:
            for number in _NUMBER_RE.findall(item):
                if _digits_key(number) not in known:
                    raise ReelError(f"{where}: la fecha/cifra '{number}' no aparece en la noticia")
        scene["items"] = items
    elif kind == "quote":
        if len(on_screen.split()) > 20 or _alnum(on_screen) not in _alnum(source):
            raise ReelError(f"{where}: la cita no esta copiada tal cual de la noticia")
        scene.update(text=on_screen, by="" if value in ("", "-") else value)
    return scene


def parse_reel_script(text: str, source: str) -> dict:
    """Valida la respuesta del modelo contra la noticia (`source` = titular + resumen + articulo).
    Devuelve {hook, title, caption, scenes, narration}; ReelError si algo no cumple."""
    cleaned = (text or "").replace("**", "").strip()
    m = _SECTIONS_RE.search(cleaned)
    if not m:
        raise ReelError("la respuesta no viene en el formato HOOK / TITLE / CAPTION / SCENE")
    hook, title, caption, rest = (g.strip() for g in m.groups())
    known = _source_numbers(source)
    raw = []
    for number, body in _SCENE_RE.findall(rest):
        parts = [p.strip() for p in body.split("|")]
        if len(parts) != 4:
            raise ReelError(f"escena {number}: se esperan 4 campos separados por ' | '")
        kind, on_screen, value, narration = parts
        kind = kind.lower()
        if kind not in SCENE_TYPES:
            raise ReelError(f"escena {number}: tipo desconocido '{kind}'")
        if not narration or len(narration.split()) > MAX_SCENE_WORDS:
            raise ReelError(f"escena {number}: la narracion debe tener de 1 a {MAX_SCENE_WORDS} palabras")
        if not on_screen:
            raise ReelError(f"escena {number}: falta el texto en pantalla")
        raw.append((int(number), kind, on_screen, value, narration))
    if not MIN_SCENES <= len(raw) <= MAX_SCENES:
        raise ReelError(f"se esperan de {MIN_SCENES} a {MAX_SCENES} escenas y hay {len(raw)}")
    if raw[0][1] != "hook" or raw[-1][1] != "outro":
        raise ReelError("la primera escena debe ser hook y la ultima outro")
    scenes = [_build_scene(n, kind, on_screen, value, narration, source, known)
              for n, kind, on_screen, value, narration in raw]
    narration = " ".join(s["narration"] for s in scenes)
    words = len(narration.split())
    if not MIN_WORDS <= words <= MAX_WORDS:
        raise ReelError(f"la narracion tiene {words} palabras (deben ser {MIN_WORDS}-{MAX_WORDS})")
    if re.search(r"\d", narration):
        raise ReelError("la narracion no puede llevar digitos (cifras escritas con letras)")
    if len(caption.splitlines()) < 4 or "source:" not in caption.lower():
        raise ReelError("el caption debe traer titular, explicacion, pregunta, fuente y hashtags")
    return {"hook": hook, "title": title[:100], "caption": caption, "scenes": scenes, "narration": narration}


# ---- tiempos y props -------------------------------------------------------------------------------------------------

def scene_frames(scenes: list, words: list, duration: float, fps: int = FPS) -> list:
    """[(startFrame, endFrame)] por escena. Cada escena arranca en la palabra que le toca segun cuantas palabras
    de narracion lleva acumuladas (proporcional a las palabras transcritas: tolera pequenas diferencias de
    conteo); la ultima llega hasta el final del audio mas la cola."""
    counts = [len(s["narration"].split()) for s in scenes]
    total = sum(counts) or 1
    starts, acc = [0.0], 0
    for count in counts[:-1]:
        acc += count
        idx = min(len(words) - 1, round(acc / total * len(words))) if words else 0
        starts.append(round(float(words[idx]["start"]) if words else duration * acc / total, 3))
    for i in range(1, len(starts)):  # estrictamente creciente
        starts[i] = max(starts[i], starts[i - 1] + 0.5)
    ends = starts[1:] + [duration + TAIL_SECONDS]
    return [(round(s * fps), max(round(e * fps), round(s * fps) + fps)) for s, e in zip(starts, ends)]


def accent_for(seed: str) -> str:
    """Color de acento estable (hex) derivado del titular: cada noticia tiene su propia paleta."""
    hue = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:6], 16) % 360 / 360
    r, g, b = colorsys.hls_to_rgb(hue, 0.58, 0.85)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def build_props(parsed: dict, words: list, audio_src: str, duration: float, source_name: str, angle: str,
                accent: str) -> dict:
    """Props de la composicion GamingReel (ver video/src/schema.ts: gamingReelSchema)."""
    frames = scene_frames(parsed["scenes"], words, duration)
    scenes = []
    for scene, (start, end) in zip(parsed["scenes"], frames):
        item = {k: v for k, v in scene.items() if k != "narration"}
        item["startFrame"], item["endFrame"] = start, end
        scenes.append(item)
    style = {k: v for k, v in video_maker.SUBTITLE_PRESETS["hormozi"].items() if k != "label"}
    return {
        "scenes": scenes,
        "subtitles": words,
        "subtitleStyle": style,
        "accent": accent,
        "angle": angle,
        "sourceName": source_name,
        "audioSrc": audio_src,
        "totalDurationSeconds": round(duration + TAIL_SECONDS, 3),
    }


def youtube_seo(parsed: dict) -> dict:
    """Titulo, descripcion y tags de YouTube Shorts a partir del guion."""
    tags = [t.lstrip("#") for t in re.findall(r"#\w+", parsed["caption"])][:8]
    body = re.sub(r"(?:\s*#\w+)+\s*$", "", parsed["caption"]).strip()
    return {"title": parsed["title"][:95], "description": body + "\n\n#Shorts #gamingnews",
            "tags": tags + ["gaming news", "video game news"]}


# ---- render y flujo completo -----------------------------------------------------------------------------------------

def render_reel(props: dict, props_path: Path, output_path: Path) -> None:
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    cmd = [
        "npx", "remotion", "render", "src/index.ts", "GamingReel", str(output_path),
        f"--props={props_path}", "--codec=h264", "--hardware-acceleration=if-possible",
        "--video-bitrate=12M", "--pixel-format=yuv420p", "--color-space=bt709",
        "--audio-codec=aac", "--audio-bitrate=192k", "--concurrency=8", "--timeout=120000", "--log=error",
    ]
    result = subprocess.run(
        cmd, cwd=str(video_maker.VIDEO_DIR), capture_output=True, text=True, shell=(sys.platform == "win32"),
    )
    if result.returncode != 0 or not output_path.exists():
        raise ReelError(f"Remotion falló al renderizar el reel: {(result.stdout + result.stderr)[-500:]}")


def write_script(item: dict, text_fn: Callable[[str], str]) -> dict:
    """Pide el guion al modelo (text_fn(mensaje) -> respuesta) y lo valida; reintenta contando el error anterior."""
    source = " ".join([item["title"], item.get("summary", ""), item.get("body", "")])
    allowed = sorted(_source_numbers(source), key=lambda n: (len(n), n))
    base = TRIGGER + gaming_news.reel_news_block(item) + (
        "\nFIGURES you may use in stat, versus and timeline scenes (the only ones present in the data): "
        + ", ".join(allowed) + ". Any other figure is rejected." if allowed
        else "\nThe data contains NO figures: do NOT use stat or versus scenes, and no numbers in timeline scenes.")
    problem = ""
    for attempt in range(1, SCRIPT_ATTEMPTS + 1):
        message = base + (f"\n\nYour previous answer was rejected: {problem}. Fix exactly that." if problem else "")
        try:
            return parse_reel_script(text_fn(message), source)
        except ReelError as e:
            problem = str(e)
            logger.warning("gaming_reel: guion rechazado (intento %d/%d): %s", attempt, SCRIPT_ATTEMPTS, e)
    raise ReelError(f"Guion de reel inválido tras {SCRIPT_ATTEMPTS} intentos: {problem}")


def generate_gaming_reel(job_key: str, text_fn: Callable[[str], str],
                         on_stage: Optional[Callable[[str], None]] = None) -> dict:
    """Noticia -> guion -> voz -> subtitulos -> render. Devuelve
    {video_path, title, caption, hook, news_link, youtube} (como generate_gaming_clip). La noticia solo se marca
    como usada si el reel sale completo; la carpeta de trabajo se borra siempre."""
    import tts_engine

    def stage(name: str) -> None:
        if on_stage:
            on_stage(name)

    folder = video_maker.VIDEO_PUBLIC_DIR / f"greel_{job_key}"
    out_path = video_maker.VIDEO_OUT_DIR / f"greel_{job_key}.mp4"
    try:
        stage("noticia")
        item = gaming_news.next_reel_news(trending_names())
        stage("guion")
        parsed = write_script(item, text_fn)

        stage("voz")
        audio = tts_engine.text_to_speech_long(parsed["narration"], voice=reel_voice(), language="en")
        if not audio:
            raise ReelError("Error al generar el audio del reel.")
        duration = video_maker.get_audio_duration(audio)

        stage("subtitulos")
        words = video_maker.correct_words_with_script(
            video_maker.transcribe_words(audio, language="en"), parsed["narration"])
        folder.mkdir(parents=True, exist_ok=True)
        if not video_maker.normalize_audio(audio, folder / "narration.wav"):
            shutil.copy(audio, folder / "narration.wav")

        stage("render")
        props = build_props(parsed, words, f"greel_{job_key}/narration.wav", duration, item["source"],
                            item["angle"], accent_for(item["title"]))
        video_maker.VIDEO_OUT_DIR.mkdir(parents=True, exist_ok=True)
        render_reel(props, folder / "props.json", out_path)
        gaming_news.mark_used(item)
        return {"video_path": str(out_path), "title": parsed["title"], "caption": parsed["caption"],
                "hook": parsed["hook"], "news_link": item["link"], "youtube": youtube_seo(parsed)}
    finally:
        shutil.rmtree(folder, ignore_errors=True)
