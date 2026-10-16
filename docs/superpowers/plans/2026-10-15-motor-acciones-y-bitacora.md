# Motor de acciones de la oficina + bitácora pasiva — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que los agentes de la oficina reparen solos lo reparable (con propuestas aprobables, pausa y freno) y que una bitácora persistente anote también qué alertas se abrieron/resolvieron y cómo cambió el estado de cada agente, aunque el panel esté cerrado.

**Architecture:** Un módulo nuevo `office_agents.py` con un único hilo daemon (ciclo de 60 s, solo en el proceso que tiene el lock del programador). Cada ciclo lee los datos con `office_state.collect_data`, anota eventos pasivos (alerta abierta/resuelta, cambio de estado), evalúa 3 reglas (`retry_cause_resolved`, `republish`, `release_stuck`) y ejecuta o propone. Todo el estado (pausa, modos, intentos, propuestas, bitácora) vive en `job_store` bajo el nombre `office_agents`. `/api/office/state` suma un bloque `automation` leído del archivo; la UI agrega dos tarjetas (Automático, Bitácora).

**Tech Stack:** Python 3.10, Flask, pytest, JS vanilla dentro de `app.py`.

**Spec:** `docs/superpowers/specs/2026-10-14-agentes-autonomos-motor-reparar-design.md` (motor + Reparar). Lo que este plan agrega al spec: los eventos pasivos en la bitácora (ver "Desviaciones del spec").

## Desviaciones del spec (deliberadas)

1. **Bitácora de 400 entradas, no 200**, y con un campo `kind`: `action` (regla ejecutada), `exhausted` (intentos agotados), `alert_open`, `alert_resolved`, `status`. Los eventos pasivos comparten la lista para que haya una sola tarjeta y un solo almacén.
2. **`open_findings` y `statuses` se persisten** en el mismo archivo, así un reinicio no vuelve a anotar como "nuevas" las alertas que ya estaban abiertas.
3. **Las alertas no se dan por resueltas mientras haya un hallazgo `read:*`** (una lectura caída hace desaparecer hallazgos de otras fuentes y daría resueltas falsas).
4. El freno global agrega el hallazgo `automation-brake` pasando `extra={"brake": ...}` a `collect_data` (así `office_state` no importa `office_agents`).

## Global Constraints

- **Textos de usuario en español**, tono de los existentes ("Reintentar", "ítem 3", "Sin datos").
- **`app.py` usa CRLF.** Después de editarlo: `python -c "d=open('app.py','rb').read(); print(d.count(b'\r\n'), d.count(b'\n'))"` debe imprimir dos números iguales. `office_state.py`, `batch_pipeline.py`, tests: LF. `job_store.py`: CRLF (no se toca).
- **Dentro de los bloques `OFFICE:START/END` de `app.py`** (el HTML es un `r"""..."""` pasado por Jinja): sin `{{`, `{%`, `{#`, sin `"""`, sin `innerHTML`; todo texto del servidor entra con `textContent`.
- **Nunca 500** en los endpoints `/api/office/*`; errores como `{"ok": false, "error": "..."}` con el texto pasado por `office_state._redact`.
- **Los endpoints de escritura aceptan solo IDs conocidos** (regla, propuesta); nunca ejecutan una URL ni función recibida del cliente.
- **No hacer commit de `app.py`, `batch_pipeline.py` ni `tests/test_office_state.py` enteros**: ya tienen cambios sin commitear que no son de este plan (ver `git status`). Los pasos de commit usan `git add -p` o se omiten hasta que el usuario lo pida. Archivos nuevos (`office_agents.py`, `tests/test_office_agents.py`, `tests/test_release_stuck.py`) sí se pueden agregar enteros.
- Tests: `python -m pytest <archivo> -q` desde la raíz del repo.

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `office_state.py` (modificar) | `collect_data` pública, `collect_state` la reusa, hallazgo `automation-brake`. Sigue siendo solo lectura. |
| `batch_pipeline.py` (modificar) | `release_stuck_item` y `holds_scheduler_lock`. |
| `office_agents.py` (crear) | Estado persistido, eventos pasivos, reglas, `evaluate`/`apply`, ciclo, acciones del usuario (pausa/modo/aprobar/rechazar), bloque `automation`, `run_forever`. |
| `app.py` (modificar) | Endpoints, bloque `automation` en `/api/office/state`, arranque del hilo, HTML/CSS/JS de las dos tarjetas. |
| `tests/test_release_stuck.py` (crear) | `release_stuck_item`. |
| `tests/test_office_agents.py` (crear) | Todo `office_agents.py`. |
| `tests/test_office_state.py` (modificar) | `collect_data`, brake, endpoints, cableado de la UI, fixture que aísla el almacén. |

---

### Task 1: `office_state` — `collect_data` pública y hallazgo del freno

**Files:**
- Modify: `office_state.py` (`_revisor_findings` ~l.270, `collect_state` ~l.382)
- Test: `tests/test_office_state.py`

**Interfaces:**
- Produces: `collect_data(readers: dict, now: Optional[datetime] = None, extra: Optional[dict] = None) -> dict` (lo que hoy hace `collect_state` antes de `build_state`; `extra` se mezcla al final en el dict). `collect_state(readers, now=None, extra=None) -> dict` ahora es `build_state(collect_data(...), now)`. `data["brake"] = {"used": int, "limit": int}` produce el hallazgo `automation-brake` (warn, agente `revisor`).

- [ ] **Step 1: Write the failing tests** — agregar al final de la sección de tests puros de `tests/test_office_state.py` (antes de `import base64`):

```python
def test_brake_finding_appears_only_when_the_hourly_limit_is_reached():
    full = os_.build_state(data(brake={"used": 20, "limit": 20}), NOW)
    finding = next(f for f in full["findings"] if f["id"] == "automation-brake")
    assert finding["severity"] == "warn" and finding["agent"] == "revisor"
    assert "20 acciones por hora" in finding["text"]
    assert "automation-brake" not in ids(os_.build_state(data(brake={"used": 19, "limit": 20}), NOW))
    assert "automation-brake" not in ids(os_.build_state(data(), NOW))


def test_collect_data_merges_extra_and_collect_state_is_build_state_over_it():
    os_.reset()
    readers = {"projects": lambda: []}
    got = os_.collect_data(readers, NOW, extra={"brake": {"used": 1, "limit": 20}})
    assert got["projects"] == [] and got["seen"] == {} and got["errors"] == {}
    assert got["brake"] == {"used": 1, "limit": 20}
    os_.reset()
    assert os_.collect_state(readers, NOW) == os_.build_state(os_.collect_data(readers, NOW), NOW)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_office_state.py -q -k "brake_finding or collect_data"`
Expected: FAIL (`AttributeError: module 'office_state' has no attribute 'collect_data'` y falta el hallazgo).

- [ ] **Step 3: Implement** — en `office_state.py`:

Agregar antes de `_revisor_findings`:

```python
def _brake_findings(data: dict) -> list:
    brake = data.get("brake") or {}
    if brake.get("limit") and brake.get("used", 0) >= brake["limit"]:
        return [_finding("automation-brake", "revisor", "warn",
                         f"Los agentes alcanzaron su límite de {brake['limit']} acciones por hora")]
    return []
```

En `_revisor_findings`, cambiar la línea `findings += _token_findings(...)` por:

```python
    findings += _token_findings(data, now) + _flow_findings(data) + _stuck_findings(data, now) + _brake_findings(data)
```

Reemplazar `collect_state` completo por:

```python
def collect_data(readers: dict, now: Optional[datetime] = None, extra: Optional[dict] = None) -> dict:
    """Lee todas las fuentes (con cache) y devuelve los datos crudos que consume `build_state`.
    `extra` se mezcla al final: lo usa quien tenga datos propios que el Revisor deba ver (el freno)."""
    now = now or datetime.now()
    data: dict = {"errors": {}}
    for name, fn in readers.items():
        value, error = _CACHE.get(name, READ_TTL.get(name, 60), fn)
        if error:
            data["errors"][name] = error
        else:
            data[name] = value
    data["seen"] = _TRACKER.update(data.get("projects") or [], now)
    data.update(extra or {})
    return data


def collect_state(readers: dict, now: Optional[datetime] = None, extra: Optional[dict] = None) -> dict:
    now = now or datetime.now()
    return build_state(collect_data(readers, now, extra), now)
```

- [ ] **Step 4: Run to verify they pass (suite de la oficina completa)**

Run: `python -m pytest tests/test_office_state.py -q`
Expected: todo PASS (incluidos los tests previos de `collect_state`).

---

### Task 2: `batch_pipeline` — soltar un ítem trabado y saber si este proceso tiene el lock

**Files:**
- Modify: `batch_pipeline.py` (después de `retry_publish_video` ~l.1122; y junto a `_scheduler_lock_file` ~l.2693)
- Test: `tests/test_release_stuck.py` (crear)

**Interfaces:**
- Produces: `release_stuck_item(project_id: str, index: int, expected_status: str) -> bool` (`generating → pending` con `stage=None`; `publishing → ready`; `False` si el ítem ya no está en `expected_status`, si el proyecto no existe o si `expected_status` no es ninguno de los dos). `holds_scheduler_lock() -> bool`.

- [ ] **Step 1: Write the failing test** — crear `tests/test_release_stuck.py`:

