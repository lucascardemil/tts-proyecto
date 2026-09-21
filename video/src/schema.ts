import {z} from "zod";

export const kenBurnsDirection = z.enum([
  "zoomIn",
  "zoomOut",
  "panLeft",
  "panRight",
  "none",
]);

export const sceneType = z.enum(["image", "video"]);

export const sceneClipSchema = z.object({
  src: z.string(),
  type: sceneType,
  startFrame: z.number(),
  endFrame: z.number(),
  // Solo aplica a imágenes (efecto Ken Burns)
  kenBurns: kenBurnsDirection.optional(),
  // Solo aplica a videos: duración real (o estimada) del clip original, para
  // saber cuántos frames dura una "vuelta" al hacer loop si el slot asignado
  // es un poco más largo que el clip.
  nativeDurationInFrames: z.number().optional(),
  // Solo aplica a videos: tasa de reproducción (<1 = cámara lenta) para
  // estirar el clip completo, sin loop, cuando el slot asignado es más
  // largo que la duración real del clip.
  playbackRate: z.number().optional(),
});

export const subtitleWordSchema = z.object({
  word: z.string(),
  start: z.number(), // segundos
  end: z.number(), // segundos
});

export const subtitleFontId = z.enum([
  "cinzel", "playfair", "poppins", "montserrat", "bebas", "anton", "bangers",
]);
export const subtitlePosition = z.enum(["top", "center", "bottom"]);
export const subtitleAnimationType = z.enum(["highlight", "scale", "bounce"]);

export const subtitleStyleSchema = z.object({
  fontFamily: subtitleFontId.default("cinzel"),
  fontSize: z.number().default(44),
  position: subtitlePosition.default("bottom"),
  // Posición libre opcional (% de ancho/alto de pantalla, 0-100). Si ambos
  // están definidos, tienen prioridad sobre `position`.
  positionX: z.number().min(0).max(100).optional(),
  positionY: z.number().min(0).max(100).optional(),
  textColor: z.string().default("#ffffff"),
  highlightColor: z.string().default("#ffd98a"),
  background: z.boolean().default(true),
  // Contorno del texto (look "karaoke TikTok": blanco + borde negro). Sin
  // definir = sin contorno, se usa solo la sombra existente.
  strokeColor: z.string().nullable().default(null),
  strokeWidth: z.number().default(2),
  uppercase: z.boolean().default(false),
  italic: z.boolean().default(false),
  letterSpacing: z.number().default(0),
  animationType: subtitleAnimationType.default("highlight"),
});

export const storyVideoSchema = z.object({
  title: z.string(),
  audioSrc: z.string(),
  totalDurationSeconds: z.number().default(10),
  scenes: z.array(sceneClipSchema),
  subtitles: z.array(subtitleWordSchema),
  // Rótulo fijo durante todo el video (p. ej. "Historia recreada con IA").
  // Sin definir = no se muestra.
  aiLabel: z.string().optional(),
  subtitleStyle: subtitleStyleSchema.default({
    fontFamily: "cinzel",
    fontSize: 44,
    position: "bottom",
    textColor: "#ffffff",
    highlightColor: "#ffd98a",
    background: true,
    strokeColor: null,
    strokeWidth: 2,
    uppercase: false,
    italic: false,
    letterSpacing: 0,
    animationType: "highlight",
  }),
});

export type StoryVideoProps = z.infer<typeof storyVideoSchema>;
export type SceneClip = z.infer<typeof sceneClipSchema>;
export type SubtitleWord = z.infer<typeof subtitleWordSchema>;
export type SubtitleStyle = z.infer<typeof subtitleStyleSchema>;
export type SubtitleFontId = z.infer<typeof subtitleFontId>;
export type SubtitlePosition = z.infer<typeof subtitlePosition>;
export type KenBurnsDirection = z.infer<typeof kenBurnsDirection>;
