# Tema Matrix + oficina 3D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tema Matrix en todo el panel (con interruptor Matrix/Clásico) y la oficina de agentes en 3D estilo Matrix con three.js, con la escena SVG actual como respaldo.

**Architecture:** El tema es solo CSS (`html[data-theme="matrix"]` sobrescribe los tokens de `:root`) más un canvas de lluvia de código y un interruptor. La escena 3D vive en `static/office3d.js` (módulo ES) apoyada en `static/office3d-logic.js` (lógica pura, probada con node) y en three.js guardado en `static/vendor/`. `app.py` solo agrega el contenedor, los botones accesibles y un cargador que monta la escena y vuelve al SVG si algo falla. El backend y `/api/office/state` no cambian.

**Tech Stack:** Flask (HTML incrustado en `app.py`), three.js (WebGL, vendorizado), JS vanilla, pytest, node 24 para probar la lógica pura.

**Spec:** `docs/superpowers/specs/2026-10-14-tema-matrix-oficina-3d-design.md`

## Global Constraints

- Responder y comentar en español; textos de UI en español, sentence case.
- **No hacer commit ni push** (solo cuando el usuario lo pida). Cada tarea termina con la suite en verde, no con un commit.
- `app.py` usa **CRLF** en todas sus líneas: toda inserción debe ser CRLF y al final `b.count(b"\r\n") == b.count(b"\n")`. Usar el helper de parcheo de abajo, nunca `Edit` con saltos de línea sueltos.
- El HTML de `app.py` vive en `HTML = r"""..."""` y pasa por Jinja: en el contenido agregado **no** puede haber `{{`, `{%`, `{#` ni `"""`. En JS usar `{ a: { b: 1 } }` con espacios, nunca `{{`.
- JS del panel: texto del servidor solo con `textContent` (nada de `innerHTML`).
- Marcadores: lo agregado va dentro de bloques `OFFICE:START/END` (oficina 3D) o `MATRIX:START/END` (tema), en CSS (`/* ... */`), HTML (`<!-- ... -->`) y JS (`/* ... */`).
- `static/` lo sirve Flask y lo protege `_require_dashboard_auth` (401 sin credenciales); no agregar `/static/` a `_PUBLIC_PATHS`.
- Archivos temporales solo en el scratchpad de la sesión y se borran al terminar. No tocar el servidor de macrame-bot ni el de "Producción del canal" (puerto 5055).
- Estados con color propio en el tema Matrix: ok verde `#00ff41`/`#00c832`, aviso ámbar `#fbbf24`, error rojo `#f87171`, inactivo gris `#6b7280`.
- Contraste mínimo en el tema Matrix: `--text-primary` ≥ 7:1 y `--text-secondary` / `--text-muted` ≥ 4.5:1 sobre `--bg-main` y sobre `--surface-1`/`--surface-2`/`--surface-3`.
- Rendimiento: ~30 fps (`FRAME_MS = 33`), `pixelRatio` ≤ 2, lluvia de monitores cada 66 ms, `prefers-reduced-motion` = sin animación.

### Helper de parcheo de `app.py` (CRLF)

Crear en el scratchpad (no en el proyecto) `patch_app.py` con este esqueleto y llenar `swap(...)` por cada inserción; borrarlo al terminar la tarea:

```python
from pathlib import Path

P = Path(r"C:\Users\lucas\Desktop\tts-proyecto\app.py")
b = P.read_bytes()
assert b.count(b"\r\n") == b.count(b"\n"), "app.py ya no es CRLF puro"


def crlf(s):
    return s.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


def swap(old, new):
    global b
    o, n = crlf(old), crlf(new)
    assert b.count(o) == 1, "ancla ausente o repetida: %r" % old[:70]
    b = b.replace(o, n)


# swap("ancla exacta (LF)", "ancla + texto nuevo (LF)")

assert b.count(b"\r\n") == b.count(b"\n"), "se rompió el CRLF"
P.write_bytes(b)
print("ok")
```

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `static/vendor/three.module.min.js` (+ `three.core.min.js` si el paquete lo trae) | three.js vendorizado, versión fija |
| `static/vendor/README.md` | versión, origen y licencia MIT de three.js |
| `static/office3d-logic.js` (nuevo) | Lógica pura sin DOM ni three: `STATUS_STYLE`, `styleFor`, `fitCamera`, `clampOrbit`, `clampZoom`, constantes |
| `static/office3d.js` (nuevo) | Escena three.js: `mountOffice3D`, `isWebGLAvailable`, reexporta `STATUS_STYLE` y `fitCamera` |
| `app.py` | `mimetypes.add_type`; tema Matrix (CSS, canvas, toggle, JS); contenedor 3D, botones accesibles y cargador con respaldo |
| `tests/test_office_3d.py` (nuevo) | Estáticos (401/200/MIME), lógica pura con node, HTML del tema y de la oficina 3D, CRLF |

---

### Task 1: Archivos estáticos: three.js vendorizado y MIME de JavaScript

**Files:**
- Create: `static/vendor/three.module.min.js` (+ dependencias relativas), `static/vendor/README.md`
- Modify: `app.py` (cerca de los imports, `import mimetypes` y `mimetypes.add_type`)
- Test: `tests/test_office_3d.py` (nuevo)

