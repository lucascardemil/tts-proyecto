# Oficina de agentes — plan de implementación

> **Para agentes que ejecuten este plan:** usar superpowers:subagent-driven-development (recomendado) o superpowers:executing-plans, tarea por tarea. Los pasos usan casillas (`- [ ]`) para el seguimiento.

**Objetivo:** reemplazar la pantalla de inicio del panel por una oficina donde cuatro agentes (Videos, Publica, Métricas, Revisor) muestran en vivo lo que ya hace el sistema y sugieren acciones con un botón.

**Arquitectura:** un módulo nuevo de solo lectura, `office_state.py`, junta lo que el sistema ya sabe (lotes, Flow, tokens de Meta, proveedor de texto, métricas) y devuelve el estado de los cuatro agentes más una lista de hallazgos. `GET /api/office/state` lo expone y el navegador lo consulta cada 3 s. La UI es una escena SVG más un panel dentro del HTML incrustado de `app.py`, como pestaña por defecto; las vistas actuales siguen intactas detrás.

**Tecnologías:** Python 3 y Flask (sin dependencias nuevas), pytest, HTML/CSS/JS plano dentro de `app.py`. Verificación visual con el navegador integrado.

**Spec:** `docs/superpowers/specs/2026-10-13-oficina-agentes-design.md`

## Restricciones globales

- **No commitear ni pushear** hasta que el usuario lo pida explícitamente. Donde la plantilla pondría "Commit", este plan pone "Punto de control" (revisar `git status` y `git diff --stat`, nada más).
- Responder y escribir textos de la UI en **español**, en minúscula tras la primera letra ("Todo en orden"), sin signos de exclamación.
- **No crear archivos temporales fuera de la carpeta de scratch** de la sesión (`C:\Users\lucas\AppData\Local\Temp\claude\C--Users-lucas-Desktop-tts-proyecto\150f17a9-6a39-4c79-aab6-fd23f61e3f0e\scratchpad`) y borrarlos al terminar.
- El HTML de `app.py` es un string `r"""..."""` que pasa por `render_template_string` (Jinja): **no puede aparecer `{{`, `{%` ni `{#`** en nada que se agregue (hay un test que lo vigila) ni `"""`.
- La oficina **solo lee**: `office_state.py` no escribe archivos ni dispara procesos. Las únicas escrituras son los POST ya existentes (`/api/batch/retry/...` y `/api/batch/retry-publish/...`) que el usuario confirma con un clic.
- Las llamadas a Meta (Graph) nunca se hacen en cada ciclo de 3 s: van con caché (TTL por fuente, tabla en la Tarea 4).
- Burbuja de cada agente: máximo **14 caracteres**.
- Estados de agente: `busy`, `ok`, `idle`, `warn`, `error`. Severidad de hallazgo: `error`, `warn`, `info`.
- Python del proyecto: `venv/Scripts/python.exe`. Tests: `venv/Scripts/python.exe -m pytest tests -q` (159 pasaban al último run).
- El servidor real corre en el puerto 5000 con auth básica (`DASHBOARD_USER` / `DASHBOARD_PASSWORD` de `.env`); no tocar el servidor de otro proyecto (macrame-bot).

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `office_state.py` | crear | `build_state` (pura), `StuckTracker`, `_Cache`, `collect_state`, `reset` |
| `tests/test_office_state.py` | crear | tests de `build_state`, tracker, caché, endpoint y HTML |
| `app.py` | modificar | `import office_state`; lectores y ruta `/api/office/state`; CSS, HTML y JS de la oficina entre marcadores `OFFICE:START` / `OFFICE:END`; `activateTab` y navegación |
| `docs/superpowers/specs/2026-10-13-oficina-agentes-design.md` | modificar | dos precisiones (estado `ok`, escena que escala en móvil) |

---

### Tarea 1: `office_state.py` — esqueleto, agente Videos y hallazgos de error

**Archivos:**
- Crear: `office_state.py`
- Crear: `tests/test_office_state.py`

**Interfaces:**
- Produce (las usan las tareas siguientes):
  - `build_state(data: dict, now: datetime) -> dict` con `{"ok": True, "generated_at": str, "agents": {id: {"status","bubble","detail"}}, "findings": [..]}`.
  - `data` acepta las claves: `projects: list[dict]`, `flow: dict|None`, `text_backends: list|None`, `tokens: list[dict]`, `remote_times: dict[str, list[datetime]]`, `metrics_rows: dict`, `seen: dict[str, datetime]`, `errors: dict[str, str]` (fuente → mensaje de la lectura que falló).
  - Helpers privados: `_norm`, `_page`, `_items`, `_parse`, `_reason`, `_short`, `_finding`, `_call`, `_view`.
  - Hallazgo: `{"id": str, "agent": str, "severity": str, "text": str, "action": {...}|None}`; acción `{"label","kind":"call","method":"POST","url"}` o `{"label","kind":"open_view","target"}`.

- [ ] **Paso 1: escribir los tests que fallan**

Crear `tests/test_office_state.py`:

```python
# -*- coding: utf-8 -*-
"""Estado de la oficina de agentes: pura sobre datos falsos, sin red ni disco."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import office_state as os_

NOW = datetime(2026, 10, 13, 15, 0)


def vid(status, when=None, error=None):
    return {"status": status, "scheduled_at": when.isoformat() if when else None, "error": error}


def proj(videos=(), page="MACRAME CREATIVO", status="running", pid="p1", **extra):
    return {"id": pid, "name": "Lote 1", "page_name": page, "status": status,
            "videos": [dict(index=i, **v) for i, v in enumerate(videos)], **extra}


def data(**kw):
    base = {"projects": [], "flow": {"state": "ok", "message": ""}, "text_backends": ["freellm:x"],
            "tokens": [], "remote_times": {}, "metrics_rows": {"facebook": [], "instagram": []},
            "seen": {}, "errors": {}}
    base.update(kw)
    return base


def test_videos_agent_is_idle_without_batches():
    state = os_.build_state(data(), NOW)
    assert state["ok"] is True
    assert state["agents"]["videos"] == {
        "status": "idle", "bubble": "Sin trabajo",
        "detail": {"generating": 0, "pending": 0, "errors": 0, "projects": []}}
    assert state["findings"] == []


def test_videos_agent_busy_shows_render_progress():
    p = proj([vid("ready"), vid("generating"), vid("pending")])
    agent = os_.build_state(data(projects=[p]), NOW)["agents"]["videos"]
    assert agent["status"] == "busy"
    assert agent["bubble"] == "Render 1/3"
    assert agent["detail"]["projects"] == [
        {"id": "p1", "name": "Lote 1", "page_name": "MACRAME CREATIVO", "done": 1, "total": 3}]


def test_videos_agent_shows_queue_when_nothing_is_generating():
    p = proj([vid("pending"), vid("pending")])
    assert os_.build_state(data(projects=[p]), NOW)["agents"]["videos"]["bubble"] == "2 en cola"


def test_error_item_becomes_a_finding_with_retry_action():
    p = proj([vid("error", error="Flow no devolvió imagen en 240s.")])
    state = os_.build_state(data(projects=[p]), NOW)
    assert state["agents"]["videos"]["status"] == "error"
    assert state["agents"]["videos"]["bubble"] == "1 con error"
    (finding,) = [f for f in state["findings"] if f["agent"] == "videos"]
    assert finding["id"] == "error:p1:0"
    assert finding["agent"] == "videos" and finding["severity"] == "error"
    assert "ítem 1" in finding["text"] and "Flow no devolvió" in finding["text"]
    assert finding["action"] == {"label": "Reintentar", "kind": "call", "method": "POST",
                                 "url": "/api/batch/retry/p1/0"}


def test_inactive_batches_and_removed_items_are_ignored():
    done = proj([vid("error")], status="done", pid="p1")
    removed = proj([vid("removed")], pid="p2")
    state = os_.build_state(data(projects=[done, removed]), NOW)
    assert state["agents"]["videos"]["status"] == "idle"
    assert state["findings"] == []


def test_projects_read_failure_degrades_videos_to_warn():
    agent = os_.build_state(data(errors={"projects": "boom"}), NOW)["agents"]["videos"]
    assert agent["status"] == "warn" and agent["bubble"] == "Sin datos"
```

- [ ] **Paso 2: correr los tests y ver que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q`
Esperado: FAIL / error de colección, `ModuleNotFoundError: No module named 'office_state'`.

- [ ] **Paso 3: implementar el mínimo**

Crear `office_state.py`:

```python
# -*- coding: utf-8 -*-
"""Estado en vivo de la oficina de agentes (ver docs/superpowers/specs/2026-10-13-oficina-agentes-design.md).

Solo lectura: junta lo que el sistema ya sabe (lotes, Flow, tokens, metricas) y lo traduce a cuatro
"agentes" con un estado y una lista de hallazgos con boton de accion. No escribe nada ni dispara ningun
proceso. `build_state` es pura (datos ya leidos -> dict) para poder testearla sin red; `collect_state`
hace las lecturas, con cache por fuente."""

import logging
import threading
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

import batch_pipeline
import feedback_analyzer

logger = logging.getLogger("office_state")

ACTIVE_PROJECT_STATUSES = ("running", "paused")
DONE_STATUSES = ("ready", "publishing", "published")
SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}
MAX_BUBBLE = 14


def _norm(name) -> str:
    return (name or "").strip().upper()


def _page(project: dict) -> str:
    return batch_pipeline._project_page_name(project)


