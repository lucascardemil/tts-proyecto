# How Big Really Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retirar la página Bebé Héroe (código y slot 5 del `.env`) y crear la página "How Big Really": posts de imagen de comparación de escala (inglés, Facebook), con banco de temas, prompt propio, Flow dibujando la imagen completa y horario automático 3/día los primeros 14 días y 2/día después.

**Architecture:** Un perfil nuevo `howbig_image` en `batch_pipeline.IMAGE_POST_PROFILES` reutiliza todo el flujo de posts de imagen existente (`generate_gaming_post` → Flow → `fit_post` → `_publish_batch_image_post`). Un módulo nuevo `howbig.py` elige plantilla (ponderada) y tema (banco `assets/howbig/topics.json`). El horario por fase se agrega a `_compute_schedule`/`_free_slot` como extensión opcional. Bebé Héroe se borra primero para no pelear por los mismos archivos.

**Tech Stack:** Python 3.10 (Flask, pytest), prompts `.md`, Remotion/TypeScript (solo para borrar la plantilla de Bebé Héroe), Pillow (`meme_maker`).

**Spec:** `docs/superpowers/specs/2026-10-14-how-big-really-design.md`

## Global Constraints

- Archivos CRLF puros: `app.py`, `auto_pipeline.py`, `tests/test_pilares_de_valor.py`. Se parchean con scripts a nivel de bytes que aseguran cada ancla una sola vez y comprueban `count(b"\n") == count(b"\r\n")`. LF: `batch_pipeline.py`, `video_maker.py`, `tts_engine.py`, `meme_maker.py`, `seo_optimizer.py`, `prompts/*.md`, los demás tests, `howbig.py`, `assets/howbig/topics.json`. Comprobar siempre el fin de línea real del archivo antes de editarlo.
- HTML de `app.py` (`HTML = r"""..."""`) pasa por Jinja: sin `{{`, `{%`, `{#` ni `"""` en lo agregado.
- La herramienta de escritura a veces corrompe barras invertidas y saltos de línea (`\b` → byte 0x08, `\n` duplicado): tras escribir, escanear los archivos tocados en busca de bytes de control (`<9`, 11, 12) y revisar que los literales con `\n` sigan siendo la secuencia de dos caracteres.
- **Credenciales:** el Page ID y el token de Meta se escriben SOLO en `.env` (ignorado por git), en `FB_PAGE_5_*`, y SOLO la hace el controlador (Tarea 6). Ningún subagente, test, doc, log ni mensaje contiene el token. Los tests nunca leen ni escriben el `.env` real.
- Sin commits ni push (regla del usuario: solo a pedido). No tocar los servidores de los puertos 5000 y 5055 ni macrame-bot. No reiniciar nada.
- Estilo fijo de los posts (spec): fondo negro `#0A0A0A`, vector plano mínimo, acento amarillo `#FFD60A` único, silueta humana blanca de 1,8 m abajo a la izquierda, 4:5 vertical, nunca foto, sin sombras. Copy en inglés americano, 5 líneas (SHOCK, DATO, línea vacía, PREGUNTA, `Source:` solo si el tema trae fuente), sin emojis (máx. 1), 0 hashtags (máx. 1), nunca "share this", "follow me", "like", "tag a friend".
- Horario (hora Chile): días 1-14 desde el primer lote → 11:00, 15:00, 22:00; desde el día 15 → 11:00 y 22:00. Solo Facebook.
- Suite completa en verde al final de cada tarea: `python -m pytest tests -q`.

## File Structure

- Borrar/ajustar (Tarea 1): `batch_pipeline.py`, `app.py`, `auto_pipeline.py`, `video_maker.py`, `tts_engine.py` (si queda código sin uso), `prompts/bebe_heroe_system.md`, `video/src/BebeHeroe.tsx`, `video/src/StoryVideo.tsx`, `video/src/schema.ts`, `assets/bebe_heroe/`, tests (`test_bebe_heroe.py` y menciones en otros).
- `howbig.py` (nuevo, Tarea 2): constantes, `load_topics`, `pick_template`, `pick_topic`, `topic_block`, `BankExhaustedError`.
- `assets/howbig/topics.json` (nuevo, Tarea 2): ~100 temas.
- `prompts/howbig_post_system.md` (nuevo, Tarea 3), perfil `howbig_image` + generación en `batch_pipeline.py`, `meme_maker.fit_post(trim=...)`.
- `batch_pipeline.py` (Tarea 4): horario por fase, `_free_slot(hour_end)`, `create_project`.
- `app.py` (Tarea 5): selectores, JS, rutas.
- Tests nuevos: `tests/test_howbig.py` (Tareas 2-4), ajustes en los existentes.

---

### Task 1: Retirar el código de Bebé Héroe

