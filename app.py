"""
Servidor web (Flask) para el sistema de Texto a Voz con Chatterbox
+ generación automática de video con Remotion.
Levanta un servidor local en http://localhost:5000
"""

import atexit
import os
import json
import logging
import re
import secrets
import shutil
import sys
import time
import uuid
import threading
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
logger = logging.getLogger("app")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
from typing import Optional
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file, render_template_string

import video_maker
import auto_pipeline
import facebook_publisher
import instagram_publisher
import meta_auth
import youtube_publisher
import feedback_analyzer
import job_store
import seo_optimizer
import thumbnail_maker
import batch_pipeline
import cloudflare_tunnel
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
PORT = int(os.environ.get("PORT", 5000))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB: tope de subida (imágenes/clips del pipeline manual)

_PUBLIC_PATHS = ("/api/batch/cover/",)  # Instagram la baja directo vía el túnel, sin credenciales


@app.before_request
def _require_dashboard_auth():
    if request.path.startswith(_PUBLIC_PATHS):
        return None

    dashboard_user = os.environ.get("DASHBOARD_USER")
    dashboard_password = os.environ.get("DASHBOARD_PASSWORD")
    if not dashboard_user or not dashboard_password:
        # Fail-closed: la app puede quedar expuesta a internet vía el túnel,
        # así que se prefiere romper todo con un error explícito antes que
        # quedar abierta sin que nadie lo note.
        return (
            "Servidor mal configurado: faltan DASHBOARD_USER / DASHBOARD_PASSWORD "
            "en .env. La app no sirve nada hasta que se configuren.",
            500,
        )

    auth = request.authorization
    valid = bool(auth) and secrets.compare_digest(auth.username or "", dashboard_user) \
        and secrets.compare_digest(auth.password or "", dashboard_password)
    if not valid:
        resp = jsonify({"ok": False, "error": "Autenticación requerida"})
        resp.status_code = 401
        resp.headers["WWW-Authenticate"] = 'Basic realm="TTS Dashboard"'
        return resp
    return None


HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Panel de Video — TTS + Video</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Cinzel:wght@400&family=Playfair+Display:wght@700&family=Poppins:wght@500&family=Montserrat:wght@600&family=Bebas+Neue&family=Anton&family=Bangers&display=swap" rel="stylesheet">
<style>
  :root {
    --bg-main: #080808;
    --bg-sidebar: #0b0b0c;

    --surface-1: #101011;
    --surface-2: #151516;
    --surface-3: #1a1a1c;

    --border: #29292c;
    --border-hover: #3a3a3e;

    --text-primary: #f5f5f5;
    --text-secondary: #a1a1aa;
    --text-muted: #707078;

    --red-dark: #650910;
    --red-primary: #a60f1f;
    --red-light: #e32c3b;

    --gradient-red: linear-gradient(110deg, #650910 0%, #a60f1f 45%, #e32c3b 100%);
    --gradient-red-hover: linear-gradient(110deg, #7a0a14 0%, #c01526 45%, #f23847 100%);
    --gradient-red-active: linear-gradient(110deg, #5f0910, #a91120, #d52736);
    --gradient-red-subtle: linear-gradient(135deg, #131314, rgba(120, 10, 20, 0.20));

    --success: #3fb56f;
    --error: #e2665f;

    --radius-sm: 8px;
    --radius-md: 10px;
    --radius-lg: 14px;

    --sidebar-w: 220px;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    min-height: 100vh;
    background: var(--bg-main);
    color: var(--text-primary);
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    -webkit-font-smoothing: antialiased;
  }

  /* ── Sidebar ── */
  .sidebar {
    position: fixed; top: 0; left: 0; bottom: 0; width: var(--sidebar-w);
    background: var(--bg-sidebar); border-right: 1px solid var(--border);
    padding: 22px 14px; display: flex; flex-direction: column; gap: 4px;
    z-index: 40; transition: transform 150ms ease;
  }
  .sidebar-version {
    font-size: 11px; color: var(--text-muted); text-align: right;
    padding: 0 8px 16px; letter-spacing: .02em;
  }
  .nav-item {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 12px; border-radius: var(--radius-sm);
    color: var(--text-secondary); font-size: 14px; font-weight: 500;
    text-decoration: none; cursor: pointer; border: 1px solid transparent;
    background: transparent; transition: background 150ms ease, color 150ms ease, border-color 150ms ease;
    -webkit-tap-highlight-color: transparent;
  }
  .nav-item svg { width: 17px; height: 17px; flex-shrink: 0; }
  .nav-item:hover { background: var(--surface-2); color: var(--text-primary); }
  .nav-item:focus-visible { outline: 2px solid var(--red-light); outline-offset: 1px; }
  .nav-item.active {
    background: var(--gradient-red-active); color: #fff;
    box-shadow: 0 4px 14px rgba(166, 15, 31, .28);
  }
  .nav-item.disabled { opacity: .45; cursor: default; }
  .nav-item.disabled:hover { background: transparent; color: var(--text-secondary); }
  .nav-divider { height: 1px; background: var(--border); margin: 10px 4px; }

  .sidebar-backdrop {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,.6);
    z-index: 35; opacity: 0; transition: opacity 150ms ease;
  }
  .sidebar-backdrop.visible { display: block; opacity: 1; }

  .menu-toggle {
    display: none; position: fixed; top: 14px; left: 14px; z-index: 41;
    width: 40px; height: 40px; border-radius: var(--radius-sm);
    background: var(--surface-2); border: 1px solid var(--border);
    color: var(--text-primary); align-items: center; justify-content: center;
    cursor: pointer;
  }
  .menu-toggle svg { width: 19px; height: 19px; }

  /* ── Main ── */
  main {
    margin-left: var(--sidebar-w);
    max-width: 1200px;
    padding: 28px 36px 60px;
  }

  .tabs {
    display: flex; gap: 8px; margin-bottom: 22px;
    background: var(--surface-1); border: 1px solid var(--border);
    padding: 5px; border-radius: var(--radius-md);
  }
  .tab-btn {
    flex: 1; padding: 12px 16px; border-radius: var(--radius-sm);
    border: 1px solid transparent; background: transparent;
    color: var(--text-secondary); font-size: 14px; font-weight: 600;
    font-family: inherit; cursor: pointer; transition: all 150ms ease;
    display: flex; align-items: center; justify-content: center; gap: 8px;
    position: relative;
  }
  .tab-btn svg { width: 16px; height: 16px; flex-shrink: 0; }
  .tab-btn:hover:not(.active) { color: var(--text-primary); background: var(--surface-2); }
  .tab-btn:focus-visible { outline: 2px solid var(--red-light); outline-offset: 2px; }
  .tab-btn.active {
    color: #fff; background: linear-gradient(110deg, rgba(101,9,16,.55), rgba(166,15,31,.4));
    border-color: rgba(227,44,59,.35);
    box-shadow: inset 0 -2px 0 var(--red-light), 0 4px 16px rgba(166,15,31,.18);
  }

  .card {
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 26px;
    margin-bottom: 18px;
    box-shadow: 0 20px 50px rgba(0,0,0,.25);
    transition: border-color 150ms ease;
  }
  .card h2 {
    font-family: inherit;
    font-size: 18px; font-weight: 600; margin: 0 0 6px;
    display: flex; align-items: center; gap: 9px;
    letter-spacing: -.01em; color: var(--text-primary);
  }
  .card h2 svg { width: 18px; height: 18px; flex-shrink: 0; color: var(--red-light); }
  .card .card-desc { color: var(--text-secondary); font-size: 13.5px; line-height: 1.55; margin: 0 0 18px; }

  label { display: block; font-size: 14px; color: var(--text-secondary); margin-bottom: 8px; font-weight: 500; }

  .textarea-wrap { position: relative; }
  textarea {
    width: 100%; min-height: 210px; padding: 20px;
    border-radius: var(--radius-md); border: 1px solid #303033;
    background: #0c0c0d; color: var(--text-primary);
    font-size: 14.5px; font-family: inherit; line-height: 1.6; resize: vertical;
    transition: border-color 150ms ease, box-shadow 150ms ease;
  }
  textarea:focus {
    outline: none; border-color: var(--red-primary);
    box-shadow: 0 0 0 3px rgba(166,15,31,.16);
  }
  textarea:disabled { opacity: .6; cursor: not-allowed; }
  .char-count {
    text-align: right; color: var(--text-muted); font-size: 12.5px;
    margin-top: 8px; font-variant-numeric: tabular-nums;
  }
  .char-count.near-limit { color: var(--red-light); }

  input[type=text], input[type=number], select {
    width: 100%; padding: 12px 14px; border-radius: var(--radius-sm);
    border: 1px solid var(--border); background: var(--surface-3);
    color: var(--text-primary); font-size: 14px; font-family: inherit;
    transition: border-color 150ms ease;
    appearance: none; -webkit-appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%23a1a1aa' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
    background-repeat: no-repeat; background-position: right 14px center;
    padding-right: 36px;
  }
  input[type=number] { background-image: none; padding-right: 14px; }
  input[type=text]:focus, input[type=number]:focus, select:focus { outline: none; border-color: var(--red-primary); }
  input[type=text]:hover, input[type=number]:hover, select:hover { border-color: var(--border-hover); }
  select option { background: var(--surface-3); color: var(--text-primary); }

  .voice-section-label { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 500; color: var(--text-secondary); margin: 22px 0 10px; }
  .voice-section-label svg { width: 15px; height: 15px; color: var(--red-light); }

  .voice-grid {
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 12px;
  }
  .voice-card {
    padding: 16px 17px; border-radius: var(--radius-md); border: 1px solid var(--border);
    background: var(--surface-2); cursor: pointer; transition: all 150ms ease;
    position: relative;
  }
  .voice-card:hover { border-color: var(--border-hover); background: var(--surface-3); }
  .voice-card:focus-visible { outline: 2px solid var(--red-light); outline-offset: 2px; }
  .voice-card.active {
    border-color: #c21b2b; background: var(--gradient-red-subtle);
  }
  .voice-card .name { font-weight: 600; font-size: 14px; margin-bottom: 8px; letter-spacing: -.005em; color: var(--text-primary); }
  .voice-card .status { font-size: 13px; color: var(--text-secondary); display: flex; align-items: center; gap: 5px; }
  .voice-card .status.ready { color: #7fd8a0; }
  .voice-card .gentle-badge {
    display: inline-block; font-size: 11.5px; font-weight: 600;
    background: var(--gradient-red); color: #fff;
    padding: 3px 10px; border-radius: 999px; margin-top: 10px;
  }

  .subtitle-preset-grid {
    display: grid; grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }
  .subtitle-preset-card {
    padding: 0; border-radius: var(--radius-md); border: 1px solid var(--border);
    background: #000; cursor: pointer; transition: all 150ms ease;
    overflow: hidden; position: relative;
  }
  .subtitle-preset-card:hover { border-color: var(--border-hover); }
  .subtitle-preset-card.active { border-color: #c21b2b; box-shadow: 0 0 0 1px #c21b2b; }
  .subtitle-preset-card .preview {
    height: 64px; display: flex; align-items: center; justify-content: center;
    background: linear-gradient(160deg, #2b2b2b, #050505);
  }
  .subtitle-preset-card .preview span {
    font-size: 24px; line-height: 1.2; padding: 4px 10px; border-radius: 6px;
    text-shadow: 0 1px 4px rgba(0,0,0,0.9);
  }
  .subtitle-preset-card .name {
    font-size: 12.5px; color: var(--text-secondary); text-align: center;
    padding: 7px 4px; border-top: 1px solid var(--border);
  }

  .row-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 22px; }
  .field-label-row { display: flex; align-items: center; gap: 7px; margin-bottom: 8px; }
  .field-label-row svg { width: 14px; height: 14px; color: var(--red-light); }
  .field-label-row label { margin-bottom: 0; }

  .checkbox-row {
    display: flex; align-items: center; gap: 10px; margin-top: 18px;
    font-size: 14px; color: var(--text-secondary); cursor: pointer; user-select: none;
  }
  input[type=checkbox] {
    appearance: none; -webkit-appearance: none; width: 18px; height: 18px;
    border: 1px solid var(--border-hover); border-radius: 5px; background: var(--surface-3);
    cursor: pointer; position: relative; flex-shrink: 0; transition: all 150ms ease;
  }
  input[type=checkbox]:checked {
    background: var(--gradient-red); border-color: var(--red-primary);
  }
  input[type=checkbox]:checked::after {
    content: ""; position: absolute; left: 5px; top: 1px; width: 5px; height: 9px;
    border: solid #fff; border-width: 0 2px 2px 0; transform: rotate(45deg);
  }
  input[type=checkbox]:focus-visible { outline: 2px solid var(--red-light); outline-offset: 2px; }

  .btn {
    border: none; border-radius: var(--radius-md);
    font-size: 15px; font-weight: 600; font-family: inherit; cursor: pointer;
    display: flex; align-items: center; justify-content: center; gap: 9px;
    transition: transform 100ms ease, box-shadow 150ms ease, opacity 150ms ease, background 150ms ease;
  }
  .btn:active:not(:disabled) { transform: scale(.99); }
  .btn:disabled { opacity: .5; cursor: not-allowed; }
  .btn:focus-visible { outline: 2px solid var(--red-light); outline-offset: 2px; }
  .btn-primary {
    width: 100%; height: 58px;
    background: var(--gradient-red); color: #fff;
    box-shadow: 0 8px 30px rgba(190,20,35,.15);
    margin-top: 22px;
  }
  .btn-primary:hover:not(:disabled) { background: var(--gradient-red-hover); }
  .btn-sm {
    padding: 9px 16px; border-radius: var(--radius-sm); border: 1px solid var(--border);
    background: var(--surface-2); color: var(--text-primary); cursor: pointer;
    font-size: 13.5px; font-weight: 500; font-family: inherit; transition: all 150ms ease;
  }
  .btn-sm:hover { border-color: var(--border-hover); background: var(--surface-3); }
  .btn-sm:focus-visible { outline: 2px solid var(--red-light); outline-offset: 2px; }

  .pipeline-action-row { display: flex; gap: 10px; margin-top: 22px; }
  .pipeline-action-row .btn-primary { margin-top: 0; }
  .pipeline-action-row .btn { flex: 1; }
  .btn-retry {
    background: var(--surface-2); color: var(--text-primary);
    border: 1px solid var(--border);
  }
  .btn-retry:hover:not(:disabled) { background: var(--surface-3); border-color: var(--border-hover); }
  .btn-outline-danger {
    width: 100%; height: 48px; margin-top: 10px;
    background: transparent; color: var(--red-light);
    border: 1px solid var(--red-primary);
  }
  .btn-outline-danger:hover:not(:disabled) { background: rgba(190,20,35,.08); }

  /* ── Filas de estado del pipeline ── */
  .status-rows { display: flex; flex-direction: column; gap: 10px; margin-top: 18px; }
  .status-row {
    display: flex; align-items: center; justify-content: space-between; gap: 14px;
    background: var(--surface-2); border: 1px solid var(--border);
    border-radius: var(--radius-md); padding: 15px 18px;
    transition: border-color 150ms ease;
  }
  .status-row-text strong { display: block; font-size: 14px; font-weight: 600; color: var(--text-primary); margin-bottom: 3px; }
  .status-row-text span { font-size: 13px; color: var(--text-secondary); line-height: 1.4; }
  .status-row.is-error { border-color: rgba(226,102,95,.35); }
  .status-row.is-error .status-row-text span { color: var(--error); }
  .status-row.is-done .status-row-text span { color: #7fd8a0; }

  .ring {
    --pct: 0;
    width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0;
    background: conic-gradient(var(--red-light) calc(var(--pct) * 1%), var(--border) 0);
    display: flex; align-items: center; justify-content: center;
  }
  .ring::after { content: ""; width: 22px; height: 22px; border-radius: 50%; background: var(--surface-2); }
  .ring.indeterminate {
    background: conic-gradient(var(--red-light), var(--border));
    animation: spin 0.9s linear infinite;
  }
  .ring.pending { background: var(--border); }
  .ring.pending::after { background: var(--surface-2); }
  .ring.done { background: var(--gradient-red); }
  .ring.done::after { content: "✓"; background: transparent; color: #fff; font-size: 13px; display: flex; align-items: center; justify-content: center; width: auto; height: auto; }
  .ring.error { background: var(--error); }
  .ring.error::after { content: "!"; background: transparent; color: #fff; font-size: 13px; font-weight: 700; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .pub-status { margin-top: 8px; font-size: 13px; display: flex; align-items: center; gap: 8px; }
  .pub-status.success { color: var(--success); }
  .pub-status.error { color: var(--error); flex-wrap: wrap; }
  .pub-status.loading { color: var(--text-secondary); }
  .pub-spinner {
    width: 14px; height: 14px; border: 2px solid var(--border);
    border-top-color: var(--red-light); border-radius: 50%;
    animation: spin .7s linear infinite; flex-shrink: 0;
  }
  .pub-target-row { display: flex; gap: 18px; flex-wrap: wrap; margin-bottom: 10px; }
  .pub-target-row label { display: flex; align-items: center; gap: 6px; font-size: 14px; color: var(--text-primary); margin-bottom: 0; width: auto; }
  .pub-target-row input[type="checkbox"] { width: auto; }
  .pub-hint { color: var(--text-muted); font-size: 12.5px; margin-top: 6px; line-height: 1.5; }
  @media (prefers-reduced-motion: reduce) {
    .ring.indeterminate { animation: none; }
  }

  #pipeline-result { display: none; }
  #pipeline-result.visible { display: block; }

  /* ── Layout de dos columnas: form de generación + panel lateral (preview + publicar) ── */
  .video-layout { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 20px; align-items: start; }
  .video-layout-main { min-width: 0; }
  .video-layout-side { position: sticky; top: 20px; }
  @media (max-width: 1023px) {
    .video-layout { grid-template-columns: 1fr; }
    .video-layout-side { position: static; }
  }
  #pipeline-player { width: 100%; max-width: 380px; border-radius: var(--radius-lg); display: block; margin: 0 auto; border: 1px solid var(--border); }

  table { width: 100%; border-collapse: collapse; }
  thead tr { text-align: left; color: var(--text-secondary); font-size: 12.5px; text-transform: uppercase; letter-spacing: .04em; }
  th, td { padding: 10px 8px; font-size: 13.5px; }
  tbody tr { border-top: 1px solid var(--border); }
  tbody tr:hover { background: var(--surface-2); }

  code {
    background: var(--surface-3); padding: 2px 7px; border-radius: 6px;
    font-size: .85em; color: var(--red-light); border: 1px solid var(--border);
  }

  .tag {
    display: inline-flex; align-items: center; gap: 5px;
    background: var(--surface-3); border: 1px solid var(--border);
    color: var(--text-secondary); font-size: 12.5px; font-weight: 500;
    padding: 5px 10px; border-radius: 999px;
  }
  .tag strong { color: var(--text-primary); font-weight: 700; }
  .tag-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }

  .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr)); gap: 12px; margin-top: 14px; }
  .stat-card { background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 14px 16px; }
  .stat-card .stat-value { font-size: 22px; font-weight: 700; color: var(--text-primary); letter-spacing: -.01em; }
  .stat-card .stat-label { font-size: 12.5px; color: var(--text-secondary); margin-top: 4px; }
  .stat-card .stat-keywords { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }

  .table-best-row { background: rgba(227,44,59,.06); }
  .table-best-row td:first-child { border-left: 2px solid var(--red-light); }

  .analytics-empty { text-align: center; padding: 24px 12px; color: var(--text-secondary); font-size: 13.5px; }

  .progress-bar { height: 8px; border-radius: 999px; background: var(--surface-3); overflow: hidden; margin-top: 10px; }
  .progress-bar-fill { height: 100%; background: var(--red-light); border-radius: 999px; transition: width .4s; }

  .lote-video-list { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--surface-3); display: flex; flex-direction: column; gap: 8px; }
  .lote-video-row { font-size: 12.5px; color: var(--text-secondary); }
  .lote-video-row .progress-bar { margin-top: 5px; height: 5px; }
  .lote-video-row-line { display: flex; align-items: center; justify-content: space-between; gap: 10px; }

  @media (max-width: 1199px) {
    .voice-grid { grid-template-columns: repeat(2, 1fr); }
  }

  @media (max-width: 767px) {
    .sidebar { transform: translateX(-100%); }
    .sidebar.open { transform: translateX(0); box-shadow: 0 0 40px rgba(0,0,0,.5); }
    .menu-toggle { display: flex; }
    main { margin-left: 0; padding: 76px 16px 48px; }
    .row-2col { grid-template-columns: 1fr; }
    .voice-grid { grid-template-columns: 1fr; }
    .card { padding: 18px; }
  }

  .qr-modal-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,.75);
    display: flex; align-items: center; justify-content: center;
    z-index: 1000; padding: 20px;
  }
  .qr-modal {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
    padding: 28px; max-width: 360px; width: 100%; text-align: center; position: relative;
  }
  .qr-modal-close {
    position: absolute; top: 10px; right: 12px; background: none; border: none;
    color: var(--text-secondary); font-size: 24px; line-height: 1; cursor: pointer;
  }
  .qr-modal-close:hover { color: var(--text); }
  .qr-modal h3 { margin: 0 0 16px; font-size: 16px; }
  .qr-modal img {
    width: 100%; max-width: 340px; border-radius: 8px; border: 1px solid var(--border);
    background: #fff; padding: 10px;
  }
  .qr-modal-hint { margin: 16px 0 0; font-size: 12.5px; color: var(--text-secondary); }
  .app-modal-actions { display: flex; gap: 10px; justify-content: center; margin-top: 20px; }
  .app-modal-actions .btn, .app-modal-actions .btn-sm { flex: 1; }

  /* ── Historial: grilla de videos generados ── */
  .hist-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 16px; margin-top: 14px; }
  .hist-item { border: 1px solid var(--border); border-radius: var(--radius-lg); overflow: hidden; background: var(--surface-2); }
  .hist-item video { width: 100%; aspect-ratio: 9/16; object-fit: cover; display: block; background: #000; }
  .hist-item-body { padding: 10px; }
  .hist-item-name { font-size: 11.5px; color: var(--text-secondary); word-break: break-all; margin: 0 0 8px; }
  .hist-item.selected { outline: 2px solid var(--red-light); }
</style>
</head>
<body>

<button class="menu-toggle" id="menu-toggle" aria-label="Abrir menú" aria-expanded="false">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
</button>
<div class="sidebar-backdrop" id="sidebar-backdrop"></div>

<nav class="sidebar" id="sidebar">
  <div class="sidebar-version">v1.0</div>
  <a class="nav-item active" id="nav-video" data-tab="video">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M15 8l5-3v14l-5-3"/><rect x="3" y="6" width="12" height="12" rx="2"/></svg>
    Crear video
  </a>
  <div class="nav-divider"></div>
  <a class="nav-item" data-tab="video">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M15 8l5-3v14l-5-3"/><rect x="3" y="6" width="12" height="12" rx="2"/></svg>
    Video
  </a>
  <a class="nav-item" id="nav-analytics" data-tab="analytics">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 20V10M12 20V4M20 20v-7"/></svg>
    Analítica
  </a>
  <a class="nav-item" id="nav-historial" data-tab="historial">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>
    Historial
  </a>
  <a class="nav-item" id="nav-lote" data-tab="lote">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
    Generación en lote
  </a>
  <a class="nav-item" id="nav-ajustes" data-tab="ajustes">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
    Ajustes
  </a>
  <a class="nav-item disabled" aria-disabled="true">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
    Guías
  </a>
  <a class="nav-item disabled" aria-disabled="true">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.4 2.32c-.77.3-1.4.98-1.4 1.68v.3"/><path d="M12 17h.01"/></svg>
    Soporte
  </a>
</nav>

<main>

  <div id="tab-video">

    <div class="video-layout">
    <div class="video-layout-main">
    <div class="card">
      <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M5 3l14 9-14 9V3z"/></svg> Generar video (automático)</h2>
      <p class="card-desc">
        Pegá el guion y los prompts de imagen que te da ChatGPT, cada uno en su
        campo. El servidor narra el guion, genera los clips en WhatsApp/Meta IA
        con los prompts y arma el video final con Remotion, todo en un solo paso.
      </p>

      <div class="field-label-row">
        <label for="pipeline-script">Guion (narración)</label>
      </div>
      <div class="textarea-wrap">
        <textarea id="pipeline-script" rows="6" placeholder="Pegá acá el guion para narrar..."></textarea>
        <div class="char-count" id="pipeline-script-char-count">0 / 5000</div>
      </div>

      <div class="field-label-row" style="margin-top:14px">
        <label for="pipeline-prompts">Prompts de imagen (Imagen 1, Imagen 2, ...)</label>
      </div>
      <div class="textarea-wrap">
        <textarea id="pipeline-prompts" rows="8" placeholder="Pegá acá el bloque de prompts de ChatGPT (Imagen 1, Frase del guion, Prompt, Imagen 2, ...)..."></textarea>
        <div class="char-count" id="pipeline-prompts-char-count">0 / 5000</div>
      </div>

      <div class="voice-section-label">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4"/></svg>
        Voz
      </div>
      <div class="voice-grid" id="pipeline-voice-grid">Cargando...</div>

      <div class="row-2col">
        <div>
          <div class="field-label-row">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="4" y="2" width="16" height="20" rx="2"/></svg>
            <label for="pipeline-orientation">Formato</label>
          </div>
          <select id="pipeline-orientation">
            <option value="vertical">Vertical 9:16 (Reels / Shorts / TikTok)</option>
            <option value="horizontal">Horizontal 16:9 (YouTube estándar)</option>
          </select>
        </div>
        <div>
          <div class="field-label-row">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 20V10M12 20V4M20 20v-7"/></svg>
            <label>Subtítulos — estilo</label>
          </div>
          <div class="subtitle-preset-grid" id="pipeline-subtitle-preset-grid">Cargando...</div>
        </div>
      </div>

      <div class="field-label-row" style="margin-top:14px">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>
        <label for="pipeline-duration">Duración del video</label>
      </div>
      <select id="pipeline-duration">
        <option value="">Automático</option>
        <option value="15">15 segundos</option>
        <option value="30">30 segundos</option>
        <option value="60">60 segundos</option>
        <option value="180" class="duration-long-option">3 minutos</option>
        <option value="300" class="duration-long-option">5 minutos</option>
        <option value="600" class="duration-long-option">10 minutos</option>
      </select>

      <div class="field-label-row" style="margin-top:14px">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M5 3l14 9-14 9V3z"/></svg>
        <label for="pipeline-provider">Generador de clips</label>
      </div>
      <select id="pipeline-provider">
        <option value="whatsapp">WhatsApp / Meta IA</option>
        <option value="qwen">Qwen (chat.qwen.ai)</option>
        <option value="mixed">Mixto (WhatsApp + Qwen en paralelo)</option>
      </select>

      <label class="checkbox-row" for="pipeline-subtitles-enabled">
        <input type="checkbox" id="pipeline-subtitles-enabled" checked>
        Incluir subtítulos
      </label>

      <label class="checkbox-row" for="pipeline-animate-images">
        <input type="checkbox" id="pipeline-animate-images" checked>
        Animar imágenes (Ken Burns)
      </label>

      <label class="checkbox-row" for="pipeline-generate-video-clips">
        <input type="checkbox" id="pipeline-generate-video-clips" checked>
        Animar clips al generarlos (si no, solo imagen estática)
      </label>

      <div class="pipeline-action-row">
        <button id="pipeline-start-btn" class="btn btn-primary">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="18" height="18"><path d="M12 3l1.9 5.7L20 10.5l-5.7 1.9L12 18l-1.9-5.6L4 10.5l5.7-1.8z"/></svg>
          Generar video
        </button>
        <button id="pipeline-retry-btn" class="btn btn-primary btn-retry" style="display:none">
          ⟳ Reintentar
        </button>
      </div>
      <button id="pipeline-cancel-btn" class="btn btn-outline-danger" style="display:none">
        ✕ Cancelar (borra audio, clips y video de este intento)
      </button>

      <div id="pipeline-panels" style="display:none">
        <div class="status-rows">
          <div class="status-row" id="pipeline-row-tts">
            <div class="status-row-text">
              <strong>Guion / TTS</strong>
              <span id="pipeline-panel-tts">—</span>
            </div>
            <div class="ring pending" id="pipeline-ring-tts"></div>
          </div>
          <div class="status-row" id="pipeline-row-clipgen">
            <div class="status-row-text">
              <strong>Clips</strong>
              <span id="pipeline-panel-clipgen">—</span>
            </div>
            <button class="btn-sm" id="pipeline-clipgen-restart-btn" style="display:none">⟳ Reiniciar</button>
            <div class="ring pending" id="pipeline-ring-clipgen"></div>
          </div>
          <div class="status-row" id="pipeline-row-video">
            <div class="status-row-text">
              <strong>Video (Remotion)</strong>
              <span id="pipeline-panel-video">—</span>
            </div>
            <div class="ring pending" id="pipeline-ring-video"></div>
          </div>
        </div>
      </div>
    </div>
    </div>

    <div class="video-layout-side" id="video-result-home">
      <div id="pipeline-result">
        <div class="card">
          <video id="pipeline-player" controls></video>
        </div>

        <div class="card" style="margin-top:20px">
          <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg> Publicar</h2>

          <div id="pub-page-wrap" style="display:none;margin-bottom:10px">
            <label for="pub-page">Página de Facebook/Instagram</label>
            <select id="pub-page"></select>
          </div>

          <div class="pub-target-row">
            <label><input type="checkbox" id="pub-fb" checked> 📘 Facebook</label>
            <label><input type="checkbox" id="pub-ig" checked> 📸 Instagram</label>
            <label><input type="checkbox" id="pub-yt"> ▶️ YouTube</label>
          </div>

          <div id="pub-fb-extra" style="margin-top:14px">
            <label for="pub-fb-description">Descripción (Facebook)</label>
            <div class="textarea-wrap">
              <textarea id="pub-fb-description" rows="3" style="min-height:auto" placeholder="Descripción del video..."></textarea>
            </div>
            <div style="margin-top:8px">
              <button class="btn-sm" type="button" id="fb-caption-btn">✨ Sugerir caption</button>
            </div>
            <div id="fb-caption-status" class="pub-hint"></div>
            <label class="checkbox-row" for="fb-auto-time" style="margin-top:10px">
              <input type="checkbox" id="fb-auto-time" checked> ⏰ Publicar en el mejor horario detectado
            </label>
            <div id="fb-best-time-hint" class="pub-hint"></div>
          </div>

          <div id="pub-ig-extra" style="margin-top:14px">
            <label for="pub-ig-description">Descripción (Instagram)</label>
            <div class="textarea-wrap">
              <textarea id="pub-ig-description" rows="3" style="min-height:auto" placeholder="Descripción del video..."></textarea>
            </div>
            <div style="margin-top:8px">
              <button class="btn-sm" type="button" id="ig-caption-btn">✨ Sugerir caption</button>
            </div>
            <div id="ig-caption-status" class="pub-hint"></div>
            <label class="checkbox-row" for="ig-auto-time" style="margin-top:10px">
              <input type="checkbox" id="ig-auto-time" checked> ⏰ Publicar en el mejor horario detectado
            </label>
            <div id="ig-best-time-hint" class="pub-hint"></div>
            <p class="pub-hint">Se publica como Reel, marcado como contenido generado con IA.</p>
          </div>

          <div id="pub-yt-extra" style="display:none;margin-top:14px">
            <div id="yt-connect-wrap">
              <p class="pub-hint">Todavía no conectaste tu cuenta de YouTube.</p>
              <button class="btn-sm" id="yt-connect-btn">🔗 Conectar YouTube</button>
              <div id="yt-connect-status" class="pub-hint"></div>
            </div>
            <div id="yt-privacy-wrap" style="display:none">
              <label for="yt-title">Título en YouTube</label>
              <input type="text" id="yt-title" placeholder="Título del video en YouTube">
              <label for="pub-yt-description" style="margin-top:10px">Descripción (YouTube)</label>
              <div class="textarea-wrap">
                <textarea id="pub-yt-description" rows="3" style="min-height:auto" placeholder="Descripción del video..."></textarea>
              </div>
              <label for="yt-tags" style="margin-top:10px">Etiquetas</label>
              <input type="text" id="yt-tags" placeholder="Etiquetas separadas por coma">
              <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
                <button class="btn-sm" type="button" id="yt-seo-btn">✨ Sugerir SEO</button>
                <button class="btn-sm" type="button" id="yt-thumb-btn">🖼️ Generar miniatura</button>
                <button class="btn-sm" type="button" id="yt-thumb-variants-btn">🅰️🅱️ Generar variantes</button>
              </div>
              <div id="yt-seo-status" class="pub-hint"></div>
              <div id="yt-thumb-preview" style="display:none;margin-top:10px">
                <img id="yt-thumb-img" style="max-width:220px;border-radius:8px;display:block" alt="Miniatura generada">
              </div>
              <div id="yt-thumb-variants" style="display:none;margin-top:10px;gap:10px;flex-wrap:wrap"></div>
              <label class="checkbox-row" for="yt-is-ai">
                <input type="checkbox" id="yt-is-ai" checked> Marcar como contenido generado con IA
              </label>
              <label for="yt-privacy" style="margin-top:10px">Privacidad en YouTube</label>
              <select id="yt-privacy">
                <option value="public">Público</option>
                <option value="unlisted">No listado</option>
                <option value="private">Privado</option>
              </select>
            </div>
          </div>

          <div style="margin-top:14px">
            <label class="checkbox-row" for="pub-schedule">
              <input type="checkbox" id="pub-schedule"> 🕒 Programar publicación
            </label>
            <div id="pub-schedule-wrap" style="display:none;margin-top:8px">
              <input type="datetime-local" id="pub-schedule-time">
              <p class="pub-hint">Se aplica a Facebook, Instagram y YouTube que estén tildados.</p>
            </div>
          </div>

          <button class="btn btn-primary" id="publish-btn" style="margin-top:16px">📤 Publicar</button>
          <div id="meta-token-status" class="pub-status" style="display:none"></div>
          <div id="fb-status" class="pub-status"></div>
          <div id="ig-status" class="pub-status"></div>
          <div id="yt-status" class="pub-status"></div>
        </div>
      </div>
    </div>
    </div>

  </div> <!-- /tab-video -->

  <div id="tab-historial" style="display:none">

    <div class="video-layout">
      <div class="video-layout-main">
        <div class="card">
          <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg> Videos generados</h2>
          <p class="card-desc">Elegí un video ya generado para publicarlo en redes sociales.</p>
          <button class="btn-sm" id="historial-refresh-btn">⟳ Actualizar</button>
          <div id="historial-grid" class="hist-grid"></div>
        </div>
      </div>

      <div class="video-layout-side" id="historial-publish-slot">
        <p class="card-desc" id="historial-publish-hint">Elegí un video de la lista para publicarlo.</p>
      </div>
    </div>

  </div> <!-- /tab-historial -->

  <div id="tab-analytics" style="display:none">

    <div class="card">
      <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 20V10M12 20V4M20 20v-7"/></svg> Analítica de Facebook</h2>
      <button class="btn-sm" id="fb-analytics-refresh-btn">⟳ Actualizar</button>
      <div id="fb-analytics-table" style="margin-top:14px"></div>
    </div>

    <div class="card">
      <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 20V10M12 20V4M20 20v-7"/></svg> Analítica de YouTube</h2>
      <button class="btn-sm" id="yt-analytics-refresh-btn">⟳ Actualizar</button>
      <div id="yt-analytics-table" style="margin-top:14px"></div>
    </div>

    <div class="card">
      <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 20V10M12 20V4M20 20v-7"/></svg> Analítica de Instagram</h2>
      <button class="btn-sm" id="ig-analytics-refresh-btn">⟳ Actualizar</button>
      <div id="ig-analytics-table" style="margin-top:14px"></div>
    </div>

    <div class="card">
      <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.6.5 1 1.3 1 2.5h6c0-1.2.4-2 1-2.5A6 6 0 0 0 12 3z"/></svg> Qué funcionó mejor</h2>
      <button class="btn-sm" id="feedback-analytics-refresh-btn">⟳ Actualizar</button>
      <div id="feedback-analytics-result" style="margin-top:14px"></div>
    </div>

  </div> <!-- /tab-analytics -->

  <div id="tab-lote" style="display:none">

    <div class="card">
      <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg> Nuevo proyecto de lote</h2>
      <p class="card-desc">
        Genera y publica varios videos completos de forma automática, sacando
        guion y prompts de un Project de Qwen (chat.qwen.ai). Los videos se
        publican espaciados en varios días, revisando antes de cada uno cuándo
        se publicó el anterior en cada red.
      </p>

      <div class="field-label-row">
        <label for="lote-nombre">Nombre del proyecto</label>
      </div>
      <input type="text" id="lote-nombre" placeholder="Ej: Historias de animales - tanda 1">

      <div class="field-label-row" style="margin-top:14px">
        <label for="lote-type">Tipo de publicación</label>
      </div>
      <select id="lote-type">
        <option value="video">Video completo (guion + clips + audio)</option>
        <option value="gaming_image">Post de imagen — Gaming viral (1:1, Facebook + Instagram)</option>
      </select>

      <div id="lote-gaming-only-fields" style="display:none">
        <div class="field-label-row" style="margin-top:14px">
          <label for="lote-image-provider">Generador de la imagen de portada</label>
        </div>
        <select id="lote-image-provider">
          <option value="qwen">Qwen (chat.qwen.ai)</option>
          <option value="whatsapp">WhatsApp / Meta IA</option>
        </select>
      </div>

      <div class="field-label-row" style="margin-top:14px">
        <label for="lote-qwen-project">Proyecto de Qwen (nombre exacto en el sidebar de Projects)</label>
      </div>
      <input type="text" id="lote-qwen-project" placeholder="Ej: HISTORIAS DE ANIMALES EMOCIONALES">

      <div class="field-label-row" style="margin-top:14px">
        <label for="lote-trigger-message">Mensaje inicial a Qwen (lo que espera ese Project para responder con el formato)</label>
      </div>
      <input type="text" id="lote-trigger-message" placeholder="dame una historia" value="dame una historia">

      <div class="row-2col">
        <div>
          <div class="field-label-row">
            <label for="lote-total">Cantidad de videos a generar</label>
          </div>
          <input type="number" id="lote-total" min="1" value="10">
        </div>
        <div>
          <div class="field-label-row">
            <label for="lote-per-day">Videos a subir por día</label>
          </div>
          <input type="number" id="lote-per-day" min="1" value="3">
        </div>
      </div>

      <div class="field-label-row" style="margin-top:22px">
        <label>Redes donde publicar</label>
      </div>
      <div class="pub-target-row">
        <label><input type="checkbox" id="lote-fb" checked> 📘 Facebook</label>
        <label><input type="checkbox" id="lote-ig" checked> 📸 Instagram</label>
        <label><input type="checkbox" id="lote-yt"> ▶️ YouTube</label>
      </div>
      <div id="lote-page-wrap" style="display:none;margin-top:10px">
        <label for="lote-page">Página de Facebook/Instagram</label>
        <select id="lote-page"></select>
      </div>

      <div id="lote-video-only-fields">
        <div class="row-2col">
          <div>
            <div class="field-label-row">
              <label for="lote-voice">Voz</label>
            </div>
            <select id="lote-voice"><option>Cargando...</option></select>
          </div>
          <div>
            <div class="field-label-row">
              <label for="lote-subtitle-preset">Subtítulos — estilo</label>
            </div>
            <select id="lote-subtitle-preset"><option>Cargando...</option></select>
          </div>
        </div>

        <div class="row-2col">
          <div>
            <div class="field-label-row">
              <label for="lote-orientation">Formato</label>
            </div>
            <select id="lote-orientation">
              <option value="vertical">Vertical 9:16 (Reels / Shorts / TikTok)</option>
              <option value="horizontal">Horizontal 16:9 (YouTube estándar)</option>
            </select>
          </div>
          <div>
            <div class="field-label-row">
              <label for="lote-provider">Generador de clips</label>
            </div>
            <select id="lote-provider">
              <option value="whatsapp">WhatsApp / Meta IA</option>
              <option value="qwen">Qwen (chat.qwen.ai)</option>
              <option value="mixed">Mixto (WhatsApp + Qwen en paralelo)</option>
            </select>
          </div>
        </div>

        <div class="field-label-row" style="margin-top:14px">
          <label for="lote-duration">Duración del video</label>
        </div>
        <select id="lote-duration">
          <option value="">Automático</option>
          <option value="15">15 segundos</option>
          <option value="30">30 segundos</option>
          <option value="60">60 segundos</option>
          <option value="180" class="duration-long-option">3 minutos</option>
          <option value="300" class="duration-long-option">5 minutos</option>
          <option value="600" class="duration-long-option">10 minutos</option>
        </select>

        <label class="checkbox-row" for="lote-subtitles-enabled">
          <input type="checkbox" id="lote-subtitles-enabled" checked>
          Incluir subtítulos
        </label>
        <label class="checkbox-row" for="lote-animate-images">
          <input type="checkbox" id="lote-animate-images" checked>
          Animar imágenes (Ken Burns)
        </label>
        <label class="checkbox-row" for="lote-generate-video-clips">
          <input type="checkbox" id="lote-generate-video-clips" checked>
          Animar clips al generarlos (si no, solo imagen estática)
        </label>
      </div>

      <button class="btn btn-primary" id="lote-create-btn" style="margin-top:16px">
        Crear proyecto de lote
      </button>
      <div id="lote-create-status" class="pub-status"></div>
    </div>

    <div class="card">
      <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg> Proyectos de lote</h2>
      <button class="btn-sm" id="lote-refresh-btn">⟳ Actualizar</button>
      <div id="lote-list" style="margin-top:14px"></div>
    </div>

  </div> <!-- /tab-lote -->

  <div id="tab-ajustes" style="display:none">

    <div class="card">
      <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 3h18v18H3z"/></svg> Conexión de WhatsApp / Meta IA</h2>
      <p class="card-desc">Usada para generar los clips de video. Si aparece "Necesita reconectar", tocá el botón para escanear el QR de nuevo.</p>
      <div class="status-rows">
        <div class="status-row" id="ajustes-row-whatsapp">
          <div class="status-row-text">
            <strong>WhatsApp Web</strong>
            <span id="ajustes-msg-whatsapp">Sin comprobar todavía.</span>
          </div>
          <div class="ring pending" id="ajustes-ring-whatsapp"></div>
        </div>
      </div>
      <div style="margin-top:14px; display:flex; gap:8px; flex-wrap:wrap">
        <button class="btn-sm" id="ajustes-check-whatsapp-btn">Comprobar conexión</button>
        <button class="btn-sm" id="ajustes-reconnect-whatsapp-btn" style="display:none">Reconectar</button>
      </div>
    </div>

    <div class="card">
      <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 3h18v18H3z"/></svg> Conexión de Qwen</h2>
      <p class="card-desc">Proveedor alternativo para generar clips. Si aparece "Necesita reconectar", tocá el botón para iniciar sesión de nuevo.</p>
      <div class="status-rows">
        <div class="status-row" id="ajustes-row-qwen">
          <div class="status-row-text">
            <strong>Qwen</strong>
            <span id="ajustes-msg-qwen">Sin comprobar todavía.</span>
          </div>
          <div class="ring pending" id="ajustes-ring-qwen"></div>
        </div>
      </div>
      <div style="margin-top:14px; display:flex; gap:8px; flex-wrap:wrap">
        <button class="btn-sm" id="ajustes-check-qwen-btn">Comprobar conexión</button>
        <button class="btn-sm" id="ajustes-reconnect-qwen-btn" style="display:none">Reconectar</button>
      </div>
    </div>

    <div class="card">
      <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 3h18v18H3z"/></svg> Conexión de Qwen (Generación en lote)</h2>
      <p class="card-desc">Sesión aparte, solo para el módulo de "Generación en lote" (independiente de la sesión de Qwen normal). Si aparece "Necesita reconectar", tocá el botón para iniciar sesión de nuevo.</p>
      <div class="status-rows">
        <div class="status-row" id="ajustes-row-qwen_batch">
          <div class="status-row-text">
            <strong>Qwen (lote)</strong>
            <span id="ajustes-msg-qwen_batch">Sin comprobar todavía.</span>
          </div>
          <div class="ring pending" id="ajustes-ring-qwen_batch"></div>
        </div>
      </div>
      <div style="margin-top:14px; display:flex; gap:8px; flex-wrap:wrap">
        <button class="btn-sm" id="ajustes-check-qwen_batch-btn">Comprobar conexión</button>
        <button class="btn-sm" id="ajustes-reconnect-qwen_batch-btn" style="display:none">Reconectar</button>
      </div>
    </div>

    <div class="card">
      <h2>🌐 URL pública (Cloudflare Tunnel)</h2>
      <p class="card-desc">
        La app lanza <code>cloudflared</code> sola al arrancar (Quick Tunnel) y
        detecta la URL pública automáticamente — no hace falta correr nada a
        mano ni pegarla acá. El campo de abajo se completa solo y sirve
        como respaldo/override manual: si preferís usar un túnel con dominio
        propio en vez del automático, pegalo acá y se usa ese en su lugar.
        Recordá que la URL automática es efímera (cambia con cada reinicio del
        servidor) — eso es normal.
        <br><br>
        Si en la consola del servidor ves un aviso de "cloudflared no está
        instalado", instalalo una vez con
        <code>winget install --id Cloudflare.cloudflared</code> y reiniciá la app.
        Todo el dashboard (no solo esta imagen) queda accesible desde esa URL,
        protegido con el usuario/clave de <code>DASHBOARD_USER</code> /
        <code>DASHBOARD_PASSWORD</code> del <code>.env</code>.
      </p>
      <div class="field-label-row">
        <label for="ajustes-public-url">URL del túnel (automática o manual)</label>
      </div>
      <input type="text" id="ajustes-public-url" placeholder="https://algo.trycloudflare.com">
      <button class="btn-sm" id="ajustes-save-public-url-btn" style="margin-top:10px">Guardar</button>
      <div id="ajustes-public-url-status" class="pub-status"></div>
    </div>

  </div> <!-- /tab-ajustes -->

  <div id="app-modal-backdrop" class="qr-modal-backdrop" style="display:none">
    <div class="qr-modal">
      <h3 id="app-modal-title"></h3>
      <p class="qr-modal-hint" id="app-modal-message"></p>
      <div id="app-modal-actions" class="app-modal-actions"></div>
    </div>
  </div>

  <div id="qr-modal-backdrop" class="qr-modal-backdrop" style="display:none">
    <div class="qr-modal">
      <button id="qr-modal-close" class="qr-modal-close" aria-label="Cerrar">&times;</button>
      <h3 id="qr-modal-title">Escaneá el código QR</h3>
      <img id="qr-modal-img" alt="Código QR" />
      <p class="qr-modal-hint" id="qr-modal-hint">WhatsApp → Menú → Dispositivos vinculados → Vincular un dispositivo, y escaneá esto con la cámara del celular.</p>
      <a id="qr-modal-devtools-link" href="#" target="_blank" rel="noopener" class="btn-primary" style="display:none; text-align:center; text-decoration:none;">Abrir sesión para loguearme</a>
    </div>
  </div>

</main>

<script>
const $ = id => document.getElementById(id);
let currentVideoPath = null;
let generatedThumbnail = null;

// ── Modal genérico (reemplaza confirm()/alert() nativos) ──
function showAppModal({ title, message, buttons }) {
  return new Promise((resolve) => {
    $("app-modal-title").textContent = title;
    $("app-modal-message").textContent = message;
    const actions = $("app-modal-actions");
    actions.innerHTML = "";
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      $("app-modal-backdrop").style.display = "none";
      resolve(value);
    };
    buttons.forEach(({ label, value, primary }) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = primary ? "btn btn-primary" : "btn";
      btn.textContent = label;
      btn.addEventListener("click", () => finish(value));
      actions.appendChild(btn);
    });
    $("app-modal-backdrop").onclick = (e) => {
      if (e.target.id === "app-modal-backdrop") finish(buttons[0].value);
    };
    $("app-modal-backdrop").style.display = "flex";
  });
}
function appConfirm(message, { title = "Confirmar", confirmLabel = "Confirmar", cancelLabel = "Cancelar" } = {}) {
  return showAppModal({
    title,
    message,
    buttons: [{ label: cancelLabel, value: false }, { label: confirmLabel, value: true, primary: true }],
  });
}
function appAlert(message, title = "Aviso") {
  return showAppModal({ title, message, buttons: [{ label: "Aceptar", value: true, primary: true }] });
}

