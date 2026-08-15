import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  interpolate,
  random,
  Sequence,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export type WordProps = { w: string; startFrame: number; endFrame: number };

export type BeatProps = {
  say: string;
  show: string;
  shot: string;
  startFrame: number;
  endFrame: number;
  image: string | null;
  credit: string | null;
  words: WordProps[];
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

const INK = "#08080C";
const ACCENT = "#F5C518";
const FG = "#FFFFFF";
const XFADE = 10; // frames of cross-dissolve between beats

/**
 * Ken Burns.
 *
 * A still photo on screen for three seconds reads as a slide. A still photo
 * drifting 8% over three seconds reads as footage. The direction alternates per
 * beat so consecutive shots don't feel like one long push.
 */
const KenBurns: React.FC<{ src: string; index: number; frames: number }> = ({
  src,
  index,
  frames,
}) => {
  const frame = useCurrentFrame();
  const p = interpolate(frame, [0, frames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const inward = index % 2 === 0;
  const scale = inward ? 1.06 + p * 0.09 : 1.15 - p * 0.09;
  // Deterministic per-beat drift — random(seed) is stable across renders, so
  // the same props always produce the same file.
  const dx = (random(`x${index}`) - 0.5) * 60 * p;
  const dy = (random(`y${index}`) - 0.5) * 44 * p;

  return (
    <AbsoluteFill style={{ overflow: "hidden" }}>
      <Img
        src={src}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `scale(${scale}) translate(${dx}px, ${dy}px)`,
          filter: "saturate(1.05) contrast(1.04)",
        }}
      />
    </AbsoluteFill>
  );
};

/** Film grain + vignette. Cheap, and it stops stock photos looking like stock photos. */
const Grade: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <>
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(ellipse at 50% 42%, rgba(0,0,0,0) 34%, rgba(0,0,0,0.72) 100%)",
        }}
      />
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(180deg, rgba(8,8,12,0.80) 0%, rgba(8,8,12,0.10) 26%, rgba(8,8,12,0.28) 52%, rgba(8,8,12,0.93) 100%)",
        }}
      />
      <AbsoluteFill
        style={{
          opacity: 0.05,
          mixBlendMode: "overlay",
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)'/%3E%3C/svg%3E\")",
          // Shift the grain each frame so it shimmers like real film.
          transform: `translate(${(frame * 7) % 140}px, ${(frame * 11) % 140}px)`,
        }}
      />
    </>
  );
};

/**
 * Karaoke captions — the TikTok convention.
 *
 * Each word highlights on the exact frame it is spoken. The timings are not
 * estimated from a words-per-minute guess; they come from ElevenLabs character
 * timestamps, mapped to frames in Python (see vira/voice.py).
 */
const Karaoke: React.FC<{ beat: BeatProps; absFrame: number }> = ({ beat, absFrame }) => {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        justifyContent: "center",
        gap: "0 18px",
        maxWidth: 880,
      }}
    >
      {beat.words.map((word, i) => {
        const isNow = absFrame >= word.startFrame && absFrame <= word.endFrame + 2;
        const isPast = absFrame > word.endFrame + 2;
        return (
          <span
            key={i}
            style={{
              fontSize: 74,
              fontWeight: 900,
              letterSpacing: -2,
              lineHeight: 1.16,
              color: isNow ? ACCENT : FG,
              opacity: isPast ? 0.55 : 1,
              transform: isNow ? "scale(1.07)" : "scale(1)",
              transformOrigin: "center bottom",
              textShadow: isNow
                ? `0 0 46px ${ACCENT}66, 0 5px 22px rgba(0,0,0,0.9)`
                : "0 5px 22px rgba(0,0,0,0.9)",
              transition: "none",
              display: "inline-block",
            }}
          >
            {word.w}
          </span>
        );
      })}
    </div>
  );
};

