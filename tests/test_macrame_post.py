# -*- coding: utf-8 -*-
"""Post de imagen de macramé y lote/mixto de macramé. Sin red ni job_store real."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline as bp
import job_store

MACRAME_REPLY = """IDEA: Llavero con nudo plano en 15 minutos
HOOK: ¿Y si con 2 metros de cuerda haces esto?
IMAGE_PROMPT: macro tutorial photo, overhead flat-lay of a finished flat square knot keychain in natural off-white braided cotton cord, female hands with short nails, light oak table, beige linen background, soft window light
TOP: LLAVERO EN 15 MINUTOS
BOTTOM: 2 cordones · nudo plano · principiante
CAPTION: ¡LLAVERO EN 15 MINUTOS! 🧶
Un llavero con nudo plano apto para principiantes.
Necesitas 2 cordones de 1,2 m y una argolla.
Comenta EBOOK si quieres mi guía. ¿Qué color harías tú?
#macramefacil #macrameparaprincipiantes #diyboho #manualidadesfaciles #hechoamano #tutorialmacrame"""


def test_profile_and_prompt():
    p = bp.IMAGE_POST_PROFILES["macrame_image"]
    assert p["kind"] == "macrame_post" and p["slot_hours"] == bp.MACRAME_SLOT_HOURS and p["network_offsets"] == {}
    for day in range(7):
        for slot in range(2):
            assert p["week"][day][slot] in p["templates"]
    assert (bp._PROMPTS_DIR / "macrame_post_system.md").exists()


def test_template_depends_on_weekday_and_hour():
    assert bp._image_post_template("macrame_image", datetime(2026, 10, 5, 13).isoformat()) == "M1"  # lunes
    assert bp._image_post_template("macrame_image", datetime(2026, 10, 5, 19).isoformat()) == "M4"


def test_reply_parses_and_hashtags_are_limited():
    post = bp._parse_gaming_post(MACRAME_REPLY)
    assert post["top"] == "LLAVERO EN 15 MINUTOS" and "Comenta EBOOK" in post["caption"]
    tags = bp.IMAGE_POST_PROFILES["macrame_image"]["max_hashtags"]
    assert bp._limit_hashtags(post["caption"], tags["facebook"]).endswith("#diyboho")
    assert bp._limit_hashtags(post["caption"], tags["instagram"]).endswith("#tutorialmacrame")


def _stub_create(monkeypatch):
    monkeypatch.setattr(bp, "_load", lambda: {})
    monkeypatch.setattr(bp, "_save", lambda projects: None)
    monkeypatch.setattr(bp, "existing_slots", lambda *a, **k: [])


def test_mixed_macrame_batch_alternates_video_and_post(monkeypatch):
    _stub_create(monkeypatch)
    project = bp.create_project(
        "MACRAME CREATIVO", 6, 2, {"facebook": {"page_id": "1"}}, {}, content_type="mix_macrame",
    )
    assert [bp.item_type(project, v) for v in project["videos"]] == ["video", "macrame_image"] * 3
    assert project["type"] == "mix_macrame" and project["network_offsets"] == {}
    assert {datetime.fromisoformat(v["scheduled_at"]).hour for v in project["videos"]} <= {13, 19}


def test_next_mixed_kind_alternates_and_only_advances_on_request(monkeypatch):
    store = {}
    monkeypatch.setattr(job_store, "load", lambda name: dict(store.get(name, {})))
    monkeypatch.setattr(job_store, "save", lambda name, data: store.__setitem__(name, dict(data)))
    assert bp.next_mixed_kind("mix_macrame") == "video"
    assert bp.next_mixed_kind("mix_macrame") == "video"  # consultar no avanza
    bp.next_mixed_kind("mix_macrame", advance=True)
    assert bp.next_mixed_kind("mix_macrame") == "macrame_image"
    bp.next_mixed_kind("mix_macrame", advance=True)
    assert bp.next_mixed_kind("mix_macrame") == "video"
    assert bp.next_mixed_kind("mix_gaming") == "gaming_clip"  # cada mixto lleva su contador



def test_ninio_and_macrame_posts_use_editorial_card(tmp_path):
    from PIL import Image
    import meme_maker
    for key in ("ninio_image", "macrame_image"):
        assert set(bp.IMAGE_POST_PROFILES[key]["card"]) == {"panel", "ink", "accent"}
    assert "card" not in bp.IMAGE_POST_PROFILES["gaming_image"]  # gaming sigue siendo meme
    base = tmp_path / "b.jpg"
    Image.new("RGB", (1024, 1536), (10, 120, 60)).save(base)
    card = bp.IMAGE_POST_PROFILES["ninio_image"]["card"]
    out = meme_maker.render_card(base, "NO ES UNA LUCHA", "es un paso", tmp_path / "c.jpg", meme_maker.SIZE_IG, **card)
    with Image.open(out) as img:
        assert img.size == meme_maker.SIZE_IG
        assert img.getpixel((540, 100)) == pytest.approx((10, 120, 60), abs=6)  # foto limpia arriba
        assert img.getpixel((5, 1300)) == pytest.approx(card["panel"], abs=3)  # panel de marca abajo


