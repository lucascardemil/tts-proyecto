# Página "How Big Really" (posts de escala) y retiro de Bebé Héroe — diseño

Fecha: 2026-10-14. Estado: diseño aprobado por el usuario en el chat, pendiente de revisión del spec escrito y de plan.
Parte de la base `4dc54b4` + el trabajo sin commit de "videos de más de 1:20" (`2026-10-14-videos-largos-1-20-design.md`). Esa regla de 1:20 es para **videos**; esta página publica **posts de imagen**, así que no le aplica.

## Contexto

El usuario pidió: (1) eliminar la página **Bebé Héroe**; (2) crear una página nueva, **How Big Really**, con el id de página y el token de Meta que pasó en el chat, que publica posts educativos de comparación de escala (inglés, público USA/UK), con la "fábrica" de plantillas, copies y horarios que pegó. Decisiones tomadas en el chat:

| Tema | Decisión |
|---|---|
| Página | **Reemplazar el slot 5 del `.env`** (`FB_PAGE_5_ID/NAME/TOKEN`, hoy Bebé Héroe) por How Big Really, y **borrar el código de Bebé Héroe**. El slot 5 mantiene el orden sin huecos que exige `meta_auth.list_pages()`. |
| Imágenes | Las dibuja **Flow** completas, con el texto corto del título dentro (como en la plantilla del usuario). Sin superponer texto desde el código. |
| Datos | **Banco de temas** de ~100 entradas con datos conocidos y verificables; el modelo solo redacta el copy con esos datos (no inventa cifras). La línea `Source:` sale solo con fuentes genéricas fiables de una lista cerrada. |
| Horario | **Automático por antigüedad de la página**: los primeros 14 días desde el primer lote, 3 posts/día a las 11:00, 15:00 y 22:00 (hora Chile; = 09:00, 13:00 y 20:00 EST); desde el día 15, 2/día a las 11:00 y 22:00. Solo Facebook (sin Instagram, sin YouTube). |

## Credenciales

- El Page ID y el Page Access Token se escriben **solo en `.env`** (archivo ignorado por git) en `FB_PAGE_5_*`. No se guardan en código, tests, docs, logs ni en la salida del chat. El token que el usuario pegó queda en la transcripción de esta sesión: se le recomienda regenerarlo después y usar un token de sistema (System User) que no expire.
- Tras escribir `.env` se valida con `meta_auth.validate(page_id)` (la función existente consulta Graph) y se informa solo `ok`/error, sin imprimir el token.
- El servidor en ejecución no ve el cambio hasta reiniciarlo (o `meta_auth.reload_env()`); el reinicio lo decide el usuario.

## Retiro de Bebé Héroe

Se elimina el código específico de la página y sus datos asociados, sin tocar lo genérico:

- `batch_pipeline.py`: `BEBE_HEROE_*`, `BEBE_HEROE_IDEAS`, `is_bebe_heroe_page`, `bebe_trigger_block`, `_recent_bebe_ideas`, `_pick_bebe_idea`, `generate_bebe_heroe_voice`, `_bebe_heroe_voice`, `_bebe_heroe_copy`, la rama `bebe` de `_text_kind_for_page`, de `_generate_batch_video` y de `create_project`, y el `narration_check_kwargs` para Bebé.
- `app.py` (`_run_pipeline_job`): toda la rama `bebe` (voz, tolerancia de verificación, `mood="none"`, idea del banco).
- `video_maker.py`: `BEBE_HEROE_*`, `assemble_bebe_heroe_audio`, `build_bebe_heroe_props`; `video/src/BebeHeroe.tsx` y su rama en `StoryVideo.tsx`; `assets/bebe_heroe/`; `prompts/bebe_heroe_system.md`.
- `tts_engine.py`: se **conserva** `text_to_speech_phrases` y el parámetro `min_similarity` de `text_to_speech_verified` solo si algún otro código los usa; si quedan sin uso se eliminan con sus tests.
- Tests: `tests/test_bebe_heroe.py` se elimina; los tests que no son de Bebé Héroe y viven ahí (lotes mixtos, `text_to_speech_phrases` si se conserva) se mueven a un archivo con nombre neutro. Se ajustan `test_long_videos.py`, `test_pilares_de_valor.py` y los demás que mencionen el kind `bebe_heroe`. `LONG_KINDS` (videos de 1:20) pierde `"bebe_heroe"`.
- No se toca la voz clonada "Voz de Bebé" del usuario (datos en `voices/`).
- **Lotes ya creados de Bebé Héroe:** si el almacén de lotes en uso tiene alguno, el plan lo detecta antes de borrar el código y se lo muestra al usuario; no se borran lotes ni videos publicados.

