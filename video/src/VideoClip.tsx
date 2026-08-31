import React from "react";
import { AbsoluteFill, Video, staticFile, Loop } from "remotion";

export const VideoClip: React.FC<{
  src: string;
  nativeDurationInFrames: number;
  playbackRate?: number;
}> = ({ src, nativeDurationInFrames, playbackRate }) => {
  const videoStyle = {
    width: "100%",
    height: "100%",
    objectFit: "cover" as const,
    filter: "brightness(0.86) saturate(1.05) contrast(1.03)",
  };

  // El slot asignado es más largo que el clip: en vez de hacer loop (que
  // reinicia el clip a frame 0 y se ve como un salto/glitch), reproducimos
  // en cámara lenta una sola vez para llenar el slot completo sin cortes.
  if (playbackRate !== undefined && playbackRate < 1) {
    return (
      <AbsoluteFill style={{ overflow: "hidden", backgroundColor: "#000" }}>
        <Video
          src={staticFile(src)}
          muted
          playbackRate={playbackRate}
          style={videoStyle}
        />
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={{ overflow: "hidden", backgroundColor: "#000" }}>
      <Loop durationInFrames={Math.max(nativeDurationInFrames, 1)}>
        <Video src={staticFile(src)} muted style={videoStyle} />
      </Loop>
    </AbsoluteFill>
  );
};
