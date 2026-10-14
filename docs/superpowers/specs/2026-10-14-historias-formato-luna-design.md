# HISTORIAS: formato fijo "Luna" (espera, giro bondadoso, reencuentro, corazón) — diseño

Fecha: 2026-10-14. Estado: diseño aprobado por el usuario, pendiente de revisión del spec escrito y de plan.

## Contexto

El usuario compartió un video de referencia (`Pippit_1014_LunaBanco7Meses.mp4`, 1080×1920, 42,3 s, 11 escenas, subtítulos grandes palabra por palabra) y su análisis, y lo quiere para la página **HISTORIAS** (rescates de animales). Elegió:

- **Formato fijo de HISTORIAS:** todas las historias siguen la estructura del video, con largo fijo de ~42 s.
- **Cierre con corazón para el animal** ("deja un ❤️ para Luna"), en lugar de una palabra clave en mayúsculas.

Hoy HISTORIAS usa `prompts/historias_system.md` (6 puntos, 110–160 palabras, cierre "Comenta PALABRA si…") más `RESCUE_STORY_CRAFT` y los patrones de `STORY_PATTERNS` en `batch_pipeline.py`, y suma los "pilares de valor" (`PILARES_KINDS = {"historias"}`). La duración del lote hoy se traduce en una meta de palabras (`build_trigger_message`).

Referencia del video, escena por escena (guion tal como se narra):

| Tiempo | Beat | Narración | Imagen |
|---|---|---|---|
| 0:00–0:02 | Gancho de espera | "Luna esperó 7 meses en el mismo banco. Todos pasaban… nadie la veía." | gata blanca y gris en un banco de piedra, plaza europea soleada |
| 0:03–0:15 | Conflicto | "Su dueña se mudó. Ella volvió cada tarde a la plaza… con frío, con lluvia." | la misma gata de noche bajo la lluvia, pelo mojado |
| 0:16–0:25 | Giro | "Hasta que Marcos, el panadero de la plaza, notó que no era una gata callejera… y encontró un cartel viejo: era Luna." | el panadero acariciándola; luego la mujer entrando en la plaza |
| 0:26–0:32 | Reencuentro | "Su dueña la buscaba desde hacía meses. Cuando la vio… Luna la reconoció al instante." | abrazo con lágrimas de alegría |
| 0:33–0:41 | Cierre | "Hoy Luna ya no espera sola. Si tú también crees que ninguna historia debe terminar en la calle, deja un ❤️ para Luna." | la gata dormida y feliz en un sofá luminoso |

## Decisiones

| Tema | Decisión |
|---|---|
| Alcance | **Formato fijo** de HISTORIAS: reemplaza la estructura de 6 puntos y la de 110–160 palabras. |
| Largo | **Fijo, ~42 s = 100–120 palabras** (a 170 palabras/min). HISTORIAS pasa a `_has_fixed_length`: la duración elegida en el lote ya no cambia el guion. |
| Estructura | **5 beats** (abajo). |
| Cierre | **"deja un corazón para [Nombre]"**; sin "Comenta PALABRA". El guion escribe la palabra *corazón* (el TTS lee el texto; el ❤️ solo va en `HOOK_TEXT` y en el texto de publicación). |
| Patrones | `STORY_PATTERNS` se conservan como **semilla de tema**, adaptados a los 5 beats, y se suma el patrón nuevo `mascota_perdida` con peso alto. |
| Pilares de valor | **Se quitan de HISTORIAS** (`PILARES_KINDS` queda vacío): ya no hay "guion largo" y la estructura fija ya incluye su propio ritmo. |
| Nombres | Se mantiene la lista de nombres evitados (Luna y Marcos incluidos): cada video usa nombres nuevos; Luna es solo el ejemplo del video. |

## Estructura de 5 beats (obligatoria)

1. **Gancho de espera con plazo:** primera frase con número o plazo concreto y una consecuencia (ej. "Luna esperó siete meses en el mismo banco. Todos pasaban… nadie la veía."). Mantiene la regla actual del gancho numérico.
2. **Conflicto y adversidad:** qué cambió (dueño que se va, separación, lugar hostil) y cómo lo soporta con tiempo/clima cambiante ("con frío, con lluvia"). Lugar específico, internacional y distinto en cada historia, como hoy.
3. **Giro con personaje bondadoso:** un personaje secundario (panadero, vecina, guardia, niño…) nota algo que los demás ignoraban (un cartel, un collar, una costumbre) y entiende quién es el animal o qué espera. El rescatista rota en cada historia.
4. **Reencuentro o rescate emotivo:** acción concreta y transformación visible (el animal reconoce a su persona al instante, o llega a su hogar); final esperanzador. Sin violencia, sangre ni muerte explícita.
5. **Cierre emocional:** frase de esperanza con el nombre del animal + invitación a dejar un corazón: "Hoy [Nombre] ya no espera sola… Si tú también crees que [valor], deja un corazón para [Nombre]." Sin "Parte 2" ni "Comenta".

`HOOK_TEXT` y `PERSONAJES` conservan su formato actual (3–6 palabras + emoji; fichas en inglés con etiquetas en mayúsculas).

## Cambios

### Prompt y estructura (`prompts/historias_system.md`, `batch_pipeline.RESCUE_STORY_CRAFT`)

