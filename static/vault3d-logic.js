// Lógica pura del Vault 3D: layout, grafo de caminos, ascensor, comportamiento de los moradores y cámara.
// Sin DOM ni three.js, para poder probarla con node. Ejes: x derecha, y arriba, z hacia la cámara.

export const STATUS_STYLE = {
  busy:  { color: "#4ade80", light: "#ffe2a8", intensity: 1.0 },
  ok:    { color: "#4ade80", light: "#ffe2a8", intensity: 0.85 },
  warn:  { color: "#fbbf24", light: "#ffb030", intensity: 1.1 },
  error: { color: "#f87171", light: "#ff3030", intensity: 1.2 },
  idle:  { color: "#9a9a9a", light: "#cdbd9a", intensity: 0.35 },
};

export function styleFor(status, offline) {
  if (offline) return STATUS_STYLE.idle;
  return STATUS_STYLE[status] || STATUS_STYLE.idle;
}

// ── Layout del corte ────────────────────────────────────────────────────────────────────────────
export const ROOM_W = 8, ROOM_H = 5, ROOM_D = 4, SHAFT_W = 2;
export const FLOOR_Y = [0, 5.6];          // piso de abajo y piso de arriba
export const SURFACE_Y = FLOOR_Y[1] + ROOM_H + 0.6;

// side: -1 izquierda, +1 derecha. work.face: hacia dónde mira el morador al trabajar (sentido del eje x).
export const ROOMS = {
  videos:   { floor: 1, side: -1, label: "VIDEOS",   work: { x: -5.4, face: -1 }, spots: [-2.8, -4.4] },
  publica:  { floor: 1, side:  1, label: "PUBLICA",  work: { x:  5.2, face:  1 }, spots: [ 2.8,  4.0] },
  metricas: { floor: 0, side: -1, label: "MÉTRICAS", work: { x: -4.3, face:  1 }, spots: [-6.4, -5.2] },
  revisor:  { floor: 0, side:  1, label: "REVISOR",  work: { x:  5.3, face:  1 }, spots: [ 2.8,  4.0] },
};
export const ROOM_IDS = Object.keys(ROOMS);
export const roomCenterX = id => ROOMS[id].side * (SHAFT_W / 2 + ROOM_W / 2);

// ── Grafo de caminos ───────────────────────────────────────────────────────────────────────────
// Nodos: "<sala>:work|a|b|door" y "E0"/"E1" (el ascensor en cada piso). z lo pone el carril de cada morador.
export const NODES = (() => {
  const n = {};
  ROOM_IDS.forEach(id => {
    const r = ROOMS[id];
    n[id + ":work"] = { x: r.work.x, floor: r.floor, room: id };
    n[id + ":a"] = { x: r.spots[0], floor: r.floor, room: id };
    n[id + ":b"] = { x: r.spots[1], floor: r.floor, room: id };
    n[id + ":door"] = { x: r.side * 1.7, floor: r.floor, room: id };
  });
  n.E0 = { x: 0, floor: 0, room: null };
  n.E1 = { x: 0, floor: 1, room: null };
  return n;
})();

export const roomNodes = id => [id + ":work", id + ":a", id + ":b", id + ":door"];

const EDGES = (() => {
  const adj = {};
  const link = (a, b) => { (adj[a] = adj[a] || []).push(b); (adj[b] = adj[b] || []).push(a); };
  ROOM_IDS.forEach(id => {
    const ns = roomNodes(id);
    ns.forEach((a, i) => ns.slice(i + 1).forEach(b => link(a, b)));
    link(id + ":door", "E" + ROOMS[id].floor);
  });
  link("E0", "E1");
  return adj;
})();

export const isRide = (a, b) => (a === "E0" && b === "E1") || (a === "E1" && b === "E0");

