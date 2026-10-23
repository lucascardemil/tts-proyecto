# -*- coding: utf-8 -*-
"""Ganchos y titulos de Shorts: puntaje del gancho, eleccion entre candidatos, lint de titulo y bloque de formulas.

Adaptado de youtube-agent-skill (Jakeschincariol, MIT): skills/yt-script/hookscore.py y hooks*.json, skills/yt-package/title.py.
Lo que cambia respecto al original: lexicos en espanol, numeros escritos con letras (la narracion TTS no lleva digitos) y
funciones importables en lugar de CLI. Las 22 formulas viven en data/hooks/hooks_{en,es}.json (una sola fuente de verdad).

QUE SABE Y QUE NO. Medido por el autor sobre 74 ganchos reales: separa ganchos deliberadamente malos de los reales, pero NO
distingue los aciertos de los fallos de un mismo creador. Es un filtro para descartar lo flojo, no una promesa de vistas.

    python hook_craft.py --hook "linea"             # puntaje de un gancho (--lang es para espanol)
    python hook_craft.py --title "titulo" --thumb "TEXTO"
"""
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data" / "hooks"
LANGS = ("en", "es")

STRONG, WORKABLE = 72, 55  # bandas del veredicto
RETRY_BELOW = 40  # solo se pide reescribir cuando el MEJOR candidato esta claramente flojo (el puntaje es un filtro, no un juez fino)
DESKTOP, MOBILE, HARD = 60, 40, 100  # largo de titulo: busqueda de escritorio / feed movil / limite de YouTube
THUMB_MAX_WORDS = 3

_NUM_EN = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
           r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion)")
_NUM_ES = (r"(?:uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|veinte|veinti\w+|"
           r"treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|cien|ciento|mil|mill[oó]n|millones)")

_LEX = {
    "en": {
        "filler": {"basically", "actually", "literally", "just", "really", "very", "so", "kind", "sort", "like", "guys", "hey",
                   "welcome", "today", "video", "subscribe", "channel"},
        "vague": {"amazing", "incredible", "insane", "crazy", "huge", "massive", "game", "changer", "secret", "powerful",
                  "ultimate", "best", "revolutionary", "mind", "blowing", "unbelievable"},
        "concrete": re.compile(
            r"\b(\d[\d,.]*\s?(%|k|m|x|s|h)?|\$\d|\d+\s?(second|minute|hour|day|week|month|year)s?|" + _NUM_EN + r")\b", re.I),
        "you": re.compile(r"\b(you|your|you're|youre|yourself)\b", re.I),
        "stake": re.compile(r"\b(lose|lost|wasting|waste|quit|fail|broke|cost|risk|before|stop|never|die|dying|dead|banned|"
                            r"delayed|cancelled|canceled)\b", re.I),
        "curiosity": re.compile(r"\b(why|how|what|which|until|before|but|nobody|almost|except|reason|actually|reportedly)\b", re.I),
        "closed": re.compile(r"\b(because|so that|which means)\b", re.I),
    },
    "es": {
        "filler": {"básicamente", "basicamente", "literalmente", "realmente", "simplemente", "hola", "bienvenidos", "amigos",
                   "video", "vídeo", "suscríbete", "suscribete", "canal", "pues", "entonces", "bueno", "gente", "hoy"},
        "vague": {"increíble", "increible", "impresionante", "brutal", "enorme", "gigante", "secreto", "poderoso", "definitivo",
                  "mejor", "revolucionario", "alucinante", "espectacular", "tremendo", "épico", "epico", "locura"},
        "concrete": re.compile(
            r"\b(\d[\d.,]*\s?(%|k|m|x|s|h)?|\$\d|\d+\s?(segundo|minuto|hora|día|dia|semana|mes|año|ano)s?|" + _NUM_ES + r")\b",
            re.I),
        "you": re.compile(r"\b(tú|tu|tus|te|ti|vos|usted|contigo|tuyo|tuya)\b", re.I),
        "stake": re.compile(r"\b(perder|perdió|perdio|perdiendo|pierde|desperdici\w+|abandon\w+|fracas\w+|cuesta|costó|costo|"
                            r"riesgo|peligro|nunca|jamás|jamas|morir|murió|moría|quedaban|antes de|dejar de|última|ultima)\b", re.I),
        "curiosity": re.compile(r"\b(por qué|cómo|como|qué|cuál|cual|hasta que|antes de|pero|nadie|casi|excepto|razón|razon|"
                                r"realmente|nunca)\b", re.I),
        "closed": re.compile(r"\b(porque|por eso|lo que significa|así que|asi que)\b", re.I),
    },
}

