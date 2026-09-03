"""
Persistencia simple de los diccionarios de jobs (TTS, video, publicaciones)
a disco, para que el estado sobreviva a un reinicio del servidor y se pueda
ver qué pasó con un job aunque el proceso se haya caído a mitad de camino.

No es una cola de tareas ni un motor de reintentos — solo un checkpoint:
cada vez que un job cambia de estado se vuelca a JSON.
"""

import json
import threading
from pathlib import Path

_STATE_DIR = Path(__file__).parent / "output" / "job_state"
_STATE_DIR.mkdir(parents=True, exist_ok=True)
_write_lock = threading.Lock()


def _path(name: str) -> Path:
    return _STATE_DIR / f"{name}.json"


def save(name: str, jobs: dict) -> None:
    """Vuelca el diccionario completo de jobs (name -> jobs dict) a disco."""
    try:
        with _write_lock:
            _path(name).write_text(json.dumps(jobs, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass  # el checkpoint es una ayuda, no debe interrumpir el pipeline


def load(name: str) -> dict:
    """Recupera el último estado guardado para `name`, o {} si no hay nada."""
    path = _path(name)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
