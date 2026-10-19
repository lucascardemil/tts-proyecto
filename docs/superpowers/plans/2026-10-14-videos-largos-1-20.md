# Videos de más de 1:20 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Todos los videos con guion (HISTORIAS, Niño Selectivo, MACRAME CREATIVO, Bebé Héroe) duran más de 1:20 (objetivo ~90 s, 260-290 palabras, 16-20 imágenes), con una comprobación de la duración real del audio.

**Architecture:** Constantes y validadores nuevos en `auto_pipeline.py`; el lote (`batch_pipeline._generate_batch_video`) y la ruta manual (`app.py::_run_pipeline_job`) los llaman; Bebé Héroe sale de su rama rígida de 18 s y usa el flujo común (voz de niño + `video_maker.build_props`); los 4 prompts de sistema piden el formato largo. Los errores nuevos son "sanables" (el lote regenera solo).

**Tech Stack:** Python 3.10 (Flask, pytest), prompts `.md`, Remotion (sin cambios).

**Spec:** `docs/superpowers/specs/2026-10-14-videos-largos-1-20-design.md`

## Global Constraints

- Duración objetivo ~90 s; ningún video por debajo de `MIN_VIDEO_SECONDS = 80`. Guion de **250 a 300 palabras** aceptadas (los prompts piden **260 a 290**); **16 a 20 imágenes** pedidas, **14 a 22** aceptadas.
- Gaming (clips de Medal), posts de imagen y memes quedan FUERA. Videos ya generados/programados no cambian.
- Archivos CRLF puros: `app.py`, `auto_pipeline.py`, `tests/test_pilares_de_valor.py`. Se parchean con scripts a nivel de bytes que aseguran cada ancla una sola vez y comprueban `count(b"\n") == count(b"\r\n")`. LF: `batch_pipeline.py`, `seo_optimizer.py`, `prompts/*.md`, los demás tests.
- HTML de `app.py` (`HTML = r"""..."""`) pasa por Jinja: sin `{{`, `{%`, `{#` ni `"""` en lo agregado.
- La herramienta de escritura a veces corrompe barras invertidas y saltos de línea (`\b` → byte 0x08, `\n` duplicado): tras escribir, escanear los archivos tocados en busca de bytes de control (`<9`, 11, 12) y comprobar que los literales con `\n` sigan siendo la secuencia de dos caracteres.
- No hacer commits (regla del usuario: commit/push solo a pedido). No tocar el servidor del puerto 5000 ni el de 5055 ni macrame-bot.
- Mensajes de error sanables (marcadores en minúscula, `_is_healable_error` baja a minúsculas): `guion fuera de largo`, `imágenes fuera de rango`, `video más corto que 1:20`.
- Suite completa en verde al final de cada tarea: `python -m pytest tests -q`.

## File Structure

- `auto_pipeline.py` — constantes, `validate_long_script_length`, `validate_image_count`, `count_image_blocks`, `validate_min_duration`, `validate_long_story_text`, `LONG_KINDS`, `LONG_FORMAT_REQUEST`; ajusta `validate_historia_script`, `build_trigger_message` y los `*_SHEET_REQUEST`.
- `batch_pipeline.py` — cadena de texto (`_generate_text_with_chain`), marcadores sanables, `_generate_batch_video` (Bebé Héroe al flujo común, guardia de duración), borra `_has_fixed_length`, `BEBE_HEROE_SCENES`, `BEBE_HEROE_MIN_WORDS`, `bebe_script_from_story`; `RESCUE_STORY_CRAFT`.
- `app.py` — `_run_pipeline_job` (mismas validaciones, Bebé Héroe al flujo común) y nota en los selectores de duración.
- `prompts/historias_system.md`, `ninio_selectivo_system.md`, `macrame_system.md`, `bebe_heroe_system.md` — formato largo.
- Tests: `tests/test_long_videos.py` (nuevo), y ajustes en `test_historias_formato_luna.py`, `test_prompts_english.py`, `test_bebe_heroe.py`, `test_text_chain.py`, `test_pilares_de_valor.py` si hace falta.

---

### Task 1: Constantes, validadores y pedido de formato largo (`auto_pipeline.py`)

**Files:**
- Modify: `auto_pipeline.py` (CRLF)
- Create: `tests/test_long_videos.py`
- Modify: `tests/test_historias_formato_luna.py` (usa `HISTORIA_MIN_WORDS/MAX_WORDS` y el texto GOOD de 122 palabras)

**Interfaces:**
- Produces (todas en `auto_pipeline`):
  - `MIN_VIDEO_SECONDS = 80`, `LONG_SCRIPT_MIN_WORDS = 250`, `LONG_SCRIPT_MAX_WORDS = 300`, `LONG_IMAGES_MIN = 14`, `LONG_IMAGES_MAX = 22`
  - `LONG_KINDS = frozenset({"historias", "ninio_selectivo", "macrame", "bebe_heroe"})`
  - `validate_long_script_length(script: str) -> None` — `PipelineError("Guion fuera de largo: N palabras (deben ser 250-300 para durar mas de 1:20)")`
  - `validate_image_count(n: int) -> None` — `PipelineError("Imágenes fuera de rango: N (deben ser 14-22)")`
  - `count_image_blocks(text: str) -> int` — número de bloques "Imagen N / Frase / Prompt" del texto crudo (sin escribir scratch)
  - `validate_min_duration(seconds: float) -> None` — `PipelineError("Video más corto que 1:20: el audio dura N s (minimo 80 s)")`
  - `validate_long_story_text(story_text: str, story_id: str) -> None` — extrae el guion como `validate_historia_story_text` (bloque "Guion" o frases de las imágenes), llama `validate_long_script_length` y `validate_image_count`
  - `LONG_FORMAT_REQUEST` (texto que se agrega al mensaje) y `build_trigger_message` que lo agrega para los `LONG_KINDS`
