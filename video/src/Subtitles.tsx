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
  // Luckiest Guy no viene en @remotion/google-fonts: la carga StoryVideo (FontFace desde public/).
  luckiest: "'Luckiest Guy', 'Liberation Sans', Arial, sans-serif",
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

const WORDS_PER_LINE = 5; // fallback si el canvas de medición no está disponible

// Canvas 2D reusado solo para medir texto (measureText), nunca se dibuja ni
// se monta en el DOM — Remotion renderiza en Chrome real (headless), así
// que esta API existe siempre durante el render, no solo en el navegador
// del usuario. Un solo canvas a nivel de módulo evita crear uno nuevo por
// frame.
let measureCtx: CanvasRenderingContext2D | null | undefined;
const getMeasureCtx = (): CanvasRenderingContext2D | null => {
  if (measureCtx !== undefined) return measureCtx;
  measureCtx = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d");
  return measureCtx;
};

// Separación horizontal entre palabras (debe coincidir con `gap: "0 12px"`
// del contenedor flex de abajo) y padding lateral cuando hay fondo (debe
// coincidir con `padding: "12px 28px"`).
const WORD_GAP_PX = 12;
const BACKGROUND_PADDING_X_PX = 28;

export const Subtitles: React.FC<{ words: SubtitleWord[]; style: SubtitleStyle }> = ({
  words,
  style,
}) => {
  const frame = useCurrentFrame();
  const { fps, height, width } = useVideoConfig();
  const t = frame / fps;
  const bottomMargin = (BOTTOM_MARGIN_PX_AT_REFERENCE * height) / BOTTOM_MARGIN_REFERENCE_HEIGHT;

  // Agrupa palabras en líneas por ANCHO REAL renderizado (canvas measureText
  // con la misma fuente/tamaño/letterSpacing/uppercase que se usa para
  // pintar), no por conteo fijo de palabras — evita que una línea de 5
  // palabras largas desborde el maxWidth del contenedor (86% del ancho de
  // video) mientras una de 5 palabras cortas deja espacio de sobra.
  const lines: SubtitleWord[][] = useMemo(() => {
    const ctx = getMeasureCtx();
    const fontFamily = fontMap[style.fontFamily];
    const maxTextWidth =
      width * 0.86 - (style.background ? BACKGROUND_PADDING_X_PX * 2 : 0) - 4; // -4px de margen de seguridad

    if (!ctx) {
      // No debería pasar durante un render real (Chrome headless siempre
      // tiene Canvas2D) — solo como red de seguridad.
      const result: SubtitleWord[][] = [];
      for (let i = 0; i < words.length; i += WORDS_PER_LINE) {
        result.push(words.slice(i, i + WORDS_PER_LINE));
      }
      return result;
    }

    ctx.font = `${style.italic ? "italic " : ""}400 ${style.fontSize}px ${fontFamily}`;
    const measure = (word: string): number => {
      const text = style.uppercase ? word.toUpperCase() : word;
      const extraLetterSpacing = Math.max(text.length - 1, 0) * style.letterSpacing;
      return ctx.measureText(text).width + extraLetterSpacing;
    };

    const result: SubtitleWord[][] = [];
    let current: SubtitleWord[] = [];
    let currentWidth = 0;
    for (const w of words) {
      const wordWidth = measure(w.word);
      const widthIfAdded = current.length === 0 ? wordWidth : currentWidth + WORD_GAP_PX + wordWidth;
      if (current.length > 0 && widthIfAdded > maxTextWidth) {
        result.push(current);
        current = [w];
        currentWidth = wordWidth;
      } else {
        current.push(w);
        currentWidth = widthIfAdded;
      }
    }
    if (current.length > 0) result.push(current);
    return result;
  }, [
    words,
    width,
    style.fontFamily,
    style.fontSize,
    style.italic,
    style.uppercase,
    style.letterSpacing,
    style.background,
  ]);

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
