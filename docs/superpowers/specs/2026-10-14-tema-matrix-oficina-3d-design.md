# Tema Matrix + oficina 3D — diseño

Fecha: 2026-10-14. Estado: diseño aprobado por el usuario, pendiente de revisión del spec escrito y de plan.
Continúa la oficina de `2026-10-13-oficina-agentes-design.md` (fase 1, ya en producción). La entrega 1 de agentes autónomos (`2026-10-14-agentes-autonomos-motor-reparar-design.md`) queda pausada y se retoma después; su interfaz heredará este tema.

## Objetivo

1. **Tema Matrix en todo el panel** (Oficina, Crear video, Lote, Historial, Ajustes): verde fosforescente sobre negro, tipografía monoespaciada, lluvia de código de fondo.
2. **Oficina en 3D estilo Matrix**: la escena SVG pasa a una escena WebGL (three.js) con agentes-silueta de líneas verdes sentados frente a monitores con lluvia de código. La escena SVG actual queda como respaldo.

Ninguna función del sistema cambia: no se toca el backend, `/api/office/state` ni el sondeo de 3 s.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Alcance del tema | Todo el panel, con interruptor "Matrix / Clásico" en la barra lateral. |
| Técnica 3D | three.js real (WebGL), guardado dentro del proyecto. |
| Agentes | Siluetas huecas de líneas verdes (wireframe) frente a un monitor con lluvia de código. |
| Dónde vive el código 3D | Archivo propio `static/office3d.js` (módulo ES), no dentro del HTML incrustado de `app.py`. |
| Respaldo | Sin WebGL o si falla la carga: escena SVG actual, con el tema aplicado. |

## Parte 1: tema Matrix

- **Colores:** el tema se activa con `data-theme="matrix"` en `<html>`; los tokens de `:root` (`--bg-main`, `--bg-sidebar`, `--border`, textos, acento) se sobrescriben bajo `html[data-theme="matrix"]`. Fondo `#000`, texto principal verde claro legible (≈ `#b6ffc8`, contraste ≥ 7:1 sobre negro), acento verde fosforescente (≈ `#00ff41`) con `text-shadow`/`box-shadow` suave en títulos, bordes y foco. El tema "Clásico" es el `:root` actual, sin cambios.
- **Estados con color propio:** OK verde, aviso ámbar, error rojo, inactivo gris. Los colores de estado no se pierden en el verde: en el tema Matrix las etiquetas `office-sev`, los puntos de estado y los badges de error de otras vistas conservan su matiz.
- **Tipografía:** pila monoespaciada del sistema (`"Cascadia Mono", Consolas, "Courier New", monospace`). No se descarga nada.
- **Lluvia de código de fondo:** un `<canvas id="matrix-rain">` fijo detrás de todo (`position: fixed; z-index: -1; pointer-events: none`), con caracteres katakana y dígitos, a ~20 fps, opacidad baja para no competir con el contenido. Se pausa con `document.hidden`; con `prefers-reduced-motion: reduce` se dibuja un solo cuadro estático. Solo se crea en el tema Matrix.
- **Interruptor:** botón en la barra lateral "Tema: Matrix" / "Tema: Clásico". Valor por defecto: Matrix. Guarda la preferencia en `localStorage` (clave `uiTheme`) con try/catch; sin `localStorage` funciona en memoria. Un script en `<head>` aplica `data-theme` antes del primer pintado para evitar el parpadeo.
- **Páginas con formularios largos:** `input`, `select`, `textarea`, tablas y botones heredan los tokens; no se agregan estilos por pantalla salvo donde un componente use colores fijos (se revisan al implementar con un recorrido por todas las vistas).

## Parte 2: oficina 3D

### Archivos