**Files:**
- Modify: `batch_pipeline.py`, `app.py` (CRLF), `auto_pipeline.py` (CRLF), `video_maker.py`, `tts_engine.py`, `video/src/StoryVideo.tsx`, `video/src/schema.ts`, `README.md` si menciona la página
- Delete: `prompts/bebe_heroe_system.md`, `video/src/BebeHeroe.tsx`, `assets/bebe_heroe/` (carpeta completa), `tests/test_bebe_heroe.py` (después de mover lo que no es de Bebé Héroe)
- Modify tests: `tests/test_long_videos.py`, `tests/test_pilares_de_valor.py`, `tests/test_historias_formato_luna.py`, `tests/test_prompts_english.py`, `tests/test_office_state.py`, `tests/test_schedule_slots.py` (solo lo que mencione Bebé Héroe)

**Interfaces:**
- Produces: ningún símbolo con `bebe`/`BEBE`/`Bebé` queda en código vivo. `_text_kind_for_page` devuelve solo `"macrame"`, `"ninio_selectivo"` o `"historias"`. `auto_pipeline.LONG_KINDS == frozenset({"historias", "ninio_selectivo", "macrame"})`.
- No se toca: la voz clonada "Voz de Bebé" (`voices/custom/index.json` y los archivos de voz), `docs/`, `.superpowers/`, `scratch/` (archivos de datos, no código).

- [ ] **Step 1: Inventario.** Ejecutar `grep -rniE "bebe|bebé|bebe_heroe" --include=*.py --include=*.md --include=*.ts --include=*.tsx --include=*.json . ` excluyendo `venv/`, `node_modules/`, `docs/`, `.superpowers/`, `scratch/`, `logs/`, `output/`, `voices/`. Anotar cada hit en el informe con la decisión (borrar / ajustar). Antes de borrar, comprobar con `python -c` que el almacén real de lotes (`batch_pipeline._load()`, solo lectura) no tiene lotes de la página Bebé Héroe en estado `pending`/`generating`/`ready`/`publishing`; si los hay, **detenerse y reportar** (NEEDS_CONTEXT) en vez de borrar.

- [ ] **Step 2: Test de ausencia (rojo).** Agregar al final de `tests/test_long_videos.py`:

```python
def test_bebe_heroe_is_fully_removed():
    import batch_pipeline as bp
    import auto_pipeline as ap
    for name in ("BEBE_HEROE_KIND", "BEBE_HEROE_VOICE_LABEL", "BEBE_HEROE_SLOT_HOURS", "BEBE_HEROE_HISTORY_LIMIT",
                 "BEBE_HEROE_DEFAULT_QUESTION", "BEBE_HEROE_IDEAS", "is_bebe_heroe_page", "bebe_trigger_block",
                 "generate_bebe_heroe_voice", "_bebe_heroe_voice", "_bebe_heroe_copy"):
        assert not hasattr(bp, name), name
    assert ap.LONG_KINDS == frozenset({"historias", "ninio_selectivo", "macrame"})
    assert bp._text_kind_for_page("Bebé Héroe") == "historias"  # ya no es una página con kind propio
    assert not (bp._PROMPTS_DIR / "bebe_heroe_system.md").exists()
```

  Ejecutar y ver que falla.

