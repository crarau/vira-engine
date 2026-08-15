"use client";

import { useState } from "react";
import { CorpusTrend, GATE } from "@/lib/api";
import { ageDays, ageLabel, compact, num, pct, when } from "@/lib/format";
import { Badge } from "./ui";

/** The cover image is a raw-payload field the scraper may or may not have set. */
export function coverUrl(t: CorpusTrend): string | null {
  const raw = (t.raw || {}) as Record<string, unknown>;
  for (const k of ["coverUrl", "cover_url", "cover", "thumbnail", "thumbnailUrl"]) {
    const v = raw[k];
    if (typeof v === "string" && v.startsWith("http")) return v;
  }
  return null;
}

export function TrendCard({
  t,
  maxAgeDays = GATE.max_age_days,
}: {
  t: CorpusTrend;
  maxAgeDays?: number;
}) {
  const [rawOpen, setRawOpen] = useState(false);
  const [imgOk, setImgOk] = useState(true);
  const age = ageDays(t.posted_at, t.age_days);
  const stale = age !== null && age > maxAgeDays;
  const cover = coverUrl(t);

  return (
    <div
      className={`flex gap-3 rounded-lg border p-2.5 ${
        stale
          ? "border-zinc-800 bg-zinc-900/20 opacity-75"
          : "border-zinc-800 bg-zinc-900/50"
      }`}
    >
      {/* 9:16 thumb, deliberately small — this is a list, not a viewer. */}
      <div className="relative h-[112px] w-[63px] shrink-0 overflow-hidden rounded border border-zinc-800 bg-zinc-900">
        {cover && imgOk ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={cover}
            alt=""
            className="h-full w-full object-cover"
            onError={() => setImgOk(false)}
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-center text-[9px] leading-tight text-zinc-700">
            {cover ? "cover\nblocked" : "no\ncover"}
          </div>
        )}
        {stale && (
          <div className="absolute inset-x-0 bottom-0 bg-zinc-950/85 py-0.5 text-center font-mono text-[9px] uppercase tracking-wide text-amber-400">
            stale
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[12px] text-zinc-200">
            @{t.author || "unknown"}
          </span>
          {t.format && <Badge tone="violet">{t.format}</Badge>}
          {t.platform && t.platform !== "tiktok" && (
            <Badge tone="info">{t.platform}</Badge>
          )}
          {stale ? (
            <Badge tone="warn" title={`posted ${when(t.posted_at)}`}>
              {ageLabel(age)} old · past {maxAgeDays}d
            </Badge>
          ) : (
            <Badge tone="good" title={`posted ${when(t.posted_at)}`}>
              {ageLabel(age)}
            </Badge>
          )}
          {age === null && <Badge tone="bad">no posted_at</Badge>}
        </div>

        <p className="mt-1 line-clamp-3 text-[12.5px] leading-snug text-zinc-300">
          {t.caption || t.title || (
            <span className="italic text-zinc-600">no caption</span>
          )}
        </p>

        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px] text-zinc-500">
          <span title="trend_score">
            score{" "}
            <b className="text-zinc-200">{num(t.trend_score ?? null, 0)}</b>
          </span>
          <span title="views">
            <b className="text-zinc-300">{compact(t.views)}</b> views
          </span>
          <span title="likes">
            <b className="text-zinc-300">{compact(t.likes)}</b> likes
          </span>
          <span title="engagement_rate">
            eng <b className="text-zinc-300">{pct(t.engagement_rate)}</b>
          </span>
          {typeof t.relevance_rank === "number" && (
            <span title="relevance_rank in category_trends">
              rank <b className="text-zinc-300">{t.relevance_rank}</b>
            </span>
          )}
          <span className="text-zinc-600">{t.trend_key}</span>
        </div>

        {t.hashtags && t.hashtags.length > 0 && (
          <div className="mt-1 truncate text-[11px] text-zinc-600">
            {t.hashtags.slice(0, 10).map((h) => `#${h.replace(/^#/, "")}`).join(" ")}
          </div>
        )}

        <div className="mt-1.5 flex items-center gap-3 text-[11px]">
          {t.source_url ? (
            <a
              href={t.source_url}
              target="_blank"
              rel="noreferrer"
              className="truncate text-sky-400 underline decoration-sky-900 underline-offset-2 hover:text-sky-300"
            >
              {t.source_url}
            </a>
          ) : (
            <span className="text-rose-400">no source_url — cannot be cited</span>
          )}
          {t.raw && Object.keys(t.raw).length > 0 && (
            <button
              onClick={() => setRawOpen((v) => !v)}
              className="ml-auto shrink-0 rounded border border-zinc-700 px-1.5 py-0.5 font-mono text-[10px] text-zinc-500 hover:text-zinc-300"
            >
              raw {rawOpen ? "▲" : "▼"}
            </button>
          )}
        </div>

        {rawOpen && t.raw && (
          <pre className="mt-2 max-h-64 overflow-auto rounded border border-zinc-800 bg-zinc-950 p-2 font-mono text-[10px] leading-relaxed text-zinc-400">
            {JSON.stringify(t.raw, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
