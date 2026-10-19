# -*- coding: utf-8 -*-
"""Agentes autonomos de la oficina (ver docs/superpowers/specs/2026-10-14-agentes-autonomos-motor-reparar-design.md).

Un ciclo (hilo daemon, solo en el proceso dueño del programador) que: anota en la bitacora lo que
cambio en la oficina (alertas abiertas/resueltas, estado de cada agente), evalua las reglas de
Reparar y las ejecuta o las propone. Todo el estado vive en job_store ("office_agents"). `evaluate`
(decide, sin efectos) esta separada de `apply` (ejecuta y persiste) para probar sin hilos ni red."""

import logging
import re
import threading
import traceback
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
STATUS_SHOWN = 5  # de las 20 filas de la bitacora, a lo mas 5 son cambios de estado: no tapan las acciones
MODES = ("off", "propose", "auto")
# El ciclo de agentes no necesita los datos de Graph tan frescos como la pantalla: menos trafico a Meta.
AGENT_READ_TTL = {"remote_times": 900, "tokens": 900, "metrics_rows": 900}

_LOCK = threading.RLock()  # serializa leer-modificar-guardar del archivo de estado (hilo + endpoints)


# ---- estado persistido ----------------------------------------------------------------------

def _default_state() -> dict:
    return {"paused": False, "modes": {}, "attempts": {}, "proposals": [], "log": [],
            "open_findings": {}, "statuses": {}, "bootstrapped": False}


def _load_state() -> dict:
    """Lee el archivo y lo sanea: lo ausente, con el tipo equivocado o con entradas basura vuelve al valor
    por defecto o se descarta (nunca corta el hilo, ni el ciclo, ni la pantalla)."""
    raw = job_store.load(STORE)
    state = _default_state()
    if not isinstance(raw, dict):
        return state
    for key, default in state.items():
        if isinstance(raw.get(key), type(default)):
            state[key] = raw[key]
    state["modes"] = {k: v for k, v in state["modes"].items() if k in RULES_BY_ID and v in MODES}
    state["log"] = [e for e in state["log"] if isinstance(e, dict)]
    state["proposals"] = [p for p in state["proposals"] if isinstance(p, dict)
                          and all(isinstance(p.get(f), str) for f in ("id", "rule", "key"))]
    state["attempts"] = {k: v for k, v in state["attempts"].items() if isinstance(v, dict)}
    state["open_findings"] = {k: v for k, v in state["open_findings"].items() if isinstance(v, dict)
                              and isinstance(v.get("agent"), str) and isinstance(v.get("text"), str)}
    state["statuses"] = {k: v for k, v in state["statuses"].items() if isinstance(v, str)}
    return state


def _save_state(state: dict) -> None:
    """Estricto: si no se pudo guardar el intento, la excepcion corta (no se ejecuta nada a ciegas)."""
    job_store.save(STORE, state, strict=True)


def _log_error(message: str, *args) -> None:
    """Como logger.exception pero con el traceback sin secretos (las excepciones de requests traen el token en la URL)."""
    logger.error(message + "\n%s", *args, office_state._redact(traceback.format_exc()))


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _log(state: dict, now: datetime, **entry) -> None:
    state["log"].append({"ts": _iso(now), **entry})
    del state["log"][:-LOG_MAX]


# ---- eventos pasivos --------------------------------------------------------------------------

def _expired_gap(fid: str, now: datetime) -> bool:
    """Un hallazgo `gap:<pagina>:<fecha>` caduca solo cuando su dia ya pasó, o cuando ya pasó la hora tope de hoy."""
    if not fid.startswith("gap:"):
        return False
    try:
        day = datetime.strptime(fid.rsplit(":", 1)[-1], "%Y-%m-%d").date()
    except ValueError:
        return False
    return day < now.date() or (day == now.date() and now.hour >= office_state.TODAY_CUTOFF_HOUR)


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
            if _expired_gap(fid, now):  # el hueco de "hoy" deja de chequearse a las 21 h: no es una resolucion real
                continue
            _log(state, now, kind="alert_resolved", agent=known["agent"], key=fid,
                 label=known["text"], message="resuelta")
    for agent, view in snapshot.get("agents", {}).items():
        before = state["statuses"].get(agent)
        if before != view["status"]:
            state["statuses"][agent] = view["status"]
            _log(state, now, kind="status", agent=agent, key=agent, label=view["bubble"],
                 message=f"{before or '—'} → {view['status']}")


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
    if re.search(r"\bflow\b", text) and (data.get("flow") or {}).get("state") == "ok":
        return True
    return any(marker in text for marker in TEXT_ERROR_MARKERS) and bool(data.get("text_backends"))


def find_cause_resolved(data: dict, now: datetime) -> list:
    """Ítem en error cuyo motivo era Flow o el proveedor de texto, que hoy funcionan, y que lleva >= 2 h así."""
    if "projects" in data.get("errors", {}):
        return []
    found = []
    for project, video in office_state._items(data.get("projects", [])):
        if project.get("status") != "running" or video.get("status") != "error":
            continue
        if not _cause_resolved(video.get("error"), data):
            continue
        if (batch_pipeline._is_healable_error(video.get("error"))
                and video.get("heal_cycles", 0) < batch_pipeline.MAX_HEAL_CYCLES):
            continue  # el auto-sanado de los lotes todavia se hace cargo de este ítem
        error_at = office_state._parse(video.get("error_at"))
        if error_at and now - error_at < MIN_ERROR_AGE:
            continue
        found.append(_target("retry_cause_resolved", project, video))
    return found


