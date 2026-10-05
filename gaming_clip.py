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

# ── Juegos en tendencia ────────────────────────────────────────────────────
# Cada vez que se pide un clip (game_for_index) y al abrir los selectores se refresca la lista
# con "Lo mas popular en Medal" (medal.tv/es/games, ya ordenada por popularidad): los juegos de
# arriba se suman a GAMES y encabezan la rotacion "mix". Los fijos de arriba (static) nunca se
# quitan. Se cachea en disco TRENDING_TTL_SECONDS para no leer Medal en cada clip de un lote.
TRENDING_STORE = "gaming_trending_games"
TRENDING_TTL_SECONDS = 3600
TRENDING_LIMIT = 15
_STATIC_GAMES = dict(GAMES)
_STATIC_ROTATION = list(GAME_ROTATION)
_games_lock = threading.Lock()
# Sin kill-feed que verificar fuera de estos: el resto se trata como juego sin kills ("¡BRUTAL!").
_KILL_GAME_HINTS = (
    "counter-strike", "valorant", "fortnite", "apex", "call-of-duty", "overwatch", "r6-siege", "deadlock",
    "marvel-rivals", "helldivers", "war-thunder", "wardogs", "rust", "pubg", "battlefield", "warzone",
    "halo", "destiny",
)


def _game_from_trending(entry: dict) -> dict:
    slug, name = entry["slug"], entry["name"]
    compact = re.sub(r"[^a-z0-9]", "", name.lower()) or re.sub(r"[^a-z0-9]", "", slug)
    return {
        "label": name, "slug": slug, "short": name.upper()[:18],
        "kills": any(h in slug for h in _KILL_GAME_HINTS),
        "tags": [f"#{compact}", f"#{compact}clips", "#jugadasepicas", "#gaming", "#viral", "#gamer", "#fyp", "#clips"],
    }


def _fetch_trending() -> list:
    """[{slug, name}] de la seccion "Lo mas popular en Medal", en orden de popularidad."""
    import html as _html
    page = _get(f"{MEDAL_BASE}/es/games").text
    head = re.search(r"<h2[^>]*>\s*Lo m[aá]s popular en Medal\s*</h2>", page)
    if not head:
        raise ClipError("Medal cambió su página de juegos: no encuentro 'Lo más popular'.")
    section = page[head.end():]
    nxt = re.search(r"<h2", section)
    section = section[:nxt.start()] if nxt else section
    entries = []
    for slug, inner in re.findall(r'<a href="/es/games/([a-z0-9\-]+)"[^>]*>(.*?)</a>', section, re.S):
        alt = re.search(r'alt="([^"]*?)(?: game clips)?"', inner)
        name = _html.unescape(alt.group(1)).strip() if alt else ""
        if name and "studio" not in slug:  # FL Studio / Roblox Studio no son juegos
            entries.append({"slug": slug, "name": name})
    if len(entries) < 3:
        raise ClipError("Medal no devolvió juegos en tendencia.")
    return entries


def _apply_trending(entries: list) -> None:
    """Reemplaza los juegos dinamicos de GAMES y reordena GAME_ROTATION (en el lugar:
    otros modulos importan estos objetos)."""
    with _games_lock:
        for key in [k for k in GAMES if k not in _STATIC_GAMES]:
            del GAMES[key]
        by_slug = {g["slug"]: k for k, g in GAMES.items()}
        order = []
        for e in entries[:TRENDING_LIMIT]:
            key = by_slug.get(e["slug"])
            if key is None:
                key = e["slug"]
                GAMES[key] = _game_from_trending(e)
                by_slug[e["slug"]] = key
            if key not in order:
                order.append(key)
        GAME_ROTATION[:] = order + [k for k in _STATIC_ROTATION if k not in order]