// ── Sidebar drawer (móvil) ──
const sidebar = $("sidebar");
const backdrop = $("sidebar-backdrop");
const menuToggle = $("menu-toggle");
function openSidebar() {
  sidebar.classList.add("open");
  backdrop.classList.add("visible");
  menuToggle.setAttribute("aria-expanded", "true");
}
function closeSidebar() {
  sidebar.classList.remove("open");
  backdrop.classList.remove("visible");
  menuToggle.setAttribute("aria-expanded", "false");
}
menuToggle.addEventListener("click", () => {
  sidebar.classList.contains("open") ? closeSidebar() : openSidebar();
});
backdrop.addEventListener("click", closeSidebar);

// ── Navegación por pestañas (tabs superiores + sidebar) ──
function activateTab(tab) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".nav-item[data-tab]").forEach(a => a.classList.toggle("active", a.dataset.tab === tab));
  $("tab-video").style.display = tab === "video" ? "" : "none";
  $("tab-historial").style.display = tab === "historial" ? "" : "none";
  $("tab-analytics").style.display = tab === "analytics" ? "" : "none";
  $("tab-ajustes").style.display = tab === "ajustes" ? "" : "none";
  $("tab-lote").style.display = tab === "lote" ? "" : "none";
  if (tab === "video") {
    checkYoutubeConnection();
    loadMetaPages().then(checkMetaToken);
  }
  if (tab === "historial") {
    loadMetaPages().then(checkMetaToken);
    loadHistorialVideos();
  }
  if (tab === "ajustes") {
    checkSessionStatus("whatsapp");
    checkSessionStatus("qwen");
    loadPublicUrl();
  }
  if (tab === "analytics") {
    loadFacebookAnalytics();
    loadYoutubeAnalytics();
    loadInstagramAnalytics();
    loadAnalyticsFeedback();
  }
  if (tab === "lote") {
    loadLotePages();
    loadLoteVoices();
    loadLoteSubtitlePresets();
    loadLoteProjects();
  }
  closeSidebar();
}
document.querySelectorAll(".tab-btn, .nav-item[data-tab]").forEach(el => {
  el.addEventListener("click", () => activateTab(el.dataset.tab));
});

// ── Historial: listar videos generados y elegir uno para publicar ──
async function loadHistorialVideos() {
  const grid = $("historial-grid");
  grid.textContent = "Cargando...";
  try {
    const res = await fetch("/api/videos");
    const files = await res.json();
    if (!files.length) {
      grid.textContent = "Todavía no generaste ningún video.";
      return;
    }
    grid.innerHTML = "";
    files.forEach(({ filename }) => {
      const item = document.createElement("div");
      item.className = "hist-item";
      item.innerHTML = `
        <video src="/video/${filename}" muted preload="metadata"></video>
        <div class="hist-item-body">
          <p class="hist-item-name">${filename}</p>
          <div style="display:flex;gap:6px">
            <button class="btn-sm" type="button" data-action="publish">📤 Publicar</button>
            <button class="btn-sm" type="button" data-action="delete">🗑️</button>
          </div>
        </div>
      `;
      item.querySelector('[data-action="publish"]').addEventListener("click", () => selectHistorialVideo(filename, item));
      item.querySelector('[data-action="delete"]').addEventListener("click", () => deleteHistorialVideo(filename, item));
      grid.appendChild(item);
    });
  } catch (e) {
    grid.textContent = "❌ Error al cargar los videos.";
  }
}
$("historial-refresh-btn").addEventListener("click", loadHistorialVideos);

async function deleteHistorialVideo(filename, item) {
  const ok = await appConfirm(
    `¿Eliminar "${filename}"? Se borra el video, el audio narrado, los clips fuente y todo registro asociado (generación y publicaciones). Esta acción no se puede deshacer.`,
    { title: "Eliminar video", confirmLabel: "Eliminar" }
  );
  if (!ok) return;

  // Soltar cualquier conexión de streaming abierta hacia ese archivo (miniatura
  // del historial y/o el reproductor del panel Publicar) antes de borrar, para
  // que el servidor no choque con el lock de archivo de Windows.
  const thumb = item.querySelector("video");
  if (thumb) { thumb.pause(); thumb.removeAttribute("src"); thumb.load(); }
  if (currentVideoPath === filename) {
    $("pipeline-player").pause();
    $("pipeline-player").removeAttribute("src");
    $("pipeline-player").load();
  }

  try {
    const res = await fetch(`/api/videos/${encodeURIComponent(filename)}`, { method: "DELETE" });
    const data = await res.json();
    if (!data.ok) {
      await appAlert(data.error || "No se pudo eliminar el video.", "Error");
      return;
    }
    if (currentVideoPath === filename) {
      currentVideoPath = null;
      $("pipeline-result").classList.remove("visible");
      $("historial-publish-hint").style.display = "";
    }
    item.remove();
  } catch (e) {
    await appAlert("Error de conexión con el servidor.", "Error");
  }
}

function selectHistorialVideo(filename, item) {
  document.querySelectorAll(".hist-item.selected").forEach(el => el.classList.remove("selected"));
  item.classList.add("selected");
  if (filename !== currentVideoPath) {
    // Video distinto al que estaba cargado: limpiar guion y sugerencias
    // viejas, si no _publishSourceText() devuelve el guion del video
    // anterior (cache en #pipeline-script) y las sugerencias de
    // caption/SEO quedan pegadas a esa publicación anterior.
    $("pipeline-script").value = "";
    $("pub-fb-description").value = "";
    $("pub-ig-description").value = "";
    $("yt-title").value = "";
    $("pub-yt-description").value = "";
    $("yt-tags").value = "";
    $("fb-caption-status").textContent = "";
    $("ig-caption-status").textContent = "";
    $("yt-seo-status").textContent = "";
    generatedThumbnail = null;
    $("yt-thumb-preview").style.display = "none";
    $("yt-thumb-variants").style.display = "none";
    $("yt-thumb-variants").innerHTML = "";
    $("fb-status").innerHTML = "";
    $("ig-status").innerHTML = "";
    $("yt-status").innerHTML = "";
  }
  currentVideoPath = filename;
  $("pipeline-player").src = `/video/${filename}`;
  $("historial-publish-hint").style.display = "none";
  $("historial-publish-slot").appendChild($("pipeline-result"));
  $("pipeline-result").classList.add("visible");
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Contador de caracteres del guion / prompts ──
const MAX_CHARS = 5000;
function _makeCharCounter(textareaId, countId) {
  const update = () => {
    const len = $(textareaId).value.length;
    const el = $(countId);
    el.textContent = `${len} / ${MAX_CHARS}`;
    el.classList.toggle("near-limit", len > MAX_CHARS);
  };
  $(textareaId).addEventListener("input", update);
  update();
}
_makeCharCounter("pipeline-script", "pipeline-script-char-count");
_makeCharCounter("pipeline-prompts", "pipeline-prompts-char-count");

// ── Pipeline unificado: guion + clips + video en un solo botón ──
let pipelineVoice = "{default_voice}";

async function loadPipelineVoices() {
  const res = await fetch("/api/voices");
  const data = await res.json();
  const grid = $("pipeline-voice-grid");
  grid.innerHTML = "";
  for (const [key, info] of Object.entries(data)) {
    const card = document.createElement("div");
    card.className = "voice-card" + (key === pipelineVoice ? " active" : "");
    card.dataset.voice = key;
    card.tabIndex = 0;
    card.innerHTML = `
      <div class="name">${info.label}</div>
      <div class="status${info.ready ? " ready" : ""}">${info.ready ? "✓ Lista" : "↓ Se genera al usar"}</div>
      ${info.gentle ? '<span class="gentle-badge">Suave</span>' : ""}
    `;
    const select = () => {
      grid.querySelectorAll(".voice-card").forEach(c => c.classList.remove("active"));
      card.classList.add("active");
      pipelineVoice = key;
    };
    card.addEventListener("click", select);
    card.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(); } });
    grid.appendChild(card);
  }
}
loadPipelineVoices();

const FONT_CSS_MAP = {
  cinzel: "'Cinzel', serif",
  playfair: "'Playfair Display', serif",
  poppins: "'Poppins', sans-serif",
  montserrat: "'Montserrat', sans-serif",
  bebas: "'Bebas Neue', sans-serif",
  anton: "'Anton', sans-serif",
  bangers: "'Bangers', cursive",
};
let pipelineSubtitlePreset = "clasico";

// Tamaño fijo de la fuente en la tarjeta de preview (ver CSS
// .subtitle-preset-card .preview span). El contorno se escala en
// proporción a este tamaño vs. el fontSize real de cada preset, igual que
// hace el repo de referencia (nicolaigaina/ai-video-captions) con su
// PREVIEW_SCALE.
const PREVIEW_FONT_SIZE = 24;

// Contorno vía 4 text-shadow diagonales (misma técnica que Subtitles.tsx):
// evita el manchado/fleco de color que produce -webkit-text-stroke en
// texto chico.
function buildStrokeShadow(color, width) {
  return [
    `${width}px ${width}px 0 ${color}`,
    `-${width}px -${width}px 0 ${color}`,
    `${width}px -${width}px 0 ${color}`,
    `-${width}px ${width}px 0 ${color}`,
  ].join(", ");
}

