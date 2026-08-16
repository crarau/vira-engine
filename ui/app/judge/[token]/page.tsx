"use client";

/**
 * The judge page. The only surface in this repo that is not an operator tool.
 *
 * Someone Terac recruited and is paying for a few minutes opens this on a
 * phone, watches some vertical ads, and says which one they would actually
 * stop for. Everything here follows from those two facts:
 *
 *  - **No engine grade reaches this page.** `GET /v1/review-batches/{token}`
 *    omits score, disposition and lane on purpose, `getJudgeBatch` whitelists
 *    the fields it reads on top of that, and nothing here fetches a video by
 *    id to fill in the gap. A judge shown "4.2" ranks the engine's opinion
 *    back at us and the measurement is worthless.
 *  - **Every answer posts the moment it is made.** A panellist who closes the
 *    tab on video four has still given us three real votes. The insert upserts
 *    on `(batch_id, video_id, reviewer_ref)`, so re-answering is an edit — the
 *    page says so rather than treating it as a double submission.
 */

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { ApiError, getJudgeBatch, JudgeBatch, submitVote } from "@/lib/api";
import { JudgeClip } from "@/components/JudgeClip";
import { JudgeVote, SaveStatus, VoteDraft } from "@/components/JudgeVote";
import {
  loadVotes,
  rememberVote,
  resolveReviewer,
  Reviewer,
  SavedVote,
  SavedVotes,
} from "../reviewer";

const EMPTY_DRAFT: VoteDraft = { rating: null, picked: false, comment: "" };
/** Long enough not to post every keystroke, short enough to beat a tab close. */
const COMMENT_DEBOUNCE_MS = 900;

/** True when the server already holds exactly what the judge is looking at. */
function unchanged(draft: VoteDraft, row: SavedVote | undefined): boolean {
  return Boolean(
    row &&
      row.rating === draft.rating &&
      row.picked === draft.picked &&
      row.comment === draft.comment,
  );
}

export default function JudgePage() {
  return (
    <Suspense fallback={<Splash>Loading…</Splash>}>
      <Judge />
    </Suspense>
  );
}