- [ ] **Step 3: Borrar en `batch_pipeline.py`:** el bloque de constantes y comentario "Pagina Bebé Héroe", `BEBE_HEROE_IDEAS`, `is_bebe_heroe_page`, `_recent_bebe_ideas`, `_pick_bebe_idea`, `bebe_trigger_block`, `generate_bebe_heroe_voice`, `_bebe_heroe_voice`, `_bebe_heroe_copy`, el `narration_check_kwargs` y su constante de similitud, la rama de Bebé Héroe en `_text_kind_for_page`, `create_project` (variable `bebe`, `BEBE_HEROE_SLOT_HOURS`), `_generate_batch_video` (voz, `mood="none"`, `title=""`, `bebe_idea/bebe_tag/story_summary`, orientación vertical forzada y `generate_video=... and not bebe`) y la copia de publicación de Bebé Héroe (`_bebe_heroe_copy` y su llamador). Dejar la lógica común intacta. Quitar `"bebe_heroe"` de lo que referencie el kind.
- [ ] **Step 4: Borrar en `app.py` (`_run_pipeline_job`):** toda la rama `bebe` (voz, `narration_check_kwargs`, `mood`, idea del banco, `generate_video_clips = False` solo-Bebé). Mantener lo genérico.
- [ ] **Step 5: Borrar en `auto_pipeline.py`:** `"bebe_heroe"` de `LONG_KINDS` y cualquier mención en `CHARACTER_SHEET_REQUEST` (la cláusula de ficha fija `[BEBE]`) y comentarios. Si `CHARACTER_SHEET_REQUEST` queda sin usuarios tras el retiro (es el pedido por defecto de `build_trigger_message` para kinds desconocidos), se conserva como está sin la cláusula de Bebé.
- [ ] **Step 6: Borrar en `video_maker.py` y `tts_engine.py`:** `BEBE_HEROE_*`, `assemble_bebe_heroe_audio`, `build_bebe_heroe_props`; en `tts_engine.py` quitar `min_similarity`/`tolerated` de `text_to_speech_verified` y `text_to_speech_phrases` **solo si** `grep` demuestra que ningún otro código los usa; si algo más los usa, conservarlos y decirlo en el informe.
- [ ] **Step 7: Video (Remotion):** borrar `video/src/BebeHeroe.tsx`, quitar su import y rama en `video/src/StoryVideo.tsx`, y los campos de Bebé Héroe en `video/src/schema.ts`; borrar `assets/bebe_heroe/`. Si existe `video/node_modules`, correr `cd video && npx tsc --noEmit` (si el proyecto tiene tsconfig) para comprobar que compila; si no se puede correr, decirlo.
- [ ] **Step 8: Tests.** Mover a un archivo nuevo con nombre neutro (`tests/test_mixed_batches.py`) los tests de `tests/test_bebe_heroe.py` que **no** son de Bebé Héroe (lotes mixtos `_create_mixed`, `test_plain_batches_keep_their_type`, y `text_to_speech_phrases` solo si el código se conservó); borrar el resto y el archivo. Ajustar los demás tests que mencionen Bebé Héroe (listados arriba): quitar o reescribir sus asserts (por ejemplo `test_pilares_de_valor.py` recorre kinds incluyendo `"bebe_heroe"`: quitar ese kind; `test_long_videos.py` tiene tests de la voz tolerante, mood y `_bebe`: borrarlos junto con el código).
- [ ] **Step 9: Verificar.** `python -m pytest tests -q` en verde; `python -c "import app"`; `GET /` con el cliente de Flask (mismo patrón que el test de render existente); `grep` final del Paso 1 sin hits vivos; comprobar fin de línea y bytes de control de los archivos tocados.

---

### Task 2: Banco de temas y selección (`howbig.py`)

**Files:**
- Create: `howbig.py`, `assets/howbig/topics.json`, `tests/test_howbig.py`

**Interfaces:**
- Produces (todo en `howbig`):
  - `SOURCES = ("NOAA", "NASA", "USGS", "WHO", "Guinness World Records", "Smithsonian", "National Geographic")`
  - `TEMPLATE_WEIGHTS = {"A": 7, "B": 2, "C": 1}`; `TEMPLATE_LABELS = {"A": "Scale comparison", "B": "Map comparison", "C": "Quantity / stack"}`
  - `class BankExhaustedError(RuntimeError)`
  - `load_topics() -> list[dict]` (cada tema: `id`, `template`, `big`, `reference`, `fact`, `source` (str o None), `question`)
  - `pick_template(recent_templates: list, rng=random) -> str` — sorteo ponderado por `TEMPLATE_WEIGHTS` que nunca devuelve la misma plantilla que `recent_templates[-1]` (si hay historial)
  - `pick_topic(template: str, used_ids: set, rng=random) -> dict` — tema de esa plantilla no usado; si no quedan, de otra plantilla con temas libres; si ninguno, `BankExhaustedError("How Big Really topic bank exhausted ...")`
  - `topic_block(topic: dict) -> str` — texto en inglés que se agrega al mensaje del generador: tema, hecho, fuente (o "no source"), pregunta
- Consumes: nada (módulo independiente).

- [ ] **Step 1: Tests (rojo)** en `tests/test_howbig.py`:

```python
# -*- coding: utf-8 -*-
"""How Big Really: banco de temas y seleccion de plantilla/tema."""
import random
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import howbig


def test_bank_schema_and_distribution():
    topics = howbig.load_topics()
    assert len(topics) >= 100
    ids = [t["id"] for t in topics]
    assert len(ids) == len(set(ids))
    counts = Counter(t["template"] for t in topics)
    assert set(counts) == {"A", "B", "C"}
    assert counts["A"] >= 60 and counts["B"] >= 15 and counts["C"] >= 5
    for t in topics:
        assert set(t) == {"id", "template", "big", "reference", "fact", "source", "question"}, t["id"]
        for key in ("big", "reference", "fact", "question"):
            assert isinstance(t[key], str) and t[key].strip(), (t["id"], key)
        assert t["source"] is None or t["source"] in howbig.SOURCES, (t["id"], t["source"])
        assert 25 <= len(t["fact"]) <= 400, t["id"]
        assert t["question"].strip().endswith("?"), t["id"]
        assert "http" not in t["fact"].lower() and "#" not in t["fact"], t["id"]


def test_template_pick_is_weighted_and_never_repeats_the_last():
    rng = random.Random(7)
    picks, last = [], "A"
    for _ in range(3000):
        last = howbig.pick_template([last], rng)
        picks.append(last)
    assert all(a != b for a, b in zip(picks, picks[1:]))
    counts = Counter(picks)
    assert counts["A"] > counts["B"] > counts["C"] and counts["C"] > 0
    assert howbig.pick_template([], random.Random(1)) in howbig.TEMPLATE_WEIGHTS


def test_pick_topic_skips_used_and_falls_back_to_other_templates():
    topics = howbig.load_topics()
    a_ids = {t["id"] for t in topics if t["template"] == "A"}
    t = howbig.pick_topic("A", set(), random.Random(3))
    assert t["template"] == "A" and t["id"] not in set()
    t2 = howbig.pick_topic("A", a_ids, random.Random(3))  # todas las A usadas
    assert t2["template"] in ("B", "C") and t2["id"] not in a_ids


def test_bank_exhausted():
    all_ids = {t["id"] for t in howbig.load_topics()}
    with pytest.raises(howbig.BankExhaustedError, match="exhausted"):
        howbig.pick_topic("A", all_ids, random.Random(1))


def test_topic_block_has_the_fact_and_only_listed_sources():
    topic = {"id": "x1", "template": "A", "big": "blue whale heart", "reference": "golf cart",
             "fact": "A blue whale's heart is roughly the size of a golf cart.", "source": "NOAA",
             "question": "What animal should we scale next?"}
    block = howbig.topic_block(topic)
    assert "blue whale heart" in block and "golf cart" in block and "Source: NOAA" in block
    no_source = howbig.topic_block({**topic, "source": None})
    assert "Source:" not in no_source and "no source" in no_source.lower()
```

- [ ] **Step 2: `howbig.py`** (LF), con comentarios en español como el resto del repo: constantes, `load_topics()` lee `assets/howbig/topics.json` (UTF-8, ruta relativa a `__file__`, cacheada con `functools.lru_cache`), `pick_template` (ponderado: excluir la última plantilla del historial y sortear entre las otras con `rng.choices`), `pick_topic`, `topic_block`:

```python
def topic_block(topic: dict) -> str:
    source = f"Source: {topic['source']}" if topic.get("source") else "No source line for this topic (do NOT write a Source line)."
    return (
        "\n\nTOPIC FOR THIS POST (use ONLY these facts; do not add numbers, dates or sources of your own):\n"
        f"- Big subject: {topic['big']}\n- Reference for scale: {topic['reference']}\n"
        f"- Fact: {topic['fact']}\n- {source}\n- Closing question: {topic['question']}"
    )
```

- [ ] **Step 3: `assets/howbig/topics.json`** — lista JSON (UTF-8, sangría de 2 espacios) de **≥ 100 temas**: ≥ 70 de plantilla `A` (escala: partes de animales, objetos, estructuras, planetas y cuerpos celestes frente a un objeto cotidiano), ≥ 20 `B` (tamaño real de países/regiones/lagos/islas superpuestos, p. ej. Russia vs Africa), ≥ 8 `C` (cantidades: todo el oro minado, toda el agua dulce, todo el plástico, etc., frente a una referencia visual). Reglas de contenido, **obligatorias** porque son hechos que se publican:
  - Solo hechos ampliamente documentados y estables; cifras aproximadas y conservadoras con "about", "roughly", "nearly"; nada disputado ni que cambie cada año. Si no estás seguro de una cifra, **no incluyas el tema**.
  - `fact` = frase de shock + dato duro en una o dos oraciones, en inglés americano, sin emojis, sin hashtags, sin URLs; 25-400 caracteres.
  - `source`: una de `NOAA`, `NASA`, `USGS`, `WHO`, `Guinness World Records`, `Smithsonian`, `National Geographic`, o `null` cuando no haya una de esas que respalde claramente el dato. No inventar citas.
  - `question`: pregunta abierta en inglés que termina en `?` y pide qué escalar después (p. ej. "What animal should we scale next?", "Which map surprised you most?", "How many do you think there are?"); variar las preguntas.
  - `id` únicos con el formato `a001`..., `b001`..., `c001`...
  - Incluir (corrigiendo cifras si no son seguras) ejemplos del usuario: corazón de la ballena azul vs carrito de golf; Rusia vs África; todo el oro minado como un cubo vs 3 piscinas olímpicas. El resto lo eliges tú entre hechos de ese nivel de certeza.
  - Informar en el reporte cualquier tema que dudes, para que el usuario lo revise.
- [ ] **Step 4:** correr `python -m pytest tests/test_howbig.py -q` (rojo → verde) y la suite completa.

---

### Task 3: Perfil `howbig_image`, prompt y generación

**Files:**
- Create: `prompts/howbig_post_system.md`
- Modify: `batch_pipeline.py` (perfil, `_image_post_template`, `generate_gaming_post`, `_generate_batch_image_post`, `_ai_scale_layout`), `meme_maker.py` (`fit_post`), `tests/test_howbig.py`

