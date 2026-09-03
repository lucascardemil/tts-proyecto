"""
Cierra el loop de analítica: mira qué videos ya publicados (con sus vistas)
funcionaron mejor y saca patrones simples (longitud de título, keywords)
para orientar el próximo video. Basado en reglas — no depende de ninguna
API de IA.
"""

import re
from datetime import datetime

STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al",
    "y", "o", "que", "en", "a", "por", "con", "para", "su", "sus", "se", "lo",
    "le", "les", "es", "era", "fue", "ser", "estar", "está", "más", "muy",
    "pero", "como", "cuando", "donde", "porque", "también", "sin", "sobre",
    "entre", "esto", "eso", "esta", "este", "yo", "tú", "él", "ella", "no",
    "sí", "me", "mi", "te", "tu", "nos", "ni",
    "hasta", "desde", "algo", "alguien", "nada", "nadie", "porqué", "cómo",
    "cuál", "cuáles", "quién", "quiénes", "mismo", "misma", "mismos", "mismas",
    "tanto", "tanta", "tantos", "tantas", "vez", "veces", "aquí", "allí",
    "ahí", "ahora", "luego", "después", "durante", "mientras", "aunque",
}

MIN_SAMPLE_SIZE = 3

# (etiqueta, horas que abarca, hora representativa a usar para programar)
DAYPARTS = [
    ("madrugada (00-05h)", range(0, 6), 3),
    ("mañana (06-11h)", range(6, 12), 9),
    ("tarde (12-17h)", range(12, 18), 15),
    ("noche (18-23h)", range(18, 24), 20),
]


def _title_bucket(length: int) -> str:
    if length < 30:
        return "corto (menos de 30 caracteres)"
    if length <= 50:
        return "medio (30-50 caracteres)"
    return "largo (más de 50 caracteres)"


def _keywords(title: str) -> list:
    words = re.findall(r"[a-záéíóúüñ]{4,}", (title or "").lower())
    return [w for w in words if w not in STOPWORDS]


def analyze_from_rows(rows: list) -> dict:
    """
    Analiza filas ya combinadas (publicado + stats: views/likes/comments) de
    las 3 plataformas juntas y sugiere qué patrón de título vino acompañado
    de más vistas.

    Args:
        rows: lista de dicts con al menos "title" y "views".

    Returns:
        {"insufficient_data": True, "sample_size": int} si hay menos de
        MIN_SAMPLE_SIZE videos con vistas registradas, o:
        {"sample_size": int, "avg_views": int, "best_title_length": str, "top_keywords": list[str]}
    """
    items = [i for i in rows if isinstance(i.get("views"), (int, float)) and i.get("title")]
    if len(items) < MIN_SAMPLE_SIZE:
        return {"insufficient_data": True, "sample_size": len(items)}

    avg_views = sum(i["views"] for i in items) / len(items)

    bucket_views: dict = {}
    for i in items:
        bucket = _title_bucket(len(i["title"]))
        bucket_views.setdefault(bucket, []).append(i["views"])
    best_bucket = max(bucket_views, key=lambda b: sum(bucket_views[b]) / len(bucket_views[b]))

    top_items = [i for i in items if i["views"] > avg_views]
    keyword_counts: dict = {}
    for i in top_items:
        for kw in _keywords(i["title"]):
            keyword_counts[kw] = keyword_counts.get(kw, 0) + 1
    top_keywords = sorted(keyword_counts, key=keyword_counts.get, reverse=True)[:8]

    return {
        "sample_size": len(items),
        "avg_views": round(avg_views),
        "best_title_length": best_bucket,
        "top_keywords": top_keywords,
    }


def best_posting_hour(rows: list) -> dict:
    """
    Analiza en qué franja horaria (mañana/tarde/noche/madrugada) las
    publicaciones ya hechas tuvieron más vistas, para sugerir el horario de
    la próxima publicación en esa misma plataforma.

    Args:
        rows: lista de dicts con al menos "published_at" (ISO) y "views".

    Returns:
        {"insufficient_data": True, "sample_size": int} si hay menos de
        MIN_SAMPLE_SIZE publicaciones con vistas registradas, o:
        {"sample_size": int, "best_daypart": str, "best_hour": int}
    """
    items = [i for i in rows if isinstance(i.get("views"), (int, float)) and i.get("published_at")]
    if len(items) < MIN_SAMPLE_SIZE:
        return {"insufficient_data": True, "sample_size": len(items)}

    daypart_views: dict = {}
    for i in items:
        try:
            hour = datetime.fromisoformat(i["published_at"]).hour
        except ValueError:
            continue
        for label, hours, rep_hour in DAYPARTS:
            if hour in hours:
                daypart_views.setdefault(label, {"views": [], "hour": rep_hour})["views"].append(i["views"])
                break

    if not daypart_views:
        return {"insufficient_data": True, "sample_size": len(items)}

    best_label = max(daypart_views, key=lambda l: sum(daypart_views[l]["views"]) / len(daypart_views[l]["views"]))
    return {
        "sample_size": len(items),
        "best_daypart": best_label,
        "best_hour": daypart_views[best_label]["hour"],
    }