def refresh_games(force: bool = False) -> bool:
    """Actualiza la lista de juegos con los mas populares de Medal. True si leyo Medal.
    Nunca falla: sin red se queda con la ultima lista guardada (o la fija)."""
    store = job_store.load(TRENDING_STORE)
    if not force and time.time() - store.get("updated", 0) < TRENDING_TTL_SECONDS:
        return False
    try:
        entries = _fetch_trending()
    except Exception as e:
        logger.warning("gaming_clip: no se pudo actualizar la lista de juegos (%s); se usa la ultima guardada", e)
        return False
    job_store.save(TRENDING_STORE, {"updated": time.time(), "entries": entries})
    _apply_trending(entries)
    logger.info("gaming_clip: juegos en tendencia actualizados: %s", ", ".join(e["slug"] for e in entries[:TRENDING_LIMIT]))
    return True


def list_games() -> list:
    """Juegos para los selectores, en orden de rotacion: [{key, label, trending}]."""
    refresh_games()
    trending = {e["slug"] for e in job_store.load(TRENDING_STORE).get("entries", [])}
    with _games_lock:
        return [{"key": k, "label": GAMES[k]["label"], "trending": GAMES[k]["slug"] in trending} for k in GAME_ROTATION if k in GAMES]


_cached = job_store.load(TRENDING_STORE).get("entries")
if _cached:
    _apply_trending(_cached)

HOOKS = [
    "JUGADA DE LOCOS", "NO PUEDE SER REAL", "MIRÁ HASTA EL FINAL", "ESTO ES ILEGAL",
    "NADIE LO VIO VENIR", "¿CÓMO LO HIZO?", "ESTO NO ES NORMAL", "ÉPICO O SUERTE?",
]
# Textos EN PANTALLA del video (en ingles, misma posicion que HOOKS): el caption/titulo de
# Facebook e Instagram siguen en español.
HOOKS_EN = [
    "INSANE PLAY", "THIS CAN'T BE REAL", "WATCH TILL THE END", "THIS IS ILLEGAL",
    "NOBODY SAW IT COMING", "HOW DID HE DO THAT?", "THIS ISN'T NORMAL", "EPIC OR LUCKY?",
]
CTAS = ["LUCK OR SKILL? 👇", "COULD YOU DO IT? 👇", "RATE IT 1 TO 10 👇", "HOW INSANE WAS THAT? 👇"]
BEAT_LABELS = ("🔥", "INSANE!")  # momentos intermedios / el ultimo, el grande

SFX_NAMES = ("whoosh_in", "whoosh_out", "hit", "ace", "pop", "ding")


class ClipError(RuntimeError):
    pass


def game_for_index(game_key: str, index: int) -> dict:
    """Juego de un lote: el fijo, o rota por indice si es "mix". Cada pedido de clip
    refresca antes la lista con los juegos en tendencia (ver refresh_games)."""
    refresh_games()
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
    screen_hook = HOOKS_EN[HOOKS.index(hook)]
    cta = rng.choice(CTAS)
    author = clip.get("author") or "su autor"
    caption = (
        f"{hook.capitalize()} 🔥\n\n¿Suerte o skill? Contame en los comentarios 👇\n\n"
        f"🎮 {game['label']}\n📹 Clip: @{author} (Medal)\n\n" + " ".join(game["tags"])
    )
    words = screen_hook.split(" ")
    return {
        "hook": screen_hook,
        "hook_highlight": [len(words) - 1],
        "tag": f"{game['short']} · EPIC PLAY",
        "cta": cta,
        "title": f"{hook.capitalize()} 🎮 {game['label']}"[:100],
        "caption": caption,
    }


