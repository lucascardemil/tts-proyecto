# HISTORIAS: formato fijo "Luna" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Las historias de HISTORIAS pasan a un formato fijo de ~42 s (5 pasos: gancho de espera, conflicto, giro con personaje bondadoso, reencuentro/rescate, cierre con "deja un corazón para [Nombre]") inspirado en el video de Luna, con validación del guion y texto de publicación alineado.

**Architecture:** Cambia el prompt del nicho (`prompts/historias_system.md`) y el pedido de estructura que va en el mensaje (`RESCUE_STORY_CRAFT`), se suma el patrón `mascota_perdida`, HISTORIAS pasa a largo fijo y sin pilares de valor, un validador nuevo (`validate_historia_script`) rechaza guiones fuera de formato con un error sanable (el lote regenera) y el copy de Facebook/Instagram pide el corazón al animal.

**Tech Stack:** Python (Flask app, `batch_pipeline.py`, `auto_pipeline.py`, `seo_optimizer.py`), prompts en Markdown, pytest.

**Spec:** `docs/superpowers/specs/2026-10-14-historias-formato-luna-design.md`

## Global Constraints

- Responder y comentar en español; textos de UI/prompts en español.
- **No hacer commit ni push** (solo cuando el usuario lo pida). Cada tarea termina con la suite en verde.
- Fines de línea según archivo (verificados): `batch_pipeline.py`, `seo_optimizer.py`, `prompts/*.md`, `tests/test_prompts_english.py` = **LF**; `auto_pipeline.py`, `app.py`, `tests/test_pilares_de_valor.py` = **CRLF** (en esos, parchear con un script que convierta a CRLF y compruebe `b.count(b"\r\n") == b.count(b"\n")`; no usar `Edit` con saltos de línea sueltos).
- Entorno de escritura: ya hubo archivos con `\b` convertido en un carácter de control y con `\\` colapsado. Después de escribir, escanear los archivos tocados por bytes < 0x20 que no sean TAB/LF/CR y releer toda regex/escape.
- HISTORIAS: largo **100–120 palabras** (~42 s a 170 palabras/min); el validador acepta **90–130**. Cierre: "deja un corazón para [Nombre]" escrito con la palabra *corazón* (no el emoji: el TTS lee el texto); **sin** "Comenta PALABRA". El ❤️ solo va en `HOOK_TEXT` y en el texto de publicación.
- Se conservan: formato de salida (HOOK_TEXT / PERSONAJES / Guion / Imagen N), 8–10 imágenes, prompts de imagen en inglés ("NUNCA en español"), transformación en dos escenas secuenciales, nombres evitados (Luna, Marcos, etc.), filtro de vocabulario de rescate.
- No tocar: voz, música, estilo de subtítulos, otras páginas (Macramé, Niño Selectivo, Bebé Héroe), posts de imagen (`historias_post_system.md`).
- Archivos temporales solo en el scratchpad de la sesión y se borran al terminar. El servidor (puerto 5000) no se reinicia dentro de este plan.

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `prompts/historias_system.md` | Prompt del nicho: 5 pasos, 100–120 palabras, cierre con corazón |
| `batch_pipeline.py` | `RESCUE_STORY_CRAFT`, patrón `mascota_perdida`, `_has_fixed_length`, `PILARES_KINDS`, marcador sanable, llamada al validador en `_generate_batch_video` |
| `auto_pipeline.py` | `validate_historia_script`, `validate_historia_story_text` |
| `app.py` | Llamada al validador en la ruta manual (`_run_pipeline_job`) |
| `prompts/historias_copy_system.md`, `seo_optimizer.py` | Petición final "un ❤️ para [Nombre]" y su respaldo |
| `tests/test_historias_formato_luna.py` (nuevo), `tests/test_pilares_de_valor.py`, `tests/test_prompts_english.py` | Pruebas |

---

### Task 1: Prompt de 5 pasos, patrón nuevo y largo fijo sin pilares

