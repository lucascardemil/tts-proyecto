# -*- coding: utf-8 -*-
"""_slideshow_summary: lo que el lote guarda en cada video (solo puntajes, sin textos)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline


def tl(*seconds, ken="zoomIn"):
    scenes, cursor = [], 0
    for s in seconds:
        scenes.append({"src": "a.jpg", "type": "image", "kenBurns": ken, "startFrame": cursor, "endFrame": cursor + round(s * 30)})
        cursor += round(s * 30)
    return {"scenes": scenes}


def test_summary_keeps_only_average_verdict_and_numeric_scores():
    summary = batch_pipeline._slideshow_summary(tl(5, 5, 5, 5), {})
    assert set(summary) == {"average", "verdict", "dimensions"}
    assert set(summary["dimensions"]) == {"static_scenes", "low_motion", "long_hold", "slow_cuts"}
    assert all(isinstance(v, float) for v in summary["dimensions"].values())


def test_summary_uses_the_video_settings_to_decide_if_motion_was_expected():
    still = tl(5, 5, 5, 5)
    assert batch_pipeline._slideshow_summary(still, {"generate_video_clips": True})["dimensions"]["low_motion"] == 5.0
    assert batch_pipeline._slideshow_summary(still, {"generate_video_clips": False})["dimensions"]["low_motion"] == 0.0
    assert batch_pipeline._slideshow_summary(still, {"provider": "flow"})["dimensions"]["low_motion"] == 0.0
