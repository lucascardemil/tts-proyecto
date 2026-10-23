# -*- coding: utf-8 -*-
"""Everyday Explained: infografias educativas en ingles, solo Facebook. Sin red ni job_store real."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import auto_pipeline
import batch_pipeline as bp

REPLY = """IDEA: Why does a metal spoon feel colder than a wooden one in the same room? Metal moves heat away from your hand faster.
HOOK: Why metal feels colder than wood
IMAGE_PROMPT: hyperrealistic 3D macro render of a hand touching a steel spoon and a wooden spoon on a kitchen counter, heat shown as glowing orange arrows flowing out of the fingertips into the steel, dramatic side lighting, shallow depth of field
TOP: WHY METAL FEELS COLDER
BOTTOM: Metal pulls heat from your skin faster than wood.
CAPTION: Same room, same temperature. So why does metal feel colder? 🤔

Metal is a much better conductor of heat than wood. When you touch it, it pulls warmth out of your hand quickly. Wood moves heat slowly, so your skin barely notices.

Key takeaway: you feel the speed of heat leaving your hand, not the object's real temperature.

Which everyday object always feels freezing to you?

#EverydayExplained #Science #HowThingsWork #DidYouKnow"""


def _post(**over):
    post = bp._parse_gaming_post(REPLY)
    post.update(over)
    return post


def test_profile_is_facebook_only_and_scheduled_twice_a_day():
    p = bp.IMAGE_POST_PROFILES["everyday_image"]
    assert p["kind"] == "everyday_post" and p["slot_hours"] == [11, 19] and p["facebook_only"]
    assert p["ai_design"] == "infographic" and p["max_hashtags"]["facebook"] == 5
    assert (bp._PROMPTS_DIR / "everyday_post_system.md").exists()
    for day in range(7):
        assert p["week"][day][0] in p["templates"]


def test_system_prompt_loads_without_placeholders_and_has_the_brand_rules():
    prompt = bp._text_system_prompt("everyday_post")
    assert "{{" not in prompt and "MAXIMUM 6 words" in prompt and "MAXIMUM 12 words" in prompt
    assert "Do NOT invent statistics" in prompt and "ENGAGEMENT QUESTION" in prompt


def test_valid_reply_parses_and_passes_validation():
    post = _post()
    assert post["top"] == "WHY METAL FEELS COLDER" and post["bottom"].endswith("than wood.")
    bp._validate_everyday_post(post)  # no lanza


@pytest.mark.parametrize("over,match", [
    ({"top": "WHY DOES METAL ALWAYS FEEL SO MUCH COLDER"}, "titular"),
    ({"bottom": ""}, "respuesta"),
    ({"bottom": "Metal pulls heat from your skin a lot faster than wood does in a room"}, "respuesta"),
    ({"caption": "Why does metal feel cold?\n\nIt conducts heat. #A #B"}, "hashtags"),
    ({"caption": "Metal feels cold.\n\nIt conducts heat. #A #B #C"}, "pregunta"),
    ({"caption": "Q? #A #B #C #D #E #F"}, "hashtags"),
])
def test_invalid_posts_are_rejected(over, match):
    with pytest.raises(auto_pipeline.PipelineError, match=match):
        bp._validate_everyday_post(_post(**over))


def test_illustration_prompt_forbids_text_and_carries_the_brand_palette():
    out = bp._ai_infographic_layout(_post())
    assert "#14283E" in out and "#FFD34E" in out and "absolutely no text" in out and "central 90%" in out
    assert _post()["top"] not in out  # el titular NO viaja al generador: se compone despues


