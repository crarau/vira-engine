"""Create a company row in Lovable Cloud, owned by the agent account.

    python new_company.py --slug bramble --name Bramble \
        --category pets --bio "..." --mission "..." --website https://...

Sunday Oats was inserted ad hoc from a shell one-liner, which meant the next
company had to reconstruct the shape from a SELECT. This is that one-liner made
repeatable: it resolves the category slug to its id, signs in as the agent so RLS
permits the write, and refuses to clobber a slug that already exists.

Input quality is the single biggest lever on the eventual score — "Selling
chips" scores 2.6, a real bio with a stated mechanism scores 3.8 — so bio and
mission are required and are expected to name what the product actually does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from vira.config import settings
from vira.supa import Supa


async def main(a: argparse.Namespace) -> int:
    anon = Supa()

    if await anon.select("companies", slug=f"eq.{a.slug}", select="id"):
        print(f"company {a.slug!r} already exists — pick another slug")
        return 1

    cats = await anon.select("categories", slug=f"eq.{a.category}", select="id,name")
    if not cats:
        known = await anon.select("categories", select="slug", order="slug")
        print(f"no category {a.category!r}. known: {', '.join(c['slug'] for c in known)}")
        return 1
    category = cats[0]

    owner_id = settings().agent_user_id
    if not owner_id:
        print("AGENT_USER_ID is unset — the row needs an owner RLS will accept")
        return 1

    supa = await Supa.signed_in()
    row = {
        "owner_id": owner_id,
        "category_id": category["id"],
        "name": a.name,
        "slug": a.slug,
        "owner_name": a.owner_name,
        "bio": a.bio,
        "mission": a.mission,
        "website": a.website,
        "status": "published",
    }
    created = await supa.insert("companies", [row])
    print(json.dumps(created[0], indent=2))
    print(f"\n{a.name} · {category['name']}")
    print(f"next: python variants.py {a.slug} --product \"<product>\" -n 5")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--slug", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--category", required=True, help="category slug, e.g. pets")
    p.add_argument("--bio", required=True)
    p.add_argument("--mission", required=True)
    p.add_argument("--website", default=None)
    p.add_argument("--owner-name", default="Chip Rarau")
    sys.exit(asyncio.run(main(p.parse_args())))
