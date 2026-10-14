# -*- coding: utf-8 -*-
"""HISTORIAS ya no usa pilares de valor: su formato fijo trae su propia estructura."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline as bp


def test_no_niche_gets_the_value_pillars_anymore():
    assert bp.PILARES_KINDS == set()
    for kind in ("historias", "bebe_heroe", "ninio_selectivo", "macrame", "gaming_news", "ninio_post", "macrame_post", "gaming"):
        assert "PILARES DE VALOR" not in bp._text_system_prompt(kind)
