// Vault 3D estilo Fallout Shelter: corte de un refugio con 4 salas, ascensor y moradores que trabajan, caminan e interactúan.
import * as THREE from "./vendor/three.module.min.js";
import {
  STATUS_STYLE, styleFor, fitCamera, clampOrbit, clampZoom, ORBIT,
  ROOMS, ROOM_IDS, ROOM_W, ROOM_H, ROOM_D, SHAFT_W, FLOOR_Y, SURFACE_Y,
  roomCenterX, createWorld, setStatus, pokeDweller, stepWorld, nodePos,
} from "./vault3d-logic.js";

export { STATUS_STYLE, fitCamera };

const TARGET = new THREE.Vector3(0, 6.6, 0);
const FRAME_MS = 33;   // ~30 fps
const HIP_Y = 0.88;
const DW_SCALE = 1.3;
const C = {
  rock: "#3b2c1f", rock2: "#4a3828", wall: "#5b6b72", floor: "#2f3438", trim: "#f2c230", blue: "#1f62b8",
  dark: "#1a1a1a", metal: "#8a949a", wood: "#7a5a35", olive: "#6b7a3a", glass: "#cfe8ee", green: "#4ade80",
};

// agentes (con datos) y extras ambientales (decoración viva)
const DWELLERS = [
  { id: "videos",   kind: "agent", home: "videos",   lane: 0.55, skin: "#f2c9a0", hair: "#5a3b1e" },
  { id: "publica",  kind: "agent", home: "publica",  lane: 0.45, skin: "#c68b5e", hair: "#1f1a17" },
  { id: "metricas", kind: "agent", home: "metricas", lane: 0.60, skin: "#e8b98a", hair: "#c9a227" },
  { id: "revisor",  kind: "agent", home: "revisor",  lane: 0.50, skin: "#f2c9a0", hair: "#8c8c8c" },
  { id: "extra1",   kind: "extra", home: "videos",   lane: 0.10, skin: "#8d5a3b", hair: "#222222", node: "videos:b" },
  { id: "extra2",   kind: "extra", home: "metricas", lane: 0.85, skin: "#e8b98a", hair: "#a0522d", node: "metricas:a" },
  { id: "extra3",   kind: "extra", home: "revisor",  lane: 0.20, skin: "#f2c9a0", hair: "#d9d9d9", node: "revisor:b" },
];
const WORK_CLIP = { videos: "gen", publica: "mic", metricas: "lab", revisor: "term" };

export function isWebGLAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch (e) {
    return false;
  }
}

// ── Texturas de canvas ─────────────────────────────────────────────────────────────────────────
function canvasTex(w, h, draw, repeat) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  draw(c.getContext("2d"), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(repeat[0], repeat[1]); }
  return t;
}

function wallTexture() {
  return canvasTex(128, 128, (g, w, h) => {
    g.fillStyle = "#5b6b72"; g.fillRect(0, 0, w, h);
    g.strokeStyle = "#46555b"; g.lineWidth = 3; g.strokeRect(1.5, 1.5, w - 3, h - 3);
    g.fillStyle = "#46555b";
    [[10, 10], [w - 10, 10], [10, h - 10], [w - 10, h - 10]].forEach(p => { g.beginPath(); g.arc(p[0], p[1], 3, 0, 6.3); g.fill(); });
  }, [4, 2.5]);
}

function floorTexture() {
  return canvasTex(64, 64, (g, w, h) => {
    g.fillStyle = "#363c40"; g.fillRect(0, 0, w, h);
    g.strokeStyle = "#1f2326"; g.lineWidth = 2; g.strokeRect(1, 1, w - 2, h - 2);
  }, [8, 4]);
}

function hazardTexture() {
  return canvasTex(64, 64, (g, w, h) => {
    g.fillStyle = "#f2c230"; g.fillRect(0, 0, w, h);
    g.fillStyle = "#1b1b1b";
    for (let i = -h; i < w + h; i += 32) {
      g.beginPath(); g.moveTo(i, h); g.lineTo(i + 16, h); g.lineTo(i + 16 + h, 0); g.lineTo(i + h, 0); g.fill();
    }
  }, [5, 1]);
}

function textTexture(text, color, bg) {
  return canvasTex(128, 64, (g, w, h) => {
    g.fillStyle = bg; g.fillRect(0, 0, w, h);
    g.fillStyle = color; g.font = "bold 40px monospace"; g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText(text, w / 2, h / 2 + 2);
  });
}

function terminalTexture() {
  return canvasTex(128, 96, (g, w, h) => {
    g.fillStyle = "#07140c"; g.fillRect(0, 0, w, h);
    g.fillStyle = "#4ade80";
    [[10, 12, 90], [10, 28, 70], [10, 44, 100], [10, 60, 55], [10, 76, 80]].forEach(l => g.fillRect(l[0], l[1], l[2], 6));
  });
}

function skyTexture() {
  return canvasTex(8, 128, (g, w, h) => {
    const gr = g.createLinearGradient(0, 0, 0, h);
    gr.addColorStop(0, "#c9763a"); gr.addColorStop(0.55, "#e8a24a"); gr.addColorStop(1, "#f3cf8a");
    g.fillStyle = gr; g.fillRect(0, 0, w, h);
  });
}

