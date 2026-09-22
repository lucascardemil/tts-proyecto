"""Orquestacion del modulo "Generacion en lote": crea proyectos que generan y
publican N videos completos, espaciados en varios dias, sacando guion+prompts
de un Project de Qwen. Independiente de _run_pipeline_job (app.py) -- solo
comparte los locks globales de render/clipgen (un solo render/generacion a la
vez en todo el proceso) y las funciones de publicacion ya existentes, que se
reusan tal cual, nunca se reescriben.

app.py importa este modulo; este modulo NO importa app.py a nivel de modulo
(se generaria un ciclo) -- las pocas cosas que necesita de ahi (los locks
globales y las funciones _record_published_*) se importan de forma diferida,
dentro de cada funcion que las usa.
"""

import json
import logging
import msvcrt
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from PIL import Image

import auto_pipeline
import video_maker
import facebook_publisher
import instagram_publisher
import youtube_publisher
import feedback_analyzer
import job_store
import seo_optimizer
from tts_engine import text_to_speech_long, DEFAULT_VOICE, BEDTIME_PRESET

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
# dejar programado) fuera de 9am-8pm, sin importar que tan "cerca" quede de
# best_hour por la metrica circular de _compute_schedule.
BATCH_HOUR_START = 9
BATCH_HOUR_END = 20  # inclusive

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
# las caidas de Qwen/WhatsApp duran horas (17/9: ~3h de timeouts) y las 3
# corridas se gastan en minutos, asi que sin esto el video queda muerto hasta
# que alguien aprieta "reintentar". Cada ciclo espera el doble del anterior
# (30min, 1h, 2h, 4h) para no martillar un servicio caido.
HEAL_COOLDOWN_SECONDS = 1800
MAX_HEAL_CYCLES = 4
# Ademas de los transitorios: fallos de proveedor que un nuevo guion/prompt
# suele resolver. NO incluye "no encontre el proyecto" (nombre mal puesto:
# reintentar no lo arregla) ni errores de parseo, salvo respuestas sin prompts.
_HEALABLE_EXTRA_MARKERS = (
    "meta ai no pudo generar", "algunas sesiones de qwen",
    "no encontre el boton 'meta ai'", "no encontre (habilitado)",
    "filtro de seguridad de contenido",  # guion rechazado: otro guion suele pasar
    "los clips no coinciden con la historia",  # regenerar desde cero lo arregla
    "is covered by",  # overlay de carga (splash) tapando el clic en Qwen/WhatsApp: pasa solo
    "no se encontraron prompts de imagen",  # Qwen a veces responde solo el guion: otra respuesta suele traerlos
)

