import React from "react";
import { Composition } from "remotion";
import { StoryVideo } from "./StoryVideo";
import { storyVideoSchema, type StoryVideoProps } from "./schema";

const FPS = 30;

const defaultProps = {
  title: "El viejo perro que esperaba cada noche junto al faro",
  audioSrc: "",
  totalDurationSeconds: 30,
  scenes: [],
  subtitles: [],
  subtitleStyle: {
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
  },
} satisfies StoryVideoProps;

const calculateMetadata = async ({ props }: { props: StoryVideoProps }) => {
  const durationInFrames = Math.max(
    FPS * 3,
    Math.round(props.totalDurationSeconds * FPS)
  );
  return { durationInFrames };
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="StoryVideo"
        component={StoryVideo}
        durationInFrames={FPS * 30}
        fps={FPS}
        width={1080} // vertical (Reels/Shorts/TikTok)
        height={1920}
        schema={storyVideoSchema}
        defaultProps={defaultProps}
        calculateMetadata={calculateMetadata}
      />
      <Composition
        id="StoryVideoHorizontal"
        component={StoryVideo}
        durationInFrames={FPS * 30}
        fps={FPS}
        width={1920} // horizontal (YouTube estándar)
        height={1080}
        schema={storyVideoSchema}
        defaultProps={defaultProps}
        calculateMetadata={calculateMetadata}
      />
    </>
  );
};