**Interfaces:**
- Produces: `GET /static/vendor/three.module.min.js` (JS, 401 sin credenciales) y el helper de tests `_client(monkeypatch)` / `_auth()` que reutilizan las tareas siguientes.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_office_3d.py`:

```python
# -*- coding: utf-8 -*-
"""Tema Matrix y oficina 3D: archivos estáticos, lógica pura (node) y HTML."""
import base64
import re
import sys
from pathlib import Path

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
    for rel in re.findall(r'from\s*"(\./[^"]+)"', text):
        assert (STATIC / "vendor" / rel).exists(), f"falta {rel} junto a three.module.min.js"
    readme = (STATIC / "vendor" / "README.md").read_text(encoding="utf-8")
    assert re.search(r"three(\.js)?\s*@?\s*r?\d+", readme, flags=re.I) and "MIT" in readme
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_3d.py -q`
Expected: FAIL (404 / archivo inexistente).

- [ ] **Step 3: Bajar three.js (descarga de un paquete npm)**

Desde una carpeta temporal del scratchpad (no desde el proyecto):

```bash
SCRATCH="C:/Users/lucas/AppData/Local/Temp/claude/C--Users-lucas-Desktop-tts-proyecto/150f17a9-6a39-4c79-aab6-fd23f61e3f0e/scratchpad/three_pkg"
mkdir -p "$SCRATCH" && cd "$SCRATCH" && VERSION=$(npm view three version) && echo "version=$VERSION" && npm pack "three@$VERSION" && tar -xzf three-$VERSION.tgz && ls package/build
```

Copiar al proyecto `package/build/three.module.min.js` y, si existe en esa carpeta, `package/build/three.core.min.js` a `C:\Users\lucas\Desktop\tts-proyecto\static\vendor\` (crear la carpeta). Escribir `static/vendor/README.md`:

```markdown
# three.js (vendorizado)

- Paquete: three@<VERSION>, bajado con `npm pack` (MIT License, © three.js authors).
- Archivos: `three.module.min.js` (+ `three.core.min.js` si el paquete lo trae), tal cual salen de `build/`.
- Se carga solo desde `static/office3d.js`. Para actualizar: reemplazar los archivos y revisar la API usada
  (Scene, PerspectiveCamera, WebGLRenderer, LineSegments, EdgesGeometry, CanvasTexture, Sprite, Raycaster).
```

(sustituir `<VERSION>` por el número real, p. ej. `three@0.180.0`). Borrar la carpeta `three_pkg` del scratchpad.

- [ ] **Step 4: MIME determinista en `app.py`**

Con el helper de parcheo: junto a los imports de `app.py` (`import` de la stdlib) agregar `import mimetypes` si no está, y inmediatamente después del bloque de imports (antes de `app = Flask(__name__)`, línea ~80) agregar:

```python
# Los módulos ES exigen un MIME de JavaScript; en Windows el registro a veces devuelve text/plain.
mimetypes.add_type("text/javascript", ".js")
```

Anclar con `swap("app = Flask(__name__)\n", "# Los módulos ES ...\nmimetypes.add_type(\"text/javascript\", \".js\")\n\napp = Flask(__name__)\n")` y agregar `import mimetypes` con otro `swap` sobre una línea de import estándar existente (por ejemplo la línea `import os` si es única; leerla antes).

- [ ] **Step 5: Correr y ver que pasan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_3d.py tests/test_office_state.py -q`
Expected: PASS.

---

### Task 2: Lógica pura de la escena (`office3d-logic.js`)

**Files:**
- Create: `static/office3d-logic.js`
- Test: `tests/test_office_3d.py`

**Interfaces:**
- Produces (todo `export`): `STATUS_STYLE`, `styleFor(status, offline)`, `FOV`, `ORBIT`, `fitCamera(aspect) -> {fov, distance}`, `clampOrbit(az, pol) -> {az, pol}`, `clampZoom(z) -> number`.
- Consumes: nada.

- [ ] **Step 1: Escribir los tests que fallan** (agregar a `tests/test_office_3d.py`)

```python
import json
import shutil
import subprocess

import pytest

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
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_3d.py -q`
Expected: FAIL (no existe el módulo).

- [ ] **Step 3: Escribir `static/office3d-logic.js`**

```js
// Lógica pura de la oficina 3D: sin DOM ni three.js, para poder probarla con node.

export const STATUS_STYLE = {
  busy:  { color: "#00ff41", speed: 1.0,  alpha: 1.0,  blink: false },
  ok:    { color: "#00c832", speed: 0.35, alpha: 0.85, blink: false },
  warn:  { color: "#fbbf24", speed: 0.2,  alpha: 0.9,  blink: false },
  error: { color: "#f87171", speed: 0.2,  alpha: 1.0,  blink: true },
  idle:  { color: "#6b7280", speed: 0.05, alpha: 0.5,  blink: false },
};

export function styleFor(status, offline) {
  if (offline) return STATUS_STYLE.idle;
  return STATUS_STYLE[status] || STATUS_STYLE.idle;
}

export const FOV = 40;
export const SCENE_WIDTH = 14;
export const DIST_MIN = 9;
export const DIST_MAX = 40;
export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 1.3;
export const ORBIT = {
  azimuth: 35 * Math.PI / 180,
  polarMin: 0.9,
  polarMax: 1.4,
  polarDefault: 1.15,
};

function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

// Distancia a la que entran los 4 escritorios a lo ancho, según la relación de aspecto del contenedor.
export function fitCamera(aspect) {
  const a = aspect > 0.2 ? aspect : 0.2;
  const raw = SCENE_WIDTH / (2 * Math.tan(FOV * Math.PI / 360) * a);
  return { fov: FOV, distance: clamp(raw, DIST_MIN, DIST_MAX) };
}

export function clampOrbit(az, pol) {
  return { az: clamp(az, -ORBIT.azimuth, ORBIT.azimuth), pol: clamp(pol, ORBIT.polarMin, ORBIT.polarMax) };
}

export function clampZoom(z) { return clamp(z, ZOOM_MIN, ZOOM_MAX); }
```

- [ ] **Step 4: Correr y ver que pasan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_3d.py -q`
Expected: PASS (los de node se omiten solo si no hay node).

---

### Task 3: Tema Matrix (CSS, lluvia de fondo, interruptor)

**Files:**
- Modify: `app.py` (script de `<head>`, bloque CSS `MATRIX`, canvas y botón HTML, JS `MATRIX`)
- Test: `tests/test_office_3d.py`

**Interfaces:**
- Consumes: `$()` (helper global del panel), `_client`/`_auth` de la Task 1.
- Produces: atributo `data-theme="matrix|classic"` en `<html>`, clave `localStorage.uiTheme`, funciones JS `themeCurrent()`, `themeApply(t)`, `rainSync()`; ids `matrix-rain` y `theme-toggle`.

- [ ] **Step 1: Escribir los tests que fallan** (agregar a `tests/test_office_3d.py`)

```python
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
    for color in ("#f87171", "#fbbf24", "#4ade80"):
        assert color in html


