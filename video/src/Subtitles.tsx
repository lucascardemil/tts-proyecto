import React, { useMemo } from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, Easing } from "remotion";
import { loadFont as loadCinzel } from "@remotion/google-fonts/Cinzel";
import { loadFont as loadPlayfairDisplay } from "@remotion/google-fonts/PlayfairDisplay";
import { loadFont as loadPoppins } from "@remotion/google-fonts/Poppins";
import { loadFont as loadMontserrat } from "@remotion/google-fonts/Montserrat";
import { loadFont as loadBebasNeue } from "@remotion/google-fonts/BebasNeue";
import { loadFont as loadAnton } from "@remotion/google-fonts/Anton";
import { loadFont as loadBangers } from "@remotion/google-fonts/Bangers";
import type { SubtitleWord, SubtitleStyle, SubtitleFontId, SubtitlePosition } from "./schema";

// Se cargan una sola vez, a nivel de módulo (no dentro del componente):
// Remotion espera a que las fuentes estén listas antes de renderizar el
// primer frame. Son pocas y quedan cacheadas, así que precargarlas todas
// es más simple que cargar la elegida en runtime.
const { fontFamily: cinzelFontFamily } = loadCinzel("normal", { weights: ["400"], subsets: ["latin"] });
const { fontFamily: playfairFontFamily } = loadPlayfairDisplay("normal", { weights: ["700"], subsets: ["latin"] });
const { fontFamily: poppinsFontFamily } = loadPoppins("normal", { weights: ["500"], subsets: ["latin"] });
const { fontFamily: montserratFontFamily } = loadMontserrat("normal", { weights: ["600"], subsets: ["latin"] });
const { fontFamily: bebasNeueFontFamily } = loadBebasNeue("normal", { weights: ["400"], subsets: ["latin"] });
const { fontFamily: antonFontFamily } = loadAnton("normal", { weights: ["400"], subsets: ["latin"] });
const { fontFamily: bangersFontFamily } = loadBangers("normal", { weights: ["400"], subsets: ["latin"] });

const fontMap: Record<SubtitleFontId, string> = {
  cinzel: cinzelFontFamily,
  playfair: playfairFontFamily,
  poppins: poppinsFontFamily,
  montserrat: montserratFontFamily,
  bebas: bebasNeueFontFamily,
  anton: antonFontFamily,
  bangers: bangersFontFamily,
};

const positionMap: Record<SubtitlePosition, React.CSSProperties> = {
  top: { justifyContent: "flex-start", paddingTop: "8%" },
  center: { justifyContent: "center" },
  bottom: { justifyContent: "flex-end" },
};

// Margen del subtítulo sobre el borde inferior: cubre la UI que TikTok/Reels
// pisan abajo del video (caption/audio bar + botones nuevos de fines de 2025,
// ~320-360px en 1080x1920) para que el subtítulo no quede tapado al republicar
// el mismo video en varias redes.
const BOTTOM_MARGIN_REFERENCE_HEIGHT = 1920;
const BOTTOM_MARGIN_PX_AT_REFERENCE = 380;

// Contorno vía 4 text-shadow diagonales en vez de -webkit-text-stroke: este
// último produce manchas/flecos de color en tamaños chicos (visto en el
// preview de app.py); la técnica de sombras es la que usa el repo de
// referencia (nicolaigaina/ai-video-captions) y no tiene ese artefacto.
const buildStrokeShadow = (color: string, width: number): string =>
  [
    `${width}px ${width}px 0 ${color}`,
    `-${width}px -${width}px 0 ${color}`,
    `${width}px -${width}px 0 ${color}`,
    `-${width}px ${width}px 0 ${color}`,
  ].join(", ");

const WORDS_PER_LINE = 5;

export const Subtitles: React.FC<{ words: SubtitleWord[]; style: SubtitleStyle }> = ({
  words,
  style,
}) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();
  const t = frame / fps;
  const bottomMargin = (BOTTOM_MARGIN_PX_AT_REFERENCE * height) / BOTTOM_MARGIN_REFERENCE_HEIGHT;

  const lines: SubtitleWord[][] = useMemo(() => {
    const result: SubtitleWord[][] = [];
    for (let i = 0; i < words.length; i += WORDS_PER_LINE) {
      result.push(words.slice(i, i + WORDS_PER_LINE));
    }
    return result;
  }, [words]);

  if (words.length === 0) return null;

  const activeLine = lines.find(
    (line) => t >= line[0].start - 0.05 && t <= line[line.length - 1].end + 0.25
  );

  if (!activeLine) return null;

  const hasCustomPosition = style.positionX !== undefined && style.positionY !== undefined;

  return (
    <AbsoluteFill
      style={
        hasCustomPosition
          ? undefined
          : {
              alignItems: "center",
              ...positionMap[style.position],
              ...(style.position === "bottom" ? { paddingBottom: bottomMargin } : {}),
            }
      }
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
          const contrastShadow = style.background
            ? "0 2px 10px rgba(0,0,0,0.85)"
            : "0 2px 6px rgba(0,0,0,0.9), 0 0 18px rgba(0,0,0,0.75)";
          const textShadow = style.strokeColor
            ? `${buildStrokeShadow(style.strokeColor, style.strokeWidth)}, ${contrastShadow}`
            : contrastShadow;
          // Progreso 0→1→0 dentro de la ventana activa de la palabra, usado
          // por las animaciones "scale"/"bounce" (un pequeño pulso de ida y
          // vuelta mientras la palabra está resaltada).
          const pulse =
            active && style.animationType !== "highlight"
              ? interpolate(
                  t,
                  [w.start, (w.start + w.end) / 2, w.end],
                  [0, 1, 0],
                  { easing: Easing.out(Easing.quad), extrapolateLeft: "clamp", extrapolateRight: "clamp" }
                )
              : 0;
          const transform =
            style.animationType === "scale"
              ? `scale(${1 + 0.15 * pulse})`
              : style.animationType === "bounce"
              ? `translateY(${-10 * pulse}px)`
              : undefined;
          return (
            <span
              key={i}
              style={{
                fontFamily: fontMap[style.fontFamily],
                fontSize: style.fontSize,
                fontWeight: 400,
                fontStyle: style.italic ? "italic" : "normal",
                letterSpacing: style.letterSpacing ? `${style.letterSpacing}px` : undefined,
                color: active ? style.highlightColor : style.textColor,
                textTransform: style.uppercase ? "uppercase" : "none",
                transform,
                display: "inline-block",
                textShadow,
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