def _items(projects: list):
    """(lote, video) de los lotes activos, sin los videos eliminados."""
    for project in projects:
        if project.get("status") not in ACTIVE_PROJECT_STATUSES:
            continue
        for video in project.get("videos", []):
            if video.get("status") != "removed":
                yield project, video


def _parse(value) -> Optional[datetime]:
    """ISO -> datetime local sin zona (los horarios del lote son locales); None si no se puede."""
    try:
        dt = datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None
    if dt is not None and dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _reason(message) -> str:
    return f" — {str(message)[:120]}" if message else ""


def _short(text: str) -> str:
    """Recorta el texto del globo para que entre en la escena."""
    return text if len(text) <= MAX_BUBBLE else text[:MAX_BUBBLE - 1] + "…"


def _finding(fid: str, agent: str, severity: str, text: str, action: Optional[dict] = None) -> dict:
    return {"id": fid, "agent": agent, "severity": severity, "text": text, "action": action}


def _call(label: str, url: str) -> dict:
    return {"label": label, "kind": "call", "method": "POST", "url": url}


def _view(label: str, target: str) -> dict:
    return {"label": label, "kind": "open_view", "target": target}


def _videos_agent(data: dict, findings: list) -> dict:
    if "projects" in data.get("errors", {}):
        return {"status": "warn", "bubble": "Sin datos", "detail": {}}
    generating = pending = failed = 0
    progress = []
    lead = None  # primer lote que esta generando: su avance va en el globo
    for project in data.get("projects", []):
        if project.get("status") not in ACTIVE_PROJECT_STATUSES:
            continue
        videos = [v for v in project.get("videos", []) if v.get("status") != "removed"]
        counts = Counter(v.get("status") for v in videos)
        generating += counts["generating"]
        pending += counts["pending"]
        failed += counts["error"]
        if counts["generating"] or counts["pending"]:
            entry = {"id": project["id"], "name": project.get("name", ""), "page_name": _page(project),
                     "done": sum(counts[s] for s in DONE_STATUSES), "total": len(videos)}
            progress.append(entry)
            if lead is None and counts["generating"]:
                lead = entry
        for video in videos:
            if video.get("status") == "error":
                findings.append(_finding(
                    f"error:{project['id']}:{video['index']}", "videos", "error",
                    f"{_page(project)}: ítem {video['index'] + 1} con error{_reason(video.get('error'))}",
                    _call("Reintentar", f"/api/batch/retry/{project['id']}/{video['index']}")))
    if lead:
        bubble = f"Render {lead['done']}/{lead['total']}"
    elif failed:
        bubble = f"{failed} con error"
    elif pending:
        bubble = f"{pending} en cola"
    else:
        bubble = "Sin trabajo"
    status = "error" if failed else "busy" if generating else "idle"
    return {"status": status, "bubble": bubble,
            "detail": {"generating": generating, "pending": pending, "errors": failed, "projects": progress}}


def build_state(data: dict, now: datetime) -> dict:
    findings: list = []
    agents = {
        "videos": _videos_agent(data, findings),
    }
    for agent in agents.values():
        agent["bubble"] = _short(agent["bubble"])
    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["id"]))
    return {"ok": True, "generated_at": now.isoformat(timespec="seconds"), "agents": agents, "findings": findings}
```

- [ ] **Paso 4: correr los tests y ver que pasan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q`
Esperado: `6 passed`.

- [ ] **Paso 5: punto de control**

Run: `git status --short` — solo deben aparecer `office_state.py` y `tests/test_office_state.py` como nuevos. No commitear.

---

### Tarea 2: agente Publica y hueco de programación

**Archivos:**
- Modificar: `office_state.py` (agregar `SCHEDULED_STATUSES`, `TODAY_CUTOFF_HOUR`, `_publica_agent`, `_gap_findings`; ampliar `build_state`)
- Test: `tests/test_office_state.py`

**Interfaces:**
- Consume: `_items`, `_page`, `_parse`, `_reason`, `_finding`, `_call`, `_view`, `_norm` (Tarea 1).
- Produce: `_publica_agent(data, now, findings) -> dict`; `_gap_findings(data, now) -> list[dict]` (la Tarea 3 lo llama desde `_revisor_findings`); `detail` de Publica: `{"publishing": int, "errors": int, "next_post": iso|None, "per_page": {page: int}}`.

- [ ] **Paso 1: escribir los tests que fallan** (agregar al final de `tests/test_office_state.py`)

```python
def at(day_offset, hour):
    d = NOW + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour)


def test_publica_counts_scheduled_items_and_reports_next_post():
    p = proj([vid("ready", at(0, 19)), vid("published", at(1, 12)), vid("pending", at(0, 9))])
    agent = os_.build_state(data(projects=[p]), NOW)["agents"]["publica"]
    assert agent["status"] == "ok" and agent["bubble"] == "2 programados"
    assert agent["detail"]["next_post"] == at(0, 19).isoformat()
    assert agent["detail"]["per_page"] == {"MACRAME CREATIVO": 2}


def test_publica_is_idle_when_nothing_is_scheduled():
    agent = os_.build_state(data(), NOW)["agents"]["publica"]
    assert agent["status"] == "idle" and agent["bubble"] == "Nada agendado"
    assert agent["detail"]["next_post"] is None


def test_publica_shows_publishing_and_publish_errors():
    busy = os_.build_state(data(projects=[proj([vid("publishing", at(0, 19))])]), NOW)["agents"]["publica"]
    assert busy["status"] == "busy" and busy["bubble"] == "Publicando"
    state = os_.build_state(data(projects=[proj([vid("publish_error", at(0, 19), error="token vencido")])]), NOW)
    assert state["agents"]["publica"]["status"] == "error"
    assert state["agents"]["publica"]["bubble"] == "1 sin publicar"
    (finding,) = [f for f in state["findings"] if f["agent"] == "publica"]
    assert finding["id"] == "publish_error:p1:0"
    assert finding["action"]["url"] == "/api/batch/retry-publish/p1/0"


def test_next_post_also_looks_at_posts_already_scheduled_on_facebook():
    remote = {"macrame creativo": [at(0, 17)]}
    agent = os_.build_state(data(projects=[proj([vid("ready", at(1, 12))])], remote_times=remote), NOW)["agents"]["publica"]
    assert agent["detail"]["next_post"] == at(0, 17).isoformat()
    assert agent["bubble"] == "1 programados"  # el conteo es solo de los lotes locales


def gap_ids(state):
    return {f["id"] for f in state["findings"] if f["id"].startswith("gap:")}


def test_gap_finding_when_today_and_tomorrow_have_no_posts():
    p = proj([vid("ready", at(2, 19))])
    state = os_.build_state(data(projects=[p]), NOW)
    assert gap_ids(state) == {f"gap:MACRAME CREATIVO:{NOW.date()}", f"gap:MACRAME CREATIVO:{(NOW + timedelta(days=1)).date()}"}
    today = next(f for f in state["findings"] if f["id"].endswith(str(NOW.date())))
    assert today["severity"] == "error" and today["text"] == "Hoy sin posts en MACRAME CREATIVO"
    assert today["action"] == {"label": "Abrir lotes", "kind": "open_view", "target": "lote"}
    tomorrow = next(f for f in state["findings"] if f["id"].endswith(str((NOW + timedelta(days=1)).date())))
    assert tomorrow["severity"] == "warn" and tomorrow["text"] == "Mañana sin posts en MACRAME CREATIVO"


def test_no_gap_when_both_days_are_covered_by_the_batch():
    p = proj([vid("published", at(0, 9)), vid("ready", at(1, 19))])
    assert gap_ids(os_.build_state(data(projects=[p]), NOW)) == set()


def test_no_gap_when_facebook_already_has_the_posts():
    p = proj([vid("ready", at(2, 19))])
    remote = {"macrame creativo": [at(0, 19), at(1, 19)]}
    assert gap_ids(os_.build_state(data(projects=[p], remote_times=remote), NOW)) == set()


def test_after_9pm_only_tomorrow_is_checked():
    late = datetime(2026, 10, 13, 21, 30)
    p = proj([vid("ready", at(2, 19))])
    assert gap_ids(os_.build_state(data(projects=[p]), late)) == {f"gap:MACRAME CREATIVO:{(late + timedelta(days=1)).date()}"}


def test_error_items_do_not_cover_a_day():
    p = proj([vid("error", at(0, 19)), vid("ready", at(1, 19))])
    assert gap_ids(os_.build_state(data(projects=[p]), NOW)) == {f"gap:MACRAME CREATIVO:{NOW.date()}"}
```