// Cartel de texto (globo, emote y placas) como sprite.
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

  // style: { border, fill, text }
  draw(text, style) {
    const key = text + "|" + style.border + "|" + style.fill + "|" + style.text;
    if (key === this.key) return;
    this.key = key;
    const { ctx, w, h } = this;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = style.fill;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(3, 3, w - 6, h - 6, 14); else ctx.rect(3, 3, w - 6, h - 6);
    ctx.fill();
    ctx.strokeStyle = style.border; ctx.lineWidth = 5; ctx.stroke();
    ctx.fillStyle = style.text;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    let size = Math.floor(h * 0.5);
    ctx.font = "bold " + size + "px monospace";
    while (ctx.measureText(text).width > w - 28 && size > 10) { size -= 2; ctx.font = "bold " + size + "px monospace"; }
    ctx.fillText(text, w / 2, h / 2 + 2);
    this.texture.needsUpdate = true;
  }

  dispose() { this.texture.dispose(); this.material.dispose(); }
}

const BUBBLE = { fill: "#fff8e6", text: "#2b2418" };
const PLATE = { border: "#1b1b1b", fill: "#d8b53a", text: "#1b1b1b" };
const EMOTE_COLOR = { PANIC: "#f87171", ALERT: "#fbbf24" };

// ── Construcción de la escena ──────────────────────────────────────────────────────────────────
function makeKit() {
  const mats = new Map();
  const lam = (color, opts) => {
    const key = color + JSON.stringify(opts || {});
    if (!mats.has(key)) mats.set(key, new THREE.MeshLambertMaterial(Object.assign({ color }, opts || {})));
    return mats.get(key);
  };
  const basic = color => {
    const key = "b" + color;
    if (!mats.has(key)) mats.set(key, new THREE.MeshBasicMaterial({ color }));
    return mats.get(key);
  };
  const mesh = (geo, mat, parent, x, y, z) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x || 0, y || 0, z || 0);
    parent.add(m);
    return m;
  };
  const box = (w, h, d, mat, parent, x, y, z) => mesh(new THREE.BoxGeometry(w, h, d), mat, parent, x, y, z);
  return { mats, lam, basic, mesh, box };
}