- Modifies: `validate_historia_script` ya no usa `HISTORIA_MIN_WORDS/MAX_WORDS` (se eliminan): el rango de palabras sale de `LONG_SCRIPT_*`.

- [ ] **Step 1: Escribir los tests (rojo)** en `tests/test_long_videos.py`:

```python
# -*- coding: utf-8 -*-
"""Videos de mas de 1:20: constantes, validadores y pedido de formato largo."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import auto_pipeline as ap


def _words(n):
    return " ".join(["palabra"] * n)


def test_constants_guarantee_more_than_80_seconds():
    assert ap.MIN_VIDEO_SECONDS == 80
    assert ap.LONG_SCRIPT_MIN_WORDS == 250 and ap.LONG_SCRIPT_MAX_WORDS == 300
    assert (ap.LONG_IMAGES_MIN, ap.LONG_IMAGES_MAX) == (14, 22)
    # a 189 palabras/min (la voz mas rapida observada) el minimo de palabras aun pasa de ~79 s
    assert ap.LONG_SCRIPT_MIN_WORDS / 189 * 60 > 79
    assert ap.LONG_KINDS == {"historias", "ninio_selectivo", "macrame", "bebe_heroe"}


@pytest.mark.parametrize("n", [250, 275, 300])
def test_script_length_accepts_the_range(n):
    ap.validate_long_script_length(_words(n))


@pytest.mark.parametrize("n", [40, 150, 249, 301, 340])
def test_script_length_rejects_outside_the_range(n):
    with pytest.raises(ap.PipelineError, match="(?i)guion fuera de largo"):
        ap.validate_long_script_length(_words(n))


@pytest.mark.parametrize("n", [14, 16, 20, 22])
def test_image_count_accepts_the_range(n):
    ap.validate_image_count(n)


@pytest.mark.parametrize("n", [0, 5, 8, 13, 23, 30])
def test_image_count_rejects_outside_the_range(n):
    with pytest.raises(ap.PipelineError, match="(?i)imágenes fuera de rango"):
        ap.validate_image_count(n)


def test_min_duration():
    ap.validate_min_duration(82.0)
    ap.validate_min_duration(80.0)
    with pytest.raises(ap.PipelineError, match="(?i)video más corto que 1:20"):
        ap.validate_min_duration(70.4)


def _story_text(n_images, script_words):
    blocks = "".join(
        f"Imagen {i}\nFrase del guion: «Frase {i}.»\nPrompt: A calm scene number {i}.\n\n" for i in range(1, n_images + 1)
    )
    return f"HOOK_TEXT: Gancho 🐕\n\nPERSONAJES:\n[PERRO]: a dog\n\nGuion\n{_words(script_words)}\n\n{blocks}"


def test_count_image_blocks_counts_real_blocks():
    assert ap.count_image_blocks(_story_text(18, 270)) == 18
    assert ap.count_image_blocks("sin bloques") == 0


def test_long_story_text_validates_script_and_images(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "SCRATCH_DIR", tmp_path)
    ap.validate_long_story_text(_story_text(18, 270), "t1")
    with pytest.raises(ap.PipelineError, match="(?i)fuera de largo"):
        ap.validate_long_story_text(_story_text(18, 120), "t2")
    with pytest.raises(ap.PipelineError, match="(?i)fuera de rango"):
        ap.validate_long_story_text(_story_text(8, 270), "t3")


@pytest.mark.parametrize("kind", ["historias", "ninio_selectivo", "macrame", "bebe_heroe"])
def test_trigger_message_asks_for_the_long_format_and_ignores_duration(kind):
    msg = ap.build_trigger_message("dame una historia", 30, kind=kind)
    assert "260 y 290 palabras" in msg and "16 y 20 imagenes" in msg
    assert "aproximadamente" not in msg  # la duracion elegida en el panel no cambia el guion largo


def test_trigger_message_keeps_the_duration_for_unknown_kinds():
    msg = ap.build_trigger_message("dame una historia", 30)
    assert "aproximadamente" in msg and "260 y 290" not in msg
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `python -m pytest tests/test_long_videos.py -q`
Expected: FAIL (`AttributeError: ... MIN_VIDEO_SECONDS` etc.).

- [ ] **Step 3: Implementar en `auto_pipeline.py`** (parche por bytes, CRLF; leer antes los bloques "SPANISH_WORDS_PER_MINUTE", `build_trigger_message`, `HISTORIA_*`/`validate_historia_script`, y `has_image_blocks`):

  1. Junto a `SPANISH_WORDS_PER_MINUTE`, agregar (con comentarios en español como el resto):

```python
MIN_VIDEO_SECONDS = 80  # todos los videos con guion duran mas de 1:20
LONG_SCRIPT_MIN_WORDS = 250  # a 189 palabras/min (la voz mas rapida observada) son ~79 s; el prompt pide 260-290
LONG_SCRIPT_MAX_WORDS = 300
LONG_IMAGES_MIN = 14  # el prompt pide 16-20; el margen evita rechazar por una imagen de mas o de menos
LONG_IMAGES_MAX = 22
LONG_KINDS = frozenset({"historias", "ninio_selectivo", "macrame", "bebe_heroe"})  # kinds de video con guion de ~90 s
```

  2. Cerca de `validate_script`, agregar las funciones. `_long_script_problem(text) -> Optional[str]` devuelve el texto del problema (o None) y `validate_long_script_length` lo lanza como `PipelineError`:

```python
def _long_script_problem(script: str) -> Optional[str]:
    words = len(script.split())
    if LONG_SCRIPT_MIN_WORDS <= words <= LONG_SCRIPT_MAX_WORDS:
        return None
    return (f"Guion fuera de largo: {words} palabras "
            f"(deben ser {LONG_SCRIPT_MIN_WORDS}-{LONG_SCRIPT_MAX_WORDS} para durar mas de 1:20)")