- [ ] **Paso 2: correr y ver que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q`
Esperado: los tests nuevos fallan con `KeyError: 'publica'`.

- [ ] **Paso 3: implementar**

En `office_state.py`, junto a las constantes agregar:

```python
SCHEDULED_STATUSES = ("pending", "generating", "ready", "publishing", "published")
TODAY_CUTOFF_HOUR = 21  # pasadas las 21 ya no se programa nada para hoy (ventana 9-21 h del lote)
```

Agregar antes de `build_state`:

```python
def _publica_agent(data: dict, now: datetime, findings: list) -> dict:
    if "projects" in data.get("errors", {}):
        return {"status": "warn", "bubble": "Sin datos", "detail": {}}
    publishing = failed = 0
    per_page: dict = {}
    upcoming = []
    for project, video in _items(data.get("projects", [])):
        status = video.get("status")
        page = _page(project)
        if status == "publishing":
            publishing += 1
        if status == "publish_error":
            failed += 1
            findings.append(_finding(
                f"publish_error:{project['id']}:{video['index']}", "publica", "error",
                f"{page}: ítem {video['index'] + 1} sin publicar{_reason(video.get('error'))}",
                _call("Reintentar publicar", f"/api/batch/retry-publish/{project['id']}/{video['index']}")))
        if status in SCHEDULED_STATUSES:
            when = _parse(video.get("scheduled_at"))
            if when and when >= now:
                upcoming.append(when)
                per_page[page] = per_page.get(page, 0) + 1
    remote = [t for times in (data.get("remote_times") or {}).values() for t in times if t >= now]
    next_post = min(upcoming + remote, default=None)
    if publishing:
        bubble = "Publicando"
    elif failed:
        bubble = f"{failed} sin publicar"
    elif upcoming:
        bubble = f"{len(upcoming)} programados"
    else:
        bubble = "Nada agendado"
    status = "error" if failed else "busy" if publishing else "ok" if upcoming else "idle"
    return {"status": status, "bubble": bubble,
            "detail": {"publishing": publishing, "errors": failed,
                       "next_post": next_post.isoformat() if next_post else None, "per_page": per_page}}


def _gap_findings(data: dict, now: datetime) -> list:
    """Paginas con lote activo (o con posts recientes en Facebook) que no tienen nada programado ni
    publicado hoy / manana: el caso 'hoy no tengo nada publicado ni programado'."""
    projects = data.get("projects", [])
    pages: dict = {}    # nombre normalizado -> nombre para mostrar
    covered: dict = {}  # nombre normalizado -> fechas con al menos un post
    for project in projects:
        has_work = any(v.get("status") != "removed" for v in project.get("videos", []))
        if project.get("status") in ACTIVE_PROJECT_STATUSES and has_work:  # un lote vaciado no es trabajo pendiente
            pages.setdefault(_norm(_page(project)), _page(project))
        for video in project.get("videos", []):
            if video.get("status") in SCHEDULED_STATUSES:
                when = _parse(video.get("scheduled_at"))
                if when:
                    covered.setdefault(_norm(_page(project)), set()).add(when.date())
    for name, times in (data.get("remote_times") or {}).items():
        key = _norm(name)
        if times:
            pages.setdefault(key, name)
            covered.setdefault(key, set()).update(t.date() for t in times)
    days = [(now.date() + timedelta(days=1), "warn", "Mañana")]
    if now.hour < TODAY_CUTOFF_HOUR:
        days.insert(0, (now.date(), "error", "Hoy"))
    findings = []
    for key, display in sorted(pages.items()):
        for day, severity, label in days:
            if day not in covered.get(key, set()):
                findings.append(_finding(f"gap:{key}:{day.isoformat()}", "revisor", severity,
                                         f"{label} sin posts en {display}", _view("Abrir lotes", "lote")))
    return findings
```

Dentro de `build_state`, ampliar el dict `agents` y agregar los gaps (la Tarea 3 los moverá a `_revisor_findings`; el bucle de `_short` de la Tarea 1 queda tal cual, después de esta parte y antes del `sort`):

```python
    agents = {
        "videos": _videos_agent(data, findings),
        "publica": _publica_agent(data, now, findings),
    }
    findings.extend(_gap_findings(data, now))
```

- [ ] **Paso 4: correr y ver que pasan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q`
Esperado: todos pasan (6 de la Tarea 1 + 9 nuevos).

- [ ] **Paso 5: punto de control** — `git status --short`; no commitear.

---

### Tarea 3: hallazgos del Revisor, `StuckTracker` y agente Revisor

**Archivos:**
- Modificar: `office_state.py`
- Test: `tests/test_office_state.py`

**Interfaces:**
- Consume: `_gap_findings`, `_items`, `_finding`, `_view`, `_parse`, `_norm`, `_page`.
- Produce:
  - `stuck_key(project_id: str, index: int, status: str) -> str` → `f"{project_id}:{index}:{status}"`.
  - `class StuckTracker` con `update(projects: list, now: datetime) -> dict[str, datetime]` (clave → primera vez visto).
  - `_revisor_findings(data, now) -> list[dict]` y `_revisor_agent(findings) -> dict`.
  - Constantes `STUCK_AFTER = timedelta(minutes=45)`, `TOKEN_WARN_DAYS = 7`, `READ_LABELS`.
- Hallazgos nuevos (ids): `token:<PAGINA>`, `text-provider`, `flow`, `stuck:<clave>`, `read:<fuente>`.
- Nota: tras reiniciar el servidor el tracker olvida desde cuándo estaba trabado un ítem; el umbral de 45 min vuelve a contar desde cero.

- [ ] **Paso 1: tests que fallan** (agregar a `tests/test_office_state.py`)

```python
def ids(state):
    return {f["id"] for f in state["findings"]}


def test_invalid_meta_token_is_an_error_finding():
    tokens = [{"page_name": "MACRAME CREATIVO", "ok": False, "error": "token vencido"}]
    state = os_.build_state(data(tokens=tokens), NOW)
    finding = next(f for f in state["findings"] if f["id"] == "token:MACRAME CREATIVO")
    assert finding["severity"] == "error" and finding["agent"] == "revisor"
    assert finding["text"] == "Token de Meta inválido en MACRAME CREATIVO"
    assert finding["action"]["target"] == "ajustes"


def test_token_close_to_expiry_is_a_warning_and_healthy_token_is_silent():
    soon = {"page_name": "Bebé Héroe", "ok": True, "expires_at": (NOW + timedelta(days=3)).isoformat()}
    fine = {"page_name": "HISTORIAS", "ok": True, "expires_at": (NOW + timedelta(days=40)).isoformat()}
    never = {"page_name": "Niño", "ok": True, "expires_at": None}
    state = os_.build_state(data(tokens=[soon, fine, never]), NOW)
    assert ids(state) == {"token:BEBÉ HÉROE"}
    assert state["findings"][0]["severity"] == "warn"
    assert state["findings"][0]["text"] == "El token de Bebé Héroe vence en 3 días"


def test_missing_text_provider_is_an_error_but_unknown_is_not():
    assert "text-provider" in ids(os_.build_state(data(text_backends=[]), NOW))
    assert "text-provider" not in ids(os_.build_state(data(text_backends=None), NOW))


def test_flow_finding_only_when_a_flow_batch_has_work_left():
    down = {"state": "no_browser", "message": "El navegador de Flow está cerrado."}
    flow_batch = proj([vid("pending", at(1, 19))], video_settings={"image_provider": "flow"})
    other_batch = proj([vid("pending", at(1, 19))], video_settings={"image_provider": "whatsapp"})
    with_flow = os_.build_state(data(projects=[flow_batch], flow=down), NOW)
    assert next(f for f in with_flow["findings"] if f["id"] == "flow")["text"] == "Flow: El navegador de Flow está cerrado."
    assert "flow" not in ids(os_.build_state(data(projects=[other_batch], flow=down), NOW))
    assert "flow" not in ids(os_.build_state(data(projects=[flow_batch], flow={"state": "ok", "message": ""}), NOW))


def test_item_stuck_longer_than_the_threshold_is_flagged():
    p = proj([vid("generating", at(1, 19))])
    key = os_.stuck_key("p1", 0, "generating")
    stuck = os_.build_state(data(projects=[p], seen={key: NOW - timedelta(minutes=50)}), NOW)
    finding = next(f for f in stuck["findings"] if f["id"] == f"stuck:{key}")
    assert finding["severity"] == "warn" and "50 min" in finding["text"]
    fresh = os_.build_state(data(projects=[p], seen={key: NOW - timedelta(minutes=10)}), NOW)
    assert f"stuck:{key}" not in ids(fresh)


def test_stuck_tracker_remembers_first_sighting_and_forgets_finished_items():
    tracker = os_.StuckTracker()
    p = proj([vid("generating")])
    key = os_.stuck_key("p1", 0, "generating")
    assert tracker.update([p], NOW) == {key: NOW}
    assert tracker.update([p], NOW + timedelta(minutes=5)) == {key: NOW}
    p["videos"][0]["status"] = "ready"
    assert tracker.update([p], NOW + timedelta(minutes=6)) == {}


def test_failed_reads_become_warnings_and_other_agents_survive():
    state = os_.build_state(data(errors={"tokens": "RuntimeError: sin red"}), NOW)
    finding = next(f for f in state["findings"] if f["id"] == "read:tokens")
    assert finding["severity"] == "warn"
    assert finding["text"] == "No pude leer los tokens de Meta: RuntimeError: sin red"
    assert {"videos", "publica", "revisor"} <= set(state["agents"])


def test_revisor_summarises_all_findings():
    clean = os_.build_state(data(), NOW)["agents"]["revisor"]
    assert clean["status"] == "ok" and clean["bubble"] == "Todo en orden"
    one = os_.build_state(data(text_backends=[]), NOW)["agents"]["revisor"]
    assert one["status"] == "error" and one["bubble"] == "1 alerta"
    warn_only = os_.build_state(data(errors={"tokens": "x"}), NOW)["agents"]["revisor"]
    assert warn_only["status"] == "warn"
    p = proj([vid("error"), vid("error")])
    many = os_.build_state(data(projects=[p], text_backends=[]), NOW)["agents"]["revisor"]
    assert many["bubble"] == "5 alertas"  # 2 errores de ítem + 2 huecos (hoy, mañana) + sin proveedor
```

Nota: en el último test los dos ítems con error no tienen `scheduled_at`, por eso no cubren ningún día y aparecen los dos huecos.

