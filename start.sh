#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# Aarogya Bandhu — Production startup script
# Used by Procfile (Railway/Render) and manual deploys.
# ══════════════════════════════════════════════════════════════
set -euo pipefail

cd "$(dirname "$0")/backend"

# Build frontend if dist/ doesn't exist
if [ ! -d "../frontend/dist" ]; then
    echo "→ building frontend..."
    cd ../frontend
    npm ci --prefer-offline 2>/dev/null || npm install
    npm run build
    cd ../backend
    # Copy built frontend to static/
    cp -r ../frontend/dist ./static
fi

# Ensure data directory exists
mkdir -p data/audio

PORT="${PORT:-8000}"
echo "→ starting Aarogya Bandhu on port $PORT..."
exec python -m uvicorn main:app --host 0.0.0.0 --port "$PORT"
