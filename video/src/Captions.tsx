import React from "react";
import { interpolate, random, spring, useCurrentFrame, useVideoConfig } from "remotion";
import type { BeatProps, WordProps } from "./types";

/* ------------------------------------------------------------------ *
 * One word at a time, in the bottom third, always.
 *
 * The generated frame is the thing that stops the scroll. A wrapped
 * three-line karaoke block sitting over the middle of it hides exactly
 * what the viewer came for, so the caption gets a fixed strip of the
 * screen and nothing it does may leave that strip. What varies between
 * beats is how hard the word lands, not where it lands.
 * ------------------------------------------------------------------ */

const FG = "#FFFFFF";
const ACCENT = "#F5C518";
const HOT = "#FF3B30";
const FONT = "Inter, -apple-system, system-ui, sans-serif";

const W = 1080;
const H = 1920;

/** Split the frame in three; the caption owns the lowest band and the two
 *  above it belong to the image. */
export const BAND_TOP = Math.round((H * 2) / 3);
const BAND_H = H - BAND_TOP;
/** TikTok, Reels and Shorts all paint their own UI over roughly the last
 *  200px, so the word sits high in its band rather than centred in it. */
const BAND_INSET = 200;
const SIDE = 64;
/** The band's own padding, minus the scale headroom the word carries. */
const PAD = 18;
const MAX_TEXT_W = W - SIDE * 2 - PAD * 2;

/** Legibility over a photo without darkening the photo: a hard shadow plus a
 *  dark stroke painted *under* the glyph, so bright frames get an outline and
 *  dark frames get separation. */
const SHADOW = "0 10px 30px rgba(0,0,0,0.85), 0 3px 10px rgba(0,0,0,0.8)";
const OUTLINE: React.CSSProperties = {
  WebkitTextStroke: "8px rgba(4,4,8,0.62)",
  paintOrder: "stroke fill",
};

type Energy = "soft" | "drift" | "firm" | "hard";

/** The director still calls the shot — `motion` just means how hard the word
 *  lands now, instead of where the line sits. Banner's opaque slab is gone;
 *  what survives of it is the intent, which was "declare this", and that is
 *  the same landing as punch. */
const ENERGY: Record<string, Energy> = {
  stack: "soft",
  slide: "drift",
  pop: "firm",
  punch: "hard",
  banner: "hard",
};

type Spec = {
  size: number;
  track: number;
  from: number;
  lift: number;
  shift: number;
  tilt: number;
  damping: number;
  mass: number;
  stiffness: number;
  frames: number;
  color: string;
};

const SPEC: Record<Energy, Spec> = {
  // Rises and settles. For narration that explains rather than hits.
  soft: { size: 126, track: -0.03, from: 0.9, lift: 30, shift: 0, tilt: 0, damping: 17, mass: 0.7, stiffness: 110, frames: 16, color: FG },
  // Slides in from the left on an ease, no bounce. The old Slide treatment's
  // direction, minus its full-width bar and left-anchored block of text.
  drift: { size: 128, track: -0.03, from: 0.94, lift: 0, shift: 78, tilt: 0, damping: 200, mass: 1, stiffness: 130, frames: 12, color: FG },
  // Snaps in with a shred of tilt. The most conversational cadence.
  firm: { size: 140, track: -0.034, from: 0.76, lift: 12, shift: 0, tilt: 3.2, damping: 11, mass: 0.5, stiffness: 175, frames: 13, color: FG },
  // Overshoots and arrives in accent. Reserved for the lines that assert.
  hard: { size: 156, track: -0.04, from: 0.5, lift: 0, shift: 0, tilt: 0, damping: 8, mass: 0.42, stiffness: 230, frames: 12, color: ACCENT },
};

/** Single-word captions read better without the sentence plumbing attached.
 *  `!` and `?` stay — they are the delivery, not punctuation. */
