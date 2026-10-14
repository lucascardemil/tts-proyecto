# -*- coding: utf-8 -*-
"""Tema Matrix y oficina 3D: archivos estáticos, lógica pura (node) y HTML."""
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


def _auth():
    return {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}


def _client(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    return app_module.app.test_client()


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


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")


def _node(js: str):
    out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _logic_import():
    return f'import * as L from "{(STATIC / "office3d-logic.js").as_uri()}";'


@needs_node
def test_status_style_covers_the_five_states_with_distinct_colors():
    styles = _node(_logic_import() + "console.log(JSON.stringify(L.STATUS_STYLE));")
    assert set(styles) == {"busy", "ok", "warn", "error", "idle"}
    assert len({s["color"] for s in styles.values()}) == 5
    assert styles["busy"]["speed"] > styles["ok"]["speed"] > styles["idle"]["speed"]
    assert styles["error"]["blink"] is True and styles["busy"]["blink"] is False


@needs_node
def test_style_for_offline_and_unknown_states_fall_back_to_idle():
    out = _node(_logic_import() + """
      console.log(JSON.stringify([L.styleFor("busy", true), L.styleFor("???", false), L.styleFor("warn", false)]));""")
    idle = _node(_logic_import() + "console.log(JSON.stringify(L.STATUS_STYLE.idle));")
    assert out[0] == idle and out[1] == idle and out[2]["color"] != idle["color"]


@needs_node
def test_fit_camera_moves_back_on_narrow_screens_and_stays_in_range():
    out = _node(_logic_import() + """
      const d = a => L.fitCamera(a).distance;
      console.log(JSON.stringify({wide: d(1.78), square: d(1), narrow: d(0.5), zero: d(0), huge: d(50)}));""")
    assert out["narrow"] > out["square"] > out["wide"]
    assert 9 <= out["huge"] <= out["wide"] and out["zero"] == 40 and out["narrow"] <= 40


@needs_node
def test_orbit_and_zoom_clamps():
    out = _node(_logic_import() + """
      const a = L.clampOrbit(10, -5), b = L.clampOrbit(-10, 10), c = L.clampOrbit(0.1, 1.2);
      console.log(JSON.stringify({a, b, c, az: L.ORBIT.azimuth, zmin: L.clampZoom(0), zmax: L.clampZoom(99), zmid: L.clampZoom(1)}));""")
    assert out["a"]["az"] == pytest.approx(out["az"]) and out["b"]["az"] == pytest.approx(-out["az"])
    assert out["a"]["pol"] == 0.9 and out["b"]["pol"] == 1.4
    assert out["c"] == {"az": 0.1, "pol": 1.2}
    assert out["zmin"] == 0.5 and out["zmax"] == 1.3 and out["zmid"] == 1


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


def test_matrix_blocks_are_jinja_safe_and_do_not_use_innerhtml():
    blocks = _blocks("MATRIX")
    assert len(blocks) >= 3, "faltan los bloques MATRIX de CSS, HTML y JS"
    for block in blocks:
        assert "{{" not in block and "{%" not in block and "{#" not in block and '"""' not in block
        assert "innerHTML" not in block


def test_matrix_theme_is_wired_in_the_page(monkeypatch):
    html = _html(monkeypatch)
    head = html.split("</head>")[0]
    assert 'localStorage.getItem("uiTheme")' in head and 'setAttribute("data-theme"' in head  # sin parpadeo
    assert 'html[data-theme="matrix"]' in html
    assert '<canvas id="matrix-rain"' in html and 'id="theme-toggle"' in html
    for name in ("themeCurrent", "themeApply", "rainSync"):
        assert f"function {name}" in html
    assert "prefers-reduced-motion" in "".join(_blocks("MATRIX"))
    # el estado de cada agente mantiene un matiz propio dentro del tema
    css = "".join(b for b in _blocks("MATRIX") if "data-theme" in b)
    assert re.search(r"--error:\s*#f87171", css)
    filters = 0
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        if "filter:" not in body:
            continue
        filters += 1
        for part in (p.strip() for p in selector.split(",")):
            subj = re.sub(r":not\([^)]*\)", "", part)  # lo que va dentro de :not() no es el sujeto del selector
            assert not re.search(r"\.screen\b", subj), f"filter sobre una pantalla de estado: {part}"
            assert not re.fullmatch(r'html\[data-theme="matrix"\]\s+\.office-scene', part), (
                "filter sobre todo el SVG borra los matices de estado")
    assert filters >= 1  # el tinte verde del fondo sigue existiendo


def test_matrix_tokens_meet_the_contrast_floor(monkeypatch):
    _html(monkeypatch)
    css = "".join(b for b in _blocks("MATRIX") if "data-theme" in b)
    tokens = dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", css.split('html[data-theme="matrix"]', 1)[1]))
    for surface in ("--bg-main", "--surface-1", "--surface-2", "--surface-3"):
        assert _contrast(tokens["--text-primary"], tokens[surface]) >= 7
        assert _contrast(tokens["--text-secondary"], tokens[surface]) >= 4.5
        assert _contrast(tokens["--text-muted"], tokens[surface]) >= 4.5


def test_office3d_module_is_served_behind_auth(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/static/office3d.js").status_code == 401
    for name in ("office3d.js", "office3d-logic.js"):
        ok = client.get(f"/static/{name}", headers=_auth())
        assert ok.status_code == 200 and ok.mimetype in ("text/javascript", "application/javascript")


@needs_node
def test_office3d_module_loads_in_node_and_exports_its_api():
    out = _node(f'''
      import * as M from "{(STATIC / "office3d.js").as_uri()}";
      console.log(JSON.stringify({{
        mount: typeof M.mountOffice3D, webgl: typeof M.isWebGLAvailable,
        fit: typeof M.fitCamera, styles: Object.keys(M.STATUS_STYLE).length }}));''')
    assert out == {"mount": "function", "webgl": "function", "fit": "function", "styles": 5}


@needs_node
def test_fog_range_keeps_the_target_in_view_at_any_camera_distance():
    out = _node(_logic_import() + """
      console.log(JSON.stringify([9, 19, 52].map(d => L.fogRange(d))));""")
    for d, fog in zip((9, 19, 52), out):
        assert fog["near"] < d < fog["far"]
    spans = [f["far"] - f["near"] for f in out]
    assert spans[0] < spans[1] < spans[2]


def test_office_page_has_the_3d_container_buttons_and_loader(monkeypatch):
    html = _html(monkeypatch)
    assert 'id="office-3d"' in html and 'id="office-agent-row"' in html
    for agent in ("videos", "publica", "metricas", "revisor"):
        assert f'id="office-btn-{agent}"' in html
        assert f'id="office-agent-{agent}"' in html  # el SVG de respaldo sigue ahí
    js = "".join(b for b in _blocks("OFFICE") if "officePoll" in b)
    for name in ("officeSelect", "officeInit3D", "officeFallbackToSvg", "officeRender3D"):
        assert f"function {name}" in js or f"async function {name}" in js
    assert 'import("/static/office3d.js")' in js
    assert "officeWebGL" in js and "office3d.dispose" in js
    assert "innerHTML" not in js


def test_office_selection_goes_through_one_function(monkeypatch):
    html = _html(monkeypatch)
    js = "".join(b for b in _blocks("OFFICE") if "officePoll" in b)
    assert len(re.findall(r"\bofficeSelect\b", js)) >= 3  # definición + onSelect de la escena 3D + listeners de SVG y botones
    assert "const select = () => { officeSelected = id;" not in js  # ya no hay selección en línea


def test_app_py_keeps_pure_crlf_line_endings():
    data = (ROOT / "app.py").read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n")
