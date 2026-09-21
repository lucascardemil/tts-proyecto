# Instrucción para Project de Qwen "HISTORIAS" (rescate animal)

Pegar en el campo de instrucciones del Project (chat.qwen.ai) y subir `Manual_maestro_rescate_animal_v3.2.md` como archivo de conocimiento del Project. Longitud: 911/1000 caracteres.

```
Write one original short story in Spanish about a rescued animal (110-160 words, about 55 seconds narrated). It is a recreated emotional tale, never presented as real. Hook: situation + "hasta que" or "pero" + an emotional twist. Show emotion and consequence, never harm: no graphic, violent or shocking content, no abuse shown, no words like brutal or impactante, never say real video or cameras caught it. End with a narrated question that invites sharing. Reply in plain text, no bold, exactly: HOOK_TEXT: 3-6 words + one emoji. Then Guion and the narration. Then 8-10 scenes, each as: Imagen N / Frase del guion: «exact narration excerpt» / Prompt: English, 9:16, photorealistic, warm and hopeful, repeating the animal's full description every time. Image 1 is the most emotional moment, never the rawest. Follow the attached manual. Do not repeat the species, conflict, setting or ending of recent stories.
```

## Por qué cada parte

- **`HOOK_TEXT:` primero, antes de "Guion"**: `batch_pipeline._split_hook_text` la lee para el texto en pantalla (3-6 palabras + emoji) y la quita antes de parsear. Si Qwen la pusiera después de la última "Imagen N", el parser la metería dentro del último prompt.
- **`Imagen N` / `Frase del guion: «…»` / `Prompt:`**: formato que exige `auto_pipeline._parse_story`.
- **Sin las palabras "sangre/heridas/gore" en la instrucción**: Qwen las copia a los prompts y Meta AI rechaza el prompt aunque estén negadas (`GRAPHIC_TERM_REPLACEMENTS`). El filtro del Manual §2.1 se pide por concepto ("nothing graphic"), no listando el vocabulario prohibido.
- **Filtro de vocabulario en el código**: si el guion trae términos de §2.1 (sangre, golpeado, muerto, brutal, "cámaras captaron"…), el lote lo rechaza y regenera solo (`filtro de seguridad de contenido` es un error sanable).
- **Anti-fatiga (§9)**: el código agrega al mensaje disparador la lista de ganchos recientes; la última frase de la instrucción refuerza eso.

## Cómo activarlo en el código

Crear el proyecto de lote con **"Perfil rescate animal"** marcado (o `video_settings.copy_profile = "rescate_animal"`). Sin ese flag no se aplica nada de lo anterior (rótulo de IA, copy por red, horario 20:00 sin lunes, filtro).

## Verificar antes de dar por bueno

Correr una historia real y auditarla contra el Manual §11.4: revisar la respuesta cruda de la sesión `qwen_batch` (que `HOOK_TEXT:` venga primera y que haya bloques `Imagen N` completos), no asumir que Qwen siguió el formato.