## La página nueva

- **Nombre en `.env`:** `FB_PAGE_5_NAME=How Big Really`. El `content_type` del lote es **`howbig_image`** (clave de `IMAGE_POST_PROFILES`; `"kind": "howbig_post"`).
- **Perfil `howbig_image`** en `batch_pipeline.IMAGE_POST_PROFILES`: `kind="howbig_post"` (`prompts/howbig_post_system.md`), plantillas `A` Scale comparison, `B` Map comparison, `C` Quantity / stack, `history_store="howbig_post_history"`, `ai_design="scale"` (Flow dibuja el post completo: ni composición ni texto desde el código), `network_offsets={}`, `max_hashtags={"facebook": 1, "instagram": 0}`, `default_trigger="give me the next How Big Really post"`, `label="Post How Big Really"`.
- **Selección de plantilla:** sorteo ponderado **A 70 % / B 20 % / C 10 %** que evita repetir la misma plantilla dos veces seguidas (según el historial). No usa `week` (que asume 2 posts/día en pares mañana/tarde).
- **Selección de tema:** el siguiente tema del banco para esa plantilla que no esté en el historial; cuando la plantilla se agota, se pasa a otra con temas disponibles; sin temas disponibles, el post falla con un mensaje claro ("banco agotado") en vez de inventar.
- **Estilo fijo** (de la fábrica del usuario): fondo negro `#0A0A0A`, vector plano mínimo, acento amarillo `#FFD60A` único, silueta humana blanca de 1,8 m abajo a la izquierda como firma, 4:5 vertical, nunca foto real, sin sombras. Flow solo ofrece 3:4: se pide 3:4 y se ajusta a 4:5 con `meme_maker.fit_post` (recorte del ~6 % vertical), por eso el prompt pide dejar el contenido dentro del 90 % central.

### Banco de temas (`assets/howbig/topics.json`)

~100 entradas editables por el usuario: `{id, template, big, reference, fact, source, question}`:
- `template`: A (≈70), B (≈20), C (≈10).
- `big` / `reference`: lo que se compara y contra qué (`blue whale heart` / `golf cart`; `Russia` / `Africa`; `all gold ever mined` / `3 Olympic swimming pools`).
- `fact`: la frase de shock + dato duro, en inglés, con cifras aproximadas y conservadoras ("about", "roughly") de hechos ampliamente documentados. Sin cifras exóticas ni disputadas.
- `source`: una de una lista cerrada (`NOAA`, `NASA`, `USGS`, `WHO`, `Guinness World Records`, `Smithsonian`, `National Geographic`) o `null`; el copy escribe `Source: X` solo si no es `null`.
- `question`: pregunta abierta de cierre ("What animal should we scale next?").
- Un test valida el esquema, el reparto A/B/C, ids únicos y que `source` pertenezca a la lista; **no puede verificar la verdad de los datos**: se avisa al usuario que revise el archivo antes de programar un lote grande.

### Prompt de sistema (`prompts/howbig_post_system.md`)

Entrada: plantilla + tema del banco + historial reciente. Salida en el formato que ya parsea `_parse_gaming_post` (`IDEA / HOOK / IMAGE_PROMPT / TOP / BOTTOM / CAPTION`; `TOP` = el título de 3 palabras; `BOTTOM` vacío):
- `IMAGE_PROMPT`: una de las 3 plantillas maestras A/B/C del usuario (textualmente, rellenando objeto grande, referencia, título de 3 palabras y, en B, mapas), en inglés, con las reglas de estilo fijas.
- `CAPTION`: la fórmula de 5 líneas del usuario: SHOCK (frase corta que rompe una creencia), DATO DURO (número + contexto del banco), línea en blanco, PREGUNTA ABIERTA (del banco o variante), `Source:` solo si el tema trae fuente. Inglés americano, **sin emojis** (máximo 1), **0 hashtags** (máximo 1 muy de nicho), nunca "share this", "follow me", "like" ni "tag a friend". Las 3 variantes de cierre según plantilla (escala: medida real + qué escalar después; mapas: comparación en km² + qué mapa sorprendió más; cantidad: pregunta de adivinanza).
- Reglas anti-invención: usar **solo** los números del tema; no agregar otros datos ni fuentes.

### Horario automático

