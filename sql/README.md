# The engine's own database

`schema.sql` is the engine-local Postgres: jobs, videos, recipes, verbatim
prompts, assets and human review. It is **not** the Lovable Cloud (Supabase)
schema — that project exposes no connection string, and the two databases never
join. See the header of `schema.sql` for why each table is shaped the way it is.

## Start it

```bash
sql/dev-db.sh start
```

That wraps one container command. The exact command, if you would rather run it
by hand:

```bash
docker run -d \
  --name vira-pg \
  -e POSTGRES_USER=vira \
  -e POSTGRES_PASSWORD="${VIRA_PG_PASSWORD:-vira-local-dev}" \
  -e POSTGRES_DB=vira \
  -p 127.0.0.1:55432:5432 \
  -v vira-pg-data:/var/lib/postgresql/data \
  postgres:16
```

Then point the engine at it:

```bash
export API_DATABASE_URL="postgresql://vira:vira-local-dev@127.0.0.1:55432/vira"
```

Three choices in that command are deliberate:

- **Port 55432, not 5432.** A Homebrew `postgresql@14` is often already
  listening on the default port. Silently applying this schema into the wrong
  cluster is a far worse failure than a refused connection.
- **Bound to `127.0.0.1`.** Without the address prefix Docker publishes on all
  interfaces, and this database has a known password.
- **A named volume.** `stop` keeps the data; only `sql/dev-db.sh reset` throws
  it away.

The password is read from `VIRA_PG_PASSWORD` and defaults to `vira-local-dev`.
That default exists so a laptop works with no setup — it is only ever reachable
from `127.0.0.1`. Deployed environments pass `API_DATABASE_URL` and never see
it.

### Podman instead of Docker

`dev-db.sh` uses whichever of `docker` / `podman` is on PATH; podman is
command-line compatible for everything here. Podman needs its VM up first, and
needs the registry spelled out because it will not guess a short name:

```bash
podman machine start
podman run -d --name vira-pg ... docker.io/library/postgres:16
```

### No container runtime

Homebrew Postgres works too — use a separate cluster on the same non-default
port rather than the one on 5432:

```bash
brew install postgresql@16
initdb -D /opt/homebrew/var/vira-pg
pg_ctl -D /opt/homebrew/var/vira-pg -o "-p 55432 -k /tmp" -l /tmp/vira-pg.log start
createdb -h 127.0.0.1 -p 55432 vira
psql -h 127.0.0.1 -p 55432 -v ON_ERROR_STOP=1 -d vira -f sql/schema.sql
```

## Apply the schema

```bash
sql/dev-db.sh apply     # or: psql -v ON_ERROR_STOP=1 -d "$API_DATABASE_URL" -f sql/schema.sql
```

`vira.api.db.init_db()` runs the same file on every boot, and the deploy
re-applies it on every deploy, so **the file has to stay idempotent**. Every
statement is `CREATE ... IF NOT EXISTS`; nothing drops or rewrites a column. A
real change gets a new numbered file next to `schema.sql`, never an edit to it.

Idempotency is verified, not assumed — `tests/test_store.py::test_schema_is_idempotent`
applies the file a second time against the already-populated database and fails
if anything errors or if a row disappears.

## Test against it

```bash
sql/dev-db.sh start
.venv/bin/python -m pytest tests/test_store.py -v
```

The suite talks to the real database on purpose: jsonb round-trips, `ON
CONFLICT` upserts, `FILTER` aggregates and transaction rollback are the things
being tested, and a stub would confirm none of them. If nothing is listening it
skips rather than fails, so the rest of the suite still runs offline.

## Other commands

| | |
|---|---|
| `sql/dev-db.sh status` | running / stopped / not created |
| `sql/dev-db.sh psql` | interactive shell |
| `sql/dev-db.sh url` | print the connection string |
| `sql/dev-db.sh stop` | stop, keeping the volume |
| `sql/dev-db.sh reset` | destroy the container **and its data** |
