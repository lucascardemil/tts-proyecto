# -*- coding: utf-8 -*-
"""Tests de la cadena de generacion de texto (FreeLLM text_provider).
Todo con dobles de prueba: sin red y sin tocar el job_store real."""
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import text_provider
import batch_pipeline


STORY_OK = "Guion\nImagen 1\nFrase del guion: «Hola mundo.»\nPrompt: a calm scene"


# ── text_provider.generate_text ──────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


def test_generate_text_freellm_success(monkeypatch):
    monkeypatch.setattr(text_provider, "FREELLM_API_KEY", "test-key")
    monkeypatch.setattr(text_provider, "FREELLM_MODEL", "auto")
    monkeypatch.setattr(text_provider, "FREELLM_MAX_TOKENS", 512)

    def fake_post(*a, **k):
        return _FakeResponse(json_data={"choices": [{"message": {"content": "hola"}}]})

    monkeypatch.setattr(text_provider.requests, "post", fake_post)
    text, backend = text_provider.generate_text("sys", "user")
    assert text == "hola"
    assert backend == "freellm"


def test_generate_text_freellm_no_api_key(monkeypatch):
    monkeypatch.setattr(text_provider, "FREELLM_API_KEY", "")
    with pytest.raises(text_provider.TextProviderError, match="FreeLLM no configurado"):
        text_provider.generate_text("sys", "user")


def test_generate_text_freellm_empty_response(monkeypatch):
    monkeypatch.setattr(text_provider, "FREELLM_API_KEY", "test-key")
    monkeypatch.setattr(
        text_provider.requests, "post",
        lambda *a, **k: _FakeResponse(json_data={"choices": [{"message": {"content": ""}}]}))
    with pytest.raises(text_provider.TextProviderError, match="texto vacío"):
        text_provider.generate_text("sys", "user")


def test_generate_text_freellm_429_raises_quota(monkeypatch):
    monkeypatch.setattr(text_provider, "FREELLM_API_KEY", "test-key")
    monkeypatch.setattr(text_provider.time, "sleep", lambda s: None)

    def fake_post(*args, **kwargs):
        return _FakeResponse(status_code=429, text="rate limited")

    monkeypatch.setattr(text_provider.requests, "post", fake_post)
    with pytest.raises(text_provider.TextProviderError, match="rate limit"):
        text_provider.generate_text("sys", "user")


def test_generate_text_freellm_5xx_retries_then_raises(monkeypatch):
    monkeypatch.setattr(text_provider, "FREELLM_API_KEY", "test-key")
    monkeypatch.setattr(text_provider.time, "sleep", lambda s: None)
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return _FakeResponse(status_code=503, text="down")

    monkeypatch.setattr(text_provider.requests, "post", fake_post)
    with pytest.raises(text_provider.TextProviderError):
        text_provider.generate_text("sys", "user")
    assert len(calls) == text_provider.REQUEST_RETRIES


def test_post_with_retries_429_raises_quota(monkeypatch):
    monkeypatch.setattr(text_provider.time, "sleep", lambda s: None)
    calls = []

    def fn():
        calls.append(1)
        return _FakeResponse(status_code=429, text="rate limited")

    with pytest.raises(text_provider.QuotaExceededError):
        text_provider._post_with_retries("test", fn, timeout=5)
    assert len(calls) == 1  # 429 no se reintenta


def test_post_with_retries_5xx_retries_then_raises(monkeypatch):
    monkeypatch.setattr(text_provider.time, "sleep", lambda s: None)
    calls = []

    def fn():
        calls.append(1)
        return _FakeResponse(status_code=503, text="down")

    with pytest.raises(text_provider.TextProviderError):
        text_provider._post_with_retries("test", fn, timeout=5)
    assert len(calls) == text_provider.REQUEST_RETRIES


def test_available_backends_no_key(monkeypatch):
    monkeypatch.setattr(text_provider, "FREELLM_API_KEY", "")
    assert text_provider.available_backends() == []


def test_available_backends_with_key(monkeypatch):
    monkeypatch.setattr(text_provider, "FREELLM_API_KEY", "test-key")
    monkeypatch.setattr(text_provider, "FREELLM_MODEL", "auto")
    assert text_provider.available_backends() == ["freellm:auto"]


# ── batch_pipeline._generate_text_with_chain ─────────────────────────────────

def test_chain_freellm_wins(monkeypatch):
    def _tp_ok(system, user, **k):
        return STORY_OK, "freellm"

    monkeypatch.setattr(text_provider, "available_backends", lambda: ["freellm:auto"])
    monkeypatch.setattr(text_provider, "generate_text", _tp_ok)
    out = batch_pipeline._generate_text_with_chain(
        "historias", "PROYECTO", "dame una historia", "s1")
    assert out == STORY_OK


def test_chain_all_fail_raises_with_detail(monkeypatch):
    monkeypatch.setattr(text_provider, "available_backends", lambda: [])
    with pytest.raises(RuntimeError, match="Sin proveedor de texto"):
        batch_pipeline._generate_text_with_chain("historias", "P", "t", "s3")


