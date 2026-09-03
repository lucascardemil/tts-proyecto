"""
Servidor web (Flask) para el sistema de Texto a Voz con Chatterbox
+ generación automática de video con Remotion.
Levanta un servidor local en http://localhost:5000
"""

import os
import json
import shutil
import sys
import time
import uuid
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file, render_template_string

import video_maker
import facebook_publisher
import instagram_publisher
import youtube_publisher
import feedback_analyzer
import job_store
import seo_optimizer
import thumbnail_maker
from tts_engine import (
    text_to_speech_long,
    VOICE_LIBRARY,
    DEFAULT_VOICE,
    BEDTIME_PRESET,
    voice_is_ready,
    OUTPUT_DIR,
)

load_dotenv()

GDRIVE_VIDEOS_DIR = Path(os.environ.get("GDRIVE_VIDEOS_DIR", r"G:\Mi unidad\VIDEOS DE FACEBOOK"))

app = Flask(__name__)


HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Historias para Dormir — TTS + Video</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lexend:wght@500;600;700&family=Inter:wght@400;500;600;700&family=Cinzel:wght@400&family=Playfair+Display:wght@400&family=Poppins:wght@400&family=Montserrat:wght@400&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0b10;
    --bg-glow-1: #1c1440;
    --bg-glow-2: #0d2b3d;
    --surface: rgba(24, 26, 38, 0.72);
    --surface2: rgba(255, 255, 255, 0.045);
    --surface-solid: #171926;
    --border: rgba(255, 255, 255, 0.09);
    --border-strong: rgba(255, 255, 255, 0.16);
    --accent: #7c6cff;
    --accent2: #b48bff;
    --accent-soft: rgba(124, 108, 255, 0.14);
    --text: #edeef5;
    --muted: #9298b3;
    --muted-dim: #6b7089;
    --success: #3ddc9b;
    --error: #ff6b7a;
    --warn: #fbbf24;
    --radius-lg: 18px;
    --radius-md: 12px;
    --shadow-soft: 0 4px 24px rgba(0, 0, 0, 0.28);
    --shadow-lift: 0 10px 32px rgba(124, 108, 255, 0.22);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    background:
      radial-gradient(ellipse 900px 500px at 15% -10%, var(--bg-glow-1), transparent 60%),
      radial-gradient(ellipse 800px 500px at 100% 0%, var(--bg-glow-2), transparent 55%),
      var(--bg);
    background-attachment: fixed;
    color: var(--text);
    font-family: "Inter", -apple-system, "Segoe UI", Roboto, sans-serif;
    padding: 3rem 1.2rem 4.5rem;
    -webkit-font-smoothing: antialiased;
  }
  .container { max-width: 760px; margin: 0 auto; }

  header { text-align: center; margin-bottom: 2.2rem; }
  header .eyebrow {
    display: inline-flex; align-items: center; gap: .4rem;
    font-size: .74rem; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
    color: var(--accent2); background: var(--accent-soft);
    border: 1px solid rgba(124,108,255,.28); padding: .35rem .8rem; border-radius: 999px;
    margin-bottom: 1rem;
  }
  header h1 {
    font-family: "Lexend", "Inter", sans-serif;
    font-size: 2rem; font-weight: 700; letter-spacing: -.01em;
    margin: 0 0 .5rem;
    background: linear-gradient(135deg, #ffffff 30%, #c9c3ff 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  header p { color: var(--muted); font-size: .95rem; margin: 0; }

  .tabs {
    display: flex; gap: .3rem; margin-bottom: 1.6rem;
    background: var(--surface2); border: 1px solid var(--border);
    padding: .3rem; border-radius: 14px;
  }
  .tab-btn {
    flex: 1; padding: .7rem 1rem; border-radius: 10px;
    border: none; background: transparent;
    color: var(--muted); font-size: .92rem; font-weight: 600;
    font-family: inherit; cursor: pointer; transition: all .18s ease;
  }
  .tab-btn:hover { color: var(--text); }
  .tab-btn.active {
    color: #fff; background: linear-gradient(135deg, var(--accent), #6255e6);
    box-shadow: 0 4px 16px rgba(124,108,255,.35);
  }

  .card {
    background: var(--surface);
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 1.6rem;
    margin-bottom: 1.1rem;
    box-shadow: var(--shadow-soft);
    transition: border-color .2s ease;
  }
  .card h2 {
    font-family: "Lexend", "Inter", sans-serif;
    font-size: .96rem; font-weight: 600; margin: 0 0 1rem;
    display: flex; align-items: center; gap: .5rem;
    letter-spacing: -.01em;
  }

  textarea {
    width: 100%; min-height: 170px; padding: 1rem 1.1rem;
    border-radius: var(--radius-md); border: 1px solid var(--border);
    background: rgba(0,0,0,.22); color: var(--text);
    font-size: .98rem; font-family: inherit; line-height: 1.55; resize: vertical;
    transition: border-color .15s ease, box-shadow .15s ease;
  }
  textarea:focus {
    outline: none; border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(124,108,255,.16);
  }
  .char-count { text-align: right; color: var(--muted-dim); font-size: .78rem; margin-top: .5rem; }

  input[type=text], select {
    width: 100%; padding: .78rem 1rem; border-radius: 10px;
    border: 1px solid var(--border); background: rgba(0,0,0,.22);
    color: var(--text); font-size: .95rem; font-family: inherit;
    transition: border-color .15s ease;
  }
  input[type=text]:focus, select:focus { outline: none; border-color: var(--accent); }
  select option { background: var(--surface-solid); }
  label { display: block; font-size: .8rem; color: var(--muted); margin-bottom: .45rem; font-weight: 500; }

  .voice-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
    gap: .7rem;
  }
  .voice-card {
    padding: .95rem 1.05rem; border-radius: var(--radius-md); border: 1px solid var(--border);
    background: rgba(0,0,0,.18); cursor: pointer; transition: all .15s ease;
  }
  .voice-card:hover { border-color: var(--border-strong); background: rgba(255,255,255,.03); }
  .voice-card.active {
    border-color: var(--accent); background: var(--accent-soft);
    box-shadow: 0 0 0 1px var(--accent), var(--shadow-lift);
  }
  .voice-card .name { font-weight: 600; font-size: .9rem; margin-bottom: .25rem; letter-spacing: -.005em; }
  .voice-card .status { font-size: .74rem; color: var(--muted-dim); margin-top: .3rem; }
  .voice-card .gentle-badge {
    display: inline-block; font-size: .68rem; font-weight: 600; background: rgba(251,191,36,.14);
    color: var(--warn); padding: 2px 9px; border-radius: 999px; margin-top: .4rem;
  }

  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 1.1rem; }
  .range-wrap { display: flex; align-items: center; gap: .8rem; }
  input[type=range] {
    flex: 1; accent-color: var(--accent); height: 4px; cursor: pointer;
  }
  .range-val {
    min-width: 44px; text-align: right; font-variant-numeric: tabular-nums;
    color: var(--text); font-size: .85rem; font-weight: 600;
  }

  .btn {
    width: 100%; padding: 1rem; border: none; border-radius: var(--radius-md);
    font-size: .98rem; font-weight: 600; font-family: inherit; cursor: pointer;
    display: flex; align-items: center; justify-content: center; gap: .55rem;
    transition: transform .12s ease, box-shadow .12s ease, opacity .15s ease;
  }
  .btn:active:not(:disabled) { transform: scale(.985); }
  .btn:disabled { opacity: .5; cursor: not-allowed; }
  .btn-primary {
    background: linear-gradient(135deg, var(--accent), #6255e6);
    color: #fff; box-shadow: 0 8px 24px rgba(124,108,255,.32);
  }
  .btn-primary:hover:not(:disabled) { box-shadow: 0 10px 30px rgba(124,108,255,.42); }
  .btn-sm {
    padding: .6rem 1.1rem; border-radius: 10px; border: 1px solid var(--border);
    background: rgba(255,255,255,.04); color: var(--text); cursor: pointer;
    font-size: .84rem; font-weight: 500; font-family: inherit; transition: all .15s ease;
  }
  .btn-sm:hover { border-color: var(--border-strong); background: rgba(255,255,255,.07); }

  #status, #video-status {
    margin-top: 1rem; font-size: .88rem; font-weight: 500;
    display: flex; align-items: center; gap: .55rem;
  }
  #status:empty, #video-status:empty { margin-top: 0; }
  #status.success, #video-status.success { color: var(--success); }
  #status.error, #video-status.error { color: var(--error); }
  #status.loading, #video-status.loading { color: var(--muted); }

  .spinner {
    width: 16px; height: 16px; border: 2px solid var(--border);
    border-top-color: var(--accent); border-radius: 50%;
    animation: spin .7s linear infinite; flex-shrink: 0;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .progress-wrap { display: none; margin-top: 1.1rem; }
  .progress-wrap.visible { display: block; }
  .progress-track {
    width: 100%; height: 8px; border-radius: 999px;
    background: rgba(255,255,255,.07); overflow: hidden;
  }
  .progress-fill {
    height: 100%; width: 0%; border-radius: 999px;
    background: linear-gradient(90deg, var(--accent), var(--accent2));
    box-shadow: 0 0 12px rgba(124,108,255,.5);
    transition: width .35s cubic-bezier(.4,0,.2,1);
  }
  .progress-meta {
    display: flex; justify-content: space-between; align-items: center;
    margin-top: .55rem; font-size: .8rem; color: var(--muted);
  }
  #progress-percent { font-variant-numeric: tabular-nums; font-weight: 600; color: var(--text); }

  #player-wrap { display: none; margin-top: 1.3rem; }
  #player-wrap.visible { display: block; }
  audio { width: 100%; border-radius: 10px; margin-bottom: .8rem; }
  .player-actions { display: flex; gap: .6rem; }

  .image-thumb {
    position: relative; width: 74px; height: 130px; border-radius: 10px;
    overflow: hidden; border: 1px solid var(--border);
  }
  .image-thumb img, .image-thumb video { width: 100%; height: 100%; object-fit: cover; display: block; }
  .image-thumb .thumb-order {
    position: absolute; top: 3px; left: 3px; background: rgba(0,0,0,.7);
    color: #fff; font-size: .68rem; font-weight: 600; padding: 1px 6px; border-radius: 6px;
  }
  .image-thumb .thumb-video-badge {
    position: absolute; bottom: 3px; right: 3px; background: rgba(0,0,0,.7);
    color: #fff; font-size: .7rem; padding: 1px 5px; border-radius: 6px;
  }

  footer { text-align: center; color: var(--muted-dim); font-size: .76rem; margin-top: 2.5rem; }
  code {
    background: rgba(255,255,255,.06); padding: 2px 7px; border-radius: 6px;
    font-size: .85em; color: var(--accent2);
  }
</style>
</head>
<body>
<div class="container">

  <header>
    <span class="eyebrow">✨ Open-source · Chatterbox + Remotion</span>
    <h1>Historias para Dormir</h1>
    <p>Voz clonada por acento con Chatterbox · Video automático con subtítulos</p>
  </header>

  <div class="tabs">
    <button class="tab-btn active" id="tab-btn-audio" data-tab="audio">🔊 Audio</button>
    <button class="tab-btn" id="tab-btn-video" data-tab="video">🎬 Video</button>
    <button class="tab-btn" id="tab-btn-analytics" data-tab="analytics">📊 Analítica</button>
  </div>

  <div id="tab-audio">

    <div class="card">
      <h2>📝 Texto</h2>
      <textarea id="text-input" placeholder="Escribe o pega tu historia aquí..."></textarea>
      <div class="char-count"><span id="char-count">0</span> / 20000 caracteres</div>
    </div>

    <div class="card">
      <h2>🗣️ Voz</h2>
      <div class="voice-grid" id="voice-grid">Cargando...</div>
    </div>

    <div class="card">
      <h2>🎛️ Entrega</h2>
      <label style="display:flex;align-items:center;gap:.5rem;cursor:pointer;margin-bottom:1rem">
        <input type="checkbox" id="bedtime-mode" style="width:auto">
        💤 Modo cuento para dormir (voz calmada y pausada)
      </label>
      <div class="row">
        <div>
          <label>Expresividad</label>
          <div class="range-wrap">
            <input type="range" id="exaggeration" min="0" max="100" step="5" value="50">
            <div class="range-val" id="exaggeration-val">0.50</div>
          </div>
        </div>
        <div>
          <label>Fidelidad a la voz</label>
          <div class="range-wrap">
            <input type="range" id="cfg-weight" min="0" max="100" step="5" value="80">
            <div class="range-val" id="cfg-weight-val">0.80</div>
          </div>
        </div>
      </div>
    </div>

    <button class="btn btn-primary" id="generate-btn">
      <span>🎙️ Generar Audio</span>
    </button>

    <div id="status"></div>

    <div id="progress-wrap" class="progress-wrap">
      <div class="progress-track">
        <div class="progress-fill" id="progress-fill"></div>
      </div>
      <div class="progress-meta">
        <span id="progress-message">Preparando...</span>
        <span id="progress-percent">0%</span>
      </div>
    </div>

    <div id="player-wrap">
      <audio id="audio-player" controls></audio>
      <div class="player-actions">
        <button class="btn-sm" id="download-btn">⬇️ Descargar</button>
        <button class="btn-sm" id="copy-text-btn">📋 Copiar texto</button>
      </div>
    </div>

  </div> <!-- /tab-audio -->

  <div id="tab-video" style="display:none">

    <div class="card">
      <h2>🖼️ Imágenes y clips de video</h2>
      <p style="color:var(--muted);font-size:.85rem;margin-bottom:.8rem">
        Podés mezclar fotos y clips de video cortos (~5s cada uno) — los clips se reproducen
        en su lugar, las fotos llevan el efecto de zoom/paneo. Se ordenan solas: si nombras
        los archivos con números (ej. <code>escena_1.mp4</code>, <code>escena_2.jpg</code>)
        se acomodan automáticamente sin importar el orden en que los subas. Sin números en
        el nombre, se intenta ordenar por la fecha de la foto (EXIF, solo aplica a imágenes);
        si nada de eso está disponible, se respeta el orden de selección.
        Formato vertical 9:16 recomendado.
      </p>
      <input type="file" id="video-images" accept="image/*,video/*" multiple
             style="width:100%;padding:.6rem;border-radius:10px;border:1px dashed var(--border);background:var(--surface2)">
      <div id="video-images-preview" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:1rem"></div>
    </div>

    <div class="card">
      <h2>🔊 Narración</h2>
      <div class="row">
        <div>
          <label for="video-audio-select">Usar un audio ya generado</label>
          <select id="video-audio-select">
            <option value="">— Elegir de la lista —</option>
          </select>
        </div>
        <div>
          <label>...o subir un audio nuevo</label>
          <input type="file" id="video-audio-upload" accept="audio/*"
                 style="width:100%;padding:.5rem;border-radius:10px;border:1px solid var(--border);background:var(--surface2)">
        </div>
      </div>
    </div>

    <div class="card">
      <h2>📐 Formato del video</h2>
      <select id="video-orientation">
        <option value="vertical">Vertical 9:16 (Reels / Shorts / TikTok)</option>
        <option value="horizontal">Horizontal 16:9 (YouTube estándar)</option>
      </select>
    </div>

    <div class="card">
      <h2>🎛️ Opciones de subtítulos</h2>
      <label style="display:flex;align-items:center;gap:.5rem">
        <input type="checkbox" id="video-subtitles-enabled" checked>
        Incluir subtítulos
      </label>
      <div id="video-subtitle-options" style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin-top:.6rem">
        <div>
          <label for="video-subtitle-font">Fuente</label>
          <select id="video-subtitle-font">
            <option value="cinzel">Cinzel</option>
            <option value="playfair">Playfair</option>
            <option value="poppins">Poppins</option>
            <option value="montserrat">Montserrat</option>
          </select>
        </div>
        <div>
          <label for="video-subtitle-size">Tamaño (px)</label>
          <input type="number" id="video-subtitle-size" value="44" min="10" max="120">
        </div>
        <div>
          <label for="video-subtitle-position">Posición</label>
          <select id="video-subtitle-position">
            <option value="bottom">Abajo</option>
            <option value="center">Centro</option>
            <option value="top">Arriba</option>
          </select>
        </div>
        <div style="display:flex;align-items:flex-end">
          <label style="display:flex;align-items:center;gap:.5rem">
            <input type="checkbox" id="video-subtitle-background" checked>
            Fondo detrás del texto
          </label>
        </div>
        <div>
          <label for="video-subtitle-color">Color del texto</label>
          <input type="color" id="video-subtitle-color" value="#ffffff">
        </div>
        <div>
          <label for="video-subtitle-highlight">Color de resaltado</label>
          <input type="color" id="video-subtitle-highlight" value="#ffd98a">
        </div>
      </div>

      <label style="margin-top:.8rem">Preview — arrastrá el texto para posicionarlo</label>
      <div id="video-subtitle-preview" style="position:relative;aspect-ratio:9/16;max-width:220px;overflow:hidden;
           border-radius:10px;background:#111 center/cover no-repeat;margin-top:.4rem;touch-action:none;user-select:none">
        <div id="video-subtitle-sample" style="position:absolute;left:50%;top:85%;transform:translate(-50%,-50%);
             cursor:grab;text-align:center;white-space:nowrap;max-width:90%">
          Así se ven tus <span id="video-subtitle-sample-hl">subtítulos</span>
        </div>
      </div>
    </div>

    <label style="margin-top:.4rem">O elegí un video generado antes</label>
    <select id="video-existing-select">
      <option value="">— O elegí un video generado antes —</option>
    </select>

    <button class="btn btn-primary" id="video-generate-btn" style="margin-top:.8rem">
      <span>🎬 Generar Video</span>
    </button>

    <div id="video-status"></div>

    <div id="video-player-wrap" style="display:none;margin-top:1.2rem">
      <video id="video-player" controls style="width:100%;max-width:380px;border-radius:14px;display:block;margin:0 auto"></video>
      <div class="player-actions" style="justify-content:center;margin-top:.8rem">
        <button class="btn-sm" id="video-download-btn">⬇️ Descargar video</button>
        <button class="btn-sm" id="video-save-drive-btn">☁️ Guardar en Drive</button>
      </div>
      <div id="video-drive-status" style="text-align:center;margin-top:.4rem"></div>

      <div class="card" style="margin-top:1.2rem">
        <h2>📤 Publicar</h2>
        <div class="player-actions" style="justify-content:flex-start;gap:1.2rem">
          <label><input type="checkbox" id="pub-fb" checked> 📘 Facebook</label>
          <label><input type="checkbox" id="pub-ig" checked> 📸 Instagram</label>
          <label><input type="checkbox" id="pub-yt"> ▶️ YouTube</label>
        </div>

        <div id="pub-fb-extra" style="margin-top:.8rem">
          <label>Descripción (Facebook)</label>
          <textarea id="pub-fb-description" rows="3" placeholder="Descripción del video..."></textarea>
          <div class="player-actions" style="margin-top:.4rem;justify-content:flex-start;gap:.6rem">
            <button class="btn-sm" type="button" id="fb-caption-btn">✨ Sugerir caption (Facebook)</button>
          </div>
          <div id="fb-caption-status" style="margin-top:.3rem"></div>
          <label style="margin-top:.6rem"><input type="checkbox" id="fb-auto-time" style="width:auto" checked> ⏰ Publicar en el mejor horario detectado</label>
          <div id="fb-best-time-hint" style="color:var(--muted);font-size:.8rem;margin-top:.2rem"></div>
          <p style="color:var(--muted);font-size:.8rem">
            Se publica al toque, salvo que hayas publicado hace menos de 1 hora — en ese caso se programa
            automáticamente para completar esa hora, sin que tengas que elegir horario.
          </p>
        </div>

        <div id="pub-ig-extra" style="margin-top:.6rem">
          <label>Descripción (Instagram)</label>
          <textarea id="pub-ig-description" rows="3" placeholder="Descripción del video..."></textarea>
          <div class="player-actions" style="margin-top:.4rem;justify-content:flex-start;gap:.6rem">
            <button class="btn-sm" type="button" id="ig-caption-btn">✨ Sugerir caption (Instagram)</button>
          </div>
          <div id="ig-caption-status" style="margin-top:.3rem"></div>
          <label style="margin-top:.6rem"><input type="checkbox" id="ig-auto-time" style="width:auto" checked> ⏰ Publicar en el mejor horario detectado</label>
          <div id="ig-best-time-hint" style="color:var(--muted);font-size:.8rem;margin-top:.2rem"></div>
          <p style="color:var(--muted);font-size:.8rem">
            Se publica como Reel en tu cuenta de Instagram vinculada, marcado como contenido generado con IA.
          </p>
        </div>

        <div id="pub-yt-extra" style="display:none;margin-top:.6rem">
          <div id="yt-connect-wrap">
            <p style="color:var(--muted);font-size:.85rem">Todavía no conectaste tu cuenta de YouTube.</p>
            <button class="btn-sm" id="yt-connect-btn">🔗 Conectar YouTube</button>
            <div id="yt-connect-status"></div>
          </div>
          <div id="yt-privacy-wrap" style="display:none">
            <label>Título en YouTube</label>
            <input type="text" id="yt-title" placeholder="Título del video en YouTube">
            <label style="margin-top:.6rem">Descripción (YouTube)</label>
            <textarea id="pub-yt-description" rows="3" placeholder="Descripción del video..."></textarea>
            <label style="margin-top:.6rem">Etiquetas</label>
            <input type="text" id="yt-tags" placeholder="Etiquetas separadas por coma">
            <div class="player-actions" style="margin-top:.6rem;justify-content:flex-start;gap:.6rem">
              <button class="btn-sm" type="button" id="yt-seo-btn">✨ Sugerir SEO</button>
              <button class="btn-sm" type="button" id="yt-thumb-btn">🖼️ Generar miniatura</button>
              <button class="btn-sm" type="button" id="yt-thumb-variants-btn">🅰️🅱️ Generar variantes</button>
            </div>
            <div id="yt-seo-status" style="margin-top:.3rem"></div>
            <div id="yt-thumb-preview" style="display:none;margin-top:.5rem">
              <img id="yt-thumb-img" style="max-width:220px;border-radius:8px;display:block" alt="Miniatura generada">
            </div>
            <div id="yt-thumb-variants" style="display:none;margin-top:.5rem;gap:.6rem;flex-wrap:wrap" class="player-actions"></div>
            <label style="margin-top:.6rem"><input type="checkbox" id="yt-is-ai" style="width:auto" checked> Marcar como contenido generado con IA</label>
            <label style="margin-top:.6rem">Privacidad en YouTube</label>
            <select id="yt-privacy">
              <option value="public">Público</option>
              <option value="unlisted">No listado</option>
              <option value="private">Privado</option>
            </select>
          </div>
        </div>

        <div style="margin-top:.8rem">
          <label><input type="checkbox" id="pub-schedule" style="width:auto"> 🕒 Programar publicación</label>
          <div id="pub-schedule-wrap" style="display:none;margin-top:.4rem">
            <input type="datetime-local" id="pub-schedule-time">
            <p style="color:var(--muted);font-size:.8rem;margin-top:.3rem">
              Se aplica a Facebook, Instagram y YouTube que estén tildados. Facebook puede además
              retrasarla un poco más si publicaste hace menos de una hora.
            </p>
          </div>
        </div>

        <div class="player-actions" style="margin-top:.8rem">
          <button class="btn btn-primary" id="publish-btn">📤 Publicar</button>
        </div>
        <div id="fb-status"></div>
        <div id="ig-status"></div>
        <div id="yt-status"></div>
      </div>
    </div>

    <div class="card" style="margin-top:1.4rem">
      <h2>ℹ️ Cómo funciona</h2>
      <p style="color:var(--muted);font-size:.85rem;line-height:1.6">
        El video se arma con <strong>Remotion</strong>: cada imagen recibe un efecto Ken Burns (zoom/paneo lento)
        y se conecta con la siguiente mediante una transición suave. Si activas los subtítulos, el audio se
        transcribe automáticamente para generarlos sincronizados palabra por palabra (se reutiliza la
        transcripción si vuelves a generar el video con el mismo audio). Requiere Node.js instalado y
        <code>npm install</code> corrido dentro de la carpeta <code>video/</code>.
      </p>
    </div>

  </div> <!-- /tab-video -->

  <div id="tab-analytics" style="display:none">

    <div class="card">
      <h2>📘 Analítica de Facebook</h2>
      <div class="player-actions" style="justify-content:flex-start">
        <button class="btn-sm" id="fb-analytics-refresh-btn">🔄 Actualizar</button>
      </div>
      <div id="fb-analytics-table" style="margin-top:.6rem;font-size:.85rem"></div>
    </div>

    <div class="card" style="margin-top:1.2rem">
      <h2>📊 Analítica de YouTube</h2>
      <div class="player-actions" style="justify-content:flex-start">
        <button class="btn-sm" id="yt-analytics-refresh-btn">🔄 Actualizar</button>
      </div>
      <div id="yt-analytics-table" style="margin-top:.6rem;font-size:.85rem"></div>
    </div>

    <div class="card" style="margin-top:1.2rem">
      <h2>📸 Analítica de Instagram</h2>
      <div class="player-actions" style="justify-content:flex-start">
        <button class="btn-sm" id="ig-analytics-refresh-btn">🔄 Actualizar</button>
      </div>
      <div id="ig-analytics-table" style="margin-top:.6rem;font-size:.85rem"></div>
    </div>

    <div class="card" style="margin-top:1.2rem">
      <h2>💡 Qué funcionó mejor</h2>
      <div class="player-actions" style="justify-content:flex-start">
        <button class="btn-sm" id="feedback-analytics-refresh-btn">🔄 Actualizar</button>
      </div>
      <div id="feedback-analytics-result" style="margin-top:.6rem;font-size:.85rem"></div>
    </div>

  </div> <!-- /tab-analytics -->

  <footer>
    Chatterbox (MIT) · Remotion · 100% gratuito y open-source
  </footer>
</div>

<script>
const $ = id => document.getElementById(id);
let currentVoice = "{default_voice}";
let currentAudioPath = null;
let currentVideoPath = null;
let selectedImages = [];

// ── Navegación por pestañas ──
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    $("tab-audio").style.display = tab === "audio" ? "" : "none";
    $("tab-video").style.display = tab === "video" ? "" : "none";
    $("tab-analytics").style.display = tab === "analytics" ? "" : "none";
    if (tab === "video") {
      loadAudioOptions();
      loadExistingVideos();
      checkYoutubeConnection();
    }
    if (tab === "analytics") {
      loadFacebookAnalytics();
      loadYoutubeAnalytics();
      loadInstagramAnalytics();
      loadAnalyticsFeedback();
    }
  });
});