function buildRoom(K, tex, id) {
  const r = ROOMS[id];
  const g = new THREE.Group();
  g.position.set(roomCenterX(id), FLOOR_Y[r.floor], 0);
  const wallMat = K.lam("#ffffff", { map: tex.wall });
  const floorMat = K.lam("#ffffff", { map: tex.floor });
  const trim = K.lam(C.trim);
  const metal = K.lam(C.metal);
  const dark = K.lam(C.dark);
  const wood = K.lam(C.wood);
  const w = ROOM_W, h = ROOM_H, d = ROOM_D;

  K.box(w, h, 0.2, wallMat, g, 0, h / 2, -d / 2);                       // pared trasera
  K.box(w, 0.35, 0.22, K.lam("#45545a"), g, 0, h - 0.18, -d / 2 + 0.02); // cenefa superior
  K.box(w, 0.6, d, floorMat, g, 0, -0.3, 0);                              // piso
  K.box(w, 0.12, 0.12, trim, g, 0, 0.0, d / 2 - 0.05);                    // borde amarillo frontal
  K.box(w, 0.18, 0.1, trim, g, 0, 0.1, -d / 2 + 0.15);                    // zócalo amarillo
  if (r.floor === 1) K.box(w, 0.6, d, K.lam(C.rock2), g, 0, h + 0.3, 0);  // techo (la superficie)
  // tuberías del techo y lámparas
  [-1.2, 0.2].forEach(zz => K.mesh(new THREE.CylinderGeometry(0.1, 0.1, w, 8), metal, g, 0, h - 0.5, zz - 1).rotation.z = Math.PI / 2);
  [-2.6, 2.6].forEach(xx => K.box(0.9, 0.12, 0.4, K.basic("#fff3b0"), g, xx, h - 0.1, -1.2));
  // marco de puerta hacia el ascensor
  const door = -r.side * (w / 2);
  K.box(0.18, 3.2, 0.3, trim, g, door, 1.6, -0.9);
  K.box(0.18, 3.2, 0.3, trim, g, door, 1.6, 0.9);
  K.box(0.18, 0.25, 2.1, trim, g, door, 3.25, 0);
  K.box(0.2, h - 3.4, d, K.lam("#45545a"), g, door, 3.4 + (h - 3.4) / 2, 0);

  const prop = { gear: null, onAir: null, rings: [], bars: [], screen: null, lamp: null };
  const px = abs => abs - roomCenterX(id);   // posición absoluta (x) → local

  if (id === "videos") {
    K.box(1.7, 1.0, 1.0, K.lam("#5d666b"), g, px(-6.9), 0.5, -0.9);       // pedestal del generador
    const gear = new THREE.Group();
    gear.position.set(px(-6.9), 2.25, -0.9);
    K.mesh(new THREE.CylinderGeometry(0.9, 0.9, 0.5, 20), K.lam("#9aa3a8"), gear).rotation.x = Math.PI / 2;
    for (let i = 0; i < 12; i++) {
      const a = i / 12 * Math.PI * 2;
      const t = K.box(0.26, 0.26, 0.5, K.lam("#7d868b"), gear, Math.cos(a) * 1.0, Math.sin(a) * 1.0, 0);
      t.rotation.z = a;
    }
    K.mesh(new THREE.CylinderGeometry(0.28, 0.28, 0.6, 12), trim, gear).rotation.x = Math.PI / 2;
    g.add(gear);
    prop.gear = gear;
    K.box(2.6, 0.04, 0.6, K.lam("#ffffff", { map: tex.hazard }), g, px(-5.5), 0.03, 0.2);
    K.box(1.7, 1.1, 0.12, K.lam(C.dark), g, px(-2.6), 3.2, -d / 2 + 0.16);   // pantalla ▶
    const tri = K.mesh(new THREE.CylinderGeometry(0.32, 0.32, 0.06, 3), K.basic(C.green), g, px(-2.6), 3.2, -d / 2 + 0.24);
    tri.rotation.set(Math.PI / 2, 0, -Math.PI / 2);
  } else if (id === "publica") {
    K.box(2.4, 0.9, 1.0, wood, g, px(6.6), 0.45, -0.3);                    // mesa
    K.mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.55, 8), dark, g, px(6.1), 1.17, -0.3);
    K.mesh(new THREE.SphereGeometry(0.13, 12, 10), dark, g, px(6.1), 1.52, -0.3);
    prop.onAir = K.box(1.7, 0.55, 0.12, new THREE.MeshBasicMaterial({ color: "#4a1212" }), g, px(5.2), 3.6, -d / 2 + 0.16);
    K.box(1.4, 0.4, 0.02, new THREE.MeshBasicMaterial({ map: tex.onAir }), g, px(5.2), 3.6, -d / 2 + 0.24);
    K.mesh(new THREE.CylinderGeometry(0.06, 0.1, 3.4, 8), metal, g, px(8.2), 1.7, -1.3);
    K.mesh(new THREE.SphereGeometry(0.18, 10, 8), trim, g, px(8.2), 3.5, -1.3);
    for (let i = 0; i < 3; i++) {
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.4, 0.46, 24),
        new THREE.MeshBasicMaterial({ color: "#f2c230", transparent: true, opacity: 0, side: THREE.DoubleSide }));
      ring.position.set(px(8.2), 3.5, -1.3);
      g.add(ring);
      prop.rings.push(ring);
    }
  } else if (id === "metricas") {
    K.box(3.2, 2.1, 0.12, K.lam("#0e1a14"), g, px(-6.3), 2.8, -d / 2 + 0.16);   // panel de gráficas
    [0.55, 0.9, 0.7, 1.25, 0.95].forEach((hh, i) => {
      const bar = K.box(0.38, 1, 0.12, K.basic(C.green), g, px(-7.4 + i * 0.55), 1.95 + hh / 2, -d / 2 + 0.3);
      bar.scale.y = hh; bar.userData.base = hh;
      prop.bars.push(bar);
    });
    K.box(1.8, 0.9, 1.0, wood, g, px(-3.1), 0.45, -0.3);                    // mesa de laboratorio
    [[-3.5, "#66ff99"], [-2.8, "#7ad7ff"]].forEach(f => {
      K.mesh(new THREE.ConeGeometry(0.22, 0.45, 12), K.lam(C.glass, { transparent: true, opacity: 0.8 }), g, px(f[0]), 1.13, -0.3);
      K.mesh(new THREE.ConeGeometry(0.17, 0.2, 12), K.basic(f[1]), g, px(f[0]), 1.03, -0.3);
    });
  } else {
    K.box(2.6, 0.9, 1.0, wood, g, px(6.5), 0.45, -0.3);                    // escritorio
    const term = new THREE.Group();
    term.position.set(px(6.5), 1.45, -0.35);
    term.rotation.y = -0.35;
    K.box(1.1, 0.8, 0.5, dark, term, 0, 0, 0);
    prop.screen = K.mesh(new THREE.PlaneGeometry(0.95, 0.65), new THREE.MeshBasicMaterial({ map: tex.terminal }), term, 0, 0.02, 0.26);
    g.add(term);
    K.box(0.9, 1.5, 0.06, K.lam(C.blue), g, px(2.8), 3.3, -d / 2 + 0.14);    // bandera Vault-Tec
    K.mesh(new THREE.CircleGeometry(0.3, 16), K.basic(C.trim), g, px(2.8), 3.3, -d / 2 + 0.2);
    K.mesh(new THREE.CircleGeometry(0.12, 12), K.basic(C.blue), g, px(2.8), 3.3, -d / 2 + 0.21);
    prop.lamp = K.mesh(new THREE.SphereGeometry(0.2, 12, 10), new THREE.MeshBasicMaterial({ color: "#5a1a1a" }), g, px(3.6), h - 0.3, -1.2);
  }

  // LED de estado y luz de la sala
  const led = K.mesh(new THREE.SphereGeometry(0.16, 12, 10), new THREE.MeshBasicMaterial({ color: STATUS_STYLE.idle.color }), g, w / 2 - 0.5, h - 0.55, d / 2 - 0.3);
  const light = new THREE.PointLight("#cdbd9a", 20, 14, 1.6);
  light.position.set(0, h - 0.8, 1.6);
  g.add(light);

  // placa con el nombre
  const plate = new Label(256, 64, 2.3);
  plate.draw(r.label, PLATE);
  plate.sprite.position.set(-2.4, h - 0.9, -d / 2 + 0.4);
  g.add(plate.sprite);

  // selección y zona de clic
  const sel = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(w + 0.3, h + 0.3, d + 0.3)),
    new THREE.LineBasicMaterial({ color: "#4ade80", transparent: true, opacity: 0 }));
  sel.position.set(0, h / 2, 0);
  g.add(sel);
  const hit = new THREE.Mesh(new THREE.BoxGeometry(w, h, 0.3),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }));
  hit.position.set(0, h / 2, d / 2 - 0.2);
  hit.userData.roomId = id;
  g.add(hit);

  return { id, group: g, led, light, plate, sel, hit, prop };
}

