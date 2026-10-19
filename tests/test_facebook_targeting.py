# -*- coding: utf-8 -*-
"""Audiencia preferida de Facebook (feed_targeting): apagada por defecto, con reintento sin ella si Meta la rechaza."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import facebook_publisher as fb


class Resp:
    def __init__(self, payload, ok=True):
        self._payload, self.ok, self.status_code = payload, ok, 200 if ok else 400

    def json(self):
        return self._payload


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.delenv("FB_FEED_TARGET_COUNTRIES", raising=False)
    monkeypatch.setattr(fb.meta_auth, "get_credentials", lambda page_id: ("pg", "tok", None))
    monkeypatch.setattr(fb.meta_auth, "validate", lambda page_id: {"ok": True})
    monkeypatch.setattr(fb, "_save_next_slot", lambda ts: None)
    calls = []
    plan = {"reject_targeting": False, "auth_error": False}

    def post(url, **kwargs):
        data = dict(kwargs.get("data") or {})
        calls.append(data)
        phase = data.get("upload_phase")
        if phase == "start":
            return Resp({"upload_session_id": "s", "video_id": "v1", "start_offset": "0", "end_offset": "0"})
        if "feed_targeting" in data and plan["reject_targeting"]:
            return Resp({"error": {"message": "Invalid parameter feed_targeting", "code": 100}}, ok=False)
        return Resp({"success": True, "id": "p1", "post_id": "p1"})

    monkeypatch.setattr(fb, "_post_with_retry", post)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 10)
    image = tmp_path / "i.jpg"
    image.write_bytes(b"x" * 10)
    return {"calls": calls, "plan": plan, "video": str(video), "image": str(image)}


def last_targeting(calls):
    return [c.get("feed_targeting") for c in calls if c.get("upload_phase") in ("finish", None)]


def test_targeting_is_off_by_default():
    assert fb.feed_targeting_fields() == {}


def test_targeting_reads_countries_from_the_env(monkeypatch):
    monkeypatch.setenv("FB_FEED_TARGET_COUNTRIES", " us , ca ")
    assert json.loads(fb.feed_targeting_fields()["feed_targeting"]) == {"geo_locations": {"countries": ["US", "CA"]}}


def test_video_and_photo_do_not_send_targeting_when_off(api):
    assert fb.publish_video(api["video"], "t", "d")["ok"]
    assert fb.publish_photo(api["image"], "c")["ok"]
    assert all("feed_targeting" not in c for c in api["calls"])


def test_video_finish_sends_targeting_when_on(api, monkeypatch):
    monkeypatch.setenv("FB_FEED_TARGET_COUNTRIES", "US")
    assert fb.publish_video(api["video"], "t", "d")["ok"]
    (sent,) = [c for c in api["calls"] if c.get("upload_phase") == "finish"]
    assert json.loads(sent["feed_targeting"])["geo_locations"]["countries"] == ["US"]


def test_photo_sends_targeting_when_on(api, monkeypatch):
    monkeypatch.setenv("FB_FEED_TARGET_COUNTRIES", "US")
    assert fb.publish_photo(api["image"], "c")["ok"]
    assert "feed_targeting" in api["calls"][-1]


def test_video_retries_without_targeting_when_meta_rejects_it(api, monkeypatch):
    monkeypatch.setenv("FB_FEED_TARGET_COUNTRIES", "US")
    api["plan"]["reject_targeting"] = True
    result = fb.publish_video(api["video"], "t", "d")
    finishes = [c for c in api["calls"] if c.get("upload_phase") == "finish"]
    assert result["ok"] and len(finishes) == 2
    assert "feed_targeting" in finishes[0] and "feed_targeting" not in finishes[1]


def test_photo_retries_without_targeting_when_meta_rejects_it(api, monkeypatch):
    monkeypatch.setenv("FB_FEED_TARGET_COUNTRIES", "US")
    api["plan"]["reject_targeting"] = True
    result = fb.publish_photo(api["image"], "c")
    assert result["ok"] and len(api["calls"]) == 2
    assert "feed_targeting" in api["calls"][0] and "feed_targeting" not in api["calls"][1]


def test_a_real_failure_is_still_reported_after_the_retry(api, monkeypatch):
    monkeypatch.setenv("FB_FEED_TARGET_COUNTRIES", "US")
    monkeypatch.setattr(fb, "_post_with_retry",
                        lambda url, **k: Resp({"error": {"message": "boom", "code": 1}}, ok=False))
    assert fb.publish_photo(api["image"], "c")["ok"] is False
