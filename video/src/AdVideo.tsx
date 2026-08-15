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
  audioFile?: string;
  fps: number;
  beats: BeatProps[];
};

const INK = "#08080C";
const ACCENT = "#F5C518";
const HOT = "#FF3B30";
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
        const span = Math.max(beat.endFrame - beat.startFrame, 1);
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

const Grade: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <>
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at 50% 40%, rgba(0,0,0,0) 30%, rgba(0,0,0,0.7) 100%)" }} />
      <AbsoluteFill style={{ background: "linear-gradient(180deg, rgba(8,8,12,0.78) 0%, rgba(8,8,12,0.06) 24%, rgba(8,8,12,0.22) 50%, rgba(8,8,12,0.94) 100%)" }} />
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

/* ------------------------------------------------------------------ *
 * Five caption treatments. The beat index picks one, so consecutive
 * beats never animate the same way. This is the difference between a
 * subtitle track and something that feels directed.
 * ------------------------------------------------------------------ */

type CapProps = { beat: BeatProps; absFrame: number; fps: number; local: number };

const useSpoken = (word: WordProps, absFrame: number) => {
  const live = absFrame >= word.startFrame && absFrame <= word.endFrame + 2;
  const past = absFrame > word.endFrame + 2;
  return { live, past };
};

/** 0 — STACK: words rise in, staggered, bottom third. */
const Stack: React.FC<CapProps> = ({ beat, absFrame, fps, local }) => (
  <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: "0 16px", maxWidth: 900 }}>
    {beat.words.map((word, i) => {
      const { live, past } = useSpoken(word, absFrame);
      const enter = spring({ frame: local - i * 2, fps, config: { damping: 14, mass: 0.5 }, durationInFrames: 14 });
      return (
        <span key={i} style={{
          fontSize: 76, fontWeight: 900, letterSpacing: -2.5, lineHeight: 1.1,
          color: live ? ACCENT : FG, opacity: past ? 0.5 : enter,
          transform: `translateY(${(1 - enter) * 46}px) scale(${live ? 1.08 : 1})`,
          textShadow: live ? `0 0 44px ${ACCENT}70, 0 4px 20px #000` : "0 4px 20px #000",
          display: "inline-block",
        }}>{word.w}</span>
      );
    })}
  </div>
);

/** 1 — PUNCH: one huge line, overshoot scale-in, centre of frame. */
const Punch: React.FC<CapProps> = ({ beat, absFrame, fps, local }) => {
  const enter = spring({ frame: local, fps, config: { damping: 9, mass: 0.6, stiffness: 140 }, durationInFrames: 18 });
  return (
    <div style={{ transform: `scale(${0.62 + enter * 0.38})`, textAlign: "center", maxWidth: 940 }}>
      {beat.words.map((word, i) => {
        const { live, past } = useSpoken(word, absFrame);
        return (
          <span key={i} style={{
            fontSize: 96, fontWeight: 900, letterSpacing: -4, lineHeight: 1.0,
            color: live ? HOT : FG, opacity: past ? 0.62 : 1,
            textShadow: "0 6px 26px #000", marginRight: 18, display: "inline-block",
          }}>{word.w}</span>
        );
      })}
    </div>
  );
};

/** 2 — SLIDE: left-anchored, accent bar wipes in behind. */
const Slide: React.FC<CapProps> = ({ beat, absFrame, fps, local }) => {
  const enter = spring({ frame: local, fps, config: { damping: 200 }, durationInFrames: 12 });
  return (
    <div style={{ width: "100%", textAlign: "left", paddingLeft: 20 }}>
      <div style={{ height: 8, background: ACCENT, width: `${enter * 62}%`, marginBottom: 22, borderRadius: 4 }} />
      <div style={{ transform: `translateX(${(1 - enter) * -70}px)`, opacity: enter }}>
        {beat.words.map((word, i) => {
          const { live, past } = useSpoken(word, absFrame);
          return (
            <span key={i} style={{
              fontSize: 70, fontWeight: 900, letterSpacing: -2, lineHeight: 1.14,
              color: live ? ACCENT : FG, opacity: past ? 0.5 : 1,
              textShadow: "0 4px 20px #000", marginRight: 15, display: "inline-block",
              transform: live ? "translateY(-6px)" : "none",
            }}>{word.w}</span>
          );
        })}
      </div>
    </div>
  );
};

