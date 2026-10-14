// Lógica pura de la oficina 3D: sin DOM ni three.js, para poder probarla con node.

export const STATUS_STYLE = {
  busy:  { color: "#00ff41", speed: 1.0,  alpha: 1.0,  blink: false },
  ok:    { color: "#00c832", speed: 0.35, alpha: 0.85, blink: false },
  warn:  { color: "#fbbf24", speed: 0.2,  alpha: 0.9,  blink: false },
  error: { color: "#f87171", speed: 0.2,  alpha: 1.0,  blink: true },
  idle:  { color: "#6b7280", speed: 0.05, alpha: 0.5,  blink: false },
};

export function styleFor(status, offline) {
  if (offline) return STATUS_STYLE.idle;
  return STATUS_STYLE[status] || STATUS_STYLE.idle;
}

export const FOV = 40;
export const SCENE_WIDTH = 18;
export const DIST_MIN = 9;
export const DIST_MAX = 40;
export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 1.3;
export const ORBIT = {
  azimuth: 35 * Math.PI / 180,
  polarMin: 0.9,
  polarMax: 1.4,
  polarDefault: 1.15,
};

function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

// Distancia a la que entran los 4 escritorios a lo ancho, según la relación de aspecto del contenedor.
export function fitCamera(aspect) {
  const a = aspect > 0.2 ? aspect : 0.2;
  const raw = SCENE_WIDTH / (2 * Math.tan(FOV * Math.PI / 360) * a);
  return { fov: FOV, distance: clamp(raw, DIST_MIN, DIST_MAX) };
}

export function clampOrbit(az, pol) {
  return { az: clamp(az, -ORBIT.azimuth, ORBIT.azimuth), pol: clamp(pol, ORBIT.polarMin, ORBIT.polarMax) };
}

export function clampZoom(z) { return clamp(z, ZOOM_MIN, ZOOM_MAX); }

// Niebla que acompaña a la cámara: el objetivo (a `distance`) queda siempre dentro del rango visible.
export function fogRange(distance) { return { near: distance * 0.75, far: distance * 2.2 }; }