def validate_long_script_length(script: str) -> None:
    problem = _long_script_problem(script)
    if problem:
        raise PipelineError(problem)


def validate_image_count(n: int) -> None:
    if not LONG_IMAGES_MIN <= n <= LONG_IMAGES_MAX:
        raise PipelineError(f"Imágenes fuera de rango: {n} (deben ser {LONG_IMAGES_MIN}-{LONG_IMAGES_MAX})")


def validate_min_duration(seconds: float) -> None:
    if seconds < MIN_VIDEO_SECONDS:
        raise PipelineError(
            f"Video más corto que 1:20: el audio dura {seconds:.0f} s (minimo {MIN_VIDEO_SECONDS} s)")
```

  3. Junto a `has_image_blocks`: `def count_image_blocks(text: str) -> int: return len(_IMAGE_BLOCK_RE.findall(text.replace("**", "")))`.
  4. `validate_historia_script`: borrar `HISTORIA_MIN_WORDS`/`HISTORIA_MAX_WORDS` y su comparación; en su lugar `length_problem = _long_script_problem(text)` y, si no es None, `problems.append(length_problem)`. Actualizar su docstring ("~90 s (260-290 palabras)"). El mensaje final sigue empezando con `"Guion de historia fuera de formato: "`.
  5. Junto a `validate_historia_story_text`, agregar `validate_long_story_text(story_text, story_id)`: obtiene el guion igual que esa función (extract_script; si queda vacío, frases de las imágenes), llama `validate_long_script_length(script)` y `validate_image_count(count_image_blocks(story_text))`.
  6. `LONG_FORMAT_REQUEST`: constante de texto que EMPIEZA con los dos saltos de línea igual que `CHARACTER_SHEET_REQUEST` (copiar su forma de escribirlos) y dice, en español sin acentos como las otras: "FORMATO LARGO (obligatorio): el video dura unos 90 segundos. La narracion tiene entre 260 y 290 palabras (cuenta las palabras antes de responder: si te quedas corto, desarrolla mas el conflicto o el reencuentro, nunca rellenes con repeticiones) y la historia lleva entre 16 y 20 imagenes (un bloque Imagen N por cada una, una cada 1 o 2 frases)." Debe contener literalmente las subcadenas `260 y 290 palabras` y `16 y 20 imagenes`.
  7. `build_trigger_message`: si `kind in LONG_KINDS` → NO agregar la meta de palabras por duración (ignorar `duration_seconds`) y, tras las fichas, agregar `LONG_FORMAT_REQUEST`; para los otros kinds, el comportamiento actual. Actualizar el docstring.
  8. Fichas: en `CHARACTER_SHEET_REQUEST` cambiar "de 40 a 60 palabras" por "de 90 a 120 palabras" (hoy solo lo usa Bebé Héroe); en `NINIO_SHEET_REQUEST` y `MACRAME_SHEET_REQUEST` cambiar "la misma en las 5 imagenes" por "la misma en todas las imagenes" y "todas las imagenes ocurren en esa misma cocina/sala" se mantiene. No tocar `HISTORIAS_SHEET_REQUEST` salvo que mencione un número de imágenes.

- [ ] **Step 4: Ajustar `tests/test_historias_formato_luna.py`**: el texto `GOOD` (122 palabras) pasa a ser un guion de 260-290 palabras que cumple el cierre de corazón (alargarlo con frases coherentes; reescribir el assert `ap.HISTORIA_MIN_WORDS <= n <= ap.HISTORIA_MAX_WORDS` como `ap.LONG_SCRIPT_MIN_WORDS <= n <= ap.LONG_SCRIPT_MAX_WORDS`), y los casos "demasiado corto"/"demasiado largo" usan los límites nuevos (p. ej. 120 y 340 palabras). Dejar para la Tarea 3 los asserts de texto de prompt ("100 a 120 palabras", "8 a 10 imágenes") y del `RESCUE_STORY_CRAFT`: en esta tarea solo se ajusta lo que rompen las constantes eliminadas.

- [ ] **Step 5: Correr los tests**

Run: `python -m pytest tests/test_long_videos.py tests/test_historias_formato_luna.py -q`
Expected: PASS (los tests de la Tarea 3 sobre el texto de los prompts pueden seguir fallando solo si ya los cambiaste; no los toques aquí).

- [ ] **Step 6: Suite completa** — `python -m pytest tests -q` debe quedar en verde (si un test de prompt falla porque asume el formato corto, déjalo: es de la Tarea 3; si falla otro, arréglalo). Confirmar `auto_pipeline.py` CRLF puro y sin bytes de control.

---

### Task 2: Cableado en el lote, la ruta manual y la interfaz

**Files:**
- Modify: `batch_pipeline.py` (LF), `app.py` (CRLF)
- Modify: `tests/test_bebe_heroe.py` (el test `test_script_is_rebuilt_from_the_four_scene_phrases` usa `bebe_script_from_story` y `BEBE_HEROE_MIN_WORDS`)
- Modify: `tests/test_text_chain.py`, `tests/test_prompts_english.py` (el assert de `_has_fixed_length`), `tests/test_historias_formato_luna.py` (casos de la cadena que dependan de rangos/mensajes)
- Modify: `tests/test_long_videos.py` (agregar tests de esta tarea)

**Interfaces:**
- Consumes (Tarea 1): `auto_pipeline.LONG_KINDS`, `validate_long_script_length`, `validate_image_count`, `count_image_blocks`, `validate_min_duration`, `validate_long_story_text`, `validate_historia_script`.
- Produces: ningún símbolo nuevo; **elimina** `batch_pipeline._has_fixed_length`, `BEBE_HEROE_SCENES`, `BEBE_HEROE_MIN_WORDS`, `bebe_script_from_story`. Nunca más se recorta el guion por duración en las páginas con guion.

- [ ] **Step 1: Tests (rojo)** — agregar a `tests/test_long_videos.py`:

```python
import batch_pipeline as bp