// ── Conteo de caracteres ──
const textInput = $("text-input");
const updateCount = () => { $("char-count").textContent = textInput.value.length; };
textInput.addEventListener("input", updateCount);
updateCount();

// ── Cargar voces ──
async function loadVoices() {
  const res = await fetch("/api/voices");
  const data = await res.json();
  const grid = $("voice-grid");
  grid.innerHTML = "";
  for (const [key, info] of Object.entries(data)) {
    const card = document.createElement("div");
    card.className = "voice-card" + (key === currentVoice ? " active" : "");
    card.dataset.voice = key;
    card.innerHTML = `
      <div class="name">${info.label}</div>
      <div class="status">${info.ready ? "✓ Lista" : "↓ Se genera al usar"}</div>
      ${info.gentle ? '<span class="gentle-badge">💤 suave</span>' : ""}
    `;
    card.addEventListener("click", () => {
      document.querySelectorAll(".voice-card").forEach(c => c.classList.remove("active"));
      card.classList.add("active");
      currentVoice = key;
    });
    grid.appendChild(card);
  }
}
loadVoices();

// ── Sliders ──
function slider(id, valId, fmt, onChange) {
  const el = $(id), disp = $(valId);
  el.addEventListener("input", () => {
    disp.textContent = fmt(el.value);
    if (onChange) onChange();
  });
}
slider("exaggeration", "exaggeration-val", v => (v / 100).toFixed(2));
slider("cfg-weight", "cfg-weight-val", v => (v / 100).toFixed(2));