function buildSurroundings(K, tex) {
  const g = new THREE.Group();
  const rock = K.lam(C.rock), rock2 = K.lam(C.rock2);
  const top = SURFACE_Y + 0.6;
  K.box(26, top + 3.6, 4, rock, g, 0, (top - 3.6) / 2, -4.2);                    // fondo
  K.box(3, top + 3.6, 8.4, rock, g, -10.5, (top - 3.6) / 2, -2);                 // laterales
  K.box(3, top + 3.6, 8.4, rock, g, 10.5, (top - 3.6) / 2, -2);
  K.box(26, 3, 8.4, rock, g, 0, -2.1, -2);                                        // base
  // peñascos en los bordes
  let s = 11;
  const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  for (let i = 0; i < 26; i++) {
    const b = K.box(0.8 + rnd() * 1.4, 0.8 + rnd() * 1.3, 0.8 + rnd(), rock2, g,
      (rnd() < 0.5 ? -1 : 1) * (9.2 + rnd() * 2.6), -3 + rnd() * 14, 1.4 + rnd() * 0.8);
    b.rotation.set(rnd(), rnd(), rnd());
  }
  // eje del ascensor
  K.box(SHAFT_W, SURFACE_Y + 0.6, 0.3, K.lam("#1c1a17"), g, 0, (SURFACE_Y - 0.6) / 2, -ROOM_D / 2 + 0.05);
  [-0.55, 0.55].forEach(x => K.box(0.08, SURFACE_Y, 0.1, K.lam("#3a362f"), g, x, SURFACE_Y / 2 - 0.3, -ROOM_D / 2 + 0.25));
  // superficie
  K.box(26, 0.5, 6, K.lam("#7a5632"), g, 0, SURFACE_Y + 0.25, -2.5);
  const sky = new THREE.Mesh(new THREE.PlaneGeometry(70, 24), new THREE.MeshBasicMaterial({ map: tex.sky, fog: false }));
  sky.position.set(0, SURFACE_Y + 11, -9);
  g.add(sky);
  [[-4, 1.3, 3], [4.5, 1.8, 3.4], [9, 1.1, 2.4]].forEach(hh => {
    const hill = K.mesh(new THREE.CylinderGeometry(hh[2] * 0.6, hh[2] * 1.2, hh[1] * 2, 7), K.lam("#7a5632"), g, hh[0], SURFACE_Y + 0.9 + hh[1] * 0.4, -4.5);
    hill.scale.z = 0.6;
  });
  // puerta del Vault (engranaje con 111)
  const door = new THREE.Group();
  door.position.set(-7.2, SURFACE_Y + 1.15, -1.2);
  door.scale.setScalar(0.85);
  const dm = K.lam("#8f969a");
  K.mesh(new THREE.CylinderGeometry(1.6, 1.6, 0.5, 24), dm, door).rotation.x = Math.PI / 2;
  for (let i = 0; i < 14; i++) {
    const a = i / 14 * Math.PI * 2;
    K.box(0.4, 0.4, 0.5, K.lam("#6f777b"), door, Math.cos(a) * 1.7, Math.sin(a) * 1.7, 0).rotation.z = a;
  }
  K.mesh(new THREE.CylinderGeometry(0.7, 0.7, 0.6, 16), K.lam(C.trim), door).rotation.x = Math.PI / 2;
  K.mesh(new THREE.PlaneGeometry(0.9, 0.45), new THREE.MeshBasicMaterial({ map: tex.n111, transparent: true }), door, 0, 0, 0.32);
  g.add(door);
  return g;
}

