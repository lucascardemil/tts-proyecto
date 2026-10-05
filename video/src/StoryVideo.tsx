import React from "react";
import {
  AbsoluteFill,
  Audio,
  Sequence,
  cancelRender,
  continueRender,
  delayRender,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import type { TransitionPresentation } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { wipe } from "@remotion/transitions/wipe";
import { slide } from "@remotion/transitions/slide";
import { KenBurnsImage } from "./KenBurnsImage";
import { VideoClip } from "./VideoClip";
import { Vignette } from "./Vignette";
import { Fireflies } from "./Fireflies";
import { TitleCard } from "./TitleCard";
import { Subtitles } from "./Subtitles";
import type { SceneClip, StoryVideoProps, SubtitleWord } from "./schema";

const TRANSITION_FRAMES = 20;

// Pseudo-aleatorio determinístico por índice de escena (mismo patrón que
// Fireflies.tsx) — Remotion re-renderiza cada frame de forma independiente,
// así que no se puede usar Math.random(): el resultado tiene que ser
// siempre el mismo para el mismo índice.
const seeded = (seed: number) => {
  const x = Math.sin(seed * 9999) * 10000;
  return x - Math.floor(x);
};

// ── Tema "bebe_heroe" ──────────────────────────────────────────────────────
// Fuente de los subtítulos: se carga solo cuando el tema la pide (el archivo vive en public/).
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

const HEART_PATH =
  "M12 21s-7.5-4.6-9.6-9.3C.9 8.1 2.9 4.5 6.4 4.5c2 0 3.7 1.1 5.6 3.2 1.9-2.1 3.6-3.2 5.6-3.2 3.5 0 5.5 3.6 4 7.2C19.5 16.4 12 21 12 21z";

// 14 corazones (SVG: el render no muestra emojis) que suben y se mecen en la escena final.
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
            <svg width={size} height={size} viewBox="0 0 24 24">
              <path fill={i % 2 ? "#ff3b5c" : "#ff7a95"} stroke="#fff" strokeWidth="0.8" d={HEART_PATH} />
            </svg>
          </div>
        );
      })}
    </AbsoluteFill>
  );
};

// Música más baja que la voz: 0.20 en las dos primeras escenas, 0.32 en la heroica y 0.28 en el cierre;
// mientras habla la voz baja otro 40%.
const heroMusicVolume = (frame: number, fps: number, scenes: SceneClip[], words: SubtitleWord[]): number => {
  const idx = scenes.findIndex((s) => frame >= s.startFrame && frame < s.endFrame);
  const sceneIdx = idx === -1 ? scenes.length - 1 : idx;
  const base = sceneIdx <= 1 ? 0.2 : sceneIdx === 2 ? 0.32 : 0.28;
  const t = frame / fps;
  const speaking = words.some((w) => t >= w.start - 0.1 && t <= w.end + 0.1);
  return base * (speaking ? 0.6 : 1);
};

const WIPE_DIRECTIONS = ["from-left", "from-right", "from-top", "from-bottom"] as const;
const SLIDE_DIRECTIONS = ["from-left", "from-right", "from-top", "from-bottom"] as const;

// Variedad de transiciones entre escenas: "fade" domina (look seguro, no
// distrae de la narración) y de tanto en tanto entra un wipe/slide para que
// un video de 8-10 escenas no se sienta repetitivo. Determinístico por
// índice, así el mismo video siempre renderiza igual.
//
// Cast a `any`: fade/wipe/slide devuelven TransitionPresentation<PropsDeCadaUno>
// (props distintas por preset), pero TransitionSeries.Transition exige un
// solo tipo de PresentationProps en todo el árbol. En runtime Remotion no
// tiene problema mezclando presentaciones frame a frame; es solo TS siendo
// estricto sobre una unión que la librería no modela.
const pickTransition = (i: number, simple = false): TransitionPresentation<any> => {
  if (simple) return fade();
  const roll = seeded(i * 3.77 + 1);
  if (roll < 0.65) return fade();
  if (roll < 0.85) {
    const dir = WIPE_DIRECTIONS[Math.floor(seeded(i * 5.11 + 2) * WIPE_DIRECTIONS.length)];
    return wipe({ direction: dir });
  }
  const dir = SLIDE_DIRECTIONS[Math.floor(seeded(i * 7.31 + 3) * SLIDE_DIRECTIONS.length)];
  return slide({ direction: dir });
};