// ── Modo cuento para dormir ──
$("bedtime-mode").addEventListener("change", () => {
  const on = $("bedtime-mode").checked;
  const exaggeration = $("exaggeration"), cfgWeight = $("cfg-weight");
  exaggeration.value = on ? 30 : 50;
  cfgWeight.value = 50;
  exaggeration.disabled = on;
  cfgWeight.disabled = on;
  exaggeration.dispatchEvent(new Event("input"));
  cfgWeight.dispatchEvent(new Event("input"));
});

function showStatus(type, msg) {
  const el = $("status");
  el.className = type;
  el.innerHTML = msg ? `<span>${msg}</span>` : "";
}

function setProgress(pct, msg) {
  $("progress-wrap").classList.add("visible");
  $("progress-fill").style.width = pct + "%";
  $("progress-percent").textContent = pct + "%";
  $("progress-message").textContent = msg;
}

function hideProgress() {
  $("progress-wrap").classList.remove("visible");
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Generar audio (asíncrono, con barra de progreso real) ──
$("generate-btn").addEventListener("click", async () => {
  const text = textInput.value.trim();
  if (!text) { showStatus("error", "⚠️ Escribe algo antes de generar."); return; }

  const btn = $("generate-btn");
  btn.disabled = true;
  showStatus("", "");
  $("player-wrap").classList.remove("visible");
  setProgress(0, "Preparando...");

  const body = {
    text,
    voice: currentVoice,
    exaggeration: parseInt($("exaggeration").value) / 100,
    cfg_weight: parseInt($("cfg-weight").value) / 100,
  };

  try {
    const startRes = await fetch("/api/tts/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const startData = await startRes.json();

    if (!startData.ok) {
      showStatus("error", `❌ ${startData.error || "Error al iniciar la generación."}`);
      hideProgress();
      return;
    }

    const jobId = startData.job_id;

    // Consultar el progreso periódicamente hasta que termine
    while (true) {
      await sleep(700);
      const statusRes = await fetch(`/api/tts/status/${jobId}`);
      const job = await statusRes.json();

      if (!job.ok) {
        showStatus("error", "❌ Se perdió el seguimiento del trabajo.");
        hideProgress();
        break;
      }

      setProgress(job.percent, job.message);

      if (job.status === "done") {
        currentAudioPath = job.filename;
        $("audio-player").src = "/audio/" + job.filename;
        $("player-wrap").classList.add("visible");
        showStatus("success", `✅ Audio generado en ${job.elapsed}s`);
        hideProgress();
        break;
      }
      if (job.status === "error") {
        showStatus("error", `❌ ${job.error || "Error al generar el audio."}`);
        hideProgress();
        break;
      }
    }
  } catch (e) {
    showStatus("error", "❌ Error de conexión con el servidor.");
    hideProgress();
  } finally {
    btn.disabled = false;
  }
});

$("download-btn").addEventListener("click", () => {
  if (!currentAudioPath) return;
  const a = document.createElement("a");
  a.href = "/audio/" + currentAudioPath;
  a.download = currentAudioPath;
  a.click();
});

$("copy-text-btn").addEventListener("click", () => {
  navigator.clipboard.writeText(textInput.value);
  $("copy-text-btn").textContent = "✅ Copiado";
  setTimeout(() => $("copy-text-btn").textContent = "📋 Copiar texto", 1500);
});

// ─────────────────────────────────────────────
// PESTAÑA DE VIDEO
// ─────────────────────────────────────────────

function naturalSortKey(name) {
  return name.replace(/\.[^/.]+$/, "") // quitar extensión
    .split(/(\d+)/)
    .map(part => /^\d+$/.test(part) ? part.padStart(10, "0") : part.toLowerCase());
}

function compareNatural(a, b) {
  const ka = naturalSortKey(a.name), kb = naturalSortKey(b.name);
  for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
    const pa = ka[i] ?? "", pb = kb[i] ?? "";
    if (pa !== pb) return pa < pb ? -1 : 1;
  }
  return 0;
}

$("video-images").addEventListener("change", (e) => {
  const files = Array.from(e.target.files);
  const hasDigits = files.some(f => /\d/.test(f.name.replace(/\.[^/.]+$/, "")));

  if (hasDigits) {
    selectedImages = [...files].sort(compareNatural);
    showVideoStatus("success", "🔢 Escenas reordenadas automáticamente según los números en el nombre del archivo.");
  } else {
    // Sin números en ningún nombre: el servidor intentará ordenar por fecha
    // de captura (EXIF) al generar; aquí se muestran en el orden de selección.
    selectedImages = files;
  }

  const preview = $("video-images-preview");
  preview.innerHTML = "";
  selectedImages.forEach((file, i) => {
    const url = URL.createObjectURL(file);
    const isVideo = file.type.startsWith("video/");
    const div = document.createElement("div");
    div.className = "image-thumb";
    div.innerHTML = isVideo
      ? `<video src="${url}" muted></video><span class="thumb-order">${i + 1}</span><span class="thumb-video-badge">🎞️</span>`
      : `<img src="${url}"><span class="thumb-order">${i + 1}</span>`;
    preview.appendChild(div);
  });

  const firstImg = selectedImages.find(f => !f.type.startsWith("video/"));
  $("video-subtitle-preview").style.backgroundImage = firstImg ? `url(${URL.createObjectURL(firstImg)})` : "none";
});

async function loadAudioOptions() {
  try {
    const res = await fetch("/api/audios");
    const data = await res.json();
    const select = $("video-audio-select");
    select.innerHTML = '<option value="">— Elegir de la lista —</option>';
    data.forEach(f => { select.innerHTML += `<option value="${f.filename}">${f.filename}</option>`; });
  } catch (e) { console.error("No se pudieron cargar los audios existentes", e); }
}

async function loadExistingVideos() {
  try {
    const res = await fetch("/api/videos");
    const files = await res.json();
    const select = $("video-existing-select");
    select.innerHTML = '<option value="">— O elegí un video generado antes —</option>' +
      files.map(f => `<option value="${f.filename}">${f.filename}</option>`).join("");
  } catch (e) { console.error("No se pudieron cargar los videos existentes", e); }
}

function showGeneratedVideo(filename, title) {
  currentVideoPath = filename;
  $("video-player").src = "/video/" + filename;
  $("video-player-wrap").style.display = "block";
  $("pub-fb-description").value = title || "";
  $("pub-ig-description").value = title || "";
  $("pub-yt-description").value = title || "";
  $("fb-status").innerHTML = "";
  $("yt-status").innerHTML = "";
  $("video-drive-status").innerHTML = "";
}

$("video-subtitles-enabled").addEventListener("change", (e) => {
  $("video-subtitle-options").style.opacity = e.target.checked ? "1" : ".4";
  $("video-subtitle-options").querySelectorAll("input,select").forEach(el => el.disabled = !e.target.checked);
});

const SUBTITLE_FONT_MAP = {
  cinzel: "'Cinzel', serif",
  playfair: "'Playfair Display', serif",
  poppins: "'Poppins', sans-serif",
  montserrat: "'Montserrat', sans-serif",
};
const SUBTITLE_PRESET_Y = { bottom: 85, center: 50, top: 15 };

let subtitlePosX = 50;
let subtitlePosY = SUBTITLE_PRESET_Y.bottom;
let subtitleDragged = false;

function positionSubtitleSample() {
  const sample = $("video-subtitle-sample");
  sample.style.left = subtitlePosX + "%";
  sample.style.top = subtitlePosY + "%";
}

function applySubtitlePositionPreset() {
  subtitlePosX = 50;
  subtitlePosY = SUBTITLE_PRESET_Y[$("video-subtitle-position").value] ?? 85;
  subtitleDragged = false;
  positionSubtitleSample();
}

function renderSubtitlePreview() {
  const box = $("video-subtitle-preview");
  const sample = $("video-subtitle-sample");
  const scale = (box.clientWidth || 220) / 1080;
  const size = parseInt($("video-subtitle-size").value, 10) || 44;
  const bg = $("video-subtitle-background").checked;

  sample.style.fontFamily = SUBTITLE_FONT_MAP[$("video-subtitle-font").value];
  sample.style.fontSize = Math.max(8, size * scale) + "px";
  sample.style.color = $("video-subtitle-color").value;
  sample.style.background = bg ? "rgba(0,0,0,.55)" : "transparent";
  sample.style.padding = bg ? `${8 * scale}px ${14 * scale}px` : "0";
  sample.style.borderRadius = bg ? `${6 * scale}px` : "0";
  $("video-subtitle-sample-hl").style.color = $("video-subtitle-highlight").value;
  positionSubtitleSample();
}

$("video-subtitle-position").addEventListener("change", () => {
  applySubtitlePositionPreset();
  renderSubtitlePreview();
});
["video-subtitle-font", "video-subtitle-size", "video-subtitle-color",
 "video-subtitle-highlight", "video-subtitle-background"].forEach(id => {
  $(id).addEventListener("input", renderSubtitlePreview);
  $(id).addEventListener("change", renderSubtitlePreview);
});

(() => {
  const sample = $("video-subtitle-sample");
  const box = $("video-subtitle-preview");
  sample.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    sample.setPointerCapture(e.pointerId);
    sample.style.cursor = "grabbing";

    const onMove = (ev) => {
      const rect = box.getBoundingClientRect();
      subtitlePosX = Math.max(0, Math.min(100, ((ev.clientX - rect.left) / rect.width) * 100));
      subtitlePosY = Math.max(0, Math.min(100, ((ev.clientY - rect.top) / rect.height) * 100));
      subtitleDragged = true;
      positionSubtitleSample();
    };
    const onUp = () => {
      sample.style.cursor = "grab";
      sample.removeEventListener("pointermove", onMove);
      sample.removeEventListener("pointerup", onUp);
    };
    sample.addEventListener("pointermove", onMove);
    sample.addEventListener("pointerup", onUp);
  });
})();