@pytest.mark.parametrize("msg", [
    "Guion fuera de largo: 120 palabras (deben ser 250-300 para durar mas de 1:20)",
    "Imágenes fuera de rango: 8 (deben ser 14-22)",
    "Video más corto que 1:20: el audio dura 70 s (minimo 80 s)",
])
def test_long_video_errors_are_healable(msg):
    assert bp._is_healable_error("Tras 3 intentos automáticos: " + msg)


def test_removed_fixed_length_machinery():
    for name in ("_has_fixed_length", "BEBE_HEROE_SCENES", "BEBE_HEROE_MIN_WORDS", "bebe_script_from_story"):
        assert not hasattr(bp, name), name


def _chain(monkeypatch, texts, kind="macrame"):
    seen = []

    def fake_generate(system, user, **kwargs):
        seen.append(kwargs)
        return texts.pop(0), "freellm"

    monkeypatch.setattr(bp.text_provider, "available_backends", lambda: ["freellm"])
    monkeypatch.setattr(bp.text_provider, "generate_text", fake_generate)
    return seen


def _text(n_images, script_words):
    blocks = "".join(
        f"Imagen {i}\nFrase del guion: «Frase {i}.»\nPrompt: Vertical 9:16, a calm scene {i}.\n\n" for i in range(1, n_images + 1)
    )
    return f"HOOK_TEXT: Gancho\n\nPERSONAJES:\n[CAMILA]: a woman\n\nGuion\n{' '.join(['palabra'] * script_words)}\n\n{blocks}"


def test_chain_retries_with_the_next_model_when_the_story_is_too_short(monkeypatch):
    seen = _chain(monkeypatch, [_text(8, 120), _text(18, 270)])
    out = bp._generate_text_with_chain("macrame", "MACRAME CREATIVO", "dame una historia", "t1")
    assert out.count("Imagen ") >= 18 and len(seen) == 2
    assert all(kw["max_tokens"] == 16384 for kw in seen)
    assert seen[0]["model"] != seen[1]["model"]


def test_chain_gives_up_after_the_last_model(monkeypatch):
    models = len(bp.text_provider.model_chain())
    _chain(monkeypatch, [_text(8, 120)] * max(bp.TEXT_FORMAT_ATTEMPTS, models))
    with pytest.raises(RuntimeError, match="(?i)fuera de largo|fuera de rango"):
        bp._generate_text_with_chain("macrame", "MACRAME CREATIVO", "dame una historia", "t2")
```

  Ajustar los helpers si `bp.TEXT_FORMAT_ATTEMPTS`/`model_chain` se llaman distinto en el código real (leer `_generate_text_with_chain` antes). Estos tests deben fallar ahora y pasar al final.

- [ ] **Step 2: Correr y ver fallar** — `python -m pytest tests/test_long_videos.py -q`.

- [ ] **Step 3: `batch_pipeline.py`**:
  1. **Sanables:** agregar a `_HEALABLE_EXTRA_MARKERS`: `"guion fuera de largo"`, `"imágenes fuera de rango"`, `"video más corto que 1:20"` (cada una con comentario en español: el modelo respondió corto/largo o con otro número de imágenes / la voz salió más corta: otro intento suele cumplir). Cambiar el comentario de `"guion de historia fuera de formato"` ("largo o cierre fuera del formato de ~42 s") a "~90 s".
  2. **`_generate_text_with_chain`:** `max_tokens=16384` (comentario: la salida son ~7-8 mil tokens y el router "auto" razona ~3 mil antes de contestar). Reemplazar el bloque `if kind == "historias": ...` por validación para todos los `LONG_KINDS`:

```python
                    # Todos los videos duran mas de 1:20: un guion fuera de largo o un numero de imagenes
                    # fuera de rango se reintenta ya con el siguiente modelo (no escribe scratch).
                    if kind in auto_pipeline.LONG_KINDS:
                        script = auto_pipeline.extract_script(text)
                        try:
                            if script.strip():
                                if kind == "historias":
                                    auto_pipeline.validate_historia_script(script)
                                else:
                                    auto_pipeline.validate_long_script_length(script)
                            auto_pipeline.validate_image_count(auto_pipeline.count_image_blocks(text))
                        except auto_pipeline.PipelineError as ve:
                            raise text_provider.TextProviderError("freellm: " + str(ve))