async function loadSubtitlePresets() {
  const res = await fetch("/api/subtitle-presets");
  const presets = await res.json();
  const grid = $("pipeline-subtitle-preset-grid");
  grid.innerHTML = "";
  for (const p of presets) {
    const card = document.createElement("div");
    card.className = "subtitle-preset-card" + (p.id === pipelineSubtitlePreset ? " active" : "");
    card.dataset.preset = p.id;
    card.tabIndex = 0;
    const span = document.createElement("span");
    span.textContent = p.uppercase ? "PALABRA" : "palabra";
    const previewStrokeWidth = p.strokeColor
      ? Math.max(0.5, (p.strokeWidth || 2) * (PREVIEW_FONT_SIZE / p.fontSize))
      : 0;
    const contrastShadow = "0 1px 4px rgba(0,0,0,0.9)";
    Object.assign(span.style, {
      fontFamily: FONT_CSS_MAP[p.fontFamily] || "inherit",
      color: p.highlightColor,
      textTransform: p.uppercase ? "uppercase" : "none",
      background: p.background ? "rgba(0,0,0,0.55)" : "transparent",
      fontStyle: p.italic ? "italic" : "normal",
      letterSpacing: p.letterSpacing ? `${p.letterSpacing}px` : "normal",
      textShadow: p.strokeColor
        ? `${buildStrokeShadow(p.strokeColor, previewStrokeWidth)}, ${contrastShadow}`
        : contrastShadow,
    });
    const preview = document.createElement("div");
    preview.className = "preview";
    preview.appendChild(span);
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = p.label;
    card.append(preview, name);
    const select = () => {
      grid.querySelectorAll(".subtitle-preset-card").forEach(c => c.classList.remove("active"));
      card.classList.add("active");
      pipelineSubtitlePreset = p.id;
    };
    card.addEventListener("click", select);
    card.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(); } });
    grid.appendChild(card);
  }
}
loadSubtitlePresets();

function renderPipelinePanel(stage, textId, ringId, rowId) {
  const el = $(textId);
  const ring = $(ringId);
  const row = $(rowId);
  row.classList.remove("is-error", "is-done");
  const restartBtn = rowId === "pipeline-row-clipgen" ? $("pipeline-clipgen-restart-btn") : null;
  if (restartBtn) restartBtn.style.display = "none";
  if (!stage || stage.status === "pending") {
    el.textContent = "—";
    ring.className = "ring pending";
    return;
  }
  if (stage.status === "running") {
    const pct = typeof stage.percent === "number" ? ` (${stage.percent}%)` : "";
    el.textContent = `${stage.message || "En proceso..."}${pct}`;
    if (typeof stage.percent === "number") {
      ring.className = "ring";
      ring.style.setProperty("--pct", stage.percent);
    } else {
      ring.className = "ring indeterminate";
    }
  } else if (stage.status === "done") {
    el.textContent = "Listo";
    ring.className = "ring done";
    row.classList.add("is-done");
  } else if (stage.status === "error") {
    el.textContent = stage.error || "Error";
    ring.className = "ring error";
    row.classList.add("is-error");
    if (restartBtn) restartBtn.style.display = "";
  }
}

$("pipeline-clipgen-restart-btn").addEventListener("click", async (e) => {
  e.stopPropagation();
  const btn = e.currentTarget;
  btn.disabled = true;
  let elapsed = 0;
  btn.textContent = "Reiniciando... (0s)";
  const tick = setInterval(() => {
    elapsed += 1;
    btn.textContent = `Reiniciando... (${elapsed}s)`;
  }, 1000);
  try {
    const res = await fetch("/api/clipgen/restart-browser", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: $("pipeline-provider").value }),
    });
    const data = await res.json();
    const state = data.status && data.status.state;
    if (state === "ok") {
      await appAlert("Sesión reiniciada y activa de nuevo. Ya podés reintentar la generación.", "Listo");
    } else if (state === "needs_login") {
      await appAlert("Sesión reiniciada, pero hace falta volver a iniciar sesión (escanear QR / loguearse) antes de reintentar.", "Falta iniciar sesión");
    } else {
      await appAlert("Se forzó el cierre del navegador colgado, pero todavía no responde. Esperá unos segundos y volvé a intentar — el próximo uso relanza la sesión desde cero.", "Sigue sin responder");
    }
  } catch (e) {
    await appAlert("No se pudo reiniciar la sesión: " + e.message, "Error");
  } finally {
    clearInterval(tick);
    btn.disabled = false;
    btn.textContent = "⟳ Reiniciar";
  }
});

let pipelinePollTimer = null;
let lastPipelineJobId = null;

function setLastPipelineJobId(jobId) {
  lastPipelineJobId = jobId;
  try {
    if (jobId) localStorage.setItem("lastPipelineJobId", jobId);
    else localStorage.removeItem("lastPipelineJobId");
  } catch (e) {}
}

async function rehydratePipelineJob() {
  let jobId = null;
  try { jobId = localStorage.getItem("lastPipelineJobId"); } catch (e) {}
  if (!jobId) return;
  try {
    const res = await fetch(`/api/pipeline/status/${jobId}`);
    const data = await res.json();
    if (!data.ok || data.status === "done") { setLastPipelineJobId(null); return; }
    setLastPipelineJobId(jobId);
    $("pipeline-panels").style.display = "";
    await pollPipelineStatus(jobId);
    if (data.status === "running") {
      if (pipelinePollTimer) clearInterval(pipelinePollTimer);
      pipelinePollTimer = setInterval(() => pollPipelineStatus(jobId), 3000);
    }
  } catch (e) {}
}
rehydratePipelineJob();

async function pollPipelineStatus(jobId) {
  try {
    const res = await fetch(`/api/pipeline/status/${jobId}`);
    const data = await res.json();
    if (!data.ok) {
      clearInterval(pipelinePollTimer);
      $("pipeline-start-btn").disabled = false;
      alert(
        "Se perdió la conexión con el trabajo en curso (el servidor se reinició). " +
        "El progreso mostrado puede estar desactualizado — revisá la consola del servidor " +
        "o volvé a apretar \"Generar video\"."
      );
      return;
    }

    renderPipelinePanel(data.story.tts, "pipeline-panel-tts", "pipeline-ring-tts", "pipeline-row-tts");
    renderPipelinePanel(data.story.clipgen, "pipeline-panel-clipgen", "pipeline-ring-clipgen", "pipeline-row-clipgen");
    renderPipelinePanel(data.story.video, "pipeline-panel-video", "pipeline-ring-video", "pipeline-row-video");

    $("pipeline-cancel-btn").style.display =
      (data.status === "running" || data.status === "error") ? "" : "none";

    if (data.status === "done") {
      clearInterval(pipelinePollTimer);
      $("pipeline-start-btn").disabled = false;
      $("pipeline-retry-btn").style.display = "none";
      $("pipeline-cancel-btn").style.display = "none";
      setLastPipelineJobId(null);
      if (data.story.video.video_name) {
        $("video-result-home").appendChild($("pipeline-result"));
        $("pipeline-result").classList.add("visible");
        $("pipeline-player").src = `/video/${data.story.video.video_name}`;
        currentVideoPath = data.story.video.video_name;
        generatedThumbnail = null;
        $("pub-fb-description").value = "";
        $("pub-ig-description").value = "";
        $("pub-yt-description").value = "";
        $("yt-title").value = "";
        $("fb-status").innerHTML = "";
        $("ig-status").innerHTML = "";
        $("yt-status").innerHTML = "";
        checkYoutubeConnection();
        _autoSuggestSeo();
      }
    } else if (data.status === "error") {
      clearInterval(pipelinePollTimer);
      $("pipeline-start-btn").disabled = false;
      $("pipeline-retry-btn").style.display = "";
    }
  } catch (e) {
    clearInterval(pipelinePollTimer);
  }
}

