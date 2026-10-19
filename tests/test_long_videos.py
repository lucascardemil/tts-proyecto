# -*- coding: utf-8 -*-
"""Videos de mas de 1:20: constantes, validadores y pedido de formato largo."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import auto_pipeline as ap


def _words(n):
    return " ".join(["palabra"] * n)


def test_constants_guarantee_more_than_80_seconds():
    assert ap.MIN_VIDEO_SECONDS == 80
    assert ap.LONG_SCRIPT_MIN_WORDS == 270 and ap.LONG_SCRIPT_MAX_WORDS == 300
    assert (ap.LONG_IMAGES_MIN, ap.LONG_IMAGES_MAX) == (14, 22)
    # a 189 palabras/min (la voz mas rapida observada) el minimo de palabras aun pasa de ~79 s
    assert ap.LONG_SCRIPT_MIN_WORDS / 189 * 60 > ap.MIN_VIDEO_SECONDS
    assert ap.LONG_KINDS == {"historias", "ninio_selectivo", "macrame"}


@pytest.mark.parametrize("n", [270, 275, 300])
def test_script_length_accepts_the_range(n):
    ap.validate_long_script_length(_words(n))


@pytest.mark.parametrize("n", [40, 150, 269, 301, 340])
def test_script_length_rejects_outside_the_range(n):
    with pytest.raises(ap.PipelineError, match="(?i)guion fuera de largo"):
        ap.validate_long_script_length(_words(n))


@pytest.mark.parametrize("n", [14, 16, 20, 22])
def test_image_count_accepts_the_range(n):
    ap.validate_image_count(n)


@pytest.mark.parametrize("n", [0, 5, 8, 13, 23, 30])
def test_image_count_rejects_outside_the_range(n):
    with pytest.raises(ap.PipelineError, match="(?i)imágenes fuera de rango"):
        ap.validate_image_count(n)


def test_min_duration():
    ap.validate_min_duration(82.0)
    ap.validate_min_duration(80.0)
    with pytest.raises(ap.PipelineError, match="(?i)video más corto que 1:20"):
        ap.validate_min_duration(70.4)


def _story_text(n_images, script_words):
    blocks = "".join(
        f"Imagen {i}\nFrase del guion: «Frase {i}.»\nPrompt: A calm scene number {i}.\n\n" for i in range(1, n_images + 1)
    )
    return f"HOOK_TEXT: Gancho 🐕\n\nPERSONAJES:\n[PERRO]: a dog\n\nGuion\n{_words(script_words)}\n\n{blocks}"


def test_count_image_blocks_counts_real_blocks():
    assert ap.count_image_blocks(_story_text(18, 270)) == 18
    assert ap.count_image_blocks("sin bloques") == 0


def test_long_story_text_validates_script_and_images(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "SCRATCH_DIR", tmp_path)
    ap.validate_long_story_text(_story_text(18, 270), "t1")
    with pytest.raises(ap.PipelineError, match="(?i)fuera de largo"):
        ap.validate_long_story_text(_story_text(18, 120), "t2")
    with pytest.raises(ap.PipelineError, match="(?i)fuera de rango"):
        ap.validate_long_story_text(_story_text(8, 270), "t3")


@pytest.mark.parametrize("kind", ["historias", "ninio_selectivo", "macrame"])
def test_trigger_message_asks_for_the_long_format_and_ignores_duration(kind):
    msg = ap.build_trigger_message("dame una historia", 30, kind=kind)
    assert "260 y 290 palabras" in msg and "16 y 20 imagenes" in msg
    assert "aproximadamente" not in msg  # la duracion elegida en el panel no cambia el guion largo


def test_trigger_message_keeps_the_duration_for_unknown_kinds():
    msg = ap.build_trigger_message("dame una historia", 30)
    assert "aproximadamente" in msg and "260 y 290" not in msg


# ── Cableado en el lote ──────────────────────────────────────────────────────
import batch_pipeline as bp


@pytest.mark.parametrize("msg", [
    "Guion fuera de largo: 120 palabras (deben ser 270-300 para durar mas de 1:20)",
    "Imágenes fuera de rango: 8 (deben ser 14-22)",
    "Video más corto que 1:20: el audio dura 70 s (minimo 80 s)",
])
def test_long_video_errors_are_healable(msg):
    assert bp._is_healable_error("Tras 3 intentos automáticos: " + msg)


def test_removed_fixed_length_machinery():
    for name in ("_has_fixed_length",):
        assert not hasattr(bp, name), name


def _chain(monkeypatch, texts):
    """Cadena de texto falsa: cada elemento de `texts` es la respuesta (o la excepcion) de un intento."""
    seen = []

    def fake_generate(system, user, **kwargs):
        seen.append(kwargs)
        reply = texts.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, "freellm"

    monkeypatch.setattr(bp.text_provider, "available_backends", lambda: ["freellm"])
    monkeypatch.setattr(bp.text_provider, "model_chain", lambda: ["m1", "m2", "m3"])
    monkeypatch.setattr(bp.text_provider, "generate_text", fake_generate)
    return seen


def _text(n_images, script_words):
    blocks = "".join(
        f"Imagen {i}\nFrase del guion: «Frase {i}.»\nPrompt: Vertical 9:16, a calm scene {i}.\n\n" for i in range(1, n_images + 1)
    )
    return f"HOOK_TEXT: Gancho\n\nPERSONAJES:\n[CAMILA]: a woman\n\nGuion\n{' '.join(['palabra'] * script_words)}\n\n{blocks}"


def test_chain_retries_with_the_next_model_when_the_story_is_too_short(monkeypatch):
    seen = _chain(monkeypatch, [_text(8, 120), _text(18, 270)])
    out = bp._generate_text_with_chain("macrame", "MACRAME CREATIVO", "dame una historia", "t1")
    assert out.count("Imagen ") >= 18 and len(seen) == 2
    assert all(kw["max_tokens"] == 16384 for kw in seen)
    assert [kw["model"] for kw in seen] == ["m1", "m2"]


def test_chain_gives_up_after_the_last_model(monkeypatch):
    _chain(monkeypatch, [_text(8, 120)] * max(bp.TEXT_FORMAT_ATTEMPTS, 3))
    with pytest.raises(RuntimeError, match="(?i)fuera de largo|fuera de rango"):
        bp._generate_text_with_chain("macrame", "MACRAME CREATIVO", "dame una historia", "t2")


# ── Timeout del proveedor de texto ───────────────────────────────────────────
READ_TIMEOUT = ("freellm(mistral-large-3): fallo tras 2 intentos: HTTPConnectionPool(host='127.0.0.1', "
                "port=31415): Read timed out. (read timeout=600)")


def test_text_timeout_default_fits_a_long_answer():
    import os
    assert bp.text_provider.FREELLM_TIMEOUT == int(os.environ.get("FREELLM_TIMEOUT", "600"))
    src = (Path(bp.__file__).parent / "text_provider.py").read_text(encoding="utf-8")
    assert 'os.environ.get("FREELLM_TIMEOUT", "600")' in src


def test_a_read_timeout_of_the_text_provider_is_healable():
    assert bp._is_healable_error("Sin proveedor de texto disponible (" + READ_TIMEOUT + ")")


def test_chain_retries_with_the_next_model_after_a_read_timeout(monkeypatch):
    seen = _chain(monkeypatch, [bp.text_provider.TextProviderError(READ_TIMEOUT), _text(18, 270)])
    out = bp._generate_text_with_chain("macrame", "MACRAME CREATIVO", "dame una historia", "t3")
    assert out.count("Imagen ") >= 18
    assert [kw["model"] for kw in seen] == ["m1", "m2"]


# ── Verificacion de la narracion y ambientacion, en el lote y en la ruta manual ──
def _run_batch_video(monkeypatch, tmp_path, page_name):
    """Maneja _generate_batch_video con texto, imagenes, voz y render falsos; devuelve lo que recibio cada etapa."""
    import app as app_module
    projects = {"p1": {"id": "p1", "page_name": page_name, "status": "running",
                       "video_settings": {"provider": "whatsapp"},
                       "videos": [{"index": 0, "status": "pending"}]}}
    got = {}
    monkeypatch.setattr(bp, "_load", lambda: projects)
    monkeypatch.setattr(bp, "_save", lambda p: None)
    monkeypatch.setattr(bp, "_generate_text_with_chain", lambda *a, **k: _text(16, 270))
    monkeypatch.setattr(ap, "SCRATCH_DIR", tmp_path)
    monkeypatch.setattr(ap, "validate_historia_script", lambda s: None)
    monkeypatch.setattr(bp.video_maker, "VIDEO_PUBLIC_DIR", tmp_path)
    monkeypatch.setattr(bp.job_store, "load", lambda *a, **k: {})
    monkeypatch.setattr(bp.job_store, "save", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "_session_for_provider", lambda provider: None)

    def fake_clips(story, clips_dir, **kwargs):
        for p in story["prompts"]:
            (clips_dir / f"scene_{p['index']}.jpg").write_bytes(b"x")
        return list(story["prompts"])

    def fake_tts(text, **kwargs):
        got["tts"] = kwargs
        return str(tmp_path / "a.wav"), {"ok": True}

    def fake_build_props(**kwargs):
        got["props"] = kwargs
        return None  # sin timeline: la corrida termina aca (no hace falta renderizar)

    monkeypatch.setattr(ap, "generate_clips", fake_clips)
    monkeypatch.setattr(bp, "text_to_speech_verified", fake_tts)
    monkeypatch.setattr(bp.video_maker, "get_audio_duration", lambda p: 90.0)
    monkeypatch.setattr(bp.video_maker, "build_props", fake_build_props)
    bp._generate_batch_video("p1", 0)
    return got


def test_batch_pages_keep_the_strict_check_and_their_mood(monkeypatch, tmp_path):
    got = _run_batch_video(monkeypatch, tmp_path, "CUENTOS")
    assert "min_similarity" not in got["tts"]
    assert got["props"]["mood"] == "warm_night"


def _run_manual_job(monkeypatch, tmp_path, page_name, story_text):
    """Maneja _run_pipeline_job (ruta manual) hasta el render; devuelve lo que recibieron la voz y build_props."""
    import app as app_module
    got = {}
    job_id = "job-mood"
    jobs = {job_id: {"status": "running", "error": None, "inputs": {},
                     "story": {k: {"status": "pending", "percent": 0, "message": "", "error": None}
                               for k in ("text", "tts", "clipgen", "video")}}}
    monkeypatch.setattr(app_module, "_pipeline_jobs", jobs)
    monkeypatch.setattr(app_module.job_store, "save", lambda *a, **k: None)
    monkeypatch.setattr(app_module.job_store, "load", lambda *a, **k: {})
    monkeypatch.setattr(ap, "SCRATCH_DIR", tmp_path)

    def fake_tts(text, **kwargs):
        got["tts"] = kwargs
        return str(tmp_path / "a.wav"), {"ok": True}

    def fake_build_props(**kwargs):
        got["props"] = kwargs
        return None

    monkeypatch.setattr(app_module, "text_to_speech_verified", fake_tts)
    monkeypatch.setattr(app_module.video_maker, "get_audio_duration", lambda p: 90.0)
    monkeypatch.setattr(app_module.auto_pipeline, "generate_clips", lambda *a, **k: [])
    monkeypatch.setattr(app_module.video_maker, "build_props", fake_build_props)
    app_module._run_pipeline_job(
        job_id, story_text, "sid", tmp_path, {}, "vertical", True, None, "", script_text="palabra " * 270,
        page_name=page_name)
    return got


def test_manual_pages_keep_the_strict_check_and_their_mood(monkeypatch, tmp_path):
    got = _run_manual_job(monkeypatch, tmp_path, "HISTORIAS", _text(16, 270))
    assert "min_similarity" not in got["tts"]
    assert got["props"].get("mood", "warm_night") == "warm_night"


# ── Pedidos del mensaje (fichas y formato largo) ─────────────────────────────
def test_generic_sheet_request_is_the_default_for_kinds_without_their_own():
    req = ap.CHARACTER_SHEET_REQUEST
    assert "90 a 120" in req and "ficha completa en cada prompt" in req
    assert "ficha fija" not in req and "[BEBE]" not in req
    assert req in ap.build_trigger_message("dame una historia", None)


def test_long_format_request_fits_every_long_kind():
    assert "desarrolla mas las escenas del medio de la historia" in ap.LONG_FORMAT_REQUEST
    assert "conflicto o el" not in ap.LONG_FORMAT_REQUEST


def test_main_page_renders_through_jinja_with_the_duration_notes(monkeypatch):
    import base64
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    resp = app_module.app.test_client().get("/", headers=auth)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert html.count("duran ~1:30 y no usan esta opción") == 2  # panel manual y lote


def _run_manual_job_with_audio_check(monkeypatch, tmp_path, duration_side_effect):
    """Maneja _run_pipeline_job (ruta manual, pagina con guion) con TTS y clips falsos."""
    import app as app_module
    job_id = "job-dur"
    jobs = {job_id: {"status": "running", "error": None, "inputs": {},
                     "story": {k: {"status": "pending", "percent": 0, "message": "", "error": None}
                               for k in ("text", "tts", "clipgen", "video")}}}
    monkeypatch.setattr(app_module, "_pipeline_jobs", jobs)
    monkeypatch.setattr(app_module.job_store, "save", lambda *a, **k: None)
    monkeypatch.setattr(app_module.job_store, "load", lambda *a, **k: {})
    monkeypatch.setattr(app_module, "text_to_speech_verified", lambda *a, **k: (str(tmp_path / "a.wav"), {"ok": True}))
    monkeypatch.setattr(app_module.video_maker, "get_audio_duration", duration_side_effect)
    monkeypatch.setattr(app_module.auto_pipeline, "generate_clips", lambda *a, **k: [])
    app_module._run_pipeline_job(
        job_id, _text(16, 270), "sid", tmp_path, {}, "vertical", True, None, "", script_text="palabra " * 270,
        page_name="HISTORIAS")
    return jobs[job_id]["story"]["tts"]


def test_manual_route_audio_check_errors_the_tts_state_for_a_short_audio(monkeypatch, tmp_path):
    tts = _run_manual_job_with_audio_check(monkeypatch, tmp_path, lambda p: 70.0)
    assert tts["status"] == "error" and "más corto que 1:20" in tts["error"]


def test_manual_route_audio_check_never_leaves_the_tts_state_running(monkeypatch, tmp_path):
    def unreadable(path):
        raise RuntimeError("No se pudo leer la duración de: " + path)

    tts = _run_manual_job_with_audio_check(monkeypatch, tmp_path, unreadable)
    assert tts["status"] == "error" and "No se pudo leer la duración" in tts["error"]  # si no, el reintento espera para siempre


# ── Prompts de formato largo ─────────────────────────────────────────────────
PROMPTS = {
    "historias": bp._PROMPTS_DIR / "historias_system.md",
    "ninio_selectivo": bp._PROMPTS_DIR / "ninio_selectivo_system.md",
    "macrame": bp._PROMPTS_DIR / "macrame_system.md",
}
OLD_SHORT_FORMAT = ("38 a 4", "38-4", "14 segundos", "17 segundos", "18 segundos", "18 s", "4 escenas",
                    "exactamente 5 imágenes", "exactamente 4 imágenes", "8 a 10 imágenes", "100 a 120 palabras")


@pytest.mark.parametrize("kind", sorted(PROMPTS))
def test_prompts_ask_for_the_long_format(kind):
    text = PROMPTS[kind].read_text(encoding="utf-8")
    assert "260 a 290 palabras" in text and "16 a 20 imágenes" in text
    assert "90 segundos" in text
    for old in OLD_SHORT_FORMAT:
        assert old not in text, (kind, old)
    for kept in ("HOOK_TEXT:", "PERSONAJES:", "Guion", "Imagen 1", "Frase del guion"):
        assert kept in text, (kind, kept)


@pytest.mark.parametrize("kind", ["ninio_selectivo", "macrame"])
def test_ad_prompts_keep_the_ebook_closing_and_fixed_sets(kind):
    text = PROMPTS[kind].read_text(encoding="utf-8")
    assert "Comenta EBOOK" in text
    assert ("[COCINA]" if kind == "ninio_selectivo" else "[ESCENA]") in text
    assert "NO dibujes ningún celular" in text


def test_rescue_story_craft_asks_for_the_long_script():
    assert "260 a 290 palabras" in bp.RESCUE_STORY_CRAFT and "90 segundos" in bp.RESCUE_STORY_CRAFT
    assert "100 a 120" not in bp.RESCUE_STORY_CRAFT and "42 segundos" not in bp.RESCUE_STORY_CRAFT


def _ranges(pattern, text):
    """Suma los minimos y los maximos de los rangos "N" o "N a M" / "N-M" que captura `pattern`."""
    lo = hi = 0
    for a, b in re.findall(pattern, text, re.MULTILINE):
        lo, hi = lo + int(a), hi + int(b or a)
    return lo, hi


def _bullet(text, name):
    return re.search(r"^- " + name + r".*?(?=^- |^\S|\Z)", text, re.MULTILINE | re.DOTALL).group(0)


def test_macrame_unfinished_piece_is_described_without_the_finished_piece_tag():
    text = PROMPTS["macrame"].read_text(encoding="utf-8")
    process = _bullet(text, "Proceso paso a paso")
    assert "[PIEZA]" not in process.replace("NUNCA uses [PIEZA]", "")
    assert "an unfinished macramé piece in progress" in process and "[NOMBRE]" in process
    assert "(y en la primera si es el gancho)" not in text
    assert "[PIEZA] solo en las imágenes de la pieza terminada y del orgullo" in text
    assert "NO dibujes ningún celular ni portada" in _bullet(text, r"Cierre \(")
    assert "Comenta EBOOK y te envío la guía." in text


def test_ninio_plate_close_ups_always_show_a_tagged_person():
    text = PROMPTS["ninio_selectivo"].read_text(encoding="utf-8")
    assert "nunca un plano solo del plato desde la Imagen 2" in text
    assert "- Dolor (3):" in text
    section = text.split("## IMÁGENES", 1)[1].split("REGLAS:", 1)[0]
    lo, hi = _ranges(r"^- [^(\n]+\((\d+)(?:-(\d+))?\)", section)
    assert 16 <= lo and hi <= 20, (lo, hi)


def _ad_story(sheet):
    """Anuncio de 3 imagenes: todas con la ficha del set; de la 2 en adelante, `sheet` pegada."""
    prompts = [{"index": 1, "prompt": "Vertical 9:16, hands knotting. Escena (cozy boho living room)."}]
    for i in (2, 3):
        prompts.append({"index": i, "prompt": f"Vertical 9:16, {sheet} smiles. Escena (cozy boho living room)."})
    return {"prompts": prompts}


@pytest.mark.parametrize("age", [
    "twenty-eight-year-old", "Thirty year old", "five-year-old", "seven years old",
    "28-year-old", "28 years old", "28 year old",
])
def test_validate_ad_story_accepts_spelled_out_and_digit_ages(age):
    ap.validate_ad_story(_ad_story(f"a {age} woman with long brown hair"), "Escena")


def test_validate_ad_story_still_rejects_images_without_a_person_sheet():
    with pytest.raises(ap.PipelineError, match="sin ficha de personaje"):
        ap.validate_ad_story(_ad_story("someone with long brown hair"), "Escena")


@pytest.mark.parametrize("kind", ["macrame", "ninio_selectivo"])
def test_ad_prompts_require_a_tagged_person_in_every_image_from_the_second(kind):
    text = " ".join(PROMPTS[kind].read_text(encoding="utf-8").split())
    assert "Desde la Imagen 2, TODAS las imágenes incluyen" in text
    assert "nunca un plano solo de la mesa, los objetos, el plato o la cuerda" in text
    section, rules = text.split("## IMÁGENES", 1)[1].split("REGLAS:", 1)
    assert "TODAS las imágenes incluyen" in section and "TODAS las imágenes incluyen" in rules


def test_macrame_previous_attempts_bullet_keeps_the_character_in_frame():
    text = PROMPTS["macrame"].read_text(encoding="utf-8")
    bullet = _bullet(text, "Problema e intentos previos")
    assert "abandonado a medias" not in bullet
    assert "manos" in bullet and "rostro" in bullet


def test_bebe_heroe_is_fully_removed():
    import batch_pipeline as bp
    import auto_pipeline as ap
    for name in ("BEBE_HEROE_SCENES", "BEBE_HEROE_MIN_WORDS", "bebe_script_from_story", "BEBE_HEROE_NARRATION_MIN_SIMILARITY",
                 "narration_check_kwargs", "_recent_bebe_ideas", "_pick_bebe_idea",
                 "BEBE_HEROE_KIND", "BEBE_HEROE_VOICE_LABEL", "BEBE_HEROE_SLOT_HOURS", "BEBE_HEROE_HISTORY_LIMIT",
                 "BEBE_HEROE_DEFAULT_QUESTION", "BEBE_HEROE_IDEAS", "is_bebe_heroe_page", "bebe_trigger_block",
                 "generate_bebe_heroe_voice", "_bebe_heroe_voice", "_bebe_heroe_copy"):
        assert not hasattr(bp, name), name
    assert ap.LONG_KINDS == frozenset({"historias", "ninio_selectivo", "macrame"})
    assert bp._text_kind_for_page("Bebé Héroe") == "historias"  # ya no es una página con kind propio
    assert not (bp._PROMPTS_DIR / "bebe_heroe_system.md").exists()
    import tts_engine
    import video_maker
    for mod, name in ((video_maker, "assemble_bebe_heroe_audio"), (video_maker, "build_bebe_heroe_props"),
                      (video_maker, "BEBE_HEROE_ASSETS"), (tts_engine, "text_to_speech_phrases")):
        assert not hasattr(mod, name), name
    root = Path(bp.__file__).parent
    assert not (root / "assets" / "bebe_heroe").exists()
    assert not (root / "video" / "src" / "BebeHeroe.tsx").exists()
