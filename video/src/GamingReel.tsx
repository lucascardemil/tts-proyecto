import React from "react";
import {
  AbsoluteFill,
  Audio,
  Easing,
  Img,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont as loadAnton } from "@remotion/google-fonts/Anton";
import { loadFont as loadMontserrat } from "@remotion/google-fonts/Montserrat";
import { Subtitles } from "./Subtitles";
import { Vignette } from "./Vignette";
import type { GamingReelProps, ReelScene } from "./schema";

const { fontFamily: anton } = loadAnton("normal", { weights: ["400"], subsets: ["latin"] });
const { fontFamily: montserrat } = loadMontserrat("normal", { weights: ["600", "800"], subsets: ["latin"] });

// Zona segura de Reels/TikTok (igual que ClipEdit): el contenido vive entre y=330 y y=1330; debajo van los
// subtitulos y la UI de la app (~380 px inferiores).
const W = 1080;
const H = 1920;
const SAFE_TOP = 330;
const SAFE_BOTTOM = 1330;
const SAFE_H = SAFE_BOTTOM - SAFE_TOP;

const ANGLE_LABEL: Record<GamingReelProps["angle"], string> = {
  RECORD: "NEW RECORD",
  FACT: "DID YOU KNOW?",
  NEWS: "GAMING NEWS",
};

const easeOut = Easing.out(Easing.cubic);

// Entrada (spring) y salida (fade) de cada escena, sobre el frame local de su <Sequence>.
const useSceneMotion = (duration: number) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 16, stiffness: 140 } });
  const exit = interpolate(frame, [duration - 8, duration], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return { frame, fps, enter, opacity: Math.min(1, frame / 4) * exit };
};

const Sfx: React.FC<{ file: string; volume?: number }> = ({ file, volume = 0.5 }) => (
  <Audio src={staticFile(`sfx/${file}.wav`)} volume={volume} />
);

// Imagen de fondo con zoom lento; cada una entra con fundido sobre la anterior (que sigue debajo).
const BG_FADE = 15;
const BackdropImage: React.FC<{ src: string; duration: number }> = ({ src, duration }) => {
  const frame = useCurrentFrame();
  const fadeIn = interpolate(frame, [0, BG_FADE], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const zoom = interpolate(frame, [0, duration], [1, 1.14], { extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ opacity: fadeIn }}>
      <Img src={staticFile(src)} style={{ width: W, height: H, objectFit: "cover", transform: `scale(${zoom})` }} />
    </AbsoluteFill>
  );
};

const Background: React.FC<{ accent: string; backgrounds: GamingReelProps["backgrounds"] }> = ({ accent, backgrounds }) => {
  const frame = useCurrentFrame();
  const drift = (frame * 0.6) % 120;
  const hasImages = backgrounds.length > 0;
  return (
    <AbsoluteFill style={{ background: "linear-gradient(180deg, #05060c 0%, #0b0d1a 55%, #05060c 100%)" }}>
      {backgrounds.map((bg, i) => (
        <Sequence key={bg.src} from={bg.startFrame} durationInFrames={Math.max(1, bg.endFrame - bg.startFrame) + (i < backgrounds.length - 1 ? BG_FADE : 0)} layout="none">
          <BackdropImage src={bg.src} duration={Math.max(1, bg.endFrame - bg.startFrame)} />
        </Sequence>
      ))}
      {hasImages ? <AbsoluteFill style={{ background: "linear-gradient(180deg, rgba(5,6,12,0.66) 0%, rgba(5,6,12,0.42) 50%, rgba(5,6,12,0.74) 100%)" }} /> : null}
      <AbsoluteFill
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,0.05) 2px, transparent 2px), linear-gradient(90deg, rgba(255,255,255,0.05) 2px, transparent 2px)",
          backgroundSize: "120px 120px",
          backgroundPosition: `0px ${drift}px`,
          transform: "perspective(900px) rotateX(50deg) scale(2.2)",
          transformOrigin: "50% 100%",
          opacity: hasImages ? 0.2 : 0.55,
        }}
      />
      {[0, 1].map((i) => (
        <div
          key={i}
          style={{
            position: "absolute",
            width: 760,
            height: 760,
            borderRadius: "50%",
            left: i === 0 ? -240 + Math.sin(frame / 70) * 60 : W - 520 + Math.cos(frame / 80) * 60,
            top: i === 0 ? 260 + Math.cos(frame / 90) * 50 : 980 + Math.sin(frame / 60) * 50,
            background: `radial-gradient(circle, ${accent}55 0%, transparent 65%)`,
          }}
        />
      ))}
      <Vignette intensity="soft" />
    </AbsoluteFill>
  );
};

