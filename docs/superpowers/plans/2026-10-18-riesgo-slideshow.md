# Riesgo de slideshow (solo avisar) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Puntuar cada video del lote (¿se ve como un slideshow?) justo después de armar su timeline, guardar el puntaje en el ítem y mostrar una alerta del Revisor en la oficina cuando salga "revise" o "fail". No bloquea ni cambia el render.

**Architecture:** Módulo puro `slideshow_risk.py` (timeline de `video_maker.build_props` → puntaje 0-5 por 4 dimensiones, promedio y veredicto). `batch_pipeline._generate_batch_video` lo calcula con el timeline que ya existe y lo guarda en `video["slideshow_risk"]`. `office_state` agrega un hallazgo `slideshow:{proyecto}:{índice}` (warn) para ítems `ready` con veredicto malo.

**Tech Stack:** Python 3.10, pytest. Idea tomada de `lib/slideshow_risk.py` de OpenMontage (AGPL-3.0): se reimplementa con código propio, no se copia.

**Spec:** decisiones acordadas con el usuario en la conversación (2026-10-18): acción = solo avisar; alcance = todos los lotes. Señales ajustadas a lo que existe en el esquema de escena (`video/src/schema.ts`: `type`, `kenBurns`, `startFrame`/`endFrame`). La "repetición de dirección de Ken Burns" se descartó como señal: `video_maker._build_kenburns_sequence` ya recorre un patrón cíclico.

## Global Constraints

- Textos al usuario en español, tono de los existentes ("ítem 3", "Abrir lotes").
- Veredictos y umbrales (iguales a OpenMontage): promedio `< 2.0` strong, `< 3.0` acceptable, `< 4.0` revise, `>= 4.0` fail. Puntajes por dimensión de 0 a 5, menor es mejor.
- El cálculo nunca puede romper la generación: en `batch_pipeline` va dentro de `try/except` y solo registra con `logger`.
- `office_state.py` sigue siendo solo lectura.
- `app.py` no se toca. `batch_pipeline.py` y `tests/test_office_state.py` ya pueden traer cambios sin commitear del usuario: editar solo lo indicado, nunca revertir ni reformatear. Archivos nuevos y `office_state.py` en LF; no hacer `git add` de archivos ajenos.
- Tests: `python -m pytest <archivo> -q` desde la raíz.

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `slideshow_risk.py` (crear) | `expects_motion(video_settings)`, `score_timeline(timeline, expects_motion)`. Puro, sin red ni disco. |
| `tests/test_slideshow_risk.py` (crear) | Pruebas del módulo anterior. |
| `office_state.py` (modificar) | `_slideshow_findings` + enganche en `_revisor_findings`. |
| `tests/test_office_state.py` (modificar) | Pruebas del hallazgo. |
| `batch_pipeline.py` (modificar) | `_slideshow_summary(timeline, vs)` y su uso en `_generate_batch_video`. |
| `tests/test_slideshow_summary.py` (crear) | Prueba de `_slideshow_summary`. |

---

### Task 1: `slideshow_risk.py`

**Files:**
- Create: `slideshow_risk.py`
- Test: `tests/test_slideshow_risk.py`

**Interfaces:**
- Produces: `expects_motion(video_settings: dict) -> bool` (True si `generate_video_clips` es verdadero —por defecto True— y ni `provider` ni `image_provider` son `"flow"`; Flow solo genera imágenes). `score_timeline(timeline: dict, expects_motion: bool = False) -> dict` con la forma `{"average": float, "verdict": "strong"|"acceptable"|"revise"|"fail", "dimensions": {nombre: {"score": float, "reason": str}}}`. Dimensiones: `static_scenes`, `low_motion`, `long_hold`, `slow_cuts`. Una timeline sin escenas devuelve `average 5.0`, `fail` y `dimensions {}`.

- [ ] **Step 1: Write the failing tests** — crear `tests/test_slideshow_risk.py`:

```python
# -*- coding: utf-8 -*-
"""Riesgo de slideshow: puro sobre timelines falsos."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import slideshow_risk as sr

FPS = 30


def scene(seconds, kind="image", ken="zoomIn", start=0):
    s = {"src": "a.jpg", "type": kind, "startFrame": start, "endFrame": start + round(seconds * FPS)}
    if kind == "image":
        s["kenBurns"] = ken
    return s


def timeline(*scenes):
    laid, cursor = [], 0
    for sc in scenes:
        sc = dict(sc, startFrame=cursor, endFrame=cursor + (sc["endFrame"] - sc["startFrame"]))
        cursor = sc["endFrame"]
        laid.append(sc)
    return {"scenes": laid}


def test_a_lively_video_is_strong():
    tl = timeline(*[scene(4.5) for _ in range(8)])
    risk = sr.score_timeline(tl)
    assert risk["verdict"] == "strong" and risk["average"] < 2.0
    assert set(risk["dimensions"]) == {"static_scenes", "low_motion", "long_hold", "slow_cuts"}
    assert all(0 <= d["score"] <= 5 and d["reason"] for d in risk["dimensions"].values())


def test_static_images_score_high_on_static_scenes():
    tl = timeline(*[scene(5, ken="none") for _ in range(6)])
    assert sr.score_timeline(tl)["dimensions"]["static_scenes"]["score"] == 5.0
    moving = timeline(*[scene(5, ken="panLeft") for _ in range(6)])
    assert sr.score_timeline(moving)["dimensions"]["static_scenes"]["score"] == 0.0


def test_low_motion_only_counts_when_motion_was_expected():
    tl = timeline(*[scene(5) for _ in range(6)])
    assert sr.score_timeline(tl, expects_motion=False)["dimensions"]["low_motion"]["score"] == 0.0
    assert sr.score_timeline(tl, expects_motion=True)["dimensions"]["low_motion"]["score"] == 5.0
    mixed = timeline(*[scene(5, kind="video") for _ in range(3)], *[scene(5) for _ in range(3)])
    assert sr.score_timeline(mixed, expects_motion=True)["dimensions"]["low_motion"]["score"] == 0.0


def test_long_hold_and_slow_cuts_scale_with_duration():
    ok = sr.score_timeline(timeline(*[scene(6) for _ in range(5)]))["dimensions"]
    assert ok["long_hold"]["score"] == 0.0
    slow = sr.score_timeline(timeline(*[scene(12) for _ in range(3)]))["dimensions"]
    assert slow["long_hold"]["score"] == 5.0 and slow["slow_cuts"]["score"] == 5.0
    video_only = sr.score_timeline(timeline(scene(12, kind="video")))["dimensions"]
    assert video_only["long_hold"]["score"] == 0.0  # una escena de video larga no es un "hold" de imagen


def test_worst_case_fails_and_thresholds_map_to_verdicts():
    bad = timeline(*[scene(14, ken="none") for _ in range(3)])
    assert sr.score_timeline(bad, expects_motion=True)["verdict"] == "fail"
    assert [sr._verdict(x) for x in (1.99, 2.0, 2.99, 3.0, 3.99, 4.0)] == [
        "strong", "acceptable", "acceptable", "revise", "revise", "fail"]


def test_empty_timeline_fails_without_dimensions():
    assert sr.score_timeline({"scenes": []}) == {"average": 5.0, "verdict": "fail", "dimensions": {}}
    assert sr.score_timeline({})["verdict"] == "fail"


def test_expects_motion_follows_the_settings():
    assert sr.expects_motion({}) is True
    assert sr.expects_motion({"generate_video_clips": False}) is False
    assert sr.expects_motion({"provider": "flow"}) is False
    assert sr.expects_motion({"image_provider": "flow"}) is False
    assert sr.expects_motion({"provider": "whatsapp", "generate_video_clips": True}) is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_slideshow_risk.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'slideshow_risk'`).

- [ ] **Step 3: Implement** — crear `slideshow_risk.py`:

```python
# -*- coding: utf-8 -*-
"""Riesgo de "slideshow": cuanto se parece un video a imagenes fijas pasando, en vez de un video dirigido.

Puntua la timeline que arma video_maker.build_props (escenas con type, kenBurns, startFrame/endFrame)
en 4 dimensiones de 0 a 5 (menor es mejor). Pura: sin red ni disco. Idea tomada de OpenMontage
(lib/slideshow_risk.py, AGPL-3.0); implementacion propia sobre el esquema de escenas de este proyecto.

  static_scenes : escenas de imagen sin movimiento (kenBurns "none")
  low_motion    : pocos clips de video reales cuando se esperaba movimiento
  long_hold     : una imagen se queda demasiado tiempo en pantalla
  slow_cuts     : promedio de segundos por escena demasiado alto

Veredicto por promedio: < 2.0 strong, < 3.0 acceptable, < 4.0 revise, >= 4.0 fail."""

FPS = 30
LONG_HOLD_OK, LONG_HOLD_WORST = 6.0, 12.0   # segundos que una imagen puede quedarse / desde donde puntua 5
SLOW_CUT_OK, SLOW_CUT_WORST = 5.0, 10.0     # segundos promedio por escena
MOTION_TARGET = 0.5                         # proporcion de clips de video bajo la cual low_motion empieza a puntuar


def expects_motion(video_settings: dict) -> bool:
    """Flow solo genera imagenes: ahi la ausencia de clips de video no es una degradacion."""
    vs = video_settings or {}
    if "flow" in (vs.get("provider"), vs.get("image_provider")):
        return False
    return bool(vs.get("generate_video_clips", True))


def _verdict(average: float) -> str:
    if average < 2.0:
        return "strong"
    if average < 3.0:
        return "acceptable"
    if average < 4.0:
        return "revise"
    return "fail"


def _seconds(scene: dict) -> float:
    return max(scene["endFrame"] - scene["startFrame"], 1) / FPS


def _scale(value: float, ok: float, worst: float) -> float:
    if value <= ok:
        return 0.0
    return min(5.0, 5.0 * (value - ok) / (worst - ok))


def score_timeline(timeline: dict, expects_motion: bool = False) -> dict:
    scenes = (timeline or {}).get("scenes") or []
    if not scenes:
        return {"average": 5.0, "verdict": "fail", "dimensions": {}}
    n = len(scenes)
    images = [s for s in scenes if s.get("type") != "video"]
    videos = n - len(images)

    static = sum(1 for s in images if s.get("kenBurns") in (None, "none"))
    longest = max((_seconds(s) for s in images), default=0.0)
    average_seconds = sum(_seconds(s) for s in scenes) / n
    video_share = videos / n

    dimensions = {
        "static_scenes": (5.0 * static / n, f"{static} de {n} escenas sin movimiento"),
        "low_motion": (
            5.0 * max(0.0, MOTION_TARGET - video_share) / MOTION_TARGET if expects_motion else 0.0,
            f"{videos} de {n} escenas son video real" if expects_motion else "no se esperaba movimiento real",
        ),
        "long_hold": (_scale(longest, LONG_HOLD_OK, LONG_HOLD_WORST), f"la imagen más larga dura {longest:.1f} s"),
        "slow_cuts": (_scale(average_seconds, SLOW_CUT_OK, SLOW_CUT_WORST), f"{average_seconds:.1f} s por escena en promedio"),
    }
    result = {name: {"score": round(score, 2), "reason": reason} for name, (score, reason) in dimensions.items()}
    average = round(sum(d["score"] for d in result.values()) / len(result), 2)
    return {"average": average, "verdict": _verdict(average), "dimensions": result}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_slideshow_risk.py -q`
Expected: 7 passed.

---

### Task 2: Hallazgo del Revisor en `office_state`

**Files:**
- Modify: `office_state.py` (junto a `_flow_findings` y en `_revisor_findings`)
- Test: `tests/test_office_state.py`

**Interfaces:**
- Consumes: `video["slideshow_risk"] = {"average": float, "verdict": str, "dimensions": {nombre: score}}` (lo escribe la Task 3).
- Produces: hallazgo `slideshow:{project_id}:{index}`, agente `revisor`, severidad `warn`, texto `"{página}: ítem {n} se ve como slideshow ({riesgo} — {causa})"` con riesgo `medio` (revise) o `alto` (fail) y causa = la dimensión con mayor puntaje (`escenas sin movimiento`, `pocos clips con movimiento`, `escenas muy largas`, `cortes muy lentos`), acción `_view("Abrir lotes", "lote")`. Solo ítems `ready` de lotes activos.

- [ ] **Step 1: Write the failing tests** — agregar a `tests/test_office_state.py` (junto a las demás pruebas puras, usando los helpers `vid`, `proj`, `data`, `ids`, `NOW` que ya existen):

