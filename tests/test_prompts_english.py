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


def test_ninio_selectivo_is_a_14s_ebook_ad_with_fixed_length():
    import batch_pipeline as bp
    text = bp._text_system_prompt("ninio_selectivo")
    assert "Comenta EBOOK" in text and "38 a 46 palabras" in text and "exactamente 5 imágenes" in text and "NO dibujes ningún celular" in text
    assert "PILARES DE VALOR" not in text
    assert bp._has_fixed_length("Niño Selectivo, Familia en Paz") and bp._has_fixed_length("Bebé Héroe")
    assert not bp._has_fixed_length("HISTORIAS") and bp._has_fixed_length("MACRAME CREATIVO")


def test_ninio_selectivo_video_gets_the_ebook_phone_card(tmp_path, monkeypatch):
    import batch_pipeline as bp
    assert bp.NINIO_EBOOK_COVER.exists()
    assert bp.phone_card_for_page("Niño Selectivo, Familia en Paz") == str(bp.NINIO_EBOOK_COVER)
    assert bp.phone_card_for_page("HISTORIAS") is None and bp.phone_card_for_page("MACRAME CREATIVO") == str(bp.AD_KINDS["macrame"]["cover"])


def test_ninio_selectivo_images_must_carry_the_character_and_kitchen_sheets():
    import pytest
    import auto_pipeline as ap
    text = (
        "HOOK_TEXT: No comía zapallo 🎃\n\nPERSONAJES:\n"
        "[MATEO]: 4 years old boy, curly dark brown hair, brown eyes, freckles, mustard (#E1AD01) t-shirt\n"
        "[LAURA]: 32 years old woman, long wavy black hair, brown eyes, white apron over a red dress\n"
        "[COCINA]: warm kitchen, sage green (#B7C4A8) walls, window on the left, light wooden table\n\n"
        "Guion\nMi hijo no comía zapallo. Comenta EBOOK y te envío las recetas, de verdad que sí lo recomiendo hoy.\n\n"
        "Imagen 1\nFrase del guion: «Mi hijo no comía zapallo»\nPrompt: Vertical 9:16, a plate of pumpkin in [COCINA].\n\n"
        "Imagen 2\nFrase del guion: «Comenta EBOOK»\nPrompt: Vertical 9:16, [MATEO] pushes the plate while [LAURA] waits in [COCINA].\n"
    )
    story = ap._parse_story(text, "t_ninio_sheets")
    ap.validate_ninio_story(story)
    assert "Cocina (warm kitchen" in story["prompts"][0]["prompt"]
    assert story["prompts"][1]["prompt"].endswith(ap.CONSISTENCY_TAIL)
    # la cocina se agrega sola aunque el modelo no use la etiqueta; lo que no se puede inventar es el personaje
    no_kitchen = ap._parse_story(text.replace("in [COCINA]", "in a kitchen"), "t_ninio_sheets")
    ap.validate_ninio_story(no_kitchen)
    assert "Setting: Cocina (warm kitchen" in no_kitchen["prompts"][1]["prompt"]
    bad = ap._parse_story(text.replace("[MATEO] pushes the plate while [LAURA] waits", "someone pushes the plate"), "t_ninio_sheets")
    with pytest.raises(ap.PipelineError, match="(?i)no se encontraron prompts de imagen"):
        ap.validate_ninio_story(bad)


def test_macrame_video_is_a_17s_ebook_ad_with_the_same_guarantees():
    import pytest
    import auto_pipeline as ap
    import batch_pipeline as bp
    text = bp._text_system_prompt("macrame")
    assert "Comenta EBOOK" in text and "38 a 48 palabras" in text and "exactamente 5 imágenes" in text
    assert "NO dibujes ningún celular" in text and "PILARES DE VALOR" not in text
    assert bp.ad_kind_for_page("MACRAME CREATIVO") == "macrame" and bp.ad_kind_for_page("HISTORIAS") is None
    sheet = ap.build_trigger_message("dame una historia", None, kind="macrame")
    assert "[ESCENA]" in sheet and "la misma en las 5 imagenes" in sheet
    story_text = (
        "HOOK_TEXT: Mi tercer proyecto 🧶\n\nPERSONAJES:\n"
        "[CAMILA]: 28 years old woman, long wavy brown hair, hazel eyes, freckles, cream (#F2E8D5) knit sweater\n"
        "[PIEZA]: large beige macrame wall hanging, 3 cm cotton cord (#E8DCC4), square knots and fringe\n"
        "[ESCENA]: cozy boho living room, sage (#B7C4A8) walls, beige sofa, window on the left\n\n"
        "Guion\nYo pensaba que el macramé era solo para expertas. Comenta EBOOK y te envío la guía, de verdad que sí.\n\n"
        "Imagen 1\nFrase del guion: «Yo pensaba»\nPrompt: Vertical 9:16, hands knotting cord in [ESCENA].\n\n"
        "Imagen 2\nFrase del guion: «Comenta EBOOK»\nPrompt: Vertical 9:16, [CAMILA] smiles holding [PIEZA] at [ESCENA].\n"
    )
    ap.validate_ad_story(ap._parse_story(story_text, "t_macrame_ad"), "Escena")
    no_tag = ap._parse_story(story_text.replace("knotting cord in [ESCENA]", "knotting cord in a room"), "t_macrame_ad")
    ap.validate_ad_story(no_tag, "Escena")  # la sala se agrega sola si el modelo no usa la etiqueta
    assert "Setting: Escena (cozy boho" in no_tag["prompts"][0]["prompt"]
    only_piece = ap._parse_story(story_text.replace("[CAMILA] smiles holding [PIEZA]", "[PIEZA] hangs on the wall"), "t_macrame_ad")
    ap.validate_ad_story(only_piece, "Escena", ("Pieza",))  # la pieza colgada sin la mujer es valida
    with pytest.raises(ap.PipelineError):
        ap.validate_ad_story(only_piece, "Escena")
    bad = ap._parse_story(story_text.replace("[CAMILA] smiles holding [PIEZA]", "someone holds a thing"), "t_macrame_ad")
    with pytest.raises(ap.PipelineError, match="(?i)no se encontraron prompts de imagen"):
        ap.validate_ad_story(bad, "Escena")


def test_macrame_ad_publication_text_and_phone_card(monkeypatch, tmp_path):
    import batch_pipeline as bp
    assert (bp._PROMPTS_DIR / "macrame_copy_system.md").exists()
    monkeypatch.setattr(bp.text_provider, "generate_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    out = bp._build_publish_content("Yo pensaba que el macramé era solo para expertas. Comenta EBOOK.", "t", copy_kind="macrame")
    assert out["facebook_description"] == bp.MACRAME_COPY_FALLBACK and "comenta EBOOK" in out["facebook_description"]
    # celular con la portada: solo si el archivo existe
    cover = tmp_path / "ebook_cover.jpg"
    monkeypatch.setitem(bp.AD_KINDS["macrame"], "cover", cover)
    assert bp.phone_card_for_page("MACRAME CREATIVO") is None
    cover.write_bytes(b"x")
    assert bp.phone_card_for_page("MACRAME CREATIVO") == str(cover)
