# Agentes autónomos — entrega 1: motor de acciones + Reparar

Fecha: 2026-10-14. Estado: diseño aprobado por el usuario, pendiente de revisión del spec escrito y de plan.
Parte de la fase 2 de `2026-10-13-oficina-agentes-design.md` (la oficina hoy solo lee y sugiere).

## Contexto y división en entregas

El usuario pidió que los agentes "actúen solos" y eligió: **Reparar**, **Reponer huecos** y **Optimizar con métricas**, con supervisión **escalonada** (Reparar solo; Reponer y Optimizar arrancan en "propone y aprobás con un clic", cada uno con interruptor para pasar a automático) y siempre bitácora y pausa general. Son tres subsistemas independientes, así que se entregan por separado, cada uno con su spec, plan y prueba:

1. **Esta entrega:** motor de acciones (modos, límites, bitácora, pausa, propuestas) + las reglas de Reparar.
2. Reponer huecos (el Revisor detecta el hueco; el agente Videos crea un lote clonando la configuración del último lote de esa página). Solo agrega reglas al motor.
3. Optimizar con métricas: alertar páginas que rinden mal, repetir lo que funciona (semillas de temas), ajustar la mezcla de formatos, reprogramar posts agendados. Solo agrega reglas al motor.

Fuera de alcance de esta entrega: las entregas 2 y 3; cambiar cómo funciona el programador de lotes existente; arreglar causas que requieren al usuario (token inválido, sesión de Google cerrada: siguen siendo avisos del Revisor).

## Qué ya se repara solo (no se duplica)

- `batch_pipeline._requeue_healable_errors`: reintenta ítems en `error` con errores transitorios, con cooldown exponencial (30 min, 1 h, 2 h, 4 h) hasta `MAX_HEAL_CYCLES = 4`. Cubre, entre otros, "flow no devolvió", "flow falló" y "tardó más de".
- Publicación: reintenta `MAX_AUTO_RETRIES = 3` veces y deja el ítem en `publish_error`; nada lo reintenta después.
- Ítems en `generating` / `publishing`: solo `_recover_stuck_videos` los suelta, al reiniciar el servidor.
- `flow_images.ensure_browser` relanza el navegador de Flow antes de cada trabajo.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Enfoque | **Motor de reglas** en un módulo nuevo `office_agents.py`: tabla de reglas (disparador, ejecutor, límites, modo por defecto) y un único ciclo que las evalúa. Entregas 2 y 3 = reglas nuevas. |
| Dónde corre | Hilo daemon del mismo proceso del servidor, arrancado en el `__main__` de `app.py` junto a `start_scheduler()` y **solo si ese proceso tiene el lock del programador** (un único proceso actúa; `publish_worker.py` no ejecuta reglas). Funciona con el panel cerrado. |
| Ciclo | Cada 60 s. Lee los datos con la misma recolección y caché que la oficina (`office_state`), evalúa reglas y ejecuta o propone. |
| Ejecutores | Llaman funciones que ya existen (`batch_pipeline.retry_video`, `retry_publish_video`) más una nueva `release_stuck_item`. No se reescribe ninguna. |
| Modos | **Por regla** (no por agente), con tres valores: `off`, `propose`, `auto`. |
| Persistencia | `job_store` con el nombre `office_agents` (pausa, modos, intentos, propuestas, bitácora). |

## Reglas de Reparar

Todas viven en el agente indicado y sus ejecutores revalidan el estado antes de actuar (los existentes ya lo hacen: `retry_video` exige `status == "error"`, `retry_publish_video` exige `publish_error`).