FIX = {
    "es": {
        "SPECIFICITY": "cambia un adjetivo por un número, un nombre o una fecha",
        "ADDRESS": "di «tú/te/tu» en las primeras seis palabras",
        "STAKES": "nombra qué pierde quien sigue haciéndolo como hasta ahora",
        "CURIOSITY": "corta la mitad de la frase que ya se responde sola",
        "BREVITY": "entre 9 y 24 palabras: léela en voz alta y para donde te quedes sin aire",
    },
    "en": {
        "SPECIFICITY": "swap one adjective for a number, a name or a date",
        "ADDRESS": "say 'you' in the first six words",
        "STAKES": "name what it costs them to keep doing it the current way",
        "CURIOSITY": "cut the half of the sentence that answers itself",
        "BREVITY": "9 to 24 words. Read it out loud and stop where you run out of breath",
    },
}


def _lang(lang: str) -> str:
    return lang if lang in LANGS else "en"


@lru_cache(maxsize=None)
def _data(lang: str) -> dict:
    return json.loads((DATA_DIR / f"hooks_{_lang(lang)}.json").read_text(encoding="utf-8"))


def formulas(lang: str = "en") -> list:
    return _data(lang)["hooks"]


def _words(text: str) -> list:
    return re.findall(r"[a-záéíóúüñ0-9'%$.]+", (text or "").lower())


def _specificity(t: str, lex: dict) -> int:
    w = _words(t)
    if not w:
        return 0
    nums = len(lex["concrete"].findall(t))
    vague = sum(1 for x in w if x in lex["vague"])
    filler = sum(1 for x in w if x in lex["filler"])
    s = 34 + nums * 22 - vague * 16 - filler * 5
    s += min(18, 6 * sum(1 for x in t.split()[1:] if x[:1].isupper()))  # nombre propio que no abre la frase
    return max(0, min(100, s))


def _address(t: str, lex: dict) -> int:
    n = len(lex["you"].findall(t))
    first = 30 if lex["you"].search(" ".join(t.split()[:6])) else 0
    return max(0, min(100, 26 + n * 20 + first))


def _stakes(t: str, lex: dict) -> int:
    n = len(lex["stake"].findall(t))
    return max(0, min(100, 22 + n * 26 + (14 if lex["concrete"].search(t) else 0)))


def _curiosity(t: str, lex: dict) -> int:
    n = len(lex["curiosity"].findall(t))
    q = 18 if t.strip().endswith("?") else 0
    closed = -18 if lex["closed"].search(t) else 0  # un gancho que se resuelve solo ya no tiene hueco
    return max(0, min(100, 24 + n * 17 + q + closed))


def _brevity(t: str, lex: dict) -> int:
    n = len(_words(t))
    if n == 0:
        return 0
    if 9 <= n <= 24:  # banda de un gancho hablado a ~150 ppm dentro de 10 segundos
        return 100
    if n < 9:
        return max(30, 100 - (9 - n) * 11)
    return max(10, 100 - (n - 24) * 7)


_PROPS = (("SPECIFICITY", _specificity), ("ADDRESS", _address), ("STAKES", _stakes),
          ("CURIOSITY", _curiosity), ("BREVITY", _brevity))


def band(verdict: int) -> str:
    return "STRONG" if verdict >= STRONG else "WORKABLE" if verdict >= WORKABLE else "WEAK"


