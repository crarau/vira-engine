"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Generate" },
  { href: "/corpus", label: "Corpus" },
  { href: "/videos", label: "Library" },
];

export function Nav() {
  const path = usePathname();
  const [health, setHealth] = useState<"?" | "up" | "down">("?");

  useEffect(() => {
    let alive = true;
    const ping = async () => {
      try {
        const r = await fetch(`${API_BASE}/healthz`, { cache: "no-store" });
        if (alive) setHealth(r.ok ? "up" : "down");
      } catch {
        if (alive) setHealth("down");
      }
    };
    ping();
    const t = setInterval(ping, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  return (
    <nav className="sticky top-0 z-30 border-b border-zinc-800 bg-zinc-950/90 backdrop-blur">
      <div className="mx-auto flex w-full max-w-[1600px] items-center gap-6 px-4 py-2">
        <Link href="/" className="font-mono text-sm font-bold tracking-tight text-zinc-100">
          vira<span className="text-sky-500">/</span>console
        </Link>
        <div className="flex gap-1">
          {LINKS.map((l) => {
            const on = l.href === "/" ? path === "/" : path.startsWith(l.href);
            return (
              <Link
                key={l.href}
                href={l.href}
                className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  on
                    ? "bg-zinc-800 text-zinc-100"
                    : "text-zinc-500 hover:bg-zinc-900 hover:text-zinc-300"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </div>
        <div className="ml-auto flex items-center gap-2 font-mono text-[10px] text-zinc-600">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              health === "up"
                ? "bg-emerald-500"
                : health === "down"
                  ? "bg-rose-500"
                  : "bg-zinc-600"
            }`}
          />
          <span className={health === "down" ? "text-rose-400" : ""}>
            {API_BASE}
          </span>
        </div>
      </div>
    </nav>
  );
}
