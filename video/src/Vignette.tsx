import React from "react";
import { AbsoluteFill } from "remotion";

export const Vignette: React.FC<{ intensity?: "normal" | "soft" }> = ({
  intensity = "normal",
}) => (
  <AbsoluteFill
    style={{
      background:
        intensity === "soft"
          ? "radial-gradient(ellipse at center, rgba(0,0,0,0) 55%, rgba(0,0,0,0.28) 100%)"
          : "radial-gradient(ellipse at center, rgba(0,0,0,0) 38%, rgba(0,0,0,0.6) 100%)",
      pointerEvents: "none",
    }}
  />
);