**Files:**
- Modify: `prompts/historias_system.md` (reescritura completa), `batch_pipeline.py` (`PILARES_KINDS`, `RESCUE_STORY_CRAFT`, `STORY_PATTERNS`, `_has_fixed_length`), `tests/test_pilares_de_valor.py` (CRLF), `tests/test_prompts_english.py`
- Test: `tests/test_historias_formato_luna.py` (nuevo)

**Interfaces:**
- Produces: `bp.STORY_PATTERNS["mascota_perdida"]`, `bp.PILARES_KINDS == set()`, `bp._has_fixed_length(page)` verdadero para las páginas cuyo `_text_kind_for_page` es `"historias"`.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_historias_formato_luna.py`:

```python
# -*- coding: utf-8 -*-
"""HISTORIAS: formato fijo de 5 pasos (~42 s), cierre con corazón para el animal."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import auto_pipeline as ap
import batch_pipeline as bp

PROMPT = bp._PROMPTS_DIR / "historias_system.md"


def test_historias_prompt_names_the_five_steps_and_the_fixed_length():
    text = PROMPT.read_text(encoding="utf-8")
    for step in ("Gancho de espera", "Conflicto", "Giro", "Reencuentro", "Cierre"):
        assert step in text, step
    assert "100 a 120 palabras" in text
    assert "corazón" in text
    assert "Comenta" not in text and "comenta" not in text  # el cierre ya no pide palabra clave
    for kept in ("HOOK_TEXT:", "PERSONAJES:", "NUNCA en español", "8 a 10 imágenes"):
        assert kept in text, kept


def test_rescue_story_craft_matches_the_prompt():
    craft = bp.RESCUE_STORY_CRAFT
    assert "corazón" in craft and "omenta" not in craft
    assert "100 a 120 palabras" in craft
    assert "giro" in craft.lower() and "reencuentro" in craft.lower()


def test_new_pattern_has_the_highest_weight_and_rescue_block_uses_it():
    weights = {k: v[0] for k, v in bp.STORY_PATTERNS.items()}
    assert "mascota_perdida" in weights
    assert weights["mascota_perdida"] == max(weights.values()) and list(weights.values()).count(max(weights.values())) == 1
    assert "cartel" in bp.STORY_PATTERNS["mascota_perdida"][1]
    assert "mascota_perdida" in bp.STORY_PATTERNS and bp.rescue_story_block("mascota_perdida").endswith(
        bp.STORY_PATTERNS["mascota_perdida"][1])


def test_historias_is_fixed_length_and_has_no_value_pillars():
    assert bp._has_fixed_length("HISTORIAS")
    assert bp._has_fixed_length("MACRAME CREATIVO") and bp._has_fixed_length("Bebé Héroe")
    assert bp.PILARES_KINDS == set()
    assert "PILARES DE VALOR" not in bp._text_system_prompt("historias")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_historias_formato_luna.py -q`
Expected: FAIL (el prompt aún no tiene los pasos, falta el patrón, `PILARES_KINDS` no está vacío).

- [ ] **Step 3: Reescribir `prompts/historias_system.md`**

Reemplazar el archivo completo (LF) por:

```markdown
Eres un escritor de historias virales en español sobre animales rescatados.
Escribes UNA historia original y recreada (ficción con tono real, nunca
presentada como real) para un video corto de redes sociales de unos 42
segundos. Sigues EXACTAMENTE el formato de salida. No agregues nada fuera del
formato.

IMPORTANTE: NO generes razonamiento, análisis, ni proceso de pensamiento.
Responde DIRECTAMENTE con el formato estructurado solicitado. Nada de
"Okay, let me think..." o "The user wants..." — solo el output final.

## ESTRUCTURA OBLIGATORIA (5 pasos, en este orden):
1) **Gancho de espera con plazo:** la primera frase lleva un número o plazo
   concreto y una consecuencia ("Nuri esperó siete meses en el mismo banco.
   Todos pasaban... nadie la veía.", "Le quedaban tres días"). Plantea un
   misterio y un protagonista vulnerable.
2) **Conflicto y adversidad:** qué cambió (se mudó su dueño, lo separaron,
   quedó solo) y cómo lo soporta mientras pasa el tiempo y cambia el clima
   (esa tarde, con frío, con lluvia, semanas más tarde). Lugar específico y
   DIFERENTE en cada historia, internacional (rota país y continente: un
   pueblo de España, un puerto de Japón, una aldea en los Andes, una ciudad de
   México...). Incluye el juicio de la gente ("todos pensaban que era...").
3) **Giro con personaje bondadoso:** un personaje secundario (panadero,
   vecina, guardia, niño, cartero...) nota lo que los demás ignoraban (un
   cartel viejo, un collar, una costumbre diaria) y entiende quién es el
   animal o qué espera: no era lo que todos creían.
4) **Reencuentro o rescate emotivo:** con una acción concreta y una
   transformación visible (la persona lo reconoce y el animal la reconoce al
   instante, o llega a su hogar definitivo); final esperanzador.
5) **Cierre emocional:** una frase de esperanza con el nombre del animal y la
   invitación a dejar un corazón. Forma de ejemplo: "Hoy Nuri ya no espera
   sola. Si tú también crees que ninguna historia debe terminar en la calle,
   deja un corazón para Nuri." Escribe la palabra "corazón" (nunca el emoji:
   la narración se lee en voz alta). No pidas una palabra clave ni prometas
   "Parte 2".

## PATRÓN TEMÁTICO
El mensaje trae el patrón temático de esta historia (separación, espera,
mascota perdida, promesa cumplida...). Adáptalo a los 5 pasos: el paso 3 puede
ser el rescatista que descubre la razón oculta, y el paso 4 un reencuentro o un
nuevo hogar.

## REGLAS DE PERSONAJES Y CIERRE:
- El personaje rescatista o bondadoso rota y es diferente en cada historia
  (distintas culturas, edades, profesiones). Incluye su descripción
  completa en PERSONAJES y usa su etiqueta en los prompts.
- La última imagen muestra la transformación (feliz en su hogar o ya reunido).
- Asegura transformación visible o misterio resuelto.
- NO gráfico/violento: sin sangre, heridas, crueldad, muerte explícita.
- NO prometas partes futuras ("Parte 2") ni cites este instructivo.

## FORMATO DE SALIDA (obligatorio, en este orden exacto):

HOOK_TEXT: 3 a 6 palabras + un emoji, expresando una contradicción
(ej: "Todos lo odiaban por ladrón 🐕", "Nadie quería acercarse a él 🌧️").

PERSONAJES:
[NOMBRE]: descripción 100% EN INGLÉS (never Spanish: "8 years old", no "8 años"; "golden fur", no "pelaje dorado") de 40-60 palabras: especie/raza, edad, tamaño,
color exacto y patrón de pelaje, ojos, rasgos distintivos, ropa/accesorios.
Usa nombres de etiqueta en MAYÚSCULAS (ej: [PERRO], [GATA], [RESCATISTA]).

NOMBRES EN LA NARRACIÓN: inventa un nombre propio distinto y poco común para el
animal y otro para la persona en CADA historia; varía origen y sonido (Tobías,
Nuri, Bruno, Zoe, Iker, Amaya, Chispa, Ñaño, Mirta...). Evita los nombres más
repetidos: Luna, Max, Bella, Rocky, Canela, Marco, Marcos, Mateo, Sofía.
Si el mensaje trae "Nombres y lugares ya usados", no uses ninguno de ellos.

Guion
Narración completa en español, de 100 a 120 palabras (unos 42 segundos),
escrita en un solo bloque, desarrollando los 5 pasos de la estructura
obligatoria y cerrando con el corazón para el animal.

Imagen 1
Frase del guion: «excerpt EXACTO de una frase de la narración»
Prompt: 70-100 palabras EN INGLÉS, 9:16 vertical, photorealistic, warm and
hopeful: plano y ángulo de cámara, acción, expresión, escenario con detalles
concretos, luz y atmósfera. Repite SIEMPRE la descripción física completa
del animal usando su etiqueta [NOMBRE].

Imagen 2
Frase del guion: «...»
Prompt: [...]

... (continúa hasta Imagen 8-10, mismo formato de 3 líneas por imagen)

REGLAS:
- Nada antes de "HOOK_TEXT:" (no saludes, no expliques, sin markdown).
- "PERSONAJES:" va antes del bloque "Guion".
- Cada "Imagen N" tiene exactamente 3 líneas: "Imagen N", "Frase del guion: «...»"
  y "Prompt: [...]".
- Prompts de imagen SIEMPRE EN INGLÉS, NUNCA en español ni una sola palabra
  (la IA de imágenes rinde mucho mejor en inglés). Cada prompt es una
  descripción visual en inglés natural y concreta, por ejemplo: "9:16 vertical,
  photorealistic, warm and hopeful: medium close-up of [PERRO] looking up at the
  old fisherman on a wooden pier at sunrise, soft golden backlight, gentle
  expression". Solo la narración, la "Frase del guion" y HOOK_TEXT van en español.
- De 8 a 10 imágenes siguiendo los 5 pasos: espera con luz cálida, el mismo
  lugar con frío o lluvia, el animal esperando en primer plano, el personaje
  bondadoso, el personaje con el animal, el reencuentro con su persona, el
  abrazo y el animal feliz en su hogar. La Imagen 1 es el momento más emotivo,
  nunca el más crudo.
- LA TRANSFORMACIÓN SE MUESTRA EN 2 ESCENAS SECUENCIALES (NO split screen):
  Imagen penúltima: el animal en transición (llegando, siendo recibido, el
  reencuentro, primera noche en casa, etc.).
  Imagen final: el animal ya transformado, feliz en su hogar definitivo.
- EVITA split screen / pantalla dividida / before-after en una sola imagen:
  causa glitches en la animación. Usa dos escenas separadas.
- El Guion cierra con una frase de esperanza y la invitación a dejar un
  corazón para el animal, usando su nombre.
```

- [ ] **Step 4: Cambios en `batch_pipeline.py`**

(a) `PILARES_KINDS`: reemplazar el comentario y el valor (líneas ~46-49):

```python
# Guiones largos que llevan "pilares de valor" (prueba tras el gancho + micro-promesas).
# Hoy ninguno: HISTORIAS usa un formato fijo de ~42 s con su propia estructura (prompts/historias_system.md).
PILARES_KINDS: set = set()
```

(b) `RESCUE_STORY_CRAFT`: reemplazar la constante completa por (mantener `"\n\n"` inicial y estilo de string concatenado):

```python
RESCUE_STORY_CRAFT = (
    "\n\nESTRUCTURA (obligatoria, 5 pasos, video de unos 42 segundos): 1) Gancho de espera: la primera frase "
    "lleva un numero o plazo concreto y una consecuencia (\"Nuri espero siete meses en el mismo banco. Todos "
    "pasaban... nadie la veia.\"). 2) Conflicto y adversidad: que cambio y como lo soporta con el tiempo y el "
    "clima (con frio, con lluvia, semanas mas tarde); lugar especifico y DIFERENTE en cada historia, "
    "internacional (rota pais y continente: un pueblo de Espana, un puerto de Japon, una aldea en los Andes, "
    "una ciudad de Mexico, etc.; NUNCA siempre el mismo pais) y el juicio de la gente (\"todos pensaban "
    "que era...\"). 3) Giro con personaje bondadoso: alguien (panadero, vecina, guardia, nino, cartero) nota lo "
    "que los demas ignoraban (un cartel viejo, un collar, una costumbre diaria) y entiende quien es el animal "
    "o que espera: no era lo que todos creian. 4) Reencuentro o rescate emotivo con una accion concreta y una "
    "transformacion visible (su persona lo reconoce y el animal la reconoce al instante, o llega a su hogar); "
    "final esperanzador. 5) Cierre: una frase de esperanza con el nombre del animal y la invitacion a dejar un "
    "corazón (\"Hoy Nuri ya no espera sola... deja un corazón para Nuri\"); escribe la palabra corazón, nunca el "
    "emoji, y no pidas una palabra clave ni prometas Parte 2. El guion de narracion tiene de 100 a 120 "
    "palabras. El personaje bondadoso o rescatista es DIFERENTE en cada historia: cambia nombre, edad, genero y "
    "ocupacion (nunca reuses el nombre de una historia anterior). Incluye su ficha completa en PERSONAJES y "
    "usa su etiqueta en los prompts. La ultima imagen muestra la transformacion (feliz en su hogar o ya "
    "reunido). Si la historia no tiene una transformacion visible o un misterio resuelto, cambia la idea."
)
```

(El test busca literalmente `"100 a 120 palabras"`, `"corazón"`, `"giro"` y `"reencuentro"` (sin distinguir mayúsculas en los dos últimos) en `RESCUE_STORY_CRAFT`.)

(c) `STORY_PATTERNS`: agregar al final del diccionario (antes de `}`):

```python
    "mascota_perdida": (18, "Mascota perdida: el animal espera cada dia en un mismo lugar (un banco, una puerta, una parada) despues de que su dueno se mudo o lo perdio; alguien del lugar nota que no es callejero (un cartel, un collar, una costumbre) y encuentra a su persona; final de reencuentro."),
```

(d) `_has_fixed_length`: reemplazar la función por:

```python
def _has_fixed_length(page_name: str) -> bool:
    """Paginas cuyo video tiene un largo fijo (el prompt lo pide): Bebé Héroe (18 s), los anuncios de
    ebook de Niño Selectivo (~14 s) y Macramé (~17 s), e HISTORIAS (~42 s; cualquier pagina que use el
    prompt de historias). La duracion elegida en el lote no les cambia el guion."""
    return (is_bebe_heroe_page(page_name) or ad_kind_for_page(page_name) is not None
            or _text_kind_for_page(page_name) == "historias")
```

- [ ] **Step 5: Ajustar los tests existentes**

`tests/test_prompts_english.py` línea 60: `assert not bp._has_fixed_length("HISTORIAS") and bp._has_fixed_length("MACRAME CREATIVO")` → `assert bp._has_fixed_length("HISTORIAS") and bp._has_fixed_length("MACRAME CREATIVO")`.

`tests/test_pilares_de_valor.py` (CRLF; parchear con script): reemplazar el contenido de las dos funciones de test por:

```python
def test_no_niche_gets_the_value_pillars_anymore():
    assert bp.PILARES_KINDS == set()
    for kind in ("historias", "bebe_heroe", "ninio_selectivo", "macrame", "gaming_news", "ninio_post", "macrame_post", "gaming"):
        assert "PILARES DE VALOR" not in bp._text_system_prompt(kind)
```

(se elimina `test_long_script_niches_get_the_value_pillars` y `test_fixed_format_and_image_posts_do_not_get_them`; actualizar el docstring del archivo a "HISTORIAS ya no usa pilares de valor: su formato fijo trae su propia estructura"; el archivo `prompts/_pilares_de_valor.md` queda en el repo).

- [ ] **Step 6: Correr los tests**

Run: `venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS (toda la suite). Revisar que no queden `tests/` referenciando `"Comenta"` de HISTORIAS (`grep -rn "Comenta" tests/`).

---

### Task 2: Validación del guion (sanable) en el lote y en la ruta manual

**Files:**
- Modify: `auto_pipeline.py` (CRLF; junto a `validate_ad_story`), `batch_pipeline.py` (marcador sanable + llamada en `_generate_batch_video`), `app.py` (CRLF; llamada en `_run_pipeline_job`)
- Test: `tests/test_historias_formato_luna.py`

**Interfaces:**
- Produces: `ap.validate_historia_script(script: str) -> None`, `ap.validate_historia_story_text(story_text: str, story_id: str) -> None`, ambas lanzan `PipelineError("Guion de historia fuera de formato: ...")`; constantes `ap.HISTORIA_MIN_WORDS = 90`, `ap.HISTORIA_MAX_WORDS = 130`.

- [ ] **Step 1: Escribir los tests que fallan** (agregar a `tests/test_historias_formato_luna.py`)

```python
GOOD = (
    "Nuri esperó siete meses en el mismo banco de una plaza de Oaxaca. Todos pasaban a su lado... nadie la veía. "
    "Su dueño se mudó una noche de tormenta y ella volvió cada tarde, con frío y con lluvia, a la misma hora. "
    "La gente pensaba que era una gata callejera más. Hasta que Camila, la panadera, notó el lazo rojo en su "
    "cuello y un cartel viejo pegado en la farmacia. Era Nuri. Su dueño la buscaba desde hacía meses y cruzó la "
    "plaza corriendo. Cuando la vio, Nuri lo reconoció al instante y saltó a sus brazos. Hoy Nuri ya no espera "
    "sola. Si tú también crees que ninguna historia debe terminar en la calle, deja un corazón para Nuri."
)


def test_good_historia_script_passes():
    n = len(GOOD.split())
    assert ap.HISTORIA_MIN_WORDS <= n <= ap.HISTORIA_MAX_WORDS, n
    ap.validate_historia_script(GOOD)


@pytest.mark.parametrize("script, fragment", [
    (" ".join(GOOD.split()[:60]) + " deja un corazón para Nuri.", "palabras"),
    (GOOD + " " + GOOD, "palabras"),
    (GOOD.replace("corazón", "like"), "corazon"),
    (GOOD.replace("deja un corazón para Nuri.", "Comenta GATA si la habrías ayudado."), "corazon"),
    (GOOD.replace("deja un corazón para Nuri.", "deja un corazón para Nuri. Comenta GATA."), "comenta"),
])
def test_bad_historia_scripts_are_rejected_with_a_healable_error(script, fragment):
    with pytest.raises(ap.PipelineError) as err:
        ap.validate_historia_script(script)
    assert fragment in str(err.value).lower()
    assert bp._is_healable_error(str(err.value))  # el lote regenera el guion


def test_validate_historia_story_text_reads_the_script_block():
    story_text = (
        "HOOK_TEXT: Nadie la veía 🌧️\n\nPERSONAJES:\n[GATA]: a white cat\n\nGuion\n" + GOOD + "\n\n"
        "Imagen 1\nFrase del guion: «Nuri esperó siete meses en el mismo banco»\n"
        "Prompt: 9:16 vertical, photorealistic: a white cat on a bench\n"
    )
    ap.validate_historia_story_text(story_text, "t1")
    with pytest.raises(ap.PipelineError):
        ap.validate_historia_story_text(story_text.replace(GOOD, "Muy corto."), "t1")
```

(Si `extract_script`/`load_story_from_text` necesitan más estructura para el caso `story_text`, ajustar el texto de prueba al formato real que acepta el parser, sin tocar el parser.)

- [ ] **Step 2: Correr y ver que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_historias_formato_luna.py -q`
Expected: FAIL (no existen los validadores).

- [ ] **Step 3: Implementar en `auto_pipeline.py`** (CRLF; insertar justo antes de `def validate_ad_story`, con un script de parcheo CRLF; asegurarse de que `unicodedata` esté importado, si no, agregarlo a los imports estándar)

```python
HISTORIA_MIN_WORDS = 90   # el prompt pide 100-120 (~42 s); margen a los lados
HISTORIA_MAX_WORDS = 130


def validate_historia_script(script: str) -> None:
    """El guion de HISTORIAS mide ~42 s (100-120 palabras), cierra pidiendo un corazon para el animal
    y no usa "Comenta PALABRA". El mensaje lleva el marcador sanable "guion de historia fuera de
    formato" para que el lote regenere."""
    text = script.strip()
    plain = unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode().lower()
    words = len(text.split())
    problems = []
    if not HISTORIA_MIN_WORDS <= words <= HISTORIA_MAX_WORDS:
        problems.append(f"{words} palabras (deben ser {HISTORIA_MIN_WORDS}-{HISTORIA_MAX_WORDS})")
    if "corazon" not in " ".join(plain.split()[-30:]):
        problems.append("no cierra pidiendo un corazon para el animal")
    if "comenta" in plain:
        problems.append("usa 'comenta' en vez de pedir un corazon")
    if problems:
        raise PipelineError("Guion de historia fuera de formato: " + "; ".join(problems))


