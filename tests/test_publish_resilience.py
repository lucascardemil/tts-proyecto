# -*- coding: utf-8 -*-
"""Publicacion: sin pedir texto a la IA mientras todas las redes esperan, y Instagram reprogramado si fallo su dia."""
import copy
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline as bp


def _store(monkeypatch, scheduled_at, networks, **video):
    v = {"index": 0, "status": "publishing", "scheduled_at": scheduled_at.isoformat(), "video_path": "x.mp4",
         "published_at": {"facebook": datetime.now().isoformat()}, "error": None, "script_text": "Guion de prueba."}
    v.update(video)
    projects = {"p1": {"id": "p1", "name": "n", "page_name": "HISTORIAS", "status": "running", "networks": networks,
                       "network_offsets": {}, "videos": [v]}}
    monkeypatch.setattr(bp, "_load", lambda: copy.deepcopy(projects))
    monkeypatch.setattr(bp, "_save", lambda p: (projects.clear(), projects.update(copy.deepcopy(p))))
    monkeypatch.setattr(bp, "_gap_ok", lambda path: True)
    monkeypatch.setitem(sys.modules, "app", type(sys)("app"))
    sys.modules["app"]._PUBLISHED_IG_PATH = Path("ig.json")
    return projects


# ---- el texto de la IA solo se pide cuando una red publica de verdad, y una sola vez -----------------------------

def test_no_text_is_requested_while_every_network_waits(monkeypatch):
    _store(monkeypatch, datetime.now() + timedelta(days=3), {"facebook": {"page_id": "1"}, "instagram": {"page_id": "1"}})
    monkeypatch.setattr(bp, "get_project", lambda pid: bp._load()[pid])
    monkeypatch.setattr(bp, "_build_publish_content", lambda *a, **k: pytest.fail("no debe llamar a la IA"))
    bp._publish_batch_video("p1", 0)
    assert bp._load()["p1"]["videos"][0]["status"] == "ready"


def test_built_text_is_saved_and_reused(monkeypatch):
    # hoy pero ya pasado (no "ayer": cerca de la medianoche now()-1h cae en el dia anterior y dispara la reprogramacion)
    projects = _store(monkeypatch, datetime.now().replace(hour=0, minute=0, second=1, microsecond=0),
                      {"instagram": {"page_id": "1"}}, published_at={})
    monkeypatch.setattr(bp, "get_project", lambda pid: bp._load()[pid])
    built = []

    def fake_build(*a, **k):
        built.append(1)
        return {"title": "T", "yt_description": "", "yt_tags": [], "facebook_description": "F",
                "instagram_description": "I", "ai": True}

    sent = []
    monkeypatch.setattr(bp, "_build_publish_content", fake_build)
    monkeypatch.setattr(bp, "_publish_prepared_instagram", lambda v, page_id: None)
    monkeypatch.setattr(bp.instagram_publisher, "publish_video",
                        lambda path, title, desc, page_id=None: sent.append((title, desc)) or {"ok": False, "error": "x"})
    bp._publish_batch_video("p1", 0)
    projects["p1"]["videos"][0]["status"] = "publishing"
    bp._publish_batch_video("p1", 0)
    assert len(built) == 1 and sent == [("T", "I"), ("T", "I")]


def test_fallback_text_is_retried_after_an_hour_but_ai_text_is_kept(monkeypatch):
    projects = _store(monkeypatch, datetime.now(), {"instagram": {"page_id": "1"}})
    saved = lambda ai, ago: {"key": bp.hashlib.sha1(b"Guion de prueba.").hexdigest(),
                             "built_at": (datetime.now() - ago).isoformat(), "content": {"title": "T", "ai": ai}}
    video = projects["p1"]["videos"][0]
    video["publish_content"] = saved(False, timedelta(minutes=10))
    assert bp._saved_publish_content("p1", 0, "Guion de prueba.") == {"title": "T", "ai": False}
    video["publish_content"] = saved(False, timedelta(hours=2))
    assert bp._saved_publish_content("p1", 0, "Guion de prueba.") is None
    video["publish_content"] = saved(True, timedelta(days=3))
    assert bp._saved_publish_content("p1", 0, "Guion de prueba.")["ai"] is True
    assert bp._saved_publish_content("p1", 0, "otro guion") is None


# ---- Instagram fallo y su dia ya paso -> reprograma -------------------------------------------------------------

def _run_failing_instagram(monkeypatch, scheduled_at, **video):
    projects = _store(monkeypatch, scheduled_at, {"instagram": {"page_id": "pg"}}, published_at={}, **video)
    bp._publish_networks("p1", 0, {"instagram": lambda v, page_id: {"ok": False, "error": "Instagram tardó demasiado en procesar el video."}})
    return projects["p1"]["videos"][0]


def test_instagram_failure_on_a_past_day_reschedules_to_a_future_slot(monkeypatch):
    v = _run_failing_instagram(monkeypatch, datetime.now() - timedelta(days=2), ig_container={"id": "c", "prepared_at": "x"})
    new = datetime.fromisoformat(v["scheduled_at"])
    assert new > datetime.now() + timedelta(hours=1) and v["status"] == "ready"
    assert v["publish_attempts"] == 0 and v["reschedules"] == 1 and "ig_container" not in v


def test_instagram_failure_the_same_day_only_counts_an_attempt(monkeypatch):
    slot = datetime.now().replace(hour=0, minute=5) if datetime.now().hour > 0 else datetime.now() - timedelta(minutes=1)
    v = _run_failing_instagram(monkeypatch, slot)
    assert v["publish_attempts"] == 1 and "reschedules" not in v and v["scheduled_at"] == slot.isoformat()


def test_reschedules_are_capped(monkeypatch):
    v = _run_failing_instagram(monkeypatch, datetime.now() - timedelta(days=2), reschedules=bp.MAX_IG_RESCHEDULES)
    assert v["reschedules"] == bp.MAX_IG_RESCHEDULES and v["publish_attempts"] == 1


def test_auth_errors_are_not_rescheduled(monkeypatch):
    projects = _store(monkeypatch, datetime.now() - timedelta(days=2), {"instagram": {"page_id": "pg"}}, published_at={})
    bp._publish_networks("p1", 0, {"instagram": lambda v, page_id: {"ok": False, "error": "token vencido", "auth_error": True}})
    v = projects["p1"]["videos"][0]
    assert v["status"] == "publish_error" and "reschedules" not in v
