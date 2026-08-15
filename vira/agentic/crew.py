"""The crew — specialists exposed as tools, and the Director that calls them.

Architecture is the one in docs/AGENTIC-VIDEO-SYSTEM.md. The deviation: the loop
runs in Python against Azure gpt-5.4 rather than in TypeScript, because every
specialist is already a Python function. A TS Director would need an HTTP
boundary between it and its own tools, which buys nothing here.

State lives in a `Production` object that the tools mutate. The Director never
sees images or audio — it sees descriptions, durations and verdicts, and decides
what to do next. That keeps its context small and its decisions auditable.

Hard rules the Director cannot reach:
  - the evidence gate runs after the loop, in Python
  - word timings come from the synthesiser, never from the model
  - image budget and turn count are capped outside the conversation
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vira.agentic import cohesion
from vira.config import settings
from vira.director import VideoPlan, critique
from vira.imagegen import generate_shots
from vira.lanes import Lane
from vira.models import Company, CorpusAnalysis, Remix, Trend
from vira.remix import build_remix
from vira.voice import synthesize

log = logging.getLogger(__name__)

MAX_TURNS = 18
MAX_IMAGE_CALLS = 24        # a metered image API needs a ceiling, not a hope
WALL_CLOCK_BUDGET_S = 600


@dataclass
class Production:
    """Everything the crew is working on. Tools read and mutate this."""

    company: Company
    product: str
    lane: Lane
    corpus: CorpusAnalysis
    trends: list[Trend]
    out_dir: Path
    public_dir: Path

    plan: VideoPlan | None = None
    remix: Remix | None = None
    shots: list[dict] = field(default_factory=list)
    descriptions: list[str] = field(default_factory=list)
    mp3: Path | None = None
    duration: float = 0.0
    image_calls: int = 0
    style_contract: str = ""
    log: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        self.log.append(msg)
        log.info("  %s", msg)


# --------------------------------------------------------------------------
# Specialists. Each returns a short string — what the Director needs to decide,
# never the raw artefact.
# --------------------------------------------------------------------------


async def t_write_script(p: Production, args: dict) -> str:
    p.plan = p.plan or VideoPlan(
        structure=args.get("structure", ""), device=args.get("device", ""),
        beat_count=int(args.get("beat_count", 7)),
        target_seconds=int(args.get("target_seconds", 28)),
        pacing=args.get("pacing", ""),
        opening_move=args.get("opening_move", ""), turn_at=args.get("turn_at", ""),
    )
    steered = Company(**{
        **p.company.model_dump(),
        "mission": f"{p.company.mission}\n\nCREATIVE DIRECTION: {p.lane.brief}",
    })
    p.remix = await build_remix(steered, p.product, p.trends, p.corpus, p.plan)
    p.note(f"script written: {len(p.remix.beats)} beats")
    return json.dumps({
        "hook": p.remix.hook,
        "beats": [{"i": i, "say": b.say, "shot": b.shot, "motion": b.motion}
                  for i, b in enumerate(p.remix.beats)],
        "cta": p.remix.cta,
        "words": len(p.remix.narration().split()),
    })


async def t_revise_script(p: Production, args: dict) -> str:
    from vira.director import Critique, revise

    if not p.remix:
        return "no script yet"
    c = Critique(notes=[str(n) for n in args.get("notes", [])],
                 verdict=args.get("reason", ""),
                 weakest_beat=int(args.get("weakest_beat", 0) or 0))
    p.remix = await revise(p.remix, c, p.trends)
    p.note(f"script revised on {len(c.notes)} note(s)")
    return json.dumps({"hook": p.remix.hook,
                       "beats": [b.say for b in p.remix.beats]})


async def t_assign_motion(p: Production, args: dict) -> str:
    """MOTION. The Director picks treatments; we validate and apply them."""
    if not p.remix:
        return "no script yet"
    valid = {"stack", "punch", "slide", "pop", "banner"}
    cams = {"push", "pull", "pan", "punch", "hold"}
    applied = []
    prev = None
    for item in args.get("assignments", []):
        i = int(item.get("beat_index", -1))
        if not (0 <= i < len(p.remix.beats)):
            continue
        m = str(item.get("motion", "")).lower()
        if m not in valid:
            continue
        # Enforced here, not trusted to the model: consecutive repeats are the
        # exact failure mode this specialist exists to prevent.
        if m == prev:
            alternatives = [v for v in valid if v != m]
            m = alternatives[i % len(alternatives)]
        p.remix.beats[i].motion = m
        cam = str(item.get("camera", "")).lower()
        if cam in cams:
            p.remix.beats[i].camera = cam
        prev = m
        applied.append(f"{i}:{m}")
    p.note(f"motion assigned: {', '.join(applied)}")
    return f"applied {len(applied)} assignments: {', '.join(applied)}"


async def t_make_imagery(p: Production, args: dict) -> str:
    if not p.remix:
        return "no script yet"
    if p.image_calls + len(p.remix.beats) > MAX_IMAGE_CALLS:
        return "image budget exhausted — proceed with what exists"
    look = args.get("look") or p.lane.look
    p.shots = await generate_shots(
        p.company, p.product, p.remix, p.public_dir / "shots", look
    )
    p.image_calls += len(p.remix.beats)
    got = sum(1 for s in p.shots if s.get("file"))
    # Pin the contract so later single-frame fixes stay in the same shoot.
    p.style_contract = next((s.get("style_contract", "") for s in p.shots if s.get("style_contract")), "")
    p.note(f"imagery: {got}/{len(p.shots)} frames")
    return f"{got}/{len(p.shots)} frames generated"


async def t_regenerate_frame(p: Production, args: dict) -> str:
    """IMAGERY, surgical. The capability the straight-line pipeline lacks."""
    if not p.remix or not p.shots:
        return "nothing to regenerate"
    if p.image_calls >= MAX_IMAGE_CALLS:
        return "image budget exhausted"
    i = int(args.get("beat_index", -1))
    if not (0 <= i < len(p.remix.beats)):
        return f"no beat {i}"

    note = str(args.get("note", ""))
    beat = p.remix.beats[i]
    single = Remix(hook=p.remix.hook, beats=[beat], cta=p.remix.cta)
    steer = f"{p.lane.look} CORRECTION: {note}"
    out = await generate_shots(
        p.company, p.product, single, p.public_dir / "shots", steer,
        style=p.style_contract or None, name_offset=i,
    )
    p.image_calls += 1
    if out and out[0].get("file"):
        # name_offset already wrote it into this beat's slot.
        dst = p.public_dir / "shots" / f"shot{i:02d}.jpg"
        p.shots[i] = {**out[0], "file": f"shot{i:02d}.jpg",
                      "style_contract": p.style_contract}
        if i < len(p.descriptions):
            p.descriptions[i] = await cohesion.describe_image(dst)
        p.note(f"frame {i} regenerated: {note[:60]}")
        return f"beat {i} regenerated. now shows: {p.descriptions[i][:160] if i < len(p.descriptions) else 'n/a'}"
    return f"regeneration of beat {i} failed"


async def t_perform_voice(p: Production, args: dict) -> str:
    if not p.remix:
        return "no script yet"
    p.mp3, p.duration = await synthesize(p.remix, p.out_dir, p.lane)
    target = p.plan.target_seconds if p.plan else 28
    p.note(f"voice: {p.duration:.1f}s (target {target}s)")
    return json.dumps({
        "duration_s": round(p.duration, 1), "target_s": target,
        "over_by_s": round(p.duration - target, 1),
        "voice": p.lane.voice_note,
    })


async def t_check_cohesion(p: Production, args: dict) -> str:
    """COHESION. Looks at what was actually produced, not what was intended."""
    if not p.remix or not p.shots:
        return "nothing to check yet"
    p.descriptions = await cohesion.describe_all(p.shots, p.public_dir / "shots")
    target = p.plan.target_seconds if p.plan else 28
    result = await cohesion.check(p.remix, p.shots, p.descriptions, target, p.duration)
    n = len(result.get("mismatches", []))
    p.note(f"cohesion: {result.get('verdict','')[:80]} ({n} mismatch(es))")
    return json.dumps(result)


async def t_critique(p: Production, args: dict) -> str:
    if not p.remix or not p.plan:
        return "no script yet"
    c = await critique(p.remix, p.plan)
    p.note(f"critic: {c.verdict[:80]}")
    return json.dumps(c.model_dump())


TOOLS: dict[str, Any] = {
    "write_script": t_write_script,
    "revise_script": t_revise_script,
    "assign_motion": t_assign_motion,
    "make_imagery": t_make_imagery,
    "regenerate_frame": t_regenerate_frame,
    "perform_voice": t_perform_voice,
    "check_cohesion": t_check_cohesion,
    "critique_film": t_critique,
}


def schemas() -> list[dict]:
    def fn(name, desc, props, required):
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}}

    return [
        fn("write_script", "Write the script. Call FIRST. You choose the shape of the film.",
           {"structure": {"type": "string", "description": "e.g. 'single-take confession'"},
            "device": {"type": "string", "description": "the structural trick carrying it"},
            "beat_count": {"type": "integer", "description": "4-10. Vary this."},
            "target_seconds": {"type": "integer", "description": "12-40. Vary this."},
            "pacing": {"type": "string", "enum": ["accelerating", "front-loaded", "slow burn", "metronomic"]},
            "opening_move": {"type": "string", "description": "what happens in the first 1.5s"},
            "turn_at": {"type": "string", "description": "where the film changes gear"}},
           ["structure", "device", "beat_count", "target_seconds", "pacing"]),
        fn("assign_motion", "Assign a caption treatment and camera move to each beat. Match the treatment to what the line does.",
           {"assignments": {"type": "array", "items": {"type": "object", "properties": {
               "beat_index": {"type": "integer"},
               "motion": {"type": "string", "enum": ["stack", "punch", "slide", "pop", "banner"]},
               "camera": {"type": "string", "enum": ["push", "pull", "pan", "punch", "hold"]}},
               "required": ["beat_index", "motion"]}}},
           ["assignments"]),
        fn("make_imagery", "Generate one photograph per beat. Call after the script is settled.",
           {"look": {"type": "string", "description": "optional override of the lane's visual grade"}}, []),
        fn("perform_voice", "Synthesize narration. Returns real duration against your target.", {}, []),
        fn("check_cohesion", "Look at the frames that were ACTUALLY produced and report mismatches against the script.", {}, []),
        fn("critique_film", "Have a hostile first viewer watch it and report where it loses them.", {}, []),
        fn("regenerate_frame", "Regenerate ONE frame with a correction note. Cheap — prefer this over redoing everything.",
           {"beat_index": {"type": "integer"}, "note": {"type": "string", "description": "what was wrong and what it must show instead"}},
           ["beat_index", "note"]),
        fn("revise_script", "Rewrite the script against specific notes.",
           {"notes": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"}, "weakest_beat": {"type": "integer"}},
           ["notes"]),
    ]


DIRECTOR_INSTRUCTIONS = """You direct a 9:16 short-form video ad. You have a crew.

