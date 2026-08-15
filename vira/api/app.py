"""The public HTTP surface of the engine.

Everything above this line is a CLI: `variants.py`, `agentic_video.py`,
`new_company.py`. That is fine for one operator on one laptop and useless to
anyone else. This turns the same pipeline into a service a hosted frontend can
drive, which is the difference between a demo someone watches and a product
someone uses.

Three decisions worth stating, because they are the ones a reader will question.

**CORS is wide open.** The frontend is a Lovable app on a generated origin that
changes whenever it is redeployed, and the API holds no cookies or session — the
judge link is an unguessable token, not an authenticated session. An origin
allowlist would therefore break the client regularly while protecting nothing.
Credentials are off, which is what makes the wildcard legal and what keeps a
browser from ever attaching ambient auth to a cross-origin call.

**mp4s are served by this process from out/.** Not S3, not a CDN. The renders
already land in `out/<slug>/v<NNN>-<stamp>/`, and a static mount over that tree
means a video made from the CLI is reachable over HTTP with no upload step and
no second source of truth. Put a CDN in front when the traffic justifies it.

**Generation never runs inside a request.** 74s deterministic, ~350s with the
crew. POST /v1/videos writes a job and returns 202; the work happens in a task
owned by `vira.api.worker`.

Run it:

    uvicorn vira.api.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from vira.api.db import init_db
from vira.api.routes import companies, jobs, reviews, videos
from vira.api.worker import OUT_DIR

log = logging.getLogger("vira.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    for noisy in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    await init_db()
    log.info("vira api up · media from %s", OUT_DIR)
    yield


app = FastAPI(
    title="vira-engine",
    version="1.0.0",
    summary="Generate grounded short-form video ads, then have humans rank them.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # Wildcard origins and credentials are mutually exclusive per the CORS spec,
    # and a browser silently drops the header rather than telling you. This API
    # authenticates nothing, so credentials off is both correct and required.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Lets a frontend read Content-Range on ranged video requests.
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)

# Must exist before the mount: StaticFiles refuses to start on a missing dir,
# and on a clean deploy nothing has rendered yet.
OUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=OUT_DIR), name="media")

app.include_router(companies.router)
app.include_router(videos.router)
app.include_router(jobs.router)
app.include_router(reviews.router)


@app.exception_handler(LookupError)
async def not_found(request: Request, exc: LookupError) -> JSONResponse:
    """The store raises LookupError when a slug resolves to no row. That is a 404.

    Narrowed to the exact type on purpose: KeyError and IndexError inherit from
    LookupError and mean a bug in this service, not a missing row. Turning those
    into a polite 404 would hide them for as long as the frontend kept working.
    """
    if type(exc) is not LookupError:
        raise exc
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def malformed_id(request: Request, exc: ValueError) -> JSONResponse:
    """Ids are coerced to UUID at the store boundary; a bad one is a 422, not a 500.

    Only requests reach this handler, so it cannot swallow a ValueError raised
    inside a background generation — those land on the job row instead.
    """
    log.info("422 on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=422, content={"detail": f"malformed identifier: {exc}"})


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, object]:
    """Liveness for the platform's health check. Deliberately touches nothing.

    A health check that queries the database or Lovable Cloud turns someone
    else's outage into a restart loop here.
    """
    return {"ok": True, "service": "vira-engine", "media_root": str(OUT_DIR)}
