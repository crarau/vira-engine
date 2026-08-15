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
 * ASSUMED SHAPE. `/v1/corpus/companies` — the LOVABLE-side company rows, which
 * carry the category join and the enrichment (`company_insights`) that the
 * local mirror does not. Read defensively: `insights` may arrive as an object,
 * as an array of rows (PostgREST embeds are arrays), or not at all.
 */
export interface CorpusInsights {
  summary?: string | null;
  positioning?: string | null;
  tone?: string | null;
  keywords?: string[] | null;
  ad_themes?: string[] | null;
}

export interface CorpusCompany {
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

/** ASSUMED SHAPE. `/v1/corpus/trends` — the scraped corpus, one row per clip. */
export interface CorpusTrend {
  trend_key: string;
  platform?: string;
  title?: string;
  caption?: string;
  source_url?: string;
  author?: string;
  format?: string;
  hashtags?: string[] | null;
  views?: number;
  likes?: number;
  engagement_rate?: number;
  trend_score?: number;
  posted_at?: string | null;
  age_days?: number | null;
  relevance_rank?: number;
  category?: string;
  category_slug?: string;
  raw?: Record<string, unknown> | null;
}

/** ASSUMED SHAPE, and entirely optional — the page derives its own if absent. */
export interface CorpusStats {
  trends_total?: number;
  companies_total?: number;
  categories_total?: number;
  fresh_count?: number;
  stale_count?: number;
  median_age_days?: number;
  max_age_days?: number;
  newest_posted_at?: string | null;
  oldest_posted_at?: string | null;
}

export interface CorpusCategory {
  slug?: string;
  name?: string;
  trend_count?: number;
  company_count?: number;
}

export async function getCorpusCompanies(): Promise<CorpusCompany[]> {
  return unwrap<CorpusCompany>(
    await api<unknown>("/v1/corpus/companies?limit=500"),
    "companies",
  );
}

export async function getCorpusTrends(params: {
  category?: string;
  limit?: number;
} = {}): Promise<CorpusTrend[]> {
  const q = new URLSearchParams();
  if (params.category) q.set("category", params.category);
  q.set("limit", String(params.limit ?? 1000));
  return unwrap<CorpusTrend>(
    await api<unknown>(`/v1/corpus/trends?${q}`),
    "trends",
  );
}

export async function getCorpusCategories(): Promise<CorpusCategory[]> {
  return unwrap<CorpusCategory>(
    await api<unknown>("/v1/corpus/categories"),
    "categories",
  );
}

export const getCorpusStats = () => api<CorpusStats>("/v1/corpus/stats");

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

export const getJobEvents = (id: string, after = 0) =>
  api<EventsWindow>(`/v1/jobs/${id}/events?after=${after}`);

export const streamUrl = (id: string, after?: number) =>
  `${API_BASE}/v1/jobs/${id}/stream${after ? `?after=${after}` : ""}`;

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