def classify(text: str, lang: str = "en") -> tuple:
    """(nombre de la formula, patrones que calzan). ("", 0) si ninguna: suele ser un resumen, no un gancho."""
    best, hits = None, 0
    for f in formulas(lang):
        n = sum(1 for p in f["match"] if re.search(p, text, re.I))
        if n > hits:
            best, hits = f, n
    return (best["name"] if best else ""), hits


def hook_score(text: str, lang: str = "en", address_counts: bool = False) -> dict:
    """{hook, properties, verdict, band, formula, weakest, fix}. Veredicto = 60 % la media + 40 % la propiedad mas debil:
    un gancho con cuatro propiedades fuertes y una muerta se cae justo en la muerta, y la media lo esconde.
    ADDRESS (hablarle al espectador de tu) se mide siempre pero solo puntua en el veredicto con `address_counts=True`:
    el original es para creadores que hablan a camara; aqui las noticias y las historias van en tercera persona y
    penalizarlas por eso las dejaria a todas en WEAK."""
    lang = _lang(lang)
    lex = _LEX[lang]
    text = (text or "").strip()
    parts = {name: fn(text, lex) for name, fn in _PROPS}
    values = [v for k, v in parts.items() if address_counts or k != "ADDRESS"]
    verdict = round(0.6 * (sum(values) / len(values)) + 0.4 * min(values))
    formula, _ = classify(text, lang)
    weakest = min((k for k in parts if address_counts or k != "ADDRESS"), key=parts.get)
    return {"hook": text, "properties": parts, "verdict": verdict, "band": band(verdict), "formula": formula,
            "weakest": weakest, "fix": FIX[lang][weakest]}


def rank_hooks(options: list, lang: str = "en") -> list:
    """Candidatos puntuados de mayor a menor (a igualdad conserva el orden en que llegaron)."""
    scored = [hook_score(o, lang) for o in options if (o or "").strip()]
    return sorted(scored, key=lambda s: -s["verdict"])


def pick_hook(options: list, lang: str = "en") -> dict:
    """El mejor candidato puntuado, o {} si no hay ninguno."""
    ranked = rank_hooks(options, lang)
    return ranked[0] if ranked else {}


def critique(ranked: list, lang: str = "en") -> str:
    """Texto para reintentar cuando NINGUN candidato llega a RETRY_BELOW ('' si alguno sirve o no hay candidatos)."""
    if not ranked or ranked[0]["verdict"] >= RETRY_BELOW:
        return ""
    best = ranked[0]
    if _lang(lang) == "es":
        return (f"los ganchos propuestos son flojos (el mejor saca {best['verdict']}/100; punto débil {best['weakest']}): "
                f"{best['fix']}. Escribe 5 ganchos nuevos, cada uno con una fórmula distinta")
    return (f"the hook options are weak (best scores {best['verdict']}/100; weakest property {best['weakest']}): "
            f"{best['fix']}. Write 5 new hook options, each using a different formula")


# ---- titulos -------------------------------------------------------------------------------------------------------

_TITLE_VAGUE = {
    "en": {"amazing", "incredible", "insane", "crazy", "huge", "massive", "ultimate", "best", "powerful", "secret",
           "revolutionary", "mindblowing", "epic", "perfect", "complete", "everything"},
    "es": {"increíble", "increible", "impresionante", "brutal", "enorme", "gigante", "secreto", "poderoso", "definitivo",
           "mejor", "revolucionario", "alucinante", "espectacular", "épico", "epico", "perfecto", "completo", "todo"},
}
_TITLE_STOP = {
    "en": {"the", "a", "an", "of", "for", "to", "in", "on", "and", "or", "is", "are", "with", "your", "you", "my", "i", "this",
           "that", "it", "how", "what", "why"},
    "es": {"el", "la", "los", "las", "un", "una", "de", "del", "en", "y", "o", "es", "son", "con", "tu", "te", "mi", "yo",
           "este", "esta", "que", "qué", "como", "cómo", "por", "para", "se", "su", "al", "lo"},
}