- [ ] **Paso 2: correr y ver que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q`
Esperado: los tests nuevos fallan (`AttributeError: ... 'stuck_key'` / `KeyError: 'revisor'`).

- [ ] **Paso 3: implementar**

Constantes (junto a las otras):

```python
STUCK_AFTER = timedelta(minutes=45)
TOKEN_WARN_DAYS = 7
READ_LABELS = {"projects": "los lotes", "flow": "el estado de Flow", "text_backends": "el proveedor de texto",
               "tokens": "los tokens de Meta", "remote_times": "los horarios de Facebook"}
```

Funciones nuevas (antes de `build_state`):

```python
def stuck_key(project_id: str, index: int, status: str) -> str:
    return f"{project_id}:{index}:{status}"


class StuckTracker:
    """Desde cuando se ve cada item en 'generating'/'publishing'. Los items no guardan cuando empezaron,
    asi que la oficina lo anota en memoria; al reiniciar el servidor la cuenta vuelve a cero."""

    def __init__(self):
        self._seen: dict = {}
        self._lock = threading.Lock()

    def update(self, projects: list, now: datetime) -> dict:
        current = {stuck_key(p["id"], v["index"], v["status"])
                   for p, v in _items(projects) if v.get("status") in ("generating", "publishing")}
        with self._lock:
            self._seen = {key: self._seen.get(key, now) for key in current}
            return dict(self._seen)


def _token_findings(data: dict, now: datetime) -> list:
    findings = []
    for token in data.get("tokens") or []:
        page = token.get("page_name") or "la página"
        if not token.get("ok"):
            findings.append(_finding(f"token:{_norm(page)}", "revisor", "error",
                                     f"Token de Meta inválido en {page}", _view("Abrir ajustes", "ajustes")))
            continue
        expires = _parse(token.get("expires_at"))
        if expires and expires - now < timedelta(days=TOKEN_WARN_DAYS):
            days = max((expires - now).days, 0)
            findings.append(_finding(f"token:{_norm(page)}", "revisor", "warn",
                                     f"El token de {page} vence en {days} días", _view("Abrir ajustes", "ajustes")))
    return findings


def _flow_findings(data: dict) -> list:
    flow = data.get("flow")
    if not flow or flow.get("state") == "ok":
        return []
    for project, video in _items(data.get("projects", [])):
        settings = project.get("video_settings") or {}
        if "flow" in (settings.get("image_provider"), settings.get("provider")) \
                and video.get("status") in ("pending", "generating"):
            return [_finding("flow", "revisor", "warn", f"Flow: {flow.get('message')}", _view("Abrir ajustes", "ajustes"))]
    return []


def _stuck_findings(data: dict, now: datetime) -> list:
    findings = []
    seen = data.get("seen") or {}
    for project, video in _items(data.get("projects", [])):
        if video.get("status") not in ("generating", "publishing"):
            continue
        key = stuck_key(project["id"], video["index"], video["status"])
        since = seen.get(key)
        if since and now - since > STUCK_AFTER:
            minutes = int((now - since).total_seconds() // 60)
            state = "generando" if video["status"] == "generating" else "publicando"
            findings.append(_finding(f"stuck:{key}", "revisor", "warn",
                                     f"{_page(project)}: ítem {video['index'] + 1} lleva {minutes} min {state}",
                                     _view("Abrir lotes", "lote")))
    return findings


def _revisor_findings(data: dict, now: datetime) -> list:
    findings = _gap_findings(data, now) + _token_findings(data, now) + _flow_findings(data) + _stuck_findings(data, now)
    backends = data.get("text_backends")
    if backends is not None and not backends:
        findings.append(_finding("text-provider", "revisor", "error", "Sin proveedor de texto disponible",
                                 _view("Abrir ajustes", "ajustes")))
    for source, message in (data.get("errors") or {}).items():
        if source in READ_LABELS:
            findings.append(_finding(f"read:{source}", "revisor", "warn",
                                     f"No pude leer {READ_LABELS[source]}: {message}"))
    return findings


def _revisor_agent(findings: list) -> dict:
    if not findings:
        return {"status": "ok", "bubble": "Todo en orden", "detail": {"findings": 0}}
    status = "error" if any(f["severity"] == "error" for f in findings) else "warn"
    bubble = f"{len(findings)} alerta" + ("" if len(findings) == 1 else "s")
    return {"status": status, "bubble": bubble, "detail": {"findings": len(findings)}}
```

Reemplazar `build_state` completo por:

```python
def build_state(data: dict, now: datetime) -> dict:
    findings: list = []
    agents = {
        "videos": _videos_agent(data, findings),
        "publica": _publica_agent(data, now, findings),
    }
    findings.extend(_revisor_findings(data, now))
    agents["revisor"] = _revisor_agent(findings)
    for agent in agents.values():
        agent["bubble"] = _short(agent["bubble"])
    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["id"]))
    return {"ok": True, "generated_at": now.isoformat(timespec="seconds"), "agents": agents, "findings": findings}
```

(Se quita el `findings.extend(_gap_findings(...))` suelto de la Tarea 2: ahora lo hace `_revisor_findings`.)

- [ ] **Paso 4: correr y ver que pasan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q`
Esperado: todos pasan. Si `test_revisor_summarises_all_findings` da otro número, revisar la cuenta del comentario antes de tocar el código.

- [ ] **Paso 5: punto de control** — `git status --short`; no commitear.

---

### Tarea 4: agente Métricas, caché por fuente y `collect_state`

**Archivos:**
- Modificar: `office_state.py`
- Test: `tests/test_office_state.py`

**Interfaces:**
- Consume: `feedback_analyzer.best_posting_hour(rows)`, `feedback_analyzer.analyze_from_rows(rows)` (firmas existentes), `build_state`, `StuckTracker`.
- Produce:
  - `_metricas_agent(data) -> dict`; `detail` = `{"facebook": {"best_time": dict, "patterns": dict, "top": [{"title","views"}]}, "instagram": {...}}`.
  - `READ_TTL: dict[str, int]` y `FAIL_TTL = 60`.
  - `class _Cache` con `get(name, ttl, fn) -> (value, error|None)`.
  - `collect_state(readers: dict[str, Callable], now: Optional[datetime] = None) -> dict`.
  - `reset() -> None` (vacía caché y tracker; para tests).
- Convención de `readers`: las claves son `projects`, `flow`, `text_backends`, `tokens`, `remote_times`, `metrics_rows`; un lector que falla queda en `data["errors"][clave]`.

- [ ] **Paso 1: tests que fallan** (agregar)

```python
def rows(*hours_and_views, titles=("uno dos tres", "cuatro cinco", "seis siete ocho")):
    return [{"published_at": f"2026-10-1{i}T{h:02d}:00:00", "views": v, "title": titles[i % 3]}
            for i, (h, v) in enumerate(hours_and_views)]


def test_metricas_agent_reports_best_daypart_and_top_posts():
    fb = rows((20, 500), (21, 700), (9, 50))
    agent = os_.build_state(data(metrics_rows={"facebook": fb, "instagram": []}), NOW)["agents"]["metricas"]
    assert agent["status"] == "ok" and agent["bubble"] == "Mejor: noche"
    assert agent["detail"]["facebook"]["best_time"]["best_daypart"].startswith("noche")
    assert [t["views"] for t in agent["detail"]["facebook"]["top"]] == [700, 500, 50]
    assert agent["detail"]["instagram"]["best_time"]["insufficient_data"] is True


def test_metricas_agent_is_idle_without_enough_data_and_warns_on_read_failure():
    assert os_.build_state(data(), NOW)["agents"]["metricas"]["bubble"] == "Sin datos"
    assert os_.build_state(data(), NOW)["agents"]["metricas"]["status"] == "idle"
    failed = os_.build_state(data(errors={"metrics_rows": "Graph caído"}), NOW)["agents"]["metricas"]
    assert failed["status"] == "warn" and failed["bubble"] == "Sin datos"


def test_all_four_agents_exist_and_bubbles_fit_the_scene():
    busy = proj([vid("generating", at(1, 19)), vid("error", error="x" * 300), vid("publish_error", at(1, 19))])
    state = os_.build_state(data(projects=[busy], text_backends=[],
                                 metrics_rows={"facebook": rows((20, 5), (21, 6), (22, 7)), "instagram": []}), NOW)
    assert set(state["agents"]) == {"videos", "publica", "metricas", "revisor"}
    for agent in state["agents"].values():
        assert set(agent) == {"status", "bubble", "detail"}
        assert agent["status"] in {"busy", "ok", "idle", "warn", "error"}
        assert len(agent["bubble"]) <= os_.MAX_BUBBLE
    assert all(len(f["text"]) < 400 for f in state["findings"])


def test_findings_are_sorted_errors_first():
    state = os_.build_state(data(text_backends=[], errors={"tokens": "x"}), NOW)
    severities = [f["severity"] for f in state["findings"]]
    assert severities == sorted(severities, key=lambda s: os_.SEVERITY_ORDER[s])


def test_collect_state_caches_failures_and_keeps_going():
    os_.reset()
    calls = {"tokens": 0}

    def bad_tokens():
        calls["tokens"] += 1
        raise RuntimeError("sin red")

    readers = {"projects": lambda: [], "tokens": bad_tokens}
    first = os_.collect_state(readers, NOW)
    second = os_.collect_state(readers, NOW)
    assert calls["tokens"] == 1  # el fallo se cachea: no se martilla a Meta cada 3 s
    assert {f["id"] for f in first["findings"] if f["id"].startswith("read:")} == {"read:tokens"}
    assert second["agents"]["videos"]["status"] == "idle"


def test_collect_state_caches_slow_sources_but_not_the_batches():
    os_.reset()
    calls = {"projects": 0, "tokens": 0}

    def projects():
        calls["projects"] += 1
        return []

    def tokens():
        calls["tokens"] += 1
        return []

    readers = {"projects": projects, "tokens": tokens}
    os_.collect_state(readers, NOW)
    os_.collect_state(readers, NOW)
    assert calls == {"projects": 2, "tokens": 1}


def test_collect_state_feeds_the_stuck_tracker():
    os_.reset()
    p = proj([vid("generating")])
    os_.collect_state({"projects": lambda: [p]}, NOW)
    later = os_.collect_state({"projects": lambda: [p]}, NOW + timedelta(minutes=50))
    assert any(f["id"].startswith("stuck:") for f in later["findings"])
```

