import React from "react";
import { AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";

// Celular con la portada de un producto (p. ej. el ebook) en pantalla: entra con un resorte desde
// abajo, queda con una ligera inclinación y flota suave. Se dibuja encima de la última escena.
export const PhoneCard: React.FC<{ src: string; startFrame: number; cta?: string }> = ({ src, startFrame, cta }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const t = frame - startFrame;
  if (t < 0) return null;

  const enter = spring({ frame: t, fps, config: { damping: 14, stiffness: 90, mass: 0.9 } });
  const float = Math.sin(t / 14) * 6;
  const phoneW = Math.round(width * 0.46);
  const phoneH = Math.round(phoneW * 1.95);
  const y = interpolate(enter, [0, 1], [height * 0.6, 0]);
  const opacity = interpolate(t, [0, 6], [0, 1], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-start", paddingTop: Math.round(height * 0.07) }}>
      <div
        style={{
          width: phoneW,
          height: phoneH,
          transform: `translateY(${y + float}px) rotate(${interpolate(enter, [0, 1], [-8, -3])}deg) scale(${interpolate(enter, [0, 1], [0.85, 1])})`,
          opacity,
          borderRadius: phoneW * 0.13,
          background: "#111",
          padding: phoneW * 0.035,
          boxShadow: "0 40px 90px rgba(0,0,0,0.55), 0 0 0 3px #2a2a2a inset",
          boxSizing: "border-box",
        }}
      >
        <div style={{ position: "relative", width: "100%", height: "100%", borderRadius: phoneW * 0.1, overflow: "hidden", background: "#fff6ea" }}>
          <Img src={staticFile(src)} style={{ width: "100%", objectFit: "contain", display: "block", marginTop: "6%" }} />
          {cta ? (
            <div
              style={{
                position: "absolute", left: "8%", right: "8%", bottom: "6%", padding: "5% 0", borderRadius: 999,
                background: "#cd6f58", color: "#fff", textAlign: "center", fontFamily: "Poppins, Arial, sans-serif",
                fontWeight: 700, fontSize: Math.round(phoneW * 0.072), letterSpacing: 1,
                boxShadow: "0 10px 24px rgba(205,111,88,0.45)",
              }}
            >
              {cta}
            </div>
          ) : null}
          <div
            style={{
              position: "absolute", top: "1.6%", left: "50%", transform: "translateX(-50%)",
              width: "26%", height: "2.4%", borderRadius: 999, background: "#111",
            }}
          />
        </div>
      </div>
    </AbsoluteFill>
  );
};