def validate_historia_story_text(story_text: str, story_id: str) -> None:
    """Valida el guion dentro de la respuesta completa del modelo (ruta manual): el bloque "Guion" o,
    si no viene separado, las frases de cada imagen en orden."""
    script = extract_script(story_text)
    if not script.strip():
        story = load_story_from_text(story_text, story_id)
        script = " ".join(p["frase"] for p in sorted(story["prompts"], key=lambda p: p["index"]))
    validate_historia_script(script)
```

Nota: los mensajes de error usan "corazon" sin tilde (por eso los fragmentos de los tests también); el texto de usuario del guion sí lleva "corazón".

- [ ] **Step 4: Marcador sanable en `batch_pipeline.py`**

En `_HEALABLE_EXTRA_MARKERS`, agregar después de `"no cierra con comenta ebook",...`:

```python
    "guion de historia fuera de formato",  # largo o cierre fuera del formato de ~42 s: otro intento suele cumplir
```

- [ ] **Step 5: Llamada en `_generate_batch_video`** (`batch_pipeline.py`)

Justo después de la línea `if ad_kind and "ebook" not in script_text.lower(): raise RuntimeError(...)` y antes de `if not _has_fixed_length(...)`:

```python
        if _text_kind_for_page(_project_page_name(project)) == "historias":
            auto_pipeline.validate_historia_script(script_text)