const clean = (w: string) => w.replace(/^[.,;:"'`…—-]+/, "").replace(/[.,;:"'`…]+$/, "");

/** No text metrics inside Remotion's renderer, so shrink long words by glyph
 *  count. The factor is measured against Inter 900 at this tracking; being a
 *  little conservative costs a few points of size and never overflows. */
const fit = (text: string, base: number) =>
  Math.max(56, Math.min(base, Math.round(MAX_TEXT_W / Math.max(text.length * 0.54, 1))));

/** The word the beat is built around is the one the read leans on longest.
 *  That comes out of the ElevenLabs timings for free — nothing is guessed, and
 *  a beat with an even cadence correctly gets no emphasis at all. */
const emphasisIndex = (words: WordProps[]): number => {
  if (words.length < 3) return -1;
  const held = words.map((w) => Math.max(w.endFrame - w.startFrame, 1));
  const median = [...held].sort((a, b) => a - b)[Math.floor(held.length / 2)];
  let best = -1;
  let bestHeld = 0;
  words.forEach((w, i) => {
    if (held[i] > bestHeld && clean(w.w).length >= 3) {
      best = i;
      bestHeld = held[i];
    }
  });
  return bestHeld >= median * 1.7 ? best : -1;
};

/** Fixed strip of darkening under the caption band only. A full-frame grade
 *  would take the image down with it, which is the whole complaint. */
export const CaptionScrim: React.FC = () => (
  <div
    style={{
      position: "absolute",
      left: 0,
      right: 0,
      top: BAND_TOP - 120,
      bottom: 0,
      background:
        "linear-gradient(180deg, rgba(8,8,12,0) 0%, rgba(8,8,12,0.34) 38%, rgba(8,8,12,0.66) 72%, rgba(8,8,12,0.82) 100%)",
      pointerEvents: "none",
    }}
  />
);

const Word: React.FC<{
  word: WordProps;
  absFrame: number;
  fps: number;
  spec: Spec;
  emphasis: boolean;
  seed: string;
}> = ({ word, absFrame, fps, spec, emphasis, seed }) => {
  // The active word has always started, so this is never negative — which is
  // the whole reason the composition renders instead of sitting at opacity 0.
  const t = absFrame - word.startFrame;
  const enter = spring({
    frame: t,
    fps,
    config: { damping: spec.damping, mass: spec.mass, stiffness: spec.stiffness },
    durationInFrames: spec.frames,
  });

  const text = clean(word.w) || word.w;
  const size = fit(text, spec.size) * (emphasis ? 1.06 : 1);
  const color = emphasis ? (spec.color === ACCENT ? HOT : ACCENT) : spec.color;

  // Breathing after the entry settles, so a word held over a long syllable is
  // never a still frame.
  const breathe = 1 + 0.006 * Math.sin(t / 10);
  const scale = (spec.from + (1 - spec.from) * enter) * breathe;
  const tilt = spec.tilt ? (random(`${seed}t`) - 0.5) * spec.tilt * (1 - enter * 0.6) : 0;
  const lift = (1 - enter) * spec.lift;
  const slide = (1 - enter) * -spec.shift;

  return (
    <div style={{ display: "inline-flex", flexDirection: "column", alignItems: "center" }}>
      <span
        style={{
          // Transforms do not grow the layout box, so the scale headroom an
          // overshoot needs is bought with padding, not with margins.
          padding: `0 ${PAD}px`,
          fontFamily: FONT,
          fontSize: size,
          fontWeight: 900,
          letterSpacing: `${spec.track}em`,
          lineHeight: 1.06,
          whiteSpace: "nowrap",
          color,
          // Ramped off the raw frame count, not off the spring: a stiff spring
          // is still ~0 on its first frame, and since the outgoing word leaves
          // the same frame this one arrives, that reads as a one-frame hole.
          opacity: interpolate(t, [0, 2], [0.3, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          }),
          transform: `translate(${slide}px, ${lift}px) scale(${scale}) rotate(${tilt}deg)`,
          textShadow: emphasis ? `0 0 52px ${color}66, ${SHADOW}` : SHADOW,
          ...OUTLINE,
        }}
      >
        {text}
      </span>
      {emphasis ? (
        <div
          style={{
            marginTop: 14,
            height: 10,
            width: "100%",
            borderRadius: 5,
            background: color,
            opacity: 0.9,
            transform: `scaleX(${interpolate(enter, [0.2, 1], [0, 1], { extrapolateLeft: "clamp" })})`,
          }}
        />
      ) : null}
    </div>
  );
};

export const Caption: React.FC<{ beat: BeatProps; showShotNotes?: boolean }> = ({
  beat,
  showShotNotes,
}) => {
  // Inside a <Sequence>, useCurrentFrame() is ALREADY sequence-relative.
  // Subtracting startFrame again drives springs negative and pins opacity at 0.
  const local = useCurrentFrame();
  const { fps } = useVideoConfig();

  const words = beat.words ?? [];
  const absFrame = beat.startFrame + local;

  // A word stays up until the next one is spoken, so there is no flicker in the
  // pauses inside a line — only the silence between beats clears the frame.
  let idx = -1;
  for (let i = 0; i < words.length; i += 1) {
    if (words[i].startFrame <= absFrame) idx = i;
  }
  if (idx < 0) return null;

  const spec = SPEC[ENERGY[(beat.motion ?? "").toLowerCase()] ?? "firm"];
  const span = Math.max(beat.endFrame - beat.startFrame, 1);
  const out = interpolate(local, [span - 4, span], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: BAND_TOP,
        height: BAND_H,
        // The guarantee, not a suggestion: nothing the caption does can reach
        // the two thirds above it.
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: `0 ${SIDE}px ${BAND_INSET}px`,
        opacity: out,
        textAlign: "center",
      }}
    >
      <Word
        key={idx}
        word={words[idx]}
        absFrame={absFrame}
        fps={fps}
        spec={spec}
        emphasis={idx === emphasisIndex(words)}
        seed={`${beat.startFrame}-${idx}`}
      />
      {showShotNotes ? (
        <div
          style={{
            marginTop: 26,
            fontFamily: FONT,
            fontSize: 21,
            fontWeight: 600,
            color: ACCENT,
            opacity: 0.7,
            letterSpacing: 0.4,
          }}
        >
          {beat.shot || beat.show}
        </div>
      ) : null}
    </div>
  );
};
