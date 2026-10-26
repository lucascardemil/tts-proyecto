# -*- coding: utf-8 -*-
"""Ganchos y titulos de Shorts (youtube-agent-skill): puntaje, eleccion, lint, bloque de formulas e integracion."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline as bp
import gaming_reel as gr
import hook_craft as hc

STRONG_EN = "Players lost forty hours of progress to a single bug, but the real cause surprised everyone."
WEAK_EN = "Amazing insane secret game changer you won't believe"


def test_scores_separate_specific_hooks_from_vague_ones():
    strong, weak = hc.hook_score(STRONG_EN, "en"), hc.hook_score(WEAK_EN, "en")
    assert strong["verdict"] > weak["verdict"] + 25
    assert weak["band"] == "WEAK" and weak["weakest"] == "SPECIFICITY"
    assert set(strong["properties"]) == {"SPECIFICITY", "ADDRESS", "STAKES", "CURIOSITY", "BREVITY"}


def test_spelled_out_numbers_count_as_concrete_in_both_languages():
    # la narracion TTS no lleva digitos: "twenty-five million" / "siete meses" tienen que valer como cifra
    assert hc.hook_score("Sold twenty five million copies", "en")["properties"]["SPECIFICITY"] > \
        hc.hook_score("Sold plenty of copies", "en")["properties"]["SPECIFICITY"]
    assert hc.hook_score("Nuri esperó siete meses en el mismo banco", "es")["properties"]["SPECIFICITY"] > \
        hc.hook_score("Nuri esperó mucho tiempo en el mismo banco", "es")["properties"]["SPECIFICITY"]


def test_spanish_lexicon_penalizes_vague_words():
    vague = hc.hook_score("Una historia increíble y espectacular", "es")
    assert vague["weakest"] == "SPECIFICITY" and vague["verdict"] < 40


def test_address_is_measured_but_does_not_decide_the_verdict_by_default():
    third_person = "Sony cancelled the sequel after three years and nobody saw it coming."
    default = hc.hook_score(third_person, "en")
    with_address = hc.hook_score(third_person, "en", address_counts=True)
    assert default["weakest"] != "ADDRESS" and with_address["verdict"] < default["verdict"]
    assert "ADDRESS" in default["properties"]


def test_rank_and_pick_choose_the_best_candidate():
    ranked = hc.rank_hooks([WEAK_EN, STRONG_EN, ""], "en")
    assert [r["hook"] for r in ranked] == [STRONG_EN, WEAK_EN]
    assert hc.pick_hook([WEAK_EN, STRONG_EN], "en")["hook"] == STRONG_EN
    assert hc.pick_hook([], "en") == {}


def test_calibration_corpus_separates_good_medium_and_weak_hooks():
    import hook_corpus as corpus
    for lang, hooks in corpus.GOOD.items():
        assert all(hc.hook_score(h, lang)["verdict"] >= hc.HOOK_TARGET for h in hooks), (lang, hooks)
    for lang, hooks in corpus.MEDIUM.items():
        assert all(55 <= hc.hook_score(h, lang)["verdict"] < hc.HOOK_TARGET for h in hooks), (lang, hooks)
    for lang, hooks in corpus.WEAK.items():
        assert all(hc.hook_score(h, lang)["verdict"] < 55 for h in hooks), (lang, hooks)
    assert hc.band(80) == "STRONG" and hc.band(60) == "WORKABLE" and hc.band(59) == "WEAK"


def test_critique_only_when_even_the_best_is_clearly_weak():
    assert hc.critique(hc.rank_hooks([WEAK_EN], "en"), "en").startswith("the hook options are weak")
    assert hc.critique(hc.rank_hooks([WEAK_EN], "es"), "es").startswith("los ganchos propuestos son flojos")
    assert hc.critique(hc.rank_hooks([WEAK_EN, STRONG_EN], "en"), "en") == ""
    assert hc.critique([], "en") == ""


def test_formulas_cover_all_22_in_both_languages_and_classify():
    assert len(hc.formulas("en")) == len(hc.formulas("es")) == 22
    for lang in ("en", "es"):
        block = hc.formulas_block(lang)
        assert len(block.splitlines()) == 22 and "{{" not in block
    assert hc.classify("97% of players quit before level five", "en")[0] == "The Statistic"
    assert hc.classify("Todos evitaban a ese perro hasta que una vecina miró dentro", "es")[0].startswith("El Guardi")


def test_title_lint_flags_length_caps_vague_and_thumbnail_duplicates():
    long_title = "Hollow Knight Silksong Epic Gameplay Highlights Best Moments And Plays 2026 #Shorts"
    r = hc.title_lint(long_title, "EPIC PLAY", "en")
    codes = {code for code, _ in r["issues"]}
    assert {"length", "mobile", "vague", "duplicate"} <= codes
    ok = hc.title_lint("Silksong sells 2.5M copies in a day", "RECORD SALES", "en")
    assert ok["issues"] == [] and ok["score"] > r["score"]
    assert "thumb-length" in {c for c, _ in hc.title_lint("Silksong 2.5 million", "ONE TWO THREE FOUR", "en")["issues"]}
    assert "shouting" in {c for c, _ in hc.title_lint("THIS IS INSANE NOW 3 times", "", "en")["issues"]}


def test_trim_title_cuts_at_a_word_and_keeps_the_start():
    title = "Hollow Knight Silksong sells one million copies in a single day and breaks a record"
    out = hc.trim_title(title, 60)
    assert len(out) <= 60 and title.startswith(out) and not out.endswith(" ")
    assert hc.trim_title("Short title", 60) == "Short title"


# ---- reel de noticias: HOOK_OPTIONS ---------------------------------------------------------------------------------

from test_gaming_reel import SCRIPT, SOURCE  # noqa: E402

OPTIONS = """HOOK_OPTIONS:
1. SILKSONG SOLD OUT FAST | Twenty five hundred thousand players bought it on day one and the studio did not see it coming
2. DAY ONE RECORD | The wait is over and a lot of people showed up
3. TWO AND A HALF MILLION | Two and a half million copies in a single day, but nobody expected the one bug that almost ruined it
"""


def test_hook_options_pick_the_best_and_rewrite_scene_one():
    out = gr.parse_reel_script(OPTIONS + SCRIPT, SOURCE)
    assert out["scenes"][0]["text"] == "TWO AND A HALF MILLION" and out["hook"] == "TWO AND A HALF MILLION"
    assert out["scenes"][0]["narration"].startswith("Two and a half million copies in a single day")
    assert out["hook_score"] >= hc.HOOK_TARGET and isinstance(out["hook_formula"], str)


def test_without_hook_options_the_script_is_unchanged():
    out = gr.parse_reel_script(SCRIPT, SOURCE)
    assert out["hook"] == "SILKSONG SOLD OUT FAST" and out["scenes"][0]["narration"].startswith("The wait is finally over")
    assert "hook_score" in out


def test_invalid_hook_options_are_ignored_not_trusted():
    bad = """HOOK_OPTIONS:
1. NINE BILLION SOLD $99 | Nine billion people bought it on the very first morning of the launch
2. AMAZING DAY | It sold 5 million copies in a day and it is amazing
"""
    out = gr.parse_reel_script(bad + SCRIPT, SOURCE)  # cifra inventada en pantalla y digitos en la frase hablada
    assert out["hook"] == "SILKSONG SOLD OUT FAST"


def test_all_weak_options_are_rejected_with_the_critique_until_the_last_attempt():
    weak = """HOOK_OPTIONS:
1. AMAZING NEWS | Amazing insane secret news about a game changer that is huge and incredible
2. WOW | Amazing incredible secret ultimate game changer here
"""
    script = weak + SCRIPT.replace("The wait is finally over and players showed up in huge numbers on launch day for the long awaited sequel",
                                   "Amazing incredible secret ultimate game changer here")
    with pytest.raises(gr.ReelError, match="hook options are weak"):
        gr.parse_reel_script(script, SOURCE, strict_hooks=True)
    out = gr.parse_reel_script(script, SOURCE, strict_hooks=False)  # ultimo intento: se acepta el mejor
    assert out["hook_score"] < hc.HOOK_TARGET


def test_write_script_feeds_the_critique_back_and_succeeds_on_retry():
    weak = "HOOK_OPTIONS:\n1. AMAZING | Amazing incredible secret ultimate game changer here\n2. WOW | Amazing incredible secret ultimate\n"
    answers = [weak + SCRIPT.replace("The wait is finally over and players showed up in huge numbers on launch day for the long awaited sequel",
                                     "Amazing incredible secret ultimate game changer here"), OPTIONS + SCRIPT]
    messages = []

    def text_fn(message):
        messages.append(message)
        return answers[len(messages) - 1]

    item = {"title": "Silksong sells big", "summary": SOURCE, "body": "", "angle": "RECORD", "source": "IGN",
            "link": "https://x", "published": datetime.now(timezone.utc)}
    out = gr.write_script(item, text_fn)
    assert len(messages) == 2 and "hook options are weak" in messages[1]
    assert out["hook"] == "TWO AND A HALF MILLION"


def test_youtube_title_of_the_reel_is_trimmed_to_sixty_characters():
    parsed = gr.parse_reel_script(SCRIPT, SOURCE)
    parsed["title"] = "Hollow Knight Silksong sells one million copies in a single day and breaks a record"
    assert len(gr.youtube_seo(parsed)["title"]) <= 60


# ---- guiones en espanol: GANCHOS ------------------------------------------------------------------------------------

STORY = """HOOK_TEXT: Nadie la veía 🐕
GANCHOS:
1. Nuri esperó siete meses en el mismo banco, y nadie se detuvo ni una sola vez.
2. Todos evitaban a esa perra hasta que una vecina miró dentro de su caja.
3. Una historia increíble y muy bonita.