// Contenedor de una pieza con contorno cartoon (casco invertido).
function buildDweller(K, G, skinHex, hairHex) {
  const root = new THREE.Group();
  const suit = K.lam(C.blue), trim = K.lam(C.trim), dark = K.lam(C.dark);
  const skin = K.lam(skinHex), hair = K.lam(hairHex);
  const outlineMat = new THREE.MeshBasicMaterial({ color: "#101010", side: THREE.BackSide });
  const part = (geo, mat, parent, x, y, z, k) => {
    const grp = new THREE.Group();
    grp.position.set(x, y, z);
    grp.add(new THREE.Mesh(geo, mat));
    if (k) { const o = new THREE.Mesh(geo, outlineMat); o.scale.setScalar(k); grp.add(o); }
    parent.add(grp);
    return grp;
  };
  const joint = (parent, x, y, z) => { const j = new THREE.Group(); j.position.set(x, y, z); parent.add(j); return j; };
  const rig = {};

  rig.hips = joint(root, 0, HIP_Y, 0);
  part(G.pelvis, suit, rig.hips, 0, 0, 0, 1.06);
  rig.torso = joint(rig.hips, 0, 0.08, 0);
  part(G.torso, suit, rig.torso, 0, 0.3, 0, 1.05).scale.set(1, 1, 0.78);
  const ring = new THREE.Mesh(G.collar, trim);
  ring.position.y = 0.62; ring.rotation.x = Math.PI / 2;
  rig.torso.add(ring);
  const stripe = new THREE.Mesh(G.stripe, trim);
  stripe.position.set(0, 0.28, 0.157);
  rig.torso.add(stripe);
  [[0.17, 0], [-0.17, Math.PI]].forEach(p => {
    const num = new THREE.Mesh(G.num, G.numMat);
    num.position.set(0.0, 0.42, p[0]); num.rotation.y = p[1];
    rig.torso.add(num);
  });

  rig.head = joint(rig.torso, 0, 0.7, 0);
  part(G.neck, skin, rig.head, 0, 0.0, 0, 0);
  part(G.head, skin, rig.head, 0, 0.3, 0, 1.05);
  const cap = new THREE.Mesh(G.hair, hair);
  cap.position.set(0, 0.31, -0.01); cap.rotation.x = -0.35;
  rig.head.add(cap);
  [-0.11, 0.11].forEach(x => {
    const e = new THREE.Mesh(G.eye, dark); e.position.set(x, 0.33, 0.265); rig.head.add(e);
    const ear = new THREE.Mesh(G.ear, skin); ear.position.set(x * 2.7, 0.3, 0); rig.head.add(ear);
  });
  const mouth = new THREE.Mesh(G.mouth, K.lam("#7a4a30"));
  mouth.position.set(0, 0.23, 0.27); mouth.rotation.z = Math.PI;
  rig.head.add(mouth);

  const arm = side => {  // side: +1 = brazo izquierdo del morador (+x, lleva el Pip-Boy)
    const sh = joint(rig.torso, 0.27 * side, 0.52, 0);
    part(G.upperArm, suit, sh, 0, -0.17, 0, 1.08);
    const cuff = new THREE.Mesh(G.cuff, trim); cuff.position.y = -0.08; cuff.rotation.x = Math.PI / 2; sh.add(cuff);
    const el = joint(sh, 0, -0.33, 0);
    part(G.foreArm, suit, el, 0, -0.15, 0, 1.08);
    part(G.hand, skin, el, 0, -0.36, 0, 1.1);
    if (side > 0) {
      const pb = new THREE.Mesh(G.pip, K.lam(C.olive)); pb.position.set(0.02, -0.2, 0); el.add(pb);
      const sc = new THREE.Mesh(G.pipScreen, G.pipScreenMat); sc.position.set(0.02, -0.2, 0.07); el.add(sc);
    }
    return { s: sh, e: el };
  };
  const aL = arm(1), aR = arm(-1);
  rig.sL = aL.s; rig.eL = aL.e; rig.sR = aR.s; rig.eR = aR.e;

  const leg = side => {
    const hp = joint(rig.hips, 0.11 * side, -0.05, 0);
    part(G.thigh, suit, hp, 0, -0.2, 0, 1.08);
    const kn = joint(hp, 0, -0.4, 0);
    part(G.shin, suit, kn, 0, -0.18, 0, 1.08);
    part(G.boot, dark, kn, 0, -0.4, 0.04, 0);
    return { h: hp, k: kn };
  };
  const lL = leg(1), lR = leg(-1);
  rig.hL = lL.h; rig.kL = lL.k; rig.hR = lR.h; rig.kR = lR.k;

  const shadow = new THREE.Mesh(G.shadow, G.shadowMat);
  shadow.rotation.x = -Math.PI / 2; shadow.position.y = 0.03;
  root.add(shadow);
  root.scale.setScalar(DW_SCALE);
  return { root, rig };
}

function makeDwellerGeo() {
  const G = {
    pelvis: new THREE.BoxGeometry(0.4, 0.2, 0.26),
    torso: new THREE.CapsuleGeometry(0.2, 0.3, 4, 12),
    collar: new THREE.TorusGeometry(0.14, 0.035, 8, 16),
    stripe: new THREE.BoxGeometry(0.035, 0.42, 0.012),
    num: new THREE.PlaneGeometry(0.2, 0.1),
    neck: new THREE.CylinderGeometry(0.07, 0.08, 0.14, 8),
    head: new THREE.SphereGeometry(0.3, 20, 16),
    hair: new THREE.SphereGeometry(0.315, 20, 14, 0, Math.PI * 2, 0, Math.PI * 0.52),
    eye: new THREE.SphereGeometry(0.05, 8, 6),
    ear: new THREE.SphereGeometry(0.06, 8, 6),
    mouth: new THREE.TorusGeometry(0.07, 0.013, 6, 12, Math.PI),
    upperArm: new THREE.CapsuleGeometry(0.07, 0.2, 4, 8),
    cuff: new THREE.TorusGeometry(0.076, 0.018, 6, 12),
    foreArm: new THREE.CapsuleGeometry(0.065, 0.17, 4, 8),
    hand: new THREE.SphereGeometry(0.075, 10, 8),
    pip: new THREE.BoxGeometry(0.16, 0.12, 0.14),
    pipScreen: new THREE.BoxGeometry(0.1, 0.07, 0.02),
    thigh: new THREE.CapsuleGeometry(0.085, 0.22, 4, 8),
    shin: new THREE.CapsuleGeometry(0.075, 0.2, 4, 8),
    boot: new THREE.BoxGeometry(0.18, 0.1, 0.28),
    shadow: new THREE.CircleGeometry(0.5, 16),
    shadowMat: new THREE.MeshBasicMaterial({ color: "#000", transparent: true, opacity: 0.35, depthWrite: false }),
    pipScreenMat: new THREE.MeshBasicMaterial({ color: "#7dff9a" }),
    numMat: new THREE.MeshBasicMaterial({ map: textTexture("111", "#f2c230", "#1f62b8"), transparent: false }),
  };
  return G;
}