Your job is judgement, not execution. Decide what the film should be, delegate,
look at what came back, and fix what is wrong.

Order that works:
  1. write_script — you choose structure, device, beat count and length.
     Do NOT default to 7 beats at 28 seconds. That is the safe average and it is
     why generated ads all look alike. A confession wants few long beats; a
     social-proof cut wants many short ones.
  2. assign_motion — match treatment to function. A confession is not "punch".
     A punchline is not "stack". Never repeat a treatment on adjacent beats.
  3. perform_voice and make_imagery.
  4. check_cohesion — this shows you what the frames ACTUALLY contain, which is
     often not what you asked for. Take it seriously.
  5. Fix what it found. regenerate_frame is cheap and surgical; prefer it.
     Only revise_script for a real writing problem.
  6. critique_film once the visuals hold up.
  7. When cohesion reports no mismatches and the critic has nothing structural,
     reply with the single word DONE and stop.

You MUST call make_imagery and perform_voice before finishing. A film with no
frames cannot be rendered. If you are running low on turns, stop revising and
make sure those two have run.

If voice duration is more than 6 seconds over target, revise the script shorter.
Do not call make_imagery twice — use regenerate_frame for individual fixes.
Be decisive. You have limited turns."""


async def direct(p: Production) -> str:
    """Run the Director loop. Returns its closing statement."""
    from openai import AsyncAzureOpenAI

    s = settings()
    client = AsyncAzureOpenAI(
        azure_endpoint=s.azure_openai_endpoint or "",
        api_key=s.azure_openai_api_key or "",
        api_version="2024-10-21",
    )

    brief = (
        f"Brand: {p.company.name} — {p.company.category}\n{p.company.bio}\n\n"
        f"Product: {p.product}\n"
        f"Creative angle ({p.lane.name}): {p.lane.brief}\n"
        f"Visual grade: {p.lane.look}\n"
        f"Voice: {p.lane.voice_note}\n\n"
        f"What works in this category: {p.corpus.what_top_performers_share}\n"
        f"Nobody is doing: {p.corpus.whitespace}\n\n"
        f"Direct this film."
    )
    messages: list[dict] = [
        {"role": "system", "content": DIRECTOR_INSTRUCTIONS},
        {"role": "user", "content": brief},
    ]
    tools = schemas()
    started = time.monotonic()

    for turn in range(MAX_TURNS):
        if time.monotonic() - started > WALL_CLOCK_BUDGET_S:
            p.note("wall-clock budget reached")
            break

        resp = await client.chat.completions.create(
            model=s.agent_model, messages=messages, tools=tools,
        )
        msg = resp.choices[0].message
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": t.id, "type": "function",
                 "function": {"name": t.function.name, "arguments": t.function.arguments}}
                for t in (msg.tool_calls or [])
            ] or None,
        })

        if not msg.tool_calls:
            text = (msg.content or "").strip()
            p.note(f"director: {text[:120]}")
            return text

        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = TOOLS.get(name)
            log.info("turn %d → %s(%s)", turn + 1, name, str(args)[:110])
            try:
                out = await fn(p, args) if fn else f"unknown tool {name}"
            except Exception as exc:  # noqa: BLE001 - a tool failure is information
                out = f"ERROR: {exc}"
                p.note(f"{name} failed: {exc}")
            messages.append({"role": "tool", "tool_call_id": call.id, "content": str(out)[:6000]})

    return "MAX_TURNS reached"