def test_ai_ad_prompt_carries_exact_text_and_layout():
    profile = bp.IMAGE_POST_PROFILES["ninio_image"]
    post = {"top": "NO ES UNA LUCHA", "bottom": "es un paso.", "caption": "x " + chr(10) + "Escribe RITUAL y te mando el paso a paso"}
    out = bp._ai_ad_layout(post, profile, "Niño Selectivo, Familia en Paz")
    assert '"NO ES UNA LUCHA"' in out and '"es un paso"' in out and "COMENTA EBOOK" in out
    assert '"Niño Selectivo, Familia en Paz"' in out and "Estrategias prácticas" in out and "1:1" in out
    assert "top 55%" in out and "NO text" in out and "ñ" in out
    # sin remate no hay subtitulo; sin palabra en el caption se usa la de la pagina
    bare = bp._ai_ad_layout({"top": "HOLA", "bottom": "", "caption": ""}, profile, "P")
    assert "subtitle" not in bare and "COMENTA EBOOK" in bare
    macrame = bp._ai_ad_layout({"top": "LLAVERO FÁCIL", "bottom": "", "caption": ""}, bp.IMAGE_POST_PROFILES["macrame_image"], "M")
    assert "COMENTA EBOOK" in macrame and "badges" not in macrame
    assert {k: bp.IMAGE_POST_PROFILES[k]["ai_design"] for k in bp.IMAGE_POST_PROFILES} == {
        "gaming_image": "news", "ninio_image": "ad", "macrame_image": "ad", "historias_image": "story"}
    meme = bp._ai_meme_layout({"top": "me mato", "bottom": ""})
    assert '"ME MATO"' in meme and "bottom text" not in meme


def test_fit_post_crops_designed_image_to_4_5(tmp_path):
    from PIL import Image
    import meme_maker
    Image.new("RGB", (1024, 1024), (200, 180, 160)).save(tmp_path / "a.png")
    out = meme_maker.fit_post(tmp_path / "a.png", tmp_path / "o.jpg", meme_maker.SIZE_IG)
    with Image.open(out) as img:
        assert img.size == meme_maker.SIZE_IG


def test_delete_one_item_removes_it_and_whole_batch_when_empty(monkeypatch, tmp_path):
    store = {"p1": {"id": "p1", "type": "video", "page_name": "P", "status": "running", "videos": [
        {"index": 0, "status": "ready", "video_path": str(tmp_path / "a.mp4")},
        {"index": 1, "status": "generating"},
        {"index": 2, "status": "pending"},
    ]}}
    monkeypatch.setattr(bp, "_load", lambda: store)
    monkeypatch.setattr(bp, "_save", lambda projects: None)
    purged = []
    monkeypatch.setattr(bp, "_purge_video_assets", lambda pid, v: purged.append(v["index"]))
    assert bp.delete_video("p1", 1) is False  # se esta generando
    assert bp.delete_video("p1", 0) and bp.delete_video("p1", 2)
    assert [v["status"] for v in store["p1"]["videos"]] == ["removed", "generating", "removed"] and purged == [0, 2]
    assert bp.delete_video("nope", 0) is False and bp.delete_video("p1", 9) is False
    store["p1"]["videos"][1]["status"] = "ready"
    assert bp.delete_video("p1", 1) and "p1" not in store  # no queda nada activo: se borra el lote


def test_removed_items_do_not_block_batch_completion_or_slots(monkeypatch):
    project = {"id": "p", "status": "running", "page_name": "P", "networks": {}, "videos": [
        {"index": 0, "status": "published", "scheduled_at": "2026-10-10T12:00:00"},
        {"index": 1, "status": "removed", "scheduled_at": "2026-10-10T13:00:00"},
    ]}
    store = {"p": project}
    monkeypatch.setattr(bp, "_load", lambda: store)
    monkeypatch.setattr(bp, "_save", lambda projects: None)
    bp.run_publish_tick([project])
    assert project["status"] == "done"
    assert bp.existing_slots("P", {}) == [datetime(2026, 10, 10, 12)]


def test_mixed_historias_alternates_video_and_post_with_rescue_profile(monkeypatch):
    _stub_create(monkeypatch)
    project = bp.create_project(
        "HISTORIAS", 4, 2, {"facebook": {"page_id": "1"}, "youtube": True}, {}, content_type="mix_historias",
    )
    assert [bp.item_type(project, v) for v in project["videos"]] == ["video", "historias_image"] * 2
    assert project["video_settings"].get("copy_profile") == bp.RESCUE_PROFILE  # el video sigue siendo de rescate
    assert {datetime.fromisoformat(v["scheduled_at"]).hour for v in project["videos"]} <= set(bp.HISTORIAS_SLOT_HOURS)
    p = bp.IMAGE_POST_PROFILES["historias_image"]
    assert (bp._PROMPTS_DIR / "historias_post_system.md").exists() and p["ai_design"] == "story"
    for day in range(7):
        for slot in range(2):
            assert p["week"][day][slot] in p["templates"]


