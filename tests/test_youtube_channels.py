# -*- coding: utf-8 -*-
"""Canales de YouTube en Ajustes: lista, estado y conexion por canal. Sin OAuth real."""
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import youtube_publisher


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    monkeypatch.setattr(youtube_publisher, "list_channels", lambda: [
        {"key": "1", "name": "Historias", "connected": True},
        {"key": "2", "name": "Otro", "connected": False},
        {"key": "3", "name": "Gaming", "connected": False},
    ])
    monkeypatch.setattr(app_module.batch_pipeline, "GAMING_YT_CHANNEL", "3")
    c = app_module.app.test_client()
    c.environ_base["HTTP_AUTHORIZATION"] = "Basic " + base64.b64encode(b"u:p").decode()
    return c


def test_channels_endpoint_marks_gaming_channel(client):
    data = client.get("/api/youtube/channels").get_json()
    assert [c["key"] for c in data["channels"]] == ["1", "2", "3"]
    assert [c["gaming"] for c in data["channels"]] == [False, False, True]


def test_connect_uses_the_requested_channel_key(client, monkeypatch):
    seen = []
    monkeypatch.setattr(youtube_publisher, "connect", lambda key=None: seen.append(key) or {"ok": True})
    for body, expected in [({"channel": "2"}, "2"), ({"channel": "gaming"}, "3"), ({"channel": "9"}, None), ({}, None)]:
        assert client.post("/api/youtube/connect", json=body).get_json() == {"ok": True}
        assert seen[-1] == expected  # una clave desconocida cae al canal por defecto, no a un token inventado


def test_connected_status_is_per_channel(client, monkeypatch):
    monkeypatch.setattr(youtube_publisher, "is_connected", lambda key=None: key == "2")
    assert client.get("/api/youtube/connected?channel=2").get_json()["connected"] is True
    assert client.get("/api/youtube/connected?channel=3").get_json()["connected"] is False
