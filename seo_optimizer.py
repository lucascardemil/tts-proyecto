"""
Sugerencias de SEO para YouTube (título, descripción, etiquetas) a partir del
guion de la historia. Basado en reglas — no depende de ninguna API de IA, así
que funciona siempre y es gratis.
"""

import re
from collections import Counter

POWER_WORDS = ["Real", "Increíble", "Impactante", "Secreto", "Definitivo", "Explicado"]

STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al",
    "y", "o", "que", "en", "a", "por", "con", "para", "su", "sus", "se", "lo",
    "le", "les", "es", "era", "fue", "ser", "estar", "está", "más", "muy",
    "pero", "como", "cuando", "donde", "porque", "también", "sin", "sobre",
    "entre", "esto", "eso", "esta", "este", "yo", "tú", "él", "ella", "no",
    "sí", "me", "mi", "te", "tu", "nos", "ni", "había", "hace", "hacía",
    "solo", "sola", "muchos", "muchas", "poco", "pocos", "todo", "toda",
    "todos", "todas", "así", "cada", "otro", "otra", "otros", "otras",
    "hasta", "desde", "algo", "alguien", "nada", "nadie", "porqué", "cómo",
    "cuál", "cuáles", "quién", "quiénes", "mismo", "misma", "mismos", "mismas",
    "tanto", "tanta", "tantos", "tantas", "vez", "veces", "aquí", "allí",
    "ahí", "ahora", "luego", "después", "durante", "mientras", "aunque",
}


def _extract_keywords(text: str, limit: int = 12) -> list:
    words = re.findall(r"[a-záéíóúüñ]{4,}", text.lower())
    words = [w for w in words if w not in STOPWORDS]
    counts = Counter(words)
    return [w for w, _ in counts.most_common(limit)]


def _build_title(script_text: str, keywords: list) -> str:
    """
    El título sale siempre del gancho real del guion (su primera oración),
    nunca de un título previo — reusar un título ya generado como semilla
    hace que, en cada click de "sugerir SEO", se le vuelvan a pegar palabras
    clave y power words encima de las que ya tenía (título cada vez más
    largo y sin sentido). Esta función es idempotente: mismo guion, mismo
    título, sin importar cuántas veces se la llame.
    """
    sentences = re.split(r"(?<=[.!?])\s+", script_text.strip())
    hook = sentences[0].strip().strip("«»\"“”") if sentences and sentences[0].strip() else ""

    title = hook or (" ".join(w.capitalize() for w in keywords[:4]) if keywords else "Video")
    if len(title) > 90:
        title = title[:87].rsplit(" ", 1)[0] + "..."

    if len(title) <= 75:
        power = next((p for p in POWER_WORDS if p.lower() not in title.lower()), None)
        if power:
            title = f"{title} ({power})"
    return title[:100]


def _build_description(script_text: str, keywords: list, title: str) -> str:
    hook = script_text.strip().split("\n")[0][:200] if script_text.strip() else title
    top_keywords = ", ".join(keywords[:8])
    lines = [
        hook,
        "",
        f"En este video: {top_keywords}." if top_keywords else "",
        "",
        "🔔 Suscribite para más contenido como este.",
        "👍 Dejá tu like y contame qué te pareció en los comentarios.",
    ]
    description = "\n".join(l for l in lines if l is not None)
    return description[:5000]


def _build_tags(keywords: list, title: str) -> list:
    tags = list(dict.fromkeys(keywords))  # dedupe preservando orden
    title_words = [w for w in re.findall(r"[a-záéíóúüñ]{4,}", title.lower()) if w not in STOPWORDS]
    for w in title_words:
        if w not in tags:
            tags.append(w)
    tags = tags[:20]

    total_len = 0
    final_tags = []
    for t in tags:
        total_len += len(t) + 1
        if total_len > 480:
            break
        final_tags.append(t)
    return final_tags


def _score(title: str, description: str, tags: list) -> int:
    score = 0
    # Título: hasta 30 pts
    if 30 <= len(title) <= 70:
        score += 15
    if any(c.isdigit() for c in title):
        score += 5
    if any(p.lower() in title.lower() for p in POWER_WORDS):
        score += 10
    # Descripción: hasta 40 pts
    if len(description) >= 150:
        score += 20
    if "suscri" in description.lower():
        score += 10
    if len(description.split("\n")) >= 3:
        score += 10
    # Tags: hasta 30 pts
    if len(tags) >= 8:
        score += 20
    if len(tags) >= 3:
        score += 10
    return min(score, 100)


def suggest_social_caption(script_text: str) -> dict:
    """
    Genera un caption corto + hashtags para Facebook/Instagram, a diferencia
    de suggest_seo (pensado para la descripción larga de YouTube).

    Args:
        script_text: texto completo de la narración.

    Returns:
        {"caption": str, "hashtags": list[str]}
    """
    keywords = _extract_keywords(script_text, limit=10)

    sentences = re.split(r"(?<=[.!?])\s+", script_text.strip())
    hook = " ".join(sentences[:2]).strip()[:200] if sentences else ""

    caption_lines = [hook, "", "🔔 Seguime para más contenido como este."]
    caption = "\n".join(l for l in caption_lines if l)

    hashtags = [f"#{w}" for w in keywords[:10]]

    return {"caption": caption, "hashtags": hashtags}


def suggest_seo(script_text: str, base_title: str = "") -> dict:
    """
    Genera título, descripción y etiquetas optimizadas a partir del guion.

    Args:
        script_text: texto completo de la narración.
        base_title: ya no se usa (se ignora) — ver _build_title().

    Returns:
        {"title": str, "description": str, "tags": list[str], "score": int}
    """
    keywords = _extract_keywords(script_text)
    title = _build_title(script_text, keywords)
    description = _build_description(script_text, keywords, title)
    tags = _build_tags(keywords, title)
    return {
        "title": title,
        "description": description,
        "tags": tags,
        "score": _score(title, description, tags),
    }
