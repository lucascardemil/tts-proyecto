# -*- coding: utf-8 -*-
"""Lista de juegos de los clips: se actualiza con lo mas popular de Medal. Sin red."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import gaming_clip as g

PAGE = """<h2 class="x">Próximos estrenos más esperados</h2><a href="/es/games/nuevo-raro"><img alt="Nuevo Raro game clips"></a>
<h2 class="x">Lo más popular en Medal</h2>
<a href="/es/games/roblox" class="a"><div><img alt="Roblox game clips"></div></a>
<a href="/es/games/gta-v" class="a"><div><img alt="GTA V game clips"></div></a>
<a href="/es/games/fl-studio" class="a"><div><img alt="FL Studio game clips"></div></a>
<a href="/es/games/apex-legends" class="a"><div><img alt="Apex Legends game clips"></div></a>
<a href="/es/games/counter-strike-2" class="a"><div><img alt="Counter-Strike 2 game clips"></div></a>
<h2 class="x">Otra sección</h2><a href="/es/games/otro"><img alt="Otro game clips"></a>"""


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """GAMES/GAME_ROTATION son globales mutables: se restauran al terminar. El disco no se toca."""
    games, rotation = dict(g.GAMES), list(g.GAME_ROTATION)
    store = {}
    monkeypatch.setattr(g.job_store, "load", lambda name: dict(store.get(name, {})))
    monkeypatch.setattr(g.job_store, "save", lambda name, data: store.__setitem__(name, data))
    yield store
    g.GAMES.clear(); g.GAMES.update(games)
    g.GAME_ROTATION[:] = rotation


def test_parses_only_the_popular_section_in_order_without_non_games(monkeypatch):
    monkeypatch.setattr(g, "_get", lambda url, **k: SimpleNamespace(text=PAGE))
    assert [e["slug"] for e in g._fetch_trending()] == ["roblox", "gta-v", "apex-legends", "counter-strike-2"]


def test_changed_page_raises_clear_error(monkeypatch):
    monkeypatch.setattr(g, "_get", lambda url, **k: SimpleNamespace(text="<h2>Otra cosa</h2>"))
    with pytest.raises(g.ClipError, match="Lo más popular"):
        g._fetch_trending()


def test_refresh_adds_trending_keeps_static_and_reorders_rotation(monkeypatch):
    monkeypatch.setattr(g, "_get", lambda url, **k: SimpleNamespace(text=PAGE))
    assert g.refresh_games(force=True) is True
    assert g.GAME_ROTATION[:4] == ["roblox", "gta-v", "apex-legends", "cs2"]  # cs2 conserva su clave fija
    assert "peak" in g.GAMES and "peak" in g.GAME_ROTATION  # los fijos no se quitan
    assert g.GAMES["gta-v"]["kills"] is False and g.GAMES["apex-legends"]["kills"] is True
    assert "#gtav" in g.GAMES["gta-v"]["tags"] and g.GAMES["gta-v"]["slug"] == "gta-v"
    assert [x["key"] for x in g.list_games()][:2] == ["roblox", "gta-v"]


def test_game_for_index_refreshes_and_mix_uses_trending_first(monkeypatch):
    monkeypatch.setattr(g, "_get", lambda url, **k: SimpleNamespace(text=PAGE))
    assert g.game_for_index("mix", 1)["key"] == "gta-v"


def test_stale_dynamic_games_are_dropped_on_next_refresh(monkeypatch):
    monkeypatch.setattr(g, "_get", lambda url, **k: SimpleNamespace(text=PAGE))
    g.refresh_games(force=True)
    g._apply_trending([{"slug": "roblox", "name": "Roblox"}, {"slug": "minecraft", "name": "Minecraft"}])
    assert "gta-v" not in g.GAMES and "apex-legends" not in g.GAMES


def test_ttl_avoids_rereading_medal_and_network_failure_is_silent(monkeypatch, isolated):
    calls = []
    monkeypatch.setattr(g, "_get", lambda url, **k: calls.append(url) or SimpleNamespace(text=PAGE))
    g.refresh_games(force=True)
    assert g.refresh_games() is False and len(calls) == 1  # dentro del TTL
    isolated[g.TRENDING_STORE]["updated"] = time.time() - g.TRENDING_TTL_SECONDS - 1

    def boom(url, **k):
        raise g.ClipError("sin red")

    monkeypatch.setattr(g, "_get", boom)
    assert g.refresh_games() is False  # no lanza: se queda con la lista guardada
    assert "gta-v" in g.GAMES


def test_games_endpoint(monkeypatch):
    import base64
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    monkeypatch.setattr(g, "_get", lambda url, **k: SimpleNamespace(text=PAGE))
    client = app_module.app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = "Basic " + base64.b64encode(b"u:p").decode()
    data = client.get("/api/gaming/games").get_json()
    assert data["ok"] and data["games"][0] == {"key": "roblox", "label": "Roblox", "trending": True}
