"use client";

import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

/**
 * One vertical ad, sized for the phone it is being watched on.
 *
 * `VideoPlayer` in this same folder caps height at 460px so a 9:16 film leaves
 * room for the score, the beats and the corpus next to it. This page has none
 * of that — the film *is* the page — so the box is sized off the viewport
 * instead, and off the *small* viewport (`svh`) so it does not resize under
 * the reader's thumb every time mobile Safari hides its address bar.
 *
 * **Nothing autoplays.** These ads have a voiceover, and a stranger's phone
 * making noise in a room is the fastest way to lose a panellist. Muted
 * autoplay would technically be allowed and would be worse: they would rate
 * the pictures. So playback starts on a tap, every time.
 */
export function JudgeClip({
  src,
  hook,
  onFirstPlay,
  onEnded,
}: {
  src: string;
  hook: string;
  onFirstPlay?: () => void;
  onEnded?: () => void;
}) {
  const ref = useRef<HTMLVideoElement | null>(null);
  const [started, setStarted] = useState(false);
  const [failed, setFailed] = useState(false);
  /** Bumped to force a fresh <video> element on retry; src alone would cache. */
  const [attempt, setAttempt] = useState(0);

  // A src change means a different film: reset, do not inherit the last one's
  // failure or its "already playing" state.
  useEffect(() => {
    setStarted(false);
    setFailed(false);
  }, [src]);

  const box: CSSProperties = {
    // Whichever is smaller: most of the short viewport, or whatever 9:16
    // allows once the width of the phone has had its say.
    height: "min(58svh, calc((100vw - 2.5rem) * 16 / 9))",
    width: "calc(min(58svh, calc((100vw - 2.5rem) * 16 / 9)) * 9 / 16)",
  };

  if (failed) {
    return (
      <div
        style={box}
        className="mx-auto flex flex-col items-center justify-center gap-3 rounded-2xl border border-amber-900/60 bg-amber-950/20 px-5 text-center"
      >
        <div className="text-sm font-medium text-amber-200">
          This clip would not play.
        </div>
        <p className="text-[13px] leading-snug text-amber-200/70">
          It is our problem, not yours. Try again, or skip to the next one —
          your other answers are already saved.
        </p>
        <div className="flex flex-wrap items-center justify-center gap-2">
          <button
            onClick={() => {
              setFailed(false);
              setAttempt((n) => n + 1);
            }}
            className="rounded-full border border-amber-700 px-4 py-2 text-[13px] font-medium text-amber-100 active:bg-amber-900/40"
          >
            Try again
          </button>
          <a
            href={src}
            target="_blank"
            rel="noreferrer"
            className="rounded-full px-3 py-2 text-[13px] text-amber-300/80 underline underline-offset-2"
          >
            Open the file
          </a>
        </div>
      </div>
    );
  }

  return (
    <div
      style={box}
      className="relative mx-auto overflow-hidden rounded-2xl border border-zinc-800 bg-black shadow-lg shadow-black/50"
    >
      <video
        key={`${src}#${attempt}`}
        ref={ref}
        src={src}
        controls
        playsInline
        preload="metadata"
        className="h-full w-full object-contain"
        onPlay={() => {
          if (!started) onFirstPlay?.();
          setStarted(true);
        }}
        onEnded={() => onEnded?.()}
        onError={() => setFailed(true)}
      />

      {!started && (
        // Tap target over the whole frame, because a 44px native play button in
        // the middle of a 9:16 video is not a phone-sized thing to hit.
        <button
          aria-label="Play this ad"
          onClick={() => {
            const el = ref.current;
            if (!el) return;
            // A rejected play() is a browser policy decision, not a broken
            // file — leave the native controls to it rather than showing the
            // "would not play" panel.
            void el.play().catch(() => undefined);
          }}
          className="absolute inset-0 flex flex-col items-center justify-end gap-4 bg-gradient-to-t from-black/85 via-black/25 to-black/50 pb-16 text-center"
        >
          <span className="flex h-16 w-16 items-center justify-center rounded-full bg-white/95 shadow-xl">
            <span className="ml-1 block h-0 w-0 border-y-[13px] border-l-[21px] border-y-transparent border-l-zinc-950" />
          </span>
          {hook ? (
            <span className="line-clamp-3 px-5 text-[15px] font-medium leading-snug text-white/90">
              &ldquo;{hook}&rdquo;
            </span>
          ) : null}
          <span className="text-[11px] uppercase tracking-widest text-white/50">
            tap to play · sound on
          </span>
        </button>
      )}
    </div>
  );
}