export function findPath(from, to) {
  if (from === to) return [from];
  const prev = { [from]: null };
  const queue = [from];
  while (queue.length) {
    const cur = queue.shift();
    for (const nxt of EDGES[cur] || []) {
      if (nxt in prev) continue;
      prev[nxt] = cur;
      if (nxt === to) {
        const out = [to];
        for (let p = cur; p !== null; p = prev[p]) out.unshift(p);
        return out;
      }
      queue.push(nxt);
    }
  }
  return null;
}

export function nodePos(id, lane) {
  const n = NODES[id];
  return { x: n.x, y: FLOOR_Y[n.floor], z: lane || 0 };
}

// ── Mundo: ascensor + moradores ────────────────────────────────────────────────────────────────
export const WALK_SPEED = 1.7, RUN_SPEED = 3.4, ELEV_SPEED = 3;
export const CHAT_LINES = ["¡Hola!", "¿Todo bien?", "Qué día...", "Vault 111", "¡Mira!", "Jeje", "¿Y el café?", "Ánimo"];

export function createWorld(defs, rng) {
  const r = rng || Math.random;
  const dwellers = defs.map(def => ({
    id: def.id, kind: def.kind, home: def.home, lane: def.lane || 0,
    at: def.node || def.home + ":a", path: [], goal: "IDLE",
    pos: nodePos(def.node || def.home + ":a", def.lane || 0),
    rotY: 0, activity: "IDLE", anim: "idle", status: "idle",
    _rng: r, t: 1 + r() * 4, cool: 4 + r() * 8, ride: null, pending: null, partner: null, emote: null, afterWave: null, panicTo: null,
  }));
  return { dwellers, elev: { y: FLOOR_Y[0], target: FLOOR_Y[0], owner: null }, rng: r };
}

function roomOf(d) {
  if (NODES[d.at].room) return NODES[d.at].room;
  const floor = d.pos.y > (FLOOR_Y[0] + FLOOR_Y[1]) / 2 ? 1 : 0;
  const side = d.pos.x < 0 ? -1 : 1;
  return ROOM_IDS.find(id => ROOMS[id].floor === floor && ROOMS[id].side === side);
}

function floorOfY(y) { return y > (FLOOR_Y[0] + FLOOR_Y[1]) / 2 ? 1 : 0; }

function travel(d, node, goal) {
  const start = d.path.length && !isRide(d.at, d.path[0]) ? d.path[0] : d.at;
  const route = findPath(start, node);
  d.path = start === d.at ? route.slice(1) : route;
  d.goal = goal;
  d.activity = "TRAVEL";
  d.partner = null; d.emote = null;
}

function arrived(d) {
  d.path = [];
  d.activity = d.goal;
  d.t = d.goal === "WORK" || d.goal === "ALERT" ? 1e9 : 2 + d._rng() * 5;
}

// Cambia lo que el morador debe estar haciendo según el estado de su agente.
export function setStatus(world, d, status) {
  d.status = status;
  if (d.kind !== "agent") return;
  if (d.ride) { d.pending = status; return; }
  d.pending = null; d.afterWave = null;
  if (status === "error") {
    d.path = []; d.partner = null;
    d.activity = "PANIC"; d.t = 0; d.panicTo = null;
    d.emote = "!!";
  } else if (status === "busy" || status === "warn") {
    const goal = status === "busy" ? "WORK" : "ALERT";
    if (d.at === d.home + ":work" && !d.path.length && d.pos.x === NODES[d.at].x) {
      d.activity = goal; d.goal = goal; d.path = [];
    } else {
      travel(d, d.home + ":work", goal);
    }
    d.emote = status === "warn" ? "!" : null;
  } else if (["WORK", "ALERT", "PANIC"].includes(d.activity) || (d.activity === "TRAVEL" && ["WORK", "ALERT"].includes(d.goal))) {
    d.emote = null; d.path = []; d.goal = "IDLE"; d.activity = "IDLE"; d.t = 1.2;
  }
}

// Un clic sobre el morador: saluda y luego vuelve a lo suyo.
export function pokeDweller(d) {
  if (d.activity === "WAVE" || d.ride) return;
  d.afterWave = { activity: d.activity, goal: d.goal, path: d.path.slice() };
  d.activity = "WAVE"; d.t = 1.6; d.partner = null; d.emote = null;
}

