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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

QWEN_SESSION = "qwen"
QWEN_URL = "https://chat.qwen.ai/"

PROVIDERS = ("whatsapp", "qwen", "mixed")

POLL_INTERVAL_SECONDS = 4
IMAGE_TIMEOUT_SECONDS = 600
CLIP_TIMEOUT_SECONDS = 600
QWEN_CLIP_TIMEOUT_SECONDS = 1200
QWEN_IMAGE_TIMEOUT_SECONDS = 180  # generar una sola imagen es mucho mas rapido que un video
QWEN_TEXT_TIMEOUT_SECONDS = 60  # una respuesta de texto corta (ej. un titular) es casi instantanea


class PipelineError(Exception):
    pass


class MetaAIFailure(PipelineError):
    """Meta AI respondio con texto de fallo (ej. 'no pude retomar la imagen
    anterior') en vez de generar la imagen pedida."""
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
    Qwen sigue con --restore, que le alcanza."""
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


def restart_browser_session(session: str) -> None:
    """Cierra el daemon de agent-browser de una sesion (se relanza solo en el proximo uso,
    conservando el login via --profile para WhatsApp o --restore para el resto). Util
    cuando el daemon quedo colgado (error os 10060)."""
    with _get_session_lock(session):
        try:
            _run_cmd([AGENT_BROWSER_CMD, "--session", session, "close"], timeout=30)
        except subprocess.TimeoutExpired:
            _force_kill_session(session)


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
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return "agent-browser" in result.stdout.lower()


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
    verificado en vez de asumir exito a ciegas (a diferencia de restart_browser_session,
    que solo le pide amablemente al daemon que cierre)."""
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
    elif session == QWEN_SESSION:
        if re.search(r"textbox", snap):
            return {"state": "ok", "message": "Sesion de Qwen activa."}
        return {"state": "needs_login", "message": "Qwen no esta logueado (falta iniciar sesion)."}
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


def screenshot_qr(session: str) -> Path:
    """Captura solo el <canvas> del QR (WhatsApp/Qwen lo renderizan como canvas),
    con fallback a la página completa si no encuentra el elemento."""
    return screenshot_session(session, selector="canvas")


