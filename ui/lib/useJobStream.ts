"use client";

import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  getJob,
  getJobEvents,
  Job,
  streamUrl,
  TraceEvent,
} from "@/lib/api";

export type Feed = "connecting" | "sse" | "polling" | "closed";

/**
 * Watch one job.
 *
 * SSE is the primary feed: the server emits one frame per trace line with the
 * sequence number as `id:`, so a browser reconnect resumes for free. Two
 * fallbacks sit behind it, in this order:
 *
 *   1. `GET /v1/jobs/{id}/events?after=` — the same events as JSON. Correct on
 *      every worker, so it is the right degraded mode.
 *   2. `GET /v1/jobs/{id}` — status and one sentence. Enough to know the job
 *      finished and what it produced.
 *
 * The switch happens when the stream errors before delivering anything, which
 * is what "the endpoint isn't built yet" looks like from the client side.
 *
 * `verbose` reopens the feed at `?level=debug`, which adds every model call with
 * its prompts. It reopens rather than filters because the level is a server-side
 * subscription — the point is that a page not in verbose mode never receives the
 * prompts at all. The reconnect deliberately drops the `after` cursor so the
 * server replays the buffer at the new level and the calls already made appear;
 * events collected so far are kept and deduplicated on `seq`, so turning it on
 * four minutes into a run backfills rather than restarts.
 */
export function useJobStream(jobId: string, verbose = false) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [feed, setFeed] = useState<Feed>("connecting");
  const [error, setError] = useState<unknown>(null);

  const seen = useRef<Set<number>>(new Set());
  const lastSeq = useRef(0);
  const done = useRef(false);
  const watching = useRef<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    // Only a different job is a fresh start. A level change is the same job
    // seen at a different resolution, and clearing here would blank the trace.
    if (watching.current !== jobId) {
      watching.current = jobId;
      seen.current = new Set();
      lastSeq.current = 0;
      done.current = false;
      setEvents([]);
    }
    const level = verbose ? "debug" : "info";
    setFeed("connecting");

    const push = (e: TraceEvent) => {
      if (seen.current.has(e.seq)) return;
      seen.current.add(e.seq);
      lastSeq.current = Math.max(lastSeq.current, e.seq);
      setEvents((prev) => [...prev, e].sort((a, b) => a.seq - b.seq));
      if (e.stage === "done" || e.stage === "failed") finish();
    };

    const finish = () => {
      done.current = true;
      setFeed("closed");
      getJob(jobId).then(
        (j) => alive && setJob(j),
        () => {},
      );
    };

    // The job row is fetched once up front so the header has something to show
    // before the first frame, and again on every terminal event.
    getJob(jobId).then(
      (j) => {
        if (!alive) return;
        setJob(j);
        if (j.status === "done" || j.status === "failed") done.current = true;
      },
      (e) => alive && setError(e),
    );

    let es: EventSource | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    const startPolling = () => {
      if (!alive || pollTimer) return;
      setFeed("polling");
      const tick = async () => {
        if (!alive) return;
        try {
          const win = await getJobEvents(jobId, lastSeq.current, level);
          win.events.forEach(push);
          if (win.complete) {
            finish();
            return;
          }
        } catch (e) {
          // No /events endpoint on this build: degrade to the job row, which
          // has existed since the first version of the API.
          try {
            const j = await getJob(jobId);
            if (!alive) return;
            setJob(j);
            if (j.status === "done" || j.status === "failed") {
              finish();
              return;
            }
          } catch (inner) {
            if (alive) setError(inner);
          }
        }
        if (alive && !done.current) pollTimer = setTimeout(tick, 2000);
      };
      pollTimer = setTimeout(tick, 0);
    };

    try {
      es = new EventSource(streamUrl(jobId, undefined, level));
      es.onopen = () => {
        if (alive) setFeed("sse");
      };
      es.onmessage = (ev) => {
        if (!alive) return;
        setFeed("sse");
        try {
          push(JSON.parse(ev.data) as TraceEvent);
        } catch {
          /* a comment frame or a partial line — the browser reassembles */
        }
      };
      es.onerror = () => {
        if (!alive) return;
        if (done.current) {
          es?.close();
          return;
        }
        // EventSource retries on its own. Only give up when it has never
        // delivered a frame — that is a missing endpoint, not a blip.
        if (seen.current.size === 0) {
          es?.close();
          es = null;
          startPolling();
        }
      };
    } catch {
      startPolling();
    }

    return () => {
      alive = false;
      es?.close();
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [jobId, verbose]);

  return { events, job, feed, error };
}

export function isMissing(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}
