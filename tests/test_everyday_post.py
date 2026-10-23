# -*- coding: utf-8 -*-
"""Everyday Explained: Formato A (publicacion individual) y Formato B (carrusel de 5), solo Facebook, 3 al dia.
Sin red ni job_store real."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import auto_pipeline
import batch_pipeline as bp
import facebook_publisher as fp
import meme_maker

REPLY = """IDEA: Why does a metal spoon feel colder than a wooden one in the same room? Metal moves heat away from your hand faster.
HOOK: Why metal feels colder than wood
IMAGE_PROMPT: hyperrealistic 3D macro render of a hand touching a steel spoon and a wooden spoon on a kitchen counter, heat shown as glowing orange arrows flowing out of the fingertips into the steel, dramatic side lighting, shallow depth of field
TOP: Why does metal feel colder than wood?
BOTTOM: Metal conducts heat away from your skin faster than wood, so it feels colder even at the same temperature.
CAPTION: Same room, same temperature. So why does metal feel colder? 🤔

Metal is a much better conductor of heat than wood. When you touch it, it pulls warmth out of your hand quickly. Wood moves heat slowly, so your skin barely notices.

Key takeaway: you feel the speed of heat leaving your hand, not the object's real temperature.

Which everyday object always feels freezing to you?

#EverydayExplained #Science #HowThingsWork #DidYouKnow"""

CAROUSEL_REPLY = """IDEA: Why does ice form from the outside in? Surfaces lose heat first, so freezing usually starts at the edges.
HOOK: Why ice freezes from the outside in
SLIDE 1
TITLE: Why does ice form from the outside in?
TEXT: -
IMAGE_PROMPT: cinematic macro photograph of a clear ice cube in a dark kitchen, frost forming along its edges, dramatic rim lighting, navy blue and electric blue tones, shallow depth of field
SLIDE 2
TITLE: Water freezes at the edges first
TEXT: Look closely at a tray of water in the freezer: a thin ice skin appears at the edges and the surface before the middle.
IMAGE_PROMPT: overhead photograph of an ice tray with a thin ice rim around each cube of water, cold blue light, realistic materials, navy background, clean composition
SLIDE 3
TITLE: Heat leaves through the surface
TEXT: The outside touches colder air or walls, so it loses heat first and drops below freezing before the center does.
IMAGE_PROMPT: 3D educational cutaway of a water-filled container in a freezer with arrows showing heat flowing outward through the walls, electric blue and yellow accents, dramatic lighting
SLIDE 4
TITLE: Ice insulates what is inside
TEXT: As the ice layer thickens it slows further heat loss, so the middle freezes more slowly and often last.
IMAGE_PROMPT: cross-section render of a half-frozen cube with a solid ice shell and liquid core, yellow arrows slowing down, high contrast, dark navy background, realistic ice texture
SLIDE 5
TITLE: Freezing starts where heat escapes
TEXT: Under ordinary conditions ice grows inward from the cold surfaces, though impurities and air bubbles can change the pattern.
IMAGE_PROMPT: striking close-up photograph of a clear ice sphere with trapped bubbles in the center on black, rim light in electric blue, cinematic scientific photography
CAPTION: Ever noticed ice forms from the outside in? 🧊

Water loses heat through its surface, so the edges reach freezing first. The new ice then slows heat loss from the middle.

Key takeaway: freezing usually starts where heat escapes fastest.

Where did you first notice this?

#EverydayExplained #Science #Physics #Freezing"""


def _post(**over):
    post = bp._parse_gaming_post(REPLY)
    post.update(over)
    return post


# ---- Formato A: publicacion individual ---------------------------------------------------------------------------

def test_profiles_are_facebook_only_and_scheduled_three_a_day():
    for key, hours in (("everyday_image", [10, 19]), ("everyday_carousel", [14])):
        p = bp.IMAGE_POST_PROFILES[key]
        assert p["slot_hours"] == hours and p["facebook_only"] and p["ai_design"] == "infographic"
        assert p["max_hashtags"]["facebook"] == 5 and p["history_store"] == "everyday_post_history"
        assert p["week"][0][0] in p["templates"]
    assert bp.IMAGE_POST_PROFILES["everyday_carousel"]["carousel"] is True
    assert bp.MIXED_TYPES["mix_everyday"] == ("everyday_image", "everyday_carousel")
    assert bp.MIXED_SLOT_HOURS["mix_everyday"] == [10, 14, 19]  # individual 10 y 19, carrusel 14: 3 al dia
    assert bp._facebook_only("mix_everyday") and bp._facebook_only("everyday_carousel") and not bp._facebook_only("macrame_image")
    for name in ("everyday_post_system.md", "everyday_carousel_system.md"):
        assert (bp._PROMPTS_DIR / name).exists()


