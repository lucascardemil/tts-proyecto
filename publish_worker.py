"""Publicacion sin servidor 24/7: script de UN SOLO PASO, pensado para
correr cada N minutos via Windows Task Scheduler (no un loop propio -- la
cadencia la da Task Scheduler).

Corre solo la mitad de "publicacion" del scheduler de lotes
(batch_pipeline.run_publish_tick), nunca la de generacion -- esa depende del
browser automation de Qwen/WhatsApp y necesita el proceso completo de app.py
abierto con sesion de navegador. Facebook y YouTube ya quedan publicados con
scheduling nativo (Meta/YouTube sostienen el horario del lado de ellos), asi
que alcanza con que este script corra una vez para registrarlos. Instagram
no tiene scheduling nativo: para que sus posts salgan a horario real, este
script (o app.py) tiene que estar corriendo en el momento exacto -- por eso
hace falta la tarea programada.

Uso manual (para probar):
    python publish_worker.py

Instagram video (Reels) funciona sin problema aca: sube el archivo local
directo, no necesita URL publica. Instagram FOTO (solo el proyecto tipo
gaming_image) si necesita la ruta publica de app.py detras del Cloudflare
Tunnel -- si app.py no esta corriendo en ese momento, esos posts quedan en
"ready" reintentando hasta que app.py vuelva a estar arriba (ver
MAX_AUTO_RETRIES en batch_pipeline.py). Es la unica limitacion conocida de
este script; el resto de los proyectos (video) no la tiene.
"""

import logging
import sys

import batch_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("publish_worker")


def main() -> int:
    lock = batch_pipeline._try_acquire_scheduler_lock_once()
    if lock is None:
        log.info("app.py ya tiene el scheduler activo (lock tomado) -- nada que hacer, salgo.")
        return 0

    try:
        threads = batch_pipeline.run_publish_tick()
        if not threads:
            log.info("sin videos listos para publicar en este momento.")
        else:
            log.info("publicando %d video(s), esperando a que terminen...", len(threads))
            for t in threads:
                t.join()
            log.info("listo.")
    finally:
        batch_pipeline._release_scheduler_lock_handle(lock)

    return 0


if __name__ == "__main__":
    sys.exit(main())
