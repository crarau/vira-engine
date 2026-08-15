"""Stage 5 — A–E eval with evidence as a gate.

An ad the corpus does not support is worse than no ad, however good it sounds.
So `evidence` is not averaged in with the rest — below the floor and the whole
thing is dropped regardless of how strong the other four look.
"""

from __future__ import annotations

import logging

from vira.config import settings
from vira.llm import complete_json
from vira.models import Company, Remix, Score, Trend

log = logging.getLogger(__name__)

SYSTEM = """You grade an advertising concept against the source material it \
claims to be built on. You are sceptical by default.

Score 0-5 per dimension:
- relevance: does it serve THIS product, not just this industry
- specificity: a concrete filmable idea, not a category of idea
- actionability: shootable this week by two people with a phone
- differentiation: not what every other brand in the corpus is already doing
- evidence: is the borrowed mechanism actually present in the cited videos

Score evidence 0-2 if the cited videos do not support the claim. Be willing to \
fail a concept that reads well. JSON only."""

PROMPT = """# Brand
{company}

# The concept
Hook: {hook}
Beats:
{beats}
Why it supposedly works: {why}
Claims to be grounded in: {grounded}

# The cited source videos
{cited}

# Task
Return JSON:
{{
  "relevance": 0-5, "specificity": 0-5, "actionability": 0-5,
  "differentiation": 0-5, "evidence": 0-5,
  "notes": "one sentence on the weakest dimension"
}}"""


async def score_remix(
    company: Company, product: str, remix: Remix, trends: list[Trend]
) -> Score:
    by_key = {t.trend_key: t for t in trends}
    cited = [by_key[k] for k in remix.grounded_in if k in by_key]

    data = await complete_json(
        PROMPT.format(
            company=company.context(product),
            hook=remix.hook,
            beats="\n".join(f"  - {b.say}  [{b.shot or b.show}]" for b in remix.beats),
            why=remix.why_this_works,
            grounded=", ".join(remix.grounded_in) or "nothing",
            cited="\n\n".join(t.brief() for t in cited) or "(none cited)",
        ),
        system=SYSTEM,
        max_tokens=1500,
    )

    def clamp(key: str) -> float:
        try:
            return max(0.0, min(5.0, float(data.get(key, 0))))
        except (TypeError, ValueError):
            return 0.0

    return Score(
        relevance=clamp("relevance"),
        specificity=clamp("specificity"),
        actionability=clamp("actionability"),
        differentiation=clamp("differentiation"),
        evidence=clamp("evidence"),
    )


def disposition(score: Score) -> tuple[str, str | None]:
    """(disposition, drop_reason). Evidence gates before the average is consulted."""
    s = settings()
    if score.evidence < s.evidence_floor:
        return "dropped", "not supported by the cited source videos"
    if score.overall >= s.surface_threshold:
        return "surfaced", None
    if score.overall >= s.watchlist_threshold:
        return "watchlist", None
    return "dropped", f"scored {score.overall}, below the watchlist threshold"