- `static/vendor/three.module.min.js`: three.js, **versión fija**: la última estable que publique npm al implementar, con su número anotado en `static/vendor/README.md` junto con la licencia MIT. Se baja una vez con `npm pack three@<esa versión>` (desde una carpeta temporal en el scratchpad) y queda en el repo; la app no depende de internet ni de un CDN.
- `static/office3d.js`: módulo ES (`import * as THREE from "./vendor/three.module.min.js"`). Flask ya sirve `static/` y `_require_dashboard_auth` ya lo protege (no está en `_PUBLIC_PATHS`).
- `app.py` (bloques `OFFICE`): contenedor `#office-3d`, fila de botones accesibles, carga dinámica del módulo y el cableado con `officeRender` (CRLF; sin `{{`, `{%`, `{#` ni `"""`).

### Interfaz del módulo `office3d.js`

Exporta una sola función `mountOffice3D(container, { onSelect, reducedMotion })` que devuelve `{ update(agents, offline), dispose() }`:

- `onSelect(id)`: se llama al tocar un agente (`videos|publica|metricas|revisor`).
- `update(agents, offline)`: `agents` es el mapa `{id: {status, bubble}}` ya calculado por `officeAgentView` (incluye el estado del Revisor con descartes); `offline` fuerza todos los monitores a gris y los globos a "Sin conexión".
- `dispose()`: libera geometrías, texturas, el renderer y los listeners.
- Fuera de la interfaz, exporta `STATUS_STYLE` (tabla estado → color y velocidad de lluvia) y `fitCamera(aspect)`: lógica pura, probada con node.

### La escena

- **Sala:** fondo negro, niebla verde oscura, piso con cuadrícula verde (`GridHelper`), pared trasera con lluvia de código grande (textura de canvas), sin ventana.
- **Escritorios:** 4 en fila. Mesa y monitor como cajas con `EdgesGeometry` + `LineSegments` verdes (brillo por línea gruesa y un segundo trazo más tenue, sin postprocesado).
- **Agente:** silueta hueca de líneas (cabeza = esfera de pocos segmentos en wireframe, torso y brazos = cajas en `EdgesGeometry`), sentada de espaldas a la cámara y mirando el monitor. Una pequeña animación de respiración cuando está `busy`.
- **Monitor:** plano con `CanvasTexture` de lluvia de código por agente. `STATUS_STYLE`: `busy` verde, caída rápida; `ok` verde, lenta; `warn` ámbar; `error` rojo, parpadeo; `idle` gris atenuado, casi sin movimiento.
- **Globo y nombre:** `Sprite` con `CanvasTexture` encima de cada agente (`bubble` ≤ 14 caracteres, borde verde, fuente monoespaciada) y el nombre del agente bajo el escritorio.
- **Cámara:** perspectiva, vista alta-frontal. Giro y zoom con mouse/tacto, con ángulo horizontal ±35°, vertical limitado y zoom con rango fijo (implementación propia mínima con pointer events: no se importa `OrbitControls`). Doble clic/toque restablece la vista. `fitCamera(aspect)` aleja la cámara en pantallas angostas para que los 4 escritorios entren siempre.
- **Selección:** `Raycaster` sobre las siluetas, monitores y globos; clic/tap llama a `onSelect(id)`. Cursor `pointer` al pasar por encima, anillo de foco verde en el agente seleccionado.

### Integración con la oficina existente

- Al abrir la pestaña Oficina (`officeStart`), si el navegador soporta WebGL se hace `import("/static/office3d.js")` una sola vez, se monta en `#office-3d` y se oculta el `<svg class="office-scene">`. Si no hay WebGL, falla el `import` o falla `mountOffice3D`, el SVG se queda visible y todo sigue como hoy.
- `officeRender` llama a `scene3d.update(...)` además de actualizar el SVG (el SVG sigue actualizándose; es barato y mantiene el respaldo coherente).
- **Accesibilidad:** con 3D activo se muestra una fila de 4 `<button class="office-agent-btn">` (id `office-btn-<id>`) bajo la escena, con `aria-label` "Nombre: texto" igual que hoy; son el camino de teclado y de lector de pantalla. El canvas lleva `aria-hidden="true"`. Seleccionar por botón o por canvas ejecuta la misma `select(id)` de hoy.
- `officeStop` pausa el bucle de render; `activateTab` a otra pestaña o `document.hidden` detienen `requestAnimationFrame`; al volver se reanuda. La escena no se destruye al cambiar de pestaña (evita recargas); `dispose()` solo se llama si se vuelve al respaldo SVG por un fallo en caliente (`webglcontextlost`).

