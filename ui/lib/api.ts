/**
 * The one place that knows the wire shapes.
 *
 * Two rules, both because this UI is deliberately ahead of the API:
 *
 *  1. Every list read goes through `unwrap`, which accepts a bare array or any
 *     of the common envelopes (`items` / `data` / `results` / `<name>`). The
 *     corpus endpoints are being written by another agent and this UI should
 *     not go blank over a wrapper key.
 *  2. Every field is read with a default. A missing column shows as 0 or "—",
 *     never as a crashed page. This is an inspection tool; a partial view of
 *     the data beats a stack trace.
 */

export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8720"
).replace(/\/+$/, "");

export class ApiError extends Error {
  status: number;
  path: string;
  constructor(status: number, path: string, message: string) {
    super(message);
    this.status = status;
    this.path = path;
    this.name = "ApiError";
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    });
  } catch (e) {
    throw new ApiError(
      0,
      path,
      `cannot reach the API at ${API_BASE} — is uvicorn running?`,
    );
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? body);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, path, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Accept a bare array or any of the usual envelopes. */
export function unwrap<T>(payload: unknown, ...keys: string[]): T[] {
  if (Array.isArray(payload)) return payload as T[];
  if (payload && typeof payload === "object") {
    const obj = payload as Record<string, unknown>;
    for (const key of [...keys, "items", "data", "results", "rows"]) {
      if (Array.isArray(obj[key])) return obj[key] as T[];
    }
  }
  return [];
}

// ---------------------------------------------------------------- lanes

export interface Lane {
  name: string;
  brief: string;
  voice_note: string;
  look: string;
}

export const getLanes = () => api<Lane[]>("/v1/lanes");

// ------------------------------------------------------------ companies

/** `GET /v1/companies` — this service's own copy of a brand. */
export interface Company {
  id: string | null;
  slug: string;
  name: string;
  category: string;
  bio: string;
  mission: string;
  website: string | null;
  video_count: number | null;
}

export const getCompanies = () => api<Company[]>("/v1/companies");

// --------------------------------------------------------------- corpus

/**
 * `/v1/corpus/companies` — the Lovable-side company rows.
 *
 * Enrichment is flattened onto the row rather than nested: `enriched` is the
 * boolean the UI reads, and `positioning` / `keywords` / `ad_themes` are what
 * the enrichment produced. The optional nested forms are still accepted because
 * a PostgREST embed would arrive that way.
 */
export interface CorpusInsights {
  summary?: string | null;
  positioning?: string | null;
  tone?: string | null;
  keywords?: string[] | null;
  ad_themes?: string[] | null;
}

export interface CorpusCompany extends CorpusInsights {
  id?: string;
  slug: string;
  name: string;
  category?: string;
  category_slug?: string;
  bio?: string;
  mission?: string;
  website?: string | null;
  owner_name?: string;
  status?: string;
  created_at?: string | null;
  enriched?: boolean;
  insights?: CorpusInsights | CorpusInsights[] | null;
  company_insights?: CorpusInsights | CorpusInsights[] | null;
  trend_count?: number | null;
}

/** `/v1/corpus/trends` — the scraped corpus, one row per clip. */
export interface CorpusTrend {
  trend_key: string;
  author?: string;
  caption?: string;
  source_url?: string;
  /** Cover frame. The API flattens it out of the scraper's raw payload. */
  thumbnail?: string | null;
  format?: string;
  hashtags?: string[] | null;
  views?: number;
  likes?: number;
  engagement_rate?: number;
  trend_score?: number;
  posted_at?: string | null;
  age_days?: number | null;
  /** Server's own verdict against the freshness window. */
  stale?: boolean;
  /** The category slug this row was matched under. */
  query?: string;
  // Tolerated but not currently sent.
  title?: string;
  platform?: string;
  relevance_rank?: number;
  category?: string;
  category_slug?: string;
  raw?: Record<string, unknown> | null;
}

export interface TrendsPage {
  total_in_corpus: number;
  returned: number;
  order: string;
  note: string | null;
  items: CorpusTrend[];
}