def test_system_prompts_load_without_placeholders_and_carry_the_rules():
    single, carousel = bp._text_system_prompt("everyday_post"), bp._text_system_prompt("everyday_carousel")
    assert "{{" not in single and "MAXIMUM 10 words" in single and "MAXIMUM 28 words" in single
    assert "Do NOT invent statistics" in single and "ENGAGEMENT QUESTION" in single
    assert "exactly 5 separate slides" in carousel and "Do NOT invent studies" in carousel
    assert "Follow Everyday Explained for more." in carousel and "no Reels" in carousel


def test_valid_reply_parses_and_passes_validation():
    post = _post()
    assert post["top"].startswith("Why does metal") and post["bottom"].endswith("same temperature.")
    bp._validate_everyday_post(post)


@pytest.mark.parametrize("over,match", [
    ({"top": "WHY DOES THE METAL SPOON IN MY KITCHEN ALWAYS FEEL SO MUCH COLDER THAN WOOD"}, "titular"),
    ({"bottom": ""}, "respuesta"),
    ({"bottom": " ".join(["word"] * 29)}, "respuesta"),
    ({"caption": "Why does metal feel cold?\n\nIt conducts heat. #A #B"}, "hashtags"),
    ({"caption": "Metal feels cold.\n\nIt conducts heat. #A #B #C"}, "pregunta"),
    ({"caption": "Q? #A #B #C #D #E #F"}, "hashtags"),
])
def test_invalid_posts_are_rejected(over, match):
    with pytest.raises(auto_pipeline.PipelineError, match=match):
        bp._validate_everyday_post(_post(**over))


def test_illustration_prompt_forbids_text_and_carries_the_new_palette():
    out = bp._ai_infographic_layout(_post())
    assert "#0B1E3B" in out and "#008AFF" in out and "#FFD600" in out and "absolutely no text" in out
    assert _post()["top"] not in out  # el texto NO viaja al generador: se compone despues


def _art(tmp_path, color=(200, 60, 60), size=(1024, 1024)):
    from PIL import Image
    Image.new("RGB", size, color).save(tmp_path / "art.png")
    return tmp_path / "art.png"


def _count(img, box, test):
    return sum(1 for px in img.crop(box).getdata() if test(px))


