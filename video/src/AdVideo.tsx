import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  interpolate,
  random,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Caption, CaptionScrim } from "./Captions";
import type { AdVideoProps, BeatProps } from "./types";

export type { AdVideoProps, BeatProps, WordProps } from "./types";

const INK = "#08080C";
const ACCENT = "#F5C518";
const FG = "#FFFFFF";
const FONT = "Inter, -apple-system, system-ui, sans-serif";
const XFADE = 8;

/* ------------------------------------------------------------------ *
 * Backdrop — continuous image track.
 *
 * Each image fades IN over the one before and never fades out, so the
 * stack is always covered. Fading both in and out is what produced the
 * black flash between beats: two half-faded layers over the base colour.
 * ------------------------------------------------------------------ */
const Backdrop: React.FC<{ beats: BeatProps[] }> = ({ beats }) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill>
      {beats.map((beat, i) => {
        if (!beat.image) return null;
        const start = Math.max(beat.startFrame - XFADE, 0);
        const opacity = interpolate(frame, [start, start + XFADE], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        if (frame < start) return null;

        const p = interpolate(frame, [beat.startFrame, beat.endFrame], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });

        // Four camera behaviours, rotating. A single Ken Burns direction on
        // every shot reads as a screensaver.
        const mode = i % 4;
        let scale = 1.08;
        let tx = 0;
        let ty = 0;
        // Restrained on purpose: these frames are generated natively at 9:16
        // and composed for it, so scale is crop. Big Ken Burns throws the
        // subject out of frame — which is a worse sin than looking static.
        if (mode === 0) scale = 1.01 + p * 0.06;                    // slow push in
        if (mode === 1) { scale = 1.08 - p * 0.06; tx = -p * 16; }  // pull back, drift left
        if (mode === 2) { scale = 1.06; ty = -p * 26; }             // vertical pan
        if (mode === 3) {                                            // small punch, then settle
          scale = interpolate(p, [0, 0.18, 1], [1.12, 1.03, 1.06]);
          tx = (random(`d${i}`) - 0.5) * 14 * p;
        }

        return (
          <AbsoluteFill key={i} style={{ opacity, overflow: "hidden" }}>
            <Img
              src={staticFile(`shots/${beat.image}`)}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                transform: `scale(${scale}) translate(${tx}px, ${ty}px)`,
                filter: `saturate(${1.0 + (i % 3) * 0.12}) contrast(1.08)`,
              }}
            />
          </AbsoluteFill>
        );
      })}
    </AbsoluteFill>
  );
};

/** Shaping, not masking. The image is the reason anyone stops scrolling, so
 *  this only buys contrast for the brand chip at the top; the caption band
 *  gets its own scrim further down and the middle is left alone. */
const Grade: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <>
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at 50% 42%, rgba(0,0,0,0) 42%, rgba(0,0,0,0.42) 100%)" }} />
      <AbsoluteFill style={{ background: "linear-gradient(180deg, rgba(8,8,12,0.6) 0%, rgba(8,8,12,0.05) 20%, rgba(8,8,12,0) 55%, rgba(8,8,12,0.18) 100%)" }} />
      <AbsoluteFill
        style={{
          opacity: 0.055,
          mixBlendMode: "overlay",
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)'/%3E%3C/svg%3E\")",
          transform: `translate(${(frame * 7) % 140}px, ${(frame * 11) % 140}px)`,
        }}
      />
    </>
  );
};

export const AdVideo: React.FC<AdVideoProps> = ({
  brand, hook, cta, hashtags, audioSrc, audioFile, beats, showShotNotes = false,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();

  const hookFrames = beats.length ? beats[0].startFrame : fps * 2;
  const outroStart = durationInFrames - Math.round(fps * 2.4);
  const outro = interpolate(frame, [outroStart, outroStart + 10], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const progress = interpolate(frame, [0, durationInFrames], [0, 100], { extrapolateRight: "clamp" });

  // Handheld float — a few pixels of continuous drift so nothing sits perfectly
  // still. Static framing is most of what makes generated video feel dead.
  const bobX = Math.sin(frame / 26) * 5;
  const bobY = Math.cos(frame / 34) * 4;

  // CTA lands on a two-frame shake.
  const shake = frame >= outroStart && frame < outroStart + 6
    ? (random(`s${frame}`) - 0.5) * 16 : 0;

  return (
    <AbsoluteFill style={{ backgroundColor: INK }}>
      {audioSrc ? <Audio src={staticFile(audioFile ?? "narration.mp3")} /> : null}

      <AbsoluteFill style={{ transform: `translate(${bobX + shake}px, ${bobY}px) scale(1.03)` }}>
        <Backdrop beats={beats} />
        <Grade />
      </AbsoluteFill>

      {/* Constant, so the band never flashes in the silence between beats. */}
      <CaptionScrim />

      {hookFrames > 2 ? (
        <Sequence durationInFrames={hookFrames}>
          <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", padding: 74 }}>
            <div style={{
              fontSize: 88, fontWeight: 900, color: FG, textAlign: "center", lineHeight: 1.03,
              letterSpacing: -3.4, fontFamily: FONT, textShadow: "0 6px 30px #000",
              transform: `scale(${interpolate(frame, [0, 9], [0.86, 1], { extrapolateRight: "clamp" })})`,
            }}>{hook}</div>
          </AbsoluteFill>
        </Sequence>
      ) : null}

      {beats.map((beat, i) => (
        <Sequence key={i} from={beat.startFrame} durationInFrames={Math.max(beat.endFrame - beat.startFrame, 1)}>
          <Caption beat={beat} showShotNotes={showShotNotes} />
        </Sequence>
      ))}

      <AbsoluteFill style={{ padding: 54, justifyContent: "flex-start", pointerEvents: "none" }}>
        <div style={{
          alignSelf: "flex-start", padding: "10px 22px", borderRadius: 999,
          background: "rgba(8,8,12,0.5)", border: `2px solid ${ACCENT}`, color: ACCENT,
          fontSize: 24, fontWeight: 800, fontFamily: FONT, letterSpacing: 1.4, textTransform: "uppercase",
        }}>{brand}</div>
      </AbsoluteFill>

      <div style={{ position: "absolute", top: 0, left: 0, height: 5, width: `${progress}%`, background: ACCENT, opacity: 0.85 }} />

      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", opacity: outro, backgroundColor: "rgba(8,8,12,0.95)" }}>
        <div style={{
          fontSize: 80, fontWeight: 900, color: ACCENT, textAlign: "center", padding: "0 72px",
          fontFamily: FONT, letterSpacing: -2.6, lineHeight: 1.04,
          transform: `scale(${interpolate(outro, [0, 1], [0.8, 1])})`,
        }}>{cta}</div>
        <div style={{ marginTop: 32, fontSize: 24, color: FG, opacity: 0.6, fontFamily: FONT, textAlign: "center", padding: "0 58px" }}>
          {hashtags.map((h) => `#${h}`).join("  ")}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
