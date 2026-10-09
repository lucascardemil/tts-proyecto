"""
Noticias de videojuegos actuales para los posts de imagen de JugadasEpicasVideojuegos.

Lee los RSS de medios de videojuegos en ingles (sin claves ni scraping), se queda con lo publicado en
las ultimas horas y elige la noticia mas cubierta (la que mas medios repiten) que todavia no se uso. Lo
usado se guarda en disco: ni el mismo enlace ni una noticia casi igual de otro medio se repiten.
"""

import html
import logging
import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from xml.etree import ElementTree as ET

import requests

import job_store

logger = logging.getLogger(__name__)

FEEDS = {
    "IGN": "https://feeds.feedburner.com/ign/games-all",
    "GameSpot": "https://www.gamespot.com/feeds/news/",
    "PC Gamer": "https://www.pcgamer.com/rss/",
    "Eurogamer": "https://www.eurogamer.net/feed",
    "Polygon": "https://www.polygon.com/rss/index.xml",
    "VGC": "https://www.videogameschronicle.com/feed/",
    "GamesRadar": "https://www.gamesradar.com/rss/",
}
FRESH_HOURS = (36, 72)  # primero lo de las ultimas 36 h; si no hay nada nuevo, hasta 72 h
SAME_STORY = 0.4  # similitud de titulos (Jaccard) a partir de la cual dos medios cuentan la misma noticia
ALREADY_USED = 0.5  # idem frente a lo ya publicado
USED_STORE = "gaming_news_used"
USED_MAX = 200
SUMMARY_MAX = 600
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}
_lock = threading.Lock()


# Los medios generalistas (3DJuegos, Vida Extra, IGN) mezclan series, peliculas y tecnologia: solo se
# aceptan noticias que hablen de videojuegos.
_GAME_WORDS = (
    "juego", "videojuego", "gameplay", "ps5", "ps4", "playstation", "xbox", "nintendo", "switch 2", "steam",
    "dlc", "epic games", "fortnite", "minecraft", "gta", "zelda", "mario", "pokemon", "call of duty", "battlefield",
    "league of legends", "valorant", "esports", "gamescom", "roblox", "elden ring", "resident evil", "jugador",
    "consola", "game", "gaming", "gamer", "console", "players", "patch", "trailer", "release date", "indie",
    "sony", "valve", "capcom", "ubisoft", "bethesda", "rockstar", "demo", "early access", "mod ", "rpg", "shooter", "battle royale", "genshin", "diablo", "fifa", "ea sports",
)
_OFF_TOPIC = ("tv show", "movie", "film ", "series", "serie", "pelicula", "netflix", "disney", "hbo", "anime", "temporada de", "estreno en cines", "smartphone", "movil")

class NoNewsError(RuntimeError):
    """No hay ninguna noticia nueva sin usar (o ningun medio respondio)."""


def _tokens(title: str) -> set:
    plain = unicodedata.normalize("NFD", title.lower()).encode("ascii", "ignore").decode()
    return {w for w in re.findall(r"[a-z0-9]+", plain) if len(w) >= 4}


def _similar(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _plain(text: str) -> str:
    return unicodedata.normalize("NFD", (text or "").lower()).encode("ascii", "ignore").decode()


def is_gaming(item: dict) -> bool:
    """True si la noticia habla de videojuegos (titular, o resumen sin pinta de serie/pelicula/tecnologia)."""
    title, summary = _plain(item["title"]), _plain(item.get("summary", ""))
    if any(w in title for w in _GAME_WORDS):
        return True
    return any(w in summary for w in _GAME_WORDS) and not any(w in title for w in _OFF_TOPIC)


def _clean(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _text(node, tag: str) -> str:
    child = node.find(tag)
    return "".join(child.itertext()) if child is not None else ""


def _parse_feed(source: str, content: bytes) -> list:
    root = ET.fromstring(content)
    items = []
    for node in root.iter("item"):
        title = _clean(_text(node, "title"))
        link = _text(node, "link").strip()
        when = _text(node, "pubDate")
        if not (title and link and when):
            continue
        try:
            published = parsedate_to_datetime(when)
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        items.append({
            "title": title, "link": link, "source": source, "published": published,
            "summary": _clean(_text(node, "description"))[:SUMMARY_MAX],
        })
    return items


def fetch_news() -> list:
    """Noticias de todos los medios que respondan, mas nuevas primero."""
    news = []
    for source, url in FEEDS.items():
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
            news += _parse_feed(source, resp.content)
        except Exception as e:  # un medio caido no frena a los demas
            logger.warning("gaming_news: %s no respondio: %s", source, e)
    news = [n for n in news if is_gaming(n)]
    news.sort(key=lambda n: n["published"], reverse=True)
    return news


def _load_used() -> list:
    return job_store.load(USED_STORE).get("entries", [])


def mark_used(item: dict) -> None:
    with _lock:
        store = job_store.load(USED_STORE)
        entries = store.get("entries", [])
        entries.append({"link": item["link"], "title": item["title"], "used_at": datetime.now().isoformat()})
        store["entries"] = entries[-USED_MAX:]
        job_store.save(USED_STORE, store)


def pick_news(news: list, used: list, now: Optional[datetime] = None) -> dict:
    """La noticia mas cubierta por los medios (empate: la mas reciente) entre las frescas y sin usar."""
    now = now or datetime.now(timezone.utc)
    used_links = {u["link"] for u in used}
    used_tokens = [_tokens(u["title"]) for u in used]
    for hours in FRESH_HOURS:
        cutoff = now - timedelta(hours=hours)
        fresh = [n for n in news if n["published"] >= cutoff]
        candidates = []
        for n in fresh:
            toks = _tokens(n["title"])
            if n["link"] in used_links or any(_similar(toks, u) >= ALREADY_USED for u in used_tokens):
                continue
            outlets = {o["source"] for o in fresh if _similar(toks, _tokens(o["title"])) >= SAME_STORY}
            candidates.append((len(outlets), n["published"], n))
        if candidates:
            return max(candidates, key=lambda c: (c[0], c[1]))[2]
    raise NoNewsError("no hay noticias de videojuegos nuevas sin usar (todas ya se publicaron o los medios no respondieron)")


def next_news() -> dict:
    """Busca en internet y devuelve la proxima noticia a publicar (sin marcarla como usada)."""
    news = fetch_news()
    if not news:
        raise NoNewsError("ningun medio de videojuegos respondio (¿sin conexión?)")
    return pick_news(news, _load_used())


def news_block(item: dict) -> str:
    """Texto con la noticia para el generador de texto."""
    local = item["published"].astimezone().strftime("%d/%m/%Y %H:%M")
    return (
        "\n\nNEWS ITEM (use ONLY this data, invent nothing):\n"
        f"Headline: {item['title']}\n"
        f"Summary: {item['summary'] or '(no summary)'}\n"
        f"Source: {item['source']} · published {local}"
    )
