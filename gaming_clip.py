"""
Publicacion "clip viral de gaming" (pagina JugadasEpicasVideojuegos): elige un
clip trending de Medal, lo descarga y lo edita en vertical con la composicion
ClipEdit de Remotion (fondo desenfocado, hook, slow-mo en los momentos fuertes,
zoom/flash/shake y efectos de sonido).

Sin dependencias nuevas: Medal se lee con `requests` (el listado de un juego y
el JSON-LD de cada clip traen titulo, autor, vistas, fecha y la URL del mp4).
Los momentos fuertes se detectan por picos de audio (disparos, explosiones),
asi que el rotulo es generico ("¡BRUTAL!"), nunca un conteo de kills que pueda
ser falso.

El clip original es de otra persona: el video lleva su credito en pantalla y
en el caption, pero publicarlo sin su permiso es decision del usuario.
"""

import hashlib
import json
import logging
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import requests

import gaming_vision
import job_store
import video_maker

logger = logging.getLogger(__name__)

MEDAL_BASE = "https://medal.tv"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "es",
}
LISTING_CANDIDATES = 14  # clips del listado que se consultan para rankear
MIN_SECONDS, MAX_SECONDS = 8, 35
MAX_AGE_DAYS = 30
MAX_BEATS = 4
HISTORY_STORE_NAME = "gaming_clip_history"
HISTORY_MAX = 200

# slug de Medal, nombre corto para el tag del video, si tiene kills y hashtags
# (los mas relevantes primero: Instagram deja 8, Facebook 3).
GAMES = {
    "cs2": {
        "label": "Counter-Strike 2", "slug": "counter-strike-2", "short": "CS2", "kills": True,
        "tags": ["#cs2", "#counterstrike2", "#cs2clips", "#jugadasepicas", "#gaming", "#fps", "#viral", "#gamer"],
    },
    "minecraft": {
        "label": "Minecraft", "slug": "minecraft", "short": "MINECRAFT", "kills": False,
        "tags": ["#minecraft", "#minecraftpvp", "#minecraftclips", "#jugadasepicas", "#gaming", "#viral", "#gamer", "#fyp"],
    },
    "valorant": {
        "label": "Valorant", "slug": "valorant", "short": "VALORANT", "kills": True,
        "tags": ["#valorant", "#valorantclips", "#valorantace", "#jugadasepicas", "#gaming", "#fps", "#viral", "#gamer"],
    },
    "roblox": {
        "label": "Roblox", "slug": "roblox", "short": "ROBLOX", "kills": False,
        "tags": ["#roblox", "#robloxclips", "#robloxedit", "#jugadasepicas", "#gaming", "#viral", "#gamer", "#fyp"],
    },
    "fortnite": {
        "label": "Fortnite", "slug": "fortnite", "short": "FORTNITE", "kills": True,
        "tags": ["#fortnite", "#fortniteclips", "#fortnitebr", "#jugadasepicas", "#gaming", "#viral", "#gamer", "#fyp"],
    },
    "marvel-rivals": {
        "label": "Marvel Rivals", "slug": "marvel-rivals", "short": "MARVEL RIVALS", "kills": True,
        "tags": ["#marvelrivals", "#marvelrivalsclips", "#jugadasepicas", "#gaming", "#viral", "#gamer", "#fyp", "#clips"],
    },
    "peak": {
        "label": "PEAK", "slug": "peak", "short": "PEAK", "kills": False,
        "tags": ["#peak", "#peakgame", "#peakclips", "#jugadasepicas", "#gaming", "#viral", "#gamer", "#fyp"],
    },
}
# Orden de rotacion cuando el lote no fija un juego ("mix"): los de mejor
# rendimiento en Medal primero.
GAME_ROTATION = ["cs2", "minecraft", "valorant", "roblox", "fortnite", "marvel-rivals", "peak"]

HOOKS = [
    "JUGADA DE LOCOS", "NO PUEDE SER REAL", "MIRÁ HASTA EL FINAL", "ESTO ES ILEGAL",
    "NADIE LO VIO VENIR", "¿CÓMO LO HIZO?", "ESTO NO ES NORMAL", "ÉPICO O SUERTE?",
]
CTAS = ["¿SUERTE O SKILL? 👇", "¿LA HARÍAS? 👇", "TU NOTA DEL 1 AL 10 👇", "¿QUÉ TAN BRUTAL? 👇"]
BEAT_LABELS = ("🔥", "¡BRUTAL!")  # momentos intermedios / el ultimo, el grande

