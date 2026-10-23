# -*- coding: utf-8 -*-
"""Memes de videojuegos de Memedroid para JUGADASEPICASVIDEOJUEGOS.

Lee las galerias publicas de es.memedroid.com (etiquetas de videojuegos/consolas/juegos y los mejores de la
semana/dia; sin paginar: su robots.txt bloquea `?page=`), se queda con memes de imagen bien votados que no se
hayan publicado: primero los de la ultima semana y, si no queda ninguno, hacia atras por tramos (30 y 90 dias), descarga la imagen y arma el caption en español con credito al autor y enlace al
original. Los memes son de usuarios y no tienen licencia: el credito reduce el riesgo, no lo elimina.
"""
import html as html_lib
import logging
import math
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

import job_store

logger = logging.getLogger(__name__)

BASE = "https://es.memedroid.com"
SOURCES = (
    "/memes/tag/videojuegos",
    "/memes/tag/gaming",
    "/memes/tag/gamer",
    "/memes/tag/playstation",
    "/memes/tag/nintendo",
    "/memes/tag/minecraft",
    "/memes/tag/pokemon",
    "/memes/tag/gta",
    "/memes/top/week",
    "/memes/top/day",
)
KIND = "gaming_meme"
USED_STORE = "gaming_meme_used"
USED_MAX = 300
WINDOW_TIERS = (7, 30, 90)  # dias hacia atras: se prueba cada tramo en orden y se usa el primero con memes aptos
CACHE_SECONDS = 600  # un lote pide varios memes seguidos: la lectura de las galerias se reutiliza
MIN_VOTES = 15
MIN_RATING_PCT = 80
MIN_SIDE_PX = 600
MAX_SIDE_PX = 1080
MAX_ASPECT = 2.2  # Instagram rechaza imagenes mas alargadas que ~1:2
TIMEOUT = 20
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MemeBot/1.0)",
    "Accept-Language": "es-ES,es;q=0.9",
}
# Un meme de otra tematica puede salir en "lo mejor de la semana": exige una etiqueta de videojuegos.
GAMING_TAGS = (
    "videojuego", "gaming", "gamer", "juegos", "nintendo", "playstation", "xbox", "minecraft", "pokemon",
    "pokémon", "zelda", "mario", "fortnite", "steam", "pc gaming", "consola", "ps5", "ps4", "switch",
    "league of legends", "gta", "call of duty", "dark souls", "elden ring",
)
HOOKS = (
    "Si juegas, esto te va a pasar 🎮",
    "Todo gamer ha vivido esto 😂",
    "Etiqueta a ese amigo que juega así 🎮",
    "Este meme lo dice todo 😅",
    "Sin comentarios, solo gamers lo entienden 🕹️",
)
HASHTAGS = "#memes #videojuegos #gamer #gaming #memesenespañol"

_lock = threading.Lock()
_ARTICLE_RE = re.compile(r'(?s)<article id="article-item-\d+"[^>]*>.*?</article>')


class MemeError(RuntimeError):
    pass


def _attr(text: str, name: str) -> Optional[str]:
    m = re.search(rf'{name}="([^"]*)"', text)
    return html_lib.unescape(m.group(1)) if m else None


def parse_gallery(page: str) -> list:
    """Memes de imagen estatica (data-type=1) de una galeria de Memedroid; los demas tipos se descartan."""
    memes = []
    for art in _ARTICLE_RE.findall(page):
        if _attr(art[:200], "data-type") != "1":
            continue
        meme_id = _attr(art[:200], "data-item-id")
        img = re.search(r'<img src="(https://memes\.memedroid\.com/images/[^"]+)"', art)
        when = re.search(r'<time[^>]*datetime="([^"]+)"', art)
        votes = re.search(r'data-votes="(\d+)"\s+data-positive-votes="(\d+)"', art)
        author = re.search(r'rel="author"[^>]*>(?:<img[^>]*>)?([^<]+)</a>', art)
        title = re.search(r'class="item-header-title[^"]*"[^>]*>([^<]*)</a>', art)
        tags = re.search(r'data-tags="([^"]*)"', art)
        if not (meme_id and img and when and votes and author):
            continue
        total, positive = int(votes.group(1)), int(votes.group(2))
        try:
            posted_at = datetime.fromisoformat(when.group(1))
        except ValueError:
            continue
        title_text = html_lib.unescape(title.group(1)).strip() if title else ""
        memes.append({
            "id": meme_id,
            "link": f"{BASE}/memes/detail/{meme_id}",
            "image_url": html_lib.unescape(img.group(1)),
            "author": html_lib.unescape(author.group(1)).strip(),
            "title": "" if title_text in ("", "-") else title_text,
            "tags": [t.strip() for t in html_lib.unescape(tags.group(1)).split(",")] if tags else [],
            "votes": total,
            "rating_pct": round(100 * positive / total) if total else 0,
            "posted_at": posted_at.isoformat(),
        })
    return memes


