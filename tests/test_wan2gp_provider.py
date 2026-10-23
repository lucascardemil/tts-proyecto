# -*- coding: utf-8 -*-
"""WanGP local como proveedor de imagenes: cola, reconocimiento de escenas por semilla y errores."""
import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

import auto_pipeline as ap
import batch_pipeline as bp
import wan2gp_images as w


def test_it_is_registered_as_an_image_provider():
    assert "wan2gp" in ap.PROVIDERS


def test_queue_has_one_task_per_scene_with_its_own_seed_and_the_aspect_size():
    queue = w.build_queue([(0, "a"), (3, "b")], "9:16", model="m")
    assert [t["seed"] for t in queue] == [w.SEED_BASE, w.SEED_BASE + 3]
    assert all(t["resolution"] == "576x1024" and t["model_type"] == "m" for t in queue)
    assert w.build_queue([(0, "a")], "1:1", model="m")[0]["resolution"] == "1024x1024"


def test_outputs_are_matched_to_scenes_by_the_seed_in_the_file_name(tmp_path):
    for name in ("2026-10-22-18h57m26s_seed1002_Vertical 916.jpg", "2026-10-22-18h57m16s_seed1000_Vertical 916.jpg",
                 "otro_seed5_x.jpg", "sin_semilla.jpg"):
        (tmp_path / name).write_bytes(b"x")
    assert sorted(w.collect_outputs(tmp_path)) == [0, 2]


def _fake_install(monkeypatch, tmp_path):
    (tmp_path / "inst" / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / "inst" / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    (tmp_path / "inst" / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / "inst" / ".venv" / "bin" / "python").write_bytes(b"")
    (tmp_path / "inst" / "wgp.py").write_text("")
    monkeypatch.setenv("WAN2GP_DIR", str(tmp_path / "inst"))


def _story(n):
    return {"prompts": [{"index": i + 1, "frase": "f", "prompt": f"p{i}"} for i in range(n)]}


def test_generate_scene_images_runs_one_queue_and_writes_scene_jpgs(monkeypatch, tmp_path):
    _fake_install(monkeypatch, tmp_path)
    runs = []

    def fake_run(cmd, cwd=None, capture_output=None, timeout=None):
        queue = json.loads(Path(cmd[3]).read_text(encoding="utf-8"))
        out = Path(cmd[5])
        out.mkdir(parents=True)
        for task in queue:
            Image.new("RGB", (576, 1024), (task["seed"] % 255, 10, 10)).save(out / f"x_seed{task['seed']}_p.jpg")
        runs.append(len(queue))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    images = tmp_path / "imgs"
    images.mkdir()
    Image.new("RGB", (576, 1024)).save(images / "scene_001.jpg", quality=100)  # ya existe y se conserva
    (images / "scene_001.jpg").write_bytes(b"x" * 20_000)
    created = w.generate_scene_images(_story(3), images, aspect="9:16")
    assert runs == [2]  # una sola corrida, solo con las escenas que faltaban
    assert sorted(p.name for p in created) == ["scene_000.jpg", "scene_002.jpg"]
    assert (images / "scene_001.jpg").read_bytes() == b"x" * 20_000
    (images / "scene_000.jpg").write_bytes(b"x" * 20_000)  # (las imagenes planas del test pesan poco)
    assert w.generate_scene_images(_story(2), images) == []  # nada que hacer: no lanza WanGP


def test_missing_images_raise_a_healable_error(monkeypatch, tmp_path):
    _fake_install(monkeypatch, tmp_path)
    monkeypatch.setattr(w.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"CUDA out of memory"))
    with pytest.raises(w.Wan2gpError, match="0 de 2") as err:
        w.generate_scene_images(_story(2), tmp_path / "i")
    assert bp._is_healable_error(str(err.value))


def test_not_installed_is_a_permanent_error(monkeypatch, tmp_path):
    monkeypatch.setenv("WAN2GP_DIR", str(tmp_path / "nada"))
    assert w.status()["state"] == "not_installed"
    with pytest.raises(w.Wan2gpError) as err:
        w.generate_scene_images(_story(1), tmp_path / "i")
    assert not bp._is_healable_error(str(err.value))


def test_generate_clips_with_wan2gp_returns_scene_jpgs_and_wraps_errors(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(w, "generate_scene_images", lambda story, d, on_progress=None, aspect="9:16": seen.append(aspect) or [])
    monkeypatch.setattr(ap, "_generate_clips_whatsapp", lambda *a, **k: pytest.fail("no debe usar WhatsApp"))
    out = ap.generate_clips(_story(2), tmp_path, unattended=True, provider="wan2gp", aspect="1:1")
    assert out == [str(tmp_path / "scene_000.jpg"), str(tmp_path / "scene_001.jpg")] and seen == ["1:1"]

    def boom(*a, **k):
        raise w.Wan2gpError("WanGP generó 0 de 2 imágenes")

    monkeypatch.setattr(w, "generate_scene_images", boom)
    with pytest.raises(ap.PipelineError, match="WanGP generó"):
        ap.generate_clips(_story(2), tmp_path, True, provider="wan2gp")


def test_selectors_offer_wan2gp_and_settings_show_its_status(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    client = app_module.app.test_client()
    html = client.get("/", headers=auth).get_data(as_text=True)
    assert html.count('<option value="wan2gp">') == 4 and "checkWan2gpSettings();" in html
    assert client.get("/api/wan2gp/status", headers=auth).get_json()["ok"]


def test_revisor_panel_has_a_retry_all_button(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    html = app_module.app.test_client().get("/", headers=auth).get_data(as_text=True)
    assert "Reintentar todo (" in html and "officeRunAll" in html
