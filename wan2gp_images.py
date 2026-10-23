# -*- coding: utf-8 -*-
"""WanGP (Wan2GP) como proveedor local de imagenes: genera las escenas en esta PC, sin navegador, sesion ni cuota.

Lanza `wgp.py --process <cola.json>` (modo sin interfaz de WanGP) con todas las escenas faltantes en UNA cola: el
arranque y la carga del modelo (~1 min) se pagan una sola vez y cada imagen tarda ~10 s en una RTX 3050 de 6 GB.
Cada tarea lleva una semilla propia (SEED_BASE + indice) y el nombre del archivo guardado la incluye: asi se reconoce
que imagen es de que escena aunque WanGP las nombre por el prompt.

Configuracion (.env): WAN2GP_DIR (carpeta de la instalacion, por defecto ~/wan2gp), WAN2GP_MODEL (model_type,
por defecto flux2_klein_4b), WAN2GP_PROFILE (perfil de memoria, por defecto 4).
"""
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

logger = logging.getLogger(__name__)

SEED_BASE = 1000
DEFAULT_MODEL = "flux2_klein_4b"
SIZES = {"9:16": "576x1024", "1:1": "1024x1024", "3:4": "768x1024", "4:5": "832x1024", "16:9": "1024x576"}
STARTUP_TIMEOUT_S = 600  # primera vez: descarga el modelo (varios GB)
PER_IMAGE_TIMEOUT_S = 120
_SEED_RE = re.compile(r"_seed(\d+)_")


class Wan2gpError(RuntimeError):
    pass


def install_dir() -> Path:
    return Path(os.environ.get("WAN2GP_DIR") or (Path.home() / "wan2gp"))


def _python() -> Path:
    base = install_dir() / ".venv"
    return base / "Scripts" / "python.exe" if sys.platform == "win32" else base / "bin" / "python"


def is_installed() -> bool:
    return (install_dir() / "wgp.py").exists() and _python().exists()


def status() -> dict:
    """{"state": "ok" | "not_installed", "message": str} para Ajustes."""
    if not is_installed():
        return {"state": "not_installed", "message": f"WanGP no está instalado en {install_dir()} (ver WAN2GP_DIR en .env)."}
    return {"state": "ok", "message": f"WanGP instalado en {install_dir()} (modelo {os.environ.get('WAN2GP_MODEL') or DEFAULT_MODEL})."}


def build_queue(prompts: list, aspect: str, model: Optional[str] = None) -> list:
    """Cola de WanGP: una tarea por (indice_de_escena, prompt), con su semilla propia."""
    model = model or os.environ.get("WAN2GP_MODEL") or DEFAULT_MODEL
    size = SIZES.get(aspect, SIZES["9:16"])
    return [
        {"model_type": model, "prompt": prompt, "resolution": size, "batch_size": 1, "seed": SEED_BASE + index,
         "num_inference_steps": 4, "embedded_guidance_scale": 1}
        for index, prompt in prompts
    ]


def collect_outputs(out_dir: Path) -> dict:
    """{indice_de_escena: ruta} de las imagenes que WanGP dejo en `out_dir` (la semilla va en el nombre)."""
    found = {}
    for path in sorted(out_dir.glob("*")):
        m = _SEED_RE.search(path.name)
        if m and path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and int(m.group(1)) >= SEED_BASE:
            found[int(m.group(1)) - SEED_BASE] = path
    return found


def generate_scene_images(story: dict, images_dir: Path, on_progress: Optional[Callable[[str], None]] = None,
                          aspect: str = "9:16") -> list:
    """Genera scene_000.jpg ... para las escenas de `story` que aun falten (reanudable). Devuelve las rutas creadas."""
    say = on_progress or (lambda m: None)
    if not is_installed():
        raise Wan2gpError(f"WanGP no está instalado en {install_dir()}: instálalo o define WAN2GP_DIR en .env.")
    prompts = story["prompts"]
    images_dir.mkdir(parents=True, exist_ok=True)
    missing = [(i, p["prompt"]) for i, p in enumerate(prompts)
               if not ((images_dir / f"scene_{i:03d}.jpg").exists() and (images_dir / f"scene_{i:03d}.jpg").stat().st_size > 10_000)]
    if not missing:
        return []

    work = Path(tempfile.mkdtemp(prefix="wan2gp_"))
    try:
        queue_path, out_dir = work / "queue.json", work / "out"
        queue_path.write_text(json.dumps(build_queue(missing, aspect), ensure_ascii=False), encoding="utf-8")
        say(f"WanGP: generando {len(missing)} imagen(es) en esta PC (la primera vez descarga el modelo)...")
        cmd = [str(_python()), "wgp.py", "--process", str(queue_path), "--output-dir", str(out_dir),
               "--profile", os.environ.get("WAN2GP_PROFILE") or "4", "--attention", "sdpa"]
        try:
            result = subprocess.run(
                cmd, cwd=str(install_dir()), capture_output=True,
                timeout=STARTUP_TIMEOUT_S + PER_IMAGE_TIMEOUT_S * len(missing),
            )
        except subprocess.TimeoutExpired as e:
            raise Wan2gpError(f"WanGP no terminó a tiempo ({len(missing)} imágenes).") from e
        found = collect_outputs(out_dir) if out_dir.exists() else {}
        created = []
        for index, _ in missing:
            src = found.get(index)
            if src is None:
                continue
            dest = images_dir / f"scene_{index:03d}.jpg"
            with Image.open(src) as img:
                img.convert("RGB").save(dest, "JPEG", quality=95)
            created.append(dest)
        if len(created) < len(missing):
            tail = (result.stdout + result.stderr).decode("utf-8", errors="replace").replace("\r", "\n")[-400:]
            raise Wan2gpError(
                f"WanGP generó {len(created)} de {len(missing)} imágenes (código {result.returncode}): {tail.strip()}")
        say(f"WanGP: {len(created)} imagen(es) listas.")
        return created
    finally:
        shutil.rmtree(work, ignore_errors=True)
