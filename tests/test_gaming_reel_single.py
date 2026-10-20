# -*- coding: utf-8 -*-
"""Reel de noticias de videojuegos en "Crear video" (generacion y publicacion sueltas)."""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import gaming_reel


def _panel_client(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    return app_module, app_module.app.test_client(), auth


class _FakeThread:
    started = []

    def __init__(self, target=None, args=(), **kw):
        self.target, self.args = target, args

    def start(self):
        _FakeThread.started.append((self.target, self.args))


def test_both_selectors_offer_the_reel_and_the_page_wires_it(monkeypatch):
    app_module, client, auth = _panel_client(monkeypatch)
    html = client.get("/", headers=auth).get_data(as_text=True)
    assert html.count('<option value="gaming_reel">') == 2  # "Crear video" y lote
    assert 'kind: "reel"' in html and "Generar reel de noticias" in html and "Escribiendo el guion del reel" in html


def test_start_route_creates_a_reel_job_that_travels_as_a_video(monkeypatch):
    app_module, client, auth = _panel_client(monkeypatch)
    _FakeThread.started = []
    monkeypatch.setattr(app_module, "_gaming_jobs", {})
    monkeypatch.setattr(app_module.job_store, "save", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "_prune_gaming_jobs", lambda: None)
    monkeypatch.setattr(app_module.threading, "Thread", _FakeThread)
    resp = client.post("/api/gaming/start", json={"kind": "reel", "text_provider": "auto"}, headers=auth)
    assert resp.get_json()["ok"]
    (target, args), = _FakeThread.started
    assert target is app_module._run_gaming_reel_generate and args[1:] == ("auto", "", "", True)
    (job,) = app_module._gaming_jobs.values()
    assert job["kind"] == "clip" and job["profile"] == "gaming_reel" and job["stage"] == "noticia"


def test_reel_runner_stores_the_result_like_a_clip(monkeypatch):
    import app as app_module
    jobs = {"j1": {"kind": "clip", "profile": "gaming_reel", "status": "generating", "networks": {}}}
    monkeypatch.setattr(app_module, "_gaming_jobs", jobs)
    monkeypatch.setattr(app_module.job_store, "save", lambda *a, **k: None)
    monkeypatch.setattr(app_module.batch_pipeline, "_generate_text_with_chain", lambda *a, **k: "reply")
    seen = {}

    def fake(key, text_fn, on_stage=None, **kw):
        seen["text"] = text_fn("hello")
        on_stage("guion")
        return {"video_path": "v.mp4", "title": "T", "caption": "C", "hook": "H", "news_link": "https://x",
                "youtube": {"title": "T", "description": "D", "tags": []}}

    monkeypatch.setattr(gaming_reel, "generate_gaming_reel", fake)
    app_module._run_gaming_reel_generate("j1", "")
    job = jobs["j1"]
    assert seen["text"] == "reply" and job["status"] == "ready" and job["hook"] == "H"
    assert job["video_path"] == "v.mp4" and job["yt_seo"]["title"] == "T" and job["source_news"] == "https://x"


def test_reel_runner_reports_errors_in_the_job(monkeypatch):
    import app as app_module
    jobs = {"j1": {"kind": "clip", "profile": "gaming_reel", "status": "generating", "networks": {}}}
    monkeypatch.setattr(app_module, "_gaming_jobs", jobs)
    monkeypatch.setattr(app_module.job_store, "save", lambda *a, **k: None)

    def boom(*a, **k):
        raise gaming_reel.ReelError("Guion de reel inválido tras 3 intentos: x")

    monkeypatch.setattr(gaming_reel, "generate_gaming_reel", boom)
    app_module._run_gaming_reel_generate("j1", "")
    assert jobs["j1"]["status"] == "error" and "Guion de reel" in jobs["j1"]["error"]


def test_publishing_a_reel_flags_it_as_ai_and_a_real_clip_as_not(monkeypatch):
    import app as app_module
    flags = []
    monkeypatch.setattr(app_module, "_gaming_net_update", lambda *a, **k: None)
    monkeypatch.setattr(app_module.youtube_publisher, "publish_video",
                        lambda *a, **k: flags.append(("yt", a[5], k.get("language"))) or {"ok": False, "error": "x"})
    monkeypatch.setattr(app_module.instagram_publisher, "publish_video",
                        lambda *a, **k: flags.append(("ig", k.get("ai_generated"))) or {"ok": False, "error": "x"})
    for profile, expected in (("gaming_reel", True), ("gaming_image", False)):
        job = {"kind": "clip", "profile": profile, "video_path": "v.mp4", "title": "T", "yt_seo": None}
        flags.clear()
        app_module._run_gaming_clip_publish("j", job, "youtube", None, None, "cap")
        app_module._run_gaming_clip_publish("j", job, "instagram", None, None, "cap")
        assert flags == [("yt", expected, "en"), ("ig", expected)], profile


def test_ajustes_has_a_flow_connection_card(monkeypatch):
    app_module, client, auth = _panel_client(monkeypatch)
    html = client.get("/", headers=auth).get_data(as_text=True)
    assert "Conexión de Google Flow" in html and "ajustes-open-flow-btn" in html and "checkFlowSettings();" in html
