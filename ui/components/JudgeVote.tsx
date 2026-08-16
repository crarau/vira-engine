"use client";

import { ApiError } from "@/lib/api";

/** What a judge has said about one film, before it has necessarily been sent. */
export interface VoteDraft {
  /** null until they have actually chosen. The API has no "no opinion" rating. */
  rating: number | null;
  picked: boolean;
  comment: string;
}

export type SaveStatus =
  /** Nothing to send: no rating yet, and nothing else has been touched. */
  | "empty"
  /** They picked or typed, but `rating` is required — so nothing can be sent. */
  | "needs-rating"
  | "saving"
  | "saved"
  /** Saved, and this is not the first write for this film. */
  | "edited"
  | "error";

const SCALE = [1, 2, 3, 4, 5];

/**
 * The whole verdict for one film: a rating, a pick and a comment.
 *
 * Ordered the way a person actually answers. The rating comes first because it
 * is the only field the API will accept a vote without — `VoteRequest.rating`
 * is `ge=1, le=5` with no default, so a pick or a comment on its own has
 * nowhere to go. Rather than fail that silently, the controls stay live and
 * the status line says what is missing.
 */
export function JudgeVote({
  draft,
  status,
  error,
  onRate,
  onPick,
  onComment,
  onCommentBlur,
  onRetry,
}: {
  draft: VoteDraft;
  status: SaveStatus;
  error?: unknown;
  onRate: (rating: number) => void;
  onPick: (picked: boolean) => void;
  onComment: (comment: string) => void;
  onCommentBlur: () => void;
  onRetry: () => void;
}) {
  return (
    <div className="space-y-4">
      <fieldset>
        <legend className="mb-2 block text-[13px] font-medium text-zinc-300">
          How good is this ad?
        </legend>
        <div className="flex gap-1.5" role="radiogroup" aria-label="Rating out of five">
          {SCALE.map((n) => {
            const on = draft.rating === n;
            return (
              <button
                key={n}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => onRate(n)}
                className={`h-14 flex-1 rounded-xl border text-lg font-semibold transition-colors ${
                  on
                    ? "border-sky-500 bg-sky-600 text-white"
                    : "border-zinc-700 bg-zinc-900 text-zinc-300 active:bg-zinc-800"
                }`}
              >
                {n}
              </button>
            );
          })}
        </div>
        <div className="mt-1.5 flex justify-between text-[11px] text-zinc-500">
          <span>1 · scroll straight past</span>
          <span>5 · I&rsquo;d watch it twice</span>
        </div>
      </fieldset>

      <button
        type="button"
        aria-pressed={draft.picked}
        onClick={() => onPick(!draft.picked)}
        className={`flex w-full items-center justify-center gap-2 rounded-xl border py-3.5 text-[14px] font-medium transition-colors ${
          draft.picked
            ? "border-amber-500 bg-amber-500/15 text-amber-200"
            : "border-zinc-700 bg-zinc-900 text-zinc-400 active:bg-zinc-800"
        }`}
      >
        <span aria-hidden className={draft.picked ? "" : "opacity-40"}>
          ★
        </span>
        {draft.picked ? "This is the one" : "Make this my pick"}
      </button>

      <label className="block">
        <span className="mb-1.5 block text-[13px] font-medium text-zinc-300">
          Why? <span className="font-normal text-zinc-500">optional</span>
        </span>
        <textarea
          value={draft.comment}
          onChange={(e) => onComment(e.target.value)}
          onBlur={onCommentBlur}
          rows={3}
          maxLength={2000}
          placeholder="One line is plenty. What worked, what did not."
          className="w-full resize-y rounded-xl border border-zinc-700 bg-zinc-900 px-3 py-2.5 text-[15px] leading-snug text-zinc-100 placeholder:text-zinc-600 focus:border-sky-600 focus:outline-none"
        />
      </label>

      <SaveLine status={status} error={error} onRetry={onRetry} />
    </div>
  );
}

/**
 * The receipt.
 *
 * A panellist paid for five minutes should never have to wonder whether
 * closing the tab loses their work, and "edited" is said out loud because the
 * insert upserts on `(batch_id, video_id, reviewer_ref)` — coming back and
 * changing an answer is a supported thing, not a double submission.
 */
function SaveLine({
  status,
  error,
  onRetry,
}: {
  status: SaveStatus;
  error?: unknown;
  onRetry: () => void;
}) {
  if (status === "error") {
    const e = error as ApiError;
    const offline = e instanceof ApiError && e.status === 0;
    return (
      <div className="flex items-center justify-between gap-3 rounded-xl border border-rose-900 bg-rose-950/40 px-3 py-2.5 text-[13px] text-rose-200">
        <span>
          {offline
            ? "No connection — this answer has not been saved yet."
            : "That did not save."}
        </span>
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded-full border border-rose-700 px-3 py-1 text-[12px] font-medium text-rose-100"
        >
          Retry
        </button>
      </div>
    );
  }

  const text: Record<Exclude<SaveStatus, "error">, string> = {
    empty: "Nothing saved yet — a rating is what records your answer.",
    "needs-rating": "Give it a rating and this gets saved.",
    saving: "Saving…",
    saved: "Saved.",
    edited: "Saved — this replaced your earlier answer.",
  };
  const tone =
    status === "saved" || status === "edited"
      ? "text-emerald-400"
      : status === "needs-rating"
        ? "text-amber-400"
        : "text-zinc-500";

  return (
    <p className={`flex items-center gap-1.5 px-1 text-[12.5px] ${tone}`}>
      {(status === "saved" || status === "edited") && <span aria-hidden>✓</span>}
      {status === "saving" && (
        <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-zinc-700 border-t-sky-500" />
      )}
      {text[status]}
    </p>
  );
}