**Interfaces:**
- Consumes (Tarea 2): `howbig.pick_template`, `howbig.pick_topic`, `howbig.topic_block`, `howbig.TEMPLATE_LABELS`, `howbig.BankExhaustedError`.
- Produces:
  - `IMAGE_POST_PROFILES["howbig_image"]` con claves: `kind="howbig_post"`, `templates=howbig.TEMPLATE_LABELS`, `week=None`, `slot_hours=[11, 15, 22]`, `network_offsets={}`, `history_store="howbig_post_history"`, `composition=""`, `text_fill=(255, 255, 255)`, `ai_design="scale"`, `topics=True`, `max_hashtags={"instagram": 0, "facebook": 1}`, `default_trigger="give me the next How Big Really post"`, `label="Post How Big Really"`.
  - `batch_pipeline._ai_scale_layout(post: dict) -> str`.
  - `meme_maker.fit_post(base_path, out_path, size, trim: bool = True)`.
  - Las entradas del historial de este perfil llevan `topic_id`.

- [ ] **Step 1: Tests (rojo)** agregados a `tests/test_howbig.py` (leer antes `generate_gaming_post` y los tests existentes de posts como `tests/test_macrame_post.py` para copiar cómo se simulan Flow, el generador de texto y el historial; usar el mismo patrón):
  - el perfil existe con las claves de arriba y `batch_pipeline._text_system_prompt("howbig_post")` lee el prompt;
  - `_image_post_template("howbig_image", None)` con historial simulado devuelve una plantilla de `A/B/C` distinta de la última;
  - `generate_gaming_post(..., profile_key="howbig_image")` con el generador de texto simulado: el mensaje que recibe contiene `TOPIC FOR THIS POST`, el hecho del tema y la plantilla; el `image_prompt` que se manda a Flow termina con el sufijo de `_ai_scale_layout` y NO contiene `profile["composition"]`; se pide aspecto `"3:4"`; la imagen final se genera con `fit_post(..., SIZE_IG, trim=False)` (monkeypatch de `meme_maker.fit_post` y comprobar el argumento); el post devuelto trae `topic_id`;
  - un tema usado en el historial no se repite (siguiente llamada elige otro) y un banco agotado hace fallar con `BankExhaustedError` (mensaje "exhausted") sin llamar a Flow;
  - `meme_maker.fit_post` con `trim=False` no recorta una imagen casi negra de 810x1080 que `trim=True` sí recorta o deforma: crear con Pillow una imagen 810x1080 con fondo `(10,10,10)` y un rectángulo amarillo al centro; con `trim=False` el resultado es exactamente 1080x1350 y el rectángulo conserva su posición relativa (±2 %) — comprobar píxeles del centro.
  - `_parse_gaming_post` acepta una respuesta de ejemplo del nuevo prompt (con `BOTTOM:` vacío y `CAPTION` de varias líneas con línea en blanco).

- [ ] **Step 2: `prompts/howbig_post_system.md`** (LF; el prompt va en inglés porque la salida es en inglés; las instrucciones al modelo pueden ir en inglés). Contenido obligatorio, con este orden y estas subcadenas literales (los tests las buscan):

