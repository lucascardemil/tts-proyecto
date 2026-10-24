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

REPLY = """IDEA: How a thermos keeps drinks hot: a vacuum layer between two walls stops heat from escaping.
HOOK: How a thermos keeps drinks hot
IMAGE_PROMPT: a stainless steel vacuum thermos flask cut in half, showing the outer wall, the empty vacuum gap, the inner wall, the plastic lid and the hot coffee inside
TOP: HOW A THERMOS KEEPS DRINKS HOT
BOTTOM: Insulated lid | Hot drink | Inner steel wall | Vacuum gap | Outer steel wall
CAPTION: Ever wondered how a thermos keeps coffee hot for hours? ☕

A thermos has two walls with almost no air between them. With nothing to carry the heat across the gap, your drink stays warm much longer.

Key takeaway: the empty space between the walls is what blocks the heat.

What do you keep in your thermos?

#EverydayExplained #HowThingsWork #Science #DidYouKnow"""

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


# ---- Formato A: infografia individual (la IA dibuja todo el texto) -----------------------------------------------------

def test_profiles_are_facebook_only_and_scheduled_by_format():
    for key, hours in (("everyday_image", [10, 19]), ("everyday_carousel", [14])):
        p = bp.IMAGE_POST_PROFILES[key]
        assert p["slot_hours"] == hours and p["facebook_only"]
        assert p["max_hashtags"]["facebook"] == 5 and p["history_store"] == "everyday_post_history"
    single = bp.IMAGE_POST_PROFILES["everyday_image"]
    assert single["ai_design"] == "diagram" and single["rotate"] and list(single["templates"]) == ["S1", "S2", "S3", "S4", "S5"]
    assert bp.IMAGE_POST_PROFILES["everyday_carousel"]["carousel"] is True
    assert bp.MIXED_TYPES["mix_everyday"] == ("everyday_image", "everyday_carousel")
    assert bp.MIXED_SLOT_HOURS["mix_everyday"] == [10, 14, 19]  # individual 10 y 19, carrusel 14: 3 al dia
    assert bp._facebook_only("mix_everyday") and bp._facebook_only("everyday_carousel") and not bp._facebook_only("macrame_image")
    for name in ("everyday_post_system.md", "everyday_carousel_system.md"):
        assert (bp._PROMPTS_DIR / name).exists()


def test_system_prompts_load_without_placeholders_and_carry_the_rules():
    single, carousel = bp._text_system_prompt("everyday_post"), bp._text_system_prompt("everyday_carousel")
    assert "{{" not in single and "MAXIMUM 8 words" in single and "MAXIMUM 4 words" in single
    for style in ("S1 CUTAWAY", "S2 BLUEPRINT", "S3 TYPES", "S4 WRONG VS RIGHT", "S5 WHAT TO CHECK"):
        assert style in single
    assert "Do NOT invent statistics" in single and "ENGAGEMENT QUESTION" in single and "WRONG:" in single
    assert "exactly 5 separate slides" in carousel and "Do NOT invent studies" in carousel
    assert "Follow Everyday Explained for more." in carousel and "no Reels" in carousel


def _post(**over):
    post = bp._parse_gaming_post(REPLY)
    post.update(over)
    return post


def test_valid_reply_parses_and_passes_validation():
    post = _post()
    assert post["top"] == "HOW A THERMOS KEEPS DRINKS HOT"
    assert bp._everyday_labels(post) == ["Insulated lid", "Hot drink", "Inner steel wall", "Vacuum gap", "Outer steel wall"]
    bp._validate_everyday_post(post, "S1")
    bp._validate_everyday_post(post, "S2")


