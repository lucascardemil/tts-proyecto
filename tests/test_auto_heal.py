# -*- coding: utf-8 -*-
"""Auto-sanado: errores de generacion (todo salvo los que piden accion humana) y de publicacion (salvo auth)."""
import copy
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline as bp


def ago(**kw):
    return (datetime.now() - timedelta(**kw)).isoformat()


@pytest.fixture
def store(monkeypatch):
    projects = {"p1": {"id": "p1", "status": "running", "videos": []}}
    monkeypatch.setattr(bp, "_load", lambda: copy.deepcopy(projects))
    monkeypatch.setattr(bp, "_save", lambda p: (projects.clear(), projects.update(copy.deepcopy(p))))
    return projects


def video(store, **fields):
    v = {"index": len(store["p1"]["videos"]), "status": "pending", "error": None}
    v.update(fields)
    store["p1"]["videos"].append(v)
    return v


def current(store, index=0):
    return store["p1"]["videos"][index]


# --- generacion: lista negra en vez de lista blanca ---------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "ffmpeg murió con codigo 1",  # error nuevo que ninguna lista conocia
    "Respuesta rara del proveedor",
    "tras 3 intento(s): Flow no devolvió imagen en 240s.",
    "Sin proveedor de texto disponible (timed out)",
])
def test_any_unknown_error_is_healable(msg):
    assert bp._is_healable_error(msg)


@pytest.mark.parametrize("msg", [
    "No encontre el proyecto 'abc'",
    "Flow pide iniciar sesión: entrá a mano en la ventana de Brave de Flow.",
    "Falta una key de visión en el .env (GEMINI_API_KEY, ...)",
    "Sin proveedor de texto disponible (text_provider: system prompt historias no encontrado)",
    "No existe el archivo x.wav.",
    "La voz clonada elegida ya no existe: elige otra voz",
])
def test_errors_that_need_a_human_are_not_healable(msg):
    assert not bp._is_healable_error(msg)


def test_empty_error_is_not_healable():
    assert not bp._is_healable_error(None) and not bp._is_healable_error("  ")


def test_healer_requeues_an_unknown_error_after_its_cooldown(store):
    video(store, status="error", error="ffmpeg murió", error_at=ago(hours=1))
    bp._requeue_healable_errors()
    v = current(store)
    assert v["status"] == "pending" and v["heal_cycles"] == 1 and v["gen_attempts"] == 0


def test_healer_waits_for_the_cooldown_and_stops_after_the_cycle_cap(store):
    video(store, status="error", error="ffmpeg murió", error_at=ago(minutes=5))
    video(store, status="error", error="ffmpeg murió", error_at=ago(days=3), heal_cycles=bp.MAX_HEAL_CYCLES)
    bp._requeue_healable_errors()
    assert current(store, 0)["status"] == "error" and current(store, 1)["status"] == "error"


# --- publicacion -------------------------------------------------------------------------------------------

def test_healer_requeues_publish_error_after_the_cooldown_and_keeps_networks_published(store):
    video(store, status="publish_error", error="facebook: timeout", publish_error_at=ago(minutes=20),
          publish_attempts=3, published_at={"youtube": "2026-01-01T00:00:00"})
    bp._requeue_healable_errors()
    v = current(store)
    assert v["status"] == "ready" and v["publish_attempts"] == 0 and v["publish_heal_cycles"] == 1
    assert v["published_at"] == {"youtube": "2026-01-01T00:00:00"}


def test_publish_healer_cooldown_doubles_each_cycle(store):
    video(store, status="publish_error", publish_error_at=ago(minutes=20), publish_heal_cycles=1)  # espera 30 min
    video(store, status="publish_error", publish_error_at=ago(minutes=40), publish_heal_cycles=1)
    bp._requeue_healable_errors()
    assert current(store, 0)["status"] == "publish_error" and current(store, 1)["status"] == "ready"


def test_publish_healer_skips_auth_errors_and_gives_up_after_the_cap(store):
    video(store, status="publish_error", publish_error_at=ago(days=1), last_publish_auth_error=True)
    video(store, status="publish_error", publish_error_at=ago(days=1), publish_heal_cycles=bp.MAX_PUBLISH_HEAL_CYCLES)
    bp._requeue_healable_errors()
    assert current(store, 0)["status"] == "publish_error" and current(store, 1)["status"] == "publish_error"


def test_publish_healer_ignores_paused_projects(store):
    store["p1"]["status"] = "paused"
    video(store, status="publish_error", publish_error_at=ago(days=1))
    bp._requeue_healable_errors()
    assert current(store)["status"] == "publish_error"


def test_manual_retry_resets_the_publish_heal_but_the_agents_does_not(store):
    video(store, status="publish_error", publish_heal_cycles=3)
    assert bp.retry_publish_video("p1", 0, reset_heal=False)
    assert current(store)["publish_heal_cycles"] == 3
    current(store)["status"] = "publish_error"
    assert bp.retry_publish_video("p1", 0)
    assert current(store)["publish_heal_cycles"] == 0
