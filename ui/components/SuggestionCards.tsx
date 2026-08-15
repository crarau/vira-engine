"use client";

/**
 * Product suggestions, as clickable starting points.
 *
 * The product field is the biggest single lever on the eventual score and the
 * hardest thing to arrive knowing — "Selling chips" scored 2.6, naming the
 * mechanism scored 3.8. These cards exist so a user does not have to guess.
 *
 * Two rules the design follows:
 *
 * **Show the receipt.** Every card carries the trend keys it came from, linked
 * to the TikTok they point at. A suggestion whose evidence a user cannot open
 * is indistinguishable from a suggestion a model invented, which is the exact
 * thing this endpoint was built to avoid.
 *
 * **A card is a starting point, not a cage.** Picking one fills the free-text
 * box below rather than replacing it, and the box stays editable.
 */

import { Suggestion, SuggestionsResponse } from "@/lib/api";
import { Badge, Empty, ErrorBox, Ext, Loading } from "@/components/ui";

export function SuggestionCards({
  data,
  loading,
  error,
  activeProduct,
  onPick,
  onRefresh,
  refreshing,
}: {
  data: SuggestionsResponse | null;
  loading: boolean;
  error: unknown;
  activeProduct: string;
  onPick: (s: Suggestion) => void;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  if (loading) return <Loading what="reading the corpus for this company" />;
  if (error) {
    return (
      <ErrorBox
        error={error}
        hint="The product box below still works — this only costs you the shortcuts."
      />
    );
  }
  if (!data) return null;

  const urls = new Map(data.sources.map((s) => [s.trend_key, s]));

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] text-zinc-500">
          Drawn from{" "}
          <span className="text-zinc-300">{data.corpus.slice_size} verified</span>{" "}
          {data.corpus.category || "category"} clips under{" "}
          {data.corpus.max_age_days} days old — the same rows the generator will
          select from. Click one to fill the field and set its lane.
        </p>
        <button
          onClick={onRefresh}
          disabled={refreshing}
          className="shrink-0 rounded border border-zinc-800 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-zinc-500 transition-colors hover:border-zinc-700 hover:text-zinc-300 disabled:opacity-50"
          title="Costs an LLM call — the cached answer is served for an hour"
        >
          {refreshing ? "regenerating…" : "regenerate"}
        </button>
      </div>

      {data.note ? (
        <div className="rounded border border-amber-900/70 bg-amber-950/30 px-2.5 py-1.5 text-[11px] text-amber-200">
          {data.note}
        </div>
      ) : null}

      {data.suggestions.length === 0 ? (
        <Empty>
          Nothing here can be grounded in this corpus. Write the product
          yourself below — and expect the evidence gate to be honest about it.
        </Empty>
      ) : (
        <div className="grid gap-2 md:grid-cols-2">
          {data.suggestions.map((s, i) => {
            const on = s.product === activeProduct;
            return (
              <div
                key={`${s.product}-${i}`}
                className={`rounded-lg border p-2.5 transition-colors ${
                  on
                    ? "border-sky-600 bg-sky-950/30"
                    : "border-zinc-800 bg-zinc-900/40 hover:border-zinc-700"
                }`}
              >
                <button
                  onClick={() => onPick(s)}
                  className="w-full text-left"
                >
                  <div className="text-[13px] font-medium leading-snug text-zinc-100">
                    {s.product}
                  </div>
                  {s.angle ? (
                    <p className="mt-1 text-[11.5px] leading-snug text-zinc-400">
                      {s.angle}
                    </p>
                  ) : null}
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <Badge tone="info">{s.lane}</Badge>
                    {s.lane_reason ? (
                      <span className="text-[11px] text-zinc-500">
                        {s.lane_reason}
                      </span>
                    ) : null}
                  </div>
                </button>

                {s.evidence.length > 0 ? (
                  <ul className="mt-2 space-y-1 border-t border-zinc-800 pt-1.5">
                    {s.evidence.map((e, n) => (
                      <li
                        key={n}
                        className="text-[11px] leading-snug text-zinc-500"
                      >
                        <span className="text-zinc-700">— </span>
                        {e}
                      </li>
                    ))}
                  </ul>
                ) : null}

                {s.grounded_in.length > 0 ? (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {s.grounded_in.map((key) => {
                      const src = urls.get(key);
                      const label = src?.author ? `@${src.author}` : key.slice(-8);
                      // Without a resolvable URL the chip is a claim, so it is
                      // drawn as plain text rather than as a dead link.
                      return src?.source_url ? (
                        <Ext key={key} href={src.source_url}>
                          <span className="font-mono text-[10px]">{label} ↗</span>
                        </Ext>
                      ) : (
                        <span
                          key={key}
                          className="font-mono text-[10px] text-zinc-600"
                        >
                          {label}
                        </span>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** The bio verdict, shown next to the company it is about. */
export function BioVerdict({ data }: { data: SuggestionsResponse | null }) {
  const q = data?.bio_quality;
  if (!q || q.verdict === "usable") return null;
  return (
    <div className="mt-2 rounded border border-amber-900/70 bg-amber-950/30 px-2.5 py-1.5 text-[11px] text-amber-200">
      <Badge tone={q.verdict === "junk" ? "bad" : "warn"}>
        bio · {q.verdict}
      </Badge>{" "}
      {q.reason} ({q.words} words). Suggestions below lean on the{" "}
      {data?.corpus.category || "category"} corpus instead.
    </div>
  );
}
