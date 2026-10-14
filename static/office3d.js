// Oficina 3D estilo Matrix: escena three.js con 4 escritorios, agentes-silueta y monitores con lluvia de código.
import * as THREE from "./vendor/three.module.min.js";
import { STATUS_STYLE, styleFor, fitCamera, clampOrbit, clampZoom, fogRange, ORBIT } from "./office3d-logic.js";

export { STATUS_STYLE, fitCamera };

const AGENTS = [
  { id: "videos", name: "VIDEOS" },
  { id: "publica", name: "PUBLICA" },
  { id: "metricas", name: "METRICAS" },
  { id: "revisor", name: "REVISOR" },
];
const SPACING = 3.4;
const TARGET = new THREE.Vector3(0, 1.2, 0);
const FRAME_MS = 33;   // ~30 fps
const RAIN_MS = 66;    // paso de la lluvia de los monitores
const DESK_COLOR = "#0a8f2a";
const GLYPHS = (() => {
  let s = "";
  for (let c = 0xFF66; c <= 0xFF9D; c++) s += String.fromCharCode(c);
  return s + "0123456789";
})();

export function isWebGLAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch (e) {
    return false;
  }
}

// Textura de canvas con lluvia de código (monitores y pared).
class Rain {
  constructor(w, h, cell) {
    this.w = w; this.h = h; this.cell = cell;
    this.canvas = document.createElement("canvas");
    this.canvas.width = w; this.canvas.height = h;
    this.ctx = this.canvas.getContext("2d");
    this.ctx.fillStyle = "#000";
    this.ctx.fillRect(0, 0, w, h);
    const rows = Math.floor(h / cell);
    this.drops = Array.from({ length: Math.floor(w / cell) }, () => Math.random() * rows);
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.texture.generateMipmaps = false;
    this.texture.minFilter = THREE.LinearFilter;
    this.color = "#00ff41"; this.speed = 0.35; this.alpha = 1; this.blink = false;
  }

  setStyle(st) { this.color = st.color; this.speed = st.speed; this.alpha = st.alpha; this.blink = st.blink; }

  step(blinkOn) {
    const { ctx, cell, w, h } = this;
    ctx.globalAlpha = 1;
    ctx.fillStyle = "rgba(0, 0, 0, " + (0.05 + 0.1 * Math.min(this.speed, 1)) + ")";
    ctx.fillRect(0, 0, w, h);
    ctx.font = cell + "px monospace";
    ctx.fillStyle = this.color;
    ctx.globalAlpha = this.alpha * (this.blink && !blinkOn ? 0.25 : 1);
    for (let i = 0; i < this.drops.length; i++) {
      const y = Math.floor(this.drops[i]) * cell;
      ctx.fillText(GLYPHS[Math.floor(Math.random() * GLYPHS.length)], i * cell, y);
      if (y > h && Math.random() > 0.96) this.drops[i] = 0;
      this.drops[i] += this.speed;
    }
    ctx.globalAlpha = 1;
    this.texture.needsUpdate = true;
  }

  warm(n) { for (let i = 0; i < n; i++) this.step(true); }
  dispose() { this.texture.dispose(); }
}

// Cartel de texto (globo y nombre) como sprite.
class Label {
  constructor(width, height, worldW) {
    this.w = width; this.h = height; this.key = "";
    this.canvas = document.createElement("canvas");
    this.canvas.width = width; this.canvas.height = height;
    this.ctx = this.canvas.getContext("2d");
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.texture.generateMipmaps = false;
    this.texture.minFilter = THREE.LinearFilter;
    this.material = new THREE.SpriteMaterial({ map: this.texture, transparent: true, depthTest: false });
    this.sprite = new THREE.Sprite(this.material);
    this.sprite.scale.set(worldW, worldW * height / width, 1);
    this.sprite.renderOrder = 10;
  }