### Rendimiento

- Render a ~30 fps (se omiten cuadros por tiempo), `setPixelRatio(min(devicePixelRatio, 2))`.
- Una sola textura de canvas por monitor (4) más una de pared; la lluvia se actualiza cada ~66 ms y solo marca `needsUpdate` cuando cambió algo.
- `prefers-reduced-motion`: lluvia estática, sin respiración ni parpadeo; la cámara solo se mueve por interacción.
- `ResizeObserver` sobre el contenedor para ajustar tamaño y `fitCamera`.

## Manejo de errores

- Sin WebGL (`canvas.getContext("webgl2"|"webgl")` falso) → SVG, sin mensajes de error.
- `import()` falla (archivo faltante, 401) → SVG; se registra en consola, nunca en pantalla.
- `webglcontextlost` → `dispose()`, vuelve el SVG; `webglcontextrestored` no se reintenta (recarga de página).
- Sin conexión con el servidor: `update(..., offline=true)`, monitores grises y globos "Sin conexión", igual que el SVG hoy.

## Pruebas

- **Python** (`tests/test_office_state.py` o un archivo nuevo `tests/test_office_3d.py`):
  - `GET /static/office3d.js` y `/static/vendor/three.module.min.js` → 401 sin credenciales, 200 con ellas y `Content-Type` JavaScript.
  - El HTML contiene `#office-3d`, la fila `office-agent-btn`, el interruptor de tema y el script de `<head>` que aplica `data-theme`; el SVG de respaldo sigue presente.
  - Los bloques `OFFICE` siguen sin `{{`, `{%`, `{#`; `app.py` conserva CRLF puro.
  - El tema Matrix: el CSS define `html[data-theme="matrix"]` y los tokens; el estado `ok/warn/error/idle` mantiene colores distintos.
- **JS con node** (`subprocess` desde pytest, se omite si no hay `node`): `STATUS_STYLE` define los 5 estados con color y velocidad; `fitCamera` devuelve cámaras que alejan más a menor `aspect` y respeta los límites.
- **Navegador integrado:** escena visible, clic en cada agente abre su panel, botones de teclado, cambio de estado en vivo con un stub de `/api/office/state` (como en la fase 1), móvil de 375 px, interruptor Matrix/Clásico, recorrido por Crear video, Lote, Historial y Ajustes en tema Matrix, y respaldo SVG forzando `webgl` ausente.
- **Suite completa:** `python -m pytest tests -q` en verde.

## Fuera de alcance

Modelos 3D importados (glTF), sonido, agentes que caminan por la sala, postprocesado (bloom), retocar los videos de Remotion, y tema Matrix para el contenido de los videos generados.

## Riesgos

- **Colores fijos en otras vistas** (`#fff`, `rgba` literales en `app.py`): se detectan en el recorrido y se pasan a tokens; el interruptor "Clásico" es la salida de emergencia.
- **Peso:** `three.module.min.js` (~0,7 MB) se carga solo al abrir la oficina y queda en caché del navegador.
- **GPU débil en móviles:** el tope de `pixelRatio`, los 30 fps y la cámara que se aleja lo mitigan; si aún así falla, el respaldo SVG.
- **Contraste/legibilidad:** el verde sobre negro cansa si el texto es verde saturado; por eso el texto principal es verde claro desaturado y el verde fosforescente se reserva a acentos.
- **Dependencia vendorizada:** actualizar three.js implica reemplazar el archivo y revisar la API usada (solo primitivas estables: `Scene`, `PerspectiveCamera`, `WebGLRenderer`, `LineSegments`, `EdgesGeometry`, `CanvasTexture`, `Sprite`, `Raycaster`).
