#!/bin/sh
# start.sh — entrypoint for the Render container.
# Runs the demo seed (idempotent; only wipes if asked) then starts uvicorn.
#
# Why we re-seed on every cold start: Render free tier spins down after 15
# minutes of no traffic. The persistent disk survives, but the easiest way
# to guarantee a judge sees live demo data is to (re)seed at startup. Use
# SEED_DEMO=0 in the env to skip this.
set -e

cd /app/backend

if [ "${SEED_DEMO:-1}" = "1" ]; then
    echo "[start] seeding demo data (idempotent)..."
    python -m app.scripts.seed_demo --reset --with-demo-data || {
        echo "[start] seed failed, continuing anyway (app may have no demo data)" >&2
    }
fi

echo "[start] launching uvicorn on port ${PORT:-8000}..."
exec python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips '*'
