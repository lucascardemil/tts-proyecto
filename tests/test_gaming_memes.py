# -*- coding: utf-8 -*-
"""Memes de videojuegos de Memedroid: lectura de la galeria, seleccion, imagen, caption y publicacion."""
import base64
import copy
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline as bp
import gaming_memes as gm

NOW = datetime(2026, 10, 20, 12, 0, 0)


def _article(item_id, kind="1", author="LEROYkid", title="Kowalski improvisa algo", votes=31, positive=28,
             when="2026-10-19T07:23:02", tags="Kowalski,Videojuegos,Memes Graciosos"):
    return (
        f'<article id="article-item-{item_id}" data-type="{kind}" data-item-id="{item_id}" class="gallery-item">'
        f'<header class="item-header" data-ts="1"><a href="/user/view/{author}" rel="author">'
        f'<img src="https://appv2.memedroid.com/a.png" class="img-circle">{author}</a>'
        f'<time class="grey-4" datetime="{when}">16h</time>'
        f'<h1><a class="item-header-title dyn-link" href="/memes/detail/{item_id}/x?refGallery=tags&amp;page=1">{title}</a></h1></header>'
        f'<picture><source srcset="https://memes.memedroid.com/images/2026-10-19/{item_id}.webp" type="image/webp">'
        f'<img src="https://memes.memedroid.com/images/2026-10-19/{item_id}.jpeg" class="img-responsive"></picture>'
        f'<div class="tags-container" data-tags="{tags}"></div>'
        f'<div class="item-rating-container" data-votes="{votes}" data-positive-votes="{positive}">'
        f'<span class="green-1">90%</span></div></article>'
    )


def test_parse_gallery_reads_image_memes_and_skips_other_types():
    page = _article("1") + _article("2", kind="3") + "<article>sin formato</article>"
    (meme,) = gm.parse_gallery(page)
    assert meme["id"] == "1" and meme["author"] == "LEROYkid" and meme["title"] == "Kowalski improvisa algo"
    assert meme["image_url"] == "https://memes.memedroid.com/images/2026-10-19/1.jpeg"
    assert meme["votes"] == 31 and meme["rating_pct"] == 90 and meme["link"] == "https://es.memedroid.com/memes/detail/1"
    assert meme["tags"] == ["Kowalski", "Videojuegos", "Memes Graciosos"] and meme["posted_at"].startswith("2026-10-19")


def test_a_dash_title_is_empty():
    (meme,) = gm.parse_gallery(_article("1", title="-"))
    assert meme["title"] == ""


def _m(item_id, **kw):
    return gm.parse_gallery(_article(item_id, **kw))[0]


def test_pick_meme_filters_and_ranks():
    memes = [
        _m("101"), _m("102", votes=5, positive=5), _m("103", votes=40, positive=20),
        _m("104", when="2026-10-01T10:00:00"), _m("105", tags="Politica,Chistes", title="x"),
        _m("106", votes=60, positive=55), _m("107", votes=300, positive=285),
    ]
    assert gm.pick_meme(memes, {"101"}, NOW)["id"] == "107"
    assert gm.pick_meme([_m("102", votes=5, positive=5)], set(), NOW) is None


def test_caption_credits_the_author_and_links_the_original():
    meme = _m("77", author="Striker_4")
    caption = gm.build_caption(meme)
    assert "Meme de Striker_4 vía Memedroid" in caption and "Original: https://es.memedroid.com/memes/detail/77" in caption
    assert caption.rstrip().endswith("#memesenespañol")
    assert gm.build_caption(meme, 1) != caption  # el gancho rota


def test_download_converts_and_rejects_unusable_images(tmp_path, monkeypatch):
    def fake_get(url):
        class R:
            content = (tmp_path / "src.png").read_bytes()
        return R

    Image.new("RGBA", (1600, 1200), (10, 20, 30, 255)).save(tmp_path / "src.png")
    monkeypatch.setattr(gm, "_get", fake_get)
    out = gm.download(_m("1"), tmp_path / "x" / "cover.jpg")
    with Image.open(out) as img:
        assert img.format == "JPEG" and img.mode == "RGB" and max(img.size) == gm.MAX_SIDE_PX
    assert not list((tmp_path / "x").glob("*.src"))
    Image.new("RGB", (300, 1500)).save(tmp_path / "src.png")  # demasiado alargada para Instagram
    with pytest.raises(gm.MemeError):
        gm.download(_m("2"), tmp_path / "y" / "cover.jpg")