# ── YouTube: SEO agresivo, en ingles ───────────────────────────────────────
# Facebook/Instagram llevan un caption corto y de gancho; YouTube es un buscador, asi que su
# titulo, descripcion y tags son otros, cargados de palabras clave (juego + "best moments /
# epic plays / highlights" + año), y SIEMPRE en ingles (audiencia global del canal).
# Limites de YouTube: titulo 100 caracteres, descripcion 5000, tags 500 en total (los que llevan
# espacios cuentan 2 mas por las comillas), y mas de 15 hashtags en la descripcion hace que
# YouTube los ignore todos.
YT_TITLE_MAX = 100
YT_DESCRIPTION_MAX = 4800
YT_TAGS_MAX_CHARS = 450
YT_MAX_HASHTAGS = 15
YT_LANGUAGE = "en"
_YT_HOOKS = ("INSANE PLAY", "NO WAY HE DID THAT", "WATCH TILL THE END", "LUCK OR SKILL?", "THIS IS CRAZY", "WAIT FOR IT")
_YT_TITLE_TEMPLATES = (
    "{hook} 😱 {game} Best Moments & Epic Plays {year} #Shorts",
    "{game} Epic Clip 🔥 {hook} | Best Plays {year} #Shorts",
    "{hook} 🤯 {game} Gameplay Highlights {year} #Shorts",
    "{game}: {hook} 🎮 Funniest & Craziest Clips {year} #Shorts",
)


def _yt_hashtag(text: str) -> str:
    return "#" + re.sub(r"[^a-z0-9]", "", text.lower())


def build_youtube_seo(clip: dict, game: dict, hook: Optional[str] = None, summary: Optional[str] = None,
                      title: Optional[str] = None) -> dict:
    """Titulo, descripcion y tags de YouTube (en ingles) para el clip: distintos de
    Facebook/Instagram y pensados para posicionar. Deterministas por clip (misma variante
    si se reintenta). `hook`/`summary`/`title` (en ingles) reemplazan al gancho aleatorio, a la frase
    de apertura y al titulo de plantilla: sirven para reescribir videos que ya estan publicados."""
    rng = random.Random(clip["id"])
    year = datetime.now().year
    name = game["label"]
    tag = _yt_hashtag(name)
    hook = hook or rng.choice(_YT_HOOKS)

    custom_title = title
    template = rng.choice(_YT_TITLE_TEMPLATES)
    title = template.format(hook=hook, game=name, year=year)
    if len(title) > YT_TITLE_MAX:  # nombres de juego largos: se saca el gancho antes que el juego o #Shorts
        title = template.replace("{hook} ", "").replace(" {hook}", "").replace("{hook}", "").format(game=name, year=year)
    if custom_title:
        room = YT_TITLE_MAX - len(" #Shorts")
        cut = custom_title if len(custom_title) <= room else custom_title[:room].rsplit(" ", 1)[0]
        title = f"{cut} #Shorts"
    title = title[:YT_TITLE_MAX].rstrip()

    hashtags = list(dict.fromkeys([
        "#Shorts", tag, f"{tag}clips", f"{tag}highlights", "#gamingshorts", "#epicplays", "#gaming",
        "#videogames", "#gamer", "#gameplay", "#gamingclips", "#bestmoments", "#viral", "#epicmoments", "#fyp",
    ]))[:YT_MAX_HASHTAGS]

    keywords = [
        f"{name} clips", f"{name} best moments", f"{name} highlights", f"best {name} plays",
        f"{name} epic moments", f"{name} gameplay", f"{name} {year}", f"{name} funny moments",
    ]
    author = clip.get("author") or ""
    credit = ""
    if clip.get("page_url"):
        credit = "📹 Original clip on Medal" + (f": @{author}" if author else "") + f" — {clip['page_url']}\n"
    opening = summary + "\n\n" if summary else (
        f"{hook} 🔥 Watch this epic {name} play: one of the best clips and moments of {name} {year}. "
        f"Luck or skill? Let me know in the comments! 👇\n\n"
    )
    description = (
        opening +
        f"🎮 Game: {name}\n{credit}"
        f"🔔 Subscribe to JugadasEpicasVideojuegos for the best gaming plays, viral clips and epic moments "
        f"every day.\n\n"
        f"🔎 On this channel you'll find:\n"
        f"• Best {name} plays and highlights {year}\n"
        f"• Epic clips and viral moments from {name}\n"
        f"• Insane plays, clutches and funny gaming moments\n"
        f"• New gaming shorts every day\n\n"
        f"🔑 Keywords: {', '.join(keywords)}, epic plays, video game clips, gaming shorts, best gaming moments.\n\n"
        + " ".join(hashtags)
    )[:YT_DESCRIPTION_MAX]

    tags, total = [], 0
    for t in dict.fromkeys(keywords + [
        name, "epic plays", "epic clips", "video games", "gaming", "gamer", "shorts", "gaming shorts",
        "viral clips", "best gaming moments", "epic gaming moments", "gameplay",
        "insane plays", "best video game plays", "gaming highlights", "clutch", "funny moments",
    ]):
        cost = len(t) + 1 + (2 if " " in t else 0)  # YouTube cuenta entre comillas los tags con espacios
        if total + cost > YT_TAGS_MAX_CHARS:
            break
        tags.append(t)
        total += cost
    return {"title": title, "description": description, "tags": tags}