const Header: React.FC<{ accent: string; angle: GamingReelProps["angle"] }> = ({ accent, angle }) => {
  const frame = useCurrentFrame();
  const pulse = 0.65 + 0.35 * Math.abs(Math.sin(frame / 14));
  return (
    <div
      style={{
        position: "absolute",
        top: 150,
        left: 0,
        width: W,
        display: "flex",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 18,
          padding: "16px 34px",
          borderRadius: 999,
          background: "rgba(255,255,255,0.08)",
          border: `3px solid ${accent}`,
          fontFamily: anton,
          fontSize: 46,
          letterSpacing: 4,
          color: "#fff",
        }}
      >
        <span style={{ width: 20, height: 20, borderRadius: "50%", background: accent, opacity: pulse }} />
        {ANGLE_LABEL[angle]}
      </div>
    </div>
  );
};

const Shell: React.FC<{ children: React.ReactNode; opacity: number; translateY?: number }> = ({
  children,
  opacity,
  translateY = 0,
}) => (
  <div
    style={{
      position: "absolute",
      top: SAFE_TOP,
      left: 60,
      width: W - 120,
      height: SAFE_H,
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      opacity,
      transform: `translateY(${translateY}px)`,
    }}
  >
    {children}
  </div>
);

// ---- Escenas -------------------------------------------------------------------------------------------------------

const HookScene: React.FC<{ scene: Extract<ReelScene, { type: "hook" }>; accent: string; duration: number }> = ({
  scene,
  accent,
  duration,
}) => {
  const { frame, fps, opacity } = useSceneMotion(duration);
  const words = scene.text.split(/\s+/).filter(Boolean);
  const bar = interpolate(frame, [6, 26], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: easeOut });
  return (
    <Shell opacity={opacity}>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: "10px 28px" }}>
        {words.map((word, i) => {
          const pop = spring({ frame: frame - i * 5, fps, config: { damping: 11, stiffness: 170 } });
          const last = i === words.length - 1;
          return (
            <span
              key={`${word}-${i}`}
              style={{
                fontFamily: anton,
                fontSize: words.length > 5 ? 128 : 156,
                lineHeight: 1.02,
                color: last ? accent : "#fff",
                textTransform: "uppercase",
                textShadow: "0 8px 0 rgba(0,0,0,0.45)",
                display: "inline-block",
                opacity: Math.min(1, pop * 1.4),
                transform: `translateY(${(1 - pop) * 70}px) scale(${0.65 + 0.35 * pop})`,
              }}
            >
              {word}
            </span>
          );
        })}
      </div>
      <div style={{ marginTop: 46, height: 14, width: 560 * bar, background: accent, borderRadius: 8 }} />
    </Shell>
  );
};

const formatNumber = (value: number, decimals: number): string =>
  new Intl.NumberFormat("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(value);

const useCount = (target: number, frame: number): number => {
  const progress = interpolate(frame, [4, 52], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: easeOut });
  return target * progress;
};

