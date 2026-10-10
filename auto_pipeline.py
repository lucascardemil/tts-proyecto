"""
Automatiza el ciclo historia -> clips de video: toma el texto de una historia
ya generada a mano en ChatGPT (pegado en un archivo de texto) y usa
agent-browser para generar y animar cada imagen en WhatsApp Web / Meta IA.

ChatGPT bloquea el navegador automatizado con un captcha de Cloudflare que no
se puede resolver por script, asi que la extraccion de la historia NO es
automatica: el usuario pega el texto completo de la respuesta de ChatGPT
(guion + prompts de imagen) en un .txt y se lo pasa al script con --from-file.

WhatsApp Web si se automatiza por UI con agent-browser -- requiere sesion
logueada persistente (usa --restore para no tener que re-escanear el QR en
cada corrida).

Uso:
    python auto_pipeline.py --from-file scratch/historia.txt
                             [--unattended] [--download-dir video/public]

Sin --unattended, pide confirmacion antes de la primera escritura en el chat
de WhatsApp; con --unattended corre todo el lote sin pausas.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from typing import Optional
import threading
import time
import urllib.request
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
logger = logging.getLogger("pipeline")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)

PROJECT_ROOT = Path(__file__).parent
SCRATCH_DIR = PROJECT_ROOT / "scratch"
DEFAULT_SCENES_DIR = PROJECT_ROOT / "video" / "public"

AGENT_BROWSER_CMD = "agent-browser.cmd" if sys.platform == "win32" else "agent-browser"

WHATSAPP_SESSION = "whatsapp"
IMAGE_MARKER = "Imagen generada por Meta AI"
VIDEO_MARKER = "video"  # a confirmar contra el DOM real tras el primer 'animar'
# Meta AI a veces pierde el hilo de edicion (ej. tras un hard-reset del navegador
# o un cambio de sesion) y responde en texto en vez de generar la imagen. Estas
# frases no aparecen nunca en una respuesta exitosa (esa siempre trae el
# IMAGE_MARKER), asi que sirven para detectar el fallo sin esperar el timeout
# completo y reintentar el prompt automaticamente.
IMAGE_FAILURE_MARKERS = (
    "no pude retomar la imagen anterior",
    "el archivo base ya no está disponible",
    "el archivo base ya no esta disponible",
    "no pude generar esta imagen",
    "no pude generar la imagen",
    "no puedo generar esta imagen",
    "no puedo generar esa imagen",
)
# Subconjunto de IMAGE_FAILURE_MARKERS que indica rechazo por politicas de
# contenido (encuadre/detalle grafico), no perdida de hilo de edicion -- en
# este caso reenviar el MISMO prompt tal cual vuelve a fallar casi siempre;
# hace falta suavizarlo.
IMAGE_CONTENT_REFUSAL_MARKERS = (
    "no pude generar esta imagen",
    "no pude generar la imagen",
    "no puedo generar esta imagen",
    "no puedo generar esa imagen",
)
# Meta AI redacta el rechazo con frases variables ("no pude generar esta
# imagen" / "no pude generar esa toma extrema del cuello..." / etc) -- las
# listas de arriba son frases exactas y no cubren toda la variacion. Este
# regex generaliza el patron comun a todas: "no pud(e|o) generar".
IMAGE_REFUSAL_RE = re.compile(r"no (?:se )?pud[eo] generar", re.IGNORECASE)
# Falla del sistema de Meta AI (no del contenido): "No se pudo generar la
# Imagen 1 ... El sistema de generacion fallo ... dime 'intentar de nuevo'".
# El mismo prompt suele salir bien al reenviarlo, asi que no se suaviza.
IMAGE_TRANSIENT_MARKERS = ("intentar de nuevo", "sistema de generación falló", "sistema de generacion fallo")
IMAGE_RETRY_ATTEMPTS = 3

# Vocabulario grafico que Meta AI rechaza seguido en el PRIMER intento (no solo
# en el reintento) cuando el prompt viene de ChatGPT describiendo heridas,
# quemaduras o sangre de forma explicita -- ver historia_preview.json del
# 2026-09-10, prompt de "Imagen 1" con "burn marks"/"soot" rechazado de
# entrada. Se aplica ya en _parse_story, antes de mandar nada, para no
# depender solo del reintento reactivo (IMAGE_CONTENT_REFUSAL_MARKERS).
GRAPHIC_TERM_REPLACEMENTS = (
    (re.compile(r"[,;]?\s*no open wounds\b", re.IGNORECASE), ""),
    (re.compile(r"\bsmall\s+superficial\s+burn\s+marks?\b", re.IGNORECASE), "slightly singed fur"),
    (re.compile(r"\bburn\s+marks?\b", re.IGNORECASE), "singed patches"),
    (re.compile(r"\bburn(?:ed|t)\b", re.IGNORECASE), "damaged"),
    (re.compile(r"\bsoot\b", re.IGNORECASE), "dust and grime"),
    (re.compile(r"\bwound(?:s)?\b", re.IGNORECASE), "scuffs"),
    (re.compile(r"\binjur(?:y|ies|ed)\b", re.IGNORECASE), "roughed up"),
    (re.compile(r"\bblood(?:y|stained)?\b", re.IGNORECASE), ""),
)


_ASPECT_RATIO_RE = re.compile(r"\b\d{1,2}\s*:\s*\d{1,2}\b")


def _ensure_vertical(prompt: str) -> str:
    """El generador de imagenes (Meta AI) solo respeta el formato si el prompt lo
    nombra; sin eso entrega 3:2 y el video vertical recorta las escenas. Si el modelo
    de texto no puso ninguna proporcion ("9:16", "16:9"...), se antepone la vertical."""
    if _ASPECT_RATIO_RE.search(prompt):
        return prompt
    return "Vertical 9:16 aspect ratio, portrait orientation. " + prompt


def _soften_prompt(prompt: str) -> str:
    """Reemplaza vocabulario grafico (quemaduras, hollin, heridas, sangre) por
    equivalentes mas suaves que Meta AI no rechaza, sin cambiar el sujeto ni
    la escena. Ver GRAPHIC_TERM_REPLACEMENTS."""
    softened = prompt
    for pattern, replacement in GRAPHIC_TERM_REPLACEMENTS:
        softened = pattern.sub(replacement, softened)
    softened = " ".join(softened.split())
    if softened != prompt:
        logger.info("prompt suavizado (vocabulario grafico removido): %.80s...", softened)
    return softened


_PHOTOREALISTIC_RE = re.compile(r"\bphotorealistic\b", re.IGNORECASE)
VISUAL_STYLE_PROMPTS = {
    "pixar3d": (
        "3D animated style, Pixar-inspired but semi-realistic adult characters, "
        "soft cinematic lighting, highly expressive faces, detailed skin texture, "
        "emotional storytelling, 8k, Unreal Engine 5 render"
    ),
}


def apply_visual_style(prompt: str, style: str) -> str:
    """Reemplaza el estilo visual del prompt de imagen (por defecto
    "photorealistic", fijo en el system prompt del generador de texto) por el
    estilo elegido en el lote. Si el prompt no trae la palabra "photorealistic"
    (el modelo cambia la redaccion), antepone el estilo igual, para no depender
    del texto exacto que devuelva el modelo."""
    style_text = VISUAL_STYLE_PROMPTS.get(style)
    if not style_text:
        return prompt
    if _PHOTOREALISTIC_RE.search(prompt):
        return _PHOTOREALISTIC_RE.sub(style_text, prompt)
    return f"{style_text}, {prompt}"


WHATSAPP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
# Perfil de Chrome persistente (incluye IndexedDB, donde WhatsApp Web guarda las
# claves de sesion multi-dispositivo). --restore (cookies+localStorage nomas) no
# alcanza: un cierre/reapertura del daemon (p.ej. el boton "Reiniciar") perdia el
# login y forzaba escanear QR de nuevo aunque la sesion estuviera sana.
WHATSAPP_PROFILE_DIR = str(Path.home() / ".agent-browser-profiles" / "whatsapp")

# Estado propio de agent-browser: <session>.pid tiene el PID vivo del daemon
# (mantenido por la herramienta), y browsers/ tiene los Chrome que descarga y
# controla (aislados del Chrome personal del usuario y del chrome-headless-shell
# de Remotion, que viven en paths distintos). Usados por hard_reset_browser_session
# para matar exactamente el proceso correcto sin ambiguedad.
AGENT_BROWSER_STATE_DIR = Path.home() / ".agent-browser"
AGENT_BROWSER_BROWSERS_DIR = AGENT_BROWSER_STATE_DIR / "browsers"
_SINGLETON_LOCK_NAMES = ("SingletonLock", "SingletonCookie", "SingletonSocket")

# "flow" = Google Flow (flow_images.py): solo imagenes, sin clips de video.
PROVIDERS = ("whatsapp", "flow")

POLL_INTERVAL_SECONDS = 4
IMAGE_TIMEOUT_SECONDS = 600
CLIP_TIMEOUT_SECONDS = 600


class PipelineError(Exception):
    pass


class MetaAIFailure(PipelineError):
    """Meta AI respondio con texto de fallo (ej. 'no pude retomar la imagen
    anterior') en vez de generar la imagen pedida."""
    pass


class MetaAIAlternativeOffered(MetaAIFailure):
    """Meta AI rechazo el pedido pero ofrecio una version alternativa y
    pregunto si la genera (el mensaje termina en '?'). A diferencia de un
    rechazo liso, esto se resuelve aceptando ('Si') en vez de reformular
    el prompt de cero."""
    pass


# El daemon de agent-browser atiende un solo comando genuino a la vez por sesion
# (una unica conexion CDP a Chrome); si dos comandos le llegan en paralelo (p.ej.
# el poll de estado desde Ajustes justo cuando se dispara un reconnect), el
# segundo se queda esperando indefinidamente en vez de fallar rapido. Este lock
# serializa todo acceso a una sesion dada para que eso no pueda pasar.
_session_locks: dict = {}
_session_locks_guard = threading.Lock()


def _get_session_lock(session: str) -> threading.RLock:
    with _session_locks_guard:
        if session not in _session_locks:
            _session_locks[session] = threading.RLock()  # RLock: _run_agent_browser puede reentrar (retry 10061)
        return _session_locks[session]


def _run_cmd(cmd: list, timeout: int) -> subprocess.CompletedProcess:
    """subprocess.run con timeout real en Windows, seguro para comandos que
    lanzan un daemon persistente (agent-browser open con la sesion fria).

    Con stdout/stderr=PIPE, Popen.communicate() no vuelve cuando el proceso
    directo termina: espera a que la pipe se cierre del todo, y si ese hijo
    lanzo un daemon (node/chrome) que hereda esos handles -- como pasa al
    spawnear una sesion nueva -- la pipe se queda abierta mientras el daemon
    siga vivo, es decir para siempre (se vio en la practica: colgado 3+ min).
    El unico "arreglo" con PIPE es matar todo el arbol al timeout, pero eso
    mata tambien al daemon que se queria dejar corriendo, así que un cold
    spawn nunca puede terminar con exito. Por eso stdout/stderr van a
    archivos reales: proc.wait() solo espera a que el proceso directo
    termine (sin importar que sus hijos sigan vivos con el handle heredado),
    que es exactamente lo que se necesita para no matar al daemon recien
    lanzado."""
    with tempfile.NamedTemporaryFile(prefix="agentbrowser_out_", suffix=".log", delete=False) as _out_tmp:
        out_path = _out_tmp.name
    with tempfile.NamedTemporaryFile(prefix="agentbrowser_err_", suffix=".log", delete=False) as _err_tmp:
        err_path = _err_tmp.name
    try:
        with open(out_path, "wb") as out_fh, open(err_path, "wb") as err_fh:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=out_fh, stderr=err_fh,
                shell=(sys.platform == "win32"),
                close_fds=True,
            )
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True, timeout=10,
                    )
                else:
                    proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                stdout = _read_text_file(out_path)
                stderr = _read_text_file(err_path)
                raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
        stdout = _read_text_file(out_path)
        stderr = _read_text_file(err_path)
    finally:
        for _p in (out_path, err_path):
            try:
                os.unlink(_p)
            except OSError:
                pass
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _read_text_file(path: str) -> str:
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def _force_kill_session(session: str) -> None:
    """Mata el daemon de una sesion sin esperar respuesta limpia (best-effort). Se usa
    tras un timeout, cuando el daemon quedo en un estado que no responde a 'close' normal
    y dejaria procesos node/chrome huerfanos bloqueando la sesion para siempre."""
    try:
        _run_cmd(
            [AGENT_BROWSER_CMD, "--session", session, "close", "--all"],
            timeout=15,
        )
    except Exception:
        pass  # best-effort: si tampoco responde a esto, se ignora


