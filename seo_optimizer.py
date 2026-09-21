"""
Sugerencias de SEO para YouTube (título, descripción, etiquetas) a partir del
guion de la historia. Basado en reglas — no depende de ninguna API de IA, así
que funciona siempre y es gratis.
"""

import re
from collections import Counter

POWER_WORDS = ["Definitivo", "Explicado"]

# Perfil "rescate_animal" (Manual maestro v3.2, §2.1/§7): vocabulario de daño
# explícito y adjetivos de shock que YouTube puede leer como contenido violento.
# Se valida sobre el guion antes de renderizar (batch_pipeline).
_FORBIDDEN_RESCUE_PATTERN = re.compile(
    r"\b(?:sangre|sangrando|sangriento|sangrienta|herida(?:s)? abierta(?:s)?|golpead[oa]s?|"
    r"torturad[oa]s?|mutilad[oa]s?|agoniz\w+|moribund[oa]s?|cad[aá]ver(?:es)?|muert[oa]s?|"
    r"brutal(?:es|mente)?|impactante(?:s)?|escalofriante(?:s)?|aterrador(?:a|es|as)?|"
    r"no apto para sensibles|c[aá]maras? (?:captaron|grabaron)|video real|im[aá]genes reales|"
    r"testigos? lo grabaron)\b",
    re.IGNORECASE,
)

RESCUE_AI_NOTICE = "Historia recreada con IA con fines narrativos."

# Hashtags validados (§7.7), en orden de prioridad por defecto.
_RESCUE_HASHTAGS_DEFAULT = [
    "#RescateAnimal", "#HistoriasDeRescate", "#SegundaOportunidad",
    "#AmorAnimal", "#FinalFeliz", "#HistoriasEmotivas",
]
_RESCUE_HASHTAG_TRIGGERS = [
    (r"adopt", "#AdoptaNoCompres"),
    (r"santuario|refugio permanente", "#Santuario"),
    (r"segunda oportunidad|nueva vida|nuevo comienzo", "#SegundaOportunidad"),
    (r"amor incondicional|lealtad|nunca (?:lo|la) abandon", "#AmorIncondicional"),
    (r"final feliz|hogar|familia", "#FinalFeliz"),
]
# Cierres con pregunta que invitan a compartir (§7.3/§7.8); se rotan por guion.
_RESCUE_CTA_QUESTIONS = [
    "¿Qué habrías hecho tú en su lugar?",
    "¿Crees que podrá volver a confiar? Compártelo con quien lo necesite.",
    "¿Alguna vez un animal cambió tu vida? Cuéntanos.",
    "Si esta historia te tocó el corazón, envíasela a alguien que ame a los animales.",
]

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


def find_forbidden_terms(text: str) -> list:
    """Términos del filtro de seguridad de contenido (§2.1) presentes en `text`."""
    return sorted({m.group(0).lower() for m in _FORBIDDEN_RESCUE_PATTERN.finditer(text or "")})


def _build_title(script_text: str, keywords: list, power_word: bool = True) -> str:
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

    if power_word and len(title) <= 75:
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


def _sentences(text: str) -> list:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]


def _clip_at_word(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _rescue_hashtags(script_text: str, count: int) -> list:
    """Hashtags validados (§7.7) que calzan con el guion; completa con los
    de uso general hasta `count`, sin repetir."""
    text = script_text.lower()
    chosen = ["#RescateAnimal"]
    for pattern, tag in _RESCUE_HASHTAG_TRIGGERS:
        if re.search(pattern, text) and tag not in chosen:
            chosen.append(tag)
    for tag in _RESCUE_HASHTAGS_DEFAULT:
        if tag not in chosen:
            chosen.append(tag)
    return chosen[:count]


def _rescue_cta(script_text: str, offset: int = 0) -> str:
    """Pregunta de cierre: la del propio guion si termina en una pregunta
    corta (nace de la historia, §7.8); si no, una del set rotando por guion."""
    last = (_sentences(script_text) or [""])[-1]
    if last.endswith("?") and len(last) <= 90:
        return last
    return _RESCUE_CTA_QUESTIONS[(sum(map(ord, script_text)) + offset) % len(_RESCUE_CTA_QUESTIONS)]


def _build_rescue_description(script_text: str) -> str:
    """Descripción de YouTube según §7.5: gancho arriba, aviso de IA dentro
    de las dos primeras líneas, keyword validada, CTA y 3-5 hashtags."""
    sentences = _sentences(script_text)
    hook = _clip_at_word(sentences[0], 200) if sentences else ""
    context = _clip_at_word(" ".join(sentences[1:3]), 400)
    blocks = [
        f"{hook}\n{RESCUE_AI_NOTICE}",
        f"{context}\nUna historia sobre animales rescatados y segundas oportunidades.".strip(),
        _rescue_cta(script_text),
        " ".join(_rescue_hashtags(script_text, 4)),
    ]
    return "\n\n".join(blocks)[:5000]


def suggest_rescue_copy(script_text: str, network: str) -> str:
    """Texto listo para pegar en Facebook o Instagram (§7.3/§7.4): 200-300
    caracteres totales, gancho completo dentro de los primeros ~125, cierre
    con pregunta que invita a compartir y hashtags validados (1-3 en
    Facebook, 3-5 en Instagram). Facebook e Instagram rotan distinto la
    pregunta de cierre para no publicar texto idéntico en ambas redes."""
    is_instagram = network == "instagram"
    sentences = _sentences(script_text)
    hook = _clip_at_word(sentences[0], 120) if sentences else ""
    cta = _rescue_cta(script_text, offset=1 if is_instagram else 0)
    tags = " ".join(_rescue_hashtags(script_text, 4 if is_instagram else 2))

    def assemble(body: str) -> str:
        return f"{body}\n\n{cta}\n\n{tags}"

    body = hook
    for extra in sentences[1:]:
        if len(assemble(body)) >= 200:
            break
        candidate = f"{body} {extra}"
        if len(assemble(candidate)) > 300:
            break
        body = candidate
    return assemble(body)


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


def suggest_seo(script_text: str, base_title: str = "", rescue: bool = False) -> dict:
    """
    Genera título, descripción y etiquetas optimizadas a partir del guion.

    Args:
        script_text: texto completo de la narración.
        base_title: ya no se usa (se ignora) — ver _build_title().
        rescue: perfil "rescate_animal" (Manual v3.2): título sin power words,
            descripción con aviso de IA en las 2 primeras líneas y hashtags
            validados, sin el "Suscribite/Dejá tu like" genérico.

    Returns:
        {"title": str, "description": str, "tags": list[str], "score": int}
    """
    keywords = _extract_keywords(script_text)
    title = _build_title(script_text, keywords, power_word=not rescue)
    description = (
        _build_rescue_description(script_text) if rescue
        else _build_description(script_text, keywords, title)
    )
    tags = _build_tags(keywords, title)
    if rescue:
        tags = list(dict.fromkeys(["rescate de animales", "animales rescatados", *tags]))
    return {
        "title": title,
        "description": description,
        "tags": tags,
        "score": _score(title, description, tags),
    }
