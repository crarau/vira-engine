# Deploying the API on chipdev

The engine runs where the cores are. `chipdev` is a 32-core / 62 GB Azure VM
(`dev-workstation-canada-v4`) that already renders three times faster than the
laptop — `render_remote.py` has been shipping work there since day one. Putting
the API on the same box removes the rsync round-trip entirely: a request
arrives, the render happens on local disk, and the mp4 is served from where it
was written.

Nothing here invents infrastructure. The edge, the DNS, the secret naming and
the shell conventions are the ones already in `ideaplaces-devops` — C3 solved
this exact problem (a service on chipdev that needs a public hostname) and this
is the same shape.

**The box is shared.** Seven GitHub Actions runner services live on it
(Ideaplaces x2, MentorlyMain x4, CatalyzeUp x1), plus C3 on 8347 and GitPipeline
local-dev on 7291. Nothing here may take a port they use, saturate the disk, or
restart docker out from under them. `setup.sh` prints the runner states at the
end of every run for that reason — if a deploy knocks one over you should learn
it from the deploy, not from a red CI badge an hour later.

```
browser
  → https://vira.ideaplaces.com
  → Cloudflare edge (TLS terminates here)
  → Cloudflare Tunnel (encrypted, OUTBOUND from the VM)
  → cloudflared on chipdev
  → 127.0.0.1:8720  uvicorn, 2 workers ──┬─→ Remotion renders (local disk)
                                         └─→ 127.0.0.1:15432  postgres (docker)
```

**No inbound port is opened on the box. No NSG rule changes. No certificate to
renew.** That is the whole reason for the tunnel, and it is what
`docs.ideaplaces.com/c3/tunnel` already documents for C3. There is no Caddy and
no nginx in this design; adding a TLS-terminating proxy on the box would mean
opening a port, and 443 is taken by sshd anyway.

## Ports, and why these

| Port | What | Why not the obvious one |
|---|---|---|
| `8720` | uvicorn, loopback only | 8000/8080/3000 are the first ports every Node and Java process on this box grabs. 8xxx-with-a-project-specific-number matches the local convention (C3 is 8347, GitPipeline 7291). Also below 32768, so the kernel will never hand it out as an ephemeral source port. |
| `15432` | Postgres, loopback only | 5432 is taken. `github-actions-runners/README.md` documents this exact collision — "a fixed `5496:5432` collides with the local dev DB on that port". 5432+10000 reads as "postgres, not the default one" at a glance. Not 55432: that sits inside the ephemeral range 32768-60999 and can intermittently fail to bind. |
| — | no inbound port | The tunnel is outbound. Nothing to open, nothing to firewall. |

Both uvicorn and Postgres bind `127.0.0.1` explicitly. The box has a public IP;
"the NSG will catch it" is not a bind policy.

## The hostname

**`vira.ideaplaces.com`** — confirmed unclaimed (NXDOMAIN, checked 2026-08-15).

What already points at chipdev, so you can see there is no collision:

| Hostname | Tunnel | Target |
|---|---|---|
| `chip.ideaplaces.dev` | `chip-dev` | `localhost:7291` (GitPipeline local dev) |
| `c3-chip.ideaplaces.com` | `c3-chipdev` | `localhost:8347` (C3) |
| `luca.ideaplaces.dev` | `luca-dev` | lucadev:7291 |

Worth correcting a common assumption: **`chip.ideaplaces.com` does not exist.**
The record is on the **`.dev`** zone, which is why it does not resolve or answer
HTTPS. It is also already occupied by GitPipeline on 7291, so it was never a
candidate for this API.

## Wiring the edge

DNS and tunnel resources are Terraform-managed in `ideaplaces-devops` —
`infrastructure/terraform/azure/environments/shared/cloudflare.tf`. Creating the
record in the Cloudflare dashboard would work and then be silently reverted by
the next `terraform apply`, so don't.

`deploy/cloudflared/vira-tunnel.tf` holds the copy-paste blocks. The short
version, and the recommended path:

1. Add one `ingress_rule` (`vira.ideaplaces.com` → `http://localhost:8720`) to
   the **existing** `cloudflare_zero_trust_tunnel_cloudflared_config.c3`, above
   the `http_status:404` catch-all — first match wins and the catch-all matches
   everything.
2. Add the `cloudflare_record.vira` CNAME to `<tunnel-id>.cfargotunnel.com`,
   `proxied = true`.
3. `terraform apply` from `infrastructure/terraform/azure/environments/shared`.

That is the entire change. Reusing the tunnel chipdev already runs means **no
second daemon, no new systemd unit, and no touching the box at all** — tunnel
config is remotely managed, so cloudflared picks up the new ingress on its own
and C3 never blips. It also avoids a real trap: `cloudflared service install`
writes `/etc/systemd/system/cloudflared.service`, and that unit already belongs
to the c3 tunnel.

