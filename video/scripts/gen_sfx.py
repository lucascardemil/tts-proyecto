"""Genera los efectos de sonido del edit vertical (video/public/sfx/*.wav).

Son sintetizados desde cero (ruido filtrado + senos con envolvente), así que no
hay problema de derechos. video/public/ está en .gitignore: si se pierde la
carpeta, se regenera con `python video/scripts/gen_sfx.py`.
"""
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent.parent / "public" / "sfx"
rng = np.random.default_rng(7)


def t_axis(dur: float) -> np.ndarray:
    return np.arange(int(SR * dur)) / SR


def lowpass(x: np.ndarray, cutoff: np.ndarray | float) -> np.ndarray:
    """Filtro de un polo con corte variable (cutoff en Hz, escalar o por muestra)."""
    a = 1 - np.exp(-2 * np.pi * np.asarray(cutoff) / SR)
    a = np.broadcast_to(a, x.shape)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += a[i] * (x[i] - acc)
        y[i] = acc
    return y


def noise(dur: float) -> np.ndarray:
    return rng.standard_normal(int(SR * dur))


def save(name: str, x: np.ndarray, peak: float = 0.85) -> None:
    x = x / max(np.max(np.abs(x)), 1e-9) * peak
    fade = min(int(SR * 0.01), len(x))
    x[-fade:] *= np.linspace(1, 0, fade)
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def whoosh(dur: float, rising: bool) -> np.ndarray:
    t = t_axis(dur)
    p = t / dur
    sweep = 300 + 5200 * (p**2 if rising else (1 - p) ** 2)
    env = np.sin(np.pi * p) ** 1.5
    return lowpass(noise(dur), sweep) * env


def boom(dur: float, f0: float = 70.0, body: float = 1.0) -> np.ndarray:
    t = t_axis(dur)
    freq = 38 + (f0 - 38) * np.exp(-t * 14)
    sub = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 4.5)
    thump = lowpass(noise(dur), 900) * np.exp(-t * 20) * 0.9 * body
    click = np.exp(-t * 400) * noise(dur) * 0.6
    return sub + thump + click


def bell(freqs: list[float], dur: float, decay: float = 7.0, start: float = 0.0) -> np.ndarray:
    n = int(SR * dur)
    t = t_axis(dur)
    x = np.zeros(n)
    s = int(start * SR)
    for i, f in enumerate(freqs):
        tt = t[: n - s]
        x[s:] += np.sin(2 * np.pi * f * tt) * np.exp(-tt * (decay + i * 2)) / (1 + i * 0.6)
    return x


# Whoosh de entrada / salida del slow-mo
save("whoosh_in", whoosh(0.45, True))
save("whoosh_out", whoosh(0.4, False), 0.7)

# Impacto de cada kill: golpe grave + ding metálico corto
hit = boom(0.7)
hit[: int(SR * 0.4)] += bell([1320, 1980, 2640], 0.4, decay=14)[: int(SR * 0.4)] * 0.35
save("hit", hit)

# ACE: golpe más largo y profundo + acorde brillante escalonado + cola de aire
dur = 2.2
ace = boom(dur, f0=55, body=1.3) * 1.0
for i, f in enumerate([523.25, 659.25, 783.99, 1046.5]):
    ace += bell([f, f * 2, f * 3], dur, decay=2.4, start=0.06 * i) * 0.35
ace += whoosh(dur, False)[:] * 0.25
save("ace", ace)

# Tics de UI (palabras del hook, contador, CTA)
save("pop", bell([880, 1760], 0.16, decay=30) + np.exp(-t_axis(0.16) * 200) * noise(0.16) * 0.3, 0.6)
save("ding", bell([1568, 2350, 3136], 0.5, decay=9), 0.6)
print("ok ->", OUT)
