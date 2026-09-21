import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile, useVideoConfig } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { KenBurnsImage } from "./KenBurnsImage";
import { VideoClip } from "./VideoClip";
import { Vignette } from "./Vignette";
import { Fireflies } from "./Fireflies";
import { TitleCard } from "./TitleCard";
import { Subtitles } from "./Subtitles";
import type { StoryVideoProps } from "./schema";

const TRANSITION_FRAMES = 20;

export const StoryVideo: React.FC<StoryVideoProps> = ({
  title,
  audioSrc,
  scenes,
  subtitles,
  subtitleStyle,
  aiLabel,
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
                presentation={fade()}
              />
            )}
          </React.Fragment>
        ))}
      </TransitionSeries>

      {/* Look cinematográfico */}
      <Vignette />
      <Fireflies count={16} />

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
