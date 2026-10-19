# -*- coding: utf-8 -*-
"""HISTORIAS: formato fijo de 5 pasos (~90 s), cierre con corazón para el animal."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import auto_pipeline as ap
import batch_pipeline as bp
import seo_optimizer as so

PROMPT = bp._PROMPTS_DIR / "historias_system.md"


def test_historias_prompt_names_the_five_steps_and_the_fixed_length():
    text = PROMPT.read_text(encoding="utf-8")
    for step in ("Gancho de espera", "Conflicto", "Giro", "Reencuentro", "Cierre"):
        assert step in text, step
    assert "270 a 290 palabras" in text
    assert "corazón" in text
    assert "Comenta" not in text and "comenta" not in text  # el cierre ya no pide palabra clave
    for kept in ("HOOK_TEXT:", "PERSONAJES:", "NUNCA en español", "16 a 20 imágenes"):
        assert kept in text, kept


def test_rescue_story_craft_matches_the_prompt():
    craft = bp.RESCUE_STORY_CRAFT
    assert "corazón" in craft and "omenta" not in craft
    assert "270 a 290 palabras" in craft
    assert "giro" in craft.lower() and "reencuentro" in craft.lower()


def test_new_pattern_has_the_highest_weight_and_rescue_block_uses_it():
    weights = {k: v[0] for k, v in bp.STORY_PATTERNS.items()}
    assert "mascota_perdida" in weights
    assert weights["mascota_perdida"] == max(weights.values()) and list(weights.values()).count(max(weights.values())) == 1
    assert "cartel" in bp.STORY_PATTERNS["mascota_perdida"][1]
    assert "mascota_perdida" in bp.STORY_PATTERNS and bp.rescue_story_block("mascota_perdida").endswith(
        bp.STORY_PATTERNS["mascota_perdida"][1])


def test_historias_is_fixed_length_and_has_no_value_pillars():
    for kind in ("historias", "macrame", "ninio_selectivo"):
        assert "aproximadamente" not in ap.build_trigger_message("x", 30, kind=kind), kind
    assert bp.PILARES_KINDS == set()
    assert "PILARES DE VALOR" not in bp._text_system_prompt("historias")


GOOD = (
    "Nuri esperó siete meses en el mismo banco de una plaza de Oaxaca. Todos pasaban a su lado... nadie la veía. "
    "Los niños de la escuela le dejaban migas de pan sobre la madera, y el vendedor de elotes le guardaba un poco "
    "de agua en una tapa vieja. Ella lo agradecía con la mirada, pero nunca se alejaba de ese rincón, porque "
    "esperaba a alguien y no pensaba rendirse. Su dueño se mudó una noche de tormenta y ella volvió cada tarde, "
    "con frío y con lluvia, a la misma hora. Cuando el viento helado levantaba las hojas secas, Nuri se hacía una "
    "bolita blanca y cerraba los ojos, soñando con el sonido de unos pasos conocidos. Pasaron las semanas y su "
    "pelaje se volvió opaco y desordenado. La gente pensaba que era una gata callejera más. Hasta que Camila, la "
    "panadera, notó el lazo rojo en su cuello y un cartel viejo pegado en la farmacia. Tenía una foto, un número "
    "de teléfono y tres palabras escritas con letra temblorosa: ayúdennos a encontrarla. Era Nuri. Camila llamó "
    "enseguida, con las manos todavía llenas de harina. Su dueño la buscaba desde hacía meses, había pegado "
    "carteles en cada barrio y cruzó la plaza corriendo, sin aliento. Cuando la vio, Nuri lo reconoció al "
    "instante, se quedó quieta un segundo y saltó a sus brazos maullando como nunca. Hoy Nuri ya no espera sola. "
    "Duerme en una cama suave, junto a la ventana donde entra el sol de la mañana, y ya no teme a las tormentas. Si tú también crees que "
    "ninguna historia debe terminar en la calle, deja un corazón para Nuri."
)


def test_good_historia_script_passes():
    n = len(GOOD.split())
    assert ap.LONG_SCRIPT_MIN_WORDS <= n <= ap.LONG_SCRIPT_MAX_WORDS, n
    ap.validate_historia_script(GOOD)


@pytest.mark.parametrize("script, fragment", [
    (" ".join(GOOD.split()[:120]) + " deja un corazón para Nuri.", "palabras"),
    (GOOD + " " + GOOD, "palabras"),
    (GOOD.replace("corazón", "like"), "corazon"),
    (GOOD.replace("deja un corazón para Nuri.", "Comenta GATA si la habrías ayudado."), "corazon"),
    (GOOD.replace("deja un corazón para Nuri.", "deja un corazón para Nuri. Comenta GATA."), "comenta"),
])
def test_bad_historia_scripts_are_rejected_with_a_healable_error(script, fragment):
    with pytest.raises(ap.PipelineError) as err:
        ap.validate_historia_script(script)
    assert fragment in str(err.value).lower()
    assert bp._is_healable_error(str(err.value))  # el lote regenera el guion


def test_validate_historia_story_text_reads_the_script_block():
    story_text = (
        "HOOK_TEXT: Nadie la veía 🌧️\n\nPERSONAJES:\n[GATA]: a white cat\n\nGuion\n" + GOOD + "\n\n"
        "Imagen 1\nFrase del guion: «Nuri esperó siete meses en el mismo banco»\n"
        "Prompt: 9:16 vertical, photorealistic: a white cat on a bench\n"
    )
    ap.validate_historia_story_text(story_text, "t1")
    with pytest.raises(ap.PipelineError):
        ap.validate_historia_story_text(story_text.replace(GOOD, "Muy corto."), "t1")


def test_copy_prompt_asks_for_a_heart_for_the_animal_not_a_keyword():
    text = (bp._PROMPTS_DIR / "historias_copy_system.md").read_text(encoding="utf-8")
    assert "❤️ para" in text
    assert "palabra clave" in text  # aclara que NO se pide


def test_rescue_cta_reuses_the_heart_closing_of_the_script():
    cta = so._rescue_cta(GOOD)
    assert "corazón" in cta and "Nuri" in cta
    # sin cierre de corazón sigue rotando las preguntas de siempre
    other = so._rescue_cta("Una historia corta sin pregunta final.")
    assert other in so._RESCUE_CTA_QUESTIONS


def test_rescue_copy_ends_with_the_heart_request():
    fb = so.suggest_rescue_copy(GOOD, "facebook")
    assert "corazón para Nuri" in fb


def test_heart_closing_is_for_facebook_only_so_the_networks_differ():
    fb_cta = so._rescue_cta(GOOD, 0)
    assert "corazón para Nuri" in fb_cta
    ig_cta = so._rescue_cta(GOOD, 1)
    assert ig_cta in so._RESCUE_CTA_QUESTIONS and ig_cta != fb_cta
    fb = so.suggest_rescue_copy(GOOD, "facebook")
    ig = so.suggest_rescue_copy(GOOD, "instagram")
    assert fb.split("\n\n")[-2] != ig.split("\n\n")[-2]


def test_declarative_heart_sentence_is_not_reused_as_closing():
    script = GOOD.split(" Si tú también")[0] + " Hoy Nuri vive con el corazón tranquilo."
    assert so._rescue_cta(script, 0) in so._RESCUE_CTA_QUESTIONS


def test_question_closing_keeps_the_original_90_char_cap():
    short_q = "¿Te habría gustado ver a Nuri llegar a casa con su familia?"
    long_q = "¿Tú también crees que ninguna historia de un animal debería terminar en la calle, sin una familia que la espere?"
    assert len(short_q) <= 90
    assert 91 <= len(long_q) <= 130
    story = "Nuri esperó siete meses en el mismo banco. "
    assert so._rescue_cta(story + short_q) == short_q
    assert so._rescue_cta(story + long_q) in so._RESCUE_CTA_QUESTIONS


# ── Revision final: reintento inmediato, patron en el lote real, acentos, "comenta" ──

def test_comenta_check_is_a_whole_word():
    ap.validate_historia_script(GOOD.replace(
        "La gente pensaba que era una gata callejera más.",
        "Los vecinos comentaban que era callejera."))
    with pytest.raises(ap.PipelineError):
        ap.validate_historia_script(GOOD.replace(
            "deja un corazón para Nuri.", "deja un corazón para Nuri. Comenta GATA."))


def _story_text(script, n_images=18):
    blocks = "".join(
        f"Imagen {i}\nFrase del guion: «Nuri esperó siete meses en el mismo banco {i}»\n"
        f"Prompt: 9:16 vertical, photorealistic: a white cat on a bench, scene {i}\n\n"
        for i in range(1, n_images + 1)
    )
    return "HOOK_TEXT: Nadie la veía 🌧️\n\nPERSONAJES:\n[GATA]: a white cat\n\nGuion\n" + script + "\n\n" + blocks


def _chain_with(monkeypatch, replies):
    calls = []
    it = iter(replies)
    monkeypatch.setattr(bp.text_provider, "available_backends", lambda: ["freellm:auto"])
    monkeypatch.setattr(bp.text_provider, "model_chain", lambda: ["m1", "m2", "m3"])

    def fake(system, user, **k):
        calls.append(k.get("model"))
        return next(it), "freellm"

    monkeypatch.setattr(bp.text_provider, "generate_text", fake)
    return calls


GOOD_ANY = _story_text(GOOD)


def test_chain_retries_a_historias_script_out_of_range_with_the_next_model(monkeypatch):
    bad = _story_text(" ".join(GOOD.split()[:83]) + " deja un corazón para Nuri.")
    good = _story_text(GOOD)
    calls = _chain_with(monkeypatch, [bad, good])
    out = bp._generate_text_with_chain("historias", "HISTORIAS", "msg", "t")
    assert out == good
    assert calls == ["m1", "m2"]


def test_chain_retries_a_short_script_of_any_long_kind_but_not_of_the_posts(monkeypatch):
    short = _story_text("Un guion corto de macrame con solo unas pocas palabras.")
    calls = _chain_with(monkeypatch, [short, GOOD_ANY])
    out = bp._generate_text_with_chain("macrame", "MACRAME CREATIVO", "msg", "t")
    assert out == GOOD_ANY and calls == ["m1", "m2"]  # macrame tambien dura mas de 1:20
    calls = _chain_with(monkeypatch, [short])
    out = bp._generate_text_with_chain("ninio_post", "NIÑO SELECTIVO", "msg", "t")  # un post no es un video largo
    assert out == short and len(calls) == 1


def test_recent_story_patterns_counts_historias_pages_without_the_rescue_profile():
    projects = {
        "a": {"page_name": "HISTORIAS", "video_settings": {}, "videos": [
            {"scheduled_at": "2026-10-02", "story_pattern": "ladron"},
            {"scheduled_at": "2026-10-01", "story_pattern": "guardian"}, {"scheduled_at": "x"}]},
        "b": {"page_name": "CUENTOS", "video_settings": {}, "videos": [
            {"scheduled_at": "2026-10-03", "story_pattern": "separacion"}]},
        "c": {"page_name": "otra", "video_settings": {"copy_profile": bp.RESCUE_PROFILE}, "videos": [
            {"scheduled_at": "2026-10-04", "story_pattern": "espera_diaria"}]},
    }
    assert bp._recent_story_patterns(projects) == ["guardian", "ladron", "espera_diaria"]


def test_historias_batch_trigger_carries_the_story_pattern_even_without_the_rescue_profile(monkeypatch):
    monkeypatch.setattr(bp, "_rescue_history_block", lambda: "")
    historias = {"page_name": "historias ", "video_settings": {}}
    assert bp._uses_story_pattern(historias)
    assert "PATRON de esta historia" in bp._story_trigger_addition(historias, "mascota_perdida")
    other = {"page_name": "MACRAME CREATIVO", "video_settings": {}}
    assert not bp._uses_story_pattern(other)
    assert bp._story_trigger_addition(other, "mascota_perdida") == ""


def test_story_craft_examples_are_written_with_accents():
    craft = bp.RESCUE_STORY_CRAFT
    assert "esperó" in craft and "veía" in craft
    assert "espero " not in craft and "veia" not in craft
    assert "acentos" in craft and "ñ" in craft
    prompt = PROMPT.read_text(encoding="utf-8")
    assert "unas 24 a 28 frases" in prompt
    assert "acentos" in prompt and "ñ" in prompt
    assert "todo animal merece un hogar" in prompt


def test_historias_prompt_asks_for_detailed_character_sheets():
    text = PROMPT.read_text(encoding="utf-8")
    for needle in ("90-120 palabras", "80-100 palabras", "[LUGAR]", "hex", "90-130 palabras"):
        assert needle in text, needle
    assert "40-60 palabras" not in text and "70-100 palabras" not in text


def test_trigger_message_uses_historias_sheet_only_for_historias():
    msg = ap.build_trigger_message("dame una historia", None, kind="historias")
    assert "[LUGAR]" in msg and "90 a 130 palabras" in msg and "hex" in msg
    assert "[LUGAR]" not in ap.build_trigger_message("dame una historia", None)
    assert "[LUGAR]" not in ap.build_trigger_message("dame una historia", None, kind="macrame")


def test_parse_story_expands_the_place_sheet_only_where_tagged(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "SCRATCH_DIR", tmp_path)
    raw = (
        "PERSONAJES:\n[GATA]: white cat with gray patch\n[LUGAR]: stone bench in a sunny plaza\n\n"
        "Guion\nUna historia.\n\n"
        "Imagen 1\nFrase del guion: «Una historia.»\nPrompt: Close-up of [GATA] on [LUGAR].\n\n"
        "Imagen 2\nFrase del guion: «Una historia.»\nPrompt: [GATA] sleeping on a sofa.\n"
    )
    story = ap._parse_story(raw, "t_lugar")
    first, second = story["prompts"][0]["prompt"], story["prompts"][1]["prompt"]
    assert "stone bench in a sunny plaza" in first and "white cat with gray patch" in first
    assert "stone bench" not in second and "white cat with gray patch" in second
