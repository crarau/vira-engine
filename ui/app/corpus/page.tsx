"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CorpusCompany,
  CorpusInsights,
  CorpusStats,
  CorpusTrend,
  GATE,
  getCorpusCompanies,
  getCorpusStats,
  getCorpusTrends,
} from "@/lib/api";
import { ageDays, num } from "@/lib/format";
import { AgeHistogram } from "@/components/AgeHistogram";
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

type Sort = "score" | "views" | "recent" | "engagement";

/** PostgREST embeds arrive as arrays; a flattened API may send an object. */
function insightsOf(c: CorpusCompany): CorpusInsights | null {
  const raw = c.insights ?? c.company_insights ?? null;
  if (!raw) return null;
  const one = Array.isArray(raw) ? raw[0] : raw;
  return one || null;
}

function isEnriched(c: CorpusCompany): boolean {
  if (typeof c.enriched === "boolean") return c.enriched;
  const i = insightsOf(c);
  if (!i) return false;
  return Boolean(
    i.summary ||
      i.positioning ||
      i.tone ||
      (i.keywords && i.keywords.length) ||
      (i.ad_themes && i.ad_themes.length),
  );
}

export default function CorpusPage() {
  const [tab, setTab] = useState<"trends" | "companies">("trends");
  const [companies, setCompanies] = useState<CorpusCompany[] | null>(null);
  const [trends, setTrends] = useState<CorpusTrend[] | null>(null);
  const [stats, setStats] = useState<CorpusStats | null>(null);
  const [cErr, setCErr] = useState<unknown>(null);
  const [tErr, setTErr] = useState<unknown>(null);

  const [category, setCategory] = useState("all");
  const [sort, setSort] = useState<Sort>("score");
  const [freshness, setFreshness] = useState<"all" | "fresh" | "stale">("all");
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(60);

  useEffect(() => {
    getCorpusCompanies().then(setCompanies).catch(setCErr);
    getCorpusTrends({ limit: 2000 }).then(setTrends).catch(setTErr);
    // Stats are a bonus tile row: everything below is derived client-side, so
    // a missing endpoint costs nothing.
    getCorpusStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const t of trends || []) {
      const c = t.category || t.category_slug;
      if (c) set.add(c);
    }
    for (const c of companies || []) {
      const v = c.category || c.category_slug;
      if (v) set.add(v);
    }
    return [...set].sort();
  }, [trends, companies]);

  const inCategory = useMemo(() => {
    if (!trends) return [];
    if (category === "all") return trends;
    return trends.filter(
      (t) => t.category === category || t.category_slug === category,
    );
  }, [trends, category]);

  const filtered = useMemo(() => {
    let rows = inCategory;
    if (freshness !== "all") {
      rows = rows.filter((t) => {
        const a = ageDays(t.posted_at, t.age_days);
        const stale = a === null || a > GATE.max_age_days;
        return freshness === "stale" ? stale : !stale;
      });
    }
    if (q.trim()) {
      const needle = q.trim().toLowerCase();
      rows = rows.filter((t) =>
        [t.caption, t.title, t.author, t.format, t.trend_key, (t.hashtags || []).join(" ")]
          .join(" ")
          .toLowerCase()
          .includes(needle),
      );
    }
    const by: Record<Sort, (a: CorpusTrend, b: CorpusTrend) => number> = {
      score: (a, b) => (b.trend_score ?? 0) - (a.trend_score ?? 0),
      views: (a, b) => (b.views ?? 0) - (a.views ?? 0),
      engagement: (a, b) => (b.engagement_rate ?? 0) - (a.engagement_rate ?? 0),
      recent: (a, b) =>
        (ageDays(a.posted_at, a.age_days) ?? 1e9) -
        (ageDays(b.posted_at, b.age_days) ?? 1e9),
    };
    return [...rows].sort(by[sort]);
  }, [inCategory, freshness, q, sort]);

  const ages = useMemo(
    () => inCategory.map((t) => ageDays(t.posted_at, t.age_days)),
    [inCategory],
  );

  const freshCount = ages.filter((a) => a !== null && a <= GATE.max_age_days).length;
  const enrichedCount = (companies || []).filter(isEnriched).length;
  const noSource = inCategory.filter((t) => !t.source_url).length;

  const missingHint = (
    <>
      This UI assumes <code className="text-zinc-300">/v1/corpus/*</code> exists.
      If the shape differs, the client is in{" "}
      <code className="text-zinc-300">ui/lib/api.ts</code> — it accepts a bare
      array or an <code>items</code>/<code>data</code>/<code>trends</code>{" "}
      envelope.
    </>
  );

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
            {
              id: "trends",
              label: `Trends${trends ? ` (${num(trends.length)})` : ""}`,
            },
            {
              id: "companies",
              label: `Companies${companies ? ` (${num(companies.length)})` : ""}`,
            },
          ]}
          active={tab}
          onChange={setTab}
        />
      </header>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Stat
          label="trends loaded"
          value={trends ? num(trends.length) : "…"}
          sub={
            stats?.trends_total
              ? `${num(stats.trends_total)} in the corpus`
              : "client-side window"
          }
        />
        <Stat
          label={`fresh ≤${GATE.max_age_days}d`}
          value={trends ? num(freshCount) : "…"}
          tone={freshCount === 0 && trends ? "bad" : "good"}
          sub={
            inCategory.length
              ? `${Math.round((freshCount / inCategory.length) * 100)}% of shown`
              : undefined
          }
        />
        <Stat
          label={`stale >${GATE.max_age_days}d`}
          value={trends ? num(inCategory.length - freshCount) : "…"}
          tone="warn"
          sub="never reaches a prompt"
        />
        <Stat
          label="no source_url"
          value={trends ? num(noSource) : "…"}
          tone={noSource ? "bad" : "default"}
          sub="dropped at selection"
        />
        <Stat
          label="companies"
          value={companies ? num(companies.length) : "…"}
          sub={`${num(enrichedCount)} enriched`}
        />
        <Stat
          label="categories"
          value={categories.length ? num(categories.length) : "…"}
          sub="seen in loaded rows"
        />
      </div>

      {tab === "trends" && (
        <>
          <Panel title="Age distribution">
            {tErr ? (
              <ErrorBox error={tErr} hint={missingHint} />
            ) : !trends ? (
              <Loading what="reading the corpus" />
            ) : (
              <AgeHistogram ages={ages} />
            )}
          </Panel>

          <Panel
            title={`Trends — ${num(filtered.length)} match`}
            right={
              <div className="flex flex-wrap items-center gap-2">
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="search caption, author, tag…"
                  className="w-56 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-sky-600 focus:outline-none"
                />
                <Select
                  label="cat"
                  value={category}
                  onChange={setCategory}
                  options={[
                    { value: "all", label: `all (${categories.length})` },
                    ...categories.map((c) => ({ value: c, label: c })),
                  ]}
                />
                <Select
                  label="age"
                  value={freshness}
                  onChange={(v) => setFreshness(v as typeof freshness)}
                  options={[
                    { value: "all", label: "all" },
                    { value: "fresh", label: `fresh ≤${GATE.max_age_days}d` },
                    { value: "stale", label: `stale >${GATE.max_age_days}d` },
                  ]}
                />
                <Select
                  label="sort"
                  value={sort}
                  onChange={(v) => setSort(v as Sort)}
                  options={[
                    { value: "score", label: "trend_score" },
                    { value: "views", label: "views" },
                    { value: "engagement", label: "engagement" },
                    { value: "recent", label: "newest" },
                  ]}
                />
              </div>
            }
          >
            {tErr ? (
              <ErrorBox error={tErr} hint={missingHint} />
            ) : !trends ? (
              <Loading what="reading the corpus" />
            ) : filtered.length === 0 ? (
              <Empty>Nothing matches those filters.</Empty>
            ) : (
              <>
                <div className="grid gap-2 xl:grid-cols-2">
                  {filtered.slice(0, limit).map((t) => (
                    <TrendCard key={t.trend_key || t.source_url} t={t} />
                  ))}
                </div>
                {filtered.length > limit && (
                  <button
                    onClick={() => setLimit((l) => l + 60)}
                    className="mt-3 w-full rounded border border-zinc-800 py-2 text-xs text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
                  >
                    show 60 more · {num(filtered.length - limit)} remaining
                  </button>
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
            <Select
              label="cat"
              value={category}
              onChange={setCategory}
              options={[
                { value: "all", label: "all" },
                ...categories.map((c) => ({ value: c, label: c })),
              ]}
            />
          }
        >
          {cErr ? (
            <ErrorBox error={cErr} hint={missingHint} />
          ) : !companies ? (
            <Loading what="reading companies" />
          ) : (
            <div className="space-y-2">
              {companies
                .filter(
                  (c) =>
                    category === "all" ||
                    c.category === category ||
                    c.category_slug === category,
                )
                .map((c) => (
                  <CompanyRow key={c.slug} c={c} trends={trends} />
                ))}
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}

function CompanyRow({
  c,
  trends,
}: {
  c: CorpusCompany;
  trends: CorpusTrend[] | null;
}) {
  const i = insightsOf(c);
  const enriched = isEnriched(c);
  const cat = c.category || c.category_slug || "";
  const matching =
    typeof c.trend_count === "number"
      ? c.trend_count
      : trends && cat
        ? trends.filter((t) => t.category === cat || t.category_slug === cat).length
        : null;
  const fresh =
    trends && cat
      ? trends.filter((t) => {
          if (t.category !== cat && t.category_slug !== cat) return false;
          const a = ageDays(t.posted_at, t.age_days);
          return a !== null && a <= GATE.max_age_days;
        }).length
      : null;

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-zinc-100">{c.name || c.slug}</span>
        <code className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-[10px] text-zinc-400">
          {c.slug}
        </code>
        {cat && <Badge tone="violet">{cat}</Badge>}
        {enriched ? (
          <Badge tone="good">enriched</Badge>
        ) : (
          <Badge tone="warn" title="no company_insights row — the engine gets bio + mission only">
            not enriched
          </Badge>
        )}
        {c.status && c.status !== "published" && (
          <Badge tone="neutral">{c.status}</Badge>
        )}
        {matching !== null && (
          <span className="font-mono text-[11px] text-zinc-500">
            {num(matching)} category trends
            {fresh !== null && (
              <>
                {" · "}
                <span className={fresh ? "text-emerald-400" : "text-rose-400"}>
                  {num(fresh)} fresh
                </span>
              </>
            )}
          </span>
        )}
        <div className="ml-auto flex items-center gap-3 text-[11px]">
          {c.website && <Ext href={c.website}>{c.website.replace(/^https?:\/\//, "")}</Ext>}
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
            {c.bio || <em className="text-rose-400">empty — scores ~2.6</em>}
          </span>
        </div>
        <div>
          <span className="text-zinc-500">mission </span>
          <span className="text-zinc-300">
            {c.mission || <em className="text-zinc-600">empty</em>}
          </span>
        </div>
      </div>

      {enriched && i && (
        <div className="mt-2 grid gap-x-6 gap-y-1 rounded border border-zinc-800 bg-zinc-950/60 p-2 text-[11.5px] md:grid-cols-2">
          {i.positioning && (
            <div>
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
            <div>
              <span className="text-zinc-500">ad themes </span>
              <span className="font-mono text-zinc-400">
                {i.ad_themes.join(", ")}
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