# Perfil de proyecto para historias de rescate animal (Manual maestro v3.2):
# activa el filtro de vocabulario del guion, copy por red, rotulo de IA,
# hook en pantalla, horario 20:00 sin lunes y anti-fatiga. Se guarda en
# video_settings["copy_profile"] para no afectar a los demas proyectos.
RESCUE_PROFILE = "rescate_animal"
RESCUE_QWEN_PROJECT = "HISTORIAS"  # el perfil se activa solo en este proyecto (con YouTube)
RESCUE_HISTORY_LIMIT = 8
RESCUE_AI_LABEL = "Historia recreada con IA"
_HOOK_TEXT_LINE = re.compile(r"^[ \t]*HOOK_TEXT:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _is_rescue_project(project: dict) -> bool:
    return (project.get("video_settings") or {}).get("copy_profile") == RESCUE_PROFILE


def _split_hook_text(story_text: str) -> tuple:
    """Separa la linea `HOOK_TEXT: ...` (texto corto para la pantalla) del
    resto de la respuesta de Qwen. Se quita SIEMPRE del texto: si quedara,
    el parser la metería dentro del último prompt de imagen o de la narración."""
    match = _HOOK_TEXT_LINE.search(story_text)
    if not match:
        return "", story_text
    hook = match.group(1).strip().strip("«»\"“”*")
    return hook, _HOOK_TEXT_LINE.sub("", story_text, count=1)


def _rescue_history_block() -> str:
    """Anti-fatiga (§9): lista los ganchos recientes de los proyectos de
    rescate para que Qwen no repita animal/conflicto/final consecutivos."""
    hooks = []
    for project in _load().values():
        if not _is_rescue_project(project):
            continue
        for v in project.get("videos", []):
            sentences = seo_optimizer._sentences(v.get("script_text", ""))
            if sentences:
                hooks.append((v.get("scheduled_at", ""), seo_optimizer._clip_at_word(sentences[0], 120)))
    recent = [h for _, h in sorted(hooks)][-RESCUE_HISTORY_LIMIT:]
    if not recent:
        return ""
    lines = "\n".join(f"- {h}" for h in recent)
    return (
        "\n\nNo repitas el animal, el conflicto, el escenario ni el tipo de final "
        f"de estas historias recientes:\n{lines}"
    )


def _is_healable_error(message: "str | None") -> bool:
    msg = (message or "").lower()
    return any(m in msg for m in _TRANSIENT_ERROR_MARKERS + _HEALABLE_EXTRA_MARKERS)


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


def _into_publish_window(dt: datetime) -> datetime:
    """Lleva `dt` al rango BATCH_HOUR_START-BATCH_HOUR_END: antes de abrir
    -> hoy a la apertura; despues de cerrar -> manana a la apertura."""
    if dt.hour < BATCH_HOUR_START:
        return dt.replace(hour=BATCH_HOUR_START, minute=0, second=0, microsecond=0)
    if dt.hour > BATCH_HOUR_END:
        return (dt + timedelta(days=1)).replace(hour=BATCH_HOUR_START, minute=0, second=0, microsecond=0)
    return dt


def _compute_schedule(total: int, per_day: int, best_hour: Optional[int], rescue: bool = False) -> list:
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
    Devuelve `total` timestamps ISO."""
    base_hours = [h for _, _, h in feedback_analyzer.DAYPARTS if BATCH_HOUR_START <= h <= BATCH_HOUR_END]
    if rescue:
        base_hours = [20, 19, 18, 17, 16]
    elif best_hour is not None:
        base_hours = sorted(base_hours, key=lambda h: min(abs(h - best_hour), 24 - abs(h - best_hour)))
    hours_for_day = list(base_hours)
    while len(hours_for_day) < per_day:
        next_hour = hours_for_day[-1] + 1
        if next_hour > BATCH_HOUR_END:
            next_hour = BATCH_HOUR_START
        hours_for_day.append(next_hour)

    now = datetime.now()
    days_needed = -(-total // per_day)
    publish_days = []
    offset = 0
    while len(publish_days) < days_needed:
        day_date = (now + timedelta(days=offset)).date()
        offset += 1
        if rescue and day_date.weekday() == 0:  # lunes
            continue
        publish_days.append(day_date)

    schedule = []
    for i in range(total):
        day_date = publish_days[i // per_day]
        hour = hours_for_day[i % per_day] % 24
        when = datetime(day_date.year, day_date.month, day_date.day, hour)
        schedule.append(when.isoformat())
    return schedule


def _next_project_name(projects: dict, qwen_project: str) -> str:
    """Nombre automatico incremental: "<proyecto> <n>", con n = lotes que ya
    hay de ese mismo Project de Qwen + 1 (ej. "historias 2")."""
    same = sum(1 for p in projects.values() if p.get("qwen_project") == qwen_project)
    return f"{qwen_project.lower()} {same + 1}"


def create_project(qwen_project: str, total_videos: int, per_day: int,
                    networks: dict, video_settings: dict,
                    trigger_message: str = "dame una historia",
                    content_type: str = "video") -> dict:
    """qwen_project es el nombre de la pagina elegida (igual al del Project de
    Qwen); el nombre del lote se genera solo (_next_project_name).

    El perfil rescate animal (Manual v3.2) se activa solo: proyecto
    RESCUE_QWEN_PROJECT + YouTube entre las redes.

    networks = {"youtube": bool, "facebook": {"page_id": str} | None,
    "instagram": {"page_id": str} | None}. trigger_message es el mensaje que
    se manda al Project de Qwen para pedir la historia -- distintos Projects
    pueden esperar frases distintas segun como este configurado su system
    prompt.

    content_type: "video" (default, el pipeline completo guion+clips+audio+
    render) o "gaming_image" (un solo post de imagen 1:1 + caption, ver
    _generate_batch_image_post/_publish_batch_image_post) -- nunca YouTube
    para este ultimo, sin importar lo que venga en `networks`."""
    total_videos = max(1, int(total_videos))
    per_day = max(1, int(per_day))
    best_hour = _best_hour_for_networks(networks)
    video_settings = dict(video_settings or {})
    rescue = (
        content_type == "video"
        and qwen_project.strip().upper() == RESCUE_QWEN_PROJECT
        and bool(networks.get("youtube"))
    )
    if rescue:
        video_settings["copy_profile"] = RESCUE_PROFILE
    schedule = _compute_schedule(total_videos, per_day, best_hour, rescue=rescue)

    project = {
        "id": uuid.uuid4().hex[:10],
        "name": "",  # se completa bajo el lock, con el conteo real de lotes
        "type": content_type,
        "qwen_project": qwen_project,
        "trigger_message": trigger_message,
        "total_videos": total_videos,
        "per_day": per_day,
        "networks": networks,
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
            }
            for i in range(total_videos)
        ],
    }
    with _lock:
        projects = _load()
        project["name"] = _next_project_name(projects, qwen_project)
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


def delete_project(project_id: str) -> bool:
    """Borra el proyecto de batch_projects.json. El scheduler ya ignora todo
    proyecto que no este "running" (ver _batch_scheduler_tick), asi que borrar
    uno cancelado no corta ningun hilo en curso -- solo evita que se lo siga
    mostrando/reintentando. No borra videos ya publicados ni sus archivos."""
    with _lock:
        projects = _load()
        if project_id not in projects:
            return False
        del projects[project_id]
        _save(projects)
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


def retry_publish_video(project_id: str, index: int) -> bool:
    """Reintenta solo la publicacion de un video que ya se genero (video_path
    intacto) pero fallo publicando en alguna red: lo vuelve a "ready" sin
    tocar published_at, asi el proximo intento (_publish_batch_video) salta
    las redes que ya tuvieron exito y reintenta solo las que fallaron. NO usa
    "pending"/retry_video porque eso dispararia una regeneracion completa
    desde cero (nuevo guion, nuevas imagenes) para un video que solo fallo al
    subirse, no al crearse."""
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


def _requeue_healable_errors() -> None:
    """Vuelve a "pending" los videos en "error" cuyo fallo pinta de caida
    externa transitoria (ver _is_healable_error), respetando un cooldown
    exponencial y MAX_HEAL_CYCLES. Un video sin `error_at` (quedo en error
    antes de existir este mecanismo) es elegible de inmediato."""
    now = datetime.now()
    with _lock:
        projects = _load()
        changed = False
        for project in projects.values():
            if project.get("status") != "running":
                continue
            for v in project.get("videos", []):
                if v["status"] != "error" or not _is_healable_error(v.get("error")):
                    continue
                cycles = v.get("heal_cycles", 0)
                if cycles >= MAX_HEAL_CYCLES:
                    continue
                error_at = v.get("error_at")
                if error_at:
                    try:
                        wait = timedelta(seconds=HEAL_COOLDOWN_SECONDS * 2 ** cycles)
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
        _save(projects)

    vs = project["video_settings"]
    story_id = f"batch_{project_id}_{index:03d}"
    trigger_message = auto_pipeline.build_qwen_trigger_message(
        project.get("trigger_message", "dame una historia"),
        vs.get("duration_seconds"),
    )
    rescue = _is_rescue_project(project)
    if rescue:
        trigger_message += _rescue_history_block()
    try:
        story_text = _run_stage_with_retry(
            lambda: auto_pipeline.generate_story_from_qwen_project(
                project["qwen_project"],
                trigger_message=trigger_message,
            ),
            attempts=BROWSER_STAGE_RETRY_ATTEMPTS,
            on_retry=lambda attempt: _reset_session(auto_pipeline.QWEN_BATCH_SESSION),
            stage_label="guion",
        )
        hook_text = ""
        if rescue:
            hook_text, story_text = _split_hook_text(story_text)
        script_text = auto_pipeline.extract_script(story_text)
        script_text = auto_pipeline.cap_script_to_duration(script_text, vs.get("duration_seconds"))
        if rescue:
            forbidden = seo_optimizer.find_forbidden_terms(f"{script_text} {hook_text}")
            if forbidden:
                raise RuntimeError(
                    "Guion rechazado por el filtro de seguridad de contenido "
                    f"(vocabulario de daño explicito o de shock): {', '.join(forbidden)}"
                )
        story = auto_pipeline.load_story_from_text(story_text, story_id)
        clips_dir = video_maker.VIDEO_PUBLIC_DIR / story_id
        # Historia nueva: los clips de un intento anterior (reinicio de app.py,
        # video vuelto a "pending") son de OTRA historia. resume_index solo
        # sirve para los reintentos de esta misma historia, mas abajo.
        shutil.rmtree(clips_dir, ignore_errors=True)

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
        audio_path = _run_stage_with_retry(
            lambda: text_to_speech_long(
                script_text,
                voice=vs.get("voice", DEFAULT_VOICE),
                exaggeration=BEDTIME_PRESET["exaggeration"],
                cfg_weight=BEDTIME_PRESET["cfg_weight"],
            ),
            attempts=LOCAL_STAGE_RETRY_ATTEMPTS,
            stage_label="audio",
        )
        if not audio_path:
            raise RuntimeError("Error al generar el audio.")

        frases = [p["frase"] for p in sorted(story["prompts"], key=lambda p: p["index"])]
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
                )
                if not timeline:
                    return None
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
        v["script_text"] = script_text
        v["gen_attempts"] = 0
        v["heal_cycles"] = 0
        _save(projects)


GAMING_IMAGE_RATIO = "1:1"


def _generate_batch_image_post(project_id: str, index: int) -> None:
    import app

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
    trigger_message = project.get("trigger_message", "dame el próximo post gaming") + _gaming_history_block()

    try:
        reply = _run_stage_with_retry(
            lambda: auto_pipeline.generate_story_from_qwen_project(
                project["qwen_project"],
                trigger_message=trigger_message,
            ),
            attempts=BROWSER_STAGE_RETRY_ATTEMPTS,
            on_retry=lambda attempt: _reset_session(auto_pipeline.QWEN_BATCH_SESSION),
            stage_label="idea",
        )
        post = _parse_gaming_post(reply)

        _set_stage(project_id, index, "imagen")
        dest_dir = video_maker.VIDEO_PUBLIC_DIR / story_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / "cover.jpg"
        image_provider = project.get("video_settings", {}).get("image_provider", "qwen")

        def _do_generate_image():
            with app._clipgen_lock:
                if image_provider == "whatsapp":
                    story = {"prompts": [{
                        "index": 0,
                        "prompt": post["image_prompt"],
                        "frase": post.get("hook") or post["image_prompt"],
                    }]}
                    generated = auto_pipeline.generate_clips(
                        story, dest_dir, unattended=True, start_index=0,
                        provider="whatsapp", generate_video=False,
                    )
                    Path(generated[0]).replace(dest_path)
                else:
                    auto_pipeline.generate_qwen_image_in_session(
                        auto_pipeline.QWEN_BATCH_SESSION,
                        post["image_prompt"],
                        dest_path,
                        image_ratio=GAMING_IMAGE_RATIO,
                    )

        _run_stage_with_retry(
            _do_generate_image,
            attempts=BROWSER_STAGE_RETRY_ATTEMPTS,
            on_retry=lambda attempt: _reset_session(
                auto_pipeline.WHATSAPP_SESSION if image_provider == "whatsapp" else auto_pipeline.QWEN_BATCH_SESSION
            ),
            stage_label="imagen",
        )
        _normalize_cover_image(dest_path)
    except Exception as e:
        logger.exception("batch %s post %d: fallo la generacion", project_id, index)
        _record_generation_failure(project_id, index, e)
        return

    _append_gaming_history({
        "project_id": project_id,
        "index": index,
        "hook": post["hook"],
        "idea": post["idea"],
        "created_at": datetime.now().isoformat(),
    })
    with _lock:
        projects = _load()
        v = projects[project_id]["videos"][index]
        v["status"] = "ready"
        v["stage"] = None
        v["story_id"] = story_id
        v["video_path"] = str(dest_path)
        v["idea"] = post["idea"]
        v["caption"] = post["caption"]
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


_GAMING_POST_RE = re.compile(
    r"IDEA:\s*(.+?)\s*HOOK:\s*(.+?)\s*IMAGE_PROMPT:\s*(.+?)\s*CAPTION:\s*(.+)",
    re.DOTALL,
)


_GAMING_TRAILING_RE = re.compile(
    r"\n[ \t]*#{0,3}[ \t]*(?:PERFORMANCE GOAL|SERIE POTENT?IAL)\b", re.IGNORECASE
)


def _parse_gaming_post(text: str) -> dict:
    """Parsea la respuesta del Project de Qwen dedicado a posts gaming
    (formato IDEA/HOOK/IMAGE_PROMPT/CAPTION, ver plan). Saca los `**` antes
    de parsear -- Qwen suele resaltar los labels en negrita, igual que
    auto_pipeline._parse_story con el guion."""
    cleaned = text.replace("**", "")
    m = _GAMING_POST_RE.search(cleaned)
    if not m:
        raise auto_pipeline.PipelineError(
            "La respuesta de Qwen no vino en el formato esperado "
            "(IDEA/HOOK/IMAGE_PROMPT/CAPTION)."
        )
    idea, hook, image_prompt, caption = (g.strip() for g in m.groups())
    # El manual v2 pide PERFORMANCE GOAL y SERIE POTENTIAL tras el caption:
    # son notas internas, no van al post.
    caption = _GAMING_TRAILING_RE.split(caption)[0].strip()
    return {"idea": idea, "hook": hook, "image_prompt": image_prompt, "caption": caption}


GAMING_HISTORY_STORE_NAME = "gaming_post_history"
GAMING_HISTORY_LIMIT = 15  # entradas que se listan en el prompt
GAMING_HISTORY_MAX = 60  # entradas guardadas antes de podar


def _load_gaming_history() -> list:
    return job_store.load(GAMING_HISTORY_STORE_NAME).get("entries", [])


def _append_gaming_history(entry: dict) -> None:
    with _lock:
        store = job_store.load(GAMING_HISTORY_STORE_NAME)
        entries = store.get("entries", [])
        entries.append(entry)
        store["entries"] = entries[-GAMING_HISTORY_MAX:]
        job_store.save(GAMING_HISTORY_STORE_NAME, store)


def _gaming_history_block(limit: int = GAMING_HISTORY_LIMIT) -> str:
    entries = _load_gaming_history()[-limit:]
    if not entries:
        return ""
    lines = "\n".join(f"- {e['hook']}" for e in entries)
    return (
        "\n\nNo repitas ninguna de estas ideas/hooks/conceptos visuales ya "
        f"usados en posts anteriores:\n{lines}"
    )


def _normalize_cover_image(path: Path) -> None:
    """Red de seguridad independiente de lo que _select_qwen_image_ratio
    ("1:1") realmente haya producido en vivo: recorta al centro a cuadrado
    real y re-guarda como JPEG."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if w != h:
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            img = img.crop((left, top, left + side, top + side))
        img.save(path, "JPEG", quality=90)


def get_public_base_url() -> Optional[str]:
    return job_store.load("batch_settings").get("public_base_url") or None


def set_public_base_url(url: str) -> None:
    with _lock:
        settings = job_store.load("batch_settings")
        settings["public_base_url"] = (url or "").strip().rstrip("/")
        job_store.save("batch_settings", settings)


def _public_image_url(project_id: str, index: int) -> Optional[str]:
    base = get_public_base_url()
    if not base:
        return None
    return f"{base}/api/batch/cover/{project_id}/{index}"


def _build_publish_content(script_text: str, fallback_title: str, rescue: bool = False) -> dict:
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
    seo = seo_optimizer.suggest_seo(script_text, rescue=rescue)
    if rescue:
        facebook_description = seo_optimizer.suggest_rescue_copy(script_text, "facebook")
        instagram_description = seo_optimizer.suggest_rescue_copy(script_text, "instagram")
    else:
        social = seo_optimizer.suggest_social_caption(script_text)
        facebook_description = social["caption"]
        if social["hashtags"]:
            facebook_description += "\n\n" + " ".join(social["hashtags"])
        instagram_description = facebook_description
    return {
        "title": seo["title"] or fallback_title,
        "yt_description": seo["description"],
        "yt_tags": seo["tags"],
        "facebook_description": facebook_description,
        "instagram_description": instagram_description,
    }


def _publish_networks(project_id: str, index: int, publishers: dict) -> None:
    """Nucleo compartido de publicacion (gap-check + registro + resolucion de
    estado final), agnostico de que red hace que -- reusado por
    _publish_batch_video (video, hasta 3 redes) y _publish_batch_image_post
    (imagen, solo Facebook/Instagram). `publishers` mapea network -> callable
    (video, page_id) -> result dict {"ok": bool, "error": str, ...}."""
    projects = _load()
    project = projects.get(project_id)
    if not project:
        return
    video = project["videos"][index]
    networks = project["networks"]
    import app

    rescheduled = False
    error_parts = []
    any_auth_error = False
    for network, publish_fn in publishers.items():
        cfg = networks.get(network)
        if not cfg or video["published_at"].get(network):
            continue
        published_path = getattr(app, _NETWORK_PUBLISHED_PATH_ATTR[network])
        page_id = cfg.get("page_id") if isinstance(cfg, dict) else None
        with _publish_lock:
            if not _gap_ok(published_path):
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
            v["last_publish_auth_error"] = True
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
        else:
            v["status"] = "ready"
            if rescheduled:
                v["scheduled_at"] = _into_publish_window(
                    datetime.now() + timedelta(seconds=BATCH_MIN_GAP_SECONDS)
                ).isoformat()
        _save(projects)


def _publish_batch_video(project_id: str, index: int) -> None:
    import app

    project = get_project(project_id)
    video = project["videos"][index]
    content = _build_publish_content(video.get("script_text", ""), project["name"], rescue=_is_rescue_project(project))
    title = content["title"]

    def _yt(v, page_id):
        result = youtube_publisher.publish_video(
            v["video_path"], title, content["yt_description"], "unlisted", content["yt_tags"], True
        )
        if result.get("ok"):
            app._record_published_video(result["video_id"], title, Path(v["video_path"]).name)
        return result

    def _fb(v, page_id):
        result = facebook_publisher.publish_video(
            v["video_path"], title, content["facebook_description"], page_id=page_id
        )
        if result.get("ok"):
            app._record_published_facebook(result["video_id"], title, Path(v["video_path"]).name, page_id)
        return result

    def _ig(v, page_id):
        result = instagram_publisher.publish_video(
            v["video_path"], title, content["instagram_description"], page_id=page_id
        )
        if result.get("ok"):
            app._record_published_instagram(result["media_id"], title, Path(v["video_path"]).name, page_id)
        return result

    _publish_networks(project_id, index, {"youtube": _yt, "facebook": _fb, "instagram": _ig})


def _publish_batch_image_post(project_id: str, index: int) -> None:
    import app

    project = get_project(project_id)
    video = project["videos"][index]
    caption = video.get("caption", "")

    def _fb(v, page_id):
        result = facebook_publisher.publish_photo(v["video_path"], caption, page_id=page_id)
        if result.get("ok"):
            app._record_published_facebook(result["post_id"], project["name"], Path(v["video_path"]).name, page_id)
        return result

    def _ig(v, page_id):
        url = _public_image_url(project_id, index)
        if not url:
            return {"ok": False, "error": "Falta configurar la URL pública del Cloudflare Tunnel (pestaña Ajustes)."}
        result = instagram_publisher.publish_photo(url, caption, page_id=page_id)
        if result.get("ok"):
            app._record_published_instagram(result["media_id"], project["name"], Path(v["video_path"]).name, page_id)
        return result

    _publish_networks(project_id, index, {"facebook": _fb, "instagram": _ig})  # nunca youtube


def _generate_batch_item(project_id: str, index: int, content_type: str) -> None:
    if content_type == "gaming_image":
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
        _save(projects)


def _publish_batch_item(project_id: str, index: int, content_type: str) -> None:
    try:
        if content_type == "gaming_image":
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
                    args=(project["id"], next_pending["index"], project.get("type", "video")),
                    daemon=True,
                ).start()
                break

    now = datetime.now()
    in_window = BATCH_HOUR_START <= now.hour <= BATCH_HOUR_END
    for project in running:
        project_id = project["id"]
        videos = project["videos"]
        for v in videos:
            # Fuera de 9-20 no se publica nada, ni siquiera lo vencido (caida
            # larga, espera de espaciado): sale a la primera hora habil.
            if v["status"] != "ready" or not in_window:
                continue
            try:
                scheduled = datetime.fromisoformat(v["scheduled_at"])
            except (TypeError, ValueError):
                scheduled = now
            if scheduled <= now and _claim_for_publish(project_id, v["index"]):
                threading.Thread(
                    target=_publish_batch_item,
                    args=(project_id, v["index"], project.get("type", "video")),
                    daemon=True,
                ).start()

        if videos and all(v["status"] == "published" for v in videos):
            with _lock:
                fresh = _load()
                if project_id in fresh:
                    fresh[project_id]["status"] = "done"
                    _save(fresh)


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
                    if not (BATCH_HOUR_START <= dt.hour <= BATCH_HOUR_END):
                        clamped_hour = BATCH_HOUR_START if dt.hour < BATCH_HOUR_START else BATCH_HOUR_END
                        v["scheduled_at"] = dt.replace(
                            hour=clamped_hour, minute=0, second=0, microsecond=0
                        ).isoformat()
                        changed = True
        if changed:
            _save(projects)


_SCHEDULER_LOCK_PATH = Path("output/job_state/.scheduler.lock")
_scheduler_lock_file = None  # referencia global: mantener el handle abierto sostiene el lock


def _acquire_scheduler_lock() -> bool:
    """Lock exclusivo de SO (no de threading.Lock, que solo protege dentro de
    un mismo proceso) para que nunca haya 2 procesos con scheduler propio
    escribiendo batch_projects.json a la vez -- eso rompe MAX_AUTO_RETRIES
    (cada proceso lee/incrementa/escribe el contador sin ver al otro) y hace
    que 2 llamadas a Qwen compartiendo la misma sesion de browser se pisen.
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
