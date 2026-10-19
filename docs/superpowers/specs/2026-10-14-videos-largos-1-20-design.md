# Videos de más de 1:20 en las páginas con guion — diseño

Fecha: 2026-10-14. Estado: diseño aprobado por el usuario en el chat, pendiente de revisión del spec escrito y de plan.
Parte de la base `4dc54b4` (formato HISTORIAS "Luna", fichas detalladas y sanado de fallos del proveedor de texto, ya publicado).

## Contexto y alcance

El usuario pidió que **todos los videos de todas las páginas duren más de 1:20**. Hoy cada página tiene un largo fijo distinto: HISTORIAS ~42 s, anuncios de ebook de Niño Selectivo y Macramé ~14-17 s, Bebé Héroe 18 s. Decisiones tomadas en el chat:

| Tema | Decisión |
|---|---|
| Duración objetivo | **~90 s** (1:30), para que ningún video baje de 1:20 aunque la voz sea más rápida. |
| Anuncios de ebook | **También pasan a 1:20+**, como testimonio largo con el mismo cierre "Comenta EBOOK" y el celular con la portada al final. |
| Imágenes | **16 a 20 por video** (una cada ~5 s), en vez de 5 a 10. |
| Gaming (clips de Medal) | **Fuera de este spec.** Los clips de origen duran 8-35 s (`gaming_clip.MIN_SECONDS, MAX_SECONDS = 8, 35`); llegar a 1:20 exige compilaciones de 3-4 clips, que es un subsistema aparte con su propio spec. Los posts de imagen y memes no son videos. |
| Publicación | Sin cambios: Facebook sube por `/videos` (sin tope de Reels de 90 s) e Instagram por `REELS`. |

Páginas dentro del alcance (las que generan guion + voz + imágenes): **HISTORIAS** (y cualquier página que use el prompt `historias` por defecto), **Niño Selectivo**, **MACRAME CREATIVO** y **Bebé Héroe**.

## Decisiones técnicas

- **Largo en palabras.** La voz mide entre 158 y 189 palabras/minuto (`SPANISH_WORDS_PER_MINUTE = 170`, calibrado con audios reales). Para no bajar de 80 s ni con la voz más rápida (189 wpm) hace falta como mínimo ~252 palabras. El prompt pide **260 a 290 palabras** (~92 s a 170 wpm) y el validador acepta **250 a 300**.
- **Garantía de duración real.** Tras generar la voz se mide el audio con `video_maker.get_audio_duration`. Si dura menos de `MIN_VIDEO_SECONDS = 80` el video falla con un error sanable ("video más corto que 1:20") y el lote regenera. Contar palabras no alcanza; esto es la red de seguridad.
- **Constantes en `auto_pipeline.py`:** `MIN_VIDEO_SECONDS = 80`, `LONG_SCRIPT_MIN_WORDS = 250`, `LONG_SCRIPT_MAX_WORDS = 300`, `LONG_IMAGES_MIN = 14`, `LONG_IMAGES_MAX = 22` (el prompt pide 16-20; el margen evita rechazar por una imagen de más o de menos).
- **Todas las páginas con guion son de largo fijo.** `_has_fixed_length` ya no necesita distinguir: la duración elegida en el lote o en "Generar video automático" no cambia el guion. El selector de duración del panel (`pipeline-duration`, `lote-duration`) deja de tener efecto: se quita o se deja con la nota "Todos los videos duran ~1:30".
- **Bebé Héroe deja su línea de tiempo rígida de 18 s** (`build_bebe_heroe_props`, `BEBE_HEROE_SCENE_BOUNDS`, mezcla de voces por frase con efectos de sonido y zooms fijos por escena) y usa el camino genérico de video largo (`video_maker.build_props` con subtítulos). Se conserva la voz de niño (`_bebe_heroe_voice`) y la ficha fija del bebé. **Se pierden** los efectos de sonido por escena y los 4 zooms fijos; es el costo de pasar de 4 escenas a 16-20. `build_bebe_heroe_props` queda sin uso (no se borra en esta entrega).

## Cambios por archivo

### Prompts (`prompts/*_system.md`)

- **`historias_system.md`:** los mismos 5 pasos de "Luna" con más desarrollo por paso (el conflicto con más de una adversidad, más de un momento en el giro, un reencuentro con detalle) en **260 a 290 palabras** (~24 a 28 frases), **16 a 20 imágenes**. El cierre "deja un corazón para [Nombre]" no cambia. Mantiene fichas detalladas, `[LUGAR]` y los prompts de imagen de 90 a 130 palabras.
- **`ninio_selectivo_system.md` y `macrame_system.md`:** el anuncio de 38-46 palabras pasa a un testimonio de **260 a 290 palabras** con beats ampliados: gancho que rompe una creencia, problema vivido con detalle, intentos que fallaron, descubrimiento del ebook, qué trae (pasos en fotos / nudos paso a paso), primer resultado, prueba, para quién es (sin promesas falsas) y cierre exacto "Comenta EBOOK y te envío la guía". **16 a 20 imágenes** dentro del mismo set fijo (`[COCINA]` / `[ESCENA]`) y las mismas fichas; prompts de 70 a 100 palabras (se mantiene para acotar la salida). Reglas ya conocidas se conservan: solo español en el guion, sin abreviaturas, sin "YouTube/boho/DIY".
- **`bebe_heroe_system.md`:** de 4 escenas / 18 s con frases de 4-9 palabras a una mini-historia en primera persona de **260 a 290 palabras** (voz de niño, español neutro, sin voseo), con la misma estructura de problema → empatía → acción → cierre pero desarrollada en 16-20 imágenes, y cierre con pregunta que invita a comentar. Ficha fija `[BEBE]` sin cambios; el animal u objeto con ficha de 90 a 120 palabras como en HISTORIAS.