def test_generate_marks_the_meme_only_after_saving_it(tmp_path, monkeypatch):
    marked = []
    monkeypatch.setattr(gm, "fetch_candidates", lambda: [_m("9", votes=100, positive=95, when=NOW.isoformat())])
    monkeypatch.setattr(gm, "_used_ids", lambda: set())
    monkeypatch.setattr(gm, "mark_used", lambda m: marked.append(m["id"]))

    def failing(meme, dest):
        raise gm.MemeError("imagen rota")

    monkeypatch.setattr(gm, "download", failing)
    with pytest.raises(gm.MemeError):
        gm.generate_gaming_meme(tmp_path / "c.jpg")
    assert marked == []
    monkeypatch.setattr(gm, "download", lambda meme, dest: dest)
    result = gm.generate_gaming_meme(tmp_path / "c.jpg")
    assert marked == ["9"] and result["source_meme"].endswith("/9") and "Memedroid" in result["caption"]


def test_generate_without_candidates_fails_with_a_clear_message(tmp_path, monkeypatch):
    monkeypatch.setattr(gm, "fetch_candidates", lambda: [])
    monkeypatch.setattr(gm, "_used_ids", lambda: set())
    with pytest.raises(gm.MemeError, match="No hay memes nuevos"):
        gm.generate_gaming_meme(tmp_path / "c.jpg")


def test_mark_used_keeps_the_last_entries(monkeypatch):
    saved = {}
    monkeypatch.setattr(gm.job_store, "load", lambda name: copy.deepcopy(saved.get(name, {})))
    monkeypatch.setattr(gm.job_store, "save", lambda name, data: saved.__setitem__(name, copy.deepcopy(data)))
    for i in range(gm.USED_MAX + 5):
        gm.mark_used({"id": str(i), "link": f"l{i}"})
    ids = gm._used_ids()
    assert len(ids) == gm.USED_MAX and "0" not in ids and str(gm.USED_MAX + 4) in ids


# ---------------- lote ----------------

def test_the_type_is_an_image_post_without_youtube():
    assert "gaming_meme" in bp.GAMING_TYPES and "gaming_meme" in bp.IMAGE_POST_PROFILES
    assert "gaming_meme" not in bp.GAMING_VIDEO_TYPES


def test_create_project_uses_the_meme_eastern_slots(monkeypatch):
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(bp, "_load", lambda: {})
    monkeypatch.setattr(bp, "_save", lambda projects: None)
    monkeypatch.setattr(bp, "existing_slots", lambda *a, **k: [])
    project = bp.create_project("JUGADASEPICASVIDEOJUEGOS", 4, 2, {"facebook": {"page_id": "1"}}, {},
                                content_type="gaming_meme")
    assert project["network_offsets"] == {"instagram": -3600}
    et = {datetime.fromisoformat(v["scheduled_at"]).astimezone(ZoneInfo(bp.US_AUDIENCE_TZ)).hour
          for v in project["videos"][2:]}  # el primer dia puede adelantarse si la hora de hoy ya paso
    assert et == set(bp.GAMING_MEME_SLOT_HOURS)


def test_dispatchers_route_the_meme(monkeypatch):
    calls = []
    monkeypatch.setattr(bp, "_generate_batch_meme", lambda pid, i: calls.append(("gen", pid, i)))
    monkeypatch.setattr(bp, "_publish_batch_image_post", lambda pid, i: calls.append(("pub", pid, i)))
    bp._generate_batch_item("p", 1, "gaming_meme")
    bp._publish_batch_item("p", 1, "gaming_meme")
    assert calls == [("gen", "p", 1), ("pub", "p", 1)]


