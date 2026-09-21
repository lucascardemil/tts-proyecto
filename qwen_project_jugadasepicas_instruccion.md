# Instrucción para Project de Qwen "JUGADASEPICASVIDEOJUEGOS"

Pegar tal cual en el campo de instrucciones del Project (chat.qwen.ai). Longitud: 975/1000 caracteres.

```
Search for recent content from gaming-focused pages for inspiration without copying. If you cannot find a suitable idea, create an original one based on current gaming trends. Generate one professional, viral 1:1 square cover image that is highly visual, eye-catching, and uses very little text. Focus on a current or engaging gaming topic with strong potential for reactions, comments, and shares. Immediately after generating the image, provide the complete social media post copy in English for the same idea. Keep the post concise, engaging, and optimized for interaction. Include a clear gaming-related CTA, such as asking people to choose between options, share their opinion, react to gaming news, rate something, or answer a gaming question. Avoid repeating ideas, visual concepts, layouts, hooks, or post copy used previously. Format the reply exactly as: IDEA: .. HOOK: .. IMAGE_PROMPT: .. CAPTION: .. (plain text, no bold, this exact order, all 4 labels required).
```

## Por qué el agregado

El código (`_parse_gaming_post`, `batch_pipeline.py:617-636`) exige respuesta con esos 4 labels literales, en ese orden, para poder separar idea/hook/prompt de imagen/caption. Sin esa instrucción, Qwen contesta en prosa libre y el parser falla siempre con:

`"La respuesta de Qwen no vino en el formato esperado (IDEA/HOOK/IMAGE_PROMPT/CAPTION)."`

## Siguiente paso

1. Pegar instrucción de arriba en el Project.
2. Reintentar el video "TEST WHATSAPP IMG PROVIDER" (botón "Reintentar").
3. Confirmar que pasa el paso "idea" y llega a generar imagen — recién ahí se puede probar la rama `image_provider=whatsapp`.