@pytest.mark.parametrize("over,template,match", [
    ({"top": "HOW DOES THE VACUUM THERMOS IN MY KITCHEN KEEP DRINKS HOT FOR SO LONG"}, "S1", "titulo"),
    ({"top": ""}, "S1", "titulo"),
    ({"bottom": "Lid | Drink"}, "S1", "etiquetas"),
    ({"bottom": " | ".join(f"Part {c}" for c in "ABCDEFGH")}, "S1", "etiquetas"),
    ({"bottom": "A very long label that explains too much | Lid | Wall"}, "S1", "etiqueta"),
    ({"bottom": "Lid | Wall | Gap"}, "S3", "4 etiquetas"),
    ({"bottom": "Lid | Wall | Gap | Cap"}, "S4", "WRONG:"),
    ({"caption": "Why does it work?\n\nIt insulates. #A #B"}, "S1", "hashtags"),
    ({"caption": "It works.\n\nIt insulates. #A #B #C"}, "S1", "pregunta"),
])
def test_invalid_posts_are_rejected(over, template, match):
    with pytest.raises(auto_pipeline.PipelineError, match=match):
        bp._validate_everyday_post(_post(**over), template)


def test_types_and_wrong_vs_right_pass_with_their_own_label_shapes():
    bp._validate_everyday_post(_post(bottom="Incandescent | Halogen | Fluorescent | LED"), "S3")
    bp._validate_everyday_post(_post(bottom="WRONG: Overloaded socket | WRONG: Frayed cable | RIGHT: One plug per socket | RIGHT: Intact cable"), "S4")


def test_diagram_prompt_carries_the_exact_title_labels_style_and_signature():
    post = _post(top='HOW A "THERMOS" KEEPS DRINKS HOT')
    out = bp._ai_diagram_layout(post, "S1")
    assert "watercolor-and-ink" in out and "a stainless steel vacuum thermos flask cut in half" in out
    assert "exactly as written: \"HOW A 'THERMOS' KEEPS DRINKS HOT\"" in out  # comillas internas neutralizadas
    for label in ("Insulated lid", "Hot drink", "Inner steel wall", "Vacuum gap", "Outer steel wall"):
        assert f'"{label}"' in out
    assert '"EVERYDAY EXPLAINED"' in out and "4:5" in out and "92%" in out and "No other text" in out
    assert "navy blue" in out


def test_each_style_has_its_own_layout_and_unknown_styles_fall_back_to_cutaway():
    post = _post()
    seen = {bp._ai_diagram_layout(post, k).split(" Vertical 4:5")[0] for k in bp.EVERYDAY_STYLES}
    assert len(seen) == 5
    assert "grid paper" in bp._ai_diagram_layout(post, "S2") and "2x2 grid" in bp._ai_diagram_layout(post, "S3")
    assert "bright yellow background" in bp._ai_diagram_layout(post, "S5")
    assert bp._ai_diagram_layout(post, "S9") == bp._ai_diagram_layout(post, "S1")


def test_wrong_vs_right_labels_go_to_their_panels():
    post = _post(bottom="WRONG: Overloaded socket | RIGHT: One plug per socket | RIGHT: Intact cable | WRONG: Frayed cable")
    out = bp._ai_diagram_layout(post, "S4")
    assert 'top panel (the mistake): "Overloaded socket", "Frayed cable"' in out
    assert 'bottom panel (the correct way): "One plug per socket", "Intact cable"' in out and "WRONG:" not in out


def test_styles_rotate_by_history_never_repeating_the_last(monkeypatch):
    history = []
    monkeypatch.setattr(bp, "_load_gaming_history", lambda store=None: [{"template": t} for t in history])
    picks = []
    for _ in range(6):
        t = bp._image_post_template("everyday_image", None)
        picks.append(t)
        history.append(t)
    assert picks == ["S1", "S2", "S3", "S4", "S5", "S1"]


