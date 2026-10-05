import React from "react";
import {
  AbsoluteFill,
  Audio,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont as loadAnton } from "@remotion/google-fonts/Anton";
import { Vignette } from "./Vignette";
import { Subtitles } from "./Subtitles";
import type { SubtitleStyle, SubtitleWord } from "./schema";

const { fontFamily: antonFontFamily } = loadAnton("normal", { weights: ["400"], subsets: ["latin"] });

export type ClipEditProps = {
  src: string;
  hook: string;
  // Palabras del hook que se resaltan en amarillo (índices 0-based).
  hookHighlight: number[];
  tag: string;
  credit: string;
  cta: string;
  // Segundos (del clip original) en que cae cada kill: dispara slow-mo,
  // zoom-punch, flash, shake, sonido y el contador. La última es el ACE.
  kills: number[];
  // Duración útil del clip original en segundos.
  clipSeconds: number;
  // Rótulo que sale al caer cada kill (el último es el grande). Por defecto
  // KILL → DOUBLE → … → ACE; para clips sin kills (p. ej. "¡BRUTAL!") se pasa
  // uno por momento.
  labels?: string[];
  // Contador de calaveras bajo el clip; false para juegos sin kills.
  counter?: boolean;
  // Recorte del clip cuadrado: % horizontal del video 16:9 que queda centrado
  // (50 = centro; más alto = corre el encuadre hacia la derecha, donde está
  // el arma en CS2).
  focusX: number;
  // Lo que se oye hablar en el clip (segundos del clip original): subtítulos
  // abajo solo mientras alguien habla. Sin palabras = sin subtítulos.
  subtitles?: SubtitleWord[];
};

export const clipEditDefaultProps: ClipEditProps = {
  src: "clips/cs2_ace_inferno.mp4",
  hook: "5 KILLS THROUGH THE SMOKE",
  hookHighlight: [0, 5],
  tag: "CS2 · ACE ON INFERNO",
  credit: "clip: @eymz (Medal)",
  cta: "ACE OR LUCK? 👇",
  kills: [1.0, 4.4, 8.0, 9.75, 13.1],
  clipSeconds: 14.8,
  focusX: 60,
};

const W = 1080;
const H = 1920;
const BOX_H = 1000; // el clip va en un recuadro de 1080x1000 (recorte de la escena 16:9)
const BOX_TOP = 330;
const COUNTER_TOP = BOX_TOP + BOX_H + 30; // todo el contenido queda sobre la franja inferior que tapa la UI de TikTok/Reels (~380 px)

const strokeShadow = (px: number, color = "#000") =>
  [`${px}px ${px}px 0`, `-${px}px -${px}px 0`, `${px}px -${px}px 0`, `-${px}px ${px}px 0`]
    .map((s) => `${s} ${color}`)
    .join(", ");

// ── Slow-mo con rampa: cada kill se rodea de 3 tramos (frena, cámara lenta,
// vuelve). Offsets en segundos del clip original respecto a la kill; el ACE
// frena más y dura más.
type Piece = { from: number; to: number; rate: number };
const SLOW_KILL: Piece[] = [
  { from: -0.2, to: -0.05, rate: 0.75 },
  { from: -0.05, to: 0.2, rate: 0.4 },
  { from: 0.2, to: 0.35, rate: 0.75 },
];
const SLOW_ACE: Piece[] = [
  { from: -0.3, to: -0.1, rate: 0.7 },
  { from: -0.1, to: 0.5, rate: 0.3 },
  { from: 0.5, to: 0.8, rate: 0.7 },
];

type Segment = { from: number; to: number; rate: number; outStart: number; outFrames: number };
type Timeline = {
  segments: Segment[];
  totalFrames: number;
  killFrames: number[]; // frame de salida de cada kill
  toOut: (t: number) => number; // segundo del clip original → frame de salida
  windows: { start: number; exit: number; end: number }[]; // frames de salida del slow-mo: entra / empieza a salir / termina
};

