# -*- coding: utf-8 -*-
"""Temas Fallout 3 / Fallout 4 y la oficina: Vault 3D (three.js) con respaldo SVG."""
import base64
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

STATIC = ROOT / "static"
THEMES = ("fallout3", "fallout4")
AGENTS = ("videos", "publica", "metricas", "revisor")


def _auth():
    return {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}


def _client(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    return app_module.app.test_client()


def _html(monkeypatch):
    client = _client(monkeypatch)
    response = client.get("/", headers=_auth())
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _blocks(name):
    import app as app_module
    return re.findall(rf"(?:/\*|<!--) {name}:START (?:\*/|-->)(.*?)(?:/\*|<!--) {name}:END (?:\*/|-->)",
                      app_module.HTML, flags=re.S)


def _luminance(hex_color):
    h = hex_color.lstrip("#")
    chans = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_theme_blocks_are_jinja_safe_and_do_not_use_innerhtml():
    blocks = _blocks("THEME")
    assert len(blocks) >= 3, "faltan los bloques THEME de CSS, HTML y JS"
    for block in blocks:
        assert "{{" not in block and "{%" not in block and "{#" not in block and '"""' not in block
        assert "innerHTML" not in block


def test_theme_is_wired_in_the_page(monkeypatch):
    html = _html(monkeypatch)
    head = html.split("</head>")[0]
    assert 'localStorage.getItem("uiTheme")' in head and 'setAttribute("data-theme"' in head  # sin parpadeo
    # un valor guardado desconocido (el antiguo "matrix") cae a Fallout 3
    assert '"classic" || t === "fallout4" ? t : "fallout3"' in head
    for theme in THEMES:
        assert f'html[data-theme="{theme}"]' in html
    assert 'id="crt-fx"' in html and 'id="theme-toggle"' in html
    assert "matrix" not in "".join(_blocks("THEME")).lower()
    for name in ("themeCurrent", "themeApply"):
        assert f"function {name}" in html
    assert "prefers-reduced-motion" in "".join(_blocks("THEME"))
    css = "".join(b for b in _blocks("THEME") if "data-theme" in b)
    assert re.search(r"--error:\s*#f87171", css)


def test_theme_tokens_meet_the_contrast_floor(monkeypatch):
    _html(monkeypatch)
    css = "".join(b for b in _blocks("THEME") if "data-theme" in b)
    for theme in THEMES:
        body = re.search(r'html\[data-theme="%s"\]\s*\{([^{}]*)\}' % theme, css).group(1)
        tokens = dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", body))
        for surface in ("--bg-main", "--surface-1", "--surface-2", "--surface-3"):
            assert _contrast(tokens["--text-primary"], tokens[surface]) >= 7, (theme, surface)
            assert _contrast(tokens["--text-secondary"], tokens[surface]) >= 4.5, (theme, surface)
            assert _contrast(tokens["--text-muted"], tokens[surface]) >= 4.5, (theme, surface)
        assert _contrast(tokens["--on-accent"], tokens["--red-primary"]) >= 4.5, theme  # texto sobre botones/acentos


def test_vendored_three_is_served_behind_auth_as_javascript(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/static/vendor/three.module.min.js").status_code == 401
    ok = client.get("/static/vendor/three.module.min.js", headers=_auth())
    assert ok.status_code == 200
    assert ok.mimetype in ("text/javascript", "application/javascript")
    assert len(ok.data) > 100_000


def test_vendored_three_relative_imports_exist_and_readme_pins_the_version():
    text = (STATIC / "vendor" / "three.module.min.js").read_text(encoding="utf-8")
    rels = re.findall(r'from\s*"(\./[^"]+)"', text)
    assert rels, "no se encontraron imports relativos en three.module.min.js"
    for rel in rels:
        assert (STATIC / "vendor" / rel).exists(), f"falta {rel} junto a three.module.min.js"
    readme = (STATIC / "vendor" / "README.md").read_text(encoding="utf-8")
    assert re.search(r"three(\.js)?\s*@?\s*r?\d+", readme, flags=re.I) and "MIT" in readme


def test_vault_modules_are_served_behind_auth(monkeypatch):
    client = _client(monkeypatch)
    for name in ("vault3d.js", "vault3d-logic.js"):
        assert client.get(f"/static/{name}").status_code == 401
        ok = client.get(f"/static/{name}", headers=_auth())
        assert ok.status_code == 200 and ok.mimetype in ("text/javascript", "application/javascript")


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")


def _node(js: str):
    out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _logic_import():
    return f'import * as L from "{(STATIC / "vault3d-logic.js").as_uri()}";' + """
      let seed = 12345;
      const rng = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
      const agent = (id, lane) => ({ id, kind: "agent", home: id, lane: lane || 0 });
      const run = (w, secs) => { for (let i = 0; i < secs * 20; i++) L.stepWorld(w, 0.05); };"""


@needs_node
def test_find_path_uses_the_elevator_between_floors_and_stays_minimal():
    out = _node(_logic_import() + """
      console.log(JSON.stringify({
        cross: L.findPath("videos:a", "metricas:a"),
        same: L.findPath("videos:a", "publica:b"),
        room: L.findPath("videos:a", "videos:b"),
        self: L.findPath("revisor:a", "revisor:a") }));""")
    assert out["cross"] == ["videos:a", "videos:door", "E1", "E0", "metricas:door", "metricas:a"]
    assert out["same"] == ["videos:a", "videos:door", "E1", "publica:door", "publica:b"]
    assert out["room"] == ["videos:a", "videos:b"] and out["self"] == ["revisor:a"]


@needs_node
def test_every_node_is_reachable_from_every_other():
    out = _node(_logic_import() + """
      const ids = Object.keys(L.NODES);
      console.log(JSON.stringify(ids.every(a => ids.every(b => L.findPath(a, b) !== null))));""")
    assert out is True


@needs_node
def test_agent_behavior_follows_the_status():
    out = _node(_logic_import() + """
      const res = {};
      for (const [status, secs] of [["busy", 40], ["warn", 40], ["error", 5], ["ok", 10]]) {
        const w = L.createWorld([agent("videos", 0.5), agent("metricas", 0.6)], rng);
        const d = w.dwellers[0];
        // el agente arranca en otra sala para que tenga que viajar (usa el ascensor)
        d.at = "revisor:a"; d.pos = L.nodePos("revisor:a", 0.5);
        L.setStatus(w, d, status);
        run(w, secs);
        res[status] = { activity: d.activity, anim: d.anim, at: d.at, x: d.pos.x, y: d.pos.y, emote: d.emote };
      }
      console.log(JSON.stringify(res));""")
    work = out["busy"]
    assert work["activity"] == "WORK" and work["anim"] == "work" and work["at"] == "videos:work"
    assert work["y"] == 5.6 and abs(work["x"] - (-5.4)) < 1e-6
    assert out["warn"]["activity"] == "ALERT" and out["warn"]["emote"] == "!"
    assert out["error"]["activity"] == "PANIC" and out["error"]["emote"] == "!!"
    assert out["ok"]["activity"] in ("IDLE", "TRAVEL", "CHAT")


@needs_node
def test_extras_never_work_and_two_dwellers_can_chat_and_then_stop():
    out = _node(_logic_import() + """
      const defs = [{ id: "e1", kind: "extra", home: "videos", lane: 0.1, node: "videos:a" },
                    { id: "e2", kind: "extra", home: "videos", lane: 0.3, node: "videos:a" }];
      const w = L.createWorld(defs, rng);
      w.dwellers.forEach(d => { d.t = 1e9; d.cool = 0; });
      const seen = new Set();
      let chatted = false;
      for (let i = 0; i < 2400 && !chatted; i++) {
        L.stepWorld(w, 0.05);
        w.dwellers.forEach(d => seen.add(d.activity));
        chatted = w.dwellers.every(d => d.activity === "CHAT");
      }
      const emotes = w.dwellers.map(d => d.emote);
      run(w, 12);
      console.log(JSON.stringify({ chatted, seen: [...seen], emotes, after: w.dwellers.map(d => d.activity) }));""")
    assert out["chatted"] is True and "WORK" not in out["seen"] and "PANIC" not in out["seen"]
    assert any(out["emotes"])  # alguien dice algo
    assert all(a != "CHAT" for a in out["after"])  # la charla termina


@needs_node
def test_poke_makes_the_dweller_wave_and_then_go_back_to_what_it_was_doing():
    out = _node(_logic_import() + """
      const w = L.createWorld([agent("publica", 0.5)], rng);
      const d = w.dwellers[0];
      L.setStatus(w, d, "busy"); run(w, 20);
      L.pokeDweller(d); L.stepWorld(w, 0.05);
      const waving = d.anim;
      run(w, 3);
      console.log(JSON.stringify({ waving, after: d.activity }));""")
    assert out == {"waving": "wave", "after": "WORK"}


@needs_node
def test_only_one_dweller_rides_the_elevator_at_a_time_and_nobody_gets_stuck():
    out = _node(_logic_import() + """
      const w = L.createWorld([agent("videos", 0.1), agent("publica", 0.4), agent("metricas", 0.7)], rng);
      const start = ["revisor:a", "metricas:a", "publica:a"];
      w.dwellers.forEach((d, i) => { d.at = start[i]; d.pos = L.nodePos(d.at, d.lane); });
      w.dwellers.forEach(d => L.setStatus(w, d, "busy"));
      let maxRiders = 0;
      for (let i = 0; i < 1600; i++) {
        L.stepWorld(w, 0.05);
        maxRiders = Math.max(maxRiders, w.dwellers.filter(d => d.ride === "riding").length);
      }
      console.log(JSON.stringify({ maxRiders, done: w.dwellers.map(d => d.activity), owner: w.elev.owner }));""")
    assert out["maxRiders"] <= 1
    assert out["done"] == ["WORK", "WORK", "WORK"] and out["owner"] is None


@needs_node
def test_camera_fit_orbit_and_zoom_clamps():
    out = _node(_logic_import() + """
      const d = a => L.fitCamera(a).distance;
      const a = L.clampOrbit(10, -5), b = L.clampOrbit(-10, 10), c = L.clampOrbit(0.1, 1.5);
      console.log(JSON.stringify({ wide: d(1.78), square: d(1), narrow: d(0.5), zero: d(0), huge: d(50),
        a, b, c, az: L.ORBIT.azimuth, zmin: L.clampZoom(0), zmax: L.clampZoom(99), zmid: L.clampZoom(1) }));""")
    assert out["narrow"] >= out["square"] >= out["wide"] and out["zero"] >= out["narrow"]
    assert 28 <= out["huge"] <= out["wide"] + 1e-9
    assert out["a"]["az"] == pytest.approx(out["az"]) and out["b"]["az"] == pytest.approx(-out["az"])
    assert out["a"]["pol"] == 1.3 and out["b"]["pol"] == 1.62 and out["c"] == {"az": 0.1, "pol": 1.5}
    assert out["zmin"] == 0.45 and out["zmax"] == 1.25 and out["zmid"] == 1


@needs_node
def test_status_styles_cover_the_five_states_with_distinct_colors():
    out = _node(_logic_import() + """
      console.log(JSON.stringify([L.STATUS_STYLE, L.styleFor("busy", true), L.styleFor("???", false)]));""")
    styles, offline, unknown = out
    assert set(styles) == {"busy", "ok", "warn", "error", "idle"}
    assert len({styles[k]["color"] for k in ("ok", "warn", "error", "idle")}) == 4
    assert offline == styles["idle"] and unknown == styles["idle"]


@needs_node
def test_vault3d_module_loads_in_node_and_exports_its_api():
    out = _node(f"""
      import * as M from "{(STATIC / "vault3d.js").as_uri()}";
      console.log(JSON.stringify({{ mount: typeof M.mountVault3D, webgl: typeof M.isWebGLAvailable,
        fit: typeof M.fitCamera, styles: Object.keys(M.STATUS_STYLE).length }}));""")
    assert out == {"mount": "function", "webgl": "function", "fit": "function", "styles": 5}


def test_svg_fallback_scene_has_four_accessible_rooms_with_dwellers(monkeypatch):
    html = _html(monkeypatch)
    scene = "".join(b for b in _blocks("OFFICE") if "office-scene" in b and "<svg" in b)
    assert 'viewBox="0 0 400 300"' in scene
    assert 'id="vault-dweller"' in scene and scene.count('href="#vault-dweller"') == len(AGENTS)
    for agent in AGENTS:
        group = re.search(r'<g class="office-agent[^"]*" id="office-agent-%s"([^>]*)>' % agent, scene)
        assert group, agent
        assert 'role="button"' in group.group(1) and 'tabindex="0"' in group.group(1) and "aria-label=" in group.group(1)
        assert f'id="office-bubble-{agent}"' in scene
    assert html.count('class="office-focus"') == len(AGENTS)
    # el contenedor 3D y los botones accesibles conviven con el respaldo SVG
    assert 'id="office-3d"' in html and 'id="office-agent-row"' in html
    for agent in AGENTS:
        assert f'id="office-btn-{agent}"' in html


def test_svg_fallback_styles_cover_every_state_and_respect_reduced_motion(monkeypatch):
    _html(monkeypatch)
    css = "".join(b for b in _blocks("OFFICE") if ".office-scene" in b and "{" in b)
    for state in ("status-busy", "status-ok", "status-warn", "status-error", "status-idle", "offline", "selected"):
        assert f".office-agent.{state}" in css, state
    reduced = css.split("prefers-reduced-motion", 1)[1]
    assert "animation: none" in reduced
    # la escena usa su paleta fija: el tema del panel no la tiñe
    theme_css = "".join(_blocks("THEME"))
    assert "office" not in theme_css


def test_office_js_loads_the_3d_vault_selects_through_one_function_and_falls_back(monkeypatch):
    _html(monkeypatch)
    js = "".join(b for b in _blocks("OFFICE") if "officePoll" in b)
    for name in ("officeSelect", "officeInit3D", "officeFallbackToSvg", "officeRender3D"):
        assert f"function {name}" in js or f"async function {name}" in js
    assert 'import("/static/vault3d.js")' in js and "mountVault3D" in js
    assert "officeWebGL" in js and "office3d.dispose" in js and "onLost: officeFallbackToSvg" in js
    assert "innerHTML" not in js
    assert len(re.findall(r"\bofficeSelect\b", js)) >= 3  # definición + onSelect de la escena 3D + listeners
    assert '.classList.toggle("selected"' in js


def test_app_py_keeps_pure_crlf_line_endings():
    data = (ROOT / "app.py").read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n")
