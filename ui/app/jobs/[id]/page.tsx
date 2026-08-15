"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { getVideo, Video } from "@/lib/api";
import { useJobStream } from "@/lib/useJobStream";
import { secs, when } from "@/lib/format";
import { StageStrip, Trace } from "@/components/Trace";
import { VideoPlayer } from "@/components/VideoPlayer";
import {
  Badge,
  DispositionBadge,
  ErrorBox,
  Internal,
  Loading,
  Panel,
  Stat,
} from "@/components/ui";

export default function JobPage() {
  const params = useParams<{ id: string }>();
  const jobId = params?.id || "";
  const { events, job, feed, error } = useJobStream(jobId);
  const [video, setVideo] = useState<Video | null>(null);

  // The terminal event carries the video id, so the film can be fetched the
  // moment the stream closes rather than after a poll.
  const videoId = useMemo(() => {
    const term = [...events].reverse().find((e) => e.stage === "done");
    const fromEvent = term?.data?.video_id;
    return (typeof fromEvent === "string" && fromEvent) || job?.video_id || null;
  }, [events, job]);

  useEffect(() => {
    if (!videoId) return;
    getVideo(videoId).then(setVideo).catch(() => setVideo(null));
  }, [videoId]);

  const startedAt = job?.created_at || events[0]?.ts;
  const elapsed = useElapsed(startedAt, job?.status);
  const running = job?.status === "queued" || job?.status === "running";

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="font-mono text-base text-zinc-100">{jobId}</h1>
            <StatusBadge status={job?.status} />
            <FeedBadge feed={feed} />
          </div>
          <p className="mt-0.5 text-[12px] text-zinc-500">
            {job?.company_slug || "—"} · {job?.lane || "—"} · {job?.mode || "—"} ·
            started {when(startedAt)}
          </p>
        </div>
        <div className="flex gap-2">
          {videoId && (
            <Internal
              href={`/videos/${videoId}`}
              className="rounded border border-sky-800 bg-sky-950/50 px-3 py-1.5 text-xs"
            >
              open video detail →
            </Internal>
          )}
        </div>
      </header>

      {error ? <ErrorBox error={error} /> : null}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        <Stat
          label="status"
          value={job?.status || "…"}
          tone={
            job?.status === "done"
              ? "good"
              : job?.status === "failed"
                ? "bad"
                : "default"
          }
        />
        <Stat label="elapsed" value={elapsed} sub={running ? "live" : "final"} />
        <Stat label="events" value={events.length} sub={`feed: ${feed}`} />
        <Stat
          label="stage"
          value={events[events.length - 1]?.stage || "—"}
          sub={job?.progress_note || ""}
        />
        <Stat
          label="video"
          value={video ? video.disposition || "—" : videoId ? "…" : "—"}
          tone={
            video?.disposition === "surfaced"
              ? "good"
              : video?.disposition === "watchlist"
                ? "warn"
                : "default"
          }
          sub={video?.score ? `score ${video.score.overall.toFixed(2)}` : ""}
        />
        <Stat
          label="duration"
          value={video ? secs(video.duration_s) : "—"}
          sub={video ? `${video.mode} mode` : ""}
        />
      </div>

      <Panel title="Pipeline">
        <StageStrip events={events} />
        {job?.progress_note && (
          <div className="mt-2 text-[12.5px] text-zinc-400">
            {job.progress_note}
          </div>
        )}
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Panel
          title="Trace"
          right={
            running ? (
              <span className="flex items-center gap-1.5 text-[11px] text-emerald-400">
                <span className="live-dot h-1.5 w-1.5 rounded-full bg-emerald-500" />
                live
              </span>
            ) : (
              <span className="text-[11px] text-zinc-600">closed</span>
            )
          }
        >
          <Trace events={events} live={!!running} startedAt={startedAt} />
        </Panel>

        <div className="space-y-4">
          <Panel title="Result">
            {job?.status === "failed" ? (
              <div className="rounded border border-rose-900 bg-rose-950/40 p-2.5 text-[13px] text-rose-200">
                <div className="font-mono text-[10px] uppercase tracking-widest opacity-70">
                  job failed
                </div>
                <div className="mt-1">{job.error || "no error recorded"}</div>
              </div>
            ) : !videoId ? (
              <div className="text-[13px] text-zinc-500">
                {running
                  ? "Nothing to show until the render lands."
                  : "This job produced no video."}
              </div>
            ) : !video ? (
              <Loading what="loading the video" />
            ) : (
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <DispositionBadge d={video.disposition} />
                  {video.score && (
                    <Badge tone="info">score {video.score.overall.toFixed(2)}</Badge>
                  )}
                  <Badge tone="neutral">{secs(video.duration_s)}</Badge>
                </div>
                {video.drop_reason && (
                  <div className="rounded border border-zinc-700 bg-zinc-900 p-2 text-[12px] text-zinc-300">
                    <span className="text-zinc-500">
                      why it was {video.disposition}:{" "}
                    </span>
                    {video.drop_reason}
                  </div>
                )}
                <div className="text-[13px] text-zinc-200">{video.hook}</div>
                <VideoPlayer src={video.mp4_url} maxHeight={400} />
                <Internal href={`/videos/${video.id}`}>
                  full detail, recipe and prompts →
                </Internal>
              </div>
            )}
          </Panel>

          <Panel title="How this feed works">
            <ul className="space-y-1.5 text-[11.5px] leading-snug text-zinc-500">
              <li>
                <b className="text-zinc-300">sse</b> — live from{" "}
                <code>GET /v1/jobs/{"{id}"}/stream</code>. Every trace line as the
                pipeline produces it; the browser resumes from Last-Event-ID on a
                reconnect.
              </li>
              <li>
                <b className="text-zinc-300">polling</b> — the stream never
                opened, so this falls back to{" "}
                <code>/v1/jobs/{"{id}"}/events</code>, and to the job row itself
                if that is not there either.
              </li>
              <li>
                <b className="text-zinc-300">closed</b> — a terminal event
                arrived. Nothing more is coming.
              </li>
            </ul>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status?: string }) {
  if (!status) return <Badge tone="neutral">…</Badge>;
  if (status === "done") return <Badge tone="good">done</Badge>;
  if (status === "failed") return <Badge tone="bad">failed</Badge>;
  if (status === "running") return <Badge tone="info">running</Badge>;
  return <Badge tone="neutral">{status}</Badge>;
}

function FeedBadge({ feed }: { feed: string }) {
  const tone =
    feed === "sse" ? "good" : feed === "polling" ? "warn" : ("neutral" as const);
  return (
    <Badge tone={tone as "good" | "warn" | "neutral"}>
      feed: {feed}
    </Badge>
  );
}

function useElapsed(startedAt?: string, status?: string) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (status === "done" || status === "failed") return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [status]);
  if (!startedAt) return "—";
  const t = Date.parse(startedAt);
  if (Number.isNaN(t)) return "—";
  const d = Math.max(0, (Date.now() - t) / 1000);
  const m = Math.floor(d / 60);
  return m ? `${m}m ${Math.floor(d % 60)}s` : `${Math.floor(d)}s`;
}
