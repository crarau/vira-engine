/**
 * A local list of jobs this browser started.
 *
 * The API has no `GET /v1/jobs` — jobs are keyed by the id the POST returned,
 * and nothing enumerates them. Rather than assume an endpoint for something so
 * easy to keep locally, the console remembers what it launched.
 */

const KEY = "vira.recent-jobs";
const MAX = 25;

export interface RecentJob {
  job_id: string;
  company_slug: string;
  product: string;
  lane: string;
  mode: string;
  started_at: string;
}

export function recentJobs(): RecentJob[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as RecentJob[]) : [];
  } catch {
    return [];
  }
}

export function rememberJob(job: RecentJob): void {
  if (typeof window === "undefined") return;
  const next = [job, ...recentJobs().filter((j) => j.job_id !== job.job_id)].slice(0, MAX);
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* private mode, quota — not worth failing a generation over */
  }
}
