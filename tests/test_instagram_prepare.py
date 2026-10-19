# -*- coding: utf-8 -*-
"""Instagram: el Reel se sube/procesa con anticipacion (container FINISHED) y en el horario real solo se publica."""
import copy
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline as bp
import instagram_publisher


def _video(minutes_ahead, **extra):
    when = datetime.now() + timedelta(minutes=minutes_ahead)
    v = {"index": 0, "status": "publishing", "scheduled_at": when.isoformat(),
         "video_path": "x.mp4", "published_at": {}, "error": None}
    v.update(extra)
    return v


@pytest.fixture
def store(monkeypatch):
    projects = {"p1": {"id": "p1", "name": "n", "networks": {"instagram": {"page_id": "pg"}},
                       "network_offsets": {}, "videos": [_video(30)]}}
    monkeypatch.setattr(bp, "_load", lambda: copy.deepcopy(projects))
    monkeypatch.setattr(bp, "_save", lambda p: (projects.clear(), projects.update(copy.deepcopy(p))))
    monkeypatch.setattr(bp, "_gap_ok", lambda path: True)
    monkeypatch.setitem(sys.modules, "app", type(sys)("app"))
    app = sys.modules["app"]
    app._PUBLISHED_IG_PATH = Path("ig.json")
    return projects


def set_when(store, minutes_ahead, **extra):
    store["p1"]["videos"][0] = _video(minutes_ahead, **extra)


def run(calls, prep_ok=True):
    def prep(v, page_id):
        calls.append("prep")
        return {"ok": prep_ok, "container_id": "c1"} if prep_ok else {"ok": False, "error": "boom"}

    def pub(v, page_id):
        calls.append("pub")
        return {"ok": True, "media_id": "m1"}

    bp._publish_networks("p1", 0, {"instagram": pub}, preparers={"instagram": prep})


def test_prepares_when_inside_the_lead_window_and_does_not_publish(store):
    calls = []
    run(calls)
    assert calls == ["prep"]
    v = store["p1"]["videos"][0]
    assert v["ig_container"]["id"] == "c1" and v["status"] == "ready" and not v["published_at"]


def test_does_not_prepare_before_the_lead_window(store):
    set_when(store, 300)
    calls = []
    run(calls)
    assert calls == []


def test_does_not_prepare_twice(store):
    calls = []
    run(calls)
    store["p1"]["videos"][0]["status"] = "publishing"
    run(calls)
    assert calls == ["prep"]


def test_failed_prepare_is_remembered_and_not_retried_at_once(store):
    calls = []
    run(calls, prep_ok=False)
    v = store["p1"]["videos"][0]
    assert "ig_container" not in v and v["ig_prepare_failed_at"] and v["status"] == "ready"
    store["p1"]["videos"][0]["status"] = "publishing"
    run(calls, prep_ok=False)
    assert calls == ["prep"]


def test_publish_time_with_container_uses_the_fast_path(store, monkeypatch):
    set_when(store, -1, ig_container={"id": "c1", "prepared_at": datetime.now().isoformat()})
    seen = []
    monkeypatch.setattr(instagram_publisher, "publish_prepared",
                        lambda cid, page_id=None, on_status=None: seen.append(cid) or {"ok": True, "media_id": "m9"})
    monkeypatch.setattr(instagram_publisher, "publish_video", lambda *a, **k: pytest.fail("full publish used"))
    assert bp._publish_prepared_instagram(store["p1"]["videos"][0], "pg") == {"ok": True, "media_id": "m9"}
    assert seen == ["c1"]


def test_stale_or_old_container_falls_back_to_full_publish(store, monkeypatch):
    stale = {"id": "c1", "prepared_at": datetime.now().isoformat()}
    monkeypatch.setattr(instagram_publisher, "publish_prepared",
                        lambda *a, **k: {"ok": False, "stale": True, "error": "gone"})
    assert bp._publish_prepared_instagram({"ig_container": stale}, "pg") is None
    old = {"id": "c1", "prepared_at": (datetime.now() - timedelta(hours=30)).isoformat()}
    assert bp._publish_prepared_instagram({"ig_container": old}, "pg") is None
    assert bp._publish_prepared_instagram({}, "pg") is None


def test_real_publish_error_is_not_swallowed_by_the_fallback(store, monkeypatch):
    c = {"id": "c1", "prepared_at": datetime.now().isoformat()}
    monkeypatch.setattr(instagram_publisher, "publish_prepared",
                        lambda *a, **k: {"ok": False, "error": "permisos"})
    assert bp._publish_prepared_instagram({"ig_container": c}, "pg") == {"ok": False, "error": "permisos"}


def test_publish_prepared_reports_stale_when_container_not_finished(monkeypatch):
    monkeypatch.setattr(instagram_publisher.meta_auth, "get_credentials", lambda p: ("pg", "tok", None))
    monkeypatch.setattr(instagram_publisher.meta_auth, "validate", lambda p: {"ok": True})

    class R:
        ok = True
        status_code = 200

        def json(self):
            return {"status_code": "EXPIRED"}

    monkeypatch.setattr(instagram_publisher, "_request_with_retry", lambda *a, **k: R())
    res = instagram_publisher.publish_prepared("c1", "pg")
    assert res["ok"] is False and res["stale"] is True