def test_matrix_tokens_meet_the_contrast_floor(monkeypatch):
    _html(monkeypatch)
    css = "".join(b for b in _blocks("MATRIX") if "data-theme" in b)
    tokens = dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", css.split('html[data-theme="matrix"]', 1)[1]))
    for surface in ("--bg-main", "--surface-1", "--surface-2", "--surface-3"):
        assert _contrast(tokens["--text-primary"], tokens[surface]) >= 7
        assert _contrast(tokens["--text-secondary"], tokens[surface]) >= 4.5
        assert _contrast(tokens["--text-muted"], tokens[surface]) >= 4.5
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_3d.py -q`
Expected: FAIL (sin bloques MATRIX).

- [ ] **Step 3: Parchear `app.py`** (helper de parcheo; anclas exactas, una vez cada una)

(a) **Script de `<head>` anti-parpadeo.** Ancla: `<style>\n  :root {\n    --bg-main: #080808;` → insertar antes del `<style>`:

```html
<script>
(function () {
  var t = "matrix";
  try { t = localStorage.getItem("uiTheme") || "matrix"; } catch (e) { /* sin localStorage */ }
  document.documentElement.setAttribute("data-theme", t === "classic" ? "classic" : "matrix");
})();
</script>
<style>
  :root {
    --bg-main: #080808;
```

(b) **CSS.** Ancla: `  /* OFFICE:END */\n</style>` → reemplazar por `  /* OFFICE:END */\n` + bloque + `</style>`:

```css
  /* MATRIX:START */
  #matrix-rain { display: none; position: fixed; inset: 0; width: 100%; height: 100%; z-index: -1; pointer-events: none; opacity: .3; }
  html[data-theme="matrix"] #matrix-rain { display: block; }
  .theme-toggle { margin-top: auto; width: 100%; font-family: inherit; text-align: left; }
  html[data-theme="matrix"] {
    --bg-main: #000000;
    --bg-sidebar: #010a03;
    --surface-1: #041006;
    --surface-2: #07180a;
    --surface-3: #0b2210;
    --border: #0f4a1c;
    --border-hover: #1a7a31;
    --text-primary: #c8ffd6;
    --text-secondary: #7fd99a;
    --text-muted: #55a96d;
    --red-dark: #04440f;
    --red-primary: #00a828;
    --red-light: #00ff41;
    --gradient-red: linear-gradient(110deg, #04440f 0%, #00a828 45%, #00ff41 100%);
    --gradient-red-hover: linear-gradient(110deg, #066016 0%, #00c032 45%, #4dff77 100%);
    --gradient-red-active: linear-gradient(110deg, #04440f, #00a828, #00d636);
    --gradient-red-subtle: linear-gradient(135deg, #041006, rgba(0, 168, 40, 0.22));
    --success: #00ff41;
    --error: #f87171;
    --mono: "Cascadia Mono", Consolas, "Courier New", monospace;
    color-scheme: dark;
  }
  html[data-theme="matrix"] body,
  html[data-theme="matrix"] button,
  html[data-theme="matrix"] input,
  html[data-theme="matrix"] select,
  html[data-theme="matrix"] textarea { font-family: var(--mono); }
  html[data-theme="matrix"] .card { background: rgba(4, 16, 6, .9); box-shadow: 0 0 18px rgba(0, 255, 65, .08); }
  html[data-theme="matrix"] .card h2 { color: var(--red-light); text-shadow: 0 0 8px rgba(0, 255, 65, .55); letter-spacing: .03em; }
  html[data-theme="matrix"] .nav-item.active,
  html[data-theme="matrix"] .btn-primary,
  html[data-theme="matrix"] .voice-card .gentle-badge,
  html[data-theme="matrix"] .ring.done::after,
  html[data-theme="matrix"] .ring.error::after { color: #00200a; font-weight: 700; }
  html[data-theme="matrix"] .nav-item.active { box-shadow: 0 0 14px rgba(0, 255, 65, .35); }
  html[data-theme="matrix"] .btn-primary { box-shadow: 0 0 22px rgba(0, 255, 65, .25); }
  html[data-theme="matrix"] .tab-btn.active {
    color: var(--red-light); background: rgba(0, 255, 65, .12);
    box-shadow: inset 0 -2px 0 var(--red-light), 0 0 14px rgba(0, 255, 65, .2);
  }
  html[data-theme="matrix"] .voice-card.active { border-color: var(--red-light); }
  html[data-theme="matrix"] .office-scene {
    filter: grayscale(.4) sepia(1) hue-rotate(75deg) saturate(2.2) brightness(.7);
  }
  html[data-theme="matrix"] { scrollbar-color: #0f4a1c #000; }
  @media (prefers-reduced-motion: reduce) { #matrix-rain { opacity: .15; } }
  /* MATRIX:END */
```

(c) **Canvas.** Ancla: `<body>\n\n<button class="menu-toggle"` → insertar entre medio:

```html
<body>
<!-- MATRIX:START -->
<canvas id="matrix-rain" aria-hidden="true"></canvas>
<!-- MATRIX:END -->

<button class="menu-toggle"
```

(d) **Interruptor.** Ancla: `</nav>\n\n<main>` → insertar antes de `</nav>`:

```html
  <!-- MATRIX:START -->
  <button type="button" class="nav-item theme-toggle" id="theme-toggle" aria-pressed="true">Tema: Matrix</button>
  <!-- MATRIX:END -->
</nav>

<main>
```

(e) **JS.** Ancla: `/* OFFICE:END */\n\n// ── Navegación por pestañas` → insertar el bloque entre ambos:

```js
/* OFFICE:END */

/* MATRIX:START */
const RAIN_CELL = 16;
const RAIN_GLYPHS = (() => {
  let s = "";
  for (let c = 0xFF66; c <= 0xFF9D; c++) s += String.fromCharCode(c);
  return s + "0123456789";
})();
let rainTimer = null;
let rainDrops = [];

function themeCurrent() {
  return document.documentElement.getAttribute("data-theme") === "classic" ? "classic" : "matrix";
}

function themeApply(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("uiTheme", t); } catch (e) { /* sin localStorage: el tema vive en memoria */ }
  const b = $("theme-toggle");
  b.textContent = t === "matrix" ? "Tema: Matrix" : "Tema: Clásico";
  b.setAttribute("aria-pressed", String(t === "matrix"));
  rainSync();
}

function rainResize() {
  const c = $("matrix-rain");
  c.width = window.innerWidth;
  c.height = window.innerHeight;
  rainDrops = Array.from({ length: Math.ceil(c.width / RAIN_CELL) }, () => -Math.floor(Math.random() * 50));
}

function rainFrame() {
  const c = $("matrix-rain");
  const ctx = c.getContext("2d");
  ctx.fillStyle = "rgba(0, 0, 0, 0.08)";
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.fillStyle = "#00ff41";
  ctx.font = RAIN_CELL + "px monospace";
  rainDrops.forEach((y, i) => {
    ctx.fillText(RAIN_GLYPHS[Math.floor(Math.random() * RAIN_GLYPHS.length)], i * RAIN_CELL, y * RAIN_CELL);
    rainDrops[i] = (y * RAIN_CELL > c.height && Math.random() > 0.975) ? 0 : y + 1;
  });
}

function rainSync() {
  if (rainTimer) { clearInterval(rainTimer); rainTimer = null; }
  const c = $("matrix-rain");
  if (themeCurrent() !== "matrix" || document.hidden) return;
  rainResize();
  c.getContext("2d").clearRect(0, 0, c.width, c.height);
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    for (let i = 0; i < 60; i++) rainFrame();  // un cuadro estático
    return;
  }
  rainTimer = setInterval(rainFrame, 50);
}

$("theme-toggle").addEventListener("click", () => themeApply(themeCurrent() === "matrix" ? "classic" : "matrix"));
document.addEventListener("visibilitychange", rainSync);
window.addEventListener("resize", () => { if (themeCurrent() === "matrix") rainSync(); });
themeApply(themeCurrent());
/* MATRIX:END */

// ── Navegación por pestañas
```

(El `swap` ancla solo el prefijo `/* OFFICE:END */

// ── Navegación por pestañas`; el resto de esa línea del original (` (tabs superiores + sidebar) ──`) queda intacto detrás.)

- [ ] **Step 4: Caza de rojos y blancos fijos**

Buscar en el CSS de `app.py` los literales que rompen el tema y agregarles override en el bloque `MATRIX`: `grep -n "166, *15\|166,15\|227, *44\|#c21b2b\|101, *9\|190, *20\|120, *10" app.py` y `grep -n "color: #fff" app.py`. Para cada uno que se vea rojo o ilegible en Matrix, agregar una regla `html[data-theme="matrix"] <selector> { ... }` con verde/negro (`#00200a` sobre fondos verdes brillantes). Este paso se cierra en la verificación visual de la Task 6; aquí solo se cubren los obvios.

- [ ] **Step 5: Correr los tests**

Run: `venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS (toda la suite; confirma que Jinja renderiza `/` y que los tests de la oficina siguen verdes).

---

### Task 4: La escena 3D (`office3d.js`)

**Files:**
- Create: `static/office3d.js`
- Test: `tests/test_office_3d.py`

**Interfaces:**
- Consumes: `./vendor/three.module.min.js`, `./office3d-logic.js` (`styleFor`, `fitCamera`, `clampOrbit`, `clampZoom`, `ORBIT`, `STATUS_STYLE`).
- Produces:
  - `export function isWebGLAvailable(): boolean`
  - `export function mountOffice3D(container: HTMLElement, opts: { onSelect(id), onLost(), reducedMotion: boolean }): { update(agents, offline), setSelected(id), dispose() }` donde `agents = {videos|publica|metricas|revisor: {status, bubble}}`.
  - `export { STATUS_STYLE, fitCamera }`.
- Decisión de diseño (ruling): el agente se sienta **al costado del monitor, en tres cuartos**, y no de espaldas a la cámara, porque desde una cámara alta su torso taparía la pantalla; mira al monitor girando la cabeza. `setSelected` se agrega a la interfaz del spec para resaltar el agente elegido.

- [ ] **Step 1: Escribir el test que falla** (agregar a `tests/test_office_3d.py`)

```python
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
```

(El módulo no debe tocar `document`/`window` al importarse; por eso carga en node.)

- [ ] **Step 2: Correr y ver que falla**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_3d.py -q`
Expected: FAIL (404 / módulo inexistente).

- [ ] **Step 3: Escribir `static/office3d.js`**

```js
// Oficina 3D estilo Matrix: escena three.js con 4 escritorios, agentes-silueta y monitores con lluvia de código.
import * as THREE from "./vendor/three.module.min.js";
import { STATUS_STYLE, styleFor, fitCamera, clampOrbit, clampZoom, ORBIT } from "./office3d-logic.js";

export { STATUS_STYLE, fitCamera };

const AGENTS = [
  { id: "videos", name: "VIDEOS" },
  { id: "publica", name: "PUBLICA" },
  { id: "metricas", name: "METRICAS" },
  { id: "revisor", name: "REVISOR" },
];
const SPACING = 3.4;
const TARGET = new THREE.Vector3(0, 1.2, 0);
const FRAME_MS = 33;   // ~30 fps
const RAIN_MS = 66;    // paso de la lluvia de los monitores
const DESK_COLOR = "#0a8f2a";
const GLYPHS = (() => {
  let s = "";
  for (let c = 0xFF66; c <= 0xFF9D; c++) s += String.fromCharCode(c);
  return s + "0123456789";
})();

export function isWebGLAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch (e) {
    return false;
  }
}

// Textura de canvas con lluvia de código (monitores y pared).
class Rain {
  constructor(w, h, cell) {
    this.w = w; this.h = h; this.cell = cell;
    this.canvas = document.createElement("canvas");
    this.canvas.width = w; this.canvas.height = h;
    this.ctx = this.canvas.getContext("2d");
    this.ctx.fillStyle = "#000";
    this.ctx.fillRect(0, 0, w, h);
    const rows = Math.floor(h / cell);
    this.drops = Array.from({ length: Math.floor(w / cell) }, () => Math.random() * rows);
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.color = "#00ff41"; this.speed = 0.35; this.alpha = 1; this.blink = false;
  }

  setStyle(st) { this.color = st.color; this.speed = st.speed; this.alpha = st.alpha; this.blink = st.blink; }

  step(blinkOn) {
    const { ctx, cell, w, h } = this;
    ctx.globalAlpha = 1;
    ctx.fillStyle = "rgba(0, 0, 0, " + (0.05 + 0.1 * Math.min(this.speed, 1)) + ")";
    ctx.fillRect(0, 0, w, h);
    ctx.font = cell + "px monospace";
    ctx.fillStyle = this.color;
    ctx.globalAlpha = this.alpha * (this.blink && !blinkOn ? 0.25 : 1);
    for (let i = 0; i < this.drops.length; i++) {
      const y = Math.floor(this.drops[i]) * cell;
      ctx.fillText(GLYPHS[Math.floor(Math.random() * GLYPHS.length)], i * cell, y);
      if (y > h && Math.random() > 0.96) this.drops[i] = 0;
      this.drops[i] += this.speed;
    }
    ctx.globalAlpha = 1;
    this.texture.needsUpdate = true;
  }

  warm(n) { for (let i = 0; i < n; i++) this.step(true); }
  dispose() { this.texture.dispose(); }
}

// Cartel de texto (globo y nombre) como sprite.
class Label {
  constructor(width, height, worldW) {
    this.w = width; this.h = height; this.key = "";
    this.canvas = document.createElement("canvas");
    this.canvas.width = width; this.canvas.height = height;
    this.ctx = this.canvas.getContext("2d");
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.material = new THREE.SpriteMaterial({ map: this.texture, transparent: true, depthTest: false });
    this.sprite = new THREE.Sprite(this.material);
    this.sprite.scale.set(worldW, worldW * height / width, 1);
    this.sprite.renderOrder = 10;
  }

  draw(text, color, framed) {
    const key = text + "|" + color + "|" + framed;
    if (key === this.key) return;
    this.key = key;
    const { ctx, w, h } = this;
    ctx.clearRect(0, 0, w, h);
    if (framed) {
      ctx.fillStyle = "rgba(0, 18, 4, 0.88)";
      ctx.fillRect(3, 3, w - 6, h - 6);
      ctx.strokeStyle = color;
      ctx.lineWidth = 3;
      ctx.strokeRect(3, 3, w - 6, h - 6);
    }
    ctx.fillStyle = color;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    let size = Math.floor(h * 0.5);
    ctx.font = "bold " + size + "px monospace";
    while (ctx.measureText(text).width > w - 24 && size > 10) {
      size -= 2;
      ctx.font = "bold " + size + "px monospace";
    }
    ctx.fillText(text, w / 2, h / 2 + 2);
    this.texture.needsUpdate = true;
  }

  dispose() { this.texture.dispose(); this.material.dispose(); }
}

// Caja en líneas (aristas) con un segundo trazo más tenue que simula el brillo.
function wire(geom, color, opacity, mats) {
  const edges = new THREE.EdgesGeometry(geom);
  geom.dispose();
  const g = new THREE.Group();
  const main = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
  const halo = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color, transparent: true, opacity: opacity * 0.3 }));
  halo.scale.setScalar(1.035);
  g.add(main, halo);
  mats.push(main.material, halo.material);
  return g;
}

function at(obj, x, y, z) { obj.position.set(x, y, z); return obj; }

function buildSlot(def, index) {
  const slot = new THREE.Group();
  slot.position.x = (index - 1.5) * SPACING;
  const deskMats = [], agentMats = [], selMats = [];

  // escritorio
  slot.add(at(wire(new THREE.BoxGeometry(2.8, 0.08, 1.3), DESK_COLOR, 0.9, deskMats), 0, 0.8, 0));
  [[-1.3, -0.55], [1.3, -0.55], [-1.3, 0.55], [1.3, 0.55]].forEach(p =>
    slot.add(at(wire(new THREE.BoxGeometry(0.08, 0.76, 0.08), DESK_COLOR, 0.7, deskMats), p[0], 0.4, p[1])));

  // monitor con la lluvia, girado hacia la cámara y hacia el agente
  const rain = new Rain(256, 160, 10);
  const monitor = new THREE.Group();
  monitor.add(wire(new THREE.BoxGeometry(1.7, 1.1, 0.1), "#00ff41", 0.9, deskMats));
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(1.55, 0.95),
    new THREE.MeshBasicMaterial({ map: rain.texture, toneMapped: false }));
  screen.position.z = 0.056;
  monitor.add(screen);
  at(monitor, -0.75, 1.5, 0.05).rotation.y = 0.4;
  slot.add(monitor);
  slot.add(at(wire(new THREE.BoxGeometry(0.12, 0.35, 0.12), DESK_COLOR, 0.8, deskMats), -0.75, 0.98, 0.05));

  // agente-silueta sentado junto al monitor, en tres cuartos
  const agent = new THREE.Group();
  agent.add(at(wire(new THREE.BoxGeometry(0.7, 0.08, 0.7), "#00ff41", 0.8, agentMats), 0, 0.5, 0));
  agent.add(at(wire(new THREE.BoxGeometry(0.7, 0.7, 0.08), "#00ff41", 0.8, agentMats), 0, 0.9, -0.35));
  agent.add(at(wire(new THREE.BoxGeometry(0.6, 0.75, 0.32), "#00ff41", 0.9, agentMats), 0, 1.0, 0));
  [-0.38, 0.38].forEach(x =>
    agent.add(at(wire(new THREE.BoxGeometry(0.14, 0.14, 0.6), "#00ff41", 0.9, agentMats), x, 1.0, 0.3)));
  const head = new THREE.Group();
  const skull = new THREE.Mesh(new THREE.SphereGeometry(0.26, 8, 6),
    new THREE.MeshBasicMaterial({ color: "#00ff41", wireframe: true, transparent: true, opacity: 0.85 }));
  agentMats.push(skull.material);
  head.add(skull);
  head.add(at(wire(new THREE.BoxGeometry(0.34, 0.07, 0.1), "#00ff41", 1, agentMats), 0, 0.03, 0.24));  // gafas
  at(head, 0, 1.7, 0).rotation.y = -0.9;  // mira hacia el monitor
  agent.add(head);
  at(agent, 0.8, 0, -0.55);
  slot.add(agent);

  // cartel de estado y nombre
  const bubble = new Label(256, 64, 2.5);
  at(bubble.sprite, 0.8, 2.6, -0.55);
  slot.add(bubble.sprite);
  const name = new Label(256, 64, 1.8);
  name.draw(def.name, "#7fd99a", false);
  at(name.sprite, 0, 0.3, 1.1);
  slot.add(name.sprite);

  // selección y zona de clic
  const sel = wire(new THREE.BoxGeometry(3.1, 2.7, 2.0), "#c8ffd6", 0.9, selMats);
  at(sel, 0, 1.3, -0.1);
  selMats.forEach(m => { m.opacity = 0; });
  slot.add(sel);
  const hit = new THREE.Mesh(new THREE.BoxGeometry(3.0, 2.6, 1.9),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }));
  at(hit, 0, 1.3, -0.1).userData.agentId = def.id;
  slot.add(hit);

  return { id: def.id, slot, agent, rain, bubble, name, hit, agentMats, selMats, status: "idle" };
}

export function mountOffice3D(container, opts) {
  const options = opts || {};
  const reducedMotion = !!options.reducedMotion;
  const abort = new AbortController();
  const listen = (target, type, fn, extra) =>
    target.addEventListener(type, fn, Object.assign({ signal: abort.signal }, extra || {}));

  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "low-power" });
  renderer.setClearColor(0x000000, 1);
  const canvas = renderer.domElement;
  canvas.setAttribute("aria-hidden", "true");
  container.appendChild(canvas);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x000000, 14, 40);
  const camera = new THREE.PerspectiveCamera(40, 16 / 9, 0.1, 100);

  // sala: piso con cuadrícula y pared con lluvia grande
  const grid = new THREE.GridHelper(30, 30, 0x00ff41, 0x0a5c1a);
  grid.material.transparent = true;
  grid.material.opacity = 0.45;
  scene.add(grid);
  const wallRain = new Rain(960, 256, 16);
  wallRain.setStyle({ color: "#00ff41", speed: 0.5, alpha: 0.55, blink: false });
  wallRain.warm(40);
  const wall = new THREE.Mesh(new THREE.PlaneGeometry(30, 8),
    new THREE.MeshBasicMaterial({ map: wallRain.texture, toneMapped: false, fog: false }));
  at(wall, 0, 4, -4.2);
  scene.add(wall);

  const slots = AGENTS.map((def, i) => buildSlot(def, i));
  slots.forEach(s => scene.add(s.slot));
  const hitBoxes = slots.map(s => s.hit);

  // cámara orbital mínima (pointer events)
  let az = 0, pol = ORBIT.polarDefault, zoom = 1, fit = fitCamera(16 / 9);
  function placeCamera() {
    const d = fit.distance * zoom;
    camera.position.set(
      TARGET.x + d * Math.sin(pol) * Math.sin(az),
      TARGET.y + d * Math.cos(pol),
      TARGET.z + d * Math.sin(pol) * Math.cos(az));
    camera.lookAt(TARGET);
  }

  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function pick(clientX, clientY) {
    const r = canvas.getBoundingClientRect();
    ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(hitBoxes, false)[0];
    return hit ? hit.object.userData.agentId : null;
  }

  const pointers = new Map();
  let moved = 0, pinch = 0;
  listen(canvas, "pointerdown", e => {
    canvas.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    moved = 0;
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinch = Math.hypot(a.x - b.x, a.y - b.y);
    }
    canvas.style.cursor = "grabbing";
  });
  listen(canvas, "pointermove", e => {
    const prev = pointers.get(e.pointerId);
    if (!prev) {
      canvas.style.cursor = pick(e.clientX, e.clientY) ? "pointer" : "grab";
      return;
    }
    const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch > 0 && d > 0) zoom = clampZoom(zoom * pinch / d);
      pinch = d;
      return;
    }
    moved += Math.abs(dx) + Math.abs(dy);
    const o = clampOrbit(az - dx * 0.005, e.pointerType === "touch" ? pol : pol - dy * 0.005);
    az = o.az; pol = o.pol;
  });
  const release = e => {
    const wasClick = e.type === "pointerup" && pointers.size === 1 && moved < 6;
    pointers.delete(e.pointerId);
    pinch = 0;
    canvas.style.cursor = "grab";
    if (wasClick) {
      const id = pick(e.clientX, e.clientY);
      if (id && options.onSelect) options.onSelect(id);
    }
  };
  listen(canvas, "pointerup", release);
  listen(canvas, "pointercancel", release);
  listen(canvas, "wheel", e => {
    e.preventDefault();
    zoom = clampZoom(zoom * (1 + e.deltaY * 0.001));
  }, { passive: false });
  listen(canvas, "dblclick", () => { az = 0; pol = ORBIT.polarDefault; zoom = 1; });
  listen(canvas, "webglcontextlost", e => {
    e.preventDefault();
    if (options.onLost) options.onLost();
  });

  // tamaño
  function resize() {
    const w = container.clientWidth, h = container.clientHeight;
    if (!w || !h) return;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    fit = fitCamera(camera.aspect);
    camera.fov = fit.fov;
    camera.updateProjectionMatrix();
    placeCamera();
    renderer.render(scene, camera);
  }
  const resizeObs = new ResizeObserver(resize);
  resizeObs.observe(container);

  // bucle: solo corre con la escena visible y la pestaña activa
  let raf = 0, lastFrame = 0, lastRain = 0, visible = true, disposed = false;
  function loop(now) {
    raf = requestAnimationFrame(loop);
    if (now - lastFrame < FRAME_MS) return;
    lastFrame = now;
    if (!reducedMotion) {
      if (now - lastRain >= RAIN_MS) {
        lastRain = now;
        const blinkOn = Math.floor(now / 500) % 2 === 0;
        slots.forEach(s => s.rain.step(blinkOn));
        wallRain.step(true);
      }
      slots.forEach(s => { s.agent.position.y = s.status === "busy" ? Math.sin(now / 400) * 0.02 : 0; });
    }
    placeCamera();
    renderer.render(scene, camera);
  }
  function syncLoop() {
    const want = visible && !document.hidden && !disposed;
    if (want && !raf) raf = requestAnimationFrame(loop);
    if (!want && raf) { cancelAnimationFrame(raf); raf = 0; }
  }
  const visObs = new IntersectionObserver(entries => {
    visible = entries[entries.length - 1].isIntersecting;
    syncLoop();
  });
  visObs.observe(container);
  listen(document, "visibilitychange", syncLoop);

  function update(agents, offline) {
    slots.forEach(s => {
      const v = (agents && agents[s.id]) || { status: "idle", bubble: "Sin datos" };
      const st = styleFor(v.status, !!offline);
      s.status = offline ? "idle" : v.status;
      s.rain.setStyle(st);
      s.agentMats.forEach(m => m.color.set(st.color));
      s.bubble.draw(offline ? "Sin conexión" : v.bubble, st.color, true);
      if (reducedMotion) s.rain.warm(30);
    });
    if (reducedMotion) renderer.render(scene, camera);
  }

  function setSelected(id) {
    slots.forEach(s => {
      const on = s.id === id;
      s.selMats.forEach((m, i) => { m.opacity = on ? (i % 2 === 0 ? 0.9 : 0.27) : 0; });
    });
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    syncLoop();
    abort.abort();
    resizeObs.disconnect();
    visObs.disconnect();
    scene.traverse(o => {
      if (o.geometry) o.geometry.dispose();
      if (o.material && o.material.dispose) o.material.dispose();
    });
    slots.forEach(s => { s.rain.dispose(); s.bubble.dispose(); s.name.dispose(); });
    wallRain.dispose();
    renderer.dispose();
    if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
  }

  canvas.style.cursor = "grab";
  resize();
  update({}, false);
  syncLoop();
  return { update, setSelected, dispose };
}
```

- [ ] **Step 4: Correr los tests**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_3d.py -q`
Expected: PASS. Si `node` falla al importar three (p. ej. `three.module.min.js` con dependencia relativa ausente), corregir la Task 1 (copiar el archivo faltante).

---

### Task 5: Integración en la oficina (contenedor, botones, cargador, respaldo)

**Files:**
- Modify: `app.py` (bloques `OFFICE`: CSS, HTML, JS)
- Test: `tests/test_office_3d.py`

**Interfaces:**
- Consumes: `mountOffice3D` de `/static/office3d.js` (Task 4; el cargador comprueba WebGL con su propio `officeWebGL()` antes de importar); `officeAgentView`, `officeRenderPanel`, `OFFICE_AGENTS`, `officeSelected`, `officeState`, `officeOffline`, `officeEl`, `$` ya existentes.
- Produces: ids `office-3d`, `office-agent-row`, `office-btn-<id>`; funciones JS `officeSelect(id)`, `officeInit3D()`, `officeFallbackToSvg()`, `officeRender3D()`; variable `office3d`.

- [ ] **Step 1: Escribir los tests que fallan** (agregar a `tests/test_office_3d.py`)

```python
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
    assert js.count("officeSelect") >= 3  # definición + onSelect de la escena 3D + listeners de SVG y botones
    assert "const select = () => { officeSelected = id;" not in js  # ya no hay selección en línea


def test_app_py_keeps_pure_crlf_line_endings():
    data = (ROOT / "app.py").read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_3d.py -q`
Expected: FAIL.

- [ ] **Step 3: Parchear `app.py`** (helper de parcheo)

(a) **CSS** — ancla: `  @media (max-width: 520px) { .office-finding { flex-wrap: wrap; } .office-finding-text { flex-basis: 100%; } }\n  /* OFFICE:END */` → agregar antes de `/* OFFICE:END */`:

```css
  #office-3d { width: 100%; aspect-ratio: 16 / 9; max-height: 440px; border-radius: var(--radius-md); overflow: hidden; background: #000; border: 1px solid var(--border); }
  #office-3d canvas { display: block; width: 100%; height: 100%; touch-action: pan-y; }
  #office-3d[hidden], .office-agent-row[hidden] { display: none; }
  .office-agent-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
  .office-agent-btn.selected { border-color: var(--red-light); }
  .office-agent-btn.status-warn { color: #fbbf24; }
  .office-agent-btn.status-error { color: #f87171; }
  @media (max-width: 520px) { #office-3d { aspect-ratio: 4 / 3; } }
```

(b) **HTML** — ancla: `      </svg>\n    </div>\n    <div class="card" id="office-panel"></div>` → insertar entre `</svg>` y el `</div>`:

```html
      </svg>
      <div id="office-3d" hidden></div>
      <div class="office-agent-row" id="office-agent-row" hidden>
        <button type="button" class="btn-sm office-agent-btn" id="office-btn-videos">Videos</button>
        <button type="button" class="btn-sm office-agent-btn" id="office-btn-publica">Publica</button>
        <button type="button" class="btn-sm office-agent-btn" id="office-btn-metricas">Métricas</button>
        <button type="button" class="btn-sm office-agent-btn" id="office-btn-revisor">Revisor</button>
      </div>
    </div>
    <div class="card" id="office-panel"></div>
```

(c) **JS** (todas dentro del bloque `OFFICE`):

1. Ancla `let officeInFlight = false;` → agregar debajo `let office3d = null;` y `let office3dTried = false;`.
2. Ancla `function officeRender(force) {` (se reemplaza la función completa y se agregan las nuevas antes). Texto nuevo, antes de `function officeRender`:

```js
function officeSelect(id) {
  officeSelected = id;
  officeRenderPanel(true);
  if (office3d) office3d.setSelected(id);
  Object.keys(OFFICE_AGENTS).forEach(k => {
    const b = $("office-btn-" + k);
    if (b) b.classList.toggle("selected", k === id);
  });
}

function officeRender3D() {
  if (!office3d) return;
  const agents = {};
  Object.keys(OFFICE_AGENTS).forEach(id => {
    const v = officeAgentView(id);
    agents[id] = { status: v.status, bubble: v.bubble };
    const text = officeOffline ? "Sin conexión" : v.bubble;
    const b = $("office-btn-" + id);
    b.textContent = OFFICE_AGENTS[id].title + " · " + text;
    b.setAttribute("aria-label", OFFICE_AGENTS[id].title + ": " + text);
    b.className = "btn-sm office-agent-btn status-" + (officeOffline ? "idle" : v.status) +
      (id === officeSelected ? " selected" : "");
  });
  office3d.update(agents, officeOffline);
}

function officeFallbackToSvg() {
  if (office3d) {
    try { office3d.dispose(); } catch (e) { /* ya liberada */ }
    office3d = null;
  }
  $("office-3d").hidden = true;
  $("office-agent-row").hidden = true;
  document.querySelector(".office-scene").style.display = "";
}

function officeWebGL() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch (e) {
    return false;
  }
}

async function officeInit3D() {
  if (office3dTried) return;
  office3dTried = true;
  if (!officeWebGL()) return;  // sin WebGL ni se descarga three.js: queda el SVG
  try {
    const mod = await import("/static/office3d.js");
    const box = $("office-3d");
    box.hidden = false;
    $("office-agent-row").hidden = false;
    office3d = mod.mountOffice3D(box, {
      onSelect: officeSelect,
      onLost: officeFallbackToSvg,
      reducedMotion: !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches),
    });
    document.querySelector(".office-scene").style.display = "none";
    office3d.setSelected(officeSelected);
    officeRender(true);
  } catch (e) {
    console.warn("Oficina 3D no disponible, se usa la escena SVG:", e);
    officeFallbackToSvg();
  }
}

function officeRender(force) {
```

3. En el cuerpo de `officeRender`, reemplazar la última línea `  officeRenderPanel(force);\n}\n\nasync function officePoll()` por `  officeRenderPanel(force);\n  officeRender3D();\n}\n\nasync function officePoll()`.
4. En `officeStart`, reemplazar `  officePoll();\n  officeTimer = setInterval(` por `  officePoll();\n  officeInit3D();\n  officeTimer = setInterval(`.
5. Reemplazar el bloque final de listeners (`Object.keys(OFFICE_AGENTS).forEach(id => {\n  const g = $("office-agent-" + id);` … hasta el `});` que lo cierra, justo antes de `/* OFFICE:END */`) por:

```js
Object.keys(OFFICE_AGENTS).forEach(id => {
  const g = $("office-agent-" + id);
  const select = () => officeSelect(id);
  g.addEventListener("click", select);
  g.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(); }
  });
  $("office-btn-" + id).addEventListener("click", select);
});
```

Nota: el `import()` dinámico y las plantillas no usan `{{`. Verificar con el test de bloques Jinja-safe de la oficina (ya existente).

- [ ] **Step 4: Correr los tests**

Run: `venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS (suite completa, incluidos los 55 tests de la oficina).

---

### Task 6: Verificación en el navegador y cierre

**Files:** ninguno nuevo (se corrige lo que aparezca). Scratch solo en el scratchpad.

- [ ] **Step 1: Servidor de verificación con stub**

Como en la fase 1: un servidor de verificación con `/api/office/state` falso (puerto 5077, **no** el 5000 ni el 5055), que sirva `app.HTML` y `/static/` reales. Reutilizar el patrón de stub usado en la verificación de la oficina de la fase 1 (leer `logs`/scratchpad de esa sesión si hace falta); usar credenciales de test del `.env` del proyecto sin imprimirlas. Registrar el servidor con `preview_start` vía `.claude/launch.json` (restaurarlo al terminar).

- [ ] **Step 2: Recorrido (navegador integrado)**

Verificar y anotar el resultado de cada punto:
1. Oficina carga con tema Matrix: fondo negro, lluvia de fondo, tipografía monoespaciada, sin errores en consola (el error preexistente `presets is not iterable` viene del stub genérico y no cuenta).
2. Escena 3D visible: 4 escritorios, siluetas, lluvia en los monitores; el SVG oculto; fila de 4 botones visible.
3. Cambiar el stub entre estados (`busy`, `ok`, `warn`, `error`, `idle`, sin conexión) y ver el color/velocidad del monitor y el texto del globo; el panel del agente sigue funcionando.
4. Clic en cada agente dentro del canvas abre su panel y resalta el marco; los botones de la fila hacen lo mismo; Enter/Espacio en los botones.
5. Arrastrar gira la cámara dentro de los límites, la rueda hace zoom, doble clic restablece.
6. Ancho 375 px: la escena entra entera (`fitCamera`), los botones pasan a varias líneas, sin scroll horizontal.
7. Forzar el respaldo: bloquear `/static/office3d.js` (o simular que no hay WebGL) y ver que reaparece el SVG con tinte verde y la oficina sigue usable.
8. Interruptor "Tema: Clásico": vuelve la apariencia original y se conserva al recargar; "Tema: Matrix" idem.
9. Recorrer Crear video, Generación en lote, Historial y Ajustes en Matrix: buscar rojos fijos, textos blancos sobre verde brillante, bordes o badges ilegibles; corregir con overrides en el bloque `MATRIX` (y volver a pasar los tests de contraste).
10. Pestaña oculta: `requestAnimationFrame` y lluvia de fondo se detienen (comprobar con `document.hidden` simulado o leyendo el contador de frames).

- [ ] **Step 3: Limpieza y suite completa**

Parar el servidor de verificación; restaurar `.claude/launch.json`; borrar archivos temporales del scratchpad (`patch_app.py`, `three_pkg`, stubs). Confirmar `git status`: solo `app.py`, `static/`, `tests/test_office_3d.py`, y los docs.

Run: `venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS completo.

- [ ] **Step 4: Cierre con el usuario**

Informar el resultado y preguntar si quiere (a) reiniciar el servidor del puerto 5000 con el código nuevo (los archivos `static/` ya se sirven sin reiniciar, pero `app.py` sí lo necesita) y (b) commit y push. No hacer ninguno sin su confirmación.
