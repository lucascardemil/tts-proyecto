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


def _tone(path, seconds):
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}", str(path)], check=True)


def test_audio_is_assembled_in_fixed_slots_of_18_seconds(tmp_path):
    paths = []
    for i, seconds in enumerate((2.0, 6.0, 2.0, 1.2)):  # la 2.ª no cabe en su hueco (3.2 -> 7.3): se acelera
        p = tmp_path / f"p{i}.wav"
        _tone(p, seconds)
        paths.append(p)
    texts = ["uno", "dos", "tres", "cuatro"]
    dest = tmp_path / "mix.wav"
    timings = video_maker.assemble_bebe_heroe_audio(paths, texts, dest)
    assert [t["start"] for t in timings] == [0.3, 3.2, 7.3, 13.3]
    assert [t["text"] for t in timings] == texts
    assert timings[1]["dur"] < 6.0 and timings[1]["dur"] >= 6.0 / video_maker.BEBE_HEROE_MAX_SPEEDUP - 0.1
    assert abs(video_maker.get_audio_duration(str(dest)) - 18.0) < 0.1


def test_props_use_fixed_scene_cuts_zoom_music_and_sfx(tmp_path, monkeypatch):
    public = tmp_path / "public"
    public.mkdir()
    monkeypatch.setattr(video_maker, "VIDEO_PUBLIC_DIR", public)
    monkeypatch.setattr(video_maker, "VIDEO_DIR", tmp_path)
    images = []
    for i in range(4):
        img = tmp_path / f"i{i}.jpg"
        img.write_bytes(b"x")
        images.append(str(img))
    audio = tmp_path / "a.wav"
    _tone(audio, 18)
    timings = [{"text": t, "start": s, "dur": 2.0} for t, s in zip("abcd", video_maker.BEBE_HEROE_VOICE_STARTS)]
    tl = video_maker.build_bebe_heroe_props(images, str(audio), timings, sfx_tag="gatito")
    assert tl["totalDurationSeconds"] == 18.0 and tl["theme"] == "bebe_heroe" and tl["phrases"] == timings
    assert [(s["startFrame"], s["endFrame"]) for s in tl["scenes"]] == [(0, 90), (90, 210), (210, 390), (390, 540)]
    assert (tl["scenes"][1]["zoomFrom"], tl["scenes"][1]["zoomTo"]) == (1.0, 1.55)
    assert tl["sfx"] == [{"src": "bebe_heroe_sfx_gatito.wav", "at": 0.5, "volume": 0.7}]
    assert all((public / f).exists() for f in ("bebe_heroe_music.wav", "bebe_heroe_luckiest.woff2", "narracion.wav", "bebe_heroe_sfx_gatito.wav"))
    assert "sfx" not in video_maker.build_bebe_heroe_props(images, str(audio), timings, sfx_tag="sin_sonido")
    assert video_maker.build_bebe_heroe_props(images[:3], str(audio), timings) is None


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


def test_script_is_rebuilt_from_the_four_scene_phrases():
    story = {"prompts": [
        {"index": 2, "frase": "Pero escuché un llanto."}, {"index": 1, "frase": "Mamá me dijo que no saliera."},
        {"index": 4, "frase": "¿Crees que hice bien?"}, {"index": 3, "frase": "Así que subí yo."},
    ]}
    script = bp.bebe_script_from_story(story)
    assert script == "Mamá me dijo que no saliera. Pero escuché un llanto. Así que subí yo. ¿Crees que hice bien?"
    assert len(script.split()) >= bp.BEBE_HEROE_MIN_WORDS


# ── Voz por frase con verificacion tolerante ─────────────────────────────────

def _phrases_env(monkeypatch, tmp_path, reports):
    import tts_engine
    calls = {"n": 0, "unload": 0}

    def fake_tts(text, **kwargs):
        calls["n"] += 1
        p = tmp_path / f"a{calls['n']}.wav"
        p.write_bytes(b"x")
        return str(p)

    monkeypatch.setattr(tts_engine, "text_to_speech_long", fake_tts)
    monkeypatch.setattr(tts_engine, "verify_narration", lambda text, path: dict(reports.pop(0)))
    monkeypatch.setattr(tts_engine, "unload_omnivoice", lambda: calls.__setitem__("unload", calls["unload"] + 1))
    return tts_engine, calls


def test_phrases_keep_the_best_attempt_and_unload_once(monkeypatch, tmp_path):
    reports = [
        {"ok": False, "similarity": 0.6, "added": [], "changed": [("a", "b")]},   # frase 1, intento 1
        {"ok": False, "similarity": 0.85, "added": [], "changed": [("a", "c")]},  # intento 2 (mejor)
        {"ok": False, "similarity": 0.7, "added": [], "changed": []},             # intento 3
        {"ok": True, "similarity": 1.0, "added": []},                             # frase 2
    ]
    tts, calls = _phrases_env(monkeypatch, tmp_path, reports)
    paths = tts.text_to_speech_phrases(["uno dos", "tres"], max_attempts=3)
    assert len(paths) == 2 and calls["unload"] == 1
    assert paths[0].endswith("a2.wav")  # se queda el intento de similitud 0.85


def test_phrases_fail_when_nothing_is_close_enough(monkeypatch, tmp_path):
    import pytest
    reports = [{"ok": False, "similarity": 0.4, "added": [], "changed": [("a", "b")]}] * 2
    tts, calls = _phrases_env(monkeypatch, tmp_path, reports)
    with pytest.raises(tts.NarrationMismatchError, match="no coincide con el guion"):
        tts.text_to_speech_phrases(["uno"], max_attempts=2)
    assert calls["unload"] == 1
