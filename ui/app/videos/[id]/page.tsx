"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  API_BASE,
  callFromRecipe,
  gateFor,
  getRecipe,
  getVideo,
  LlmCall,
  Recipe,
  RecipeAsset,
  RecipeBeat,
  regenerate,
  Video,
} from "@/lib/api";
import { num, secs, when } from "@/lib/format";
import { rememberJob } from "@/lib/recent";
import { CallList, CallTally } from "@/components/Prompts";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";
import { VideoPlayer } from "@/components/VideoPlayer";
import {
  Badge,
  DispositionBadge,
  Empty,
  ErrorBox,
  Ext,
  Internal,
  KV,
  Loading,
  Panel,
  Tabs,
} from "@/components/ui";

type Tab = "score" | "script" | "sources" | "frames" | "recipe";

export default function VideoPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id || "";
  const [video, setVideo] = useState<Video | null>(null);
  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [recipeErr, setRecipeErr] = useState<unknown>(null);
  const [tab, setTab] = useState<Tab>("score");

  useEffect(() => {
    if (!id) return;
    getVideo(id).then(setVideo).catch(setErr);
    getRecipe(id)
      .then((r) => setRecipe(r.recipe))
      .catch(setRecipeErr);
  }, [id]);

  const gate = gateFor(recipe);
  const beats = (recipe?.beats || []) as RecipeBeat[];
  const sources = recipe?.corpus || [];
  const assets = (recipe?.assets || []) as RecipeAsset[];
  const frames = assets.filter((a) => a.kind === "image");
  const audio = assets.find((a) => a.kind === "audio");
  const calls = (recipe?.llm_calls || []) as LlmCall[];

  if (err) return <ErrorBox error={err} />;
  if (!video) return <Loading what="loading the video" />;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold text-zinc-100">
              {video.company_slug || "—"}
            </h1>
            <Badge tone="violet">{video.lane}</Badge>
            <Badge tone={video.mode === "agentic" ? "info" : "neutral"}>
              {video.mode}
            </Badge>
            <DispositionBadge d={video.disposition} />
            {video.score && (
              <Badge tone="info">score {video.score.overall.toFixed(2)}</Badge>
            )}
          </div>
          <p className="mt-0.5 text-[12px] text-zinc-500">
            {video.product} · {secs(video.duration_s)} · created{" "}
            {when(video.created_at)} ·{" "}
            <span className="font-mono text-zinc-600">{video.id}</span>
          </p>
        </div>
        <div className="flex items-center gap-3 text-[12px]">
          {video.job_id && (
            <Internal href={`/jobs/${video.job_id}`}>job trace →</Internal>
          )}
          <Ext href={video.mp4_url}>mp4 ↗</Ext>
        </div>
      </header>

      {video.disposition === "dropped" && (
        <div className="rounded-lg border border-zinc-700 bg-zinc-900 p-3">
          <div className="font-mono text-[10px] uppercase tracking-widest text-zinc-500">
            dropped — the gate did its job
          </div>
          <div className="mt-1 text-[13.5px] text-zinc-200">
            {video.drop_reason || "no reason recorded"}
          </div>
          <p className="mt-1.5 text-[11.5px] text-zinc-500">
            A drop is a verdict, not an error. The film below rendered fine; the
            engine judged its claims unsupported by the clips it cited. The fix
            is grounding — a better bio, a fresher corpus, a narrower product —
            not a lower threshold.
          </p>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
        <div className="space-y-3">
          <VideoPlayer src={video.mp4_url} maxHeight={440} />
          <Panel title="Copy">
            <div className="space-y-2 text-[13px]">
              <Field label="hook" value={video.hook} />
              <Field label="cta" value={video.cta} />
              <Field label="caption" value={video.caption} />
              <div>
                <div className="text-[10px] uppercase tracking-widest text-zinc-500">
                  hashtags ({video.hashtags.length})
                </div>
                <div className="mt-0.5 font-mono text-[12px] text-sky-400">
                  {video.hashtags.length
                    ? video.hashtags.map((h) => `#${h.replace(/^#/, "")}`).join(" ")
                    : "—"}
                </div>
              </div>
            </div>
          </Panel>
          <Regenerate videoId={video.id} lane={video.lane} slug={video.company_slug} product={video.product} mode={video.mode} />
        </div>

        <div className="space-y-3">
          <Tabs
            tabs={[
              { id: "score", label: "Score" },
              { id: "script", label: `Script (${beats.length})` },
              { id: "sources", label: `Sources (${sources.length})` },
              { id: "frames", label: `Frames (${frames.length})` },
              { id: "recipe", label: `Recipe (${calls.length} calls)` },
            ]}
            active={tab}
            onChange={setTab}
          />

          {tab === "score" && (
            <Panel title="The engine's verdict">
              <ScoreBreakdown
                score={video.score}
                gate={gate}
                disposition={video.disposition}
                dropReason={video.drop_reason}
              />
            </Panel>
          )}

          {tab === "script" && (
            <Panel title="Beat by beat, with real timings">
              {beats.length === 0 ? (
                <Empty>
                  No beats in the recipe.{" "}
                  {recipeErr ? "The recipe read failed." : ""}
                </Empty>
              ) : (
                <Beats beats={beats} duration={video.duration_s} />
              )}
              <p className="mt-2 text-[11px] text-zinc-600">
                `start_s` / `end_s` are derived from ElevenLabs character
                timestamps, never authored. Change the copy and the video
                re-times itself; a null means the voice stage had not run yet
                when the beat was written.
              </p>
            </Panel>
          )}

          {tab === "sources" && (
            <Panel title="What this ad was grounded in">
              {sources.length === 0 ? (
                <Empty>No corpus rows recorded on this recipe.</Empty>
              ) : (
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="border-b border-zinc-800 text-left text-[10px] uppercase tracking-widest text-zinc-500">
                      <th className="py-1 pr-2">#</th>
                      <th className="py-1 pr-2">author</th>
                      <th className="py-1 pr-2 text-right">score</th>
                      <th className="py-1 pr-2 text-right">age</th>
                      <th className="py-1">source_url</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sources.map((s, i) => {
                      const stale = (s.age_days ?? 0) > gate.max_age_days;
                      return (
                        <tr
                          key={s.trend_key || i}
                          className="border-b border-zinc-900 last:border-0"
                        >
                          <td className="py-1 pr-2 font-mono text-zinc-600">
                            {i + 1}
                          </td>
                          <td className="py-1 pr-2 font-mono text-zinc-300">
                            @{s.author || "?"}
                          </td>
                          <td className="py-1 pr-2 text-right font-mono text-zinc-300">
                            {num(s.trend_score ?? null)}
                          </td>
                          <td
                            className={`py-1 pr-2 text-right font-mono ${stale ? "text-amber-400" : "text-emerald-400"}`}
                          >
                            {s.age_days === undefined
                              ? "—"
                              : `${Math.round(s.age_days)}d`}
                          </td>
                          <td className="max-w-0 truncate py-1">
                            {s.source_url ? (
                              <Ext href={s.source_url}>{s.source_url}</Ext>
                            ) : (
                              <span className="text-rose-400">missing</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
              <p className="mt-2 text-[11px] text-zinc-600">
                Every one of these was fetched and verified before it reached a
                prompt. Nothing is stored without a live `source_url`.
              </p>
              <Rejections plan={(recipe?.plan || {}) as Record<string, unknown>} />
            </Panel>
          )}

          {tab === "frames" && (
            <Panel title="Generated frames and the prompts behind them">
              {frames.length === 0 ? (
                <Empty>No image assets recorded.</Empty>
              ) : (
                <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                  {frames.map((f, i) => (
                    <FrameCard key={i} f={f} index={i} />
                  ))}
                </div>
              )}
              {audio && (
                <div className="mt-3 rounded border border-zinc-800 bg-zinc-950/60 p-2">
                  <div className="text-[10px] uppercase tracking-widest text-zinc-500">
                    narration · voice {audio.credit || "—"}
                  </div>
                  <div className="mt-1 text-[12px] leading-snug text-zinc-400">
                    {audio.prompt || "—"}
                  </div>
                </div>
              )}
            </Panel>
          )}

          {tab === "recipe" && (
            <RecipeTab
              recipe={recipe}
              error={recipeErr}
              calls={calls}
              videoId={video.id}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-zinc-500">
        {label}
      </div>
      <div className="mt-0.5 text-zinc-200">
        {value || <span className="text-zinc-600">—</span>}
      </div>
    </div>
  );
}

function Beats({
  beats,
  duration,
}: {
  beats: RecipeBeat[];
  duration: number;
}) {
  const total = duration || Math.max(1, ...beats.map((b) => b.end_s ?? b.t ?? 0));
  return (
    <div className="space-y-2">
      {/* One strip so the shape of the film is visible before reading it. */}
      <div className="flex h-2 gap-px overflow-hidden rounded bg-zinc-900">
        {beats.map((b, i) => {
          const start = b.start_s ?? b.t ?? 0;
          const end = b.end_s ?? (beats[i + 1]?.start_s ?? total);
          const w = Math.max(1, ((end - start) / total) * 100);
          return (
            <div
              key={i}
              className={i % 2 ? "bg-sky-700" : "bg-sky-600"}
              style={{ width: `${w}%` }}
              title={`beat ${i + 1}: ${start.toFixed(1)}–${end.toFixed(1)}s`}
            />
          );
        })}
      </div>

      {beats.map((b, i) => {
        const start = b.start_s ?? b.t ?? null;
        const end = b.end_s ?? null;
        return (
          <div
            key={i}
            className="rounded border border-zinc-800 bg-zinc-900/40 p-2.5"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                beat {i + 1}
              </span>
              <span className="font-mono text-[11px] text-emerald-400">
                {start === null ? "—" : `${start.toFixed(2)}s`}
                {end !== null && ` → ${end.toFixed(2)}s`}
                {start !== null && end !== null && (
                  <span className="text-zinc-600">
                    {" "}
                    ({(end - start).toFixed(2)}s)
                  </span>
                )}
              </span>
              {b.delivery && <Badge tone="good">{b.delivery}</Badge>}
              {b.motion && <Badge tone="violet">motion {b.motion}</Badge>}
              {b.camera && <Badge tone="info">camera {b.camera}</Badge>}
              {b.words && b.words.length > 0 && (
                <span className="font-mono text-[10px] text-zinc-600">
                  {b.words.length} word timings
                </span>
              )}
            </div>
            <p className="mt-1.5 text-[13.5px] leading-snug text-zinc-100">
              {b.say || <span className="text-zinc-600">(silent)</span>}
            </p>
            {b.show && (
              <p className="mt-1 text-[11.5px] leading-snug text-zinc-500">
                <span className="text-zinc-600">show · </span>
                {b.show}
              </p>
            )}
            {b.shot && (
              <p className="mt-0.5 text-[11.5px] leading-snug text-zinc-500">
                <span className="text-zinc-600">shot · </span>
                {b.shot}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * What the corpus lost on the way to this ad.
 *
 * `select.py` counts every rejection by reason, and `verify` counts the URLs
 * that were dead by the time they were fetched. Together they explain a thin
 * source list far better than the surviving rows do — a video grounded in three
 * clips is usually a selection problem, not a writing one.
 */
function Rejections({ plan }: { plan: Record<string, unknown> }) {
  const rejected = plan.rejected_at_selection;
  const dead = plan.dead_urls;
  const rows =
    rejected && typeof rejected === "object" && !Array.isArray(rejected)
      ? Object.entries(rejected as Record<string, number>)
      : [];
  if (rows.length === 0 && typeof dead !== "number") return null;
  const total = rows.reduce((s, [, n]) => s + (Number(n) || 0), 0);

  return (
    <div className="mt-3 rounded border border-zinc-800 bg-zinc-950/60 p-2.5">
      <div className="text-[10px] uppercase tracking-widest text-zinc-500">
        rejected before a prompt ever saw it
      </div>
      {rows.length > 0 && (
        <div className="mt-1.5 space-y-1">
          {rows
            .sort((a, b) => Number(b[1]) - Number(a[1]))
            .map(([reason, n]) => (
              <div key={reason} className="flex items-center gap-2 text-[12px]">
                <span className="w-10 shrink-0 text-right font-mono text-zinc-200">
                  {String(n)}
                </span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-900">
                  <div
                    className="h-full bg-zinc-600"
                    style={{
                      width: `${total ? (Number(n) / total) * 100 : 0}%`,
                    }}
                  />
                </div>
                <span className="w-56 shrink-0 text-zinc-400">{reason}</span>
              </div>
            ))}
        </div>
      )}
      {typeof dead === "number" && (
        <div className="mt-2 text-[12px] text-zinc-400">
          <span className="font-mono text-zinc-200">{dead}</span> source URL
          {dead === 1 ? " was" : "s were"} dead at verification time and dropped.
        </div>
      )}
    </div>
  );
}

function FrameCard({ f, index }: { f: RecipeAsset; index: number }) {
  const [ok, setOk] = useState(true);
  const path = f.path || "";
  const src = path.startsWith("http")
    ? path
    : path
      ? `${API_BASE}/media/${path.replace(/^\/+/, "")}`
      : "";

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900/40 p-2">
      <div className="mb-1.5 flex items-center gap-2">
        <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
          beat {f.beat_index === null ? index + 1 : f.beat_index + 1}
        </span>
        {f.credit && (
          <span className="truncate text-[10px] text-zinc-600">{f.credit}</span>
        )}
      </div>
      <div className="mb-2 aspect-[9/16] max-h-56 overflow-hidden rounded border border-zinc-800 bg-black">
        {src && ok ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={src}
            alt=""
            className="h-full w-full object-cover"
            onError={() => setOk(false)}
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-1 px-2 text-center text-[9.5px] leading-tight text-zinc-600">
            {path ? (
              <>
                <span className="font-mono text-zinc-500">{path}</span>
                <span>
                  frames live under video/public/shots/&lt;job_id&gt;/, which is
                  not mounted — only out/ is served at /media
                </span>
              </>
            ) : (
              "no path recorded"
            )}
          </div>
        )}
      </div>
      <div className="text-[10px] uppercase tracking-widest text-zinc-500">
        prompt
      </div>
      <p className="mt-0.5 max-h-32 overflow-y-auto text-[11.5px] leading-snug text-zinc-300">
        {f.prompt || "—"}
      </p>
      {f.description && (
        <>
          <div className="mt-1.5 text-[10px] uppercase tracking-widest text-amber-600">
            what a vision model says it actually shows
          </div>
          <p className="mt-0.5 max-h-32 overflow-y-auto text-[11.5px] leading-snug text-amber-200/80">
            {f.description}
          </p>
        </>
      )}
    </div>
  );
}

function RecipeTab({
  recipe,
  error,
  calls,
  videoId,
}: {
  recipe: Recipe | null;
  error: unknown;
  calls: LlmCall[];
  videoId: string;
}) {
  if (error) return <ErrorBox error={error} />;
  if (!recipe) return <Loading what="loading the recipe" />;

  const settings = (recipe.settings || {}) as Record<string, unknown>;
  const plan = (recipe.plan || {}) as Record<string, unknown>;
  // Same normalisation the live trace uses, so a call reads identically whether
  // you are watching it happen or reading it back a week later.
  const prompts = calls.map(callFromRecipe);

  return (
    <div className="space-y-3">
      <Panel title="Settings in force at generation time">
        <div className="grid gap-x-6 md:grid-cols-2">
          {Object.entries(settings).map(([k, v]) => (
            <KV key={k} k={k} v={<span className="font-mono">{fmt(v)}</span>} />
          ))}
        </div>
      </Panel>

      <Panel title="Authored intent (recipes.plan)">
        {Object.keys(plan).length === 0 ? (
          <Empty>Nothing recorded.</Empty>
        ) : (
          <div className="space-y-1">
            {Object.entries(plan).map(([k, v]) => (
              <KV
                key={k}
                k={k}
                v={
                  typeof v === "object" && v !== null ? (
                    <pre className="max-h-56 overflow-auto rounded border border-zinc-800 bg-zinc-950 p-2 font-mono text-[10.5px] text-zinc-400">
                      {JSON.stringify(v, null, 2)}
                    </pre>
                  ) : (
                    <span className="whitespace-pre-wrap">{fmt(v)}</span>
                  )
                }
              />
            ))}
          </div>
        )}
      </Panel>

      <Panel
        title="Prompts, verbatim"
        right={
          <div className="flex items-center gap-3">
            <CallTally calls={prompts} />
            <a
              href={`${API_BASE}/v1/videos/${videoId}/recipe`}
              target="_blank"
              rel="noreferrer"
              className="text-[11px] text-sky-400 hover:text-sky-300"
            >
              raw JSON ↗
            </a>
          </div>
        }
      >
        {prompts.length === 0 ? (
          <Empty>
            No LLM calls recorded. Videos generated before provenance was wired
            in have no prompts; anything since carries all of them.
          </Empty>
        ) : (
          <CallList calls={prompts} initiallyOpen={[1]} />
        )}
      </Panel>
    </div>
  );
}

function Regenerate({
  videoId,
  lane,
  slug,
  product,
  mode,
}: {
  videoId: string;
  lane: string;
  slug: string;
  product: string;
  mode: string;
}) {
  const router = useRouter();
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  async function go() {
    setBusy(true);
    setErr(null);
    try {
      const lines = notes
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean)
        .slice(0, 20);
      const job = await regenerate(videoId, { notes: lines });
      rememberJob({
        job_id: job.job_id,
        company_slug: slug,
        product,
        lane,
        mode,
        started_at: new Date().toISOString(),
      });
      router.push(`/jobs/${job.job_id}`);
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  }

  return (
    <Panel title="Regenerate">
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        rows={3}
        placeholder={"one note per line\ne.g. the hook is too soft\nname the mechanism in beat 2"}
        className="w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-[12px] text-zinc-100 placeholder:text-zinc-600 focus:border-sky-600 focus:outline-none"
      />
      <button
        onClick={go}
        disabled={busy}
        className="mt-2 w-full rounded border border-zinc-700 bg-zinc-800 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-700 disabled:opacity-50"
      >
        {busy ? "posting…" : `re-run ${lane} with these notes`}
      </button>
      {err ? (
        <div className="mt-2">
          <ErrorBox error={err} />
        </div>
      ) : null}
      <p className="mt-1.5 text-[11px] text-zinc-600">
        The corpus is re-selected rather than replayed — sources age out and some
        are dead by now. What the recipe pins is company, product, lane and mode.
      </p>
    </Panel>
  );
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
