# -*- coding: utf-8 -*-
"""Los prompts de imagen tienen que venir en ingles (Meta AI rinde mejor): si no, se regenera el texto."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import auto_pipeline as ap
import batch_pipeline as bp

EN = ("9:16 vertical, photorealistic, warm and hopeful: close-up of the rescued dog looking up at the old fisherman "
      "on a wooden pier at sunrise, soft golden light, detailed fur, gentle expression")
ES = ("9:16 vertical, fotorrealista, cálido y esperanzador: primer plano del perro rescatado mirando al viejo "
      "pescador en un muelle de madera al amanecer, con una luz suave y dorada")


def _story(*prompts):
    return {"prompts": [{"index": i + 1, "frase": "f", "prompt": p} for i, p in enumerate(prompts)]}


def test_english_prompts_pass():
    ap.validate_prompts_english(_story(EN, EN))


def test_spanish_prompts_are_rejected_with_their_numbers():
    with pytest.raises(ap.PipelineError, match=r"no están en inglés.*2"):
        ap.validate_prompts_english(_story(EN, ES))


def test_character_sheet_in_english_with_few_spanish_names_is_fine():
    ap.validate_prompts_english(_story("9:16, photorealistic: the old fisherman (Don José) feeds the dog at the pier of Valparaíso"))


def test_error_is_healable_and_historias_prompt_demands_english():
    assert bp._is_healable_error("Los prompts de imagen no están en inglés (imágenes: 2)")
    text = bp._text_system_prompt("historias")
    assert "NUNCA en español" in text


def test_script_after_last_image_is_not_part_of_its_prompt():
    import auto_pipeline as ap
    text = (
        "Imagen 1\nFrase del guion: «Hola mundo»\nPrompt: 9:16, photorealistic, a keychain on a wooden table, soft light.\n\n"
        "Imagen 2\nFrase del guion: «Adiós»\nPrompt: 9:16, photorealistic, hands holding the finished keychain.\n\n"
        "GUION\n¿Y si te digo que con dos metros de cuerda puedes hacer esto? En solo doce minutos y con dos cordones sencillos."
    )
    story = ap._parse_story(text, "t_trailing_guion")
    assert "GUION" not in story["prompts"][-1]["prompt"] and "cuerda" not in story["prompts"][-1]["prompt"]
    ap.validate_prompts_english(story)