function stepElevator(elev, dt) {
  const diff = elev.target - elev.y;
  const step = ELEV_SPEED * dt;
  elev.y = Math.abs(diff) <= step ? elev.target : elev.y + Math.sign(diff) * step;
}

function moveTo(d, tx, tz, speed, dt) {
  const dx = tx - d.pos.x;
  const dz = tz - d.pos.z;
  const dist = Math.hypot(dx, dz);
  const step = speed * dt;
  if (dist <= step) { d.pos.x = tx; d.pos.z = tz; return true; }
  d.pos.x += dx / dist * step;
  d.pos.z += dz / dist * step;
  if (Math.abs(dx) > 1e-3) d.rotY = Math.sign(dx) * (Math.PI / 2);
  return false;
}

// Camina por d.path. Devuelve true cuando llegó al final.
function followPath(world, d, speed, dt) {
  if (!d.path.length) return true;
  const next = d.path[0];
  if (isRide(d.at, next)) {
    const elev = world.elev;
    const fromY = FLOOR_Y[NODES[d.at].floor], toY = FLOOR_Y[NODES[next].floor];
    if (!d.ride) {
      if (elev.owner && elev.owner !== d.id) { d.anim = "idle"; d.rotY = 0; return false; }
      elev.owner = d.id; elev.target = fromY; d.ride = "call";
    }
    if (d.ride === "call") {
      d.anim = "idle"; d.rotY = 0;
      if (elev.y === fromY) { d.ride = "riding"; elev.target = toY; }
      return false;
    }
    d.anim = "ride"; d.rotY = 0;
    d.pos.y = elev.y;
    if (elev.y === toY) {
      d.at = next; d.path.shift(); d.ride = null; elev.owner = null;
      if (d.pending) { const p = d.pending; d.pending = null; setStatus(world, d, p); }
    }
    return false;
  }
  const pos = nodePos(next, d.lane);
  d.anim = speed > WALK_SPEED ? "run" : "walk";
  if (moveTo(d, pos.x, pos.z, speed, dt)) { d.at = next; d.path.shift(); }
  return d.path.length === 0;
}

function pickIdleAction(world, d) {
  const r = world.rng();
  const room = roomOf(d);
  const free = d.kind === "extra" || d.status === "ok" || d.status === "idle";
  if (r < 0.35 || !free) { d.t = 2 + world.rng() * 5; return; }
  if (r < 0.7) {
    const spots = roomNodes(room).filter(n => n !== d.at && !n.endsWith(":door"));
    travel(d, spots[Math.floor(world.rng() * spots.length)], "IDLE");
  } else {
    const other = ROOM_IDS.filter(id => id !== room);
    const dest = other[Math.floor(world.rng() * other.length)];
    travel(d, dest + (world.rng() < 0.5 ? ":a" : ":b"), "IDLE");
  }
}

function canChat(d) {
  return d.cool <= 0 && !d.ride && (d.activity === "IDLE" || (d.activity === "TRAVEL" && d.goal === "IDLE"));
}

export function stepWorld(world, dt) {
  stepElevator(world.elev, dt);
  const ds = world.dwellers;
  ds.forEach(d => stepDweller(world, d, dt));
  // charlas: dos moradores libres que se cruzan en el mismo piso
  for (let i = 0; i < ds.length; i++) {
    for (let j = i + 1; j < ds.length; j++) {
      const a = ds[i], b = ds[j];
      if (!canChat(a) || !canChat(b) || a.pos.y !== b.pos.y || Math.abs(a.pos.x - b.pos.x) > 1.4) continue;
      if (world.rng() > 0.02) continue;
      [a, b].forEach(d => { d.activity = "CHAT"; d.t = 4 + world.rng() * 2; d.path = []; d.cool = 25 + world.rng() * 20; });
      a.partner = b.id; b.partner = a.id;
      a.speak = 0; b.speak = 0.9;
      a.emote = CHAT_LINES[Math.floor(world.rng() * CHAT_LINES.length)];
    }
  }
}

