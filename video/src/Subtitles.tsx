import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont as loadCinzel } from "@remotion/google-fonts/Cinzel";
import { loadFont as loadPlayfairDisplay } from "@remotion/google-fonts/PlayfairDisplay";
import { loadFont as loadPoppins } from "@remotion/google-fonts/Poppins";
import { loadFont as loadMontserrat } from "@remotion/google-fonts/Montserrat";
import type { SubtitleWord, SubtitleStyle, SubtitleFontId, SubtitlePosition } from "./schema";

// Se cargan una sola vez, a nivel de módulo (no dentro del componente):
// Remotion espera a que las fuentes estén listas antes de renderizar el
// primer frame. Son pocas (4) y quedan cacheadas, así que precargarlas
// todas es más simple que cargar la elegida en runtime.
const { fontFamily: cinzelFontFamily } = loadCinzel("normal", { weights: ["400"], subsets: ["latin"] });
const { fontFamily: playfairFontFamily } = loadPlayfairDisplay("normal", { weights: ["700"], subsets: ["latin"] });
const { fontFamily: poppinsFontFamily } = loadPoppins("normal", { weights: ["500"], subsets: ["latin"] });
const { fontFamily: montserratFontFamily } = loadMontserrat("normal", { weights: ["600"], subsets: ["latin"] });

const fontMap: Record<SubtitleFontId, string> = {
  cinzel: cinzelFontFamily,
  playfair: playfairFontFamily,
  poppins: poppinsFontFamily,
  montserrat: montserratFontFamily,
};

const positionMap: Record<SubtitlePosition, React.CSSProperties> = {
  top: { justifyContent: "flex-start", paddingTop: "8%" },
  center: { justifyContent: "center" },
  bottom: { justifyContent: "flex-end", paddingBottom: "13%" },
};

const WORDS_PER_LINE = 5;

export const Subtitles: React.FC<{ words: SubtitleWord[]; style: SubtitleStyle }> = ({
  words,
  style,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;

  if (words.length === 0) return null;

  const lines: SubtitleWord[][] = [];
  for (let i = 0; i < words.length; i += WORDS_PER_LINE) {
    lines.push(words.slice(i, i + WORDS_PER_LINE));
  }

  const activeLine = lines.find(
    (line) => t >= line[0].start - 0.05 && t <= line[line.length - 1].end + 0.25
  );

  if (!activeLine) return null;

  const hasCustomPosition = style.positionX !== undefined && style.positionY !== undefined;

  return (
    <AbsoluteFill
      style={hasCustomPosition ? undefined : { alignItems: "center", ...positionMap[style.position] }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          gap: "0 12px",
          maxWidth: "86%",
          padding: style.background ? "12px 28px" : 0,
          borderRadius: style.background ? 18 : 0,
          background: style.background ? "rgba(0,0,0,0.38)" : "transparent",
          ...(hasCustomPosition
            ? {
                position: "absolute",
                left: `${style.positionX}%`,
                top: `${style.positionY}%`,
                transform: "translate(-50%, -50%)",
              }
            : {}),
        }}
      >
        {activeLine.map((w, i) => {
          const active = t >= w.start && t <= w.end;
          return (
            <span
              key={i}
              style={{
                fontFamily: fontMap[style.fontFamily],
                fontSize: style.fontSize,
                fontWeight: 400,
                color: active ? style.highlightColor : style.textColor,
                // Sin el fondo semitransparente detrás, se refuerza la sombra
                // un poco para que el texto siga siendo legible sobre la imagen.
                textShadow: style.background
                  ? "0 2px 10px rgba(0,0,0,0.85)"
                  : "0 2px 6px rgba(0,0,0,0.9), 0 0 18px rgba(0,0,0,0.75)",
              }}
            >
              {w.word}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