// Mapa tiempo-del-clip → frames de salida. Lo usan el video, los efectos, el
// audio y la duración de la composición (calculateMetadata en Root.tsx).
export const buildTimeline = (props: Pick<ClipEditProps, "kills" | "clipSeconds">, fps: number): Timeline => {
  const last = props.kills.length - 1;
  // Una kill pegada al inicio o al final del clip dejaría la rampa fuera de [0, clipSeconds]
  // (trimBefore negativo: Remotion aborta el render); se recorta y se descartan los tramos vacíos.
  const pieces = props.kills
    .flatMap((t, i) =>
      (i === last ? SLOW_ACE : SLOW_KILL).map((p) => ({
        from: Math.max(0, t + p.from),
        to: Math.min(props.clipSeconds, t + p.to),
        rate: p.rate,
      }))
    )
    .filter((p) => p.to > p.from);
  const raw: Piece[] = [];
  let cursor = 0;
  for (const p of pieces) {
    if (p.from > cursor) raw.push({ from: cursor, to: p.from, rate: 1 });
    raw.push(p);
    cursor = p.to;
  }
  if (cursor < props.clipSeconds) raw.push({ from: cursor, to: props.clipSeconds, rate: 1 });

  let out = 0;
  const segments: Segment[] = raw.map((r) => {
    const outStart = Math.round(out);
    out += ((r.to - r.from) / r.rate) * fps;
    return { ...r, outStart, outFrames: Math.max(Math.round(out) - outStart, 1) };
  });
  const toOut = (t: number) => {
    const seg = segments.find((s) => t >= s.from && t < s.to) ?? segments[segments.length - 1];
    return Math.round(seg.outStart + ((t - seg.from) / seg.rate) * fps);
  };
  const windows = props.kills.map((t, i) => {
    const w = i === last ? SLOW_ACE : SLOW_KILL;
    return { start: toOut(t + w[0].from), exit: toOut(t + w[2].from), end: toOut(t + w[2].to) };
  });
  return { segments, totalFrames: Math.round(out), killFrames: props.kills.map(toOut), toOut, windows };
};

// El clip como secuencia de tramos con su velocidad; en cámara lenta baja el
// volumen del audio original para que no tape los efectos.
const ClipSegments: React.FC<{
  src: string;
  segments: Segment[];
  muted?: boolean;
  style: React.CSSProperties;
}> = ({ src, segments, muted, style }) => {
  const { fps } = useVideoConfig();
  return (
    <>
      {segments.map((seg, i) => (
        <Sequence key={i} from={seg.outStart} durationInFrames={seg.outFrames}>
          <OffthreadVideo
            src={staticFile(src)}
            trimBefore={Math.round(seg.from * fps)}
            playbackRate={seg.rate}
            muted={muted}
            volume={seg.rate < 1 ? 0.45 : 1}
            style={style}
          />
        </Sequence>
      ))}
    </>
  );
};

// Efecto de sonido sintetizado (scripts/gen_sfx.py → public/sfx/).
const Sfx: React.FC<{ file: string; at: number; volume: number }> = ({ file, at, volume }) => (
  <Sequence from={Math.max(at, 0)} layout="none">
    <Audio src={staticFile(`sfx/${file}.wav`)} volume={volume} />
  </Sequence>
);

const KILL_LABELS = ["KILL", "DOUBLE", "TRIPLE", "QUAD", "ACE"];

// Subtítulos sobre la parte baja del clip (el recuadro llega hasta y=1330): quedan por
// encima del contador/CTA y de la franja que tapa la UI de TikTok/Reels.
const SUBTITLE_STYLE: SubtitleStyle = {
  fontFamily: "anton",
  fontSize: 64,
  position: "bottom",
  positionX: 50,
  positionY: ((BOX_TOP + BOX_H - 150) / H) * 100,
  textColor: "#ffffff",
  highlightColor: "#ffd400",
  background: false,
  strokeColor: "#000000",
  strokeWidth: 5,
  uppercase: true,
  italic: false,
  letterSpacing: 1,
  animationType: "highlight",
};

