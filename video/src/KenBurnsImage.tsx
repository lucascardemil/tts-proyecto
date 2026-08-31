import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { KenBurnsDirection } from "./schema";

const MAX_ZOOM = 1.15;
const MAX_PAN_PERCENT = 4;

export const KenBurnsImage: React.FC<{
  src: string;
  direction: KenBurnsDirection;
}> = ({ src, direction }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const progress = interpolate(frame, [0, Math.max(durationInFrames - 1, 1)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  let scale = 1;
  let translateX = 0;

  switch (direction) {
    case "zoomIn":
      scale = interpolate(progress, [0, 1], [1, MAX_ZOOM]);
      break;
    case "zoomOut":
      scale = interpolate(progress, [0, 1], [MAX_ZOOM, 1]);
      break;
    case "panLeft":
      scale = MAX_ZOOM;
      translateX = interpolate(progress, [0, 1], [MAX_PAN_PERCENT, -MAX_PAN_PERCENT]);
      break;
    case "panRight":
      scale = MAX_ZOOM;
      translateX = interpolate(progress, [0, 1], [-MAX_PAN_PERCENT, MAX_PAN_PERCENT]);
      break;
  }

  return (
    <AbsoluteFill style={{ overflow: "hidden", backgroundColor: "#000" }}>
      <Img
        src={staticFile(src)}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `scale(${scale}) translateX(${translateX}%)`,
          filter: "brightness(0.86) saturate(1.05) contrast(1.03)",
        }}
      />
    </AbsoluteFill>
  );
};
