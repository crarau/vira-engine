"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  CORPUS_PAGE_MAX,
  CorpusCategory,
  CorpusCompany,
  CorpusInsights,
  CorpusStats,
  CorpusTrend,
  GATE,
  getCorpusCategories,
  getCorpusCompanies,
  getCorpusStats,
  getCorpusTrends,
  TrendOrder,
  TrendsPage,
} from "@/lib/api";
import { ageDays, num } from "@/lib/format";
import { AgeHistogram, CorpusAgeBands } from "@/components/AgeHistogram";
import { TrendCard } from "@/components/TrendCard";
import {
  Badge,
  Empty,
  ErrorBox,
  Ext,
  Internal,
  Loading,
  Panel,
  Select,
  Stat,
  Tabs,
} from "@/components/ui";

/** Enrichment arrives flattened, but a nested embed is tolerated. */
function insightsOf(c: CorpusCompany): CorpusInsights {
  const nested = c.insights ?? c.company_insights ?? null;
  const one = (Array.isArray(nested) ? nested[0] : nested) || {};
  return {
    summary: c.summary ?? one.summary ?? null,
    positioning: c.positioning ?? one.positioning ?? null,
    tone: c.tone ?? one.tone ?? null,
    keywords: c.keywords ?? one.keywords ?? null,
    ad_themes: c.ad_themes ?? one.ad_themes ?? null,
  };
}

/**
 * "Did enrichment actually run?"
 *
 * The API sends an `enriched` flag, but it disagrees with the payload often
 * enough to matter — a row can carry a full positioning paragraph and still
 * read `enriched: false`. Both are shown: the flag as the API reports it, and
 * whether there is in fact anything there.
 */
function hasInsightContent(i: CorpusInsights): boolean {
  return Boolean(
    i.summary ||
      i.positioning ||
      i.tone ||
      (i.keywords && i.keywords.length) ||
      (i.ad_themes && i.ad_themes.length),
  );
}

export default function CorpusPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Corpus />
    </Suspense>
  );
}