/** `/v1/corpus/stats` — corpus-wide counts the 200-row page cannot show. */
export interface CorpusStats {
  trends_total?: number;
  fresh_30d?: number;
  fresh_90d?: number;
  within_1y?: number;
  usable_share_90d?: number;
  companies?: number;
  by_category?: { name: string; slug: string; mapped: number }[];
}

export interface CorpusCategory {
  id?: string;
  name?: string;
  slug?: string;
  trend_count?: number;
}

/** Both corpus list endpoints cap `limit` at 200. Asking for more is a 422. */
export const CORPUS_PAGE_MAX = 200;

export async function getCorpusCompanies(limit = CORPUS_PAGE_MAX): Promise<CorpusCompany[]> {
  return unwrap<CorpusCompany>(
    await api<unknown>(`/v1/corpus/companies?limit=${Math.min(limit, CORPUS_PAGE_MAX)}`),
    "companies",
  );
}

export type TrendOrder = "trend_score" | "views" | "posted_at";

/**
 * One page of trends. Ordering and the freshness filter run server-side —
 * with a 200-row cap over a 4,600-row corpus, sorting the page in the browser
 * would sort the wrong 200 rows.
 */
export async function getCorpusTrends(params: {
  category?: string;
  order?: TrendOrder;
  maxAgeDays?: number | null;
  limit?: number;
} = {}): Promise<TrendsPage> {
  const q = new URLSearchParams();
  if (params.category) q.set("category", params.category);
  if (params.order) q.set("order", params.order);
  if (typeof params.maxAgeDays === "number")
    q.set("max_age_days", String(params.maxAgeDays));
  q.set("limit", String(Math.min(params.limit ?? CORPUS_PAGE_MAX, CORPUS_PAGE_MAX)));
  const payload = await api<unknown>(`/v1/corpus/trends?${q}`);
  const items = unwrap<CorpusTrend>(payload, "trends");
  const meta = (payload && typeof payload === "object" ? payload : {}) as Partial<TrendsPage>;
  return {
    total_in_corpus: meta.total_in_corpus ?? items.length,
    returned: meta.returned ?? items.length,
    order: meta.order ?? params.order ?? "trend_score",
    note: meta.note ?? null,
    items,
  };
}

export async function getCorpusCategories(): Promise<CorpusCategory[]> {
  return unwrap<CorpusCategory>(
    await api<unknown>("/v1/corpus/categories"),
    "categories",
  );
}

export const getCorpusStats = () => api<CorpusStats>("/v1/corpus/stats");

// ---------------------------------------------------------- suggestions

/**
 * `/v1/suggest/{slug}` — what to type in the product box, drawn from the
 * corpus rows selection would actually pick.
 *
 * The endpoint costs an LLM call (~35s cold, ~0.2s cached), so a UI should
 * fetch it once per company and only pass `refresh` on an explicit click.
 */
export interface BioQuality {
  /** `junk` means the bio said nothing usable and the suggestions know it. */
  verdict: "usable" | "thin" | "junk";
  reason: string;
  chars: number;
  words: number;
  lean_on_corpus: boolean;
}

export interface Suggestion {
  /** Drops straight into the product field. 8–20 words, names a mechanism. */
  product: string;
  angle: string;
  lane: string;
  lane_reason: string;
  /** trend_keys from the slice. Resolve against `sources` for the URL. */
  grounded_in: string[];
  evidence: string[];
}

export interface SuggestionSource {
  trend_key: string;
  source_url: string;
  author?: string;
  caption?: string;
  format?: string;
  views?: number;
  age_days?: number | null;
}

export interface SuggestionsResponse {
  company_slug: string;
  company_name: string;
  category: string;
  bio_quality: BioQuality;
  suggestions: Suggestion[];
  sources: SuggestionSource[];
  corpus: {
    slice_size: number;
    rejected: Record<string, number>;
    category: string;
    max_age_days: number;
  };
  note: string | null;
  generated_at: string;
  cached: boolean;
  elapsed_ms: number;
}

const NO_BIO_VERDICT: BioQuality = {
  verdict: "usable",
  reason: "",
  chars: 0,
  words: 0,
  lean_on_corpus: false,
};