```python
# -*- coding: utf-8 -*-
"""release_stuck_item: suelta un unico item trabado, bajo el lock del modulo y solo si sigue en el estado esperado."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline


@pytest.fixture
def store(monkeypatch):
    projects = {"p1": {"id": "p1", "videos": [
        {"index": 0, "status": "generating", "stage": "voz"},
        {"index": 1, "status": "publishing", "stage": None},
        {"index": 2, "status": "ready", "stage": None},
    ]}}
    monkeypatch.setattr(batch_pipeline, "_load", lambda: copy.deepcopy(projects))
    monkeypatch.setattr(batch_pipeline, "_save", lambda p: (projects.clear(), projects.update(copy.deepcopy(p))))
    return projects


def status_of(store, index):
    return next(v for v in store["p1"]["videos"] if v["index"] == index)


def test_generating_goes_back_to_pending_and_clears_the_stage(store):
    assert batch_pipeline.release_stuck_item("p1", 0, "generating") is True
    assert status_of(store, 0)["status"] == "pending" and status_of(store, 0)["stage"] is None


def test_publishing_goes_back_to_ready(store):
    assert batch_pipeline.release_stuck_item("p1", 1, "publishing") is True
    assert status_of(store, 1)["status"] == "ready"


def test_does_nothing_when_the_item_changed_state(store):
    assert batch_pipeline.release_stuck_item("p1", 2, "generating") is False
    assert status_of(store, 2)["status"] == "ready"


def test_unknown_project_item_or_status_is_refused(store):
    assert batch_pipeline.release_stuck_item("nope", 0, "generating") is False
    assert batch_pipeline.release_stuck_item("p1", 9, "generating") is False
    assert batch_pipeline.release_stuck_item("p1", 0, "error") is False
    assert status_of(store, 0)["status"] == "generating"


def test_holds_scheduler_lock_reflects_the_module_handle(monkeypatch):
    monkeypatch.setattr(batch_pipeline, "_scheduler_lock_file", None)
    assert batch_pipeline.holds_scheduler_lock() is False
    monkeypatch.setattr(batch_pipeline, "_scheduler_lock_file", object())
    assert batch_pipeline.holds_scheduler_lock() is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_release_stuck.py -q`
Expected: FAIL (`AttributeError: ... no attribute 'release_stuck_item'`).

- [ ] **Step 3: Implement** — en `batch_pipeline.py`, después de `retry_publish_video`:

```python
def release_stuck_item(project_id: str, index: int, expected_status: str) -> bool:
    """Suelta UN item trabado, igual que _recover_stuck_videos hace con todos al arrancar:
    "generating" -> "pending" (stage None) y "publishing" -> "ready". Solo actua si el item
    sigue en `expected_status` (puede haber terminado mientras alguien decidia soltarlo)."""
    target = {"generating": "pending", "publishing": "ready"}.get(expected_status)
    if target is None:
        return False
    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return False
        video = next((v for v in project.get("videos", []) if v["index"] == index), None)
        if not video or video["status"] != expected_status:
            return False
        video["status"] = target
        if expected_status == "generating":
            video["stage"] = None
        _save(projects)
    return True
```

Y justo debajo de `_scheduler_lock_file = None  # ...`:

```python
def holds_scheduler_lock() -> bool:
    """True si ESTE proceso es el dueño del scheduler (y por lo tanto el unico que puede actuar solo)."""
    return _scheduler_lock_file is not None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_release_stuck.py -q`
Expected: 5 passed.

---

### Task 3: `office_agents` — estado persistido y eventos pasivos (la bitácora del #1)

**Files:**
- Create: `office_agents.py`
- Test: `tests/test_office_agents.py` (crear)

**Interfaces:**
- Produces:
  - `office_agents.STORE = "office_agents"`, `LOG_MAX = 400`, `_LOCK = threading.RLock()`.
  - `_default_state() -> dict` con claves `paused: bool, modes: dict, attempts: dict, proposals: list, log: list, open_findings: dict, statuses: dict`.
  - `_load_state() -> dict` (tipos inválidos o archivo ausente → valores por defecto), `_save_state(state) -> None`.
  - `_iso(dt) -> str` (segundos), `_log(state, now, **entry)` (agrega `ts`, recorta a `LOG_MAX`).
  - `record_events(state: dict, snapshot: dict, now: datetime) -> None`: entradas `kind` = `alert_open` (`message` = severidad), `alert_resolved` (`message` = `"resuelta"`), `status` (`label` = globo, `message` = `"idle → busy"`; el primero usa `"— → idle"`). Cada entrada: `ts, kind, agent, key, label, message`.

- [ ] **Step 1: Write the failing tests** — crear `tests/test_office_agents.py`:

```python
# -*- coding: utf-8 -*-
"""Motor de acciones de la oficina: reglas, ciclo, propuestas y bitacora. Todo sobre datos falsos y un almacen en memoria."""
import copy
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import office_agents
import office_state

NOW = datetime(2026, 10, 15, 15, 0)


@pytest.fixture(autouse=True)
def memory_store(monkeypatch):
    saved = {}
    monkeypatch.setattr(office_agents.job_store, "load", lambda name: copy.deepcopy(saved.get(name, {})))
    monkeypatch.setattr(office_agents.job_store, "save", lambda name, d: saved.__setitem__(name, copy.deepcopy(d)))
    office_state.reset()
    return saved


def vid(status, error=None, error_at=None, **extra):
    return {"status": status, "scheduled_at": None, "error": error,
            "error_at": error_at.isoformat() if error_at else None, **extra}


def proj(videos=(), page="MACRAME CREATIVO", pid="p1"):
    return {"id": pid, "name": "Lote 1", "page_name": page, "status": "running",
            "videos": [dict(index=i, **v) for i, v in enumerate(videos)]}


def data(**kw):
    base = {"projects": [], "flow": {"state": "ok", "message": ""}, "text_backends": ["freellm:x"],
            "tokens": [], "remote_times": {}, "metrics_rows": {"facebook": [], "instagram": []},
            "seen": {}, "errors": {}}
    base.update(kw)
    return base


def snapshot(findings=(), agents=None, ok=True):
    agents = agents or {"videos": ("idle", "Sin trabajo")}
    return {"ok": ok, "agents": {k: {"status": s, "bubble": b} for k, (s, b) in agents.items()},
            "findings": [{"id": i, "agent": a, "severity": sev, "text": t, "action": None}
                         for i, a, sev, t in findings]}


# ---- estado persistido ----------------------------------------------------------------------

def test_a_missing_or_malformed_store_falls_back_to_defaults(memory_store):
    assert office_agents._load_state() == office_agents._default_state()
    memory_store["office_agents"] = {"paused": "si", "log": 5, "modes": {"republish": "off"}}
    state = office_agents._load_state()
    assert state["paused"] is False and state["log"] == []
    assert state["modes"] == {"republish": "off"}


def test_state_round_trips_through_the_store():
    state = office_agents._default_state()
    state["paused"] = True
    office_agents._save_state(state)
    assert office_agents._load_state()["paused"] is True


# ---- eventos pasivos (bitacora) ---------------------------------------------------------------

def test_first_snapshot_logs_one_status_per_agent_and_each_new_alert():
    state = office_agents._default_state()
    snap = snapshot([("token-invalid:X", "revisor", "error", "Token de Meta inválido en X")],
                    {"videos": ("idle", "Sin trabajo"), "revisor": ("error", "1 alerta")})
    office_agents.record_events(state, snap, NOW)
    kinds = [(e["kind"], e["agent"]) for e in state["log"]]
    assert ("alert_open", "revisor") in kinds
    assert ("status", "videos") in kinds and ("status", "revisor") in kinds
    status = next(e for e in state["log"] if e["kind"] == "status" and e["agent"] == "videos")
    assert status["message"] == "— → idle" and status["label"] == "Sin trabajo"
    opened = next(e for e in state["log"] if e["kind"] == "alert_open")
    assert opened["key"] == "token-invalid:X" and opened["message"] == "error" and opened["ts"] == "2026-10-15T15:00:00"


def test_an_unchanged_snapshot_adds_nothing():
    state = office_agents._default_state()
    snap = snapshot([("gap:x", "revisor", "warn", "Hoy sin posts en X")])
    office_agents.record_events(state, snap, NOW)
    before = len(state["log"])
    office_agents.record_events(state, snap, NOW + timedelta(minutes=1))
    assert len(state["log"]) == before


def test_an_alert_that_disappears_is_logged_as_resolved_once():
    state = office_agents._default_state()
    office_agents.record_events(state, snapshot([("gap:x", "revisor", "warn", "Hoy sin posts en X")]), NOW)
    office_agents.record_events(state, snapshot(), NOW + timedelta(minutes=1))
    resolved = [e for e in state["log"] if e["kind"] == "alert_resolved"]
    assert len(resolved) == 1 and resolved[0]["label"] == "Hoy sin posts en X" and resolved[0]["key"] == "gap:x"
    office_agents.record_events(state, snapshot(), NOW + timedelta(minutes=2))
    assert len([e for e in state["log"] if e["kind"] == "alert_resolved"]) == 1


def test_status_change_is_logged_with_from_and_to():
    state = office_agents._default_state()
    office_agents.record_events(state, snapshot(agents={"videos": ("idle", "Sin trabajo")}), NOW)
    office_agents.record_events(state, snapshot(agents={"videos": ("busy", "Render 1/3")}), NOW + timedelta(minutes=1))
    last = state["log"][-1]
    assert (last["kind"], last["message"], last["label"]) == ("status", "idle → busy", "Render 1/3")


def test_alerts_are_not_resolved_while_a_source_could_not_be_read():
    state = office_agents._default_state()
    office_agents.record_events(state, snapshot([("gap:x", "revisor", "warn", "Hoy sin posts en X")]), NOW)
    degraded = snapshot([("read:projects", "revisor", "warn", "No pude leer los lotes: boom")])
    office_agents.record_events(state, degraded, NOW + timedelta(minutes=1))
    assert not [e for e in state["log"] if e["kind"] == "alert_resolved"]
    office_agents.record_events(state, snapshot(), NOW + timedelta(minutes=2))
    assert {e["key"] for e in state["log"] if e["kind"] == "alert_resolved"} == {"gap:x", "read:projects"}


def test_a_failed_state_is_ignored_and_the_log_is_capped():
    state = office_agents._default_state()
    office_agents.record_events(state, {"ok": False, "error": "boom"}, NOW)
    assert state["log"] == [] and state["open_findings"] == {}
    for i in range(office_agents.LOG_MAX + 25):
        office_agents._log(state, NOW, kind="status", agent="videos", key="videos", label=str(i), message="")
    assert len(state["log"]) == office_agents.LOG_MAX and state["log"][-1]["label"] == str(office_agents.LOG_MAX + 24)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_office_agents.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'office_agents'`).

