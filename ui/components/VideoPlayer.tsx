"use client";

/**
 * 9:16 at a size that leaves room for the data.
 *
 * A vertical video at any natural size eats the viewport and pushes the score,
 * the beats and the corpus below the fold — which is the opposite of what this
 * console is for. Height is capped and the width follows from the ratio.
 */
export function VideoPlayer({
  src,
  poster,
  maxHeight = 460,
}: {
  src: string;
  poster?: string;
  maxHeight?: number;
}) {
  return (
    <div
      className="overflow-hidden rounded-lg border border-zinc-800 bg-black"
      style={{ height: maxHeight, width: (maxHeight * 9) / 16 }}
    >
      <video
        src={src}
        poster={poster}
        controls
        playsInline
        preload="metadata"
        className="h-full w-full object-contain"
      />
    </div>
  );
}