def _get(url: str) -> requests.Response:
    last: Exception = MemeError("sin intentos")
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise MemeError(f"No pude leer Memedroid ({url}): {last}")


_cache: dict = {"at": 0.0, "memes": []}


def fetch_candidates() -> list:
    """Memes de todas las galerias de SOURCES, sin repetir id (cache de CACHE_SECONDS)."""
    with _lock:
        if _cache["memes"] and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return list(_cache["memes"])
    memes = _fetch_all()
    with _lock:
        _cache.update(at=time.monotonic(), memes=memes)
    return list(memes)


def _fetch_all() -> list:
    """Lee todas las galerias de SOURCES. Una galeria caida no corta las demas."""
    seen, memes, failures = set(), [], 0
    for path in SOURCES:
        try:
            page = _get(BASE + path).content.decode("utf-8", errors="replace")
        except MemeError as e:
            logger.warning("memes: %s", e)
            failures += 1
            continue
        for meme in parse_gallery(page):
            if meme["id"] not in seen:
                seen.add(meme["id"])
                memes.append(meme)
    if failures == len(SOURCES):
        raise MemeError("Memedroid no respondió: reintentaré más tarde.")
    return memes


def is_gaming(meme: dict) -> bool:
    haystack = " ".join(meme["tags"] + [meme["title"]]).lower()
    return any(tag in haystack for tag in GAMING_TAGS)


def _used_ids() -> set:
    return {e["id"] for e in job_store.load(USED_STORE).get("entries", [])}


def mark_used(meme: dict) -> None:
    with _lock:
        store = job_store.load(USED_STORE)
        entries = store.get("entries", [])
        entries.append({"id": meme["id"], "link": meme["link"], "used_at": datetime.now().isoformat()})
        store["entries"] = entries[-USED_MAX:]
        job_store.save(USED_STORE, store)


def score(meme: dict) -> float:
    return meme["rating_pct"] * math.log(1 + meme["votes"])


def pick_meme(memes: list, used: set, now: Optional[datetime] = None) -> Optional[dict]:
    """El mejor meme de videojuegos bien votado y no publicado; None si no hay ninguno. Prefiere lo reciente: prueba
    la ultima semana y, si no hay nada apto, 30 y luego 90 dias hacia atras (el maximo)."""
    now = now or datetime.now()
    eligible = [
        m for m in memes
        if m["id"] not in used and is_gaming(m) and m["votes"] >= MIN_VOTES and m["rating_pct"] >= MIN_RATING_PCT
    ]
    for days in WINDOW_TIERS:
        oldest = now - timedelta(days=days)
        ok = [m for m in eligible if datetime.fromisoformat(m["posted_at"]) >= oldest]
        if ok:
            ok.sort(key=lambda m: (score(m), m["posted_at"]), reverse=True)
            return ok[0]
    return None


def download(meme: dict, dest: Path) -> Path:
    """Baja la imagen a `dest` como JPEG RGB (lado mayor <= 1080), sin recortar para no cortar el texto del meme."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = dest.with_suffix(".src")
    try:
        raw.write_bytes(_get(meme["image_url"]).content)
        with Image.open(raw) as img:
            img = img.convert("RGB")
            w, h = img.size
            if min(w, h) < MIN_SIDE_PX // 2 or max(w, h) / min(w, h) > MAX_ASPECT:
                raise MemeError(f"Imagen no apta para Instagram ({w}x{h}).")
            if max(w, h) > MAX_SIDE_PX:
                scale = MAX_SIDE_PX / max(w, h)
                img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
            img.save(dest, "JPEG", quality=92)
    except OSError as e:
        raise MemeError(f"Imagen de meme ilegible: {e}") from e
    finally:
        raw.unlink(missing_ok=True)
    return dest


def build_caption(meme: dict, index: int = 0) -> str:
    hook = HOOKS[index % len(HOOKS)]
    return f"{hook}\n\nMeme de {meme['author']} vía Memedroid\nOriginal: {meme['link']}\n\n{HASHTAGS}"


def generate_gaming_meme(dest: Path, index: int = 0) -> dict:
    """Elige el mejor meme disponible, lo guarda en `dest` y lo marca como usado. Devuelve
    {image_path, caption, hook, source_meme, author}. MemeError si no hay ninguno apto."""
    meme = pick_meme(fetch_candidates(), _used_ids())
    if not meme:
        raise MemeError(f"No hay memes nuevos de videojuegos bien votados en Memedroid (revisé los últimos {WINDOW_TIERS[-1]} días).")
    download(meme, dest)
    mark_used(meme)  # solo con el archivo ya guardado
    caption = build_caption(meme, index)
    return {
        "image_path": str(dest), "caption": caption, "hook": meme["title"] or HOOKS[index % len(HOOKS)],
        "source_meme": meme["link"], "author": meme["author"],
    }