const StatScene: React.FC<{ scene: Extract<ReelScene, { type: "stat" }>; accent: string; duration: number }> = ({
  scene,
  accent,
  duration,
}) => {
  const { frame, enter, opacity } = useSceneMotion(duration);
  const value = useCount(scene.number, frame);
  const ring = interpolate(frame, [4, 52], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: easeOut });
  const R = 400;
  const C = 2 * Math.PI * R;
  const text = `${scene.prefix}${formatNumber(value, scene.decimals)}`;
  return (
    <Shell opacity={opacity} translateY={(1 - enter) * 60}>
      <div style={{ position: "relative", width: 900, height: 900, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <svg width={900} height={900} style={{ position: "absolute", transform: "rotate(-90deg)" }}>
          <circle cx={450} cy={450} r={R} stroke="rgba(255,255,255,0.12)" strokeWidth={26} fill="none" />
          <circle
            cx={450}
            cy={450}
            r={R}
            stroke={accent}
            strokeWidth={26}
            strokeLinecap="round"
            fill="none"
            strokeDasharray={C}
            strokeDashoffset={C * (1 - ring)}
          />
        </svg>
        <div style={{ textAlign: "center" }}>
          <div
            style={{
              fontFamily: anton,
              fontSize: text.length > 6 ? 190 : 250,
              lineHeight: 1,
              color: "#fff",
              textShadow: `0 0 50px ${accent}aa`,
            }}
          >
            {text}
            {scene.suffix ? <span style={{ color: accent, fontSize: "0.55em", marginLeft: 12 }}>{scene.suffix}</span> : null}
          </div>
          <div style={{ margin: "26px auto 0", maxWidth: 560, lineHeight: 1.2, fontFamily: montserrat, fontWeight: 800, fontSize: 46, letterSpacing: 2, color: accent, textTransform: "uppercase" }}>
            {scene.text}
          </div>
        </div>
      </div>
    </Shell>
  );
};

const VersusScene: React.FC<{ scene: Extract<ReelScene, { type: "versus" }>; accent: string; duration: number }> = ({
  scene,
  accent,
  duration,
}) => {
  const { frame, fps, opacity } = useSceneMotion(duration);
  const top = Math.max(scene.valueA, scene.valueB, 1);
  const MAX_H = 640;
  const bars = [
    { label: scene.labelA, value: scene.valueA, color: scene.valueA >= scene.valueB ? accent : "#8b93a7" },
    { label: scene.labelB, value: scene.valueB, color: scene.valueB > scene.valueA ? accent : "#8b93a7" },
  ];
  return (
    <Shell opacity={opacity}>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "center", gap: 90, height: MAX_H + 180 }}>
        {bars.map((bar, i) => {
          const grow = spring({ frame: frame - 6 - i * 8, fps, config: { damping: 18, stiffness: 90 } });
          const shown = bar.value * Math.min(1, grow);
          return (
            <div key={bar.label} style={{ width: 330, display: "flex", flexDirection: "column", alignItems: "center" }}>
              <div style={{ fontFamily: anton, fontSize: 84, color: "#fff", marginBottom: 16 }}>
                {`${scene.prefix}${formatNumber(shown, bar.value % 1 ? 1 : 0)}${scene.suffix}`}
              </div>
              <div
                style={{
                  width: 330,
                  height: Math.max(6, (bar.value / top) * MAX_H * Math.min(1, grow)),
                  background: `linear-gradient(180deg, ${bar.color}, ${bar.color}99)`,
                  borderRadius: "22px 22px 0 0",
                  boxShadow: `0 0 40px ${bar.color}66`,
                }}
              />
              <div style={{ fontFamily: montserrat, fontWeight: 800, fontSize: 44, color: "#fff", marginTop: 20, textAlign: "center", textTransform: "uppercase" }}>
                {bar.label}
              </div>
            </div>
          );
        })}
      </div>
    </Shell>
  );
};