```python
def _risky(verdict, **scores):
    dims = {"static_scenes": 0.0, "low_motion": 0.0, "long_hold": 0.0, "slow_cuts": 0.0, **scores}
    return {"average": 3.5, "verdict": verdict, "dimensions": dims}


def test_slideshow_finding_for_a_ready_item_with_a_bad_verdict():
    v = vid("ready")
    v["slideshow_risk"] = _risky("revise", static_scenes=5.0)
    state = os_.build_state(data(projects=[proj([v])]), NOW)
    finding = next(f for f in state["findings"] if f["id"] == "slideshow:p1:0")
    assert finding["agent"] == "revisor" and finding["severity"] == "warn"
    assert finding["text"] == "MACRAME CREATIVO: ítem 1 se ve como slideshow (riesgo medio — escenas sin movimiento)"
    assert finding["action"] == {"label": "Abrir lotes", "kind": "open_view", "target": "lote"}
    v["slideshow_risk"] = _risky("fail", slow_cuts=5.0)
    text = next(f for f in os_.build_state(data(projects=[proj([v])]), NOW)["findings"] if f["id"] == "slideshow:p1:0")["text"]
    assert "riesgo alto — cortes muy lentos" in text


def test_no_slideshow_finding_for_good_verdicts_other_statuses_or_missing_data():
    for verdict in ("strong", "acceptable"):
        v = vid("ready")
        v["slideshow_risk"] = _risky(verdict)
        assert "slideshow:p1:0" not in ids(os_.build_state(data(projects=[proj([v])]), NOW))
    published = vid("published")
    published["slideshow_risk"] = _risky("fail", static_scenes=5.0)
    assert "slideshow:p1:0" not in ids(os_.build_state(data(projects=[proj([published])]), NOW))
    assert "slideshow:p1:0" not in ids(os_.build_state(data(projects=[proj([vid("ready")])]), NOW))
    broken = vid("ready")
    broken["slideshow_risk"] = "mal"
    assert "slideshow:p1:0" not in ids(os_.build_state(data(projects=[proj([broken])]), NOW))
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_office_state.py -q -k slideshow`
Expected: FAIL (`StopIteration`, no existe el hallazgo).

- [ ] **Step 3: Implement** — en `office_state.py`, antes de `_stuck_findings`:

```python
SLIDESHOW_CAUSES = {"static_scenes": "escenas sin movimiento", "low_motion": "pocos clips con movimiento",
                    "long_hold": "escenas muy largas", "slow_cuts": "cortes muy lentos"}


def _slideshow_findings(data: dict) -> list:
    """Videos listos que el puntaje de slideshow (ver slideshow_risk) marco 'revise' o 'fail'."""
    findings = []
    for project, video in _items(data.get("projects", [])):
        risk = video.get("slideshow_risk")
        if video.get("status") != "ready" or not isinstance(risk, dict) or risk.get("verdict") not in ("revise", "fail"):
            continue
        dims = risk.get("dimensions") if isinstance(risk.get("dimensions"), dict) else {}
        scored = {k: v for k, v in dims.items() if k in SLIDESHOW_CAUSES and isinstance(v, (int, float))}
        cause = f" — {SLIDESHOW_CAUSES[max(scored, key=scored.get)]}" if scored else ""
        level = "alto" if risk["verdict"] == "fail" else "medio"
        findings.append(_finding(
            f"slideshow:{project['id']}:{video['index']}", "revisor", "warn",
            f"{_page(project)}: ítem {video['index'] + 1} se ve como slideshow (riesgo {level}{cause})",
            _view("Abrir lotes", "lote")))
    return findings
```