# ── Render ─────────────────────────────────────────────────────────────────

# Voz del clip -> subtitulos. El audio de un clip de juego trae disparos, musica y ruido: Whisper
# alucina texto sobre eso, asi que solo se conserva lo que el VAD marca como voz y supera
# umbrales de confianza; con muy pocas palabras se asume que no habla nadie.
SPEECH_MIN_WORD_PROB = 0.45
SPEECH_MAX_NO_SPEECH_PROB = 0.6
SPEECH_MIN_AVG_LOGPROB = -1.0
SPEECH_MAX_COMPRESSION = 2.4
SPEECH_MIN_LANGUAGE_PROB = 0.5
SPEECH_MIN_WORDS = 3
_WHISPER_BOILERPLATE = ("amara.org", "subtítulos", "subtitulos", "subtitles by", "thanks for watching")


def transcribe_speech(path: Path) -> list:
    """Palabras que se OYEN hablar en el clip: [{word, start, end}] en segundos del clip.
    Lista vacia si no habla nadie (o si lo detectado parece ruido)."""
    model = video_maker._get_whisper_model()
    segments, info = model.transcribe(
        str(path), word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 150},
        condition_on_previous_text=False,
    )
    words = []
    for seg in segments:
        text = (seg.text or "").lower()
        if (seg.no_speech_prob > SPEECH_MAX_NO_SPEECH_PROB or seg.avg_logprob < SPEECH_MIN_AVG_LOGPROB
                or seg.compression_ratio > SPEECH_MAX_COMPRESSION or any(b in text for b in _WHISPER_BOILERPLATE)):
            continue
        for w in seg.words or []:
            token = w.word.strip()
            if token and w.probability >= SPEECH_MIN_WORD_PROB:
                words.append({"word": token, "start": round(w.start, 3), "end": round(w.end, 3)})
    if len(words) < SPEECH_MIN_WORDS or info.language_probability < SPEECH_MIN_LANGUAGE_PROB:
        return []
    return words


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

    stage("subtitulos")
    try:
        subtitles = transcribe_speech(src_path)
    except Exception:  # los subtitulos son un extra: si Whisper falla el clip sale igual, sin ellos
        logger.exception("gaming_clip %s: no se pudo transcribir el clip, se edita sin subtitulos", job_key)
        subtitles = []
    logger.info("gaming_clip %s: %d palabras de voz detectadas", job_key, len(subtitles))

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
        "subtitles": subtitles,
    }
    output_path = video_maker.VIDEO_OUT_DIR / f"gclip_{job_key}.mp4"
    render_clip(props, src_dir / "props.json", output_path)
    _mark_used(clip["id"])
    shutil.rmtree(src_dir, ignore_errors=True)  # el clip fuente ya esta dentro del render
    return {
        "video_path": str(output_path), "title": texts["title"], "caption": texts["caption"],
        "hook": texts["hook"], "clip": clip,
        "youtube": build_youtube_seo(clip, game),
        "verified_events": [
            {"time": e.time, "kind": e.kind, "confidence": e.confidence, "label": e.label, "source": e.source}
            for e in events
        ],
    }
