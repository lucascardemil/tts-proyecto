# -*- coding: utf-8 -*-
"""
text_provider.py — Generación de texto (guiones/posts) con FreeLLM endpoint local.

Único backend: FreeLLM API en http://127.0.0.1:31415/v1 (OpenAI-compatible).
- Modelos disponibles: auto, gemini-3.5-flash, nemotron-3-ultra, gpt-oss, qwen, llama, etc.
- "auto" hace routing inteligente al mejor modelo disponible.
- API key desde env HERMES_CUSTOM_FREELLMAPI_API_KEY.

VRAM: no usa GPU local (el endpoint corre en su propio proceso/servidor).
Formato de salida: el texto vuelve tal cual para los parsers existentes
(load_story_from_text / _parse_gaming_post).
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

class TextProviderError(RuntimeError):
    """El backend FreeLLM no pudo generar texto útil."""

class QuotaExceededError(TextProviderError):
    """Rate limit / cuota del backend agotada (429 persistente)."""

# ── Configuración (env-overridable) ─────────────────────────────────────────
FREELLM_URL = (os.environ.get("FREELLM_URL") or "http://127.0.0.1:31415/v1").rstrip("/")
FREELLM_API_KEY = os.environ.get("HERMES_CUSTOM_FREELLMAPI_API_KEY") or ""
# Modelo principal. "auto" enruta a modelos de razonamiento que se quedan pensando hasta
# agotar max_tokens (respuesta truncada), asi que el default es un modelo que responde directo.
FREELLM_MODEL = os.environ.get("FREELLM_MODEL") or "mistral-large-3"
# Se prueban en este orden cuando el principal falla (cuota 429, respuesta truncada o sin
# formato): ver model_chain(). Sobrescribible con FREELLM_FALLBACK_MODELS=a,b,c.
FREELLM_FALLBACK_MODELS = tuple(
    m.strip() for m in (os.environ.get("FREELLM_FALLBACK_MODELS") or "mistral-medium-3.5,gemini-3.5-flash,auto").split(",")
    if m.strip()
)
FREELLM_TIMEOUT = int(os.environ.get("FREELLM_TIMEOUT", "300"))  # seg
FREELLM_MAX_TOKENS = int(os.environ.get("FREELLM_MAX_TOKENS", "4096"))

# Reintentos internos por request ante red/timeouts/5xx
REQUEST_RETRIES = 2
RETRY_BACKOFF_SECONDS = 3

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

def _post_with_retries(
    label: str,
    fn: Callable[[], requests.Response],
    timeout: int,
) -> requests.Response:
    """POST con reintentos ante errores de red/timeout y 5xx. Un 429 se
    considera cuota/rate-limit: se raisea QuotaExceededError sin reintentar."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            resp = fn()
            if resp.status_code == 429:
                raise QuotaExceededError(
                    f"{label}: rate limit / cuota agotada (429). Reintenta mas tarde."
                )
            if resp.status_code >= 500:
                last_exc = RuntimeError(f"{label}: HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                return resp
        except QuotaExceededError:
            raise
        except requests.RequestException as e:
            last_exc = e
        if attempt < REQUEST_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise TextProviderError(f"{label}: fallo tras {REQUEST_RETRIES} intentos: {last_exc}")

def _extract_text_openai_shape(payload: dict) -> str:
    try:
        choice = payload["choices"][0]
        text = (choice["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        raise TextProviderError(f"Respuesta con formato inesperado: {payload}") from e
    # Un modelo de razonamiento (router "auto") puede gastar todo max_tokens
    # pensando: el texto queda cortado antes de la respuesta real.
    if choice.get("finish_reason") == "length":
        raise TextProviderError("freellm: respuesta truncada por max_tokens (el modelo se quedo razonando)")
    return _THINK_RE.sub("", text).strip()

def model_chain() -> list:
    """Modelos a probar, en orden: el principal y luego los de respaldo (sin repetir)."""
    return [FREELLM_MODEL] + [m for m in FREELLM_FALLBACK_MODELS if m != FREELLM_MODEL]


def _generate_freellm(
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    model: Optional[str] = None,
) -> str:
    """Llama al endpoint FreeLLM (OpenAI-compatible /v1/chat/completions)."""
    if not FREELLM_API_KEY:
        raise TextProviderError("freellm: falta HERMES_CUSTOM_FREELLMAPI_API_KEY en .env")

    model = model or FREELLM_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max(max_tokens, FREELLM_MAX_TOKENS),
        "temperature": temperature,
    }

    resp = _post_with_retries(
        f"freellm({model})",
        lambda: requests.post(
            f"{FREELLM_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {FREELLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=FREELLM_TIMEOUT,
        ),
        timeout=FREELLM_TIMEOUT,
    )

    if resp.status_code == 404:
        raise TextProviderError(f"freellm: modelo '{model}' no disponible en el endpoint")
    if resp.status_code != 200:
        raise TextProviderError(f"freellm: HTTP {resp.status_code}: {resp.text[:300]}")

    text = _extract_text_openai_shape(resp.json())
    if not text:
        raise TextProviderError("freellm: el modelo devolvió texto vacío")
    return text

def available_backends() -> list:
    """Backends utilizables (solo FreeLLM si está configurado)."""
    backends = []
    if FREELLM_API_KEY:
        backends.append(f"freellm:{FREELLM_MODEL}")
    return backends

def generate_text(
    system: str,
    user: str,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.8,
    prefer: str = "",
    model: Optional[str] = None,
) -> tuple[str, str]:
    """Genera texto con FreeLLM; devuelve (texto, backend)."""
    # Solo FreeLLM disponible
    if not FREELLM_API_KEY:
        raise TextProviderError("FreeLLM no configurado: falta HERMES_CUSTOM_FREELLMAPI_API_KEY en .env")

    try:
        text = _generate_freellm(system, user, max_tokens, temperature, model)
        if text.strip():
            return text.strip(), "freellm"
        raise TextProviderError("freellm: texto vacío")
    except QuotaExceededError as e:
        raise TextProviderError(str(e)) from e
    except TextProviderError:
        raise
    except Exception as e:
        raise TextProviderError(f"freellm: {e}") from e