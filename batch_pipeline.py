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
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import auto_pipeline
import video_maker
import facebook_publisher
import instagram_publisher
import youtube_publisher
import feedback_analyzer
import job_store
import seo_optimizer
from tts_engine import text_to_speech_long, DEFAULT_VOICE, BEDTIME_PRESET

logger = logging.getLogger(__name__)

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

_TRANSIENT_ERROR_MARKERS = (
    "10060", "10061", "no respondio a tiempo", "timeout esperando",
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
    raise type(last_exc)(f"tras {attempts} intento(s): {last_exc}") from last_exc

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


def _compute_schedule(total: int, per_day: int, best_hour: Optional[int]) -> list:
    """Reparte `total` publicaciones en dias de `per_day`, usando los
    DAYPARTS fijos de feedback_analyzer como horarios del dia (filtrados a
    BATCH_HOUR_START-BATCH_HOUR_END -- nunca se publica en la madrugada,
    aunque quede "circularmente cerca" de best_hour), ordenados empezando
    por el mas cercano a `best_hour` (o el orden fijo si no hay dato). Si
    `per_day` supera la cantidad de dayparts filtrados, agrega horas extra
    1h despues de la ultima, recortando (wrap) a BATCH_HOUR_START al llegar
    a BATCH_HOUR_END para que la extension tampoco se escape del rango.
    Devuelve `total` timestamps ISO."""
    base_hours = [h for _, _, h in feedback_analyzer.DAYPARTS if BATCH_HOUR_START <= h <= BATCH_HOUR_END]
    if best_hour is not None:
        base_hours = sorted(base_hours, key=lambda h: min(abs(h - best_hour), 24 - abs(h - best_hour)))
    hours_for_day = list(base_hours)
    while len(hours_for_day) < per_day:
        next_hour = hours_for_day[-1] + 1
        if next_hour > BATCH_HOUR_END:
            next_hour = BATCH_HOUR_START
        hours_for_day.append(next_hour)

    now = datetime.now()
    schedule = []
    for i in range(total):
        day = i // per_day
        slot = i % per_day
        hour = hours_for_day[slot] % 24
        day_date = (now + timedelta(days=day)).date()
        when = datetime(day_date.year, day_date.month, day_date.day, hour)
        schedule.append(when.isoformat())
    return schedule


def create_project(name: str, qwen_project: str, total_videos: int, per_day: int,
                    networks: dict, video_settings: dict,
                    trigger_message: str = "dame una historia") -> dict:
    """networks = {"youtube": bool, "facebook": {"page_id": str} | None,
    "instagram": {"page_id": str} | None}. trigger_message es el mensaje que
    se manda al Project de Qwen para pedir la historia -- distintos Projects
    pueden esperar frases distintas segun como este configurado su system
    prompt."""
    total_videos = max(1, int(total_videos))
    per_day = max(1, int(per_day))
    best_hour = _best_hour_for_networks(networks)
    schedule = _compute_schedule(total_videos, per_day, best_hour)

    project = {
        "id": uuid.uuid4().hex[:10],
        "name": name,
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
            }
            for i in range(total_videos)
        ],
    }
    with _lock:
        projects = _load()
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


