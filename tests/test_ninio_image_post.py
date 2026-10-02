# -*- coding: utf-8 -*-
"""Post de imagen de "Niño Selectivo": perfil, plantillas, parser, texto y lote.
Sin red ni job_store real."""
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline
import meme_maker
import text_provider

NINIO_REPLY = """IDEA: Mito vs realidad: no es maña, es neofobia
HOOK: Si tu hijo escupe todo, no es maña
IMAGE_PROMPT: candid documentary photo of a latin american mom 30yo, light brown skin, close-up of a child's hands pushing a small plate with one spoon of rice, natural window light
TOP: NO ES MAÑA
BOTTOM: es neofobia y es normal
CAPTION: ¡NO ES MAÑA! 🍽️
Entre los 2 y 6 años rechazar lo nuevo es normal.
Hoy prueba una porción mini y come tú primero.
Escribe CALMA y te envío el audio de 1 minuto.
#ninoselectivo #alimentacioninfantil #pickyeater #mamaprimeriza #crianzarespetuosa"""


def test_profile_keys_and_templates():
    p = batch_pipeline.IMAGE_POST_PROFILES["ninio_image"]
    assert p["kind"] == "ninio_post" and p["slot_hours"] == [12, 19] and p["network_offsets"] == {}
    for day in range(7):  # la semana tipo solo usa plantillas definidas
        for slot in range(2):
            assert p["week"][day][slot] in p["templates"]
    assert Path(batch_pipeline._PROMPTS_DIR / "ninio_post_system.md").exists()


def test_template_depends_on_weekday_and_hour():
    monday_noon = datetime(2026, 10, 5, 12).isoformat()  # lunes
    assert batch_pipeline._image_post_template("ninio_image", monday_noon) == "N1"
    assert batch_pipeline._image_post_template("ninio_image", datetime(2026, 10, 5, 19).isoformat()) == "N4"
    # gaming no cambia
    assert batch_pipeline._image_post_template("gaming_image", monday_noon) == "T1"


def test_reply_parses_with_gaming_parser():
    post = batch_pipeline._parse_gaming_post(NINIO_REPLY)
    assert post["top"] == "NO ES MAÑA" and post["bottom"].startswith("es neofobia")
    assert "Escribe CALMA" in post["caption"] and "#ninoselectivo" in post["caption"]


def test_hashtags_limited_per_network():
    caption = batch_pipeline._parse_gaming_post(NINIO_REPLY)["caption"]
    tags = batch_pipeline.IMAGE_POST_PROFILES["ninio_image"]["max_hashtags"]
    assert batch_pipeline._limit_hashtags(caption, tags["facebook"]).endswith("#pickyeater")
    assert batch_pipeline._limit_hashtags(caption, tags["instagram"]).endswith("#crianzarespetuosa")


def test_chain_does_not_require_image_blocks_for_post_kind(monkeypatch):
    monkeypatch.setattr(text_provider, "available_backends", lambda: ["freellm:auto"])
    monkeypatch.setattr(text_provider, "generate_text", lambda *a, **k: (NINIO_REPLY, "freellm"))
    out = batch_pipeline._generate_text_with_chain("ninio_post", "P", "t", "s1")
    assert out == NINIO_REPLY


def test_meme_text_color_is_configurable(tmp_path):
    base = tmp_path / "base.jpg"
    Image.new("RGB", (1080, 1350), (90, 90, 90)).save(base)
    out = meme_maker.render_meme(base, "NO ES MAÑA", "es normal", tmp_path / "o.jpg", meme_maker.SIZE_IG, fill=(255, 221, 51))
    with Image.open(out) as img:
        assert img.size == meme_maker.SIZE_IG
        yellow = sum(1 for px in img.crop((0, 0, 1080, 300)).getdata() if px[0] > 220 and px[1] > 180 and px[2] < 120)
    assert yellow > 200


def test_create_project_ninio_image_uses_meal_slots_and_no_offsets(monkeypatch):
    monkeypatch.setattr(batch_pipeline, "_load", lambda: {})
    monkeypatch.setattr(batch_pipeline, "_save", lambda projects: None)
    monkeypatch.setattr(batch_pipeline, "_best_hour_for_networks", lambda networks: None)
    project = batch_pipeline.create_project(
        page_name="Niño Selectivo", total_videos=4, per_day=2,
        networks={"facebook": {"page_id": "1"}, "instagram": {"page_id": "1"}},
        video_settings={}, trigger_message="t", content_type="ninio_image",
    )
    assert project["type"] == "ninio_image" and project["network_offsets"] == {}
    hours = {datetime.fromisoformat(v["scheduled_at"]).hour for v in project["videos"]}
    assert hours <= {12, 19}


def test_ninio_food_hint_rotates_and_is_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(text_provider, "available_backends", lambda: ["freellm:auto"])
    monkeypatch.setattr(text_provider, "generate_text", lambda system, user, **k: (sent.append(user) or NINIO_REPLY, "freellm"))
    for _ in range(30):
        batch_pipeline._generate_text_with_chain("ninio_post", "P", "dame un post", "s")
    foods = [u.split("Alimento protagonista de esta pieza: ")[1].split(".")[0] for u in sent]
    assert all(f in batch_pipeline.NINIO_FOODS for f in foods)
    assert all(a != b for a, b in zip(foods, foods[1:]))  # nunca el mismo dos veces seguidas
    assert len(set(foods)) > 5  # variedad real, no solo brocoli


def test_food_hint_not_added_to_other_kinds(monkeypatch):
    sent = []
    monkeypatch.setattr(text_provider, "available_backends", lambda: ["freellm:auto"])
    monkeypatch.setattr(text_provider, "generate_text", lambda system, user, **k: (sent.append(user) or NINIO_REPLY, "freellm"))
    batch_pipeline._generate_text_with_chain("gaming", "P", "dame un post", "s")
    assert "Alimento protagonista" not in sent[0]