- [ ] **Step 3: Implement** — crear `office_agents.py`:

```python
# -*- coding: utf-8 -*-
"""Agentes autonomos de la oficina (ver docs/superpowers/specs/2026-10-14-agentes-autonomos-motor-reparar-design.md).

Un ciclo (hilo daemon, solo en el proceso dueño del programador) que: anota en la bitacora lo que
cambio en la oficina (alertas abiertas/resueltas, estado de cada agente), evalua las reglas de
Reparar y las ejecuta o las propone. Todo el estado vive en job_store ("office_agents"). `evaluate`
(decide, sin efectos) esta separada de `apply` (ejecuta y persiste) para probar sin hilos ni red."""

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

import batch_pipeline
import job_store
import office_state

logger = logging.getLogger("office_agents")

STORE = "office_agents"
CYCLE_SECONDS = 60
AUTO_ACTIONS_PER_HOUR = 20
PROPOSAL_TTL = timedelta(hours=24)
REJECT_SILENCE = timedelta(hours=24)
ATTEMPTS_TTL = timedelta(days=7)
LOG_MAX = 400
LOG_SHOWN = 20
MODES = ("off", "propose", "auto")

_LOCK = threading.RLock()  # serializa leer-modificar-guardar del archivo de estado (hilo + endpoints)


# ---- estado persistido ----------------------------------------------------------------------

def _default_state() -> dict:
    return {"paused": False, "modes": {}, "attempts": {}, "proposals": [], "log": [],
            "open_findings": {}, "statuses": {}}


def _load_state() -> dict:
    """Lee el archivo; lo ausente o con el tipo equivocado vuelve al valor por defecto (nunca corta el hilo)."""
    raw = job_store.load(STORE)
    state = _default_state()
    if isinstance(raw, dict):
        for key, default in state.items():
            if isinstance(raw.get(key), type(default)):
                state[key] = raw[key]
    return state


def _save_state(state: dict) -> None:
    job_store.save(STORE, state)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _log(state: dict, now: datetime, **entry) -> None:
    state["log"].append({"ts": _iso(now), **entry})
    del state["log"][:-LOG_MAX]


# ---- eventos pasivos --------------------------------------------------------------------------

def record_events(state: dict, snapshot: dict, now: datetime) -> None:
    """Anota lo que cambio desde el ciclo anterior: alerta abierta, alerta resuelta, cambio de estado
    de un agente. Una alerta no se da por resuelta mientras haya un hallazgo `read:*` (una fuente caida
    hace desaparecer los hallazgos de otras y daria resueltas falsas)."""
    if not snapshot.get("ok"):
        return
    findings = {f["id"]: f for f in snapshot.get("findings", [])}
    for fid, finding in findings.items():
        if fid not in state["open_findings"]:
            state["open_findings"][fid] = {"agent": finding["agent"], "text": finding["text"]}
            _log(state, now, kind="alert_open", agent=finding["agent"], key=fid,
                 label=finding["text"], message=finding["severity"])
    if not any(fid.startswith("read:") for fid in findings):
        for fid in [k for k in state["open_findings"] if k not in findings]:
            known = state["open_findings"].pop(fid)
            _log(state, now, kind="alert_resolved", agent=known["agent"], key=fid,
                 label=known["text"], message="resuelta")
    for agent, view in snapshot.get("agents", {}).items():
        before = state["statuses"].get(agent)
        if before != view["status"]:
            state["statuses"][agent] = view["status"]
            _log(state, now, kind="status", agent=agent, key=agent, label=view["bubble"],
                 message=f"{before or '—'} → {view['status']}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_office_agents.py -q`
Expected: 7 passed.

---

### Task 4: `office_agents` — reglas de Reparar (predicados y ejecutores)

**Files:**
- Modify: `office_agents.py`
- Test: `tests/test_office_agents.py`

**Interfaces:**
- Consumes: `office_state._items`, `_page`, `_norm`, `_parse`, `stuck_key`, `STUCK_AFTER`; `batch_pipeline.retry_video(pid, index) -> bool`, `retry_publish_video(pid, index) -> bool`, `release_stuck_item(pid, index, status) -> bool`.
- Produces:
  - `Target(key: str, label: str, payload: dict)` (dataclass frozen). `payload = {"project_id", "index", "status"}`.
  - `Rule(id, agent, label, description, default_mode, max_attempts, cooldown, find, execute)` (dataclass frozen). `find(data, now) -> list[Target]`; `execute(target) -> (bool, str)`.
  - `RULES: tuple[Rule, ...]` (ids `retry_cause_resolved`, `republish`, `release_stuck`), `RULES_BY_ID: dict`.
  - `find_cause_resolved`, `find_republish`, `find_stuck`, `_scan(data, now) -> list[tuple[Rule, Target]]` (una regla que lanza se registra y se salta).
  - Target key: `"{rule_id}:{project_id}:{index}"`; label: `"{página}: ítem {n}"` (n = index + 1).

- [ ] **Step 1: Write the failing tests** — agregar a `tests/test_office_agents.py`:

```python
# ---- reglas -----------------------------------------------------------------------------------

OLD = NOW - timedelta(hours=3)


def keys(found):
    return [t.key for _, t in found]


def test_cause_resolved_flow_error_when_flow_is_back_and_old_enough():
    p = proj([vid("error", "Flow no devolvió imagen en 240s.", OLD)])
    (found,) = office_agents._scan(data(projects=[p]), NOW)
    rule, target = found
    assert rule.id == "retry_cause_resolved" and target.key == "retry_cause_resolved:p1:0"
    assert "ítem 1" in target.label
    assert target.payload == {"project_id": "p1", "index": 0, "status": "error"}


def test_cause_resolved_waits_for_the_cause_and_for_two_hours():
    p_new = proj([vid("error", "Flow falló", NOW - timedelta(minutes=30))])
    p_ok = proj([vid("error", "Flow falló", OLD)])
    down = {"state": "no_browser", "message": "cerrado"}
    assert office_agents._scan(data(projects=[p_new]), NOW) == []
    assert office_agents._scan(data(projects=[p_ok], flow=down), NOW) == []
    assert office_agents._scan(data(projects=[p_ok], errors={"flow": "x"}, flow=None), NOW) == []


def test_cause_resolved_text_provider_error_needs_a_backend_and_unrelated_errors_are_ignored():
    p = proj([vid("error", "Sin proveedor de texto disponible", OLD), vid("error", "ffmpeg murió", OLD)])
    assert keys(office_agents._scan(data(projects=[p]), NOW)) == ["retry_cause_resolved:p1:0"]
    assert office_agents._scan(data(projects=[p], text_backends=[]), NOW) == []
    assert office_agents._scan(data(projects=[p], text_backends=None), NOW) == []


def test_cause_resolved_error_without_error_at_is_eligible_immediately():
    p = proj([vid("error", "Flow falló", None)])
    assert keys(office_agents._scan(data(projects=[p]), NOW)) == ["retry_cause_resolved:p1:0"]


def test_republish_needs_a_valid_token_for_that_page_and_no_auth_error():
    p = proj([vid("publish_error"), vid("publish_error", last_publish_auth_error=True)])
    ok = [{"page_name": "macrame creativo", "ok": True}]
    assert keys(office_agents._scan(data(projects=[p], tokens=ok), NOW)) == ["republish:p1:0"]
    bad = [{"page_name": "MACRAME CREATIVO", "ok": False, "auth_error": True}]
    assert office_agents._scan(data(projects=[p], tokens=bad), NOW) == []
    other = [{"page_name": "OTRA PAGINA", "ok": True}]
    assert office_agents._scan(data(projects=[p], tokens=other), NOW) == []
    assert office_agents._scan(data(projects=[p], tokens=[]), NOW) == []


def test_release_stuck_uses_the_stuck_threshold_and_remembers_the_expected_status():
    p = proj([vid("generating"), vid("publishing")])
    seen = {office_state.stuck_key("p1", 0, "generating"): NOW - timedelta(minutes=50),
            office_state.stuck_key("p1", 1, "publishing"): NOW - timedelta(minutes=10)}
    (found,) = office_agents._scan(data(projects=[p], seen=seen), NOW)
    assert found[1].key == "release_stuck:p1:0" and found[1].payload["status"] == "generating"


def test_all_rules_skip_when_batches_could_not_be_read():
    p = proj([vid("error", "Flow falló", OLD)])
    assert office_agents._scan(data(projects=[p], errors={"projects": "boom"}), NOW) == []


def test_a_rule_that_raises_is_skipped_and_the_others_still_run(monkeypatch):
    def boom(d, n):
        raise RuntimeError("regla rota")
    broken = office_agents.Rule("broken", "videos", "x", "x", "auto", 1, timedelta(0), boom, lambda t: (True, ""))
    monkeypatch.setattr(office_agents, "RULES", (broken,) + office_agents.RULES)
    p = proj([vid("error", "Flow falló", OLD)])
    assert keys(office_agents._scan(data(projects=[p]), NOW)) == ["retry_cause_resolved:p1:0"]


def test_executors_call_the_existing_batch_functions(monkeypatch):
    calls = []
    monkeypatch.setattr(office_agents.batch_pipeline, "retry_video", lambda pid, i: calls.append(("retry", pid, i)) or True)
    monkeypatch.setattr(office_agents.batch_pipeline, "retry_publish_video",
                        lambda pid, i: calls.append(("republish", pid, i)) or False)
    monkeypatch.setattr(office_agents.batch_pipeline, "release_stuck_item",
                        lambda pid, i, s: calls.append(("release", pid, i, s)) or True)
    t = office_agents.Target("k", "l", {"project_id": "p1", "index": 2, "status": "generating"})
    assert office_agents.RULES_BY_ID["retry_cause_resolved"].execute(t)[0] is True
    ok, message = office_agents.RULES_BY_ID["republish"].execute(t)
    assert ok is False and "ya no" in message.lower()
    assert office_agents.RULES_BY_ID["release_stuck"].execute(t)[0] is True
    assert calls == [("retry", "p1", 2), ("republish", "p1", 2), ("release", "p1", 2, "generating")]


def test_default_modes_and_limits_follow_the_spec():
    r = office_agents.RULES_BY_ID
    assert (r["retry_cause_resolved"].default_mode, r["retry_cause_resolved"].max_attempts,
            r["retry_cause_resolved"].cooldown) == ("auto", 2, timedelta(hours=2))
    assert (r["republish"].default_mode, r["republish"].max_attempts, r["republish"].cooldown) == ("auto", 2, timedelta(hours=1))
    assert (r["release_stuck"].default_mode, r["release_stuck"].max_attempts, r["release_stuck"].cooldown) == ("propose", 3, timedelta(hours=1))
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_office_agents.py -q -k "cause_resolved or republish or release_stuck or rule or executors or default_modes"`
Expected: FAIL (`AttributeError: ... '_scan'` / `'Rule'`).