- [ ] **Paso 2: correr y ver que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q`
Esperado: fallan por `KeyError: 'metricas'` y `AttributeError: ... 'reset'`.

- [ ] **Paso 3: implementar**

Constantes:

```python
FAIL_TTL = 60  # un fallo se recuerda un minuto como maximo: no se reintenta a Meta en cada ciclo de 3 s
READ_TTL = {"projects": 0, "flow": 15, "text_backends": 30, "tokens": 300, "remote_times": 60, "metrics_rows": 600}
```

Agente Métricas (antes de `build_state`):

```python
def _metricas_agent(data: dict) -> dict:
    rows = data.get("metrics_rows")
    if "metrics_rows" in data.get("errors", {}) or rows is None:
        return {"status": "warn" if "metrics_rows" in data.get("errors", {}) else "idle",
                "bubble": "Sin datos", "detail": {}}
    detail = {}
    for network in ("facebook", "instagram"):
        network_rows = rows.get(network) or []
        with_views = [r for r in network_rows if isinstance(r.get("views"), (int, float))]
        top = sorted(with_views, key=lambda r: r["views"], reverse=True)[:3]
        detail[network] = {
            "best_time": feedback_analyzer.best_posting_hour(network_rows),
            "patterns": feedback_analyzer.analyze_from_rows(network_rows),
            "top": [{"title": r.get("title", ""), "views": r["views"]} for r in top],
        }
    best = max(detail.values(), key=lambda n: n["best_time"].get("sample_size", 0))["best_time"]
    if best.get("insufficient_data"):
        return {"status": "idle", "bubble": "Sin datos", "detail": detail}
    return {"status": "ok", "bubble": f"Mejor: {best['best_daypart'].split()[0]}", "detail": detail}
```

En `build_state`, agregar `"metricas": _metricas_agent(data),` al dict `agents` (después de `publica`).

Caché y recolector (al final del módulo):

```python
class _Cache:
    """Cache por fuente: guarda valor o error con la hora de lectura. ttl=0 -> siempre lee."""

    def __init__(self):
        self._entries: dict = {}
        self._lock = threading.Lock()

    def get(self, name: str, ttl: float, fn):
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(name)
        if hit is not None:
            read_at, value, error = hit
            limit = ttl if error is None else min(ttl, FAIL_TTL)
            if now - read_at < limit:
                return value, error
        try:
            value, error = fn(), None
        except Exception as exc:  # una lectura rota no tumba la oficina: ese agente queda en aviso
            logger.warning("oficina: fallo la lectura '%s': %s", name, exc)
            value, error = None, f"{type(exc).__name__}: {exc}"
        with self._lock:
            self._entries[name] = (now, value, error)
        return value, error


_CACHE = _Cache()
_TRACKER = StuckTracker()


def reset() -> None:
    global _CACHE, _TRACKER
    _CACHE = _Cache()
    _TRACKER = StuckTracker()


