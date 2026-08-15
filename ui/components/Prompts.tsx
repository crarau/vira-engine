"use client";

/**
 * How a model call is drawn, wherever it comes from.
 *
 * A prompt is 1–12 KB. Printed inline it buries the timeline it was supposed to
 * annotate, so every block here is collapsed by default and scrolls inside a
 * bounded height when opened — the panel stays the same size whether the prompt
 * is a paragraph or a corpus dump.
 *
 * The header row is the part you read without expanding anything: model, token
 * budget, stop reason, and the size of the call. `stop` is there because
 * `max_tokens` is how this pipeline's failures actually present — a truncated
 * JSON body is a `max_tokens` stop, and seeing it in the header is the
 * difference between "the model wrote nonsense" and "the model ran out of room".
 */

import { useState } from "react";
import { PromptCall, promptChars } from "@/lib/api";

export function CopyBlock({
  label,
  text,
  tone,
}: {
  label: string;
  text: string;
  tone?: "response";
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(text || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard blocked (no https, no permission) — the text is still selectable */
    }
  };

  return (
    <div>
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-widest text-zinc-500">
          {label}
        </span>
        <span className="font-mono text-[9.5px] text-zinc-700">
          {(text || "").length.toLocaleString()} chars
        </span>
        <button
          onClick={copy}
          className="rounded border border-zinc-800 px-1 font-mono text-[9px] text-zinc-600 hover:text-zinc-300"
        >
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre
        className={`mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded border border-zinc-800 p-2 font-mono text-[10.5px] leading-relaxed ${
          tone === "response"
            ? "bg-zinc-950 text-emerald-200/70"
            : "bg-black text-zinc-400"
        }`}
      >
        {text || "—"}
      </pre>
    </div>
  );
}

export function CallCard({
  call,
  open,
  onToggle,
}: {
  call: PromptCall;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="rounded border border-zinc-800 bg-zinc-900/40">
      <button
        onClick={onToggle}
        className="flex w-full flex-wrap items-center gap-2 px-2.5 py-1.5 text-left hover:bg-zinc-900"
      >
        <span className="font-mono text-[11px] text-zinc-500">#{call.n}</span>
        {call.stage && (
          <span className="rounded bg-zinc-800 px-1 font-mono text-[9.5px] uppercase tracking-wider text-zinc-400">
            {call.stage}
          </span>
        )}
        <span className="font-mono text-[12px] text-zinc-200">{call.model}</span>
        <span className="font-mono text-[10px] text-zinc-600">
          max_tokens {call.max_tokens || "—"} · stop{" "}
          <span
            className={
              call.stop_reason === "max_tokens" ? "text-amber-400" : undefined
            }
          >
            {call.stop_reason || "—"}
          </span>
          {call.elapsed_ms != null && ` · ${(call.elapsed_ms / 1000).toFixed(1)}s`}
        </span>
        <span className="ml-auto font-mono text-[10px] text-zinc-600">
          {promptChars(call).toLocaleString()} in ·{" "}
          {call.response.length.toLocaleString()} out
        </span>
        <span className="text-zinc-600">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-zinc-800 p-2.5">
          <CopyBlock label="system prompt" text={call.system_prompt} />
          <CopyBlock label="user prompt" text={call.user_prompt} />
          <CopyBlock label="response" text={call.response} tone="response" />
        </div>
      )}
    </div>
  );
}

/** A list of calls with independent open/closed state, in call order. */
export function CallList({
  calls,
  initiallyOpen = [],
}: {
  calls: PromptCall[];
  initiallyOpen?: number[];
}) {
  const [open, setOpen] = useState<Set<number>>(new Set(initiallyOpen));
  const toggle = (n: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });

  return (
    <div className="space-y-2">
      {calls.map((c) => (
        <CallCard key={c.n} call={c} open={open.has(c.n)} onToggle={() => toggle(c.n)} />
      ))}
    </div>
  );
}

/**
 * What the run cost, in the only unit visible from here.
 *
 * Not tokens: this UI never sees a usage count, and guessing one from character
 * length would be a number that looks authoritative and is wrong. Characters of
 * prompt is the honest proxy for the shape of the bill, and it is exact.
 */
export function CallTally({ calls }: { calls: PromptCall[] }) {
  const chars = calls.reduce((n, c) => n + promptChars(c), 0);
  const out = calls.reduce((n, c) => n + c.response.length, 0);
  return (
    <span className="font-mono text-[10.5px] text-zinc-500">
      {calls.length} llm call{calls.length === 1 ? "" : "s"} ·{" "}
      {chars.toLocaleString()} chars of prompt · {out.toLocaleString()} back
    </span>
  );
}