function Judge() {
  const params = useParams<{ token: string }>();
  const token = Array.isArray(params.token) ? params.token[0] : params.token || "";
  // The string, not the object: a value dependency cannot re-fire this effect
  // on a re-render that happened to hand back a new ReadonlyURLSearchParams.
  const query = useSearchParams().toString();

  const [reviewer, setReviewer] = useState<Reviewer | null>(null);
  const [batch, setBatch] = useState<JudgeBatch | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);

  const [index, setIndex] = useState(0);
  const [done, setDone] = useState(false);

  const [drafts, setDrafts] = useState<Record<string, VoteDraft>>({});
  const [status, setStatus] = useState<Record<string, SaveStatus>>({});
  const [errors, setErrors] = useState<Record<string, unknown>>({});
  const [saved, setSaved] = useState<SavedVotes>({});

  // The savers and the tab-close handler need current values without being
  // rebuilt on every keystroke.
  const draftsRef = useRef(drafts);
  const savedRef = useRef(saved);
  const reviewerRef = useRef<Reviewer | null>(null);
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  useEffect(() => {
    draftsRef.current = drafts;
  }, [drafts]);
  useEffect(() => {
    savedRef.current = saved;
  }, [saved]);
  useEffect(() => {
    reviewerRef.current = reviewer;
  }, [reviewer]);

  // Resolved in an effect, not during render: for a direct visitor it mints and
  // persists a value, which is a side effect and must not happen mid-render.
  // Resolved once — who is voting cannot change halfway through a session, and
  // re-resolving would orphan the votes already filed under the first ref.
  useEffect(() => {
    setReviewer((prev) => prev ?? resolveReviewer(new URLSearchParams(query)));
  }, [query]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getJudgeBatch(token)
      .then((b) => !cancelled && setBatch(b))
      .catch((e) => !cancelled && setLoadError(e));
    return () => {
      cancelled = true;
    };
  }, [token]);

  /** Re-hydrate what this browser already posted, so "saved" survives a reload. */
  useEffect(() => {
    if (!reviewer || !batch) return;
    const mine = loadVotes(token, reviewer.ref);
    setSaved(mine);
    setDrafts(
      Object.fromEntries(
        batch.videos.map((v) => {
          const row = mine[v.video_id];
          return [
            v.video_id,
            row
              ? { rating: row.rating, picked: row.picked, comment: row.comment }
              : { ...EMPTY_DRAFT },
          ];
        }),
      ),
    );
    setStatus(
      Object.fromEntries(
        batch.videos.map((v) => {
          const row = mine[v.video_id];
          return [v.video_id, row ? (row.writes > 1 ? "edited" : "saved") : "empty"];
        }),
      ),
    );
    // Put them back where they stopped rather than making them tap through
    // films they have already rated.
    const firstUnrated = batch.videos.findIndex((v) => !mine[v.video_id]);
    if (firstUnrated > 0) setIndex(firstUnrated);
  }, [reviewer, batch, token]);

  /**
   * Send one film's verdict.
   *
   * `patch` is applied on top of the stored draft rather than being read back
   * out of state, so a tap that both changes a value and triggers the save
   * cannot post the value it had a render ago.
   *
   * `rating` is required by the API (`ge=1, le=5`, no default), so a pick or a
   * comment on its own genuinely cannot be stored. That is surfaced as
   * "needs-rating" rather than swallowed — the pick and the comment stay in the
   * draft and go up with the rating the moment one is chosen.
   */
  const save = useCallback(
    async (videoId: string, patch?: Partial<VoteDraft>) => {
      const who = reviewerRef.current;
      if (!who) return;
      clearTimeout(timers.current[videoId]);

      const draft: VoteDraft = {
        ...EMPTY_DRAFT,
        ...draftsRef.current[videoId],
        ...patch,
      };

      if (draft.rating === null) {
        const touched = draft.picked || draft.comment.trim().length > 0;
        setStatus((s) => ({ ...s, [videoId]: touched ? "needs-rating" : "empty" }));
        return;
      }
      const row = savedRef.current[videoId];
      if (row && unchanged(draft, row)) {
        setStatus((s) => ({ ...s, [videoId]: row.writes > 1 ? "edited" : "saved" }));
        return;
      }

      setStatus((s) => ({ ...s, [videoId]: "saving" }));
      const body = {
        reviewer_ref: who.ref,
        video_id: videoId,
        rating: draft.rating,
        picked: draft.picked,
        comment: draft.comment,
      };
      try {
        await submitVote(token, body);
        const stored = rememberVote(token, who.ref, body);
        setSaved((s) => ({ ...s, [videoId]: stored }));
        setErrors((e) => ({ ...e, [videoId]: null }));
        // The first write from *this browser* reads as "saved". A judge who
        // cleared their storage and is really editing a server-side row also
        // sees "saved", which is the harmless direction to be wrong in.
        setStatus((s) => ({ ...s, [videoId]: stored.writes > 1 ? "edited" : "saved" }));
      } catch (e) {
        setErrors((err) => ({ ...err, [videoId]: e }));
        setStatus((s) => ({ ...s, [videoId]: "error" }));
      }
    },
    [token],
  );

  const edit = useCallback((videoId: string, patch: Partial<VoteDraft>) => {
    setDrafts((d) => ({ ...d, [videoId]: { ...(d[videoId] || EMPTY_DRAFT), ...patch } }));
  }, []);

  /** Anything rated but not yet posted leaves as the tab is being closed. */
  useEffect(() => {
    const flush = () => {
      const who = reviewerRef.current;
      if (!who) return;
      for (const [videoId, draft] of Object.entries(draftsRef.current)) {
        if (draft.rating === null) continue;
        if (unchanged(draft, savedRef.current[videoId])) continue;
        void submitVote(
          token,
          {
            reviewer_ref: who.ref,
            video_id: videoId,
            rating: draft.rating,
            picked: draft.picked,
            comment: draft.comment,
          },
          // keepalive: the request has to outlive the document.
          true,
        ).catch(() => undefined);
      }
    };
    const onHidden = () => {
      if (document.visibilityState === "hidden") flush();
    };
    // Both, because iOS Safari frequently never fires `pagehide` on a tab
    // switch and `visibilitychange` is what actually arrives.
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", onHidden);
    return () => {
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onHidden);
    };
  }, [token]);

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const t of Object.values(pending)) clearTimeout(t);
    };
  }, []);

  const videos = batch?.videos ?? [];
  const total = videos.length;
  const current: (typeof videos)[number] | undefined = videos[index];
  const ratedCount = useMemo(
    () => videos.filter((v) => saved[v.video_id]).length,
    [videos, saved],
  );
  const picks = useMemo(
    () => videos.filter((v) => saved[v.video_id]?.picked),
    [videos, saved],
  );

  const goTo = useCallback(
    (next: number) => {
      if (current) void save(current.video_id);
      setDone(false);
      setIndex(Math.max(0, Math.min(total - 1, next)));
      // The next film at the top of the screen, not wherever the last comment
      // box happened to leave the scroll.
      document
        .getElementById("judge-surface")
        ?.scrollTo({ top: 0, behavior: "smooth" });
    },
    [current, save, total],
  );

  // ------------------------------------------------------------- states

  if (loadError) {
    const e = loadError as ApiError;
    const missing = e instanceof ApiError && e.status === 404;
    return (
      <Splash>
        <h1 className="text-xl font-semibold text-zinc-100">
          {missing ? "This link is not valid" : "We cannot load this right now"}
        </h1>
        <p className="mt-2 text-[14px] leading-relaxed text-zinc-400">
          {missing
            ? "The review it points at does not exist, or it has been closed. Nothing you did caused this — check that you copied the whole link, or open it again from wherever you found it."
            : "Something on our side is not answering. Wait a moment and reload the page; anything you have already answered is safe."}
        </p>
      </Splash>
    );
  }

  if (!batch || !reviewer) return <Splash>Loading the ads…</Splash>;

  if (total === 0) {
    return (
      <Splash>
        <h1 className="text-xl font-semibold text-zinc-100">Nothing to review</h1>
        <p className="mt-2 text-[14px] leading-relaxed text-zinc-400">
          This review has no ads in it yet, so there is nothing for you to do
          here. You can close the tab.
        </p>
      </Splash>
    );
  }

  if (done) {
    return (
      <Finish
        rated={ratedCount}
        total={total}
        pickHooks={picks.map((v) => v.hook).filter(Boolean)}
        reviewer={reviewer}
        onBack={() => {
          setDone(false);
          setIndex(0);
        }}
      />
    );
  }

  const last = index === total - 1;

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-10 border-b border-zinc-900 bg-zinc-950/95 px-4 py-3 backdrop-blur">
        <div className="mx-auto w-full max-w-lg">
          <div className="flex items-baseline justify-between gap-3">
            <h1 className="truncate text-[13px] font-medium text-zinc-300">
              {batch.title || "Rate these ads"}
            </h1>
            <span className="shrink-0 font-mono text-[12px] text-zinc-500">
              {index + 1} of {total}
            </span>
          </div>
          <div className="mt-2 flex gap-1">
            {videos.map((v, i) => (
              <button
                key={v.video_id}
                aria-label={`Go to ad ${i + 1}`}
                onClick={() => goTo(i)}
                className={`h-1.5 flex-1 rounded-full transition-colors ${
                  i === index
                    ? "bg-sky-500"
                    : saved[v.video_id]
                      ? "bg-emerald-600"
                      : "bg-zinc-800"
                }`}
              />
            ))}
          </div>
        </div>
      </header>

      <div className="mx-auto w-full max-w-lg flex-1 px-4 pb-10 pt-4">
        {current && (
          <>
            <JudgeClip
              // Keyed so moving on unmounts the previous player: exactly one
              // film can be playing without the page having to police it.
              key={current.video_id}
              src={current.mp4_url}
              hook={current.hook}
            />

            {current.hook ? (
              <p className="mx-auto mt-4 max-w-md text-center text-[15px] italic leading-snug text-zinc-400">
                &ldquo;{current.hook}&rdquo;
              </p>
            ) : null}

            <div className="mt-6">
              <JudgeVote
                draft={drafts[current.video_id] || EMPTY_DRAFT}
                status={status[current.video_id] || "empty"}
                error={errors[current.video_id]}
                onRate={(rating) => {
                  edit(current.video_id, { rating });
                  void save(current.video_id, { rating });
                }}
                onPick={(picked) => {
                  edit(current.video_id, { picked });
                  void save(current.video_id, { picked });
                }}
                onComment={(comment) => {
                  edit(current.video_id, { comment });
                  clearTimeout(timers.current[current.video_id]);
                  timers.current[current.video_id] = setTimeout(
                    () => void save(current.video_id, { comment }),
                    COMMENT_DEBOUNCE_MS,
                  );
                }}
                onCommentBlur={() => void save(current.video_id)}
                onRetry={() => void save(current.video_id)}
              />
            </div>
          </>
        )}
      </div>

      <nav className="sticky bottom-0 border-t border-zinc-900 bg-zinc-950/95 px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur">
        <div className="mx-auto flex w-full max-w-lg gap-2">
          <button
            onClick={() => goTo(index - 1)}
            disabled={index === 0}
            className="rounded-xl border border-zinc-700 px-5 py-3 text-[14px] font-medium text-zinc-300 disabled:opacity-30"
          >
            Back
          </button>
          <button
            onClick={() => {
              if (!last) {
                goTo(index + 1);
                return;
              }
              if (current) void save(current.video_id);
              setDone(true);
            }}
            className="flex-1 rounded-xl bg-sky-600 py-3 text-[15px] font-semibold text-white active:bg-sky-500"
          >
            {last ? "Finish" : "Next ad"}
          </button>
        </div>
      </nav>
    </div>
  );
}

