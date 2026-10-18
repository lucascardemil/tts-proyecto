# -*- coding: utf-8 -*-
"""Riesgo de slideshow: puro sobre timelines falsos."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import slideshow_risk as sr

FPS = 30


def scene(seconds, kind="image", ken="zoomIn", start=0):
    s = {"src": "a.jpg", "type": kind, "startFrame": start, "endFrame": start + round(seconds * FPS)}
    if kind == "image":
        s["kenBurns"] = ken
    return s


def timeline(*scenes):
    laid, cursor = [], 0
    for sc in scenes:
        sc = dict(sc, startFrame=cursor, endFrame=cursor + (sc["endFrame"] - sc["startFrame"]))
        cursor = sc["endFrame"]
        laid.append(sc)
    return {"scenes": laid}


def test_a_lively_video_is_strong():
    tl = timeline(*[scene(4.5) for _ in range(8)])
    risk = sr.score_timeline(tl)
    assert risk["verdict"] == "strong" and risk["average"] < 2.0
    assert set(risk["dimensions"]) == {"static_scenes", "low_motion", "long_hold", "slow_cuts"}
    assert all(0 <= d["score"] <= 5 and d["reason"] for d in risk["dimensions"].values())


def test_static_images_score_high_on_static_scenes():
    tl = timeline(*[scene(5, ken="none") for _ in range(6)])
    assert sr.score_timeline(tl)["dimensions"]["static_scenes"]["score"] == 5.0
    moving = timeline(*[scene(5, ken="panLeft") for _ in range(6)])
    assert sr.score_timeline(moving)["dimensions"]["static_scenes"]["score"] == 0.0


def test_low_motion_only_counts_when_motion_was_expected():
    tl = timeline(*[scene(5) for _ in range(6)])
    assert sr.score_timeline(tl, expects_motion=False)["dimensions"]["low_motion"]["score"] == 0.0
    assert sr.score_timeline(tl, expects_motion=True)["dimensions"]["low_motion"]["score"] == 5.0
    mixed = timeline(*[scene(5, kind="video") for _ in range(3)], *[scene(5) for _ in range(3)])
    assert sr.score_timeline(mixed, expects_motion=True)["dimensions"]["low_motion"]["score"] == 0.0


def test_long_hold_and_slow_cuts_scale_with_duration():
    ok = sr.score_timeline(timeline(*[scene(6) for _ in range(5)]))["dimensions"]
    assert ok["long_hold"]["score"] == 0.0
    slow = sr.score_timeline(timeline(*[scene(12) for _ in range(3)]))["dimensions"]
    assert slow["long_hold"]["score"] == 5.0 and slow["slow_cuts"]["score"] == 5.0
    video_only = sr.score_timeline(timeline(scene(12, kind="video")))["dimensions"]
    assert video_only["long_hold"]["score"] == 0.0  # una escena de video larga no es un "hold" de imagen


def test_worst_case_fails_and_thresholds_map_to_verdicts():
    bad = timeline(*[scene(14, ken="none") for _ in range(3)])
    assert sr.score_timeline(bad, expects_motion=True)["verdict"] == "fail"
    assert [sr._verdict(x) for x in (1.99, 2.0, 2.99, 3.0, 3.99, 4.0)] == [
        "strong", "acceptable", "acceptable", "revise", "revise", "fail"]


def test_empty_timeline_fails_without_dimensions():
    assert sr.score_timeline({"scenes": []}) == {"average": 5.0, "verdict": "fail", "dimensions": {}}
    assert sr.score_timeline({})["verdict"] == "fail"


def test_expects_motion_follows_the_settings():
    assert sr.expects_motion({}) is True
    assert sr.expects_motion({"generate_video_clips": False}) is False
    assert sr.expects_motion({"provider": "flow"}) is False
    assert sr.expects_motion({"image_provider": "flow"}) is False
    assert sr.expects_motion({"provider": "whatsapp", "generate_video_clips": True}) is True