SFX_NAMES = ("whoosh_in", "whoosh_out", "hit", "ace", "pop", "ding")


class ClipError(RuntimeError):
    pass


def game_for_index(game_key: str, index: int) -> dict:
    """Juego de un lote: el fijo, o rota por indice si es "mix"."""
    key = GAME_ROTATION[index % len(GAME_ROTATION)] if game_key == "mix" else game_key
    if key not in GAMES:
        raise ClipError(f"Juego desconocido: {game_key}")
    return {"key": key, **GAMES[key]}


# ── Medal ──────────────────────────────────────────────────────────────────

_LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?([\d.]+)S")


def _get(url: str, **kwargs) -> requests.Response:
    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30, **kwargs)
            if resp.status_code == 200:
                return resp
            last = ClipError(f"HTTP {resp.status_code} en {url}")
        except requests.RequestException as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise ClipError(f"No se pudo leer Medal ({url}): {last}")


def _parse_clip_page(html: str, page_url: str) -> Optional[dict]:
    for block in _LD_RE.findall(html):
        try:
            ld = json.loads(block)
        except ValueError:
            continue
        if ld.get("@type") != "VideoObject" or not ld.get("contentUrl"):
            continue
        dur = _DUR_RE.search(ld.get("duration", ""))
        if not dur:
            return None
        seconds = int(dur.group(1) or 0) * 3600 + int(dur.group(2) or 0) * 60 + float(dur.group(3))
        views = next(
            (s.get("userInteractionCount", 0) for s in ld.get("interactionStatistic", [])
             if str(s.get("interactionType", "")).endswith("WatchAction")),
            0,
        )
        age_days = max(
            (datetime.now(timezone.utc) - datetime.fromisoformat(ld["uploadDate"].replace("Z", "+00:00"))).total_seconds() / 86400,
            0.1,
        )
        author = ld.get("author") or {}
        return {
            "id": page_url.rstrip("/").split("/")[-1],
            "title": ld.get("name", ""),
            "author": author.get("name", ""),
            "author_url": author.get("url", ""),
            "views": int(views),
            "age_days": age_days,
            "views_per_day": views / age_days,
            "seconds": seconds,
            "content_url": ld["contentUrl"],
            "page_url": page_url,
        }
    return None


def _clip_from_url(url: str) -> dict:
    clip = _parse_clip_page(_get(url).text, url)
    if not clip:
        raise ClipError("No pude leer ese clip de Medal (¿es un link de clip público?).")
    return clip


def _used_ids() -> set:
    return set(job_store.load(HISTORY_STORE_NAME).get("used", []))


_history_lock = threading.Lock()


def _mark_used(clip_id: str) -> None:
    with _history_lock:
        store = job_store.load(HISTORY_STORE_NAME)
        store["used"] = (store.get("used", []) + [clip_id])[-HISTORY_MAX:]
        job_store.save(HISTORY_STORE_NAME, store)


def pick_clip(game: dict, exclude: Optional[set] = None) -> dict:
    """El clip con mas vistas/dia del listado de Medal del juego, dentro del
    rango de duracion y edad, que no se haya usado antes."""
    slug = game["slug"]
    listing = _get(f"{MEDAL_BASE}/es/games/{slug}").text
    hrefs = list(dict.fromkeys(re.findall(rf'href="(/es/games/{re.escape(slug)}/clips/[^"]+)"', listing)))
    if not hrefs:
        raise ClipError(f"Medal no devolvió clips para {game['label']}.")
    used = (exclude or set()) | _used_ids()

    def fetch(href: str) -> Optional[dict]:
        try:
            return _parse_clip_page(_get(MEDAL_BASE + href).text, MEDAL_BASE + href)
        except ClipError:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        clips = [c for c in pool.map(fetch, hrefs[:LISTING_CANDIDATES]) if c]
    fits = [
        c for c in clips
        if MIN_SECONDS <= c["seconds"] <= MAX_SECONDS and c["age_days"] <= MAX_AGE_DAYS and c["id"] not in used
    ]
    if not fits:
        raise ClipError(f"No encontré un clip nuevo y adecuado de {game['label']} en Medal.")
    return max(fits, key=lambda c: c["views_per_day"])