def test_single_post_generation_needs_flow_and_ends_as_a_4_5_cover(tmp_path, monkeypatch):
    import app
    from PIL import Image
    monkeypatch.setattr(bp, "_generate_text_with_chain", lambda *a, **k: REPLY)
    monkeypatch.setattr(bp, "_gaming_history_block", lambda **k: "")
    with pytest.raises(auto_pipeline.PipelineError, match="solo Flow"):
        bp.generate_gaming_post("Everyday Explained", "give me the next Everyday Explained post", "wan2gp", tmp_path / "a", "S1",
                                profile_key="everyday_image")
    seen = {}

    def fake_clips(story, dest, unattended, start_index=0, provider="", generate_video=False, aspect=""):
        seen.update(prompt=story["prompts"][0]["prompt"], provider=provider, aspect=aspect)
        dest.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (768, 1024), (20, 40, 80)).save(dest / "scene_000.jpg")
        return [str(dest / "scene_000.jpg")]

    monkeypatch.setattr(auto_pipeline, "generate_clips", fake_clips)
    post = bp.generate_gaming_post("Everyday Explained", "give me the next Everyday Explained post", "flow", tmp_path / "b", "S2",
                                   profile_key="everyday_image")
    assert seen["provider"] == "flow" and seen["aspect"] == "3:4"
    assert '"HOW A THERMOS KEEPS DRINKS HOT"' in seen["prompt"] and "grid paper" in seen["prompt"]
    with Image.open(tmp_path / "b" / "cover.jpg") as img:
        assert img.size == meme_maker.SIZE_IG
    assert post["top"] == "HOW A THERMOS KEEPS DRINKS HOT"


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
        {"facebook": {"page_id": "1"}, "instagram": {"page_id": "1"}, "youtube": True}, {"image_provider": "flow"}, content_type=content_type,
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
    project = bp.create_project("Everyday Explained", 6, 3, {"facebook": {"page_id": "1"}}, {"image_provider": "flow"}, content_type="mix_everyday")
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


# ---- diapositivas del carrusel (el texto lo compone el codigo) --------------------------------------------------------

def _art(tmp_path, color=(90, 120, 160), size=(1024, 1024)):
    from PIL import Image
    Image.new("RGB", size, color).save(tmp_path / "art.png")
    return tmp_path / "art.png"


def _count(img, box, test):
    return sum(1 for px in img.crop(box).getdata() if test(px))


def test_render_slides_differ_by_role_and_stay_inside_margins(tmp_path):
    from PIL import Image
    art = _art(tmp_path)
    hook = meme_maker.render_carousel_slide(art, "WHY DOES THE SKY LOOK BLUE", "ignored on the hook slide", tmp_path / "s1.jpg", 1)
    last = meme_maker.render_carousel_slide(art, "Freezing starts where heat escapes", "Under ordinary conditions ice grows inward from the cold surfaces.",
                                            tmp_path / "s5.jpg", 5)
    with Image.open(hook) as a, Image.open(last) as b:
        w, h = a.size
        assert a.size == meme_maker.SIZE_IG
        footer = (0, int(h * 0.93), w, h)
        yellow = lambda p: p[0] > 220 and p[1] > 180 and p[2] < 60
        assert _count(a, footer, yellow) > 150  # SWIPE
        assert _count(b, footer, yellow) > 300  # Follow Everyday Explained for more.
        art_rows = lambda im: sum(1 for y in range(0, h, 10) if max(abs(c - n) for c, n in zip(im.getpixel((w // 2, y)), (90, 120, 160))) < 20)
        assert art_rows(a) > art_rows(b)  # sin explicacion, la ilustracion del gancho es mas alta
        for img in (a, b):
            for box in ((0, 0, int(w * 0.03), h), (int(w * 0.97), 0, w, h)):
                assert max(abs(c - n) for px in img.crop(box).getdata() for c, n in zip(px, meme_maker.INFO_NAVY)) < 12


@pytest.mark.parametrize("content_type", ["everyday_image", "mix_everyday"])
def test_single_infographics_refuse_providers_that_cannot_write_text(monkeypatch, content_type):
    _stub_create(monkeypatch)
    for settings in ({}, {"image_provider": "wan2gp"}, {"image_provider": "whatsapp"}):
        with pytest.raises(ValueError, match="necesita Flow"):
            bp.create_project("Everyday Explained", 3, 2, {"facebook": {"page_id": "1"}}, settings, content_type=content_type)
    # el carrusel solo pide ilustraciones sin texto: cualquier proveedor sirve
    bp.create_project("Everyday Explained", 3, 1, {"facebook": {"page_id": "1"}}, {"image_provider": "wan2gp"}, content_type="everyday_carousel")
