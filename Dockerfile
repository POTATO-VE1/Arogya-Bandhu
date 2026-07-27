# Aarogya Bandhu — production image
# Multi-stage: build the React frontend, then a slim Python runtime.

# ── Stage 1: build the React frontend ──
FROM node:20-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --prefer-offline 2>/dev/null || npm install
COPY frontend/ ./
RUN npm run build
# The built assets land in /build/frontend/dist

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
# Copy the built frontend into the static dir FastAPI serves
COPY --from=frontend /build/frontend/dist/ ./backend/static/

# Persistent data dir (mounted as a Railway volume)
RUN mkdir -p /app/backend/data && chown -R app:app /app
VOLUME /app/backend/data
USER app
WORKDIR /app/backend

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/healthz || exit 1

# Single worker — the in-process state (rate limiter, OTP store,
# SSE subscribers, Twilio cooldown map) is single-process by design.
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