```

Verificar con `grep -n "_generate_batch_video" batch_pipeline.py app.py` que esta función solo sirve a lotes de historias/anuncios/Bebé Héroe y no a los de gaming (los de gaming no pasan por `_text_kind_for_page`); si algún flujo no-historias cae en `"historias"` por defecto, reportarlo.

- [ ] **Step 6: Llamada en la ruta manual** (`app.py`, CRLF, parchear con el helper)

En `_run_pipeline_job`, dentro del bloque `try:` que genera la historia, justo después del bloque `if text_kind in batch_pipeline.AD_KINDS: ... validate_ad_story(...)` y antes de `if bebe:`:

```python
            if text_kind == "historias":
                auto_pipeline.validate_historia_story_text(story_text, story_id)
```

(El `except Exception` de ese bloque ya registra el error en el job.)

- [ ] **Step 7: Correr los tests**

Run: `venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS. `app.py` sigue en CRLF puro.

---

### Task 3: Texto de publicación con corazón para el animal

**Files:**
- Modify: `prompts/historias_copy_system.md`, `seo_optimizer.py` (`_rescue_cta`)
- Test: `tests/test_historias_formato_luna.py`

**Interfaces:**
- Produces: `seo_optimizer._rescue_cta(script_text, offset)` devuelve el cierre del guion cuando es una petición de corazón.

