# Instrucción para Project de Qwen "HISTORIAS" (rescate animal)

Pegar en el campo de instrucciones del Project (chat.qwen.ai) y subir `Manual_maestro_rescate_animal_v3.3.md` como archivo de conocimiento del Project. Longitud: 988/1000 caracteres.

```
Write one original short story in Spanish about a rescued animal (113-142 words, 40-50 seconds narrated). Recreated emotional tale, never presented as real. Hook: situation + "hasta que" or "pero" + emotional twist. Start narration with a contradiction like "Todos lo odiaban...", never "Habia una vez". Show emotion and consequence, never harm: no graphic, violent or shocking content, no abuse, no words like brutal or impactante, never say real video or cameras caught it. End with a narrated question inviting sharing. Reply in plain text, no bold, exactly: HOOK_TEXT: 3-6 words + one emoji, a contradiction. Then Guion and the narration. Then 8-10 scenes, each as: Imagen N / Frase del guion: «exact narration excerpt» / Prompt: English, 9:16, photorealistic, warm and hopeful, repeating the animal's full description every time. Image 1 is the most emotional moment, never the rawest. Follow the attached manual. Do not repeat species, conflict, setting or ending of recent stories.
```

## Qué cambió en v3.3 (vs. v3.2)

- **Duración**: 110-160 palabras / ~55s → **113-142 palabras / 40-50s** (Manual v3.3 §4.1: los top posts reales caen en 38-45s, y Reels 2026 premia <50s para loop).
- **Arranque de la voz**: se agrega la instrucción explícita de empezar con una contradicción ("Todos lo odiaban...") y nunca con "Había una vez" (Manual v3.3 §4.2).
- **`HOOK_TEXT`**: se aclara que debe ser una contradicción (alineado con la Biblioteca de 20 hooks del Manual v3.3 §2.2, ej. "Lo odiaban por ladrón hasta que vieron esto").
- **No incluido todavía**: la Matriz de viralidad (§1.5) y el Sistema de series Parte 1/2/3 (§7.8) del Manual v3.3 quedan solo en el archivo de conocimiento (Qwen los puede usar como contexto/guía), pero el pipeline (`batch_pipeline.py`/`auto_pipeline.py`) no tiene lógica de código para series todavía — se deja para una iteración aparte.
- **Nota**: `duration_seconds` del proyecto batch "HISTORIAS" en `batch_projects.json` sigue en 60s (cap real que aplica `cap_script_to_duration`) — no se tocó en este cambio, solo la instrucción de Qwen. Si querés que el cap real también baje a 45-50s, avisame.

## Por qué cada parte

- **`HOOK_TEXT:` primero, antes de "Guion"**: `batch_pipeline._split_hook_text` la lee para el texto en pantalla (3-6 palabras + emoji) y la quita antes de parsear. Si Qwen la pusiera después de la última "Imagen N", el parser la metería dentro del último prompt.
- **`Imagen N` / `Frase del guion: «…»` / `Prompt:`**: formato que exige `auto_pipeline._parse_story`.
- **Sin las palabras "sangre/heridas/gore" en la instrucción**: Qwen las copia a los prompts y Meta AI rechaza el prompt aunque estén negadas (`GRAPHIC_TERM_REPLACEMENTS`). El filtro del Manual §2.1 se pide por concepto ("nothing graphic"), no listando el vocabulario prohibido.
- **Filtro de vocabulario en el código**: si el guion trae términos de §2.1 (sangre, golpeado, muerto, brutal, "cámaras captaron"…), el lote lo rechaza y regenera solo (`filtro de seguridad de contenido` es un error sanable).
- **Anti-fatiga (§9)**: el código agrega al mensaje disparador la lista de ganchos recientes; la última frase de la instrucción refuerza eso.

## Cómo activarlo en el código

Es automático: al crear un lote con la página **HISTORIAS** y YouTube marcado, `create_project` fija `video_settings.copy_profile = "rescate_animal"` (ya no hay check en el formulario). Sin ese flag no se aplica nada de lo anterior (rótulo de IA, copy por red, horario 20:00 sin lunes, filtro).

## Verificar antes de dar por bueno

Correr una historia real y auditarla contra el Manual §11 (v3.3): revisar la respuesta cruda de la sesión `qwen_batch` (que `HOOK_TEXT:` venga primera, sea una contradicción, y que haya bloques `Imagen N` completos), no asumir que Qwen siguió el formato.