def test_render_single_post_has_brand_header_question_answer_and_tagline(tmp_path):
    from PIL import Image
    out = meme_maker.render_infographic(_art(tmp_path), "Why does ice form from the outside in?",
                                        "Ice forms as water loses heat and often starts at the cold surfaces.", tmp_path / "o.jpg", number=7)
    with Image.open(out) as img:
        w, h = img.size
        assert img.size == meme_maker.SIZE_IG and img.getpixel((3, h // 2)) == pytest.approx(meme_maker.INFO_NAVY, abs=6)
        assert _count(img, (0, 0, w // 2, int(h * 0.09)), lambda p: p[0] > 220 and p[1] > 180 and p[2] < 60) > 300  # EVERYDAY amarillo
        assert _count(img, (w // 2, 0, w, int(h * 0.09)), lambda p: p[2] > 200 and p[0] < 60) > 100  # SCIENCE • 07 azul electrico
        assert _count(img, (0, int(h * 0.15), w, int(h * 0.4)), lambda p: min(p) > 230) > 3000  # pregunta en blanco
        assert _count(img, (0, int(h * 0.93), w, h), lambda p: p[0] > 220 and p[1] > 180 and p[2] < 60) > 300  # lema amarillo
        assert img.getpixel((w // 2, int(h * 0.55)))[0] > 150  # ilustracion al centro


def test_render_slides_differ_by_role_and_stay_inside_margins(tmp_path):
    from PIL import Image
    art = _art(tmp_path, (90, 120, 160))
    hook = meme_maker.render_carousel_slide(art, "WHY DOES THE SKY LOOK BLUE", "ignored on the hook slide", tmp_path / "s1.jpg", 1)
    last = meme_maker.render_carousel_slide(art, "Freezing starts where heat escapes", "Under ordinary conditions ice grows inward from the cold surfaces.",
                                            tmp_path / "s5.jpg", 5)
    with Image.open(hook) as a, Image.open(last) as b:
        w, h = a.size
        footer = (0, int(h * 0.93), w, h)
        assert _count(a, footer, lambda p: p[0] > 220 and p[1] > 180 and p[2] < 60) > 150  # SWIPE →
        assert _count(b, footer, lambda p: p[0] > 220 and p[1] > 180 and p[2] < 60) > 300  # Follow Everyday Explained for more.
        art_rows = lambda im: sum(1 for y in range(0, h, 10) if max(abs(c - n) for c, n in zip(im.getpixel((w // 2, y)), (90, 120, 160))) < 20)
        assert art_rows(a) > art_rows(b)  # sin explicacion, la ilustracion del gancho es mas alta
        for img in (a, b):
            for box in ((0, 0, int(w * 0.03), h), (int(w * 0.97), 0, w, h)):
                assert max(abs(c - n) for px in img.crop(box).getdata() for c, n in zip(px, meme_maker.INFO_NAVY)) < 12


def test_hashtags_are_limited_for_facebook():
    post = _post(caption=REPLY.split("CAPTION: ")[1] + " #Extra")
    assert len(bp._limit_hashtags(post["caption"], 5).split("#")) - 1 == 5


# ---- Formato B: carrusel ---------------------------------------------------------------------------------------

def test_carousel_reply_parses_into_five_slides_and_a_valid_caption():
    c = bp._parse_everyday_carousel(CAROUSEL_REPLY)
    assert len(c["slides"]) == 5 and c["hook"].startswith("Why ice") and c["slides"][0]["text"] == ""
    assert c["slides"][2]["title"] == "Heat leaves through the surface" and c["slides"][4]["text"].startswith("Under ordinary")
    assert all(len(s["image_prompt"].split()) >= 8 for s in c["slides"])
    assert c["caption"].endswith("#Freezing") and "?" in c["caption"]


@pytest.mark.parametrize("mutate,match", [
    (lambda t: t.replace("SLIDE 5\n", "SLIDE 6\n"), "exactamente 5"),
    (lambda t: t[:t.index("SLIDE 5")] + "CAPTION:" + t.split("CAPTION:")[1], "exactamente 5"),
    (lambda t: t.replace("TITLE: Water freezes at the edges first", "TITLE: " + " ".join(["word"] * 11)), "titulo de la diapositiva 2"),
    (lambda t: t.replace("TEXT: The outside touches colder air or walls, so it loses heat first and drops below freezing before the center does.", "TEXT: Short."), "texto de la diapositiva 3"),
    (lambda t: t.replace("#EverydayExplained #Science #Physics #Freezing", "#One"), "hashtags"),
    (lambda t: t.replace("Where did you first notice this?", "Notice this."), "pregunta"),
    (lambda t: "nonsense", "formato"),
])
def test_carousel_rejects_bad_formats(mutate, match):
    with pytest.raises(auto_pipeline.PipelineError, match=match):
        bp._parse_everyday_carousel(mutate(CAROUSEL_REPLY))


def test_generate_carousel_composes_five_branded_slides_and_a_cover(tmp_path, monkeypatch):
    import app
    from PIL import Image
    monkeypatch.setattr(bp, "_generate_text_with_chain", lambda *a, **k: CAROUSEL_REPLY)
    monkeypatch.setattr(bp, "_gaming_history_block", lambda **k: "")
    seen = {}

    def fake_clips(story, dest, unattended, start_index=0, provider="", generate_video=False, aspect=""):
        seen.update(prompts=[p["prompt"] for p in story["prompts"]], provider=provider, aspect=aspect)
        dest.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(len(story["prompts"])):
            Image.new("RGB", (1024, 1024), (40 + 30 * i, 90, 140)).save(dest / f"scene_{i:03d}.jpg")
            paths.append(str(dest / f"scene_{i:03d}.jpg"))
        return paths

    monkeypatch.setattr(auto_pipeline, "generate_clips", fake_clips)
    stages = []
    result = bp.generate_everyday_carousel("Everyday Explained", "give me the next Everyday Explained carousel", "wan2gp",
                                           tmp_path / "post", on_stage=stages.append)
    assert stages == ["idea", "imagen", "diseño"] and seen["provider"] == "wan2gp" and seen["aspect"] == "1:1"
    assert all("absolutely no text" in p for p in seen["prompts"]) and len(seen["prompts"]) == 5
    assert [Path(p).name for p in result["slides"]] == [f"slide_{i}.jpg" for i in range(1, 6)]
    for path in result["slides"] + [str(tmp_path / "post" / "cover.jpg")]:
        with Image.open(path) as img:
            assert img.size == meme_maker.SIZE_IG
    assert not (tmp_path / "post" / "art").exists()  # las ilustraciones sueltas se limpian


# ---- lotes ----------------------------------------------------------------------------------------------------

def _stub_create(monkeypatch):
    monkeypatch.setattr(bp, "_load", lambda: {})
    monkeypatch.setattr(bp, "_save", lambda projects: None)
    monkeypatch.setattr(bp, "existing_slots", lambda *a, **k: [])


@pytest.mark.parametrize("content_type,hours", [("everyday_image", (10, 19)), ("everyday_carousel", (14,)), ("mix_everyday", (10, 14, 19))])
def test_create_project_is_facebook_only_with_its_own_daily_slots(monkeypatch, content_type, hours):
    _stub_create(monkeypatch)
    from zoneinfo import ZoneInfo
    project = bp.create_project(
        "Everyday Explained", 6, 1,  # per_day pedido = 1: la pagina impone sus 3 franjas
        {"facebook": {"page_id": "1"}, "instagram": {"page_id": "1"}, "youtube": True}, {}, content_type=content_type,
    )
    assert set(project["networks"]) == {"facebook"} and project["per_day"] == len(hours)
    days = {}
    for v in project["videos"]:
        dt = datetime.fromisoformat(v["scheduled_at"])
        assert bp.BATCH_HOUR_START <= dt.hour <= bp.BATCH_HOUR_END
        assert dt.astimezone(ZoneInfo(bp.US_AUDIENCE_TZ)).hour in hours
        days.setdefault(dt.date(), []).append(dt)
    assert set(len(v) for v in days.values()) <= {len(hours)} | {6 % len(hours) or len(hours)}  # dias completos de su tamano


def test_mixed_batch_publishes_individual_carousel_individual_every_day(monkeypatch):
    _stub_create(monkeypatch)
    project = bp.create_project("Everyday Explained", 6, 3, {"facebook": {"page_id": "1"}}, {}, content_type="mix_everyday")
    assert [bp.item_type(project, v) for v in project["videos"]] == ["everyday_image", "everyday_carousel", "everyday_image"] * 2


# ---- publicacion en Facebook ------------------------------------------------------------------------------------

class _Resp:
    def __init__(self, payload, ok=True):
        self._p, self.ok, self.status_code = payload, ok, 200 if ok else 400

    def json(self):
        return self._p


def test_publish_carousel_uploads_unpublished_photos_then_attaches_them_to_one_scheduled_post(tmp_path, monkeypatch):
    import time
    from PIL import Image
    paths = []
    for i in range(3):
        Image.new("RGB", (40, 40), (i * 40, 0, 0)).save(tmp_path / f"s{i}.jpg")
        paths.append(str(tmp_path / f"s{i}.jpg"))
    monkeypatch.setattr(fp.meta_auth, "get_credentials", lambda page_id=None: ("PG", "TOKEN", None))
    monkeypatch.setattr(fp.meta_auth, "validate", lambda page_id: {"ok": True})
    monkeypatch.setattr(fp, "_save_next_slot", lambda ts: None)
    monkeypatch.delenv("FB_FEED_TARGET_COUNTRIES", raising=False)
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        return _Resp({"id": f"photo{len(calls)}"} if url.endswith("/photos") else {"id": "PG_999"})

    monkeypatch.setattr(fp, "_post_with_retry", fake_post)
    result = fp.publish_carousel(paths, "Caption #A", page_id="PG", scheduled_time=time.time() + 7200)
    assert result["ok"] and result["post_id"] == "PG_999" and result["scheduled_time"]
    photos, feed = calls[:3], calls[3]
    assert all(u.endswith("/PG/photos") and kw["data"]["published"] == "false" for u, kw in photos)
    url, kw = feed
    assert url.endswith("/PG/feed") and kw["data"]["message"] == "Caption #A"
    assert [kw["data"][f"attached_media[{n}]"] for n in range(3)] == ['{"media_fbid": "photo1"}', '{"media_fbid": "photo2"}', '{"media_fbid": "photo3"}']
    assert kw["data"]["published"] == "false" and "scheduled_publish_time" in kw["data"]


def test_publish_carousel_needs_two_images_and_reports_upload_errors(tmp_path, monkeypatch):
    from PIL import Image
    Image.new("RGB", (40, 40)).save(tmp_path / "a.jpg")
    monkeypatch.setattr(fp.meta_auth, "get_credentials", lambda page_id=None: ("PG", "T", None))
    assert "al menos 2" in fp.publish_carousel([str(tmp_path / "a.jpg")], "c")["error"]
    assert "No se encontró" in fp.publish_carousel([str(tmp_path / "a.jpg"), str(tmp_path / "nope.jpg")], "c")["error"]
    monkeypatch.setattr(fp.meta_auth, "validate", lambda page_id: {"ok": True})
    monkeypatch.setattr(fp, "_post_with_retry", lambda url, **kw: _Resp({"error": {"message": "x", "code": 1}}, ok=False))
    assert fp.publish_carousel([str(tmp_path / "a.jpg")] * 2, "c")["ok"] is False


def test_batch_publish_uses_the_carousel_when_the_item_has_slides(monkeypatch):
    sent = {}
    project = {"id": "p", "name": "Lote", "type": "everyday_carousel", "videos": [{
        "index": 0, "status": "publishing", "item_type": "everyday_carousel", "video_path": "cover.jpg",
        "slides": ["s1.jpg", "s2.jpg"], "caption": "Hi #A #B #C", "scheduled_at": None, "published_at": {}}]}
    monkeypatch.setattr(bp, "get_project", lambda pid: project)
    monkeypatch.setattr(fp, "publish_carousel", lambda slides, text, page_id=None, scheduled_time=None: sent.update(slides=slides, text=text) or {"ok": True, "post_id": "9"})
    monkeypatch.setattr(fp, "publish_photo", lambda *a, **k: pytest.fail("un carrusel no usa publish_photo"))
    import app
    monkeypatch.setattr(app, "_record_published_facebook", lambda *a, **k: None)
    monkeypatch.setattr(bp, "_publish_networks", lambda pid, i, fns, **k: sent.update(result=fns["facebook"](project["videos"][0], "PG")))
    bp._publish_batch_image_post("p", 0)
    assert sent["slides"] == ["s1.jpg", "s2.jpg"] and sent["text"].startswith("Hi") and sent["result"]["ok"]


# ---- paginas y app ------------------------------------------------------------------------------------------------

def test_everyday_page_is_registered_from_env(monkeypatch):
    import meta_auth
    monkeypatch.setenv("FB_PAGE_1_ID", "1444420808745609")
    monkeypatch.setenv("FB_PAGE_1_NAME", "Everyday Explained")
    monkeypatch.setenv("FB_PAGE_1_TOKEN", "x")
    monkeypatch.delenv("FB_PAGE_2_ID", raising=False)
    assert meta_auth.list_pages() == [{"page_id": "1444420808745609", "name": "Everyday Explained"}]
    assert "token" not in str(meta_auth.list_pages()).lower()


def test_app_exposes_the_three_types_and_facebook_only_endpoints():
    src = (Path(__file__).parent.parent / "app.py").read_bytes().decode("utf-8")
    assert src.count('<option value="everyday_image">') == 2
    assert src.count('<option value="everyday_carousel">') == 1 and src.count('<option value="mix_everyday">') == 1
    assert 'everyday_image: "give me the next Everyday Explained post"' in src
    assert 'EVERYDAY_TYPES = ["everyday_image", "everyday_carousel", "mix_everyday"]' in src
    assert "individual 10:00 y 19:00 · carrusel 14:00 · mixto los tres" in src
    assert '"everyday": "everyday_image"' in src and 'everyday_image: "everyday"' in src
    assert "_facebook_only(" in src
