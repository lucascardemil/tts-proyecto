"""Generacion de imagenes con Google Flow (flow.google.com), alternativa a WhatsApp / Meta IA.

Basado en canal/produccion/motor/flow_images.py de youtube-agent-skill. Flow no deja iniciar sesion en
un navegador automatizado ("este navegador no es seguro"), asi que NO se lanza un navegador desde aca:
se usa un navegador normal con perfil dedicado y puerto de depuracion propio (launch_browser, o el boton
"Abrir Flow" de la app), donde se inicia sesion a mano UNA vez. Este modulo solo se conecta a el con
agent-browser (sesion "flowtts") y maneja la UI de Flow.

Por defecto es Microsoft Edge en su propio perfil y puerto 9334 (FLOW_BROWSER=edge): totalmente separado
del Brave del puerto 9333 que usa otro proyecto, asi que no hay nada que compartir ni que pisar.
Con FLOW_BROWSER=brave se usa ese mismo Brave: ahi la app NO toca las pestañas de otros programas, abre
una pestaña PROPIA (via /json/new), trabaja en un proyecto de Flow propio y al terminar devuelve el
foco a la pestaña que estaba activa.

Todas las generaciones comparten un solo proyecto de Flow (su URL queda guardada): crear uno nuevo en
cada imagen tarda minutos. Las imagenes no consumen creditos (Flow solo cobra los videos); Flow genera
las que diga "Ajustes > Generacion de imagenes" y aca se usa la primera nueva.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import socket
import subprocess
import tempfile
from urllib.parse import quote
import time
from pathlib import Path
from typing import Callable, Optional

import requests

import job_store

logger = logging.getLogger("pipeline")

# Navegadores posibles para Flow: ejecutable, puerto de depuracion y perfil dedicado. Edge tiene el suyo
# propio (nadie mas lo usa); Brave es el del otro proyecto, compartido (puerto 9333).
BROWSERS = {
    "edge": {
        "label": "Edge", "dedicated": True, "port": 9334,
        "exe": os.environ.get("FLOW_EDGE_EXE") or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "profile": Path(os.environ.get("FLOW_EDGE_PROFILE") or (Path.home() / ".flow-edge-profile")),
    },
    "brave": {
        "label": "Brave", "dedicated": False, "port": 9333,
        "exe": os.environ.get("FLOW_BRAVE_EXE") or r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        "profile": Path(os.environ.get("FLOW_PROFILE_DIR") or (Path.home() / ".flow-browser-profile")),
    },
}
BROWSER = BROWSERS.get((os.environ.get("FLOW_BROWSER") or "edge").strip().lower(), BROWSERS["edge"])
CDP_PORT = BROWSER["port"]
SESSION = "flowtts"  # sesion propia: "flowcdp" es la del otro proyecto que usa el Brave
_AB_EXE = Path.home() / "AppData" / "Roaming" / "npm" / "node_modules" / "agent-browser" / "bin" / "agent-browser-win32-x64.exe"
# El .exe directo: pasando por agent-browser.cmd, cmd.exe interpreta | & ( ) del JS que se evalua.
AGENT_BROWSER = str(_AB_EXE) if _AB_EXE.exists() else "agent-browser.cmd"
FLOW_HOME = "https://flow.google.com/"
SETTINGS_STORE = "flow_settings"
GENERATION_TIMEOUT_S = 240  # espera maxima por prompt (Flow suele tardar 20-40 s)
POLL_S = 4
MAX_ATTEMPTS = 2  # reintentos por prompt si Flow no devuelve imagen
BATCH_SIZE = 8  # prompts por mensaje al agente de Flow
BATCH_TIMEOUT_S = 420
_IMG_ALT = "imagen del usuario"  # alt de las tarjetas de imagen en la UI en español
_NAME_RE = re.compile(r"escena_(\d{3})\s*$")
_manual = False  # True: se genera sin el agente (cuando su cuota se agota)
_attached_tab: Optional[str] = None  # id de la pestaña a la que esta conectada la sesion de agent-browser


class FlowError(RuntimeError):
    pass


# --- Navegador ---------------------------------------------------------------------------------

def browser_up() -> bool:
    """¿Hay algo escuchando en el puerto de depuracion del navegador de Flow?"""
    try:
        with socket.create_connection(("127.0.0.1", CDP_PORT), timeout=1.5):
            return True
    except OSError:
        return False


def launch_browser() -> bool:
    """Abre el navegador de Flow (perfil dedicado + su puerto de depuracion) si no estaba abierto.
    True si lo lanzo. El perfil propio es obligatorio: con el perfil de uso diario, el navegador se
    une a la ventana que ya esta abierta y nunca activa el puerto de depuracion."""
    if browser_up():
        return False
    exe, profile = BROWSER["exe"], BROWSER["profile"]
    if not Path(exe).exists():
        raise FlowError(f"No encontré {BROWSER['label']} en {exe} (definí su ruta en .env o cambiá FLOW_BROWSER).")
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [exe, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", FLOW_HOME],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return True


def _ab(args: list, timeout: int = 90) -> str:
    """Corre un comando de agent-browser en la sesion de Flow y devuelve su stdout."""
    cmd = [AGENT_BROWSER, "--session", SESSION] + [str(a) for a in args]
    # La salida va a archivos temporales, NO a pipes: si el comando arranca el daemon de agent-browser, este
    # hereda los pipes y subprocess.run no termina nunca (communicate espera su cierre, y el timeout no lo
    # corta). Con archivos solo se espera al proceso hijo.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            r = subprocess.run(cmd, stdout=out, stderr=err, stdin=subprocess.DEVNULL, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise FlowError(f"agent-browser {' '.join(map(str, args))[:60]} tardó más de {timeout}s") from e
        out.seek(0)
        err.seek(0)
        stdout = out.read().decode("utf-8", errors="replace")
        stderr = err.read().decode("utf-8", errors="replace")
    if r.returncode != 0 and "✗" in (stderr + stdout):
        raise FlowError(f"agent-browser {' '.join(map(str, args))[:60]} falló: {(stderr or stdout).strip()[:300]}")
    return stdout


def _eval(js: str, timeout: int = 90):
    """Evalua JS en la pagina y devuelve el valor (agent-browser lo imprime como JSON)."""
    out = _ab(["eval", js], timeout=timeout).strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def _cdp(path: str, method: str = "GET"):
    """Llamada HTTP al puerto de depuracion del navegador (lista de pestañas, abrir, activar, cerrar)."""
    r = requests.request(method, f"http://127.0.0.1:{CDP_PORT}{path}", timeout=10)
    if r.status_code == 405 and method != "GET":  # versiones viejas del navegador solo aceptan GET
        r = requests.get(f"http://127.0.0.1:{CDP_PORT}{path}", timeout=10)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return r.text


def _pages() -> list:
    """Pestañas abiertas del navegador de Flow, la usada mas recientemente primero."""
    return [t for t in _cdp("/json") if t.get("type") == "page"]


def _own_tab() -> Optional[dict]:
    """La pestaña de esta app. En un navegador dedicado (Edge) se adopta la de Flow que ya este abierta."""
    tab_id = job_store.load(SETTINGS_STORE).get("tab_id")
    pages = _pages()
    tab = next((t for t in pages if t["id"] == tab_id), None) if tab_id else None
    if tab is None and BROWSER["dedicated"]:
        tab = next((t for t in pages if "flow.google.com" in t.get("url", "")), None)
        if tab:
            job_store.save(SETTINGS_STORE, {**job_store.load(SETTINGS_STORE), "tab_id": tab["id"]})
    return tab


def _open_own_tab() -> dict:
    """Abre una pestaña nueva de Flow y la deja como la de esta app (queda guardado su id)."""
    tab = _cdp(f"/json/new?{quote(FLOW_HOME, safe=':/')}", "PUT")
    job_store.save(SETTINGS_STORE, {**job_store.load(SETTINGS_STORE), "tab_id": tab["id"]})
    return tab


def ensure_connected() -> str:
    """Se asegura de estar conectado a la pestaña PROPIA de Flow (abriendola si hace falta, sin tocar las
    de otros programas) y devuelve su URL actual."""
    global _attached_tab
    if not browser_up():
        raise FlowError("El navegador de Flow está cerrado: abrilo con «Abrir Flow» e iniciá sesión en Google.")
    tab = _own_tab()
    if tab is None:
        tab = _open_own_tab()
        _attached_tab = None
    if _attached_tab != tab["id"]:
        _ab(["connect", tab["webSocketDebuggerUrl"]], timeout=60)  # directo a SU pestaña, no a la activa
        _attached_tab = tab["id"]
    url = ""
    for _ in range(10):  # una pestaña recien abierta tarda un momento en llegar a Flow
        url = _ab(["get", "url"], timeout=30).strip()
        if "flow.google.com" in url or "accounts.google.com" in url:
            break
        time.sleep(1.5)
    if "flow.google.com" not in url:
        raise FlowError("La pestaña de Flow no cargó (¿sesión de Google cerrada?): abrí «Abrir Flow» e iniciá sesión en Google.")
    if _eval("document.body.innerText.includes('Iniciar sesión') && "
             "!document.body.innerText.includes('Nuevo proyecto') && "
             "!document.querySelector('[contenteditable]')") is True:
        raise FlowError(f"Flow pide iniciar sesión: entrá a mano en la ventana de {BROWSER['label']} de Flow.")
    return url


def status() -> dict:
    """{"state": "ok" | "no_browser" | "needs_login" | "unreachable", "message": str} para Ajustes."""
    if not browser_up():
        return {"state": "no_browser", "message": "El navegador de Flow está cerrado."}
    try:  # pasivo: solo mira las pestañas, no abre ni cambia nada
        urls = [t.get("url", "") for t in _pages()]
        own = _own_tab()
    except (requests.RequestException, ValueError):
        return {"state": "unreachable", "message": "No pude leer las pestañas del navegador de Flow."}
    others = [u for u in urls if "flow.google.com/project" in u and not (own and u == own.get("url"))]
    if not any("flow.google.com" in u for u in urls) and any("accounts.google.com" in u for u in urls):
        return {"state": "needs_login",
                "message": f"Flow pide iniciar sesión: entrá a mano en la ventana de {BROWSER['label']} de Flow."}
    note = " Hay otro proyecto de Flow abierto: se usará una pestaña propia." if others else ""
    return {"state": "ok", "message": "Conectado a Google Flow." + note}


# --- Proyecto y generacion ---------------------------------------------------------------------

def _ref_for(name: str) -> Optional[str]:
    """ref (@eN) del primer boton/enlace interactivo cuyo nombre contiene `name`."""
    snap = _ab(["snapshot", "-i"], timeout=60)
    for line in snap.splitlines():
        if name.lower() in line.lower():
            m = re.search(r"\[(?:[^\]]*, )?ref=(e\d+)\]", line)
            if m:
                return "@" + m.group(1)
    return None


def _wait_ready(timeout: int = 40) -> None:
    """Espera a que desaparezca la pantalla de carga de Flow (si no, tapa los botones)."""
    end = time.time() + timeout
    while time.time() < end:
        if _eval("!document.querySelector('flow-loading-page')") is True:
            return
        time.sleep(1)


def open_project(project_url: Optional[str] = None, on_progress: Optional[Callable[[str], None]] = None) -> str:
    """Abre un proyecto existente (project_url) o crea uno nuevo. Devuelve su URL."""
    say = on_progress or (lambda m: None)
    current = ensure_connected()
    if project_url:
        # recargar deja la UI sin responder a clics un buen rato: si ya estamos ahi, no recargar
        if current.split("?")[0].rstrip("/") != project_url.split("?")[0].rstrip("/"):
            _ab(["open", project_url], timeout=60)
    else:
        say("Creando proyecto nuevo en Flow...")
        # Tras cargar la pagina, Flow ignora los clics durante un buen rato: se abre UNA vez y se
        # reintenta el clic sin recargar (recargar reinicia esa espera).
        if "/project/" in current:
            pass
        else:
            _ab(["open", FLOW_HOME], timeout=60)
        for _ in range(20):  # esperar a que aparezca la tarjeta 'Nuevo proyecto'
            if _eval("document.body.innerText.includes('Nuevo proyecto')") is True:
                break
            time.sleep(1.5)
        for attempt in range(40):  # hasta ~10 min: tras cargar, Flow tarda en habilitar el boton
            time.sleep(15 if attempt else 3)
            try:
                _ab(["click", "button.mdc-fab"], timeout=60)  # boton flotante 'Nuevo proyecto'
            except FlowError:
                continue
            for _ in range(8):
                if "/project/" in _ab(["get", "url"], timeout=30):
                    break
                time.sleep(1.5)
            else:
                continue
            break
    for _ in range(20):
        url = _ab(["get", "url"], timeout=30).strip()
        if "/project/" in url:
            break
        time.sleep(1.5)
    else:
        raise FlowError("No se pudo abrir/crear el proyecto de Flow.")
    _wait_ready()
    for _ in range(20):  # esperar a que cargue la caja de prompts
        if _eval("!!document.querySelector('[contenteditable]')") is True:
            return url
        time.sleep(1.5)
    raise FlowError("La caja de prompts de Flow no apareció.")


def ensure_project(on_progress: Optional[Callable[[str], None]] = None) -> str:
    """Abre el proyecto de Flow compartido (el de la corrida anterior, o uno nuevo) y guarda su URL."""
    saved = job_store.load(SETTINGS_STORE).get("project_url")
    try:
        url = open_project(saved, on_progress)
    except FlowError:
        if not saved:
            raise
        url = open_project(None, on_progress)  # el proyecto guardado ya no existe: uno nuevo
    if url != saved:
        job_store.save(SETTINGS_STORE, {**job_store.load(SETTINGS_STORE), "project_url": url})
    return url


def _dismiss_announcements() -> None:
    """Cierra los avisos emergentes de novedades de Flow, que tapan todo."""
    _eval("(()=>{const d=document.querySelector('[role=dialog],mat-dialog-container,.cdk-overlay-pane');"
          "if(!d||!/Empezar|Entendido|Got it/.test(d.innerText))return 0;"
          "const b=[...d.querySelectorAll('button')].find(b=>/^(Empezar|Entendido|Got it)$/.test(b.innerText.trim()));"
          "if(b)b.click();return 1})()")


def _is_busy() -> bool:
    """Flow esta generando mientras el boton 'Detener' esta en pantalla."""
    return _eval("[...document.querySelectorAll('button')].some(b=>"
                 "(b.getAttribute('aria-label')||b.innerText||'').trim()==='Detener')") is True


def _click_generate() -> None:
    """Pulsa 'Iniciar generación': primero por ref; si algo lo tapa, por JavaScript."""
    time.sleep(2.5)  # el boton se habilita un instante despues de insertar el texto
    for _ in range(30):  # tras cargar la pagina, Flow tarda minutos en responder a clics
        _eval("document.querySelector('flow-loading-page')?.remove(); 1")
        _dismiss_announcements()
        ref = _ref_for("Iniciar generación")
        if not ref:
            time.sleep(3)
            continue
        try:
            _ab(["click", ref], timeout=30)
        except FlowError:
            time.sleep(2)
            continue
        time.sleep(4)
        if _is_busy() or not _eval("document.querySelector('[contenteditable]')?.innerText?.trim()"):
            return  # ya arranco o el texto se envio
    ok = _eval("(()=>{const b=[...document.querySelectorAll('button')].filter(b=>"
               "((b.getAttribute('aria-label')||b.innerText||'').includes('Iniciar generación'))"
               "&&!b.disabled&&b.getBoundingClientRect().width>0).pop(); "
               "if(!b) return false; b.click(); return true;})()")
    if ok is not True:
        raise FlowError("No pude pulsar 'Iniciar generación' (botón inexistente o deshabilitado).")


def _image_srcs() -> list:
    val = _eval("JSON.stringify([...document.querySelectorAll('img')]"
                f".filter(i=>(i.alt||'').toLowerCase().includes('{_IMG_ALT}') && i.naturalWidth>200)"
                ".map(i=>i.src))")
    return json.loads(val) if isinstance(val, str) else (val or [])


def _quota_exhausted() -> bool:
    """¿Flow muestra «Has alcanzado tu límite de cuota del agente»?"""
    return _eval("document.body.innerText.includes('límite de cuota del agente')") is True


def _use_manual(say: Optional[Callable[[str], None]] = None) -> None:
    """Apaga el interruptor «Agente» de Flow: cada imagen se pide directo al modelo (una por mensaje)."""
    global _manual
    if say:
        say("Cuota del agente de Flow agotada: sigo en modo manual (una imagen por petición).")
    _manual = True
    # con «Agente» encendido la barra no muestra el modelo; con el apagado aparece «Nano Banana … x1»
    for _ in range(3):
        if _eval("/Nano Banana/.test(document.body.innerText)") is True:
            return
        _eval("(()=>{const b=[...document.querySelectorAll('button')].find(b=>(b.innerText||'').trim()==='Agente');"
              "if(b)b.click();})()")
        time.sleep(2)


def _download(src: str, dest: Path) -> None:
    logger.info("flow: descargando imagen -> %s", dest.name)
    # Las imagenes recien generadas llegan como enlaces firmados de flow-content.google, que el navegador
    # rechaza si se piden con credenciales (CORS): se prueba con y sin ellas.
    b64 = None
    for creds in ("include", "omit"):
        try:
            b64 = _eval("(async()=>{const r=await fetch(" + json.dumps(src) + ",{credentials:'" + creds + "'});"
                        "if(!r.ok) return 'ERR'+r.status; const b=await r.blob();"
                        "return await new Promise(res=>{const f=new FileReader();"
                        "f.onload=()=>res(String(f.result).split(',')[1]);f.readAsDataURL(b);});})()",
                        timeout=120)
        except FlowError:
            continue
        if isinstance(b64, str) and not b64.startswith("ERR") and len(b64) >= 1000:
            break
    if not isinstance(b64, str) or b64.startswith("ERR") or len(b64) < 1000:
        raise FlowError(f"No se pudo descargar la imagen de Flow ({str(b64)[:40]}).")
    from PIL import Image
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=92)


# Ajustes de generacion de Flow (boton "🍌 Nano Banana … crop_9_16 x1" de la barra): imagen, vertical, 1 por peticion.
_ASPECTS = ("16:9", "4:3", "1:1", "3:4", "9:16")  # los que ofrece el selector de Flow
_ASPECT_WORDS = {"9:16": "vertical 9:16", "1:1": "cuadrado 1:1"}
_aspect = "9:16"  # el que dejo configure_generation (lo usa el mensaje al agente de Flow)
_TRIGGER = "button.settings-trigger-button"
_TOGGLE_JS = (
    "((label)=>{const t=[...document.querySelectorAll('mat-button-toggle')].find(e=>"
    "(e.querySelector('.toggle-text')?.innerText||'').trim()===label&&e.getBoundingClientRect().width>0);"
    "if(!t) return 'missing'; if(t.classList.contains('mat-button-toggle-checked')) return 'on';"
    "t.querySelector('button').click(); return 'clicked';})"
)


def _format_words() -> str:
    return _ASPECT_WORDS.get(_aspect, _aspect)


def configure_generation(aspect: str = "9:16") -> None:
    """Deja los ajustes de Flow en Imagen / `aspect` / x1: 9:16 para las escenas de los videos, 1:1 para los
    posts de imagen (por defecto Flow genera 16:9). Se hace en cada tanda porque la siguiente puede pedir
    otro formato. Con el agente encendido la barra no muestra el boton de ajustes: ahi el formato se pide
    en el propio mensaje."""
    global _aspect
    if aspect not in _ASPECTS:
        raise FlowError(f"Formato de Flow desconocido: {aspect}")
    _aspect = aspect
    if _eval(f"!!document.querySelector('{_TRIGGER}')") is not True:
        return
    for _ in range(3):  # abrir el panel con un clic real (el .click() de JS no siempre lo abre)
        if _eval("document.querySelectorAll('mat-button-toggle').length > 0") is True:
            break
        _ab(["click", _TRIGGER], timeout=30)
        time.sleep(1.5)
    else:
        raise FlowError("No pude abrir el panel de ajustes de Flow.")
    for label in ("Imagen", aspect, "x1"):
        for _ in range(3):
            state = _eval(f"{_TOGGLE_JS}({json.dumps(label)})")
            if state == "on":
                break
            if state == "missing":
                raise FlowError(f"No encontré la opción «{label}» en los ajustes de Flow.")
            time.sleep(0.8)
        else:
            raise FlowError(f"No pude activar «{label}» en los ajustes de Flow.")
    _ab(["press", "Escape"], timeout=30)  # cierra el panel de ajustes
    time.sleep(0.5)
    bar = str(_eval(f"document.querySelector('{_TRIGGER}')?.innerText || ''"))
    if aspect == "9:16" and "crop_9_16" not in bar:  # el icono de la barra confirma el 9:16
        raise FlowError(f"Flow no quedó en vertical 9:16 (ajustes: {bar.strip()[:60]!r}).")
    if aspect != "9:16" and "crop_9_16" in bar:
        raise FlowError(f"Flow sigue en vertical 9:16 y se pidió {aspect} (ajustes: {bar.strip()[:60]!r}).")


def _type_prompt(text: str) -> None:
    _eval("(()=>{const e=document.querySelector('[contenteditable]');"
          "e.focus();document.execCommand('selectAll');document.execCommand('delete');})()")
    _ab(["keyboard", "inserttext", text], timeout=60)
    time.sleep(1)


def generate_image(prompt: str, dest: Path) -> Path:
    """Genera UNA imagen para `prompt` en el proyecto de Flow ya abierto y la guarda como JPEG en `dest`."""
    last: Optional[Exception] = None
    for _ in range(MAX_ATTEMPTS):
        try:
            if not _manual and (_quota_exhausted() or _eval("/Nano Banana/.test(document.body.innerText)") is True):
                _use_manual()
            before = set(_image_srcs())
            _type_prompt(("" if _manual else f"Genera una imagen en formato {_format_words()}: ") + prompt)
            _click_generate()
            deadline = time.time() + GENERATION_TIMEOUT_S
            time.sleep(6)
            while time.time() < deadline:
                new = [s for s in _image_srcs() if s not in before]
                if new and not _is_busy():
                    _download(new[0], dest)
                    return dest
                time.sleep(POLL_S)
            raise FlowError(f"Flow no devolvió imagen en {GENERATION_TIMEOUT_S}s.")
        except FlowError as e:
            last = e
            time.sleep(3)
    raise FlowError(f"Flow falló generando la imagen tras {MAX_ATTEMPTS} intentos: {last}")


def _begin_job() -> Optional[str]:
    """Trae al frente la pestaña propia (Flow solo responde bien en primer plano) y devuelve el id de la
    pestaña que estaba activa, para devolverle el foco al terminar."""
    own_id = job_store.load(SETTINGS_STORE).get("tab_id")
    # ANTES de abrir la pestaña propia (que pasa a ser la activa): la ultima pestaña ajena usada
    prev = next((t["id"] for t in _pages() if t["id"] != own_id), None)
    ensure_connected()
    own = _own_tab()
    if own:
        _cdp(f"/json/activate/{own['id']}")
    return prev


def _end_job(prev: Optional[str]) -> None:
    try:
        if prev and any(t["id"] == prev for t in _pages()):
            _cdp(f"/json/activate/{prev}")
    except requests.RequestException:
        pass


def generate_post_image(prompt: str, dest: Path, on_progress: Optional[Callable[[str], None]] = None,
                        aspect: str = "1:1") -> Path:
    """Una imagen suelta (portada de un post): abre el proyecto compartido y la genera."""
    prev = _begin_job()
    try:
        ensure_project(on_progress)
        configure_generation(aspect)
        return generate_image(prompt, dest)
    finally:
        _end_job(prev)


def _named_tiles(exclude: set) -> dict:
    """{numero de escena: src} de las tarjetas nuevas que Flow nombro 'escena_NNN'."""
    val = _eval(
        "JSON.stringify([...document.querySelectorAll('img')]"
        f".filter(i=>(i.alt||'').toLowerCase().includes('{_IMG_ALT}') && i.naturalWidth>200)"
        ".map(i=>{let c=i;for(let k=0;k<6&&c;k++){c=c.parentElement;"
        "if(c&&c.innerText&&c.innerText.trim().length>2)break;}"
        "return {src:i.src,txt:(c&&c.innerText||'').trim()};}))")
    tiles = json.loads(val) if isinstance(val, str) else (val or [])
    out = {}
    for t in tiles:
        if t["src"] in exclude:
            continue
        m = _NAME_RE.search(t["txt"])
        if m and int(m.group(1)) not in out:
            out[int(m.group(1))] = t["src"]
    return out


def _generate_batch(items: list, images_dir: Path) -> None:
    """items = [(indice_de_escena, prompt)]. Un solo mensaje al agente con todos los prompts; cada imagen
    se reconoce por el nombre escena_NNN que se le pide a Flow. Las que falten se piden de a una."""
    before = set(_image_srcs())
    wanted = {i + 1: p for i, p in items}  # escena_NNN usa numeracion desde 1
    names = ", ".join(f"escena_{n:03d}" for n in wanted)
    msg = (f"Genera estas {len(wanted)} imágenes en formato {_format_words()}, una por cada prompt, y nombra cada imagen "
           f"exactamente como {names} (una imagen por nombre):\n" +
           "\n".join(f"escena_{n:03d}: {p}" for n, p in wanted.items()))
    _type_prompt(msg)
    _click_generate()
    t0 = time.time()
    time.sleep(8)
    found: dict = {}
    while time.time() < t0 + BATCH_TIMEOUT_S:
        if _quota_exhausted():
            break  # el agente no va a responder: pasa a modo manual
        found = _named_tiles(before)
        if all(n in found for n in wanted) and not _is_busy():
            break
        if not _is_busy() and time.time() - t0 > 60 and found:
            break  # Flow termino y faltan algunas: se reintentan una por una
        time.sleep(POLL_S)
    for n, src in found.items():
        if n in wanted:
            _download(src, images_dir / f"scene_{n - 1:03d}.jpg")


def generate_scene_images(story: dict, images_dir: Path, on_progress: Optional[Callable[[str], None]] = None,
                          aspect: str = "9:16") -> list:
    """Genera scene_000.jpg ... para las escenas de `story` que aun falten (reanudable): primero en
    lotes de BATCH_SIZE prompts por mensaje; lo que falte, de a una. Devuelve las rutas de esta corrida."""
    say = on_progress or (lambda m: None)
    prompts = story["prompts"]
    total = len(prompts)
    images_dir.mkdir(parents=True, exist_ok=True)

    def ok(i: int) -> bool:
        f = images_dir / f"scene_{i:03d}.jpg"
        return f.exists() and f.stat().st_size > 10_000

    logger.info("flow: %d imagen(es) por generar en %s", total, images_dir)
    prev = _begin_job()
    try:
        ensure_project(say)
        logger.info("flow: proyecto abierto, configurando ajustes (%s)", aspect)
        configure_generation(aspect)
        missing = [i for i in range(total) if not ok(i)]
        if _quota_exhausted() or _eval("/Nano Banana/.test(document.body.innerText)") is True:
            _use_manual(say)
        for k in range(0, len(missing), BATCH_SIZE):
            if _manual:
                break  # sin agente no hay lotes: el resto va de a una
            chunk = missing[k:k + BATCH_SIZE]
            say(f"[{chunk[0] + 1}-{chunk[-1] + 1}/{total}] Flow: lote de {len(chunk)} imágenes...")
            try:
                _generate_batch([(i, prompts[i]["prompt"]) for i in chunk], images_dir)
            except FlowError as e:
                say(f"Lote falló ({e}); sigo de a una.")
            if _quota_exhausted():
                _use_manual(say)
        for i in [i for i in missing if not ok(i)]:
            say(f"[{i + 1}/{total}] Flow: imagen individual...")
            generate_image(prompts[i]["prompt"], images_dir / f"scene_{i:03d}.jpg")
        return [images_dir / f"scene_{i:03d}.jpg" for i in missing if ok(i)]
    finally:
        _end_job(prev)