def collect_state(readers: dict, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    data: dict = {"errors": {}}
    for name, fn in readers.items():
        value, error = _CACHE.get(name, READ_TTL.get(name, 60), fn)
        if error:
            data["errors"][name] = error
        else:
            data[name] = value
    data["seen"] = _TRACKER.update(data.get("projects") or [], now)
    return build_state(data, now)
```

- [ ] **Paso 4: correr y ver que pasan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q`
Esperado: todos pasan.

- [ ] **Paso 5: punto de control** — `git status --short`; no commitear.

---

### Tarea 5: endpoint `/api/office/state` y lectores reales

**Archivos:**
- Modificar: `app.py` (import cerca de `import batch_pipeline`, línea ~53; bloque nuevo tras `api_instagram_best_time`, línea ~5638)
- Test: `tests/test_office_state.py`

**Interfaces:**
- Consume: `office_state.collect_state(readers)`, `office_state.reset()`; en `app.py` ya existen `batch_pipeline`, `meta_auth`, `facebook_publisher`, `instagram_publisher`, `text_provider`, `_analytics_rows`, `_PUBLISHED_FB_PATH`, `_PUBLISHED_IG_PATH`, `logger`.
- Produce: `_office_readers() -> dict[str, Callable]` en `app.py` (los tests lo reemplazan con monkeypatch) y la ruta `GET /api/office/state` → JSON de `collect_state`, o `{"ok": False, "error": str}` si algo inesperado falla (nunca 500).

- [ ] **Paso 1: test que falla** (agregar a `tests/test_office_state.py`)

```python
import base64


def _auth():
    return {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}


def test_office_endpoint_requires_auth_and_returns_the_four_agents(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    monkeypatch.setattr(app_module, "_office_readers", lambda: {"projects": lambda: []})
    os_.reset()
    client = app_module.app.test_client()
    assert client.get("/api/office/state").status_code == 401
    body = client.get("/api/office/state", headers=_auth()).get_json()
    assert body["ok"] is True
    assert set(body["agents"]) == {"videos", "publica", "metricas", "revisor"}


def test_office_endpoint_never_returns_500(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    monkeypatch.setattr(app_module, "_office_readers", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    response = app_module.app.test_client().get("/api/office/state", headers=_auth())
    assert response.status_code == 200
    assert response.get_json()["ok"] is False
```

- [ ] **Paso 2: correr y ver que falla**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q -k endpoint`
Esperado: FAIL (`AttributeError: module 'app' has no attribute '_office_readers'` o 404).

- [ ] **Paso 3: implementar**

En `app.py`, junto a `import batch_pipeline` agregar `import office_state`. Después de `api_instagram_best_time` (antes del bloque "Generación en lote"), insertar:

```python
# ─────────────────────────────────────────────
# Oficina de agentes (solo lectura, ver office_state.py)
# ─────────────────────────────────────────────

def _office_flow() -> dict:
    import flow_images
    return flow_images.status()


def _office_tokens() -> list:
    return [{**meta_auth.validate(p["page_id"]), "page_name": p["name"]} for p in meta_auth.list_pages()]


def _office_remote_times() -> dict:
    """{nombre de pagina: horarios (datetime) de sus posts recientes y programados en Facebook}.
    Las paginas que no se pudieron consultar quedan afuera (list_post_times devuelve None)."""
    out = {}
    for page in meta_auth.list_pages():
        times = facebook_publisher.list_post_times(page["page_id"])
        if times is not None:
            out[page["name"]] = times
    return out


def _office_metrics_rows() -> dict:
    return {
        "facebook": _analytics_rows(_PUBLISHED_FB_PATH, "video_id", facebook_publisher.get_video_stats, multi_page=True),
        "instagram": _analytics_rows(_PUBLISHED_IG_PATH, "media_id", instagram_publisher.get_media_stats, multi_page=True),
    }


def _office_readers() -> dict:
    return {
        "projects": batch_pipeline.list_projects,
        "flow": _office_flow,
        "text_backends": text_provider.available_backends,
        "tokens": _office_tokens,
        "remote_times": _office_remote_times,
        "metrics_rows": _office_metrics_rows,
    }


@app.route("/api/office/state")
def api_office_state():
    try:
        return jsonify(office_state.collect_state(_office_readers()))
    except Exception as exc:
        logger.exception("oficina: no se pudo armar el estado")
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
```

- [ ] **Paso 4: correr y ver que pasan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q`
Esperado: todos pasan.

- [ ] **Paso 5: prueba de humo con datos reales (solo lectura)**

Run:

```bash
venv/Scripts/python.exe -I - <<'EOF'
import sys, json
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
import app, office_state
state = office_state.collect_state(app._office_readers())
print(state["ok"], {k: (v["status"], v["bubble"]) for k, v in state["agents"].items()})
for f in state["findings"]:
    print(f["severity"], f["id"], "|", f["text"])
EOF
```

Esperado: `True` y cuatro agentes con estado. Hace lecturas GET a Meta con los tokens del `.env` (las mismas que el panel ya hace). **No imprimir tokens.** Anotar qué hallazgos salen y confirmar con el usuario si alguno parece un falso positivo antes de la Tarea 8.

- [ ] **Paso 6: punto de control** — `git status --short`; no commitear.

---

### Tarea 6: escena SVG, CSS y navegación por defecto

**Archivos:**
- Modificar: `app.py` (CSS antes de `</style>` en la línea ~572; sidebar ~583; HTML antes de `<div id="tab-video">` ~616; `activateTab` ~1322)
- Test: `tests/test_office_state.py`

**Interfaces:**
- Produce en el DOM (las usa la Tarea 7): `#tab-oficina`; `#office-agent-<id>` (elemento `<g>` con `role="button"`, `tabindex="0"`) y `#office-bubble-<id>` (`<text>`) para `<id>` en `videos`, `publica`, `metricas`, `revisor`; contenedor `#office-panel`.
- Clases CSS: `.office-agent.status-{busy|ok|idle|warn|error}` y `.offline`; `.office-finding`, `.office-sev.{error|warn|info}`, `.office-finding-text`, `.office-views`.
- Convención de marcadores: el CSS entre `/* OFFICE:START */` y `/* OFFICE:END */`, el HTML entre `<!-- OFFICE:START -->` y `<!-- OFFICE:END -->`.

- [ ] **Paso 1: tests que fallan** (agregar)

```python
import re

AGENT_IDS = ("videos", "publica", "metricas", "revisor")


def _office_blocks():
    import app as app_module
    blocks = re.findall(r"(?:/\*|<!--) OFFICE:START (?:\*/|-->)(.*?)(?:/\*|<!--) OFFICE:END (?:\*/|-->)",
                        app_module.HTML, flags=re.S)
    assert blocks, "faltan los marcadores OFFICE:START / OFFICE:END"
    return blocks


def test_office_blocks_do_not_contain_jinja_sequences_or_triple_quotes():
    for block in _office_blocks():
        assert "{{" not in block and "{%" not in block and "{#" not in block
        assert '"""' not in block


def test_home_page_renders_with_the_office_scene_as_default_tab(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    response = app_module.app.test_client().get("/", headers=_auth())
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    for agent in AGENT_IDS:
        assert f'id="office-agent-{agent}"' in html and f'id="office-bubble-{agent}"' in html
    assert 'id="office-panel"' in html
    assert '<div id="tab-oficina">' in html
    assert '<div id="tab-video" style="display:none">' in html
    assert 'class="nav-item active" data-tab="oficina"' in html
```

- [ ] **Paso 2: correr y ver que fallan**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q -k "office_blocks or home_page"`
Esperado: FAIL (`faltan los marcadores`).

- [ ] **Paso 3: CSS** — antes de la línea `</style>` (la primera, ~572) insertar:

```css
  /* OFFICE:START */
  .office-scene { width: 100%; height: auto; display: block; border-radius: var(--radius-md); }
  .office-agent { cursor: pointer; outline: none; }
  .office-focus { fill: transparent; stroke: transparent; }
  .office-agent:focus-visible .office-focus { stroke: var(--red-light); stroke-width: 2; }
  .office-agent .screen { fill: #9a9a9a; }
  .office-agent.status-ok .screen, .office-agent.status-busy .screen { fill: #4ade80; }
  .office-agent.status-warn .screen { fill: #fbbf24; }
  .office-agent.status-error .screen { fill: #f87171; }
  .office-agent.offline .screen { fill: #9a9a9a; }
  .office-agent.status-busy .screen { animation: officeBlink 1.2s ease-in-out infinite; }
  @keyframes officeBlink { 50% { opacity: .35; } }
  @media (prefers-reduced-motion: reduce) { .office-agent.status-busy .screen { animation: none; } }
  .office-agent:hover .bubble-bg { stroke: #8a7f6a; }
  .office-finding { display: flex; gap: 12px; align-items: center; padding: 10px 0; border-top: 1px solid var(--border); }
  .office-sev { font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 999px; flex-shrink: 0; }
  .office-sev.error { background: rgba(248,113,113,.18); color: #fca5a5; }
  .office-sev.warn { background: rgba(251,191,36,.18); color: #fcd34d; }
  .office-sev.info { background: rgba(96,165,250,.18); color: #93c5fd; }
  .office-finding-text { flex: 1; font-size: 14px; }
  .office-views { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
  @media (max-width: 520px) { .office-finding { flex-wrap: wrap; } }
  /* OFFICE:END */
```

- [ ] **Paso 4: sidebar** — en `<nav class="sidebar" id="sidebar">` (~583), antes del item `id="nav-video"`, agregar el item "Oficina" activo y quitar `active` del item "Crear video":

```html
  <a class="nav-item active" data-tab="oficina">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 21V8l9-5 9 5v13"/><path d="M9 21v-6h6v6"/></svg>
    Oficina
  </a>
```

y cambiar `<a class="nav-item active" id="nav-video" data-tab="video">` por `<a class="nav-item" id="nav-video" data-tab="video">`.

- [ ] **Paso 5: HTML** — justo antes de `<div id="tab-video">` (~616) insertar el bloque, y cambiar esa línea a `<div id="tab-video" style="display:none">`:

```html
  <!-- OFFICE:START -->
  <div id="tab-oficina">
    <div class="card">
      <h2>Oficina</h2>
      <p class="card-desc">Los agentes trabajan con lo que ya hace el sistema. Hacé clic en uno para ver su detalle y sus sugerencias.</p>
      <svg class="office-scene" viewBox="0 0 320 176" role="group" aria-label="Oficina con cuatro agentes">
        <rect width="320" height="176" fill="#f4efe6"/>
        <rect y="132" width="320" height="44" fill="#cdbfa6"/>
        <rect x="133" y="10" width="54" height="34" fill="#bfe3f5" stroke="#9a9a9a"/>
        <line x1="160" y1="10" x2="160" y2="44" stroke="#9a9a9a"/>
        <line x1="133" y1="27" x2="187" y2="27" stroke="#9a9a9a"/>
        <g class="office-agent status-idle" id="office-agent-videos" role="button" tabindex="0" aria-label="Videos">
          <rect class="office-focus" x="4" y="52" width="72" height="118" rx="6"/>
          <rect class="bubble-bg" x="4" y="56" width="72" height="20" rx="4" fill="#fff" stroke="#c9c2b4"/>
          <text id="office-bubble-videos" x="40" y="70" text-anchor="middle" fill="#3b2f1e" font-size="11">Cargando…</text>
          <circle cx="40" cy="98" r="9" fill="#f2c9a0"/>
          <rect x="30" y="107" width="20" height="20" rx="6" fill="#D85A30"/>
          <rect x="12" y="122" width="56" height="26" rx="3" fill="#a9743f"/>
          <rect x="30" y="112" width="20" height="12" rx="2" fill="#2b2b3a"/>
          <rect class="screen" x="32" y="114" width="16" height="8"/>
          <text x="40" y="165" text-anchor="middle" fill="#3b2f1e" font-size="11">Videos</text>
        </g>
        <g class="office-agent status-idle" id="office-agent-publica" role="button" tabindex="0" aria-label="Publica" transform="translate(80 0)">
          <rect class="office-focus" x="4" y="52" width="72" height="118" rx="6"/>
          <rect class="bubble-bg" x="4" y="56" width="72" height="20" rx="4" fill="#fff" stroke="#c9c2b4"/>
          <text id="office-bubble-publica" x="40" y="70" text-anchor="middle" fill="#3b2f1e" font-size="11">Cargando…</text>
          <circle cx="40" cy="98" r="9" fill="#f2c9a0"/>
          <rect x="30" y="107" width="20" height="20" rx="6" fill="#378ADD"/>
          <rect x="12" y="122" width="56" height="26" rx="3" fill="#a9743f"/>
          <rect x="30" y="112" width="20" height="12" rx="2" fill="#2b2b3a"/>
          <rect class="screen" x="32" y="114" width="16" height="8"/>
          <text x="40" y="165" text-anchor="middle" fill="#3b2f1e" font-size="11">Publica</text>
        </g>
        <g class="office-agent status-idle" id="office-agent-metricas" role="button" tabindex="0" aria-label="Métricas" transform="translate(160 0)">
          <rect class="office-focus" x="4" y="52" width="72" height="118" rx="6"/>
          <rect class="bubble-bg" x="4" y="56" width="72" height="20" rx="4" fill="#fff" stroke="#c9c2b4"/>
          <text id="office-bubble-metricas" x="40" y="70" text-anchor="middle" fill="#3b2f1e" font-size="11">Cargando…</text>
          <circle cx="40" cy="98" r="9" fill="#f2c9a0"/>
          <rect x="30" y="107" width="20" height="20" rx="6" fill="#1D9E75"/>
          <rect x="12" y="122" width="56" height="26" rx="3" fill="#a9743f"/>
          <rect x="30" y="112" width="20" height="12" rx="2" fill="#2b2b3a"/>
          <rect class="screen" x="32" y="114" width="16" height="8"/>
          <text x="40" y="165" text-anchor="middle" fill="#3b2f1e" font-size="11">Métricas</text>
        </g>
        <g class="office-agent status-idle" id="office-agent-revisor" role="button" tabindex="0" aria-label="Revisor" transform="translate(240 0)">
          <rect class="office-focus" x="4" y="52" width="72" height="118" rx="6"/>
          <rect class="bubble-bg" x="4" y="56" width="72" height="20" rx="4" fill="#fff" stroke="#c9c2b4"/>
          <text id="office-bubble-revisor" x="40" y="70" text-anchor="middle" fill="#3b2f1e" font-size="11">Cargando…</text>
          <circle cx="40" cy="98" r="9" fill="#f2c9a0"/>
          <rect x="30" y="107" width="20" height="20" rx="6" fill="#7F77DD"/>
          <rect x="12" y="122" width="56" height="26" rx="3" fill="#a9743f"/>
          <rect x="30" y="112" width="20" height="12" rx="2" fill="#2b2b3a"/>
          <rect class="screen" x="32" y="114" width="16" height="8"/>
          <text x="40" y="165" text-anchor="middle" fill="#3b2f1e" font-size="11">Revisor</text>
        </g>
      </svg>
    </div>
    <div class="card" id="office-panel"></div>
  </div>
  <!-- OFFICE:END -->
```

- [ ] **Paso 6: `activateTab`** (~1322) — dentro de la función, junto a las otras líneas de `display`, agregar:

```js
  $("tab-oficina").style.display = tab === "oficina" ? "" : "none";
  if (tab === "oficina") officeStart(); else officeStop();
```

(`officeStart` / `officeStop` se definen en la Tarea 7; hasta entonces el panel queda sin datos pero la página carga. No abrir el navegador hasta terminar la Tarea 7.)

- [ ] **Paso 7: correr los tests**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q`
Esperado: todos pasan, incluidos los 2 nuevos.

- [ ] **Paso 8: punto de control** — `git diff --stat`; no commitear.

---

### Tarea 7: JavaScript de la oficina (polling, burbujas, panel, hallazgos y acciones)

**Archivos:**
- Modificar: `app.py` (bloque JS justo antes de `function activateTab`, ~1321; arranque tras el `forEach` que ata los clics de pestañas, ~1353)
- Test: `tests/test_office_state.py`

**Interfaces:**
- Consume: DOM de la Tarea 6, `$` (definido ~1259), `activateTab`, `GET /api/office/state` (Tarea 5), `state.agents[id].{status,bubble,detail}`, `state.findings[]`.
- Produce: `officeStart()`, `officeStop()`, `officePoll()`, `officeRender(force)`, `officeRenderPanel(force)`. El panel solo se reconstruye si cambió su contenido (para no perder el foco del teclado en cada ciclo de 3 s).
- Reglas: el Revisor recalcula su globo con los hallazgos **no descartados**; los descartes se guardan en `localStorage` (con `try/catch`) y siempre también en memoria.

- [ ] **Paso 1: test que falla** (agregar)

```python
def test_office_script_is_wired_and_free_of_unsafe_sinks():
    import app as app_module
    script_blocks = [b for b in _office_blocks() if "officePoll" in b]
    assert script_blocks, "falta el JS de la oficina entre los marcadores"
    js = "\n".join(script_blocks)
    for name in ("officeStart", "officeStop", "officePoll", "officeRender", "officeRenderPanel", "officeRunAction"):
        assert f"function {name}" in js or f"async function {name}" in js
    assert "innerHTML" not in js  # todo el texto del servidor entra con textContent
    assert "officeStart();" in app_module.HTML  # arranque al cargar (la oficina es la pestaña por defecto)
    assert "document.hidden" in js  # el polling se pausa con la pestaña oculta
```

- [ ] **Paso 2: correr y ver que falla**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q -k office_script`
Esperado: FAIL (`falta el JS de la oficina`).

- [ ] **Paso 3: implementar** — antes de `// ── Navegación por pestañas (tabs superiores + sidebar) ──` insertar:

```js
/* OFFICE:START */
const OFFICE_POLL_MS = 3000;
const OFFICE_AGENTS = {
  videos:   { title: "Videos",   views: [["video", "Crear video"], ["lote", "Generación en lote"]] },
  publica:  { title: "Publica",  views: [["historial", "Historial"], ["lote", "Lotes"]] },
  metricas: { title: "Métricas", views: [] },
  revisor:  { title: "Revisor",  views: [["ajustes", "Ajustes"]] },
};
const OFFICE_SEVERITY_LABEL = { error: "Error", warn: "Aviso", info: "Info" };
let officeTimer = null;
let officeState = null;
let officeOffline = false;
let officeSelected = "revisor";
let officeLastSig = "";
const officeDismissedMem = new Set();
try {
  JSON.parse(localStorage.getItem("officeDismissed") || "[]").forEach(id => officeDismissedMem.add(id));
} catch (e) { /* sin localStorage: los descartes viven solo en memoria */ }

function officeEl(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function officeDismiss(id) {
  officeDismissedMem.add(id);
  try { localStorage.setItem("officeDismissed", JSON.stringify([...officeDismissedMem])); } catch (e) { /* idem */ }
}

function officeVisibleFindings() {
  return ((officeState && officeState.findings) || []).filter(f => !officeDismissedMem.has(f.id));
}

function officeAgentView(id) {
  const a = officeState && officeState.agents[id];
  if (!a) return { status: "idle", bubble: officeState ? "Sin datos" : "Cargando…", detail: {} };
  if (id !== "revisor") return a;
  const list = officeVisibleFindings();
  if (!list.length) return Object.assign({}, a, { status: "ok", bubble: "Todo en orden" });
  const worst = list.some(f => f.severity === "error") ? "error" : "warn";
  return Object.assign({}, a, { status: worst, bubble: list.length + (list.length === 1 ? " alerta" : " alertas") });
}

function officeMetricLines(d) {
  const lines = [];
  ["facebook", "instagram"].forEach(net => {
    const n = d[net];
    if (!n) return;
    const name = net === "facebook" ? "Facebook" : "Instagram";
    const bt = n.best_time || {};
    lines.push(bt.insufficient_data
      ? name + ": datos insuficientes (" + (bt.sample_size || 0) + " posts con vistas)"
      : name + ": mejor franja " + bt.best_daypart + " (" + bt.sample_size + " posts)");
    const p = n.patterns || {};
    if (!p.insufficient_data && p.best_title_length) {
      const kw = p.top_keywords && p.top_keywords.length ? ", palabras que funcionan: " + p.top_keywords.join(", ") : "";
      lines.push(name + ": títulos " + p.best_title_length + kw);
    }
    (n.top || []).forEach(t => lines.push(name + " · más visto: " + t.title + " (" + t.views + " vistas)"));
  });
  return lines;
}

function officeDetailLines(id) {
  const a = officeState && officeState.agents[id];
  if (!a || !a.detail) return [];
  const d = a.detail;
  if (id === "videos") {
    return (d.projects || []).map(p => p.page_name + " · " + p.name + ": " + p.done + " de " + p.total + " listos");
  }
  if (id === "publica") {
    const lines = [];
    if (d.next_post) lines.push("Próximo post: " + new Date(d.next_post).toLocaleString("es"));
    Object.keys(d.per_page || {}).forEach(k => lines.push(k + ": " + d.per_page[k] + " programados"));
    return lines;
  }
  if (id === "metricas") return officeMetricLines(d);
  return [];
}

async function officeRunAction(f) {
  const a = f.action;
  if (a.kind === "open_view") { activateTab(a.target); return; }
  if (a.kind !== "call" || !window.confirm(a.label + ": " + f.text + "\n\n¿Continuar?")) return;
  try {
    const res = await fetch(a.url, { method: a.method || "POST" });
    const data = await res.json();
    if (!data.ok) alert(data.error || "No se pudo completar la acción.");
  } catch (e) {
    alert("No se pudo completar la acción.");
  }
  officePoll();
}

function officeFindingRow(f) {
  const row = officeEl("div", "office-finding");
  row.appendChild(officeEl("span", "office-sev " + f.severity, OFFICE_SEVERITY_LABEL[f.severity] || f.severity));
  row.appendChild(officeEl("span", "office-finding-text", f.text));
  if (f.action) {
    const run = officeEl("button", "btn-sm", f.action.label);
    run.type = "button";
    run.addEventListener("click", () => officeRunAction(f));
    row.appendChild(run);
  }
  const dismiss = officeEl("button", "btn-sm", "Descartar");
  dismiss.type = "button";
  dismiss.addEventListener("click", () => { officeDismiss(f.id); officeRender(true); });
  row.appendChild(dismiss);
  return row;
}

function officeRenderPanel(force) {
  const cfg = OFFICE_AGENTS[officeSelected];
  const view = officeAgentView(officeSelected);
  const details = officeDetailLines(officeSelected);
  const findings = officeSelected === "revisor"
    ? officeVisibleFindings()
    : officeVisibleFindings().filter(f => f.agent === officeSelected);
  const sig = JSON.stringify([officeSelected, officeOffline, view.bubble, details, findings.map(f => f.id + f.text)]);
  if (!force && sig === officeLastSig) return;
  officeLastSig = sig;
  const box = $("office-panel");
  box.textContent = "";
  box.appendChild(officeEl("h2", "", cfg.title));
  box.appendChild(officeEl("p", "card-desc", officeOffline ? "Sin conexión con el servidor. Reintentando…" : view.bubble));
  details.forEach(t => box.appendChild(officeEl("p", "card-desc", t)));
  findings.forEach(f => box.appendChild(officeFindingRow(f)));
  if (!findings.length && officeSelected === "revisor" && !officeOffline) {
    box.appendChild(officeEl("p", "card-desc", "Sin alertas por ahora."));
  }
  if (cfg.views.length) {
    const row = officeEl("div", "office-views");
    cfg.views.forEach(pair => {
      const b = officeEl("button", "btn-sm", pair[1]);
      b.type = "button";
      b.addEventListener("click", () => activateTab(pair[0]));
      row.appendChild(b);
    });
    box.appendChild(row);
  }
}

function officeRender(force) {
  Object.keys(OFFICE_AGENTS).forEach(id => {
    const v = officeAgentView(id);
    const text = officeOffline ? "Sin conexión" : v.bubble;
    $("office-agent-" + id).setAttribute("class",
      "office-agent status-" + (officeOffline ? "idle" : v.status) + (officeOffline ? " offline" : ""));
    $("office-bubble-" + id).textContent = text;
    $("office-agent-" + id).setAttribute("aria-label", OFFICE_AGENTS[id].title + ": " + text);
  });
  officeRenderPanel(force);
}

async function officePoll() {
  try {
    const res = await fetch("/api/office/state");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "estado no disponible");
    officeState = data;
    officeOffline = false;
  } catch (e) {
    officeOffline = true;
  }
  officeRender(false);
}

function officeStop() {
  if (officeTimer) { clearInterval(officeTimer); officeTimer = null; }
}

function officeStart() {
  officeStop();
  officePoll();
  officeTimer = setInterval(() => { if (!document.hidden) officePoll(); }, OFFICE_POLL_MS);
}

Object.keys(OFFICE_AGENTS).forEach(id => {
  const g = $("office-agent-" + id);
  const select = () => { officeSelected = id; officeRenderPanel(true); };
  g.addEventListener("click", select);
  g.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(); }
  });
});
/* OFFICE:END */
```

Después del `document.querySelectorAll(".tab-btn, .nav-item[data-tab]").forEach(...)` que ata los clics (~1353) agregar una línea:

```js
officeStart();
```

- [ ] **Paso 4: correr los tests**

Run: `venv/Scripts/python.exe -m pytest tests/test_office_state.py -q`
Esperado: todos pasan.

- [ ] **Paso 5: punto de control** — `git diff --stat`; no commitear.

---

### Tarea 8: verificación visual, regresión y cierre

**Archivos:**
- Modificar: `docs/superpowers/specs/2026-10-13-oficina-agentes-design.md`
- Temporales (en scratch, se borran al final): `office_stub.py`, `office_states.json`; entrada temporal `office-stub` en `.claude/launch.json` (se revierte).

**Interfaces:**
- Consume: todo lo anterior; `app.HTML`, `app.get_default_voice`, `app.render_template_string`.

- [ ] **Paso 1: stub del servidor para ver la UI sin credenciales ni datos reales**

Crear en la carpeta de scratch `office_stub.py`:

```python
import json, sys
from pathlib import Path
sys.path.insert(0, r"C:\Users\lucas\Desktop\tts-proyecto")
import app as A
from flask import Flask, jsonify

stub = Flask(__name__)
STATES = json.loads(Path(__file__).with_name("office_states.json").read_text(encoding="utf-8"))
CURRENT = {"name": "mixed"}


@stub.route("/")
def index():
    return A.render_template_string(A.HTML.replace("{default_voice}", A.get_default_voice()))


@stub.route("/api/office/state")
def state():
    return jsonify(STATES[CURRENT["name"]])


@stub.route("/stub/set/<name>")
def set_state(name):
    CURRENT["name"] = name
    return jsonify({"ok": True})


@stub.route("/api/<path:rest>", methods=["GET", "POST"])
def any_api(rest):
    return jsonify({"ok": True, "pages": [], "projects": [], "voices": [], "videos": []})


if __name__ == "__main__":
    stub.run(port=5055)
```

y `office_states.json` con tres estados (`mixed`, `calm`, `broken`) generados llamando a `office_state.build_state` con datos como los de los tests: `mixed` = un lote generando + un ítem con error + un hueco de hoy + token inválido (producirá globos "Render 1/3", "1 con error"…, revisor "N alertas" en rojo); `calm` = todo `ok`/`idle`; `broken` = `{"ok": false, "error": "x"}` (para ver "Sin conexión"). Generarlo con un script corto en scratch que importe `office_state` y vuelque los tres dicts a JSON.

Agregar temporalmente a `.claude/launch.json` la configuración:

```json
{ "name": "office-stub", "runtimeExecutable": "venv/Scripts/python.exe", "runtimeArgs": ["<ruta absoluta de scratch>/office_stub.py"], "port": 5055 }
```

- [ ] **Paso 2: abrir y revisar en el navegador integrado**

Con `preview_start {name: "office-stub"}`: verificar con `read_page` / `get_page_text` / screenshot:
1. La oficina es lo primero que se ve; los 4 globos muestran el texto del estado `mixed` y las pantallas de los monitores tienen color (rojo/ámbar/verde).
2. Clic en cada agente: el panel cambia (Videos lista el lote; Métricas muestra líneas de métricas o "datos insuficientes"; Revisor lista los hallazgos con botones).
3. "Descartar" en un hallazgo: desaparece y el globo del Revisor baja la cuenta; al recargar sigue descartado (localStorage).
4. Botón "Abrir lotes" → abre la pestaña Lote; en la barra lateral "Oficina" vuelve a la escena y reanuda el polling.
5. Cambiar el estado con `/stub/set/calm` y esperar ≤ 3 s: globos pasan a "Todo en orden" y pantallas verdes; con `/stub/set/broken`: los 4 globos dicen "Sin conexión" y las pantallas quedan grises.
6. `read_console_messages` con `onlyErrors`: sin errores de JS (los 404 de otros endpoints ya los absorbe el stub).
7. Navegación con teclado: Tab llega a cada agente (se ve el aro de foco), Enter/Espacio lo selecciona.
8. `resize_window` a `mobile` (375×812): la escena escala sin desbordar, el texto de los globos sigue legible y los hallazgos pasan a varias líneas sin scroll horizontal; volver a `desktop` al terminar.
9. Modo oscuro (`colorScheme: "dark"`) y claro: el chrome del panel se ve bien en ambos (la escena es de colores fijos a propósito).

Si algo falla, corregir en `app.py`, recargar y repetir desde el punto afectado.

- [ ] **Paso 3: regresión de las vistas antiguas**

En el stub (o en el servidor real, ver Paso 5) abrir una por una "Crear video", "Historial", "Generación en lote" y "Ajustes" desde la barra lateral: cada una debe mostrarse como antes y ocultar la oficina; `read_console_messages` sin errores nuevos.

- [ ] **Paso 4: suite completa**

Run: `venv/Scripts/python.exe -m pytest tests -q`
Esperado: todo en verde (159 previos + los nuevos de `tests/test_office_state.py`).

- [ ] **Paso 5: servidor real**

Reiniciar el servidor como en las sesiones anteriores (parar el `python app.py` del puerto 5000 de este proyecto —no el de macrame-bot— y relanzar `venv/Scripts/python.exe app.py`), comprobar que `/` responde 401 sin credenciales, y pedir al usuario que abra el panel para mirar la oficina con datos reales. No ingresar credenciales por él.

- [ ] **Paso 6: limpieza y spec**

- Borrar `office_stub.py` y `office_states.json` de la carpeta de scratch y **revertir** la entrada `office-stub` de `.claude/launch.json` (`git diff .claude/launch.json` debe quedar vacío).
- Parar el servidor del stub (`preview_stop`).
- En el spec: (1) en la sección "Arquitectura" cambiar `"status": "busy|idle|warn|error"` por `"status": "busy|ok|idle|warn|error"` y añadir una línea: "`ok` = sin novedades (verde); `idle` = sin trabajo (gris)". (2) En "UI → Móvil" reemplazar "la escena pasa a 2×2 por debajo de ~520 px" por "la escena es un SVG que escala al ancho disponible (viewBox 320×176) y los hallazgos pasan a varias líneas por debajo de ~520 px".

- [ ] **Paso 7: punto de control final**

Run: `git status --short` y `git diff --stat`. Resumen al usuario con: archivos tocados, resultado de los tests, capturas de la oficina y los hallazgos que aparecieron con datos reales. **No commitear** salvo que el usuario lo pida.

---

## Autorrevisión contra el spec

| Requisito del spec | Tarea |
|---|---|
| 4 agentes con globo/estado/detalle | 1 (Videos), 2 (Publica), 3 (Revisor), 4 (Métricas) |
| Hallazgo 1 hueco de programación | 2 (`_gap_findings`) |
| Hallazgo 2 ítems con error + Reintentar | 1 y 2 (`error:` / `publish_error:`) |
| Hallazgo 3 token de Meta | 3 (`_token_findings`) |
| Hallazgo 4 proveedor de texto | 3 (`text-provider`) |
| Hallazgo 5 lote trabado | 3 (`StuckTracker`, `_stuck_findings`) |
| Hallazgo 6 Flow caído | 3 (`_flow_findings`) |
| Un fallo de lectura no tumba la oficina | 3 (`read:*`), 4 (`_Cache`), 5 (endpoint nunca 500) |
| Caché por fuente / sin Graph cada 3 s | 4 (`READ_TTL`, `FAIL_TTL`) |
| Endpoint `/api/office/state` solo lectura | 5 |
| Pestaña por defecto + barra lateral con "Oficina" + vistas antiguas intactas | 6, 8 |
| Escena SVG con agentes accesibles (`role="button"`, `aria-label`, teclado) | 6, 7 |
| Panel por agente, panel nuevo de Métricas, acciones `open_view` / `call` con confirmación | 7 |
| Descartes en `localStorage` con `try/catch` | 7 |
| Polling pausado con la pestaña oculta; "Sin conexión" | 7 |
| `prefers-reduced-motion` | 6 (CSS) |
| Móvil y modo oscuro | 8 |
| Tests con datos falsos para cada caso del spec | 1 a 5 |
| Marcadores para extraer luego el bloque de `app.py` | 6, 7 |

Desviaciones deliberadas respecto del spec (ya reflejadas en la Tarea 8, Paso 6): se agrega el estado `ok` (verde sin novedades) y la escena escala en móvil en lugar de pasar a 2×2. Además, el "panel" de cada agente se implementa como un panel inline bajo la escena con botones que navegan a las vistas actuales (en lugar de mover cada pestaña dentro del agente), para no tocar el HTML de esas vistas; el globo de Métricas muestra la mejor franja horaria ("Mejor: noche") porque el sistema no tiene CTR.