def download_clip(clip: dict, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = _get(clip["content_url"], stream=True)
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(1 << 20):
            f.write(chunk)
    if dest.stat().st_size < 50_000:
        raise ClipError("El clip descargado está vacío o incompleto.")


# ── Momentos fuertes (audio) ───────────────────────────────────────────────

def probe_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise ClipError("No pude leer la duración del clip (¿ffprobe instalado?).")


def detect_beats(path: Path, seconds: float, max_beats: int = MAX_BEATS) -> list:
    """Segundos de los picos de audio mas fuertes y separados (hasta max_beats),
    dejando margen para la ventana de slow-mo de cada uno. Si el clip no tiene
    audio util, un unico momento hacia el 60%."""
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True,
    ).stdout
    hop = 800  # 50 ms
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    fallback = [round(seconds * 0.6, 2)]
    if len(samples) < hop * 10:
        return fallback
    rms = np.sqrt(np.mean(samples[: len(samples) // hop * hop].reshape(-1, hop) ** 2, axis=1))
    floor = max(float(np.median(rms)) * 1.8, float(rms.max()) * 0.75)
    lo, hi = 0.4, seconds - 0.9  # ventanas: -0.3 s antes .. +0.8 s despues
    cands = [
        (float(rms[i]), i * 0.05)
        for i in range(len(rms))
        if rms[i] >= floor and lo <= i * 0.05 <= hi and rms[i] == rms[max(0, i - 10): i + 11].max()
    ]
    beats: list = []
    for _, t in sorted(cands, reverse=True):
        if all(abs(t - b) >= 1.4 for b in beats):
            beats.append(t)
        if len(beats) == max_beats:
            break
    return [round(b, 2) for b in sorted(beats)] or fallback


# ── Textos ─────────────────────────────────────────────────────────────────

def build_texts(clip: dict, game: dict) -> dict:
    rng = random.Random(clip["id"])  # mismo clip -> mismos textos
    hook = rng.choice(HOOKS)
    cta = rng.choice(CTAS)
    author = clip.get("author") or "su autor"
    caption = (
        f"{hook.capitalize()} 🔥\n\n¿Suerte o skill? Contame en los comentarios 👇\n\n"
        f"🎮 {game['label']}\n📹 Clip: @{author} (Medal)\n\n" + " ".join(game["tags"])
    )
    words = hook.split(" ")
    return {
        "hook": hook,
        "hook_highlight": [len(words) - 1],
        "tag": f"{game['short']} · JUGADA ÉPICA",
        "cta": cta,
        "title": f"{hook.capitalize()} 🎮 {game['label']}"[:100],
        "caption": caption,
    }


# ── Render ─────────────────────────────────────────────────────────────────

def ensure_sfx() -> None:
    sfx_dir = video_maker.VIDEO_PUBLIC_DIR / "sfx"
    if all((sfx_dir / f"{n}.wav").exists() for n in SFX_NAMES):
        return
    script = video_maker.VIDEO_DIR / "scripts" / "gen_sfx.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    if result.returncode != 0:
        raise ClipError(f"No se pudieron generar los efectos de sonido: {result.stderr[-300:]}")


def render_clip(props: dict, props_path: Path, output_path: Path) -> None:
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    # nvenc no es un --codec del CLI: se pide h264 y Remotion usa la GPU si
    # puede (if-possible; cae a libx264 si no). Con encoder por hardware no se
    # admite --crf, asi que la calidad va por bitrate.
    cmd = [
        "npx", "remotion", "render", "src/index.ts", "ClipEdit", str(output_path),
        f"--props={props_path}", "--codec=h264", "--hardware-acceleration=if-possible",
        "--video-bitrate=12M", "--pixel-format=yuv420p", "--color-space=bt709",
        "--audio-codec=aac", "--audio-bitrate=192k", "--concurrency=8", "--timeout=120000", "--log=error",
    ]
    result = subprocess.run(
        cmd, cwd=str(video_maker.VIDEO_DIR), capture_output=True, text=True, shell=(sys.platform == "win32"),
    )
    if result.returncode != 0 or not output_path.exists():
        raise ClipError(f"Remotion falló al renderizar el clip: {(result.stdout + result.stderr)[-500:]}")


def generate_gaming_clip(
    game: dict,
    job_key: str,
    medal_url: Optional[str] = None,
    on_stage: Optional[Callable[[str], None]] = None,
) -> dict:
    """Ver _generate_gaming_clip. Ante cualquier fallo borra la carpeta fuente
    (video/public/gclip_<job_key>) para no dejar clips huerfanos."""
    try:
        return _generate_gaming_clip(game, job_key, medal_url, on_stage)
    except BaseException:
        shutil.rmtree(video_maker.VIDEO_PUBLIC_DIR / f"gclip_{job_key}", ignore_errors=True)
        raise


def _generate_gaming_clip(
    game: dict,
    job_key: str,
    medal_url: Optional[str],
    on_stage: Optional[Callable[[str], None]],
) -> dict:
    """Clip de Medal -> video vertical editado en video/out/gclip_<job_key>.mp4.
    Devuelve {video_path, title, caption, hook, clip}. `medal_url` fija un clip
    concreto en vez de elegir el mejor del juego. El clip solo queda marcado
    como usado si el render sale bien.

    Los momentos fuertes (`kills`) ya no son solo picos de audio: cada pico
    se verifica con Gemini (gaming_vision.verify_clip) leyendo el kill-feed
    real, y se suma un barrido visual que encuentra kills que el audio no
    haya generado un pico suficiente (p.ej. una pelea que sigue justo hasta
    que el clip de Medal se corta). Si ningún momento se puede confirmar
    visualmente, el clip se descarta (no se edita con efectos que no
    corresponden a nada real) y, si no vino un `medal_url` explícito, se
    prueba con otro clip del mismo juego."""
    def stage(name: str) -> None:
        if on_stage:
            on_stage(name)

    tried_ids: set = set()
    max_attempts = 1 if medal_url else 3
    clip = events = seconds = src_dir = src_path = folder = None

    for attempt in range(max_attempts):
        stage("clip")
        clip = _clip_from_url(medal_url) if medal_url else pick_clip(game, exclude=tried_ids)
        tried_ids.add(clip["id"])

        stage("descarga")
        folder = f"gclip_{job_key}"
        src_dir = video_maker.VIDEO_PUBLIC_DIR / folder
        src_path = src_dir / "clip.mp4"
        download_clip(clip, src_path)
        seconds = probe_seconds(src_path)
        beats = detect_beats(src_path, seconds)

        stage("verificacion")
        try:
            events = gaming_vision.verify_clip(src_path, seconds, beats, work_dir=src_dir / "vision")
        except gaming_vision.QuotaExceededError as e:
            # Cuota de Gemini agotada: reintentar con otro clip solo quema mas
            # llamadas contra la misma cuota agotada. Se propaga tal cual para
            # que batch_pipeline la reconozca (ver _is_healable_error) y la
            # trate con el cooldown largo, no como un timeout de red de segundos.
            shutil.rmtree(src_dir, ignore_errors=True)
            raise ClipError(f"cuota de Gemini agotada: {e}") from e
        if events:
            break
        logger.info(
            "gaming_clip %s: clip %s descartado (Gemini no confirmó ningún momento real)",
            job_key, clip["id"],
        )
        shutil.rmtree(src_dir, ignore_errors=True)
        if medal_url:
            raise ClipError("Ese clip de Medal no tiene ningún momento verificable (kill/acción real).")
    else:
        raise ClipError(
            f"No encontré un clip de {game['label']} con un momento real verificable "
            f"tras {max_attempts} intentos."
        )

    # Cap a MAX_BEATS: si hay mas eventos confirmados que los que la
    # composicion soporta bien, se quedan los de mayor confianza.
    if len(events) > MAX_BEATS:
        events = sorted(events, key=lambda e: e.confidence, reverse=True)[:MAX_BEATS]
        events.sort(key=lambda e: e.time)

    texts = build_texts(clip, game)

    stage("render")
    ensure_sfx()
    kills = [e.time for e in events]
    labels = [e.label for e in events]
    props = {
        "src": f"{folder}/clip.mp4",
        "hook": texts["hook"],
        "hookHighlight": texts["hook_highlight"],
        "tag": texts["tag"],
        "credit": f"clip: @{clip['author']} (Medal)" if clip.get("author") else "clip: Medal",
        "cta": texts["cta"],
        "kills": kills,
        "clipSeconds": round(seconds - 0.05, 2),
        "focusX": 50,
        "labels": labels,
        "counter": False,
    }
    output_path = video_maker.VIDEO_OUT_DIR / f"gclip_{job_key}.mp4"
    render_clip(props, src_dir / "props.json", output_path)
    _mark_used(clip["id"])
    shutil.rmtree(src_dir, ignore_errors=True)  # el clip fuente ya esta dentro del render
    return {
        "video_path": str(output_path), "title": texts["title"], "caption": texts["caption"],
        "hook": texts["hook"], "clip": clip,
        "verified_events": [
            {"time": e.time, "kind": e.kind, "confidence": e.confidence, "label": e.label, "source": e.source}
            for e in events
        ],
    }