PERSONAJES:
[PERRA]: golden dog

Guion
Nuri esperó en un banco de la plaza. Pasaron los días y nadie venía a buscarla nunca más.

Imagen 1
Frase del guion: «Nuri esperó en un banco de la plaza.»
Prompt: a dog on a bench
"""


def test_split_hook_options_removes_the_numbered_block_only():
    options, text = bp._split_hook_options(STORY)
    assert len(options) == 3 and options[0].startswith("Nuri esperó siete meses")
    assert "GANCHOS" not in text and "HOOK_TEXT: Nadie la veía" in text and "PERSONAJES:" in text
    assert bp._split_hook_options("HOOK_TEXT: x\nGuion\nHola.") == ([], "HOOK_TEXT: x\nGuion\nHola.")


def test_apply_best_hook_replaces_the_first_sentence_in_script_and_image_phrase():
    options, text = bp._split_hook_options(STORY)
    text, info = bp._apply_best_hook(text, options)
    assert info["hook"].startswith("Nuri esperó siete meses") and info["hook_score"] > 0
    assert text.count("Nuri esperó siete meses en el mismo banco") == 2  # guion + "Frase del guion"
    assert "Nuri esperó en un banco de la plaza." not in text


def test_apply_best_hook_keeps_the_original_when_the_gain_is_small_or_nothing_to_score():
    flat = "HOOK_TEXT: x\nGuion\nNuri esperó siete meses en el mismo banco y nadie la veía.\n\nImagen 1\nFrase del guion: «a»\nPrompt: p\n"
    text, info = bp._apply_best_hook(flat, ["Nuri esperó siete meses en el mismo banco y nadie la miraba."])
    assert text == flat and info["hook"].startswith("Nuri esperó siete meses en el mismo banco y nadie la veía")
    assert bp._apply_best_hook(flat, []) == (flat, None)
    assert bp._apply_best_hook("sin guion", ["x"]) == ("sin guion", None)


@pytest.mark.parametrize("kind,lang", [("historias", "es"), ("macrame", "es"), ("ninio_selectivo", "es"),
                                      ("gaming_reel", "en"), ("gaming_news", "en")])
def test_system_prompts_carry_the_formulas_without_placeholders(kind, lang):
    prompt = bp._text_system_prompt(kind)
    assert "{{" not in prompt
    first = hc.formulas(lang)[0]["name"]
    assert first in prompt
    assert ("GANCHOS:" in prompt) == (lang == "es")
    assert ("HOOK_OPTIONS:" in prompt) == (kind == "gaming_reel")


def test_other_prompts_are_untouched():
    assert "GANCHOS" not in bp._text_system_prompt("historias_post")