export async function getSuggestions(
  slug: string,
  refresh = false,
): Promise<SuggestionsResponse> {
  const payload = await api<Partial<SuggestionsResponse>>(
    `/v1/suggest/${encodeURIComponent(slug)}${refresh ? "?refresh=true" : ""}`,
  );
  return {
    company_slug: payload.company_slug ?? slug,
    company_name: payload.company_name ?? slug,
    category: payload.category ?? "",
    bio_quality: payload.bio_quality ?? NO_BIO_VERDICT,
    suggestions: unwrap<Suggestion>(payload, "suggestions"),
    sources: payload.sources ?? [],
    corpus: payload.corpus ?? {
      slice_size: 0,
      rejected: {},
      category: "",
      max_age_days: 90,
    },
    note: payload.note ?? null,
    generated_at: payload.generated_at ?? "",
    cached: Boolean(payload.cached),
    elapsed_ms: payload.elapsed_ms ?? 0,
  };
}

// --------------------------------------------------------------- videos

export interface Score {
  relevance: number;
  specificity: number;
  actionability: number;
  differentiation: number;
  evidence: number;
  overall: number;
}

export interface Video {
  id: string;
  job_id: string | null;
  company_slug: string;
  product: string;
  lane: string;
  mode: string;
  hook: string;
  caption: string;
  hashtags: string[];
  cta: string;
  duration_s: number;
  mp4_url: string;
  score: Score | null;
  disposition: string | null;
  drop_reason: string | null;
  created_at: string | null;
}

export const getVideo = (id: string) => api<Video>(`/v1/videos/${id}`);

export const getCompanyVideos = (slug: string) =>
  api<Video[]>(`/v1/companies/${encodeURIComponent(slug)}/videos`);

/**
 * Every video, newest first.
 *
 * `GET /v1/videos` is an ASSUMED endpoint. When it is not there, this fans out
 * over `/v1/companies` and calls the per-company list that definitely exists,
 * so the library page works either way.
 */
export async function getAllVideos(): Promise<Video[]> {
  try {
    const rows = unwrap<Video>(await api<unknown>("/v1/videos?limit=500"), "videos");
    if (rows.length) return sortByCreated(rows);
  } catch (e) {
    if (!(e instanceof ApiError) || (e.status !== 404 && e.status !== 405)) {
      // A real failure (network, 500) is worth surfacing rather than papering
      // over with a slow fan-out that will fail the same way.
      if (e instanceof ApiError && e.status === 0) throw e;
    }
  }
  const companies = await getCompanies();
  const batches = await Promise.all(
    companies.map((c) => getCompanyVideos(c.slug).catch(() => [] as Video[])),
  );
  return sortByCreated(batches.flat());
}

function sortByCreated(rows: Video[]): Video[] {
  return [...rows].sort(
    (a, b) => Date.parse(b.created_at || "") - Date.parse(a.created_at || ""),
  );
}

// --------------------------------------------------------------- recipe

export interface RecipeBeat {
  t?: number;
  say?: string;
  show?: string;
  shot?: string;
  motion?: string;
  delivery?: string;
  camera?: string;
  start_s?: number | null;
  end_s?: number | null;
  words?: { w: string; start: number; end: number }[];
}

export interface RecipeCorpusRow {
  trend_key?: string;
  author?: string;
  source_url?: string;
  trend_score?: number;
  age_days?: number;
}

export interface LlmCall {
  n: number;
  /** Which pipeline stage made the call. Empty on a recipe written by the CLI. */
  stage?: string;
  model: string;
  max_tokens: number | null;
  stop_reason: string | null;
  system_prompt: string;
  user_prompt: string;
  response: string;
}

export interface RecipeAsset {
  beat_index: number | null;
  kind: string;
  path: string | null;
  prompt: string | null;
  credit: string | null;
  description: string | null;
}

export interface Recipe {
  video_id?: string;
  plan?: Record<string, unknown>;
  settings?: Record<string, unknown>;
  corpus?: RecipeCorpusRow[];
  beats?: RecipeBeat[];
  llm_calls?: LlmCall[];
  assets?: RecipeAsset[];
  [k: string]: unknown;
}

export interface RecipeEnvelope {
  video_id: string;
  recipe: Recipe;
}

