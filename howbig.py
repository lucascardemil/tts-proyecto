# -*- coding: utf-8 -*-
"""
howbig.py — Banco de temas y selección de plantilla/tema para la página "How Big Really".

Posts educativos de comparación de escala (inglés, público USA/UK). El modelo de
texto solo redacta el copy con los datos del banco (`assets/howbig/topics.json`);
no inventa cifras ni fuentes.

- Plantillas: A (Scale comparison), B (Map comparison), C (Quantity / stack),
  sorteadas con pesos 7/2/1 y sin repetir la última plantilla del historial.
- Temas: uno no usado de la plantilla elegida; si se agota, de otra plantilla
  con temas libres; si no queda ninguno, `BankExhaustedError` (nunca inventar).
"""

from __future__ import annotations

import re
import functools
import json
import random
from pathlib import Path

# Lista cerrada de fuentes que el copy puede citar en la línea "Source:".
SOURCES = ("NOAA", "NASA", "USGS", "WHO", "Guinness World Records", "Smithsonian", "National Geographic")

# Pesos del sorteo de plantilla (≈ 70 % / 20 % / 10 %).
TEMPLATE_WEIGHTS = {"A": 7, "B": 2, "C": 1}
TEMPLATE_LABELS = {"A": "Scale comparison", "B": "Map comparison", "C": "Quantity / stack"}

TOPICS_PATH = Path(__file__).resolve().parent / "assets" / "howbig" / "topics.json"


class BankExhaustedError(RuntimeError):
    """No quedan temas sin usar en el banco de How Big Really."""


@functools.lru_cache(maxsize=1)
def _load_topics_cached() -> tuple:
    with open(TOPICS_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return tuple(data)


def load_topics() -> list[dict]:
    """Lista de temas del banco (copias, para que nadie modifique la caché)."""
    return [dict(t) for t in _load_topics_cached()]


def pick_template(recent_templates: list, rng=random) -> str:
    """Sorteo ponderado por TEMPLATE_WEIGHTS sin repetir la última plantilla del historial."""
    last = recent_templates[-1] if recent_templates else None
    options = [k for k in TEMPLATE_WEIGHTS if k != last]
    weights = [TEMPLATE_WEIGHTS[k] for k in options]
    return rng.choices(options, weights=weights, k=1)[0]


def pick_topic(template: str, used_ids: set, rng=random) -> dict:
    """Tema no usado de `template`; si se agotó, de otra plantilla con temas libres."""
    used = set(used_ids or ())
    free = [t for t in load_topics() if t["id"] not in used]
    same = [t for t in free if t["template"] == template]
    if same:
        return rng.choice(same)
    # Respaldo: otra plantilla con temas libres, elegida con los mismos pesos.
    others = sorted({t["template"] for t in free} - {template})
    if not others:
        raise BankExhaustedError(
            "How Big Really topic bank exhausted: every topic in assets/howbig/topics.json "
            "has already been used. Add new topics to the bank."
        )
    weights = [TEMPLATE_WEIGHTS.get(k, 1) for k in others]
    fallback = rng.choices(others, weights=weights, k=1)[0]
    return rng.choice([t for t in free if t["template"] == fallback])


def topic_block(topic: dict) -> str:
    """Bloque en inglés que se agrega al mensaje del generador con el tema del post."""
    source = f"Source: {topic['source']}" if topic.get("source") else "No source line for this topic (do NOT write a Source line)."
    return (
        "\n\nTOPIC FOR THIS POST (use ONLY these facts; do not add numbers, dates or sources of your own):\n"
        f"- Big subject: {topic['big']}\n- Reference for scale: {topic['reference']}\n"
        f"- Fact: {topic['fact']}\n- {source}\n- Closing question: {topic['question']}"
    )


_SOURCE_LINE = re.compile(r"^\(?\s*sources?\s*[:\-]", re.IGNORECASE)
_HASHTAG = re.compile(r"#\w+")


def format_caption(caption: str, topic: dict) -> str:
    """Deja el caption en la fórmula fija: gancho + dato, línea en blanco, pregunta,
    `Source:` solo si el tema lo trae y, al final, los hashtags (los limita
    `_limit_hashtags`, que mira el final del texto). El modelo a veces omite el salto
    o inventa la fuente; la línea Source sale siempre del banco, nunca del texto generado."""
    tags = _HASHTAG.findall(caption)
    lines = []
    for raw in caption.splitlines():
        line = raw.strip()
        if not line or _SOURCE_LINE.match(line):
            continue
        line = _HASHTAG.sub("", line).strip()
        if line:
            lines.append(line)
    if len(lines) >= 2:
        text = "\n".join(lines[:-1]) + "\n\n" + lines[-1]
    else:
        text = "\n".join(lines)
    if topic.get("source") and text:
        text += f"\nSource: {topic['source']}"
    if tags and text:
        text += "\n" + " ".join(tags)
    return text