$("pipeline-cancel-btn").addEventListener("click", async () => {
  if (!lastPipelineJobId) return;
  const ok = await appConfirm(
    "Esto borra el audio, los clips y el video generados en este intento. " +
    "No se puede deshacer.",
    { title: "¿Cancelar y borrar todo?", confirmLabel: "Cancelar y borrar", cancelLabel: "Seguir" }
  );
  if (!ok) return;
  const btn = $("pipeline-cancel-btn");
  btn.disabled = true;
  btn.textContent = "Cancelando...";
  try {
    const res = await fetch(`/api/pipeline/discard/${lastPipelineJobId}`, { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      await appAlert(data.error || "No se pudo cancelar.", "Error");
    } else {
      clearInterval(pipelinePollTimer);
      setLastPipelineJobId(null);
      $("pipeline-panels").style.display = "none";
      $("pipeline-retry-btn").style.display = "none";
      $("pipeline-start-btn").disabled = false;
    }
  } catch (e) {
    await appAlert("No se pudo cancelar: " + e.message, "Error");
  } finally {
    btn.disabled = false;
    btn.textContent = "✕ Cancelar (borra audio, clips y video de este intento)";
    btn.style.display = "none";
  }
});

$("pipeline-retry-btn").addEventListener("click", async () => {
  if (!lastPipelineJobId) return;
  $("pipeline-retry-btn").disabled = true;
  $("pipeline-retry-btn").style.display = "none";
  $("pipeline-start-btn").disabled = true;

  try {
    const res = await fetch(`/api/pipeline/retry/${lastPipelineJobId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: $("pipeline-provider").value }),
    });
    const data = await res.json();
    if (!data.ok) {
      alert(data.error || "No se pudo reintentar.");
      $("pipeline-start-btn").disabled = false;
      $("pipeline-retry-btn").disabled = false;
      $("pipeline-retry-btn").style.display = "";
      return;
    }
    setLastPipelineJobId(data.job_id);
    if (pipelinePollTimer) clearInterval(pipelinePollTimer);
    pipelinePollTimer = setInterval(() => pollPipelineStatus(data.job_id), 3000);
  } catch (e) {
    alert("No se pudo reintentar.");
    $("pipeline-start-btn").disabled = false;
  }
  $("pipeline-retry-btn").disabled = false;
});

$("pipeline-start-btn").addEventListener("click", async () => {
  const scriptText = $("pipeline-script").value.trim();
  const promptsText = $("pipeline-prompts").value.trim();
  if (!scriptText || !promptsText) { alert("Pegá el guion y los prompts de imagen antes de generar."); return; }
  const storyText = `${scriptText}\n\n${promptsText}`;

  const previewRes = await fetch("/api/pipeline/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ story_text: storyText }),
  });
  const preview = await previewRes.json();
  if (!preview.ok) {
    await appAlert(preview.error || "No se pudo interpretar el guion.", "Revisá el formato");
    return;
  }
  const resumen = preview.script_preview.length > 300
    ? preview.script_preview.slice(0, 300) + "…"
    : preview.script_preview;
  const confirmado = await appConfirm(
    `Guion (${preview.script_len} caracteres):\n\n${resumen}\n\n${preview.prompt_count} imágenes detectadas.`,
    { title: "Revisá antes de generar", confirmLabel: "Generar video", cancelLabel: "Volver a editar" }
  );
  if (!confirmado) return;

  $("pipeline-start-btn").disabled = true;
  $("pipeline-retry-btn").style.display = "none";
  $("pipeline-cancel-btn").style.display = "none";
  $("pipeline-panels").style.display = "";
  $("pipeline-result").classList.remove("visible");
  renderPipelinePanel(null, "pipeline-panel-tts", "pipeline-ring-tts", "pipeline-row-tts");
  renderPipelinePanel(null, "pipeline-panel-clipgen", "pipeline-ring-clipgen", "pipeline-row-clipgen");
  renderPipelinePanel(null, "pipeline-panel-video", "pipeline-ring-video", "pipeline-row-video");

  try {
    const res = await fetch("/api/pipeline/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        story_text: storyText,
        script_text: scriptText,
        voice: pipelineVoice,
        provider: $("pipeline-provider").value,
        orientation: $("pipeline-orientation").value,
        subtitles_enabled: $("pipeline-subtitles-enabled").checked,
        subtitle_preset: pipelineSubtitlePreset,
        animate_images: $("pipeline-animate-images").checked,
        generate_video_clips: $("pipeline-generate-video-clips").checked,
        duration_seconds: $("pipeline-duration").value ? parseInt($("pipeline-duration").value, 10) : null,
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      alert(data.error);
      $("pipeline-start-btn").disabled = false;
      return;
    }
    setLastPipelineJobId(data.job_id);
    if (pipelinePollTimer) clearInterval(pipelinePollTimer);
    pipelinePollTimer = setInterval(() => pollPipelineStatus(data.job_id), 3000);
  } catch (e) {
    alert("No se pudo iniciar el pipeline.");
    $("pipeline-start-btn").disabled = false;
  }
});

function _escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function _fmtNum(n) {
  return typeof n === "number" ? n.toLocaleString("es") : "-";
}

async function _loadAnalytics(tableId, url, emptyMsg) {
  const wrap = $(tableId);
  wrap.textContent = "Cargando...";
  try {
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok || !data.videos.length) {
      wrap.innerHTML = `<div class="analytics-empty">${_escapeHtml(emptyMsg)}</div>`;
      return;
    }
    const videos = [...data.videos].sort((a, b) => (b.views ?? -1) - (a.views ?? -1));
    const viewsList = videos.map(v => v.views).filter(v => typeof v === "number");
    const avgViews = viewsList.length ? Math.round(viewsList.reduce((a, b) => a + b, 0) / viewsList.length) : null;
    const bestId = viewsList.length ? videos[0] : null;

    const summary = `
      <div class="tag-row">
        <span class="tag"><strong>${videos.length}</strong> publicados</span>
        ${avgViews !== null ? `<span class="tag">👁️ promedio <strong>${_fmtNum(avgViews)}</strong> vistas</span>` : ""}
      </div>`;

    const rows = videos.map(v => `
      <tr class="${v === bestId ? "table-best-row" : ""}">
        <td>${v === bestId ? "🏆 " : ""}${_escapeHtml(v.title || v.video_id || v.media_id)}</td>
        <td>${_fmtNum(v.views)}</td>
        <td>${_fmtNum(v.likes)}</td>
        <td>${_fmtNum(v.comments)}</td>
      </tr>`).join("");
    wrap.innerHTML = `
      ${summary}
      <div style="overflow-x:auto">
      <table>
        <thead><tr>
          <th>Título</th><th>Vistas</th><th>Likes</th><th>Comentarios</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      </div>`;
  } catch (e) {
    wrap.innerHTML = `<div class="analytics-empty">❌ Error de conexión con el servidor.</div>`;
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
      el.innerHTML = `<div class="analytics-empty">❌ ${_escapeHtml(data.error || "No se pudo cargar.")}</div>`;
      return;
    }
    if (data.insufficient_data) {
      const pct = Math.min(100, Math.round((data.sample_size / 3) * 100));
      el.innerHTML = `
        <div class="analytics-empty">
          Todavía no hay suficientes videos publicados con estadísticas
          (${data.sample_size}/3 mínimo).
          <div class="progress-bar"><div class="progress-bar-fill" style="width:${pct}%"></div></div>
        </div>`;
      return;
    }
    const keywordTags = data.top_keywords.length
      ? data.top_keywords.map(k => `<span class="tag">${_escapeHtml(k)}</span>`).join("")
      : `<span class="tag">—</span>`;
    el.innerHTML = `
      <div class="stat-grid">
        <div class="stat-card">
          <div class="stat-value">${_fmtNum(data.avg_views)}</div>
          <div class="stat-label">👁️ Promedio de vistas (${data.sample_size} videos)</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${_escapeHtml(data.best_title_length)}</div>
          <div class="stat-label">📏 Mejor largo de título</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">🔑 Palabras clave con más vistas</div>
          <div class="stat-keywords">${keywordTags}</div>
        </div>
      </div>
    `;
  } catch (e) {
    el.innerHTML = `<div class="analytics-empty">❌ Error de conexión con el servidor.</div>`;
  }
}
$("feedback-analytics-refresh-btn").addEventListener("click", loadAnalyticsFeedback);

// ── Generación en lote ──
async function loadLotePages() {
  try {
    const res = await fetch("/api/meta/pages");
    const data = await res.json();
    const pages = (data.ok && data.pages) || [];
    const sel = $("lote-page");
    sel.innerHTML = pages.map(p => `<option value="${p.page_id}">${p.name}</option>`).join("");
    $("lote-page-wrap").style.display = pages.length > 1 ? "" : "none";
  } catch (e) { console.error("No se pudieron cargar las páginas de Facebook/Instagram", e); }
}

async function loadLoteVoices() {
  try {
    const res = await fetch("/api/voices");
    const data = await res.json();
    const sel = $("lote-voice");
    sel.innerHTML = Object.entries(data).map(([key, info]) => `<option value="${key}">${info.label}</option>`).join("");
  } catch (e) { console.error("No se pudieron cargar las voces", e); }
}

async function loadLoteSubtitlePresets() {
  try {
    const res = await fetch("/api/subtitle-presets");
    const presets = await res.json();
    const sel = $("lote-subtitle-preset");
    sel.innerHTML = presets.map(p => `<option value="${p.id}">${p.label}</option>`).join("");
  } catch (e) { console.error("No se pudieron cargar los presets de subtítulos", e); }
}

function _loteStatusLabel(status) {
  return {
    running: "▶️ Corriendo", paused: "⏸️ Pausado", done: "✅ Terminado", cancelled: "🚫 Cancelado",
  }[status] || status;
}

function _loteVideoSummary(project) {
  const videos = project.videos || [];
  const generated = videos.filter(v => v.status !== "pending" && v.status !== "generating").length;
  const published = videos.filter(v => v.status === "published").length;
  const next = videos.find(v => v.status === "ready" || v.status === "pending");
  const errores = videos.filter(v => v.status === "error" || v.status === "publish_error").length;
  let next_txt = "—";
  if (next) {
    try { next_txt = new Date(next.scheduled_at).toLocaleString(); } catch (e) { next_txt = next.scheduled_at; }
  }
  let extra = errores ? ` — ⚠️ ${errores} con error` : "";
  return `${generated}/${videos.length} generados · ${published}/${videos.length} publicados · próximo: ${next_txt}${extra}`;
}

const _LOTE_STAGE_INFO = {
  guion: { label: "Escribiendo guion (Qwen)", pct: 10 },
  imagenes: { label: "Generando imágenes/clips", pct: 35 },
  audio: { label: "Generando audio (TTS)", pct: 65 },
  render: { label: "Renderizando video", pct: 85 },
  idea: { label: "Generando idea y prompt de imagen (Qwen)", pct: 25 },
  imagen: { label: "Generando imagen de portada (Qwen)", pct: 70 },
};

function _loteDate(iso) {
  try { return new Date(iso).toLocaleString(); } catch (e) { return iso || "—"; }
}

function _loteVideoRowHtml(v, projectId) {
  const n = v.index + 1;
  if (v.status === "generating") {
    const info = _LOTE_STAGE_INFO[v.stage] || { label: "Generando...", pct: 5 };
    return `
      <div class="lote-video-row">
        <div>#${n} — ⚙️ ${_escapeHtml(info.label)}</div>
        <div class="progress-bar"><div class="progress-bar-fill" style="width:${info.pct}%"></div></div>
      </div>`;
  }
  if (v.status === "publishing") {
    return `<div class="lote-video-row">#${n} — 📤 Publicando...</div>`;
  }
  if (v.status === "error") {
    const intentos = v.gen_attempts || 0;
    return `
      <div class="lote-video-row lote-video-row-line">
        <div>#${n} — ❌ Error tras ${intentos} intentos automáticos: ${_escapeHtml(v.error || "desconocido")}</div>
        <button class="btn-sm" data-lote-retry="${projectId}" data-lote-retry-index="${v.index}">Reintentar</button>
      </div>`;
  }
  if (v.status === "publish_error") {
    const redesListas = Object.keys(v.published_at || {}).filter(k => v.published_at[k]);
    const parcial = redesListas.length ? ` (ya publicado en ${_escapeHtml(redesListas.join(", "))})` : "";
    const authMsg = v.last_publish_auth_error
      ? ` — el token de esa red venció o perdió permisos, renovalo en Ajustes antes de reintentar`
      : "";
    return `
      <div class="lote-video-row lote-video-row-line">
        <div>#${n} — ❌ Error publicando: ${_escapeHtml(v.error || "desconocido")}${parcial}${authMsg}</div>
        <button class="btn-sm" data-lote-retry-publish="${projectId}" data-lote-retry-index="${v.index}">Reintentar publicación</button>
      </div>`;
  }
  const captionLine = v.caption
    ? `<div style="font-size:12px;color:var(--text-secondary);margin-top:2px">${_escapeHtml(v.caption)}</div>` : "";
  if (v.status === "published") {
    const redes = Object.keys(v.published_at || {}).filter(k => v.published_at[k]).join(", ") || "—";
    return `<div class="lote-video-row">#${n} — ✅ Publicado (${_escapeHtml(redes)})${captionLine}</div>`;
  }
  if (v.status === "ready") {
    const redesListas = Object.keys(v.published_at || {}).filter(k => v.published_at[k]);
    const parcial = redesListas.length ? ` — ya publicado en ${_escapeHtml(redesListas.join(", "))}, falta el resto` : "";
    if (v.publish_attempts > 0) {
      return `<div class="lote-video-row">#${n} — 🔄 Reintentando publicación automáticamente (intento ${v.publish_attempts}/3)${parcial}${captionLine}</div>`;
    }
    return `<div class="lote-video-row">#${n} — 🟡 Listo, espera publicación (${_loteDate(v.scheduled_at)})${parcial}${captionLine}</div>`;
  }
  if (v.gen_attempts > 0) {
    return `<div class="lote-video-row">#${n} — 🔄 Reintentando generación automáticamente (intento ${v.gen_attempts}/3)</div>`;
  }
  return `<div class="lote-video-row">#${n} — ⏳ Pendiente (programado ${_loteDate(v.scheduled_at)})</div>`;
}

function _loteVideoRows(project) {
  const videos = project.videos || [];
  if (!videos.length) return "";
  return `<div class="lote-video-list">${videos.map(v => _loteVideoRowHtml(v, project.id)).join("")}</div>`;
}

async function loadLoteProjects() {
  const wrap = $("lote-list");
  try {
    const res = await fetch("/api/batch/list");
    const data = await res.json();
    const projects = (data.ok && data.projects) || [];
    if (!projects.length) {
      wrap.innerHTML = `<div class="analytics-empty">Todavía no creaste ningún proyecto de lote.</div>`;
      return;
    }
    wrap.innerHTML = projects.map(p => `
      <div class="card" style="margin-top:10px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
          <div>
            <strong>${_escapeHtml(p.name)}</strong> — ${_loteStatusLabel(p.status)}
            <div style="font-size:13px;color:var(--text-secondary);margin-top:4px">${_loteVideoSummary(p)}</div>
          </div>
          <div style="display:flex;gap:6px;flex-shrink:0">
            ${p.status === "running" ? `<button class="btn-sm" data-lote-pause="${p.id}">Pausar</button>` : ""}
            ${p.status === "paused" ? `<button class="btn-sm" data-lote-resume="${p.id}">Reanudar</button>` : ""}
            ${(p.status === "running" || p.status === "paused") ? `<button class="btn-sm" data-lote-cancel="${p.id}">Cancelar</button>` : ""}
            ${(p.status === "cancelled" || p.status === "done") ? `<button class="btn-sm" data-lote-delete="${p.id}">Eliminar</button>` : ""}
          </div>
        </div>
        ${_loteVideoRows(p)}
      </div>`).join("");
    wrap.querySelectorAll("[data-lote-pause]").forEach(btn =>
      btn.addEventListener("click", () => loteProjectAction(btn.dataset.lotePause, "pause")));
    wrap.querySelectorAll("[data-lote-resume]").forEach(btn =>
      btn.addEventListener("click", () => loteProjectAction(btn.dataset.loteResume, "resume")));
    wrap.querySelectorAll("[data-lote-cancel]").forEach(btn =>
      btn.addEventListener("click", () => loteProjectAction(btn.dataset.loteCancel, "cancel")));
    wrap.querySelectorAll("[data-lote-delete]").forEach(btn =>
      btn.addEventListener("click", () => {
        if (confirm("¿Eliminar este proyecto de lote? No se borran los videos ya publicados.")) {
          loteProjectAction(btn.dataset.loteDelete, "delete");
        }
      }));
    wrap.querySelectorAll("[data-lote-retry]").forEach(btn =>
      btn.addEventListener("click", async () => {
        try {
          await fetch(`/api/batch/retry/${btn.dataset.loteRetry}/${btn.dataset.loteRetryIndex}`, { method: "POST" });
        } catch (e) {}
        loadLoteProjects();
      }));
    wrap.querySelectorAll("[data-lote-retry-publish]").forEach(btn =>
      btn.addEventListener("click", async () => {
        try {
          await fetch(`/api/batch/retry-publish/${btn.dataset.loteRetryPublish}/${btn.dataset.loteRetryIndex}`, { method: "POST" });
        } catch (e) {}
        loadLoteProjects();
      }));

    const anyRunning = projects.some(p => p.status === "running");
    if (anyRunning && !lotePollTimer) {
      lotePollTimer = setInterval(loadLoteProjects, 5000);
    } else if (!anyRunning && lotePollTimer) {
      clearInterval(lotePollTimer);
      lotePollTimer = null;
    }
  } catch (e) {
    wrap.innerHTML = `<div class="analytics-empty">❌ Error de conexión con el servidor.</div>`;
  }
}
let lotePollTimer = null;

async function loteProjectAction(projectId, action) {
  try {
    await fetch(`/api/batch/${action}/${projectId}`, { method: "POST" });
  } catch (e) {}
  loadLoteProjects();
}

$("lote-fb").addEventListener("change", () => { $("lote-page-wrap").style.display = ($("lote-fb").checked || $("lote-ig").checked) ? "" : "none"; });
$("lote-ig").addEventListener("change", () => { $("lote-page-wrap").style.display = ($("lote-fb").checked || $("lote-ig").checked) ? "" : "none"; });
$("lote-refresh-btn").addEventListener("click", loadLoteProjects);

function updateDurationOptions(orientationId, durationId) {
  const isHorizontal = $(orientationId).value === "horizontal";
  const durationSelect = $(durationId);
  durationSelect.querySelectorAll(".duration-long-option").forEach((opt) => {
    opt.hidden = !isHorizontal;
    opt.disabled = !isHorizontal;
  });
  const selected = durationSelect.querySelector(`option[value="${durationSelect.value}"]`);
  if (!isHorizontal && selected && selected.classList.contains("duration-long-option")) {
    durationSelect.value = "";
  }
}
$("pipeline-orientation").addEventListener("change", () => updateDurationOptions("pipeline-orientation", "pipeline-duration"));
$("lote-orientation").addEventListener("change", () => updateDurationOptions("lote-orientation", "lote-duration"));
updateDurationOptions("pipeline-orientation", "pipeline-duration");
updateDurationOptions("lote-orientation", "lote-duration");

function updateLoteTypeVisibility() {
  const isGaming = $("lote-type").value === "gaming_image";
  $("lote-video-only-fields").style.display = isGaming ? "none" : "";
  $("lote-gaming-only-fields").style.display = isGaming ? "" : "none";
  $("lote-yt").checked = isGaming ? false : $("lote-yt").checked;
  $("lote-yt").closest("label").style.display = isGaming ? "none" : "";
  $("lote-trigger-message").placeholder = isGaming ? "dame el próximo post gaming" : "dame una historia";
}
$("lote-type").addEventListener("change", updateLoteTypeVisibility);
updateLoteTypeVisibility();

$("lote-create-btn").addEventListener("click", async () => {
  const btn = $("lote-create-btn");
  const statusEl = $("lote-create-status");
  const name = $("lote-nombre").value.trim();
  const qwenProject = $("lote-qwen-project").value.trim();
  if (!name || !qwenProject) {
    statusEl.innerHTML = `<div class="analytics-empty">Completá el nombre del proyecto y el proyecto de Qwen.</div>`;
    return;
  }
  const contentType = $("lote-type").value;
  const isGaming = contentType === "gaming_image";
  const pageId = $("lote-page").value || null;
  const networks = {};
  if ($("lote-fb").checked) networks.facebook = { page_id: pageId };
  if ($("lote-ig").checked) networks.instagram = { page_id: pageId };
  if (!isGaming && $("lote-yt").checked) networks.youtube = true;
  if (!Object.keys(networks).length) {
    statusEl.innerHTML = `<div class="analytics-empty">Elegí al menos una red social.</div>`;
    return;
  }
  const payload = {
    name, qwen_project: qwenProject, type: contentType,
    trigger_message: $("lote-trigger-message").value.trim()
      || (isGaming ? "dame el próximo post gaming" : "dame una historia"),
    total_videos: parseInt($("lote-total").value, 10) || 1,
    per_day: parseInt($("lote-per-day").value, 10) || 1,
    networks,
    video_settings: isGaming ? { image_provider: $("lote-image-provider").value } : {
      voice: $("lote-voice").value,
      subtitle_preset: $("lote-subtitle-preset").value,
      orientation: $("lote-orientation").value,
      provider: $("lote-provider").value,
      duration_seconds: $("lote-duration").value ? parseInt($("lote-duration").value, 10) : null,
      subtitles_enabled: $("lote-subtitles-enabled").checked,
      animate_images: $("lote-animate-images").checked,
      generate_video_clips: $("lote-generate-video-clips").checked,
    },
  };
  btn.disabled = true;
  statusEl.textContent = "Creando proyecto...";
  try {
    const res = await fetch("/api/batch/create", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.ok) {
      statusEl.innerHTML = `<div class="analytics-empty">❌ ${_escapeHtml(data.error || "No se pudo crear el proyecto.")}</div>`;
      return;
    }
    statusEl.innerHTML = `<div class="analytics-empty">✅ Proyecto "${_escapeHtml(name)}" creado.</div>`;
    $("lote-nombre").value = "";
    $("lote-qwen-project").value = "";
    $("lote-trigger-message").value = "";
    loadLoteProjects();
  } catch (e) {
    statusEl.innerHTML = `<div class="analytics-empty">❌ Error de conexión con el servidor.</div>`;
  } finally {
    btn.disabled = false;
  }
});

// ── Publicación en redes sociales ──
function updatePublishExtras() {
  $("pub-fb-extra").style.display = $("pub-fb").checked ? "block" : "none";
  $("pub-ig-extra").style.display = $("pub-ig").checked ? "block" : "none";
  $("pub-yt-extra").style.display = $("pub-yt").checked ? "block" : "none";
  $("fb-status").style.display = $("pub-fb").checked ? "flex" : "none";
  $("ig-status").style.display = $("pub-ig").checked ? "flex" : "none";
  $("yt-status").style.display = $("pub-yt").checked ? "flex" : "none";
}
$("pub-fb").addEventListener("change", updatePublishExtras);
$("pub-ig").addEventListener("change", updatePublishExtras);
$("pub-yt").addEventListener("change", () => { updatePublishExtras(); checkYoutubeConnection(); });
$("pub-page").addEventListener("change", checkMetaToken);
updatePublishExtras();

$("pub-schedule").addEventListener("change", () => {
  $("pub-schedule-wrap").style.display = $("pub-schedule").checked ? "block" : "none";
});

function getScheduledTimeIso() {
  if (!$("pub-schedule").checked) return null;
  return $("pub-schedule-time").value || null;
}

function _nextDatetimeLocalAtHour(hour) {
  const clampedHour = Math.min(20, Math.max(9, hour)); // nunca fuera de 9:00-20:00
  const now = new Date();
  const target = new Date(now.getFullYear(), now.getMonth(), now.getDate(), clampedHour, 0, 0);
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

$("fb-auto-time").addEventListener("change", () => {
  if ($("fb-auto-time").checked) loadBestTimeHint("fb-best-time-hint", "/api/facebook/best-time");
  else $("fb-best-time-hint").textContent = "";
});
$("ig-auto-time").addEventListener("change", () => {
  if ($("ig-auto-time").checked) loadBestTimeHint("ig-best-time-hint", "/api/instagram/best-time");
  else $("ig-best-time-hint").textContent = "";
});

function _showPubStatus(elId, type, msg, retryFn, authError) {
  const el = $(elId);
  el.className = "pub-status " + type;
  const text = _escapeHtml(msg);
  if (type === "loading") {
    el.innerHTML = `<span class="pub-spinner"></span><span>${text}</span>`;
  } else if (type === "error" && retryFn) {
    // Ante un token de Meta inválido, reintentar la subida vuelve a fallar
    // igual: lo único que sirve es releer el token nuevo desde .env.
    const label = authError ? "🔑 Recargar token" : "🔄 Reintentar";
    el.innerHTML = `<span>${text}</span> <button class="btn-sm" id="${elId}-retry-btn">${label}</button>`;
    $(`${elId}-retry-btn`).addEventListener("click", authError ? () => reloadMetaToken(retryFn) : retryFn);
  } else {
    el.innerHTML = `<span>${text}</span>`;
  }
}
const showFbStatus = (type, msg, authError) => _showPubStatus("fb-status", type, msg, () => publishToFacebook(), authError);
const showIgStatus = (type, msg, authError) => _showPubStatus("ig-status", type, msg, () => publishToInstagram(), authError);
const showYtStatus = (type, msg) => _showPubStatus("yt-status", type, msg, () => publishToYoutube());

/** Carga las páginas configuradas en .env y llena el selector "Publicar". */
async function loadMetaPages() {
  try {
    const res = await fetch("/api/meta/pages");
    const data = await res.json();
    const pages = (data.ok && data.pages) || [];
    const sel = $("pub-page");
    const previousValue = sel.value;
    sel.innerHTML = pages.map(p => `<option value="${p.page_id}">${p.name}</option>`).join("");
    if (previousValue && pages.some(p => p.page_id === previousValue)) {
      sel.value = previousValue;
    }
    $("pub-page-wrap").style.display = pages.length > 1 ? "" : "none";
  } catch (e) { console.error("No se pudieron cargar las páginas de Facebook/Instagram", e); }
}

function selectedPageId() {
  const sel = $("pub-page");
  return sel && sel.value ? sel.value : null;
}

async function checkMetaToken() {
  const el = $("meta-token-status");
  const pageId = selectedPageId();
  try {
    const res = await fetch("/api/meta/token-status" + (pageId ? "?page_id=" + encodeURIComponent(pageId) : ""));
    const data = await res.json();
    if (data.ok) {
      el.style.display = "none";
      return;
    }
    el.style.display = "";
    _showPubStatus("meta-token-status", "error", "⚠️ Facebook/Instagram: " + data.error,
                   () => checkMetaToken(), data.auth_error);
  } catch (e) {
    // sin conexión al server: no tocar el banner, dejar el último estado visible
  }
}

/** Relee .env para tomar el token nuevo y, si ahora es válido, reintenta lo que falló. */
async function reloadMetaToken(retryFn) {
  try {
    const res = await fetch("/api/meta/reload-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page_id: selectedPageId() }),
    });
    const data = await res.json();
    if (data.ok) {
      $("meta-token-status").style.display = "none";
      if (retryFn) retryFn();
      return;
    }
    await appAlert(data.error, "El token sigue sin ser válido");
    checkMetaToken();
  } catch (e) {
    await appAlert("Error de conexión con el servidor.", "No se pudo recargar el token");
  }
}

async function pollFacebookJob(jobId) {
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const res = await fetch("/api/facebook/status/" + jobId);
    const data = await res.json();
    if (!data.ok) { showFbStatus("error", "❌ " + (data.error || "Error al consultar el estado.")); return; }
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
      showFbStatus("error", "❌ Facebook: " + data.error, data.auth_error);
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
      showIgStatus("success", `🕒 Instagram programado para ${when} (se publica solo, no hace falta esperar acá)`);
      return;
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
      showIgStatus("error", "❌ Instagram: " + data.error, data.auth_error);
      return;
    }
  }
}

async function publishToFacebook(force) {
  if (!currentVideoPath) return;
  if (!$("yt-title").value.trim()) await _autoSuggestSeo();
  if (!$("pub-fb-description").value.trim()) await _suggestCaption("pub-fb-description", "fb-caption-status");
  showFbStatus("loading", "Publicando en Facebook...");
  const scheduledTime = await getAutoOrScheduledTimeIso("fb-auto-time", "/api/facebook/best-time");
  showFbStatus("loading", scheduledTime ? "Programando en Facebook..." : "Publicando en Facebook...");
  try {
    const res = await fetch("/api/facebook/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: currentVideoPath,
        title: $("yt-title").value.trim(),
        description: $("pub-fb-description").value.trim(),
        scheduled_time: scheduledTime,
        page_id: selectedPageId(),
        force: !!force,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      await pollFacebookJob(data.job_id);
    } else if (data.duplicate && await appConfirm(data.error + " ¿Publicar de todas formas?", { title: "Publicación duplicada", confirmLabel: "Publicar de todas formas" })) {
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
  if (!$("yt-title").value.trim()) await _autoSuggestSeo();
  if (!$("pub-ig-description").value.trim()) await _suggestCaption("pub-ig-description", "ig-caption-status");
  showIgStatus("loading", "Publicando en Instagram...");
  const scheduledTime = await getAutoOrScheduledTimeIso("ig-auto-time", "/api/instagram/best-time");
  showIgStatus("loading", scheduledTime ? "Programando en Instagram..." : "Publicando en Instagram...");
  try {
    const res = await fetch("/api/instagram/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: currentVideoPath,
        title: $("yt-title").value.trim(),
        description: $("pub-ig-description").value.trim(),
        scheduled_time: scheduledTime,
        page_id: selectedPageId(),
        force: !!force,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      await pollInstagramJob(data.job_id);
    } else if (data.duplicate && await appConfirm(data.error + " ¿Publicar de todas formas?", { title: "Publicación duplicada", confirmLabel: "Publicar de todas formas" })) {
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
  status.textContent = "Abriendo el navegador para conectar tu cuenta de Google...";
  try {
    const res = await fetch("/api/youtube/connect", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      status.textContent = "";
      checkYoutubeConnection();
    } else {
      status.textContent = `❌ ${data.error || "No se pudo conectar."}`;
    }
  } catch (e) {
    status.textContent = "❌ Error de conexión con el servidor.";
  } finally {
    btn.disabled = false;
  }
});

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
      if (data.thumbnail_error) {
        showYtStatus("error", `⚠️ Publicado en YouTube (id: ${data.video_id}), pero la miniatura NO se subió: ${data.thumbnail_error}`);
      } else {
        showYtStatus("success", `✅ Publicado en YouTube (id: ${data.video_id})`);
      }
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
    } else if (data.duplicate && await appConfirm(data.error + " ¿Publicar de todas formas?", { title: "Publicación duplicada", confirmLabel: "Publicar de todas formas" })) {
      return publishToYoutube(true);
    } else {
      showYtStatus("error", "❌ " + (data.error || "Error al publicar."));
    }
  } catch (e) {
    showYtStatus("error", "❌ Error de conexión con el servidor.");
  }
}

async function _publishSourceText() {
  const local = $("pipeline-script").value.trim();
  if (local) return local;
  if (!currentVideoPath) return "";
  try {
    const res = await fetch(`/api/pipeline/script/${encodeURIComponent(currentVideoPath)}`);
    const data = await res.json();
    if (data.ok && data.text) {
      return data.text;
    }
  } catch (e) {}
  return "";
}

async function _autoSuggestSeo(statusId) {
  const text = await _publishSourceText();
  const status = statusId ? $(statusId) : null;
  if (!text) {
    if (status) status.textContent = "❌ Pegá el guion primero.";
    return false;
  }
  if (status) status.textContent = "✨ Generando sugerencia SEO...";
  try {
    const res = await fetch("/api/seo/suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, title: $("yt-title").value.trim() }),
    });
    const data = await res.json();
    if (!data.ok) {
      if (status) status.textContent = "❌ " + (data.error || "No se pudo generar la sugerencia.");
      return false;
    }
    $("yt-title").value = data.title;
    $("pub-yt-description").value = data.description;
    $("yt-tags").value = data.tags.join(", ");
    if (status) status.textContent = `✅ SEO sugerido (puntaje ${data.score}/100)`;
    return true;
  } catch (e) {
    if (status) status.textContent = "❌ Error de conexión con el servidor.";
    return false;
  }
}