- [ ] **Step 3: Implement** — agregar a `office_agents.py` (después de `record_events`):

```python
# ---- reglas de Reparar ------------------------------------------------------------------------

MIN_ERROR_AGE = timedelta(hours=2)
TEXT_ERROR_MARKERS = ("proveedor de texto", "freellm")


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    payload: dict


@dataclass(frozen=True)
class Rule:
    id: str
    agent: str
    label: str
    description: str
    default_mode: str
    max_attempts: int
    cooldown: timedelta
    find: Callable
    execute: Callable


def _target(rule_id: str, project: dict, video: dict) -> Target:
    return Target(f"{rule_id}:{project['id']}:{video['index']}",
                  f"{office_state._page(project)}: ítem {video['index'] + 1}",
                  {"project_id": project["id"], "index": video["index"], "status": video.get("status")})


def _cause_resolved(error, data: dict) -> bool:
    text = (error or "").lower()
    if "flow" in text and (data.get("flow") or {}).get("state") == "ok":
        return True
    return any(marker in text for marker in TEXT_ERROR_MARKERS) and bool(data.get("text_backends"))


def find_cause_resolved(data: dict, now: datetime) -> list:
    """Ítem en error cuyo motivo era Flow o el proveedor de texto, que hoy funcionan, y que lleva >= 2 h así."""
    if "projects" in data.get("errors", {}):
        return []
    found = []
    for project, video in office_state._items(data.get("projects", [])):
        if video.get("status") != "error" or not _cause_resolved(video.get("error"), data):
            continue
        error_at = office_state._parse(video.get("error_at"))
        if error_at and now - error_at < MIN_ERROR_AGE:
            continue
        found.append(_target("retry_cause_resolved", project, video))
    return found


def find_republish(data: dict, now: datetime) -> list:
    """Ítem en publish_error cuyo token de Meta para esa página está válido y cuyo último error no fue de autenticación."""
    if "projects" in data.get("errors", {}):
        return []
    tokens = {office_state._norm(t.get("page_name")): t for t in data.get("tokens") or []}
    found = []
    for project, video in office_state._items(data.get("projects", [])):
        if video.get("status") != "publish_error" or video.get("last_publish_auth_error"):
            continue
        token = tokens.get(office_state._norm(office_state._page(project)))
        if token and token.get("ok") and not token.get("auth_error"):
            found.append(_target("republish", project, video))
    return found


def find_stuck(data: dict, now: datetime) -> list:
    """Ítem en generating/publishing más de STUCK_AFTER (el mismo criterio del hallazgo del Revisor)."""
    if "projects" in data.get("errors", {}):
        return []
    seen = data.get("seen") or {}
    found = []
    for project, video in office_state._items(data.get("projects", [])):
        if video.get("status") not in ("generating", "publishing"):
            continue
        since = seen.get(office_state.stuck_key(project["id"], video["index"], video["status"]))
        if since and now - since > office_state.STUCK_AFTER:
            found.append(_target("release_stuck", project, video))
    return found


def _exec_retry(target: Target):
    ok = batch_pipeline.retry_video(target.payload["project_id"], target.payload["index"])
    return ok, "Reintentado" if ok else "El ítem ya no estaba en error"


def _exec_republish(target: Target):
    ok = batch_pipeline.retry_publish_video(target.payload["project_id"], target.payload["index"])
    return ok, "Publicación reintentada" if ok else "El ítem ya no estaba sin publicar"


def _exec_release(target: Target):
    ok = batch_pipeline.release_stuck_item(target.payload["project_id"], target.payload["index"],
                                           target.payload["status"])
    return ok, "Ítem soltado" if ok else "El ítem ya no estaba trabado"


RULES = (
    Rule("retry_cause_resolved", "videos", "Reintentar errores ya resueltos",
         "Reintenta un ítem con error cuando Flow o el proveedor de texto, que lo causaron, ya funcionan.",
         "auto", 2, timedelta(hours=2), find_cause_resolved, _exec_retry),
    Rule("republish", "publica", "Reintentar publicaciones",
         "Reintenta publicar cuando el token de Meta de esa página está válido.",
         "auto", 2, timedelta(hours=1), find_republish, _exec_republish),
    Rule("release_stuck", "videos", "Soltar ítems trabados",
         "Suelta un ítem trabado para que el programador lo retome (puede duplicar si el hilo sigue vivo).",
         "propose", 3, timedelta(hours=1), find_stuck, _exec_release),
)
RULES_BY_ID = {rule.id: rule for rule in RULES}


def _scan(data: dict, now: datetime) -> list:
    """[(regla, objetivo)] de todas las reglas; una regla que falla se registra y se salta."""
    found = []
    for rule in RULES:
        try:
            found.extend((rule, target) for target in rule.find(data, now))
        except Exception:
            logger.exception("oficina: fallo la regla '%s'", rule.id)
    return found
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_office_agents.py -q`
Expected: todo PASS.

---

### Task 5: `office_agents` — `evaluate`, `apply` y el ciclo

**Files:**
- Modify: `office_agents.py`
- Test: `tests/test_office_agents.py`

**Interfaces:**
- Consumes: `Rule`, `Target`, `RULES`, `_scan`, `record_events`, `_log`, `_iso`, `_load_state`, `_save_state`, `_LOCK`.
- Produces:
  - `_mode(state, rule) -> str` (`state["modes"].get(rule.id, rule.default_mode)`).
  - `_run(rule, target) -> (bool, str)` (ejecuta; una excepción es `(False, redactado)`).
  - `_auto_used(state, now) -> int` (acciones `kind == "action"` y `mode == "auto"` en los últimos 60 min).
  - `expire(state, found, now) -> None` (propuestas caducadas/sin objetivo; intentos de más de 7 días).
  - `evaluate(state, found, now) -> list[tuple[str, Rule, Target]]` con verbo `"propose" | "execute" | "exhausted"`; con `paused` devuelve `[]`.
  - `apply(state, todo, now, save) -> None`: `execute` guarda el intento **antes** de ejecutar (`save(state)`), registra `action` con `mode: "auto"`; respeta el freno de `AUTO_ACTIONS_PER_HOUR`; `propose` agrega `{"id", "rule", "key", "label", "created_at", "expires_at"}`; `exhausted` registra una sola vez (`kind: "exhausted"`, marca `exhausted_logged`).
  - `run_cycle(readers, now=None) -> None`, `brake(now=None) -> dict`, `run_forever(readers_fn, stop, interval=CYCLE_SECONDS) -> None`.

- [ ] **Step 1: Write the failing tests** — agregar a `tests/test_office_agents.py`:

```python
# ---- evaluate / apply -------------------------------------------------------------------------

def flow_error_data():
    return data(projects=[proj([vid("error", "Flow falló", OLD)])])


def run_once(state, d, now=NOW, saves=None):
    found = office_agents._scan(d, now)
    office_agents.expire(state, found, now)
    todo = office_agents.evaluate(state, found, now)
    office_agents.apply(state, todo, now, (lambda s: saves.append(copy.deepcopy(s))) if saves is not None else (lambda s: None))
    return todo


@pytest.fixture
def batch_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(office_agents.batch_pipeline, "retry_video", lambda pid, i: calls.append(("retry", pid, i)) or True)
    monkeypatch.setattr(office_agents.batch_pipeline, "retry_publish_video", lambda pid, i: calls.append(("republish", pid, i)) or True)
    monkeypatch.setattr(office_agents.batch_pipeline, "release_stuck_item", lambda pid, i, s: calls.append(("release", pid, i, s)) or True)
    return calls


def test_auto_rule_executes_and_saves_the_attempt_before_running(batch_calls):
    state, saves = office_agents._default_state(), []
    run_once(state, flow_error_data(), saves=saves)
    assert batch_calls == [("retry", "p1", 0)]
    assert saves[0]["attempts"]["retry_cause_resolved:p1:0"]["count"] == 1 and saves[0]["log"] == []
    entry = state["log"][-1]
    assert (entry["kind"], entry["mode"], entry["ok"], entry["rule"]) == ("action", "auto", True, "retry_cause_resolved")
    assert entry["message"] == "Reintentado" and "ítem 1" in entry["label"]


def test_off_mode_does_nothing_and_propose_mode_creates_one_proposal(batch_calls):
    state = office_agents._default_state()
    state["modes"]["retry_cause_resolved"] = "off"
    assert run_once(state, flow_error_data()) == [] and batch_calls == []
    state["modes"]["retry_cause_resolved"] = "propose"
    run_once(state, flow_error_data())
    run_once(state, flow_error_data(), NOW + timedelta(minutes=1))
    (proposal,) = state["proposals"]
    assert proposal["rule"] == "retry_cause_resolved" and proposal["key"] == "retry_cause_resolved:p1:0"
    assert proposal["expires_at"] == "2026-10-16T15:00:00" and len(proposal["id"]) == 8
    assert batch_calls == []


def test_release_stuck_defaults_to_a_proposal():
    state = office_agents._default_state()
    p = proj([vid("generating")])
    seen = {office_state.stuck_key("p1", 0, "generating"): NOW - timedelta(minutes=50)}
    run_once(state, data(projects=[p], seen=seen))
    assert [x["rule"] for x in state["proposals"]] == ["release_stuck"]


def test_pause_stops_execution_proposals_and_exhaustion_notes(batch_calls):
    state = office_agents._default_state()
    state["paused"] = True
    state["modes"]["release_stuck"] = "propose"
    assert run_once(state, flow_error_data()) == [] and batch_calls == [] and state["proposals"] == []


def test_cooldown_and_attempt_cap(batch_calls):
    state = office_agents._default_state()
    run_once(state, flow_error_data(), NOW)
    run_once(state, flow_error_data(), NOW + timedelta(minutes=30))      # dentro del cooldown de 2 h
    assert len(batch_calls) == 1
    run_once(state, flow_error_data(), NOW + timedelta(hours=3))          # segundo y ultimo intento
    assert len(batch_calls) == 2
    run_once(state, flow_error_data(), NOW + timedelta(hours=6))          # agotado
    run_once(state, flow_error_data(), NOW + timedelta(hours=7))
    assert len(batch_calls) == 2
    exhausted = [e for e in state["log"] if e["kind"] == "exhausted"]
    assert len(exhausted) == 1 and exhausted[0]["key"] == "retry_cause_resolved:p1:0"


def test_hourly_brake_blocks_further_automatic_actions(batch_calls):
    state = office_agents._default_state()
    for i in range(office_agents.AUTO_ACTIONS_PER_HOUR):
        office_agents._log(state, NOW - timedelta(minutes=5), kind="action", mode="auto", ok=True, key=f"k{i}",
                           agent="videos", label="x", message="x", rule="retry_cause_resolved")
    assert office_agents._auto_used(state, NOW) == office_agents.AUTO_ACTIONS_PER_HOUR
    run_once(state, flow_error_data())
    assert batch_calls == []
    assert office_agents._auto_used(state, NOW + timedelta(hours=2)) == 0


def test_an_executor_that_raises_is_logged_as_failed_and_counts_as_an_attempt(monkeypatch):
    def boom(pid, i):
        raise RuntimeError("access_token=SECRET fallo")
    monkeypatch.setattr(office_agents.batch_pipeline, "retry_video", boom)
    state = office_agents._default_state()
    run_once(state, flow_error_data())
    entry = state["log"][-1]
    assert entry["ok"] is False and "SECRET" not in entry["message"] and "RuntimeError" in entry["message"]
    assert state["attempts"]["retry_cause_resolved:p1:0"]["count"] == 1


def test_expire_drops_old_proposals_vanished_targets_and_stale_attempts():
    state = office_agents._default_state()
    state["proposals"] = [
        {"id": "a", "rule": "retry_cause_resolved", "key": "retry_cause_resolved:p1:0", "label": "x",
         "created_at": "2026-10-15T14:00:00", "expires_at": "2026-10-16T14:00:00"},
        {"id": "b", "rule": "retry_cause_resolved", "key": "retry_cause_resolved:p1:9", "label": "x",
         "created_at": "2026-10-15T14:00:00", "expires_at": "2026-10-16T14:00:00"},
        {"id": "c", "rule": "retry_cause_resolved", "key": "retry_cause_resolved:p1:0", "label": "x",
         "created_at": "2026-10-14T00:00:00", "expires_at": "2026-10-15T00:00:00"},
    ]
    state["attempts"] = {"old": {"count": 1, "last_at": "2026-10-01T00:00:00"},
                         "recent": {"count": 1, "last_at": "2026-10-14T00:00:00"}}
    office_agents.expire(state, office_agents._scan(flow_error_data(), NOW), NOW)
    assert [p["id"] for p in state["proposals"]] == ["a"]
    assert list(state["attempts"]) == ["recent"]


# ---- ciclo ------------------------------------------------------------------------------------

def test_run_cycle_records_events_runs_rules_and_persists(batch_calls, memory_store):
    readers = {"projects": lambda: [proj([vid("error", "Flow falló", OLD)])],
               "flow": lambda: {"state": "ok", "message": ""}, "text_backends": lambda: ["freellm:x"]}
    office_agents.run_cycle(readers, NOW)
    assert batch_calls == [("retry", "p1", 0)]
    saved = memory_store["office_agents"]
    kinds = {e["kind"] for e in saved["log"]}
    assert {"action", "status", "alert_open"} <= kinds
    assert saved["attempts"]["retry_cause_resolved:p1:0"]["count"] == 1


def test_run_cycle_does_not_act_when_the_batches_could_not_be_read(batch_calls, monkeypatch):
    def broken():
        raise RuntimeError("sin disco")
    office_agents.run_cycle({"projects": broken}, NOW)
    assert batch_calls == []
    assert any(e["kind"] == "alert_open" and e["key"] == "read:projects" for e in office_agents._load_state()["log"])


def test_run_cycle_feeds_the_brake_finding_to_the_reviewer(batch_calls):
    state = office_agents._default_state()
    for i in range(office_agents.AUTO_ACTIONS_PER_HOUR):
        office_agents._log(state, NOW - timedelta(minutes=5), kind="action", mode="auto", ok=True, key=f"k{i}",
                           agent="videos", label="x", message="x", rule="retry_cause_resolved")
    office_agents._save_state(state)
    office_agents.run_cycle({"projects": lambda: []}, NOW)
    assert any(e["kind"] == "alert_open" and e["key"] == "automation-brake" for e in office_agents._load_state()["log"])
    assert office_agents.brake(NOW) == {"used": office_agents.AUTO_ACTIONS_PER_HOUR, "limit": office_agents.AUTO_ACTIONS_PER_HOUR}


def test_run_forever_survives_a_failing_cycle_and_stops_on_the_event(monkeypatch):
    stop, calls = threading.Event(), []

    def cycle(readers, now=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        stop.set()
    monkeypatch.setattr(office_agents, "run_cycle", cycle)
    office_agents.run_forever(lambda: {}, stop, interval=0)
    assert len(calls) == 2
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_office_agents.py -q -k "auto_rule or off_mode or defaults_to or pause or cooldown or brake or raises or expire or run_cycle or run_forever"`
Expected: FAIL (`AttributeError: ... 'expire'` etc.).

- [ ] **Step 3: Implement** — agregar a `office_agents.py`:

```python
# ---- motor ------------------------------------------------------------------------------------

def _mode(state: dict, rule: Rule) -> str:
    return state["modes"].get(rule.id, rule.default_mode)


def _run(rule: Rule, target: Target):
    """Ejecuta el objetivo; una excepcion cuenta como fallo (con el texto sin secretos), no corta nada."""
    try:
        return rule.execute(target)
    except Exception as exc:
        logger.exception("oficina: fallo el ejecutor de '%s'", rule.id)
        return False, office_state._redact(f"{type(exc).__name__}: {exc}")


def _auto_used(state: dict, now: datetime) -> int:
    since = now - timedelta(hours=1)
    return sum(1 for e in state["log"]
               if e.get("kind") == "action" and e.get("mode") == "auto"
               and (office_state._parse(e.get("ts")) or since) > since)


def expire(state: dict, found: list, now: datetime) -> None:
    """Caduca propuestas (24 h, o cuyo objetivo ya no aparece) y poda intentos de mas de 7 dias."""
    live = {target.key for _, target in found}
    state["proposals"] = [p for p in state["proposals"]
                          if p["key"] in live and (office_state._parse(p.get("expires_at")) or now) > now]
    cutoff = now - ATTEMPTS_TTL
    state["attempts"] = {key: a for key, a in state["attempts"].items()
                         if (office_state._parse(a.get("last_at")) or office_state._parse(a.get("silenced_until")) or now) > cutoff}


def evaluate(state: dict, found: list, now: datetime) -> list:
    """Decide que hacer con cada objetivo hallado; no tiene efectos. Verbos: propose | execute | exhausted."""
    if state["paused"]:
        return []
    todo = []
    for rule, target in found:
        mode = _mode(state, rule)
        if mode == "off":
            continue
        attempt = state["attempts"].get(target.key, {})
        silenced = office_state._parse(attempt.get("silenced_until"))
        if silenced and now < silenced:
            continue
        if attempt.get("count", 0) >= rule.max_attempts:
            if not attempt.get("exhausted_logged"):
                todo.append(("exhausted", rule, target))
            continue
        last = office_state._parse(attempt.get("last_at"))
        if last and now - last < rule.cooldown:
            continue
        if mode == "propose":
            if not any(p["key"] == target.key for p in state["proposals"]):
                todo.append(("propose", rule, target))
        else:
            todo.append(("execute", rule, target))
    return todo


def apply(state: dict, todo: list, now: datetime, save: Callable) -> None:
    """Ejecuta lo decidido. El intento se guarda ANTES de ejecutar: un reinicio a mitad no repite la accion."""
    for verb, rule, target in todo:
        if verb == "propose":
            state["proposals"].append({"id": uuid.uuid4().hex[:8], "rule": rule.id, "key": target.key,
                                       "label": target.label, "created_at": _iso(now),
                                       "expires_at": _iso(now + PROPOSAL_TTL)})
        elif verb == "exhausted":
            state["attempts"].setdefault(target.key, {"count": rule.max_attempts})["exhausted_logged"] = True
            _log(state, now, kind="exhausted", agent=rule.agent, key=target.key, label=target.label,
                 message="Intentos agotados")
        elif verb == "execute":
            if _auto_used(state, now) >= AUTO_ACTIONS_PER_HOUR:
                continue
            attempt = state["attempts"].setdefault(target.key, {"count": 0})
            attempt["count"] += 1
            attempt["last_at"] = _iso(now)
            save(state)
            ok, message = _run(rule, target)
            _log(state, now, kind="action", rule=rule.id, agent=rule.agent, key=target.key,
                 label=target.label, mode="auto", ok=ok, message=message)


def brake(now: Optional[datetime] = None) -> dict:
    with _LOCK:
        return {"used": _auto_used(_load_state(), now or datetime.now()), "limit": AUTO_ACTIONS_PER_HOUR}


def run_cycle(readers: dict, now: Optional[datetime] = None) -> None:
    """Un ciclo completo: leer, anotar eventos, evaluar reglas, ejecutar/proponer, guardar.
    Si los lotes no se pudieron leer solo anota eventos: nunca decide con datos incompletos."""
    now = now or datetime.now()
    data = office_state.collect_data(readers, now, extra={"brake": brake(now)})
    snapshot = office_state.build_state(data, now)
    with _LOCK:
        state = _load_state()
        record_events(state, snapshot, now)
        if "projects" not in data["errors"]:
            found = _scan(data, now)
            expire(state, found, now)
            apply(state, evaluate(state, found, now), now, _save_state)
        _save_state(state)


def run_forever(readers_fn: Callable, stop: threading.Event, interval: float = CYCLE_SECONDS) -> None:
    """Cuerpo del hilo daemon: un ciclo, esperar, repetir; un ciclo roto se registra y sigue."""
    while True:
        try:
            run_cycle(readers_fn())
        except Exception:
            logger.exception("oficina: fallo un ciclo de agentes")
        if stop.wait(interval):
            return
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_office_agents.py -q`
Expected: todo PASS.