// ── Animación procedural de los moradores ──────────────────────────────────────────────────────
const JOINTS = ["torso", "head", "sL", "eL", "sR", "eR", "hL", "kL", "hR", "kR"];

function applyPose(rig, d, t) {
  JOINTS.forEach(j => rig[j].rotation.set(0, 0, 0));
  rig.hips.position.y = HIP_Y;
  rig.torso.scale.y = 1;
  const anim = d.anim;
  const R = (j, x, y, z) => rig[j].rotation.set(x || 0, y || 0, z || 0);
  if (anim === "walk" || anim === "run") {
    const run = anim === "run";
    const p = t * (run ? 11 : 7), a = run ? 0.9 : 0.62, s = Math.sin(p);
    R("hL", s * a); R("hR", -s * a);
    R("kL", Math.max(0, Math.cos(p)) * a * 1.2); R("kR", Math.max(0, -Math.cos(p)) * a * 1.2);
    R("sL", -s * a * 0.8, 0, 0.08); R("sR", s * a * 0.8, 0, -0.08);
    R("eL", run ? -1.2 : -0.35); R("eR", run ? -1.2 : -0.35);
    R("torso", run ? 0.22 : 0.06);
    rig.hips.position.y = HIP_Y + Math.abs(Math.cos(p)) * 0.05;
  } else if (anim === "work") {
    const v = WORK_CLIP[d.home];
    if (v === "term") {
      R("sL", -1.1); R("sR", -1.1); R("eL", -1.0 + Math.sin(t * 14) * 0.12); R("eR", -1.0 + Math.sin(t * 14 + 3.1) * 0.12);
      R("head", 0.25); R("torso", 0.1);
    } else if (v === "gen") {
      R("sR", -1.2 + Math.sin(t * 4) * 0.5, 0, -0.2); R("eR", -0.8 + Math.cos(t * 4) * 0.5);
      R("sL", -0.9); R("eL", -0.6); R("torso", 0.15 + Math.sin(t * 4) * 0.05); R("head", 0.1);
    } else if (v === "mic") {
      R("sR", -1.3); R("eR", -1.8); R("sL", -0.3 + Math.sin(t * 3) * 0.4, 0, 0.1); R("eL", -0.8);
      R("head", Math.sin(t * 5) * 0.08, Math.sin(t * 1.5) * 0.1);
    } else {
      R("sL", -1.0); R("sR", -1.0, 0, Math.sin(t * 5) * 0.25); R("eL", -1.1); R("eR", -1.1); R("head", 0.3); R("torso", 0.08);
    }
  } else if (anim === "talk") {
    R("head", Math.sin(t * 6) * 0.06, Math.sin(t * 2) * 0.15);
    R("sR", -0.7 + Math.sin(t * 3.5) * 0.4, 0, -0.1); R("eR", -1.0 + Math.sin(t * 3.5 + 1) * 0.3); R("sL", 0, 0, 0.1);
  } else if (anim === "wave") {
    R("sR", 0, 0, -2.7); rig.eR.rotation.z = Math.sin(t * 10) * 0.5;
    R("head", 0, 0.2); R("sL", 0, 0, 0.1);
  } else if (anim === "alert") {
    R("sL", -1.2); R("eL", -1.5); R("head", 0.35, 0.35); R("sR", 0, 0, -0.1);
  } else {  // idle, ride, panic base
    const b = Math.sin(t * 2);
    rig.torso.scale.y = 1 + 0.02 * b;
    R("sL", 0, 0, 0.08 + 0.02 * b); R("sR", 0, 0, -0.08 - 0.02 * b);
    R("head", 0, Math.sin(t * 0.7) * 0.3);
  }
  if (d.activity === "PANIC") {  // brazos arriba y cabeza agitada, caminando o quieto
    R("sL", 0, 0, 2.7 + Math.sin(t * 18) * 0.2); R("sR", 0, 0, -2.7 - Math.sin(t * 18 + 1) * 0.2);
    R("head", 0, Math.sin(t * 12) * 0.3);
    if (d.anim === "panic") rig.hips.position.y = HIP_Y + Math.abs(Math.sin(t * 14)) * 0.06;
  }
}

function angleDiff(a, b) {
  let d = (b - a) % (Math.PI * 2);
  if (d > Math.PI) d -= Math.PI * 2;
  if (d < -Math.PI) d += Math.PI * 2;
  return d;
}

