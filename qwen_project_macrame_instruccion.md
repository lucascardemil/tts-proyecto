# Instrucción para Project de Qwen "MACRAME"

Pegar en el campo de instrucciones del Project (chat.qwen.ai) y subir `Workflow-Maestro-Macrame-V2-Mejorado.md` como archivo de conocimiento (reemplaza al anterior). Longitud: 968/1000 caracteres.

```
Create one original beginner macrame Reel. Pick one viral pattern: before/after, common mistake, one-minute project, cheap material, secret trick or 5-minute gift. Project: under 3 m of cord, basic knots, quick to show. Reply in Spanish, plain text, no bold, no titles inside the script. Guion: 25-35 seconds narrated (70-95 words). Open with a curiosity hook like "¿Y si te digo que con 2 metros de cuerda puedes hacer esto?", never "Hoy vamos a hacer". End with "Comenta EBOOK". Then 6-8 scenes, each exactly: Imagen N / Frase del guion: «exact narration excerpt» / Prompt: English, 9:16, photorealistic, modern boho, soft natural window light from left, warm 4000K, light oak table, macro cotton cord texture, natural hands, no text or logos, same project and materials every scene. Image 1 is the finished result, never the materials. Write nothing after the last prompt: no caption, no notes. Follow the attached manual. Never repeat a project, hook or narration.
```

## Qué cambió en v2

- **Duración**: 30-40 s → **25-35 s** (70-95 palabras). Los datos de la cuenta muestran caída de retención después de 35 s.
- **Hook**: biblioteca de ganchos por patrón (antes/después, error común, truco, regalo en 5 min, 1 minuto). Prohibido abrir con "Hoy vamos a hacer...".
- **Imagen 1 = resultado final**, no los materiales (hook visual).
- **6-8 imágenes** en vez de 6-9. Prompts con luz de ventana izquierda 4000K, madera clara y manos naturales.
- **CTA**: sigue "Comenta EBOOK" al final del guion.

## Por qué cada parte

- **`Imagen N` / `Frase del guion: «…»` / `Prompt:`**: formato que exige `auto_pipeline._parse_story`.
- **"Nothing after the last prompt"**: el manual v2 pide además MATRIZ, CAPTION, PERFORMANCE GOAL y SERIE POTENCIAL, pero el caption lo arma el código (`_build_publish_content`). Esa instrucción evita gastar la respuesta en eso; si Qwen igual los agrega, `_parse_story` los corta del último prompt (`_TRAILING_SECTION_RE`).
- **La matriz de viralidad** (patrón elegido) la usa Qwen internamente; no se pide en la respuesta.

## Ojo

El `duration_seconds` del proyecto (selector `lote-duration`) es el tope real: `cap_script_to_duration` recorta el guion a ese presupuesto. Para este manual elegir 30 s o el más cercano a 25-35 s.

