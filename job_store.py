"""
Persistencia simple de los diccionarios de jobs (TTS, video, publicaciones)
a disco, para que el estado sobreviva a un reinicio del servidor y se pueda
ver qué pasó con un job aunque el proceso se haya caído a mitad de camino.

No es una cola de tareas ni un motor de reintentos — solo un checkpoint:
cada vez que un job cambia de estado se vuelca a JSON.
"""

import json
import os
import threading
from pathlib import Path

_STATE_DIR = Path(__file__).parent / "output" / "job_state"
_STATE_DIR.mkdir(parents=True, exist_ok=True)
_write_lock = threading.Lock()


def _path(name: str) -> Path:
    return _STATE_DIR / f"{name}.json"


def save(name: str, jobs: dict, *, strict: bool = False) -> None:
    """Vuelca el diccionario completo de jobs (name -> jobs dict) a disco.

    strict=True: escribe a un temporal del mismo directorio y lo mueve con os.replace (atomico), y
    deja pasar cualquier excepcion (para estado de seguridad que no puede perderse en silencio)."""
    if strict:
        with _write_lock:
            path = _path(name)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(jobs, ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(tmp, path)
        return
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
        # Bajo el mismo lock que save(): write_text trunca el archivo antes de
        # escribirlo, y un lector sin lock veia JSON a medias -> {} silencioso
        # (un proyecto "desaparecia" y el hilo de publicacion moria).
        with _write_lock:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
