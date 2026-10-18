# -*- coding: utf-8 -*-
"""Estado en vivo de la oficina de agentes (ver docs/superpowers/specs/2026-10-13-oficina-agentes-design.md).

Solo lectura: junta lo que el sistema ya sabe (lotes, Flow, tokens, metricas) y lo traduce a cuatro
"agentes" con un estado y una lista de hallazgos con boton de accion. No escribe nada ni dispara ningun
proceso. `build_state` es pura (datos ya leidos -> dict) para poder testearla sin red; `collect_state`
hace las lecturas, con cache por fuente."""

import logging
import re
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
SCHEDULED_STATUSES = ("pending", "generating", "ready", "publishing", "published")
TODAY_CUTOFF_HOUR = 21  # pasadas las 21 ya no se programa nada para hoy (ventana 9-21 h del lote)
STUCK_AFTER = timedelta(minutes=45)
TOKEN_WARN_DAYS = 7
READ_LABELS = {"projects": "los lotes", "flow": "el estado de Flow", "text_backends": "el proveedor de texto",
               "tokens": "los tokens de Meta", "remote_times": "los horarios de Facebook"}
FAIL_TTL = 60  # un fallo se recuerda un minuto como maximo: no se reintenta a Meta en cada ciclo de 3 s
READ_TTL = {"projects": 0, "flow": 15, "text_backends": 30, "tokens": 300, "remote_times": 60, "metrics_rows": 600}


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


def _redact(message: str) -> str:
    """Tapa access_token=..., input_token=... y client_secret=... (las excepciones de requests traen la URL completa)."""
    return re.sub(r"((?:access_token|input_token|client_secret)=)[^&\s'\")]+", r"\1***", message)


def _reason(message) -> str:
    return f" — {_redact(str(message))[:120]}" if message else ""


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
        bubble = f"{len(upcoming)} programado" + ("" if len(upcoming) == 1 else "s")
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
        if project.get("status") in ACTIVE_PROJECT_STATUSES and any(
                v.get("status") != "removed" for v in project.get("videos", [])):
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
            if token.get("auth_error") is False:  # red caida, rate limit, respuesta rara: no se pudo comprobar
                findings.append(_finding(f"token-check:{_norm(page)}", "revisor", "warn",
                                         f"No pude verificar el token de {page}", _view("Abrir ajustes", "ajustes")))
            else:
                findings.append(_finding(f"token-invalid:{_norm(page)}", "revisor", "error",
                                         f"Token de Meta inválido en {page}", _view("Abrir ajustes", "ajustes")))
            continue
        expires = _parse(token.get("expires_at"))
        if expires and expires - now < timedelta(days=TOKEN_WARN_DAYS):
            days = max((expires - now).days, 0)
            unit = "día" if days == 1 else "días"
            findings.append(_finding(f"token-expiry:{_norm(page)}", "revisor", "warn",
                                     f"El token de {page} vence en {days} {unit}", _view("Abrir ajustes", "ajustes")))
    return findings


def _flow_findings(data: dict) -> list:
    flow = data.get("flow")
    if not flow or flow.get("state") == "ok":
        return []
    for project, video in _items(data.get("projects", [])):
        settings = project.get("video_settings") or {}
        if ("flow" in (settings.get("image_provider"), settings.get("provider"))
                and video.get("status") in ("pending", "generating")):
            return [_finding("flow", "revisor", "warn", f"Flow: {flow.get('message')}", _view("Abrir ajustes", "ajustes"))]
    return []


SLIDESHOW_CAUSES = {"static_scenes": "escenas sin movimiento", "low_motion": "pocos clips con movimiento",
                    "long_hold": "escenas muy largas", "slow_cuts": "cortes muy lentos"}