```
You write Facebook posts for the page "How Big Really": minimalist educational scale visuals for a US/UK audience. You create ONE post per answer, following the OUTPUT FORMAT exactly. Nothing outside the format.

IMPORTANT: do NOT output reasoning, analysis or thinking. Answer DIRECTLY with the format, no greetings, no markdown.

The user message gives you the template (A, B or C), the TOPIC FOR THIS POST (big subject, reference, fact, optional source, closing question) and recent hooks to avoid. Use ONLY the facts in the TOPIC: never add numbers, dates, comparisons or sources of your own. If the topic has no source line, write no Source line.

## IMAGE PROMPT TEMPLATES (fill the brackets, keep the rest word for word)
A - SCALE COMPARISON:
Facebook post image 4:5 portrait, matte black background #0A0A0A, minimal flat vector style, [BIG SUBJECT] huge vs [REFERENCE] small for scale comparison, small white human silhouette 1.8m for scale at bottom left, yellow accent #FFD60A, text '[TITLE IN 3 WORDS]' minimal, clean high contrast, educational scale visual, flat vector illustration, no photo, no shadow
B - MAP COMPARISON:
Facebook post image 4:5 portrait, matte black background #0A0A0A, minimal flat vector style, map comparison showing [MAP A] vs [MAP B] true size, [MAP A] in yellow #FFD60A, [MAP B] in white outline, scale bar 1000km at bottom, small human icon, text '[NAME vs NAME] true size comparison' minimal, educational map visual, flat vector illustration, no photo, no shadow
C - QUANTITY / STACK:
Facebook post image 4:5 portrait, matte black background #0A0A0A, minimal flat vector style, [QUANTITY] as [VISUAL SHAPE] vs [REFERENCE], small white human silhouette 1.8m for scale, yellow main object #FFD60A, minimal text '[NAME]', educational quantity visual, flat vector illustration, no photo, no shadow

## STYLE RULES (never break them)
- Always a matte black #0A0A0A background. Accent color only yellow #FFD60A, plus white. Nothing else.
- Always the small white human silhouette (1.8 m) at the bottom left as the page signature (template B uses a small human icon).
- Always flat vector illustration. Never a photo, never realistic textures.
- The title text is 3 simple English words (template B: "[NAME] vs [NAME] true size comparison"), spelled exactly.

## CAPTION FORMULA (5 lines, exactly this order)
Line 1 - SHOCK: one short sentence that breaks a common belief.
Line 2 - HARD FACT: the number and context from the TOPIC fact.
Line 3 - empty line.
Line 4 - OPEN QUESTION: the closing question from the TOPIC (rephrase lightly if needed). Never ask for likes, shares, follows or tags.
Line 5 - SOURCE: "Source: NAME" only if the TOPIC gives a source; otherwise omit this line.
Tone: American English. No emojis (one at most). No hashtags (one very niche hashtag at most). Never write "share this", "follow me", "like", "tag a friend" or "comment".
Closing by template: A = real measurement + which thing to scale next; B = km2 comparison + which map surprised you most; C = visual comparison + a guessing question.

## OUTPUT FORMAT (mandatory, this exact order, nothing before IDEA:)
IDEA: one short English sentence with the concept.
HOOK: the hook in 5-10 English words (internal, used to avoid repeats).
IMAGE_PROMPT: the filled template, one paragraph in English. Add: keep every element inside the central 90% of the frame.
TOP: the 3-word title (template B: "[NAME] vs [NAME]").
BOTTOM:
CAPTION: the 5-line caption above.

## RULES
- Nothing before "IDEA:" and nothing after the CAPTION. Use the 6 literal labels in order: IDEA, HOOK, IMAGE_PROMPT, TOP, BOTTOM, CAPTION. BOTTOM stays empty.
```

  (Mantener estas subcadenas exactas: `matte black background #0A0A0A`, `#FFD60A`, `white human silhouette 1.8m`, `A - SCALE COMPARISON`, `B - MAP COMPARISON`, `C - QUANTITY / STACK`, `CAPTION FORMULA`, `share this`, `Source:`, `IDEA:`, `BOTTOM:`.)

- [ ] **Step 3: `meme_maker.fit_post`**: agregar `trim: bool = True`; con `trim=False` no llamar a `_trim_dark_borders` (solo `_crop_to`). Los demás llamadores no cambian.
- [ ] **Step 4: `batch_pipeline.py`:**
  1. `import howbig` (al lado de los otros imports de módulos del proyecto) y el perfil `howbig_image` en `IMAGE_POST_PROFILES` (valores en Interfaces). Agregar `"howbig_image"` a la docstring de `create_project` donde se listan los `content_type`.
  2. `_image_post_template`: si `profile.get("topics")`: `return howbig.pick_template([e.get("template") for e in _load_gaming_history(profile["history_store"])])` (antes de la rama `rotate`).
  3. `_ai_scale_layout(post)`: devuelve (en inglés) el sufijo para Flow: `" Keep every element inside the central 90% of the frame, nothing near the edges. The title text must be spelled exactly as written: "<TOP>". No other text, no logos, no watermark."` usando `post["top"]` (sin comillas dobles; reemplazarlas por simples).
  4. `generate_gaming_post`: en la rama `else` de `template_message`, si `profile.get("topics")`: elegir `topic = howbig.pick_topic(template, {e.get("topic_id") for e in historial if e.get("topic_id")})`; `template_message = trigger_message + f"\n\nTemplate for this post: {template} ({profile['templates'][template]})." + howbig.topic_block(topic) + _gaming_history_block(store_name=...)`; guardar `post["topic_id"] = topic["id"]` tras parsear. Si la plantilla pedida no tiene tema libre, `pick_topic` ya cae a otra y el post usa la plantilla del tema elegido: **actualizar `template` al `topic["template"]`** y devolverlo en el post (`post["template"]`) para que el historial refleje la real. `BankExhaustedError` se propaga sin tragarse (falla clara).
  5. Generación de la imagen: `ai_design == "scale"` → `image_prompt = post["image_prompt"].rstrip() + _ai_scale_layout(post)`; aspecto `"3:4"` (no `"1:1"`) para este diseño; tras generar, `meme_maker.fit_post(base_path, dest_dir / "cover.jpg", meme_maker.SIZE_IG, trim=False)`. Los demás `ai_design` no cambian.
  6. `_generate_batch_image_post`: el historial agrega `"topic_id": post.get("topic_id")` y `template` usa `post.get("template", template)`; `v["template"]` igual.
