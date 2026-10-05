import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile, useVideoConfig } from "remotion";
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
import { BebeHeroeVideo } from "./BebeHeroe";
import type { StoryVideoProps } from "./schema";

const TRANSITION_FRAMES = 20;

// Pseudo-aleatorio determinístico por índice de escena (mismo patrón que
// Fireflies.tsx) — Remotion re-renderiza cada frame de forma independiente,
// así que no se puede usar Math.random(): el resultado tiene que ser
// siempre el mismo para el mismo índice.
const seeded = (seed: number) => {
  const x = Math.sin(seed * 9999) * 10000;
  return x - Math.floor(x);
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
const pickTransition = (i: number): TransitionPresentation<any> => {
  const roll = seeded(i * 3.77 + 1);
  if (roll < 0.65) return fade();
  if (roll < 0.85) {
    const dir = WIPE_DIRECTIONS[Math.floor(seeded(i * 5.11 + 2) * WIPE_DIRECTIONS.length)];
    return wipe({ direction: dir });
  }
  const dir = SLIDE_DIRECTIONS[Math.floor(seeded(i * 7.31 + 3) * SLIDE_DIRECTIONS.length)];
  return slide({ direction: dir });
};

// Tema de la página Bebé Héroe: composición propia (cortes secos, tiempos fijos de 18 s).
export const StoryVideo: React.FC<StoryVideoProps> = (props) =>
  props.theme === "bebe_heroe" ? <BebeHeroeVideo {...props} /> : <StandardStoryVideo {...props} />;

const StandardStoryVideo: React.FC<StoryVideoProps> = ({
  title,
  audioSrc,
  scenes,
  subtitles,
  subtitleStyle,
  aiLabel,
  mood,
}) => {
  const { fps } = useVideoConfig();
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
                <KenBurnsImage src={scene.src} direction={scene.kenBurns ?? "zoomIn"} />
              )}
            </TransitionSeries.Sequence>
            {i < scenes.length - 1 && (
              <TransitionSeries.Transition
                timing={linearTiming({ durationInFrames: TRANSITION_FRAMES })}
                presentation={pickTransition(i)}
              />
            )}
          </React.Fragment>
        ))}
      </TransitionSeries>

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

    </AbsoluteFill>
  );
};