def open_login_page(session: str) -> None:
    """Abre la pagina de login del proveedor en la sesion dada (para reconexion).
    Reintenta una vez si el daemon anterior todavia estaba terminando de cerrarse
    (error os 10061, "conexion denegada" justo tras un restart_browser_session)."""
    url = "https://web.whatsapp.com/" if session == WHATSAPP_SESSION else QWEN_URL
    try:
        _run_agent_browser(["open", url], session)
    except PipelineError as e:
        if "10061" not in str(e):
            raise
        time.sleep(2)
        _run_agent_browser(["open", url], session)


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
    HEADING = r"(?<!\w)#{0,3}[ \t]*Imagen[ \t]*(\d+)[ \t]*$"
    HEADING_NOCAP = r"(?<!\w)#{0,3}[ \t]*Imagen[ \t]*\d+[ \t]*$"

    starts = [m.start() for m in re.finditer(r"(?<!\w)#{0,3}[ \t]*Imagen[ \t]*1[ \t]*$", text, re.MULTILINE)]
    if starts:
        text = text[starts[-1]:]

    prompts = []
    for match in re.finditer(
        HEADING + r".*?Frase(?: del guion)?:\s*[«\"](.+?)[»\"].*?Prompt:\s*(.+?)(?=" + HEADING_NOCAP + r"|\Z)",
        text,
        re.DOTALL | re.MULTILINE,
    ):
        idx, frase, prompt = match.groups()
        prompts.append({
            "index": int(idx),
            "frase": frase.strip(),
            "prompt": _soften_prompt(" ".join(prompt.split()).strip()),
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
    head = raw_text[: starts[0]] if starts else raw_text

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


def resume_index(download_dir: Path) -> int:
    """Primer indice de scene_*.mp4 que falta en download_dir, para saber desde
    donde retomar generate_clips() sin repetir prompts ya generados.

    No alcanza con contar archivos: la generacion paralela de clips (Qwen, N
    sesiones a la vez) puede terminar con huecos en el medio si una sesion
    falla mientras las otras siguen -- hay que buscar el primer hueco real,
    no asumir que lo ya generado es un prefijo contiguo."""
    if not download_dir.exists():
        return 0
    existing = set()
    for p in download_dir.glob("scene_*.mp4"):
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


def _find_select_mode_ref(snapshot_text: str) -> str:
    """Ref clickeable para abrir el menu de modos de Qwen ('Select Mode').

    El boton en si a veces queda con area de click nula (a criterio del propio
    render de Qwen, visto en vivo en la sesion 'qwen2'); el hit-target real que
    siempre funciona es su generic padre con [onclick]. Se intenta ese primero
    y se cae al ref del boton si por algun motivo no aparece envuelto."""
    wrapper_ref = _find_ref(
        snapshot_text,
        r'generic \[ref=(\w+)\] clickable \[onclick\]\s*\n\s*- button "Select Mode"',
    )
    if wrapper_ref:
        return wrapper_ref
    return _find_ref(snapshot_text, r'button "Select Mode" \[ref=(\w+)\]')


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
    if not meta_ai_ref:
        raise PipelineError(
            "No pude ubicar la referencia del boton Meta AI. Snapshot:\n" + snap[:2000]
        )
    _run_agent_browser(["click", f"@{meta_ai_ref}"], WHATSAPP_SESSION)


def _open_qwen(unattended: bool, session: str = QWEN_SESSION) -> None:
    _run_agent_browser(["open", QWEN_URL], session)

    snap = ""
    for _ in range(6):
        time.sleep(3)
        snap = _run_agent_browser(["snapshot", "-i"], session)
        if re.search(r"textbox", snap):
            break
    else:
        raise PipelineError(
            f"No encontre el textbox del chat en chat.qwen.ai (sesion '{session}'). "
            "Puede que la sesion no este logueada (iniciar sesion una vez a mano)."
        )


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
    snap = _run_agent_browser(["snapshot", "-i"], session)
    textbox_ref = _find_ref(snap, textbox_pattern)
    if not textbox_ref:
        raise PipelineError(f"No encontre el textbox del chat ({session}).")
    _run_agent_browser(["fill", f"@{textbox_ref}", text], session)
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
            if any(f in lowered for f in IMAGE_FAILURE_MARKERS):
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
        "  const videos = document.querySelectorAll('video');"
        "  const v = videos[videos.length - 1];"
        "  if (!v || !v.src) return null;"
        "  const resp = await fetch(v.src);"
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
        raise PipelineError(
            f"No pude descargar el clip para {dest_path.name} "
            "(el mecanismo de descarga del video en WhatsApp Web puede haber cambiado)."
        )
    import base64
    b64_data = result.stdout.strip().strip('"')
    dest_path.write_bytes(base64.b64decode(b64_data))


def _generate_one_clip_whatsapp(item: dict, scene_path: Path, unattended: bool, is_first: bool) -> None:
    """Genera+anima UNA imagen en el chat de Meta IA ya abierto y descarga el
    clip resultante en scene_path. Cuerpo por-item de _generate_clips_whatsapp,
    reusado tambien por _generate_clips_mixed."""
    if is_first:
        _confirm("Voy a mandar el primer prompt de imagen. Confirmas?", unattended)

    content_refused = False
    for attempt in range(1, IMAGE_RETRY_ATTEMPTS + 1):
        img_baseline = _count_media_nodes(WHATSAPP_SESSION, IMAGE_MARKER)
        if attempt == 1:
            _send_chat_message(WHATSAPP_SESSION, f"Imagen {item['index']}: {item['prompt']}")
        elif content_refused:
            # Meta AI rechazo el encuadre/contenido (no perdio el hilo) --
            # reenviar el mismo prompt tal cual casi siempre vuelve a
            # fallar; se pide una version mas segura en su lugar.
            _send_chat_message(
                WHATSAPP_SESSION,
                f"Genera la Imagen {item['index']} de forma mas segura y documental, "
                "sin detalles graficos de heridas, sufrimiento o daño visible "
                f"(mantene el mismo sujeto y contexto): {item['prompt']}",
            )
        else:
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
        except MetaAIFailure as e:
            content_refused = any(m in str(e).lower() for m in IMAGE_CONTENT_REFUSAL_MARKERS)
            if attempt == IMAGE_RETRY_ATTEMPTS:
                raise PipelineError(
                    f"Meta AI no pudo generar la Imagen {item['index']} tras {IMAGE_RETRY_ATTEMPTS} intentos."
                )

    if is_first:
        _confirm("Imagen generada. Mando 'animar'?", unattended)

    clip_baseline = _count_media_nodes(WHATSAPP_SESSION, VIDEO_MARKER)
    _send_chat_message(WHATSAPP_SESSION, f"anima la imagen {item['index']}")
    _wait_for_new_media(WHATSAPP_SESSION, VIDEO_MARKER, clip_baseline, CLIP_TIMEOUT_SECONDS)

    _download_last_video(WHATSAPP_SESSION, scene_path)


def _generate_clips_whatsapp(story: dict, download_dir: Path, unattended: bool, start_index: int,
                              report) -> list:
    _confirm("Voy a abrir WhatsApp Web y entrar al chat de Meta IA. Confirmas?", unattended)
    _open_meta_ai(unattended)

    generated = []
    for i, item in enumerate(story["prompts"]):
        if i < start_index:
            continue
        scene_path = download_dir / f"scene_{i:03d}.mp4"
        report(f"[{i + 1}/{len(story['prompts'])}] Imagen {item['index']}: {item['frase'][:60]}...")

        _generate_one_clip_whatsapp(item, scene_path, unattended, is_first=(i == start_index))
        generated.append(str(scene_path))
        report(f"  -> guardado en {scene_path}")

    return generated


def _select_qwen_video_mode(session: str) -> None:
    """Abre el menu de modos y activa 'Create Video' (necesario antes de cada prompt:
    el modo se desactiva solo despues de enviar un mensaje)."""
    mode_btn_ref = None
    for attempt in range(5):
        snap = _run_agent_browser(["snapshot", "-i"], session)
        mode_btn_ref = _find_select_mode_ref(snap)
        if mode_btn_ref:
            break
        # Justo tras abrir/recargar la pagina, el boton puede tardar un
        # instante en hidratarse -- reintentar antes de asumir que no esta.
        time.sleep(1.5)
    if not mode_btn_ref:
        raise PipelineError("No encontre el boton 'Select Mode' en chat.qwen.ai.")
    _run_agent_browser(["click", f"@{mode_btn_ref}"], session)

    video_mode_ref = None
    for attempt in range(5):
        snap = _run_agent_browser(["snapshot", "-i"], session)
        video_mode_ref = _find_ref(snap, r'menuitem "Create Video" \[ref=(\w+)\]')
        if video_mode_ref:
            break
        time.sleep(1.5)
    if not video_mode_ref:
        raise PipelineError(
            "No encontre (habilitado) 'Create Video' en el menu de Qwen. "
            "Puede requerir estar logueado o haber cambiado el nombre del modo."
        )
    _run_agent_browser(["click", f"@{video_mode_ref}"], session)


def _get_qwen_video_srcs(session: str) -> list:
    """Devuelve las URLs (no-blob, directas a cdn.qwenlm.ai) de los <video> ya
    renderizados en la pagina, sin duplicados."""
    js = "JSON.stringify([...new Set([...document.querySelectorAll('video')].map(v => v.currentSrc).filter(Boolean))])"
    result = subprocess.run(
        _base_cmd(session) + ["eval", js],
        capture_output=True, text=True, timeout=60, shell=(sys.platform == "win32"),
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise PipelineError(f"No pude leer los videos de la pagina: {result.stderr.strip()}")
    raw = result.stdout.strip().strip('"').replace('\\"', '"')
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _wait_for_qwen_video(session: str, baseline_srcs: list, timeout_seconds: int) -> str:
    deadline = time.time() + timeout_seconds
    last_report = time.time()
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        current = _get_qwen_video_srcs(session)
        new_srcs = [s for s in current if s not in baseline_srcs]
        if new_srcs:
            return new_srcs[-1]
        if time.time() - last_report > 30:
            print("  ...esperando video nuevo en Qwen (sigo vivo, aun no aparece)")
            last_report = time.time()
    raise PipelineError("Timeout esperando el video nuevo en Qwen.")


def _download_url(url: str, dest_path: Path) -> None:
    import urllib.request
    with urllib.request.urlopen(url, timeout=120) as resp:
        dest_path.write_bytes(resp.read())


def _select_qwen_image_mode(session: str) -> None:
    """Abre el menu de modos y activa 'Create Image' (mismo patron que
    _select_qwen_video_mode, pero este modo no requiere login)."""
    snap = _run_agent_browser(["snapshot", "-i"], session)
    mode_btn_ref = _find_select_mode_ref(snap)
    if not mode_btn_ref:
        raise PipelineError("No encontre el boton 'Select Mode' en chat.qwen.ai.")
    _run_agent_browser(["click", f"@{mode_btn_ref}"], session)

    snap = _run_agent_browser(["snapshot", "-i"], session)
    image_mode_ref = _find_ref(snap, r'menuitem "Create Image" \[ref=(\w+)\]')
    if not image_mode_ref:
        raise PipelineError(
            "No encontre (habilitado) 'Create Image' en el menu de Qwen. "
            "Puede haber cambiado el nombre del modo."
        )
    _run_agent_browser(["click", f"@{image_mode_ref}"], session)


def _select_qwen_image_ratio(session: str, ratio: str) -> None:
    """Clickea el selector real de proporcion de imagen (boton con la
    proporcion actual, ej. '16:9', visible solo en modo 'Create Image') y
    elige `ratio` (ej. '9:16') del menu desplegable. Solo confiar en texto
    de prompt para el aspect ratio no funciona -- Qwen lo ignora seguido;
    este control si lo respeta."""
    ratio_btn_ref = None
    for attempt in range(5):
        snap = _run_agent_browser(["snapshot", "-i"], session)
        ratio_btn_ref = _find_ref(snap, r'generic "\d+:\d+" \[ref=(\w+)\] clickable \[onclick\]')
        if ratio_btn_ref:
            break
        time.sleep(1.5)
    if not ratio_btn_ref:
        raise PipelineError("No encontre el selector de proporcion en chat.qwen.ai.")
    _run_agent_browser(["click", f"@{ratio_btn_ref}"], session)

    ratio_opt_ref = None
    for attempt in range(5):
        snap = _run_agent_browser(["snapshot", "-i"], session)
        ratio_opt_ref = _find_ref(snap, rf'menuitem "{re.escape(ratio)}" \[ref=(\w+)\]')
        if ratio_opt_ref:
            break
        time.sleep(1.5)
    if not ratio_opt_ref:
        raise PipelineError(
            f"No encontre la opcion de proporcion '{ratio}' en el menu de Qwen."
        )
    _run_agent_browser(["click", f"@{ratio_opt_ref}"], session)


def _get_qwen_image_srcs(session: str) -> list:
    """Devuelve las URLs (cdn.qwenlm.ai, no iconos/avatares de otros dominios)
    de las imagenes ya renderizadas en la pagina, sin duplicados."""
    js = (
        "JSON.stringify([...new Set([...document.images]"
        ".map(i => i.currentSrc || i.src)"
        ".filter(s => s.includes('cdn.qwenlm.ai')))])"
    )
    result = subprocess.run(
        _base_cmd(session) + ["eval", js],
        capture_output=True, text=True, timeout=60, shell=(sys.platform == "win32"),
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise PipelineError(f"No pude leer las imagenes de la pagina: {result.stderr.strip()}")
    raw = result.stdout.strip().strip('"').replace('\\"', '"')
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _wait_for_qwen_image(session: str, baseline_srcs: list, timeout_seconds: int) -> str:
    deadline = time.time() + timeout_seconds
    last_report = time.time()
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        current = _get_qwen_image_srcs(session)
        new_srcs = [s for s in current if s not in baseline_srcs]
        if new_srcs:
            return new_srcs[-1]
        if time.time() - last_report > 30:
            print("  ...esperando imagen nueva en Qwen (sigo vivo, aun no aparece)")
            last_report = time.time()
    raise PipelineError("Timeout esperando la imagen nueva en Qwen.")


def generate_qwen_image(
    prompt: str, dest_path: Path, unattended: bool = True, image_ratio: str = None
) -> str:
    """Genera una imagen vía chat.qwen.ai (modo 'Create Image') y la descarga a
    dest_path. Reusa la misma sesion/daemon persistente que generate_clips usa
    para video (QWEN_SESSION) -- pensada para usos puntuales (ej. fondo de
    miniatura de YouTube), no para el batch de escenas.

    image_ratio (opcional, ej. '9:16', '16:9'): si se pasa, clickea el selector
    real de proporcion de Qwen en vez de confiar solo en texto del prompt --
    el texto solo no garantiza que Qwen respete el formato pedido."""
    _confirm(f"Voy a generar una imagen en Qwen: {prompt[:60]}... Confirmas?", unattended)
    _open_qwen(unattended)
    with _get_session_lock(QWEN_SESSION):
        baseline_srcs = _get_qwen_image_srcs(QWEN_SESSION)
        _select_qwen_image_mode(QWEN_SESSION)
        if image_ratio:
            _select_qwen_image_ratio(QWEN_SESSION, image_ratio)
        _send_chat_message(QWEN_SESSION, prompt, textbox_pattern=r'textbox "Ask Qwen" \[ref=(\w+)\]')
        image_url = _wait_for_qwen_image(QWEN_SESSION, baseline_srcs, QWEN_IMAGE_TIMEOUT_SECONDS)
    _download_url(image_url, dest_path)
    return str(dest_path)


_QWEN_MODE_CHIP_NAMES = ("Create Image", "Create Video", "Web search", "Deep Research", "Web Dev", "Slides")


def _deselect_qwen_mode(session: str) -> None:
    """Si quedo un modo especial (ej. 'Create Image') seleccionado de un uso
    anterior, lo saca haciendo click en su 'x' -- si no se saca, el proximo
    mensaje de texto plano dispara otra generacion de imagen/video en vez de
    devolver una respuesta de texto."""
    snap = _run_agent_browser(["snapshot", "-i"], session)
    for name in _QWEN_MODE_CHIP_NAMES:
        m = re.search(
            rf'generic "{re.escape(name)}" \[ref=\w+\][^\n]*\n\s*-\s*image \[ref=(\w+)\]', snap
        )
        if m:
            _run_agent_browser(["click", f"@{m.group(1)}"], session)
            return


def _get_last_ai_text_reply(session: str) -> "str | None":
    """Devuelve el texto de la ultima respuesta de Qwen (chat de texto plano),
    o None si la ultima burbuja todavia no termino de generarse. Se apoya en
    que los botones de accion (Copy/Good Response/Bad Response) solo aparecen
    debajo de una respuesta ya completa -- mientras esta en streaming no
    matchea nada."""
    snap = _run_agent_browser(["snapshot"], session)
    matches = list(re.finditer(r'StaticText "((?:[^"\\]|\\.)*)"', snap))
    for i in range(len(matches) - 1, -1, -1):
        tail = snap[matches[i].end():matches[i].end() + 400]
        if 'button "Copy"' in tail:
            return matches[i].group(1).replace('\\"', '"')
    return None


def _wait_for_qwen_text_reply(session: str, baseline: "str | None", timeout_seconds: int) -> str:
    deadline = time.time() + timeout_seconds
    last_report = time.time()
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        reply = _get_last_ai_text_reply(session)
        if reply is not None and reply != baseline:
            return reply
        if time.time() - last_report > 30:
            print("  ...esperando respuesta de texto de Qwen (sigo vivo, aun no aparece)")
            last_report = time.time()
    raise PipelineError("Timeout esperando la respuesta de texto de Qwen.")


def generate_qwen_text(prompt: str, unattended: bool = True) -> str:
    """Pide una respuesta de texto corta a Qwen (chat plano, sin modo
    especial) -- ej. para armar un titular viral acorde a la imagen. Reusa la
    misma sesion persistente que usan las imagenes/videos (QWEN_SESSION)."""
    _confirm(f"Voy a pedirle un texto a Qwen: {prompt[:60]}... Confirmas?", unattended)
    _open_qwen(unattended)
    with _get_session_lock(QWEN_SESSION):
        _deselect_qwen_mode(QWEN_SESSION)
        baseline = _get_last_ai_text_reply(QWEN_SESSION)
        _send_chat_message(QWEN_SESSION, prompt, textbox_pattern=r'textbox "Ask Qwen" \[ref=(\w+)\]')
        reply = _wait_for_qwen_text_reply(QWEN_SESSION, baseline, QWEN_TEXT_TIMEOUT_SECONDS)
    return reply.strip()


def _generate_one_clip_qwen(session: str, item: dict, scene_path: Path) -> None:
    """Genera UN video directamente desde el prompt en la sesion Qwen ya abierta
    (sin paso previo de imagen) y lo descarga en scene_path. Cuerpo por-item de
    _generate_clips_qwen, reusado tambien por _generate_clips_mixed."""
    baseline_srcs = _get_qwen_video_srcs(session)
    _select_qwen_video_mode(session)
    _send_chat_message(session, item["prompt"], textbox_pattern=r'textbox "Ask Qwen" \[ref=(\w+)\]')
    video_url = _wait_for_qwen_video(session, baseline_srcs, QWEN_CLIP_TIMEOUT_SECONDS)
    _download_url(video_url, scene_path)


def _generate_clips_qwen(story: dict, download_dir: Path, unattended: bool, start_index: int,
                          report) -> list:
    """Qwen genera el video directamente desde el prompt (sin paso previo de imagen).

    Secuencial, una sola sesion: la cuenta de Qwen solo permite 1 generacion de
    video concurrente (confirmado en vivo -- abrir varias sesiones/pestanas con
    la misma cuenta NO paraleliza, todas menos la primera se bloquean con
    'Create Video' deshabilitado hasta que la que esta generando termina). Para
    paralelizar de verdad usar provider='mixed' (Qwen + WhatsApp a la vez, que
    si son cuentas/servicios independientes sin ese limite compartido)."""
    _confirm("Voy a abrir chat.qwen.ai. Confirmas?", unattended)
    _open_qwen(unattended)

    generated = []
    for i, item in enumerate(story["prompts"]):
        if i < start_index:
            continue
        scene_path = download_dir / f"scene_{i:03d}.mp4"
        report(f"[{i + 1}/{len(story['prompts'])}] Video {item['index']}: {item['frase'][:60]}...")

        _generate_one_clip_qwen(QWEN_SESSION, item, scene_path)
        generated.append(str(scene_path))
        report(f"  -> guardado en {scene_path}")

    return generated


def _generate_clips_mixed(story: dict, download_dir: Path, unattended: bool, start_index: int,
                           report) -> list:
    """Paraleliza de verdad repartiendo los prompts pendientes entre WhatsApp/Meta
    IA y Qwen a la vez (round-robin) -- son cuentas/servicios independientes, sin
    el limite de 1-concurrente-por-cuenta que tiene Qwen entre sesiones propias."""
    pending = [(i, item) for i, item in enumerate(story["prompts"]) if i >= start_index]
    if not pending:
        return []

    _confirm("Voy a abrir WhatsApp Web y Qwen en paralelo. Confirmas?", unattended)

    report_lock = threading.Lock()

    def report_safe(msg: str) -> None:
        with report_lock:
            report(msg)

    def worker_whatsapp(items: list) -> list:
        if not items:
            return []
        _open_meta_ai(unattended)
        generated_local = []
        for n, (i, item) in enumerate(items):
            scene_path = download_dir / f"scene_{i:03d}.mp4"
            if scene_path.exists():
                continue  # ya generado por una corrida anterior (reintento parcial)
            report_safe(f"[whatsapp] [{i + 1}/{len(story['prompts'])}] Imagen {item['index']}: {item['frase'][:60]}...")
            _generate_one_clip_whatsapp(item, scene_path, unattended, is_first=(n == 0))
            generated_local.append(str(scene_path))
            report_safe(f"  [whatsapp] -> guardado en {scene_path}")
        return generated_local

    def worker_qwen(items: list) -> list:
        if not items:
            return []
        _open_qwen(unattended)
        generated_local = []
        for i, item in items:
            scene_path = download_dir / f"scene_{i:03d}.mp4"
            if scene_path.exists():
                continue
            report_safe(f"[qwen] [{i + 1}/{len(story['prompts'])}] Video {item['index']}: {item['frase'][:60]}...")
            _generate_one_clip_qwen(QWEN_SESSION, item, scene_path)
            generated_local.append(str(scene_path))
            report_safe(f"  [qwen] -> guardado en {scene_path}")
        return generated_local

    whatsapp_items = [pair for n, pair in enumerate(pending) if n % 2 == 0]
    qwen_items = [pair for n, pair in enumerate(pending) if n % 2 == 1]

    generated = []
    errors = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(worker_whatsapp, whatsapp_items): "whatsapp",
            executor.submit(worker_qwen, qwen_items): "qwen",
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                generated.extend(future.result())
            except Exception as e:
                errors.append((name, e))
                report_safe(f"[{name}] fallo: {e}")

    generated.sort(key=lambda p: int(Path(p).stem.split("_")[1]))

    if errors:
        raise PipelineError(
            "Algun proveedor fallo generando clips (" + str(len(generated)) +
            " se generaron OK igual): " + "; ".join(f"{s}: {e}" for s, e in errors)
        )

    return generated


def generate_clips(story: dict, download_dir: Path, unattended: bool, start_index: int = 0,
                    on_progress=None, provider: str = "whatsapp") -> list:
    if provider not in PROVIDERS:
        raise PipelineError(f"Proveedor desconocido: {provider} (opciones: {', '.join(PROVIDERS)})")

    def report(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        else:
            print(msg)

    download_dir.mkdir(parents=True, exist_ok=True)

    n_prompts = len(story.get("prompts", []))
    logger.info("generate_clips: provider=%s start_index=%d prompts=%d dir=%s",
                provider, start_index, n_prompts, download_dir)
    try:
        if provider == "qwen":
            clips = _generate_clips_qwen(story, download_dir, unattended, start_index, report)
        elif provider == "mixed":
            clips = _generate_clips_mixed(story, download_dir, unattended, start_index, report)
        else:
            clips = _generate_clips_whatsapp(story, download_dir, unattended, start_index, report)
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