- [ ] **Step 1: Escribir los tests que fallan** (agregar a `tests/test_historias_formato_luna.py`)

```python
import seo_optimizer as so


def test_copy_prompt_asks_for_a_heart_for_the_animal_not_a_keyword():
    text = (bp._PROMPTS_DIR / "historias_copy_system.md").read_text(encoding="utf-8")
    assert "❤️ para" in text
    assert "palabra clave" in text  # aclara que NO se pide


def test_rescue_cta_reuses_the_heart_closing_of_the_script():
    cta = so._rescue_cta(GOOD)
    assert "corazón" in cta and "Nuri" in cta
    # sin cierre de corazón sigue rotando las preguntas de siempre
    other = so._rescue_cta("Una historia corta sin pregunta final.")
    assert other in so._RESCUE_CTA_QUESTIONS


def test_rescue_copy_ends_with_the_heart_request():
    fb = so.suggest_rescue_copy(GOOD, "facebook")
    assert "corazón para Nuri" in fb
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_historias_formato_luna.py -q`
Expected: FAIL.

- [ ] **Step 3: `prompts/historias_copy_system.md`**

Reemplazar la viñeta de cierre (líneas 17-18 originales: "Cierra con UNA petición simple de reacción o comentario (ej: ...)") por:

```markdown
- Cierra con UNA petición simple de reacción dirigida al animal: un corazón
  para él o ella, con su nombre (ej: "¿Me dejas un ❤️ para Nuri?", "Deja un ❤️
  para Nuri si crees que merecía un final feliz"). No pidas una palabra clave
  ni que comenten una palabra en mayúsculas.
```