| id | Agente | Dispara cuando | Ejecuta | Modo por defecto | Límite por ítem |
|---|---|---|---|---|---|
| `retry_cause_resolved` | videos | Ítem en `error` cuyo texto de error menciona **Flow** (`"flow"`) y `flow.state == "ok"` ahora; o cuyo error menciona el proveedor de texto (`"proveedor de texto"`, `"freellm"`) y `text_backends` no está vacío. Y han pasado ≥ 2 h desde `error_at` (o no tiene `error_at`). | `retry_video(pid, index)` | `auto` | 2 intentos, ≥ 2 h entre ellos |
| `republish` | publica | Ítem en `publish_error` cuyo token de Meta para su página está válido (`tokens[]` con `ok: True`, sin `auth_error`), y el último error no fue de autenticación (`last_publish_auth_error` falso). | `retry_publish_video(pid, index)` | `auto` | 2 intentos, ≥ 1 h entre ellos |
| `release_stuck` | videos | Hallazgo de lote trabado: ítem en `generating` o `publishing` más de `STUCK_AFTER` (45 min, `office_state`). | Nueva `batch_pipeline.release_stuck_item(pid, index, expected_status)`: bajo el lock del módulo, si el ítem sigue en `expected_status`, `generating → pending` (stage `None`) o `publishing → ready`, igual que `_recover_stuck_videos`. Devuelve `bool`. | `propose` (puede chocar con un hilo que sigue vivo y duplicar la generación) | 3 intentos, ≥ 1 h entre ellos |

Lo que no se puede reparar solo sigue siendo aviso del Revisor.

## Motor (`office_agents.py`)

### Modelo

- `Rule`: `id`, `agent`, `label`, `description`, `default_mode`, `max_attempts`, `cooldown` (timedelta), `find(data, now) -> list[Target]`, `execute(target) -> (bool, str)`.
- `Target`: `key` estable (`"{rule}:{project_id}:{index}"`), `label` legible (`"MACRAME CREATIVO: ítem 3"`), `payload` (project_id, index, estado esperado).
- Estado persistido (`job_store`, nombre `office_agents`):

```json
{
  "paused": false,
  "modes": {"release_stuck": "auto"},
  "attempts": {"retry_cause_resolved:p1:2": {"count": 1, "last_at": "2026-10-14T10:00:00"}},
  "proposals": [{"id": "ab12cd34", "rule": "release_stuck", "key": "...", "label": "...", "created_at": "...", "expires_at": "..."}],
  "log": [{"ts": "...", "rule": "...", "agent": "...", "key": "...", "label": "...", "mode": "auto|approved", "ok": true, "message": "..."}]
}
```

`modes` solo guarda lo que el usuario cambió; el resto toma `default_mode`. La bitácora conserva las últimas 200 entradas; los intentos con más de 7 días se podan.

### Algoritmo de un ciclo (`run_cycle(now)`)

1. Recolectar `data` con `office_state.collect_data(readers, now)` (nueva función pública: lo que hoy hace `collect_state` antes de `build_state`; `collect_state` pasa a ser `build_state(collect_data(...))`, sin cambiar su resultado). Si la lectura de `projects` falló, el ciclo termina sin actuar.
2. Cargar el estado persistido y caducar propuestas (24 h o si su `Target` ya no aparece en `find`).
3. Por cada regla con modo efectivo distinto de `off`, por cada `Target`:
   - Saltar si agotó `max_attempts` (queda una sola entrada en bitácora: "intentos agotados") o si no pasó `cooldown` desde el último intento.
   - `propose`: crear propuesta si no existe una para esa `key`.
   - `auto`: si no está pausado y el freno global lo permite, revalidar (el `Target` sigue en `find` sobre datos recién leídos) y ejecutar; registrar intento, bitácora y contador del freno. Máximo 1 acción por `Target` por ciclo.
4. Guardar. Cualquier excepción de un ejecutor se registra como `ok: false` y cuenta como intento; no corta el ciclo.

`evaluate` (decide qué hacer, sin efectos) está separada de `apply` (ejecuta y persiste) para probar el motor sin hilos, red ni disco.

### Controles de seguridad