def _slideshow_findings(data: dict) -> list:
    """Videos listos que el puntaje de slideshow (ver slideshow_risk) marco 'revise' o 'fail'."""
    findings = []
    for project, video in _items(data.get("projects", [])):
        risk = video.get("slideshow_risk")
        if video.get("status") != "ready" or not isinstance(risk, dict) or risk.get("verdict") not in ("revise", "fail"):
            continue
        dims = risk.get("dimensions") if isinstance(risk.get("dimensions"), dict) else {}
        scored = {k: v for k, v in dims.items() if k in SLIDESHOW_CAUSES and isinstance(v, (int, float))}
        cause = f" — {SLIDESHOW_CAUSES[max(scored, key=scored.get)]}" if scored else ""
        level = "alto" if risk["verdict"] == "fail" else "medio"
        findings.append(_finding(
            f"slideshow:{project['id']}:{video['index']}", "revisor", "warn",
            f"{_page(project)}: ítem {video['index'] + 1} se ve como slideshow (riesgo {level}{cause})",
            _view("Abrir lotes", "lote")))
    return findings


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


def _brake_findings(data: dict) -> list:
    brake = data.get("brake") or {}
    if brake.get("limit") and brake.get("used", 0) >= brake["limit"]:
        return [_finding("automation-brake", "revisor", "warn",
                         f"Los agentes alcanzaron su límite de {brake['limit']} acciones por hora")]
    return []


def _revisor_findings(data: dict, now: datetime) -> list:
    errors = data.get("errors") or {}
    # sin lotes leidos, los horarios remotos solos darian falsos huecos: se omiten (sale 'read:projects')
    findings = [] if "projects" in errors else _gap_findings(data, now)
    findings += _token_findings(data, now) + _flow_findings(data) + _stuck_findings(data, now) + _brake_findings(data) + _slideshow_findings(data)
    backends = data.get("text_backends")
    if backends is not None and not backends:
        findings.append(_finding("text-provider", "revisor", "error", "Sin proveedor de texto disponible",
                                 _view("Abrir ajustes", "ajustes")))
    for source, message in errors.items():
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


def _metricas_agent(data: dict) -> dict:
    rows = data.get("metrics_rows")
    if not isinstance(rows, dict):  # algo que no es un diccionario se trata como sin datos
        rows = None
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


def _safe(name: str, fn, fallback):
    """Llama fn(); si falla lo registra y devuelve el fallback: un agente roto no tumba a los demas."""
    try:
        return fn()
    except Exception:
        logger.exception("oficina: fallo el agente '%s'", name)
        return fallback


def _no_data() -> dict:
    return {"status": "warn", "bubble": "Sin datos", "detail": {}}


def build_state(data: dict, now: datetime) -> dict:
    findings: list = []
    agents = {
        "videos": _safe("videos", lambda: _videos_agent(data, findings), _no_data()),
        "publica": _safe("publica", lambda: _publica_agent(data, now, findings), _no_data()),
        "metricas": _safe("metricas", lambda: _metricas_agent(data), _no_data()),
    }
    findings.extend(_safe("revisor", lambda: _revisor_findings(data, now), []))
    agents["revisor"] = _revisor_agent(findings)
    for agent in agents.values():
        agent["bubble"] = _short(agent["bubble"])
    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["id"]))
    return {"ok": True, "generated_at": now.isoformat(timespec="seconds"), "agents": agents, "findings": findings}


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
            error = _redact(f"{type(exc).__name__}: {exc}")
            logger.warning("oficina: fallo la lectura '%s': %s", name, error)
            value = None
        with self._lock:
            self._entries[name] = (now, value, error)
        return value, error


_CACHE = _Cache()
_TRACKER = StuckTracker()


def reset() -> None:
    global _CACHE, _TRACKER
    _CACHE = _Cache()
    _TRACKER = StuckTracker()


def collect_data(readers: dict, now: Optional[datetime] = None, extra: Optional[dict] = None,
                 ttl_overrides: Optional[dict] = None) -> dict:
    """Lee todas las fuentes (con cache) y devuelve los datos crudos que consume `build_state`.
    `extra` se mezcla al final: lo usa quien tenga datos propios que el Revisor deba ver (el freno).
    `ttl_overrides` cambia el TTL de la cache de algunas fuentes (el ciclo de agentes lee Graph con menos frecuencia)."""
    now = now or datetime.now()
    data: dict = {"errors": {}}
    for name, fn in readers.items():
        value, error = _CACHE.get(name, (ttl_overrides or {}).get(name, READ_TTL.get(name, 60)), fn)
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