- [ ] **Step 5:** el flujo de "post suelto" de `app.py` (`_run_image_post_job`, línea ~5351) llama a `generate_gaming_post` y escribe historial: leerlo y, si agrega entradas al historial, que incluya `topic_id` y la plantilla real; si no, no tocarlo (se cablea en la Tarea 5).
- [ ] **Step 6:** `python -m pytest tests/test_howbig.py -q` verde y suite completa verde; revisar bytes de control.

---

### Task 4: Horario automático por fase

**Files:**
- Modify: `batch_pipeline.py` (`_free_slot`, `_compute_schedule`, `create_project`), `tests/test_howbig.py`, `tests/test_schedule_slots.py` si hace falta (solo para no romperlo)

**Interfaces:**
- Consumes: perfil `howbig_image` (Tarea 3).
- Produces:
  - Constantes `HOWBIG_PHASE1_DAYS = 14`, `HOWBIG_PHASE1_HOURS = [11, 15, 22]`, `HOWBIG_PHASE2_HOURS = [11, 22]`, `HOWBIG_HOUR_END = 22`.
  - `howbig_hours_for_date(day: date, launch: date) -> list` — fase 1 si `(day - launch).days < 14`, si no fase 2.
  - `_howbig_launch_date() -> date` — lee `job_store.load("howbig_launch")["date"]`; si no existe, la guarda (hoy) y la devuelve; debe llamarse bajo `_lock` en `create_project`.
  - `_free_slot(desired, taken, rescue=False, hour_end=BATCH_HOUR_END)`: el rango de horas es `BATCH_HOUR_START..hour_end` (hoy 9-21); por defecto idéntico a hoy.
  - `_compute_schedule(..., hours_for_date=None, hour_end=BATCH_HOUR_END)`: con `hours_for_date` (función `date -> list[int]`) genera, día a día desde hoy, los horarios de cada día en orden hasta completar `total`, **saltando** los que ya pasaron (`< ahora + NATIVE_SCHEDULE_MIN_LEAD`) en vez de correr todo el lote; luego aplica `_free_slot(..., hour_end=hour_end)` con `taken` igual que hoy. Sin `hours_for_date` el comportamiento actual no cambia.

- [ ] **Step 1: Tests (rojo)** en `tests/test_howbig.py`, con `datetime` y `job_store` simulados (monkeypatch de `batch_pipeline.job_store`/`_load`/`_save`, mismo patrón de `tests/test_schedule_slots.py`; leerlo primero):
  - `howbig_hours_for_date`: día 0 y 13 → `[11, 15, 22]`; día 14 → `[11, 22]`;
  - `_compute_schedule(30, 3, None, hours_for_date=f, hour_end=22, taken=[])` con `launch` hace 10 días: los primeros 4 días tienen 3 horarios y luego 2 por día, todos entre 9 y 22, ninguno antes del adelanto mínimo, nunca más de 3 en un día, orden creciente, 30 resultados, y todos los `hour` ∈ {11, 15, 22};
  - un `taken` que ocupa las 22:00 de un día mueve ese post a la hora libre más cercana dentro de 9-22 (p. ej. 21 o 20), nunca a 23 ni antes de las 9;
  - `_free_slot` sin `hour_end` conserva el tope 21 (regresión: pedir las 22:00 devuelve ≤ 21);
  - `create_project("How Big Really", 10, 3, {"facebook": {"page_id": "1"}}, {}, content_type="howbig_image")` (con `_load/_save/existing_slots` simulados): `per_day == 3`, 10 horarios, el `scheduled_at` es de las horas permitidas, `_howbig_launch_date()` se guardó una sola vez (una segunda llamada no la cambia).
- [ ] **Step 2: Implementar** lo descrito. En `create_project`: si `content_type == "howbig_image"`: `per_day = len(HOWBIG_PHASE1_HOURS)`, `launch = _howbig_launch_date()` (bajo `_lock`), `hours_for_date = lambda d: howbig_hours_for_date(d, launch)`, `hour_end = HOWBIG_HOUR_END`, y pasar ambos a `_compute_schedule`; `fixed_hours` no se usa para este tipo. Para el resto de tipos nada cambia.
- [ ] **Step 3: `_free_slot` en otros llamadores:** `grep -n "_free_slot(" batch_pipeline.py app.py`; los llamadores que reagendan (por ejemplo al publicar) deben conservar su comportamiento; si alguno trabaja sobre posts de How Big Really (p. ej. reprogramar por lote caído), pasar `hour_end=HOWBIG_HOUR_END` cuando `item_type(project, video) == "howbig_image"`. Listar en el reporte cada llamador y la decisión.
- [ ] **Step 4:** tests verdes y suite completa verde.

---

### Task 5: Interfaz y rutas (`app.py`)

**Files:**
- Modify: `app.py` (CRLF), `tests/test_howbig.py`

**Interfaces:**
- Consumes: perfil `howbig_image`.
- Produces: opción `howbig_image` en los dos selectores de tipo de contenido; `isImagePostType` la incluye; trigger por defecto; la ruta de "post suelto" acepta `kind="howbig"`; al elegir este tipo la interfaz muestra la nota de horario y deja solo Facebook marcado y sin permitir Instagram/YouTube.

