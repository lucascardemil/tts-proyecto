# Oficina de agentes — diseño

Fecha: 2026-10-13. Estado: aprobado por el usuario, pendiente de plan de implementación.

## Objetivo

Reemplazar la pantalla de inicio del panel (`app.py`, `HTML = r"""..."""`, barra lateral + pestañas Video, Historial, Lote, Ajustes) por una **oficina** donde agentes-bot trabajan en sus escritorios: crean videos, publican, analizan métricas y revisan las páginas, con su estado en vivo.

Fase 1 (este spec): **capa visual sobre lo que el sistema ya hace**. Los agentes muestran estado y **sugieren**; no deciden ni ejecutan solos. Cada sugerencia trae un botón que llama a una acción que ya existe. Fase 2 (fuera de alcance): agentes autónomos que aplican mejoras.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Alcance | Capa visual primero; autonomía después, agente por agente. |
| Estilo | "C · Plano en corte": sala con ventana y piso, 4 escritorios en fila, agentes con globo de estado. Flat, sin gradientes. |
| Navegación | La oficina es la pantalla de inicio. Clic en un agente abre su panel con las vistas actuales. Barra lateral compacta como atajo. Nada existente se elimina. |
| Estado en vivo | Un endpoint agregador consultado por polling cada 3 s. No se tocan los pipelines. |

## Agentes

Por función, no por página (las 5 páginas se muestran como filtro dentro de cada panel).

| Agente | Globo (ejemplos) | Fuente de datos existente |
|---|---|---|
| **Videos** | "Render 3/6", "Sin trabajo" | `batch_pipeline.list_projects()` (estados de ítem `pending`, `generating`, `error`, campo `stage`); `flow_images.status()` |
| **Publica** | "14 posts", "Publicando" | Ítems `ready`, `publishing`, `publish_error`; `scheduled_at`; `facebook_publisher.list_post_times` |
| **Métricas** | "CTR +12%", "Mejor hora 19h" | `feedback_analyzer`, `/api/facebook/best-time`, `/api/instagram/best-time` |
| **Revisor** | "Revisando", "2 alertas" | `meta_auth.validate`, `text_provider.available_backends()`, huecos de programación, lotes trabados |

Color del punto de cada agente: verde = sin novedades, ámbar = aviso, rojo = error que requiere acción, gris = inactivo.

## Arquitectura

```
Navegador (oficina SVG + paneles)
   │ polling 3 s
   ▼
GET /api/office/state ──► office_state.py (módulo nuevo, solo lectura)
                              ├─ batch_pipeline.list_projects()
                              ├─ flow_images.status()
                              ├─ text_provider.available_backends()
                              ├─ meta_auth.validate(page_id)        (caché)
                              ├─ facebook_publisher.list_post_times (caché)
                              └─ feedback_analyzer / best-time      (caché 10 min)
```

### `office_state.py` (nuevo)

- Una función `build_state(now)` pura sobre datos ya leídos, para poder testearla con datos falsos. Un wrapper `collect_state()` hace las lecturas reales.
- Devuelve:

```json
{
  "ok": true,
  "generated_at": "2026-10-13T20:00:00",
  "agents": {
    "videos":   {"status": "busy|ok|idle|warn|error", "bubble": "Render 3/6", "detail": {...}},
    "publica":  {...},
    "metricas": {...},
    "revisor":  {...}
  },
  "findings": [
    {"id": "gap:MACRAME CREATIVO:2026-10-14", "agent": "revisor",
     "severity": "warn|error|info", "text": "Mañana sin posts en Macramé",
     "action": {"label": "Generar lote", "kind": "open_view", "target": "lote"}}
  ]
}
```

- Las lecturas lentas o externas (Graph API, best-time) se cachean con TTL (10 min analítica, 60 s horarios de Meta, 5 min token) en memoria. Si una lectura falla, el agente correspondiente reporta `warn` con el mensaje y el resto del estado se devuelve igual (un fallo no tumba la oficina).
- El endpoint no escribe nada ni dispara ningún proceso.

### Hallazgos del Revisor (reglas fijas, sin IA)

1. **Hueco de programación**: una página con lote activo o historial reciente no tiene ningún post programado/publicado hoy o mañana (el caso "hoy no tengo nada publicado ni programado").
2. **Ítems con error**: ítems en `error` o `publish_error`; acción = Reintentar (usa `/api/batch/retry/...` y `/api/batch/retry-publish/...`).
3. **Token de Meta** inválido o por vencer (`meta_auth.validate`).
4. **Proveedor de texto** sin backends disponibles (`text_provider.available_backends()` vacío).
5. **Lote trabado**: ítem en `generating` o `publishing` durante más de un umbral fijo (constante en el módulo).
6. **Navegador de Flow** caído cuando hay imágenes pendientes (`flow_images.status()`).