- `HOWBIG_PHASE1_DAYS = 14`, `HOWBIG_PHASE1_HOURS = [11, 15, 22]`, `HOWBIG_PHASE2_HOURS = [11, 22]` (hora Chile).
- La fecha de lanzamiento se guarda al crear el **primer lote** de la página (`job_store`, clave `howbig_launch`, fecha ISO). Para cada día de publicación: `días_desde_lanzamiento < 14` → 3 horarios; si no → 2.
- `_compute_schedule` hoy recibe `fixed_hours` como lista; se extiende para aceptar una función `fecha → horas` (o se agrega un parámetro `hours_for_date`), sin cambiar el comportamiento de las demás páginas. `per_day` del lote se ignora para esta página (lo fija la fase) y la interfaz lo indica. Respeta `existing_slots` (no choca con posts ya programados de la página) y nunca programa 10 de golpe en el mismo día.
- Solo Facebook: la interfaz de lote precarga solo Facebook para esta página; el publicador de imágenes ignora Instagram/YouTube si no están elegidos.

### Texto en inglés

El flujo de posts hoy asume copy en español en algunos puntos (activadores como "dame el próximo post…", fallbacks, `seo_optimizer`). El plan revisa cada punto que toca el perfil `howbig_image` (trigger, texto de respaldo si falla la IA, hashtags, rótulos de la interfaz) para que todo lo visible en Facebook salga en inglés; las etiquetas internas del panel pueden seguir en español.

### Interfaz

- El selector de página lista `How Big Really` (viene de `.env`); el selector de tipo de contenido ofrece "Post How Big Really" para esa página (como ya ocurre con los otros nichos). Nota bajo el selector de duración/horario para esta página: "Horario automático: 3/día los primeros 14 días, luego 2/día".
- Sin cambios en el flujo de revisión/publicación existente.

## Pruebas

- Banco: esquema, ids únicos, reparto A/B/C aproximado (≥60 A, ≥15 B, ≥5 C), fuentes de la lista, sin campos vacíos, longitud de `fact`/`question`.
- Perfil `howbig_image`: existe y es coherente con `IMAGE_POST_PROFILES` (claves obligatorias que usa `generate_gaming_post`); selección de plantilla ponderada y sin repetición inmediata (con `random` fijado); selección de tema sin repetir y error "banco agotado".
- Prompt `howbig_post_system.md`: contiene las 3 plantillas, las reglas de estilo (`#0A0A0A`, `#FFD60A`, 4:5, silueta 1,8 m), la fórmula de 5 líneas, y las prohibiciones ("share this", hashtags, emojis); `_parse_gaming_post` acepta una respuesta de ejemplo.
- Prompt de imagen final (`ai_design="scale"`): no agrega `composition` ni capa de texto; pide 3:4 y deja margen al 90 % central.
- Horario: 14 primeros días con 3 horarios, desde el 15 con 2; un lote que cruza el límite; respeta `existing_slots`; otras páginas intactas.
- `.env`: función/guía que reemplaza el slot 5 sin dejar huecos (probado con un `.env` temporal; nunca el real en tests); `list_pages()` devuelve How Big Really y no devuelve Bebé Héroe.
- Retiro de Bebé Héroe: `grep` de `bebe` / `Bebé` sin referencias vivas en código (los comentarios históricos y la voz clonada quedan fuera), suite completa verde, `import app` y render de `/`.
- Verificación real: 1 post completo con el proveedor real (texto + imagen de Flow si hay sesión) para cada plantilla, o el texto solo si Flow no está disponible; validar con `meta_auth.validate` el token nuevo (sin publicar nada).

## Fuera de alcance

Publicar de inmediato (el primer lote lo crea el usuario desde el panel), Instagram/TikTok/YouTube para esta página, videos, Reels, un verificador de datos externo, imágenes en español, y borrar la voz "Voz de Bebé".

## Riesgos

- **Datos del banco:** los escribo yo a partir de conocimiento general; pueden tener errores. Cifras conservadoras y fuentes genéricas reducen el riesgo, pero el usuario debe revisarlo.
- **Texto dentro de la imagen de Flow:** puede salir con errores de ortografía; el título es de 3 palabras simples para minimizarlo. Si resulta mal, se puede cambiar a texto superpuesto por código (una línea en el perfil).
- **Token expuesto en el chat:** recomendar regenerarlo y usar un token de sistema.
- **Borrado de código:** Bebé Héroe toca texto, voz, video y TTS; se hace en una tarea aparte con la suite completa como red de seguridad.
- **Flow solo 3:4:** el recorte a 4:5 puede cortar el borde; por eso el prompt pide margen.
- **Hora de verano:** las horas fijas en Chile no siguen el cambio horario de EST; el desfase puede ser de 1 hora algunos meses.