Y en `_revisor_findings`, agregar `+ _slideshow_findings(data)` a la línea `findings += _token_findings(...) + ...` (al final de la cadena).

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_office_state.py -q`
Expected: todo PASS (los tests previos siguen verdes; el recuento "5 alertas" de `test_revisor_summarises_all_findings` no cambia porque esos ítems no tienen `slideshow_risk`).

---

### Task 3: Guardar el puntaje al generar cada video

**Files:**
- Modify: `batch_pipeline.py` (import de módulos al inicio; `_generate_batch_video` ~l.1394-1440)
- Test: `tests/test_slideshow_summary.py` (crear)

**Interfaces:**
- Consumes: `slideshow_risk.score_timeline`, `slideshow_risk.expects_motion`.
- Produces: `_slideshow_summary(timeline: dict, vs: dict) -> dict` = `{"average": float, "verdict": str, "dimensions": {nombre: score_float}}`; y `video["slideshow_risk"]` en el ítem al quedar `ready`.

- [ ] **Step 1: Write the failing test** — crear `tests/test_slideshow_summary.py`:

```python
# -*- coding: utf-8 -*-
"""_slideshow_summary: lo que el lote guarda en cada video (solo puntajes, sin textos)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline


def tl(*seconds, ken="zoomIn"):
    scenes, cursor = [], 0
    for s in seconds:
        scenes.append({"src": "a.jpg", "type": "image", "kenBurns": ken, "startFrame": cursor, "endFrame": cursor + round(s * 30)})
        cursor += round(s * 30)
    return {"scenes": scenes}


def test_summary_keeps_only_average_verdict_and_numeric_scores():
    summary = batch_pipeline._slideshow_summary(tl(5, 5, 5, 5), {})
    assert set(summary) == {"average", "verdict", "dimensions"}
    assert set(summary["dimensions"]) == {"static_scenes", "low_motion", "long_hold", "slow_cuts"}
    assert all(isinstance(v, float) for v in summary["dimensions"].values())


def test_summary_uses_the_video_settings_to_decide_if_motion_was_expected():
    still = tl(5, 5, 5, 5)
    assert batch_pipeline._slideshow_summary(still, {"generate_video_clips": True})["dimensions"]["low_motion"] == 5.0
    assert batch_pipeline._slideshow_summary(still, {"generate_video_clips": False})["dimensions"]["low_motion"] == 0.0
    assert batch_pipeline._slideshow_summary(still, {"provider": "flow"})["dimensions"]["low_motion"] == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_slideshow_summary.py -q`
Expected: FAIL (`AttributeError: module 'batch_pipeline' has no attribute '_slideshow_summary'`).

- [ ] **Step 3: Implement** — en `batch_pipeline.py`:

1. Junto a los demás imports de módulos propios (donde está `import auto_pipeline`/`import video_maker`), agregar `import slideshow_risk`.
2. Antes de `_generate_batch_video`, agregar:

```python
def _slideshow_summary(timeline: dict, vs: dict) -> dict:
    """Puntaje de slideshow del video recien armado, en la forma compacta que se guarda en el item."""
    risk = slideshow_risk.score_timeline(timeline, expects_motion=slideshow_risk.expects_motion(vs))
    return {"average": risk["average"], "verdict": risk["verdict"],
            "dimensions": {name: d["score"] for name, d in risk["dimensions"].items()}}
```

3. En `_generate_batch_video`: justo antes de `def _do_render():` agregar `risk_box: dict = {}`; dentro de `_do_render`, entre `if not timeline: return None` y `return video_maker.render_props(`, agregar:

```python
                try:
                    risk_box["risk"] = _slideshow_summary(timeline, vs)
                except Exception:
                    logger.exception("batch %s video %d: no se pudo puntuar el riesgo de slideshow", project_id, index)
```

4. En el bloque final donde el ítem pasa a `ready` (`v["status"] = "ready"` … `v["audio_path"] = str(audio_path)`), agregar a continuación de `v["audio_path"] = str(audio_path)`:

```python
        if risk_box.get("risk"):
            v["slideshow_risk"] = risk_box["risk"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_slideshow_summary.py tests/test_slideshow_risk.py tests/test_office_state.py -q`
Expected: todo PASS. Luego `python -c "import batch_pipeline"` sin error (la edición del cuerpo de `_generate_batch_video` no tiene prueba unitaria: se verifica por lectura y por el import).

- [ ] **Step 5: Suite completa**

Run: `python -m pytest -q`
Expected: todo PASS.

---

## Riesgos

- **Umbrales sin calibrar con videos reales.** Los valores (6 s/12 s de hold, 5 s/10 s por escena, 50 % de video) son una primera estimación. Un video Luna de ~42 s con 5 escenas de ~8 s queda "strong" (≈1.4); uno con `animate_images` apagado y sin clips de video queda en "revise". Si las alertas salen demasiado o muy pocas, se ajustan las constantes de `slideshow_risk.py` sin tocar nada más.
- **Solo videos.** Los posts de imagen (howbig, niño selectivo, etc.) no pasan por `build_props`, así que no tienen puntaje.
- **Los videos ya generados no tienen puntaje** (campo ausente → sin alerta). Solo aplica a los que se generen desde ahora.

## Self-Review

- **Cobertura:** puntaje puro (T1), alerta del Revisor (T2), guardado al generar (T3), solo avisar sin bloquear, todos los lotes.
- **Sin placeholders:** todo el código y las pruebas están completos.
- **Consistencia:** `score_timeline`/`expects_motion` (T1) → `_slideshow_summary` (T3) → `video["slideshow_risk"]` con `dimensions` como `{nombre: float}` → `_slideshow_findings` (T2) lee ese mismo formato.
