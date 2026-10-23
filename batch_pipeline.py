"""Orquestacion del modulo "Generacion en lote": crea proyectos que generan y
publican N videos completos, espaciados en varios dias, sacando guion+prompts
del generador de texto (cadena de LLMs). Independiente de _run_pipeline_job (app.py) -- solo
comparte los locks globales de render/clipgen (un solo render/generacion a la
vez en todo el proceso) y las funciones de publicacion ya existentes, que se
reusan tal cual, nunca se reescriben.

app.py importa este modulo; este modulo NO importa app.py a nivel de modulo
(se generaria un ciclo) -- las pocas cosas que necesita de ahi (los locks
globales y las funciones _record_published_*) se importan de forma diferida,
dentro de cada funcion que las usa.
"""

import hashlib
import json
import logging
import msvcrt
import os
import random
import re
import shutil
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Callable, Optional

import text_provider

SCRIPT_DIR = Path(__file__).parent
_PROMPTS_DIR = SCRIPT_DIR / "prompts"

# Proveedor de texto del lote: FreeLLM endpoint local (text_provider)
TEXT_PROVIDER_CHOICES = ("auto",)

def _text_system_prompt(kind: str) -> str:
    """System prompt autocontenido (prompts/historias_system.md o
    gaming_system.md): el formato de salida que exigen los parsers.
    FileNotFoundError => el eslabon LLM se salta y la cadena falla clara.
    Los guiones largos (PILARES_KINDS) suman la tecnica de retencion de prompts/_pilares_de_valor.md."""
    prompt = (_PROMPTS_DIR / f"{kind}_system.md").read_text(encoding="utf-8")
    if kind in PILARES_KINDS:
        prompt += "\n\n" + (_PROMPTS_DIR / "_pilares_de_valor.md").read_text(encoding="utf-8")
    return prompt

# Guiones largos que llevan "pilares de valor" (prueba tras el gancho + micro-promesas).
# Hoy ninguno: HISTORIAS usa un formato fijo de ~90 s con su propia estructura (prompts/historias_system.md).
PILARES_KINDS: set = set()

TEXT_FORMAT_ATTEMPTS = 3  # reintentos ante respuesta truncada o sin formato
STORY_TEXT_KINDS = {"historias", "macrame", "ninio_selectivo"}  # los demas (posts) no traen bloques Imagen

# Alimento protagonista de cada historia/post de niño selectivo. Sin esto el modelo
# se ancla a los ejemplos del prompt (brocoli) y todo gira alrededor de lo mismo; se
# sortea aqui (no lo decide el modelo) y nunca se repite el de la vez anterior.
NINIO_FOODS = (
    "brócoli", "zanahoria", "calabacín", "espinaca", "zapallo (calabaza)", "tomate", "palta (aguacate)",
    "arroz", "pollo", "pan", "huevo", "fideos", "lentejas", "plátano", "papa", "manzana",
)

_NINIO_KINDS = {"ninio_selectivo", "ninio_post"}
_last_ninio_food: Optional[str] = None


def _pick_ninio_food() -> str:
    global _last_ninio_food
    food = random.choice([f for f in NINIO_FOODS if f != _last_ninio_food])
    _last_ninio_food = food
    return food


def _generate_text_with_chain(
    kind: str,
    page_name: str,
    trigger_message: str,
    story_id: str,
    duration_seconds: Optional[int] = None,
    prefer: str = "",
    attempted: Optional[list] = None,
) -> str:
    """Texto (guion/idea de post) con FreeLLM (text_provider).
    Si falla, lanza excepción (no hay fallback). `attempted` recibe
    el nombre del proveedor intentado (diagnostico/logs)."""
    def _mark(name: str) -> None:
        if attempted is not None:
            attempted.append(name)

    errors = []
    if kind in _NINIO_KINDS:
        trigger_message += (
            f"\n\nAlimento protagonista de esta pieza: {_pick_ninio_food()}. "
            "Es el que el niño rechaza o el que aparece en el plato; no lo cambies por brócoli."
        )

    if text_provider.available_backends():
        _mark("freellm")
        try:
            system = _text_system_prompt(kind)
            # Cada intento usa el siguiente modelo de la cadena: si uno esta sin cuota
            # o se queda razonando, el que sigue lo cubre (en vez de repetir el mismo).
            models = text_provider.model_chain()
            attempts = max(TEXT_FORMAT_ATTEMPTS, len(models))
            feedback = ""  # motivo del rechazo anterior: el siguiente intento lo recibe para corregirlo
            for attempt in range(1, attempts + 1):
                try:
                    text, backend = text_provider.generate_text(
                        system,
                        trigger_message + feedback,
                        max_tokens=16384,  # la salida son ~7-8 mil tokens y el router "auto" razona ~3 mil antes de contestar
                        temperature=0.8,
                        model=models[(attempt - 1) % len(models)],
                    )
                    # Los nichos de historia piden bloques "Imagen N": sin ellos es
                    # razonamiento volcado por el modelo, no una historia.
                    if kind in STORY_TEXT_KINDS and not auto_pipeline.has_image_blocks(text):
                        raise text_provider.TextProviderError(
                            "freellm: la respuesta no trae bloques Imagen/Frase/Prompt (razonamiento o formato roto)")
                    # Todos los videos duran mas de 1:20: un guion fuera de largo o un numero de imagenes
                    # fuera de rango se reintenta ya con el siguiente modelo (no escribe scratch).
                    if kind in auto_pipeline.LONG_KINDS:
                        script = auto_pipeline.extract_script(text)
                        try:
                            if script.strip():
                                if kind == "historias":
                                    auto_pipeline.validate_historia_script(script)
                                else:
                                    auto_pipeline.validate_long_script_length(script)
                            auto_pipeline.validate_image_count(auto_pipeline.count_image_blocks(text))
                        except auto_pipeline.PipelineError as ve:
                            raise text_provider.TextProviderError("freellm: " + str(ve))
                except text_provider.TextProviderError as e:
                    # "timed out"/"fallo tras": el modelo no termino a tiempo una respuesta larga (o la red
                    # fallo en sus reintentos internos): el siguiente modelo de la cadena suele contestar.
                    retryable = any(m in str(e) for m in ("truncada", "bloques Imagen", "formato de historia", "fuera de largo",
                                                 "fuera de rango", "fuera de formato", "rate limit",
                                                 "timed out", "fallo tras", "no disponible en el endpoint"))
                    if not retryable or attempt == attempts:
                        raise
                    logger.warning("batch: %s; reintento %d/%d con otro modelo", e, attempt, attempts)
                    if any(m in str(e) for m in ("fuera de largo", "fuera de rango", "fuera de formato", "formato de historia")):
                        feedback = (f"\n\nTu respuesta anterior fue rechazada: {e}. Reescribe todo corrigiendo exactamente eso "
                                    "y cuenta las palabras del guion antes de responder.")
                    elif "bloques Imagen" in str(e):
                        feedback = ("\n\nTu respuesta anterior fue rechazada: no traia los bloques \"Imagen N / Frase del guion / "
                                    "Prompt\". Responde SOLO con el formato pedido, sin razonamiento, analisis ni explicaciones.")
                    continue
                logger.info("batch: texto generado por %s (story_id=%s)", backend, story_id)
                return text
        except FileNotFoundError as e:
            errors.append(f"text_provider: system prompt {kind} no encontrado")
            logger.warning("batch: %s", errors[-1])
        except text_provider.TextProviderError as e:
            errors.append(str(e))
            logger.warning("batch: text_provider fallo: %s", e)

    raise RuntimeError(
        "Sin proveedor de texto disponible (" + ("; ".join(errors) or "ningun eslabon intentado") + ")"
    )

import auto_pipeline
import video_maker
import slideshow_risk
import facebook_publisher
import instagram_publisher
import youtube_publisher
import feedback_analyzer
import gaming_clip
import gaming_reel
import gaming_news
import gaming_memes
import howbig
import job_store
import meme_maker
import seo_optimizer
from tts_engine import text_to_speech_verified, get_default_voice, BEDTIME_PRESET, NARRATION_CHECK_SUFFIX

