import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auto_pipeline
import tts_engine

SCRIPT = ("Hoy te enseño a hacer un colgador de plantas con solo dos metros de cuerda. "
          "Primero corta la cuerda y átala al aro con un nudo alondra. Comenta EBOOK")


def test_exact_match_is_ok():
    r = tts_engine.narration_diff(SCRIPT, SCRIPT.lower())
    assert r["ok"] and r["similarity"] == 1.0 and not r["added"] and not r["removed"]


def test_added_words_fail_and_are_reported():
    heard = SCRIPT.replace("Primero corta", "Primero y sinceramente corta")
    r = tts_engine.narration_diff(SCRIPT, heard)
    assert not r["ok"]
    assert any("sinceramente" in a for a in r["added"])


def test_omitted_phrase_fails():
    heard = SCRIPT.replace("con solo dos metros de cuerda", "")
    r = tts_engine.narration_diff(SCRIPT, heard)
    assert not r["ok"]
    assert any("metros" in x for x in r["removed"])


def test_changed_word_is_reported():
    r = tts_engine.narration_diff(SCRIPT, SCRIPT.replace("colgador", "cortinero"))
    assert any(c == ("colgador", "cortinero") for c in r["changed"])


def test_phonetic_noise_is_tolerated():
    r = tts_engine.narration_diff(SCRIPT, SCRIPT.replace("colgador", "colgadora"))
    assert r["ok"] and r["noise"]


def test_verified_regenerates_then_raises(monkeypatch, tmp_path):
    calls = []

    def fake_tts(text, **kw):
        p = tmp_path / f"a{len(calls)}.wav"
        p.write_bytes(b"x")
        calls.append(p)
        return str(p)

    monkeypatch.setattr(tts_engine, "text_to_speech_long", fake_tts)
    monkeypatch.setattr(tts_engine, "verify_narration",
                        lambda s, a: tts_engine.narration_diff(s, s + " palabra inventada"))
    with pytest.raises(tts_engine.NarrationMismatchError) as e:
        tts_engine.text_to_speech_verified(SCRIPT, max_attempts=3)
    assert len(calls) == 3 and "inventada" in str(e.value)
    assert not calls[0].exists() and calls[2].exists()


def test_verified_stops_on_first_good(monkeypatch, tmp_path):
    def fake_tts(text, **kw):
        p = tmp_path / "ok.wav"
        p.write_bytes(b"x")
        return str(p)

    monkeypatch.setattr(tts_engine, "text_to_speech_long", fake_tts)
    monkeypatch.setattr(tts_engine, "verify_narration", lambda s, a: tts_engine.narration_diff(s, s))
    path, report = tts_engine.text_to_speech_verified(SCRIPT)
    assert report["ok"] and report["attempt"] == 1
    assert Path(path + tts_engine.NARRATION_CHECK_SUFFIX).exists()


MACRAME_TEXT = """MATRIZ
x
IDEA
Nombre: colgador
PERSONAJES:
[PIEZA]: cream cotton plant hanger, 60 cm, 3mm braided cord #F5F1E8, six square knots, 20 cm fringe
[MANOS]: female hands, short natural nails
[ESCENA]: light oak table, beige linen backdrop, window light from the left
GUION
{script}
IMAGENES
Imagen 1
Frase del guion: «Mira este colgador.»
Prompt: {p1}
Imagen 2
Frase del guion: «Corta la cuerda.»
Prompt: {p2}
"""
LONG = " ".join(["detail"] * 70)


def _story(p1, p2):
    return auto_pipeline._parse_story(
        MACRAME_TEXT.format(script="Hola " * 40, p1=p1, p2=p2), story_id="test_macrame")


def test_macrame_sheet_expanded_and_valid():
    story = _story(f"9:16 finished [PIEZA] held by [MANOS], [ESCENA] {LONG}",
                   f"9:16 overhead [PIEZA] with [MANOS] on [ESCENA] {LONG}")
    auto_pipeline.validate_macrame_story(story)
    assert "6 square knots" in story["prompts"][0]["prompt"] or "six square knots" in story["prompts"][0]["prompt"]


def test_macrame_prompt_without_tags_rejected():
    story = _story("generic macrame photo", f"9:16 [PIEZA] [MANOS] [ESCENA] {LONG}")
    with pytest.raises(auto_pipeline.PipelineError, match="(?i)no se encontraron prompts de imagen"):
        auto_pipeline.validate_macrame_story(story)


def test_trigger_message_uses_macrame_request():
    assert "[PIEZA]" in auto_pipeline.build_trigger_message("x", None, kind="macrame")
    assert "[PIEZA]" not in auto_pipeline.build_trigger_message("x", None)


@pytest.mark.parametrize("heading", ["IMAGENES", "IMÁGENES", "## Imágenes:"])
def test_extract_script_drops_images_heading(heading):
    text = MACRAME_TEXT.replace("IMAGENES\n", heading + "\n").format(
        script="Hola " * 40 + "Comenta EBOOK", p1=LONG, p2=LONG)
    script = auto_pipeline.extract_script(text)
    assert script.endswith("Comenta EBOOK")


def test_narration_diff_counts_similar_but_different_word():
    # "ansia" por "anciana" (0.83) ya no se tolera como ruido; "colgadora" (0.94) si.
    r = tts_engine.narration_diff("Yukiko y su anciana amiga volvieron", "Yukiko y su ansia amiga volvieron")
    assert r["changed"] == [("anciana", "ansia")] and not r["noise"]


