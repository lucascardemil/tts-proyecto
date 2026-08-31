import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";

// Pseudo-aleatorio determinístico (Remotion necesita que cada frame se
// renderice igual siempre, así que no se puede usar Math.random()).
const seeded = (seed: number) => {
  const x = Math.sin(seed * 9999) * 10000;
  return x - Math.floor(x);
};

export const Fireflies: React.FC<{ count?: number }> = ({ count = 16 }) => {
  const frame = useCurrentFrame();
  const { width, height, durationInFrames } = useVideoConfig();

  const dots = Array.from({ length: count }).map((_, i) => {
    const baseX = seeded(i * 1.7) * width;
    const baseY = seeded(i * 3.1 + 1) * height * 0.8 + height * 0.05;
    const speed = 0.15 + seeded(i * 5.3) * 0.25;
    const phase = seeded(i * 2.2) * Math.PI * 2;

    const cycle = (frame / durationInFrames) * Math.PI * 2 * speed + phase;
    const driftX = Math.sin(cycle) * 26;
    const driftY = Math.cos(cycle * 0.8) * 18;
    const opacity = 0.2 + 0.55 * Math.abs(Math.sin(frame / 45 + i * 10));
    const size = 2 + seeded(i * 7.7) * 3;

    return (
      <div
        key={i}
        style={{
          position: "absolute",
          left: baseX + driftX,
          top: baseY + driftY,
          width: size,
          height: size,
          borderRadius: "50%",
          background: "#ffe9b0",
          boxShadow: "0 0 8px 3px rgba(255,220,150,0.6)",
          opacity,
        }}
      />
    );
  });

  return <AbsoluteFill style={{ pointerEvents: "none" }}>{dots}</AbsoluteFill>;
};