renderSubtitlePreview();

$("video-existing-select").addEventListener("change", () => {
  const filename = $("video-existing-select").value;
  if (!filename) return;
  showGeneratedVideo(filename, "");
});

function showVideoStatus(type, msg) {
  const el = $("video-status");
  el.className = type;
  el.innerHTML = type === "loading" ? `<div class="spinner"></div><span>${msg}</span>` : `<span>${msg}</span>`;
}

$("video-orientation").addEventListener("change", () => {
  const horizontal = $("video-orientation").value === "horizontal";
  const preview = $("video-subtitle-preview");
  preview.style.aspectRatio = horizontal ? "16/9" : "9/16";
  preview.style.maxWidth = horizontal ? "340px" : "220px";
});

$("video-generate-btn").addEventListener("click", async () => {
  const title = "";
  const audioFile = $("video-audio-upload").files[0];
  const audioChoice = $("video-audio-select").value;

  if (selectedImages.length === 0) { showVideoStatus("error", "⚠️ Sube al menos una imagen o clip de video."); return; }
  if (!audioFile && !audioChoice) { showVideoStatus("error", "⚠️ Elige un audio ya generado o sube uno nuevo."); return; }

  const btn = $("video-generate-btn");
  btn.disabled = true;
  showVideoStatus("loading", "Preparando...");
  $("video-player-wrap").style.display = "none";

  const formData = new FormData();
  formData.append("title", title);
  formData.append("orientation", $("video-orientation").value);
  selectedImages.forEach(file => formData.append("images", file));
  if (audioFile) { formData.append("audio_file", audioFile); } else { formData.append("audio_choice", audioChoice); }

  const subtitlesEnabled = $("video-subtitles-enabled").checked;
  formData.append("subtitles_enabled", subtitlesEnabled ? "1" : "0");
  if (subtitlesEnabled) {
    formData.append("subtitle_font", $("video-subtitle-font").value);
    formData.append("subtitle_size", $("video-subtitle-size").value);
    formData.append("subtitle_position", $("video-subtitle-position").value);
    if (subtitleDragged) {
      formData.append("subtitle_x", subtitlePosX.toFixed(1));
      formData.append("subtitle_y", subtitlePosY.toFixed(1));
    }
    formData.append("subtitle_color", $("video-subtitle-color").value);
    formData.append("subtitle_highlight", $("video-subtitle-highlight").value);
    formData.append("subtitle_background", $("video-subtitle-background").checked ? "1" : "0");
  }

  try {
    const startRes = await fetch("/api/video/start", { method: "POST", body: formData });
    const startData = await startRes.json();

    if (!startData.ok) {
      showVideoStatus("error", `❌ ${startData.error || "Error al iniciar la generación."}`);
      return;
    }

    const jobId = startData.job_id;

    // Consultar el progreso periódicamente hasta que termine (puede tardar varios minutos)
    while (true) {
      await sleep(1000);
      const statusRes = await fetch(`/api/video/status/${jobId}`);
      const job = await statusRes.json();

      if (!job.ok) {
        showVideoStatus("error", "❌ Se perdió el seguimiento del trabajo.");
        break;
      }

      if (job.status === "running") {
        showVideoStatus("loading", job.message || "Generando video...");
      } else if (job.status === "done") {
        if (job.video_name) {
          showGeneratedVideo(job.video_name, title);
          showVideoStatus("success", "✅ Video renderizado.");
        } else {
          showVideoStatus("success", "✅ Edición lista.");
        }
        break;
      } else if (job.status === "error") {
        showVideoStatus("error", `❌ ${job.error || "Error al generar el video."}`);
        break;
      }
    }
  } catch (e) {
    showVideoStatus("error", "❌ Error de conexión con el servidor.");
  } finally {
    btn.disabled = false;
  }
});

$("video-download-btn").addEventListener("click", () => {
  if (!currentVideoPath) return;
  const a = document.createElement("a");
  a.href = "/video/" + currentVideoPath;
  a.download = currentVideoPath;
  a.click();
});

$("video-save-drive-btn").addEventListener("click", async () => {
  if (!currentVideoPath) return;
  const btn = $("video-save-drive-btn");
  const status = $("video-drive-status");
  btn.disabled = true;
  status.className = "loading";
  status.innerHTML = '<div class="spinner"></div><span>Guardando en Drive...</span>';
  try {
    const res = await fetch("/api/video/save-drive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: currentVideoPath }),
    });
    const data = await res.json();
    status.className = data.ok ? "success" : "error";
    status.innerHTML = data.ok ? "✅ Guardado en Google Drive" : `❌ ${data.error || "Error al guardar en Drive."}`;
  } catch (e) {
    status.className = "error";
    status.innerHTML = "❌ Error de conexión con el servidor.";
  } finally {
    btn.disabled = false;
  }
});

// ── Selector de destinos de publicación ──
function updatePublishExtras() {
  $("pub-fb-extra").style.display = $("pub-fb").checked ? "block" : "none";
  $("pub-ig-extra").style.display = $("pub-ig").checked ? "block" : "none";
  $("pub-yt-extra").style.display = $("pub-yt").checked ? "block" : "none";
  $("fb-status").style.display = $("pub-fb").checked ? "block" : "none";
  $("ig-status").style.display = $("pub-ig").checked ? "block" : "none";
  $("yt-status").style.display = $("pub-yt").checked ? "block" : "none";
}
$("pub-fb").addEventListener("change", updatePublishExtras);
$("pub-ig").addEventListener("change", updatePublishExtras);
$("pub-yt").addEventListener("change", () => { updatePublishExtras(); checkYoutubeConnection(); });
updatePublishExtras();

$("pub-schedule").addEventListener("change", () => {
  $("pub-schedule-wrap").style.display = $("pub-schedule").checked ? "block" : "none";
});

$("fb-auto-time").addEventListener("change", () => {
  if ($("fb-auto-time").checked) loadBestTimeHint("fb-best-time-hint", "/api/facebook/best-time");
  else $("fb-best-time-hint").textContent = "";
});
$("ig-auto-time").addEventListener("change", () => {
  if ($("ig-auto-time").checked) loadBestTimeHint("ig-best-time-hint", "/api/instagram/best-time");
  else $("ig-best-time-hint").textContent = "";
});
if ($("fb-auto-time").checked) loadBestTimeHint("fb-best-time-hint", "/api/facebook/best-time");
if ($("ig-auto-time").checked) loadBestTimeHint("ig-best-time-hint", "/api/instagram/best-time");

function getScheduledTimeIso() {
  if (!$("pub-schedule").checked) return null;
  const val = $("pub-schedule-time").value;
  return val || null;
}

function _nextDatetimeLocalAtHour(hour) {
  const now = new Date();
  const target = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hour, 0, 0);
  if (target <= now) target.setDate(target.getDate() + 1);
  const pad = (n) => String(n).padStart(2, "0");
  return `${target.getFullYear()}-${pad(target.getMonth() + 1)}-${pad(target.getDate())}T${pad(target.getHours())}:${pad(target.getMinutes())}`;
}