def test_subtitle_words_corrected_from_script():
    import video_maker
    def w(t, s): return {"word": t, "start": s, "end": s + 0.3}
    heard = [w("lo", 0.0), w("había", 0.3), w("adoptado", 0.6), w("y,", 0.9), w("juntos,", 1.2),
             w("ansia", 1.5), w("en", 1.8), w("el", 2.1), w("gato,", 2.4), w("encontraron", 2.7)]
    script = "lo había adoptado y juntos, anciana y gato, encontraron"
    out = video_maker.correct_words_with_script(heard, script)
    assert [x["word"] for x in out] == ["lo", "había", "adoptado", "y,", "juntos,", "anciana", "y", "gato,", "encontraron"]
    ans = [x for x in out if x["word"] in ("anciana", "y") and x["start"] >= 1.5]
    assert ans[0]["start"] == 1.5 and ans[-1]["end"] == pytest.approx(2.4, abs=0.01)


def test_subtitle_correction_noop_without_script():
    import video_maker
    words = [{"word": "hola", "start": 0.0, "end": 0.3}]
    assert video_maker.correct_words_with_script(words, "") == words


def test_prompts_without_aspect_ratio_get_vertical_prefix():
    # Los de niño selectivo salian 3:2 porque el modelo omitia "9:16"; ahora se fuerza.
    assert auto_pipeline._ensure_vertical("Close-up of a boy").startswith("Vertical 9:16")
    assert auto_pipeline._ensure_vertical("Vertical 9:16, a scene") == "Vertical 9:16, a scene"
    assert auto_pipeline._ensure_vertical("16:9 landscape scene") == "16:9 landscape scene"
    story = auto_pipeline._parse_story(
        "Guion\nImagen 1\nFrase del guion: «Hola mundo.»\nPrompt: Close-up of a boy pushing a plate", story_id="t916")
    assert story["prompts"][0]["prompt"].startswith("Vertical 9:16")


def test_first_scene_starts_at_zero_even_if_narration_starts_late():
    # Con 2.5 s de silencio inicial, el corte 0 no debe correrse: si no, las escenas suman
    # menos que el audio y el video queda en negro al final.
    import video_maker
    def w(t, s): return {"word": t, "start": s, "end": s + 0.3}
    words = [w("uno", 2.5), w("dos", 3.0), w("tres", 3.5), w("cuatro", 8.0), w("cinco", 8.5), w("seis", 9.0)]
    cuts = video_maker._align_boundaries_to_script(["uno dos tres", "cuatro cinco seis"], words, 12.0)
    assert cuts[0] == 0.0 and cuts[-1] == 12.0 and cuts[1] == 8.0


def test_timeline_visible_content_covers_whole_audio():
    import video_maker
    def w(t, s): return {"word": t, "start": s, "end": s + 0.3}
    words = [w("uno", 2.5), w("dos", 3.0), w("tres", 3.5), w("cuatro", 8.0), w("cinco", 8.5), w("seis", 9.0)]
    scenes = [{"name": f"scene_{i:03d}.jpg", "type": "image", "native_duration": None} for i in range(2)]
    tl = video_maker._build_timeline(scenes, "a.wav", 12.0, words, "", frases=["uno dos tres", "cuatro cinco seis"])
    # Remotion pone las escenas una tras otra (duracion = end - start) menos el solape de transicion
    visible = sum(sc["endFrame"] - sc["startFrame"] for sc in tl["scenes"]) - (len(tl["scenes"]) - 1) * video_maker.TRANSITION_FRAMES
    assert abs(visible - round(12.0 * video_maker.FPS)) <= 1


def test_digits_and_words_are_the_same_to_the_narration_check():
    assert tts_engine._normalize_words("Tenía 4 años y 21 gatos, 150 sillas y 2026") == (
        "tenia cuatro anos y veintiuno gatos ciento cincuenta sillas y dos mil veintiseis".split())
    r = tts_engine.narration_diff("Eran 4 amigos y 12 perros", "Eran cuatro amigos y doce perros")
    assert r["ok"] and not r["changed"] and not r["added"]


def test_split_words_and_repeated_name_spelling_are_tolerated_but_real_errors_are_not():
    script = "Aanya dijo que crees en ti. Más tarde Aanya sonrió y sus amigos creen en ella."
    heard = "Anja dijo que crecen ti. Más tarde Anja sonrió y sus amigos creen en ella."
    r = tts_engine.narration_diff(script, heard)
    assert r["ok"], r
    assert ("aanya", "anja") in r["noise"] and ("crees en", "crecen") in r["noise"]
    # una palabra distinta que se oye una sola vez sigue contando como error
    bad = tts_engine.narration_diff("La anciana cruzó el río con su perro fiel y dormido", "La ansia cruzó el río con su perro fiel y dormido")
    assert bad["changed"] == [("anciana", "ansia")]


def test_speakable_spells_out_units_and_symbols_for_tts_and_verification():
    from tts_engine import speakable, narration_diff
    out = speakable("2 cordones de 4 mm, 1,2 m, 10 cm, 12 USD, 50% y 12 minutos")
    assert "4 milímetros" in out and "1 coma 2 metros" in out and "10 centímetros" in out
    assert "12 dólares" in out and "50 por ciento" in out and "12 minutos" in out
    heard = "dos cordones de cuatro milimetros uno coma dos metros diez centimetros doce dolares cincuenta por ciento y doce minutos"
    assert narration_diff(out, heard)["ok"]
