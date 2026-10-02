import batch_pipeline
import gaming_clip

CLIP = {"id": "abc123", "title": "x", "author": "Pepe", "page_url": "https://medal.tv/clips/abc123"}


def _seo(game_key="valorant", clip=CLIP):
    game = gaming_clip.GAMES[game_key]
    return gaming_clip.build_youtube_seo(clip, game, "MIRÁ ESTA JUGADA!"), game


def test_seo_limits_and_keywords():
    seo, game = _seo()
    assert len(seo["title"]) <= gaming_clip.YT_TITLE_MAX
    assert game["label"] in seo["title"]
    assert "#Shorts" in seo["title"]
    assert "MIRÁ" not in seo["title"]
    hashtags = [w for w in seo["description"].split() if w.startswith("#")]
    assert 3 <= len(hashtags) <= gaming_clip.YT_MAX_HASHTAGS
    assert hashtags[0] == "#Shorts"
    assert len(seo["description"]) <= 5000
    assert sum(len(t) + 1 + (2 if " " in t else 0) for t in seo["tags"]) <= 450
    assert game["label"] in seo["tags"]
    assert "@Pepe" in seo["description"] and "@ " not in seo["description"]


def test_seo_deterministic_and_differs_from_social_caption():
    a, game = _seo()
    b, _ = _seo()
    assert a == b
    caption = gaming_clip.build_texts(CLIP, game)["caption"]
    assert a["description"] != caption and a["title"] != gaming_clip.build_texts(CLIP, game)["title"]


def test_seo_long_game_name_still_fits():
    game = {"label": "X" * 70, "slug": "x", "short": "X", "kills": False, "tags": []}
    seo = gaming_clip.build_youtube_seo(CLIP, game, "HOOK LARGO " * 5)
    assert len(seo["title"]) <= gaming_clip.YT_TITLE_MAX


def test_youtube_fields_use_seo_or_fallback():
    seo, _ = _seo()
    assert batch_pipeline.gaming_youtube_fields(seo, "t", "cap #a") == (seo["title"], seo["description"], seo["tags"])
    title, desc, tags = batch_pipeline.gaming_youtube_fields(None, "Titulo", "cap #a")
    assert tags == [] and "#Shorts" in (title + desc)


def test_medal_network_and_remotion_errors_are_healable():
    assert batch_pipeline._is_healable_error("No se pudo leer Medal (https://medal.tv/x): NameResolutionError")
    assert batch_pipeline._is_healable_error("Remotion falló al renderizar el clip: trimBefore must be greater")
    assert not batch_pipeline._is_healable_error("No encontre el proyecto 'abc'")
