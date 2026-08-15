"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { defaultProduct } from "@/lib/defaults";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Company,
  CorpusCompany,
  createVideo,
  getCompanies,
  getCorpusCompanies,
  getLanes,
  getSuggestions,
  Lane,
  Suggestion,
  SuggestionsResponse,
} from "@/lib/api";
import { ago } from "@/lib/format";
import { rememberJob, recentJobs, RecentJob } from "@/lib/recent";
import {
  Badge,
  Empty,
  ErrorBox,
  Internal,
  Loading,
  Panel,
} from "@/components/ui";
import { BioVerdict, SuggestionCards } from "@/components/SuggestionCards";

const ETA: Record<string, number> = { fast: 90, agentic: 360 };

export default function GeneratePage() {
  return (
    <Suspense fallback={<Loading what="loading" />}>
      <Generate />
    </Suspense>
  );
}

function Generate() {
  const router = useRouter();
  const params = useSearchParams();

  const [companies, setCompanies] = useState<Company[] | null>(null);
  const [corpusCompanies, setCorpusCompanies] = useState<CorpusCompany[]>([]);
  const [lanes, setLanes] = useState<Lane[] | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const [slug, setSlug] = useState(params.get("company") || "");
  const [product, setProduct] = useState(params.get("product") || "");
  // Only auto-fill while the field is still ours. The moment the user types,
  // switching company must not wipe what they wrote.
  const [productTouched, setProductTouched] = useState(
    Boolean(params.get("product"))
  );
  const [lane, setLane] = useState("founder-story");
  const [mode, setMode] = useState<"fast" | "agentic">("fast");
  const [submitting, setSubmitting] = useState(false);
  const [submitErr, setSubmitErr] = useState<unknown>(null);
  const [recent, setRecent] = useState<RecentJob[]>([]);

  const [suggestions, setSuggestions] = useState<SuggestionsResponse | null>(null);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [suggestErr, setSuggestErr] = useState<unknown>(null);
  const [regenerating, setRegenerating] = useState(false);

  useEffect(() => {
    getCompanies().then(setCompanies).catch(setErr);
    getLanes().then(setLanes).catch(setErr);
    // The Lovable-side list is richer (category, enrichment) but optional; the
    // local list is what POST /v1/videos actually resolves against.
    getCorpusCompanies()
      .then(setCorpusCompanies)
      .catch(() => setCorpusCompanies([]));
    setRecent(recentJobs());
  }, []);

  /**
   * Suggestions are per company and cost a ~35s LLM call on a cold cache, so
   * this fires on the company change and nothing else. The cancelled flag is
   * what stops a slow first company from overwriting a fast second one.
   */
  useEffect(() => {
    if (!slug) {
      setSuggestions(null);
      setSuggestErr(null);
      return;
    }
    let cancelled = false;
    setSuggestions(null);
    setSuggestErr(null);
    setSuggestLoading(true);
    getSuggestions(slug)
      .then((d) => !cancelled && setSuggestions(d))
      .catch((e) => !cancelled && setSuggestErr(e))
      .finally(() => !cancelled && setSuggestLoading(false));
    return () => {
      cancelled = true;
    };
  }, [slug]);

  /** A suggestion is a starting point: it fills the box, it does not lock it. */
  function pickSuggestion(s: Suggestion) {
    setProduct(s.product);
    setProductTouched(true);
    if (s.lane) setLane(s.lane);
  }

  async function regenerateSuggestions() {
    if (!slug || regenerating) return;
    setRegenerating(true);
    setSuggestErr(null);
    try {
      setSuggestions(await getSuggestions(slug, true));
    } catch (e) {
      setSuggestErr(e);
    } finally {
      setRegenerating(false);
    }
  }

  /** Local companies, annotated with whatever the corpus knows about them. */
  const merged = useMemo(() => {
    const byslug = new Map(corpusCompanies.map((c) => [c.slug, c]));
    const rows = (companies || []).map((c) => ({
      ...c,
      corpus: byslug.get(c.slug),
    }));
    // Companies that exist upstream but were never mirrored locally are still
    // generatable — the worker copies the row down on first use.
    for (const c of corpusCompanies) {
      if (!rows.some((r) => r.slug === c.slug)) {
        rows.push({
          id: c.id || null,
          slug: c.slug,
          name: c.name,
          category: c.category || c.category_slug || "",
          bio: c.bio || "",
          mission: c.mission || "",
          website: c.website || null,
          video_count: null,
          corpus: c,
        });
      }
    }
    return rows.sort((a, b) => a.name.localeCompare(b.name));
  }, [companies, corpusCompanies]);

  const selected = merged.find((c) => c.slug === slug);
  const chosenLane = (lanes || []).find((l) => l.name === lane);
  const canSubmit = slug && product.trim().length >= 2 && !submitting;

  async function submit() {
    setSubmitErr(null);
    setSubmitting(true);
    try {
      const job = await createVideo({
        company_slug: slug,
        product: product.trim(),
        lane,
        mode,
      });
      rememberJob({
        job_id: job.job_id,
        company_slug: slug,
        product: product.trim(),
        lane,
        mode,
        started_at: new Date().toISOString(),
      });
      router.push(`/jobs/${job.job_id}`);
    } catch (e) {
      setSubmitErr(e);
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-lg font-semibold text-zinc-100">Generate</h1>
        <p className="text-[12px] text-zinc-500">
          POST <code className="text-zinc-400">/v1/videos</code> returns 202 with
          a job id. Generation is {ETA.fast}s deterministic, ~{ETA.agentic}s with
          the crew — nothing blocks on it.
        </p>
      </header>

      {err ? <ErrorBox error={err} /> : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-4">
          <Panel title="1 · Company">
            {!companies && !corpusCompanies.length ? (
              <Loading what="reading companies" />
            ) : merged.length === 0 ? (
              <Empty>
                No companies. POST /v1/companies or run{" "}
                <code>python new_company.py</code>.
              </Empty>
            ) : (
              <div className="grid max-h-[280px] gap-1.5 overflow-y-auto pr-1 sm:grid-cols-2">
                {merged.map((c) => {
                  const on = c.slug === slug;
                  return (
                    <button
                      key={c.slug}
                      onClick={() => {
                        setSlug(c.slug);
                        if (!productTouched) setProduct(defaultProduct(c));
                      }}
                      className={`rounded border px-2.5 py-2 text-left transition-colors ${
                        on
                          ? "border-sky-600 bg-sky-950/40"
                          : "border-zinc-800 bg-zinc-900/40 hover:border-zinc-700"
                      }`}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-[13px] font-medium text-zinc-100">
                          {c.name || c.slug}
                        </span>
                        {c.category && <Badge tone="violet">{c.category}</Badge>}
                      </div>
                      <div className="truncate font-mono text-[10px] text-zinc-600">
                        {c.slug}
                      </div>
                      {c.bio ? (
                        <div className="mt-0.5 line-clamp-2 text-[11px] text-zinc-500">
                          {c.bio}
                        </div>
                      ) : (
                        <div className="mt-0.5 text-[11px] text-amber-500">
                          no bio — thin input scores low
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
            <BioVerdict data={suggestions} />
          </Panel>

          <Panel
            title="2 · Start from the corpus"
            right={
              suggestions && !suggestLoading ? (
                <span className="font-mono text-[10px] uppercase tracking-wide text-zinc-600">
                  {suggestions.cached ? "cached" : `${suggestions.elapsed_ms}ms`}
                </span>
              ) : null
            }
          >
            {!slug ? (
              <Empty>Pick a company and the corpus will propose angles.</Empty>
            ) : (
              <SuggestionCards
                data={suggestions}
                loading={suggestLoading}
                error={suggestErr}
                activeProduct={product}
                onPick={pickSuggestion}
                onRefresh={regenerateSuggestions}
                refreshing={regenerating}
              />
            )}
          </Panel>

          <Panel title="3 · Product">
            <input
              value={product}
              onChange={(e) => {
                setProduct(e.target.value);
                setProductTouched(e.target.value.trim().length > 0);
              }}
              placeholder="e.g. a slow-release treat dispenser for anxious dogs"
              className="w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-sky-600 focus:outline-none"
            />
            <p className="mt-1.5 text-[11px] text-zinc-500">
              Input quality is the biggest single lever on the score. &ldquo;Selling
              chips&rdquo; scores 2.6; naming the mechanism scores 3.8.
            </p>
          </Panel>

          <Panel title="4 · Lane">
            {!lanes ? (
              <Loading what="reading /v1/lanes" />
            ) : (
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {lanes.map((l) => {
                  const on = l.name === lane;
                  return (
                    <button
                      key={l.name}
                      onClick={() => setLane(l.name)}
                      className={`rounded-lg border p-2.5 text-left transition-colors ${
                        on
                          ? "border-sky-600 bg-sky-950/30"
                          : "border-zinc-800 bg-zinc-900/40 hover:border-zinc-700"
                      }`}
                    >
                      <div className="font-mono text-[12px] font-semibold text-zinc-100">
                        {l.name}
                      </div>
                      <p className="mt-1 text-[11.5px] leading-snug text-zinc-400">
                        {l.brief}
                      </p>
                      <div className="mt-2 space-y-1 border-t border-zinc-800 pt-1.5">
                        <div className="text-[11px]">
                          <span className="text-zinc-600">voice · </span>
                          <span className="text-emerald-400">{l.voice_note}</span>
                        </div>
                        <div className="text-[11px]">
                          <span className="text-zinc-600">look · </span>
                          <span className="text-zinc-500">{l.look}</span>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </Panel>

          <Panel title="5 · Mode">
            <div className="grid gap-2 sm:grid-cols-2">
              {(["fast", "agentic"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`rounded border p-2.5 text-left ${
                    mode === m
                      ? "border-sky-600 bg-sky-950/30"
                      : "border-zinc-800 bg-zinc-900/40 hover:border-zinc-700"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[12px] font-semibold text-zinc-100">
                      {m}
                    </span>
                    <Badge tone={m === "fast" ? "good" : "info"}>
                      ~{ETA[m]}s
                    </Badge>
                  </div>
                  <p className="mt-1 text-[11.5px] text-zinc-500">
                    {m === "fast"
                      ? "Deterministic pipeline: select → verify → analyze → write → voice ‖ imagery → score → render."
                      : "Director-led crew. Adds motion authoring, a hostile critique pass, and a cohesion check that regenerates frames that contradict their beat."}
                  </p>
                </button>
              ))}
            </div>
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title="Launch">
            <dl className="space-y-1 text-[12px]">
              <Row k="company" v={selected ? selected.name : "—"} />
              <Row k="slug" v={slug || "—"} mono />
              <Row k="product" v={product || "—"} />
              <Row k="lane" v={lane} mono />
              <Row k="mode" v={mode} mono />
              <Row k="eta" v={`~${ETA[mode]}s`} mono />
            </dl>

            {chosenLane && (
              <div className="mt-3 rounded border border-zinc-800 bg-zinc-950/60 p-2 text-[11px] text-zinc-400">
                <span className="text-zinc-600">voice · </span>
                {chosenLane.voice_note}
              </div>
            )}

            <button
              disabled={!canSubmit}
              onClick={submit}
              className="mt-3 w-full rounded bg-sky-600 py-2 text-sm font-semibold text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-600"
            >
              {submitting ? "posting…" : "Generate video"}
            </button>
            {submitErr ? (
              <div className="mt-2">
                <ErrorBox error={submitErr} />
              </div>
            ) : null}
            <p className="mt-2 text-[11px] text-zinc-600">
              The evidence gate runs server-side after generation, always. No
              parameter here can skip it or move its threshold.
            </p>
          </Panel>

          <Panel title="Jobs started from this browser">
            {recent.length === 0 ? (
              <Empty>Nothing yet.</Empty>
            ) : (
              <ul className="space-y-1">
                {recent.map((j) => (
                  <li key={j.job_id} className="text-[11.5px]">
                    <Internal href={`/jobs/${j.job_id}`} className="font-mono">
                      {j.job_id.slice(0, 8)}
                    </Internal>
                    <span className="text-zinc-500">
                      {" "}
                      · {j.company_slug} · {j.lane} · {j.mode} · {ago(j.started_at)}
                    </span>
                    <div className="truncate text-zinc-600">{j.product}</div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex gap-2 border-b border-zinc-800/60 py-1 last:border-0">
      <dt className="w-20 shrink-0 text-zinc-500">{k}</dt>
      <dd
        className={`min-w-0 flex-1 break-words text-zinc-200 ${mono ? "font-mono text-[11.5px]" : ""}`}
      >
        {v}
      </dd>
    </div>
  );
}