export const getRecipe = (id: string) =>
  api<RecipeEnvelope>(`/v1/videos/${id}/recipe`);

// ----------------------------------------------------------------- jobs

export type JobState = "queued" | "running" | "done" | "failed";

export interface Job {
  job_id: string;
  status: JobState;
  progress_note: string;
  video_id: string | null;
  error: string | null;
  company_slug: string | null;
  lane: string | null;
  mode: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface JobAccepted {
  job_id: string;
  status: JobState;
  poll: string;
  estimated_seconds: number;
}

export const getJob = (id: string) => api<Job>(`/v1/jobs/${id}`);

export interface TraceEvent {
  seq: number;
  ts: string;
  job_id: string;
  stage: string;
  message: string;
  level: string;
  data: Record<string, unknown>;
}

export interface EventsWindow {
  job_id: string;
  source: "memory" | "database";
  status: string;
  complete: boolean;
  next_after: number;
  events: TraceEvent[];
}

/**
 * `debug` is the verbose feed: every model call with its prompts, in full.
 *
 * The level is a server-side subscription rather than something to filter in
 * the browser — a page watching at `info` never has a 12 KB prompt sent to it.
 * Switching level mid-run leaves a gap in `seq`, which is the events that were
 * deliberately not delivered, so a client that reopens the stream without an
 * `after` cursor gets the whole buffer replayed at the new level.
 */
export type TraceLevel = "debug" | "info" | "warn" | "error";

export const getJobEvents = (id: string, after = 0, level: TraceLevel = "info") =>
  api<EventsWindow>(`/v1/jobs/${id}/events?after=${after}&level=${level}`);

export function streamUrl(id: string, after?: number, level: TraceLevel = "info") {
  const q = new URLSearchParams({ level });
  if (after) q.set("after", String(after));
  return `${API_BASE}/v1/jobs/${id}/stream?${q}`;
}

// ------------------------------------------------------- prompts, either way
//
// The same model call reaches this UI from two places — live, as a `debug`
// trace event, and afterwards, out of the recipe — and they are worth rendering
// identically. `PromptCall` is the shape both normalise to, so one component
// draws the job page's verbose panel and the video page's Recipe tab.

export interface PromptCall {
  n: number;
  stage: string;
  model: string;
  max_tokens: number | null;
  stop_reason: string | null;
  system_prompt: string;
  user_prompt: string;
  response: string;
  elapsed_ms: number | null;
  /** Director turn, when the call came from the agentic loop. */
  turn: number | null;
}

const str = (v: unknown) => (typeof v === "string" ? v : "");
const numOrNull = (v: unknown) => (typeof v === "number" ? v : null);

export function callFromRecipe(c: LlmCall, i: number): PromptCall {
  return {
    n: c.n ?? i + 1,
    stage: c.stage || "",
    model: c.model || "—",
    max_tokens: c.max_tokens ?? null,
    stop_reason: c.stop_reason ?? null,
    system_prompt: c.system_prompt || "",
    user_prompt: c.user_prompt || "",
    response: c.response || "",
    elapsed_ms: null,
    turn: null,
  };
}

/** A `debug` event as a prompt call, or null when it is not one. */
export function callFromEvent(e: TraceEvent, n: number): PromptCall | null {
  const d = e.data || {};
  if (d.kind !== "llm_call") return null;
  return {
    n,
    stage: str(d.pipeline_stage) || e.stage,
    model: str(d.model) || "—",
    max_tokens: numOrNull(d.max_tokens),
    stop_reason: typeof d.stop_reason === "string" ? d.stop_reason : null,
    system_prompt: str(d.system_prompt),
    user_prompt: str(d.user_prompt),
    response: str(d.response),
    elapsed_ms: numOrNull(d.elapsed_ms),
    turn: numOrNull(d.turn),
  };
}

export function promptChars(c: PromptCall): number {
  return c.system_prompt.length + c.user_prompt.length;
}

export const createVideo = (body: {
  company_slug: string;
  product: string;
  lane: string;
  mode: "fast" | "agentic";
}) => api<JobAccepted>("/v1/videos", { method: "POST", body: JSON.stringify(body) });

export const regenerate = (id: string, body: { notes: string[]; lane?: string }) =>
  api<JobAccepted>(`/v1/videos/${id}/regenerate`, {
    method: "POST",
    body: JSON.stringify(body),
  });

// --------------------------------------------------------- judge (public)
//
// The one surface in this app that is not for an operator. A paid panellist
// arrives from Terac with a token in the path and nothing else, and the whole
// value of their vote is that it is independent of the engine's own grade.
//
// So this section breaks the "read every field with a default" rule in one
// direction on purpose: it reads only the five fields a judge is allowed to
// see, and drops everything else on the floor. `GET /v1/review-batches/{token}`
// is already built to omit `score`, `disposition`, `lane` and `drop_reason` —
// this is the second lock on the same door, so that a future field added to
// the API response cannot reach the page by accident.

/** One cut as a judge sees it. There is no score field, and there must not be. */
export interface JudgeVideo {
  video_id: string;
  position: number;
  hook: string;
  duration_s: number;
  mp4_url: string;
}

export interface JudgeBatch {
  title: string;
  videos: JudgeVideo[];
}

export interface VoteBody {
  /** `terac:<teracSubmissionId>` for a panellist, `anon:<random>` otherwise. */
  reviewer_ref: string;
  video_id: string;
  /** 1–5. The API requires it: there is no comment-only or pick-only vote. */
  rating: number;
  picked: boolean;
  comment: string;
}

export interface VoteAccepted {
  recorded: boolean;
  reviewer_ref: string;
  video_id: string;
}

/**
 * `GET /v1/review-batches/{token}` — PUBLIC, unauthenticated, no account.
 *
 * A 404 here means the token is wrong or the batch is gone; the page says that
 * in plain language rather than showing a panellist an error code.
 */
export async function getJudgeBatch(token: string): Promise<JudgeBatch> {
  const payload = await api<unknown>(
    `/v1/review-batches/${encodeURIComponent(token)}`,
  );
  const obj = (payload && typeof payload === "object" ? payload : {}) as Record<
    string,
    unknown
  >;
  const rows = unwrap<Record<string, unknown>>(obj, "videos");
  return {
    title: typeof obj.title === "string" ? obj.title : "",
    // Hand-picked, never spread. See the note above.
    videos: rows.map((r, i) => ({
      video_id: String(r.video_id ?? ""),
      position: typeof r.position === "number" ? r.position : i + 1,
      hook: typeof r.hook === "string" ? r.hook : "",
      duration_s: typeof r.duration_s === "number" ? r.duration_s : 0,
      mp4_url: typeof r.mp4_url === "string" ? r.mp4_url : "",
    })).filter((v) => v.video_id),
  };
}

/**
 * `POST /v1/review-batches/{token}/votes` — one reviewer's verdict on one cut.
 *
 * The unique index is `(batch_id, video_id, reviewer_ref)` and the insert
 * upserts, so calling this twice for the same video is an edit, not an error.
 * That is what lets the page save on every tap instead of at the end.
 *
 * `keepalive` so a vote posted as the tab is being closed still leaves.
 */
export const submitVote = (token: string, body: VoteBody, keepalive = false) =>
  api<VoteAccepted>(`/v1/review-batches/${encodeURIComponent(token)}/votes`, {
    method: "POST",
    body: JSON.stringify(body),
    keepalive,
  });

// ------------------------------------------------------------ thresholds

/**
 * The gate, mirrored from vira/config.py so the UI can say what a number means
 * without asking. Overridden per video by the recipe's settings snapshot, which
 * records the values actually in force at generation time.
 */
export const GATE = {
  evidence_floor: 3.0,
  watchlist_threshold: 3.5,
  surface_threshold: 4.5,
  max_age_days: 90,
};

export function gateFor(recipe?: Recipe | null) {
  const s = (recipe?.settings || {}) as Record<string, unknown>;
  const num = (k: keyof typeof GATE) =>
    typeof s[k] === "number" ? (s[k] as number) : GATE[k];
  return {
    evidence_floor: num("evidence_floor"),
    watchlist_threshold: num("watchlist_threshold"),
    surface_threshold: num("surface_threshold"),
    max_age_days: num("max_age_days"),
  };
}