function Corpus() {
  const params = useSearchParams();
  const [tab, setTab] = useState<"trends" | "companies">("trends");

  const [companies, setCompanies] = useState<CorpusCompany[] | null>(null);
  const [categories, setCategories] = useState<CorpusCategory[]>([]);
  const [stats, setStats] = useState<CorpusStats | null>(null);
  const [page, setPage] = useState<TrendsPage | null>(null);

  const [cErr, setCErr] = useState<unknown>(null);
  const [tErr, setTErr] = useState<unknown>(null);
  const [loadingTrends, setLoadingTrends] = useState(true);

  // Server-side controls. With a 200-row cap over thousands, sorting or
  // date-filtering in the browser would operate on the wrong 200 rows.
  const [category, setCategory] = useState(params.get("category") || "all");
  const [order, setOrder] = useState<TrendOrder>("trend_score");
  const [freshOnly, setFreshOnly] = useState(false);
  const [limit, setLimit] = useState(CORPUS_PAGE_MAX);

  // Client-side, over the loaded page only.
  const [q, setQ] = useState("");
  const [show, setShow] = useState(60);

  useEffect(() => {
    getCorpusCompanies().then(setCompanies).catch(setCErr);
    getCorpusCategories()
      .then(setCategories)
      .catch(() => setCategories([]));
    getCorpusStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  useEffect(() => {
    let alive = true;
    setLoadingTrends(true);
    setTErr(null);
    getCorpusTrends({
      category: category === "all" ? undefined : category,
      order,
      maxAgeDays: freshOnly ? GATE.max_age_days : null,
      limit,
    })
      .then((p) => alive && setPage(p))
      .catch((e) => alive && setTErr(e))
      .finally(() => alive && setLoadingTrends(false));
    setShow(60);
    return () => {
      alive = false;
    };
  }, [category, order, freshOnly, limit]);

  const items = page?.items || [];

  const filtered = useMemo(() => {
    if (!q.trim()) return items;
    const needle = q.trim().toLowerCase();
    return items.filter((t) =>
      [t.caption, t.author, t.format, t.trend_key, (t.hashtags || []).join(" ")]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [items, q]);

  const ages = useMemo(
    () => items.map((t) => ageDays(t.posted_at, t.age_days)),
    [items],
  );
  const staleShown = items.filter((t) => isStale(t)).length;
  const noSource = items.filter((t) => !t.source_url).length;
  const noCover = items.filter((t) => !t.thumbnail).length;

  const catOptions = [
    { value: "all", label: `all categories (${num(stats?.trends_total ?? 0)})` },
    ...categories.map((c) => ({
      value: c.slug || "",
      label: `${c.name} (${num(c.trend_count ?? 0)})`,
    })),
  ];

  const enrichedFlag = (companies || []).filter((c) => c.enriched).length;
  const enrichedReal = (companies || []).filter((c) =>
    hasInsightContent(insightsOf(c)),
  ).length;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Corpus</h1>
          <p className="text-[12px] text-zinc-500">
            What the engine has to ground an ad in. Lovable Cloud owns these
            rows; the engine reads them and never writes back.
          </p>
        </div>
        <Tabs
          tabs={[
            { id: "trends", label: `Trends (${num(stats?.trends_total ?? items.length)})` },
            { id: "companies", label: `Companies (${companies ? companies.length : "…"})` },
          ]}
          active={tab}
          onChange={setTab}
        />
      </header>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Stat
          label="trends in corpus"
          value={stats ? num(stats.trends_total ?? 0) : "…"}
          sub={`page caps at ${CORPUS_PAGE_MAX}`}
        />
        <Stat
          label={`fresh ≤${GATE.max_age_days}d`}
          value={stats ? num(stats.fresh_90d ?? 0) : "…"}
          tone={stats && (stats.usable_share_90d ?? 0) >= 0.5 ? "good" : "warn"}
          sub={
            stats
              ? `${Math.round((stats.usable_share_90d ?? 0) * 100)}% of the corpus`
              : undefined
          }
        />
        <Stat
          label="fresh ≤30d"
          value={stats ? num(stats.fresh_30d ?? 0) : "…"}
          tone="good"
          sub="the genuinely current slice"
        />
        <Stat
          label={`stale >${GATE.max_age_days}d`}
          value={
            stats ? num((stats.trends_total ?? 0) - (stats.fresh_90d ?? 0)) : "…"
          }
          sub="filtered out before selection"
        />
        <Stat
          label="companies"
          value={companies ? num(companies.length) : "…"}
          sub={`${num(enrichedReal)} have insights`}
        />
        <Stat
          label="categories"
          value={categories.length ? num(categories.length) : "…"}
          sub="mapped to trends"
        />
      </div>

      {tab === "trends" && (
        <>
          <Panel title="Age distribution — whole corpus">
            {!stats ? (
              <Loading what="reading /v1/corpus/stats" />
            ) : (
              <CorpusAgeBands stats={stats} />
            )}
            {stats?.by_category && stats.by_category.length > 0 && (
              <div className="mt-3 border-t border-zinc-800 pt-2">
                <div className="mb-1 text-[10px] uppercase tracking-widest text-zinc-500">
                  trends mapped per category
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {stats.by_category.map((c) => (
                    <button
                      key={c.slug}
                      onClick={() => setCategory(c.slug)}
                      className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${
                        category === c.slug
                          ? "border-sky-600 bg-sky-950/50 text-sky-200"
                          : "border-zinc-800 text-zinc-400 hover:border-zinc-700"
                      }`}
                    >
                      {c.name} · {num(c.mapped)}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </Panel>

          <Panel
            title={`Age distribution — this page (${num(items.length)} rows, ordered by ${page?.order || order})`}
          >
            {loadingTrends ? (
              <Loading what="reading trends" />
            ) : tErr ? (
              <ErrorBox error={tErr} />
            ) : items.length === 0 ? (
              <Empty>No trends returned.</Empty>
            ) : (
              <AgeHistogram ages={ages} />
            )}
          </Panel>

          <Panel
            title={`Trends — ${num(filtered.length)} shown`}
            right={
              <div className="flex flex-wrap items-center gap-2">
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="search this page…"
                  className="w-48 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-sky-600 focus:outline-none"
                />
                <Select
                  label="cat"
                  value={category}
                  onChange={setCategory}
                  options={catOptions}
                />
                <Select
                  label="order"
                  value={order}
                  onChange={(v) => setOrder(v as TrendOrder)}
                  options={[
                    { value: "trend_score", label: "trend_score" },
                    { value: "views", label: "views" },
                    { value: "posted_at", label: "newest" },
                  ]}
                />
                <Select
                  label="limit"
                  value={String(limit)}
                  onChange={(v) => setLimit(Number(v))}
                  options={[
                    { value: "60", label: "60" },
                    { value: "120", label: "120" },
                    { value: "200", label: "200 (max)" },
                  ]}
                />
                <label className="flex items-center gap-1.5 text-[11px] text-zinc-500">
                  <input
                    type="checkbox"
                    checked={freshOnly}
                    onChange={(e) => setFreshOnly(e.target.checked)}
                    className="accent-sky-600"
                  />
                  only ≤{GATE.max_age_days}d
                </label>
              </div>
            }
          >
            <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500">
              <span>
                <b className="font-mono text-amber-400">{num(staleShown)}</b> of
                the loaded rows are stale
              </span>
              <span>
                <b className="font-mono text-zinc-300">{num(noCover)}</b> without a
                cover image
              </span>
              <span className={noSource ? "text-rose-400" : ""}>
                <b className="font-mono">{num(noSource)}</b> without a source_url
              </span>
              {page?.note && (
                <span className="text-amber-400">note: {page.note}</span>
              )}
              {freshOnly && (
                <span className="text-emerald-400">
                  server-side max_age_days={GATE.max_age_days} applied
                </span>
              )}
            </div>

            {loadingTrends ? (
              <Loading what="reading trends" />
            ) : tErr ? (
              <ErrorBox error={tErr} />
            ) : filtered.length === 0 ? (
              <Empty>Nothing matches.</Empty>
            ) : (
              <>
                <div className="grid gap-2 xl:grid-cols-2">
                  {filtered.slice(0, show).map((t) => (
                    <TrendCard key={t.trend_key} t={t} />
                  ))}
                </div>
                {filtered.length > show && (
                  <button
                    onClick={() => setShow((s) => s + 60)}
                    className="mt-3 w-full rounded border border-zinc-800 py-2 text-xs text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
                  >
                    show 60 more · {num(filtered.length - show)} remaining on this
                    page
                  </button>
                )}
                {limit < CORPUS_PAGE_MAX && filtered.length >= limit && (
                  <div className="mt-2 text-center text-[11px] text-zinc-600">
                    This is a {limit}-row page of {num(page?.total_in_corpus ?? 0)}.
                    Raise the limit or narrow the category.
                  </div>
                )}
              </>
            )}
          </Panel>
        </>
      )}

      {tab === "companies" && (
        <Panel
          title="Companies in the Lovable database"
          right={
            <span className="text-[11px] text-zinc-500">
              <b className="font-mono text-zinc-300">{num(enrichedReal)}</b> carry
              insights · API flags{" "}
              <b className="font-mono text-zinc-300">{num(enrichedFlag)}</b> as
              enriched
            </span>
          }
        >
          {cErr ? (
            <ErrorBox error={cErr} />
          ) : !companies ? (
            <Loading what="reading companies" />
          ) : companies.length === 0 ? (
            <Empty>No companies upstream.</Empty>
          ) : (
            <div className="space-y-2">
              {companies.map((c) => (
                <CompanyRow key={c.slug} c={c} categories={categories} />
              ))}
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}

function isStale(t: CorpusTrend): boolean {
  if (typeof t.stale === "boolean") return t.stale;
  const a = ageDays(t.posted_at, t.age_days);
  return a === null || a > GATE.max_age_days;
}

function CompanyRow({
  c,
  categories,
}: {
  c: CorpusCompany;
  categories: CorpusCategory[];
}) {
  const i = insightsOf(c);
  const real = hasInsightContent(i);
  const cat = categories.find(
    (x) => x.slug === c.category_slug || x.name === c.category,
  );

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-zinc-100">{c.name || c.slug}</span>
        <code className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-[10px] text-zinc-400">
          {c.slug}
        </code>
        {c.category && <Badge tone="violet">{c.category}</Badge>}
        {real ? (
          <Badge tone="good" title="positioning / keywords / ad_themes present">
            insights present
          </Badge>
        ) : (
          <Badge
            tone="warn"
            title="no enrichment content — the engine gets bio + mission only"
          >
            no insights
          </Badge>
        )}
        {c.enriched === false && real && (
          <Badge
            tone="bad"
            title="the API reports enriched:false, yet the row carries insight content"
          >
            flag says not enriched
          </Badge>
        )}
        {c.enriched === true && <Badge tone="info">enriched: true</Badge>}
        {c.status && c.status !== "published" && (
          <Badge tone="neutral">{c.status}</Badge>
        )}
        {cat && (
          <span className="font-mono text-[11px] text-zinc-500">
            {num(cat.trend_count ?? 0)} trends in {cat.slug}
          </span>
        )}
        <div className="ml-auto flex items-center gap-3 text-[11px]">
          {c.website && (
            <Ext href={c.website}>{c.website.replace(/^https?:\/\//, "")}</Ext>
          )}
          {c.category_slug && (
            <Internal href={`/corpus?category=${c.category_slug}`}>
              its corpus
            </Internal>
          )}
          <Internal href={`/?company=${encodeURIComponent(c.slug)}`}>
            generate →
          </Internal>
          <Internal href={`/videos?company=${encodeURIComponent(c.slug)}`}>
            videos →
          </Internal>
        </div>
      </div>

      <div className="mt-1.5 grid gap-x-6 gap-y-1 text-[12px] md:grid-cols-2">
        <div>
          <span className="text-zinc-500">bio </span>
          <span className="text-zinc-300">
            {c.bio || (
              <em className="text-rose-400">empty — thin input scores ~2.6</em>
            )}
          </span>
        </div>
        <div>
          <span className="text-zinc-500">mission </span>
          <span className="text-zinc-300">
            {c.mission || <em className="text-zinc-600">empty</em>}
          </span>
        </div>
        {c.owner_name && (
          <div>
            <span className="text-zinc-500">owner </span>
            <span className="font-mono text-zinc-400">{c.owner_name}</span>
          </div>
        )}
      </div>

      {real && (
        <div className="mt-2 grid gap-x-6 gap-y-1 rounded border border-zinc-800 bg-zinc-950/60 p-2 text-[11.5px] md:grid-cols-2">
          {i.positioning && (
            <div className="md:col-span-2">
              <span className="text-zinc-500">positioning </span>
              <span className="text-zinc-300">{i.positioning}</span>
            </div>
          )}
          {i.tone && (
            <div>
              <span className="text-zinc-500">tone </span>
              <span className="text-zinc-300">{i.tone}</span>
            </div>
          )}
          {i.keywords && i.keywords.length > 0 && (
            <div>
              <span className="text-zinc-500">keywords </span>
              <span className="font-mono text-zinc-400">
                {i.keywords.join(", ")}
              </span>
            </div>
          )}
          {i.ad_themes && i.ad_themes.length > 0 && (
            <div className="md:col-span-2">
              <span className="text-zinc-500">ad themes </span>
              <span className="font-mono text-zinc-400">
                {i.ad_themes.join(" · ")}
              </span>
            </div>
          )}
          {i.summary && (
            <div className="md:col-span-2">
              <span className="text-zinc-500">summary </span>
              <span className="text-zinc-300">{i.summary}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