function stepDweller(world, d, dt) {
  d.cool = Math.max(0, d.cool - dt);
  d.t -= dt;
  switch (d.activity) {
    case "TRAVEL":
      if (followPath(world, d, WALK_SPEED, dt)) arrived(d);
      break;
    case "WORK":
      d.anim = "work"; d.rotY = ROOMS[d.home].work.face * 1.0;
      break;
    case "ALERT":
      d.anim = "alert"; d.rotY = ROOMS[d.home].work.face * 0.6;
      break;
    case "PANIC": {
      const room = roomOf(d);
      if (!d.panicTo) {
        const ns = roomNodes(room).filter(n => !n.endsWith(":door"));
        d.panicTo = ns[Math.floor(world.rng() * ns.length)];
      }
      const pos = nodePos(d.panicTo, d.lane);
      d.anim = "run";
      if (moveTo(d, pos.x, pos.z, RUN_SPEED, dt)) { d.at = d.panicTo; d.panicTo = null; d.anim = "panic"; d.rotY = 0; }
      break;
    }
    case "WAVE":
      d.anim = "wave"; d.rotY = 0;
      if (d.t <= 0) {
        const prev = d.afterWave || { activity: "IDLE", goal: "IDLE", path: [] };
        d.afterWave = null; d.goal = prev.goal; d.path = prev.path; d.activity = prev.activity;
        if (d.activity === "IDLE") d.t = 1.5;
        d.emote = d.activity === "PANIC" ? "!!" : d.activity === "ALERT" ? "!" : null;
      }
      break;
    case "CHAT": {
      const partner = world.dwellers.find(o => o.id === d.partner);
      d.anim = "talk";
      if (partner) d.rotY = Math.sign(partner.pos.x - d.pos.x || 1) * 1.1;
      d.speak = (d.speak || 0) + dt;
      if (d.speak > 1.8 && partner) {  // cambia el turno de palabra
        d.speak = -1.8; d.emote = null;
        partner.emote = CHAT_LINES[Math.floor(world.rng() * CHAT_LINES.length)];
        partner.speak = 0;
      }
      if (d.t <= 0) { d.activity = "IDLE"; d.partner = null; d.emote = null; d.t = 2 + world.rng() * 3; }
      break;
    }
    default:  // IDLE
      d.anim = "idle";
      d.rotY = 0;
      if (d.t <= 0) pickIdleAction(world, d);
  }
  d.pos.y = d.ride === "riding" ? world.elev.y : FLOOR_Y[floorOfY(d.pos.y)];
}

// ── Cámara ─────────────────────────────────────────────────────────────────────────────────────
export const FOV = 24;
export const SCENE_WIDTH = 22;
export const SCENE_HEIGHT = 14.5;
export const DIST_MIN = 28;
export const DIST_MAX = 90;
export const ZOOM_MIN = 0.45;
export const ZOOM_MAX = 1.25;
export const ORBIT = {
  azimuth: 22 * Math.PI / 180,
  polarMin: 1.3,
  polarMax: 1.62,
  polarDefault: 1.5,
};

function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

// Distancia a la que entra todo el corte (ancho y alto), según la relación de aspecto del contenedor.
export function fitCamera(aspect) {
  const a = aspect > 0.2 ? aspect : 0.2;
  const t = 2 * Math.tan(FOV * Math.PI / 360);
  const raw = Math.max(SCENE_WIDTH / (t * a), SCENE_HEIGHT / t);
  return { fov: FOV, distance: clamp(raw, DIST_MIN, DIST_MAX) };
}

export function clampOrbit(az, pol) {
  return { az: clamp(az, -ORBIT.azimuth, ORBIT.azimuth), pol: clamp(pol, ORBIT.polarMin, ORBIT.polarMax) };
}

export function clampZoom(z) { return clamp(z, ZOOM_MIN, ZOOM_MAX); }