```

  y ampliar la tupla `retryable` con `"fuera de largo"`, `"fuera de rango"`, `"fuera de formato"` (conservando las existentes; `"formato de historia"` puede quedar). Actualizar el comentario de "HISTORIAS dura ~42 s".
  3. **Eliminar** `BEBE_HEROE_SCENES`, `BEBE_HEROE_MIN_WORDS` y `bebe_script_from_story`; reescribir el comentario de cabecera de "Pagina Bebé Héroe" ("Reels de 18 s (4 escenas)" → "videos de ~90 s (16-20 imágenes) de un bebé bombero..."; la música/zoom por escena ya no se usan: dejar solo lo que sigue vigente: voz clonada "Voz de Bebé"). `generate_bebe_heroe_voice` y `video_maker.build_bebe_heroe_props` quedan sin uso (no se borran).
  4. **Eliminar `_has_fixed_length`** y sus usos: en `_generate_batch_video` el `build_trigger_message(...)` pasa `None` como duración (las páginas con guion no usan la duración del panel) y se borra el bloque `if not _has_fixed_length(...): cap_script_to_duration(...)`.
  5. **Validaciones en `_generate_batch_video`:** reemplazar el chequeo `if bebe and len(story["prompts"]) != BEBE_HEROE_SCENES` por `auto_pipeline.validate_image_count(len(story["prompts"]))` (para todas las páginas). Borrar `if bebe: script_text = bebe_script_from_story(story)`; la llamada a `validate_script` pasa a `auto_pipeline.validate_script(script_text)` y a continuación `auto_pipeline.validate_long_script_length(script_text)`. Se mantienen el chequeo "ebook" de los anuncios y `validate_historia_script` para historias.
  6. **Audio:** unificar las dos ramas: `voice = _bebe_heroe_voice(vs.get("voice")) if bebe else (vs.get("voice") or get_default_voice())` y una sola llamada `text_to_speech_verified(script_text, voice=voice, exaggeration=..., cfg_weight=...)` con `_run_stage_with_retry`. Eliminar `bebe_timings`. Tras `if not audio_path: raise RuntimeError("Error al generar el audio.")` agregar `auto_pipeline.validate_min_duration(video_maker.get_audio_duration(audio_path))`.
  7. **Render:** quitar la rama `if bebe: build_bebe_heroe_props(...)`; siempre `video_maker.build_props(...)`, con `title="" if bebe else hook_text` (en Bebé Héroe el resumen es solo para la descripción, no va en pantalla). Mantener `orientation="vertical" if bebe else ...` y `generate_video=... and not bebe`.
  8. **Al terminar:** quitar `bebe_phrases=bebe_timings` del `v.update(...)` de Bebé Héroe (los demás campos se mantienen).
  9. Corregir comentarios/docstrings que mencionen 14/17/18/42 s (grep `18 s|14 s|17 s|42 s|reel de 18`).

- [ ] **Step 4: `app.py` (`_run_pipeline_job`, CRLF)**:
  1. Quitar `bebe_timings` y su comentario. Del bloque `if bebe:` inicial conservar la voz (`tts_kwargs`), `generate_video_clips = False`; borrar `duration_seconds = None` condicionado a `_has_fixed_length` y reemplazar por: `if page_name: duration_seconds = None  # las paginas con guion duran ~1:30 fijos`.
  2. En el bloque de generación (`if page_name and not story_text`): tras las validaciones de anuncio/historias, para Bebé Héroe mantener `_, story_text = batch_pipeline._split_hook_text(story_text)` y reemplazar el chequeo de `BEBE_HEROE_SCENES` por `auto_pipeline.validate_long_story_text(story_text, story_id)` (para todas las páginas, también sin Bebé Héroe: llamarlo una vez, después del split de Bebé Héroe).
  3. En la extracción del guion: borrar `if bebe: script_text = bebe_script_from_story(...)` y usar `auto_pipeline.validate_script(script_text)`.
  4. `run_tts`: borrar la rama `if bebe:` con `generate_bebe_heroe_voice` y los `phrases`/`bebe_tag` del `_pipeline_sub_update`; queda `text_to_speech_verified(script_text, on_progress=on_progress, **tts_kwargs)`. Después de obtener `output_path`, si `page_name`, medir `video_maker.get_audio_duration(output_path)` y, si `validate_min_duration` lanza `PipelineError`, marcar el sub-estado `tts` con `status="error", error=str(e), percent=100` y retornar (mismo patrón que el `except` de TTS).
  5. Render: quitar la rama `if bebe: build_bebe_heroe_props`; `build_props(... title="" if bebe else title ...)`.
  6. Corregir los comentarios que dicen "reel de 18 s" / "anuncio de ~14 s".
  7. **HTML:** bajo cada uno de los selectores `pipeline-duration` y `lote-duration` (después del `</select>`), agregar una nota corta con la clase de "hint" que ya use la interfaz (buscar una existente, p. ej. `field-hint`/`hint`/`muted` en el HTML; si no existe, un `<div style="font-size:12px;opacity:.7;margin-top:4px">`): "Las páginas con guion (Historias, Macramé, Niño Selectivo, Bebé Héroe) duran ~1:30 y no usan esta opción." Sin `{{`, `{%`, `{#`.

- [ ] **Step 5: Ajustar tests existentes**:
  - `tests/test_bebe_heroe.py`: reemplazar `test_script_is_rebuilt_from_the_four_scene_phrases` por un test que compruebe que `bebe_script_from_story`, `BEBE_HEROE_SCENES` y `BEBE_HEROE_MIN_WORDS` ya no existen **solo si** no está ya cubierto en `test_long_videos.py` (está: `test_removed_fixed_length_machinery`) → simplemente borrar ese test. Dejar intactos los de `build_bebe_heroe_props`/`text_to_speech_phrases` (el código sigue existiendo).
  - `tests/test_prompts_english.py` / `test_historias_formato_luna.py`: donde se afirme `_has_fixed_length(...)`, reemplazar por: `build_trigger_message("x", 30, kind=<kind>)` no contiene `"aproximadamente"` para los 4 kinds.
  - `tests/test_text_chain.py` y los tests de la cadena en `test_historias_formato_luna.py`: adaptar mensajes y textos de ejemplo a los rangos nuevos (guion de 260-290 palabras y 16-20 imágenes); los tests de truncado/429 no deben cambiar de significado.

