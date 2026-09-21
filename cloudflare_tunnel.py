"""
Lanza `cloudflared tunnel --url http://localhost:<port>` (Quick Tunnel, sin
dominio propio) como subproceso al arrancar la app, para no depender de que
el usuario lo corra a mano en otra terminal y pegue la URL en Ajustes.

La URL `https://algo.trycloudflare.com` es efímera y cambia en cada
reinicio -- eso es esperado (ver card "Ajustes" del dashboard).

cloudflared imprime su log (incluida la línea con la URL) por stderr, no
por stdout -- por eso acá se combinan ambos (stderr=subprocess.STDOUT) antes
de leerlos.

Si `cloudflared` no está instalado, start() no lanza nada y devuelve None:
la app sigue funcionando en local sin túnel, no debe romper el arranque.
"""

import logging
import re
import shutil
import subprocess
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
_STARTUP_TIMEOUT_SECONDS = 15
_INSTALL_HINT = (
    "cloudflared no está instalado. Instalalo una vez con:\n"
    "    winget install --id Cloudflare.cloudflared\n"
    "y reiniciá la app. Mientras tanto, el dashboard sigue funcionando "
    "solo en la red local (sin URL pública)."
)

_proc: Optional[subprocess.Popen] = None
_lock = threading.Lock()


def is_installed() -> bool:
    return shutil.which("cloudflared") is not None


def start(local_port: int, on_url: Callable[[str], None]) -> Optional[subprocess.Popen]:
    """
    Lanza cloudflared en background. Cuando detecta la URL pública en su
    output, llama a on_url(url) desde el hilo lector (no bloquea el resto
    del arranque más allá de _STARTUP_TIMEOUT_SECONDS). No lanza excepciones:
    cualquier fallo se loguea y devuelve None.
    """
    global _proc

    if not is_installed():
        logger.warning("cloudflared no está instalado -- sigue sin túnel.")
        print(f"[cloudflare_tunnel] {_INSTALL_HINT}")
        return None

    cmd = [
        "cloudflared", "tunnel", "--url", f"http://localhost:{local_port}",
        "--metrics", "localhost:0",  # puerto de métricas libre: evita choque si quedó un cloudflared zombie
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except OSError as e:
        logger.warning("No se pudo lanzar cloudflared: %s", e)
        return None

    with _lock:
        _proc = proc

    result = {"found": False}
    found_event = threading.Event()

    def _reader() -> None:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line:
                logger.info("[cloudflared] %s", line)
            if not result["found"]:
                m = _URL_RE.search(line)
                if m:
                    result["found"] = True
                    found_event.set()
                    try:
                        on_url(m.group(0))
                    except Exception:
                        logger.exception("cloudflare_tunnel: on_url callback falló")
        found_event.set()  # stdout se cerró -> el proceso terminó (con o sin URL)

    threading.Thread(target=_reader, daemon=True, name="cloudflared-reader").start()

    found_event.wait(timeout=_STARTUP_TIMEOUT_SECONDS)
    if not result["found"]:
        if proc.poll() is not None:
            logger.warning("cloudflared terminó (código %s) sin imprimir URL pública.", proc.poll())
            print(f"[cloudflare_tunnel] cloudflared se cerró solo (código {proc.poll()}), sigue sin túnel.")
        else:
            logger.warning("cloudflared no imprimió URL en %ss, sigue corriendo en background.", _STARTUP_TIMEOUT_SECONDS)
            print(f"[cloudflare_tunnel] Aviso: sin URL pública tras {_STARTUP_TIMEOUT_SECONDS}s, "
                  "la app sigue arrancando sin esperar más (puede aparecer después).")

    return proc


def stop() -> None:
    """Termina cloudflared si sigue corriendo. Idempotente -- seguro llamarla más de una vez."""
    global _proc
    with _lock:
        proc, _proc = _proc, None
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        logger.exception("cloudflare_tunnel: error cerrando cloudflared")
