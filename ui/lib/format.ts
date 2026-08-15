export function num(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function compact(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (n < 1000) return String(Math.round(n));
  return n.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 });
}

export function pct(x: number | null | undefined, digits = 1): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  // The corpus stores engagement as a ratio (0.043) but scrapers sometimes
  // write percent (4.3). Anything above 1 is already a percentage.
  const v = x > 1 ? x / 100 : x;
  return `${(v * 100).toFixed(digits)}%`;
}

export function secs(s: number | null | undefined): string {
  if (s === null || s === undefined || Number.isNaN(s)) return "—";
  return `${s.toFixed(1)}s`;
}

/** Age in days from either an explicit age_days or a posted_at timestamp. */
export function ageDays(
  posted_at?: string | null,
  explicit?: number | null,
): number | null {
  if (typeof explicit === "number" && Number.isFinite(explicit)) return explicit;
  if (!posted_at) return null;
  const t = Date.parse(posted_at);
  if (Number.isNaN(t)) return null;
  return (Date.now() - t) / 86_400_000;
}

export function ageLabel(days: number | null): string {
  if (days === null) return "no date";
  if (days < 1) return "today";
  if (days < 60) return `${Math.round(days)}d`;
  if (days < 730) return `${Math.round(days / 30.4)}mo`;
  return `${(days / 365).toFixed(1)}y`;
}

export function when(iso?: string | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return String(iso);
  return new Date(t).toLocaleString("en-CA", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function ago(iso?: string | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const s = (Date.now() - t) / 1000;
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function clockFromStart(startIso?: string, tsIso?: string): string {
  if (!startIso || !tsIso) return "";
  const a = Date.parse(startIso);
  const b = Date.parse(tsIso);
  if (Number.isNaN(a) || Number.isNaN(b)) return "";
  const d = Math.max(0, (b - a) / 1000);
  const m = Math.floor(d / 60);
  const s = d % 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}

export function titleCase(s: string): string {
  return s.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