async function _fetchBestTime(url) {
  try {
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok || data.insufficient_data) return null;
    return data;
  } catch (e) {
    return null;
  }
}

async function loadBestTimeHint(hintId, url) {
  const el = $(hintId);
  el.textContent = "Analizando publicaciones anteriores...";
  const data = await _fetchBestTime(url);
  if (!data) {
    el.textContent = "Todavía no hay suficientes datos — se publica ahora.";
    return;
  }
  el.textContent = `Mejor franja detectada: ${data.best_daypart} (muestra: ${data.sample_size} publicaciones).`;
}

async function getAutoOrScheduledTimeIso(autoCheckboxId, bestTimeUrl) {
  if ($(autoCheckboxId).checked) {
    const data = await _fetchBestTime(bestTimeUrl);
    return data ? _nextDatetimeLocalAtHour(data.best_hour) : null;
  }
  return getScheduledTimeIso();
}

function showFbStatus(type, msg) {
  const el = $("fb-status");
  el.className = type;
  if (type === "loading") {
    el.innerHTML = `<div class="spinner"></div><span>${msg}</span>`;
  } else if (type === "error") {
    el.innerHTML = `<span>${msg}</span> <button class="btn-sm" id="fb-retry-btn">🔄 Reintentar</button>`;
    $("fb-retry-btn").addEventListener("click", () => publishToFacebook());
  } else {
    el.innerHTML = `<span>${msg}</span>`;
  }
}

function showIgStatus(type, msg) {
  const el = $("ig-status");
  el.className = type;
  if (type === "loading") {
    el.innerHTML = `<div class="spinner"></div><span>${msg}</span>`;
  } else if (type === "error") {
    el.innerHTML = `<span>${msg}</span> <button class="btn-sm" id="ig-retry-btn">🔄 Reintentar</button>`;
    $("ig-retry-btn").addEventListener("click", () => publishToInstagram());
  } else {
    el.innerHTML = `<span>${msg}</span>`;
  }
}

async function pollFacebookJob(jobId) {
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const res = await fetch("/api/facebook/status/" + jobId);
    const data = await res.json();
    if (!data.ok) { showFbStatus("error", "❌ " + (data.error || "Error al consultar el estado.")); return; }
    if (data.status === "scheduled") {
      const when = new Date(data.scheduled_for).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
      showFbStatus("loading", `Facebook programado para ${when}...`);
      continue;
    }
    if (data.status === "running" && data.stage) {
      showFbStatus("loading", data.stage);
      continue;
    }
    if (data.status === "done") {
      let msg;
      if (data.scheduled_time) {
        const when = new Date(data.scheduled_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        msg = `🕒 Facebook programado para las ${when} (para no publicar dos veces seguidas)`;
      } else {
        msg = `✅ Facebook (id: ${data.video_id})`;
      }
      showFbStatus("success", msg);
      return;
    }
    if (data.status === "error") {
      showFbStatus("error", "❌ Facebook: " + data.error);
      return;
    }
  }
}

async function pollInstagramJob(jobId) {
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const res = await fetch("/api/instagram/status/" + jobId);
    const data = await res.json();
    if (!data.ok) { showIgStatus("error", "❌ " + (data.error || "Error al consultar el estado.")); return; }
    if (data.status === "scheduled") {
      const when = new Date(data.scheduled_for).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
      showIgStatus("loading", `Instagram programado para ${when}...`);
      continue;
    }
    if (data.status === "running" && data.stage) {
      showIgStatus("loading", data.stage);
      continue;
    }
    if (data.status === "done") {
      showIgStatus("success", `✅ Instagram (id: ${data.media_id})`);
      return;
    }
    if (data.status === "error") {
      showIgStatus("error", "❌ Instagram: " + data.error);
      return;
    }
  }
}

async function publishToFacebook(force) {
  if (!currentVideoPath) return;
  showFbStatus("loading", "Publicando en Facebook...");
  const scheduledTime = await getAutoOrScheduledTimeIso("fb-auto-time", "/api/facebook/best-time");
  showFbStatus("loading", scheduledTime ? "Programando en Facebook..." : "Publicando en Facebook...");
  try {
    const res = await fetch("/api/facebook/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: currentVideoPath,
        title: "",
        description: $("pub-fb-description").value.trim(),
        scheduled_time: scheduledTime,
        force: !!force,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      await pollFacebookJob(data.job_id);
    } else if (data.duplicate && confirm(data.error + " ¿Publicar de todas formas?")) {
      return publishToFacebook(true);
    } else {
      showFbStatus("error", "❌ " + (data.error || "Error al publicar."));
    }
  } catch (e) {
    showFbStatus("error", "❌ Error de conexión con el servidor.");
  }
}

async function publishToInstagram(force) {
  if (!currentVideoPath) return;
  showIgStatus("loading", "Publicando en Instagram...");
  const scheduledTime = await getAutoOrScheduledTimeIso("ig-auto-time", "/api/instagram/best-time");
  showIgStatus("loading", scheduledTime ? "Programando en Instagram..." : "Publicando en Instagram...");
  try {
    const res = await fetch("/api/instagram/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: currentVideoPath,
        title: "",
        description: $("pub-ig-description").value.trim(),
        scheduled_time: scheduledTime,
        force: !!force,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      await pollInstagramJob(data.job_id);
    } else if (data.duplicate && confirm(data.error + " ¿Publicar de todas formas?")) {
      return publishToInstagram(true);
    } else {
      showIgStatus("error", "❌ " + (data.error || "Error al publicar."));
    }
  } catch (e) {
    showIgStatus("error", "❌ Error de conexión con el servidor.");
  }
}

async function checkYoutubeConnection() {
  try {
    const res = await fetch("/api/youtube/connected");
    const data = await res.json();
    $("yt-connect-wrap").style.display = data.connected ? "none" : "block";
    $("yt-privacy-wrap").style.display = data.connected ? "block" : "none";
  } catch (e) { console.error("No se pudo consultar la conexión con YouTube", e); }
}

$("yt-connect-btn").addEventListener("click", async () => {
  const btn = $("yt-connect-btn");
  const status = $("yt-connect-status");
  btn.disabled = true;
  status.className = "loading";
  status.innerHTML = '<div class="spinner"></div><span>Abriendo el navegador para conectar tu cuenta de Google...</span>';
  try {
    const res = await fetch("/api/youtube/connect", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      status.innerHTML = "";
      checkYoutubeConnection();
    } else {
      status.className = "error";
      status.innerHTML = `❌ ${data.error || "No se pudo conectar."}`;
    }
  } catch (e) {
    status.className = "error";
    status.innerHTML = "❌ Error de conexión con el servidor.";
  } finally {
    btn.disabled = false;
  }
});

function showYtStatus(type, msg) {
  const el = $("yt-status");
  el.className = type;
  if (type === "loading") {
    el.innerHTML = `<div class="spinner"></div><span>${msg}</span>`;
  } else if (type === "error") {
    el.innerHTML = `<span>${msg}</span> <button class="btn-sm" id="yt-retry-btn">🔄 Reintentar</button>`;
    $("yt-retry-btn").addEventListener("click", () => publishToYoutube());
  } else {
    el.innerHTML = `<span>${msg}</span>`;
  }
}

async function pollYoutubeJob(jobId) {
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const res = await fetch("/api/youtube/publish/status/" + jobId);
    const data = await res.json();
    if (!data.ok) { showYtStatus("error", "❌ " + (data.error || "Error al consultar el estado.")); return; }
    if (data.status === "scheduled") {
      const when = new Date(data.scheduled_for).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
      showYtStatus("loading", `YouTube programado para ${when}...`);
      continue;
    }
    if (data.status === "running" && data.stage) {
      showYtStatus("loading", data.stage);
      continue;
    }
    if (data.status === "done") {
      showYtStatus("success", `✅ Publicado en YouTube (id: ${data.video_id})`);
      generatedThumbnail = null;
      loadYoutubeAnalytics();
      return;
    }
    if (data.status === "error") {
      showYtStatus("error", "❌ " + data.error);
      return;
    }
  }
}

async function publishToYoutube(force) {
  if (!currentVideoPath) return;
  if ($("yt-connect-wrap").style.display !== "none") {
    showYtStatus("error", "❌ Conectá tu cuenta de YouTube primero.");
    return;
  }
  const scheduledTime = getScheduledTimeIso();
  showYtStatus("loading", scheduledTime ? "Programando en YouTube..." : "Publicando en YouTube...");
  try {
    const res = await fetch("/api/youtube/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: currentVideoPath,
        title: $("yt-title").value.trim(),
        description: $("pub-yt-description").value.trim(),
        privacy_status: $("yt-privacy").value,
        tags: $("yt-tags").value.trim(),
        is_ai_generated: $("yt-is-ai").checked,
        scheduled_time: scheduledTime,
        thumbnail: generatedThumbnail,
        force: !!force,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      await pollYoutubeJob(data.job_id);
    } else if (data.duplicate && confirm(data.error + " ¿Publicar de todas formas?")) {
      return publishToYoutube(true);
    } else {
      showYtStatus("error", "❌ " + (data.error || "Error al publicar."));
    }
  } catch (e) {
    showYtStatus("error", "❌ Error de conexión con el servidor.");
  }
}

let generatedThumbnail = null;

$("yt-seo-btn").addEventListener("click", async () => {
  const text = $("text-input").value.trim();
  if (!text) {
    $("yt-seo-status").textContent = "❌ Escribí o pegá el guion primero.";
    return;
  }
  $("yt-seo-status").textContent = "✨ Generando sugerencia SEO...";
  try {
    const res = await fetch("/api/seo/suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, title: $("yt-title").value.trim() }),
    });
    const data = await res.json();
    if (!data.ok) {
      $("yt-seo-status").textContent = "❌ " + (data.error || "No se pudo generar la sugerencia.");
      return;
    }
    $("yt-title").value = data.title;
    $("pub-yt-description").value = data.description;
    $("yt-tags").value = data.tags.join(", ");
    $("yt-seo-status").textContent = `✅ SEO sugerido (puntaje ${data.score}/100)`;
  } catch (e) {
    $("yt-seo-status").textContent = "❌ Error de conexión con el servidor.";
  }
});

function makeSocialCaptionHandler(descId, statusId) {
  return async () => {
    const text = $("text-input").value.trim();
    const status = $(statusId);
    if (!text) {
      status.textContent = "❌ Escribí o pegá el guion primero.";
      return;
    }
    status.textContent = "✨ Generando caption...";
    try {
      const res = await fetch("/api/seo/suggest-social", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      if (!data.ok) {
        status.textContent = "❌ " + (data.error || "No se pudo generar el caption.");
        return;
      }
      $(descId).value = data.caption + "\n\n" + data.hashtags.join(" ");
      status.textContent = "✅ Caption sugerido.";
    } catch (e) {
      status.textContent = "❌ Error de conexión con el servidor.";
    }
  };
}
$("fb-caption-btn").addEventListener("click", makeSocialCaptionHandler("pub-fb-description", "fb-caption-status"));
$("ig-caption-btn").addEventListener("click", makeSocialCaptionHandler("pub-ig-description", "ig-caption-status"));

$("yt-thumb-btn").addEventListener("click", async () => {
  $("yt-seo-status").textContent = "🖼️ Generando miniatura...";
  try {
    const res = await fetch("/api/thumbnail/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: $("yt-title").value.trim() }),
    });
    const data = await res.json();
    if (!data.ok) {
      $("yt-seo-status").textContent = "❌ " + (data.error || "No se pudo generar la miniatura.");
      return;
    }
    generatedThumbnail = data.filename;
    $("yt-thumb-img").src = data.url + "?t=" + Date.now();
    $("yt-thumb-preview").style.display = "block";
    $("yt-thumb-variants").style.display = "none";
    $("yt-seo-status").textContent = "✅ Miniatura lista — se subirá junto con el video.";
  } catch (e) {
    $("yt-seo-status").textContent = "❌ Error de conexión con el servidor.";
  }
});