- **Pausa general:** con `paused: true` el ciclo no ejecuta nada ni crea propuestas nuevas. Aprobar una propuesta a mano sigue funcionando (es una acción del usuario).
- **Freno global:** máximo `AUTO_ACTIONS_PER_HOUR = 20` acciones automáticas en los últimos 60 min entre todas las reglas. Al alcanzarlo no ejecuta más y el Revisor muestra el hallazgo `automation-brake` (warn): "Los agentes alcanzaron su límite de 20 acciones por hora".
- **Aprobación:** ejecuta el `Target` revalidado, ignorando el cooldown pero no los topes de seguridad del ejecutor; queda en bitácora con `mode: "approved"`. Rechazar quita la propuesta y la silencia 24 h (guardado en `attempts`).
- **Solo ID conocidos:** los endpoints de escritura aceptan solo ids de regla y de propuesta existentes; nunca ejecutan una URL o función recibida del cliente.

## API

- `GET /api/office/state` suma un bloque `automation`: `{"paused", "rules": [{"id","agent","label","description","mode","default_mode"}], "proposals": [...], "log": [las últimas 20], "brake": {"used": n, "limit": 20}}`. El sondeo de 3 s sigue siendo uno solo; el bloque se lee del archivo (sin Graph).
- `POST /api/office/automation/pause` `{"paused": bool}`.
- `POST /api/office/automation/mode` `{"rule": id, "mode": "off|propose|auto"}` (valida ambos).
- `POST /api/office/automation/proposals/<id>/approve` y `/reject`.
- Todos pasan por `_require_dashboard_auth` (no están en `_PUBLIC_PATHS`) y devuelven `{"ok": bool, "error"?: str}` sin 500.

## Interfaz (en los bloques `OFFICE` de `app.py`)

- Tarjeta de la oficina: botón **Pausar agentes** / **Reanudar agentes** y una línea de estado ("Agentes activos", "Agentes en pausa", "Límite por hora alcanzado").
- Panel de cada agente: sección **Automático** con sus reglas y un selector de tres posiciones (No, Propone, Automático) y la descripción de cada una. Cambiar el selector llama a `/mode`.
- Propuestas pendientes: filas con **Aprobar** y **Rechazar** (confirmación del navegador antes de aprobar, igual que las acciones actuales).
- Tarjeta **Bitácora** bajo el panel: últimas 20 acciones (hora, agente, qué se hizo, "automático"/"aprobado", resultado).
- Texto del servidor siempre con `textContent`; el HTML pasa por Jinja (nada de `{{`, `{%`, `{#`); CRLF en `app.py`.

## Manejo de errores

- Lectura de datos caída: el ciclo no actúa (nunca decide con datos incompletos).
- Archivo de estado ilegible o ausente: arranca con valores por defecto (los modos por defecto y sin pausa); nunca corta el hilo.
- Excepción en una regla: se registra y se sigue con las demás.
- El hilo registra con `logger` y no imprime secretos (los mensajes pasan por `office_state._redact`).

## Pruebas

- `evaluate` / `apply` puras con datos y ejecutores falsos: modos `off`/`propose`/`auto`; pausa; cooldown y tope de intentos; freno de 20/hora; revalidación (el ítem cambió de estado antes de ejecutar); caducidad y rechazo de propuestas; aprobación con cooldown vigente; ejecutor que lanza excepción.
- Predicados de cada regla con casos reales (error de Flow con Flow caído/ok, error de texto, `publish_error` con token ok/inválido/sin dato, ítem trabado).
- `release_stuck_item`: transiciones `generating → pending` y `publishing → ready`, no actúa si el estado cambió.
- `collect_data` + `collect_state` conservan el resultado actual (las pruebas de la oficina siguen en verde).
- Endpoints con el cliente de Flask (auth, validación, no 500) y verificación en el navegador integrado con un stub, como en la oficina; suite completa verde.

## Riesgos

- `release_stuck` puede pisar un hilo que sigue trabajando: por eso arranca en `propose`.
- El hilo comparte lock de lotes con el programador; las funciones existentes ya lo toman (`batch_pipeline._lock`), y los ejecutores nuevos deben hacerlo igual.
- Si el servidor se reinicia a mitad de ciclo, la bitácora puede quedar sin la última acción (se guarda después de ejecutar); los intentos se guardan **antes** de ejecutar para no repetir una acción por un reinicio.
- La pausa y los modos viven en disco: si el archivo se borra, vuelven a los valores por defecto (Reparar vuelve a actuar).
