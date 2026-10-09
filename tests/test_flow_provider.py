# -*- coding: utf-8 -*-
"""Google Flow como alternativa a WhatsApp para crear imagenes. Sin navegador ni red."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import auto_pipeline as ap
import batch_pipeline as bp
import flow_images as flow


def test_flow_is_an_image_provider():
    assert ap.PROVIDERS == ("whatsapp", "flow")


def test_generate_clips_with_flow_uses_flow_and_returns_scene_jpgs(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(flow, "generate_scene_images",
                        lambda story, d, on_progress=None, aspect="9:16": calls.append((len(story["prompts"]), d, aspect)) or [])
    monkeypatch.setattr(ap, "_generate_clips_whatsapp", lambda *a, **k: pytest.fail("no debe usar WhatsApp"))
    story = {"prompts": [{"index": 1, "frase": "a", "prompt": "p1"}, {"index": 2, "frase": "b", "prompt": "p2"}]}
    out = ap.generate_clips(story, tmp_path, unattended=True, provider="flow", generate_video=True)
    assert out == [str(tmp_path / "scene_000.jpg"), str(tmp_path / "scene_001.jpg")]
    assert calls == [(2, tmp_path, "9:16")]  # las escenas de un video van verticales


def test_flow_errors_become_pipeline_errors_that_the_batch_retries(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise flow.FlowError("Flow no devolvió imagen en 240s.")
    monkeypatch.setattr(flow, "generate_scene_images", boom)
    with pytest.raises(ap.PipelineError, match="Flow no devolvió"):
        ap.generate_clips({"prompts": [{"index": 1, "frase": "a", "prompt": "p"}]}, tmp_path, True, provider="flow")
    assert bp._is_healable_error("tras 3 intento(s): Flow no devolvió imagen en 240s.")
    assert not bp._is_healable_error("Flow pide iniciar sesión: entrá a mano en la ventana de Brave de Flow.")


def test_post_cover_uses_the_chosen_provider(monkeypatch, tmp_path):
    import app
    seen = []

    def fake_generate_clips(story, dest, unattended, start_index=0, on_progress=None, provider="whatsapp", generate_video=True,
                            aspect="9:16"):
        seen.append((provider, aspect))
        p = Path(dest) / "scene_000.jpg"
        p.write_bytes(b"x")
        return [str(p)]

    monkeypatch.setattr(ap, "generate_clips", fake_generate_clips)
    monkeypatch.setattr(bp, "_generate_text_with_chain", lambda *a, **k: (
        "IDEA: i\nHOOK: h\nIMAGE_PROMPT: p\nTOP: T\nBOTTOM: b\nCAPTION: c"))
    monkeypatch.setattr(bp.gaming_news, "next_news", lambda: {
        "title": "t", "link": "l", "source": "s", "summary": "", "published": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)})
    monkeypatch.setattr(bp.gaming_news, "mark_used", lambda item: None)
    from PIL import Image

    def fake_fit(base, out, size):
        Image.new("RGB", (10, 10)).save(out)
        return out
    monkeypatch.setattr(bp.meme_maker, "fit_post", fake_fit)
    for provider in ("flow", "whatsapp"):
        bp.generate_gaming_post("Jugadas", "t", provider, tmp_path / provider, "T1")
    assert seen == [("flow", "1:1"), ("whatsapp", "1:1")]  # los posts de imagen son cuadrados


def test_status_when_the_flow_browser_is_closed(monkeypatch):
    monkeypatch.setattr(flow, "browser_up", lambda: False)
    assert flow.status()["state"] == "no_browser"
    with pytest.raises(flow.FlowError, match="cerrado"):
        flow.ensure_connected()


def _fake_browser(monkeypatch, pages, store, browser="brave"):
    """Brave de mentira: lista de pestañas, /json/new, /json/activate y agent-browser grabados."""
    log = []

    def cdp(path, method="GET"):
        log.append((method, path))
        if path == "/json":
            return pages
        if path.startswith("/json/new"):
            tab = {"id": "mine", "type": "page", "url": "https://flow.google.com/", "webSocketDebuggerUrl": "ws://x/mine"}
            pages.insert(0, tab)
            return tab
        return ""

    monkeypatch.setattr(flow, "_cdp", cdp)
    monkeypatch.setattr(flow, "browser_up", lambda: True)
    monkeypatch.setattr(flow.job_store, "load", lambda name: dict(store.get(name, {})))
    monkeypatch.setattr(flow.job_store, "save", lambda name, data: store.__setitem__(name, dict(data)))
    monkeypatch.setattr(flow, "_ab", lambda args, timeout=90: log.append(("ab", list(args))) or "https://flow.google.com/")
    monkeypatch.setattr(flow, "_eval", lambda js, timeout=90: False)
    monkeypatch.setattr(flow, "_attached_tab", None)
    monkeypatch.setattr(flow, "BROWSER", flow.BROWSERS[browser])
    return log


def test_other_flow_project_is_left_alone_and_own_tab_is_opened(monkeypatch):
    other = {"id": "other", "type": "page", "url": "https://flow.google.com/project/abc", "webSocketDebuggerUrl": "ws://x/other"}
    store = {}
    log = _fake_browser(monkeypatch, [other], store)
    assert "otro proyecto de Flow" in flow.status()["message"]
    prev = flow._begin_job()
    assert prev == "other"  # el foco vuelve a la pestaña del otro proyecto
    assert ("PUT", "/json/new?https://flow.google.com/") in log
    assert ("ab", ["connect", "ws://x/mine"]) in log  # conectada a SU pestaña, nunca a la del otro proyecto
    assert all(args != ["connect", flow.CDP_PORT] for kind, args in log if kind == "ab")
    assert store["flow_settings"]["tab_id"] == "mine" and flow.SESSION != "flowcdp"
    flow._end_job(prev)
    assert log[-1] == ("GET", "/json/activate/other")


def test_own_tab_is_reused_next_time(monkeypatch):
    mine = {"id": "mine", "type": "page", "url": "https://flow.google.com/project/p", "webSocketDebuggerUrl": "ws://x/mine"}
    log = _fake_browser(monkeypatch, [mine], {"flow_settings": {"tab_id": "mine"}})
    flow.ensure_connected()
    assert not any(kind == "PUT" for kind, _ in log)  # no abre otra pestaña


def test_edge_is_the_default_dedicated_browser_on_its_own_port():
    edge, brave = flow.BROWSERS["edge"], flow.BROWSERS["brave"]
    assert edge["dedicated"] and edge["port"] == 9334 and not brave["dedicated"] and brave["port"] == 9333
    assert edge["port"] != brave["port"] and edge["profile"] != brave["profile"]
    assert "msedge.exe" in edge["exe"]


def test_launch_uses_its_own_profile_and_debug_port(monkeypatch, tmp_path):
    exe = tmp_path / "msedge.exe"
    exe.write_text("")
    edge = {**flow.BROWSERS["edge"], "exe": str(exe), "profile": tmp_path / "profile"}
    monkeypatch.setattr(flow, "BROWSER", edge)
    monkeypatch.setattr(flow, "CDP_PORT", 9334)
    monkeypatch.setattr(flow, "browser_up", lambda: False)
    launched = []
    monkeypatch.setattr(flow.subprocess, "Popen", lambda cmd, **k: launched.append(cmd))
    assert flow.launch_browser() is True
    cmd = launched[0]
    assert cmd[0] == str(exe) and "--remote-debugging-port=9334" in cmd and f"--user-data-dir={tmp_path / 'profile'}" in cmd
    assert (tmp_path / "profile").is_dir() and cmd[-1] == flow.FLOW_HOME


def test_dedicated_browser_adopts_its_open_flow_tab_instead_of_opening_another(monkeypatch):
    tab = {"id": "t1", "type": "page", "url": "https://flow.google.com/", "webSocketDebuggerUrl": "ws://x/t1"}
    store = {}
    log = _fake_browser(monkeypatch, [tab], store, browser="edge")
    flow.ensure_connected()
    assert not any(kind == "PUT" for kind, _ in log) and store["flow_settings"]["tab_id"] == "t1"
    assert ("ab", ["connect", "ws://x/t1"]) in log


def test_configure_generation_sets_image_vertical_x1_and_verifies(monkeypatch):
    state = {"open": False, "on": {"Imagen": False, "9:16": False, "x1": True}, "clicks": []}

    def fake_eval(js, timeout=90):
        if "settings-trigger-button')" in js and js.startswith("!!"):
            return True
        if "mat-button-toggle').length" in js:
            return state["open"]
        for label in state["on"]:
            if f'"{label}"' in js:
                if state["on"][label]:
                    return "on"
                state["on"][label] = True
                state["clicks"].append(label)
                return "clicked"
        if "innerText" in js:  # texto de la barra tras configurar
            return "Nano Banana 2.1 crop_9_16 x1" if all(state["on"].values()) else "crop_16_9"
        return None

    def fake_ab(args, timeout=90):
        if args[0] == "click":
            state["open"] = True
        return ""

    monkeypatch.setattr(flow, "_eval", fake_eval)
    monkeypatch.setattr(flow, "_ab", fake_ab)
    monkeypatch.setattr(flow.time, "sleep", lambda s: None)
    flow.configure_generation()
    assert state["clicks"] == ["Imagen", "9:16"]  # x1 ya estaba activo: no se toca


def test_configure_generation_fails_loudly_if_flow_stays_horizontal(monkeypatch):
    monkeypatch.setattr(flow, "_ab", lambda *a, **k: "")
    monkeypatch.setattr(flow.time, "sleep", lambda s: None)
    monkeypatch.setattr(flow, "_eval", lambda js, timeout=90: (
        True if ("length > 0" in js or js.startswith("!!")) else "on" if "mat-button-toggle" in js else "crop_16_9 x1"))
    with pytest.raises(flow.FlowError, match="vertical 9:16"):
        flow.configure_generation()


def test_ab_does_not_hang_when_the_command_leaves_a_daemon_holding_its_output(monkeypatch):
    """agent-browser arranca un daemon que hereda la salida: con pipes, subprocess.run no volvia nunca."""
    import sys
    import time
    code = ("import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)']); print('listo')")
    # _ab antepone ["--session", SESSION]: un lanzador minimo que ignora esos dos argumentos y corre `code`
    launcher = Path(__file__).parent / "_launcher_tmp.py"
    launcher.write_text("import sys\nexec(sys.argv[-1])\n", encoding="utf-8")
    try:
        monkeypatch.setattr(flow, "AGENT_BROWSER", sys.executable)
        monkeypatch.setattr(flow, "SESSION", str(launcher))
        # cmd = [python, "--session", launcher, code] no es valido para python: se arma el comando a mano
        monkeypatch.setattr(flow.subprocess, "run", (lambda real: lambda cmd, **kw: real(
            [sys.executable, str(launcher), code], **kw))(flow.subprocess.run))
        t = time.time()
        assert flow._ab(["x"], timeout=10).strip() == "listo"
        assert time.time() - t < 8
    finally:
        launcher.unlink(missing_ok=True)


def test_configure_generation_can_set_square_for_posts(monkeypatch):
    monkeypatch.setattr(flow, "_aspect", "9:16")  # se restaura al terminar el test
    on = {"Imagen": True, "1:1": False, "x1": True}
    clicked = []

    def fake_eval(js, timeout=90):
        if js.startswith("!!"):
            return True
        if "mat-button-toggle').length" in js:
            return True
        for label in on:
            if f'"{label}"' in js:
                if on[label]:
                    return "on"
                on[label] = True
                clicked.append(label)
                return "clicked"
        return "Nano Banana 2.1 crop_square x1"

    monkeypatch.setattr(flow, "_eval", fake_eval)
    monkeypatch.setattr(flow, "_ab", lambda *a, **k: "")
    monkeypatch.setattr(flow.time, "sleep", lambda s: None)
    flow.configure_generation("1:1")
    assert clicked == ["1:1"] and flow._format_words() == "cuadrado 1:1"
    with pytest.raises(flow.FlowError, match="desconocido"):
        flow.configure_generation("2:1")