$("yt-thumb-variants-btn").addEventListener("click", async () => {
  $("yt-seo-status").textContent = "🅰️🅱️ Generando variantes de miniatura...";
  try {
    const res = await fetch("/api/thumbnail/variants", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: $("yt-title").value.trim() }),
    });
    const data = await res.json();
    if (!data.ok) {
      $("yt-seo-status").textContent = "❌ " + (data.error || "No se pudieron generar las variantes.");
      return;
    }
    const wrap = $("yt-thumb-variants");
    wrap.innerHTML = data.variants.map((v, i) => `
      <div style="text-align:center">
        <img src="${v.url}?t=${Date.now()}" data-filename="${v.filename}"
             style="width:160px;border-radius:8px;cursor:pointer;border:3px solid transparent"
             class="yt-thumb-variant-img" alt="Variante ${i + 1}">
        <div style="font-size:.75rem;color:var(--muted)">Variante ${i + 1}</div>
      </div>`).join("");
    wrap.style.display = "flex";
    $("yt-thumb-preview").style.display = "none";
    wrap.querySelectorAll(".yt-thumb-variant-img").forEach(img => {
      img.addEventListener("click", () => {
        wrap.querySelectorAll(".yt-thumb-variant-img").forEach(i => i.style.borderColor = "transparent");
        img.style.borderColor = "var(--accent, #6366f1)";
        generatedThumbnail = img.dataset.filename;
        $("yt-seo-status").textContent = "✅ Miniatura elegida — se subirá junto con el video.";
      });
    });
    $("yt-seo-status").textContent = "Elegí la miniatura que más te guste.";
  } catch (e) {
    $("yt-seo-status").textContent = "❌ Error de conexión con el servidor.";
  }
});

