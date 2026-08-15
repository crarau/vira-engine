"use client";

import { CorpusStats, GATE } from "@/lib/api";
import { num } from "@/lib/format";

const BUCKETS: { label: string; lo: number; hi: number }[] = [
  { label: "0–7d", lo: 0, hi: 7 },
  { label: "7–30d", lo: 7, hi: 30 },
  { label: "30–60d", lo: 30, hi: 60 },
  { label: "60–90d", lo: 60, hi: 90 },
  { label: "90–180d", lo: 90, hi: 180 },
  { label: "180–365d", lo: 180, hi: 365 },
  { label: "1y+", lo: 365, hi: Infinity },
];

/**
 * The single most important number on the corpus page.
 *
 * `select.py` filters `posted_at >= now - max_age_days` in the database, so
 * anything to the right of the 90d line can never reach a prompt. A corpus that
 * looks large and is mostly to the right of that line is the known failure mode
 * — the histogram exists to make that visible at a glance rather than after a
 * generation comes back with three sources.
 */
export function AgeHistogram({
  ages,
  maxAgeDays = GATE.max_age_days,
}: {
  ages: (number | null)[];
  maxAgeDays?: number;
}) {
  const dated = ages.filter((a): a is number => a !== null);
  const undated = ages.length - dated.length;
  const counts = BUCKETS.map(
    (b) => dated.filter((a) => a >= b.lo && a < b.hi).length,
  );
  const peak = Math.max(1, ...counts);
  const usable = dated.filter((a) => a <= maxAgeDays).length;
  const stale = dated.length - usable;
  const sorted = [...dated].sort((a, b) => a - b);
  const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : null;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-5 gap-y-1 text-[11px]">
        <span className="text-emerald-400">
          <b className="font-mono text-sm">{num(usable)}</b> usable (≤{maxAgeDays}
          d)
        </span>
        <span className="text-zinc-500">
          <b className="font-mono text-sm text-zinc-400">{num(stale)}</b> stale (&gt;
          {maxAgeDays}d — filtered out before selection)
        </span>
        {undated > 0 && (
          <span className="text-amber-400">
            <b className="font-mono text-sm">{num(undated)}</b> with no posted_at
          </span>
        )}
        <span className="text-zinc-500">
          median{" "}
          <b className="font-mono text-sm text-zinc-300">
            {median === null ? "—" : `${Math.round(median)}d`}
          </b>
        </span>
        <span className="text-zinc-500">
          {dated.length
            ? `${Math.round((usable / dated.length) * 100)}% of the dated corpus is reachable`
            : ""}
        </span>
      </div>

      <div className="flex items-end gap-1" style={{ height: 96 }}>
        {BUCKETS.map((b, i) => {
          const stale = b.lo >= maxAgeDays;
          const h = (counts[i] / peak) * 100;
          return (
            <div
              key={b.label}
              className="group flex flex-1 flex-col items-center justify-end gap-1"
              title={`${b.label}: ${counts[i]} trends`}
            >
              <div className="font-mono text-[10px] text-zinc-500">
                {counts[i] || ""}
              </div>
              <div
                className={`w-full rounded-sm ${
                  stale ? "bg-zinc-700" : "bg-emerald-600"
                } group-hover:opacity-80`}
                style={{ height: `${Math.max(h, counts[i] ? 2 : 0)}%` }}
              />
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex gap-1">
        {BUCKETS.map((b) => (
          <div
            key={b.label}
            className={`flex-1 text-center text-[9px] ${
              b.lo >= maxAgeDays ? "text-zinc-600" : "text-emerald-600"
            }`}
          >
            {b.label}
          </div>
        ))}
      </div>
      <div className="mt-2 text-[11px] text-zinc-600">
        Grey buckets are past the {maxAgeDays}-day freshness window. `select.py`
        applies that filter in the database, before any row cap, so those rows
        never reach a prompt.
      </div>
    </div>
  );
}

/**
 * Corpus-wide age bands, from `/v1/corpus/stats`.
 *
 * The trends endpoint caps a page at 200 rows over a corpus of thousands, so
 * a histogram built from the loaded page describes the page, not the corpus.
 * These four bands are the only honest whole-corpus view, and `usable_share_90d`
 * is the number that decides whether grounding will work at all.
 */
export function CorpusAgeBands({
  stats,
  maxAgeDays = GATE.max_age_days,
}: {
  stats: CorpusStats;
  maxAgeDays?: number;
}) {
  const total = stats.trends_total ?? 0;
  const f30 = stats.fresh_30d ?? 0;
  const f90 = stats.fresh_90d ?? 0;
  const y1 = stats.within_1y ?? 0;
  const bands = [
    { label: "≤30d", n: f30, tone: "bg-emerald-500", usable: true },
    { label: "30–90d", n: Math.max(0, f90 - f30), tone: "bg-emerald-700", usable: true },
    { label: "90d–1y", n: Math.max(0, y1 - f90), tone: "bg-zinc-700", usable: false },
    { label: "over 1y", n: Math.max(0, total - y1), tone: "bg-zinc-800", usable: false },
  ];
  const share =
    typeof stats.usable_share_90d === "number"
      ? stats.usable_share_90d
      : total
        ? f90 / total
        : 0;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline gap-x-5 gap-y-1 text-[11px]">
        <span className="text-zinc-400">
          <b className="font-mono text-sm text-zinc-100">{num(total)}</b> trends
          in the corpus
        </span>
        <span className="text-emerald-400">
          <b className="font-mono text-sm">{num(f90)}</b> inside the {maxAgeDays}
          -day window
        </span>
        <span className={share < 0.5 ? "text-amber-400" : "text-zinc-500"}>
          usable share{" "}
          <b className="font-mono text-sm">{(share * 100).toFixed(0)}%</b>
        </span>
        <span className="text-zinc-500">
          <b className="font-mono text-sm text-zinc-400">
            {num(Math.max(0, total - f90))}
          </b>{" "}
          can never reach a prompt
        </span>
      </div>

      <div className="flex h-7 w-full overflow-hidden rounded border border-zinc-800">
        {bands.map((b) => (
          <div
            key={b.label}
            className={`${b.tone} flex items-center justify-center`}
            style={{ width: `${total ? (b.n / total) * 100 : 0}%` }}
            title={`${b.label}: ${b.n}`}
          >
            <span className="truncate px-1 font-mono text-[9px] text-black/70">
              {total && b.n / total > 0.06 ? num(b.n) : ""}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 text-[10px]">
        {bands.map((b) => (
          <span key={b.label} className="flex items-center gap-1">
            <span className={`inline-block h-2 w-2 rounded-sm ${b.tone}`} />
            <span className={b.usable ? "text-emerald-500" : "text-zinc-600"}>
              {b.label} · {num(b.n)}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