- [ ] **Step 1: Leer** el HTML y JS que rodean las líneas con `historias_image` en `app.py` (selectores de tipo ~817 y ~1201, `isImagePostType` ~2178, defaults ~2181, `GAMING_START_KIND` ~2610, mapeo `profile_key` ~5430, `default_trigger` ~5446, `max_hashtags` ~5530, `image_profile` ~6250) y el manejo de redes (casillas de Facebook/Instagram/YouTube según el tipo). Tests antes: la prueba existente de render de `/` (ver `tests/test_long_videos.py`, GET `/` con el cliente de Flask) debe verificar que el HTML contiene `value="howbig_image"` dos veces y la nota, y que `/api/meta/pages` (si existe test) devuelve páginas sin token.
- [ ] **Step 2: Cambios (patch por bytes, CRLF):**
  1. Ambos `<select>` de tipo: `<option value="howbig_image">Post de imagen — How Big Really (4:5, solo Facebook)</option>` (misma posición que las otras opciones de imagen).
  2. `isImagePostType` agrega `type === "howbig_image"`; el objeto de triggers por defecto agrega `howbig_image: "give me the next How Big Really post"`; `GAMING_START_KIND` agrega `howbig_image: "howbig"`.
  3. Ruta de post suelto: `profile_key = {"ninio": ..., "macrame": ..., "historias": ..., "howbig": "howbig_image"}.get(...)`.
  4. Lógica de redes: cuando el tipo del lote es `howbig_image`, marcar solo Facebook, desmarcar y deshabilitar Instagram y YouTube, y mostrar bajo el selector de horario/duración la nota "Horario automático: 3 posts al día los primeros 14 días y 2 al día después (hora Chile). Solo Facebook." (reutilizar la clase de nota ya existente en esa zona, p. ej. `pub-hint`). Al cambiar a otro tipo, volver al comportamiento anterior (rehabilitar).
  5. En el servidor, si un lote `howbig_image` llega con Instagram o YouTube activados, `create_project`/la ruta los descarta (solo `facebook`) en vez de fallar.
  6. Sin `{{`, `{%`, `{#` ni `"""` en lo agregado.
- [ ] **Step 3: Tests (rojo → verde):** el render de `/` contiene las opciones y la nota; una llamada a la ruta de creación de lote con `howbig_image` + Instagram devuelve un lote solo con Facebook (mismo patrón que los tests de rutas existentes: leer cómo prueban `/api/batch/...`).
- [ ] **Step 4:** suite completa verde; `python -c "import app"`; fin de línea CRLF intacto; sin bytes de control.

---

### Task 6: Registro de la página en `.env` y verificación (la hace el controlador)

**Files:**
- Modify: `.env` (ignorado por git; solo las 3 líneas `FB_PAGE_5_*`)

- [ ] **Step 1:** Con un script de Python (en el scratchpad, borrarlo después) reemplazar SOLO los valores de `FB_PAGE_5_ID`, `FB_PAGE_5_NAME` y `FB_PAGE_5_TOKEN` por el Page ID `1335338896337833`, el nombre `How Big Really` y el token que el usuario dio en el chat. Antes de escribir: hacer una copia de seguridad `.env.bak-howbig` fuera del repo (scratchpad), comprobar que el resto del archivo queda byte a byte igual (comparar contra la copia salvo esas 3 líneas) y que los slots 1-4 no cambian. Nunca imprimir ni registrar el token.
- [ ] **Step 2:** `python -c` con `dotenv` + `meta_auth.list_pages()` (no imprime tokens): la lista contiene `How Big Really` en la posición 5 y no `Bebé Héroe`. `meta_auth.validate(page_id)` (consulta Graph): informar solo `ok`/error y el motivo; no imprimir el token. Si el token falla (expirado/permisos), informarlo sin reintentos.
- [ ] **Step 3:** Prueba real del texto con el proveedor: generar 1 post por plantilla (`A`, `B`, `C`) con `generate_gaming_post` hasta el paso de texto (monkeypatch del paso de imagen) y comprobar: `IMAGE_PROMPT` con la plantilla correcta, `TOP` de 3 palabras (B: "X vs Y"), `CAPTION` en 5 líneas sin emojis ni hashtags ni "share this", `Source:` solo cuando el tema trae fuente, y que solo usa cifras del banco. Si Flow tiene sesión disponible, generar además 1 imagen y revisar visualmente (4:5, fondo negro, amarillo, silueta). No publicar nada. Borrar los archivos temporales.
- [ ] **Step 4:** Informar al usuario: resultados, temas del banco dudosos que el reporte de la Tarea 2 marcó, el recordatorio de regenerar el token (quedó en el chat) y que el servidor necesita reiniciarse para ver la página nueva. No reiniciar ni hacer commit.
