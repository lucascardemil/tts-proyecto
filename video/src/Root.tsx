import React from "react";
import { Composition } from "remotion";
import { StoryVideo } from "./StoryVideo";
import { storyVideoSchema, type StoryVideoProps } from "./schema";

const FPS = 30;
const WIDTH = 1080; // formato vertical (Reels/Shorts/TikTok)
const HEIGHT = 1920;

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="StoryVideo"
      component={StoryVideo}
      durationInFrames={FPS * 30}
      fps={FPS}
      width={WIDTH}
      height={HEIGHT}
      schema={storyVideoSchema}
      defaultProps={{
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
        },
      } satisfies StoryVideoProps}
      calculateMetadata={async ({ props }) => {
        const durationInFrames = Math.max(
          FPS * 3,
          Math.round(props.totalDurationSeconds * FPS)
        );
        return { durationInFrames };
      }}
    />
  );
};
