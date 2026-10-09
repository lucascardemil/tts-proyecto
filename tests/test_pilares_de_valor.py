# -*- coding: utf-8 -*-
"""Los guiones largos llevan la tecnica de "pilares de valor"; el resto de prompts no."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline as bp


def test_long_script_niches_get_the_value_pillars():
    assert (bp._PROMPTS_DIR / "_pilares_de_valor.md").exists()
    for kind in ("historias", "macrame", "ninio_selectivo"):
        text = bp._text_system_prompt(kind)
        assert "PILARES DE VALOR" in text and "Prueba (frase 2)" in text
        # el prompt del nicho va primero y su formato de salida no se pisa
        assert text.startswith((bp._PROMPTS_DIR / f"{kind}_system.md").read_text(encoding="utf-8")[:80])


def test_fixed_format_and_image_posts_do_not_get_them():
    for kind in ("bebe_heroe", "gaming_news", "ninio_post", "macrame_post", "gaming"):
        assert "PILARES DE VALOR" not in bp._text_system_prompt(kind)