// ── Montaje ────────────────────────────────────────────────────────────────────────────────────
export function mountVault3D(container, opts) {
  const options = opts || {};
  const reducedMotion = !!options.reducedMotion;
  const abort = new AbortController();
  const listen = (target, type, fn, extra) =>
    target.addEventListener(type, fn, Object.assign({ signal: abort.signal }, extra || {}));

  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "low-power" });
  renderer.setClearColor(0x2a2018, 1);
  const canvas = renderer.domElement;
  canvas.setAttribute("aria-hidden", "true");
  canvas.style.display = "block";
  canvas.style.width = "100%";
  canvas.style.height = "100%";
  canvas.style.touchAction = "pan-y";  // el arrastre vertical con un dedo sigue desplazando la página
  container.appendChild(canvas);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(24, 4 / 3, 0.5, 300);
  scene.add(new THREE.HemisphereLight("#fff1d6", "#3a2a1c", 2.5));
  const sun = new THREE.DirectionalLight("#ffe8c0", 1.1);
  sun.position.set(-6, 14, 16);
  scene.add(sun);

  const K = makeKit();
  const tex = {
    wall: wallTexture(), floor: floorTexture(), hazard: hazardTexture(), sky: skyTexture(),
    onAir: textTexture("ON AIR", "#ffffff", "#c01818"), terminal: terminalTexture(), n111: textTexture("111", "#1b1b1b", "rgba(0,0,0,0)"),
  };
  scene.add(buildSurroundings(K, tex));
  const rooms = {};
  ROOM_IDS.forEach(id => { rooms[id] = buildRoom(K, tex, id); scene.add(rooms[id].group); });

  // ascensor
  const cab = new THREE.Group();
  const cabTrim = K.lam(C.trim);
  K.box(1.8, 0.14, 2.4, cabTrim, cab, 0, 0.07, 0);
  [[-0.85, -1.1], [0.85, -1.1], [-0.85, 1.1], [0.85, 1.1]].forEach(p => K.box(0.1, 3.0, 0.1, cabTrim, cab, p[0], 1.5, p[1]));
  K.box(1.8, 0.14, 2.4, cabTrim, cab, 0, 3.0, 0);
  K.box(1.7, 2.8, 0.05, K.lam("#2a2825"), cab, 0, 1.5, -1.15);
  scene.add(cab);
  const cable = K.box(0.05, 1, 0.05, K.lam("#2a2825"), scene, 0, 0, -0.4);

  // moradores
  const G = makeDwellerGeo();
  const world = createWorld(DWELLERS, Math.random);
  const vis = DWELLERS.map((def, i) => {
    const { root, rig } = buildDweller(K, G, def.skin, def.hair);
    scene.add(root);
    const hit = new THREE.Mesh(new THREE.BoxGeometry(1.0, 2.4, 1.0),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }));
    hit.position.y = 1.2; hit.userData.dwellerId = def.id;
    root.add(hit);
    const bubble = def.kind === "agent" ? new Label(256, 64, 2.6) : null;
    if (bubble) { bubble.sprite.position.set(0, 3.2, 0); root.add(bubble.sprite); }
    const emote = new Label(192, 64, 1.9);
    emote.sprite.position.set(0, 2.55, 0); emote.sprite.visible = false;
    root.add(emote.sprite);
    return { def, d: world.dwellers[i], root, rig, hit, bubble, emote, yaw: 0, off: i * 1.7 };
  });
  const byId = Object.fromEntries(vis.map(v => [v.def.id, v]));
  const hitDwellers = vis.map(v => v.hit);
  const hitRooms = ROOM_IDS.map(id => rooms[id].hit);

  // cámara orbital mínima (pointer events)
  let az = 0, pol = ORBIT.polarDefault, zoom = 1, fit = fitCamera(4 / 3);
  function placeCamera() {
    const dist = fit.distance * zoom;
    camera.position.set(
      TARGET.x + dist * Math.sin(pol) * Math.sin(az),
      TARGET.y + dist * Math.cos(pol),
      TARGET.z + dist * Math.sin(pol) * Math.cos(az));
    camera.lookAt(TARGET);
  }

  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function pick(clientX, clientY) {
    const r = canvas.getBoundingClientRect();
    ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const dw = ray.intersectObjects(hitDwellers, false)[0];
    if (dw) return { dweller: dw.object.userData.dwellerId };
    const rm = ray.intersectObjects(hitRooms, false)[0];
    return rm ? { room: rm.object.userData.roomId } : null;
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
      const dd = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch > 0 && dd > 0) zoom = clampZoom(zoom * pinch / dd);
      pinch = dd;
      return;
    }
    moved += Math.abs(dx) + Math.abs(dy);
    const o = clampOrbit(az - dx * 0.004, e.pointerType === "touch" ? pol : pol - dy * 0.004);
    az = o.az; pol = o.pol;
  });
  const release = e => {
    if (!pointers.has(e.pointerId)) return;
    const wasClick = e.type === "pointerup" && pointers.size === 1 && !gesture && moved < 6;
    pointers.delete(e.pointerId);
    if (pointers.size === 0) gesture = false;
    pinch = 0;
    canvas.style.cursor = "grab";
    if (!wasClick) return;
    const hit = pick(e.clientX, e.clientY);
    if (!hit) return;
    if (hit.dweller) pokeDweller(byId[hit.dweller].d);
    const agent = hit.room || (byId[hit.dweller].def.kind === "agent" ? hit.dweller : null);
    if (agent && options.onSelect) options.onSelect(agent);
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

  // estado visual de cada sala (LED, luz y props) según el estado del agente
  const roomState = {};
  ROOM_IDS.forEach(id => { roomState[id] = { status: "idle", light: 0 }; });
  const tmpColor = new THREE.Color();

  function animateRooms(time, dt) {
    ROOM_IDS.forEach(id => {
      const R = rooms[id], st = roomState[id];
      const style = styleFor(st.status, false);
      const busy = st.status === "busy";
      const target = style.intensity * (st.status === "error" ? 0.7 + 0.3 * Math.sin(time * 6) : 1);
      st.light += (target - st.light) * Math.min(1, dt * 4);
      R.light.intensity = 30 * st.light;
      R.light.color.set(style.light);
      R.led.material.color.set(style.color);
      R.led.scale.setScalar(busy ? 1 + 0.2 * Math.sin(time * 6) : 1);
      const p = R.prop;
      if (p.gear) p.gear.rotation.z -= dt * (busy ? 1.6 : 0.12);
      if (p.onAir) p.onAir.material.color.set(busy ? "#c01818" : "#4a1212");
      p.rings.forEach((ring, i) => {
        const ph = ((time * 0.8 + i / 3) % 1);
        ring.scale.setScalar(1 + ph * 2.2);
        ring.material.opacity = busy ? (1 - ph) * 0.9 : 0;
      });
      p.bars.forEach((bar, i) => {
        const f = busy ? 0.65 + 0.35 * Math.sin(time * 2.2 + i * 1.3) : 1;
        bar.scale.y = Math.max(0.05, bar.userData.base * f);
        bar.position.y = 1.95 + bar.scale.y / 2;
      });
      if (p.lamp) p.lamp.material.color.set(st.status === "error" ? (Math.sin(time * 8) > 0 ? "#ff4040" : "#5a1a1a") : "#5a1a1a");
    });
  }

  function syncVisuals(time, dt) {
    vis.forEach(v => {
      const d = v.d;
      v.root.position.set(d.pos.x, d.pos.y, d.pos.z);
      v.yaw += angleDiff(v.yaw, d.rotY) * Math.min(1, dt * 9);
      v.root.rotation.y = v.yaw;
      applyPose(v.rig, d, time + v.off);
      const text = d.emote;
      v.emote.sprite.visible = !!text;
      if (text) {
        const color = EMOTE_COLOR[d.activity];
        v.emote.draw(text, { border: color || "#9a9a9a", fill: BUBBLE.fill, text: color ? "#2b2418" : BUBBLE.text });
      }
    });
    cab.position.y = world.elev.y;
    cable.scale.y = Math.max(0.01, SURFACE_Y - (world.elev.y + 3.0));
    cable.position.y = world.elev.y + 3.0 + cable.scale.y / 2;
    ROOM_IDS.forEach(id => {
      const R = rooms[id];
      R.sel.material.opacity = selected === id ? 0.7 + 0.3 * Math.sin(time * 4) : 0;
    });
    animateRooms(time, dt);
  }

  function renderFrame(dt) {
    syncVisuals(time, dt);
    placeCamera();
    renderer.render(scene, camera);
  }

  // bucle: solo corre con la escena visible y la pestaña activa
  let raf = 0, lastFrame = 0, time = 0, visible = true, disposed = false, selected = null;
  function loop(now) {
    raf = requestAnimationFrame(loop);
    if (now - lastFrame < FRAME_MS) return;
    const dt = Math.min(0.1, lastFrame ? (now - lastFrame) / 1000 : 0.033);
    lastFrame = now;
    time += dt;
    if (!reducedMotion) stepWorld(world, dt);
    renderFrame(dt);
  }
  function syncLoop() {
    const want = visible && !document.hidden && !disposed;
    if (want && !raf) { lastFrame = 0; raf = requestAnimationFrame(loop); }
    if (!want && raf) { cancelAnimationFrame(raf); raf = 0; }
  }
  const visObs = new IntersectionObserver(entries => {
    visible = entries[entries.length - 1].isIntersecting;
    syncLoop();
  });
  visObs.observe(container);
  listen(document, "visibilitychange", syncLoop);

  // sin animación: cada morador queda quieto en su puesto con la pose de su estado
  function freeze() {
    vis.forEach(v => {
      const d = v.d;
      if (d.kind === "agent") {
        const node = d.status === "busy" || d.status === "warn" ? d.home + ":work" : d.home + ":a";
        d.at = node; d.path = []; d.pos = nodePos(node, d.lane);
        d.activity = d.status === "busy" ? "WORK" : d.status === "warn" ? "ALERT" : d.status === "error" ? "PANIC" : "IDLE";
        d.anim = { WORK: "work", ALERT: "alert", PANIC: "panic", IDLE: "idle" }[d.activity];
        d.rotY = d.activity === "WORK" ? ROOMS[d.home].work.face : 0;
      }
    });
  }

  function update(agents, offline) {
    if (disposed) return;
    ROOM_IDS.forEach(id => {
      const v = (agents && agents[id]) || { status: "idle", bubble: "Sin datos" };
      const status = offline ? "idle" : v.status;
      const style = styleFor(status, false);
      const dv = byId[id];
      if (dv.d.status !== status) setStatus(world, dv.d, status);
      roomState[id].status = status;
      dv.bubble.draw(offline ? "Sin conexión" : (v.bubble == null ? "" : String(v.bubble)),
        { border: style.color, fill: BUBBLE.fill, text: BUBBLE.text });
    });
    if (reducedMotion) { freeze(); renderFrame(0); }
  }

  function setSelected(id) {
    selected = id;
    if (reducedMotion) renderFrame(0);
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    syncLoop();
    abort.abort();
    resizeObs.disconnect();
    visObs.disconnect();
    const mats = new Set();
    scene.traverse(o => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) mats.add(o.material);
    });
    K.mats.forEach(m => mats.add(m));
    mats.forEach(m => { if (m.map) m.map.dispose(); m.dispose(); });
    Object.values(G).forEach(x => { if (x.dispose) x.dispose(); });
    Object.values(tex).forEach(t => t.dispose());
    vis.forEach(v => { if (v.bubble) v.bubble.dispose(); v.emote.dispose(); });
    ROOM_IDS.forEach(id => rooms[id].plate.dispose());
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