export const ClipEdit: React.FC<ClipEditProps> = (props) => {
  const { src, hook, hookHighlight, tag, credit, cta, kills, focusX, labels = KILL_LABELS, counter = true } = props;
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const { segments, killFrames, windows, toOut } = React.useMemo(() => buildTimeline(props, fps), [props, fps]);
  // Las palabras vienen en tiempo del clip original; el slow-mo estira/acorta el tiempo de salida.
  const subtitleWords = React.useMemo(
    () =>
      (props.subtitles ?? []).map((w) => {
        const start = toOut(w.start) / fps;
        return { word: w.word, start, end: Math.max(toOut(w.end) / fps, start + 0.05) };
      }),
    [props.subtitles, toOut, fps]
  );

  // ── Zoom-punch + shake: cada kill empuja la escena y se asienta con rebote.
  let punch = 0;
  let shakeX = 0;
  let shakeY = 0;
  let flash = 0;
  killFrames.forEach((kf, i) => {
    const f = frame - kf;
    if (f < 0 || f > 40) return;
    const isAce = i === killFrames.length - 1;
    const s = spring({ frame: f, fps, config: { damping: 9, stiffness: 220, mass: 0.5 } });
    punch += (isAce ? 0.2 : 0.13) * (1 - s);
    const decay = Math.max(0, 1 - f / 9);
    shakeX += Math.sin(f * 2.7 + i) * 14 * decay * (isAce ? 1.5 : 1);
    shakeY += Math.cos(f * 3.1 + i) * 10 * decay * (isAce ? 1.5 : 1);
    flash = Math.max(flash, interpolate(f, [0, 5], [isAce ? 0.55 : 0.28, 0], { extrapolateRight: "clamp" }));
  });
  // 0..1 durante cada slow-mo (con rampa de entrada/salida): empuja un poco
  // más el zoom y oscurece los bordes para dar foco.
  const slowAmt = windows.reduce(
    (m, w) =>
      Math.max(
        m,
        interpolate(frame, [w.start, w.start + 8, w.exit, w.end], [0, 1, 1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      ),
    0
  );
  const creep = interpolate(frame, [0, durationInFrames], [1, 1.05]);
  const scale = creep + punch + slowAmt * 0.06;

  const killsDone = killFrames.filter((kf) => frame >= kf).length;

  // ── Hook: palabra por palabra, 0 → ~2.4 s.
  const words = hook.split(" ");
  const hookOut = interpolate(frame, [62, 74], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const tagIn = interpolate(frame, [70, 84], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // ── CTA tras el último kill.
  const lastKill = killFrames[killFrames.length - 1];
  const ctaSpring = spring({ frame: frame - (lastKill + 18), fps, config: { damping: 10, stiffness: 160 } });

  return (
    <AbsoluteFill style={{ backgroundColor: "#000", fontFamily: antonFontFamily }}>
      {/* Fondo: el mismo clip, ampliado y desenfocado */}
      <AbsoluteFill style={{ overflow: "hidden" }}>
        <ClipSegments
          src={src}
          segments={segments}
          muted
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            transform: "scale(1.6)",
            filter: "blur(38px) brightness(0.45) saturate(1.3)",
          }}
        />
      </AbsoluteFill>

      {/* Clip principal (con el audio original) */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: BOX_TOP,
          width: W,
          height: BOX_H,
          overflow: "hidden",
          boxShadow: "0 0 80px rgba(0,0,0,0.7)",
        }}
      >
        <div
          style={{
            width: "100%",
            height: "100%",
            transform: `translate(${shakeX}px, ${shakeY}px) scale(${scale})`,
            transformOrigin: "60% 55%",
          }}
        >
          <ClipSegments
            src={src}
            segments={segments}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              objectPosition: `${focusX}% 50%`,
              filter: "contrast(1.08) saturate(1.15)",
            }}
          />
        </div>
        <AbsoluteFill style={{ backgroundColor: "#fff", opacity: flash, pointerEvents: "none" }} />
      </div>

      <Vignette intensity="soft" />
      <AbsoluteFill style={{ opacity: slowAmt, pointerEvents: "none" }}>
        <Vignette />
      </AbsoluteFill>

      {/* Hook grande (arriba), luego etiqueta chica fija */}
      <div
        style={{
          position: "absolute",
          top: 70,
          left: 40,
          right: 40,
          textAlign: "center",
          opacity: hookOut,
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          gap: "0 22px",
        }}
      >
        {words.map((w, i) => {
          const s = spring({ frame: frame - i * 4, fps, config: { damping: 11, stiffness: 200 } });
          return (
            <span
              key={i}
              style={{
                fontSize: 118,
                lineHeight: 1.05,
                color: hookHighlight.includes(i) ? "#ffd400" : "#fff",
                textShadow: strokeShadow(6),
                transform: `scale(${interpolate(s, [0, 1], [0.3, 1])}) translateY(${interpolate(s, [0, 1], [40, 0])}px)`,
                opacity: interpolate(s, [0, 0.4], [0, 1], { extrapolateRight: "clamp" }),
                display: "inline-block",
              }}
            >
              {w}
            </span>
          );
        })}
      </div>
      <div
        style={{
          position: "absolute",
          top: 130,
          width: W,
          textAlign: "center",
          opacity: tagIn,
          fontSize: 64,
          color: "#fff",
          letterSpacing: 4,
          textShadow: strokeShadow(4),
        }}
      >
        {tag}
      </div>

      {/* Contador de kills (bajo el clip, sobre la zona segura) */}
      <div
        style={{
          position: "absolute",
          top: COUNTER_TOP,
          width: W,
          opacity: counter ? 1 - ctaSpring : 0,
          display: "flex",
          justifyContent: "center",
          gap: 26,
        }}
      >
        {kills.map((_, i) => {
          const done = i < killsDone;
          const s = spring({ frame: frame - killFrames[i], fps, config: { damping: 8, stiffness: 240, mass: 0.6 } });
          const isAce = i === kills.length - 1;
          return (
            <div
              key={i}
              style={{
                width: 132,
                height: 132,
                borderRadius: 66,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 78,
                color: done ? "#fff" : "rgba(255,255,255,0.35)",
                background: done ? (isAce ? "#ff2d2d" : "#ff5a1f") : "rgba(0,0,0,0.45)",
                border: `5px solid ${done ? "#fff" : "rgba(255,255,255,0.3)"}`,
                transform: `scale(${done ? interpolate(s, [0, 1], [1.7, 1]) : 1})`,
                boxShadow: done ? "0 0 36px rgba(255,90,31,0.8)" : "none",
              }}
            >
              {done ? "☠" : i + 1}
            </div>
          );
        })}
      </div>

      {/* Etiqueta KILL / DOUBLE / ... / ACE sobre el clip */}
      {killFrames.map((kf, i) => {
        const f = frame - kf;
        if (f < 0 || f > 26) return null;
        const isAce = i === killFrames.length - 1;
        const s = spring({ frame: f, fps, config: { damping: 9, stiffness: 260, mass: 0.6 } });
        const out = interpolate(f, [18, 26], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              top: BOX_TOP + 70,
              width: W,
              textAlign: "center",
              fontSize: isAce ? 230 : 150,
              color: isAce ? "#ffd400" : "#fff",
              textShadow: strokeShadow(isAce ? 9 : 6),
              opacity: out,
              transform: `scale(${interpolate(s, [0, 1], [2.2, 1])}) rotate(${isAce ? -6 : -3}deg)`,
              pointerEvents: "none",
            }}
          >
            {labels[Math.min(i, labels.length - 1)]}
          </div>
        );
      })}

      {/* Subtítulos de lo que se habla en el clip (solo mientras se habla) */}
      <Subtitles words={subtitleWords} style={SUBTITLE_STYLE} />

      {/* CTA (ocupa el lugar del contador) + crédito bajo la etiqueta */}
      <div
        style={{
          position: "absolute",
          top: COUNTER_TOP + 10,
          width: W,
          textAlign: "center",
          fontSize: 100,
          color: "#ffd400",
          textShadow: strokeShadow(5),
          opacity: interpolate(ctaSpring, [0, 0.3], [0, 1], { extrapolateRight: "clamp" }),
          transform: `scale(${interpolate(ctaSpring, [0, 1], [0.6, 1])})`,
        }}
      >
        {cta}
      </div>
      <div
        style={{
          position: "absolute",
          top: 215,
          width: W,
          textAlign: "center",
          opacity: tagIn,
          fontSize: 40,
          letterSpacing: 2,
          color: "rgba(255,255,255,0.85)",
          textShadow: strokeShadow(3),
        }}
      >
        {credit}
      </div>

      {/* Efectos de sonido */}
      {words.map((_, i) => (
        <Sfx key={`pop${i}`} file="pop" at={i * 4} volume={0.35} />
      ))}
      {killFrames.map((kf, i) => {
        const isAce = i === killFrames.length - 1;
        return (
          <React.Fragment key={`sfx${i}`}>
            <Sfx file="whoosh_in" at={kf - 13} volume={0.5} />
            <Sfx file={isAce ? "ace" : "hit"} at={kf} volume={isAce ? 0.9 : 0.7} />
            <Sfx file="whoosh_out" at={windows[i].exit} volume={0.4} />
          </React.Fragment>
        );
      })}
      <Sfx file="ding" at={lastKill + 18} volume={0.5} />

      {/* Barra de progreso fina */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          height: 10,
          width: `${(frame / durationInFrames) * 100}%`,
          background: "#ffd400",
        }}
      />
    </AbsoluteFill>
  );
};
