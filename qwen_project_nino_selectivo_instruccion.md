# Instrucción para Project de Qwen "NIÑO SELECTIVO, FAMILIA EN PAZ"

Pegar en el campo de instrucciones del Project (chat.qwen.ai) y subir `Workflow-Maestro-Reels-9x16-V2-Mejorado.md` como archivo de conocimiento (reemplaza a `workflow_maestro_reels_9x16.md`). Longitud: 984/1000 caracteres.

```
Create one Reel in Spanish about selective eating in kids 2-6, for parents. Pick one pattern: common mistake, what it is not, phrase that works, the minute before eating, when to consult. Never blame the parent; hopeful, responsible wording ("puede ayudar"), no guarantees. Plain text, no bold. Guion: 25-30 seconds narrated (70-85 words), no titles inside. Open with a validating hook, never "Hola". One practical idea. Last two sentences exactly: "Guarda este video para probar en la cena de hoy. Comenta EBOOK y te envío toda la información por mensaje privado." Then 6 scenes, each exactly: Imagen N / Frase del guion: «exact narration excerpt» / Prompt: English, 9:16, photorealistic, calm warm family kitchen, soft window light from left 4000K, cream, beige and light oak palette, relaxed parent hands, curious child never crying, no text or logos. Image 1: calm table, curious child. Write nothing after the last prompt. Follow the attached manual. Never repeat a hook or idea.
```

## Qué cambió en v2

- **Duración**: 20 s → **25-30 s** (70-85 palabras).
- **Hook**: validación sin culpa (error común, "no es maña", frase que sí funciona). Nunca "Hola".
- **CTA doble**: "Guarda este video para probar en la cena de hoy. Comenta EBOOK y te envío toda la información por mensaje privado." La conversión de comentarios es 0.3 %: se pide guardar, no confesar en público.
- **Imágenes**: niño explorando tranquilo, nunca llorando; paleta crema/beige/madera clara/verde pastel. 6 imágenes.
- **Lenguaje responsable**: "puede ayudar", sin garantías.

## Por qué cada parte

- **`Imagen N` / `Frase del guion: «…»` / `Prompt:`**: formato que exige `auto_pipeline._parse_story`.
- **"Nothing after the last prompt"**: el manual v2 también pide texto para edición, caption, performance goal y serie. El caption lo arma el código (`_build_publish_content`); si Qwen igual los agrega, `_parse_story` los corta del último prompt (`_TRAILING_SECTION_RE`).
- **Última frase exacta**: el CTA "Comenta EBOOK" debe ser lo último que se narra.

## Ojo

El `duration_seconds` del proyecto (selector `lote-duration`) es el tope real: `cap_script_to_duration` recorta el guion a ese presupuesto. Para este manual elegir 30 s.

