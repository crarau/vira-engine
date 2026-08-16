/** The seam with Python. Every field here is written by `vira.render.build_props`,
 *  and every frame number in it comes from ElevenLabs character timestamps —
 *  the composition never derives timing of its own. */

export type WordProps = {
  w: string;
  /** Absolute frames, not relative to the beat. */
  startFrame: number;
  endFrame: number;
};

export type BeatProps = {
  say: string;
  show: string;
  shot: string;
  /** Director's caption call: stack|punch|slide|pop|banner. It used to select a
   *  whole-line layout; it now selects how hard a single word lands. See
   *  Captions.tsx — position is fixed, energy is what varies. */
  motion?: string;
  camera?: string;
  startFrame: number;
  endFrame: number;
  image?: string | null;
  credit?: string | null;
  words?: WordProps[];
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
  /** Burn the director's camera notes into the frame. Off by default — they
   *  are instructions for whoever shoots the real thing, not something a
   *  viewer should ever see. Turn on to produce a shooting guide. */
  showShotNotes?: boolean;
  fps: number;
  beats: BeatProps[];
};