- `historias_system.md`: reescribir "ESTRUCTURA OBLIGATORIA" con los 5 beats, el largo de 100–120 palabras (en un solo bloque) y el cierre con corazón; la regla final "comentar una palabra clave en MAYÚSCULAS" pasa a "deja un corazón para [Nombre]". Se conserva todo lo demás: formato de salida, reglas de personajes, prompts de imagen en inglés, 8–10 imágenes, transformación en dos escenas secuenciales (sin split screen), nombres evitados.
- `RESCUE_STORY_CRAFT` (mensaje que va junto al patrón): mismo cambio (5 beats, cierre con corazón, sin palabra clave). Debe seguir coherente con el prompt del sistema.
- **Imágenes (8–10):** la secuencia sugerida es espera luminosa → mismo lugar en la noche/lluvia → primer plano de la espera → personaje bondadoso → personaje y animal → reencuentro (con la persona) → abrazo → animal feliz en su hogar. La penúltima muestra al animal en transición y la última ya transformado, como hoy.

### Largo fijo (`batch_pipeline._has_fixed_length`, `_text_system_prompt`, `PILARES_KINDS`)

- `_has_fixed_length` incluye HISTORIAS (por nombre de página, usando el mismo criterio que `_text_kind_for_page`/`RESCUE_PAGE_NAME`; leer el código real al planificar para no afectar otras páginas). Con eso `build_trigger_message` ya no agrega la meta de palabras según la duración del lote.
- `PILARES_KINDS = set()` y su comentario se actualizan; el archivo `prompts/_pilares_de_valor.md` queda en el repo sin uso por ahora (no se borra).
- La ruta manual "Generar video automático" de `app.py` (la que llama `batch_pipeline.rescue_story_block`) debe quedar coherente: mismo prompt y mismo largo fijo para HISTORIAS.

### Patrón nuevo (`STORY_PATTERNS`)

```
"mascota_perdida": (18, "Mascota perdida: el animal espera cada dia en un mismo lugar (un banco, una puerta, una parada) despues de que su dueno se mudo o lo perdio; alguien del lugar nota que no es callejero (un cartel, un collar, una costumbre) y encuentra a su persona; final de reencuentro."),
```

Peso 18 (el mayor de la tabla, hoy 16), para que la historia del video aparezca seguido sin repetirse (el cooldown `STORY_PATTERN_COOLDOWN = 2` sigue aplicando). `espera_diaria` y `perdida_reencuentro` se conservan; el texto de cada patrón sigue sirviendo como semilla de tema.

### Texto de publicación

- `prompts/historias_copy_system.md`: la petición final pasa a pedir "un ❤️ para [nombre del animal]" (ej. "¿Me dejas un ❤️ para Luna?"), sin invitar a comentar una palabra clave. Mantener todo lo demás (sin voseo, hashtags, distinto entre Facebook e Instagram).
- Si `batch_pipeline` tiene un texto de respaldo para HISTORIAS (revisar `_story_copy_llm` y su fallback), alinearlo con ese cierre.

### Validación

- `auto_pipeline.validate_script` exige hoy un mínimo de 30 palabras; el guion de HISTORIAS debe estar entre 90 y 130 palabras (margen sobre 100–120) y cerrar con "corazón" y sin "Comenta". Un guion fuera de rango lanza un error **sanable** (mismo mecanismo de marcadores sanables que usan los anuncios, p. ej. `"no cierra con comenta ebook"`) para que el lote regenere. La comprobación vive junto a la de los anuncios, en `_generate_batch_video` y en la ruta manual de `app.py`.

## Pruebas

- `tests/test_pilares_de_valor.py`: `historias` deja de llevar pilares: el test "los guiones largos llevan pilares" se ajusta (ya no hay nichos largos) y el de "formato fijo no lleva pilares" suma `historias`.
- `tests/test_prompts_english.py`: el prompt de HISTORIAS sigue exigiendo prompts de imagen en inglés (se revisa que no se rompa).
- Nuevos: el prompt de HISTORIAS nombra los 5 beats y "100 a 120 palabras", y no contiene "Comenta"; `_has_fixed_length("HISTORIAS")` es verdadero y para otras páginas no cambia; `STORY_PATTERNS` contiene `mascota_perdida` con el mayor peso; el validador acepta un guion de ~110 palabras con cierre de corazón y rechaza uno de 160 palabras, uno sin "corazón" y uno con "Comenta"; el copy de HISTORIAS pide el corazón al animal.
- Verificación manual: generar 2–3 guiones reales con el proveedor de texto (cada uno con 100–120 palabras, hook con plazo, giro con personaje bondadoso, cierre con corazón, 8–10 imágenes en inglés con la ficha del animal) y un texto de publicación real. Suite completa en verde.

## Fuera de alcance

Voz, música, estilo y animación de subtítulos (se mantiene "Clásico (caja)"), formato de las otras páginas, y los posts de imagen de HISTORIAS (`historias_post_system.md`). No se descarta tocar el estilo de subtítulos después, a partir del video de referencia.

## Riesgos

- **Mezcla de patrones:** patrones como "ladrón héroe" o "injusticia visual" no tienen un reencuentro natural; el prompt debe dejar claro que el beat 4 es "reencuentro **o** rescate emotivo" y que el beat 3 sigue funcionando sin cartel.
- **TTS y emoji:** por eso la narración dice la palabra *corazón* y el ❤️ va solo en texto de pantalla/publicación.
- **Variedad:** peso alto de `mascota_perdida` + largo fijo pueden hacer historias parecidas; el cooldown de patrones y la lista de nombres/lugares ya usados mitigan, y el peso se puede bajar en una línea si resulta repetitivo.
- **Cambio de duración:** al pasar a largo fijo se pierde la elección de duración en HISTORIAS; las programaciones ya generadas no cambian.