- [ ] **Step 6: Suite completa** — `python -m pytest tests -q` en verde. Comprobar CRLF/bytes de control en `app.py` y que `python -c "import app"` no falla (carga el HTML con Jinja: renderizar `/` con el cliente de Flask si existe un test; si no, `app.app.test_client().get("/")` con las credenciales que usen los tests existentes).

---

### Task 3: Prompts de formato largo (4 archivos + `RESCUE_STORY_CRAFT`)

**Files:**
- Modify: `prompts/historias_system.md`, `prompts/ninio_selectivo_system.md`, `prompts/macrame_system.md`, `prompts/bebe_heroe_system.md` (LF)
- Modify: `batch_pipeline.py` (`RESCUE_STORY_CRAFT`)
- Modify: `tests/test_historias_formato_luna.py`, `tests/test_prompts_english.py`
- Modify: `tests/test_long_videos.py` (agregar tests de prompts)

**Interfaces:**
- Consumes: `bp._PROMPTS_DIR` (carpeta de prompts), `bp.RESCUE_STORY_CRAFT`.
- Produces: cada prompt contiene literalmente `260 a 290 palabras` y `16 a 20 imágenes`; ninguno contiene ya `38 a 4`, `38-4`, `14 segundos`, `17 segundos`, `18 segundos`, `4 escenas`, `exactamente 5 imágenes`, `8 a 10 imágenes` ni `100 a 120 palabras`.

- [ ] **Step 1: Tests (rojo)** — agregar a `tests/test_long_videos.py`:

```python
PROMPTS = {
    "historias": bp._PROMPTS_DIR / "historias_system.md",
    "ninio_selectivo": bp._PROMPTS_DIR / "ninio_selectivo_system.md",
    "macrame": bp._PROMPTS_DIR / "macrame_system.md",
    "bebe_heroe": bp._PROMPTS_DIR / "bebe_heroe_system.md",
}
OLD_SHORT_FORMAT = ("38 a 4", "38-4", "14 segundos", "17 segundos", "18 segundos", "18 s", "4 escenas",
                    "exactamente 5 imágenes", "exactamente 4 imágenes", "8 a 10 imágenes", "100 a 120 palabras")


@pytest.mark.parametrize("kind", sorted(PROMPTS))
def test_prompts_ask_for_the_long_format(kind):
    text = PROMPTS[kind].read_text(encoding="utf-8")
    assert "260 a 290 palabras" in text and "16 a 20 imágenes" in text
    assert "90 segundos" in text
    for old in OLD_SHORT_FORMAT:
        assert old not in text, (kind, old)
    for kept in ("HOOK_TEXT:", "PERSONAJES:", "Guion", "Imagen 1", "Frase del guion"):
        assert kept in text, (kind, kept)


@pytest.mark.parametrize("kind", ["ninio_selectivo", "macrame"])
def test_ad_prompts_keep_the_ebook_closing_and_fixed_sets(kind):
    text = PROMPTS[kind].read_text(encoding="utf-8")
    assert "Comenta EBOOK" in text
    assert ("[COCINA]" if kind == "ninio_selectivo" else "[ESCENA]") in text
    assert "NO dibujes ningún celular" in text


def test_bebe_heroe_prompt_keeps_the_fixed_baby_sheet_and_the_closing_question():
    text = PROMPTS["bebe_heroe"].read_text(encoding="utf-8")
    assert "[BEBE]: cute chubby baby" in text and "F.D. JUNIOR" in text
    assert "pregunta" in text


def test_rescue_story_craft_asks_for_the_long_script():
    assert "260 a 290 palabras" in bp.RESCUE_STORY_CRAFT and "90 segundos" in bp.RESCUE_STORY_CRAFT
    assert "100 a 120" not in bp.RESCUE_STORY_CRAFT and "42 segundos" not in bp.RESCUE_STORY_CRAFT
```

- [ ] **Step 2: Correr y ver fallar** — `python -m pytest tests/test_long_videos.py -q`.

- [ ] **Step 3: `prompts/historias_system.md`** (leer el archivo actual y editarlo en lugar de reescribir todo):
  - Intro: "unos 42 segundos" → "unos 90 segundos".
  - Bajo "ESTRUCTURA OBLIGATORIA (5 pasos...)": agregar una línea de ritmo: "Reparte la narración así (≈ 24 a 28 frases en total): gancho 2 a 3 frases; conflicto 7 a 8 frases con al menos dos adversidades distintas (el clima, la indiferencia de la gente, el hambre, el paso de los días); giro 5 a 6 frases (el personaje bondadoso nota el detalle, duda, investiga y actúa); reencuentro o rescate 6 a 8 frases con una escena concreta y emotiva; cierre 3 frases."
  - Sección "Guion": "Narración completa en español, de 260 a 290 palabras (unas 24 a 28 frases, unos 90 segundos), escrita en un solo bloque... Cuenta las palabras antes de responder: si quedas por debajo de 260, desarrolla más el conflicto o el reencuentro; nunca rellenes con repeticiones." (conservar la instrucción de acentos y ñ).
  - "(continúa hasta Imagen 8-10...)" → "(continúa hasta Imagen 16-20, mismo formato de 3 líneas por imagen)".
  - Regla "De 8 a 10 imágenes siguiendo los 5 pasos: ..." → "De 16 a 20 imágenes (una por cada 1 o 2 frases) siguiendo los 5 pasos: espera con luz cálida, el mismo lugar con frío o lluvia, el animal esperando en primer plano, la indiferencia de la gente, otro momento de la espera, el personaje bondadoso, el detalle que descubre, el personaje con el animal, el reencuentro con su persona, el abrazo y el animal feliz en su hogar. Cada imagen es una escena DISTINTA (otro plano, otra hora, otro detalle); no repitas la misma composición. La Imagen 1 es el momento más emotivo, nunca el más crudo."
  - Mantener: fichas detalladas, `[LUGAR]`, prompts de 90-130 palabras, "NUNCA en español", transformación en 2 escenas secuenciales, cierre con corazón, nombres evitados.