const TimelineScene: React.FC<{ scene: Extract<ReelScene, { type: "timeline" }>; accent: string; duration: number }> = ({
  scene,
  accent,
  duration,
}) => {
  const { frame, fps, opacity } = useSceneMotion(duration);
  const count = scene.items.length;
  const line = interpolate(frame, [0, 34], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: easeOut });
  const GAP = Math.min(250, 760 / Math.max(count - 1, 1));
  return (
    <Shell opacity={opacity}>
      <div style={{ position: "relative", width: 900, height: GAP * (count - 1) + 140 }}>
        <div
          style={{
            position: "absolute",
            left: 26,
            top: 40,
            width: 10,
            height: GAP * (count - 1) * line,
            background: accent,
            borderRadius: 6,
          }}
        />
        {scene.items.map((item, i) => {
          const pop = spring({ frame: frame - 8 - i * 12, fps, config: { damping: 13, stiffness: 150 } });
          return (
            <div
              key={`${item}-${i}`}
              style={{ position: "absolute", left: 0, top: i * GAP, display: "flex", alignItems: "center", gap: 44, opacity: Math.min(1, pop * 1.5), transform: `translateX(${(1 - pop) * 80}px)` }}
            >
              <div style={{ width: 62, height: 62, borderRadius: "50%", background: "#0b0d1a", border: `10px solid ${accent}`, transform: `scale(${pop})` }} />
              <div style={{ fontFamily: anton, fontSize: 70, lineHeight: 1.05, color: "#fff", width: 740, textTransform: "uppercase" }}>{item}</div>
            </div>
          );
        })}
      </div>
    </Shell>
  );
};

const QuoteScene: React.FC<{ scene: Extract<ReelScene, { type: "quote" }>; accent: string; duration: number }> = ({
  scene,
  accent,
  duration,
}) => {
  const { frame, enter, opacity } = useSceneMotion(duration);
  const words = scene.text.split(/\s+/).filter(Boolean);
  const shown = Math.min(words.length, Math.max(0, Math.floor((frame - 6) / 3)));
  return (
    <Shell opacity={opacity} translateY={(1 - enter) * 40}>
      <div style={{ fontFamily: anton, fontSize: 340, lineHeight: 0.6, color: accent, alignSelf: "flex-start", marginLeft: 20, height: 150 }}>“</div>
      <div style={{ fontFamily: montserrat, fontWeight: 800, fontSize: 70, lineHeight: 1.22, color: "#fff", textAlign: "left", width: 900 }}>
        {words.map((word, i) => (
          <span key={`${word}-${i}`} style={{ opacity: i < shown ? 1 : 0.12, marginRight: 16, display: "inline-block" }}>
            {word}
          </span>
        ))}
      </div>
      {scene.by ? (
        <div style={{ marginTop: 44, alignSelf: "flex-start", marginLeft: 20, fontFamily: anton, fontSize: 56, color: accent, letterSpacing: 2, textTransform: "uppercase" }}>
          — {scene.by}
        </div>
      ) : null}
    </Shell>
  );
};

const OutroScene: React.FC<{ scene: Extract<ReelScene, { type: "outro" }>; accent: string; source: string; duration: number }> = ({
  scene,
  accent,
  source,
  duration,
}) => {
  const { frame, fps, opacity } = useSceneMotion(duration);
  const pop = spring({ frame: frame - 4, fps, config: { damping: 10, stiffness: 160 } });
  const pulse = 1 + 0.05 * Math.sin(frame / 7);
  return (
    <Shell opacity={opacity}>
      <div style={{ fontFamily: anton, fontSize: 130, lineHeight: 1.05, color: "#fff", textAlign: "center", textTransform: "uppercase", transform: `scale(${0.7 + 0.3 * pop})`, opacity: Math.min(1, pop * 1.4) }}>
        {scene.text}
      </div>
      <div style={{ marginTop: 60, padding: "26px 80px", borderRadius: 999, background: accent, fontFamily: anton, fontSize: 70, color: "#0b0d1a", letterSpacing: 4, transform: `scale(${pulse})` }}>
        FOLLOW
      </div>
      {source ? (
        <div style={{ marginTop: 50, fontFamily: montserrat, fontWeight: 600, fontSize: 40, color: "rgba(255,255,255,0.75)", letterSpacing: 2 }}>
          SOURCE: {source.toUpperCase()}
        </div>
      ) : null}
    </Shell>
  );
};

