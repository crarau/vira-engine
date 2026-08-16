#!/usr/bin/env bash
#
# Publish the local engine to the box behind https://vira.ideaplaces.com.
#
#   deploy/publish.sh              commit nothing, ship what is already pushed
#   deploy/publish.sh -m "message" commit everything first, then ship
#
# The box serves a public URL that other people are using, so this script is
# deliberately conservative: it refuses to ship a dirty tree without a commit
# message, health-checks before and after, and rolls back to the previous commit
# if the new one will not come up.
#
# It does NOT touch the tunnel or DNS. Those are Terraform in ideaplaces-devops
# and a dashboard edit gets reverted by the next apply.

set -euo pipefail

HOST=chipdev
REMOTE='$HOME/vira-engine'   # single-quoted: must expand on the box, not here
PORT=8720
PUBLIC=https://vira.ideaplaces.com
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

blue()  { printf '\033[0;34m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }

cd "$HERE"

# --- 1. local state -------------------------------------------------------
if [[ -n "$(git status --porcelain)" ]]; then
  if [[ "${1:-}" == "-m" && -n "${2:-}" ]]; then
    blue "==> committing local changes"
    git add -A
    git -c user.name="Chip" commit -q -m "$2"
  else
    red "working tree is dirty. Commit first, or: deploy/publish.sh -m \"message\""
    git status --short
    exit 1
  fi
fi

blue "==> running tests before shipping"
if ! .venv/bin/python -m pytest -q >/tmp/publish-tests.log 2>&1; then
  red "tests failed — not shipping. See /tmp/publish-tests.log"
  tail -20 /tmp/publish-tests.log
  exit 1
fi
green "    $(tail -1 /tmp/publish-tests.log)"

blue "==> pushing to origin"
git push -q origin main
LOCAL_SHA="$(git rev-parse --short HEAD)"
green "    $LOCAL_SHA"

# --- 2. remote update -----------------------------------------------------
blue "==> updating $HOST"
PREV_SHA="$(ssh "$HOST" "cd $REMOTE && git rev-parse --short HEAD")"
echo "    was $PREV_SHA"

ssh "$HOST" "bash -s" <<REMOTE_SCRIPT
set -euo pipefail
cd $REMOTE
git fetch -q origin
git reset -q --hard origin/main
# Only reinstall when the dependency set actually moved; pip is slow and this
# runs on every publish.
if ! git diff --quiet $PREV_SHA HEAD -- requirements.txt 2>/dev/null; then
  echo "    requirements changed, reinstalling"
  ./.venv312/bin/pip install -q -r requirements.txt
fi
# Schema is idempotent by design, so applying every time is safe and means a
# migration can never be forgotten.
docker exec -i vira-pg psql -q -U vira -d vira -v ON_ERROR_STOP=1 < sql/schema.sql
REMOTE_SCRIPT

# --- 3. reload and verify ---------------------------------------------------
# SIGHUP, not restart. The team is using this URL and a generation runs for
# minutes; killing the process throws away work someone is waiting on. Gunicorn
# starts workers on the new code and retires the old ones only once they are
# idle. Falls back to a cold start if nothing is running yet.
blue "==> reloading the API (no downtime)"
ssh "$HOST" "bash -s" <<REMOTE_SCRIPT
set -uo pipefail
# systemd owns the process now. A backgrounded ssh command does not survive the
# session closing, which is how a stale pre-Azure uvicorn kept port 8720 while a
# publish reported success — the health check was answered by the OLD process.
sudo systemctl reload-or-restart vira-api
for i in \$(seq 1 40); do
  curl -sf -o /dev/null -m 2 http://127.0.0.1:$PORT/healthz && exit 0
  sleep 2
done
echo "LOCAL HEALTHCHECK FAILED"; sudo journalctl -u vira-api -n 30 --no-pager; exit 1
REMOTE_SCRIPT
set -uo pipefail
cd $REMOTE
set -a; . ./.env; set +a

PID=\$(cat /tmp/vira-api.pid 2>/dev/null || true)
if [ -n "\$PID" ] && kill -0 "\$PID" 2>/dev/null; then
  echo "    HUP -> \$PID (in-flight jobs keep running on the old workers)"
  kill -HUP "\$PID"
else
  echo "    nothing running, cold start"
  # Kill BOTH runtimes. A legacy `uvicorn vira.api.app` does not match a
  # gunicorn pattern, so an old process kept port 8720, gunicorn failed to bind
  # and died, and the health check passed because the STALE process answered it
  # — a publish that reported success while serving month-old code.
  pkill -f "vira.api.app" 2>/dev/null || true
  for i in $(seq 1 10); do
    ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN || break
    sleep 1
  done
  if ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then
    echo "    port $PORT still held after 10s"; exit 1
  fi
  setsid nohup ./.venv312/bin/gunicorn vira.api.app:app \
    -c deploy/gunicorn.conf.py > /tmp/vira-api.log 2>&1 < /dev/null &
  disown 2>/dev/null || true
fi

for i in \$(seq 1 40); do
  curl -sf -o /dev/null -m 2 http://127.0.0.1:$PORT/healthz && exit 0
  sleep 2
done
echo "LOCAL HEALTHCHECK FAILED"; tail -30 /tmp/vira-api.log; exit 1
REMOTE_SCRIPT

if [[ $? -ne 0 ]]; then
  red "==> new commit will not start. Rolling back to $PREV_SHA"
  ssh "$HOST" "cd $REMOTE && git reset -q --hard $PREV_SHA && sudo systemctl reload-or-restart vira-api"
  exit 1
fi

blue "==> verifying through the tunnel"
sleep 3
for i in {1..10}; do
  if curl -sf -o /dev/null -m 15 "$PUBLIC/healthz"; then
    SERVED=$(ssh "$HOST" "cd $REMOTE && git rev-parse --short HEAD" 2>/dev/null)
    if [ "$SERVED" != "$LOCAL_SHA" ]; then
      red "    box reports $SERVED but we shipped $LOCAL_SHA"; exit 1
    fi
    green "    $PUBLIC is serving $LOCAL_SHA"
    curl -s -m 15 "$PUBLIC/v1/corpus/stats" \
      | python3 -c 'import json,sys;d=json.load(sys.stdin);print(f"    corpus: {d[\"trends_total\"]} trends, {d[\"companies\"]} companies")' 2>/dev/null || true
    exit 0
  fi
  sleep 3
done

red "local health passed but $PUBLIC did not answer — check the tunnel:"
red "  ssh $HOST 'systemctl status cloudflared'"
exit 1