def test_chain_missing_prompt_file_skips_link(monkeypatch, tmp_path):
    # Sin prompts/<kind>_system.md la cadena falla con detalle.
    monkeypatch.setattr(batch_pipeline, "_PROMPTS_DIR", tmp_path)  # vacio
    monkeypatch.setattr(text_provider, "available_backends", lambda: ["freellm:auto"])
    with pytest.raises(RuntimeError, match="Sin proveedor de texto"):
        batch_pipeline._generate_text_with_chain("gaming", "P", "t", "s5")


# ── create_project: validacion del campo nuevo ───────────────────────────────

def test_create_project_normalizes_text_provider(monkeypatch, tmp_path):
    from datetime import datetime
    monkeypatch.setattr(batch_pipeline, "_load", lambda: {})
    monkeypatch.setattr(batch_pipeline, "_save", lambda projects: None)
    monkeypatch.setattr(batch_pipeline, "_compute_schedule", lambda *a, **k: [datetime.now()])
    monkeypatch.setattr(batch_pipeline, "_best_hour_for_networks", lambda networks: 20)
    project = batch_pipeline.create_project(
        page_name="PAGINA", total_videos=1, per_day=1,
        networks={"facebook": None}, video_settings={"text_provider": "auto"},
        trigger_message="t", content_type="video",
    )
    assert project["video_settings"]["text_provider"] == "auto"
    assert project["page_name"] == "PAGINA"
    # Valor invalido -> "auto"
    project2 = batch_pipeline.create_project(
        page_name="PAGINA", total_videos=1, per_day=1,
        networks={"facebook": None}, video_settings={"text_provider": "no-existe"},
        trigger_message="t", content_type="video",
    )
    assert project2["video_settings"]["text_provider"] == "auto"

def test_generate_text_truncated_by_max_tokens_raises(monkeypatch):
    monkeypatch.setattr(text_provider, "FREELLM_API_KEY", "test-key")
    monkeypatch.setattr(
        text_provider.requests, "post",
        lambda *a, **k: _FakeResponse(json_data={"choices": [{"finish_reason": "length", "message": {"content": "We need to output..."}}]}))
    with pytest.raises(text_provider.TextProviderError, match="truncada"):
        text_provider.generate_text("sys", "user")


def test_generate_text_strips_think_block(monkeypatch):
    monkeypatch.setattr(text_provider, "FREELLM_API_KEY", "test-key")
    monkeypatch.setattr(
        text_provider.requests, "post",
        lambda *a, **k: _FakeResponse(json_data={"choices": [{"message": {"content": "<think>hmm</think>hola"}}]}))
    assert text_provider.generate_text("sys", "user")[0] == "hola"


def test_chain_retries_reasoning_dump_then_succeeds(monkeypatch):
    replies = iter(["We need to output... Then Imagen 1, Imagen 2, ...", STORY_OK])
    monkeypatch.setattr(text_provider, "available_backends", lambda: ["freellm:auto"])
    monkeypatch.setattr(text_provider, "generate_text", lambda *a, **k: (next(replies), "freellm"))
    out = batch_pipeline._generate_text_with_chain("historias", "P", "t", "s6")
    assert out == STORY_OK


def test_chain_gives_up_after_attempts(monkeypatch):
    calls = []
    monkeypatch.setattr(text_provider, "available_backends", lambda: ["freellm:auto"])
    monkeypatch.setattr(text_provider, "generate_text", lambda *a, **k: (calls.append(1) or "solo razonamiento", "freellm"))
    with pytest.raises(RuntimeError, match="bloques Imagen"):
        batch_pipeline._generate_text_with_chain("historias", "P", "t", "s7")
    assert len(calls) == max(batch_pipeline.TEXT_FORMAT_ATTEMPTS, len(text_provider.model_chain()))


def test_chain_rotates_models_on_quota_or_reasoning(monkeypatch):
    used = []
    replies = iter([text_provider.TextProviderError("freellm(a): rate limit / cuota agotada (429)."),
                    text_provider.TextProviderError("freellm: respuesta truncada por max_tokens"), STORY_OK])

    def fake(system, user, **k):
        used.append(k.get("model"))
        r = next(replies)
        if isinstance(r, Exception):
            raise r
        return r, "freellm"

    monkeypatch.setattr(text_provider, "available_backends", lambda: ["freellm:x"])
    monkeypatch.setattr(text_provider, "generate_text", fake)
    out = batch_pipeline._generate_text_with_chain("historias", "P", "t", "s8")
    assert out == STORY_OK
    chain = text_provider.model_chain()
    assert used == chain[:3] and len(set(used)) == 3  # un modelo distinto por intento


def test_model_chain_has_no_duplicates_and_starts_with_primary():
    chain = text_provider.model_chain()
    assert chain[0] == text_provider.FREELLM_MODEL and len(chain) == len(set(chain))
