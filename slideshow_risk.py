# -*- coding: utf-8 -*-
"""Riesgo de "slideshow": cuanto se parece un video a imagenes fijas pasando, en vez de un video dirigido.

Puntua la timeline que arma video_maker.build_props (escenas con type, kenBurns, startFrame/endFrame)
en 4 dimensiones de 0 a 5 (menor es mejor). Pura: sin red ni disco. Idea tomada de OpenMontage
(lib/slideshow_risk.py, AGPL-3.0); implementacion propia sobre el esquema de escenas de este proyecto.

  static_scenes : escenas de imagen sin movimiento (kenBurns "none")
  low_motion    : pocos clips de video reales cuando se esperaba movimiento
  long_hold     : una imagen se queda demasiado tiempo en pantalla
  slow_cuts     : promedio de segundos por escena demasiado alto

Veredicto por promedio: < 2.0 strong, < 3.0 acceptable, < 4.0 revise, >= 4.0 fail."""

FPS = 30
LONG_HOLD_OK, LONG_HOLD_WORST = 6.0, 12.0   # segundos que una imagen puede quedarse / desde donde puntua 5
SLOW_CUT_OK, SLOW_CUT_WORST = 5.0, 10.0     # segundos promedio por escena
MOTION_TARGET = 0.5                         # proporcion de clips de video bajo la cual low_motion empieza a puntuar


def expects_motion(video_settings: dict) -> bool:
    """Flow solo genera imagenes: ahi la ausencia de clips de video no es una degradacion."""
    vs = video_settings or {}
    if "flow" in (vs.get("provider"), vs.get("image_provider")):
        return False
    return bool(vs.get("generate_video_clips", True))


def _verdict(average: float) -> str:
    if average < 2.0:
        return "strong"
    if average < 3.0:
        return "acceptable"
    if average < 4.0:
        return "revise"
    return "fail"


def _seconds(scene: dict) -> float:
    return max(scene["endFrame"] - scene["startFrame"], 1) / FPS


def _scale(value: float, ok: float, worst: float) -> float:
    if value <= ok:
        return 0.0
    return min(5.0, 5.0 * (value - ok) / (worst - ok))


def score_timeline(timeline: dict, expects_motion: bool = False) -> dict:
    scenes = (timeline or {}).get("scenes") or []
    if not scenes:
        return {"average": 5.0, "verdict": "fail", "dimensions": {}}
    n = len(scenes)
    images = [s for s in scenes if s.get("type") != "video"]
    videos = n - len(images)

    static = sum(1 for s in images if s.get("kenBurns") in (None, "none"))
    longest = max((_seconds(s) for s in images), default=0.0)
    average_seconds = sum(_seconds(s) for s in scenes) / n
    video_share = videos / n

    dimensions = {
        "static_scenes": (5.0 * static / n, f"{static} de {n} escenas sin movimiento"),
        "low_motion": (
            5.0 * max(0.0, MOTION_TARGET - video_share) / MOTION_TARGET if expects_motion else 0.0,
            f"{videos} de {n} escenas son video real" if expects_motion else "no se esperaba movimiento real",
        ),
        "long_hold": (_scale(longest, LONG_HOLD_OK, LONG_HOLD_WORST), f"la imagen más larga dura {longest:.1f} s"),
        "slow_cuts": (_scale(average_seconds, SLOW_CUT_OK, SLOW_CUT_WORST), f"{average_seconds:.1f} s por escena en promedio"),
    }
    result = {name: {"score": round(score, 2), "reason": reason} for name, (score, reason) in dimensions.items()}
    average = round(sum(d["score"] for d in result.values()) / len(result), 2)
    return {"average": average, "verdict": _verdict(average), "dimensions": result}
