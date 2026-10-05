import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  Sequence,
  cancelRender,
  continueRender,
  delayRender,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { SceneClip, StoryVideoProps } from "./schema";

// Composición de la página "Bebé Héroe" (plantilla de Remotion del cliente): 18 s, 4 escenas con
// cortes secos (3 / 7 / 13 s), fundido de entrada de 8 frames, zoom y punto de enfoque por escena,
// degradado oscuro arriba y abajo, corazones en la escena final, un subtítulo por frase (Luckiest Guy
// amarillo con contorno rojo oscuro), música que baja mientras habla la voz y efecto de sonido.

const FONT = "'Luckiest Guy', 'Liberation Sans', Arial, sans-serif";
const STROKE: React.CSSProperties = {
  WebkitTextStroke: "12px #7a0a0a",
  paintOrder: "stroke fill",
  strokeLinejoin: "round",
  textShadow: "0 10px 0 #3d0505, 0 16px 28px rgba(0,0,0,.6)",
};
const HEART_PATH =
  "M12 21s-7.5-4.6-9.6-9.3C.9 8.1 2.9 4.5 6.4 4.5c2 0 3.7 1.1 5.6 3.2 1.9-2.1 3.6-3.2 5.6-3.2 3.5 0 5.5 3.6 4 7.2C19.5 16.4 12 21 12 21z";

// La fuente vive en public/ (no está en @remotion/google-fonts): se espera a que cargue antes de renderizar.
const HeroFont: React.FC<{ src: string }> = ({ src }) => {
  const [handle] = React.useState(() => delayRender("Cargando fuente del tema"));
  React.useEffect(() => {
    new FontFace("Luckiest Guy", `url(${staticFile(src)})`)
      .load()
      .then((f) => {
        document.fonts.add(f);
        continueRender(handle);
      })
      .catch((e) => cancelRender(e));
  }, [src, handle]);
  return null;
};

const Heart: React.FC<{ size: number; color: string }> = ({ size, color }) => (
  <svg width={size} height={size} viewBox="0 0 24 24">
    <path fill={color} stroke="#fff" strokeWidth="0.8" d={HEART_PATH} />
  </svg>
);

// 14 corazones (SVG: el render no muestra emojis) que suben y se mecen.
const Hearts: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill>
      {Array.from({ length: 14 }, (_, i) => {
        const f = frame - i * 7;
        if (f < 0) return null;
        const size = 50 + ((i * 37) % 60);
        const y = 1900 - f * (7 + (i % 4)) * 1.2;
        const x = 80 + ((i * 197) % 880) + Math.sin(f / 10 + i) * (20 + (i % 5) * 8);
        const opacity = interpolate(y, [150, 500, 1500, 1900], [0, 1, 1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        if (opacity <= 0) return null;
        return (
          <div key={i} style={{ position: "absolute", left: x, top: y, opacity }}>
            <Heart size={size} color={i % 2 ? "#ff3b5c" : "#ff7a95"} />
          </div>
        );
      })}
    </AbsoluteFill>
  );
};

const HeroScene: React.FC<{ scene: SceneClip; first: boolean; last: boolean }> = ({ scene, first, last }) => {
  const frame = useCurrentFrame();
  const dur = Math.max(scene.endFrame - scene.startFrame, 1);
  const scale = interpolate(frame, [0, dur], [scene.zoomFrom ?? 1, scene.zoomTo ?? 1.1], {
    extrapolateRight: "clamp",
  });
  // Fundido de entrada de 8 frames en todas las escenas menos la primera: con fundido el frame 0 sale
  // negro y la vista previa (que muestra ese frame) queda en negro.
  const fade = first ? 1 : interpolate(frame, [0, 8], [0, 1], { extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ backgroundColor: "#000", opacity: fade }}>
      <Img
        src={staticFile(scene.src)}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `scale(${scale})`,
          transformOrigin: scene.origin ?? "50% 50%",
        }}
      />
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(to bottom, rgba(0,0,0,.45), rgba(0,0,0,0) 30%, rgba(0,0,0,0) 65%, rgba(0,0,0,.5))",
        }}
      />
      {last ? <Hearts /> : null}
    </AbsoluteFill>
  );
};

const PhraseSubtitle: React.FC<{ text: string }> = ({ text }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const pop = spring({ frame, fps, config: { damping: 13, stiffness: 170 } });
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 250 }}>
      <div
        style={{
          transform: `scale(${0.85 + 0.15 * pop})`,
          opacity: Math.min(1, pop),
          maxWidth: 940,
          textAlign: "center",
          fontFamily: FONT,
          fontWeight: 400,
          fontSize: 84,
          lineHeight: 1.1,
          letterSpacing: 2,
          color: "#ffd54f",
          ...STROKE,
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

export const BebeHeroeVideo: React.FC<StoryVideoProps> = ({
  scenes,
  phrases,
  audioSrc,
  musicSrc,
  sfx,
  fontSrc,
}) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const sec = frame / fps;
  const voices = phrases ?? [];
  // Música más baja que la voz: 0.20 en las dos primeras escenas, 0.32 en la heroica, 0.28 en el
  // cierre; mientras habla la voz baja otro 40%.
  const speaking = voices.some((v) => sec >= v.start - 0.1 && sec <= v.start + v.dur + 0.1);
  const base = interpolate(sec, [0, 6.8, 7.3, 13, 13.5, 18], [0.2, 0.2, 0.32, 0.32, 0.28, 0.28], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {fontSrc ? <HeroFont src={fontSrc} /> : null}
      {scenes.map((s, i) => (
        <Sequence key={`${s.src}-${i}`} from={s.startFrame} durationInFrames={Math.max(s.endFrame - s.startFrame, 1)}>
          <HeroScene scene={s} first={i === 0} last={i === scenes.length - 1} />
        </Sequence>
      ))}
      {musicSrc ? <Audio src={staticFile(musicSrc)} volume={base * (speaking ? 0.6 : 1)} loop /> : null}
      {(sfx ?? []).map((fx, i) => (
        <Sequence key={`sfx-${i}`} from={Math.round(fx.at * fps)}>
          <Audio src={staticFile(fx.src)} volume={fx.volume} />
        </Sequence>
      ))}
      {audioSrc ? <Audio src={staticFile(audioSrc)} /> : null}
      {voices.map((v, i) => (
        <Sequence key={`t-${i}`} from={Math.round(v.start * fps)} durationInFrames={Math.round((v.dur + 0.15) * fps)}>
          <PhraseSubtitle text={v.text} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
