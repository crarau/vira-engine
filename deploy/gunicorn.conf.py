"""Gunicorn config — the API is reloaded, never restarted.

The team is using this URL. A `pkill uvicorn && start` drops every in-flight
request and kills any generation running at that moment, and generation runs for
90–350 seconds, so a restart during a busy afternoon silently destroys work
someone is waiting on.

Gunicorn's `SIGHUP` reload avoids that: it re-execs the master, starts workers on
the NEW code, and lets the OLD workers finish what they are already doing before
retiring. Nothing in flight is interrupted and no request is refused during the
handover.

    kill -HUP $(cat /tmp/vira-api.pid)

The subtlety that makes this correct here: a generation is a background asyncio
task, not a request. Gunicorn's graceful window counts request lifetime, not
task lifetime, so `graceful_timeout` is set to outlive the slowest job rather
than the slowest request. An agentic run peaks around 350s; 600 leaves headroom.
"""

import multiprocessing  # noqa: F401  (kept for the comment below)

bind = "127.0.0.1:8720"
worker_class = "uvicorn.workers.UvicornWorker"

# Two, not `cpu_count()`. The scarce resource on this box is cores for Remotion,
# not cores for HTTP — the API writes a job row and returns 202. It matters more
# that the render semaphore is sized PER PROCESS: four workers each allowing
# five concurrent renders is twenty renders on a box that fits five, and nothing
# in the code looks wrong while it thrashes.
workers = 2

# Long enough for the slowest agentic job to finish on the old worker.
graceful_timeout = 600
# A request never legitimately takes this long — generation is a background
# task — so anything that does is wedged and should be recycled.
timeout = 120
keepalive = 30

proc_name = "vira-api"
pidfile = "/tmp/vira-api.pid"

# Behind a Cloudflare tunnel, cloudflared always dials from loopback.
forwarded_allow_ips = "127.0.0.1"
proxy_allow_ips = "127.0.0.1"

accesslog = "-"
errorlog = "-"
loglevel = "info"
# Skip healthz: the tunnel probes it constantly and it drowns the real log.
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(M)sms'


def on_starting(server):
    server.log.info("vira-api starting · workers=%s · graceful=%ss", workers, graceful_timeout)


def on_reload(server):
    """Fires on SIGHUP. Old workers keep running until their jobs finish."""
    server.log.info("vira-api reloading on new code · in-flight work is preserved")
