import React from "react";
import {
  AbsoluteFill,
  Audio,
  interpolate,
  Sequence,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export type BeatProps = {
  say: string;
  show: string;
  shot: string;
  startFrame: number;
  endFrame: number;
};

export type AdVideoProps = {
  brand: string;
  product: string;
  hook: string;
  cta: string;
  caption: string;
  hashtags: string[];
  audioSrc: string | null;
  fps: number;
  beats: BeatProps[];
};

const BG = "#0B0B0F";
const ACCENT = "#F5C518";
const FG = "#FAFAF7";

/**
 * Caption card for one beat.
 *
 * Beat boundaries come from ElevenLabs character timestamps, not from hand-set
 * frame numbers — see vira/voice.py. That is what lets the copy change (or be
 * translated) without anyone re-timing the video.
 */
const Beat: React.FC<{ beat: BeatProps; index: number }> = ({ beat, index }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = frame - beat.startFrame;

  const enter = spring({ frame: local, fps, config: { damping: 200 }, durationInFrames: 12 });
  const y = interpolate(enter, [0, 1], [40, 0]);

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        padding: "0 80px 320px",
      }}
    >
      <div
        style={{
          transform: `translateY(${y}px)`,
          opacity: enter,
          textAlign: "center",
          fontFamily: "Inter, -apple-system, system-ui, sans-serif",
        }}
      >
        <div
          style={{
            fontSize: 68,
            lineHeight: 1.15,
            fontWeight: 800,
            color: FG,
            textShadow: "0 4px 32px rgba(0,0,0,0.65)",
            letterSpacing: -1.5,
          }}
        >
          {beat.say}
        </div>
        <div
          style={{
            marginTop: 28,
            fontSize: 26,
            fontWeight: 500,
            color: ACCENT,
            opacity: 0.85,
            letterSpacing: 0.5,
          }}
        >
          {/* The shot direction is on-screen on purpose: this render is a
              shooting guide for the brand, not the finished ad. */}
          {beat.shot || beat.show}
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          bottom: 120,
          fontSize: 22,
          color: FG,
          opacity: 0.4,
          fontFamily: "Inter, system-ui, sans-serif",
        }}
      >
        beat {index + 1}
      </div>
    </AbsoluteFill>
  );
};

export const AdVideo: React.FC<AdVideoProps> = ({
  brand,
  hook,
  cta,
  hashtags,
  audioSrc,
  beats,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();

  const outro = durationInFrames - fps * 2;
  const outroIn = interpolate(frame, [outro, outro + 12], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: BG }}>
      {audioSrc ? <Audio src={audioSrc.startsWith("http") ? audioSrc : staticFile("narration.mp3")} /> : null}

      {/* Brand chip, always visible */}
      <AbsoluteFill style={{ padding: 64, justifyContent: "flex-start" }}>
        <div
          style={{
            alignSelf: "flex-start",
            padding: "12px 24px",
            borderRadius: 999,
            border: `2px solid ${ACCENT}`,
            color: ACCENT,
            fontSize: 26,
            fontWeight: 700,
            fontFamily: "Inter, system-ui, sans-serif",
            letterSpacing: 1,
            textTransform: "uppercase",
          }}
        >
          {brand}
        </div>
      </AbsoluteFill>

      {/* Hook holds the first 2 seconds on its own */}
      <Sequence durationInFrames={fps * 2}>
        <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", padding: 80 }}>
          <div
            style={{
              fontSize: 88,
              fontWeight: 900,
              color: FG,
              textAlign: "center",
              lineHeight: 1.05,
              letterSpacing: -3,
              fontFamily: "Inter, system-ui, sans-serif",
            }}
          >
            {hook}
          </div>
        </AbsoluteFill>
      </Sequence>

      {beats.map((beat, i) => (
        <Sequence
          key={i}
          from={beat.startFrame}
          durationInFrames={Math.max(beat.endFrame - beat.startFrame, 1)}
        >
          <Beat beat={beat} index={i} />
        </Sequence>
      ))}

      {/* CTA */}
      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          opacity: outroIn,
          backgroundColor: "rgba(11,11,15,0.92)",
        }}
      >
        <div
          style={{
            fontSize: 72,
            fontWeight: 900,
            color: ACCENT,
            textAlign: "center",
            padding: 80,
            fontFamily: "Inter, system-ui, sans-serif",
            letterSpacing: -2,
          }}
        >
          {cta}
        </div>
        <div style={{ fontSize: 26, color: FG, opacity: 0.6, fontFamily: "Inter, system-ui" }}>
          {hashtags.map((h) => `#${h}`).join("  ")}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
