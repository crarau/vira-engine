"""Connection plumbing for the engine's own Postgres.

One engine per process, created lazily on first use so importing this module
costs nothing and a CLI run that never touches the API never opens a socket.

Three things here are not obvious and all three have bitten this stack before:

**The URL has to be rewritten.** Render (and Heroku, and Supabase's pooler)
hand out `postgres://user:pw@host/db?sslmode=require`. SQLAlchemy needs the
driver in the scheme, and asyncpg rejects libpq-only query parameters outright
rather than ignoring them, so `sslmode` is translated into asyncpg's own `ssl`
connect argument instead of being passed through.

**jsonb round-trips as text unless you say otherwise.** Raw `text()` SQL gives
SQLAlchemy no column type to work from, so asyncpg's default codec would hand
back a JSON *string* for every jsonb column. A per-connection codec fixes both
directions at once, which is why store.py passes plain dicts and lists.

**Schema DDL runs on the raw connection.** schema.sql is many statements; the
extended query protocol SQLAlchemy uses allows exactly one per execute. The
asyncpg connection underneath speaks the simple protocol, which does not care.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from vira.config import settings

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class DatabaseNotConfigured(RuntimeError):
    pass


def _normalise(url: str) -> tuple[str, dict]:
    """Return (sqlalchemy url, connect_args) for a libpq-flavoured URL."""
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"

    # sslmode is libpq's spelling; asyncpg wants ssl= on the connect call, and
    # it accepts libpq's mode names verbatim — so the value is handed straight
    # through rather than collapsed into a bool.
    #
    # ssl=True is NOT a synonym for "require": asyncpg reads True as
    # verify-full, which checks the hostname against a CA chain it looks for in
    # ~/.postgresql/root.crt. Every managed provider that hands out
    # ?sslmode=require — Render, Heroku, the Supabase pooler — serves a
    # certificate that fails exactly that check, so the boolean turned a
    # working connection string into a CERTIFICATE_VERIFY_FAILED at boot.
    # libpq's "require" means encrypt without verifying, and the string says so.
    keep, connect_args = [], {}
    for pair in parts.query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        if key == "sslmode":
            connect_args["ssl"] = value
        else:
            keep.append(pair)

    return urlunsplit((scheme, parts.netloc, parts.path, "&".join(keep), parts.fragment)), connect_args


def _register_json_codec(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection, _record) -> None:  # noqa: ANN001
        for pg_type in ("json", "jsonb"):
            dbapi_connection.await_(
                dbapi_connection.driver_connection.set_type_codec(
                    pg_type,
                    encoder=json.dumps,
                    decoder=json.loads,
                    schema="pg_catalog",
                )
            )


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    raw = settings().api_database_url
    if not raw:
        raise DatabaseNotConfigured(
            "API_DATABASE_URL is unset. This is the engine's own Postgres, not "
            "the Lovable Cloud project — set it in .env before starting the API."
        )

    url, connect_args = _normalise(raw)
    _engine = create_async_engine(
        url,
        connect_args=connect_args,
        # A render box runs a handful of workers, not a web farm. A small pool
        # that recycles beats a large one that holds connections a managed
        # Postgres will cut from under us.
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    _register_json_codec(_engine)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """ORM session, for callers that want one. store.py does not."""
    async with session_factory()() as s:
        yield s


@asynccontextmanager
async def connection() -> AsyncIterator[AsyncConnection]:
    """A connection with an open transaction, committed on clean exit.

    This is what store.py runs on: the queries are plain SQL and an identity
    map would only get in the way.
    """
    async with get_engine().begin() as conn:
        yield conn


async def init_db(schema_path: Path | None = None) -> None:
    """Apply sql/schema.sql. Safe on every boot — the file is idempotent."""
    path = schema_path or SCHEMA_PATH
    ddl = path.read_text()
    async with get_engine().begin() as conn:
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(ddl)
    log.info("schema applied from %s", path)


async def close_db() -> None:
    """Release the pool. Call from the API's shutdown hook."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