// -------------------------------------------------------------- chrome

function Splash({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-full items-center justify-center px-6 py-16">
      <div className="w-full max-w-sm text-center text-zinc-400">{children}</div>
    </div>
  );
}

/**
 * The end.
 *
 * It says the work is banked and that changing an answer is allowed, because
 * both are true and a panellist unsure of either will either re-submit or
 * abandon. It does not thank them for a "submission": under
 * `review_type: manual_review` the Terac task itself is still theirs to close.
 */
function Finish({
  rated,
  total,
  pickHooks,
  reviewer,
  onBack,
}: {
  rated: number;
  total: number;
  pickHooks: string[];
  reviewer: Reviewer;
  onBack: () => void;
}) {
  return (
    <div className="flex min-h-full items-center justify-center px-6 py-16">
      <div className="w-full max-w-sm">
        <div className="mb-5 flex h-14 w-14 items-center justify-center rounded-full bg-emerald-600/15 text-2xl text-emerald-400">
          ✓
        </div>
        <h1 className="text-2xl font-semibold text-zinc-100">
          That&rsquo;s everything.
        </h1>
        <p className="mt-2 text-[15px] leading-relaxed text-zinc-400">
          You rated {rated} of {total} ads and every answer is saved. Thank you —
          this is the part the software cannot do for itself.
        </p>

        {pickHooks.length > 0 ? (
          <div className="mt-5 rounded-xl border border-amber-900/60 bg-amber-950/20 p-3">
            <div className="text-[11px] uppercase tracking-widest text-amber-500/80">
              {pickHooks.length === 1 ? "your pick" : "your picks"}
            </div>
            <ul className="mt-1 space-y-1">
              {pickHooks.map((h) => (
                <li key={h} className="text-[14px] leading-snug text-amber-100">
                  &ldquo;{h}&rdquo;
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {rated < total ? (
          <p className="mt-4 text-[13px] text-amber-400">
            {total - rated} {total - rated === 1 ? "ad has" : "ads have"} no rating
            yet.
          </p>
        ) : null}

        <button
          onClick={onBack}
          className="mt-6 w-full rounded-xl border border-zinc-700 py-3 text-[14px] font-medium text-zinc-200 active:bg-zinc-900"
        >
          Go back and change an answer
        </button>
        <p className="mt-3 text-[12.5px] leading-relaxed text-zinc-500">
          Changing an answer replaces the old one — it does not count twice. When
          you are happy with it you can close this tab
          {reviewer.fromTerac ? " and finish your task on Terac" : ""}.
        </p>
        <p className="mt-6 font-mono text-[10px] text-zinc-700">{reviewer.ref}</p>
      </div>
    </div>
  );
}