---

### Task 6: `office_agents` — acciones del usuario y bloque `automation`

**Files:**
- Modify: `office_agents.py`
- Test: `tests/test_office_agents.py`

**Interfaces:**
- Produces (todas devuelven `{"ok": bool, "error"?: str}`):
  - `set_paused(paused: bool) -> dict`
  - `set_mode(rule_id: str, mode: str) -> dict` (valida ambos contra `RULES_BY_ID` / `MODES`)
  - `reject(proposal_id: str, now=None) -> dict` (quita la propuesta y silencia ese objetivo 24 h en `attempts[key]["silenced_until"]`)
  - `approve(proposal_id: str, readers: dict, now=None) -> dict` (relee datos, revalida que el objetivo siga hallándose, ignora el cooldown pero cuenta el intento, registra `mode: "approved"`; funciona con la pausa activa; con `projects` ilegible no ejecuta)
  - `automation_block(now=None) -> dict`: `{"paused", "rules": [{"id","agent","label","description","mode","default_mode"}], "proposals": [...], "log": [las últimas 20, más nueva primero], "brake": {"used","limit"}}`

- [ ] **Step 1: Write the failing tests** — agregar a `tests/test_office_agents.py`:

```python
# ---- acciones del usuario ----------------------------------------------------------------------

def stuck_data():
    p = proj([vid("generating")])
    return data(projects=[p], seen={office_state.stuck_key("p1", 0, "generating"): NOW - timedelta(minutes=50)})


def stuck_readers():
    return {"projects": lambda: [proj([vid("generating")])]}


def make_proposal():
    state = office_agents._default_state()
    run_once(state, stuck_data())
    office_agents._save_state(state)
    return state["proposals"][0]["id"]


def test_pause_and_mode_are_validated_and_persisted():
    assert office_agents.set_paused(True) == {"ok": True} and office_agents._load_state()["paused"] is True
    assert office_agents.set_mode("republish", "off") == {"ok": True}
    assert office_agents._load_state()["modes"] == {"republish": "off"}
    assert office_agents.set_mode("nope", "off")["ok"] is False
    assert office_agents.set_mode("republish", "turbo")["ok"] is False
    assert office_agents._load_state()["modes"] == {"republish": "off"}


def test_reject_removes_the_proposal_and_silences_it_for_24_hours():
    pid = make_proposal()
    assert office_agents.reject(pid, NOW) == {"ok": True}
    state = office_agents._load_state()
    assert state["proposals"] == []
    run_once(state, stuck_data(), NOW + timedelta(hours=2))
    assert state["proposals"] == []
    run_once(state, stuck_data(), NOW + timedelta(hours=25))
    assert len(state["proposals"]) == 1
    assert office_agents.reject("zzzz", NOW)["ok"] is False


def test_approve_revalidates_executes_logs_as_approved_and_ignores_the_cooldown(batch_calls):
    pid = make_proposal()
    state = office_agents._load_state()
    state["attempts"]["release_stuck:p1:0"] = {"count": 1, "last_at": office_agents._iso(NOW)}  # cooldown vigente
    state["paused"] = True                                                                       # y pausa general
    office_agents._save_state(state)
    seen_data = office_state.collect_data(stuck_readers(), NOW)  # prepara 'seen'
    assert not seen_data["errors"]
    result = office_agents.approve(pid, stuck_readers(), NOW + timedelta(minutes=50))
    assert result["ok"] is True
    assert batch_calls == [("release", "p1", 0, "generating")]
    state = office_agents._load_state()
    assert state["proposals"] == [] and state["attempts"]["release_stuck:p1:0"]["count"] == 2
    entry = state["log"][-1]
    assert (entry["kind"], entry["mode"], entry["ok"]) == ("action", "approved", True)


def test_approve_refuses_when_the_target_is_gone_or_the_data_is_incomplete(batch_calls):
    pid = make_proposal()
    gone = {"projects": lambda: [proj([vid("ready")])]}
    result = office_agents.approve(pid, gone, NOW)
    assert result["ok"] is False and "ya no hace falta" in result["error"].lower() and batch_calls == []
    assert office_agents._load_state()["proposals"] == []
    pid = make_proposal()

    def broken():
        raise RuntimeError("sin disco")
    result = office_agents.approve(pid, {"projects": broken}, NOW)
    assert result["ok"] is False and batch_calls == []
    assert len(office_agents._load_state()["proposals"]) == 1  # sigue pendiente
    assert office_agents.approve("zzzz", gone, NOW)["ok"] is False


def test_automation_block_lists_rules_proposals_newest_log_first_and_the_brake():
    state = office_agents._default_state()
    state["modes"]["republish"] = "off"
    for i in range(25):
        office_agents._log(state, NOW, kind="status", agent="videos", key="videos", label=str(i), message="")
    office_agents._save_state(state)
    block = office_agents.automation_block(NOW)
    assert block["paused"] is False and block["proposals"] == []
    assert [r["id"] for r in block["rules"]] == ["retry_cause_resolved", "republish", "release_stuck"]
    republish = next(r for r in block["rules"] if r["id"] == "republish")
    assert republish["mode"] == "off" and republish["default_mode"] == "auto" and republish["agent"] == "publica"
    assert len(block["log"]) == office_agents.LOG_SHOWN and block["log"][0]["label"] == "24"
    assert block["brake"] == {"used": 0, "limit": office_agents.AUTO_ACTIONS_PER_HOUR}
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_office_agents.py -q -k "pause_and_mode or reject or approve or automation_block"`
Expected: FAIL (`AttributeError: ... 'set_paused'`).

- [ ] **Step 3: Implement** — agregar a `office_agents.py`:

```python
# ---- acciones del usuario ----------------------------------------------------------------------

def set_paused(paused: bool) -> dict:
    with _LOCK:
        state = _load_state()
        state["paused"] = bool(paused)
        _save_state(state)
    return {"ok": True}


def set_mode(rule_id: str, mode: str) -> dict:
    if rule_id not in RULES_BY_ID or mode not in MODES:
        return {"ok": False, "error": "Regla o modo inválido."}
    with _LOCK:
        state = _load_state()
        state["modes"][rule_id] = mode
        _save_state(state)
    return {"ok": True}


def reject(proposal_id: str, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    with _LOCK:
        state = _load_state()
        proposal = next((p for p in state["proposals"] if p["id"] == proposal_id), None)
        if proposal is None:
            return {"ok": False, "error": "La propuesta ya no existe."}
        state["proposals"].remove(proposal)
        state["attempts"].setdefault(proposal["key"], {"count": 0})["silenced_until"] = _iso(now + REJECT_SILENCE)
        _save_state(state)
    return {"ok": True}


def approve(proposal_id: str, readers: dict, now: Optional[datetime] = None) -> dict:
    """Ejecuta una propuesta a mano: relee los datos y solo actua si el objetivo sigue hallandose.
    Ignora el cooldown y la pausa (es una orden del usuario) pero cuenta el intento."""
    now = now or datetime.now()
    with _LOCK:
        proposal = next((p for p in _load_state()["proposals"] if p["id"] == proposal_id), None)
    if proposal is None:
        return {"ok": False, "error": "La propuesta ya no existe."}
    rule = RULES_BY_ID.get(proposal["rule"])
    if rule is None:
        return {"ok": False, "error": "La regla de esa propuesta ya no existe."}
    data = office_state.collect_data(readers, now)
    if "projects" in data["errors"]:
        return {"ok": False, "error": "No pude leer los lotes: no se ejecuta con datos incompletos."}
    with _LOCK:
        state = _load_state()
        state["proposals"] = [p for p in state["proposals"] if p["id"] != proposal_id]
        target = next((t for r, t in _scan(data, now) if r.id == rule.id and t.key == proposal["key"]), None)
        if target is None:
            _save_state(state)
            return {"ok": False, "error": "Ya no hace falta: el ítem cambió desde que se propuso."}
        attempt = state["attempts"].setdefault(target.key, {"count": 0})
        attempt["count"] += 1
        attempt["last_at"] = _iso(now)
        _save_state(state)
        ok, message = _run(rule, target)
        _log(state, now, kind="action", rule=rule.id, agent=rule.agent, key=target.key,
             label=target.label, mode="approved", ok=ok, message=message)
        _save_state(state)
    return {"ok": True, "message": message} if ok else {"ok": False, "error": message}


def automation_block(now: Optional[datetime] = None) -> dict:
    """Lo que la oficina muestra de la automatizacion; se lee del archivo (sin Graph ni lotes)."""
    now = now or datetime.now()
    with _LOCK:
        state = _load_state()
    return {
        "paused": state["paused"],
        "rules": [{"id": r.id, "agent": r.agent, "label": r.label, "description": r.description,
                   "mode": _mode(state, r), "default_mode": r.default_mode} for r in RULES],
        "proposals": state["proposals"],
        "log": list(reversed(state["log"][-LOG_SHOWN:])),
        "brake": {"used": _auto_used(state, now), "limit": AUTO_ACTIONS_PER_HOUR},
    }
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_office_agents.py tests/test_release_stuck.py -q`
Expected: todo PASS.

