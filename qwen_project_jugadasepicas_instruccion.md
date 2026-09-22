# Instrucción para Project de Qwen "JUGADASEPICASVIDEOJUEGOS"

Pegar tal cual en el campo de instrucciones del Project (chat.qwen.ai) y subir `Gaming-Covers-Workflow-V2-Mejorado.md` como archivo de conocimiento. Longitud: 821/1000 caracteres.

```
Search for recent gaming content for inspiration without copying; never reproduce an existing post. Pick one viral pattern: universal gamer moment, gamer terror, RPG betrayal, painful nostalgia, impossible debate, or news with an emotional angle. Generate one premium 1:1 AAA key art cover with a short English headline inside the image (2-5 words, with emotion), top center at 15-20% of the image, one focal point, a character with visible emotion, no collage, readable as a thumbnail. Then write the post caption in English, 200-300 characters, ending with a one-word CTA (e.g. Comment OLD or NEW). Avoid repeating ideas, visuals, hooks or captions. Follow the attached manual. Reply in plain text, no bold, exactly these 4 labels in this order, nothing after the caption: IDEA: .. HOOK: .. IMAGE_PROMPT: .. CAPTION: ..
```

## Por qué cada parte

El código (`_parse_gaming_post`, `batch_pipeline.py`) exige respuesta con esos 4 labels literales, en ese orden, para separar idea/hook/prompt de imagen/caption. Sin esa instrucción, Qwen contesta en prosa libre y el parser falla con:

`"La respuesta de Qwen no vino en el formato esperado (IDEA/HOOK/IMAGE_PROMPT/CAPTION)."`

## Qué cambió en v2

- **Matriz de viralidad**: se elige un patrón (momento universal, terror del gamer, traición RPG, nostalgia, debate, noticia con ángulo emocional) en vez de arte bonito sin gancho.
- **Headline**: 2-5 palabras en inglés con emoción, arriba al centro, 15-20 % de la imagen, legible en miniatura.
- **Composición**: un solo foco, personaje con emoción visible, sin collage ni split-screen salvo debate.
- **Caption**: 200-300 caracteres, termina con CTA de una palabra ("Comment OLD or NEW").
- **"Nothing after the caption"**: el manual pide también PERFORMANCE GOAL y SERIE POTENTIAL; son notas internas. Si Qwen las agrega, `_parse_gaming_post` las corta del caption (`_GAMING_TRAILING_RE`) para que no se publiquen.