def _base_cmd(session: str) -> list:
    """Argumentos base de agent-browser para direccionar y persistir una sesion.
    WhatsApp usa --profile (directorio de Chrome persistente, incluye IndexedDB)
    en vez de --restore (solo cookies+localStorage, insuficiente para su login).
    las demas sesiones siguen con --restore, que les alcanza."""
    cmd = [AGENT_BROWSER_CMD, "--session", session]
    if session == WHATSAPP_SESSION:
        cmd += ["--profile", WHATSAPP_PROFILE_DIR, "--user-agent", WHATSAPP_USER_AGENT]
    else:
        cmd += ["--restore", session]
    return cmd


def _run_agent_browser(args: list, session: str, _retry_on_10061: bool = True) -> str:
    """Corre un comando de agent-browser en la sesion dada y devuelve stdout.

    Si el daemon anterior todavia estaba terminando de cerrarse (error os 10061,
    "conexion denegada" justo tras cerrar/reabrir la sesion), reintenta una vez
    tras una pausa corta en vez de fallar de una: esta condicion de carrera puede
    darse tanto al reconectar desde Ajustes como cuando el pipeline abre la
    sesion mientras otro comando la estaba reiniciando."""
    cmd = _base_cmd(session) + args
    with _get_session_lock(session):
        try:
            result = _run_cmd(cmd, timeout=60)
        except subprocess.TimeoutExpired:
            # subprocess.run solo mata el proceso hijo directo (cmd.exe en Windows), no el
            # daemon node/chrome que ese proceso lanzo -- sin esta limpieza el daemon queda
            # huerfano y todo comando futuro en esta sesion se cuelga igual.
            _force_kill_session(session)
            raise PipelineError(
                f"agent-browser {' '.join(args)} no respondio a tiempo (60s). "
                "El daemon quedo colgado y se cerro; probá reconectar de nuevo."
            )
        if result.returncode != 0:
            if _retry_on_10061 and "10061" in result.stderr:
                time.sleep(2)
                return _run_agent_browser(args, session, _retry_on_10061=False)
            raise PipelineError(f"agent-browser {' '.join(args)} fallo: {result.stderr.strip()}")
        return result.stdout


def _read_daemon_pid(session: str) -> "int | None":
    """Lee el PID vivo del daemon desde el archivo que agent-browser mismo mantiene."""
    try:
        raw = (AGENT_BROWSER_STATE_DIR / f"{session}.pid").read_text(encoding="utf-8").strip()
        return int(raw)
    except (OSError, ValueError):
        return None


