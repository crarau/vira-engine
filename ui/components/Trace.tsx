"use client";

import { useEffect, useRef, useState } from "react";
import { TraceEvent } from "@/lib/api";
import { clockFromStart } from "@/lib/format";

/**
 * The documented stage vocabulary, in pipeline order.
 *
 * `events.py` is explicit that this list will grow and that an unknown stage
 * must still render — so this map only decides colour and ordering. Anything
 * not in it falls through to a neutral row rather than disappearing.
 */
export const STAGE_ORDER = [
  "queued",
  "select",
  "verify",
  "analyze",
  "plan",
  "write",
  "motion",
  "critique",
  "voice",
  "imagery",
  "cohesion",
  "tool",
  "director",
  "crew",
  "score",
  "render",
  "done",
  "failed",
] as const;

const STAGE_COLOR: Record<string, string> = {
  queued: "bg-zinc-600",
  select: "bg-sky-500",
  verify: "bg-sky-400",
  analyze: "bg-cyan-500",
  plan: "bg-violet-500",
  write: "bg-violet-400",
  motion: "bg-fuchsia-500",
  critique: "bg-orange-500",
  voice: "bg-emerald-500",
  imagery: "bg-amber-500",
  cohesion: "bg-rose-400",
  tool: "bg-teal-500",
  director: "bg-indigo-400",
  crew: "bg-zinc-500",
  score: "bg-yellow-500",
  render: "bg-blue-500",
  done: "bg-emerald-400",
  failed: "bg-rose-500",
};

const LEVEL_TEXT: Record<string, string> = {
  debug: "text-zinc-600",
  info: "text-zinc-300",
  warn: "text-amber-300",
  error: "text-rose-300",
};

export function stageColor(stage: string): string {
  return STAGE_COLOR[stage] || "bg-zinc-600";
}

/** Contiguous events sharing a stage, so the timeline reads as phases. */
function group(events: TraceEvent[]): { stage: string; items: TraceEvent[] }[] {
  const out: { stage: string; items: TraceEvent[] }[] = [];
  for (const e of events) {
    const last = out[out.length - 1];
    if (last && last.stage === e.stage) last.items.push(e);
    else out.push({ stage: e.stage, items: [e] });
  }
  return out;
}

export function Trace({
  events,
  live,
  startedAt,
}: {
  events: TraceEvent[];
  live: boolean;
  startedAt?: string;
}) {
  const [follow, setFollow] = useState(true);
  const [levels, setLevels] = useState<Set<string>>(
    new Set(["info", "warn", "error"]),
  );
  const [dataOpen, setDataOpen] = useState<Set<number>>(new Set());
  const bottom = useRef<HTMLDivElement>(null);

  const shown = events.filter((e) => levels.has(e.level || "info"));
  const groups = group(shown);
  const t0 = startedAt || events[0]?.ts;

  useEffect(() => {
    if (follow) bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [events.length, follow]);

  const toggleLevel = (l: string) => {
    setLevels((prev) => {
      const next = new Set(prev);
      if (next.has(l)) next.delete(l);
      else next.add(l);
      return next;
    });
  };

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-3 text-[11px]">
        <span className="font-mono text-zinc-500">
          {events.length} event{events.length === 1 ? "" : "s"}
        </span>
        <div className="flex gap-1">
          {["debug", "info", "warn", "error"].map((l) => (
            <button
              key={l}
              onClick={() => toggleLevel(l)}
              className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${
                levels.has(l)
                  ? "border-zinc-600 bg-zinc-800 text-zinc-200"
                  : "border-zinc-800 text-zinc-600"
              }`}
            >
              {l}
            </button>
          ))}
        </div>
        <label className="ml-auto flex items-center gap-1.5 text-zinc-500">
          <input
            type="checkbox"
            checked={follow}
            onChange={(e) => setFollow(e.target.checked)}
            className="accent-sky-600"
          />
          follow
        </label>
      </div>

      <div className="max-h-[560px] overflow-y-auto rounded border border-zinc-800 bg-zinc-950/80">
        {groups.length === 0 ? (
          <div className="px-3 py-8 text-center text-sm text-zinc-600">
            {live ? "waiting for the first trace line…" : "no trace events"}
          </div>
        ) : (
          groups.map((g, gi) => (
            <div key={gi} className="border-b border-zinc-900 last:border-0">
              <div className="sticky top-0 z-10 flex items-center gap-2 bg-zinc-950/95 px-2.5 py-1 backdrop-blur">
                <span className={`h-2 w-2 rounded-full ${stageColor(g.stage)}`} />
                <span className="font-mono text-[11px] font-semibold uppercase tracking-widest text-zinc-300">
                  {g.stage}
                </span>
                <span className="font-mono text-[10px] text-zinc-600">
                  {g.items.length} line{g.items.length === 1 ? "" : "s"}
                </span>
                <span className="ml-auto font-mono text-[10px] text-zinc-600">
                  +{clockFromStart(t0, g.items[0].ts)}
                </span>
              </div>
              <ul>
                {g.items.map((e) => {
                  const hasData = e.data && Object.keys(e.data).length > 0;
                  const open = dataOpen.has(e.seq);
                  return (
                    <li
                      key={e.seq}
                      className="flex gap-2 px-2.5 py-1 hover:bg-zinc-900/60"
                    >
                      <span className="w-12 shrink-0 pt-0.5 text-right font-mono text-[10px] text-zinc-700">
                        {e.seq}
                      </span>
                      <span className="w-14 shrink-0 pt-0.5 font-mono text-[10px] text-zinc-600">
                        +{clockFromStart(t0, e.ts)}
                      </span>
                      <div className="min-w-0 flex-1">
                        <span
                          className={`text-[12.5px] leading-snug ${LEVEL_TEXT[e.level] || "text-zinc-300"}`}
                        >
                          {e.message}
                        </span>
                        {hasData && (
                          <button
                            onClick={() =>
                              setDataOpen((prev) => {
                                const next = new Set(prev);
                                if (next.has(e.seq)) next.delete(e.seq);
                                else next.add(e.seq);
                                return next;
                              })
                            }
                            className="ml-2 rounded border border-zinc-800 px-1 font-mono text-[9px] text-zinc-600 hover:text-zinc-300"
                          >
                            data {open ? "▲" : "▼"}
                          </button>
                        )}
                        {open && (
                          <pre className="mt-1 max-h-56 overflow-auto rounded border border-zinc-800 bg-black p-2 font-mono text-[10px] text-zinc-400">
                            {JSON.stringify(e.data, null, 2)}
                          </pre>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
        )}
        <div ref={bottom} />
      </div>
    </div>
  );
}

/** Which stages have been reached, as a one-line pipeline strip. */
export function StageStrip({ events }: { events: TraceEvent[] }) {
  const reached = new Set(events.map((e) => e.stage));
  const last = events[events.length - 1]?.stage;
  return (
    <div className="flex flex-wrap gap-1">
      {STAGE_ORDER.filter((s) => s !== "failed").map((s) => {
        const on = reached.has(s);
        const current = s === last;
        return (
          <span
            key={s}
            className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
              current
                ? "bg-sky-900/70 text-sky-200 ring-1 ring-sky-600"
                : on
                  ? "bg-zinc-800 text-zinc-300"
                  : "bg-zinc-900/60 text-zinc-700"
            }`}
          >
            {s}
          </span>
        );
      })}
    </div>
  );
}
