#!/usr/bin/env python3
"""Talk to the Terac MCP from a terminal. The demo path when the UI is not ready.

    python terac_cli.py status                      # org, balance, opportunities
    python terac_cli.py tools                       # the 23 tools, live
    python terac_cli.py show <opportunity_id>       # one opportunity, verbatim
    python terac_cli.py publish <batch_id>          # DRY RUN — prints the payload
    python terac_cli.py publish <batch_id> --create # creates the DRAFT (free)
    python terac_cli.py responses <opportunity_id>  # submissions as they land
    python terac_cli.py launch <opportunity_id> --yes-spend-real-money

Everything except the last line is free and reversible. `launch` recruits and
pays a real panel out of a real balance and cannot be undone, which is why it
needs a flag that is hard to type by accident and impossible to type by
mistake — and why no other command, and no HTTP route, can reach it.

`publish` needs the API database (it reads the batch and its judge link);
everything else talks only to Terac.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from vira import terac

# The judge link must be the same string the API hands out, or the panel lands
# somewhere the batch is not. Same precedence as vira/api/routes/reviews.py:
# the frontend route when it is configured, this API's own JSON endpoint
# otherwise — so the link always resolves to something a judge can use.
JUDGE_BASE_URL = os.environ.get("VIRA_JUDGE_BASE_URL", "").rstrip("/")
API_BASE_URL = os.environ.get("VIRA_API_BASE_URL", "https://vira.ideaplaces.com").rstrip("/")


def judge_url(token: str) -> str:
    if JUDGE_BASE_URL:
        return f"{JUDGE_BASE_URL}/{token}"
    return f"{API_BASE_URL}/v1/review-batches/{token}"


def dump(value: object) -> None:
    print(json.dumps(value, indent=2, default=str))


async def cmd_status(_args: argparse.Namespace) -> int:
    summary = await terac.org_summary()
    print(f"organization : {summary['organization']}")
    print(f"balance      : {summary['balance']}")
    print(f"dashboard    : {summary['dashboard']}")
    tools = await terac.list_tools()
    print(f"tools        : {len(tools)} exposed by {terac.MCP_URL}")
    print("\nopportunities")
    for row in await terac.list_opportunities():
        print(
            f"  {row.get('id')}  {row.get('status','?'):<10}"
            f"  n={row.get('num_participants','?'):<4}  {row.get('title','')}"
        )
    return 0


async def cmd_tools(_args: argparse.Namespace) -> int:
    for tool in await terac.list_tools():
        summary = (tool.get("description") or "").strip().split("\n")[0][:96]
        print(f"{tool.get('name',''):<34} {summary}")
    return 0


async def cmd_show(args: argparse.Namespace) -> int:
    dump(await terac.get_opportunity(args.opportunity_id))
    return 0


async def cmd_publish(args: argparse.Namespace) -> int:
    # Imported here, not at module scope: `status`, `tools` and `responses`
    # must work on a laptop with no API_DATABASE_URL set.
    from vira.api import store

    batch = await store.get_batch_with_videos(batch_id=args.batch_id)
    if not batch:
        print(f"no review batch {args.batch_id}", file=sys.stderr)
        return 1

    url = judge_url(str(batch["public_token"]))
    payload = terac.judge_opportunity_payload(
        batch_id=str(batch["id"]),
        judge_url=url,
        title=args.title or (batch.get("title") or "Rate these video ads"),
        num_participants=args.participants,
        duration_minutes=args.minutes,
    )

    if not args.create:
        print(f"# DRY RUN — nothing sent. {len(batch.get('videos') or [])} videos, judge_url:")
        print(f"# {url}")
        dump({"tool": "terac_create_opportunity", "arguments": payload})
        print("\n# add --create to create the DRAFT (free, recruits nobody)")
        return 0

    created = await terac.create_judge_opportunity(
        batch_id=str(batch["id"]),
        judge_url=url,
        title=payload["title"],
        num_participants=args.participants,
        duration_minutes=args.minutes,
    )
    dump(created)
    cents = (created.get("pricing") or {}).get("total_cost_cents")
    if isinstance(cents, (int, float)):
        print(f"\nprice to launch: ${cents / 100:.2f}")
    print(
        "DRAFT created. Nothing has been spent and nobody recruited.\n"
        f"To go live: python terac_cli.py launch {created.get('id')} --yes-spend-real-money"
    )
    return 0


async def cmd_responses(args: argparse.Namespace) -> int:
    submissions = await terac.get_submissions(args.opportunity_id, args.status or None)
    print(f"{len(submissions)} submissions")
    for submission in submissions:
        print(
            f"\n{terac.submission_ref(submission)}  [{submission.get('status','?')}]\n"
            f"  {terac.submission_text(submission) or '(no text)'}"
        )
    return 0


async def cmd_launch(args: argparse.Namespace) -> int:
    if not args.yes_spend_real_money:
        print(
            "refusing: launching debits the live Terac balance and cannot be undone.\n"
            "Re-run with --yes-spend-real-money if that is what you want.",
            file=sys.stderr,
        )
        return 2
    dump(await terac.launch_draft(args.opportunity_id, i_understand_this_spends_real_money=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="terac_cli.py",
        description=__doc__,
        # The docstring is a worked example list; reflowing it turns the
        # commands into a paragraph nobody can copy a line out of.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="org, balance, opportunities").set_defaults(fn=cmd_status)
    sub.add_parser("tools", help="list the MCP tools").set_defaults(fn=cmd_tools)

    show = sub.add_parser("show", help="one opportunity, verbatim")
    show.add_argument("opportunity_id")
    show.set_defaults(fn=cmd_show)

    publish = sub.add_parser("publish", help="offer a review batch to a Terac panel")
    publish.add_argument("batch_id")
    publish.add_argument("--participants", type=int, default=5)
    publish.add_argument("--minutes", type=int, default=5, help="task length; drives the price")
    publish.add_argument("--title", default="")
    publish.add_argument("--create", action="store_true", help="create the DRAFT (free)")
    publish.set_defaults(fn=cmd_publish)

    responses = sub.add_parser("responses", help="submissions for an opportunity")
    responses.add_argument("opportunity_id")
    responses.add_argument("--status", default="")
    responses.set_defaults(fn=cmd_responses)

    launch = sub.add_parser("launch", help="SPENDS MONEY. Recruits a real panel.")
    launch.add_argument("opportunity_id")
    launch.add_argument("--yes-spend-real-money", action="store_true")
    launch.set_defaults(fn=cmd_launch)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(args.fn(args))
    except (terac.TeracError, terac.TeracNotConfigured) as exc:
        print(f"terac: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