def test_historias_story_layout_looks_like_viral_facebook_photos():
    post = {"top": "NADIE SE ACERCABA", "bottom": "hasta que se quedó a esperar"}
    collage = bp._ai_story_layout(post, "H1", "HISTORIAS")
    assert "collage" in collage and '"NADIE SE ACERCABA"' in collage and "speech bubbles" in collage
    assert "EBOOK" not in collage and "pill" not in collage
    voice = bp._ai_story_layout(post, "H2", "HISTORIAS")
    assert "handwritten" in voice and "@HISTORIAS" in voice and '"hasta que se quedó a esperar"' in voice
    sign = bp._ai_story_layout(post, "H3", "HISTORIAS")
    assert "wooden sign" in sign and '"NADIE SE ACERCABA"' in sign
    clean = bp._ai_story_layout(post, "H4", "HISTORIAS")
    assert "NO text" in clean and "NADIE" not in clean


def test_historias_templates_rotate_by_history(monkeypatch):
    hist = []
    monkeypatch.setattr(bp, "_load_gaming_history", lambda store: hist)
    seen = []
    for _ in range(8):
        t = bp._image_post_template("historias_image", datetime(2026, 10, 5, 11).isoformat())
        seen.append(t)
        hist.append({"template": t})
    assert seen == ["H1", "H2", "H3", "H4"] * 2
    # los otros nichos siguen la semana tipo
    assert bp._image_post_template("macrame_image", datetime(2026, 10, 5, 13).isoformat()) == "M1"


def test_story_copy_is_written_by_ai_not_a_script_extract(monkeypatch):
    reply = ("FACEBOOK:\nA veces una mirada lo dice todo 🐾 Nadie la miraba, hasta que alguien se quedó a esperar con ella. "
             "¿Me dejas un corazoncito? ❤️ Cuéntame si la habrías adoptado en los comentarios, por favor.\n#RescateAnimal #AmorAnimal\n"
             "INSTAGRAM:\nNadie sabía su nombre 🐕 pero ella esperaba cada tarde en la misma esquina del pueblo con paciencia. "
             "Comenta AMOR si también crees en las segundas oportunidades de verdad.\n#RescateAnimal #PerrosRescatados #AdoptaNoCompres #AmorAnimal")
    monkeypatch.setattr(bp.text_provider, "generate_text", lambda *a, **k: (reply, "x"))
    out = bp._build_publish_content("[LUNA] esperó siete meses. Marco la vio.", "t", story_copy=True)
    assert out["facebook_description"].startswith("A veces una mirada") and "#RescateAnimal" in out["facebook_description"]
    assert out["instagram_description"] != out["facebook_description"]
    # sin IA: texto de rescate (hashtags validados) y sin corchetes
    monkeypatch.setattr(bp.text_provider, "generate_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    out = bp._build_publish_content("[LUNA] esperó siete meses. Marco la vio.", "t", story_copy=True)
    assert "[" not in out["facebook_description"] and "#RescateAnimal" in out["facebook_description"]
    assert "#luna" not in out["facebook_description"]


def test_used_names_collects_proper_nouns_from_past_scripts():
    names = bp._used_names(["Durante siete meses, Luna esperó en la plaza. Ese día el herrero Marco la vio.", "[PERRO] corrió a Lima."])
    assert {"Luna", "Marco", "Lima"} <= set(names) and "Durante" not in names and "PERRO" not in names


def test_future_slot_keeps_todays_slot_at_night_and_ignores_other_pages(monkeypatch):
    class FakeNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 9, 2, 0)
    monkeypatch.setattr(bp, "datetime", FakeNow)
    projects = {
        "mine": {"page_name": "MACRAME CREATIVO", "videos": [{"status": "generating", "scheduled_at": "2026-10-09T13:00:00"}]},
        "other": {"page_name": "HISTORIAS", "videos": [{"status": "ready", "scheduled_at": "2026-10-09T13:00:00"}]},
        "same": {"page_name": "MACRAME CREATIVO", "videos": [{"status": "ready", "scheduled_at": "2026-10-09T19:00:00"}]},
    }
    # a las 2 a. m. las 13:00 de hoy siguen siendo de hoy, y otra pagina a la misma hora no lo empuja
    assert bp._future_slot(projects, ("mine", 0), "2026-10-09T13:00:00") == datetime(2026, 10, 9, 13)
    # otro item de la MISMA pagina a menos de una hora si lo mueve un dia
    projects["same"]["videos"][0]["scheduled_at"] = "2026-10-09T13:30:00"
    assert bp._future_slot(projects, ("mine", 0), "2026-10-09T13:00:00") == datetime(2026, 10, 10, 13)