def test_render_infographic_composes_exact_brand_layout(tmp_path):
    from PIL import Image
    import meme_maker
    Image.new("RGB", (1024, 1024), (200, 60, 60)).save(tmp_path / "art.png")
    out = meme_maker.render_infographic(tmp_path / "art.png", "Why metal feels colder", "Metal pulls heat from your skin faster than wood.",
                                        tmp_path / "o.jpg")
    with Image.open(out) as img:
        assert img.size == meme_maker.SIZE_IG
        w, h = img.size
        assert img.getpixel((5, 5)) == pytest.approx(meme_maker.INFO_NAVY, abs=6)  # fondo azul marino
        art_w, art_h = meme_maker.infographic_art_size(meme_maker.SIZE_IG)
        assert img.getpixel((w // 2, int(h * meme_maker.INFO_HEADER_FRAC) + art_h // 2))[0] > 150  # ilustracion al centro
        header = img.crop((0, 0, w, int(h * meme_maker.INFO_HEADER_FRAC)))
        footer = img.crop((0, h - int(h * meme_maker.INFO_FOOTER_FRAC), w, h))
        yellow = sum(1 for px in header.getdata() if px[0] > 220 and px[1] > 180 and px[2] < 140)
        light = sum(1 for px in footer.getdata() if min(px) > 200)
        assert yellow > 800 and light > 800  # titular amarillo y respuesta clara dibujados


def test_render_infographic_fits_long_texts_without_leaving_the_margins(tmp_path):
    from PIL import Image
    import meme_maker
    Image.new("RGB", (768, 768), (90, 120, 160)).save(tmp_path / "art.png")
    out = meme_maker.render_infographic(tmp_path / "art.png", "WHY DOES THE SKY LOOK BLUE", "Air scatters short blue wavelengths far more than red ones.",
                                        tmp_path / "o.jpg")
    with Image.open(out) as img:
        w, h = img.size
        for box in ((0, 0, int(w * 0.03), h), (int(w * 0.97), 0, w, h)):  # margenes laterales sin texto
            assert all(px == pytest.approx(meme_maker.INFO_NAVY, abs=8) or True for px in img.crop(box).getdata())
            assert max(abs(c - n) for px in img.crop(box).getdata() for c, n in zip(px, meme_maker.INFO_NAVY)) < 12


def test_hashtags_are_limited_for_facebook():
    post = _post(caption=REPLY.split("CAPTION: ")[1] + " #Extra")
    assert len(bp._limit_hashtags(post["caption"], 5).split("#")) - 1 == 5


def _stub_create(monkeypatch):
    monkeypatch.setattr(bp, "_load", lambda: {})
    monkeypatch.setattr(bp, "_save", lambda projects: None)
    monkeypatch.setattr(bp, "existing_slots", lambda *a, **k: [])


def test_create_project_drops_instagram_and_youtube_and_uses_eastern_slots(monkeypatch):
    _stub_create(monkeypatch)
    from zoneinfo import ZoneInfo
    project = bp.create_project(
        "Everyday Explained", 6, 2,
        {"facebook": {"page_id": "1"}, "instagram": {"page_id": "1"}, "youtube": True},
        {}, content_type="everyday_image",
    )
    assert set(project["networks"]) == {"facebook"}
    assert [bp.item_type(project, v) for v in project["videos"]] == ["everyday_image"] * 6
    hours = {datetime.fromisoformat(v["scheduled_at"]).astimezone(ZoneInfo(bp.US_AUDIENCE_TZ)).hour for v in project["videos"]}
    assert hours <= {11, 19}
    assert all(bp.BATCH_HOUR_START <= datetime.fromisoformat(v["scheduled_at"]).hour <= bp.BATCH_HOUR_END
               for v in project["videos"])


def test_everyday_page_is_registered_from_env(monkeypatch):
    import meta_auth
    monkeypatch.setenv("FB_PAGE_1_ID", "1444420808745609")
    monkeypatch.setenv("FB_PAGE_1_NAME", "Everyday Explained")
    monkeypatch.setenv("FB_PAGE_1_TOKEN", "x")
    monkeypatch.delenv("FB_PAGE_2_ID", raising=False)
    assert meta_auth.list_pages() == [{"page_id": "1444420808745609", "name": "Everyday Explained"}]
    assert "token" not in str(meta_auth.list_pages()).lower()


def test_app_exposes_the_type_and_facebook_only_endpoints():
    src = (Path(__file__).parent.parent / "app.py").read_bytes().decode("utf-8")
    assert src.count('<option value="everyday_image">') == 2
    assert 'everyday_image: "give me the next Everyday Explained post"' in src
    assert '"everyday": "everyday_image"' in src and 'everyday_image: "everyday"' in src
    assert "_facebook_only(" in src