(el resto del archivo queda igual: formato FACEBOOK/INSTAGRAM, sin voseo, hashtags, distintos entre sí).

- [ ] **Step 4: `seo_optimizer._rescue_cta`** (archivo LF)

Reemplazar el cuerpo por:

```python
def _rescue_cta(script_text: str, offset: int = 0) -> str:
    """Cierre: el del propio guion si termina en una pregunta corta o en la peticion de un corazon
    para el animal (nace de la historia, §7.8); si no, una pregunta del set rotando por guion."""
    last = (_sentences(script_text) or [""])[-1]
    if (last.endswith("?") or "corazón" in last.lower()) and len(last) <= 130:
        return last
    return _RESCUE_CTA_QUESTIONS[(sum(map(ord, script_text)) + offset) % len(_RESCUE_CTA_QUESTIONS)]
```

Si el test `test_rescue_copy_ends_with_the_heart_request` falla porque `_sentences` parte el guion antes de "deja un corazón para Nuri." (p. ej. por la coma), ajustar solo la lógica de detección dentro de `_rescue_cta` (no `_sentences`) hasta que el CTA devuelto contenga "corazón para Nuri"; y que el total siga en 200-300 caracteres como exige el docstring de `suggest_rescue_copy` (revisar con una aserción de longitud ≤ 300 + hashtags si el test lo permite).

