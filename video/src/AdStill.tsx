import React from "react";
import { AbsoluteFill, Img, staticFile } from "remotion";
import { Caption, CaptionScrim } from "./Captions";
import type { BeatProps } from "./types";

/* ------------------------------------------------------------------ *
 * The static ad — one frame of the film, rendered by `remotion still`.
 *
 * A separate composition rather than a flag on AdVideo, because the two
 * differ in what they are, not in how they are configured: AdVideo is
 * driven by narration timings and has no meaning without audio, and a
 * still has no Sequence, no Audio and no Ken Burns to be turned off.
 *
 * What it deliberately does NOT do is invent a poster treatment. The
 * scrim and the word are the imported components, so a static ad and a
 * paused video are the same picture — which is the point: a brand
 * running both should not look like it hired two agencies.
 *
 * Python decides which frame this renders at (`--frame`), because Python
 * owns the word timings, exactly as it does for the video. See
 * vira/still.py.
 * ------------------------------------------------------------------ */

const INK = "#08080C";
const ACCENT = "#F5C518";
const FG = "#FFFFFF";
const FONT = "Inter, -apple-system, system-ui, sans-serif";

export type AdStillProps = {
  brand: string;
  /** The full hook. Not drawn — the band shows the stressed word, the same
   *  as the video does. Carried so the props file explains the picture. */
  headline: string;
  cta: string;
  beat: BeatProps;
  image: string | null;
  fps: number;
  durationInFrames: number;
};

/** The grade from AdVideo, which does not export it. Same values on purpose:
 *  a still that sits over a differently-graded photograph reads as a different
 *  product even when the type matches. */
const Grade: React.FC = () => (
  <>
    <AbsoluteFill style={{ background: "radial-gradient(ellipse at 50% 42%, rgba(0,0,0,0) 42%, rgba(0,0,0,0.42) 100%)" }} />
    <AbsoluteFill style={{ background: "linear-gradient(180deg, rgba(8,8,12,0.6) 0%, rgba(8,8,12,0.05) 20%, rgba(8,8,12,0) 55%, rgba(8,8,12,0.18) 100%)" }} />
  </>
);

export const AdStill: React.FC<AdStillProps> = ({ brand, cta, beat, image }) => (
  <AbsoluteFill style={{ backgroundColor: INK }}>
    {image ? (
      <AbsoluteFill style={{ overflow: "hidden" }}>
        <Img
          src={staticFile(`shots/${image}`)}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            // The video's first beat sits at scale 1.01; matching it keeps the
            // crop identical rather than nearly identical.
            transform: "scale(1.01)",
            filter: "saturate(1.0) contrast(1.08)",
          }}
        />
      </AbsoluteFill>
    ) : null}

    <Grade />
    <CaptionScrim />

    {/* Absolutely positioned against the frame, which is what Caption expects:
        in the video it lives inside a Sequence's AbsoluteFill. */}
    <AbsoluteFill>
      <Caption beat={beat} />
    </AbsoluteFill>

    <AbsoluteFill style={{ padding: 54, justifyContent: "flex-start", pointerEvents: "none" }}>
      <div
        style={{
          alignSelf: "flex-start",
          padding: "10px 22px",
          borderRadius: 999,
          background: "rgba(8,8,12,0.5)",
          border: `2px solid ${ACCENT}`,
          color: ACCENT,
          fontSize: 24,
          fontWeight: 800,
          fontFamily: FONT,
          letterSpacing: 1.4,
          textTransform: "uppercase",
        }}
      >
        {brand}
      </div>
    </AbsoluteFill>

    {/* The band reserves its lowest 200px for the platform's own UI, so the
        call to action sits in that reserved strip and never collides with the
        word above it. */}
    {cta ? (
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 78,
          textAlign: "center",
          fontFamily: FONT,
          fontSize: 40,
          fontWeight: 800,
          letterSpacing: -0.6,
          color: FG,
          textShadow: "0 8px 24px rgba(0,0,0,0.9)",
          padding: "0 72px",
        }}
      >
        {cta}
      </div>
    ) : null}
  </AbsoluteFill>
);
