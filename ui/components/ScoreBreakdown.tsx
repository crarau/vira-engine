"use client";

import { Score } from "@/lib/api";
import { Bar } from "./ui";

const DIMENSIONS: { key: keyof Score; label: string; why: string }[] = [
  { key: "relevance", label: "relevance", why: "does it speak to this category's audience" },
  { key: "specificity", label: "specificity", why: "concrete claims, not adjectives" },
  { key: "actionability", label: "actionability", why: "is there a thing to do" },
  { key: "differentiation", label: "differentiation", why: "would any competitor run this" },
  { key: "evidence", label: "evidence", why: "supported by the cited source videos" },
];

/**
 * All five dimensions, with the evidence gate called out.
 *
 * Evidence is not one fifth of the verdict — it is a veto that runs before the
 * average is consulted. `score.py::disposition` returns "dropped" the moment
 * evidence falls below the floor, whatever the other four say, so the row is
 * separated and the floor is drawn on it.
 */
export function ScoreBreakdown({
  score,
  gate,
  disposition,
  dropReason,
}: {
  score: Score | null;
  gate: {
    evidence_floor: number;
    watchlist_threshold: number;
    surface_threshold: number;
  };
  disposition?: string | null;
  dropReason?: string | null;
}) {
  if (!score) {
    return (
      <div className="text-sm text-zinc-500">
        No score breakdown stored for this video.
      </div>
    );
  }

  const gated = score.evidence < gate.evidence_floor;

  return (
    <div className="space-y-3">
      <div className="flex items-baseline gap-3">
        <div className="font-mono text-3xl leading-none text-zinc-100">
          {score.overall.toFixed(2)}
        </div>
        <div className="text-[11px] text-zinc-500">
          overall — mean of the five, computed in Python after all creative work
        </div>
      </div>

      <div className="relative">
        <Bar
          value={score.overall}
          tone={
            score.overall >= gate.surface_threshold
              ? "good"
              : score.overall >= gate.watchlist_threshold
                ? "warn"
                : "bad"
          }
        />
        <Ticks gate={gate} />
      </div>

      <div className="space-y-2 pt-3">
        {DIMENSIONS.filter((d) => d.key !== "evidence").map((d) => (
          <Dim key={d.key} label={d.label} why={d.why} value={score[d.key]} />
        ))}
      </div>

      <div
        className={`rounded-md border p-2.5 ${
          gated ? "border-zinc-600 bg-zinc-800/40" : "border-emerald-900 bg-emerald-950/30"
        }`}
      >
        <div className="mb-1 flex items-center justify-between text-[11px]">
          <span className="font-mono uppercase tracking-widest text-zinc-400">
            evidence gate
          </span>
          <span className={gated ? "text-zinc-300" : "text-emerald-400"}>
            {gated ? "did not clear" : "cleared"} · floor {gate.evidence_floor.toFixed(1)}
          </span>
        </div>
        <Dim
          label="evidence"
          why="supported by the cited source videos"
          value={score.evidence}
          tone={gated ? "bad" : "good"}
        />
        <p className="mt-2 text-[11px] leading-snug text-zinc-500">
          {gated ? (
            <>
              Evidence below {gate.evidence_floor.toFixed(1)} drops the video
              before the average is even consulted. This is the gate working, not
              a failure — the script made claims the cited clips do not support.
            </>
          ) : (
            <>
              Evidence cleared the floor, so the overall average decides:{" "}
              ≥{gate.surface_threshold} surfaces, ≥{gate.watchlist_threshold}{" "}
              goes on the watchlist, below that drops.
            </>
          )}
        </p>
      </div>

      {dropReason && (
        <div className="rounded-md border border-zinc-700 bg-zinc-900 p-2.5">
          <div className="font-mono text-[10px] uppercase tracking-widest text-zinc-500">
            why it was {disposition || "dropped"}
          </div>
          <div className="mt-1 text-[13px] text-zinc-200">{dropReason}</div>
        </div>
      )}
    </div>
  );
}

function Ticks({
  gate,
}: {
  gate: { watchlist_threshold: number; surface_threshold: number };
}) {
  return (
    <div className="relative mt-1 h-4">
      {[
        { v: gate.watchlist_threshold, label: "watchlist" },
        { v: gate.surface_threshold, label: "surface" },
      ].map((t) => (
        <div
          key={t.label}
          className="absolute top-0 -translate-x-1/2 text-[9px] text-zinc-600"
          style={{ left: `${(t.v / 5) * 100}%` }}
        >
          <div className="mx-auto h-1.5 w-px bg-zinc-700" />
          {t.label} {t.v}
        </div>
      ))}
    </div>
  );
}

function Dim({
  label,
  why,
  value,
  tone,
}: {
  label: string;
  why: string;
  value: number;
  tone?: "good" | "bad";
}) {
  return (
    <div className="flex items-center gap-3">
      <div className="w-32 shrink-0">
        <div className="text-[12px] text-zinc-300">{label}</div>
        <div className="text-[10px] leading-tight text-zinc-600">{why}</div>
      </div>
      <div className="flex-1">
        <Bar
          value={value}
          tone={tone || (value >= 4 ? "good" : value >= 3 ? "warn" : "bad")}
        />
      </div>
      <div className="w-10 shrink-0 text-right font-mono text-[13px] text-zinc-200">
        {value.toFixed(1)}
      </div>
    </div>
  );
}
