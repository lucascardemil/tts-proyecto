# -*- coding: utf-8 -*-
"""Lote de reels de noticias de videojuegos: registro del tipo, horarios, generacion y publicacion."""
import copy
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline as bp
import gaming_reel


def _stub_create(monkeypatch):
    monkeypatch.setattr(bp, "_load", lambda: {})
    monkeypatch.setattr(bp, "_save", lambda projects: None)
    monkeypatch.setattr(bp, "existing_slots", lambda *a, **k: [])


def test_the_type_is_registered_as_a_gaming_video():
    assert "gaming_reel" in bp.GAMING_TYPES and "gaming_reel" in bp.GAMING_VIDEO_TYPES
    assert "gaming_reel" not in bp.IMAGE_POST_PROFILES  # no es un post de imagen: lleva YouTube
    assert (bp._PROMPTS_DIR / "gaming_reel_system.md").exists()


def test_create_project_uses_the_gaming_eastern_slots_and_offsets(monkeypatch):
    _stub_create(monkeypatch)
    monkeypatch.setattr(gaming_reel.gaming_news, "next_reel_news", lambda *a, **k: pytest.fail("no genera al crear"))
    project = bp.create_project("JUGADASEPICASVIDEOJUEGOS", 4, 2, {"facebook": {"page_id": "1"}, "youtube": True}, {},
                                content_type="gaming_reel")
    assert project["type"] == "gaming_reel" and project["network_offsets"] == {"instagram": -3600}
    et = {datetime.fromisoformat(v["scheduled_at"]).astimezone(ZoneInfo(bp.US_AUDIENCE_TZ)).hour
          for v in project["videos"]}
    assert et <= set(bp.GAMING_SLOT_HOURS)
    assert project["networks"].get("youtube") is True  # los reels si van a YouTube Shorts


def test_dispatchers_route_the_reel(monkeypatch):
    calls = []
    monkeypatch.setattr(bp, "_generate_batch_reel", lambda pid, i: calls.append(("gen", pid, i)))
    monkeypatch.setattr(bp, "_publish_batch_clip", lambda pid, i: calls.append(("pub", pid, i)))
    bp._generate_batch_item("p", 1, "gaming_reel")
    bp._publish_batch_item("p", 1, "gaming_reel")
    assert calls == [("gen", "p", 1), ("pub", "p", 1)]


@pytest.fixture
def store(monkeypatch):
    projects = {"p1": {"id": "p1", "name": "gaming 1", "page_name": "JUGADASEPICASVIDEOJUEGOS", "status": "running",
                       "type": "gaming_reel", "video_settings": {"text_provider": "auto"},
                       "videos": [{"index": 0, "status": "pending", "error": None, "stage": None}]}}
    monkeypatch.setattr(bp, "_load", lambda: copy.deepcopy(projects))
    monkeypatch.setattr(bp, "_save", lambda p: (projects.clear(), projects.update(copy.deepcopy(p))))
    return projects


def test_generate_batch_reel_fills_the_item_like_a_clip(store, monkeypatch):
    seen = {}

    def fake(key, text_fn, on_stage=None):
        seen["key"] = key
        on_stage("guion")
        return {"video_path": "video/out/greel_x.mp4", "title": "T", "caption": "C\nSource: IGN",
                "hook": "H", "news_link": "https://x/1", "youtube": {"title": "T", "description": "D", "tags": ["a"]}}

    monkeypatch.setattr(gaming_reel, "generate_gaming_reel", fake)
    bp._generate_batch_reel("p1", 0)
    v = store["p1"]["videos"][0]
    assert seen["key"] == "p1_000"
    assert v["status"] == "ready" and v["video_path"].endswith("greel_x.mp4") and v["title"] == "T"
    assert v["yt_seo"]["tags"] == ["a"] and v["source_news"] == "https://x/1" and v["stage"] is None


def test_generate_batch_reel_failure_goes_through_the_retry_budget(store, monkeypatch):
    def boom(*a, **k):
        raise gaming_reel.ReelError("Guion de reel inválido tras 3 intentos: x")

    monkeypatch.setattr(gaming_reel, "generate_gaming_reel", boom)
    bp._generate_batch_reel("p1", 0)
    v = store["p1"]["videos"][0]
    assert v["status"] == "pending" and v["gen_attempts"] == 1 and "Guion de reel" in v["error"]
    for _ in range(bp.MAX_AUTO_RETRIES - 1):
        v["status"] = "pending"
        bp._generate_batch_reel("p1", 0)
    assert store["p1"]["videos"][0]["status"] == "error" and store["p1"]["videos"][0]["error_at"]


def test_text_function_uses_the_reel_prompt_kind(store, monkeypatch):
    captured = {}

    def fake_chain(kind, page_name, message, session, prefer="", attempted=None):
        captured.update(kind=kind, page=page_name, message=message)
        return "reply"

    monkeypatch.setattr(bp, "_generate_text_with_chain", fake_chain)

    def fake(key, text_fn, on_stage=None):
        assert text_fn("hello") == "reply"
        return {"video_path": "v.mp4", "title": "T", "caption": "C", "hook": "H", "news_link": "l", "youtube": {}}

    monkeypatch.setattr(gaming_reel, "generate_gaming_reel", fake)
    bp._generate_batch_reel("p1", 0)
    assert captured == {"kind": "gaming_reel", "page": "JUGADASEPICASVIDEOJUEGOS", "message": "hello"}


@pytest.mark.parametrize("item_type_name, expected", [("gaming_reel", True), ("gaming_clip", False)])
def test_publishing_labels_the_reel_as_ai_but_not_the_real_gameplay_clip(monkeypatch, item_type_name, expected):
    project = {"id": "p1", "name": "n", "type": item_type_name,
               "videos": [{"index": 0, "video_path": "v.mp4", "title": "T", "caption": "Cap #a #b", "yt_seo": None}]}
    monkeypatch.setattr(bp, "get_project", lambda pid: project)
    monkeypatch.setattr(bp, "_load", lambda: {"p1": project})
    monkeypatch.setattr(bp, "_save", lambda p: None)
    captured = {}
    monkeypatch.setattr(bp, "_publish_networks", lambda pid, i, publishers, preparers=None: captured.update(
        publishers=publishers, preparers=preparers))
    flags = {}
    monkeypatch.setattr(bp.instagram_publisher, "prepare_video",
                        lambda *a, **k: flags.__setitem__("ig_prepare", k.get("ai_generated")) or {"ok": False})
    monkeypatch.setattr(bp.instagram_publisher, "publish_video",
                        lambda *a, **k: flags.__setitem__("ig", k.get("ai_generated")) or {"ok": False, "error": "x"})
    monkeypatch.setattr(bp.youtube_publisher, "publish_video",
                        lambda *a, **k: flags.__setitem__("yt", a[5]) or {"ok": False, "error": "x"})
    monkeypatch.setitem(sys.modules, "app", type(sys)("app"))
    bp._publish_batch_clip("p1", 0)
    video = project["videos"][0]
    captured["preparers"]["instagram"](video, "pg")
    captured["publishers"]["instagram"](video, "pg")
    captured["publishers"]["youtube"](video, None)
    assert flags == {"ig_prepare": expected, "ig": expected, "yt": expected}
