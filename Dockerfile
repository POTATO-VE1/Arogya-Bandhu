# ══════════════════════════════════════════════════════════════
# Aarogya Bandhu — Multi-stage Docker build
# Works on Railway, Render, Fly.io, or any Docker host.
# ══════════════════════════════════════════════════════════════

# ── Stage 1: Build frontend ──
FROM node:20-alpine AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --prefer-offline 2>/dev/null || npm install
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Production image ──
FROM python:3.12-slim AS production

# Security: non-root user
RUN groupadd -r app && useradd -r -g app -d /app app

WORKDIR /app

# Install Python deps
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ ./backend/

# Copy built frontend into backend/static/
COPY --from=frontend-builder /build/frontend/dist/ ./backend/static/

# Create data directory for SQLite
RUN mkdir -p /app/backend/data && chown -R app:app /app

USER app
WORKDIR /app/backend

EXPOSE 8000

# Production entrypoint
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
