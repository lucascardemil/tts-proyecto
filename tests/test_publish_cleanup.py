# -*- coding: utf-8 -*-
"""Un item publicado/programado en todas sus redes borra sus archivos; el texto y los horarios se quedan. Las fotos van solo a Facebook."""
import copy
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline as bp


@pytest.fixture
def item(tmp_path, monkeypatch):
    """Item "publishing" de un lote de macrame con sus archivos reales en un VIDEO_PUBLIC_DIR temporal."""
    public = tmp_path / "public"
    folder = public / "batch_img_p1_000"
    folder.mkdir(parents=True)
    cover = folder / "cover.jpg"
    cover.write_bytes(b"jpg")
    (folder / "slide.jpg").write_bytes(b"jpg")
    audio = tmp_path / "voz.mp3"
    audio.write_bytes(b"mp3")
    monkeypatch.setattr(bp.video_maker, "VIDEO_PUBLIC_DIR", public)
    monkeypatch.setattr(bp.auto_pipeline, "SCRATCH_DIR", tmp_path / "scratch")
    monkeypatch.setattr(bp.job_store, "load", lambda name: {})
    monkeypatch.setattr(bp.job_store, "save", lambda name, data: None)
    video = {"index": 0, "status": "publishing", "scheduled_at": (datetime.now() + timedelta(days=1)).isoformat(),
             "video_path": str(cover), "audio_path": str(audio), "published_at": {}, "error": None,
             "caption": "Texto del post #A #B #C", "item_type": "macrame_image"}
    projects = {"p1": {"id": "p1", "name": "n", "page_name": "Macrame", "status": "running", "type": "macrame_image",
                       "networks": {"facebook": {"page_id": "1"}}, "network_offsets": {}, "videos": [video]}}
    monkeypatch.setattr(bp, "_load", lambda: copy.deepcopy(projects))
    monkeypatch.setattr(bp, "_save", lambda p: (projects.clear(), projects.update(copy.deepcopy(p))))
    monkeypatch.setattr(bp, "_gap_ok", lambda path: True)
    monkeypatch.setattr(bp, "get_project", lambda pid: bp._load()[pid])
    app = type(sys)("app")
    app._record_published_facebook = lambda *a, **k: None
    app._PUBLISHED_FB_PATH = Path("fb.json")
    monkeypatch.setitem(sys.modules, "app", app)
    return projects, folder, audio


def _fb(monkeypatch, ok):
    monkeypatch.setattr(bp.facebook_publisher, "publish_photo",
                        lambda *a, **k: {"ok": True, "post_id": "9"} if ok else {"ok": False, "error": "x"})


def test_published_item_loses_its_files_but_keeps_its_text(item, monkeypatch):
    projects, folder, audio = item
    _fb(monkeypatch, True)
    bp._publish_batch_image_post("p1", 0)
    v = projects["p1"]["videos"][0]
    assert v["status"] == "published" and v["assets_purged"] is True
    assert not folder.exists() and not audio.exists()
    assert v["caption"] == "Texto del post #A #B #C" and v["scheduled_at"]


def test_failed_publish_keeps_every_file(item, monkeypatch):
    projects, folder, audio = item
    _fb(monkeypatch, False)
    bp._publish_batch_image_post("p1", 0)
    assert projects["p1"]["videos"][0]["status"] != "published"
    assert folder.exists() and audio.exists() and not projects["p1"]["videos"][0].get("assets_purged")


def test_a_failing_delete_never_reverts_the_published_state(item, monkeypatch):
    projects, folder, _ = item
    _fb(monkeypatch, True)
    monkeypatch.setattr(bp, "_purge_video_assets", lambda pid, v: (_ for _ in ()).throw(OSError("bloqueado")))
    bp._publish_batch_image_post("p1", 0)
    v = projects["p1"]["videos"][0]
    assert v["status"] == "published" and not v.get("assets_purged") and folder.exists()


def test_photo_batches_drop_instagram_but_mixed_batches_with_clips_keep_it(monkeypatch):
    assert bp._photo_post("macrame_image") and bp._photo_post("mix_everyday") and bp._photo_post("gaming_meme")
    assert not bp._photo_post("video") and not bp._photo_post("gaming_clip") and not bp._photo_post("mix_gaming")
