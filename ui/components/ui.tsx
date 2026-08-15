"use client";

import Link from "next/link";
import { ReactNode } from "react";
import { ApiError } from "@/lib/api";

export function Panel({
  title,
  right,
  children,
  className = "",
}: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-zinc-800 bg-zinc-900/40 ${className}`}
    >
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-zinc-800 px-3 py-2">
          <h2 className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">
            {title}
          </h2>
          {right}
        </header>
      )}
      <div className="p-3">{children}</div>
    </section>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "default" | "good" | "warn" | "bad";
}) {
  const tones = {
    default: "text-zinc-100",
    good: "text-emerald-400",
    warn: "text-amber-400",
    bad: "text-rose-400",
  };
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-2">
      <div className="text-[10px] uppercase tracking-widest text-zinc-500">
        {label}
      </div>
      <div className={`mt-0.5 font-mono text-xl leading-none ${tones[tone]}`}>
        {value}
      </div>
      {sub && <div className="mt-1 text-[11px] text-zinc-500">{sub}</div>}
    </div>
  );
}

const badgeTones: Record<string, string> = {
  neutral: "border-zinc-700 bg-zinc-800/60 text-zinc-300",
  good: "border-emerald-800 bg-emerald-950/60 text-emerald-300",
  warn: "border-amber-800 bg-amber-950/60 text-amber-300",
  bad: "border-rose-800 bg-rose-950/60 text-rose-300",
  info: "border-sky-800 bg-sky-950/60 text-sky-300",
  violet: "border-violet-800 bg-violet-950/60 text-violet-300",
};

export function Badge({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: keyof typeof badgeTones;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide ${badgeTones[tone]}`}
    >
      {children}
    </span>
  );
}

/**
 * A drop is a correct outcome of the evidence gate, not an error. It is drawn
 * in a muted slate rather than red for exactly that reason — red would say the
 * engine broke, and the engine did its job.
 */
export function DispositionBadge({ d }: { d?: string | null }) {
  const key = (d || "unknown").toLowerCase();
  if (key === "surfaced") return <Badge tone="good">surfaced</Badge>;
  if (key === "watchlist") return <Badge tone="warn">watchlist</Badge>;
  if (key === "dropped")
    return (
      <span className="inline-flex items-center whitespace-nowrap rounded border border-zinc-600 bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-zinc-400">
        dropped
      </span>
    );
  return <Badge tone="neutral">{key}</Badge>;
}

export function Bar({
  value,
  max = 5,
  tone = "default",
}: {
  value: number;
  max?: number;
  tone?: "default" | "good" | "warn" | "bad";
}) {
  const w = Math.max(0, Math.min(100, (value / max) * 100));
  const colors = {
    default: "bg-sky-500",
    good: "bg-emerald-500",
    warn: "bg-amber-500",
    bad: "bg-rose-500",
  };
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
      <div className={`h-full ${colors[tone]}`} style={{ width: `${w}%` }} />
    </div>
  );
}

export function Loading({ what = "loading" }: { what?: string }) {
  return (
    <div className="flex items-center gap-2 px-1 py-6 text-sm text-zinc-500">
      <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-zinc-700 border-t-sky-500" />
      {what}…
    </div>
  );
}

export function ErrorBox({
  error,
  hint,
}: {
  error: unknown;
  hint?: ReactNode;
}) {
  const e = error as ApiError;
  const missing = e instanceof ApiError && e.status === 404;
  return (
    <div
      className={`rounded-md border px-3 py-2 text-sm ${
        missing
          ? "border-amber-900 bg-amber-950/40 text-amber-200"
          : "border-rose-900 bg-rose-950/40 text-rose-200"
      }`}
    >
      <div className="font-mono text-[11px] uppercase tracking-widest opacity-70">
        {e instanceof ApiError
          ? `${e.status || "network"} · ${e.path}`
          : "error"}
      </div>
      <div className="mt-1">{(error as Error)?.message || String(error)}</div>
      {hint && <div className="mt-2 text-[12px] opacity-80">{hint}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-zinc-800 px-3 py-6 text-center text-sm text-zinc-500">
      {children}
    </div>
  );
}

export function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex gap-2 border-b border-zinc-800/60 py-1 text-[13px] last:border-0">
      <div className="w-36 shrink-0 text-zinc-500">{k}</div>
      <div className="min-w-0 flex-1 break-words text-zinc-200">{v}</div>
    </div>
  );
}

export function Ext({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-sky-400 underline decoration-sky-900 underline-offset-2 hover:text-sky-300"
    >
      {children}
    </a>
  );
}

export function Internal({
  href,
  children,
  className = "",
}: {
  href: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Link
      href={href}
      className={`text-sky-400 hover:text-sky-300 hover:underline ${className}`}
    >
      {children}
    </Link>
  );
}

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: T; label: ReactNode }[];
  active: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="flex gap-1 border-b border-zinc-800">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={`-mb-px border-b-2 px-3 py-1.5 text-xs font-medium transition-colors ${
            active === t.id
              ? "border-sky-500 text-zinc-100"
              : "border-transparent text-zinc-500 hover:text-zinc-300"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Select({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  label?: string;
}) {
  return (
    <label className="flex items-center gap-1.5 text-[11px] text-zinc-500">
      {label && <span className="uppercase tracking-widest">{label}</span>}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-100 focus:border-sky-600 focus:outline-none"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