const SceneView: React.FC<{ scene: ReelScene; props: GamingReelProps; duration: number }> = ({ scene, props, duration }) => {
  const { accent, sourceName } = props;
  switch (scene.type) {
    case "hook":
      return <HookScene scene={scene} accent={accent} duration={duration} />;
    case "stat":
      return <StatScene scene={scene} accent={accent} duration={duration} />;
    case "versus":
      return <VersusScene scene={scene} accent={accent} duration={duration} />;
    case "timeline":
      return <TimelineScene scene={scene} accent={accent} duration={duration} />;
    case "quote":
      return <QuoteScene scene={scene} accent={accent} duration={duration} />;
    case "outro":
      return <OutroScene scene={scene} accent={accent} source={sourceName} duration={duration} />;
  }
};

export const GamingReel: React.FC<GamingReelProps> = (props) => {
  const { scenes, backgrounds, subtitles, subtitleStyle, accent, angle, audioSrc } = props;
  return (
    <AbsoluteFill style={{ backgroundColor: "#05060c", width: W, height: H }}>
      <Background accent={accent} backgrounds={backgrounds} />
      <Header accent={accent} angle={angle} />
      {scenes.map((scene, i) => {
        const duration = Math.max(1, scene.endFrame - scene.startFrame);
        return (
          <Sequence key={`${scene.type}-${i}`} from={scene.startFrame} durationInFrames={duration}>
            <SceneView scene={scene} props={props} duration={duration} />
            {i > 0 ? <Sfx file="whoosh_in" volume={0.35} /> : <Sfx file="pop" volume={0.5} />}
            {scene.type === "stat" || scene.type === "versus" ? <Sequence from={8} layout="none"><Sfx file="hit" volume={0.45} /></Sequence> : null}
            {scene.type === "outro" ? <Sequence from={6} layout="none"><Sfx file="ding" volume={0.4} /></Sequence> : null}
          </Sequence>
        );
      })}
      {audioSrc ? <Audio src={staticFile(audioSrc)} /> : null}
      <Subtitles words={subtitles} style={subtitleStyle} />
    </AbsoluteFill>
  );
};

export const gamingReelDefaultProps: GamingReelProps = {
  scenes: [
    { type: "hook", text: "SILKSONG SOLD OUT FAST", startFrame: 0, endFrame: 90 },
    { type: "stat", text: "COPIES SOLD IN DAY ONE", prefix: "", number: 2.5, decimals: 1, suffix: "M", startFrame: 90, endFrame: 200 },
    { type: "versus", labelA: "DAY ONE", labelB: "PRICE", valueA: 2.5, valueB: 20, prefix: "", suffix: "", startFrame: 200, endFrame: 310 },
    { type: "timeline", items: ["Sep 4: Launch", "Sep 5: Sold out", "Sep 9: Patch"], startFrame: 310, endFrame: 430 },
    { type: "quote", text: "We are overwhelmed by the response from players", by: "Team Cherry", startFrame: 430, endFrame: 540 },
    { type: "outro", text: "FOLLOW FOR MORE GAMING NEWS", startFrame: 540, endFrame: 630 },
  ],
  backgrounds: [],
  subtitles: [],
  subtitleStyle: {
    fontFamily: "montserrat",
    fontSize: 46,
    position: "bottom",
    textColor: "#ffffff",
    highlightColor: "#ffd60a",
    background: false,
    strokeColor: "#000000",
    strokeWidth: 4,
    uppercase: true,
    italic: false,
    letterSpacing: 0,
    animationType: "scale",
  },
  accent: "#ffd60a",
  angle: "RECORD",
  sourceName: "IGN",
  audioSrc: "",
  totalDurationSeconds: 21,
};