  draw(text, color, framed) {
    const key = text + "|" + color + "|" + framed;
    if (key === this.key) return;
    this.key = key;
    const { ctx, w, h } = this;
    ctx.clearRect(0, 0, w, h);
    if (framed) {
      ctx.fillStyle = "rgba(0, 18, 4, 0.88)";
      ctx.fillRect(3, 3, w - 6, h - 6);
      ctx.strokeStyle = color;
      ctx.lineWidth = 3;
      ctx.strokeRect(3, 3, w - 6, h - 6);
    }
    ctx.fillStyle = color;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    let size = Math.floor(h * 0.5);
    ctx.font = "bold " + size + "px monospace";
    while (ctx.measureText(text).width > w - 24 && size > 10) {
      size -= 2;
      ctx.font = "bold " + size + "px monospace";
    }
    ctx.fillText(text, w / 2, h / 2 + 2);
    this.texture.needsUpdate = true;
  }

  dispose() { this.texture.dispose(); this.material.dispose(); }
}

// Caja en líneas (aristas) con un segundo trazo más tenue que simula el brillo.
function wire(geom, color, opacity, mats) {
  const edges = new THREE.EdgesGeometry(geom);
  geom.dispose();
  const g = new THREE.Group();
  const main = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
  const halo = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color, transparent: true, opacity: opacity * 0.3 }));
  halo.scale.setScalar(1.035);
  g.add(main, halo);
  mats.push(main.material, halo.material);
  return g;
}

function at(obj, x, y, z) { obj.position.set(x, y, z); return obj; }

function buildSlot(def, index) {
  const slot = new THREE.Group();
  slot.position.x = (index - 1.5) * SPACING;
  const deskMats = [], agentMats = [], selMats = [];

  // escritorio
  slot.add(at(wire(new THREE.BoxGeometry(2.8, 0.08, 1.3), DESK_COLOR, 0.9, deskMats), 0, 0.8, 0));
  [[-1.3, -0.55], [1.3, -0.55], [-1.3, 0.55], [1.3, 0.55]].forEach(p =>
    slot.add(at(wire(new THREE.BoxGeometry(0.08, 0.76, 0.08), DESK_COLOR, 0.7, deskMats), p[0], 0.4, p[1])));

  // monitor con la lluvia, girado hacia la cámara y hacia el agente
  const rain = new Rain(256, 160, 10);
  const monitor = new THREE.Group();
  monitor.add(wire(new THREE.BoxGeometry(1.7, 1.1, 0.1), "#00ff41", 0.9, deskMats));
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(1.55, 0.95),
    new THREE.MeshBasicMaterial({ map: rain.texture, toneMapped: false }));
  screen.position.z = 0.056;
  monitor.add(screen);
  at(monitor, -0.75, 1.5, 0.05).rotation.y = 0.4;
  slot.add(monitor);
  slot.add(at(wire(new THREE.BoxGeometry(0.12, 0.35, 0.12), DESK_COLOR, 0.8, deskMats), -0.75, 0.98, 0.05));

  // agente-silueta sentado junto al monitor, en tres cuartos
  const agent = new THREE.Group();
  agent.add(at(wire(new THREE.BoxGeometry(0.7, 0.08, 0.7), "#00ff41", 0.8, agentMats), 0, 0.5, 0));
  agent.add(at(wire(new THREE.BoxGeometry(0.7, 0.7, 0.08), "#00ff41", 0.8, agentMats), 0, 0.9, -0.35));
  agent.add(at(wire(new THREE.BoxGeometry(0.6, 0.75, 0.32), "#00ff41", 0.9, agentMats), 0, 1.0, 0));
  [-0.38, 0.38].forEach(x =>
    agent.add(at(wire(new THREE.BoxGeometry(0.14, 0.14, 0.6), "#00ff41", 0.9, agentMats), x, 1.0, 0.3)));
  const head = new THREE.Group();
  const skull = new THREE.Mesh(new THREE.SphereGeometry(0.26, 8, 6),
    new THREE.MeshBasicMaterial({ color: "#00ff41", wireframe: true, transparent: true, opacity: 0.85 }));
  agentMats.push(skull.material);
  head.add(skull);
  head.add(at(wire(new THREE.BoxGeometry(0.34, 0.07, 0.1), "#00ff41", 1, agentMats), 0, 0.03, 0.24));  // gafas
  at(head, 0, 1.7, 0).rotation.y = -0.9;  // mira hacia el monitor
  agent.add(head);
  at(agent, 0.8, 0, -0.55);
  slot.add(agent);

  // cartel de estado y nombre
  const bubble = new Label(256, 64, 2.5);
  at(bubble.sprite, 0.8, 2.6, -0.55);
  slot.add(bubble.sprite);
  const name = new Label(256, 64, 1.8);
  name.draw(def.name, "#7fd99a", false);
  at(name.sprite, 0, 0.3, 1.1);
  slot.add(name.sprite);

  // selección y zona de clic
  const sel = wire(new THREE.BoxGeometry(3.1, 2.7, 2.0), "#c8ffd6", 0.9, selMats);
  at(sel, 0, 1.3, -0.1);
  selMats.forEach(m => { m.opacity = 0; });
  slot.add(sel);
  const hit = new THREE.Mesh(new THREE.BoxGeometry(3.0, 2.6, 1.9),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }));
  at(hit, 0, 1.3, -0.1).userData.agentId = def.id;
  slot.add(hit);

  return { id: def.id, slot, agent, rain, bubble, name, hit, agentMats, selMats, status: "idle" };
}