def _generate_batch_video(project_id: str, index: int) -> None:
    import app

    with _lock:
        projects = _load()
        project = projects.get(project_id)
        if not project:
            return
        project["videos"][index]["status"] = "generating"
        project["videos"][index]["stage"] = "guion"
        _save(projects)

    vs = project["video_settings"]
    story_id = f"batch_{project_id}_{index:03d}"
    trigger_message = auto_pipeline.build_qwen_trigger_message(
        project.get("trigger_message", "dame una historia"),
        vs.get("duration_seconds"),
    )
    try:
        story_text = _run_stage_with_retry(
            lambda: auto_pipeline.generate_story_from_qwen_project(
                project["qwen_project"],
                trigger_message=trigger_message,
            ),
            attempts=BROWSER_STAGE_RETRY_ATTEMPTS,
            on_retry=lambda attempt: auto_pipeline.hard_reset_browser_session(
                auto_pipeline.QWEN_BATCH_SESSION
            ),
            stage_label="guion",
        )
        script_text = auto_pipeline.extract_script(story_text)
        script_text = auto_pipeline.cap_script_to_duration(script_text, vs.get("duration_seconds"))
        story = auto_pipeline.load_story_from_text(story_text, story_id)
        clips_dir = video_maker.VIDEO_PUBLIC_DIR / story_id

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
                (lambda attempt: auto_pipeline.hard_reset_browser_session(images_session))
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
        subtitle_style = video_maker.get_subtitle_preset_style(vs.get("subtitle_preset", ""))

        def _do_render():
            with app._video_render_lock:
                timeline = video_maker.build_props(
                    image_paths=clip_paths,
                    audio_path=audio_path,
                    title="",
                    subtitles_enabled=vs.get("subtitles_enabled", True),
                    subtitle_style=subtitle_style,
                    frases=frases,
                    animate_images=vs.get("animate_images", True),
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
        with _lock:
            projects = _load()
            v = projects[project_id]["videos"][index]
            v["status"] = "error"
            v["error"] = str(e)
            _save(projects)
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


def _build_publish_content(script_text: str, fallback_title: str) -> dict:
    """Genera titulo/descripcion/tags igual que el flujo manual (mismas
    funciones que usan /api/seo/suggest y /api/seo/suggest-social) para que
    el lote nunca publique con esos campos vacios. El titulo se comparte
    entre las 3 redes -- el flujo manual hace lo mismo (una sola caja de
    titulo reusada para YouTube/Facebook/Instagram)."""
    if not script_text:
        return {"title": fallback_title, "yt_description": "", "yt_tags": [], "social_description": ""}
    seo = seo_optimizer.suggest_seo(script_text)
    social = seo_optimizer.suggest_social_caption(script_text)
    social_description = social["caption"]
    if social["hashtags"]:
        social_description += "\n\n" + " ".join(social["hashtags"])
    return {
        "title": seo["title"] or fallback_title,
        "yt_description": seo["description"],
        "yt_tags": seo["tags"],
        "social_description": social_description,
    }


def _publish_batch_video(project_id: str, index: int) -> None:
    import app

    projects = _load()
    project = projects.get(project_id)
    if not project:
        return
    video = project["videos"][index]
    networks = project["networks"]
    video_path = video["video_path"]
    content = _build_publish_content(video.get("script_text", ""), project["name"])
    title = content["title"]

    rescheduled = False
    for network, path_attr in _NETWORK_PUBLISHED_PATH_ATTR.items():
        cfg = networks.get(network)
        if not cfg or video["published_at"].get(network):
            continue
        published_path = getattr(app, path_attr)
        page_id = cfg.get("page_id") if isinstance(cfg, dict) else None
        with _publish_lock:
            if not _gap_ok(published_path):
                rescheduled = True
                continue
            try:
                if network == "youtube":
                    result = youtube_publisher.publish_video(
                        video_path, title, content["yt_description"], "unlisted", content["yt_tags"], True
                    )
                    if result.get("ok"):
                        app._record_published_video(result["video_id"], title, Path(video_path).name)
                elif network == "facebook":
                    result = facebook_publisher.publish_video(
                        video_path, title, content["social_description"], page_id=page_id
                    )
                    if result.get("ok"):
                        app._record_published_facebook(result["video_id"], title, Path(video_path).name, page_id)
                else:
                    result = instagram_publisher.publish_video(
                        video_path, title, content["social_description"], page_id=page_id
                    )
                    if result.get("ok"):
                        app._record_published_instagram(result["media_id"], title, Path(video_path).name, page_id)
            except Exception as e:
                logger.exception("batch %s video %d: fallo publicando en %s", project_id, index, network)
                result = {"ok": False, "error": str(e)}

            with _lock:
                projects = _load()
                v = projects[project_id]["videos"][index]
                if result.get("ok"):
                    v["published_at"][network] = datetime.now().isoformat()
                else:
                    v["error"] = f"{network}: {result.get('error')}"
                _save(projects)

    with _lock:
        projects = _load()
        v = projects[project_id]["videos"][index]
        active_networks = [n for n in _NETWORK_PUBLISHED_PATH_ATTR if networks.get(n)]
        if active_networks and all(v["published_at"].get(n) for n in active_networks):
            v["status"] = "published"
        else:
            v["status"] = "ready"
            if rescheduled:
                v["scheduled_at"] = (datetime.now() + timedelta(seconds=BATCH_MIN_GAP_SECONDS)).isoformat()
        _save(projects)


def _batch_scheduler_tick() -> None:
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
                    target=_generate_batch_video, args=(project["id"], next_pending["index"]), daemon=True
                ).start()
                break

    now = datetime.now()
    for project in running:
        project_id = project["id"]
        videos = project["videos"]
        for v in videos:
            if v["status"] != "ready":
                continue
            try:
                scheduled = datetime.fromisoformat(v["scheduled_at"])
            except (TypeError, ValueError):
                scheduled = now
            if scheduled <= now and _claim_for_publish(project_id, v["index"]):
                threading.Thread(target=_publish_batch_video, args=(project_id, v["index"]), daemon=True).start()

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


def start_scheduler() -> None:
    """Arranca el loop del scheduler en un hilo daemon unico (llamar una sola
    vez al iniciar app.py). Relee batch_projects.json en cada vuelta: un
    reinicio del server simplemente retoma desde el ultimo estado guardado,
    salvo los videos que hayan quedado "generating" de la corrida anterior
    (ver _recover_stuck_videos)."""
    _recover_stuck_videos()

    def loop():
        while True:
            try:
                _batch_scheduler_tick()
            except Exception:
                logger.exception("batch scheduler: fallo un tick")
            time.sleep(TICK_SECONDS)

    threading.Thread(target=loop, daemon=True).start()
