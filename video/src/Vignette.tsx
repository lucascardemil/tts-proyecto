import React from "react";
import { AbsoluteFill } from "remotion";

export const Vignette: React.FC = () => (
  <AbsoluteFill
    style={{
      background:
        "radial-gradient(ellipse at center, rgba(0,0,0,0) 38%, rgba(0,0,0,0.6) 100%)",
      pointerEvents: "none",
    }}
  />
);