---

### Task 7: `app.py` — endpoints, bloque `automation` y arranque del hilo

**Files:**
- Modify: `app.py` (import ~l.55; `api_office_state` ~l.6388; `__main__` ~l.6542)
- Test: `tests/test_office_state.py`

**Interfaces:**
- Consumes: `office_agents.automation_block/brake/set_paused/set_mode/approve/reject/run_forever`, `office_state.collect_state(readers, now, extra)`, `batch_pipeline.holds_scheduler_lock()`.
- Produces: `GET /api/office/state` suma `automation`. `POST /api/office/automation/pause` (`{"paused": bool}`), `/mode` (`{"rule","mode"}`), `/proposals/<id>/approve`, `/proposals/<id>/reject`. Éxito 200; `ok: false` de validación 400; excepción 200 con `ok: false`.

- [ ] **Step 1: Write the failing tests** — en `tests/test_office_state.py`: agregar imports `import pytest`, `import office_agents` junto a `import office_state as os_`, y este fixture justo después de `NOW = ...`:

```python
@pytest.fixture(autouse=True)
def memory_agents_store(monkeypatch):
    """Los endpoints de la oficina leen el estado de los agentes: se aisla del archivo real."""
    saved = {}
    monkeypatch.setattr(office_agents.job_store, "load", lambda name: copy.deepcopy(saved.get(name, {})))
    monkeypatch.setattr(office_agents.job_store, "save", lambda name, d: saved.__setitem__(name, copy.deepcopy(d)))
    return saved
```
(agregar `import copy` arriba). Y al final del bloque de endpoints:

```python
def _client(monkeypatch, readers=None):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    monkeypatch.setattr(app_module, "_office_readers", lambda: readers or {"projects": lambda: []})
    os_.reset()
    return app_module.app.test_client()


def test_office_state_includes_the_automation_block(monkeypatch):
    body = _client(monkeypatch).get("/api/office/state", headers=_auth()).get_json()
    auto = body["automation"]
    assert auto["paused"] is False and auto["proposals"] == [] and auto["log"] == []
    assert [r["id"] for r in auto["rules"]] == ["retry_cause_resolved", "republish", "release_stuck"]
    assert auto["brake"]["limit"] == 20


def test_automation_endpoints_require_auth_and_validate_input(monkeypatch):
    client = _client(monkeypatch)
    assert client.post("/api/office/automation/pause", json={"paused": True}).status_code == 401
    assert client.post("/api/office/automation/pause", json={"paused": "x"}, headers=_auth()).status_code == 400
    ok = client.post("/api/office/automation/pause", json={"paused": True}, headers=_auth())
    assert ok.status_code == 200 and ok.get_json() == {"ok": True}
    assert client.get("/api/office/state", headers=_auth()).get_json()["automation"]["paused"] is True
    assert client.post("/api/office/automation/mode", json={"rule": "republish", "mode": "off"}, headers=_auth()).get_json() == {"ok": True}
    bad = client.post("/api/office/automation/mode", json={"rule": "nope", "mode": "off"}, headers=_auth())
    assert bad.status_code == 400 and bad.get_json()["ok"] is False
    for action in ("approve", "reject"):
        missing = client.post(f"/api/office/automation/proposals/zzzz/{action}", headers=_auth())
        assert missing.status_code == 400 and missing.get_json()["ok"] is False


def test_automation_endpoint_errors_never_500_and_do_not_leak_tokens(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(office_agents, "set_paused",
                        lambda paused: (_ for _ in ()).throw(RuntimeError("access_token=SECRET")))
    response = client.post("/api/office/automation/pause", json={"paused": True}, headers=_auth())
    assert response.status_code == 200 and response.get_json()["ok"] is False
    assert "SECRET" not in response.get_data(as_text=True)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_office_state.py -q -k "automation"`
Expected: FAIL (`KeyError: 'automation'` / 404).

- [ ] **Step 3: Implement** — en `app.py`:

1. Debajo de `import office_state` (l.55) agregar `import office_agents`.
2. Reemplazar `api_office_state` y agregar los endpoints justo debajo:

```python
@app.route("/api/office/state")
def api_office_state():
    try:
        now = datetime.now()
        state = office_state.collect_state(_office_readers(), now, extra={"brake": office_agents.brake(now)})
        state["automation"] = office_agents.automation_block(now)
        return jsonify(state)
    except Exception as exc:
        logger.exception("oficina: no se pudo armar el estado")
        return jsonify({"ok": False, "error": office_state._redact(f"{type(exc).__name__}: {exc}")})


def _office_automation(fn, *args):
    """Acciones de los agentes: nunca 500; el error de validacion es 400 y el texto sale sin secretos."""
    try:
        result = fn(*args)
    except Exception as exc:
        logger.exception("oficina: fallo una accion de los agentes")
        return jsonify({"ok": False, "error": office_state._redact(f"{type(exc).__name__}: {exc}")})
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/office/automation/pause", methods=["POST"])
def api_office_automation_pause():
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("paused"), bool):
        return jsonify({"ok": False, "error": "Falta 'paused' (true o false)."}), 400
    return _office_automation(office_agents.set_paused, body["paused"])


@app.route("/api/office/automation/mode", methods=["POST"])
def api_office_automation_mode():
    body = request.get_json(silent=True) or {}
    return _office_automation(office_agents.set_mode, str(body.get("rule", "")), str(body.get("mode", "")))


@app.route("/api/office/automation/proposals/<proposal_id>/approve", methods=["POST"])
def api_office_automation_approve(proposal_id):
    return _office_automation(lambda: office_agents.approve(proposal_id, _office_readers()))


@app.route("/api/office/automation/proposals/<proposal_id>/reject", methods=["POST"])
def api_office_automation_reject(proposal_id):
    return _office_automation(office_agents.reject, proposal_id)
```

3. En el `__main__`, justo después de `batch_pipeline.start_scheduler()`:

```python
    # Los agentes actuan solos solo en el proceso dueño del programador (un unico proceso decide).
    if batch_pipeline.holds_scheduler_lock():
        threading.Thread(target=office_agents.run_forever, args=(_office_readers, threading.Event()),
                         daemon=True, name="office-agents").start()
```

4. Verificar que `app.py` mantiene CRLF: `python -c "d=open('app.py','rb').read(); print(d.count(b'\r\n'), d.count(b'\n'))"` → dos números iguales.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_office_state.py -q`
Expected: todo PASS (los tests previos de endpoints siguen verdes con el fixture de aislamiento).

---

### Task 8: UI — tarjetas "Agentes automáticos" y "Bitácora"

**Files:**
- Modify: `app.py` (CSS dentro de `/* OFFICE:START */…END` ~l.584-674; HTML ~l.998; JS ~l.1712-1991)
- Test: `tests/test_office_state.py`

**Interfaces:**
- Consumes: `officeState.automation` (forma de `automation_block`), `OFFICE_AGENTS`, `officeEl`, `officeSelected`, `officePoll`, `$`.
- Produces: elementos `#office-automation`, `#office-log`; funciones `officePost(url, body)`, `officeRenderAutomation(force)`, `officeRenderLog(force)`, `officeLogText(e)`.

- [ ] **Step 1: Write the failing test** — en `tests/test_office_state.py`, en `test_home_page_renders_with_the_office_scene_as_default_tab` agregar:

```python
    assert 'id="office-automation"' in html and 'id="office-log"' in html
```
y en `test_office_script_is_wired_and_free_of_unsafe_sinks` cambiar la tupla de nombres a:

```python
    for name in ("officeStart", "officeStop", "officePoll", "officeRender", "officeRenderPanel", "officeRunAction",
                 "officePost", "officeRenderAutomation", "officeRenderLog", "officeLogText"):
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_office_state.py -q -k "home_page or office_script"`
Expected: FAIL.

- [ ] **Step 3: Implement**

CSS — antes de `/* OFFICE:END */` (el del `<style>`, ~l.674) agregar:

```css
  .office-rule { display: flex; gap: 12px; align-items: center; padding: 10px 0; border-top: 1px solid var(--border); }
  .office-rule-text { flex: 1; font-size: 14px; }
  .office-rule-text small { display: block; opacity: .7; }
  .office-prop { display: flex; gap: 8px; align-items: center; padding: 8px 0; border-top: 1px solid var(--border); font-size: 14px; }
  .office-prop-text { flex: 1; }
  .office-log-row { display: flex; gap: 10px; padding: 4px 0; font-size: 13px; }
  .office-log-time { flex-shrink: 0; opacity: .6; font-variant-numeric: tabular-nums; }
  .office-log-text { flex: 1; }
  .office-log-row.failed .office-log-text { color: #f87171; }
  #office-automation[hidden], #office-log[hidden] { display: none; }
  @media (max-width: 520px) { .office-rule { flex-wrap: wrap; } .office-rule-text { flex-basis: 100%; } }
```