/** 3 — POP: each word snaps in with its own tilt, scattered baseline.
 *
 * `gap` is generous and the scale range is tight on purpose. A span scaled to
 * 1.12 visually overflows its layout box by ~9px per side, which eats a normal
 * gap and runs the words together — the layout box does not grow with a
 * transform. Horizontal separation therefore comes from padding (which is
 * layout) rather than from gap alone.
 */
const Pop: React.FC<CapProps> = ({ beat, absFrame, fps }) => (
  <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", alignItems: "center", gap: "14px 10px", maxWidth: 900 }}>
    {beat.words.map((word, i) => {
      const { live, past } = useSpoken(word, absFrame);
      const pop = spring({ frame: absFrame - word.startFrame, fps, config: { damping: 8, mass: 0.4, stiffness: 200 }, durationInFrames: 12 });
      const tilt = (random(`t${word.w}${i}`) - 0.5) * 7;
      return (
        <span key={i} style={{
          fontSize: 74, fontWeight: 900, letterSpacing: -2,
          padding: "0 11px",
          color: live ? ACCENT : FG, opacity: past ? 0.55 : Math.max(pop, 0.22),
          transform: `scale(${0.82 + pop * 0.22}) rotate(${tilt}deg) translateY(${(random(`y${i}`) - 0.5) * 9}px)`,
          textShadow: live ? `0 0 40px ${ACCENT}80, 0 4px 18px #000` : "0 4px 18px #000",
          display: "inline-block",
        }}>{word.w}</span>
      );
    })}
  </div>
);

/** 4 — BANNER: solid accent slab, dark text, wipes open. */
const Banner: React.FC<CapProps> = ({ beat, absFrame, fps, local }) => {
  const enter = spring({ frame: local, fps, config: { damping: 200 }, durationInFrames: 11 });
  return (
    <div style={{
      background: ACCENT, padding: "26px 34px", borderRadius: 6, maxWidth: 920,
      transform: `scaleX(${0.3 + enter * 0.7})`, transformOrigin: "left center", opacity: enter,
    }}>
      <div style={{ opacity: interpolate(enter, [0.55, 1], [0, 1], { extrapolateLeft: "clamp" }) }}>
        {beat.words.map((word, i) => {
          const { live } = useSpoken(word, absFrame);
          return (
            <span key={i} style={{
              fontSize: 64, fontWeight: 900, letterSpacing: -2, lineHeight: 1.16,
              color: live ? "#FFFFFF" : INK, marginRight: 14, display: "inline-block",
              transform: live ? "scale(1.06)" : "none",
            }}>{word.w}</span>
          );
        })}
      </div>
    </div>
  );
};

const TREATMENTS = [Stack, Punch, Slide, Pop, Banner];
// Vertical anchor per treatment, so captions don't all sit in the same band.
const ANCHOR: Array<React.CSSProperties> = [
  { justifyContent: "flex-end", padding: "0 70px 300px" },
  { justifyContent: "center", padding: "0 60px" },
  { justifyContent: "flex-end", padding: "0 70px 340px" },
  { justifyContent: "center", padding: "0 60px 120px" },
  { justifyContent: "flex-end", padding: "0 70px 280px" },
];

const Caption: React.FC<{ beat: BeatProps; index: number }> = ({ beat, index }) => {
  // Inside a <Sequence>, useCurrentFrame() is ALREADY sequence-relative.
  // Subtracting startFrame again drives springs negative and pins opacity at 0.
  const local = useCurrentFrame();
  const { fps } = useVideoConfig();
  const span = Math.max(beat.endFrame - beat.startFrame, 1);
  const Treatment = TREATMENTS[index % TREATMENTS.length];

  const out = interpolate(local, [span - 6, span], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ alignItems: "center", ...ANCHOR[index % ANCHOR.length], opacity: out }}>
      <div style={{ fontFamily: FONT, textAlign: "center" }}>
        <Treatment beat={beat} absFrame={beat.startFrame + local} fps={fps} local={local} />
        <div style={{ marginTop: 62, fontSize: 21, fontWeight: 600, color: ACCENT, opacity: 0.7, letterSpacing: 0.4 }}>
          {beat.shot || beat.show}
        </div>
      </div>
    </AbsoluteFill>
  );
};

export const AdVideo: React.FC<AdVideoProps> = ({
  brand, hook, cta, hashtags, audioSrc, audioFile, beats,
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
          <Caption beat={beat} index={i} />
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