def title_lint(title: str, thumb: str = "", lang: str = "en") -> dict:
    """{title, chars, score, issues: [(codigo, mensaje)], good: [mensaje]}. Titulo y miniatura son UNA pareja: si la
    miniatura repite el titulo se desperdicia la mitad de la superficie de clic."""
    lang = _lang(lang)
    t = (title or "").strip()
    n = len(t)
    issues, good = [], []
    if n > HARD:
        issues.append(("length", f"{n} caracteres: el límite de YouTube es {HARD}"))
    elif n > DESKTOP:
        issues.append(("length", f"{n} caracteres: la búsqueda de escritorio corta cerca de {DESKTOP}"))
    else:
        good.append(f"{n} caracteres, dentro del corte de {DESKTOP}")
    if n > MOBILE:
        head = t[:MOBILE].rsplit(" ", 1)[0]
        issues.append(("mobile", f'el feed móvil muestra «{head}…»: revisa que sobreviva el sujeto'))
    caps = [w for w in t.split() if len(w) > 2 and w.isupper() and not w.startswith("#")]
    if len(caps) > 2:
        issues.append(("shouting", f"{len(caps)} palabras en MAYÚSCULAS: dos es el techo antes de parecer spam"))
    vague = [w for w in _words(t) if w in _TITLE_VAGUE[lang]]
    if vague:
        issues.append(("vague", f"{', '.join(sorted(set(vague)))}: cámbialas por un número, un nombre o una fecha"))
    if re.search(r"\d", t) or _LEX[lang]["concrete"].search(t):
        good.append("lleva una cifra concreta")
    else:
        issues.append(("no-number", "sin número, fecha ni nombre: es el arreglo más fiable"))
    if thumb:
        stop = _TITLE_STOP[lang]
        shared = (set(_words(t)) - stop) & (set(_words(thumb)) - stop)
        if shared:
            issues.append(("duplicate", f"la miniatura repite el título en {', '.join(sorted(shared))}"))
        else:
            good.append("título y miniatura dicen cosas distintas")
        if len(_words(thumb)) > THUMB_MAX_WORDS:
            issues.append(("thumb-length", f"{len(_words(thumb))} palabras en la miniatura: el techo es {THUMB_MAX_WORDS}"))
    score = max(0, min(100, 100 - 14 * len(issues) + 4 * len(good)))
    return {"title": t, "chars": n, "score": score, "issues": issues, "good": good}


def trim_title(title: str, limit: int = DESKTOP) -> str:
    """Recorta por palabra a `limit` caracteres conservando el principio (donde va el sujeto), sin puntos suspensivos."""
    t = re.sub(r"\s+", " ", (title or "")).strip()
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0] if " " in t[:limit] else t[:limit]
    return cut.rstrip(" -:|,;·—–")


# ---- bloque de formulas para los prompts ---------------------------------------------------------------------------

def formulas_block(lang: str = "en") -> str:
    """Una linea por formula: nombre, forma y un ejemplo de ESTRUCTURA (el tema del ejemplo no debe copiarse)."""
    lang = _lang(lang)
    lead = "Example of the structure only, never copy its topic" if lang == "en" else "Ejemplo solo de la estructura, no copies su tema"
    lines = []
    for f in formulas(lang):
        lines.append(f"- {f['name']}: {f['shape']} {lead}: «{f['example']}»")
    return "\n".join(lines)


def main(argv: list) -> int:
    lang = argv[argv.index("--lang") + 1] if "--lang" in argv else "en"
    if "--hook" in argv:
        r = hook_score(argv[argv.index("--hook") + 1], lang)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    if "--title" in argv:
        thumb = argv[argv.index("--thumb") + 1] if "--thumb" in argv else ""
        print(json.dumps(title_lint(argv[argv.index("--title") + 1], thumb, lang), ensure_ascii=False, indent=1))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