def _pid_is_agent_browser(pid: int) -> bool:
    """Confirma que el PID leido todavia corresponde a un proceso agent-browser
    (guarda contra reuso de PID por el SO si el daemon ya murio y otro proceso
    tomo ese numero)."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return "agent-browser" in (result.stdout or "").lower()


def _hard_kill_daemon(session: str) -> bool:
    """Mata el proceso OS real del daemon (y todo su arbol, incluido Chrome) via
    su PID, en vez de mandarle un comando que puede no responder si esta colgado."""
    pid = _read_daemon_pid(session)
    if pid is None or not _pid_is_agent_browser(pid):
        return False
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _sweep_orphaned_chrome() -> int:
    """Mata cualquier chrome.exe que cuelgue especificamente de las instalaciones
    propias de agent-browser (AGENT_BROWSER_BROWSERS_DIR), nunca el Chrome
    personal del usuario ni el chrome-headless-shell.exe de Remotion (paths
    distintos). Devuelve cuantos proceso se mataron."""
    ps_script = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($env:AB_BROWSERS_DIR) } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "AB_BROWSERS_DIR": str(AGENT_BROWSER_BROWSERS_DIR)},
        )
    except (subprocess.TimeoutExpired, OSError):
        return 0
    pids = [line.strip() for line in result.stdout.splitlines() if line.strip().isdigit()]
    killed = 0
    for pid in pids:
        try:
            r = subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=10)
            if r.returncode == 0:
                killed += 1
        except (subprocess.TimeoutExpired, OSError):
            pass
    return killed


def _clear_stale_singleton_locks() -> None:
    """Borra los locks de instancia unica de Chrome que quedan pisados tras un
    hard-kill (nunca cookies/IndexedDB/Login Data, asi se preserva el login)."""
    profile_dir = Path(WHATSAPP_PROFILE_DIR)
    for name in _SINGLETON_LOCK_NAMES:
        try:
            (profile_dir / name).unlink(missing_ok=True)
        except OSError:
            pass


def hard_reset_browser_session(session: str) -> dict:
    """Reinicio agresivo de una sesion de agent-browser: cierre educado corto y,
    pase lo que pase, mata el proceso OS real del daemon (via su .pid) mas
    cualquier chrome.exe huerfano y locks viejos, y devuelve el estado real
    verificado en vez de asumir exito a ciegas."""
    with _get_session_lock(session):
        try:
            _run_cmd([AGENT_BROWSER_CMD, "--session", session, "close"], timeout=10)
        except subprocess.TimeoutExpired:
            pass
        hard_killed = _hard_kill_daemon(session)
        orphans_killed = _sweep_orphaned_chrome()
        if session == WHATSAPP_SESSION:
            _clear_stale_singleton_locks()

    status = check_session_status(session)
    return {
        "ok": status["state"] in ("ok", "needs_login"),
        "hard_killed": hard_killed,
        "orphans_killed": orphans_killed,
        "status": status,
    }


def check_session_status(session: str, force_reload: bool = True) -> dict:
    """Chequeo de si la sesion de agent-browser esta logueada. Devuelve
    {"state": "ok"|"needs_login"|"unreachable", "message": str}.

    force_reload=False hace un chequeo pasivo (solo snapshot, sin navegar): usar
    mientras hay un QR en pantalla que el usuario puede estar escaneando, ya que
    recargar la pagina en ese momento regenera el QR y corta la vinculacion."""
    try:
        if session == WHATSAPP_SESSION:
            if force_reload:
                # Tras un daemon frio, Chrome a veces auto-restaura la ultima pestana antes
                # de que agent-browser aplique el --user-agent, y esa primera carga cae en
                # la pantalla de "navegador no soportado" de WhatsApp. Un open explicito
                # fuerza una navegacion nueva con el user-agent ya aplicado.
                _run_agent_browser(["open", "https://web.whatsapp.com/"], session)
                # La lista de chats tarda un par de segundos en montar tras el open frio;
                # sin este reintento corto, un chequeo justo despues de un restart daba
                # "needs_login" en falso aunque la sesion siguiera logueada.
                snap = ""
                for _ in range(6):
                    snap = _run_agent_browser(["snapshot", "-i"], session)
                    if "Meta AI" in snap:
                        break
                    time.sleep(1.5)
            else:
                snap = _run_agent_browser(["snapshot", "-i"], session)
        else:
            snap = _run_agent_browser(["snapshot", "-i"], session)
    except PipelineError as e:
        return {"state": "unreachable", "message": str(e)}

    if session == WHATSAPP_SESSION:
        if "Meta AI" in snap:
            return {"state": "ok", "message": "Sesion de WhatsApp Web activa."}
        return {"state": "needs_login", "message": "WhatsApp Web no esta logueado (falta escanear QR)."}
    return {"state": "unreachable", "message": f"Sesion desconocida: {session}"}


def screenshot_session(session: str, selector: str = None) -> Path:
    """Saca una captura de la sesion de agent-browser y devuelve el Path del PNG.
    Si se pasa `selector`, recorta la captura a ese elemento (p.ej. el <canvas>
    del QR, para que se vea grande y fácil de escanear en vez de la página entera)."""
    cmd = _base_cmd(session) + ["screenshot"]
    if selector:
        cmd += [selector]
    with _get_session_lock(session):
        try:
            result = _run_cmd(cmd, timeout=30)
        except subprocess.TimeoutExpired:
            _force_kill_session(session)
            raise PipelineError("agent-browser screenshot no respondio a tiempo (30s).")
        if result.returncode != 0:
            if selector:
                return screenshot_session(session)  # sin selector, captura de página completa
            raise PipelineError(f"agent-browser screenshot fallo: {result.stderr.strip()}")
    match = re.search(r"Screenshot saved to (.+)", result.stdout)
    if not match:
        raise PipelineError("No se pudo parsear el path de la captura.")
    return Path(match.group(1).strip())


DIAG_DIR = LOG_DIR / "diag"
DIAG_KEEP_FILES = 60


def _save_failure_diagnostics(session: str, label: str) -> None:
    """Guarda captura + cola del snapshot de la sesion en logs/diag/ justo antes
    de fallar por timeout/descarga, para saber despues POR QUE fallo (login
    vencido, modal, captcha, rate-limit, UI cambiada) -- sin esto el log solo
    dice "Timeout" y la pantalla real se pierde. Best-effort: nunca lanza."""
    try:
        DIAG_DIR.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = DIAG_DIR / f"{stamp}_{session}_{label}"
        try:
            shot = screenshot_session(session)
            base.with_suffix(".png").write_bytes(shot.read_bytes())
        except Exception as e:
            logger.warning("diag %s/%s: sin captura (%s)", session, label, e)
        try:
            snap = _run_agent_browser(["snapshot"], session)
            base.with_suffix(".txt").write_text(snap[-6000:], encoding="utf-8")
        except Exception as e:
            logger.warning("diag %s/%s: sin snapshot (%s)", session, label, e)
        logger.info("diag %s/%s guardado en %s", session, label, DIAG_DIR)
        for old in sorted(DIAG_DIR.glob("*"), key=lambda p: p.stat().st_mtime)[:-DIAG_KEEP_FILES]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


def screenshot_qr(session: str) -> Path:
    """Captura solo el <canvas> del QR (WhatsApp lo renderiza como canvas),
    con fallback a la página completa si no encuentra el elemento."""
    return screenshot_session(session, selector="canvas")


def open_login_page(session: str) -> None:
    """Abre la pagina de login del proveedor en la sesion dada (para reconexion).
    Reintenta una vez si el daemon anterior todavia estaba terminando de cerrarse
    (error os 10061, "conexion denegada" justo tras un hard_reset_browser_session)."""
    url = "https://web.whatsapp.com/"
    try:
        _run_agent_browser(["open", url], session)
    except PipelineError as e:
        if "10061" not in str(e):
            raise
        time.sleep(2)
        _run_agent_browser(["open", url], session)


def get_remote_devtools_url(session: str) -> str:
    """La sesion headless de agent-browser no tiene forma de escanear un QR. Esta
    funcion expone la sesion via Chrome DevTools remoto (CDP) para que el usuario
    pueda abrir esa pagina en vivo desde su propio Chrome y loguearse a mano
    (email/Google/lo que sea) directo en el navegador automatizado."""
    out = _run_agent_browser(["get", "cdp-url"], session)
    m = re.search(r"ws://([\d.:]+)/devtools/browser/", out)
    if not m:
        raise PipelineError("No pude obtener el endpoint CDP de la sesion.")
    host_port = m.group(1)
    with urllib.request.urlopen(f"http://{host_port}/json/list", timeout=5) as resp:
        pages = json.loads(resp.read())
    page = next(
        (p for p in pages if p.get("type") == "page" and not p.get("url", "").startswith("chrome://")),
        pages[0],
    )
    return page["devtoolsFrontendUrl"]


def _confirm(prompt: str, unattended: bool) -> None:
    if unattended:
        return
    answer = input(f"{prompt} [s/N]: ").strip().lower()
    if answer != "s":
        raise PipelineError("Cancelado por el usuario.")


# --- Etapa 1: historia (pegada a mano desde ChatGPT) -----------------------

def load_story_from_file(path: Path) -> dict:
    """Lee el texto de una historia (guion + prompts) pegado a mano en un .txt."""
    if not path.exists():
        raise PipelineError(f"No existe el archivo {path}.")
    text = path.read_text(encoding="utf-8")
    return _parse_story(text)


def load_story_from_text(text: str, story_id: str) -> dict:
    """Como load_story_from_file, pero para texto ya en memoria (pegado en la UI)."""
    return _parse_story(text, story_id=story_id)


_TRAILING_SECTION_RE = re.compile(
    r"\n[ \t]*#{0,3}[ \t]*(?:CAPTION|PERFORMANCE GOAL|SERIE POTENCIAL|GUION)\b",
    re.IGNORECASE,
)
# Heading de seccion "IMAGENES"/"IMÁGENES" (sola en su linea) que separa guion y prompts.
_IMAGES_HEADING_RE = re.compile(r"\n[ \t]*#{0,3}[ \t]*IM[ÁA]GENES[ \t]*:?[ \t]*(?:\n|$)", re.IGNORECASE)

# Ficha de personajes que el generador de texto escribe una sola vez antes del
# guion ("PERSONAJES:" + una linea "[LUNA]: descripcion detallada" por
# personaje). En cada prompt de imagen el modelo escribe solo la etiqueta [LUNA] y
# _parse_story la expande con la descripcion completa, textual: asi el
# personaje se repite identico en las 8-10 imagenes sin depender de que el modelo
# lo copie fielmente cada vez.
_CHARACTERS_HEADER_RE = re.compile(r"^[ \t]*#{0,3}[ \t]*PERSONAJES[ \t]*:?[ \t]*$", re.IGNORECASE | re.MULTILINE)
_CHARACTER_LINE_RE = re.compile(r"^[ \t]*-?[ \t]*\[([^\]\n]+)\][ \t]*:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
_CHARACTER_TAG_RE = re.compile(r"\[([^\]\n]+)\]")


# (Los personajes recurrentes con ficha fija en codigo se eliminaron: el
# rescatista rota en cada historia y su ficha la escribe el modelo en
# PERSONAJES, respetando la descripcion detallada que pide
# CHARACTER_SHEET_REQUEST.)


def _extract_characters(head: str) -> dict:
    """{ETIQUETA_EN_MAYUSCULAS: descripcion} de las lineas "[NOMBRE]: ..." del
    texto que precede a "Imagen 1". Vacio si no las trae."""
    return {
        m.group(1).strip().upper(): " ".join(m.group(2).split()).rstrip(".")
        for m in _CHARACTER_LINE_RE.finditer(head)
    }


def _strip_characters_block(text: str) -> str:
    """Saca el encabezado "PERSONAJES:" y las lineas "[NOMBRE]: ..." (no son
    narracion del guion)."""
    return _CHARACTER_LINE_RE.sub("", _CHARACTERS_HEADER_RE.sub("", text))


def _expand_characters(prompt: str, characters: dict) -> str:
    """Reemplaza cada etiqueta [NOMBRE] del prompt por su descripcion completa.
    La descripcion va entre parentesis, precedida del nombre, para que quede
    delimitada de la accion. Una etiqueta sin ficha se deja sin corchetes
    (queda el nombre suelto)."""
    if not characters:
        return prompt

    def _sub(m):
        name = m.group(1).strip()
        desc = characters.get(name.upper())
        if not desc:
            return m.group(1)
        return f"{name.title()} ({desc.rstrip(' .')})"

    return _CHARACTER_TAG_RE.sub(_sub, prompt)


_HEADING_END = r"(?=[ \t]*(?:$|/))"
_IMAGE_HEADING = r"(?<!\w)#{0,3}[ \t]*Imagen[ \t]*(\d+)" + _HEADING_END
_IMAGE_HEADING_NOCAP = r"(?<!\w)#{0,3}[ \t]*Imagen[ \t]*\d+" + _HEADING_END
_IMAGE_BLOCK_RE = re.compile(
    _IMAGE_HEADING + r".*?Frase(?: del guion)?:\s*[«\"](.+?)[»\"].*?Prompt:\s*(.+?)(?=" + _IMAGE_HEADING_NOCAP + r"|\Z)",
    re.DOTALL | re.MULTILINE,
)


def has_image_blocks(text: str) -> bool:
    """True si el texto trae al menos un bloque "Imagen N / Frase / Prompt"
    real. Un modelo de razonamiento que vuelca su pensamiento ("Then Imagen 1,
    Imagen 2...") o una respuesta cortada por max_tokens no lo cumplen."""
    return bool(_IMAGE_BLOCK_RE.search(text.replace("**", "")))


def _parse_story(text: str, story_id: str = None) -> dict:
    """Extrae guion y prompts de imagen del texto de la historia.

    Con `story_id`, escribe/sobreescribe siempre el mismo archivo de scratch
    (para que un reintento edite el mismo JSON en vez de acumular uno nuevo
    por corrida). Sin `story_id` (uso CLI actual), mantiene el nombre con
    timestamp de siempre.
    """
    # El heading "Imagen N" no puede tener nada mas que espacios despues del
    # numero en su linea -- si no se exige esto, un fragmento pegado por
    # error como "Imagen 1, vertical 9:16, no text..." (texto suelto que
    # arranca con "Imagen 1" pero sigue con mas contenido en la misma linea,
    # no es un heading real) matchea igual, "roba" el bloque de la imagen
    # real que le sigue y la hace desaparecer del resultado. No se exige que
    # el heading empiece la linea (a veces queda pegado al final del guion,
    # p. ej. "...oportunidad? Imagen 1"), solo que no venga pegado a otra
    # palabra.
    # El modelo suele resaltar las etiquetas "Frase"/"Prompt" en negrita markdown
    # (p. ej. "**Frase:** \"...\""), formato que usa literalmente el archivo
    # workflow_maestro_reels_9x16.md (Project CONTENIDO DIARIO DE MAGRAME).
    # El "**" entre "Frase:"/"Prompt:" y el valor rompe el regex de abajo, que
    # exige la comilla/valor inmediatamente después del ":" -- se lo saca acá
    # antes de parsear, ya que nunca es contenido real del guion/prompt.
    text = text.replace("**", "")

    # El numero de imagen puede venir solo al final de su linea ("Imagen 5\n")
    # o, cuando el modelo comprime el bloque en una sola linea con "/" como
    # separador ("Imagen 5 / Frase del guion: ... / Prompt: ..."), seguido
    # de espacios y una "/". Antes solo se aceptaba fin de linea (\Z/$) y
    # ese segundo formato (confirmado en vivo, alterna con el primero para
    # otra corrida del lote) hacia fallar el regex entero -> "No se
    # encontraron prompts de imagen" aunque la respuesta viniera completa.
    starts = [m.start() for m in re.finditer(r"(?<!\w)#{0,3}[ \t]*Imagen[ \t]*1" + _HEADING_END, text, re.MULTILINE)]
    sheet = _extract_characters(text[: starts[-1]]) if starts else {}
    characters = sheet
    if starts:
        text = text[starts[-1]:]

    prompts = []
    for match in _IMAGE_BLOCK_RE.finditer(text):
        idx, frase, prompt = match.groups()
        # El ultimo bloque absorbe todo lo que el modelo agregue despues (caption,
        # performance goal, serie potencial de los manuales v2): no es prompt.
        prompt = _TRAILING_SECTION_RE.split(prompt)[0]
        prompt = " ".join(prompt.split()).strip()
        if sheet and not _CHARACTER_TAG_RE.search(prompt):
            logger.warning("prompt de Imagen %s sin etiqueta de personaje: no se repite la descripcion", idx)
        prompts.append({
            "index": int(idx),
            "frase": frase.strip(),
            "prompt": _ensure_vertical(_soften_prompt(_expand_characters(prompt, characters))),
        })

    if not prompts:
        raise PipelineError(
            "No se encontraron prompts de imagen en la respuesta. "
            "Puede que el formato del guion haya cambiado."
        )

    prompts.sort(key=lambda p: p["index"])

    SCRATCH_DIR.mkdir(exist_ok=True)
    out_name = f"historia_{story_id}.json" if story_id else f"historia_{int(time.time())}.json"
    out_path = SCRATCH_DIR / out_name
    story = {"raw_text": text, "prompts": prompts}
    out_path.write_text(json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8")
    story["_saved_to"] = str(out_path)
    return story


def extract_script(raw_text: str) -> str:
    """Extrae el guion (narración) del texto completo pegado desde ChatGPT.

    Busca un heading tipo "Guion para ElevenLabs" y corta hasta el siguiente
    heading numerado ("2. ..."). Si no encuentra el marcador, usa como
    fallback todo el texto antes del primer "Imagen 1" (mismo corte que ya
    usa _parse_story para descartar el resto).

    La busqueda del heading se limita al texto ANTES del primer "Imagen 1":
    sin este limite, la regex del heading matchea la palabra "guion" dentro
    de "Frase del guion:" (label que aparece en cada bloque Imagen N) cuando
    el pegado no trae un heading real de guion, y termina capturando todo el
    documento (guion + prompts de imagen mezclados) como si fuera la narracion.
    """
    starts = [m.start() for m in re.finditer(r"#{0,3}\s*Imagen\s*1\b", raw_text)]
    head = _strip_characters_block(raw_text[: starts[0]] if starts else raw_text)
    # Los manuales v2 (macrame) separan secciones con headings sin numerar
    # ("GUION" / "IMAGENES"): el ultimo queda pegado al final de la narracion y
    # el TTS lo leia en voz alta ("...Comenta EBOOK. Imagenes").
    head = _IMAGES_HEADING_RE.split(head)[0]

    match = re.search(
        r"#{0,3}\s*\d*\.?\s*Guion\b.*?(?:\n|$)(.*?)(?=\n\s*#{0,3}\s*\d+\.|\Z)",
        head,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        script = match.group(1).strip()
        logger.info("extract_script: guion extraido via heading (%d chars)", len(script))
        return script

    if starts:
        script = head.strip()
        logger.info("extract_script: guion extraido via fallback 'Imagen 1' (%d chars)", len(script))
        return script
    logger.warning("extract_script: no se encontro marcador, usando texto completo (%d chars)", len(raw_text))
    return raw_text.strip()


SPANISH_WORDS_PER_MINUTE = 170  # calibrado contra .subs.json reales generados con Chatterbox (158-189 wpm observado)


MIN_SCRIPT_WORDS = 30  # ~10s de narracion; por debajo no es una historia


def validate_script(script: str, min_words: int = MIN_SCRIPT_WORDS) -> None:
    """Rechaza un guion que es una plantilla o un texto demasiado corto.

    El modelo a veces devuelve bajo "Guion" un marcador entre corchetes (ej. "[Narracion
    lista para locucion: hook fuerte, ...]") en vez de la narracion. Sin este
    chequeo el TTS lo lee tal cual, el video sale de ~7s y se publica con ese
    texto como titulo y descripcion en las 3 redes."""
    text = script.strip()
    if re.fullmatch(r"\[[^\]]*\]", text) or len(text.split()) < min_words:
        raise PipelineError(
            f"Guion invalido ({len(text.split())} palabras, minimo {min_words}): "
            f"{text[:100]!r}"
        )


def cap_script_to_duration(script: str, duration_seconds: Optional[int]) -> str:
    """Recorta el guion a las oraciones que quepan en el presupuesto de
    palabras de duration_seconds (estimado a SPANISH_WORDS_PER_MINUTE).
    None/0 = "Automatico" (sin recorte). Nunca corta a mitad de oracion, y
    siempre conserva al menos la primera aunque sola ya exceda el
    presupuesto, para no devolver un guion vacio."""
    if not duration_seconds:
        return script
    word_budget = round(duration_seconds / 60 * SPANISH_WORDS_PER_MINUTE)
    sentences = re.split(r"(?<=[.!?])\s+", script.strip())
    kept = []
    count = 0
    for s in sentences:
        n = len(s.split())
        if kept and count + n > word_budget:
            break
        kept.append(s)
        count += n
    return " ".join(kept)


# Pedido de ficha de personajes + prompts largos. Va en el mensaje y no en la
# system prompt del generador de texto: no conviene depender solo de el
# alcanza para pedir descripciones detalladas (el modelo las acortaba a ~8 palabras
# y la de los humanos ni aparecia). _parse_story expande las etiquetas [NOMBRE].
CHARACTER_SHEET_REQUEST = (
    "\n\nFORMATO DE IMAGENES (obligatorio): despues de HOOK_TEXT (si lo hay) y antes de "
    "\"Guion\", escribi un bloque \"PERSONAJES:\" con una linea por cada personaje que "
    "aparezca en las imagenes (el animal y cada humano; si no hay personajes, la pieza u "
    "objeto principal), con este formato: [NOMBRE]: descripcion en ingles de 40 a 60 "
    "palabras con especie o raza, edad, tamano, color y patron exacto del pelaje o pelo, "
    "ojos, rasgos distintivos y ropa o accesorios. La ficha lleva SOLO rasgos "
    "permanentes; el estado de cada momento (apagado, sucio, feliz, dormido) va en la "
    "accion de cada escena. En cada \"Prompt:\" escribi entre 70 y 100 palabras en ingles "
    "(plano, angulo de camara, accion, expresion, escenario con detalles concretos, luz "
    "y atmosfera) y nombra a cada personaje SOLO con su etiqueta, por ejemplo [LUNA], sin "
    "redescribirlo: el sistema pega la ficha completa en cada prompt."
)


# Variante para macrame: la "ficha" es la pieza que se teje (mas las manos y la
# escena), y los prompts muestran la MISMA pieza avanzando etapa por etapa.
MACRAME_SHEET_REQUEST = (
    "\n\nFORMATO DE IMAGENES (obligatorio): despues de IDEA y antes de \"Guion\", escribi un "
    "bloque \"PERSONAJES:\" con exactamente tres lineas: [PIEZA] (50 a 70 palabras en ingles: "
    "tipo de pieza, tamano en cm, cuerda con grosor, color con hex y textura, nudos y diseno "
    "distintivos, flecos/borlas, accesorios y un rasgo unico reconocible), [MANOS] (15 a 25 "
    "palabras) y [ESCENA] (30 a 45 palabras: superficie, fondo, luz, camara, objetos fijos). "
    "Solo rasgos permanentes. En cada \"Prompt:\" escribi entre 60 y 90 palabras en ingles, "
    "SIN corchetes envolventes, que indiquen la etapa (Stage N of M, % de avance), la accion "
    "exacta de [MANOS] y las herramientas, el estado visible de [PIEZA] (lo que ya existe + "
    "el unico cambio nuevo) y el encuadre, nombrando [PIEZA], [MANOS] y [ESCENA] solo con su "
    "etiqueta: el sistema pega la ficha completa en cada prompt. Todas las imagenes muestran "
    "la MISMA pieza (mismo color, grosor, tamano y diseno) en avance logico; la Imagen 1 es "
    "el resultado final."
)


def build_trigger_message(base_message: str, duration_seconds: Optional[int], kind: Optional[str] = None) -> str:
    """Arma el mensaje disparador de un Project de historias: agrega el pedido
    de ficha de personajes y, si hay una duracion objetivo, un pedido de
    longitud aproximada en palabras, para que el modelo genere directamente un
    guion cercano al objetivo en vez de depender solo del recorte posterior
    de cap_script_to_duration (que nunca puede alargar una historia corta,
    solo acortarla si se pasa)."""
    message = base_message
    if duration_seconds:
        word_target = round(duration_seconds / 60 * SPANISH_WORDS_PER_MINUTE)
        message += f" (el guion de narracion debe tener aproximadamente {word_target} palabras)"
    return message + (MACRAME_SHEET_REQUEST if kind == "macrame" else CHARACTER_SHEET_REQUEST)


MACRAME_MIN_PROMPT_WORDS = 70
_MACRAME_SHEET_TAGS = ("Pieza", "Manos", "Escena")


_ES_WORDS = frozenset(
    "el la los las del con una un unos unas y en que su sus por para se al lo es está como más pero sin sobre "
    "entre desde hasta cuando mientras luz suave primer plano perro gato niño niña mano manos ojos".split()
)
_EN_WORDS = frozenset(
    "the a an of with and in on at to his her its is are from by for under over while soft light close up dog cat "
    "boy girl hand hands eyes looking".split()
)


def _looks_spanish(prompt: str) -> bool:
    words = re.findall(r"[a-záéíóúñü]+", prompt.lower())
    es = sum(w in _ES_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    accents = len(re.findall(r"[áéíóúñ¿¡]", prompt.lower()))
    return (es >= 3 and es > en) or (accents >= 3 and en < es + accents)


def validate_prompts_english(story: dict) -> None:
    """Los prompts de imagen deben estar en ingles (Meta AI interpreta mucho mejor ese idioma). Si el
    modelo de texto los escribio en español, se rechaza para regenerar el guion (otro modelo suele cumplir)."""
    bad = [str(p["index"]) for p in story["prompts"] if _looks_spanish(p["prompt"])]
    if bad:
        first = next(p for p in story["prompts"] if str(p["index"]) == bad[0])
        logger.warning("prompt de imagen %s detectado como español: %.400s", bad[0], first["prompt"])
        raise PipelineError(f"Los prompts de imagen no están en inglés (imágenes: {', '.join(bad)}).")


def validate_macrame_story(story: dict) -> None:
    """Rechaza una historia de macrame cuyos prompts no traen la ficha fija de
    la pieza (sin ella no hay continuidad visual entre imagenes) o son
    demasiado cortos/genericos. El mensaje lleva el marcador sanable
    "no se encontraron prompts de imagen" para que el lote regenere."""
    problems = []
    prompts = sorted(story.get("prompts", []), key=lambda p: p["index"])
    for pos, p in enumerate(prompts):
        text = p["prompt"]
        # [PIEZA] describe la pieza TERMINADA: solo es obligatoria en la primera
        # imagen y en la ultima. En las de proceso el modelo describe el avance
        # de esa etapa (pegar la pieza final ahi la contradice).
        required = _MACRAME_SHEET_TAGS if pos in (0, len(prompts) - 1) else _MACRAME_SHEET_TAGS[1:]
        missing = [t for t in required if f"{t} (" not in text]
        if missing:
            problems.append(f"Imagen {p['index']}: sin ficha de {', '.join(missing)}")
        elif len(text.split()) < MACRAME_MIN_PROMPT_WORDS:
            problems.append(f"Imagen {p['index']}: prompt de {len(text.split())} palabras (minimo {MACRAME_MIN_PROMPT_WORDS})")
    if problems:
        raise PipelineError(
            "No se encontraron prompts de imagen validos de macrame (ficha de pieza incompleta): "
            + "; ".join(problems[:4])
        )


def resume_index(download_dir: Path) -> int:
    """Primer indice de scene_*.mp4 que falta en download_dir, para saber desde
    donde retomar generate_clips() sin repetir prompts ya generados.

    No alcanza con contar archivos: si alguna corrida anterior fallo a la
    mitad, puede haber huecos en el medio -- hay que buscar el primer hueco
    real, no asumir que lo ya generado es un prefijo contiguo."""
    if not download_dir.exists():
        return 0
    existing = set()
    for p in list(download_dir.glob("scene_*.mp4")) + list(download_dir.glob("scene_*.jpg")):
        try:
            existing.add(int(p.stem.split("_")[1]))
        except (IndexError, ValueError):
            continue
    i = 0
    while i in existing:
        i += 1
    return i


# --- Etapa 2: WhatsApp / Meta IA -------------------------------------------

def _find_ref(snapshot_text: str, pattern: str) -> str:
    match = re.search(pattern, snapshot_text)
    return match.group(1) if match else None


def _open_meta_ai(unattended: bool) -> None:
    _run_agent_browser(["open", "https://web.whatsapp.com/"], WHATSAPP_SESSION)

    snap = ""
    for _ in range(15):
        time.sleep(3)
        snap = _run_agent_browser(["snapshot", "-i"], WHATSAPP_SESSION)
        # Tras escanear el QR, WhatsApp Web a veces muestra un dialogo modal
        # "Novedades en WhatsApp Web" que tapa el boton de Meta AI hasta que
        # se cierra explicitamente.
        if "Novedades en WhatsApp Web" in snap:
            cerrar_ref = _find_ref(snap, r'button "Cerrar" \[ref=(\w+)\]')
            if cerrar_ref:
                _run_agent_browser(["click", f"@{cerrar_ref}"], WHATSAPP_SESSION)
                time.sleep(1)
                snap = _run_agent_browser(["snapshot", "-i"], WHATSAPP_SESSION)
        if "Meta AI" in snap:
            break
    else:
        raise PipelineError(
            "No encontre el boton 'Meta AI' en WhatsApp Web. "
            "Puede que la sesion no este logueada (escanear QR una vez a mano)."
        )
    # No se fija el rol (button/link/listitem) porque WhatsApp Web lo cambia
    # entre versiones -- alcanza con que el elemento clickeable tenga "Meta AI"
    # en su nombre accesible.
    meta_ai_ref = _find_ref(snap, r'"Meta AI[^"]*"\s*\[ref=(\w+)\]')
    # La referencia (@eN) vale solo para el snapshot que la dio: si WhatsApp Web se vuelve a dibujar
    # entre el snapshot y el clic, agent-browser responde "Unknown ref". Se toma otro snapshot y se
    # reintenta en vez de fallar el video.
    for attempt in range(1, 4):
        if not meta_ai_ref:
            raise PipelineError(
                "No pude ubicar la referencia del boton Meta AI. Snapshot:\n" + snap[:2000]
            )
        try:
            _run_agent_browser(["click", f"@{meta_ai_ref}"], WHATSAPP_SESSION)
            return
        except PipelineError as e:
            if "Unknown ref" not in str(e) or attempt == 3:
                raise
            time.sleep(2)
            snap = _run_agent_browser(["snapshot", "-i"], WHATSAPP_SESSION)
            meta_ai_ref = _find_ref(snap, r'"Meta AI[^"]*"\s*\[ref=(\w+)\]')


def _last_row_block(snap: str) -> str:
    """Devuelve el ultimo '- row' del snapshot (la burbuja mas nueva/al fondo del chat).

    El chat de WhatsApp Web virtualiza el DOM: mensajes viejos se desmontan al
    hacer scroll, asi que contar ocurrencias totales de un marcador no es
    confiable (el conteo puede subir o bajar sin relacion con mensajes nuevos).
    Mirar solo la ultima burbuja evita ese problema.
    """
    idx = snap.rfind("- row")
    return snap[idx:] if idx != -1 else snap


def _count_media_nodes(session: str, marker: str) -> str:
    """Devuelve el contenido de la ultima burbuja del chat (para comparar antes/despues)."""
    snap = _run_agent_browser(["snapshot"], session)
    return _last_row_block(snap)


def _send_chat_message(session: str, text: str, textbox_pattern: str = r'textbox "Escribir un mensaje[^"]*" \[ref=(\w+)\]') -> None:
    """Escribe `text` en el textbox del chat y lo manda con Enter.

    Si `text` tiene mas de una linea (ej. trigger_message con bloque de
    historial pegado), NO se puede mandar todo de una con `fill`: confirmado
    en vivo que el `\\n` embebido dispara el mismo submit-on-Enter que tiene
    bindeado el chat (WhatsApp) y trunca el resto del mensaje en
    silencio -- ni se manda como texto ni aparece nada, se pierde. Por eso
    despues de la primera linea se inserta cada linea siguiente a mano con
    Shift+Enter (salto de linea real, sin submit) + `keyboard inserttext`,
    en vez de tirarle el texto completo de un tiro a `fill`."""
    snap = _run_agent_browser(["snapshot", "-i"], session)
    textbox_ref = _find_ref(snap, textbox_pattern)
    if not textbox_ref:
        raise PipelineError(f"No encontre el textbox del chat ({session}).")
    lines = text.split("\n")
    # Tomar snapshot fresco antes de cada fill porque WhatsApp puede
    # actualizar el DOM entre llamadas y invalidar la referencia anterior.
    _run_agent_browser(["fill", f"@{textbox_ref}", lines[0]], session)
    for line in lines[1:]:
        _run_agent_browser(["press", "Shift+Enter"], session)
        if line:
            # Snapshot fresco para cada linea adicional
            _run_agent_browser(["snapshot", "-i"], session)
            _run_agent_browser(["keyboard", "inserttext", line], session)
    _run_agent_browser(["press", "Enter"], session)


def _wait_for_new_media(session: str, marker: str, baseline_block: str, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    last_report = time.time()
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        current_block = _count_media_nodes(session, marker)
        if marker in current_block and current_block != baseline_block:
            return
        if current_block != baseline_block:
            lowered = current_block.lower()
            if IMAGE_REFUSAL_RE.search(lowered) or any(f in lowered for f in IMAGE_FAILURE_MARKERS):
                if current_block.strip().endswith("?"):
                    raise MetaAIAlternativeOffered(current_block.strip())
                raise MetaAIFailure(current_block.strip())
        if time.time() - last_report > 30:
            print(f"  ...esperando '{marker}' (sigo vivo, aun no aparece)")
            last_report = time.time()
    raise PipelineError(f"Timeout esperando '{marker}' nuevo en el chat.")


def _download_last_video(session: str, dest_path: Path) -> None:
    """
    Descarga el ultimo clip de video del chat. WhatsApp Web sirve el media
    como blob: URL -- se extrae con eval y se vuelca a disco via base64.
    """
    js = (
        "(async () => {"
        # El <video> puede montarse antes de tener src (blob se asigna tarde) y
        # a veces la URL vive en currentSrc o en un <source> hijo: se prueban
        # las tres y se reintenta ~5s antes de rendirse.
        "  const pick = () => {"
        "    const videos = document.querySelectorAll('video');"
        "    const v = videos[videos.length - 1];"
        "    if (!v) return null;"
        "    const s = v.currentSrc || v.src || (v.querySelector('source') || {}).src;"
        "    return s || null;"
        "  };"
        "  let src = pick();"
        "  for (let t = 0; !src && t < 10; t++) {"
        "    await new Promise(r => setTimeout(r, 500));"
        "    src = pick();"
        "  }"
        "  if (!src) return null;"
        "  const resp = await fetch(src);"
        "  const buf = await resp.arrayBuffer();"
        "  let binary = '';"
        "  const bytes = new Uint8Array(buf);"
        "  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);"
        "  return btoa(binary);"
        "})()"
    )
    result = subprocess.run(
        _base_cmd(session) + ["eval", js],
        capture_output=True, text=True, timeout=60, shell=(sys.platform == "win32"),
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0 or not result.stdout.strip() or result.stdout.strip() == "null":
        detail = result.stderr.strip()
        logger.warning(
            "_download_last_video: eval fallo (rc=%s) stderr=%s stdout=%s",
            result.returncode, detail, result.stdout.strip()[:200],
        )
        _save_failure_diagnostics(session, "video_download")
        raise PipelineError(
            f"No pude descargar el clip para {dest_path.name} "
            "(el mecanismo de descarga del video en WhatsApp Web puede haber cambiado)."
            + (f" Detalle: {detail}" if detail else "")
        )
    import base64
    b64_data = result.stdout.strip().strip('"')
    dest_path.write_bytes(base64.b64decode(b64_data))


def _download_last_image(session: str, dest_path: Path) -> None:
    """
    Descarga la ultima imagen del chat. Mismo mecanismo que
    _download_last_video pero apuntando a <img> en vez de <video>.
    """
    js = (
        "(async () => {"
        "  const imgs = document.querySelectorAll('img');"
        "  const im = imgs[imgs.length - 1];"
        "  if (!im || !im.src) return null;"
        "  const resp = await fetch(im.src);"
        "  const buf = await resp.arrayBuffer();"
        "  let binary = '';"
        "  const bytes = new Uint8Array(buf);"
        "  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);"
        "  return btoa(binary);"
        "})()"
    )
    result = subprocess.run(
        _base_cmd(session) + ["eval", js],
        capture_output=True, text=True, timeout=60, shell=(sys.platform == "win32"),
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0 or not result.stdout.strip() or result.stdout.strip() == "null":
        detail = result.stderr.strip()
        logger.warning("_download_last_image: eval fallo (rc=%s) stderr=%s", result.returncode, detail)
        _save_failure_diagnostics(session, "image_download")
        raise PipelineError(
            f"No pude descargar la imagen para {dest_path.name} "
            "(el mecanismo de descarga de imagenes en WhatsApp Web puede haber cambiado)."
            + (f" Detalle: {detail}" if detail else "")
        )
    import base64
    b64_data = result.stdout.strip().strip('"')
    dest_path.write_bytes(base64.b64decode(b64_data))


def _generate_one_clip_whatsapp(item: dict, scene_path: Path, unattended: bool, is_first: bool,
                                 generate_video: bool = True) -> Path:
    """Genera+anima UNA imagen en el chat de Meta IA ya abierto y descarga el
    clip resultante en scene_path. Cuerpo por-item de _generate_clips_whatsapp,
    reusado tambien por _generate_clips_mixed."""
    if is_first:
        _confirm("Voy a mandar el primer prompt de imagen. Confirmas?", unattended)

    next_action = "initial"
    for attempt in range(1, IMAGE_RETRY_ATTEMPTS + 1):
        img_baseline = _count_media_nodes(WHATSAPP_SESSION, IMAGE_MARKER)
        if next_action == "initial":
            _send_chat_message(WHATSAPP_SESSION, f"Imagen {item['index']}: {item['prompt']}")
        elif next_action == "accept":
            # Meta AI ofrecio una version alternativa mas segura y pregunto
            # si la genera -- aceptarla es mas simple y confiable que
            # hacerle adivinar al pipeline una reformulacion propia.
            _send_chat_message(WHATSAPP_SESSION, "Sí")
        elif next_action == "soften":
            # Meta AI rechazo el encuadre/contenido (no perdio el hilo) --
            # reenviar el mismo prompt tal cual casi siempre vuelve a
            # fallar; se pide una version mas segura en su lugar.
            _send_chat_message(
                WHATSAPP_SESSION,
                f"Genera la Imagen {item['index']} de forma mas segura y documental, "
                "sin detalles graficos de heridas, sufrimiento o daño visible "
                f"(mantene el mismo sujeto y contexto): {item['prompt']}",
            )
        else:  # "fresh"
            # Meta AI perdio el hilo de edicion (no pudo "retomar" la
            # imagen anterior) -- reenviar pidiendo generarla de cero en
            # vez de editar, para no depender de un archivo base perdido.
            _send_chat_message(
                WHATSAPP_SESSION,
                f"Genera la Imagen {item['index']} desde cero (no edites ninguna imagen previa): {item['prompt']}",
            )
        try:
            _wait_for_new_media(WHATSAPP_SESSION, IMAGE_MARKER, img_baseline, IMAGE_TIMEOUT_SECONDS)
            break
        except MetaAIAlternativeOffered:
            # Primera vez: aceptar. Si ya habiamos aceptado una vez y Meta
            # volvio a ofrecer/rechazar, no insistir con "Si" de nuevo --
            # caer al reintento existente (prompt suavizado).
            next_action = "soften" if next_action == "accept" else "accept"
            if attempt == IMAGE_RETRY_ATTEMPTS:
                raise PipelineError(
                    f"Meta AI no pudo generar la Imagen {item['index']} tras {IMAGE_RETRY_ATTEMPTS} intentos."
                )
        except MetaAIFailure as e:
            lowered = str(e).lower()
            next_action = "soften" if (
                (IMAGE_REFUSAL_RE.search(lowered) or any(m in lowered for m in IMAGE_CONTENT_REFUSAL_MARKERS))
                and not any(m in lowered for m in IMAGE_TRANSIENT_MARKERS)
            ) else "fresh"
            if attempt == IMAGE_RETRY_ATTEMPTS:
                raise PipelineError(
                    f"Meta AI no pudo generar la Imagen {item['index']} tras {IMAGE_RETRY_ATTEMPTS} intentos."
                )

    if not generate_video:
        _download_last_image(WHATSAPP_SESSION, scene_path)
        return scene_path

    # Respaldo: la imagen ya esta generada, se baja YA como scene_XXX.jpg. Si la
    # animacion falla despues (timeout o descarga del blob), el video del lote
    # sale igual con la imagen (el render la anima con Ken Burns) en vez de
    # morir entero -- resume_index/build_props ya aceptan .jpg mezclado con .mp4.
    fallback_path = scene_path.with_suffix(".jpg")
    try:
        _download_last_image(WHATSAPP_SESSION, fallback_path)
    except PipelineError as e:
        logger.warning("clip %s: sin imagen de respaldo (%s)", scene_path.name, e)
        fallback_path = None

    if is_first:
        _confirm("Imagen generada. Mando 'animar'?", unattended)

    try:
        clip_baseline = _count_media_nodes(WHATSAPP_SESSION, VIDEO_MARKER)
        _send_chat_message(WHATSAPP_SESSION, f"anima la imagen {item['index']}")
        _wait_for_new_media(WHATSAPP_SESSION, VIDEO_MARKER, clip_baseline, CLIP_TIMEOUT_SECONDS)
        _download_last_video(WHATSAPP_SESSION, scene_path)
    except PipelineError as e:
        msg = str(e)
        # Daemon colgado / sesion muerta: no es un fallo de la animacion, dejar
        # que el reintento por etapa la resetee en vez de seguir a ciegas.
        if fallback_path is None or "10060" in msg or "no respondio a tiempo" in msg:
            raise
        logger.warning(
            "clip %s: animacion fallo, se usa la imagen estatica: %s", scene_path.name, msg
        )
        scene_path.unlink(missing_ok=True)
        return fallback_path

    if fallback_path is not None:
        fallback_path.unlink(missing_ok=True)
    return scene_path


def _generate_clips_whatsapp(story: dict, download_dir: Path, unattended: bool, start_index: int,
                              report, generate_video: bool = True) -> list:
    _confirm("Voy a abrir WhatsApp Web y entrar al chat de Meta IA. Confirmas?", unattended)
    _open_meta_ai(unattended)

    ext = "mp4" if generate_video else "jpg"
    generated = []
    for i, item in enumerate(story["prompts"]):
        if i < start_index:
            continue
        scene_path = download_dir / f"scene_{i:03d}.{ext}"
        report(f"[{i + 1}/{len(story['prompts'])}] Imagen {item['index']}: {item['frase'][:60]}...")

        saved_path = _generate_one_clip_whatsapp(item, scene_path, unattended, is_first=(i == start_index),
                                                  generate_video=generate_video)
        generated.append(str(saved_path))
        report(f"  -> guardado en {saved_path}")

    return generated


def generate_clips(story: dict, download_dir: Path, unattended: bool, start_index: int = 0,
                    on_progress=None, provider: str = "whatsapp", generate_video: bool = True,
                    aspect: str = "9:16") -> list:
    """`aspect` solo lo usa Flow ("9:16" para las escenas de un video, "1:1" para un post de imagen)."""
    if provider not in PROVIDERS:
        raise PipelineError(f"Proveedor desconocido: {provider} (opciones: {', '.join(PROVIDERS)})")

    def report(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        else:
            print(msg)

    download_dir.mkdir(parents=True, exist_ok=True)

    n_prompts = len(story.get("prompts", []))
    logger.info("generate_clips: provider=%s start_index=%d prompts=%d dir=%s generate_video=%s",
                provider, start_index, n_prompts, download_dir, generate_video)
    try:
        if provider == "flow":
            import flow_images
            try:  # reanuda sola: salta las scene_NNN.jpg que ya existen
                flow_images.generate_scene_images(story, download_dir, on_progress=report, aspect=aspect)
            except flow_images.FlowError as e:
                raise PipelineError(str(e)) from e
            clips = [str(download_dir / f"scene_{i:03d}.jpg") for i in range(n_prompts)]
        else:
            clips = _generate_clips_whatsapp(story, download_dir, unattended, start_index, report,
                                              generate_video=generate_video)
        logger.info("generate_clips: listo, %d clips generados en %s", len(clips), download_dir)
        return clips
    except Exception:
        logger.exception("generate_clips: fallo generando clips (provider=%s)", provider)
        raise


# --- Main -------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-file", required=True, help="Archivo .txt con el texto de la historia pegado desde ChatGPT")
    parser.add_argument("--download-dir", default=str(DEFAULT_SCENES_DIR), help="Carpeta donde guardar los clips (default: video/public)")
    parser.add_argument("--unattended", action="store_true", help="No pedir confirmacion antes de escribir en el chat de WhatsApp")
    parser.add_argument("--start-index", type=int, default=0, help="Indice (0-based) de la primera imagen a generar, para retomar tras un corte")
    parser.add_argument("--provider", choices=PROVIDERS, default="whatsapp", help="Servicio a usar para generar los clips")
    args = parser.parse_args()

    try:
        print(f"Leyendo historia desde {args.from_file}...")
        story = load_story_from_file(Path(args.from_file))
        print(f"Historia recibida: {len(story['prompts'])} imagenes. Guardada en {story['_saved_to']}")

        print(f"Generando clips con {args.provider}...")
        clips = generate_clips(story, Path(args.download_dir), args.unattended, args.start_index,
                                provider=args.provider)
        print(f"Listo: {len(clips)} clips generados en {args.download_dir}")
        return 0
    except PipelineError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