export function mountOffice3D(container, opts) {
  const options = opts || {};
  const reducedMotion = !!options.reducedMotion;
  const abort = new AbortController();
  const listen = (target, type, fn, extra) =>
    target.addEventListener(type, fn, Object.assign({ signal: abort.signal }, extra || {}));

  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "low-power" });
  renderer.setClearColor(0x000000, 1);
  const canvas = renderer.domElement;
  canvas.setAttribute("aria-hidden", "true");
  // setSize(..., false) no toca el estilo: el canvas ocupa el contenedor por CSS.
  canvas.style.display = "block";
  canvas.style.width = "100%";
  canvas.style.height = "100%";
  // pan-y: el arrastre vertical con un dedo sigue desplazando la página; el horizontal y el pellizco son nuestros.
  canvas.style.touchAction = "pan-y";
  container.appendChild(canvas);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x000000, 14, 40);
  const camera = new THREE.PerspectiveCamera(40, 16 / 9, 0.1, 100);

  // sala: piso con cuadrícula y pared con lluvia grande
  const grid = new THREE.GridHelper(30, 30, 0x0a7a24, 0x0a5c1a);
  grid.material.transparent = true;
  grid.material.opacity = 0.45;
  scene.add(grid);
  const wallRain = new Rain(960, 256, 16);
  wallRain.setStyle({ color: "#00ff41", speed: 0.5, alpha: 0.8, blink: false });
  wallRain.warm(40);
  const wall = new THREE.Mesh(new THREE.PlaneGeometry(30, 8),
    new THREE.MeshBasicMaterial({ map: wallRain.texture, toneMapped: false, fog: false }));
  at(wall, 0, 4, -4.2);
  scene.add(wall);

  const slots = AGENTS.map((def, i) => buildSlot(def, i));
  slots.forEach(s => scene.add(s.slot));
  const hitBoxes = slots.map(s => s.hit);

  // cámara orbital mínima (pointer events)
  let az = 0, pol = ORBIT.polarDefault, zoom = 1, fit = fitCamera(16 / 9);
  function placeCamera() {
    const d = fit.distance * zoom;
    const fr = fogRange(d);
    scene.fog.near = fr.near;
    scene.fog.far = fr.far;
    camera.position.set(
      TARGET.x + d * Math.sin(pol) * Math.sin(az),
      TARGET.y + d * Math.cos(pol),
      TARGET.z + d * Math.sin(pol) * Math.cos(az));
    camera.lookAt(TARGET);
  }

  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function pick(clientX, clientY) {
    const r = canvas.getBoundingClientRect();
    ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(hitBoxes, false)[0];
    return hit ? hit.object.userData.agentId : null;
  }

  const pointers = new Map();
  let moved = 0, pinch = 0, gesture = false;
  listen(canvas, "pointerdown", e => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* sin captura */ }
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    moved = 0;
    if (pointers.size === 2) {
      gesture = true;
      const [a, b] = [...pointers.values()];
      pinch = Math.hypot(a.x - b.x, a.y - b.y);
    }
    canvas.style.cursor = "grabbing";
  });
  listen(canvas, "pointermove", e => {
    const prev = pointers.get(e.pointerId);
    if (!prev) {
      canvas.style.cursor = pick(e.clientX, e.clientY) ? "pointer" : "grab";
      return;
    }
    const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch > 0 && d > 0) zoom = clampZoom(zoom * pinch / d);
      pinch = d;
      return;
    }
    moved += Math.abs(dx) + Math.abs(dy);
    const o = clampOrbit(az - dx * 0.005, e.pointerType === "touch" ? pol : pol - dy * 0.005);
    az = o.az; pol = o.pol;
  });
  const release = e => {
    if (!pointers.has(e.pointerId)) return;
    const wasClick = e.type === "pointerup" && pointers.size === 1 && !gesture && moved < 6;
    pointers.delete(e.pointerId);
    if (pointers.size === 0) gesture = false;
    pinch = 0;
    canvas.style.cursor = "grab";
    if (wasClick) {
      const id = pick(e.clientX, e.clientY);
      if (id && options.onSelect) options.onSelect(id);
    }
  };
  listen(canvas, "pointerup", release);
  listen(canvas, "pointercancel", release);
  listen(canvas, "wheel", e => {
    const dy = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY;
    const next = clampZoom(zoom * (1 + dy * 0.001));
    if (next === zoom) return;  // en el límite: dejar que la página haga scroll
    e.preventDefault();
    zoom = next;
  }, { passive: false });
  listen(canvas, "dblclick", () => { az = 0; pol = ORBIT.polarDefault; zoom = 1; });
  listen(canvas, "webglcontextlost", e => {
    e.preventDefault();
    if (options.onLost) options.onLost();
  });

  // tamaño
  function resize() {
    const w = container.clientWidth, h = container.clientHeight;
    if (!w || !h) return;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    fit = fitCamera(camera.aspect);
    camera.fov = fit.fov;
    camera.updateProjectionMatrix();
    placeCamera();
    renderer.render(scene, camera);
  }
  const resizeObs = new ResizeObserver(resize);
  resizeObs.observe(container);

  // bucle: solo corre con la escena visible y la pestaña activa
  let raf = 0, lastFrame = 0, lastRain = 0, visible = true, disposed = false;
  function loop(now) {
    raf = requestAnimationFrame(loop);
    if (now - lastFrame < FRAME_MS) return;
    lastFrame = now;
    if (!reducedMotion) {
      if (now - lastRain >= RAIN_MS) {
        lastRain = now;
        const blinkOn = Math.floor(now / 500) % 2 === 0;
        slots.forEach(s => s.rain.step(blinkOn));
        wallRain.step(true);
      }
      slots.forEach(s => { s.agent.position.y = s.status === "busy" ? Math.sin(now / 400) * 0.02 : 0; });
    }
    placeCamera();
    renderer.render(scene, camera);
  }
  function syncLoop() {
    const want = visible && !document.hidden && !disposed;
    if (want && !raf) raf = requestAnimationFrame(loop);
    if (!want && raf) { cancelAnimationFrame(raf); raf = 0; }
  }
  const visObs = new IntersectionObserver(entries => {
    visible = entries[entries.length - 1].isIntersecting;
    syncLoop();
  });
  visObs.observe(container);
  listen(document, "visibilitychange", syncLoop);

  function update(agents, offline) {
    if (disposed) return;
    slots.forEach(s => {
      const v = (agents && agents[s.id]) || { status: "idle", bubble: "Sin datos" };
      const st = styleFor(v.status, !!offline);
      s.status = offline ? "idle" : v.status;
      s.rain.setStyle(st);
      s.agentMats.forEach(m => m.color.set(st.color));
      s.bubble.draw(offline ? "Sin conexión" : (v.bubble == null ? "" : String(v.bubble)), st.color, true);
      if (reducedMotion) s.rain.warm(30);
    });
    if (reducedMotion) renderer.render(scene, camera);
  }

  function setSelected(id) {
    slots.forEach(s => {
      const on = s.id === id;
      s.selMats.forEach((m, i) => { m.opacity = on ? (i % 2 === 0 ? 0.9 : 0.27) : 0; });
    });
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    syncLoop();
    abort.abort();
    resizeObs.disconnect();
    visObs.disconnect();
    scene.traverse(o => {
      if (o.geometry) o.geometry.dispose();
      if (o.material && o.material.dispose) o.material.dispose();
    });
    slots.forEach(s => { s.rain.dispose(); s.bubble.dispose(); s.name.dispose(); });
    wallRain.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
    if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
  }

  canvas.style.cursor = "grab";
  resize();
  update({}, false);
  syncLoop();
  return { update, setSelected, dispose };
}