- [ ] **Step 4: `prompts/ninio_selectivo_system.md`**: reescribir las secciones de largo y beats (leer el archivo actual; conservar TONO, ALIMENTO, formato de fichas, reglas de etiquetas y de imágenes):
  - Intro: "unos 14 segundos" → "unos 90 segundos".
  - **ESTRUCTURA (9 beats):** 1 Gancho (testimonio con contradicción o plazo, conservar las fórmulas de ejemplo); 2 Dolor (la batalla diaria con el alimento X, con 2 ejemplos concretos: ruegos, platos que vuelven llenos); 3 Lo que ya intenté (lo que probó y no funcionó, sin culpar al niño); 4 El giro (dejó de forzar, descubrió recetas pensadas para niños selectivos); 5 Qué trae el ebook (platos atractivos para ellos y saludables para toda la familia, pasos simples; usar solo datos genéricos del ebook, sin inventar cifras); 6 Primer resultado (el niño prueba, con una escena concreta); 7 Prueba (la mesa en paz, repite el plato otro día); 8 Para quién es (para quien vive lo mismo; sin culpa, sin presión, sin promesas médicas); 9 Cierre EXACTO: "Comenta EBOOK y te envío las recetas." (siempre EBOOK en mayúsculas).
  - **REGLAS DEL GUION:** "260 a 290 palabras en total (≈ 90 segundos; nunca menos de 250), en un solo bloque, en español" — conservar las reglas de solo-español, ritmo (frases cortas a medias, ahora con 24-28 frases), rotación de adulto/niño y "NO prometas partes futuras". Agregar "Cuenta las palabras antes de responder".
  - **FORMATO:** `Guion`: "Narración completa, 260 a 290 palabras, en un solo bloque, siguiendo los 9 beats". Fichas igual, con "la misma en todas las imágenes" en vez de "en las 5". Prompts de imagen: igual (70-100 palabras, empiezan con "Vertical 9:16, photorealistic, warm and bright:", etiquetas y `[COCINA]` siempre). "(exactamente 5 imágenes...)" → "(de 16 a 20 imágenes, mismo formato de 3 líneas por imagen)".
  - **IMÁGENES (16 a 20, una cada 1 o 2 frases):** reemplazar la lista de 5 por un reparto por beats: gancho (plato limpio y apetitoso, plano cercano), dolor (el niño dudando / apartando el plato, adulto paciente; 3-4 imágenes con planos distintos), lo que ya intenté (adulto cocinando otros platos, el niño sin comer; 2-3 imágenes), giro (el adulto leyendo/preparando una receta nueva; 2 imágenes), qué trae (adulto cocinando paso a paso con el niño mirando, sin dibujar libro ni portada; 2-3), primer resultado (el niño probando; 2), prueba (la mesa en paz, el niño repite; 2), para quién es (adulto sonriendo, ambiente cálido; 1-2), cierre (adulto sonriendo con el niño comiendo feliz; **NO dibujes ningún celular, tablet, libro ni portada**: el sistema superpone después un celular con la portada real del ebook). Cada imagen en un plano/ángulo distinto, misma cocina `[COCINA]` y misma ropa.
  - Reglas finales: mantener; "Nada de split screen"; sin texto ni logos.

- [ ] **Step 5: `prompts/macrame_system.md`**: igual enfoque (conservar la descripción del producto "Kit Nudo a Nudo" y sus datos reales: 15 nudos con fotos paso a paso, 3 patrones, 2 ebooks de regalo, primera pieza en 15 minutos con 2 metros de cuerda; sin precios, sin "gratis"):
  - Intro "unos 17 segundos" → "unos 90 segundos".
  - **ESTRUCTURA (9 beats):** 1 Gancho (creencia limitante en primera persona, con las fórmulas actuales); 2 Problema (tutoriales en video demasiado rápidos, no se entiende el nudo); 3 Lo que ya intenté (cuerda enredada, abandonar a mitad; sin inventar cifras); 4 Descubrimiento (el kit con nudos paso a paso en fotos); 5 Qué trae (15 nudos, 3 patrones: llavero celeste bohemio, tapiz mini, portamaceta simple); 6 Primer intento (empieza con 2 metros de cuerda, sigue una foto a la vez); 7 Prueba (la pieza terminada: "tercer proyecto y el primero tan prolijo", variando número y pieza); 8 Para quién es (principiantes, sin experiencia; sin promesas exageradas); 9 Cierre EXACTO: "Comenta EBOOK y te envío la guía." (EBOOK en mayúsculas).
  - Reglas de guion: "260 a 290 palabras (≈ 90 segundos; nunca menos de 250)"; conservar solo-español (sin "YouTube", "boho", "DIY", "tips", "online", sin abreviaturas ni símbolos; decir "tutoriales en video"). "Cuenta las palabras antes de responder".
  - Formato: `Guion` "260 a 290 palabras... siguiendo los 9 beats"; fichas `[NOMBRE]`, `[PIEZA]`, `[ESCENA]` igual (con "la misma en todas las imágenes"); "(exactamente 5 imágenes...)" → "(de 16 a 20 imágenes...)".
  - **IMÁGENES (16 a 20):** reparto por beats con planos distintos: manos anudando (gancho), frustración con la cuerda enredada (problema/intentos, 4-5), descubrimiento con el libro de nudos abierto sobre la mesa y SIN dibujar celular ni portada (4-5), proceso paso a paso con [PIEZA] a medio hacer, no terminada (3-4), pieza terminada colgada (2), plano medio de [NOMBRE] orgullosa sosteniendo [PIEZA] (2), cierre sonriendo (1-2). `[PIEZA]` solo completa en las dos últimas escenas de prueba y en la primera si es gancho; la ropa, la sala y la luz no cambian.
  - **NO dibujes ningún celular ni portada** debe seguir literal.