export const StoryVideo: React.FC<StoryVideoProps> = ({
  title,
  audioSrc,
  scenes,
  subtitles,
  subtitleStyle,
  aiLabel,
  mood,
  theme,
  musicSrc,
  fontSrc,
}) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const hero = theme === "bebe_heroe";
  const titleDurationInFrames = Math.round(fps * 3.5);

  return (
    <AbsoluteFill style={{ backgroundColor: "#05060a" }}>
      {/* Secuencia de escenas (imágenes con Ken Burns o clips de video) + crossfade entre cada una */}
      <TransitionSeries>
        {scenes.map((scene, i) => (
          <React.Fragment key={`${scene.src}-${i}`}>
            <TransitionSeries.Sequence
              durationInFrames={Math.max(scene.endFrame - scene.startFrame, 1)}
            >
              {scene.type === "video" ? (
                <VideoClip
                  src={scene.src}
                  nativeDurationInFrames={scene.nativeDurationInFrames ?? fps * 5}
                  playbackRate={scene.playbackRate}
                />
              ) : (
                <KenBurnsImage
                  src={scene.src}
                  direction={scene.kenBurns ?? "zoomIn"}
                  zoomFrom={scene.zoomFrom}
                  zoomTo={scene.zoomTo}
                  origin={scene.origin}
                />
              )}
            </TransitionSeries.Sequence>
            {i < scenes.length - 1 && (
              <TransitionSeries.Transition
                timing={linearTiming({ durationInFrames: TRANSITION_FRAMES })}
                presentation={pickTransition(i, hero)}
              />
            )}
          </React.Fragment>
        ))}
      </TransitionSeries>

      {hero && fontSrc ? <HeroFont src={fontSrc} /> : null}
      {hero && scenes.length > 0 ? (
        <Sequence from={scenes[scenes.length - 1].startFrame}>
          <Hearts />
        </Sequence>
      ) : null}

      {/* Look cinematográfico — solo en mood "warm_night" (default); en
          "bright"/"none" desentonan (macramé/gaming, luz de día) */}
      {mood !== "none" ? <Vignette intensity={mood === "bright" ? "soft" : "normal"} /> : null}
      {mood === "warm_night" ? <Fireflies count={16} /> : null}

      {/* Tarjeta de título, solo los primeros ~3.5s, sobre la primera escena (se omite si no hay título) */}
      {title ? (
        <Sequence from={0} durationInFrames={titleDurationInFrames}>
          <TitleCard title={title} durationInFrames={titleDurationInFrames} />
        </Sequence>
      ) : null}

      {/* Rótulo fijo de contenido recreado con IA, arriba y centrado para no tapar la acción */}
      {aiLabel ? (
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-start", paddingTop: "7%" }}>
          <div
            style={{
              padding: "8px 22px",
              borderRadius: 999,
              background: "rgba(0,0,0,0.5)",
              color: "#fff",
              fontFamily: "Poppins, Arial, sans-serif",
              fontSize: 30,
              fontWeight: 500,
              letterSpacing: 0.5,
            }}
          >
            {aiLabel}
          </div>
        </AbsoluteFill>
      ) : null}

      {/* Subtítulos sincronizados con la narración */}
      <Subtitles words={subtitles} style={subtitleStyle} />

      {audioSrc ? <Audio src={staticFile(audioSrc)} /> : null}
      {musicSrc ? <Audio src={staticFile(musicSrc)} volume={hero ? heroMusicVolume(frame, fps, scenes, subtitles) : 0.2} loop /> : null}
    </AbsoluteFill>
  );
};
