"""CLI. Each stage runnable on its own so you can inspect the seam between them.

    python -m vira.cli companies
    python -m vira.cli select  chips --product "spicy chips" --verify
    python -m vira.cli analyze chips --product "spicy chips"
    python -m vira.cli remix   chips --product "spicy chips" --out out/remix.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from vira.models import Company
from vira.select import shortlist
from vira.supa import Supa, get_company
from vira.verify import verify_all

console = Console()


async def cmd_companies(_: argparse.Namespace) -> int:
    supa = Supa()
    rows = await supa.select(
        "companies", select="slug,name,website,status,categories(name)", order="created_at"
    )
    t = Table(title="companies")
    for col in ("slug", "name", "category", "website", "status"):
        t.add_column(col)
    for r in rows:
        t.add_row(
            r["slug"], r["name"],
            (r.get("categories") or {}).get("name", "—"),
            r.get("website") or "[dim]none[/dim]", r["status"],
        )
    console.print(t)
    return 0


async def _load(slug: str) -> tuple[Supa, Company]:
    supa = Supa()
    row = await get_company(supa, slug)
    if not row:
        console.print(f"[red]no company with slug {slug!r}[/red]")
        raise SystemExit(1)
    return supa, Company.from_row(row)


async def cmd_select(args: argparse.Namespace) -> int:
    supa, company = await _load(args.slug)
    console.print(f"[bold]{company.name}[/bold] · {company.category}")
    console.print(f"[dim]{company.bio}[/dim]\n")

    picked, rejected = await shortlist(supa, company, args.product)

    if args.verify:
        console.print("[dim]verifying sources…[/dim]")
        picked, dead = await verify_all(picked)
        for d in dead:
            rejected[d.drop_reason or "unverified"] = (
                rejected.get(d.drop_reason or "unverified", 0) + 1
            )

    t = Table(title=f"shortlist · {len(picked)} trends")
    for col, kw in (
        ("score", {"justify": "right"}), ("age", {"justify": "right"}),
        ("views", {"justify": "right"}), ("eng", {"justify": "right"}),
        ("format", {}), ("author", {}), ("caption", {"max_width": 46}),
    ):
        t.add_column(col, **kw)
    for tr in picked:
        t.add_row(
            f"{tr.trend_score:.0f}", f"{tr.age_days:.0f}d", f"{tr.views:,}",
            f"{tr.engagement_rate:.1%}", tr.format, f"@{tr.author}",
            tr.caption[:46].replace("\n", " "),
        )
    console.print(t)

    if rejected:
        r = Table(title="rejected — the panel judges actually care about")
        r.add_column("reason")
        r.add_column("n", justify="right")
        for reason, n in sorted(rejected.items(), key=lambda kv: -kv[1]):
            r.add_row(reason, str(n))
        console.print(r)

    if args.out:
        Path(args.out).write_text(
            json.dumps([tr.model_dump(mode="json") for tr in picked], indent=2)
        )
        console.print(f"[green]wrote {args.out}[/green]")
    return 0


async def cmd_analyze(args: argparse.Namespace) -> int:
    from vira.analyze import analyze_corpus, analyze_competitors

    supa, company = await _load(args.slug)
    picked, _ = await shortlist(supa, company, args.product)
    picked, _dead = await verify_all(picked)

    corpus = await analyze_corpus(company, args.product, picked)
    console.print("\n[bold]Dominant formats[/bold]")
    for f in corpus.dominant_formats:
        console.print(f"  · {f}")
    console.print("\n[bold]Recurring hooks[/bold]")
    for h in corpus.recurring_hooks:
        console.print(f"  · {h}")
    console.print(f"\n[bold]What top performers share[/bold]\n  {corpus.what_top_performers_share}")
    console.print(f"\n[bold]Whitespace[/bold]\n  {corpus.whitespace}")
    console.print(f"\n[dim]grounded in: {', '.join(corpus.citations)}[/dim]")

    if args.competitors:
        for finding in await analyze_competitors(
            company, args.competitors, picked
        ):
            console.print(f"\n[bold]{finding.competitor}[/bold]")
            if not finding.present_in_corpus:
                console.print("  [yellow]not present in the current corpus[/yellow]")
            else:
                console.print(f"  {finding.what_they_run}")
    return 0


async def cmd_remix(args: argparse.Namespace) -> int:
    from vira.analyze import analyze_corpus
    from vira.remix import build_remix
    from vira.score import score_remix

    supa, company = await _load(args.slug)
    picked, _ = await shortlist(supa, company, args.product)
    picked, _dead = await verify_all(picked)
    if not picked:
        console.print("[red]nothing survived selection — widen MAX_AGE_DAYS[/red]")
        return 1

    corpus = await analyze_corpus(company, args.product, picked)
    remix = await build_remix(company, args.product, picked, corpus)
    score = await score_remix(company, args.product, remix, picked)

    console.print(f"\n[bold cyan]{remix.hook}[/bold cyan]\n")
    for b in remix.beats:
        console.print(f"  [dim]{b.t:>5.1f}s[/dim]  {b.say}")
        console.print(f"          [dim italic]{b.shot or b.show}[/dim italic]")
    console.print(f"\n[bold]Caption[/bold] {remix.caption}")
    console.print(f"[bold]Tags[/bold] {' '.join('#' + h for h in remix.hashtags)}")
    console.print(f"[bold]CTA[/bold] {remix.cta}")
    console.print(f"\n[bold]Why[/bold] {remix.why_this_works}")
    console.print(f"[dim]grounded in: {', '.join(remix.grounded_in)}[/dim]")
    console.print(f"\n[bold]Score {score.overall}[/bold] {score.model_dump()}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(
                {
                    "company": company.model_dump(mode="json"),
                    "product": args.product,
                    "remix": remix.model_dump(mode="json"),
                    "score": score.model_dump(),
                    "sources": [t.model_dump(mode="json") for t in picked],
                },
                indent=2,
            )
        )
        console.print(f"[green]wrote {args.out}[/green]")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="vira")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("companies").set_defaults(fn=cmd_companies)

    for name, fn in (("select", cmd_select), ("analyze", cmd_analyze), ("remix", cmd_remix)):
        sp = sub.add_parser(name)
        sp.add_argument("slug")
        sp.add_argument("--product", required=True)
        sp.add_argument("--out")
        sp.add_argument("--competitors", nargs="*", default=[])
        sp.add_argument("--verify", action="store_true")
        sp.add_argument("-v", "--verbose", action="store_true")
        sp.set_defaults(fn=fn)

    args = p.parse_args(argv)
    if getattr(args, "verbose", False):
        logging.getLogger().setLevel(logging.INFO)
    return asyncio.run(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