@pytest.fixture
def store(monkeypatch):
    projects = {"p1": {"id": "p1", "name": "memes", "page_name": "JUGADASEPICASVIDEOJUEGOS", "status": "running",
                       "type": "gaming_meme", "video_settings": {},
                       "videos": [{"index": 0, "status": "pending", "error": None, "stage": None}]}}
    monkeypatch.setattr(bp, "_load", lambda: copy.deepcopy(projects))
    monkeypatch.setattr(bp, "_save", lambda p: (projects.clear(), projects.update(copy.deepcopy(p))))
    return projects


def test_generate_batch_meme_fills_the_item(store, monkeypatch):
    seen = {}

    def fake(dest, index=0):
        seen["dest"] = dest
        return {"image_path": str(dest), "caption": "C", "hook": "H", "source_meme": "https://x/1"}

    monkeypatch.setattr(gm, "generate_gaming_meme", fake)
    bp._generate_batch_meme("p1", 0)
    v = store["p1"]["videos"][0]
    assert seen["dest"].name == "cover.jpg" and seen["dest"].parent.name == "gmeme_p1_000"
    assert v["status"] == "ready" and v["video_path"].endswith("cover.jpg") and v["caption"] == "C"
    assert v["source_meme"] == "https://x/1" and v["stage"] is None


def test_generate_batch_meme_failure_goes_through_the_retry_budget(store, monkeypatch):
    def boom(*a, **k):
        raise gm.MemeError("No hay memes nuevos de videojuegos")

    monkeypatch.setattr(gm, "generate_gaming_meme", boom)
    bp._generate_batch_meme("p1", 0)
    v = store["p1"]["videos"][0]
    assert v["status"] == "pending" and v["gen_attempts"] == 1 and "No hay memes" in v["error"]


# ---------------- Crear video (suelto) ----------------

def _panel_client(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    return app_module, app_module.app.test_client(), auth


def test_both_selectors_offer_the_meme(monkeypatch):
    _, client, auth = _panel_client(monkeypatch)
    html = client.get("/", headers=auth).get_data(as_text=True)
    assert html.count('<option value="gaming_meme">') == 2
    assert 'kind: "meme"' in html and "Buscando el mejor meme en Memedroid" in html


def test_start_route_creates_a_meme_job_that_publishes_as_an_image(monkeypatch):
    app_module, client, auth = _panel_client(monkeypatch)
    started = []

    class FakeThread:
        def __init__(self, target=None, args=(), **kw):
            self.target, self.args = target, args

        def start(self):
            started.append((self.target, self.args))

    monkeypatch.setattr(app_module, "_gaming_jobs", {})
    monkeypatch.setattr(app_module.job_store, "save", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "_prune_gaming_jobs", lambda: None)
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    assert client.post("/api/gaming/start", json={"kind": "meme"}, headers=auth).get_json()["ok"]
    (target, args), = started
    assert target is app_module._run_gaming_meme_generate and len(args) == 1
    (job,) = app_module._gaming_jobs.values()
    assert job["kind"] == "image" and job["profile"] == "gaming_meme" and job["stage"] == "meme"


def test_meme_runner_stores_the_result_and_reports_errors(monkeypatch):
    import app as app_module
    jobs = {"j1": {"kind": "image", "profile": "gaming_meme", "status": "generating", "networks": {}}}
    monkeypatch.setattr(app_module, "_gaming_jobs", jobs)
    monkeypatch.setattr(app_module.job_store, "save", lambda *a, **k: None)
    monkeypatch.setattr(gm, "generate_gaming_meme", lambda dest, index=0: {
        "image_path": str(dest), "caption": "C", "hook": "H", "source_meme": "https://x/1"})
    app_module._run_gaming_meme_generate("j1")
    assert jobs["j1"]["status"] == "ready" and jobs["j1"]["image_path"].endswith("cover.jpg")
    assert jobs["j1"]["source_meme"] == "https://x/1"

    def boom(*a, **k):
        raise gm.MemeError("Memedroid no respondió")

    jobs["j2"] = {"kind": "image", "profile": "gaming_meme", "status": "generating", "networks": {}}
    monkeypatch.setattr(gm, "generate_gaming_meme", boom)
    app_module._run_gaming_meme_generate("j2")
    assert jobs["j2"]["status"] == "error" and "Memedroid" in jobs["j2"]["error"]
