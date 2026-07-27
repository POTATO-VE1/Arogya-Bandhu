# Aarogya Bandhu — production image
# Multi-stage: build the React frontend, then a slim Python runtime.

# ── Stage 1: build the React frontend ──
FROM node:20-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --prefer-offline 2>/dev/null || npm install
COPY frontend/ ./
RUN npm run build
# Vite outputs to ../backend/static relative to WORKDIR, which resolves
# to /build/backend/static in the build context. (The comment in
# frontend/vite.config.ts: build.outDir = "../backend/static".)

# ── Stage 2: Python runtime ──
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd -r app && useradd -r -g app -d /app -m app

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
# Copy the built frontend into the static dir FastAPI serves.
# Vite wrote to ../backend/static (relative to WORKDIR /build/frontend),
# so the absolute path inside the frontend stage is /build/backend/static.
COPY --from=frontend /build/backend/static/ ./backend/static/

# Persistent data dir. On Render we mount a persistent disk here (see
# render.yaml: disk.mountPath = /var/data). We deliberately do NOT switch
# to a non-root user here because the persistent-disk mount point is
# owned by root and the app user can't write to it. For a hackathon
# demo this is fine; for production you'd run as non-root and adjust
# the mount perms in start.sh.
RUN mkdir -p /app/backend/data /var/data
WORKDIR /app/backend

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -fsS http://localhost:${PORT:-8000}/api/healthz || exit 1

# start.sh runs the demo seed (idempotent on warm starts, resets on cold
# starts) then execs uvicorn. Single worker — in-process state is
# single-process by design.
CMD ["./start.sh"]
