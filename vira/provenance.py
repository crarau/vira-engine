"""Record exactly how a video was made, so it can be re-made or tweaked.

A generated ad is worthless as a starting point if you cannot see how it was
generated. Six months from now — or twenty minutes from now, mid-hackathon —
"make the hook punchier" needs the exact prompt that produced the original hook,
not a guess at it.

Every LLM call routed through `vira.llm` is captured here: the full system
prompt, the full user prompt, the model, the token budget, and the raw response.
Plus the inputs that are not prompts — which corpus rows were in scope, which
thresholds were in force, which voice spoke it, which stock photos were used and
under what licence, and the git commit of the code that did it.

The output is a `recipe.json` (complete, machine-readable) and a `RECIPE.md`
(readable, diffable) written next to every video.

Usage:

    async with Recorder(out_dir) as rec:
        rec.note("lane", "problem-first")
        ...                       # llm calls capture themselves
        rec.finish(company, product, remix, score, shots, sources)
"""

from __future__ import annotations

import json
import subprocess
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_active: ContextVar["Recorder | None"] = ContextVar("vira_recorder", default=None)


def current() -> "Recorder | None":
    return _active.get()


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


class Recorder:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = Path(out_dir)
        self.calls: list[dict[str, Any]] = []
        self.notes: dict[str, Any] = {}
        self.started = datetime.now(timezone.utc)
        self._token = None

    async def __aenter__(self) -> "Recorder":
        self._token = _active.set(self)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._token is not None:
            _active.reset(self._token)

    def note(self, key: str, value: Any) -> None:
        self.notes[key] = value

    def capture(
        self, *, system: str, prompt: str, model: str, max_tokens: int,
        response: str, stop_reason: str | None, stage: str = "",
    ) -> None:
        """Called by vira.llm on every completion. Stores prompts verbatim.

        `stage` is which part of the pipeline made the call ("plan", "write",
        "score"). Optional, and empty from the CLI, where nothing tracks it —
        the prompt is the record and the stage is only there to save a reader
        matching a system prompt against the module that wrote it.
        """
        self.calls.append({
            "n": len(self.calls) + 1,
            "at": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "model": model,
            "max_tokens": max_tokens,
            "stop_reason": stop_reason,
            "system_prompt": system,
            "user_prompt": prompt,
            "response": response,
        })

    def finish(
        self, *, company, product: str, remix, score=None, shots=None, sources=None,
        voice_id: str | None = None, settings_snapshot: dict | None = None,
    ) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        recipe = {
            "generated_at": self.started.isoformat(),
            "git_commit": _git_sha(),
            "company": company.model_dump(mode="json"),
            "product": product,
            "notes": self.notes,
            "settings": settings_snapshot or {},
            "voice_id": voice_id,
            "corpus": [
                {"trend_key": t.trend_key, "author": t.author,
                 "source_url": t.source_url, "trend_score": t.trend_score,
                 "age_days": round(t.age_days, 1)}
                for t in (sources or [])
            ],
            "stock": shots or [],
            "output": {
                "hook": remix.hook,
                "narration": remix.narration(),
                "beats": [b.model_dump(mode="json") for b in remix.beats],
                "caption": remix.caption,
                "hashtags": remix.hashtags,
                "cta": remix.cta,
                "grounded_in": remix.grounded_in,
                "why_this_works": remix.why_this_works,
            },
            "score": score.model_dump() if score else None,
            "llm_calls": self.calls,
        }
        (self.out_dir / "recipe.json").write_text(json.dumps(recipe, indent=2))
        (self.out_dir / "RECIPE.md").write_text(_markdown(recipe))
        return self.out_dir / "recipe.json"


def _markdown(r: dict) -> str:
    c = r["company"]
    L: list[str] = [
        f"# Recipe — {c['name']} · {r['notes'].get('lane', 'default')}",
        "",
        "Everything needed to reproduce or tweak this video. Edit a prompt below,",
        "re-run, and you get a different ad from the same corpus.",
        "",
        "## Provenance",
        "",
        f"- **Generated** {r['generated_at']}",
        f"- **Code commit** `{r['git_commit']}`",
        f"- **Product** {r['product']}",
        f"- **Voice** `{r.get('voice_id') or 'n/a'}`",
        f"- **LLM calls** {len(r['llm_calls'])}",
    ]
    if r.get("settings"):
        L += ["", "## Settings in force", "", "```json",
              json.dumps(r["settings"], indent=2), "```"]

    L += ["", "## Corpus in scope", "",
          f"{len(r['corpus'])} verified sources. The ad was told to borrow from these and nothing else.", ""]
    for t in r["corpus"][:25]:
        L.append(f"- `{t['trend_key']}` @{t['author']} · score {t['trend_score']} · {t['age_days']}d — {t['source_url']}")

    out = r["output"]
    L += ["", "## Output", "", f"**Hook** — {out['hook']}", "", "| t | line | shot |", "|---|---|---|"]
    for b in out["beats"]:
        start = b.get("start_s")
        L.append(f"| {start if start is not None else b.get('t')}s | {b['say']} | {b.get('shot','')} |")
    L += ["", f"**CTA** {out['cta']}", "",
          f"**Caption** {out['caption']}", "",
          f"**Tags** {' '.join('#' + h for h in out['hashtags'])}", "",
          f"**Grounded in** {', '.join(out['grounded_in'])}", "",
          f"**Mechanism borrowed** {out['why_this_works']}"]

    if r.get("score"):
        L += ["", "## Score", "", "```json", json.dumps(r["score"], indent=2), "```"]

    if r.get("stock"):
        L += ["", "## Imagery", "", "| beat | query | credit |", "|---|---|---|"]
        for i, s in enumerate(r["stock"]):
            L.append(f"| {i + 1} | `{s.get('query','')}` | {s.get('credit') or '—'} |")

    L += ["", "## Prompts, verbatim", "",
          "These are the exact strings sent to the model. To change the ad, change these.", ""]
    for call in r["llm_calls"]:
        where = f" · {call['stage']}" if call.get("stage") else ""
        L += [f"### Call {call['n']}{where} — {call['model']} (max_tokens={call['max_tokens']}, stop={call['stop_reason']})",
              "", "**System**", "", "```text", call["system_prompt"], "```", "",
              "**User**", "", "```text", call["user_prompt"], "```", "",
              "<details><summary>Response</summary>", "", "```json", call["response"], "```", "", "</details>", ""]
    return "\n".join(L) + "\n"
