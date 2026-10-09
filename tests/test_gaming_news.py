# -*- coding: utf-8 -*-
"""Noticias de videojuegos para los posts gaming: RSS, eleccion sin repetir y prompt. Sin red."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline as bp
import gaming_news as gn
import job_store

NOW = datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc)


def _item(title, source, hours_ago, link=None):
    return {"title": title, "link": link or f"https://x/{abs(hash(title))}", "source": source,
            "published": NOW - timedelta(hours=hours_ago), "summary": "resumen"}


def test_rss_is_parsed_with_html_stripped():
    xml = (
        '<rss><channel><item><title>Nuevo <b>DLC</b> de Elden Ring</title><link>https://x/1</link>'
        '<pubDate>Wed, 07 Oct 2026 16:04:00 GMT</pubDate><description>&lt;p&gt;Llega &amp;amp; sorprende&lt;/p&gt;</description>'
        '</item><item><title>sin fecha</title><link>https://x/2</link></item></channel></rss>'
    ).encode()
    items = gn._parse_feed("Vandal", xml)
    assert len(items) == 1 and items[0]["title"] == "Nuevo DLC de Elden Ring"
    assert items[0]["summary"] == "Llega & sorprende" and items[0]["source"] == "Vandal"


def test_picks_the_story_most_outlets_cover_and_skips_used():
    gta = "GTA 6 muestra por fin su primer gameplay oficial"
    news = [
        _item("Pequeño parche menor para un juego indie olvidado", "Vandal", 1),
        _item(gta, "Vandal", 5), _item("Primer gameplay oficial de GTA 6 por fin se muestra", "3DJuegos", 4),
        _item("GTA 6 gameplay oficial: primer vistazo", "IGN España", 3),
    ]
    best = gn.pick_news(news, [], NOW)
    assert "gta" in best["title"].lower()
    used = [{"link": n["link"], "title": n["title"]} for n in news if "gta" in n["title"].lower()]
    # lo usado (y las versiones casi iguales de otros medios) no vuelve: queda la otra noticia
    assert gn.pick_news(news, used[:1], NOW)["title"].startswith("Pequeño parche")


def test_old_news_are_ignored_and_empty_raises():
    old = [_item("Noticia de hace una semana sobre un juego", "Vandal", 24 * 7)]
    with pytest.raises(gn.NoNewsError, match="no hay noticias de videojuegos"):
        gn.pick_news(old, [], NOW)
    # sin nada de las ultimas 36 h se acepta lo de hasta 72 h
    assert gn.pick_news([_item("Noticia de ayer y medio sobre Zelda", "Vandal", 50)], [], NOW)["source"] == "Vandal"


def test_used_news_are_persisted(monkeypatch):
    store = {}
    monkeypatch.setattr(job_store, "load", lambda name: dict(store.get(name, {})))
    monkeypatch.setattr(job_store, "save", lambda name, data: store.__setitem__(name, dict(data)))
    gn.mark_used(_item("Noticia uno de prueba", "Vandal", 1, link="https://x/uno"))
    assert [u["link"] for u in gn._load_used()] == ["https://x/uno"]


def test_gaming_profile_is_news_and_layout_has_exact_text():
    p = bp.IMAGE_POST_PROFILES["gaming_image"]
    assert p["news"] and p["kind"] == "gaming_news" and p["ai_design"] == "news"
    assert (bp._PROMPTS_DIR / "gaming_news_system.md").exists()
    out = bp._ai_news_layout({"top": "XBOX REGALA 9 JUEGOS", "bottom": "disponibles en octubre."}, "3DJuegos")
    assert '"NEWS"' in out and '"XBOX REGALA 9 JUEGOS"' in out and '"disponibles en octubre"' in out
    assert '"Source: 3DJuegos"' in out
    block = gn.news_block(_item("Titular X", "Vandal", 2))
    assert "Headline: Titular X" in block and "Source: Vandal" in block


def test_only_videogame_news_pass_the_filter():
    ok = _item("Xbox anuncia los juegos gratis de octubre", "3DJuegos", 1)
    off = {**_item("Mañana llega el estreno más importante de la semana a Netflix", "3DJuegos", 1), "summary": "una serie de seis episodios"}
    assert gn.is_gaming(ok) and not gn.is_gaming(off)
    summary_only = {**_item("Esto lo cambia todo", "Vandal", 1), "summary": "El nuevo DLC llega a Steam la próxima semana"}
    assert gn.is_gaming(summary_only)


def test_gaming_text_is_all_english():
    import gaming_clip
    prompt = (bp._PROMPTS_DIR / "gaming_news_system.md").read_text(encoding="utf-8")
    assert "ALL OUTPUT IS IN ENGLISH" in prompt and "en español" not in prompt.lower()
    assert all(not n.endswith(("España",)) for n in gn.FEEDS) and "es.ign.com" not in str(gn.FEEDS)
    texts = gaming_clip.build_texts({"id": "abc", "author": "pro"}, gaming_clip.GAMES["cs2"])
    assert "Luck or skill" in texts["caption"] and "Contame" not in texts["caption"]
    assert not any(c in texts["title"] + texts["hook"] for c in "¿¡áéíóúñ")
    assert "#jugadasepicas" not in " ".join(gaming_clip.GAMES["cs2"]["tags"])