- [ ] **Step 5: Correr los tests**

Run: `venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS.

---

### Task 4: Verificación real y cierre

**Files:** ninguno nuevo; scratch solo en el scratchpad.

- [ ] **Step 1: Suite completa**

Run: `venv/Scripts/python.exe -m pytest tests -q`
Expected: todo en verde (los warnings preexistentes de google.api_core y Pillow son normales).

- [ ] **Step 2: Guiones reales (si hay proveedor de texto)**

En el scratchpad, un script que arme el mensaje como el lote (`auto_pipeline.build_trigger_message("dame una historia", None, kind="historias")` + `batch_pipeline.rescue_story_block("mascota_perdida")` y otro patrón), llame a `batch_pipeline._generate_text_with_chain("historias", "HISTORIAS", mensaje, "luna_test")` 3 veces y por cada respuesta: `_split_hook_text`, `load_story_from_text`, `validate_prompts_english`, `extract_script` + `validate_historia_script`, y muestre: palabras del guion, HOOK_TEXT, número de imágenes y el cierre. Si `text_provider.available_backends()` está vacío, avisar que no se pudo verificar. No debe escribir en el estado de lotes ni publicar nada; borrar los archivos temporales (`output/`, `scratch/historia_luna_test.json`) y el script al terminar.

- [ ] **Step 3: Informe**

Informar: guiones obtenidos (palabras, cierre), cuántos pasaron el validador a la primera, qué falló y por qué. Si el modelo se pasa sistemáticamente del rango de palabras, proponer (sin aplicarlo) ajustar el rango o el prompt. No reiniciar el servidor ni hacer commit sin que el usuario lo pida.