### `auto_pipeline.py`

- Constantes nuevas (arriba) y `validate_long_script_length(script)`: lanza `PipelineError` con el marcador sanable "guion fuera de largo" si no está entre 250 y 300 palabras. `validate_historia_script` (cierre con "corazón", sin "comenta") lo usa en lugar de su rango 90-130, que se elimina.
- `validate_story_image_count(story)`: entre 14 y 22 imágenes; marcador "imágenes fuera de rango".
- `validate_min_duration(audio_path)`: marcador "video más corto que 1:20".
- `build_trigger_message`: ya no agrega la meta de palabras por duración elegida cuando el guion es de largo fijo. Las variantes de fichas (`HISTORIAS_SHEET_REQUEST`, `NINIO_SHEET_REQUEST`, `MACRAME_SHEET_REQUEST`) piden "entre 16 y 20 imágenes" en vez de "las 5 imágenes"; `CHARACTER_SHEET_REQUEST` (Bebé Héroe) se alinea.

### `batch_pipeline.py` y `app.py`

- `_generate_batch_video` y `_run_pipeline_job`: llaman `validate_long_script_length` y `validate_story_image_count` junto a las validaciones actuales; tras el audio llaman `validate_min_duration`. Los anuncios siguen exigiendo "ebook" en el guion.
- Bebé Héroe: sale de su rama especial (`BEBE_HEROE_SCENES == 4`, `generate_bebe_heroe_voice`, `build_bebe_heroe_props`, `bebe_script_from_story`) y usa el flujo común con `_bebe_heroe_voice` como voz. `BEBE_HEROE_MIN_WORDS` y `BEBE_HEROE_SCENES` se eliminan.
- `_HEALABLE_EXTRA_MARKERS`: suma "guion fuera de largo", "imágenes fuera de rango" y "video más corto que 1:20".
- `_generate_text_with_chain`: `max_tokens` sube a **16384** (la salida pasa a ~7-8 mil tokens y el router `auto` razona ~3 mil antes de contestar); el reintento inmediato con otro modelo también cubre "guion fuera de largo" y "imágenes fuera de rango". `FREELLM_TIMEOUT` (300 s) se revisa contra el tiempo real de una respuesta larga.
- El texto "(el reel ya mide 18 s / el anuncio 14 s)" y los docstrings que hablan de 14/17/18/42 s se corrigen.

### Interfaz (`app.py`, HTML en `HTML = r"""..."""`)

- Selectores de duración del panel: nota "Todos los videos duran ~1:30" (o se ocultan). Sin `{{`, `{%`, `{#`; CRLF puro.

## Pruebas

- Prompts: cada uno nombra "260 a 290 palabras" y "16 a 20 imágenes"; ya no contienen "38-46", "18 segundos", "4 escenas" ni "8 a 10 imágenes".
- `validate_long_script_length`: acepta 255 y 295 palabras; rechaza 150 y 340. `validate_historia_script` mantiene sus reglas de cierre.
- `validate_story_image_count`: acepta 16 y 20; rechaza 8 y 30.
- `validate_min_duration`: acepta 82 s, rechaza 70 s (con ffprobe falso).
- Marcadores nuevos: `_is_healable_error` los reconoce.
- `build_trigger_message`: no agrega meta de palabras para ninguna de las 4 páginas con duración elegida.
- Bebé Héroe: el flujo no exige 4 escenas.
- Suite completa en verde.
- **Verificación real:** generar 1 guion real por página y comprobar palabras y número de imágenes; renderizar al menos 1 video completo (HISTORIAS) y medir con ffprobe que pase de 80 s; revisar visualmente un fotograma del anuncio con el celular al final.

## Fuera de alcance

Compilaciones de clips de gaming, posts de imagen y memes, cambiar música/voz/estilo de subtítulos, reprocesar videos ya generados o programados, y retocar el programador de lotes.

## Riesgos

- **Cuota y tiempo de generación de imágenes:** de 8-10 a 16-20 imágenes por video duplica el trabajo en Flow / Meta AI. Un lote de 10 videos puede tardar horas más.
- **Límite por minuto del proveedor de texto:** guiones más largos gastan más y las respuestas largas cortan más seguido en `auto`. El sanado automático y `max_tokens` 16384 lo mitigan, pero no lo eliminan.
- **Calidad de relato:** 260 palabras para un anuncio de ebook es largo; el riesgo es que suene a relleno. El prompt define beats concretos para evitarlo, y se revisa con guiones reales.
- **Bebé Héroe pierde sus efectos de sonido y zooms por escena.** Si se quieren recuperar, es una entrega aparte.
- **Variación de la voz:** a 189 wpm 250 palabras dan ~79 s; por eso existe la garantía de duración real tras el audio.
