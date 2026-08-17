"""Optional bearer auth on the endpoints that spend money. Off unless asked for.

`CLAUDE.md` says this service is public by design, and that stays true: with
`VIRA_ENGINE_TOKEN` unset, nothing here does anything at all. Setting it turns
on a single shared secret over the four generation endpoints, which is what
Lovable asked for — they call the engine server-to-server and can hold a token;
a judge on a phone arriving from Terac cannot.

Three properties, and each is the reason for a decision below.

**The gate is a list of routes, not a rule about methods.** "All POSTs" would
catch `POST /v1/review-batches/{token}/votes`, and a panellist has no
credential — the batch token IS their credential. So the gated set is written
out, and a route that is not on it is open however it is added later. Enumerating
is the direction that fails safe for the people we cannot hand a token to.

**It is middleware, not a route dependency.** The generation endpoints live in
routers that also serve GETs (`/v1/lanes`, `/v1/videos/{id}`), so
`include_router(dependencies=[...])` would gate reads too. Middleware keeps the
whole policy legible in one place instead of scattering a decorator argument
across four files.

**CORS must stay outside it.** A 401 with no `Access-Control-Allow-Origin` shows
up in a browser as a network error with no status, which is a miserable thing to
debug. `install()` is therefore called BEFORE the CORS middleware is added, so
CORS ends up the outer layer and labels the rejection properly.
"""

from __future__ import annotations

import logging
import os
import re
import secrets

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

log = logging.getLogger(__name__)

ENV_VAR = "VIRA_ENGINE_TOKEN"

# Every endpoint that starts paid work. Exact, anchored patterns: a prefix match
# on "/v1/videos" would have caught the GETs beside it.
GATED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^/v1/videos/?$")),
    # Same cost as the line above — it runs the whole pipeline again — so it is
    # gated with it rather than left as an unauthenticated way to do the same thing.
    ("POST", re.compile(r"^/v1/videos/[^/]+/regenerate/?$")),
    ("POST", re.compile(r"^/v1/briefs/?$")),
    ("POST", re.compile(r"^/v1/ads/image/?$")),
)


def token() -> str | None:
    """The configured secret, or None when the service is open.

    Read per request rather than cached at import: the tests flip it in both
    directions, and a value captured at process start could not be turned off
    without a restart — which is the wrong way round for a feature whose default
    is "off".
    """
    return os.environ.get(ENV_VAR) or None


def enabled() -> bool:
    return token() is not None


def is_gated(method: str, path: str) -> bool:
    return any(method == m and p.match(path) for m, p in GATED)


def presented(header: str | None) -> str | None:
    """The token out of an `Authorization: Bearer <token>` header, if it is one."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def authorised(header: str | None) -> bool:
    expected = token()
    if expected is None:
        return True
    offered = presented(header)
    if offered is None:
        return False
    # Constant time, because the alternative leaks the token one byte at a time
    # to anyone willing to make a few thousand requests.
    return secrets.compare_digest(offered, expected)


class WriteTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not is_gated(request.method, request.url.path):
            return await call_next(request)
        if authorised(request.headers.get("authorization")):
            return await call_next(request)
        log.info("401 on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=401,
            content={
                "detail": (
                    "this endpoint needs Authorization: Bearer <token>. "
                    "Ask for the engine token."
                )
            },
            headers={"WWW-Authenticate": 'Bearer realm="vira-engine"'},
        )


def install(app: FastAPI) -> None:
    """Register the gate. Must run before CORS is added — see the module docstring."""
    app.add_middleware(WriteTokenMiddleware)