async function _loadAnalytics(tableId, url, emptyMsg) {
  const wrap = $(tableId);
  wrap.textContent = "Cargando...";
  try {
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok || !data.videos.length) {
      wrap.textContent = emptyMsg;
      return;
    }
    const rows = data.videos.map(v => `
      <tr>
        <td style="padding:.3rem">${v.title || v.video_id || v.media_id}</td>
        <td style="padding:.3rem">${v.views ?? "-"}</td>
        <td style="padding:.3rem">${v.likes ?? "-"}</td>
        <td style="padding:.3rem">${v.comments ?? "-"}</td>
      </tr>`).join("");
    wrap.innerHTML = `
      <table style="width:100%;border-collapse:collapse">
        <thead><tr style="text-align:left;color:var(--muted)">
          <th style="padding:.3rem">Título</th><th>Vistas</th><th>Likes</th><th>Comentarios</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (e) {
    wrap.textContent = "❌ Error de conexión con el servidor.";
  }
}
function loadYoutubeAnalytics() {
  return _loadAnalytics("yt-analytics-table", "/api/youtube/analytics", "Todavía no hay videos publicados en YouTube.");
}
function loadFacebookAnalytics() {
  return _loadAnalytics("fb-analytics-table", "/api/facebook/analytics", "Todavía no hay videos publicados en Facebook.");
}
function loadInstagramAnalytics() {
  return _loadAnalytics("ig-analytics-table", "/api/instagram/analytics", "Todavía no hay videos publicados en Instagram.");
}
$("yt-analytics-refresh-btn").addEventListener("click", loadYoutubeAnalytics);
$("fb-analytics-refresh-btn").addEventListener("click", loadFacebookAnalytics);
$("ig-analytics-refresh-btn").addEventListener("click", loadInstagramAnalytics);

async function loadAnalyticsFeedback() {
  const el = $("feedback-analytics-result");
  el.textContent = "Cargando...";
  try {
    const res = await fetch("/api/analytics/feedback");
    const data = await res.json();
    if (!data.ok) {
      el.textContent = "❌ " + (data.error || "No se pudo cargar.");
      return;
    }
    if (data.insufficient_data) {
      el.textContent = `Todavía no hay suficientes videos publicados con estadísticas (${data.sample_size}/3 mínimo).`;
      return;
    }
    el.innerHTML = `
      <p>📦 Muestra: ${data.sample_size} videos — 👁️ Promedio de vistas: ${data.avg_views}</p>
      <p>📏 Mejor largo de título: ${data.best_title_length}</p>
      <p>🔑 Palabras clave más frecuentes en los videos con más vistas: ${data.top_keywords.join(", ") || "—"}</p>
    `;
  } catch (e) {
    el.textContent = "❌ Error de conexión con el servidor.";
  }
}
$("feedback-analytics-refresh-btn").addEventListener("click", loadAnalyticsFeedback);

$("publish-btn").addEventListener("click", async () => {
  if (!currentVideoPath) return;
  const targets = [];
  if ($("pub-fb").checked) targets.push(publishToFacebook());
  if ($("pub-ig").checked) targets.push(publishToInstagram());
  if ($("pub-yt").checked) targets.push(publishToYoutube());
  if (!targets.length) return;

  const btn = $("publish-btn");
  btn.disabled = true;
  await Promise.allSettled(targets);
  btn.disabled = false;
});
</script>
</body>
</html>"""


# ─────────────────────────────────────────────
# RUTAS
# ─────────────────────────────────────────────

@app.route("/")
def index():
    html = HTML.replace("{default_voice}", DEFAULT_VOICE)
    return render_template_string(html)


@app.route("/api/voices")
def api_voices():
    result = {}
    for key, info in VOICE_LIBRARY.items():
        result[key] = {
            "label": info["label"],
            "gentle": info["gentle"],
            "ready": voice_is_ready(key),
        }
    return jsonify(result)


# ─────────────────────────────────────────────
# TRABAJOS DE AUDIO EN SEGUNDO PLANO (para la barra de progreso)
# ─────────────────────────────────────────────

_tts_jobs = {}  # job_id -> {status, percent, message, filename, error, started_at}
_tts_jobs_lock = threading.Lock()


def _run_tts_job(job_id: str, text: str, kwargs: dict):
    def on_progress(pct, msg):
        with _tts_jobs_lock:
            _tts_jobs[job_id].update(percent=pct, message=msg)

    try:
        output_path = text_to_speech_long(text, on_progress=on_progress, **kwargs)
    except Exception as e:
        with _tts_jobs_lock:
            _tts_jobs[job_id].update(status="error", error=str(e), percent=100)
            job_store.save("tts", _tts_jobs)
        return

    with _tts_jobs_lock:
        if output_path:
            _tts_jobs[job_id].update(
                status="done", percent=100, message="Listo", filename=Path(output_path).name
            )
        else:
            _tts_jobs[job_id].update(
                status="error", percent=100, error="Error al generar el audio. Revisa los logs del servidor."
            )
        job_store.save("tts", _tts_jobs)


@app.route("/api/tts/start", methods=["POST"])
def api_tts_start():
    data = request.get_json(force=True)
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"ok": False, "error": "El texto está vacío."}), 400
    if len(text) > 20000:
        return jsonify({"ok": False, "error": "El texto supera los 20000 caracteres."}), 400

    kwargs = {
        "voice": data.get("voice", DEFAULT_VOICE),
        "exaggeration": float(data.get("exaggeration", BEDTIME_PRESET["exaggeration"])),
        "cfg_weight": float(data.get("cfg_weight", BEDTIME_PRESET["cfg_weight"])),
    }

    job_id = uuid.uuid4().hex
    with _tts_jobs_lock:
        _tts_jobs[job_id] = {
            "status": "running",
            "percent": 0,
            "message": "Preparando...",
            "filename": None,
            "error": None,
            "started_at": time.time(),
        }
        job_store.save("tts", _tts_jobs)

    thread = threading.Thread(target=_run_tts_job, args=(job_id, text, kwargs), daemon=True)
    thread.start()

    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/tts/status/<job_id>")
def api_tts_status(job_id):
    with _tts_jobs_lock:
        job = _tts_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "Trabajo no encontrado."}), 404
        response = dict(job)

    elapsed = round(time.time() - response.pop("started_at"), 1)
    response["ok"] = True
    response["elapsed"] = elapsed
    return jsonify(response)


@app.route("/audio/<filename>")
def serve_audio(filename):
    filepath = OUTPUT_DIR / filename
    if not filepath.exists():
        return "Archivo no encontrado", 404
    return send_file(str(filepath))


@app.route("/api/audios")
def api_audios():
    """Lista los audios ya generados en output/ (mp3 y wav), más recientes primero."""
    files = [f for f in OUTPUT_DIR.glob("*") if f.suffix.lower() in (".mp3", ".wav")]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return jsonify([{"filename": f.name} for f in files])


_video_jobs = {}  # job_id -> {status, message, filename, error, started_at}
_video_jobs_lock = threading.Lock()
# video_maker usa una sola carpeta compartida (video/public) y un solo
# props.json para armar cada render — si dos videos se generaran en paralelo
# se pisarían los assets entre sí. Este lock serializa los renders para que
# cada uno termine antes de que empiece el siguiente.
_video_render_lock = threading.Lock()


def _run_video_job(job_id: str, image_paths: list, audio_path: str, title: str,
                    subtitles_enabled: bool, subtitle_style: Optional[dict], orientation: str, cleanup):
    def on_progress(msg):
        with _video_jobs_lock:
            _video_jobs[job_id]["message"] = msg

    if _video_render_lock.locked():
        with _video_jobs_lock:
            _video_jobs[job_id]["message"] = "En cola: esperando a que termine otra edición que se está preparando..."

    try:
        with _video_render_lock:
            timeline = video_maker.build_props(
                image_paths=image_paths,
                audio_path=audio_path,
                title=title,
                subtitles_enabled=subtitles_enabled,
                subtitle_style=subtitle_style,
                on_progress=on_progress,
            )
            video_path = None
            if timeline:
                video_path = video_maker.render_props(
                    video_maker.VIDEO_DIR / "props.json",
                    orientation=orientation,
                    on_progress=on_progress,
                )
    except Exception as e:
        with _video_jobs_lock:
            _video_jobs[job_id].update(status="error", error=str(e))
            job_store.save("video", _video_jobs)
        cleanup()
        return

    with _video_jobs_lock:
        if timeline and video_path:
            _video_jobs[job_id].update(status="done", video_path=video_path,
                                        video_name=Path(video_path).name)
        else:
            _video_jobs[job_id].update(
                status="error", error="Error al preparar o renderizar la edición. Revisa los logs del servidor."
            )
        job_store.save("video", _video_jobs)
    cleanup()


@app.route("/api/video/start", methods=["POST"])
def api_video_start():
    import tempfile
    from werkzeug.utils import secure_filename

    title = request.form.get("title", "").strip()

    image_files = request.files.getlist("images")
    if not image_files:
        return jsonify({"ok": False, "error": "Sube al menos una imagen o clip de video."}), 400

    audio_choice = request.form.get("audio_choice", "").strip()
    audio_file = request.files.get("audio_file")

    tmp_dir = tempfile.mkdtemp(prefix="tts_video_")
    tmp_path = Path(tmp_dir)

    def cleanup():
        shutil.rmtree(tmp_dir, ignore_errors=True)

    image_paths = []
    for i, f in enumerate(image_files):
        name = secure_filename(f.filename) or f"img_{i}.jpg"
        # Cada imagen en su propia subcarpeta: así el nombre de archivo
        # original queda intacto (sin prefijo de índice) y video_maker
        # puede detectar números/orden reales en el nombre que subió el usuario.
        img_dir = tmp_path / f"img{i}"
        img_dir.mkdir()
        dest = img_dir / name
        f.save(dest)
        image_paths.append(str(dest))

    if audio_file and audio_file.filename:
        audio_name = secure_filename(audio_file.filename) or "audio.mp3"
        audio_path = tmp_path / audio_name
        audio_file.save(audio_path)
    elif audio_choice:
        audio_path = OUTPUT_DIR / audio_choice
        if not audio_path.exists():
            cleanup()
            return jsonify({"ok": False, "error": f"No se encontró el audio '{audio_choice}'."}), 400
    else:
        cleanup()
        return jsonify({"ok": False, "error": "Elige un audio ya generado o sube uno nuevo."}), 400

    subtitles_enabled = request.form.get("subtitles_enabled", "1").strip() not in ("0", "false", "")
    style_overrides = {
        "fontFamily": request.form.get("subtitle_font") or None,
        "fontSize": int(request.form["subtitle_size"]) if request.form.get("subtitle_size") else None,
        "position": request.form.get("subtitle_position") or None,
        "positionX": float(request.form["subtitle_x"]) if request.form.get("subtitle_x") else None,
        "positionY": float(request.form["subtitle_y"]) if request.form.get("subtitle_y") else None,
        "textColor": request.form.get("subtitle_color") or None,
        "highlightColor": request.form.get("subtitle_highlight") or None,
        "background": False if request.form.get("subtitle_background") == "0" else None,
    }
    subtitle_style = {k: v for k, v in style_overrides.items() if v is not None} or None
    orientation = request.form.get("orientation", "vertical").strip()
    if orientation not in ("vertical", "horizontal"):
        orientation = "vertical"

    job_id = uuid.uuid4().hex
    with _video_jobs_lock:
        _video_jobs[job_id] = {
            "status": "running",
            "message": "Preparando...",
            "error": None,
            "started_at": time.time(),
        }
        job_store.save("video", _video_jobs)

    thread = threading.Thread(
        target=_run_video_job,
        args=(job_id, image_paths, str(audio_path), title, subtitles_enabled, subtitle_style, orientation, cleanup),
        daemon=True,
    )
    thread.start()

    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/video/status/<job_id>")
def api_video_status(job_id):
    with _video_jobs_lock:
        job = _video_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "Trabajo no encontrado."}), 404
        response = dict(job)

    elapsed = round(time.time() - response.pop("started_at"), 1)
    response["ok"] = True
    response["elapsed"] = elapsed
    return jsonify(response)


@app.route("/video/<filename>")
def serve_video(filename):
    filepath = video_maker.VIDEO_OUT_DIR / filename
    if not filepath.exists():
        return "Archivo no encontrado", 404
    return send_file(str(filepath))


@app.route("/api/videos")
def api_videos():
    """Lista los videos ya generados en video/out/, más recientes primero."""
    files = list(video_maker.VIDEO_OUT_DIR.glob("*.mp4"))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return jsonify([{"filename": f.name} for f in files])


@app.route("/api/video/save-drive", methods=["POST"])
def api_video_save_drive():
    data = request.get_json(force=True)
    filename = data.get("filename", "").strip()
    if not filename:
        return jsonify({"ok": False, "error": "Falta el video a guardar."}), 400

    src = video_maker.VIDEO_OUT_DIR / filename
    if not src.exists():
        return jsonify({"ok": False, "error": f"No se encontró el video: {filename}"}), 400

    if not GDRIVE_VIDEOS_DIR.exists():
        return jsonify({
            "ok": False,
            "error": f"No se encontró la carpeta de Google Drive: {GDRIVE_VIDEOS_DIR}. ¿Está sincronizando?",
        }), 400

    shutil.copy2(src, GDRIVE_VIDEOS_DIR / filename)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
# PUBLICACIÓN EN FACEBOOK (inmediata o programada)
# ─────────────────────────────────────────────

_fb_jobs = {}  # job_id -> {status, error, video_id, started_at}
_fb_jobs_lock = threading.Lock()

_ig_jobs = {}  # job_id -> {status, error, media_id, started_at}
_ig_jobs_lock = threading.Lock()


def _parse_scheduled_time(data: dict) -> tuple[Optional[float], Optional[str]]:
    """Lee 'scheduled_time' (datetime-local ISO) del body. Devuelve (timestamp, error)."""
    raw = (data.get("scheduled_time") or "").strip()
    if not raw:
        return None, None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None, "Fecha/hora de programación inválida."
    ts = time.mktime(dt.timetuple())
    if ts <= time.time():
        return None, "La fecha programada tiene que ser en el futuro."
    return ts, None


MIN_VIDEO_BYTES = 10 * 1024  # por debajo de esto, casi seguro está corrupto o vacío


def _video_precheck(video_path: Path) -> Optional[str]:
    """Chequeos básicos antes de publicar: el archivo existe y no está vacío/corrupto."""
    if not video_path.exists():
        return f"No se encontró el video: {video_path.name}"
    size = video_path.stat().st_size
    if size < MIN_VIDEO_BYTES:
        return f"El video parece estar corrupto o vacío ({size} bytes): {video_path.name}"
    return None


def _already_published(path: Path, filename: str) -> bool:
    """True si ya existe un registro de publicación para ese mismo archivo."""
    try:
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        return False
    return any(item.get("filename") == filename for item in items)


def _fb_set_stage(job_id: str, stage: str):
    with _fb_jobs_lock:
        if job_id in _fb_jobs:
            _fb_jobs[job_id]["stage"] = stage


def _run_facebook_job(job_id: str, video_path: str, title: str, description: str, target_ts: Optional[float] = None):
    if target_ts is not None:
        time.sleep(max(0, target_ts - time.time()))
        with _fb_jobs_lock:
            _fb_jobs[job_id]["status"] = "running"
    result = facebook_publisher.publish_video(
        video_path, title, description, on_status=lambda s: _fb_set_stage(job_id, s)
    )
    with _fb_jobs_lock:
        if result["ok"]:
            _fb_jobs[job_id].update(
                status="done", video_id=result["video_id"], scheduled_time=result["scheduled_time"]
            )
        else:
            _fb_jobs[job_id].update(status="error", error=result["error"])
        job_store.save("facebook", _fb_jobs)
    if result["ok"]:
        _record_published_facebook(result["video_id"], title, Path(video_path).name)


def _ig_set_stage(job_id: str, stage: str):
    with _ig_jobs_lock:
        if job_id in _ig_jobs:
            _ig_jobs[job_id]["stage"] = stage


def _run_instagram_job(job_id: str, video_path: str, title: str, description: str, target_ts: Optional[float] = None):
    if target_ts is not None:
        time.sleep(max(0, target_ts - time.time()))
        with _ig_jobs_lock:
            _ig_jobs[job_id]["status"] = "running"
    result = instagram_publisher.publish_video(
        video_path, title, description, on_status=lambda s: _ig_set_stage(job_id, s)
    )
    with _ig_jobs_lock:
        if result["ok"]:
            _ig_jobs[job_id].update(status="done", media_id=result.get("media_id"))
        else:
            _ig_jobs[job_id].update(status="error", error=result.get("error"))
        job_store.save("instagram", _ig_jobs)
    if result["ok"]:
        _record_published_instagram(result.get("media_id"), title, Path(video_path).name)


@app.route("/api/facebook/publish", methods=["POST"])
def api_facebook_publish():
    data = request.get_json(force=True)
    filename = data.get("filename", "").strip()
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()

    if not filename:
        return jsonify({"ok": False, "error": "Falta el video a publicar."}), 400

    video_path = video_maker.VIDEO_OUT_DIR / filename
    precheck_error = _video_precheck(video_path)
    if precheck_error:
        return jsonify({"ok": False, "error": precheck_error}), 400

    if not data.get("force") and _already_published(_PUBLISHED_FB_PATH, filename):
        return jsonify({"ok": False, "error": "Este video ya fue publicado antes en Facebook.", "duplicate": True}), 409

    target_ts, sched_err = _parse_scheduled_time(data)
    if sched_err:
        return jsonify({"ok": False, "error": sched_err}), 400

    job_id = uuid.uuid4().hex
    with _fb_jobs_lock:
        _fb_jobs[job_id] = {
            "status": "scheduled" if target_ts else "running",
            "stage": None,
            "error": None,
            "video_id": None,
            "scheduled_time": None,
            "scheduled_for": datetime.fromtimestamp(target_ts).isoformat() if target_ts else None,
            "started_at": time.time(),
        }
        job_store.save("facebook", _fb_jobs)

    thread = threading.Thread(
        target=_run_facebook_job,
        args=(job_id, str(video_path), title, description, target_ts),
        daemon=True,
    )
    thread.start()

    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/facebook/status/<job_id>")
def api_facebook_status(job_id):
    with _fb_jobs_lock:
        job = _fb_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "Trabajo no encontrado."}), 404
        response = dict(job)

    response.pop("started_at", None)
    response["ok"] = True
    return jsonify(response)


@app.route("/api/instagram/publish", methods=["POST"])
def api_instagram_publish():
    data = request.get_json(force=True)
    filename = data.get("filename", "").strip()
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()

    if not filename:
        return jsonify({"ok": False, "error": "Falta el video a publicar."}), 400

    video_path = video_maker.VIDEO_OUT_DIR / filename
    precheck_error = _video_precheck(video_path)
    if precheck_error:
        return jsonify({"ok": False, "error": precheck_error}), 400

    if not data.get("force") and _already_published(_PUBLISHED_IG_PATH, filename):
        return jsonify({"ok": False, "error": "Este video ya fue publicado antes en Instagram.", "duplicate": True}), 409

    target_ts, sched_err = _parse_scheduled_time(data)
    if sched_err:
        return jsonify({"ok": False, "error": sched_err}), 400

    job_id = uuid.uuid4().hex
    with _ig_jobs_lock:
        _ig_jobs[job_id] = {
            "status": "scheduled" if target_ts else "running",
            "stage": None,
            "error": None,
            "media_id": None,
            "scheduled_for": datetime.fromtimestamp(target_ts).isoformat() if target_ts else None,
            "started_at": time.time(),
        }
        job_store.save("instagram", _ig_jobs)

    thread = threading.Thread(
        target=_run_instagram_job,
        args=(job_id, str(video_path), title, description, target_ts),
        daemon=True,
    )
    thread.start()

    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/instagram/status/<job_id>")
def api_instagram_status(job_id):
    with _ig_jobs_lock:
        job = _ig_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "Trabajo no encontrado."}), 404
        response = dict(job)

    response.pop("started_at", None)
    response["ok"] = True
    return jsonify(response)


# ─────────────────────────────────────────────
# PUBLICACIÓN EN YOUTUBE
# ─────────────────────────────────────────────

_yt_jobs = {}  # job_id -> {status, error, video_id, started_at}
_yt_jobs_lock = threading.Lock()


@app.route("/api/youtube/connected")
def api_youtube_connected():
    return jsonify({"ok": True, "connected": youtube_publisher.is_connected()})


@app.route("/api/youtube/connect", methods=["POST"])
def api_youtube_connect():
    result = youtube_publisher.connect()
    return jsonify(result)


def _yt_set_stage(job_id: str, stage: str):
    with _yt_jobs_lock:
        if job_id in _yt_jobs:
            _yt_jobs[job_id]["stage"] = stage


def _run_youtube_job(job_id: str, video_path: str, title: str, description: str, privacy_status: str, tags: list, is_ai_generated: bool, target_ts: Optional[float] = None, thumbnail_path: Optional[str] = None):
    if target_ts is not None:
        time.sleep(max(0, target_ts - time.time()))
        with _yt_jobs_lock:
            _yt_jobs[job_id]["status"] = "running"
    result = youtube_publisher.publish_video(
        video_path, title, description, privacy_status, tags, is_ai_generated,
        on_status=lambda s: _yt_set_stage(job_id, s),
    )
    if result.get("ok") and thumbnail_path and Path(thumbnail_path).exists():
        _yt_set_stage(job_id, "Subiendo miniatura...")
        thumb_result = youtube_publisher.set_thumbnail(result["video_id"], thumbnail_path)
        if not thumb_result.get("ok"):
            result["thumbnail_error"] = thumb_result.get("error")

    with _yt_jobs_lock:
        if result["ok"]:
            _yt_jobs[job_id].update(status="done", video_id=result["video_id"],
                                     thumbnail_error=result.get("thumbnail_error"))
            _record_published_video(result["video_id"], title, Path(video_path).name)
        else:
            _yt_jobs[job_id].update(status="error", error=result["error"])
        job_store.save("youtube", _yt_jobs)


@app.route("/api/youtube/publish", methods=["POST"])
def api_youtube_publish():
    data = request.get_json(force=True)
    filename = data.get("filename", "").strip()
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    privacy_status = data.get("privacy_status", "public").strip()
    tags = [t.strip() for t in data.get("tags", "").split(",") if t.strip()]
    is_ai_generated = bool(data.get("is_ai_generated"))
    thumbnail_name = data.get("thumbnail", "").strip()

    if not filename:
        return jsonify({"ok": False, "error": "Falta el video a publicar."}), 400

    video_path = video_maker.VIDEO_OUT_DIR / filename
    precheck_error = _video_precheck(video_path)
    if precheck_error:
        return jsonify({"ok": False, "error": precheck_error}), 400

    if not data.get("force") and _already_published(_PUBLISHED_VIDEOS_PATH, filename):
        return jsonify({"ok": False, "error": "Este video ya fue publicado antes en YouTube.", "duplicate": True}), 409

    thumbnail_path = None
    if thumbnail_name:
        candidate = video_maker.VIDEO_OUT_DIR / thumbnail_name
        if candidate.exists():
            thumbnail_path = str(candidate)

    target_ts, sched_err = _parse_scheduled_time(data)
    if sched_err:
        return jsonify({"ok": False, "error": sched_err}), 400

    job_id = uuid.uuid4().hex
    with _yt_jobs_lock:
        _yt_jobs[job_id] = {
            "status": "scheduled" if target_ts else "running",
            "stage": None,
            "error": None,
            "video_id": None,
            "scheduled_for": datetime.fromtimestamp(target_ts).isoformat() if target_ts else None,
            "started_at": time.time(),
        }
        job_store.save("youtube", _yt_jobs)

    thread = threading.Thread(
        target=_run_youtube_job,
        args=(job_id, str(video_path), title, description, privacy_status, tags, is_ai_generated, target_ts, thumbnail_path),
        daemon=True,
    )
    thread.start()

    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/youtube/publish/status/<job_id>")
def api_youtube_publish_status(job_id):
    with _yt_jobs_lock:
        job = _yt_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "Trabajo no encontrado."}), 404
        response = dict(job)

    response.pop("started_at", None)
    response["ok"] = True
    return jsonify(response)


# ─────────────────────────────────────────────
# SEO, MINIATURAS Y ANALÍTICA DE YOUTUBE
# ─────────────────────────────────────────────

_PUBLISHED_VIDEOS_PATH = OUTPUT_DIR / "youtube_published.json"


def _record_published_video(video_id: str, title: str, filename: str = "") -> None:
    """Guarda cada video subido a YouTube para poder consultar su analítica después."""
    try:
        videos = json.loads(_PUBLISHED_VIDEOS_PATH.read_text(encoding="utf-8")) if _PUBLISHED_VIDEOS_PATH.exists() else []
    except Exception:
        videos = []
    videos.append({"video_id": video_id, "title": title, "filename": filename, "published_at": datetime.now().isoformat()})
    try:
        _PUBLISHED_VIDEOS_PATH.write_text(json.dumps(videos, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


@app.route("/api/seo/suggest", methods=["POST"])
def api_seo_suggest():
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    base_title = data.get("title", "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Falta el texto de la historia."}), 400
    suggestion = seo_optimizer.suggest_seo(text, base_title)
    return jsonify({"ok": True, **suggestion})


@app.route("/api/seo/suggest-social", methods=["POST"])
def api_seo_suggest_social():
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Falta el texto de la historia."}), 400
    suggestion = seo_optimizer.suggest_social_caption(text)
    return jsonify({"ok": True, **suggestion})


@app.route("/api/thumbnail/generate", methods=["POST"])
def api_thumbnail_generate():
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    scene_name = data.get("scene", "scene_000").strip()

    scene_path = None
    for f in video_maker.VIDEO_PUBLIC_DIR.glob(f"{scene_name}.*"):
        scene_path = f
        break
    if scene_path is None:
        return jsonify({"ok": False, "error": f"No se encontró una escena '{scene_name}' para usar de fondo."}), 400

    out_name = f"thumbnail_{uuid.uuid4().hex}.jpg"
    out_path = video_maker.VIDEO_OUT_DIR / out_name
    try:
        thumbnail_maker.generate_thumbnail(str(scene_path), title, str(out_path))
    except Exception as e:
        return jsonify({"ok": False, "error": f"Error generando la miniatura: {e}"}), 500

    return jsonify({"ok": True, "filename": out_name, "url": f"/video/{out_name}"})


@app.route("/api/thumbnail/variants", methods=["POST"])
def api_thumbnail_variants():
    """Genera hasta 3 variantes de miniatura (A/B) usando distintas escenas del video, para elegir la más llamativa."""
    data = request.get_json(force=True)
    title = data.get("title", "").strip()

    scenes = sorted(video_maker.VIDEO_PUBLIC_DIR.glob("scene_*.*"))
    if not scenes:
        return jsonify({"ok": False, "error": "No hay escenas disponibles para generar miniaturas."}), 400

    # elige hasta 3 escenas repartidas a lo largo del video (inicio, medio, final)
    count = min(3, len(scenes))
    indices = sorted({round(i * (len(scenes) - 1) / (count - 1)) for i in range(count)}) if count > 1 else [0]

    variants = []
    for idx in indices:
        scene_path = scenes[idx]
        out_name = f"thumbnail_{uuid.uuid4().hex}.jpg"
        out_path = video_maker.VIDEO_OUT_DIR / out_name
        try:
            thumbnail_maker.generate_thumbnail(str(scene_path), title, str(out_path))
        except Exception:
            continue
        variants.append({"filename": out_name, "url": f"/video/{out_name}"})

    if not variants:
        return jsonify({"ok": False, "error": "No se pudo generar ninguna variante de miniatura."}), 500

    return jsonify({"ok": True, "variants": variants})


def _analytics_rows(path: Path, id_field: str, stats_fn) -> list:
    """Lee un JSON de publicados y le mezcla las stats (views/likes/comments) ya frescas."""
    try:
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        items = []

    ids = [i[id_field] for i in items]
    stats_by_id = stats_fn(ids) if ids else {}
    return [{**i, **stats_by_id.get(i[id_field], {})} for i in items]


@app.route("/api/youtube/analytics")
def api_youtube_analytics():
    rows = _analytics_rows(_PUBLISHED_VIDEOS_PATH, "video_id", youtube_publisher.get_video_stats)
    return jsonify({"ok": True, "videos": rows})


# ─────────────────────────────────────────────
# ANALÍTICA DE FACEBOOK E INSTAGRAM
# ─────────────────────────────────────────────

_PUBLISHED_FB_PATH = OUTPUT_DIR / "facebook_published.json"
_PUBLISHED_IG_PATH = OUTPUT_DIR / "instagram_published.json"


def _record_published_item(path: Path, id_field: str, item_id, title: str, filename: str = "") -> None:
    try:
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        items = []
    items.append({id_field: item_id, "title": title, "filename": filename, "published_at": datetime.now().isoformat()})
    try:
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _record_published_facebook(video_id, title: str, filename: str = "") -> None:
    """Guarda cada video publicado en Facebook para poder consultar su analítica después."""
    if not video_id:
        return
    _record_published_item(_PUBLISHED_FB_PATH, "video_id", video_id, title, filename)


def _record_published_instagram(media_id, title: str, filename: str = "") -> None:
    """Guarda cada media publicado en Instagram para poder consultar su analítica después."""
    if not media_id:
        return
    _record_published_item(_PUBLISHED_IG_PATH, "media_id", media_id, title, filename)


@app.route("/api/facebook/analytics")
def api_facebook_analytics():
    rows = _analytics_rows(_PUBLISHED_FB_PATH, "video_id", facebook_publisher.get_video_stats)
    return jsonify({"ok": True, "videos": rows})


@app.route("/api/instagram/analytics")
def api_instagram_analytics():
    rows = _analytics_rows(_PUBLISHED_IG_PATH, "media_id", instagram_publisher.get_media_stats)
    return jsonify({"ok": True, "videos": rows})


@app.route("/api/analytics/feedback")
def api_analytics_feedback():
    all_rows = (
        _analytics_rows(_PUBLISHED_VIDEOS_PATH, "video_id", youtube_publisher.get_video_stats)
        + _analytics_rows(_PUBLISHED_FB_PATH, "video_id", facebook_publisher.get_video_stats)
        + _analytics_rows(_PUBLISHED_IG_PATH, "media_id", instagram_publisher.get_media_stats)
    )
    result = feedback_analyzer.analyze_from_rows(all_rows)
    return jsonify({"ok": True, **result})


@app.route("/api/facebook/best-time")
def api_facebook_best_time():
    rows = _analytics_rows(_PUBLISHED_FB_PATH, "video_id", facebook_publisher.get_video_stats)
    result = feedback_analyzer.best_posting_hour(rows)
    return jsonify({"ok": True, **result})


@app.route("/api/instagram/best-time")
def api_instagram_best_time():
    rows = _analytics_rows(_PUBLISHED_IG_PATH, "media_id", instagram_publisher.get_media_stats)
    result = feedback_analyzer.best_posting_hour(rows)
    return jsonify({"ok": True, **result})


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("   🎙️  SERVIDOR TTS INICIADO")
    print("=" * 55)
    print("   → Abre en tu navegador: http://localhost:5000")
    print("   → Ctrl+C para detener")
    print("=" * 55 + "\n")
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