HTML — debajo de `<div class="card" id="office-panel"></div>` (~l.998):

```html
    <div class="card" id="office-automation" hidden></div>
    <div class="card" id="office-log" hidden></div>
```

JS — dentro del bloque `OFFICE` de scripts, antes de `function officeSelect(id)`:

```js
const OFFICE_MODE_LABEL = { off: "No", propose: "Propone", auto: "Automático" };
let officeAutoSig = "";
let officeLogSig = "";

async function officePost(url, body) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json();
    if (!data.ok) alert(data.error || "No se pudo completar la acción.");
  } catch (e) {
    alert("No se pudo completar la acción.");
  }
  officePoll();
}

function officeLogText(e) {
  const who = OFFICE_AGENTS[e.agent] ? OFFICE_AGENTS[e.agent].title : e.agent;
  if (e.kind === "action") {
    return who + " · " + e.label + " · " + (e.mode === "approved" ? "aprobado" : "automático") + " · " +
      (e.ok ? "" : "falló: ") + e.message;
  }
  if (e.kind === "exhausted") return who + " · " + e.label + " · intentos agotados";
  if (e.kind === "alert_open") return who + " · Alerta: " + e.label;
  if (e.kind === "alert_resolved") return who + " · Resuelta: " + e.label;
  return who + " · " + e.label + " (" + e.message + ")";
}

function officeRenderLog(force) {
  const auto = officeState && officeState.automation;
  const box = $("office-log");
  const rows = (auto && auto.log) || [];
  box.hidden = !rows.length;
  const sig = JSON.stringify(rows);
  if (!force && sig === officeLogSig) return;
  officeLogSig = sig;
  box.textContent = "";
  if (!rows.length) return;
  box.appendChild(officeEl("h2", "", "Bitácora"));
  rows.forEach(e => {
    const ts = String(e.ts || "");
    const row = officeEl("div", "office-log-row" + (e.kind === "action" && !e.ok ? " failed" : ""));
    row.appendChild(officeEl("span", "office-log-time", ts.slice(8, 10) + "/" + ts.slice(5, 7) + " " + ts.slice(11, 16)));
    row.appendChild(officeEl("span", "office-log-text", officeLogText(e)));
    box.appendChild(row);
  });
}

function officeRenderAutomation(force) {
  const auto = officeState && officeState.automation;
  const box = $("office-automation");
  box.hidden = !auto;
  const sig = JSON.stringify([auto, officeSelected]);
  if (!force && sig === officeAutoSig) return;
  officeAutoSig = sig;
  box.textContent = "";
  if (!auto) return;
  box.appendChild(officeEl("h2", "", "Agentes automáticos"));
  const braked = auto.brake.used >= auto.brake.limit;
  const line = auto.paused ? "Agentes en pausa" : braked ? "Límite por hora alcanzado" : "Agentes activos";
  box.appendChild(officeEl("p", "card-desc", line + " · " + auto.brake.used + "/" + auto.brake.limit + " acciones esta hora"));
  const pause = officeEl("button", "btn-sm", auto.paused ? "Reanudar agentes" : "Pausar agentes");
  pause.type = "button";
  pause.addEventListener("click", () => officePost("/api/office/automation/pause", { paused: !auto.paused }));
  box.appendChild(pause);
  auto.proposals.forEach(p => {
    const rule = auto.rules.find(r => r.id === p.rule);
    const row = officeEl("div", "office-prop");
    row.appendChild(officeEl("span", "office-prop-text", (rule ? rule.label : p.rule) + " · " + p.label));
    const yes = officeEl("button", "btn-sm", "Aprobar");
    yes.type = "button";
    yes.addEventListener("click", () => {
      if (window.confirm("Aprobar: " + p.label + "\n\n¿Continuar?")) officePost("/api/office/automation/proposals/" + encodeURIComponent(p.id) + "/approve");
    });
    const no = officeEl("button", "btn-sm", "Rechazar");
    no.type = "button";
    no.addEventListener("click", () => officePost("/api/office/automation/proposals/" + encodeURIComponent(p.id) + "/reject"));
    row.appendChild(yes);
    row.appendChild(no);
    box.appendChild(row);
  });
  auto.rules.filter(r => officeSelected === "revisor" || r.agent === officeSelected).forEach(r => {
    const row = officeEl("div", "office-rule");
    const text = officeEl("span", "office-rule-text", r.label);
    text.appendChild(officeEl("small", "", r.description));
    row.appendChild(text);
    const select = document.createElement("select");
    ["off", "propose", "auto"].forEach(mode => {
      const option = document.createElement("option");
      option.value = mode;
      option.textContent = OFFICE_MODE_LABEL[mode];
      option.selected = mode === r.mode;
      select.appendChild(option);
    });
    select.setAttribute("aria-label", r.label);
    select.addEventListener("change", () => officePost("/api/office/automation/mode", { rule: r.id, mode: select.value }));
    row.appendChild(select);
    box.appendChild(row);
  });
}
```

En `officeRender(force)` agregar, justo después de `officeRenderPanel(force);`:

```js
  officeRenderAutomation(force);
  officeRenderLog(force);
```

En `officeSelect(id)` agregar, después de `officeRenderPanel(true);`:

```js
  officeRenderAutomation(true);
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_office_state.py -q`
Expected: todo PASS (incluido el test que prohíbe `{{`, `{%`, `{#`, `"""` e `innerHTML` en los bloques `OFFICE`). Verificar CRLF de `app.py` como en el Task 7.

---

### Task 9: Verificación de punta a punta

**Files:** ninguno nuevo.

- [ ] **Step 1: Suite completa**

Run: `python -m pytest -q`
Expected: todo PASS. Si algo falla fuera de los archivos de este plan, comparar con `git stash`-free: correr el mismo test en `HEAD` no es posible (el árbol ya tenía cambios); reportar el fallo al usuario con la salida exacta en vez de arreglarlo a ciegas.

- [ ] **Step 2: Arranque real y verificación en el navegador integrado**

1. `preview_start` con `{name: "flask-app"}` (`.claude/launch.json`, puerto 5000).
2. `preview_logs` no debe mostrar excepciones; debe aparecer el hilo `office-agents` (si el lock del programador lo tiene este proceso).
3. Si el navegador pide usuario y clave (Basic Auth), **no ingresarlos**: pedirle al usuario que abra la página una vez y seguir.
4. Con la sesión autenticada, `javascript_tool`: `await (await fetch('/api/office/state')).json()` → debe traer `automation` con 3 reglas.
5. `read_page` en la pestaña Oficina: tarjetas "Agentes automáticos" y (tras el primer ciclo, ≤60 s) "Bitácora" con filas `Alerta`/`(— → idle)`. Cambiar un selector de modo, recargar y comprobar que persiste (`output/job_state/office_agents.json`).
6. `computer {action:"screenshot"}` de la oficina como prueba, y `read_console_messages` sin errores.

- [ ] **Step 3: Cierre**

No hacer commit de `app.py`, `batch_pipeline.py` ni `tests/test_office_state.py` enteros (traen cambios ajenos). Proponer al usuario: `git add office_agents.py tests/test_office_agents.py tests/test_release_stuck.py office_state.py docs/superpowers/plans/2026-10-15-motor-acciones-y-bitacora.md` y `git add -p app.py batch_pipeline.py tests/test_office_state.py` eligiendo solo los hunks de este plan.

---

## Riesgos

- **Lecturas 24/7:** con el panel cerrado el ciclo de 60 s sigue leyendo `remote_times` (TTL 60 s → una consulta a Facebook por página por minuto). Es el costo de que los agentes trabajen solos; si molesta, subir `READ_TTL["remote_times"]` o `CYCLE_SECONDS`.
- **Solape con `_requeue_healable_errors`:** esa curación ya reintenta errores de Flow con cooldown exponencial hasta `MAX_HEAL_CYCLES = 4`. `retry_cause_resolved` solo actúa sobre ítems que quedaron en `error` y cumple 2 h; si ambos coincidieran, `retry_video` es idempotente (exige `status == "error"`).
- **`release_stuck` puede pisar un hilo vivo** → por eso arranca en `propose`.
- **`seen` (StuckTracker) vive en memoria:** tras reiniciar, la cuenta de "trabado" vuelve a cero; es el comportamiento actual del Revisor.
- **Archivo de estado borrado:** vuelve a los valores por defecto (Reparar actúa de nuevo; pausa y modos se pierden).

## Self-Review

- **Cobertura del spec:** modelo/`Rule`/`Target` (T4), estado persistido (T3), `evaluate`/`apply`/`run_cycle` con revalidación, cooldown, tope, pausa, freno (T5), aprobación/rechazo/pausa/modo (T6), API (T7), UI de pausa, selector por regla, propuestas y bitácora (T8), `collect_data` (T1), `release_stuck_item` (T2), hilo solo con el lock del programador (T7), errores y redacción (T5, T7). Eventos pasivos del #1: T3. El #3 (acciones sobre hallazgos) queda cubierto por `retry_cause_resolved`, `republish` y `release_stuck`.
- **Placeholders:** ninguno.
- **Consistencia de nombres:** `Target/Rule/RULES/RULES_BY_ID/_scan/record_events/expire/evaluate/apply/run_cycle/brake/run_forever/set_paused/set_mode/reject/approve/automation_block` y `collect_data/collect_state(extra)`, `release_stuck_item`, `holds_scheduler_lock` usados igual en tareas y tests.
