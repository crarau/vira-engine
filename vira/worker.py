"""The unattended loop — the thing that makes the autonomy claim true.

Wakes on an interval, walks every eligible company, runs the pipeline, writes
the result back to Lovable Cloud. Nobody triggers it and nobody approves its
output. Runs as a Render background worker (`python -m vira.worker`).

RLS note: the agent account can only write remixes for companies it owns, so by
default the loop processes exactly those. That needs no schema change and works
today. Set PROCESS_ALL_PUBLISHED=true once the agent-write policy from SPEC.md
exists, and it will cover everyone's companies.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from vira.analyze import analyze_corpus
from vira.config import settings
from vira.models import Company
from vira.remix import build_remix
from vira.score import disposition, score_remix
from vira.select import shortlist
from vira.supa import Supa, SupabaseError
from vira.verify import verify_all

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("worker")

TICK_SECONDS = int(os.environ.get("TICK_SECONDS", "1800"))
PROCESS_ALL = os.environ.get("PROCESS_ALL_PUBLISHED", "false").lower() == "true"
# Hard ceiling per tick. An unattended loop on a metered LLM is how you wake up
# to an exhausted plan.
MAX_COMPANIES_PER_TICK = int(os.environ.get("MAX_COMPANIES_PER_TICK", "3"))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


async def process(supa: Supa, company: Company) -> dict:
    """One company, one pass. Returns a summary suitable for logging."""
    product = company.ad_themes[0] if company.ad_themes else company.name

    picked, rejected = await shortlist(supa, company, product)
    picked, dead = await verify_all(picked)
    if not picked:
        return {
            "company": company.slug,
            "status": "no_material",
            "rejected": rejected,
            "dead": len(dead),
        }

    corpus = await analyze_corpus(company, product, picked)
    remix = await build_remix(company, product, picked, corpus)
    score = await score_remix(company, product, remix, picked)
    dispo, reason = disposition(score)

    summary = {
        "company": company.slug,
        "status": dispo,
        "score": score.overall,
        "evidence": score.evidence,
        "reason": reason,
        "sources": len(picked),
        "rejected": rejected,
    }

    if dispo == "dropped":
        log.info("%s: dropped (%s)", company.slug, reason)
        return summary

    if DRY_RUN:
        log.info("%s: DRY_RUN, not writing", company.slug)
        return summary

    try:
        await supa.insert(
            "company_remixes",
            [
                {
                    "company_id": company.id,
                    "owner_id": os.environ["AGENT_USER_ID"],
                    # The corpus lives in `trends`, not `prescripts`, but the
                    # existing table requires a prescript_key FK. Until an
                    # observed_ads table exists, borrow the top trend's key is
                    # NOT possible (FK mismatch) — so this writes only when a
                    # prescript_key is supplied via env for the demo path.
                    "prescript_key": os.environ["DEMO_PRESCRIPT_KEY"],
                    "platform": "tiktok",
                    "hook": remix.hook,
                    "script": "\n".join(
                        f"{b.start_s or b.t:.1f}s  {b.say}  [{b.shot}]"
                        for b in remix.beats
                    ),
                    "caption": remix.caption,
                    "hashtags": remix.hashtags,
                    "differentiator": remix.why_this_works,
                }
            ],
        )
        summary["written"] = True
    except (SupabaseError, KeyError) as exc:
        log.warning("%s: could not write remix (%s)", company.slug, exc)
        summary["written"] = False

    return summary


async def tick() -> None:
    started = datetime.now(timezone.utc)

    # Read-only fallback. Without an agent account the loop still runs the whole
    # pipeline unattended and logs its verdicts — it just cannot persist them.
    # That is a degraded mode, not a dead one, and it is what lets the worker
    # prove itself before the agent account and RLS policy exist.
    global DRY_RUN
    try:
        supa = await Supa.signed_in()
    except SupabaseError as exc:
        log.warning("no agent credentials (%s) — running READ-ONLY this tick", exc)
        supa = Supa()
        DRY_RUN = True

    agent_id = os.environ.get("AGENT_USER_ID")
    if PROCESS_ALL or not agent_id:
        rows = await supa.select(
            "companies",
            status="eq.published",
            select="id,name,slug,bio,mission,website,owner_name,category_id,"
            "categories(name),company_insights(summary,positioning,tone,keywords,ad_themes)",
        )
    else:
        rows = await supa.select(
            "companies",
            owner_id=f"eq.{agent_id}",
            select="id,name,slug,bio,mission,website,owner_name,category_id,"
            "categories(name),company_insights(summary,positioning,tone,keywords,ad_themes)",
        )

    companies = [Company.from_row(r) for r in rows][:MAX_COMPANIES_PER_TICK]
    log.info("tick: %d compan%s", len(companies), "y" if len(companies) == 1 else "ies")

    for company in companies:
        try:
            log.info("processing %s", company.slug)
            log.info("  → %s", await process(supa, company))
        except Exception:  # noqa: BLE001 - one company must not stop the loop
            log.exception("failed on %s", company.slug)

    log.info(
        "tick done in %.1fs", (datetime.now(timezone.utc) - started).total_seconds()
    )


async def main() -> None:
    s = settings()
    log.info(
        "vira worker up · tick %ss · model %s · dry_run=%s",
        TICK_SECONDS, s.llm_model, DRY_RUN,
    )
    while True:
        try:
            await tick()
        except Exception:  # noqa: BLE001 - the loop outlives any single failure
            log.exception("tick failed")
        await asyncio.sleep(TICK_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
