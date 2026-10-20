# -*- coding: utf-8 -*-
"""Reel de noticias de videojuegos: validacion del guion, tiempos de escena y props de la composicion."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import gaming_news as gn
import gaming_reel as gr

SOURCE = (
    "Silksong sells big. Hollow Knight: Silksong sold 2.5 million copies in its first day on Steam, Team Cherry "
    "said. The game launched on September 4, 2025 for $20. The studio said: \"We are overwhelmed by the response "
    "from players\" after the launch."
)

SCRIPT = """HOOK: SILKSONG SOLD OUT FAST
TITLE: Silksong sold two and a half million copies in a day
CAPTION: SILKSONG BREAKS RECORDS 🔥
The sequel sold 2.5 million copies on its first day, the studio said. It launched on Steam for $20.
Did you pick it up on day one?
Source: IGN
#Silksong #HollowKnight #Steam #gaming #videogames #gamingnews
SCENE 1 | hook | SILKSONG SOLD OUT FAST | - | The wait is finally over and players showed up in huge numbers on launch day for the long awaited sequel
SCENE 2 | stat | COPIES SOLD IN DAY ONE | 2.5 million | The team says the game sold two point five million copies in its first twenty four hours on the platform
SCENE 3 | versus | DAY ONE vs PRICE | 2.5 vs 20 | That is a lot of players for a game that costs just twenty dollars which makes it an easy purchase for fans
SCENE 4 | timeline | Sep 4: Launch / 2025: First year | - | The release came in September and the studio has been busy ever since keeping up with demand from the community
SCENE 5 | quote | We are overwhelmed by the response from players | Team Cherry | The studio thanked its community right after the numbers started coming in from every region of the world
SCENE 6 | outro | FOLLOW FOR MORE GAMING NEWS | - | Follow the page for more gaming news every day and remember this story comes from IGN so check them out
"""


def parse(script=SCRIPT, source=SOURCE):
    return gr.parse_reel_script(script, source)


def test_valid_script_parses_into_typed_scenes():
    out = parse()
    assert [s["type"] for s in out["scenes"]] == ["hook", "stat", "versus", "timeline", "quote", "outro"]
    stat = out["scenes"][1]
    assert stat["number"] == 2.5 and stat["decimals"] == 1 and stat["suffix"] == "million" and stat["prefix"] == ""
    versus = out["scenes"][2]
    assert (versus["labelA"], versus["labelB"], versus["valueA"], versus["valueB"]) == ("DAY ONE", "PRICE", 2.5, 20.0)
    assert out["scenes"][3]["items"] == ["Sep 4: Launch", "2025: First year"]
    assert out["scenes"][4]["by"] == "Team Cherry"
    assert out["hook"] == "SILKSONG SOLD OUT FAST" and "Source: IGN" in out["caption"]
    assert gr.MIN_WORDS <= len(out["narration"].split()) <= gr.MAX_WORDS


def test_numbers_that_are_not_in_the_news_are_rejected():
    with pytest.raises(gr.ReelError, match="no aparece en la noticia"):
        parse(SCRIPT.replace("| 2.5 million |", "| 9 million |"))
    with pytest.raises(gr.ReelError, match="no aparece"):
        parse(SCRIPT.replace("2.5 vs 20", "2.5 vs 99"))
    with pytest.raises(gr.ReelError, match="no aparece"):
        parse(SCRIPT.replace("2025: First year", "2031: Next game"))


def test_a_quote_must_be_copied_from_the_news():
    with pytest.raises(gr.ReelError, match="cita"):
        parse(SCRIPT.replace("We are overwhelmed by the response from players |", "We made the best game ever |"))


def test_structure_errors_are_rejected():
    with pytest.raises(gr.ReelError, match="formato"):
        parse("just some text")
    with pytest.raises(gr.ReelError, match="4 campos"):
        parse(SCRIPT.replace("| - | The wait is", "| The wait is"))
    with pytest.raises(gr.ReelError, match="tipo desconocido"):
        parse(SCRIPT.replace("| outro |", "| banner |"))
    with pytest.raises(gr.ReelError, match="primera escena"):
        parse(SCRIPT.replace("SCENE 1 | hook", "SCENE 1 | stat"))
    with pytest.raises(gr.ReelError, match="digitos"):
        parse(SCRIPT.replace("a game that costs just twenty dollars", "a game that costs just 20 dollars"))
    with pytest.raises(gr.ReelError, match="palabras"):
        import re
        parse(re.sub(r"^(SCENE \d \|.*\| )[^|\n]+$", r"\1Short line here.", SCRIPT, flags=re.MULTILINE))


def test_scene_frames_follow_the_words_and_cover_the_audio():
    scenes = [{"narration": "a b c d"}, {"narration": "e f g h"}, {"narration": "i j k l"}]
    words = [{"word": w, "start": i * 1.0, "end": i * 1.0 + 0.9} for i, w in enumerate("abcdefghijkl")]
    frames = gr.scene_frames(scenes, words, duration=12.0)
    assert frames[0][0] == 0 and frames[1][0] == 4 * gr.FPS and frames[2][0] == 8 * gr.FPS
    assert frames[0][1] == frames[1][0] and frames[1][1] == frames[2][0]
    assert frames[-1][1] == round((12.0 + gr.TAIL_SECONDS) * gr.FPS)


def test_scene_frames_without_words_split_by_narration_length():
    frames = gr.scene_frames([{"narration": "a b"}, {"narration": "c d"}], [], duration=10.0)
    assert frames[1][0] == 5 * gr.FPS and frames[1][1] > frames[1][0]


def test_props_match_the_composition_inputs():
    parsed = parse()
    words = [{"word": f"w{i}", "start": i * 0.3, "end": i * 0.3 + 0.25} for i in range(60)]
    props = gr.build_props(parsed, words, "greel_x/narration.wav", 18.0, "IGN", "RECORD", "#ffd60a")
    assert props["audioSrc"] == "greel_x/narration.wav" and props["sourceName"] == "IGN" and props["angle"] == "RECORD"
    assert props["totalDurationSeconds"] == round(18.0 + gr.TAIL_SECONDS, 3)
    assert [s["type"] for s in props["scenes"]] == ["hook", "stat", "versus", "timeline", "quote", "outro"]
    assert all("narration" not in s and s["endFrame"] > s["startFrame"] for s in props["scenes"])
    assert props["subtitles"] == words and props["subtitleStyle"]["fontFamily"]
    starts = [s["startFrame"] for s in props["scenes"]]
    assert starts == sorted(starts) and starts[0] == 0


def test_accent_is_a_stable_hex_color_per_headline():
    a = gr.accent_for("Silksong sells big")
    assert a == gr.accent_for("Silksong sells big") and len(a) == 7 and a.startswith("#")
    assert a != gr.accent_for("A different headline")


def test_youtube_seo_uses_the_caption_hashtags_as_tags():
    seo = gr.youtube_seo(parse())
    assert seo["title"].startswith("Silksong") and "Silksong" in seo["tags"] and "#Shorts" in seo["description"]
    assert "#Silksong" not in seo["description"].split("\n\n")[0]


def test_write_script_retries_with_the_rejection_reason():
    item = {"title": "Silksong sells big", "summary": SOURCE, "body": "", "source": "IGN", "angle": "RECORD",
            "published": datetime.now(timezone.utc)}
    messages = []

    def text_fn(message):
        messages.append(message)
        return "garbage" if len(messages) == 1 else SCRIPT

    out = gr.write_script(item, text_fn)
    assert len(messages) == 2 and "previous answer was rejected" in messages[1] and out["scenes"]


def test_write_script_gives_up_after_the_attempt_limit():
    item = {"title": "t", "summary": "s", "body": "", "source": "IGN", "angle": "NEWS",
            "published": datetime.now(timezone.utc)}
    with pytest.raises(gr.ReelError, match="tras 3 intentos"):
        gr.write_script(item, lambda m: "garbage")


def test_reel_script_errors_are_healable_by_the_batch_healer():
    import batch_pipeline
    assert batch_pipeline._is_healable_error("Guion de reel inválido tras 3 intentos: la narracion tiene 20 palabras")


# --- noticias de las ultimas 2 semanas ---------------------------------------------------------------------------

def _item(title, source, days_old, now):
    return {"title": title, "link": f"https://x/{title[:6]}{source}", "source": source,
            "published": now - timedelta(days=days_old), "summary": ""}


def test_reel_window_reaches_two_weeks_but_not_beyond():
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    ten_days = [_item("Elden Ring expansion sells wildly", "IGN", 10, now)]
    with pytest.raises(gn.NoNewsError):
        gn.pick_news(ten_days, [], now)  # ventana de posts de imagen: solo 72 h
    assert gn.pick_news(ten_days, [], now, fresh_hours=gn.REEL_FRESH_HOURS)["source"] == "IGN"
    too_old = [_item("Ancient gaming story about Zelda", "IGN", 15, now)]
    with pytest.raises(gn.NoNewsError):
        gn.pick_news(too_old, [], now, fresh_hours=gn.REEL_FRESH_HOURS)


def test_reel_prefers_recent_news_and_trending_games():
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    old_hot = _item("Fortnite season launches today with huge map", "IGN", 9, now)
    new = _item("Nintendo reveals mysterious console prototype photos", "GameSpot", 1, now)
    assert gn.pick_news([old_hot, new], [], now, fresh_hours=gn.REEL_FRESH_HOURS)["source"] == "GameSpot"
    both = [_item("Fortnite season launches with huge map", "IGN", 1, now),
            _item("Nintendo reveals prototype console photos", "GameSpot", 1, now)]
    picked = gn.pick_news(both, [], now, fresh_hours=gn.REEL_FRESH_HOURS, trending=("Fortnite",))
    assert picked["source"] == "IGN"


def test_reel_angle_by_keywords():
    assert gn.reel_angle({"title": "Game sells 5 million copies", "summary": ""}) == "RECORD"
    assert gn.reel_angle({"title": "Easter egg found after years", "summary": ""}) == "FACT"
    assert gn.reel_angle({"title": "Studio announces a new trailer", "summary": ""}) == "NEWS"


def test_article_text_keeps_long_paragraphs_and_skips_menus(monkeypatch):
    html_page = ("<nav><p>Home menu item that should never appear in the article text at all really</p></nav>"
                 "<p>Short.</p><p>" + "The game sold two million copies and players keep coming back every day. " * 2
                 + "</p><script>var x=1;</script>")

    class Resp:
        text = html_page

        def raise_for_status(self):
            pass

    monkeypatch.setattr(gn.requests, "get", lambda *a, **k: Resp())
    text = gn.fetch_article_text("https://x/a")
    assert "two million copies" in text and "Home menu" not in text and "Short." not in text


def test_article_text_is_empty_when_the_page_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(gn.requests, "get", boom)
    assert gn.fetch_article_text("https://x/a") == ""


def test_next_reel_news_marks_angle_and_body_without_marking_used(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(gn, "fetch_news", lambda: [_item("Game sells 5 million copies worldwide", "IGN", 1, now)])
    monkeypatch.setattr(gn, "_load_used", lambda: [])
    monkeypatch.setattr(gn, "fetch_article_text", lambda link: "Article body")
    item = gn.next_reel_news(("Anything",))
    assert item["angle"] == "RECORD" and item["body"] == "Article body"
    block = gn.reel_news_block(item)
    assert "Angle: RECORD" in block and "Article excerpt: Article body" in block and "Source: IGN" in block
