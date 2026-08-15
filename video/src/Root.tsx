import React from "react";
import { Composition } from "remotion";
import { AdVideo, type AdVideoProps } from "./AdVideo";

/**
 * Duration is never hardcoded — it comes from the props Python emits, which are
 * derived from the narration audio length. See vira/voice.py.
 */
const defaultProps: AdVideoProps = {
  brand: "Chips",
  product: "spicy chips",
  hook: "Everyone's chasing heat. Nobody's chasing flavour.",
  cta: "Tap to try the first batch",
  caption: "We made the chip we couldn't find.",
  hashtags: ["snacktok", "smallbusiness", "spicy"],
  audioSrc: null,
  fps: 30,
  beats: [
    { say: "Everyone's chasing heat.", show: "hand grabbing bag", shot: "close, handheld", startFrame: 0, endFrame: 60 },
    { say: "Nobody's chasing flavour.", show: "pour into bowl", shot: "top-down, natural light", startFrame: 60, endFrame: 130 },
  ],
};

export const RemotionRoot: React.FC = () => (
  <Composition
    id="AdVideo"
    component={AdVideo}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={900}
    defaultProps={defaultProps}
    calculateMetadata={({ props }) => ({
      // Audio length drives composition length, not the other way round.
      durationInFrames:
        (props as AdVideoProps & { durationInFrames?: number }).durationInFrames ?? 900,
      fps: props.fps ?? 30,
    })}
  />
);