- [ ] **Step 6: `prompts/bebe_heroe_system.md`**: leer el archivo y reescribir:
  - Intro: "una mini-historia de 4 escenas (18 segundos)" → "una historia de unos 90 segundos (260 a 290 palabras, entre 16 y 20 imágenes)" con voz en off de niño.
  - Estructura: HOOK (el problema, 2-3 frases con tensión), EMPATÍA (el bebé mira preocupado, duda, 4-5 frases), ACCIÓN (el bebé rescata o ayuda, con obstáculos que supera uno a uno, 10-12 frases; es el centro), CIERRE (abrazo, final feliz, 4-5 frases, y la última frase es SIEMPRE una pregunta que invita a comentar). Total 24-28 frases. Primera persona, voz de niño pequeño, español neutro sin voseo; frases de 6 a 14 palabras; sin peligro gráfico ni maltrato.
  - Ficha `[BEBE]` fija idéntica (copiar tal cual, incluida "F.D. JUNIOR" y el casco rojo); el animal u objeto con ficha de **90 a 120 palabras** en inglés con etiqueta en mayúsculas; humanos de la familia con ficha de 80-100 palabras y ropa fija. Los prompts de imagen: 70-100 palabras, empiezan con el mismo prefijo actual ("Vertical 9:16, photorealistic, cinematic golden hour sunset, ultra detailed, emotional storytelling, shallow depth of field:"), usan `[BEBE]` y el animal con su etiqueta.
  - Formato: "Las frases de la voz en off, en un solo bloque" → `Guion` de 260 a 290 palabras; imágenes "de 16 a 20 imágenes, mismo formato de 3 líneas"; "Frase del guion" es un fragmento exacto de la narración. Mantener: HOOK_TEXT de resumen en una línea (máx. 14 palabras), "Imagen 1: solo el problema (el bebé no aparece o al fondo)", la imagen final con el bebé y el animal/familiar juntos, mismo uniforme y casco ROJOS, sin cartoon/anime/3D, sin prometer partes futuras.
  - Eliminar toda mención a "18 s", "4 escenas", tiempos por escena y "(0-3 s)".

- [ ] **Step 7: `batch_pipeline.RESCUE_STORY_CRAFT`**: "video de unos 42 segundos" → "video de unos 90 segundos"; "El guion de narracion tiene de 100 a 120 palabras" → "El guion de narracion tiene de 260 a 290 palabras (unas 24 a 28 frases: desarrolla el conflicto con varias adversidades y el reencuentro con una escena concreta)"; mantener el resto.

- [ ] **Step 8: Ajustar tests existentes:** en `tests/test_historias_formato_luna.py` `test_historias_prompt_names_the_five_steps_and_the_fixed_length`: "100 a 120 palabras" → "260 a 290 palabras", "8 a 10 imágenes" → "16 a 20 imágenes"; `test_rescue_story_craft_matches_the_prompt`: "100 a 120 palabras" → "260 a 290 palabras". En `tests/test_prompts_english.py` líneas con `"38 a 46 palabras"`, `"38 a 48 palabras"` y `"exactamente 5 imágenes"` → las cadenas nuevas (`260 a 290 palabras`, `16 a 20 imágenes`); el resto de lo que afirman (Comenta EBOOK, NO dibujes ningún celular, "PILARES DE VALOR" ausente) se conserva.

- [ ] **Step 9: Suite completa** — `python -m pytest tests -q` en verde; escanear los `.md` y tests tocados en busca de bytes de control.

---

### Task 4: Verificación real (la ejecuta el controlador, no un subagente)

**Files:** ninguno (solo archivos temporales en el scratchpad de la sesión; borrarlos al terminar).

- [ ] **Step 1:** Generar 1 guion real por kind (`historias`, `ninio_selectivo`, `macrame`, `bebe_heroe`) con `batch_pipeline._generate_text_with_chain` (cargar `.env` con `dotenv`, usar `auto_pipeline.build_trigger_message("dame una historia", None, kind=...)` más los bloques extra que cada ruta agrega: patrón de rescate para historias, alimento de Niño Selectivo y `bebe_trigger_block()`). Si el proveedor responde 429, esperar el reinicio (~70 s) y reintentar; si sigue agotado, anotarlo y continuar con los demás.
- [ ] **Step 2:** Por cada guion: palabras del bloque "Guion" (250-300), número de imágenes (14-22), prompts en inglés (`validate_prompts_english`), y para anuncios `validate_ad_story`. Registrar los resultados.
- [ ] **Step 3:** Con un guion de HISTORIAS válido, generar la voz (`tts_engine.text_to_speech_verified`) y medir `video_maker.get_audio_duration`: debe pasar de 80 s. Si hay imágenes ya generadas disponibles, renderizar el video completo y medirlo con `ffprobe`; si no, medir solo el audio y decir que el render completo no se probó.
- [ ] **Step 4:** Informar al usuario los números reales, los riesgos que se vieron (cuota, tiempos) y que el servidor del puerto 5000 necesita reiniciarse para tomar los cambios. No reiniciar ni hacer commit sin que el usuario lo pida.
