"""Lotes mixtos (video / post alternados) y lotes simples: tipo de cada item."""
import batch_pipeline as bp


# ── Lotes mixtos (video / post alternados) ──────────────────────────────────

def _create_mixed(monkeypatch, tmp_path, content_type, total):
    monkeypatch.setattr(bp, "_load", lambda: {})
    monkeypatch.setattr(bp, "_save", lambda projects: None)
    monkeypatch.setattr(bp, "existing_slots", lambda *a, **k: [])
    monkeypatch.setattr(bp.gaming_clip.gaming_vision, "is_configured", lambda: True)
    return bp.create_project(
        "Niño Selectivo, Familia en Paz", total, 2, {"facebook": {"page_id": "1"}}, {}, content_type=content_type,
    )


def test_mixed_batch_alternates_video_and_post(monkeypatch, tmp_path):
    for total, videos, posts in ((10, 5, 5), (11, 6, 5), (1, 1, 0)):
        project = _create_mixed(monkeypatch, tmp_path, "mix_ninio", total)
        kinds = [bp.item_type(project, v) for v in project["videos"]]
        assert kinds == ["video", "ninio_image"] * (total // 2) + (["video"] if total % 2 else [])
        assert (kinds.count("video"), kinds.count("ninio_image")) == (videos, posts)
        assert project["type"] == "mix_ninio"


def test_mixed_gaming_uses_clip_and_gaming_post(monkeypatch, tmp_path):
    project = _create_mixed(monkeypatch, tmp_path, "mix_gaming", 4)
    assert [bp.item_type(project, v) for v in project["videos"]] == ["gaming_clip", "gaming_image"] * 2
    assert project["network_offsets"] == bp.GAMING_NETWORK_OFFSETS


def test_plain_batches_keep_their_type():
    assert bp.item_type({"type": "gaming_clip"}, {}) == "gaming_clip"
    assert bp.item_type({}, {}) == "video"
