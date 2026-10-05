import batch_pipeline as bp
import video_maker


def test_page_detection_ignores_accents_and_case():
    for name in ("Bebé Héroe", "BEBE HEROE", "bebe heroe oficial"):
        assert bp.is_bebe_heroe_page(name)
        assert bp._text_kind_for_page(name) == "bebe_heroe"
    assert not bp.is_bebe_heroe_page("HISTORIAS")
    assert bp._text_kind_for_page("Niño Selectivo, Familia en Paz") == "ninio_selectivo"


def test_prompt_file_and_story_kind_registered():
    assert "bebe_heroe" in bp.STORY_TEXT_KINDS
    text = bp._text_system_prompt("bebe_heroe")
    assert "[BEBE]" in text and "Imagen 4" in text


def test_idea_pick_avoids_recent_ones():
    first = bp.BEBE_HEROE_IDEAS[0][0]
    projects = {"p": {"page_name": "Bebé Héroe", "videos": [
        {"scheduled_at": "2026-10-01T20:00:00", "bebe_idea": first},
    ]}}
    assert bp._recent_bebe_ideas(projects) == [first]
    for _ in range(30):
        assert bp._pick_bebe_idea(projects)[0] != first


def test_copy_uses_story_question_summary_and_hashtags():
    video = {
        "story_summary": "Un bebé bombero rescata un gatito 🐱",
        "script_text": "Mamá me dijo que no saliera. Pero escuché un llanto. Así que subí. ¿Crees que hice bien?",
        "bebe_tag": "gatito",
    }
    copy = bp._bebe_heroe_copy(video)
    assert copy["title"] == video["story_summary"]
    assert copy["description"].startswith("¿Crees que hice bien? 🥺❤️")
    assert "#BebéHéroe #HistoriasQueInspiran #Rescate #gatito" in copy["description"]


def test_voice_prefers_chosen_clone_then_baby_voice(monkeypatch):
    import tts_engine
    monkeypatch.setattr(tts_engine, "list_custom_voices", lambda: {"voz_de_bebe": {"label": "Voz de Bebé"}, "otra": {"label": "x"}})
    assert bp._bebe_heroe_voice("custom:otra") == "custom:otra"
    assert bp._bebe_heroe_voice("alejo_calm") == "custom:voz_de_bebe"
    monkeypatch.setattr(tts_engine, "list_custom_voices", lambda: {})
    assert bp._bebe_heroe_voice("alejo_calm") == "alejo_calm"


def test_timeline_has_theme_zoom_and_assets():
    scenes = [{"name": f"scene_{i:03d}.jpg", "type": "image", "native_duration": None} for i in range(4)]
    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate("uno dos tres cuatro".split())]
    tl = video_maker._build_timeline(
        scenes, "narracion.wav", 18.0, words, "", video_maker.BEBE_HEROE_SUBTITLE_STYLE,
        mood="none", theme=video_maker.BEBE_HEROE_THEME,
    )
    assert tl["theme"] == "bebe_heroe" and tl["musicSrc"] and tl["fontSrc"]
    assert (tl["scenes"][1]["zoomFrom"], tl["scenes"][1]["zoomTo"]) == (1.0, 1.55)
    assert tl["subtitleStyle"]["fontFamily"] == "luckiest"
    assert (video_maker.BEBE_HEROE_ASSETS / "music.wav").exists()
    assert (video_maker.BEBE_HEROE_ASSETS / "luckiest.woff2").exists()


# ── Lotes mixtos (video / post alternados) ──────────────────────────────────

def _create_mixed(monkeypatch, tmp_path, content_type, total):
    monkeypatch.setattr(bp, "_load", lambda: {})
    monkeypatch.setattr(bp, "_save", lambda projects: None)
    monkeypatch.setattr(bp.gaming_clip.gaming_vision, "is_configured", lambda: True)
    return bp.create_project(
        "Niño Selectivo, Familia en Paz", total, 2, {"facebook": {"page_id": "1"}}, {}, content_type=content_type,
    )


def test_mixed_batch_alternates_video_and_post(monkeypatch, tmp_path):
    for total, videos, posts in ((10, 5, 5), (11, 6, 5), (1, 1, 0)):
        project = _create_mixed(monkeypatch, tmp_path, "mix_ninio", total)
        kinds = [bp.item_type(project, v) for v in project["videos"]]
        assert kinds == ["video", "ninio_image"] * (total // 2) + (["video"] if total % 2 else [])
        assert (kinds.count("video"), kinds.count("ninio_image")) == (videos, posts)
        assert project["type"] == "mix_ninio"


def test_mixed_gaming_uses_clip_and_gaming_post(monkeypatch, tmp_path):
    project = _create_mixed(monkeypatch, tmp_path, "mix_gaming", 4)
    assert [bp.item_type(project, v) for v in project["videos"]] == ["gaming_clip", "gaming_image"] * 2
    assert project["network_offsets"] == bp.GAMING_NETWORK_OFFSETS


def test_plain_batches_keep_their_type():
    assert bp.item_type({"type": "gaming_clip"}, {}) == "gaming_clip"
    assert bp.item_type({}, {}) == "video"