If vira needs its own blast radius, `vira-tunnel.tf` also carries the dedicated
`vira-chipdev` tunnel — new Key Vault secret, second daemon, hand-written
`cloudflared-vira.service`.

**Nothing gates the hostname.** Once DNS is live the API is on the open
internet. C3 put magic-link auth in the app; the no-code option is a Cloudflare
Access policy on the hostname (the shared `cloudflare-api-token` already has
`Account.Access: Apps and Policies` Edit). Decide before it goes live.

## Secrets

Application secrets come from **Azure Key Vault `kv-zerohuman-hack`** (Chip-only
ACL). No secret value appears in this repo, in any file under `deploy/`, or in
any log line. `deploy/*.env` is gitignored.

```bash
az login
az keyvault secret show --vault-name kv-zerohuman-hack --name <name> --query value -o tsv
```

| Key Vault secret | Env var | Needed for |
|---|---|---|
| `anthropic-api-key` | `ANTHROPIC_API_KEY` | remix, score — the pipeline is dead without it |
| `gemini-api-key` | `GEMINI_API_KEY` | frame generation and vision cohesion |
| `elevenlabs-api-key` | `ELEVENLABS_API_KEY` | voice, and therefore all timing |
| `supabase-url` | `SUPABASE_URL` | the corpus |
| `supabase-publishable-key` | `SUPABASE_PUBLISHABLE_KEY` | the corpus (public by design, RLS-bound) |
| `agent-email` | `AGENT_EMAIL` | writes back to Lovable Cloud |
| `agent-password` | `AGENT_PASSWORD` | writes back to Lovable Cloud |
| `agent-user-id` | `AGENT_USER_ID` | RLS matches it against `companies.owner_id` |
| `azure-openai-endpoint` | `AZURE_OPENAI_ENDPOINT` | the agentic crew |
| `azure-openai-api-key` | `AZURE_OPENAI_API_KEY` | the agentic crew |
| `stripe-secret-key` | `STRIPE_SECRET_KEY` | billing |
| `terac-api-key` | `TERAC_API_KEY` | Terac MCP |

Two vaults, on purpose. `kv-zerohuman-hack` holds this project's *application*
keys, with the flat names the hackathon vault already uses. Anything that is
**IdeaPlaces infrastructure** — a tunnel secret, for instance — belongs in
`kv-ideaplaces` beside `cloudflare-account-id` and `cloudflare-api-token`, and
follows the devops naming convention `{service}-{purpose}-{project-code-name}`:
`cloudflare-tunnel-secret-vira`. Only Option B in `vira-tunnel.tf` needs one.

Two values are in **neither** vault, because they are generated on the box and
never leave it: `VIRA_DB_PASSWORD` and `API_DATABASE_URL`. `setup.sh` writes
both on first run — one generated password, written into both lines, so the API
and Postgres cannot disagree. Do not hand-edit one without the other.

```bash
ssh chipdev
cd ~/vira-engine
./deploy/setup.sh                    # creates deploy/vira-api.env with empty slots
vim deploy/vira-api.env              # paste values from Key Vault
sudo systemctl restart vira-api
```

Every key is read at runtime from `EnvironmentFile`, so rotating one is an edit
plus a restart — never a rebuild.

## Deploy

```bash
ssh chipdev
cd ~/vira-engine
git pull
./deploy/setup.sh
```

In order: install Python 3.12 via deadsnakes if absent (the box ships 3.10 and
the models will not import under it); create or rebuild `.venv`; install
`requirements.txt`; create `deploy/vira-api.env` if missing; bring up Postgres 16
under docker compose and wait for its healthcheck; apply `sql/schema.sql`;
install and restart the systemd unit; curl `/health` on loopback; report on the
tunnel; print the seven runner states.

Re-running is safe. An existing venv on the right interpreter is reused, an
existing env file is never overwritten, an unchanged unit file is not
reinstalled, and compose converges rather than recreates.

**`sql/schema.sql` is applied on every run, so it must be idempotent** —
`CREATE TABLE IF NOT EXISTS`, `CREATE OR REPLACE`, `ALTER TABLE ... IF NOT
EXISTS`. `psql` runs with `ON_ERROR_STOP=1`, so a non-idempotent statement fails
the deploy loudly instead of leaving half a schema behind.

`setup.sh` never creates DNS or tunnels. Step 8 only *verifies* the edge and
tells you what is missing, because those resources belong to Terraform.

## Health

```bash
# on the box
curl -s http://127.0.0.1:8720/health
systemctl is-active vira-api
systemctl is-active cloudflared
docker inspect -f '{{.State.Health.Status}}' vira-postgres

# from anywhere, once the ingress rule is applied
curl -s https://vira.ideaplaces.com/health
```

Reading edge failures — the status code says which layer broke:

| Code | Meaning |
|---|---|
| `000` / NXDOMAIN | DNS record not applied yet |
| `530` | Cloudflare has the hostname, no tunnel is connected — `cloudflared` is down |
| `502` | Tunnel is up but nothing is listening on the port in the ingress rule — wrong port, or `vira-api` is down |
| `200` | All four layers are good |

Triage on the box:

```bash
systemctl status vira-api --no-pager -l
docker compose -f deploy/docker-compose.yml --env-file deploy/vira-api.env ps
journalctl -u vira-api -n 50 --no-pager
```

If `/health` answers but generation fails, check node first. Remotion is invoked
through `npx`, and a systemd service does not inherit the login shell's nvm
setup. `setup.sh` writes a PATH drop-in with the resolved node bin directory;
confirm it survived:

```bash
systemctl show vira-api -p Environment
```

## Logs

```bash
journalctl -u vira-api -f                       # follow
journalctl -u vira-api --since "10 min ago"     # recent
journalctl -u vira-api -p err --since today     # errors only
sudo journalctl -u cloudflared -f               # the edge
docker logs -f vira-postgres                    # database
```

Journald keeps this for the machine's retention; nothing writes an application
log file of its own, so there is one place to look.

Individual render logs still land in `/tmp/render-<variant>.log`, the same
convention `render_remote.py` uses. When a video is blank rather than missing,
that file plus an extracted frame is the only thing that finds it — a Remotion
render exits 0 on 24 seconds of black (`docs/BUILD-LOG.md`, finding 4).

## Rollback

Code, schema, and data roll back separately. In practice you almost always want
the first one only.

**Code** — roughly twenty seconds:

```bash
ssh chipdev
cd ~/vira-engine
git log --oneline -5
git checkout <last-good-sha>
./deploy/setup.sh
```

`setup.sh` on an older commit reinstalls that commit's dependencies and unit
file, so this is a real rollback and not just a checkout.

**Stop serving immediately**, without deciding anything else:

```bash
sudo systemctl stop vira-api        # renders in flight get 180s to finish
sudo systemctl disable vira-api     # and stay down across a reboot
```

The public hostname then returns 502 from Cloudflare. To take the hostname down
cleanly instead, remove the ingress rule in `cloudflare.tf` and apply — do not
stop `cloudflared`, which would also take C3 offline.

**Schema.** There is no down-migration. `sql/schema.sql` only moves forward, so
an incompatible change is fixed by writing the next forward statement, not by
reverting the file. Snapshot first if a change is genuinely destructive:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/vira-api.env \
  exec -T postgres pg_dump -U vira -d vira > ~/vira-$(date +%F-%H%M).sql
```

**Data, nuclear.** Destroys every job and media row this API owns. The corpus is
untouched — it lives in Lovable Cloud and this database has never held it:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/vira-api.env down -v
./deploy/setup.sh
```

Note the trap in `down -v`: it deletes the `vira-pgdata` volume, and the new
volume initialises with whatever `VIRA_DB_PASSWORD` is in the env file at that
moment. Keeping the env file is what makes the rebuild work. Deleting the env
file *and* the volume is fine (setup.sh generates a fresh pair); deleting the
env file while keeping the volume gives a password mismatch and a confusing
authentication failure.

## What the systemd unit assumes

Written down because these break silently if the API layer evolves away from
them.

- **Two uvicorn workers, not more.** Full reasoning is in the unit file. Short
  version: the scarce resource on this box is cores for Remotion, not cores for
  HTTP, and a render semaphore sized to the machine is sized *per process* —
  four workers each admitting five renders is twenty renders on a box that fits
  five.
- **Shared state lives in Postgres.** With two workers an in-memory job dict is
  two job dicts. Job claiming needs `SELECT ... FOR UPDATE SKIP LOCKED` or both
  workers run the same job. If the API keeps its queue in process memory, set
  `--workers 1` and fix it properly afterwards.
- **Absolute URLs come from the forwarded host.** Behind the tunnel the request
  URL the framework sees is the internal `127.0.0.1:8720` bind, not
  `vira.ideaplaces.com`. Any `/media` link the API hands back must be built from
  the `Host` / `X-Forwarded-Host` header. C3 hit exactly this; it is written up
  in `docs.ideaplaces.com/c3/tunnel`. The unit passes `--proxy-headers
  --forwarded-allow-ips 127.0.0.1` so the headers are trusted from cloudflared
  and from nowhere else.
- **The app must start with an unreachable database.** `After=docker.service`
  orders the unit; it does not wait for the container inside docker to accept
  connections. Crashing on a cold boot because Postgres needed four more seconds
  turns a reboot into an incident.
- **`WorkingDirectory` is `/home/chipdev/vira-engine`.** `vira/config.py` reads
  `.env` relative to the working directory and Remotion resolves `video/public`
  the same way.
