"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { GATE, getAllVideos, Video } from "@/lib/api";
import { num, secs, when } from "@/lib/format";
import {
  Badge,
  DispositionBadge,
  Empty,
  ErrorBox,
  Internal,
  Loading,
  Panel,
  Select,
  Stat,
} from "@/components/ui";

type Sort = "recent" | "score" | "duration";

export default function LibraryPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Library />
    </Suspense>
  );
}

function Library() {
  const params = useSearchParams();
  const [videos, setVideos] = useState<Video[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [company, setCompany] = useState(params.get("company") || "all");
  const [disposition, setDisposition] = useState("all");
  const [lane, setLane] = useState("all");
  const [sort, setSort] = useState<Sort>("recent");

  useEffect(() => {
    getAllVideos().then(setVideos).catch(setErr);
  }, []);

  const companies = useMemo(
    () => [...new Set((videos || []).map((v) => v.company_slug).filter(Boolean))].sort(),
    [videos],
  );
  const lanes = useMemo(
    () => [...new Set((videos || []).map((v) => v.lane).filter(Boolean))].sort(),
    [videos],
  );

  const rows = useMemo(() => {
    let out = videos || [];
    if (company !== "all") out = out.filter((v) => v.company_slug === company);
    if (lane !== "all") out = out.filter((v) => v.lane === lane);
    if (disposition !== "all")
      out = out.filter((v) => (v.disposition || "unknown") === disposition);
    const by: Record<Sort, (a: Video, b: Video) => number> = {
      recent: (a, b) => Date.parse(b.created_at || "") - Date.parse(a.created_at || ""),
      score: (a, b) => (b.score?.overall ?? -1) - (a.score?.overall ?? -1),
      duration: (a, b) => b.duration_s - a.duration_s,
    };
    return [...out].sort(by[sort]);
  }, [videos, company, lane, disposition, sort]);

  const counts = useMemo(() => {
    const c = { surfaced: 0, watchlist: 0, dropped: 0, other: 0 };
    for (const v of videos || []) {
      const d = (v.disposition || "").toLowerCase();
      if (d === "surfaced") c.surfaced++;
      else if (d === "watchlist") c.watchlist++;
      else if (d === "dropped") c.dropped++;
      else c.other++;
    }
    return c;
  }, [videos]);

  const evidenceDrops = (videos || []).filter(
    (v) => v.score && v.score.evidence < GATE.evidence_floor,
  ).length;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-lg font-semibold text-zinc-100">Library</h1>
        <p className="text-[12px] text-zinc-500">
          Every video the engine has produced, dropped ones included — the
          rejections are part of the record, not a failure log.
        </p>
      </header>

      {err ? <ErrorBox error={err} /> : null}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="videos" value={videos ? num(videos.length) : "…"} />
        <Stat label="surfaced" value={num(counts.surfaced)} tone="good" sub={`≥ ${GATE.surface_threshold} overall`} />
        <Stat label="watchlist" value={num(counts.watchlist)} tone="warn" sub={`≥ ${GATE.watchlist_threshold} overall`} />
        <Stat label="dropped" value={num(counts.dropped)} sub="a verdict, not an error" />
        <Stat
          label="dropped on evidence"
          value={num(evidenceDrops)}
          sub={`evidence < ${GATE.evidence_floor}`}
        />
        <Stat
          label="mean score"
          value={
            videos && videos.length
              ? (
                  videos.reduce((s, v) => s + (v.score?.overall ?? 0), 0) /
                  videos.length
                ).toFixed(2)
              : "—"
          }
        />
      </div>

      <Panel
        title={`${num(rows.length)} shown`}
        right={
          <div className="flex flex-wrap gap-2">
            <Select
              label="company"
              value={company}
              onChange={setCompany}
              options={[
                { value: "all", label: "all" },
                ...companies.map((c) => ({ value: c, label: c })),
              ]}
            />
            <Select
              label="lane"
              value={lane}
              onChange={setLane}
              options={[
                { value: "all", label: "all" },
                ...lanes.map((l) => ({ value: l, label: l })),
              ]}
            />
            <Select
              label="disposition"
              value={disposition}
              onChange={setDisposition}
              options={[
                { value: "all", label: "all" },
                { value: "surfaced", label: `surfaced (${counts.surfaced})` },
                { value: "watchlist", label: `watchlist (${counts.watchlist})` },
                { value: "dropped", label: `dropped (${counts.dropped})` },
              ]}
            />
            <Select
              label="sort"
              value={sort}
              onChange={(v) => setSort(v as Sort)}
              options={[
                { value: "recent", label: "newest" },
                { value: "score", label: "score" },
                { value: "duration", label: "duration" },
              ]}
            />
          </div>
        }
      >
        {!videos ? (
          <Loading what="reading videos" />
        ) : rows.length === 0 ? (
          <Empty>
            Nothing here.{" "}
            <Internal href="/">Generate one →</Internal>
          </Empty>
        ) : (
          <div className="space-y-2">
            {rows.map((v) => (
              <VideoRow key={v.id} v={v} />
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}

/**
 * Surfaced and dropped are visually distinct without dropped reading as broken:
 * surfaced gets a left accent and full contrast, dropped gets a flat card and a
 * stated reason. Nothing is red — red is reserved for a job that actually
 * failed, which is a different thing entirely.
 */
function VideoRow({ v }: { v: Video }) {
  const d = (v.disposition || "").toLowerCase();
  const accent =
    d === "surfaced"
      ? "border-l-4 border-l-emerald-500"
      : d === "watchlist"
        ? "border-l-4 border-l-amber-500"
        : "border-l-4 border-l-zinc-700";

  return (
    <Link
      href={`/videos/${v.id}`}
      className={`block rounded-lg border border-zinc-800 bg-zinc-900/40 p-2.5 transition-colors hover:border-zinc-700 hover:bg-zinc-900/70 ${accent} ${
        d === "dropped" ? "opacity-90" : ""
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <DispositionBadge d={v.disposition} />
        <span className="font-mono text-[12px] text-zinc-300">
          {v.company_slug}
        </span>
        <Badge tone="violet">{v.lane}</Badge>
        <Badge tone={v.mode === "agentic" ? "info" : "neutral"}>{v.mode}</Badge>
        <span className="font-mono text-[11px] text-zinc-500">
          {secs(v.duration_s)}
        </span>
        <span className="ml-auto font-mono text-[11px] text-zinc-600">
          {when(v.created_at)}
        </span>
      </div>

      <div className="mt-1 text-[13.5px] leading-snug text-zinc-100">
        {v.hook || <span className="text-zinc-600">no hook</span>}
      </div>
      <div className="mt-0.5 text-[11.5px] text-zinc-500">{v.product}</div>

      {v.score && (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px]">
          <span className="text-zinc-300">
            overall <b className="text-zinc-100">{v.score.overall.toFixed(2)}</b>
          </span>
          <Dim label="rel" v={v.score.relevance} />
          <Dim label="spec" v={v.score.specificity} />
          <Dim label="act" v={v.score.actionability} />
          <Dim label="diff" v={v.score.differentiation} />
          <span
            className={
              v.score.evidence < GATE.evidence_floor
                ? "text-amber-400"
                : "text-emerald-400"
            }
            title={`evidence floor ${GATE.evidence_floor}`}
          >
            evid <b>{v.score.evidence.toFixed(1)}</b>
            {v.score.evidence < GATE.evidence_floor && " ↓gate"}
          </span>
        </div>
      )}

      {v.drop_reason && (
        <div className="mt-1.5 rounded border border-zinc-800 bg-zinc-950/70 px-2 py-1 text-[11.5px] text-zinc-400">
          <span className="text-zinc-600">why {v.disposition}: </span>
          {v.drop_reason}
        </div>
      )}
    </Link>
  );
}

function Dim({ label, v }: { label: string; v: number }) {
  return (
    <span className="text-zinc-500">
      {label}{" "}
      <b className={v >= 4 ? "text-emerald-400" : v >= 3 ? "text-zinc-300" : "text-zinc-500"}>
        {v.toFixed(1)}
      </b>
    </span>
  );
}
