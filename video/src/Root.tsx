import React from "react";
import { Composition } from "remotion";
import { AdVideo, type AdVideoProps } from "./AdVideo";
import { AdStill, type AdStillProps } from "./AdStill";

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

/**
 * The static ad. A Composition rather than a Still because the caption's
 * entry spring has to have SETTLED before the frame is grabbed — at frame 0
 * every word is at 30% opacity and half scale, which is the "render succeeded
 * and is blank" trap CLAUDE.md warns about. `remotion still AdStill --frame=N`
 * picks the settled frame, and N comes from Python with the word timings.
 */
const stillProps: AdStillProps = {
  brand: "Chips",
  headline: "I gave these ten SECONDS and lost the bag",
  cta: "Tap to try the first batch",
  image: null,
  fps: 30,
  durationInFrames: 120,
  beat: {
    say: "I gave these ten SECONDS and lost the bag",
    show: "hand grabbing bag",
    shot: "close, handheld",
    motion: "punch",
    startFrame: 0,
    endFrame: 120,
    words: [
      { w: "gave", startFrame: 0, endFrame: 12 },
      { w: "ten", startFrame: 12, endFrame: 24 },
      { w: "SECONDS", startFrame: 24, endFrame: 74 },
    ],
  },
};

export const RemotionRoot: React.FC = () => (
  <>
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
    <Composition
      id="AdStill"
      component={AdStill}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={120}
      defaultProps={stillProps}
      calculateMetadata={({ props }) => ({
        durationInFrames: (props as AdStillProps).durationInFrames ?? 120,
        fps: props.fps ?? 30,
      })}
    />
  </>
);