# logging.getLogger(__name__) ("batch_pipeline") no tenia ningun handler propio
# ni de un ancestro configurado (el logger "pipeline" de auto_pipeline.py es un
# hermano, no un padre -- los nombres no anidan solo por compartir tema). Sin
# handler, warning()/exception() caian al lastResort de stdlib (stderr, se
# pierde si nadie mira la consola de app.py) -- por eso los reintentos y
# errores reales de _run_stage_with_retry nunca aparecian en logs/pipeline.log
# aunque estuvieran pasando, dando la falsa impresion de un pipeline colgado.
logger = logging.getLogger(__name__)
if not logger.handlers:
    _LOG_DIR = Path(__file__).parent / "logs"
    _LOG_DIR.mkdir(exist_ok=True)
    _handler = logging.FileHandler(_LOG_DIR / "pipeline.log", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

JOB_STORE_NAME = "batch_projects"
TICK_SECONDS = 60
# Separacion minima entre publicaciones de una misma red (mismo rol que
# facebook_publisher.POST_SPACING_SECONDS): si el ultimo publicado en esa red
# quedo mas cerca que esto, se reprograma en vez de publicar ya.
BATCH_MIN_GAP_SECONDS = 3600
# Rango horario permitido para publicaciones del lote -- nunca programar (ni
# dejar programado) fuera de 9am-9pm, sin importar que tan "cerca" quede de
# best_hour por la metrica circular de _compute_schedule.
BATCH_HOUR_START = 9
BATCH_HOUR_END = 21  # inclusive: las 21:00 en punto es el ultimo horario

# How Big Really: horario por fase desde el primer lote de la pagina, en HORA DEL ESTE de EE. UU. (se convierte a la
# hora local dia por dia, ver us_hours_for_date). Dias 1-14: 3 posts al dia (11:00, 15:00, 20:00); desde el dia 15:
# 2 al dia (11:00 y 20:00). Las 20:00 del Este pueden caer pasadas las 21 locales, fuera del rango general 9-21,
# asi que esta pagina usa su propio tope local (HOWBIG_HOUR_END).
HOWBIG_PHASE1_DAYS = 14
HOWBIG_PHASE1_HOURS = [11, 15, 20]
HOWBIG_PHASE2_HOURS = [11, 20]
HOWBIG_HOUR_END = 22
HOWBIG_LAUNCH_STORE = "howbig_launch"

# Las horas de los posts de imagen (*_SLOT_HOURS) estan en HORA DEL ESTE de EE. UU. (publico objetivo) y se convierten
# a la hora local dia por dia (ver us_hours_for_date).
# Posts de imagen gaming (Workflow JugadasEpicasVideojuegos, sec. 4): Facebook
# a las 12:00 y 19:00 (son el scheduled_at del video), Instagram una hora
# antes (11:00 y 18:00, via network_offsets del proyecto).
GAMING_SLOT_HOURS = [12, 19]
GAMING_NETWORK_OFFSETS = {"instagram": -3600}  # segundos respecto de scheduled_at
GAMING_MEME_SLOT_HOURS = [14, 17]  # memes de Memedroid (hora del Este): entre los posts de las 12 y las 19; sin pasar de las 21 locales
# Plantillas de la "semana tipo" (sec. 2 y 4): [slot del mediodia, slot de la
# noche] por dia, lunes=0. Domingo solo trae T1 en el workflow; el segundo
# slot repite T2 (la otra plantilla mas usada).
GAMING_TEMPLATES = {
    "T1": "Dilema Binario",
    "T2": "Situación Relatable 2AM",
    "T3": "Screenshot Tweet / Fake Chat",
    "T4": "Honor Level / Stat Absurdo",
}
GAMING_WEEK = [("T1", "T2"), ("T1", "T4"), ("T2", "T3"), ("T1", "T2"), ("T1", "T4"), ("T2", "T3"), ("T1", "T2")]
# Hashtags por red (sec. 6): Instagram hasta 8, Facebook 2-3.
GAMING_MAX_HASHTAGS = {"instagram": 8, "facebook": 3, "youtube": 5}
# Posts de imagen de "Niño Selectivo, Familia en Paz": un post antes del almuerzo
# (12:00) y otro antes de la cena (19:00), a la misma hora en Facebook e Instagram.
# Plantillas N1-N5 y semana tipo (mediodia, noche) por dia, lunes=0: mito, error, ritual
# y lista de 3 errores rotan al mediodia (3 formatos/semana del plan de mejoras); la noche
# es casi siempre una frase de paz.
NINIO_SLOT_HOURS = [12, 19]
NINIO_TEMPLATES = {
    "N1": "Mito vs Realidad",
    "N2": "El error que empeora todo",
    "N3": "Ritual de 1 minuto",
    "N4": "Frase de paz",
    "N5": "3 errores comunes",
}
NINIO_WEEK = [("N1", "N4"), ("N2", "N4"), ("N3", "N4"), ("N5", "N4"), ("N3", "N4"), ("N2", "N4"), ("N1", "N4")]
# Posts de imagen de "MACRAME CREATIVO": uno al mediodia (12:00) y otro por la tarde (19:00), misma
# hora en Facebook e Instagram. Plantillas M1-M4 y semana tipo (mediodia, tarde) por dia, lunes=0.
# Posts de imagen de "HISTORIAS" (rescates de animales): uno a media mañana y otro a la tarde, igual en
# Facebook e Instagram. Plantillas H1-H4 y semana tipo (mañana, tarde) por dia, lunes=0.
HISTORIAS_SLOT_HOURS = [11, 18]  # media manana y tarde
HISTORIAS_TEMPLATES = {
    "H1": "Mini historia en collage",
    "H2": "El animal habla",
    "H3": "El animal pide",
    "H4": "Foto limpia con caption largo",
}
HISTORIAS_WEEK = [("H1", "H3"), ("H2", "H4"), ("H3", "H2"), ("H1", "H4"), ("H2", "H3"), ("H3", "H1"), ("H4", "H2")]
MACRAME_SLOT_HOURS = [12, 19]
MACRAME_TEMPLATES = {
    "M1": "Pieza terminada del día",
    "M2": "Tip de nudo",
    "M3": "Error común y cómo evitarlo",
    "M4": "Frase creativa",
}
MACRAME_WEEK = [("M1", "M4"), ("M2", "M4"), ("M3", "M4"), ("M1", "M4"), ("M2", "M4"), ("M3", "M4"), ("M1", "M4")]
# Tipos de lote de la pagina gaming: mismos horarios. YouTube solo para los
# videos 9:16 (clip de Medal y reel de noticias con motion graphics -> Short), nunca para la imagen.
GAMING_TYPES = {"gaming_image", "gaming_clip", "gaming_reel", "gaming_meme"}
GAMING_VIDEO_TYPES = ("gaming_clip", "gaming_reel")
# Lotes mixtos: alternan un video y un post de imagen del mismo nicho (video, post, video, post...);
# el reparto sale solo del total (10 -> 5 y 5; 11 -> 6 videos y 5 posts). Cada item guarda su
# `item_type` y se genera/publica como ese tipo; los horarios son los del post de imagen del nicho.
MIXED_TYPES = {
    "mix_ninio": ("video", "ninio_image"),
    "mix_gaming": ("gaming_clip", "gaming_image"),
    "mix_macrame": ("video", "macrame_image"),
    "mix_historias": ("video", "historias_image"),
}


def item_type(project: dict, video: dict) -> str:
    """Tipo con que se genera y publica un item: el propio en un lote mixto, el del lote si no."""
    return video.get("item_type") or project.get("type", "video")


def next_mixed_kind(mix_type: str, advance: bool = False) -> str:
    """Tipo que toca generar ahora en "Generar video (automatico)" para un mixto: alterna video/post
    como un lote (primero el video) con un contador por mixto que se guarda en disco. Sin `advance`
    solo consulta; con `advance` registra que esa publicacion se va a generar y pasa a la siguiente."""
    pair = MIXED_TYPES[mix_type]
    with _lock:
        store = job_store.load("mix_cursor")
        n = int(store.get(mix_type, 0))
        if advance:
            store[mix_type] = n + 1
            job_store.save("mix_cursor", store)
    return pair[n % 2]
# Canal de YouTube de videojuegos (clave de youtube_publisher.list_channels():
# YT_CHANNEL_<N>_*; su token es youtube_token_<N>.json). Separado del canal de
# historias para no mezclar contenido.
GAMING_YT_CHANNEL = os.environ.get("YT_GAMING_CHANNEL") or "3"

# Reintentos por etapa de _generate_batch_video. Etapas que dependen de
# agent-browser (guion/imagenes) fallan seguido por timeouts de red
# transitorios (os error 10060/10061, daemon colgado) -- se reintentan mas
# veces y con un reset duro de la sesion de por medio. Etapas locales
# (audio/render) no tienen browser de por medio, asi que alcanza con un
# reintento simple por si hay un hiccup de IO/ffmpeg.
BROWSER_STAGE_RETRY_ATTEMPTS = 3
LOCAL_STAGE_RETRY_ATTEMPTS = 2
STAGE_RETRY_BACKOFF_SECONDS = 3

# Reintentos automaticos de generacion/publicacion a nivel de video del lote
# (distinto de BROWSER_STAGE_RETRY_ATTEMPTS/LOCAL_STAGE_RETRY_ATTEMPTS, que son
# reintentos dentro de una misma corrida de _generate_batch_video). Este limite
# cubre corridas completas: cuantas veces un video puede volver solo a
# pending/ready antes de quedar en error/publish_error terminal.
MAX_AUTO_RETRIES = 3

_TRANSIENT_ERROR_MARKERS = (
    "10060", "10061", "no respondio a tiempo", "timeout esperando",
    "no pude descargar el clip", "no pude descargar la imagen",
)

# Auto-sanado de videos que agotaron MAX_AUTO_RETRIES y quedaron en "error":
# las caidas de WhatsApp duran horas (17/9: ~3h de timeouts) y las 3
# corridas se gastan en minutos, asi que sin esto el video queda muerto hasta
# que alguien aprieta "reintentar". Cada ciclo espera el doble del anterior
# (30min, 1h, 2h, 4h) para no martillar un servicio caido.
HEAL_COOLDOWN_SECONDS = 1800
MAX_HEAL_CYCLES = 4

# Cuota de Gemini agotada (gaming_vision.QuotaExceededError, propagada como
# ClipError con este texto): el free tier resetea por DIA, no en minutos, asi
# que usa su propio cooldown mucho mas largo que HEAL_COOLDOWN_SECONDS -- 
# reintentar cada 30min contra una cuota diaria agotada solo gasta ciclos de
# heal en vano y nunca da tiempo a que resetee.
GEMINI_QUOTA_MARKER = "cuota de gemini agotada"
GEMINI_QUOTA_COOLDOWN_SECONDS = 6 * 3600  # 6h, 12h, 24h, 48h con MAX_HEAL_CYCLES=4

# Errores que reintentar NO arregla porque hace falta una accion humana (iniciar sesion, configurar una
# key, nombre de proyecto mal puesto, archivo o voz que ya no existen). Todo otro error de generacion se
# considera transitorio (red, proveedor, un guion/audio/imagen mal salido) y se sana solo con cooldown.
_PERMANENT_ERROR_MARKERS = (
    "no encontre el proyecto",  # nombre mal puesto
    "pide iniciar sesión", "pide iniciar sesion",  # Flow/Google: hay que entrar a mano
    "falta una key",  # sin key de vision/proveedor en el .env
    "system prompt",  # prompts/<kind>_system.md no encontrado (configuracion)
    "no existe el archivo", "ya no existe",  # archivo borrado / voz clonada eliminada
    "wangp no está instalado", "wangp no esta instalado",  # proveedor local sin instalar: hay que instalarlo
)

# Sanado de videos cuya PUBLICACION agoto MAX_AUTO_RETRIES (publish_error): una caida de internet, de Meta o de
# YouTube dura mas de los ~3 min que gastan esos intentos. Espera creciente (15, 30, 60, 120 min). Los errores
# de autenticacion (token vencido, cuenta) no se sanan solos: hace falta renovar el token.
PUBLISH_HEAL_COOLDOWN_SECONDS = 900
MAX_PUBLISH_HEAL_CYCLES = 4

# Perfil de proyecto para historias de rescate animal (Manual maestro v3.2):
# activa el filtro de vocabulario del guion, copy por red, rotulo de IA,
# hook en pantalla, horario 20:00 sin lunes y anti-fatiga. Se guarda en
# video_settings["copy_profile"] para no afectar a los demas proyectos.
RESCUE_PROFILE = "rescate_animal"
RESCUE_PAGE_NAME = "HISTORIAS"  # el perfil se activa solo en esta pagina (con YouTube)
RESCUE_HISTORY_LIMIT = 8
RESCUE_AI_LABEL = "Historia recreada con IA"
_HOOK_TEXT_LINE = re.compile(r"^[ \t]*HOOK_TEXT:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _is_rescue_project(project: dict) -> bool:
    return (project.get("video_settings") or {}).get("copy_profile") == RESCUE_PROFILE


def _split_hook_text(story_text: str) -> tuple:
    """Separa la linea `HOOK_TEXT: ...` (texto corto para la pantalla) del
    resto de la respuesta del modelo. Se quita SIEMPRE del texto: si quedara,
    el parser la metería dentro del último prompt de imagen o de la narración."""
    match = _HOOK_TEXT_LINE.search(story_text)
    if not match:
        return "", story_text
    hook = match.group(1).strip().strip("«»\"“”*")
    return hook, _HOOK_TEXT_LINE.sub("", story_text, count=1)


def _rescue_history_block() -> str:
    """Anti-fatiga (§9): lista los ganchos recientes de los proyectos de
    rescate para que el modelo no repita animal/conflicto/final consecutivos."""
    hooks, scripts = [], []
    for project in _load().values():
        if not _is_rescue_project(project) and _project_page_name(project).strip().upper() != RESCUE_PAGE_NAME:
            continue
        for v in project.get("videos", []):
            script = v.get("script_text", "")
            sentences = seo_optimizer._sentences(script)
            if sentences:
                hooks.append((v.get("scheduled_at", ""), seo_optimizer._clip_at_word(sentences[0], 120)))
                scripts.append((v.get("scheduled_at", ""), script))
    recent = [h for _, h in sorted(hooks)][-RESCUE_HISTORY_LIMIT:]
    if not recent:
        return ""
    lines = "\n".join(f"- {h}" for h in recent)
    names = _used_names([t for _, t in sorted(scripts)][-RESCUE_HISTORY_LIMIT * 3:])
    banned = (
        "\n\nNombres y lugares ya usados, PROHIBIDO repetirlos (ni para el animal ni para las personas): "
        f"{', '.join(names)}."
    ) if names else ""
    return (
        "\n\nNo repitas el animal, el conflicto, el escenario, el pais, el tipo de final ni el "
        "nombre del rescatista (crea personaje y lugar nuevos cada vez) "
        f"de estas historias recientes:\n{lines}" + banned
    )


_NAME_WORD = re.compile(r"(?<![.!?¿¡\"«]\s)(?<!^)\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\b")


def _used_names(scripts: list) -> list:
    """Nombres propios (animales, personas, lugares) que ya aparecen en guiones anteriores."""
    names = []
    for script in scripts:
        for n in _NAME_WORD.findall(re.sub(r"\[[^\]]*\]", "", script)):
            if n not in names:
                names.append(n)
    return names


# Patrones de historia validados (Workflow V6, posts reales de la pagina).
# El peso es el promedio de la mezcla recomendada para FB e IG (50/50), porque
# el mismo video sale a ambas redes. Elegir el patron en codigo (y no dejarlo a
# el codigo) garantiza la mezcla y permite no repetir el de los ultimos videos.
# "injusticia_visual" no nombra maltrato: la instruccion del Project prohibe
# abuso/violencia y Meta AI rechaza esos prompts.
STORY_PATTERNS = {
    "separacion": (12, "Separacion + huelga: el animal deja de comer o de moverse tras separarlo de su companero o de su dueno; todos creen que es enfermedad y es duelo."),
    "supervivencia": (7, "Supervivencia imposible: el animal aparece solo en un lugar imposible (el mar, un techo, una isla, una montana nevada); como llego ahi y quien lo rescata."),
    "cuenta_regresiva": (6, "Cuenta regresiva: le quedan pocas horas o dias antes de un desalojo, un cierre o un traslado; el rescatista llega en el ultimo momento."),
    "ladron": (16, "Ladron heroe: todos lo odian por robar un objeto (mantas, calcetines, zapatos, comida); en realidad se lo lleva a alguien que lo necesita."),
    "guardian": (8, "Guardian sagrado: lo quieren echar de un lugar (cementerio, iglesia, hospital); esperaba o cuidaba a alguien."),
    "injusticia_visual": (15, "Injusticia visual: el animal aparece con algo raro que la gente ve y ignora (cubierto de pintura, con una cinta, con una marca); no digas quien ni por que, ni describas crueldad; el rescatista lo limpia y vuelve su aspecto natural."),
    "perdida_reencuentro": (8, "Hermanos separados: dos animales crecieron juntos; uno \"no pudo quedarse\" en el lugar y el otro lo espera cada dia sin comer bien; meses despues el rescatista los reune y el final es agridulce pero esperanzador. Nunca nombres la muerte ni uses palabras de dano."),
    "promesa_cumplida": (8, "Promesa cumplida: al inicio el narrador promete algo concreto y pequeno (un dia en la playa, correr en un campo, dormir en una cama) a un animal muy debil; la recuperacion se narra con avances graduales naturales (sin contar dia por dia) y la ultima escena cumple la promesa."),
    "condenado_vuelve": (5, "El que nadie esperaba: todos decian que ya no habia esperanza para el animal; el rescatista no se rindio; el final salta anos adelante y lo muestra sano y feliz. Nunca uses palabras como dormir, sacrificar o eutanasia."),
    "espera_diaria": (15, "Espera diaria: cada dia a la misma hora espera en el mismo lugar (una parada, una puerta, una ventana); esperaba a alguien; final de reencuentro o nuevo hogar."),
    "mascota_perdida": (18, "Mascota perdida: el animal espera cada dia en un mismo lugar (un banco, una puerta, una parada) despues de que su dueno se mudo o lo perdio; alguien del lugar nota que no es callejero (un cartel, un collar, una costumbre) y encuentra a su persona; final de reencuentro."),
}
STORY_PATTERN_COOLDOWN = 2  # no repite un patron usado en los ultimos N videos

RESCUE_STORY_CRAFT = (
    "\n\nESTRUCTURA (obligatoria, 5 pasos, video de unos 90 segundos): 1) Gancho de espera: la primera frase "
    "lleva un numero o plazo concreto y una consecuencia (\"Nuri esperó siete meses en el mismo banco. Todos "
    "pasaban... nadie la veía.\"). 2) Conflicto y adversidad: que cambio y como lo soporta con el tiempo y el "
    "clima (con frio, con lluvia, semanas mas tarde); lugar especifico y DIFERENTE en cada historia, "
    "en EE. UU. (rota estado y tipo de lugar: un pueblo de Texas, un puerto de Florida, un barrio de Nueva York, "
    "un rancho de California, etc.; NUNCA siempre el mismo estado) y el juicio de la gente (\"todos pensaban "
    "que era...\"). 3) Giro con personaje bondadoso: alguien (panadero, vecina, guardia, niño, cartero) nota lo "
    "que los demas ignoraban (un cartel viejo, un collar, una costumbre diaria) y entiende quien es el animal "
    "o que espera: no era lo que todos creian. 4) Reencuentro o rescate emotivo con una accion concreta y una "
    "transformacion visible (su persona lo reconoce y el animal la reconoce al instante, o llega a su hogar); "
    "final esperanzador. 5) Cierre: una frase de esperanza con el nombre del animal y la invitacion a dejar un "
    "corazón (\"Hoy Nuri ya no espera sola... deja un corazón para Nuri\"); escribe la palabra corazón, nunca el "
    "emoji, y no pidas una palabra clave ni prometas Parte 2. El guion de narracion tiene de 270 a 290 "
    "palabras (unas 24 a 28 frases: desarrolla el conflicto con varias adversidades y el reencuentro con una escena concreta), escritas con los acentos y la ñ correctos (esperó, veía, niño). El personaje bondadoso o rescatista es DIFERENTE en cada historia: cambia nombre, edad, genero y "
    "ocupacion (nunca reuses el nombre de una historia anterior). Incluye su ficha completa en PERSONAJES y "
    "usa su etiqueta en los prompts. La ultima imagen muestra la transformacion (feliz en su hogar o ya "
    "reunido). Si la historia no tiene una transformacion visible o un misterio resuelto, cambia la idea."
)


def _uses_story_pattern(project: dict) -> bool:
    """Los lotes de la pagina HISTORIAS (con o sin perfil de rescate/YouTube) llevan patron de historia."""
    return _is_rescue_project(project) or _project_page_name(project).strip().upper() == RESCUE_PAGE_NAME


def _story_trigger_addition(project: dict, pattern: "str | None") -> str:
    """Lo que el lote agrega al mensaje de HISTORIAS: estructura + patron + anti-fatiga."""
    if not pattern or not _uses_story_pattern(project):
        return ""
    return rescue_story_block(pattern) + _rescue_history_block()


def _pick_story_pattern(recent: list) -> str:
    """Patron ponderado que no este entre los `recent` (mas nuevo al final)."""
    avoid = set(recent[-STORY_PATTERN_COOLDOWN:])
    keys = [k for k in STORY_PATTERNS if k not in avoid] or list(STORY_PATTERNS)
    return random.choices(keys, weights=[STORY_PATTERNS[k][0] for k in keys])[0]


def _recent_story_patterns(projects: dict) -> list:
    """Patrones de los ultimos videos de rescate (o de la pagina HISTORIAS), del mas viejo al mas nuevo."""
    seen = []
    for project in projects.values():
        if _uses_story_pattern(project):
            seen += [(v.get("scheduled_at", ""), v["story_pattern"])
                     for v in project.get("videos", []) if v.get("story_pattern")]
    return [pattern for _, pattern in sorted(seen)]


def rescue_story_block(pattern: str) -> str:
    """Pedido de estructura + patron para el mensaje al generador de texto (historias de
    rescate). Va en el mensaje y no en la instruccion del Project por el tope
    de 1000 caracteres de esta (ver auto_pipeline.CHARACTER_SHEET_REQUEST)."""
    return f"{RESCUE_STORY_CRAFT}\n\nPATRON de esta historia: {STORY_PATTERNS[pattern][1]}"


def _is_healable_error(message: "str | None") -> bool:
    """True si reintentar puede arreglar el error: cualquiera salvo los que piden una accion humana."""
    msg = (message or "").strip().lower()
    return bool(msg) and not any(m in msg for m in _PERMANENT_ERROR_MARKERS)


def _reset_session(session: str) -> None:
    """hard_reset_browser_session + log del resultado (antes se descartaba, y
    nunca se sabia si la sesion quedo sin login tras el reset)."""
    result = auto_pipeline.hard_reset_browser_session(session)
    status = result.get("status", {})
    log = logger.info if result.get("ok") and status.get("state") == "ok" else logger.warning
    log(
        "batch: reset de sesion %s -> estado=%s (%s) daemon_matado=%s chromes_huerfanos=%s",
        session, status.get("state"), status.get("message"),
        result.get("hard_killed"), result.get("orphans_killed"),
    )


def _is_transient_error(exc: Exception) -> bool:
    """True si el error pinta como un problema de red/daemon transitorio
    (reintentar tiene sentido) en vez de un bug real de parseo/formato
    (reintentar el mismo texto mal formado no arregla nada)."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_ERROR_MARKERS)


def _run_stage_with_retry(fn, *, attempts: int, on_retry=None, stage_label: str):
    """Corre `fn()` reintentando ante errores transitorios hasta `attempts`
    veces en total. `on_retry(attempt)` se llama antes de cada reintento (por
    ejemplo, para resetear la sesion de browser). Errores no transitorios, o
    el ultimo intento agotado, se relanzan tal cual (con el conteo de
    intentos agregado al mensaje para que quede claro en el JSON de error)."""
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            is_last = attempt == attempts
            if is_last or not _is_transient_error(e):
                break
            logger.warning(
                "batch: etapa %s fallo (intento %d/%d), reintentando: %s",
                stage_label, attempt, attempts, e,
            )
            if on_retry:
                on_retry(attempt)
            time.sleep(STAGE_RETRY_BACKOFF_SECONDS)
    raise type(last_exc)(f"tras {attempt} intento(s): {last_exc}") from last_exc

_lock = threading.Lock()  # protege el read-modify-write de batch_projects.json
# Serializa las publicaciones reales (upload + gap-check + registro) de todo
# el modulo -- nunca hay 2 subidas en simultaneo, asi _gap_ok()/el next_slot
# de facebook_publisher siempre ven el estado recien actualizado antes de
# decidir la proxima publicacion (evita el choque de horario en Facebook).
_publish_lock = threading.Lock()


def _load() -> dict:
    return job_store.load(JOB_STORE_NAME)


def _save(projects: dict) -> None:
    job_store.save(JOB_STORE_NAME, projects)


def _read_json_list(path: Path) -> list:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        return []


def _best_hour_for_networks(networks: dict) -> Optional[int]:
    """Mejor horario ya detectado por feedback_analyzer.best_posting_hour()
    combinando el historial de las redes activas del proyecto. Usa las vistas
    ya cacheadas en los *_published.json (se llenan cuando se abre la pestaña
    de Analitica) -- si no hay muestra suficiente, devuelve None y el
    calendario cae a los DAYPARTS fijos."""
    import app
    rows = []
    if networks.get("youtube"):
        rows += _read_json_list(app._PUBLISHED_VIDEOS_PATH)
    if networks.get("facebook"):
        rows += _read_json_list(app._PUBLISHED_FB_PATH)
    if networks.get("instagram"):
        rows += _read_json_list(app._PUBLISHED_IG_PATH)
    result = feedback_analyzer.best_posting_hour(rows)
    return None if result.get("insufficient_data") else result.get("best_hour")


def _into_publish_window(dt: datetime, hour_end: int = BATCH_HOUR_END) -> datetime:
    """Lleva `dt` al rango BATCH_HOUR_START-`hour_end` (por defecto BATCH_HOUR_END): antes de abrir
    -> hoy a la apertura; despues de cerrar -> manana a la apertura."""
    if dt.hour < BATCH_HOUR_START:
        return dt.replace(hour=BATCH_HOUR_START, minute=0, second=0, microsecond=0)
    if dt.hour > hour_end or (dt.hour == hour_end and (dt.minute or dt.second)):
        return (dt + timedelta(days=1)).replace(hour=BATCH_HOUR_START, minute=0, second=0, microsecond=0)
    return dt


def _next_free_reschedule_slot(projects: dict, exclude: tuple, base_dt: datetime,
                               hour_end: int = BATCH_HOUR_END) -> datetime:
    """Encuentra un scheduled_at libre para un reschedule por falta de gap,
    escalonandolo respecto de TODOS los demas videos "ready"/"publishing"
    de TODOS los proyectos (no solo el actual) que ya apuntan a >= base_dt.

    Sin esto, cuando el scheduler reintenta en el mismo tick varios videos
    que fallan el chequeo de gap de Instagram (_gap_ok), cada uno calcula
    "ahora + 1h" de forma independiente y todos quedan con el mismo
    scheduled_at (solo distinguido por microsegundos) -- ese mismo campo es
    el que Facebook/YouTube usan para su scheduling nativo, asi que todos
    terminan publicandose casi en el mismo minuto en vez de espaciados.
    Ver conversacion 2026-09-23: 3 videos de HISTORIAS salieron con ~1 min
    de diferencia por este bug.
    """
    taken = []
    for pid, project in projects.items():
        for i, v in enumerate(project.get("videos", [])):
            if (pid, i) == exclude:
                continue
            if v.get("status") not in ("ready", "publishing"):
                continue
            try:
                dt = datetime.fromisoformat(v["scheduled_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if dt >= base_dt - timedelta(seconds=BATCH_MIN_GAP_SECONDS):
                taken.append(dt)
    slot = _into_publish_window(base_dt, hour_end)
    # Mientras el candidato caiga a menos de BATCH_MIN_GAP_SECONDS de algun
    # slot ya ocupado, lo empuja justo despues de ese slot y lo vuelve a
    # encuadrar en la ventana horaria (empujar puede cruzar las 20h, o el
    # propio encuadre a "manana 9am" puede generar una NUEVA colision con
    # otro slot ya empujado a esa misma hora) -- repite hasta estabilizar.
    changed = True
    while changed:
        changed = False
        for dt in taken:
            if abs((slot - dt).total_seconds()) < BATCH_MIN_GAP_SECONDS:
                slot = _into_publish_window(dt + timedelta(seconds=BATCH_MIN_GAP_SECONDS), hour_end)
                changed = True
    return slot


def howbig_hours_for_date(day, launch) -> list:
    """Horas LOCALES de publicacion de How Big Really para `day`: fase 1 (3 al dia) los primeros
    HOWBIG_PHASE1_DAYS dias desde `launch` (fecha del primer lote de la pagina), despues fase 2 (2 al dia)."""
    et_hours = HOWBIG_PHASE1_HOURS if (day - launch).days < HOWBIG_PHASE1_DAYS else HOWBIG_PHASE2_HOURS
    return us_hours_for_date(day, et_hours, HOWBIG_HOUR_END)


def _howbig_launch_date():
    """Fecha del primer lote de How Big Really: se guarda (hoy) la primera vez y de ahi en mas es fija.
    Llamar bajo `_lock` para que dos lotes simultaneos no la pisen."""
    saved = (job_store.load(HOWBIG_LAUNCH_STORE) or {}).get("date")
    try:
        return datetime.fromisoformat(str(saved)).date()
    except (TypeError, ValueError):
        pass
    today = datetime.now().date()
    job_store.save(HOWBIG_LAUNCH_STORE, {"date": today.isoformat()})
    return today


def _free_slot(desired: datetime, taken: list, rescue: bool = False, hour_end: int = BATCH_HOUR_END) -> datetime:
    """Primer horario libre a partir de `desired`, dentro de 9:00-`hour_end`:00 (por defecto 21:00) y a no menos de
    BATCH_MIN_GAP_SECONDS de cualquier publicacion ya hecha o programada (`taken`). Ese dia se prueba
    primero la hora pedida y despues las demas horas del rango, de la mas cercana a la mas lejana; si no
    queda ninguna, los dias siguientes. Nunca antes de ahora + el adelanto minimo de programacion."""
    earliest = datetime.now() + NATIVE_SCHEDULE_MIN_LEAD
    desired = max(desired, earliest.replace(minute=0, second=0, microsecond=0))
    hours = sorted(range(BATCH_HOUR_START, hour_end + 1), key=lambda h: (abs(h - desired.hour), h))
    for day in range(90):
        date = (desired + timedelta(days=day)).date()
        if rescue and date.weekday() == 0:  # el perfil rescate no publica los lunes
            continue
        for hour in hours:
            slot = datetime(date.year, date.month, date.day, hour)
            if slot >= earliest and all(abs((slot - t).total_seconds()) >= BATCH_MIN_GAP_SECONDS for t in taken):
                return slot
    return desired


def existing_slots(page_name: str, networks: dict, exclude_project: Optional[str] = None) -> list:
    """Horarios ya ocupados de la pagina: los items de otros lotes de la misma pagina (todo lo que no
    fallo) y lo que las redes ya tienen publicado o programado (Facebook de la pagina, YouTube del canal).
    Una red que no responde se ignora: se sigue con lo que se sabe localmente."""
    taken = []
    for pid, project in _load().items():
        if pid == exclude_project or _project_page_name(project) != page_name:
            continue
        for v in project.get("videos", []):
            if v.get("status") in ("error", "removed"):
                continue
            try:
                taken.append(datetime.fromisoformat(v["scheduled_at"]))
            except (KeyError, TypeError, ValueError):
                continue
    fb = networks.get("facebook")
    if fb:
        try:
            taken += facebook_publisher.list_post_times(fb.get("page_id")) or []
        except Exception:
            logger.warning("batch: no se pudo consultar lo programado en Facebook", exc_info=True)
    if networks.get("youtube"):
        try:
            channel = GAMING_YT_CHANNEL if "gaming" in page_name.lower() or "jugadas" in page_name.lower() else None
            taken += youtube_publisher.list_scheduled(channel) or []
        except Exception:
            logger.warning("batch: no se pudo consultar lo programado en YouTube", exc_info=True)
    return taken


# Publico objetivo: hispanos de EE. UU. Dos franjas (mediodia y noche, hora del Este) y despues horas extra
# para per_day > 2. Se convierten a la hora local del sistema dia por dia (los horarios de verano de EE. UU. y de
# Chile cambian en fechas distintas) y se descartan las que quedan fuera de BATCH_HOUR_START-BATCH_HOUR_END.
US_AUDIENCE_TZ = "America/New_York"
US_SLOT_HOURS_ET = (12, 19, 15, 17, 10, 21)


def us_hours_for_date(day: date, et_hours=US_SLOT_HOURS_ET, hour_end: int = BATCH_HOUR_END) -> list:
    """Horas locales (en el orden de `et_hours`) que corresponden a esas horas del Este de EE. UU. ese dia; se
    descartan las que caen fuera de BATCH_HOUR_START-`hour_end` o en otra fecha."""
    tz = ZoneInfo(US_AUDIENCE_TZ)
    hours = []
    for et_hour in et_hours:
        local = datetime(day.year, day.month, day.day, et_hour, tzinfo=tz).astimezone().replace(tzinfo=None)
        if local.date() == day and BATCH_HOUR_START <= local.hour <= hour_end:
            hours.append(local.hour)
    return hours


def _compute_schedule(total: int, per_day: int, best_hour: Optional[int], rescue: bool = False,
                      fixed_hours: Optional[list] = None, skip_days: int = 0,
                      taken: Optional[list] = None, hours_for_date: Optional[Callable] = None,
                      hour_end: int = BATCH_HOUR_END, us_audience: bool = False,
                      et_hours: Optional[list] = None) -> list:
    """Reparte `total` publicaciones en dias de `per_day`, usando los
    DAYPARTS fijos de feedback_analyzer como horarios del dia (filtrados a
    BATCH_HOUR_START-BATCH_HOUR_END -- nunca se publica en la madrugada,
    aunque quede "circularmente cerca" de best_hour), ordenados empezando
    por el mas cercano a `best_hour` (o el orden fijo si no hay dato). Si
    `per_day` supera la cantidad de dayparts filtrados, agrega horas extra
    1h despues de la ultima, recortando (wrap) a BATCH_HOUR_START al llegar
    a BATCH_HOUR_END para que la extension tampoco se escape del rango.
    Con `rescue` (Manual v3.2 §8) se publica en el bloque 20:00 -> 16:00 y
    nunca en lunes (el dia de menor alcance del historico de la pagina).
    Con `fixed_hours` se usan esas horas tal cual, en ese orden (posts gaming).
    Con `skip_days` se agregan dias vacios entre fechas de publicacion
    (skip_days=1 = publicar cada 2 dias).
    Con `taken` (horarios ya ocupados de la pagina, ver existing_slots) cada publicacion se corre al
    primer horario libre del rango 9-21 h (ver _free_slot) para no pisar ni duplicar las existentes.
    Con `hours_for_date` (fecha -> lista de horas, p. ej. howbig_hours_for_date) el reparto es por calendario:
    desde hoy, cada dia usa sus propias horas en orden, saltando las que ya pasaron (antes de ahora + el
    adelanto minimo) en vez de correr el lote, y sin pasar del tope del dia (cantidad de horas de ese dia menos lo
    que `taken` ya tiene en esa fecha; un dia lleno se salta, y las horas a menos de BATCH_MIN_GAP_SECONDS de un
    horario ocupado no se usan); `per_day`/`skip_days`/`fixed_hours` no se usan. `hour_end` es
    el tope horario de _free_slot (How Big Really publica hasta las 22:00).
    Con `us_audience` las horas del dia son las franjas del Este de EE. UU. (`et_hours`, por defecto
    US_SLOT_HOURS_ET; ver us_hours_for_date) en vez de los DAYPARTS/best_hour/rescate/fixed_hours.
    Devuelve `total` timestamps ISO."""
    if hours_for_date is not None:
        now = datetime.now()
        earliest = now + NATIVE_SCHEDULE_MIN_LEAD
        occupied = taken if taken is not None else []
        schedule = []
        for offset in range(400):
            if len(schedule) >= total:
                break
            day_date = (now + timedelta(days=offset)).date()
            day_hours = hours_for_date(day_date)
            # Tope del dia: la fase manda cuantos posts caben por fecha, contando lo ya ocupado (otros lotes,
            # posts externos). Un dia lleno se salta entero; no se reubican los horarios a otras horas.
            capacity = max(0, len(day_hours) - sum(1 for t in occupied if t.date() == day_date))
            chosen = []
            for hour in day_hours:
                if len(chosen) >= capacity or len(schedule) + len(chosen) >= total:
                    break
                slot = datetime(day_date.year, day_date.month, day_date.day, hour % 24)
                if slot < earliest:
                    continue  # ya paso (o no da el adelanto minimo): se salta, no se corre el lote
                if any(abs((slot - t).total_seconds()) < BATCH_MIN_GAP_SECONDS for t in occupied):
                    continue
                chosen.append(slot)
            for when in chosen:
                if taken is not None:
                    when = _free_slot(when, occupied, rescue=rescue, hour_end=hour_end)  # red de seguridad
                    occupied.append(when)
                schedule.append(when.isoformat())
        return schedule
    base_hours = [h for _, _, h in feedback_analyzer.DAYPARTS if BATCH_HOUR_START <= h <= BATCH_HOUR_END]
    if fixed_hours:
        base_hours = list(fixed_hours)
    elif rescue:
        base_hours = [20, 19, 18, 17, 16]
    elif best_hour is not None:
        base_hours = sorted(base_hours, key=lambda h: min(abs(h - best_hour), 24 - abs(h - best_hour)))

    def hours_of_day(day_date: date) -> list:
        hours = (us_hours_for_date(day_date, et_hours or US_SLOT_HOURS_ET) if us_audience else []) or list(base_hours)
        while len(hours) < per_day:
            next_hour = hours[-1] + 1
            if next_hour > BATCH_HOUR_END:
                next_hour = BATCH_HOUR_START
            hours.append(next_hour)
        return hours

    now = datetime.now()
    days_needed = -(-total // per_day)
    publish_days = []
    offset = 0
    day_step = skip_days + 1  # skip_days=1 -> cada 2 dias
    while len(publish_days) < days_needed:
        day_date = (now + timedelta(days=offset * day_step)).date()
        offset += 1
        if rescue and day_date.weekday() == 0:  # lunes
            continue
        publish_days.append(day_date)

    desired = []
    for i in range(total):
        day_date = publish_days[i // per_day]
        hour = hours_of_day(day_date)[i % per_day] % 24
        desired.append(datetime(day_date.year, day_date.month, day_date.day, hour))
    if taken is None:
        return [d.isoformat() for d in desired]
    # Si el primer horario del lote ya paso, todo el lote se corre de a dias enteros (misma hora, mismo
    # reparto por dia) en vez de apilar los del dia de hoy encima de los de manana.
    earliest = datetime.now() + NATIVE_SCHEDULE_MIN_LEAD
    while desired and min(desired) < earliest:
        desired = [d + timedelta(days=1) for d in desired]
    schedule = []
    for when in desired:
        when = _free_slot(when, taken, rescue=rescue)
        taken.append(when)  # las siguientes del mismo lote tampoco pueden caer encima
        schedule.append(when.isoformat())
    return schedule


def _project_page_name(project: dict) -> str:
    """Nombre de pagina del lote. Los lotes creados antes del desmontaje de
    Qwen lo guardaban como `qwen_project`: se lee como fallback para no
    romper reanudaciones/conteos de lotes ya en el job_store."""
    return project.get("page_name") or project.get("qwen_project") or ""


def _text_kind_for_page(page_name: str) -> str:
    """System prompt (kind) de generacion de texto segun la pagina del lote.
    Cada nicho tiene su propio prompts/<kind>_system.md; default: historias."""
    name = (page_name or "").strip().upper()
    if name == "MACRAME CREATIVO":
        return "macrame"
    if "SELECTIVO" in name:
        return "ninio_selectivo"
    return "historias"


# Anuncios de ebook (~90 s, estilo testimonio): paginas cuyo video es un anuncio con largo fijo, cierre
# "Comenta EBOOK", fichas fijas (personajes + set) y, al final, un celular con la portada del ebook.
AD_KINDS = {
    "ninio_selectivo": {"cover": SCRIPT_DIR / "assets" / "ninio" / "ebook_cover.jpg", "setting": "Cocina"},
    "macrame": {"cover": SCRIPT_DIR / "assets" / "macrame" / "ebook_cover.jpg", "setting": "Escena", "subjects": ("Pieza",)},
}
NINIO_EBOOK_COVER = AD_KINDS["ninio_selectivo"]["cover"]


def ad_kind_for_page(page_name: str) -> Optional[str]:
    """"ninio_selectivo" / "macrame" si el video de la pagina es un anuncio de ebook, o None."""
    kind = _text_kind_for_page(page_name)
    return kind if kind in AD_KINDS else None


def phone_card_for_page(page_name: str) -> Optional[str]:
    """Portada del ebook para el celular del cierre del video de la pagina, o None (sin anuncio o sin archivo)."""
    kind = ad_kind_for_page(page_name)
    if kind and AD_KINDS[kind]["cover"].exists():
        return str(AD_KINDS[kind]["cover"])
    return None


def _next_project_name(projects: dict, page_name: str) -> str:
    """Nombre automatico incremental: "<proyecto> <n>", con n = lotes que ya
    hay de ese mismo nombre de pagina + 1 (ej. "historias 2")."""
    same = sum(1 for p in projects.values() if _project_page_name(p) == page_name)
    return f"{page_name.lower()} {same + 1}"


def create_project(page_name: str, total_videos: int, per_day: int,
                    networks: dict, video_settings: dict,
                    trigger_message: str = "dame una historia",
                    content_type: str = "video", every_n_days: int = 1) -> dict:
    """page_name es el nombre de la pagina elegida (define el nicho del
    lote); el nombre del lote se genera solo (_next_project_name).

    El perfil rescate animal (Manual v3.2) se activa solo: proyecto
    RESCUE_PAGE_NAME + YouTube entre las redes.

    networks = {"youtube": bool, "facebook": {"page_id": str} | None,
    "instagram": {"page_id": str} | None}. trigger_message es el mensaje que
    se manda al generador de texto para pedir la historia.

    content_type: "video" (default, el pipeline completo guion+clips+audio+
    render), "gaming_image" / "ninio_image" / "howbig_image" (un solo post de imagen 4:5 + caption;
    ver IMAGE_POST_PROFILES, _generate_batch_image_post/_publish_batch_image_post)
    o "gaming_clip" (clip viral de Medal editado en vertical, ver
    _generate_batch_clip) -- YouTube solo para el clip (canal de gaming)."""
    total_videos = max(1, int(total_videos))
    per_day = max(1, int(per_day))
    every_n_days = min(30, max(1, int(every_n_days or 1)))
    if content_type == "howbig_image":
        # How Big Really publica solo en Facebook: punto unico por el que pasa todo lote (la ruta, reintentos...).
        networks = {k: v for k, v in (networks or {}).items() if k == "facebook"}
    mixed = MIXED_TYPES.get(content_type)
    if "gaming_clip" in (mixed or (content_type,)) and not gaming_clip.gaming_vision.is_configured():
        raise ValueError(gaming_clip.gaming_vision.NOT_CONFIGURED_MSG)
    best_hour = _best_hour_for_networks(networks)
    video_settings = dict(video_settings or {})
    # Proveedor de texto del lote (guion/idea). "auto" = FreeLLM endpoint local
    # (ver _generate_text_with_chain). Solo un backend disponible.
    video_settings["text_provider"] = (
        str(video_settings.get("text_provider") or "auto").strip().lower()
        if str(video_settings.get("text_provider") or "auto").strip().lower() in TEXT_PROVIDER_CHOICES
        else "auto"
    )
    rescue = (
        content_type in ("video", "mix_historias")
        and page_name.strip().upper() == RESCUE_PAGE_NAME
        and bool(networks.get("youtube"))
    )
    if rescue:
        video_settings["copy_profile"] = RESCUE_PROFILE
    profile = IMAGE_POST_PROFILES.get(mixed[1] if mixed else content_type)
    clip_post = (mixed[0] if mixed else content_type) in GAMING_VIDEO_TYPES
    et_hours = profile["slot_hours"] if profile else GAMING_SLOT_HOURS if clip_post else None  # hora del Este
    # HISTORIAS con YouTube: 1 video cada 2 dias (independientemente de otras redes)
    youtube_on = bool(networks.get("youtube"))
    rescue_skip = 1 if (youtube_on and content_type == "video" and page_name.strip().upper() == RESCUE_PAGE_NAME) else 0
    skip_days = max(rescue_skip, every_n_days - 1)  # cada N dias = N-1 dias vacios entre publicaciones
    if content_type == "howbig_image":
        # Horario por fase (dias 1-14: 3 al dia; despues 2 al dia), ver howbig_hours_for_date.
        per_day = len(HOWBIG_PHASE1_HOURS)
        with _lock:
            launch = _howbig_launch_date()
        schedule = _compute_schedule(
            total_videos, per_day, best_hour,
            taken=existing_slots(page_name, networks),
            hours_for_date=lambda day: howbig_hours_for_date(day, launch),
            hour_end=HOWBIG_HOUR_END,
        )
    else:
        schedule = _compute_schedule(
            total_videos, per_day, best_hour, rescue=rescue,
            skip_days=skip_days,
            taken=existing_slots(page_name, networks),
            us_audience=True,
            et_hours=et_hours,  # None = las dos franjas generales (12:00 y 19:00 del Este)
        )

    project = {
        "id": uuid.uuid4().hex[:10],
        "name": "",  # se completa bajo el lock, con el conteo real de lotes
        "type": content_type,
        "page_name": page_name,
        "trigger_message": trigger_message,
        "total_videos": total_videos,
        "per_day": per_day,
        "every_n_days": every_n_days,
        "networks": networks,
        # Desfase (segundos) del horario real de cada red respecto de
        # scheduled_at; solo las redes sin scheduling nativo lo aplican al
        # decidir cuando publicar (ver _publish_networks).
        "network_offsets": dict(profile["network_offsets"]) if profile else dict(GAMING_NETWORK_OFFSETS) if clip_post else {},
        "video_settings": video_settings,
        "status": "running",
        "created_at": datetime.now().isoformat(),
        "videos": [
            {
                "index": i,
                "status": "pending",
                "story_id": None,
                "video_path": None,
                "scheduled_at": schedule[i],
                "published_at": {},
                "error": None,
                "stage": None,
                "gen_attempts": 0,
                "publish_attempts": 0,
                "last_publish_auth_error": False,
                **({"item_type": mixed[i % 2]} if mixed else {}),
            }
            for i in range(total_videos)
        ],
    }
    with _lock:
        projects = _load()
        project["name"] = _next_project_name(projects, page_name)
        projects[project["id"]] = project
        _save(projects)
    return project


def list_projects() -> list:
    return list(_load().values())


def get_project(project_id: str) -> Optional[dict]:
    return _load().get(project_id)


def set_status(project_id: str, status: str) -> bool:
    with _lock:
        projects = _load()
        if project_id not in projects:
            return False
        projects[project_id]["status"] = status
        _save(projects)
    return True


def pause_project(project_id: str) -> bool:
    return set_status(project_id, "paused")


def resume_project(project_id: str) -> bool:
    return set_status(project_id, "running")


def cancel_project(project_id: str) -> bool:
    return set_status(project_id, "cancelled")


def _purge_video_assets(project_id: str, video: dict) -> None:
    """Borra lo que un video del lote dejo en disco: el mp4, el audio narrado
    (y sus subtitulos), la carpeta de clips y el guion de scratch. Los nombres
    de story_id son deterministas (ver _generate_batch_video/_image_post), asi
    que tambien limpia videos que fallaron antes de guardar story_id."""
    def _unlink(path) -> None:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("batch %s: no se pudo borrar %s", project_id, path, exc_info=True)

    index = video["index"]
    if video.get("video_path"):
        _unlink(video["video_path"])
    if video.get("audio_path"):
        _unlink(video["audio_path"])
        _unlink(f"{video['audio_path']}.subs.json")
        _unlink(f"{video['audio_path']}{NARRATION_CHECK_SUFFIX}")
    story_ids = [
        f"batch_{project_id}_{index:03d}", f"batch_img_{project_id}_{index:03d}",
        f"gclip_{project_id}_{index:03d}",
    ]
    folders = job_store.load("clip_folders")
    for story_id in story_ids:
        shutil.rmtree(video_maker.VIDEO_PUBLIC_DIR / story_id, ignore_errors=True)
        _unlink(auto_pipeline.SCRATCH_DIR / f"historia_{story_id}.json")
        folders.pop(story_id, None)
    job_store.save("clip_folders", folders)


def delete_project(project_id: str) -> bool:
    """Borra el proyecto de batch_projects.json y todos los archivos de sus
    videos, publicados o no (las redes guardan su propia copia). No cancela lo
    ya programado en Facebook/YouTube, pero lo pendiente (ej. Instagram) deja
    de publicarse. El scheduler ya ignora todo proyecto que no este "running"
    (ver _batch_scheduler_tick), asi que borrar uno cancelado no corta ningun
    hilo en curso. No toca los registros *_published.json (anti-repeticion)."""
    with _lock:
        projects = _load()
        project = projects.pop(project_id, None)
        if project is None:
            return False
        _save(projects)
    for video in project["videos"]:
        _purge_video_assets(project_id, video)
    return True


def delete_video(project_id: str, index: int) -> bool:
    """Elimina UN item del lote: lo marca "removed" (no se saca de la lista porque el resto del
    modulo accede por posicion), borra sus archivos y deja de generarse/publicarse. No cancela lo ya
    programado en Facebook/YouTube. Un item generandose o publicandose no se puede eliminar. Si no
    queda ningun item activo, se elimina el lote entero."""
    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project or not (0 <= index < len(project["videos"])):
            return False
        video = project["videos"][index]
        if video["status"] in ("generating", "publishing"):
            return False
        if video["status"] != "removed":
            video["status"] = "removed"
            video["stage"] = None
            video["error"] = None
            _save(projects)
        everything_gone = all(v["status"] == "removed" for v in project["videos"])
    _purge_video_assets(project_id, video)
    if everything_gone:
        delete_project(project_id)
    return True


def retry_video(project_id: str, index: int) -> bool:
    """Reintenta un video en error: lo vuelve a "pending" (limpia error/stage)
    para que el proximo tick del scheduler lo tome de nuevo, mismo camino que
    cualquier video pendiente normal (ver _batch_scheduler_tick)."""
    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return False
        video = next((v for v in project["videos"] if v["index"] == index), None)
        if not video or video["status"] != "error":
            return False
        video["status"] = "pending"
        video["error"] = None
        video["stage"] = None
        video["gen_attempts"] = 0
        video["heal_cycles"] = 0
        _save(projects)
    return True


def retry_publish_video(project_id: str, index: int, reset_heal: bool = True) -> bool:
    """Reintenta solo la publicacion de un video que ya se genero (video_path
    intacto) pero fallo publicando en alguna red: lo vuelve a "ready" sin
    tocar published_at, asi el proximo intento (_publish_batch_video) salta
    las redes que ya tuvieron exito y reintenta solo las que fallaron. NO usa
    "pending"/retry_video porque eso dispararia una regeneracion completa
    desde cero (nuevo guion, nuevas imagenes) para un video que solo fallo al
    subirse, no al crearse. `reset_heal=False` (reintentos automaticos) conserva el conteo de ciclos de sanado."""
    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return False
        video = next((v for v in project["videos"] if v["index"] == index), None)
        if not video or video["status"] != "publish_error":
            return False
        video["status"] = "ready"
        video["error"] = None
        video["publish_attempts"] = 0
        video["last_publish_auth_error"] = False
        if reset_heal:
            video["publish_heal_cycles"] = 0
        _save(projects)
    return True


def release_stuck_item(project_id: str, index: int, expected_status: str) -> bool:
    """Suelta UN item trabado, igual que _recover_stuck_videos hace con todos al arrancar:
    "generating" -> "pending" (stage None) y "publishing" -> "ready". Solo actua si el item
    sigue en `expected_status` (puede haber terminado mientras alguien decidia soltarlo)."""
    target = {"generating": "pending", "publishing": "ready"}.get(expected_status)
    if target is None:
        return False
    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return False
        video = next((v for v in project.get("videos", []) if v["index"] == index), None)
        if not video or video["status"] != expected_status:
            return False
        video["status"] = target
        if expected_status == "generating":
            video["stage"] = None
        _save(projects)
    return True


def _set_stage(project_id: str, index: int, stage: "str | None") -> None:
    """Guarda la etapa actual de generacion de un video (guion/imagenes/audio/
    render) para que el front pueda mostrar un desglose de avance por video,
    en vez de solo "generando" sin detalle."""
    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return
        project["videos"][index]["stage"] = stage
        _save(projects)


def _claim_for_publish(project_id: str, index: int) -> bool:
    """Mismo patron que el claim de _generate_batch_video (status como marca
    de "en curso" bajo _lock): si el video sigue "ready" lo pasa a
    "publishing" y devuelve True; si otro tick/thread ya lo tomo, devuelve
    False sin tocar nada. Evita que el tick dispare 2 threads de publicacion
    para el mismo video."""
    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return False
        v = project["videos"][index]
        if v["status"] != "ready":
            return False
        v["status"] = "publishing"
        _save(projects)
        return True


def _record_generation_failure(project_id: str, index: int, exc: Exception) -> None:
    """Fallo de una corrida completa de generacion: vuelve a "pending" hasta
    agotar MAX_AUTO_RETRIES; agotado, queda en "error" con `error_at` para que
    _requeue_healable_errors lo levante despues del cooldown."""
    with _lock:
        projects = _load()
        v = projects[project_id]["videos"][index]
        v["gen_attempts"] = v.get("gen_attempts", 0) + 1
        v["error"] = str(exc)
        if v["gen_attempts"] < MAX_AUTO_RETRIES:
            v["status"] = "pending"
        else:
            v["status"] = "error"
            v["error_at"] = datetime.now().isoformat()
        v["stage"] = None
        _save(projects)


def _cooldown_elapsed(since_iso: Optional[str], wait: timedelta, now: datetime) -> bool:
    """True si ya paso `wait` desde `since_iso`; sin fecha (o ilegible) se considera cumplido."""
    if not since_iso:
        return True
    try:
        return now - datetime.fromisoformat(since_iso) >= wait
    except (TypeError, ValueError):
        return True


def _requeue_healable_errors() -> None:
    """Vuelve a "pending" los videos en "error" salvo que el fallo pida una accion humana
    (ver _is_healable_error), respetando un cooldown exponencial y MAX_HEAL_CYCLES. Un video sin
    `error_at` (quedo en error antes de existir este mecanismo) es elegible de inmediato.
    Igual con la publicacion: un "publish_error" que no es de autenticacion vuelve a "ready"
    (solo reintenta las redes que fallaron) con cooldown exponencial y MAX_PUBLISH_HEAL_CYCLES."""
    now = datetime.now()
    with _lock:
        projects = _load()
        changed = False
        for project in projects.values():
            if project.get("status") != "running":
                continue
            for v in project.get("videos", []):
                if v["status"] == "publish_error" and not v.get("last_publish_auth_error"):
                    cycles = v.get("publish_heal_cycles", 0)
                    wait = timedelta(seconds=PUBLISH_HEAL_COOLDOWN_SECONDS * 2 ** cycles)
                    if cycles < MAX_PUBLISH_HEAL_CYCLES and _cooldown_elapsed(v.get("publish_error_at"), wait, now):
                        v["status"] = "ready"
                        v["publish_attempts"] = 0
                        v["publish_heal_cycles"] = cycles + 1
                        changed = True
                        logger.info(
                            "batch %s item %d: auto-reintento de publicacion %d/%d tras error: %s",
                            project["id"], v["index"], cycles + 1, MAX_PUBLISH_HEAL_CYCLES, (v.get("error") or "")[:120],
                        )
                    continue
                if v["status"] != "error" or not _is_healable_error(v.get("error")):
                    continue
                cycles = v.get("heal_cycles", 0)
                if cycles >= MAX_HEAL_CYCLES:
                    continue
                is_quota = GEMINI_QUOTA_MARKER in (v.get("error") or "").lower()
                base_cooldown = GEMINI_QUOTA_COOLDOWN_SECONDS if is_quota else HEAL_COOLDOWN_SECONDS
                error_at = v.get("error_at")
                if error_at:
                    try:
                        wait = timedelta(seconds=base_cooldown * 2 ** cycles)
                        if now - datetime.fromisoformat(error_at) < wait:
                            continue
                    except (TypeError, ValueError):
                        pass
                v["status"] = "pending"
                v["gen_attempts"] = 0
                v["heal_cycles"] = cycles + 1
                v["stage"] = None
                changed = True
                logger.info(
                    "batch %s item %d: auto-reintento %d/%d tras error: %s",
                    project["id"], v["index"], cycles + 1, MAX_HEAL_CYCLES, (v.get("error") or "")[:120],
                )
        if changed:
            _save(projects)


def _slideshow_summary(timeline: dict, vs: dict) -> dict:
    """Puntaje de slideshow del video recien armado, en la forma compacta que se guarda en el item."""
    risk = slideshow_risk.score_timeline(timeline, expects_motion=slideshow_risk.expects_motion(vs))
    return {"average": risk["average"], "verdict": risk["verdict"],
            "dimensions": {name: d["score"] for name, d in risk["dimensions"].items()}}


def _generate_batch_video(project_id: str, index: int) -> None:
    import app

    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return
        project["videos"][index]["status"] = "generating"
        project["videos"][index]["stage"] = "guion"
        project["videos"][index]["error"] = None
        pattern = None
        if _uses_story_pattern(project):
            pattern = _pick_story_pattern(_recent_story_patterns(projects))
            project["videos"][index]["story_pattern"] = pattern
        _save(projects)

    vs = project["video_settings"]
    story_id = f"batch_{project_id}_{index:03d}"
    trigger_message = auto_pipeline.build_trigger_message(
        project.get("trigger_message", "dame una historia"),
        None,  # las paginas con guion duran ~1:30 fijos: no usan la duracion del panel
        kind=_text_kind_for_page(_project_page_name(project)),
    )
    rescue = _is_rescue_project(project)
    if _uses_story_pattern(project):  # Historias (con o sin YouTube): patron + sin repetir nombres
        trigger_message += _story_trigger_addition(project, pattern)
    text_provider_pref = vs.get("text_provider", "auto")
    _provider_attempts: list = []
    try:
        story_text = _run_stage_with_retry(
            lambda: _generate_text_with_chain(
                _text_kind_for_page(_project_page_name(project)),
                _project_page_name(project),
                trigger_message,
                story_id,
                duration_seconds=vs.get("duration_seconds"),
                prefer=text_provider_pref,
                attempted=_provider_attempts,
            ),
            attempts=BROWSER_STAGE_RETRY_ATTEMPTS,
            on_retry=None,
            stage_label="guion",
        )
        hook_text = ""
        if rescue:
            hook_text, story_text = _split_hook_text(story_text)
        story = auto_pipeline.load_story_from_text(story_text, story_id)
        auto_pipeline.validate_image_count(len(story["prompts"]))
        auto_pipeline.validate_prompts_english(story)
        ad_kind = ad_kind_for_page(_project_page_name(project))
        if ad_kind:
            auto_pipeline.validate_ad_story(story, AD_KINDS[ad_kind]["setting"], AD_KINDS[ad_kind].get("subjects", ()))
        visual_style = vs.get("visual_style")
        if visual_style and visual_style != "realista":
            for p in story["prompts"]:
                p["prompt"] = auto_pipeline.apply_visual_style(p["prompt"], visual_style)
        script_text = auto_pipeline.extract_script(story_text)
        if not script_text.strip():
            # Algunos generadores de texto (ej. el de macrame) no mandan un
            # bloque "Guion" separado antes de "Imagen 1" -- la respuesta
            # arranca directo en el primer bloque de imagen. extract_script
            # devuelve "" en ese caso (nada que cortar antes de "Imagen 1")
            # y el TTS fallaba con "Error al generar el audio" aunque la
            # narracion completa SI estaba, repartida en el "Frase del
            # guion" de cada bloque. Reconstruye el guion concatenando esas
            # frases en orden -- es exactamente el texto que ya se muestra
            # superpuesto en cada escena, asi que sigue narrando lo mismo.
            script_text = " ".join(
                p["frase"] for p in sorted(story["prompts"], key=lambda p: p["index"])
            )
            logger.info(
                "batch: guion vacio via extract_script, reconstruido desde %d frases de imagen (%d chars)",
                len(story["prompts"]), len(script_text),
            )
        auto_pipeline.validate_script(script_text)
        auto_pipeline.validate_long_script_length(script_text)
        if ad_kind and "ebook" not in script_text.lower():
            raise RuntimeError("el guion no cierra con Comenta EBOOK (llamado a la accion del anuncio)")
        if _text_kind_for_page(_project_page_name(project)) == "historias":
            auto_pipeline.validate_historia_script(script_text)
        if rescue:
            forbidden = seo_optimizer.find_forbidden_terms(f"{script_text} {hook_text}")
            if forbidden:
                raise RuntimeError(
                    "Guion rechazado por el filtro de seguridad de contenido "
                    f"(vocabulario de daño explicito o de shock): {', '.join(forbidden)}"
                )
        clips_dir = video_maker.VIDEO_PUBLIC_DIR / story_id
        # Historia nueva: los clips de un intento anterior (reinicio de app.py,
        # video vuelto a "pending") son de OTRA historia. resume_index solo
        # sirve para los reintentos de esta misma historia, mas abajo.
        shutil.rmtree(clips_dir, ignore_errors=True)
        clips_dir.mkdir(parents=True, exist_ok=True)

        _set_stage(project_id, index, "imagenes")
        provider = vs.get("provider", "whatsapp")
        images_session = app._session_for_provider(provider)

        def _do_generate_clips():
            with app._clipgen_lock:
                start_index = auto_pipeline.resume_index(clips_dir)
                return auto_pipeline.generate_clips(
                    story, clips_dir, unattended=True, start_index=start_index,
                    provider=provider,
                    generate_video=vs.get("generate_video_clips", True),
                )

        clips = _run_stage_with_retry(
            _do_generate_clips,
            attempts=BROWSER_STAGE_RETRY_ATTEMPTS,
            on_retry=(
                (lambda attempt: _reset_session(images_session))
                if images_session else None
            ),
            stage_label="imagenes",
        )
        (auto_pipeline.SCRATCH_DIR / f"historia_{story_id}.json").unlink(missing_ok=True)
        folders = job_store.load("clip_folders")
        folders[story_id] = {"dir": str(clips_dir), "clip_count": len(clips)}
        job_store.save("clip_folders", folders)

        _set_stage(project_id, index, "audio")
        frases = [p["frase"] for p in sorted(story["prompts"], key=lambda p: p["index"])]
        voice = vs.get("voice") or get_default_voice()
        audio_path, narration = _run_stage_with_retry(
            lambda: text_to_speech_verified(
                script_text,
                voice=voice,
                exaggeration=BEDTIME_PRESET["exaggeration"],
                cfg_weight=BEDTIME_PRESET["cfg_weight"],
            ),
            attempts=LOCAL_STAGE_RETRY_ATTEMPTS,
            stage_label="audio",
        )
        if not audio_path:
            raise RuntimeError("Error al generar el audio.")
        auto_pipeline.validate_min_duration(video_maker.get_audio_duration(audio_path))

        clip_paths = sorted(
            (str(p) for p in clips_dir.glob("scene_*.*") if p.suffix in (".mp4", ".jpg")),
            key=lambda s: int(Path(s).stem.split("_")[1]),
        )
        if len(clip_paths) != len(frases):
            raise RuntimeError(
                f"Los clips no coinciden con la historia: {len(clip_paths)} clips "
                f"para {len(frases)} escenas"
            )
        subtitle_style = video_maker.get_subtitle_preset_style(vs.get("subtitle_preset", ""))
        # Ambientación (luciérnagas + viñeta): configurable por proyecto vía
        # video_settings["mood"] ("warm_night" default | "bright" | "none").
        # Sin definir, se mantiene "warm_night" (comportamiento igual al de
        # antes de este campo) para no cambiarle el look a ningún proyecto
        # ya corriendo sin que se elija explícitamente.
        mood = vs.get("mood", "warm_night")

        risk_box: dict = {}

        def _do_render():
            with app._video_render_lock:
                timeline = video_maker.build_props(
                    image_paths=clip_paths,
                    audio_path=audio_path,
                    title=hook_text,
                    subtitles_enabled=vs.get("subtitles_enabled", True),
                    subtitle_style=subtitle_style,
                    frases=frases,
                    animate_images=vs.get("animate_images", True),
                    ai_label=RESCUE_AI_LABEL if rescue else None,
                    mood=mood,
                    script_text=script_text,
                    phone_card=phone_card_for_page(_project_page_name(project)),
                    phone_card_cta="COMENTA EBOOK",
                )
                if not timeline:
                    return None
                try:
                    risk_box["risk"] = _slideshow_summary(timeline, vs)
                except Exception:
                    logger.exception("batch %s video %d: no se pudo puntuar el riesgo de slideshow", project_id, index)
                return video_maker.render_props(
                    video_maker.VIDEO_DIR / "props.json",
                    orientation=vs.get("orientation", "vertical"),
                )

        _set_stage(project_id, index, "render")
        video_path = _run_stage_with_retry(
            _do_render, attempts=LOCAL_STAGE_RETRY_ATTEMPTS, stage_label="render",
        )
        if not video_path:
            raise RuntimeError("Falló el renderizado del video.")

    except Exception as e:
        logger.exception("batch %s video %d: fallo la generacion", project_id, index)
        _record_generation_failure(project_id, index, e)
        return

    with _lock:
        projects = _load()
        folders = job_store.load("clip_folders")
        if story_id in folders:
            folders[story_id]["video_name"] = Path(video_path).name
            job_store.save("clip_folders", folders)
        v = projects[project_id]["videos"][index]
        v["status"] = "ready"
        v["stage"] = None
        v["story_id"] = story_id
        v["video_path"] = str(video_path)
        v["audio_path"] = str(audio_path)
        if risk_box.get("risk"):
            v["slideshow_risk"] = risk_box["risk"]
        v["narration_check"] = {
            "ok": narration.get("ok"), "similarity": narration.get("similarity"),
            "attempt": narration.get("attempt"), "skipped": narration.get("skipped", False),
        }
        v["script_text"] = script_text
        v["gen_attempts"] = 0
        v["heal_cycles"] = 0
        _save(projects)


# Proporcion que se le pide al generador de imagen: la mas cercana a 4:5
# (Instagram) que ofrece su selector; meme_maker la recorta a 4:5.
GAMING_IMAGE_RATIO = "3:4"
# Se agrega al IMAGE_PROMPT: el meme pone texto arriba y abajo, asi que
# el HUD y el sujeto tienen que quedar en la franja central. La ultima frase
# evita que el generador (Meta AI sobre todo) entregue la captura como un
# rectangulo dentro de barras borrosas o como foto de un monitor.
GAMING_IMAGE_COMPOSITION = (
    " Composition: keep the main subject and every HUD/UI element (health bars, minimap, "
    "dialogue boxes, buttons) inside the central 60% of the frame, compact and away from "
    "the edges; leave the top 20% and bottom 20% as calm background with no UI and no key details. "
    "The in-game screenshot fills the entire image edge to edge: no borders, no letterboxing, "
    "no blurred bars, no frame, and not a photo of a monitor or TV screen."
)


NINIO_IMAGE_COMPOSITION = (
    " Composition: calm, composed editorial lifestyle photograph, medium-wide shot (not an extreme "
    "close-up of a face), peaceful mood, nobody crying or shouting; keep the faces, hands and plate "
    "inside the central 60% of the frame. Full-bleed photo edge to edge: no borders, no frame, no "
    "text, no letters, no logos."
)

HISTORIAS_IMAGE_COMPOSITION = (
    " Composition: warm, cinematic documentary photo of the rescued animal in a safe, hopeful moment, medium "
    "close-up, eyes visible; full-bleed photo edge to edge: no borders, no frame, no text, no letters, no logos."
)

MACRAME_IMAGE_COMPOSITION = (
    " Composition: the macrame piece and the hands centered in the central 60% of the frame, "
    "clean linen background. Natural window light, light oak table, beige tones. "
    "Full-bleed photo edge to edge: no borders, no frame, no text, no letters, no logos."
)

GAMING_HISTORY_STORE_NAME = "gaming_post_history"
GAMING_HISTORY_LIMIT = 15  # entradas que se listan en el prompt
GAMING_HISTORY_MAX = 60  # entradas guardadas antes de podar

# Un "post de imagen" = idea (texto) -> foto (IA) -> texto superpuesto 4:5 -> Facebook +
# Instagram. Cada nicho es un perfil: gaming y niño selectivo comparten todo el flujo y solo
# difieren en estos valores. La clave es el tipo de lote (`content_type`).
IMAGE_POST_PROFILES = {
    "gaming_image": {
        "kind": "gaming_news",  # prompts/gaming_news_system.md: noticias reales de videojuegos (gaming_news.py)
        "news": True,
        "templates": GAMING_TEMPLATES, "week": GAMING_WEEK,
        "slot_hours": GAMING_SLOT_HOURS, "network_offsets": GAMING_NETWORK_OFFSETS,
        "history_store": GAMING_HISTORY_STORE_NAME,
        "composition": GAMING_IMAGE_COMPOSITION, "text_fill": (255, 255, 255),
        "max_hashtags": {"instagram": GAMING_MAX_HASHTAGS["instagram"], "facebook": GAMING_MAX_HASHTAGS["facebook"]},
        "default_trigger": "dame la próxima noticia de videojuegos", "label": "Noticia gaming",
        "ai_design": "news",
    },
    "gaming_meme": {
        # Meme de Memedroid tal cual (gaming_memes.py): sin IA ni plantillas; imagen + credito al autor.
        "kind": gaming_memes.KIND, "ai_design": None,
        "slot_hours": GAMING_MEME_SLOT_HOURS, "network_offsets": GAMING_NETWORK_OFFSETS,
        "history_store": "gaming_meme_history",
        "max_hashtags": {"instagram": GAMING_MAX_HASHTAGS["instagram"], "facebook": GAMING_MAX_HASHTAGS["facebook"]},
        "default_trigger": "dame el próximo meme de videojuegos", "label": "Meme gaming",
    },
    "ninio_image": {
        "kind": "ninio_post",  # prompts/ninio_post_system.md
        "templates": NINIO_TEMPLATES, "week": NINIO_WEEK,
        "slot_hours": NINIO_SLOT_HOURS, "network_offsets": {},
        "history_store": "ninio_post_history",
        "composition": NINIO_IMAGE_COMPOSITION, "text_fill": (255, 221, 51),
        "card": {"panel": (251, 243, 228), "ink": (59, 42, 32), "accent": (214, 106, 79)},  # crema, cafe, coral
        "ai_design": "ad",  # Meta AI dibuja el anuncio completo (foto + texto) con un prompt a WhatsApp
        "ad": {
            "blob": "mint green", "blob_hex": (221, 235, 211), "pill": "salmon pink", "pill_hex": (249, 199, 184),
            "ink": (31, 59, 53), "ink_name": "teal green",
            "badges": ("Estrategias prácticas", "Ideas para la mesa", "Sin presión"),
            "panel": (251, 243, 228),
        },
        "max_hashtags": {"instagram": 5, "facebook": 3},
        "default_trigger": "dame el próximo post de niño selectivo", "label": "Post niño selectivo",
    },
    "historias_image": {
        "kind": "historias_post",  # prompts/historias_post_system.md
        "templates": HISTORIAS_TEMPLATES, "week": HISTORIAS_WEEK,
        "slot_hours": HISTORIAS_SLOT_HOURS, "network_offsets": {},
        "history_store": "historias_post_history",
        "composition": HISTORIAS_IMAGE_COMPOSITION, "text_fill": (255, 255, 255),
        "rotate": True,  # rota las plantillas por historial: nunca dos iguales seguidas
        "ai_design": "story",  # look de foto viral de Facebook (collage / foto con texto manuscrito / cartel)
        "max_hashtags": {"instagram": 6, "facebook": 3},
        "default_trigger": "dame el próximo post de historias de rescate", "label": "Post historias",
    },
    "macrame_image": {
        "kind": "macrame_post",  # prompts/macrame_post_system.md
        "templates": MACRAME_TEMPLATES, "week": MACRAME_WEEK,
        "slot_hours": MACRAME_SLOT_HOURS, "network_offsets": {},
        "history_store": "macrame_post_history",
        "composition": MACRAME_IMAGE_COMPOSITION, "text_fill": (255, 255, 255),
        "card": {"panel": (240, 230, 212), "ink": (74, 52, 38), "accent": (176, 120, 84)},  # lino, cafe, terracota
        "ai_design": "ad",
        "ad": {
            "blob": "warm beige", "blob_hex": (240, 230, 212), "pill": "terracotta peach", "pill_hex": (236, 190, 160),
            "ink": (74, 52, 38), "ink_name": "brown",
            "panel": (245, 236, 220),
            "photo": "the finished macrame piece is the hero of the photo, in sharp focus.",
        },
        "max_hashtags": {"instagram": 6, "facebook": 3},
        "default_trigger": "dame el próximo post de macramé", "label": "Post macramé",
    },
    "howbig_image": {
        "kind": "howbig_post",  # prompts/howbig_post_system.md: comparaciones de escala (How Big Really), en ingles
        "templates": howbig.TEMPLATE_LABELS, "week": None,  # la plantilla se sortea (howbig.pick_template), no hay semana tipo
        "slot_hours": list(HOWBIG_PHASE1_HOURS), "network_offsets": {},
        "history_store": "howbig_post_history",
        "composition": "", "text_fill": (255, 255, 255),
        "ai_design": "scale",  # Flow dibuja la ilustracion vectorial con su titulo; fondo negro #0A0A0A intencional
        "topics": True,  # cada post sale de un tema del banco (assets/howbig/topics.json), nunca inventado
        "max_hashtags": {"instagram": 0, "facebook": 1},
        "default_trigger": "give me the next How Big Really post", "label": "Post How Big Really",
    },
}


def _hex(rgb: tuple) -> str:
    return "#%02X%02X%02X" % tuple(rgb)


def _ai_ad_layout(post: dict, profile: dict, page_name: str) -> str:
    """Instrucciones (en ingles) para que Meta AI dibuje el post completo como un anuncio de marca
    de crianza/hogar (estilo infografia calida): titular grande en una burbuja pastel, subtitulo
    manuscrito, insignias con iconos, burbuja de llamado a la accion, sello de la marca y adornos
    dibujados a mano, sobre una foto luminosa. Los textos van escritos tal cual (tildes y enes)."""
    ad = profile["ad"]
    clean = lambda s: (s or "").strip().rstrip(".").replace('"', "'")  # noqa: E731
    headline, sub = meme_maker.sentence_case(clean(post["top"])).upper(), clean(post.get("bottom"))
    words = headline.split()
    mark = (  # la palabra mas fuerte del titular va resaltada en una pildora de color
        f' Highlight the words "{" ".join(words[len(words) // 2:][:2])}" inside a rounded {ad["pill"]} pill.'
        if len(words) > 2 else ""
    )
    parts = [
        f'(a) a wide rounded {ad["blob"]} bubble holding the headline in big, chunky, friendly rounded bold '
        f'sans-serif capitals, dark {ad["ink_name"]} ({_hex(ad["ink"])}): "{headline}".{mark}',
    ]
    if sub:
        parts.append(f'(b) under it, a handwritten-style subtitle with a hand-drawn underline: "{sub}"')
    if ad.get("badges"):
        names = "; ".join(f'"{t}"' for t in ad["badges"])
        parts.append(f"(c) a row of {len(ad['badges'])} small round pastel badges, each with a simple line icon and a short caption: {names}")
    cta, cta_sub = ad.get("cta", ("COMENTA EBOOK", "y te envío la guía por mensaje privado"))
    parts.append(f'(d) a rounded {ad["pill"]} call-to-action bubble with a chat-bubble icon reading "{cta}" '
                 f'in bold capitals and, below it in small text, "{cta_sub}"')
    parts.append(f'(e) next to the call-to-action, a small brand seal with a tiny leaf icon and the name "{clean(page_name)}"')
    photo = f" {ad['photo']}" if ad.get("photo") else ""
    return (
        " Design this as a finished, professional square 1:1 social media post for a " + ad.get("brand", "warm family-and-parenting brand") + ". "
        "Structure: the top 55% of the image is a clean, bright, natural photo with absolutely NO text, icons or "
        "graphics over it (people, hands and the plate complete and well framed, never cut by the edges);" + photo +
        f" a thin {_hex(ad['pill_hex'])} accent line separates it from a solid soft cream ({_hex(ad['panel'])}) panel "
        "that fills the bottom 45%. Inside the panel, centered and stacked with even spacing and equal side margins, "
        "in this order, in a polished pastel infographic style with a few small hand-drawn doodles (tiny hearts, "
        f'sparkles, short dash marks) in the panel only: {"; ".join(parts)}. '
        "Every text spelled EXACTLY as written, with every accent and the letter ñ intact, fully inside the frame with "
        "at least 5% margin, nothing cut off, nothing overlapping. No other text, no watermark, no borders."
    )


def _ai_story_layout(post: dict, template: str, page_name: str) -> str:
    """Instrucciones (en ingles) para posts de animales con look de foto viral de Facebook, no de
    anuncio: H1 collage de 3 paneles (2 fotos reales + 1 ilustracion con globos de dialogo), H2 foto
    con la voz del animal en texto manuscrito y @pagina, H3 el animal con un cartel de madera que
    pide una reaccion, H4 foto limpia sin texto (la fuerza va en el caption)."""
    clean = lambda s: (s or "").strip().rstrip(".").replace('"', "'")  # noqa: E731
    top, bottom, page = clean(post["top"]), clean(post.get("bottom")), clean(page_name)
    exact = ("Every text spelled EXACTLY as written, accents, ¿ ¡ and the letter ñ intact, fully inside the frame "
             "with at least 5% margin. No other text, no logos, no watermark, no borders.")
    photo = ("a realistic candid smartphone photo filling the whole square 1:1 frame, the animal looking at the "
             "camera, natural light, slightly imperfect framing like a real phone photo. ")
    if template == "H1":
        return (
            " Design this as a viral Facebook storytelling collage, square 1:1, with no margins: 3 panels in a "
            "tall-left layout. Left: one tall realistic smartphone photo of the main scene. Top-right: a realistic "
            "smartphone photo of the same scene a moment later. Bottom-right: a warm, soft 3D-cartoon illustration "
            "of the happy ending, with two white comic speech bubbles in bold black capitals, one per character: "
            f'"{top.upper()}" and "{bottom.upper()}". Thin white dividers between panels. ' + exact
        )
    if template == "H2":
        return (
            f" Design this as a viral Facebook photo post: {photo}Over the calm, empty side of the photo, centered, "
            "write in a casual dark handwritten marker font, as if the animal were speaking, in 3-4 short lines: "
            f'"{top}" and, as its own last line with a hand-drawn underline, "{bottom}". '
            f'At the very bottom-left, small white underlined text: "@{page}". ' + exact
        )
    if template == "H3":
        return (
            f" Design this as a viral Facebook photo post: {photo}The animal sits next to (or wears around its neck) "
            "a small hand-made wooden sign with burned lettering in a casual handwritten font that reads: "
            f'"{top}". The sign is clearly readable and is the only text in the image. ' + exact
        )
    return (
        f" Make this a viral Facebook photo post: {photo}Absolutely NO text, letters, signs, logos or watermark "
        "anywhere in the image."
    )


def _ai_news_layout(post: dict, source: str) -> str:
    """Instrucciones (en ingles) para que Meta AI dibuje la tarjeta de noticia completa: ilustracion a
    sangre, etiqueta NOTICIA, titular grande, dato clave y fuente."""
    clean = lambda s: (s or "").strip().rstrip(".").replace('"', "'")  # noqa: E731
    headline, detail = clean(post["top"]).upper(), clean(post.get("bottom"))
    parts = [
        '(a) top-left, a small bold neon-red rounded label reading "NEWS" in white capitals',
        f'(b) in the lower third, over a soft dark gradient, the headline in huge white condensed bold capitals: "{headline}"',
    ]
    if detail:
        parts.append(f'(c) right under the headline, smaller, in a bright accent color (cyan or yellow): "{detail}"')
    parts.append(f'(d) at the very bottom, tiny and discreet, in light gray: "Source: {clean(source)}"')
    return (
        " Design this as a finished, professional gaming-news social media post, square 1:1, full-bleed cinematic "
        "illustration edge to edge, in the style of a modern esports/news channel graphic. Overlay these text elements, "
        "each spelled EXACTLY as written, with every accent and the letter ñ intact: " + "; ".join(parts) + ". "
        "Clean hierarchy, generous margins, every text fully inside the frame with at least 5% margin, nothing cut off, "
        "nothing overlapping. No other text, no logos, no watermark, no borders."
    )


def _ai_meme_layout(post: dict) -> str:
    """Instrucciones (en ingles) para que Meta AI dibuje el meme completo: texto superior y, si hay,
    remate inferior, en blanco con contorno negro grueso sobre la captura."""
    top = (post["top"] or "").strip().upper().replace('"', "'")
    bottom = (post.get("bottom") or "").strip().replace('"', "'")
    text = f'the top text "{top}"' + (f' and the bottom text "{bottom.upper()}"' if bottom else "")
    return (
        " Design this as a finished viral meme post, vertical 4:5, full-bleed image edge to edge. Overlay "
        f"{text} in huge white condensed bold sans-serif capitals with a thick black outline, centered, "
        + ("top text near the top edge and bottom text near the bottom edge" if bottom else "text near the top edge")
        + ", with generous margins so nothing is cut off. The text must be spelled EXACTLY as written, accents and the letter ñ intact. No other text, "
        "no logos, no watermark, no borders."
    )


def _ai_scale_layout(post: dict) -> str:
    """Sufijo (en ingles) para el prompt de Flow en How Big Really: todo dentro del 90 % central del
    cuadro y el titulo escrito tal cual (sin comillas dobles dentro del titulo)."""
    title = (post.get("top") or "").strip().replace('"', "'")
    return (
        " Keep every element inside the central 90% of the frame, nothing near the edges. "
        f'The title text must be spelled exactly as written: "{title}". '
        "No other text, no logos, no watermark."
    )


def _image_post_template(profile_key: str, scheduled_at: Optional[str]) -> str:
    """Plantilla del post. Con `topics` en el perfil, la sortea howbig.pick_template (ponderada, nunca la
    misma que la ultima); con `rotate`, la que lleva mas tiempo sin usarse (historial);
    si no, la de la semana tipo que le toca a este horario."""
    profile = IMAGE_POST_PROFILES[profile_key]
    if profile.get("topics"):
        return howbig.pick_template([e.get("template") for e in _load_gaming_history(profile["history_store"])])
    if profile.get("rotate"):
        recent = [e.get("template") for e in _load_gaming_history(profile["history_store"])]
        last = {t: max((i for i, r in enumerate(recent) if r == t), default=-1) for t in profile["templates"]}
        return min(profile["templates"], key=lambda t: last[t])  # empate: la primera (H1..H4)
    try:
        dt = datetime.fromisoformat(scheduled_at)
    except (TypeError, ValueError):
        dt = datetime.now()
    return profile["week"][dt.weekday()][0 if dt.hour < 16 else 1]


def generate_gaming_post(
    page_name: str,
    trigger_message: str,
    image_provider: str,
    dest_dir: Path,
    template: str,
    on_stage: Optional[Callable[[str], None]] = None,
    text_provider_pref: str = "",
    profile_key: str = "gaming_image",
) -> dict:
    """Idea (generador de texto) -> imagen base -> meme 4:5 en dest_dir/cover.jpg.
    `profile_key` elige el nicho (IMAGE_POST_PROFILES).
    Devuelve el post parseado (idea/hook/top/bottom/caption). Lo comparten el
    lote (_generate_batch_image_post) y el post suelto de "Crear video"; el
    llamador decide que hacer con los errores y con el historial."""
    import app

    profile = IMAGE_POST_PROFILES[profile_key]

    def _stage(name: str) -> None:
        if on_stage:
            on_stage(name)

    _stage("idea")
    news_item = None
    topic = None
    if profile.get("news"):  # noticia real y reciente, que no se haya publicado ya
        news_item = gaming_news.next_news()
        template_message = trigger_message + gaming_news.news_block(news_item)
    elif profile.get("topics"):  # tema del banco: el modelo solo redacta, no inventa datos
        used = {e.get("topic_id") for e in _load_gaming_history(profile["history_store"]) if e.get("topic_id")}
        topic = howbig.pick_topic(template, used)  # BankExhaustedError: falla claro, antes de gastar texto o Flow
        template = topic["template"]  # si la plantilla pedida no tenia tema libre, manda la del tema
        template_message = (
            trigger_message
            + f"\n\nTemplate for this post: {template} ({profile['templates'][template]})."
            + howbig.topic_block(topic)
            + _gaming_history_block(store_name=profile["history_store"])
        )
    else:
        template_message = (
            trigger_message
            + f"\n\nPlantilla de este post: {template} ({profile['templates'][template]})."
            + _gaming_history_block(store_name=profile["history_store"])
        )
    provider_pref = (text_provider_pref if text_provider_pref in TEXT_PROVIDER_CHOICES else "auto")
    _provider_attempts: list = []
    reply = _run_stage_with_retry(
        lambda: _generate_text_with_chain(
            profile["kind"],
            page_name,
            template_message,
            f"{profile['kind']}_{dest_dir.name}",
            prefer=provider_pref,
            attempted=_provider_attempts,
        ),
        attempts=BROWSER_STAGE_RETRY_ATTEMPTS,
        on_retry=None,
        stage_label="idea",
    )
    post = _parse_gaming_post(reply)
    if topic:
        post["topic_id"] = topic["id"]
        post["template"] = template
        post["caption"] = howbig.format_caption(post["caption"], topic)

    _stage("imagen")
    dest_dir.mkdir(parents=True, exist_ok=True)
    base_path = dest_dir / "base.jpg"
    ai_design = profile.get("ai_design")  # "card" / "meme": Meta AI dibuja el post completo, con texto
    image_prompt = post["image_prompt"].rstrip() + (
        _ai_ad_layout(post, profile, page_name) if ai_design == "ad"
        else _ai_story_layout(post, template, page_name) if ai_design == "story"
        else _ai_news_layout(post, news_item["source"]) if ai_design == "news"
        else _ai_meme_layout(post) if ai_design == "meme"
        else _ai_scale_layout(post) if ai_design == "scale"
        else profile["composition"]
    )

    def _do_generate_image():
        with app._clipgen_lock:
            story = {"prompts": [{
                "index": 0,
                "prompt": image_prompt,
                "frase": post.get("hook") or post["image_prompt"],
            }]}
            generated = auto_pipeline.generate_clips(
                story, dest_dir, unattended=True, start_index=0,
                provider=image_provider if image_provider in auto_pipeline.PROVIDERS else "whatsapp",
                generate_video=False,
                # Flow: posts cuadrados (los disenados por la IA); la escala va 3:4 y se ajusta a 4:5 sin recortar el fondo negro
                aspect="3:4" if ai_design == "scale" else "1:1" if ai_design else "3:4",
            )
            Path(generated[0]).replace(base_path)

    _run_stage_with_retry(
        _do_generate_image,
        attempts=BROWSER_STAGE_RETRY_ATTEMPTS,
        on_retry=(lambda attempt: _reset_session(auto_pipeline.WHATSAPP_SESSION)) if image_provider == "whatsapp" else None,
        stage_label="imagen",
    )
    if ai_design == "scale":  # ilustracion con fondo negro intencional: 4:5 sin quitar bordes oscuros
        meme_maker.fit_post(base_path, dest_dir / "cover.jpg", meme_maker.SIZE_IG, trim=False)
    elif ai_design:  # la IA ya dibujo el post completo (foto + texto): solo se ajusta a cuadrado 1:1
        meme_maker.fit_post(base_path, dest_dir / "cover.jpg", meme_maker.SIZE_SQUARE)
    elif profile.get("card"):  # tarjeta editorial: texto en un panel, no sobre la foto
        meme_maker.render_card(
            base_path, post["top"], post["bottom"], dest_dir / "cover.jpg", meme_maker.SIZE_IG, **profile["card"],
        )
    else:
        meme_maker.render_meme(
            base_path, post["top"], post["bottom"], dest_dir / "cover.jpg", meme_maker.SIZE_IG,
            fill=profile["text_fill"],
        )
    base_path.unlink(missing_ok=True)
    if news_item:
        gaming_news.mark_used(news_item)  # solo si el post salio completo
        post["news_link"] = news_item["link"]
    return post


def _generate_batch_image_post(project_id: str, index: int) -> None:
    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return
        project["videos"][index]["status"] = "generating"
        project["videos"][index]["stage"] = "idea"
        project["videos"][index]["error"] = None
        _save(projects)

    story_id = f"batch_img_{project_id}_{index:03d}"
    profile_key = item_type(project, project["videos"][index])
    profile = IMAGE_POST_PROFILES[profile_key]
    template = _image_post_template(profile_key, project["videos"][index].get("scheduled_at"))
    dest_dir = video_maker.VIDEO_PUBLIC_DIR / story_id
    dest_path = dest_dir / "cover.jpg"  # con texto, cuadrada 1:1 (Facebook e Instagram)

    try:
        post = generate_gaming_post(
            _project_page_name(project),
            profile["default_trigger"] if project.get("type") in MIXED_TYPES
            else project.get("trigger_message", profile["default_trigger"]),
            project.get("video_settings", {}).get("image_provider", "whatsapp"),
            dest_dir,
            template,
            on_stage=lambda stage: _set_stage(project_id, index, stage),
            text_provider_pref=project.get("video_settings", {}).get("text_provider", "auto"),
            profile_key=profile_key,
        )
    except Exception as e:
        logger.exception("batch %s post %d: fallo la generacion", project_id, index)
        _record_generation_failure(project_id, index, e)
        return

    _append_gaming_history({
        "project_id": project_id,
        "index": index,
        "hook": post["hook"],
        "idea": post["idea"],
        "template": post.get("template", template),
        "topic_id": post.get("topic_id"),
        "created_at": datetime.now().isoformat(),
    }, profile["history_store"])
    with _lock:
        projects = _load()
        v = projects[project_id]["videos"][index]
        v["status"] = "ready"
        v["stage"] = None
        v["story_id"] = story_id
        v["video_path"] = str(dest_path)
        v["template"] = post.get("template", template)
        v["idea"] = post["idea"]
        v["caption"] = post["caption"]
        v["gen_attempts"] = 0
        v["heal_cycles"] = 0
        _save(projects)


def _generate_batch_meme(project_id: str, index: int) -> None:
    """Meme de videojuegos de Memedroid (gaming_memes): el mejor reciente sin publicar, como imagen + credito."""
    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return
        project["videos"][index]["status"] = "generating"
        project["videos"][index]["stage"] = "meme"
        project["videos"][index]["error"] = None
        _save(projects)

    story_id = f"gmeme_{project_id}_{index:03d}"
    try:
        meme = gaming_memes.generate_gaming_meme(video_maker.VIDEO_PUBLIC_DIR / story_id / "cover.jpg", index)
    except Exception as e:
        logger.exception("batch %s meme %d: fallo la generacion", project_id, index)
        _record_generation_failure(project_id, index, e)
        return

    with _lock:
        projects = _load()
        v = projects[project_id]["videos"][index]
        v["status"] = "ready"
        v["stage"] = None
        v["story_id"] = story_id
        v["video_path"] = meme["image_path"]
        v["idea"] = meme["hook"]
        v["caption"] = meme["caption"]
        v["source_meme"] = meme["source_meme"]
        v["gen_attempts"] = 0
        v["heal_cycles"] = 0
        _save(projects)


def _generate_batch_clip(project_id: str, index: int) -> None:
    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return
        project["videos"][index]["status"] = "generating"
        project["videos"][index]["stage"] = "clip"
        project["videos"][index]["error"] = None
        _save(projects)

    try:
        game = gaming_clip.game_for_index(
            project.get("video_settings", {}).get("gaming_game", "mix"), index,
        )
        clip = gaming_clip.generate_gaming_clip(
            game, f"{project_id}_{index:03d}",
            on_stage=lambda stage: _set_stage(project_id, index, stage),
        )
    except Exception as e:
        logger.exception("batch %s clip %d: fallo la generacion", project_id, index)
        _record_generation_failure(project_id, index, e)
        return

    with _lock:
        projects = _load()
        v = projects[project_id]["videos"][index]
        v["status"] = "ready"
        v["stage"] = None
        v["story_id"] = f"gclip_{project_id}_{index:03d}"
        v["video_path"] = clip["video_path"]
        v["title"] = clip["title"]
        v["caption"] = clip["caption"]
        v["yt_seo"] = clip["youtube"]  # titulo/descripcion/tags propios de YouTube (SEO)
        v["game"] = game["key"]
        v["source_clip"] = clip["clip"]["page_url"]
        v["gen_attempts"] = 0
        v["heal_cycles"] = 0
        _save(projects)


def flow_background_images(prompts: list, dest_dir: Path, on_progress: Optional[Callable[[str], None]] = None) -> list:
    """Imagenes de fondo del reel con Google Flow (9:16): bg_000.jpg... dentro de `dest_dir`. Devuelve los nombres
    creados. Usa el mismo candado que el resto de generaciones de imagen: Flow y WhatsApp comparten navegador."""
    import app
    import flow_images

    story = {"prompts": [{"index": i, "prompt": p, "frase": p} for i, p in enumerate(prompts)]}
    with app._clipgen_lock:
        paths = flow_images.generate_scene_images(story, dest_dir, on_progress, aspect="9:16")
    names = []
    for i, path in enumerate(sorted(paths)):
        target = dest_dir / f"bg_{i:03d}.jpg"
        Path(path).replace(target)
        names.append(target.name)
    return names


def _generate_batch_reel(project_id: str, index: int) -> None:
    """Reel de noticias de videojuegos con motion graphics (gaming_reel.generate_gaming_reel): una noticia real
    de las ultimas 2 semanas -> guion en ingles -> voz -> render. Mismos campos que un clip de gaming."""
    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return
        project["videos"][index]["status"] = "generating"
        project["videos"][index]["stage"] = "noticia"
        project["videos"][index]["error"] = None
        _save(projects)

    provider_pref = (project.get("video_settings", {}).get("text_provider") or "auto")
    attempted: list = []

    def text_fn(message: str) -> str:
        return _generate_text_with_chain(
            gaming_reel.KIND, project["page_name"], message, f"{gaming_reel.KIND}_{project_id}_{index:03d}",
            prefer=provider_pref if provider_pref in TEXT_PROVIDER_CHOICES else "auto", attempted=attempted,
        )

    try:
        settings = project.get("video_settings", {})
        reel = gaming_reel.generate_gaming_reel(
            f"{project_id}_{index:03d}", text_fn, on_stage=lambda stage: _set_stage(project_id, index, stage),
            voice=settings.get("voice") or "", subtitle_preset=settings.get("subtitle_preset") or "",
            image_fn=flow_background_images if settings.get("reel_images", True) else None,
        )
    except Exception as e:
        logger.exception("batch %s reel %d: fallo la generacion", project_id, index)
        _record_generation_failure(project_id, index, e)
        return

    with _lock:
        projects = _load()
        v = projects[project_id]["videos"][index]
        v["status"] = "ready"
        v["stage"] = None
        v["story_id"] = f"greel_{project_id}_{index:03d}"
        v["video_path"] = reel["video_path"]
        v["title"] = reel["title"]
        v["caption"] = reel["caption"]
        v["yt_seo"] = reel["youtube"]
        v["source_news"] = reel["news_link"]
        v["gen_attempts"] = 0
        v["heal_cycles"] = 0
        _save(projects)


def _last_published_at(path: Path) -> Optional[datetime]:
    timestamps = []
    for item in _read_json_list(path):
        try:
            timestamps.append(datetime.fromisoformat(item["published_at"]))
        except (KeyError, TypeError, ValueError):
            continue
    return max(timestamps) if timestamps else None


def _gap_ok(path: Path) -> bool:
    last = _last_published_at(path)
    return last is None or (datetime.now() - last).total_seconds() >= BATCH_MIN_GAP_SECONDS


_NETWORK_PUBLISHED_PATH_ATTR = {
    "youtube": "_PUBLISHED_VIDEOS_PATH",
    "facebook": "_PUBLISHED_FB_PATH",
    "instagram": "_PUBLISHED_IG_PATH",
}

# Redes que soportan scheduling nativo del lado de la plataforma (Facebook:
# scheduled_publish_time: YouTube: publishAt). Para estas, _publish_networks
# no espera a que llegue scheduled_at ni chequea _gap_ok -- la llamada se
# hace apenas el video esta "ready" y es la plataforma la que sostiene el
# horario real y el espaciado. Instagram no tiene scheduling nativo: sigue
# esperando el horario real y respetando _gap_ok, igual que siempre.
_NATIVE_SCHEDULE_NETWORKS = {"facebook", "youtube"}

# Instagram no programa de forma nativa, pero subir y procesar el Reel es lo lento (minutos): se hace
# con IG_PREPARE_LEAD de anticipacion (container en FINISHED, ver instagram_publisher.prepare_video) y en
# el horario real solo queda el media_publish (segundos). Los containers vencen a las ~24 h: pasado
# IG_CONTAINER_MAX_AGE se descartan y se publica por el camino completo. Si preparar falla, se
# reintenta recien tras IG_PREPARE_RETRY (el publish del horario real funciona igual sin container).
IG_PREPARE_LEAD = timedelta(minutes=60)
IG_RESCHEDULE_LEAD = timedelta(hours=2)  # un Reel reprogramado por fallo sale como pronto en 2 h (Instagram va 1 h antes)
MAX_IG_RESCHEDULES = 5
IG_CONTAINER_MAX_AGE = timedelta(hours=20)
IG_PREPARE_RETRY = timedelta(minutes=10)
_ig_prepare_lock = threading.Lock()  # una subida de preparacion a la vez


def _recent(iso: Optional[str], within: timedelta) -> bool:
    try:
        return datetime.now() - datetime.fromisoformat(iso) < within
    except (TypeError, ValueError):
        return False


def _ig_container_id(video: dict) -> Optional[str]:
    """Id del container de Instagram ya preparado y todavia vigente, o None."""
    container = video.get("ig_container")
    if isinstance(container, dict) and _recent(container.get("prepared_at"), IG_CONTAINER_MAX_AGE):
        return container.get("id")
    return None


def _prepare_ahead(project_id: str, index: int, prepare_fn: Callable, page_id: Optional[str]) -> None:
    """Prepara el Reel de Instagram antes de su horario (ver IG_PREPARE_LEAD) y guarda el container en el
    item. Un fallo solo se registra: el publish del horario real cae al camino completo."""
    with _lock:
        video = _load()[project_id]["videos"][index]
        if _ig_container_id(video) or _recent(video.get("ig_prepare_failed_at"), IG_PREPARE_RETRY):
            return
    with _ig_prepare_lock:
        try:
            result = prepare_fn(video, page_id)
        except Exception as e:
            logger.exception("batch %s item %d: fallo preparando Instagram", project_id, index)
            result = {"ok": False, "error": str(e)}
    with _lock:
        projects = _load()
        v = projects[project_id]["videos"][index]
        if result.get("ok"):
            v["ig_container"] = {"id": result["container_id"], "prepared_at": datetime.now().isoformat()}
            v.pop("ig_prepare_failed_at", None)
        else:
            v["ig_prepare_failed_at"] = datetime.now().isoformat()
            logger.warning("batch %s item %d: no se pudo preparar Instagram: %s", project_id, index, result.get("error"))
        _save(projects)


# Anticipacion minima para programar de forma nativa: el minimo de Facebook
# (10 min) mas un margen para que la subida del archivo no lo deje justo debajo.
NATIVE_SCHEDULE_MIN_LEAD = timedelta(seconds=facebook_publisher.MIN_SCHEDULE_SECONDS + 300)


def _future_slot(projects: dict, exclude: tuple, scheduled_at: Optional[str]) -> datetime:
    """Horario con el que se programa un video que ya esta listo: su
    scheduled_at si cae en un dia valido; si no, el mismo horario del dia
    siguiente, y asi hasta uno que no choque (a menos de BATCH_MIN_GAP_SECONDS)
    con el de otro video ya listo o publicado. Mover de a dias enteros mantiene
    el horario dentro de 9-20 y respeta los N por dia del lote.

    El adelanto minimo cuenta desde ahora a cualquier hora (el horario del item ya esta en 9-21): a las
    2 a. m. un item de las 13:00 de hoy sigue siendo de hoy. Solo cuentan los videos de la MISMA pagina:
    el espaciado es por pagina, y los de otras paginas no deben empujar este lote."""
    now = datetime.now()
    earliest = now + NATIVE_SCHEDULE_MIN_LEAD
    page = _project_page_name(projects.get(exclude[0], {}))
    try:
        slot = datetime.fromisoformat(scheduled_at)
    except (TypeError, ValueError):
        slot = _into_publish_window(earliest)
    taken = []
    for pid, project in projects.items():
        if _project_page_name(project) != page:
            continue
        for i, v in enumerate(project.get("videos", [])):
            # Solo cuentan los que ya tienen un horario firme; un "pending" o
            # "generating" se mueve solo cuando le toque (mismo criterio).
            if (pid, i) == exclude or v.get("status") not in ("ready", "publishing", "published"):
                continue
            try:
                taken.append(datetime.fromisoformat(v["scheduled_at"]))
            except (KeyError, TypeError, ValueError):
                continue
    while slot < earliest or any(
        abs((slot - dt).total_seconds()) < BATCH_MIN_GAP_SECONDS for dt in taken
    ):
        slot += timedelta(days=1)
    return slot


def _youtube_slot(projects: dict, exclude: tuple, scheduled_at: str) -> datetime:
    """Horario de YouTube: el de la red principal si su dia esta libre; YouTube
    lleva un video por dia, asi que si ya hay uno programado ese dia se pasa al
    mismo horario del dia siguiente, y asi hasta uno libre. La fuente de verdad
    es el canal (youtube_publisher.list_scheduled): incluye lo que se programo
    o movio a mano. Si no se puede consultar, se usan los registros del lote
    (youtube_at, o scheduled_at en los subidos antes de existir ese campo)."""
    slot = datetime.fromisoformat(scheduled_at)
    remote = youtube_publisher.list_scheduled()
    if remote is not None:
        days = {dt.date() for dt in remote}
        while slot.date() in days:
            slot += timedelta(days=1)
        return slot
    logger.warning("youtube: no se pudo leer el canal, uso los registros del lote para el dia libre")
    days = set()
    for pid, project in projects.items():
        for i, v in enumerate(project.get("videos", [])):
            if (pid, i) == exclude or not v.get("published_at", {}).get("youtube"):
                continue
            try:
                days.add(datetime.fromisoformat(v.get("youtube_at") or v["scheduled_at"]).date())
            except (KeyError, TypeError, ValueError):
                continue
    while slot.date() in days:
        slot += timedelta(days=1)
    return slot


def _scheduled_epoch(video: dict) -> Optional[float]:
    """scheduled_at (ISO local) como timestamp epoch, para pasarle a
    facebook_publisher (scheduled_time). None si falta o esta corrupto."""
    try:
        return datetime.fromisoformat(video["scheduled_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def _rfc3339_utc(iso_str: Optional[str], min_lead_seconds: int = 600) -> Optional[str]:
    """Convierte scheduled_at (ISO local) a RFC3339 UTC para YouTube
    publishAt. None si falta la fecha, esta corrupta, o entra dentro del
    margen minimo (min_lead_seconds) -- en ese caso se publica ya en vez de
    programar, mismo criterio que facebook_publisher.MIN_SCHEDULE_SECONDS."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # naive = hora local del sistema, igual que el resto del modulo
    if (dt - datetime.now(dt.tzinfo)).total_seconds() < min_lead_seconds:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_GAMING_POST_RE = re.compile(
    r"IDEA:\s*(.+?)\s*HOOK:\s*(.+?)\s*IMAGE_PROMPT:\s*(.+?)\s*TOP:\s*(.+?)\s*BOTTOM:\s*(.*?)\s*CAPTION:\s*(.+)",
    re.DOTALL,
)


_GAMING_TRAILING_RE = re.compile(
    r"\n[ \t]*#{0,3}[ \t]*(?:PERFORMANCE GOAL|SERIE POTENT?IAL)\b", re.IGNORECASE
)


def _parse_gaming_post(text: str) -> dict:
    """Parsea la respuesta del generador de texto dedicado a posts gaming
    (formato IDEA/HOOK/IMAGE_PROMPT/TOP/BOTTOM/CAPTION). BOTTOM puede venir
    vacio (plantilla T4: un solo texto). Saca los `**` antes
    de parsear -- el modelo suele resaltar los labels en negrita, igual que
    auto_pipeline._parse_story con el guion."""
    cleaned = text.replace("**", "")
    m = _GAMING_POST_RE.search(cleaned)
    if not m:
        raise auto_pipeline.PipelineError(
            "La respuesta del generador de texto no vino en el formato esperado "
            "(IDEA/HOOK/IMAGE_PROMPT/TOP/BOTTOM/CAPTION). "
            f"Respuesta recibida: {cleaned.strip()[:300]!r}"
        )
    idea, hook, image_prompt, top, bottom, caption = (g.strip() for g in m.groups())
    # El manual v2 pide PERFORMANCE GOAL y SERIE POTENTIAL tras el caption:
    # son notas internas, no van al post.
    caption = _GAMING_TRAILING_RE.split(caption)[0].strip()
    return {
        "idea": idea, "hook": hook, "image_prompt": image_prompt,
        "top": top, "bottom": bottom.strip("-— "), "caption": caption,
    }


_TRAILING_HASHTAGS_RE = re.compile(r"(?:\s*#\w+)+\s*$")


def _limit_hashtags(caption: str, max_tags: int) -> str:
    """Deja como mucho `max_tags` hashtags en el bloque final del caption
    (el workflow los quiere al final, y distinto tope por red)."""
    m = _TRAILING_HASHTAGS_RE.search(caption)
    if not m:
        return caption
    tags = re.findall(r"#\w+", m.group())[:max_tags]
    body = caption[:m.start()].rstrip()
    return f"{body}\n\n{' '.join(tags)}" if tags else body


def _load_gaming_history(store_name: str = GAMING_HISTORY_STORE_NAME) -> list:
    return job_store.load(store_name).get("entries", [])


def _history_keeps_all(store_name: str) -> bool:
    """El historial de un perfil con banco de temas (`topics`) no se poda: de el sale la lista de temas
    ya usados, y podarlo haria que los mas viejos volvieran a salir (y que el banco nunca se agotara).
    Esta acotado por el tamano del banco, porque un tema agotado no genera mas posts."""
    return any(p.get("topics") and p.get("history_store") == store_name for p in IMAGE_POST_PROFILES.values())


def _append_gaming_history(entry: dict, store_name: str = GAMING_HISTORY_STORE_NAME) -> None:
    with _lock:
        store = job_store.load(store_name)
        entries = store.get("entries", [])
        entries.append(entry)
        store["entries"] = entries if _history_keeps_all(store_name) else entries[-GAMING_HISTORY_MAX:]
        job_store.save(store_name, store)


def _gaming_history_block(limit: int = GAMING_HISTORY_LIMIT, store_name: str = GAMING_HISTORY_STORE_NAME) -> str:
    entries = _load_gaming_history(store_name)[-limit:]
    if not entries:
        return ""
    lines = "\n".join(f"- {e['hook']}" for e in entries)
    return (
        "\n\nNo repitas ninguna de estas ideas/hooks/conceptos visuales ya "
        f"usados en posts anteriores:\n{lines}"
    )


def get_public_base_url() -> Optional[str]:
    return job_store.load("batch_settings").get("public_base_url") or None


def set_public_base_url(url: str) -> None:
    with _lock:
        settings = job_store.load("batch_settings")
        settings["public_base_url"] = (url or "").strip().rstrip("/")
        job_store.save("batch_settings", settings)


def _public_image_url(project_id: str, index: int) -> Optional[str]:
    """URL publica de la portada del post."""
    base = get_public_base_url()
    if not base:
        return None
    return f"{base}/api/batch/cover/{project_id}/{index}"


_COPY_LABELS = re.compile(r"^(FACEBOOK|INSTAGRAM):[ \t]*\n?", re.IGNORECASE | re.MULTILINE)


def _story_copy_llm(script_text: str, kind: str = "historias") -> Optional[dict]:
    """Texto de Facebook/Instagram redactado por la IA para el video (prompts/<kind>_copy_system.md),
    en vez de un extracto del guion. None si no hay IA o la respuesta no sirve: el llamador usa
    entonces el texto deterministico."""
    try:
        text, _ = text_provider.generate_text(
            (_PROMPTS_DIR / f"{kind}_copy_system.md").read_text(encoding="utf-8"),
            f"Guion del video:\n{script_text}", max_tokens=1200, temperature=0.8,
        )
    except Exception as e:
        logger.warning("batch: copy de historias por IA fallo (%s); texto deterministico", e)
        return None
    parts = _COPY_LABELS.split(text)  # ['', 'FACEBOOK', fb, 'INSTAGRAM', ig]
    found = {parts[i].upper(): re.sub(r"\s*-{3,}\s*$", "", parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)}
    copy = {k: found.get(k.upper(), "") for k in ("FACEBOOK", "INSTAGRAM")}
    if all(120 <= len(v) <= 2000 and "[" not in v and "#" in v for v in copy.values()):
        return {"facebook": copy["FACEBOOK"], "instagram": copy["INSTAGRAM"]}
    logger.warning("batch: copy de historias por IA con formato invalido; texto deterministico")
    return None


# Hashtags de Facebook/Instagram por nicho para los videos que no son de rescate: fijos y con sentido
# (las palabras sueltas del guion, "#digo #metros #cuerda", no lo son).
NICHE_HASHTAGS = {
    "macrame": ["#macrame", "#macramefacil", "#macrameparaprincipiantes", "#diymacrame", "#hechoamano"],
    "ninio_selectivo": ["#niñoselectivo", "#crianza", "#alimentacioninfantil", "#maternidad", "#comidaparaniños"],
}


# Texto de publicacion de Niño Selectivo si la IA no responde: el modelo de conversion de la pagina.
NINIO_COPY_FALLBACK = (
    "Mi hijo NO comía verduras hasta que probé ESTO 🥦👇\nNo fue obligarlo.\nNo fue esconderle la comida.\n"
    "No fue negociar un bocado más.\nFue cambiar la receta.\n"
    "Cuando entendí que un niño selectivo no necesita presión, necesita una receta que le haga sentir seguro... "
    "todo cambió.\nPasamos de:\n❌ Peleas en cada comida\n❌ \"No me gusta\" antes de probar\n"
    "❌ Yo estresada y con culpa\nA:\n✅ Un plato colorido que ÉL quiere probar\n✅ Yo celebrando, no rogando\n"
    "✅ Paz en la mesa por fin\nPor eso creé 127 Recetas Sin Peleas — recetas fáciles, rápidas y pensadas para que "
    "digan SÍ sin peleas.\n¿Quieres el link? Encuéntralo en mi bio.\nDe la lucha en la mesa a la paz en familia 🤎\n"
    "👇 comenta EBOOK\n\n#niñoselectivo #crianzarespetuosa #alimentacioninfantil #recetasparaniños"
)


# Texto de publicacion de Macramé si la IA no responde.
MACRAME_COPY_FALLBACK = (
    "Yo pensaba que el macramé era solo para expertas 🧶👇\n"
    "No era falta de talento.\nNo eran mis manos.\nEran los tutoriales que van rapidísimo.\n"
    "Cuando tuve los nudos paso a paso en fotos, todo se volvió fácil.\n"
    "Pasé de:\n❌ Pausar el video cada cinco segundos\n❌ Nudos torcidos y cuerda desperdiciada\n"
    "❌ Pensar que no era para mí\nA:\n✅ Seguir cada paso con calma\n✅ Una pieza prolija en mi pared\n"
    "✅ Ganas de hacer la siguiente\n"
    "Por eso uso el Kit Nudo a Nudo: 15 nudos con fotos paso a paso y 3 patrones para empezar.\n"
    "¿Quieres empezar? Encuéntralo en mi bio.\n👇 comenta EBOOK\n\n"
    "#macrame #macramefacil #macrameparaprincipiantes #diymacrame #hechoamano"
)
COPY_FALLBACKS = {"ninio_selectivo": NINIO_COPY_FALLBACK, "macrame": MACRAME_COPY_FALLBACK}


def _build_publish_content(script_text: str, fallback_title: str, rescue: bool = False,
                           copy_kind: Optional[str] = None, niche_tags: Optional[list] = None) -> dict:
    """Genera titulo/descripcion/tags igual que el flujo manual (mismas
    funciones que usan /api/seo/suggest y /api/seo/suggest-social) para que
    el lote nunca publique con esos campos vacios. El titulo se comparte
    entre las 3 redes -- el flujo manual hace lo mismo (una sola caja de
    titulo reusada para YouTube/Facebook/Instagram). Con `rescue` (perfil
    rescate animal) Facebook e Instagram reciben cada uno su propio texto."""
    if not script_text:
        return {
            "title": fallback_title, "yt_description": "", "yt_tags": [],
            "facebook_description": "", "instagram_description": "",
        }
    script_text = re.sub(r"\[([^\]]+)\]", lambda m: m.group(1).title(), script_text)  # [LUNA] -> Luna
    seo = seo_optimizer.suggest_seo(script_text, rescue=rescue)
    written = _story_copy_llm(script_text, copy_kind) if copy_kind else None
    if written:  # Historias / Niño Selectivo: texto largo redactado por la IA
        facebook_description, instagram_description = written["facebook"], written["instagram"]
    elif copy_kind in COPY_FALLBACKS:
        facebook_description = instagram_description = COPY_FALLBACKS[copy_kind]
    elif rescue or copy_kind:  # Historias sin IA: el de rescate (hashtags validados)
        facebook_description = seo_optimizer.suggest_rescue_copy(script_text, "facebook")
        instagram_description = seo_optimizer.suggest_rescue_copy(script_text, "instagram")
    else:
        social = seo_optimizer.suggest_social_caption(script_text)
        facebook_description = social["caption"]
        if niche_tags:  # nunca palabras sueltas del guion como hashtags
            facebook_description += "\n\n" + " ".join(niche_tags)
        instagram_description = facebook_description
    return {
        "title": seo["title"] or fallback_title,
        "yt_description": seo["description"],
        "yt_tags": seo["tags"],
        "facebook_description": facebook_description,
        "instagram_description": instagram_description,
        "ai": bool(written) or not copy_kind,  # False: la IA no respondio y se uso el texto de respaldo
    }


PUBLISH_CONTENT_RETRY = timedelta(hours=1)  # un texto de respaldo se vuelve a pedir a la IA como mucho cada hora


def _saved_publish_content(project_id: str, index: int, script_text: str) -> Optional[dict]:
    """Titulo/descripciones ya guardados en el video para este guion: la IA redactada no se pide dos veces; el
    texto de respaldo (la IA no respondio) se reintenta pasada una hora."""
    with _lock:
        saved = _load()[project_id]["videos"][index].get("publish_content")
    if not saved or saved.get("key") != hashlib.sha1(script_text.encode("utf-8")).hexdigest():
        return None
    content = saved["content"]
    if content.get("ai") or not _cooldown_elapsed(saved.get("built_at"), PUBLISH_CONTENT_RETRY, datetime.now()):
        return content
    return None


def _save_publish_content(project_id: str, index: int, script_text: str, content: dict) -> None:
    with _lock:
        projects = _load()
        projects[project_id]["videos"][index]["publish_content"] = {
            "key": hashlib.sha1(script_text.encode("utf-8")).hexdigest(),
            "built_at": datetime.now().isoformat(), "content": content,
        }
        _save(projects)


def _publish_networks(project_id: str, index: int, publishers: dict, preparers: Optional[dict] = None) -> None:
    """Nucleo compartido de publicacion (gap-check + registro + resolucion de
    estado final), agnostico de que red hace que -- reusado por
    _publish_batch_video (video, hasta 3 redes) y _publish_batch_image_post
    (imagen, solo Facebook/Instagram). `publishers` mapea network -> callable
    (video, page_id) -> result dict {"ok": bool, "error": str, ...}. `preparers` (opcional) mapea una red sin
    scheduling nativo -> callable (video, page_id) que adelanta el trabajo lento: se llama mientras la red
    espera su horario, dentro de IG_PREPARE_LEAD (ver _prepare_ahead)."""
    projects = _load()
    project = projects.get(project_id)
    if not project:
        return
    video = project["videos"][index]
    networks = project["networks"]
    import app

    # Facebook/YouTube se suben apenas el video esta listo, programados en su
    # horario; si ese horario ya paso, se pasa al mismo horario del dia
    # siguiente (queda guardado: Instagram usa el mismo scheduled_at).
    if any(networks.get(n) and not video["published_at"].get(n)
           for n in publishers if n in _NATIVE_SCHEDULE_NETWORKS):
        with _lock:
            projects = _load()
            video = projects[project_id]["videos"][index]
            slot = _future_slot(projects, (project_id, index), video.get("scheduled_at"))
            try:
                changed = datetime.fromisoformat(video["scheduled_at"]) != slot
            except (KeyError, TypeError, ValueError):
                changed = True
            if changed:
                video["scheduled_at"] = slot.isoformat()
                _save(projects)

    rescheduled = False  # gap real de espaciado -> empuja scheduled_at
    waiting = False  # instagram todavia no llego a su horario real -> NO tocar scheduled_at
    error_parts = []
    any_auth_error = False
    day_passed = False  # una red sin scheduling nativo (Instagram) fallo y el dia de su horario ya paso
    for network, publish_fn in publishers.items():
        cfg = networks.get(network)
        if not cfg or video["published_at"].get(network):
            continue
        native = network in _NATIVE_SCHEDULE_NETWORKS
        if not native:
            try:
                scheduled_dt = datetime.fromisoformat(video["scheduled_at"])
            except (KeyError, TypeError, ValueError):
                scheduled_dt = datetime.now()
            scheduled_dt += timedelta(seconds=project.get("network_offsets", {}).get(network, 0))
            if scheduled_dt > datetime.now():
                waiting = True
                prepare_fn = (preparers or {}).get(network)
                if prepare_fn and scheduled_dt - datetime.now() <= IG_PREPARE_LEAD:
                    _prepare_ahead(project_id, index, prepare_fn, cfg.get("page_id") if isinstance(cfg, dict) else None)
                continue
        published_path = getattr(app, _NETWORK_PUBLISHED_PATH_ATTR[network])
        page_id = cfg.get("page_id") if isinstance(cfg, dict) else None
        with _publish_lock:
            if not native and not _gap_ok(published_path):
                rescheduled = True
                continue
            try:
                result = publish_fn(video, page_id)
            except Exception as e:
                logger.exception("batch %s item %d: fallo publicando en %s", project_id, index, network)
                result = {"ok": False, "error": str(e)}

            with _lock:
                projects = _load()
                v = projects[project_id]["videos"][index]
                if result.get("ok"):
                    v["published_at"][network] = datetime.now().isoformat()
                else:
                    error_parts.append(f"{network}: {result.get('error')}")
                    v["error"] = "; ".join(error_parts)
                    if not native and scheduled_dt.date() < datetime.now().date():
                        day_passed = True
                    if result.get("auth_error"):
                        any_auth_error = True
                _save(projects)

    with _lock:
        projects = _load()
        v = projects[project_id]["videos"][index]
        active_networks = [n for n in publishers if networks.get(n)]
        if active_networks and all(v["published_at"].get(n) for n in active_networks):
            v["status"] = "published"
            v["error"] = None
            v["publish_attempts"] = 0
            v["last_publish_auth_error"] = False
        elif error_parts and any_auth_error:
            # Token vencido/cuenta baneada: reintentar no arregla nada, va
            # directo a terminal sin gastar el presupuesto de auto-retry.
            v["status"] = "publish_error"
            v["publish_error_at"] = datetime.now().isoformat()
            v["last_publish_auth_error"] = True
        elif error_parts and day_passed and v.get("reschedules", 0) < MAX_IG_RESCHEDULES:
            # Instagram fallo y su dia ya paso: publicar tarde fuera de horario no sirve, se reprograma al proximo
            # horario libre (Facebook/YouTube ya publicados quedan como estan) y empieza de cero.
            old_slot = v.get("scheduled_at")
            v["scheduled_at"] = _next_free_reschedule_slot(
                projects, (project_id, index), datetime.now() + IG_RESCHEDULE_LEAD,
                hour_end=HOWBIG_HOUR_END if item_type(projects[project_id], v) == "howbig_image" else BATCH_HOUR_END,
            ).isoformat()
            v["status"] = "ready"
            v["publish_attempts"] = 0
            v["publish_heal_cycles"] = 0
            v["reschedules"] = v.get("reschedules", 0) + 1
            v.pop("ig_container", None)
            v.pop("ig_prepare_failed_at", None)
            logger.info("batch %s item %d: Instagram fallo y su dia (%s) ya paso: reprogramado a %s (%s)",
                        project_id, index, old_slot, v["scheduled_at"], (v.get("error") or "")[:100])
        elif error_parts:
            # Fallo real (no solo espera de espaciado): auto-reintenta hasta
            # MAX_AUTO_RETRIES volviendo a "ready" (el proximo tick reintenta
            # solo las redes que fallaron); agotado el limite, queda visible
            # con boton de reintentar (ver retry_publish_video).
            v["publish_attempts"] = v.get("publish_attempts", 0) + 1
            if v["publish_attempts"] < MAX_AUTO_RETRIES:
                v["status"] = "ready"
            else:
                v["status"] = "publish_error"
                v["publish_error_at"] = datetime.now().isoformat()
        else:
            v["status"] = "ready"
            if rescheduled:
                v["scheduled_at"] = _next_free_reschedule_slot(
                    projects, (project_id, index),
                    datetime.now() + timedelta(seconds=BATCH_MIN_GAP_SECONDS),
                    hour_end=HOWBIG_HOUR_END if item_type(projects[project_id], v) == "howbig_image" else BATCH_HOUR_END,
                ).isoformat()
        _save(projects)


def reschedule_published_video(project_id: str, index: int, new_slot: datetime) -> dict:
    """Cambia el horario de un video del lote que ya salio programado a
    Facebook/YouTube: actualiza la programacion en cada plataforma (buscando
    los ids en los registros *_published.json por nombre de archivo). Guarda
    scheduled_at (el que usa Facebook e Instagram) si Facebook acepto, y
    youtube_at (un video por dia, ver _youtube_slot) si YouTube acepto.
    Devuelve {red: resultado} para ver que se pudo y que no."""
    import app

    with _lock:
        projects = _load()
        video = projects[project_id]["videos"][index]
        gaming_clip_project = item_type(projects[project_id], video) in GAMING_VIDEO_TYPES
        # El canal de gaming no sigue la regla de 1 video/dia del de historias
        # (y _youtube_slot consultaria el canal equivocado).
        yt_slot = new_slot if gaming_clip_project else _youtube_slot(projects, (project_id, index), new_slot.isoformat())
    yt_channel = GAMING_YT_CHANNEL if gaming_clip_project else None
    filename = Path(video["video_path"]).name
    results = {}
    updates = {}
    if video["published_at"].get("facebook"):
        record = next((r for r in _read_json_list(app._PUBLISHED_FB_PATH) if r.get("filename") == filename), None)
        results["facebook"] = (
            facebook_publisher.reschedule_video(record["video_id"], new_slot.timestamp(), record.get("page_id"))
            if record else {"ok": False, "error": "sin registro de publicacion"}
        )
        if results["facebook"]["ok"]:
            updates["scheduled_at"] = new_slot.isoformat()
    if video["published_at"].get("youtube"):
        record = next((r for r in _read_json_list(app._PUBLISHED_VIDEOS_PATH) if r.get("filename") == filename), None)
        results["youtube"] = (
            youtube_publisher.reschedule_video(record["video_id"], _rfc3339_utc(yt_slot.isoformat()), channel_key=yt_channel)
            if record else {"ok": False, "error": "sin registro de publicacion"}
        )
        if results["youtube"]["ok"]:
            updates["youtube_at"] = yt_slot.isoformat()
    if updates:
        with _lock:
            projects = _load()
            projects[project_id]["videos"][index].update(updates)
            _save(projects)
    return results


def _publish_prepared_instagram(video: dict, page_id: Optional[str]) -> Optional[dict]:
    """Publica el container que _prepare_ahead dejo listo. None si no hay uno vigente o si Instagram ya lo
    descarto (stale): el que llama publica por el camino completo."""
    container_id = _ig_container_id(video)
    if not container_id:
        return None
    result = instagram_publisher.publish_prepared(container_id, page_id)
    return None if result.get("stale") else result


def _publish_batch_video(project_id: str, index: int) -> None:
    import app

    project = get_project(project_id)
    video = project["videos"][index]
    kind = _text_kind_for_page(_project_page_name(project))
    script_text = video.get("script_text", "")
    memo: dict = {}

    def content() -> dict:
        """Titulo y textos de las redes. Se piden a la IA solo cuando una red va a publicar de verdad (no mientras
        todas esperan su horario: el tick corre cada minuto y agotaba la cuota del modelo) y quedan guardados."""
        if not memo:
            built = _saved_publish_content(project_id, index, script_text)
            if built is None:
                built = _build_publish_content(
                    script_text, project["name"], rescue=_is_rescue_project(project),
                    copy_kind="historias" if _project_page_name(project).strip().upper() == RESCUE_PAGE_NAME
                    else kind if kind in COPY_FALLBACKS else None,
                    niche_tags=NICHE_HASHTAGS.get(kind),
                )
                _save_publish_content(project_id, index, script_text, built)
            memo.update(built)
        return memo

    def _yt(v, page_id):
        with _lock:
            try:
                slot = _youtube_slot(_load(), (project_id, index), v["scheduled_at"]).isoformat()
            except (KeyError, TypeError, ValueError):
                slot = v.get("scheduled_at")
        c = content()
        result = youtube_publisher.publish_video(
            v["video_path"], c["title"], c["yt_description"], "unlisted", c["yt_tags"], True,
            publish_at=_rfc3339_utc(slot), language="es",
        )
        if result.get("ok"):
            app._record_published_video(result["video_id"], c["title"], Path(v["video_path"]).name)
            with _lock:
                projects = _load()
                projects[project_id]["videos"][index]["youtube_at"] = slot
                _save(projects)
        return result

    def _fb(v, page_id):
        c = content()
        result = facebook_publisher.publish_video(
            v["video_path"], c["title"], c["facebook_description"], page_id=page_id,
            scheduled_time=_scheduled_epoch(v),
        )
        if result.get("ok"):
            app._record_published_facebook(result["video_id"], c["title"], Path(v["video_path"]).name, page_id)
        return result

    def _ig_prep(v, page_id):
        c = content()
        return instagram_publisher.prepare_video(v["video_path"], c["title"], c["instagram_description"], page_id=page_id)

    def _ig(v, page_id):
        result = _publish_prepared_instagram(v, page_id)
        c = content()
        if result is None:
            result = instagram_publisher.publish_video(
                v["video_path"], c["title"], c["instagram_description"], page_id=page_id
            )
        if result.get("ok"):
            app._record_published_instagram(result["media_id"], c["title"], Path(v["video_path"]).name, page_id)
        return result

    _publish_networks(project_id, index, {"youtube": _yt, "facebook": _fb, "instagram": _ig},
                      preparers={"instagram": _ig_prep})


def _publish_batch_image_post(project_id: str, index: int) -> None:
    import app

    project = get_project(project_id)
    video = project["videos"][index]
    caption = video.get("caption", "")
    max_tags = IMAGE_POST_PROFILES[item_type(project, video)]["max_hashtags"]

    def _fb(v, page_id):
        result = facebook_publisher.publish_photo(
            v["video_path"], _limit_hashtags(caption, max_tags["facebook"]), page_id=page_id, scheduled_time=_scheduled_epoch(v),
        )
        if result.get("ok"):
            app._record_published_facebook(result["post_id"], project["name"], Path(v["video_path"]).name, page_id)
        return result

    def _ig(v, page_id):
        url = _public_image_url(project_id, index)
        if not url:
            return {"ok": False, "error": "Falta configurar la URL pública del Cloudflare Tunnel (pestaña Ajustes)."}
        result = instagram_publisher.publish_photo(
            url, _limit_hashtags(caption, max_tags["instagram"]), page_id=page_id,
        )
        if result.get("ok"):
            app._record_published_instagram(result["media_id"], project["name"], Path(v["video_path"]).name, page_id)
        return result

    _publish_networks(project_id, index, {"facebook": _fb, "instagram": _ig})  # nunca youtube


def _publish_batch_clip(project_id: str, index: int) -> None:
    import app

    project = get_project(project_id)
    video = project["videos"][index]
    title = video.get("title") or project["name"]
    caption = video.get("caption", "")
    # El clip es gameplay real (sin etiqueta de IA); el reel de noticias lleva voz sintetica y graficos generados.
    ai_generated = item_type(project, video) == "gaming_reel"

    def _fb(v, page_id):
        result = facebook_publisher.publish_video(
            v["video_path"], title, _limit_hashtags(caption, GAMING_MAX_HASHTAGS["facebook"]),
            page_id=page_id, scheduled_time=_scheduled_epoch(v),
        )
        if result.get("ok"):
            app._record_published_facebook(result["video_id"], title, Path(v["video_path"]).name, page_id)
        return result

    def _ig_prep(v, page_id):
        return instagram_publisher.prepare_video(
            v["video_path"], title, _limit_hashtags(caption, GAMING_MAX_HASHTAGS["instagram"]),
            page_id=page_id, ai_generated=ai_generated,
        )

    def _ig(v, page_id):
        result = _publish_prepared_instagram(v, page_id)
        if result is None:
            result = instagram_publisher.publish_video(
                v["video_path"], title, _limit_hashtags(caption, GAMING_MAX_HASHTAGS["instagram"]),
                page_id=page_id, ai_generated=ai_generated,
            )
        if result.get("ok"):
            app._record_published_instagram(result["media_id"], title, Path(v["video_path"]).name, page_id)
        return result

    def _yt(v, page_id):
        # Facebook y YouTube programan nativo: _publish_networks ya movio
        # scheduled_at a un horario futuro valido. El canal es el de gaming, no el de historias.
        yt_title, yt_description, yt_tags = gaming_youtube_fields(v.get("yt_seo"), title, caption)
        result = youtube_publisher.publish_video(
            v["video_path"], yt_title, yt_description, "public", yt_tags, ai_generated,
            publish_at=_rfc3339_utc(v.get("scheduled_at")), channel_key=GAMING_YT_CHANNEL, language="en",
        )
        if result.get("ok"):
            app._record_published_video(result["video_id"], yt_title, Path(v["video_path"]).name)
            with _lock:
                projects = _load()
                projects[project_id]["videos"][index]["youtube_at"] = v.get("scheduled_at")
                _save(projects)
        return result

    _publish_networks(project_id, index, {"youtube": _yt, "facebook": _fb, "instagram": _ig},
                      preparers={"instagram": _ig_prep})


def gaming_youtube_fields(seo: Optional[dict], title: str, caption: str) -> tuple:
    """(titulo, descripcion, tags) de YouTube para un clip gaming. Usa el SEO propio del clip
    (gaming_clip.build_youtube_seo); un clip generado antes de existir eso cae al caption de
    Facebook/Instagram con #Shorts."""
    if seo:
        return seo["title"], seo["description"], list(seo.get("tags") or [])
    yt_title, yt_description = shorts_text(title, _limit_hashtags(caption, GAMING_MAX_HASHTAGS["youtube"]))
    return yt_title, yt_description, []


def shorts_text(title: str, description: str) -> tuple:
    """Titulo (<=100) y descripcion con #Shorts para subir un clip 9:16 a YouTube."""
    title = (title or "Gaming clip").strip()[:100]
    description = (description or "").strip()
    if "#shorts" not in description.lower():
        description = f"{description}\n\n#Shorts".strip()
    return title, description


def _generate_batch_item(project_id: str, index: int, content_type: str) -> None:
    if content_type == "gaming_clip":
        _generate_batch_clip(project_id, index)
    elif content_type == "gaming_reel":
        _generate_batch_reel(project_id, index)
    elif content_type == "gaming_meme":
        _generate_batch_meme(project_id, index)
    elif content_type in IMAGE_POST_PROFILES:
        _generate_batch_image_post(project_id, index)
    else:
        _generate_batch_video(project_id, index)


def _record_publish_failure(project_id: str, index: int, exc: Exception) -> None:
    """El hilo de publicacion murio con una excepcion inesperada (fuera del
    try de cada red): sin esto el video queda en "publishing" para siempre,
    porque nadie mas lo suelta hasta el proximo reinicio. Mismo presupuesto
    de reintentos que un fallo de red normal (ver _publish_networks)."""
    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return
        v = project["videos"][index]
        if v["status"] != "publishing":
            return
        v["publish_attempts"] = v.get("publish_attempts", 0) + 1
        v["error"] = str(exc)
        v["status"] = "ready" if v["publish_attempts"] < MAX_AUTO_RETRIES else "publish_error"
        if v["status"] == "publish_error":
            v["publish_error_at"] = datetime.now().isoformat()
        _save(projects)


def _publish_batch_item(project_id: str, index: int, content_type: str) -> None:
    try:
        if content_type in GAMING_VIDEO_TYPES:
            _publish_batch_clip(project_id, index)
        elif content_type in IMAGE_POST_PROFILES:
            _publish_batch_image_post(project_id, index)
        else:
            _publish_batch_video(project_id, index)
    except Exception as exc:
        logger.exception("batch %s item %d: fallo inesperado publicando", project_id, index)
        _record_publish_failure(project_id, index, exc)


def _batch_scheduler_tick() -> None:
    _requeue_healable_errors()
    projects = _load()
    running = sorted(
        (p for p in projects.values() if p.get("status") == "running"),
        key=lambda p: p.get("created_at", ""),
    )

    # Generacion: un solo video generandose a la vez EN TODO EL LOTE, y va
    # siempre para el proyecto mas viejo (por created_at) que todavia tenga
    # "pending" -- asi un proyecto agota todos sus videos antes de que el
    # siguiente empiece a generar. Si el mas viejo se queda sin "pending"
    # (terminado o todo en "error"), pasa al que sigue en orden de creacion.
    # Publicacion (mas abajo) sigue siendo independiente/paralela por proyecto.
    if not any(v["status"] == "generating" for p in running for v in p["videos"]):
        for project in running:
            next_pending = next((v for v in project["videos"] if v["status"] == "pending"), None)
            if next_pending is not None:
                threading.Thread(
                    target=_generate_batch_item,
                    args=(project["id"], next_pending["index"], item_type(project, next_pending)),
                    daemon=True,
                ).start()
                break

    run_publish_tick(running)


def run_publish_tick(running: Optional[list] = None) -> list:
    """Mitad de "publicacion" de _batch_scheduler_tick, separada para poder
    correrla sola (sin la mitad de generacion, que depende del browser
    automation desatendida) -- la reusa tanto el loop de 60s de start_scheduler
    como publish_worker.py, el script liviano por Task Scheduler que no
    necesita a app.py corriendo. `running` se puede pasar ya cargado (evita
    un _load() de mas si el caller ya lo tiene); si no, lo carga solo.

    Devuelve los threads de publicacion que arranco. El loop de app.py los
    ignora (el proceso sigue vivo igual y los threads daemon terminan solos),
    pero publish_worker.py SI necesita joinearlos: al ser un script de un
    solo paso, si terminara sin esperar, los threads daemon mueren a mitad
    de subida junto con el proceso."""
    if running is None:
        running = sorted(
            (p for p in _load().values() if p.get("status") == "running"),
            key=lambda p: p.get("created_at", ""),
        )

    started = []
    for project in running:
        project_id = project["id"]
        videos = project["videos"]
        for v in videos:
            # Un video listo se intenta subir de inmediato, a cualquier hora:
            # Facebook/YouTube quedan programados en su horario (9-20) y
            # _publish_networks decide por red; Instagram (sin scheduling
            # nativo) espera ahi su horario real.
            if v["status"] != "ready":
                continue
            if _claim_for_publish(project_id, v["index"]):
                t = threading.Thread(
                    target=_publish_batch_item,
                    args=(project_id, v["index"], item_type(project, v)),
                    daemon=True,
                )
                t.start()
                started.append(t)

        active = [v for v in videos if v["status"] != "removed"]
        if active and all(v["status"] == "published" for v in active):
            with _lock:
                fresh = _load()
                if project_id in fresh:
                    fresh[project_id]["status"] = "done"
                    _save(fresh)

    return started


def _recover_stuck_videos() -> None:
    """Al arrancar el proceso, ningun hilo de _generate_batch_video puede
    seguir vivo de una corrida anterior (mueren con el proceso viejo) -- un
    video que quedo en "generating" al reiniciar app.py queda huerfano para
    siempre si no se lo vuelve a poner "pending" aca, porque
    _batch_scheduler_tick nunca larga una generacion nueva para ese proyecto
    mientras crea que ya hay una en curso.

    Tambien recorta a BATCH_HOUR_START-BATCH_HOUR_END cualquier scheduled_at
    ya guardado en disco que haya quedado fuera de rango (proyectos creados
    antes de este fix, ej. las 3am de madrugada) -- corre una sola vez al
    arrancar, mismo lugar que ya resetea los status huerfanos."""
    with _lock:
        projects = _load()
        changed = False
        for project in projects.values():
            for v in project.get("videos", []):
                if v["status"] == "generating":
                    v["status"] = "pending"
                    v["stage"] = None
                    changed = True
                elif v["status"] == "publishing":
                    v["status"] = "ready"
                    changed = True
                if v["status"] in ("pending", "ready") and v.get("scheduled_at"):
                    try:
                        dt = datetime.fromisoformat(v["scheduled_at"])
                    except (TypeError, ValueError):
                        continue
                    hour_end = HOWBIG_HOUR_END if item_type(project, v) == "howbig_image" else BATCH_HOUR_END
                    if not (BATCH_HOUR_START <= dt.hour <= hour_end):
                        clamped_hour = BATCH_HOUR_START if dt.hour < BATCH_HOUR_START else hour_end
                        v["scheduled_at"] = dt.replace(
                            hour=clamped_hour, minute=0, second=0, microsecond=0
                        ).isoformat()
                        changed = True
        if changed:
            _save(projects)


_SCHEDULER_LOCK_PATH = Path("output/job_state/.scheduler.lock")
_scheduler_lock_file = None  # referencia global: mantener el handle abierto sostiene el lock


def holds_scheduler_lock() -> bool:
    """True si ESTE proceso es el dueño del scheduler (y por lo tanto el unico que puede actuar solo)."""
    return _scheduler_lock_file is not None


def _acquire_scheduler_lock() -> bool:
    """Lock exclusivo de SO (no de threading.Lock, que solo protege dentro de
    un mismo proceso) para que nunca haya 2 procesos con scheduler propio
    escribiendo batch_projects.json a la vez -- eso rompe MAX_AUTO_RETRIES
    (cada proceso lee/incrementa/escribe el contador sin ver al otro) y hace
    que 2 llamadas compartiendo la misma sesion de browser se pisen.
    El SO libera el lock solo, sin codigo de cleanup, cuando el proceso muere
    (crash, kill, cierre normal) -- asi un reinicio despues de matar el
    proceso viejo a la fuerza recupera el lock automaticamente.

    Reintenta hasta 30s antes de rendirse: en un reinicio manual (cerrar la
    terminal vieja y lanzar una nueva) se vio en la practica que el proceso
    viejo puede seguir vivo varios segundos de mas mientras termina de
    cerrarse (la terminal/IDE tarda en matarlo del todo) -- 5s de margen no
    alcanzo y el proceso nuevo se quedaba sin scheduler para siempre (hasta
    el proximo reinicio) aunque el viejo terminara muriendo un instante
    despues. 30s es un costo de arranque unico y aceptable; si pasado ese
    margen el lock sigue tomado, recien ahi es un segundo proceso de verdad
    corriendo en paralelo."""
    global _scheduler_lock_file
    _SCHEDULER_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    attempts = 60
    for attempt in range(attempts):
        f = open(_SCHEDULER_LOCK_PATH, "a+")
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            f.close()
            if attempt < attempts - 1:
                time.sleep(0.5)
                continue
            return False
        _scheduler_lock_file = f
        return True
    return False


def _try_acquire_scheduler_lock_once():
    """Intento unico (sin el retry de 30s) del mismo lock exclusivo de
    _acquire_scheduler_lock, para un proceso de un solo paso (publish_worker.py,
    corrido cada N minutos por Task Scheduler): si app.py ya tiene el lock
    tomado -- su propio scheduler esta vivo y ya se encarga de publicar --
    el worker no debe esperar, sale al toque. A diferencia de
    _acquire_scheduler_lock, este NO guarda el handle en un global: el
    llamador es dueno del handle devuelto y tiene que soltarlo el mismo con
    _release_scheduler_lock_handle al terminar, porque este proceso no lo
    sostiene para siempre.

    Returns:
        el file handle si consiguio el lock, o None si ya estaba tomado.
    """
    _SCHEDULER_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    f = open(_SCHEDULER_LOCK_PATH, "a+")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    return f


def _release_scheduler_lock_handle(f) -> None:
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        f.close()


def start_scheduler() -> None:
    """Arranca el loop del scheduler en un hilo daemon unico (llamar una sola
    vez al iniciar app.py). Relee batch_projects.json en cada vuelta: un
    reinicio del server simplemente retoma desde el ultimo estado guardado,
    salvo los videos que hayan quedado "generating" de la corrida anterior
    (ver _recover_stuck_videos).

    Si otro proceso ya tiene el lock de scheduler tomado (ver
    _acquire_scheduler_lock), este proceso NO arranca un segundo loop -- el
    dashboard web sigue funcionando igual, solo que sin scheduler propio."""
    if not _acquire_scheduler_lock():
        logger.error(
            "batch scheduler: ya hay otro proceso corriendo el scheduler de "
            "lotes (lock tomado) -- este proceso NO va a arrancar un segundo "
            "loop ni va a tocar batch_projects.json."
        )
        return

    _recover_stuck_videos()

    def loop():
        while True:
            try:
                _batch_scheduler_tick()
            except Exception:
                logger.exception("batch scheduler: fallo un tick")
            time.sleep(TICK_SECONDS)

    threading.Thread(target=loop, daemon=True).start()