$("yt-seo-btn").addEventListener("click", () => _autoSuggestSeo("yt-seo-status"));

async function _suggestCaption(descId, statusId) {
  const text = await _publishSourceText();
  const status = statusId ? $(statusId) : null;
  if (!text) {
    if (status) status.textContent = "❌ Pegá el guion primero.";
    return false;
  }
  if (status) status.textContent = "✨ Generando caption...";
  try {
    const res = await fetch("/api/seo/suggest-social", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    if (!data.ok) {
      if (status) status.textContent = "❌ " + (data.error || "No se pudo generar el caption.");
      return false;
    }
    $(descId).value = data.caption + "\n\n" + data.hashtags.join(" ");
    if (status) status.textContent = "✅ Caption sugerido.";
    return true;
  } catch (e) {
    if (status) status.textContent = "❌ Error de conexión con el servidor.";
    return false;
  }
}
$("fb-caption-btn").addEventListener("click", () => _suggestCaption("pub-fb-description", "fb-caption-status"));
$("ig-caption-btn").addEventListener("click", () => _suggestCaption("pub-ig-description", "ig-caption-status"));

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
             style="width:150px;border-radius:8px;cursor:pointer;border:3px solid transparent"
             class="yt-thumb-variant-img" alt="Variante ${i + 1}">
        <div style="font-size:11.5px;color:var(--text-muted)">Variante ${i + 1}</div>
      </div>`).join("");
    wrap.style.display = "flex";
    $("yt-thumb-preview").style.display = "none";
    wrap.querySelectorAll(".yt-thumb-variant-img").forEach(img => {
      img.addEventListener("click", () => {
        wrap.querySelectorAll(".yt-thumb-variant-img").forEach(i => i.style.borderColor = "transparent");
        img.style.borderColor = "var(--red-light)";
        generatedThumbnail = img.dataset.filename;
        $("yt-seo-status").textContent = "✅ Miniatura elegida — se subirá junto con el video.";
      });
    });
    $("yt-seo-status").textContent = "Elegí la miniatura que más te guste.";
  } catch (e) {
    $("yt-seo-status").textContent = "❌ Error de conexión con el servidor.";
  }
});

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

// ── Ajustes: estado y reconexión de sesiones (WhatsApp / Qwen) ──
const _ajustesPollTimers = {};

const _ajustesProviderLabel = { whatsapp: "WhatsApp", qwen: "Qwen", qwen_batch: "Qwen (lote)" };

function closeQrModal() {
  $("qr-modal-backdrop").style.display = "none";
  Object.keys(_ajustesPollTimers).forEach((p) => {
    if (_ajustesPollTimers[p]) {
      clearInterval(_ajustesPollTimers[p]);
      _ajustesPollTimers[p] = null;
    }
  });
}
$("qr-modal-close").addEventListener("click", closeQrModal);
$("qr-modal-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "qr-modal-backdrop") closeQrModal();
});

function _renderSessionStatus(provider, state, message) {
  const ring = $(`ajustes-ring-${provider}`);
  const msg = $(`ajustes-msg-${provider}`);
  const reconnectBtn = $(`ajustes-reconnect-${provider}-btn`);
  msg.textContent = message || "";
  if (state === "ok") {
    ring.className = "ring done";
    reconnectBtn.style.display = "none";
    if ($("qr-modal-backdrop").dataset.provider === provider) closeQrModal();
  } else if (state === "needs_login") {
    ring.className = "ring error";
    reconnectBtn.style.display = "";
  } else {
    ring.className = "ring error";
    reconnectBtn.style.display = "";
  }
}

async function checkSessionStatus(provider, { reload = true } = {}) {
  const ring = $(`ajustes-ring-${provider}`);
  ring.className = "ring indeterminate";
  try {
    const url = `/api/session/status/${provider}${reload ? "" : "?reload=0"}`;
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok) {
      _renderSessionStatus(provider, "unreachable", data.error || "No se pudo comprobar.");
      return;
    }
    _renderSessionStatus(provider, data.state, data.message);
    return data.state;
  } catch (e) {
    _renderSessionStatus(provider, "unreachable", "Error de conexión con el servidor.");
  }
}

async function reconnectSession(provider) {
  const btn = $(`ajustes-reconnect-${provider}-btn`);
  btn.disabled = true;
  btn.textContent = "Reconectando...";
  try {
    const res = await fetch(`/api/session/reconnect/${provider}`, { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      alert(data.error || "No se pudo reconectar.");
      return;
    }
    let state = await checkSessionStatus(provider);
    for (let i = 0; i < 4 && state !== "needs_login" && state !== "ok"; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      state = await checkSessionStatus(provider);
    }
    if (state === "needs_login") {
      const backdrop = $("qr-modal-backdrop");
      const qrImg = $("qr-modal-img");
      const hint = $("qr-modal-hint");
      const devtoolsLink = $("qr-modal-devtools-link");
      const label = _ajustesProviderLabel[provider] || provider;
      backdrop.dataset.provider = provider;
      const isQwen = provider === "qwen" || provider === "qwen_batch";
      if (isQwen) {
        // Qwen no tiene QR (no renderiza <canvas>) -- esta sesion es un Chrome
        // aparte, sin relacion con el Chrome personal del usuario aunque ahi
        // ya este logueado. Hay que loguear ESTA sesion puntual via DevTools.
        $("qr-modal-title").textContent = `Iniciar sesión en ${label}`;
        qrImg.style.display = "none";
        hint.textContent = "Esta es una sesión de navegador aislada, separada de tu Chrome personal. Estar logueado en tu Chrome no la loguea a ella. Hacé clic abajo para abrir esta sesión en una pestaña de DevTools y loguearte ahí (con tu cuenta de Qwen).";
        devtoolsLink.style.display = "block";
        devtoolsLink.textContent = "Cargando enlace...";
        fetch(`/api/session/devtools-url/${provider}`)
          .then((r) => r.json())
          .then((d) => {
            if (d.ok) {
              devtoolsLink.href = d.url;
              devtoolsLink.textContent = "Abrir sesión para loguearme";
            } else {
              devtoolsLink.textContent = "No se pudo generar el enlace";
            }
          })
          .catch(() => { devtoolsLink.textContent = "No se pudo generar el enlace"; });
      } else {
        $("qr-modal-title").textContent = `Escaneá el QR de ${label}`;
        qrImg.style.display = "block";
        hint.textContent = "WhatsApp → Menú → Dispositivos vinculados → Vincular un dispositivo, y escaneá esto con la cámara del celular.";
        devtoolsLink.style.display = "none";
        qrImg.src = `/api/session/screenshot/${provider}?t=${Date.now()}`;
      }
      backdrop.style.display = "flex";
      if (_ajustesPollTimers[provider]) clearInterval(_ajustesPollTimers[provider]);
      _ajustesPollTimers[provider] = setInterval(async () => {
        const s = await checkSessionStatus(provider, { reload: false });
        if (s === "ok") {
          clearInterval(_ajustesPollTimers[provider]);
          _ajustesPollTimers[provider] = null;
        } else if (s === "needs_login" && !isQwen && backdrop.dataset.provider === provider) {
          qrImg.src = `/api/session/screenshot/${provider}?t=${Date.now()}`;
        }
      }, 5000);
    }
  } catch (e) {
    alert("No se pudo reconectar.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Reconectar";
  }
}

$("ajustes-check-whatsapp-btn").addEventListener("click", () => checkSessionStatus("whatsapp"));
$("ajustes-check-qwen-btn").addEventListener("click", () => checkSessionStatus("qwen"));
$("ajustes-check-qwen_batch-btn").addEventListener("click", () => checkSessionStatus("qwen_batch"));
$("ajustes-reconnect-whatsapp-btn").addEventListener("click", () => reconnectSession("whatsapp"));
$("ajustes-reconnect-qwen-btn").addEventListener("click", () => reconnectSession("qwen"));
$("ajustes-reconnect-qwen_batch-btn").addEventListener("click", () => reconnectSession("qwen_batch"));

// ── Ajustes: URL pública del Cloudflare Tunnel (posts de imagen gaming) ──
async function loadPublicUrl() {
  try {
    const res = await fetch("/api/batch/get-public-url");
    const data = await res.json();
    if (data.ok) $("ajustes-public-url").value = data.url || "";
  } catch (e) { /* silencioso, no bloquea el resto de la pestaña */ }
}
$("ajustes-save-public-url-btn").addEventListener("click", async () => {
  const btn = $("ajustes-save-public-url-btn");
  const statusEl = $("ajustes-public-url-status");
  btn.disabled = true;
  try {
    const res = await fetch("/api/batch/set-public-url", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: $("ajustes-public-url").value.trim() }),
    });
    const data = await res.json();
    statusEl.innerHTML = data.ok
      ? `<div class="analytics-empty">✅ Guardado.</div>`
      : `<div class="analytics-empty">❌ No se pudo guardar.</div>`;
  } catch (e) {
    statusEl.innerHTML = `<div class="analytics-empty">❌ Error de conexión con el servidor.</div>`;
  } finally {
    btn.disabled = false;
  }
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


@app.route("/api/subtitle-presets")
def api_subtitle_presets():
    return jsonify([
        {"id": key, **style}
        for key, style in video_maker.SUBTITLE_PRESETS.items()
    ])


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
    filename = secure_filename_safe(filename)
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
                    subtitles_enabled: bool, subtitle_style: Optional[dict], orientation: str, cleanup,
                    clip_story_id: Optional[str] = None, animate_images: bool = True):
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
                animate_images=animate_images,
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
            if clip_story_id:
                folders = job_store.load("clip_folders")
                if clip_story_id in folders:
                    folders[clip_story_id]["video_name"] = Path(video_path).name
                    job_store.save("clip_folders", folders)
        else:
            _video_jobs[job_id].update(
                status="error", error="Error al preparar o renderizar la edición. Revisa los logs del servidor."
            )
        job_store.save("video", _video_jobs)
    cleanup()


_clipgen_jobs = {}  # job_id -> {status, message, story_id, error, started_at}
_clipgen_jobs_lock = threading.Lock()
# La generación de clips comparte una única sesión de WhatsApp (agent-browser
# --session whatsapp); dos jobs en paralelo se pisarían el chat.
_clipgen_lock = threading.Lock()


def _run_clipgen_job(job_id: str, story_text: str, story_id: str, clips_dir: Path, provider: str,
                      generate_video_clips: bool = True):
    def on_progress(msg):
        with _clipgen_jobs_lock:
            _clipgen_jobs[job_id]["message"] = msg

    if _clipgen_lock.locked():
        with _clipgen_jobs_lock:
            _clipgen_jobs[job_id]["message"] = "En cola: esperando a que termine otra generación de clips..."

    logger.info("clipgen job %s: iniciando (story_id=%s provider=%s dir=%s)", job_id, story_id, provider, clips_dir)
    try:
        with _clipgen_lock:
            story = auto_pipeline.load_story_from_text(story_text, story_id)
            start_index = auto_pipeline.resume_index(clips_dir)
            clips = auto_pipeline.generate_clips(
                story, clips_dir, unattended=True, start_index=start_index, on_progress=on_progress,
                provider=provider, generate_video=generate_video_clips,
            )
    except Exception as e:
        logger.exception("clipgen job %s: fallo", job_id)
        with _clipgen_jobs_lock:
            _clipgen_jobs[job_id].update(status="error", error=str(e))
            job_store.save("clipgen", _clipgen_jobs)
        return
    logger.info("clipgen job %s: listo, %d clips", job_id, len(clips))

    scratch_path = auto_pipeline.SCRATCH_DIR / f"historia_{story_id}.json"
    scratch_path.unlink(missing_ok=True)

    folders = job_store.load("clip_folders")
    folders[story_id] = {"dir": str(clips_dir), "clip_count": len(clips)}
    job_store.save("clip_folders", folders)

    with _clipgen_jobs_lock:
        _clipgen_jobs[job_id].update(status="done", clip_count=len(clips))
        job_store.save("clipgen", _clipgen_jobs)


@app.route("/api/clipgen/start", methods=["POST"])
def api_clipgen_start():
    data = request.get_json(force=True)
    story_text = data.get("story_text", "").strip()
    story_id = data.get("story_id", "").strip() or uuid.uuid4().hex[:8]
    provider = data.get("provider", "whatsapp").strip()
    generate_video_clips = bool(data.get("generate_video_clips", True))

    if not story_text:
        return jsonify({"ok": False, "error": "Pega el texto de la historia (guion + prompts)."}), 400
    if provider not in auto_pipeline.PROVIDERS:
        return jsonify({"ok": False, "error": f"Proveedor desconocido: {provider}"}), 400

    clips_dir = video_maker.VIDEO_PUBLIC_DIR / story_id

    job_id = uuid.uuid4().hex
    with _clipgen_jobs_lock:
        _clipgen_jobs[job_id] = {
            "status": "running",
            "message": "Preparando...",
            "story_id": story_id,
            "provider": provider,
            "error": None,
            "started_at": time.time(),
        }
        job_store.save("clipgen", _clipgen_jobs)

    thread = threading.Thread(
        target=_run_clipgen_job,
        args=(job_id, story_text, story_id, clips_dir, provider),
        kwargs={"generate_video_clips": generate_video_clips},
        daemon=True,
    )
    thread.start()

    return jsonify({"ok": True, "job_id": job_id, "story_id": story_id})


@app.route("/api/clipgen/restart-browser", methods=["POST"])
def api_clipgen_restart_browser():
    data = request.get_json(force=True)
    provider = data.get("provider", "whatsapp").strip()
    if provider not in auto_pipeline.PROVIDERS:
        return jsonify({"ok": False, "error": f"Proveedor desconocido: {provider}"}), 400
    session = auto_pipeline.QWEN_SESSION if provider == "qwen" else auto_pipeline.WHATSAPP_SESSION
    result = auto_pipeline.hard_reset_browser_session(session)
    return jsonify(result)


def _session_for_provider(provider):
    # "mixed" no es una sesion real de agent-browser (usa whatsapp+qwen a la
    # vez) -- solo whatsapp/qwen tienen estado de login individual chequeable.
    if provider == "qwen":
        return auto_pipeline.QWEN_SESSION
    if provider == "qwen_batch":
        return auto_pipeline.QWEN_BATCH_SESSION
    if provider == "whatsapp":
        return auto_pipeline.WHATSAPP_SESSION
    return None


@app.route("/api/session/status/<provider>", methods=["GET"])
def api_session_status(provider):
    session = _session_for_provider(provider)
    if session is None:
        return jsonify({"ok": False, "error": f"Proveedor desconocido: {provider}"}), 400
    force_reload = request.args.get("reload", "1") != "0"
    result = auto_pipeline.check_session_status(session, force_reload=force_reload)
    return jsonify({"ok": True, **result})


@app.route("/api/session/reconnect/<provider>", methods=["POST"])
def api_session_reconnect(provider):
    session = _session_for_provider(provider)
    if session is None:
        return jsonify({"ok": False, "error": f"Proveedor desconocido: {provider}"}), 400
    # Solo se reinicia el daemon si esta realmente colgado (unreachable). Si solo
    # esta deslogueado (needs_login) el daemon/Chrome siguen sanos -- matarlo y
    # relanzarlo de cero es mucho mas lento/fragil que navegar a la URL de login
    # en la sesion ya abierta.
    current = auto_pipeline.check_session_status(session)
    if current["state"] == "unreachable":
        auto_pipeline.hard_reset_browser_session(session)
        time.sleep(1)
    try:
        auto_pipeline.open_login_page(session)
    except auto_pipeline.PipelineError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/api/session/screenshot/<provider>", methods=["GET"])
def api_session_screenshot(provider):
    session = _session_for_provider(provider)
    if session is None:
        return jsonify({"ok": False, "error": f"Proveedor desconocido: {provider}"}), 400
    try:
        png_path = auto_pipeline.screenshot_qr(session)
    except auto_pipeline.PipelineError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return send_file(str(png_path), mimetype="image/png")


@app.route("/api/session/devtools-url/<provider>", methods=["GET"])
def api_session_devtools_url(provider):
    session = _session_for_provider(provider)
    if session is None:
        return jsonify({"ok": False, "error": f"Proveedor desconocido: {provider}"}), 400
    try:
        url = auto_pipeline.get_remote_devtools_url(session)
    except auto_pipeline.PipelineError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "url": url})


@app.route("/api/clipgen/status/<job_id>")
def api_clipgen_status(job_id):
    with _clipgen_jobs_lock:
        job = _clipgen_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "Trabajo no encontrado."}), 404
        response = dict(job)
    response.pop("started_at", None)
    response["ok"] = True
    return jsonify(response)


@app.route("/api/clipgen/clips/<story_id>")
def api_clipgen_clips(story_id):
    clips_dir = video_maker.VIDEO_PUBLIC_DIR / secure_filename_safe(story_id)
    if not clips_dir.exists():
        return jsonify({"ok": True, "clips": []})
    clips = sorted(f.name for f in clips_dir.glob("scene_*.mp4"))
    return jsonify({"ok": True, "clips": clips, "dir": str(clips_dir)})


@app.route("/api/clipgen/clip/<story_id>/<filename>")
def api_clipgen_clip_file(story_id, filename):
    filepath = video_maker.VIDEO_PUBLIC_DIR / secure_filename_safe(story_id) / secure_filename_safe(filename)
    if not filepath.exists():
        return "Archivo no encontrado", 404
    return send_file(str(filepath))


def secure_filename_safe(name: str) -> str:
    """story_id siempre es hex generado por el server, pero por si viene de un
    query param se sanea antes de usarlo para construir un path."""
    from werkzeug.utils import secure_filename
    return secure_filename(name)


_pipeline_jobs = job_store.load("pipeline")  # job_id -> {status, error, inputs, story: {tts, clipgen, video}}
for _job in _pipeline_jobs.values():
    # Un job "running" en el checkpoint significa que el servidor se cayó/reinició
    # a mitad de camino, no que siga corriendo: se marca como error para que la UI
    # deje de mostrar un spinner infinito, pero se preservan los sub-estados (tts,
    # clipgen) ya terminados para que /api/pipeline/retry pueda reusarlos.
    if _job.get("status") == "running":
        _job["status"] = "error"
        _job["error"] = "El servidor se reinició mientras este trabajo estaba en curso."
    for _stage in _job.get("story", {}).values():
        if _stage.get("status") == "running":
            _stage["status"] = "error"
            _stage["error"] = "Interrumpido por un reinicio del servidor."
_pipeline_jobs_lock = threading.Lock()


def _pipeline_sub_update(job_id: str, stage: str, **fields):
    with _pipeline_jobs_lock:
        job = _pipeline_jobs.get(job_id)
        if job is None:
            return  # job descartado (cancelar-todo) mientras el hilo seguía vivo
        job["story"][stage].update(fields)
        job_store.save("pipeline", _pipeline_jobs)


def _run_pipeline_job(job_id: str, story_text: str, story_id: str, clips_dir: Path,
                       tts_kwargs: dict, orientation: str, subtitles_enabled: bool,
                       subtitle_style: Optional[dict], title: str, provider: str = "whatsapp",
                       skip_tts: bool = False, script_text: Optional[str] = None,
                       wait_tts_from: Optional[str] = None, animate_images: bool = True,
                       generate_video_clips: bool = True):
    # Si el guion vino ya separado desde la UI (campo propio), se usa tal cual --
    # extract_script() adivina por heading/posición y puede confundirse si el
    # bloque de prompts menciona la palabra "guion" (p.ej. "Frase del guion:").
    if not script_text:
        script_text = auto_pipeline.extract_script(story_text)
    logger.info("pipeline job %s: guion extraido (%d chars) story_id=%s", job_id, len(script_text), story_id)

    def run_tts():
        _pipeline_sub_update(job_id, "tts", status="running", percent=0, message="Preparando...")
        logger.info("pipeline job %s: tts iniciado", job_id)

        def on_progress(pct, msg):
            _pipeline_sub_update(job_id, "tts", percent=pct, message=msg)

        try:
            output_path = text_to_speech_long(script_text, on_progress=on_progress, **tts_kwargs)
        except Exception as e:
            logger.exception("pipeline job %s: tts fallo", job_id)
            _pipeline_sub_update(job_id, "tts", status="error", error=str(e), percent=100)
            return
        if output_path:
            logger.info("pipeline job %s: tts listo (%s)", job_id, output_path)
            _pipeline_sub_update(job_id, "tts", status="done", percent=100, message="Listo",
                                  filename=Path(output_path).name)
        else:
            logger.error("pipeline job %s: tts fallo sin excepcion", job_id)
            _pipeline_sub_update(job_id, "tts", status="error", percent=100,
                                  error="Error al generar el audio.")

    def wait_tts():
        """El intento anterior sigue generando el audio: se espera a que termine
        en vez de regenerarlo (cancelar los clips no debe tirar el TTS a la basura)."""
        _pipeline_sub_update(job_id, "tts", status="running", percent=0,
                              message="Esperando el audio del intento anterior...")
        while True:
            with _pipeline_jobs_lock:
                src = _pipeline_jobs.get(wait_tts_from, {}).get("story", {}).get("tts")
                src = dict(src) if src else None
            if src is None:
                _pipeline_sub_update(job_id, "tts", status="error", percent=100,
                                      error="Se perdió el intento anterior que estaba generando el audio.")
                return
            if src.get("status") == "done" and src.get("filename"):
                _pipeline_sub_update(job_id, "tts", status="done", percent=100,
                                      message="Reutilizado del intento anterior",
                                      filename=src.get("filename"))
                return
            if src.get("status") == "error":
                _pipeline_sub_update(job_id, "tts", status="error", percent=100,
                                      error=src.get("error") or "El audio del intento anterior falló.")
                return
            _pipeline_sub_update(job_id, "tts", percent=src.get("percent", 0),
                                  message=f"Esperando el audio del intento anterior: {src.get('message', '')}")
            time.sleep(2)

    def run_clipgen():
        _pipeline_sub_update(job_id, "clipgen", status="running", message="Preparando...")
        logger.info("pipeline job %s: clipgen iniciado", job_id)
        if _clipgen_lock.locked():
            _pipeline_sub_update(job_id, "clipgen",
                                  message="En cola: esperando a que termine otra generación de clips...")

        def on_progress(msg):
            _pipeline_sub_update(job_id, "clipgen", message=msg)

        try:
            with _clipgen_lock:
                story = auto_pipeline.load_story_from_text(story_text, story_id)
                start_index = auto_pipeline.resume_index(clips_dir)
                clips = auto_pipeline.generate_clips(
                    story, clips_dir, unattended=True, start_index=start_index, on_progress=on_progress,
                    provider=provider, generate_video=generate_video_clips,
                )
        except Exception as e:
            logger.exception("pipeline job %s: clipgen fallo", job_id)
            with _pipeline_jobs_lock:
                cancelled = bool(_pipeline_jobs.get(job_id, {}).get("cancel_requested"))
            _pipeline_sub_update(
                job_id, "clipgen", status="error",
                error=("Cancelado desde la web. Apretá \"Reintentar\" para retomar los clips "
                       "desde el último descargado, sin rehacer el audio." if cancelled else str(e)),
            )
            return
        logger.info("pipeline job %s: clipgen listo (%d clips)", job_id, len(clips))
        (auto_pipeline.SCRATCH_DIR / f"historia_{story_id}.json").unlink(missing_ok=True)
        folders = job_store.load("clip_folders")
        folders[story_id] = {"dir": str(clips_dir), "clip_count": len(clips)}
        job_store.save("clip_folders", folders)
        _pipeline_sub_update(job_id, "clipgen", status="done", clip_count=len(clips))

    thread_clipgen = threading.Thread(target=run_clipgen, daemon=True)
    if skip_tts:
        # El audio ya se generó en un intento anterior (reintento tras un corte) —
        # el estado "tts" ya viene marcado "done" desde /api/pipeline/retry.
        logger.info("pipeline job %s: tts omitido (reintento, audio reutilizado)", job_id)
    else:
        thread_tts = threading.Thread(
            target=wait_tts if wait_tts_from else run_tts, daemon=True
        )
        thread_tts.start()
    thread_clipgen.start()
    if not skip_tts:
        thread_tts.join()
    thread_clipgen.join()

    with _pipeline_jobs_lock:
        tts_ok = _pipeline_jobs[job_id]["story"]["tts"]["status"] == "done"
        clipgen_ok = _pipeline_jobs[job_id]["story"]["clipgen"]["status"] == "done"
        audio_filename = _pipeline_jobs[job_id]["story"]["tts"].get("filename")

    if not (tts_ok and clipgen_ok):
        with _pipeline_jobs_lock:
            _pipeline_jobs[job_id].update(
                status="error", error="Falló guion o clips — revisa los paneles arriba."
            )
            job_store.save("pipeline", _pipeline_jobs)
        return

    _pipeline_sub_update(job_id, "video", status="running", message="Preparando...")

    def on_progress(msg):
        m = re.search(r"\((\d+)%\)\s*$", msg)
        if m:
            _pipeline_sub_update(job_id, "video", message=msg, percent=int(m.group(1)))
        else:
            _pipeline_sub_update(job_id, "video", message=msg)

    try:
        clips = sorted(
            (str(p) for p in clips_dir.glob("scene_*.*") if p.suffix in (".mp4", ".jpg")),
            key=lambda s: int(Path(s).stem.split("_")[1]),
        )
        audio_path = str(OUTPUT_DIR / audio_filename)
        story = auto_pipeline.load_story_from_text(story_text, story_id)
        frases = [p["frase"] for p in sorted(story["prompts"], key=lambda p: p["index"])]
        with _video_render_lock:
            timeline = video_maker.build_props(
                image_paths=clips,
                audio_path=audio_path,
                title=title,
                subtitles_enabled=subtitles_enabled,
                subtitle_style=subtitle_style,
                frases=frases,
                animate_images=animate_images,
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
        _pipeline_sub_update(job_id, "video", status="error", error=str(e))
        with _pipeline_jobs_lock:
            _pipeline_jobs[job_id].update(status="error", error=str(e))
            job_store.save("pipeline", _pipeline_jobs)
        return

    if timeline and video_path:
        _pipeline_sub_update(job_id, "video", status="done", video_name=Path(video_path).name)
        folders = job_store.load("clip_folders")
        if story_id in folders:
            folders[story_id]["video_name"] = Path(video_path).name
            job_store.save("clip_folders", folders)
        with _pipeline_jobs_lock:
            _pipeline_jobs[job_id]["status"] = "done"
            job_store.save("pipeline", _pipeline_jobs)
    else:
        _pipeline_sub_update(job_id, "video", status="error",
                              error="Error al preparar o renderizar la edición.")
        with _pipeline_jobs_lock:
            _pipeline_jobs[job_id].update(status="error", error="Falló el renderizado del video.")
            job_store.save("pipeline", _pipeline_jobs)


@app.route("/api/pipeline/preview", methods=["POST"])
def api_pipeline_preview():
    data = request.get_json(silent=True) or {}
    story_text = (data.get("story_text") or "").strip()
    if not story_text:
        return jsonify({"ok": False, "error": "Pegá el guion primero."}), 400
    try:
        script_text = auto_pipeline.extract_script(story_text)
        story = auto_pipeline.load_story_from_text(story_text, "preview")
    except auto_pipeline.PipelineError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({
        "ok": True,
        "script_preview": script_text,
        "script_len": len(script_text),
        "prompt_count": len(story["prompts"]),
        "prompts": [{"index": p["index"], "frase": p["frase"]} for p in story["prompts"]],
    })


@app.route("/api/pipeline/start", methods=["POST"])
def api_pipeline_start():
    data = request.get_json(force=True)
    story_text = data.get("story_text", "").strip()
    if not story_text:
        return jsonify({"ok": False, "error": "Pega el texto completo de la historia."}), 400
    script_text = data.get("script_text", "").strip() or None
    duration_seconds = data.get("duration_seconds") or None
    if script_text and duration_seconds:
        script_text = auto_pipeline.cap_script_to_duration(script_text, int(duration_seconds))

    story_id = uuid.uuid4().hex[:8]
    clips_dir = video_maker.VIDEO_PUBLIC_DIR / story_id
    title = data.get("title", "").strip()

    tts_kwargs = {
        "voice": data.get("voice", DEFAULT_VOICE),
        "exaggeration": float(data.get("exaggeration", BEDTIME_PRESET["exaggeration"])),
        "cfg_weight": float(data.get("cfg_weight", BEDTIME_PRESET["cfg_weight"])),
    }
    orientation = data.get("orientation", "vertical").strip()
    if orientation not in ("vertical", "horizontal"):
        orientation = "vertical"
    provider = data.get("provider", "whatsapp").strip()
    if provider not in auto_pipeline.PROVIDERS:
        return jsonify({"ok": False, "error": f"Proveedor desconocido: {provider}"}), 400
    subtitles_enabled = bool(data.get("subtitles_enabled", True))
    subtitle_style = video_maker.get_subtitle_preset_style(data.get("subtitle_preset", ""))
    animate_images = bool(data.get("animate_images", True))
    generate_video_clips = bool(data.get("generate_video_clips", True))

    job_id = uuid.uuid4().hex
    inputs = {
        "story_text": story_text,
        "script_text": script_text,
        "tts_kwargs": tts_kwargs,
        "orientation": orientation,
        "subtitles_enabled": subtitles_enabled,
        "subtitle_style": subtitle_style,
        "title": title,
        "provider": provider,
        "animate_images": animate_images,
        "generate_video_clips": generate_video_clips,
    }
    with _pipeline_jobs_lock:
        _pipeline_jobs[job_id] = {
            "status": "running",
            "error": None,
            "story_id": story_id,
            "started_at": time.time(),
            "inputs": inputs,
            "story": {
                "tts": {"status": "pending", "percent": 0, "message": "", "error": None},
                "clipgen": {"status": "pending", "message": "", "error": None},
                "video": {"status": "pending", "message": "", "error": None},
            },
        }
        job_store.save("pipeline", _pipeline_jobs)

    thread = threading.Thread(
        target=_run_pipeline_job,
        args=(job_id, story_text, story_id, clips_dir, tts_kwargs, orientation,
              subtitles_enabled, subtitle_style, title, provider),
        kwargs={"script_text": script_text, "animate_images": animate_images,
                "generate_video_clips": generate_video_clips},
        daemon=True,
    )
    thread.start()

    return jsonify({"ok": True, "job_id": job_id, "story_id": story_id})


@app.route("/api/pipeline/retry/<job_id>", methods=["POST"])
def api_pipeline_retry(job_id):
    """Reintenta un job de pipeline que quedó en error, reusando el guion, el
    audio ya generado (si lo hay) y los clips ya descargados — solo retoma lo
    que falta en vez de arrancar todo desde cero."""
    with _pipeline_jobs_lock:
        old = _pipeline_jobs.get(job_id)
    if not old or "inputs" not in old:
        return jsonify({"ok": False, "error": "No se encontró el trabajo original para reintentar."}), 404

    inputs = dict(old["inputs"])  # copia: no pisar el job viejo ya guardado
    data = request.get_json(silent=True) or {}
    new_provider = (data.get("provider") or "").strip()
    if new_provider:
        if new_provider not in auto_pipeline.PROVIDERS:
            return jsonify({"ok": False, "error": f"Proveedor desconocido: {new_provider}"}), 400
        inputs["provider"] = new_provider
    story_id = old["story_id"]
    clips_dir = video_maker.VIDEO_PUBLIC_DIR / story_id

    old_tts = old["story"]["tts"]
    audio_filename = old_tts.get("filename")
    skip_tts = (
        old_tts.get("status") == "done"
        and bool(audio_filename)
        and (OUTPUT_DIR / audio_filename).exists()
    )
    # Si el audio del intento anterior sigue generándose (típico tras cancelar
    # solo los clips), el reintento lo espera en vez de regenerarlo desde cero.
    wait_tts_from = job_id if (not skip_tts and old_tts.get("status") == "running") else None

    new_job_id = uuid.uuid4().hex
    with _pipeline_jobs_lock:
        _pipeline_jobs[new_job_id] = {
            "status": "running",
            "error": None,
            "story_id": story_id,
            "started_at": time.time(),
            "inputs": inputs,
            "story": {
                "tts": (
                    {"status": "done", "percent": 100, "message": "Reutilizado de un intento anterior",
                     "error": None, "filename": audio_filename}
                    if skip_tts else
                    {"status": "pending", "percent": 0,
                     "message": "Esperando el audio del intento anterior..." if wait_tts_from else "",
                     "error": None}
                ),
                "clipgen": {"status": "pending", "message": "", "error": None},
                "video": {"status": "pending", "message": "", "error": None},
            },
        }
        job_store.save("pipeline", _pipeline_jobs)

    thread = threading.Thread(
        target=_run_pipeline_job,
        args=(new_job_id, inputs["story_text"], story_id, clips_dir, inputs["tts_kwargs"],
              inputs["orientation"], inputs["subtitles_enabled"], inputs["subtitle_style"],
              inputs["title"], inputs["provider"]),
        kwargs={"skip_tts": skip_tts, "script_text": inputs.get("script_text"),
                "wait_tts_from": wait_tts_from,
                "animate_images": inputs.get("animate_images", True),
                "generate_video_clips": inputs.get("generate_video_clips", True)},
        daemon=True,
    )
    thread.start()

    return jsonify({"ok": True, "job_id": new_job_id, "story_id": story_id})


def _kill_provider_sessions(provider: str) -> dict:
    sessions = (
        [auto_pipeline.WHATSAPP_SESSION, auto_pipeline.QWEN_SESSION]
        if provider == "mixed" else [_session_for_provider(provider)]
    )
    return {s: auto_pipeline.hard_reset_browser_session(s) for s in sessions if s}


@app.route("/api/pipeline/cancel/<job_id>", methods=["POST"])
def api_pipeline_cancel(job_id):
    """Corta la generación de clips en curso (la única etapa que puede quedarse
    colgada esperando al navegador). El TTS sigue su curso: un reintento posterior
    reutiliza o espera ese audio en vez de regenerarlo."""
    with _pipeline_jobs_lock:
        job = _pipeline_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "Trabajo no encontrado."}), 404
        if job.get("status") != "running":
            return jsonify({"ok": False, "error": "Ese trabajo ya no está en curso."}), 409
        job["cancel_requested"] = True
        provider = job.get("inputs", {}).get("provider", "whatsapp")
        job_store.save("pipeline", _pipeline_jobs)

    # No hay forma de matar el hilo: se corta por abajo matando la sesión del
    # navegador que el clipgen está esperando, y el PipelineError resultante
    # lleva la etapa a "error" liberando el lock global de clips.
    results = _kill_provider_sessions(provider)
    return jsonify({"ok": True, "browser": results})


@app.route("/api/pipeline/discard/<job_id>", methods=["POST"])
def api_pipeline_discard(job_id):
    """Descarta el intento completo: corta el navegador si seguía esperando,
    borra el audio, los clips y el video (si llegó a generarse) y elimina el
    job — a diferencia de /cancel, no deja nada para reintentar."""
    with _pipeline_jobs_lock:
        job = _pipeline_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "Trabajo no encontrado."}), 404
        story_id = job["story_id"]
        provider = job.get("inputs", {}).get("provider", "whatsapp")
        was_running = job.get("status") == "running"
        audio_filename = job["story"]["tts"].get("filename")
        video_name = job["story"]["video"].get("video_name")
        del _pipeline_jobs[job_id]
        job_store.save("pipeline", _pipeline_jobs)

    if was_running:
        _kill_provider_sessions(provider)

    if audio_filename:
        (OUTPUT_DIR / audio_filename).unlink(missing_ok=True)
    shutil.rmtree(video_maker.VIDEO_PUBLIC_DIR / story_id, ignore_errors=True)
    if video_name:
        (video_maker.VIDEO_OUT_DIR / video_name).unlink(missing_ok=True)

    folders = job_store.load("clip_folders")
    if story_id in folders:
        del folders[story_id]
        job_store.save("clip_folders", folders)

    return jsonify({"ok": True})


@app.route("/api/pipeline/status/<job_id>")
def api_pipeline_status(job_id):
    with _pipeline_jobs_lock:
        job = _pipeline_jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "Trabajo no encontrado."}), 404
        response = dict(job)
        response["story"] = {k: dict(v) for k, v in response["story"].items()}
    response.pop("started_at", None)
    response["ok"] = True
    return jsonify(response)


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
    animate_images = request.form.get("animate_images", "1").strip() not in ("0", "false", "")
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

    clip_story_id = request.form.get("clip_story_id", "").strip() or None

    thread = threading.Thread(
        target=_run_video_job,
        args=(job_id, image_paths, str(audio_path), title, subtitles_enabled, subtitle_style, orientation, cleanup,
              clip_story_id, animate_images),
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
    filename = secure_filename_safe(filename)
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


@app.route("/api/videos/<filename>", methods=["DELETE"])
def api_video_delete(filename):
    """Borra un video generado y todo rastro asociado: el archivo en video/out/,
    la carpeta de clips fuente (video/public/<story_id>/), su entrada en
    clip_folders.json, el audio narrado y las entradas de job_state (guion/
    edición) que lo generaron, y los registros de publicación (Facebook/
    Instagram/YouTube) que lo mencionen."""
    filename = secure_filename_safe(filename)
    video_path = video_maker.VIDEO_OUT_DIR / filename
    if not video_path.exists():
        return jsonify({"ok": False, "error": f"No se encontró el video: {filename}"}), 404

    # En Windows el archivo puede seguir con lock un instante si el navegador
    # todavía tiene abierta la conexión de streaming del <video> (miniatura del
    # historial); se reintenta un par de segundos antes de darse por vencido.
    last_error = None
    for attempt in range(10):
        try:
            video_path.unlink(missing_ok=True)
            last_error = None
            break
        except PermissionError as e:
            last_error = e
            time.sleep(0.3)
    if last_error is not None:
        return jsonify({
            "ok": False,
            "error": "El video está en uso (reproduciéndose en el navegador). Cerrá la vista previa e intentá de nuevo.",
        }), 409

    _cleanup_clip_folder_for_video(filename)
    _cleanup_generation_records_for_video(filename)

    for path in (_PUBLISHED_FB_PATH, _PUBLISHED_IG_PATH, _PUBLISHED_VIDEOS_PATH):
        try:
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except Exception:
            continue
        filtered = [item for item in items if item.get("filename") != filename]
        if len(filtered) != len(items):
            path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")

    return jsonify({"ok": True})


@app.route("/api/video/save-drive", methods=["POST"])
def api_video_save_drive():
    data = request.get_json(force=True)
    filename = secure_filename_safe(data.get("filename", "").strip())
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


def _cleanup_generation_records_for_video(video_filename: str) -> None:
    """Borra el audio narrado (guion en voz) y las entradas de job_state
    (pipeline.json / video.json) asociadas a un video ya eliminado, para no
    acumular basura en output/ y output/job_state/."""
    with _pipeline_jobs_lock:
        for job_id, job in list(_pipeline_jobs.items()):
            if job.get("story", {}).get("video", {}).get("video_name") != video_filename:
                continue
            audio_filename = job.get("story", {}).get("tts", {}).get("filename")
            if audio_filename:
                (OUTPUT_DIR / audio_filename).unlink(missing_ok=True)
            del _pipeline_jobs[job_id]
        job_store.save("pipeline", _pipeline_jobs)

    with _video_jobs_lock:
        for job_id, job in list(_video_jobs.items()):
            if job.get("video_name") != video_filename:
                continue
            del _video_jobs[job_id]
        job_store.save("video", _video_jobs)


def _find_pipeline_script(video_filename: str) -> Optional[str]:
    """Busca el guion de un video ya generado en los jobs de pipeline
    todavía vivos (sobreviven hasta que el video se publica o se borra),
    para poder recuperarlo aunque el textarea del navegador se haya
    perdido (recarga de página)."""
    with _pipeline_jobs_lock:
        for job in _pipeline_jobs.values():
            if job.get("story", {}).get("video", {}).get("video_name") != video_filename:
                continue
            inputs = job.get("inputs", {})
            script_text = inputs.get("script_text")
            if script_text:
                return script_text
            story_text = inputs.get("story_text")
            if story_text:
                return auto_pipeline.extract_script(story_text)
    return None


@app.route("/api/pipeline/script/<video_filename>")
def api_pipeline_script(video_filename):
    text = _find_pipeline_script(video_filename)
    if not text:
        return jsonify({"ok": False, "error": "No se encontró el guion para este video."}), 404
    return jsonify({"ok": True, "text": text})


def _cleanup_clip_folder_for_video(video_filename: str) -> None:
    """Borra la carpeta de clips (video/public/<story_id>/) asociada al video ya
    publicado, para liberar espacio -- se llama tras el éxito de cualquiera de
    las 3 publicaciones (Facebook/Instagram/YouTube)."""
    folders = job_store.load("clip_folders")
    story_id = None
    for sid, info in list(folders.items()):
        if info.get("video_name") == video_filename:
            story_id = sid
            break
    if not story_id:
        return
    clips_dir = Path(folders[story_id]["dir"])
    shutil.rmtree(clips_dir, ignore_errors=True)
    del folders[story_id]
    job_store.save("clip_folders", folders)


def _fb_set_stage(job_id: str, stage: str):
    with _fb_jobs_lock:
        if job_id in _fb_jobs:
            _fb_jobs[job_id]["stage"] = stage


def _run_facebook_job(
    job_id: str, video_path: str, title: str, description: str, page_id: Optional[str] = None, target_ts: Optional[float] = None
):
    # target_ts se manda tal cual a Facebook (scheduled_publish_time real de
    # la Graph API): la subida pasa ya mismo, Facebook es quien retiene la
    # publicación hasta esa hora — no hay que esperar acá ni arriesgarse a
    # perder la programación si este server se reinicia antes de esa hora.
    result = facebook_publisher.publish_video(
        video_path, title, description, page_id=page_id, scheduled_time=target_ts,
        on_status=lambda s: _fb_set_stage(job_id, s)
    )
    with _fb_jobs_lock:
        if result["ok"]:
            _fb_jobs[job_id].update(
                status="done", video_id=result["video_id"], scheduled_time=result["scheduled_time"]
            )
        else:
            _fb_jobs[job_id].update(
                status="error", error=result["error"], auth_error=result.get("auth_error", False)
            )
        job_store.save("facebook", _fb_jobs)
    if result["ok"]:
        _record_published_facebook(result["video_id"], title or Path(video_path).stem, Path(video_path).name, page_id)
        _cleanup_clip_folder_for_video(Path(video_path).name)


def _ig_set_stage(job_id: str, stage: str):
    with _ig_jobs_lock:
        if job_id in _ig_jobs:
            _ig_jobs[job_id]["stage"] = stage


def _run_instagram_job(
    job_id: str, video_path: str, title: str, description: str, page_id: Optional[str] = None, target_ts: Optional[float] = None
):
    if target_ts is not None:
        time.sleep(max(0, target_ts - time.time()))
        with _ig_jobs_lock:
            _ig_jobs[job_id]["status"] = "running"
    result = instagram_publisher.publish_video(
        video_path, title, description, page_id=page_id, on_status=lambda s: _ig_set_stage(job_id, s)
    )
    with _ig_jobs_lock:
        if result["ok"]:
            _ig_jobs[job_id].update(status="done", media_id=result.get("media_id"))
        else:
            _ig_jobs[job_id].update(
                status="error", error=result.get("error"), auth_error=result.get("auth_error", False)
            )
        job_store.save("instagram", _ig_jobs)
    if result["ok"]:
        _record_published_instagram(result.get("media_id"), title or Path(video_path).stem, Path(video_path).name, page_id)
        _cleanup_clip_folder_for_video(Path(video_path).name)


def _resolve_page_id(data: dict) -> tuple[Optional[str], Optional[dict]]:
    """Valida el page_id pedido contra las páginas configuradas (o toma la
    primera si no se especificó ninguna). Devuelve (page_id, err)."""
    pages = meta_auth.list_pages()
    if not pages:
        return None, {"ok": False, "error": meta_auth.MISSING_CREDENTIALS_ERROR}
    page_id = (data.get("page_id") or "").strip() or pages[0]["page_id"]
    if page_id not in {p["page_id"] for p in pages}:
        return None, {"ok": False, "error": "La página seleccionada no está configurada."}
    return page_id, None


@app.route("/api/facebook/publish", methods=["POST"])
def api_facebook_publish():
    data = request.get_json(force=True)
    filename = secure_filename_safe(data.get("filename", "").strip())
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()

    if not filename:
        return jsonify({"ok": False, "error": "Falta el video a publicar."}), 400

    page_id, page_err = _resolve_page_id(data)
    if page_err:
        return jsonify(page_err), 400

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
            "status": "running",
            "stage": None,
            "error": None,
            "video_id": None,
            "scheduled_time": None,
            "started_at": time.time(),
        }
        job_store.save("facebook", _fb_jobs)

    thread = threading.Thread(
        target=_run_facebook_job,
        args=(job_id, str(video_path), title, description, page_id, target_ts),
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
    filename = secure_filename_safe(data.get("filename", "").strip())
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()

    if not filename:
        return jsonify({"ok": False, "error": "Falta el video a publicar."}), 400

    page_id, page_err = _resolve_page_id(data)
    if page_err:
        return jsonify(page_err), 400

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
        args=(job_id, str(video_path), title, description, page_id, target_ts),
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


@app.route("/api/meta/pages")
def api_meta_pages():
    """Páginas de Facebook configuradas, para el selector de la sección Publicar."""
    return jsonify({"ok": True, "pages": meta_auth.list_pages()})


@app.route("/api/meta/token-status")
def api_meta_token_status():
    """Estado del token de Meta de la página pedida (Facebook + Instagram usan el mismo)."""
    return jsonify(meta_auth.validate(request.args.get("page_id")))


@app.route("/api/meta/reload-token", methods=["POST"])
def api_meta_reload_token():
    """
    Vuelve a leer .env y revalida el token de la página pedida. Es lo que
    permite pegar un token nuevo y seguir publicando sin reiniciar el servidor.
    """
    data = request.get_json(silent=True) or {}
    return jsonify(meta_auth.reload_env(data.get("page_id")))


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
            _cleanup_clip_folder_for_video(Path(video_path).name)
        else:
            _yt_jobs[job_id].update(status="error", error=result["error"])
        job_store.save("youtube", _yt_jobs)


@app.route("/api/youtube/publish", methods=["POST"])
def api_youtube_publish():
    data = request.get_json(force=True)
    # `or ""`: el front manda null (no ausente) para campos vacios, y
    # data.get(k, "") solo cubre la clave ausente -> None.strip() reventaba.
    filename = secure_filename_safe((data.get("filename") or "").strip())
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    privacy_status = (data.get("privacy_status") or "unlisted").strip()
    tags = [t.strip() for t in (data.get("tags") or "").split(",") if t.strip()]
    is_ai_generated = bool(data.get("is_ai_generated"))
    thumbnail_name = (data.get("thumbnail") or "").strip()

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


@app.route("/api/thumbnail/prompt", methods=["POST"])
def api_thumbnail_prompt():
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"ok": False, "error": "Falta el título."}), 400
    prompt = thumbnail_maker.build_chatgpt_prompt(title)
    return jsonify({"ok": True, "prompt": prompt})


@app.route("/api/thumbnail/upload", methods=["POST"])
def api_thumbnail_upload():
    file = request.files.get("image")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "Falta el archivo de imagen."}), 400

    out_name = f"thumbnail_{uuid.uuid4().hex}.jpg"
    out_path = video_maker.VIDEO_OUT_DIR / out_name
    try:
        thumbnail_maker.import_uploaded_thumbnail(file.read(), str(out_path))
    except Exception as e:
        return jsonify({"ok": False, "error": f"Error procesando la imagen: {e}"}), 500

    return jsonify({"ok": True, "filename": out_name, "url": f"/video/{out_name}"})


@app.route("/api/thumbnail/variants", methods=["POST"])
def api_thumbnail_variants():
    """Genera hasta 3 variantes de miniatura (A/B) usando distintas escenas del video, para elegir la más llamativa."""
    data = request.get_json(force=True)
    title = data.get("title", "").strip()

    scenes = sorted(video_maker.VIDEO_PUBLIC_DIR.glob("scene_*.*"))
    if not scenes:
        return jsonify({"ok": False, "error": "No hay escenas disponibles para generar miniaturas."}), 400

    # con fondo IA (Qwen, vía navegador) cada variante tarda bastante más que un
    # recorte local, así que se genera solo una; sin IA disponible (respaldo por
    # recorte de escena) es instantáneo y se generan hasta 3 para elegir entre ellas.
    max_variants = 1 if thumbnail_maker.is_ai_backend_available() else 3
    count = min(max_variants, len(scenes))
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


def _analytics_rows(path: Path, id_field: str, stats_fn, multi_page: bool = False) -> list:
    """Lee un JSON de publicados y le mezcla las stats (views/likes/comments) ya
    frescas. Si la consulta a la API funciona, esas stats quedan guardadas en
    el mismo archivo -- así, si una consulta futura falla (token vencido,
    rate limit, etc.) la fila conserva el último dato real en vez de quedar
    sin vistas.

    multi_page=True (Facebook/Instagram, con una página distinta por
    registro) también manda a stats_fn el mapeo {id: page_id} para que
    consulte cada uno con el token de su propia página."""
    try:
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        items = []

    ids = [i[id_field] for i in items]
    if multi_page:
        id_to_page = {i[id_field]: i.get("page_id") for i in items}
        stats_by_id = stats_fn(ids, id_to_page) if ids else {}
    else:
        stats_by_id = stats_fn(ids) if ids else {}

    changed = False
    for item in items:
        fresh = stats_by_id.get(item[id_field])
        if fresh and isinstance(fresh.get("views"), (int, float)) and fresh != {
            k: item.get(k) for k in ("views", "likes", "comments")
        }:
            item.update(fresh)
            changed = True
    if changed:
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

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
_published_records_lock = threading.Lock()  # protege el read-modify-write de estos JSON (varios jobs pueden terminar casi juntos)


def _record_published_item(path: Path, id_field: str, item_id, title: str, filename: str = "", page_id: Optional[str] = None) -> None:
    with _published_records_lock:
        try:
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except Exception:
            items = []
        items.append({
            id_field: item_id,
            "title": title,
            "filename": filename,
            "page_id": page_id,
            "published_at": datetime.now().isoformat(),
        })
        try:
            path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def _record_published_facebook(video_id, title: str, filename: str = "", page_id: Optional[str] = None) -> None:
    """Guarda cada video publicado en Facebook para poder consultar su analítica después."""
    if not video_id:
        return
    _record_published_item(_PUBLISHED_FB_PATH, "video_id", video_id, title, filename, page_id)


def _record_published_instagram(media_id, title: str, filename: str = "", page_id: Optional[str] = None) -> None:
    """Guarda cada media publicado en Instagram para poder consultar su analítica después."""
    if not media_id:
        return
    _record_published_item(_PUBLISHED_IG_PATH, "media_id", media_id, title, filename, page_id)


@app.route("/api/facebook/analytics")
def api_facebook_analytics():
    rows = _analytics_rows(_PUBLISHED_FB_PATH, "video_id", facebook_publisher.get_video_stats, multi_page=True)
    return jsonify({"ok": True, "videos": rows})


@app.route("/api/instagram/analytics")
def api_instagram_analytics():
    rows = _analytics_rows(_PUBLISHED_IG_PATH, "media_id", instagram_publisher.get_media_stats, multi_page=True)
    return jsonify({"ok": True, "videos": rows})


@app.route("/api/analytics/feedback")
def api_analytics_feedback():
    all_rows = (
        _analytics_rows(_PUBLISHED_VIDEOS_PATH, "video_id", youtube_publisher.get_video_stats)
        + _analytics_rows(_PUBLISHED_FB_PATH, "video_id", facebook_publisher.get_video_stats, multi_page=True)
        + _analytics_rows(_PUBLISHED_IG_PATH, "media_id", instagram_publisher.get_media_stats, multi_page=True)
    )
    result = feedback_analyzer.analyze_from_rows(all_rows)
    return jsonify({"ok": True, **result})


@app.route("/api/facebook/best-time")
def api_facebook_best_time():
    rows = _analytics_rows(_PUBLISHED_FB_PATH, "video_id", facebook_publisher.get_video_stats, multi_page=True)
    result = feedback_analyzer.best_posting_hour(rows)
    return jsonify({"ok": True, **result})


@app.route("/api/instagram/best-time")
def api_instagram_best_time():
    rows = _analytics_rows(_PUBLISHED_IG_PATH, "media_id", instagram_publisher.get_media_stats, multi_page=True)
    result = feedback_analyzer.best_posting_hour(rows)
    return jsonify({"ok": True, **result})


# ─────────────────────────────────────────────
# Generación en lote (módulo independiente, ver batch_pipeline.py)
# ─────────────────────────────────────────────

@app.route("/api/batch/create", methods=["POST"])
def api_batch_create():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    qwen_project = (data.get("qwen_project") or "").strip()
    if not name or not qwen_project:
        return jsonify({"ok": False, "error": "Falta el nombre del proyecto o el proyecto de Qwen."})
    content_type = (data.get("type") or "video").strip()
    default_trigger = "dame el próximo post gaming" if content_type == "gaming_image" else "dame una historia"
    networks = data.get("networks") or {}
    if content_type == "gaming_image":
        networks = {k: v for k, v in networks.items() if k != "youtube"}
    try:
        project = batch_pipeline.create_project(
            name=name,
            qwen_project=qwen_project,
            total_videos=data.get("total_videos", 1),
            per_day=data.get("per_day", 1),
            networks=networks,
            video_settings=data.get("video_settings") or {},
            trigger_message=(data.get("trigger_message") or "").strip() or default_trigger,
            content_type=content_type,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "project": project})


@app.route("/api/batch/get-public-url")
def api_batch_get_public_url():
    return jsonify({"ok": True, "url": batch_pipeline.get_public_base_url() or ""})


@app.route("/api/batch/set-public-url", methods=["POST"])
def api_batch_set_public_url():
    data = request.get_json(force=True) or {}
    batch_pipeline.set_public_base_url(data.get("url") or "")
    return jsonify({"ok": True})


@app.route("/api/batch/cover/<project_id>/<int:index>")
def api_batch_cover(project_id, index):
    """Sirve la imagen de portada de un post gaming del lote -- ruta publica
    minima para que Instagram (via Cloudflare Tunnel) pueda descargarla; la
    Graph API de Instagram no acepta subida de archivo local para imagenes."""
    project = batch_pipeline.get_project(project_id)
    video = project and next((v for v in project["videos"] if v["index"] == index), None)
    if not video or not video.get("video_path") or not Path(video["video_path"]).exists():
        return "No encontrado", 404
    return send_file(video["video_path"])


@app.route("/api/batch/list")
def api_batch_list():
    return jsonify({"ok": True, "projects": batch_pipeline.list_projects()})


@app.route("/api/batch/status/<project_id>")
def api_batch_status(project_id):
    project = batch_pipeline.get_project(project_id)
    if not project:
        return jsonify({"ok": False, "error": "Proyecto no encontrado."})
    return jsonify({"ok": True, "project": project})


@app.route("/api/batch/pause/<project_id>", methods=["POST"])
def api_batch_pause(project_id):
    return jsonify({"ok": batch_pipeline.pause_project(project_id)})


@app.route("/api/batch/resume/<project_id>", methods=["POST"])
def api_batch_resume(project_id):
    return jsonify({"ok": batch_pipeline.resume_project(project_id)})


@app.route("/api/batch/cancel/<project_id>", methods=["POST"])
def api_batch_cancel(project_id):
    return jsonify({"ok": batch_pipeline.cancel_project(project_id)})


@app.route("/api/batch/delete/<project_id>", methods=["POST"])
def api_batch_delete(project_id):
    return jsonify({"ok": batch_pipeline.delete_project(project_id)})


@app.route("/api/batch/retry/<project_id>/<int:index>", methods=["POST"])
def api_batch_retry(project_id, index):
    return jsonify({"ok": batch_pipeline.retry_video(project_id, index)})


@app.route("/api/batch/retry-publish/<project_id>/<int:index>", methods=["POST"])
def api_batch_retry_publish(project_id, index):
    return jsonify({"ok": batch_pipeline.retry_publish_video(project_id, index)})


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

batch_pipeline.start_scheduler()

if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("   🎙️  SERVIDOR TTS INICIADO")
    print("=" * 55)
    print(f"   → Abre en tu navegador: http://localhost:{PORT}")
    print("   → Ctrl+C para detener")
    print("=" * 55 + "\n")

    _token_status = meta_auth.validate()
    if not _token_status["ok"]:
        logger.warning("Token de Meta no válido: %s", _token_status["error"])

    if not (os.environ.get("DASHBOARD_USER") and os.environ.get("DASHBOARD_PASSWORD")):
        print("!" * 55)
        print("  AVISO: DASHBOARD_USER / DASHBOARD_PASSWORD no están en .env")
        print("  La app va a responder 500 a TODAS las requests hasta que los agregues.")
        print("!" * 55)
        logger.warning("DASHBOARD_USER/DASHBOARD_PASSWORD no configurados en .env")

    def _on_tunnel_url(url: str) -> None:
        batch_pipeline.set_public_base_url(url)
        print(f"   → URL pública (Cloudflare Quick Tunnel): {url}")
        logger.info("Cloudflare Quick Tunnel activo: %s", url)

    cloudflare_tunnel.start(PORT, _on_tunnel_url)
    atexit.register(cloudflare_tunnel.stop)

    app.run(debug=False, host="0.0.0.0", port=PORT, threaded=True)