Cada hallazgo tiene `id` estable para poder descartarlo en la UI (descartes guardados solo en `localStorage`, con try/catch).

### Acciones de los botones

Solo dos tipos, ambos ya existentes:
- `open_view`: abre el panel/vista actual correspondiente (Lote, Historial, Ajustes, Crear video).
- `call`: llama un endpoint existente por POST (retry, retry-publish), tras confirmación de un clic si la acción es de escritura.

No se agregan endpoints de escritura nuevos en la fase 1.

## UI

- Nuevo bloque `#tab-oficina` dentro del HTML de `app.py`, **pestaña por defecto**. `showTab` ya maneja las pestañas por `data-tab` (`app.py` ~línea 1323), así que `oficina` se suma como una más y las demás quedan intactas.
- Escena SVG (viewBox 210×176 escalado a ancho completo; agentes en fila de 4), con tokens de color del panel actual para el chrome y colores fijos para la escena (no se invierte en modo oscuro, mismo criterio que la maqueta aprobada).
- Cada agente: cabeza, cuerpo, monitor, globo con el texto de `bubble`, punto de estado. Animación mínima y barata por CSS (parpadeo del monitor cuando `busy`); `prefers-reduced-motion` la desactiva.
- Clic o Enter/Espacio en un agente (cada uno es un `<button>` accesible con `aria-label` "Videos: Render 3/6") abre su panel:
  - **Videos** → "Crear video" y "Generación en lote" (vistas actuales).
  - **Publica** → Historial.
  - **Métricas** → panel nuevo (mejor hora por red, patrones de `feedback_analyzer`, top de vistas); es lo único realmente nuevo.
  - **Revisor** → lista de hallazgos con sus botones + acceso a Ajustes.
- Barra lateral actual se mantiene como atajo; añade "Oficina" arriba.
- Móvil: la escena es un SVG que escala al ancho disponible (viewBox 320×176) y los hallazgos pasan a varias líneas por debajo de ~520 px.
- Estados: `ok` = sin novedades (pantalla verde), `idle` = sin trabajo (gris), `busy` = trabajando (verde parpadeante), `warn` = aviso (ámbar), `error` = requiere acción (rojo).
- Texto de la UI en español, sentence case.

## Manejo de errores

- Endpoint: nunca devuelve 500 por una lectura fallida; degrada ese agente a `warn`.
- Cliente: si el polling falla, los globos muestran "Sin conexión" y el punto pasa a gris; reintenta en el siguiente ciclo. Se pausa el polling con la pestaña oculta (`document.hidden`).
- Si falta `localStorage`, el descarte de hallazgos simplemente no persiste.

## Pruebas

- `tests/test_office_state.py`, con datos falsos sobre `build_state`:
  - lote con ítem en `error` → hallazgo `error` con acción retry y agente Videos en `error`;
  - página sin posts hoy ni mañana → hallazgo de hueco;
  - token inválido → hallazgo de token;
  - sin backends de texto → hallazgo de proveedor;
  - ítem `generating` pasado el umbral → lote trabado;
  - una lectura que lanza excepción → ese agente en `warn`, los demás intactos;
  - sin nada activo → los 4 agentes `idle` y `findings` vacío.
- Verificación manual en el navegador integrado: escena, clic en cada agente, polling, vista móvil (375 px) y modo oscuro. Recorrido de las vistas antiguas (Lote, Historial, Ajustes, Crear video) para confirmar que no se rompieron.
- Suite completa: `python -m pytest tests -q` debe seguir verde (159 al último run).

## Fuera de alcance

- Agentes autónomos que apliquen cambios o publiquen por su cuenta (fase 2).
- Bus de eventos / SSE (se evalúa solo si el polling resulta insuficiente).
- Sprites personalizables, sonido, movimiento de personajes por la sala.
- Nuevos endpoints de escritura.
- Mover el HTML fuera de `app.py` (refactor aparte; la oficina se agrega en el mismo bloque para no mezclar cambios).

## Riesgos

- `app.py` ya tiene ~5.800 líneas; sumar la oficina engorda el HTML incrustado. Mitigación: el estado va en `office_state.py` separado; el SVG y el JS de la oficina quedan agrupados en un solo bloque delimitado por comentarios para extraerlo luego.
- Llamadas a Graph desde el polling: cubiertas por caché con TTL; nunca se llama a Meta en cada ciclo de 3 s.
- Estados de ítem y umbrales de "trabado" dependen de `batch_pipeline`; el plan debe leer los estados reales antes de fijar constantes.