def find_republish(data: dict, now: datetime) -> list:
    """Ítem en publish_error cuyo token de Meta para esa página está válido, cuyo último error no fue de autenticación
    y que el auto-sanado de publicaciones de los lotes ya dio por perdido."""
    if "projects" in data.get("errors", {}):
        return []
    tokens = {office_state._norm(t.get("page_name")): t for t in data.get("tokens") or []}
    found = []
    for project, video in office_state._items(data.get("projects", [])):
        if video.get("status") != "publish_error" or video.get("last_publish_auth_error"):
            continue
        if video.get("publish_heal_cycles", 0) < batch_pipeline.MAX_PUBLISH_HEAL_CYCLES:
            continue  # el auto-sanado de publicaciones de los lotes todavia se hace cargo de este ítem
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
    ok = batch_pipeline.retry_publish_video(target.payload["project_id"], target.payload["index"], reset_heal=False)
    return ok, "Publicación reintentada" if ok else "El ítem ya no estaba sin publicar"


def _exec_release(target: Target):
    ok = batch_pipeline.release_stuck_item(target.payload["project_id"], target.payload["index"],
                                           target.payload["status"])
    if ok:
        office_state.forget_stuck(target.payload["project_id"], target.payload["index"], target.payload["status"])
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
            _log_error("oficina: fallo la regla '%s'", rule.id)
    return found


# ---- motor ------------------------------------------------------------------------------------

def _mode(state: dict, rule: Rule) -> str:
    return state["modes"].get(rule.id, rule.default_mode)


def _run(rule: Rule, target: Target):
    """Ejecuta el objetivo; una excepcion cuenta como fallo (con el texto sin secretos), no corta nada."""
    try:
        return rule.execute(target)
    except Exception as exc:
        _log_error("oficina: fallo el ejecutor de '%s'", rule.id)
        return False, office_state._redact(f"{type(exc).__name__}: {exc}")


def _auto_used(state: dict, now: datetime) -> int:
    since = now - timedelta(hours=1)
    return sum(1 for e in state["log"]
               if e.get("kind") == "action" and e.get("mode") == "auto"
               and (office_state._parse(e.get("ts")) or since) > since)


def expire(state: dict, found: list, now: datetime) -> None:
    """Caduca propuestas (24 h, objetivo que ya no aparece o regla apagada) y poda intentos de mas de 7 dias."""
    live = {target.key for _, target in found}
    state["proposals"] = [p for p in state["proposals"]
                          if p["key"] in live and (office_state._parse(p.get("expires_at")) or now) > now
                          and p["rule"] in RULES_BY_ID and _mode(state, RULES_BY_ID[p["rule"]]) != "off"]
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
        pending = any(p["key"] == target.key for p in state["proposals"])
        if mode == "propose":
            if not pending:
                todo.append(("propose", rule, target))
        elif mode == "auto" and not pending:  # una propuesta pendiente espera tu decision: no se ejecuta sola
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
    data = office_state.collect_data(readers, now, extra={"brake": brake(now)}, ttl_overrides=AGENT_READ_TTL)
    snapshot = office_state.build_state(data, now)
    with _LOCK:
        state = _load_state()
        record_events(state, snapshot, now)
        if "projects" not in data["errors"]:
            found = _scan(data, now)
            expire(state, found, now)
            todo = evaluate(state, found, now)
            first_run = not state["bootstrapped"]
            if first_run:
                # Primer ciclo: lo que ya estaba pendiente no se ejecuta solo, se propone para que lo apruebes.
                proposed = {p["key"] for p in state["proposals"]}
                todo = [("propose", r, t) if v == "execute" else (v, r, t) for v, r, t in todo
                        if not (v == "execute" and t.key in proposed)]
            apply(state, todo, now, _save_state)
            if first_run and not state["paused"]:  # en pausa no se decidio nada: el arranque queda pendiente
                state["bootstrapped"] = True
                _log(state, now, kind="info", agent="revisor", key="bootstrap",
                     label="Primer ciclo: los pendientes anteriores quedan como propuestas para que los apruebes",
                     message="")
        _save_state(state)


def run_forever(readers_fn: Callable, stop: threading.Event, interval: float = CYCLE_SECONDS) -> None:
    """Cuerpo del hilo daemon: un ciclo, esperar, repetir; un ciclo roto se registra y sigue."""
    while True:
        try:
            run_cycle(readers_fn())
        except Exception:
            _log_error("oficina: fallo un ciclo de agentes")
        if stop.wait(interval):
            return


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
    data = office_state.collect_data(readers, now, ttl_overrides=AGENT_READ_TTL)
    if "projects" in data["errors"]:
        return {"ok": False, "error": "No pude leer los lotes: no se ejecuta con datos incompletos."}
    with _LOCK:
        state = _load_state()
        if not any(p["id"] == proposal_id for p in state["proposals"]):
            return {"ok": False, "error": "La propuesta ya no existe."}
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


def _recent_log(state: dict) -> list:
    """Las ultimas LOG_SHOWN filas, mas nueva primero; los cambios de estado ocupan a lo mas STATUS_SHOWN
    para que una racha de idle/busy no empuje fuera las acciones y alertas."""
    shown, statuses = [], 0
    for entry in reversed(state["log"]):
        if entry.get("kind") == "status":
            if statuses >= STATUS_SHOWN:
                continue
            statuses += 1
        shown.append(entry)
        if len(shown) >= LOG_SHOWN:
            break
    return shown


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
        "log": _recent_log(state),
        "brake": {"used": _auto_used(state, now), "limit": AUTO_ACTIONS_PER_HOUR},
    }
