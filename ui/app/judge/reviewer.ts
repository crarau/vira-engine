/**
 * Who is voting, and what they have already said.
 *
 * Two jobs, both of them about not losing a paid panellist's work.
 *
 * **The ref.** Terac appends its own identifiers to the `task_url` per
 * participant — `teracSubmissionId`, `submissionId` and `taskId` (docs/TERAC.md,
 * "The flow"). `teracSubmissionId` is the one the reconciliation path expects:
 * `vira/terac.py:submission_ref` stores a Terac submission under
 * `terac:<submission id>`, and `POST /v1/terac/opportunities/{id}/sync` matches
 * on exactly that string. Send anything else and a panellist Terac paid shows
 * up in `unlinked` even though their votes are sitting in the table.
 *
 * Someone opening the link directly — the team, a judge at a demo table — has
 * none of those. They get a random ref, persisted so a reload is the same
 * person, prefixed `anon:` so the two populations are separable in the data
 * afterwards. A vote from the couch must never be counted as a paid panellist.
 *
 * **The cache.** There is no `GET` for "my votes": the judge payload is
 * deliberately one-way and the results endpoint is keyed by batch id, not by
 * the public token, so holding the judge link does not hand you the tally.
 * Which means the only way this page can show "saved" after a reload is to
 * remember locally what it successfully posted. The server stays the source of
 * truth — this is a receipt, not a queue.
 */

import type { VoteBody } from "@/lib/api";

const REF_KEY = "vira.judge.reviewer-ref";
const VOTES_KEY = "vira.judge.votes";

/** `reviewer_ref` is `max_length=120` in the API schema. Stay well under. */
const MAX_REF = 120;

function readLocal(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null; // Private mode. Everything below degrades to in-memory.
  }
}

function writeLocal(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* quota or private mode — a lost receipt is not worth failing a vote over */
  }
}

function randomId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  if (c && typeof c.getRandomValues === "function") {
    const bytes = c.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export interface Reviewer {
  ref: string;
  /** True when Terac sent them. Drives the copy at the finish line. */
  fromTerac: boolean;
}

/**
 * Resolve the ref for this visit.
 *
 * `search` is the raw query string so this stays testable and does not care
 * whether it was handed `useSearchParams()` or `window.location.search`.
 */
export function resolveReviewer(search: URLSearchParams): Reviewer {
  // `teracSubmissionId` is the contract. `submissionId` is only consulted when
  // the first is missing — Terac appends both, and an anon ref for a paid
  // panellist is a worse failure than a ref that needs reconciling by hand.
  const terac =
    (search.get("teracSubmissionId") || search.get("submissionId") || "").trim();
  if (terac) {
    return { ref: `terac:${terac}`.slice(0, MAX_REF), fromTerac: true };
  }

  const saved = readLocal(REF_KEY);
  if (saved && saved.startsWith("anon:")) return { ref: saved, fromTerac: false };

  const fresh = `anon:${randomId()}`.slice(0, MAX_REF);
  writeLocal(REF_KEY, fresh);
  return { ref: fresh, fromTerac: false };
}

// ------------------------------------------------------------ the receipt

/** What this browser knows it successfully posted, for one video. */
export interface SavedVote {
  rating: number;
  picked: boolean;
  comment: string;
  /** ISO. Only used to say "updated" rather than "saved" on a second pass. */
  saved_at: string;
  /** How many times this row has been written. >1 means the vote was edited. */
  writes: number;
}

export type SavedVotes = Record<string, SavedVote>;

/** One bucket per (batch token, reviewer) so two people on one phone do not mix. */
function bucket(token: string, ref: string): string {
  return `${token}::${ref}`;
}

function readAll(): Record<string, SavedVotes> {
  const raw = readLocal(VOTES_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function loadVotes(token: string, ref: string): SavedVotes {
  if (typeof window === "undefined") return {};
  const all = readAll();
  const mine = all[bucket(token, ref)];
  return mine && typeof mine === "object" ? mine : {};
}

/** Record a vote the API accepted. Returns the row as stored, `writes` bumped. */
export function rememberVote(
  token: string,
  ref: string,
  body: Pick<VoteBody, "video_id" | "rating" | "picked" | "comment">,
): SavedVote {
  const previous = loadVotes(token, ref)[body.video_id];
  const row: SavedVote = {
    rating: body.rating,
    picked: body.picked,
    comment: body.comment,
    saved_at: new Date().toISOString(),
    writes: (previous?.writes ?? 0) + 1,
  };
  if (typeof window === "undefined") return row;
  const all = readAll();
  const key = bucket(token, ref);
  all[key] = { ...(all[key] || {}), [body.video_id]: row };
  writeLocal(VOTES_KEY, JSON.stringify(all));
  return row;
}