const Beat: React.FC<{ beat: BeatProps; index: number; absStart: number }> = ({
  beat,
  index,
  absStart,
}) => {
  // Inside a <Sequence>, useCurrentFrame() is ALREADY sequence-relative.
  // Subtracting startFrame again drives the spring negative and pins opacity at
  // 0 — which renders a video that is technically valid and completely blank.
  const local = useCurrentFrame();
  const { fps } = useVideoConfig();
  const frames = Math.max(beat.endFrame - beat.startFrame, 1);

  const enter = spring({ frame: local, fps, config: { damping: 200 }, durationInFrames: 10 });
  const fadeOut = interpolate(local, [frames - XFADE, frames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const opacity = Math.min(enter, fadeOut);
  const rise = interpolate(enter, [0, 1], [34, 0]);

  return (
    <AbsoluteFill style={{ opacity }}>
      {beat.image ? <KenBurns src={staticFile(`shots/${beat.image}`)} index={index} frames={frames} /> : null}
      <Grade />

      <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", padding: "0 70px 300px" }}>
        <div style={{ transform: `translateY(${rise}px)`, textAlign: "center", fontFamily: "Inter, -apple-system, system-ui, sans-serif" }}>
          <Karaoke beat={beat} absFrame={absStart + local} />
          <div style={{ marginTop: 30, fontSize: 25, fontWeight: 600, color: ACCENT, opacity: 0.82, letterSpacing: 0.4 }}>
            {beat.shot || beat.show}
          </div>
        </div>
      </AbsoluteFill>

      {beat.credit ? (
        <div
          style={{
            position: "absolute", bottom: 26, left: 0, right: 0, textAlign: "center",
            fontSize: 17, color: FG, opacity: 0.34,
            fontFamily: "Inter, system-ui, sans-serif",
          }}
        >
          {/* CC-BY obliges us to credit. It also looks deliberate. */}
          photo: {beat.credit}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

export const AdVideo: React.FC<AdVideoProps> = ({
  brand, hook, cta, hashtags, audioSrc, beats,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();

  const hookFrames = beats.length ? beats[0].startFrame : fps * 2;
  const outroStart = durationInFrames - Math.round(fps * 2.4);
  const outro = interpolate(frame, [outroStart, outroStart + 12], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const progress = interpolate(frame, [0, durationInFrames], [0, 100], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: INK }}>
      {audioSrc ? <Audio src={staticFile("narration.mp3")} /> : null}

      {/* Hook card holds until the first beat's audio actually begins */}
      {hookFrames > 2 ? (
        <Sequence durationInFrames={hookFrames}>
          <AbsoluteFill>
            {beats[0]?.image ? <KenBurns src={staticFile(`shots/${beats[0].image}`)} index={0} frames={hookFrames} /> : null}
            <Grade />
            <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", padding: 78 }}>
              <div style={{ fontSize: 84, fontWeight: 900, color: FG, textAlign: "center", lineHeight: 1.04, letterSpacing: -3, fontFamily: "Inter, system-ui, sans-serif", textShadow: "0 6px 30px rgba(0,0,0,0.9)" }}>
                {hook}
              </div>
            </AbsoluteFill>
          </AbsoluteFill>
        </Sequence>
      ) : null}

      {beats.map((beat, i) => (
        <Sequence key={i} from={beat.startFrame} durationInFrames={Math.max(beat.endFrame - beat.startFrame + XFADE, 1)}>
          <Beat beat={beat} index={i} absStart={beat.startFrame} />
        </Sequence>
      ))}

      {/* Brand chip, always on */}
      <AbsoluteFill style={{ padding: 56, justifyContent: "flex-start" }}>
        <div style={{ alignSelf: "flex-start", padding: "11px 23px", borderRadius: 999, background: "rgba(8,8,12,0.55)", border: `2px solid ${ACCENT}`, color: ACCENT, fontSize: 25, fontWeight: 800, fontFamily: "Inter, system-ui, sans-serif", letterSpacing: 1.4, textTransform: "uppercase", backdropFilter: "blur(8px)" }}>
          {brand}
        </div>
      </AbsoluteFill>

      {/* Progress bar — the feed convention that says "this is short" */}
      <div style={{ position: "absolute", top: 0, left: 0, height: 5, width: `${progress}%`, background: ACCENT, opacity: 0.85 }} />

      {/* CTA */}
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", opacity: outro, backgroundColor: "rgba(8,8,12,0.95)" }}>
        <div style={{ fontSize: 78, fontWeight: 900, color: ACCENT, textAlign: "center", padding: "0 74px", fontFamily: "Inter, system-ui, sans-serif", letterSpacing: -2.4, lineHeight: 1.06 }}>
          {cta}
        </div>
        <div style={{ marginTop: 34, fontSize: 25, color: FG, opacity: 0.62, fontFamily: "Inter, system-ui, sans-serif", textAlign: "center", padding: "0 60px" }}>
          {hashtags.map((h) => `#${h}`).join("  ")}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
